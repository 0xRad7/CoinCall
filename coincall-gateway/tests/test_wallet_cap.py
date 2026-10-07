"""服务端钱包日累计上限（咽喉卡口）：绕过 SDK 直打网关也绕不过（T4 缓解）。"""

import pytest

from app.core.config import Settings
from tests.conftest import (
    API_KEY,
    CONSUMER_WALLET,
    FakeAuth,
    FakeManifests,
    call_headers,
    gateway_serve,
    make_manifest,
    make_x_payment_header,
)

pytestmark = pytest.mark.unit


async def _call(client, text, nonce_hex, **pkw):
    return await client.post(
        "/call/svc_demo",
        headers=call_headers(payment=make_x_payment_header(value="300000", nonce=nonce_hex, **pkw)),
        json={"text": text},
    )


async def test_wallet_daily_cap_second_call_rejected(tmp_path):
    m = make_manifest(service_id="svc_demo")
    m.manifest.pricing.amount_raw = "300000"
    m.manifest.pricing.amount = "0.30"
    from tests.conftest import CHAIN_ID, VAULT

    settings = Settings(
        duckdb_path=str(tmp_path / "g.duckdb"),
        pay_vault_address=VAULT,
        chain_id=CHAIN_ID,
        wallet_daily_cap_raw=300000,  # 卡口=一笔价：第二笔必触
    )
    async with gateway_serve(settings, manifests=FakeManifests({"svc_demo": m})) as (client, _app):
        r1 = await _call(client, "one", "0x" + "11" * 32)
        assert r1.status_code == 200, r1.text
        r2 = await _call(client, "two", "0x" + "22" * 32)
        assert r2.status_code == 402
        body = r2.json()
        assert body["code"] == "wallet_daily_cap_exceeded"
        assert "今日已累计" in body["detail"]


async def test_cap_independent_per_wallet(tmp_path):
    m = make_manifest(service_id="svc_demo")
    m.manifest.pricing.amount_raw = "300000"
    m.manifest.pricing.amount = "0.30"
    from eth_keys import keys as _keys

    other_key = _keys.PrivateKey(bytes.fromhex("ee" * 32))
    other_wallet = other_key.public_key.to_checksum_address()
    from app.modules.auth import ApiKeyInfo

    auth = FakeAuth(
        {
            API_KEY: ApiKeyInfo(
                key_id="k1", consumer_wallet=CONSUMER_WALLET, quota_raw=None, status="active"
            ),
            "cck_other": ApiKeyInfo(
                key_id="k2", consumer_wallet=other_wallet, quota_raw=None, status="active"
            ),
        }
    )
    from tests.conftest import CHAIN_ID, VAULT

    settings = Settings(
        duckdb_path=str(tmp_path / "g.duckdb"),
        pay_vault_address=VAULT,
        chain_id=CHAIN_ID,
        wallet_daily_cap_raw=300000,  # 卡口=一笔价：第二笔必触
    )
    async with gateway_serve(settings, auth=auth, manifests=FakeManifests({"svc_demo": m})) as (
        client,
        _app,
    ):
        assert (await _call(client, "one", "0x" + "33" * 32)).status_code == 200
        assert (await _call(client, "two", "0x" + "44" * 32)).status_code == 402
        # 另一个钱包（另一把 key）不受前者累计影响
        r3 = await client.post(
            "/call/svc_demo",
            headers=call_headers(
                api_key="cck_other",
                payment=make_x_payment_header(
                    from_addr=other_wallet,
                    value="300000",
                    nonce="0x" + "55" * 32,
                    signer=other_key,
                ),
            ),
            json={"text": "x"},
        )
        assert r3.status_code == 200, r3.text
