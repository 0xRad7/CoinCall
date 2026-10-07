"""02 节核心路由：POST /call/{service_id} —— 7 步时序，每步可注入。

① api key 认证（httpx → core /internal/apikeys/validate）
② 查服务（manifest 缓存，status!=active → 404）
③ 请求 schema 校验（jsonschema，不合规不计费）
④ 幂等（X-Idempotency-Key：同 body 重放返回上次结果，不同 body 409）
⑤ 支付验证与影子闸门（scheme.verify 本地验签+链上约束 → ShadowGate → 写 calls inflight）
⑥ Provider 转发（2xx→success+settle 队列；非 2xx/超时→aborted 零扣款）
⑦ 收据头（X-Receipt-Id / X-Charged-Raw / X-Receipt-Sig）
"""

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from app.core.errors import ApiError, PaymentRequiredError
from app.core.payment import PaymentError, XPayment, parse_x_payment
from app.modules.auth import ApiKeyInfo, AuthError, CoreAuthClient
from app.modules.calls import CallRecord, CallStatus, CallStore
from app.modules.credentials_client import UpstreamCredentialsClient
from app.modules.keeper import Keeper
from app.modules.manifest_client import ManifestInfo, ServiceNotFoundError
from app.modules.providers import ProviderAdapter, ProviderError, ProviderResult
from app.modules.receipt import ReceiptSigner, build_receipt, receipt_ts, sign_receipt

router = APIRouter(tags=["call"])


def _body_hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


async def _challenge(request: Request, code: str, detail: str) -> PaymentRequiredError:
    """构造 402 质询（02 §3；字段由 scheme 产出；manifest 尽力加载以填 pricing）。"""
    scheme = request.app.state.scheme
    service_id = str(request.path_params.get("service_id", ""))
    manifest = getattr(request.state, "manifest", None)
    if manifest is None:
        try:
            manifest = await request.app.state.manifests.get(service_id)
            request.state.manifest = manifest
        except ServiceNotFoundError:
            manifest = None
    pricing = (
        manifest.manifest.pricing.model_dump()
        if manifest is not None
        else {"amount": "0", "amount_raw": "0", "token": "USDT"}
    )
    balance = getattr(request.state, "wallet_balance_raw", 0)
    challenge = scheme.build_challenge(
        code=code,
        detail=detail,
        service_id=service_id,
        pricing=pricing,
        wallet_balance_raw=balance,
        pay_to=scheme.verifying_contract,
    )
    return PaymentRequiredError(challenge)


async def _authenticate(request: Request, api_key: str | None) -> ApiKeyInfo:
    """步骤①：无 key → 402（带注册指引口径）；坏 key → 401；坏账消费者 → 402 bad_debt。"""
    auth: CoreAuthClient = request.app.state.auth
    if not api_key:
        raise await _challenge(request, "missing_api_key", "缺少 X-Api-Key（先到 core 签发）")
    try:
        key_info = await auth.validate(api_key)
    except AuthError as exc:
        if exc.code == "core_unavailable":
            raise ApiError(
                status_code=502, error="service_error", detail=exc.detail, code=exc.code
            ) from exc
        raise ApiError(
            status_code=401, error="unauthorized", detail=exc.detail, code=exc.code
        ) from exc
    keeper: Keeper = request.app.state.keeper
    if keeper.blacklisted(key_info.consumer_wallet):
        # 拉黑联动（09 P0-5）：keeper 坏账 → 影子闸门 K 限幅之外的最后防线
        raise await _challenge(
            request, "bad_debt", "消费者存在未结坏账（bad_debt），付费调用已被拦截"
        )
    return key_info


async def _load_manifest(request: Request, service_id: str) -> ManifestInfo:
    """步骤②：manifest（缓存）；不存在/paused → 404。"""
    try:
        manifest_client = request.app.state.manifests
        info = await manifest_client.get(service_id)
    except ServiceNotFoundError as exc:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 不存在或未激活: {exc.service_id}",
            code="service_not_found",
        ) from exc
    if info.status != "active" or info.manifest.status != "active":
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 未激活: {service_id}",
            code="service_not_found",
        )
    return info


def _validate_schema(manifest: ManifestInfo, body: Any) -> None:
    """步骤③：input_schema 校验，不合规 → 422（不产生计费）。"""
    try:
        Draft202012Validator(manifest.manifest.input_schema).validate(body)
    except JsonSchemaValidationError as exc:
        raise ApiError(
            status_code=422,
            error="invalid_request",
            detail=f"请求体不符合 input_schema: {exc.message}",
            code="request_schema_invalid",
        ) from exc
    except SchemaError as exc:
        raise ApiError(
            status_code=500,
            error="internal_error",
            detail=f"服务 input_schema 本身非法: {exc.message}",
            code="schema_broken",
        ) from exc


def _idempotency_replay(
    request: Request, service_id: str, consumer_wallet: str, key: str, body: Any
) -> JSONResponse | None:
    """步骤④：同钱包同 body → 上次结果（防重试双扣）；不同 body → 409。

    缓存键含消费钱包：幂等只对「同一钱包的重试」生效——换钱包带同键是新一笔购买，
    不得回放他人的旧收据（否则签名未被消费、链上永不扣款，消费端却显示已付费）。
    回放响应带 X-Idempotency-Replay: 1，客户端与日志可分辨。
    """
    idempotency: dict[tuple[str, str, str], dict[str, Any]] = request.app.state.idempotency
    hit = idempotency.get((service_id, consumer_wallet.lower(), key))
    if hit is None:
        return None
    if hit["body_hash"] != _body_hash(body):
        raise ApiError(
            status_code=409,
            error="idempotency_conflict",
            detail="同 Idempotency-Key 但请求体不同",
            code="idempotency_conflict",
        )
    replay: dict[str, Any] = dict(hit)
    replay_headers = dict(replay["headers"])
    replay_headers["X-Idempotency-Replay"] = "1"
    return JSONResponse(
        status_code=replay["status_code"],
        content=replay["body"],
        headers=replay_headers,
    )


async def _admit_payment(
    request: Request,
    manifest: ManifestInfo,
    key_info: ApiKeyInfo,
    payment_header: str | None,
) -> tuple[str, XPayment]:
    """步骤⑤：quota → 解析 → verify（验签+链上+nonce）→ 影子闸门 → inflight 流水。"""
    scheme = request.app.state.scheme
    gate = request.app.state.shadow_gate
    store: CallStore = request.app.state.store
    price_raw = manifest.manifest.pricing.amount_raw

    if key_info.quota_raw is not None and int(price_raw) > key_info.quota_raw:
        raise await _challenge(request, "quota_exceeded", "超出该 api key 单笔授权上限")
    if not payment_header:
        raise await _challenge(request, "missing_x_payment", "缺少 X-PAYMENT 支付授权头")
    try:
        x_payment = parse_x_payment(payment_header)
    except PaymentError as exc:
        raise await _challenge(request, exc.code, exc.detail) from exc

    verify = await scheme.verify(
        x_payment=x_payment,
        expected_from=key_info.consumer_wallet,
        expected_value_raw=price_raw,
        pay_to=scheme.verifying_contract,
    )
    request.state.wallet_balance_raw = verify.wallet_balance_raw or 0
    if not verify.ok:
        raise await _challenge(request, verify.code or "payment_rejected", verify.detail)

    # 服务端咽喉卡口：按钱包日累计（绕过 SDK 直打也绕不过；0=关闭）
    settings = request.app.state.settings
    if settings.wallet_daily_cap_raw > 0:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        spent_today = request.app.state.store.daily_spent_by_wallet(key_info.consumer_wallet, day)
        if spent_today + int(price_raw) > settings.wallet_daily_cap_raw:
            raise await _challenge(
                request,
                "wallet_daily_cap_exceeded",
                f"钱包 {key_info.consumer_wallet[:10]}… 今日已累计 {spent_today / 1e6:.2f} USDT，"
                f"加本笔 {int(price_raw) / 1e6:.2f} 将超过平台侧日上限 "
                f"{settings.wallet_daily_cap_raw / 1e6:.0f} USDT"
                "（服务端硬顶，绕过客户端策略也生效；如需提升请联系平台或明日再来）",
            )

    onchain_limit = min(verify.wallet_balance_raw or 0, verify.wallet_allowance_raw or 0)
    decision = gate.try_acquire(key_info.key_id, int(price_raw), limit=onchain_limit)
    if not decision.allowed:
        raise await _challenge(request, decision.code or "shadow_rejected", decision.detail)

    call_id = f"call_{uuid.uuid4().hex[:16]}"
    store.insert_call(
        CallRecord(
            call_id=call_id,
            service_id=manifest.service_id,
            provider_agent_id=manifest.manifest.provider.agent_id,
            consumer_key_id=key_info.key_id,
            consumer_wallet=x_payment.from_,
            amount_raw=int(price_raw),
            payment_nonce=x_payment.nonce,
        )
    )
    return call_id, x_payment


def _provider_for(request: Request, manifest: ManifestInfo) -> ProviderAdapter:
    providers: dict[str, ProviderAdapter] = request.app.state.providers
    provider = providers.get(manifest.manifest.endpoint.type)
    if provider is None:
        raise ApiError(
            status_code=502,
            error="service_error",
            detail=f"无 {manifest.manifest.endpoint.type} 型 provider 适配器",
            code="provider_missing",
        )
    return provider


async def _forward_and_finalize(  # noqa: PLR0917 —— 单请求上下文参数组
    request: Request,
    manifest: ManifestInfo,
    key_info: ApiKeyInfo,
    call_id: str,
    x_payment: XPayment,
    body: Any,
    idempotency_key: str | None,
) -> JSONResponse:
    """步骤⑥⑦：转发 → success/aborted → settle 队列 → 收据头。"""
    scheme = request.app.state.scheme
    gate = request.app.state.shadow_gate
    store: CallStore = request.app.state.store
    price_raw = manifest.manifest.pricing.amount_raw
    price = int(price_raw)
    provider = _provider_for(request, manifest)
    upstream_headers = None
    if manifest.manifest.endpoint.type == "http_json":
        creds: UpstreamCredentialsClient = request.app.state.upstream_credentials
        upstream_headers = await creds.get_for(
            manifest.service_id, agent_id=manifest.manifest.provider.agent_id
        )

    started = time.monotonic()
    try:
        result: ProviderResult = await provider.forward(
            manifest, body, upstream_headers=upstream_headers
        )
    except ProviderError as exc:
        gate.release(key_info.key_id, price)
        store.mark_call(call_id, CallStatus.ABORTED)
        raise ApiError(
            status_code=502, error="service_error", detail=str(exc), code="provider_failed"
        ) from exc
    latency_ms = int((time.monotonic() - started) * 1000)
    result_hash = (
        "sha256:"
        + hashlib.sha256(json.dumps(result.body, sort_keys=True, default=str).encode()).hexdigest()
    )
    store.mark_call(
        call_id,
        CallStatus.SUCCESS,
        http_status=result.status_code,
        latency_ms=latency_ms,
        result_hash=result_hash,
    )
    scheme.enqueue_settle(
        call_id=call_id,
        provider_token_id=manifest.manifest.provider.agent_id,
        x_payment=x_payment,
    )
    gate.release(key_info.key_id, price)

    receipt = build_receipt(
        call_id=call_id,
        service_id=manifest.service_id,
        provider_agent_id=manifest.manifest.provider.agent_id,
        consumer_key_id=key_info.key_id,
        amount_raw=price_raw,
        result_hash=result_hash,
    )
    settings = request.app.state.settings
    signer: ReceiptSigner = request.app.state.receipt_signer
    # 双签过渡（10 §2）：HMAC 旧头保留一个窗口；Ed25519 新头离线可验（ts 随头发布）
    headers = {
        "X-Receipt-Id": str(receipt["receipt_id"]),
        "X-Charged-Raw": price_raw,
        "X-Receipt-Ts": str(receipt_ts(receipt)),
        "X-Receipt-Sig": sign_receipt(receipt, str(settings.receipt_secret)),
        "X-Receipt-Sig-Ed25519": signer.sign_receipt(receipt),
    }
    if idempotency_key:
        idempotency: dict[tuple[str, str, str], dict[str, Any]] = request.app.state.idempotency
        idempotency[(manifest.service_id, key_info.consumer_wallet.lower(), idempotency_key)] = {
            "body_hash": _body_hash(body),
            "status_code": result.status_code,
            "body": result.body,
            "headers": headers,
        }
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)


@router.post("/call/{service_id}")
async def call_service(service_id: str, request: Request) -> JSONResponse:
    body = await request.json()
    idempotency_key = request.headers.get("X-Idempotency-Key")

    # ① 认证
    key_info = await _authenticate(request, request.headers.get("X-Api-Key"))
    # ② 查服务
    manifest = await _load_manifest(request, service_id)
    request.state.manifest = manifest
    # ③ schema 校验（不合规不计费）
    _validate_schema(manifest, body)
    # ④ 幂等（24h 保留，02 §6）——键含消费钱包：仅同钱包重试可重放
    if idempotency_key:
        replayed = _idempotency_replay(
            request, service_id, key_info.consumer_wallet, idempotency_key, body
        )
        if replayed is not None:
            return replayed
    # ⑤ 支付验证 + 影子闸门 + inflight 流水
    call_id, x_payment = await _admit_payment(
        request, manifest, key_info, request.headers.get("X-PAYMENT")
    )
    # ⑥⑦ 转发 + settle + 收据
    return await _forward_and_finalize(
        request, manifest, key_info, call_id, x_payment, body, idempotency_key
    )
