"""上游响应内容安全扫描（L1 投毒启发式 + L2 凭证泄露，design/api-security-probe.md §1.2）。

确定性规则扫描（禁 LLM、零网络、零新依赖）：对 probe 响应体递归扫描字符串叶，
命中输出 {category, rule, severity, path, occurrences, match_offset, snippet}。

脱敏纪律（公开面安全）：
- leak 类命中绝不回显值本身——只回显字段路径+规则名+字符偏移，snippet 恒空串；
- poison 类 snippet 为命中点前后文（≤60 字符），窗口内出现的 leak 值以 ‹已脱敏› 占位。

规则表为模块级常量（可测试、可扩展）；诚实边界：正则挡剧本化投毒与已知凭证
形态，不语义判恶（对抗改写可绕过，见设计文档 §6）。
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.errors import ApiError
from app.storage.db import CoreStore

router = APIRouter(tags=["security"])

#: 命中前后文窗口（单侧字符数；snippet 截断 ≤60）
_SNIPPET_CONTEXT_CHARS = 25
_SNIPPET_MAX_CHARS = 60
#: 单次扫描 findings 上限（防超大响应刷爆记录；到达即停，确定性）
_MAX_FINDINGS = 64
_REDACTED = "‹已脱敏›"

Severity = Literal["high", "medium", "low"]
Category = Literal["leak", "poison"]


@dataclass(frozen=True)
class ScanRule:
    """一条确定性扫描规则（name 即对外稳定标识，进持久化记录）。"""

    name: str
    category: Category
    severity: Severity
    pattern: re.Pattern[str]
    description: str


#: L2 凭证泄露（确定性高：已知凭证形态）
LEAK_RULES: tuple[ScanRule, ...] = (
    ScanRule(
        "openai_style_key",
        "leak",
        "high",
        re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9][A-Za-z0-9_-]{19,}"),
        "OpenAI 风格 API key（sk- 前缀长串）",
    ),
    ScanRule(
        "aws_access_key",
        "leak",
        "high",
        re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![0-9A-Z])"),
        "AWS AccessKeyId",
    ),
    ScanRule(
        "github_pat",
        "leak",
        "high",
        re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9])"),
        "GitHub Personal Access Token",
    ),
    ScanRule(
        "evm_private_key_like",
        "leak",
        "medium",
        re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{64}(?![0-9a-fA-F])"),
        "EVM 私钥形态 0x+64hex（与交易哈希同形，中置信）",
    ),
    ScanRule(
        "pem_private_key",
        "leak",
        "high",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----"),
        "PEM 私钥块头",
    ),
    ScanRule(
        "bearer_long_token",
        "leak",
        "medium",
        re.compile(r"(?<![A-Za-z])Bearer[ \t]+[A-Za-z0-9._~+/=-]{20,}"),
        "Bearer 后长 token 回显",
    ),
    ScanRule(
        "authorization_header_echo",
        "leak",
        "medium",
        re.compile(
            r"(?i)\b(?:authorization|x-api-key|api[-_ ]?key|x-auth-token)\b"
            r"['\"”’]?\s*[:=]\s*['\"‘“]?"
            r"(?:(?:bearer|basic|digest|token|apikey)[ \t]+)?"
            r"[A-Za-z0-9._~+/=-]{8,}"
        ),
        "认证头键值成对回显",
    ),
)

#: L1 投毒/指令注入（启发式确定性规则：命中≠恶意，供决策标记不自动拦截）
POISON_RULES: tuple[ScanRule, ...] = (
    ScanRule(
        "injection_ignore_previous",
        "poison",
        "high",
        re.compile(
            r"(?i)\bignore\s+(?:all\s+|the\s+)?(?:previous|prior|above)\s+"
            r"(?:instructions?|prompts?|rules?|context|messages?)"
        ),
        "指令注入：要求忽略先前指令",
    ),
    ScanRule(
        "injection_disregard_above",
        "poison",
        "high",
        re.compile(
            r"(?i)\bdisregard\s+(?:the\s+)?(?:above|previous|prior|all|any)\s+"
            r"(?:instructions?|prompts?|rules?|context|messages?)"
        ),
        "指令注入：要求无视上文",
    ),
    ScanRule(
        "prompt_overwrite_you_are_now",
        "poison",
        "medium",
        re.compile(r"(?i)\byou\s+are\s+now\b"),
        "system prompt 覆写用语（you are now）",
    ),
    ScanRule(
        "prompt_overwrite_act_as",
        "poison",
        "low",
        re.compile(r"(?i)\bact(?:s|ed|ing)?\s+as\b"),
        "角色覆写用语（act as，低置信：正常文本常见）",
    ),
    ScanRule(
        "exfiltrate_language",
        "poison",
        "high",
        re.compile(r"(?i)\bexfiltrat\w*\b"),
        "数据外传用语（exfiltrate）",
    ),
    ScanRule(
        "secret_request",
        "poison",
        "high",
        re.compile(
            r"(?i)\b(?:send|reveal|share|print|output|repeat|leak|post|upload)\s+"
            r"(?:your|the|all|any)\s+"
            r"(?:api[_ ]?keys?|secrets?|credentials?|private\s+keys?|seed\s+phrases?|"
            r"system\s+prompts?|wallets?)\b"
        ),
        "要求交出密钥/系统提示词",
    ),
    ScanRule(
        "exfil_webhook_domain",
        "poison",
        "high",
        re.compile(
            r"(?i)\b(?:[a-z0-9-]+\.)*(?:webhook\.site|requestbin\.(?:com|net)|"
            r"pipedream\.net|interact\.sh|oast\.(?:pro|live|site|fun|me)|"
            r"burpcollaborator\.net|canarytokens\.(?:com|org)|"
            r"hooks\.slack\.com/services/|discord(?:app)?\.com/api/webhooks|"
            r"api\.telegram\.org/bot)"
        ),
        "外传 webhook/回连域名特征",
    ),
    ScanRule(
        "pseudo_system_marker",
        "poison",
        "medium",
        re.compile(r"SYSTEM\s+PROMPT\s*[:：]|\bSYSTEM\s*[:：]"),
        "伪 system 角色标记（大写 SYSTEM:）",
    ),
    ScanRule(
        "long_base64_payload",
        "poison",
        "low",
        re.compile(r"[A-Za-z0-9+/]{256,}={0,2}"),
        "超长 base64 载荷（≥256 字符）",
    ),
)

SCAN_RULES: tuple[ScanRule, ...] = LEAK_RULES + POISON_RULES


# ---------------------------------------------------------------------------
# 扫描器（纯函数，可单测）
# ---------------------------------------------------------------------------


def _leak_spans(text: str) -> list[tuple[int, int]]:
    """文本上全部 leak 命中区间（poison snippet 窗口内要整体遮蔽这些值）。"""
    spans: list[tuple[int, int]] = []
    for rule in LEAK_RULES:
        for m in rule.pattern.finditer(text):
            spans.append((m.start(), m.end()))
    return spans


def _snippet(text: str, start: int, end: int, spans: list[tuple[int, int]]) -> str:
    """命中点前后文（≤60 字符），窗口内 leak 值以 ‹已脱敏› 占位。"""
    lo = max(0, start - _SNIPPET_CONTEXT_CHARS)
    hi = min(len(text), max(end + _SNIPPET_CONTEXT_CHARS, lo + 1), lo + _SNIPPET_MAX_CHARS)
    pieces: list[str] = []
    cur = lo
    for s, e in sorted(spans):
        if e <= lo or s >= hi:
            continue
        if s > cur:
            pieces.append(text[cur:s])
        pieces.append(_REDACTED)
        cur = max(cur, e)
    if cur < hi:
        pieces.append(text[cur:hi])
    return "".join(pieces)[:_SNIPPET_MAX_CHARS]


#: 认证头键名（dict 键值成对回显判定：键名命中 + 字符串值 ≥8 字符即 leak）
_HEADER_KEY_RE = re.compile(r"(?i)^(?:authorization|x-api-key|api[-_ ]?key|x-auth-token)$")
_HEADER_PAIR_MIN_LEN = 8
#: authorization_header_echo 规则对象（键值对通道复用同一条规则定义）
_ECHO_RULE = next(r for r in LEAK_RULES if r.name == "authorization_header_echo")


def _scan_text(text: str, path: str, out: list[dict[str, Any]]) -> None:
    if not text:
        return
    leak_spans = _leak_spans(text)
    for rule in SCAN_RULES:
        matches = list(rule.pattern.finditer(text))
        if not matches:
            continue
        first = matches[0]
        out.append(
            {
                "category": rule.category,
                "rule": rule.name,
                "severity": rule.severity,
                "path": path,
                "occurrences": len(matches),
                "match_offset": first.start(),
                # leak 类不回显值本身（snippet 恒空）；poison 类摘要且窗口内遮蔽凭证值
                "snippet": ""
                if rule.category == "leak"
                else _snippet(text, first.start(), first.end(), leak_spans),
            }
        )


def _emit_header_pair(rule: ScanRule, path: str, out: list[dict[str, Any]]) -> None:
    """{"Authorization": "<value>"} 形态的认证头回显（键名+值成对才算命中）。"""
    out.append(
        {
            "category": rule.category,
            "rule": rule.name,
            "severity": rule.severity,
            "path": path,
            "occurrences": 1,
            "match_offset": 0,
            "snippet": "",
        }
    )


def _walk(node: object, path: str, out: list[dict[str, Any]]) -> None:
    if len(out) >= _MAX_FINDINGS:
        return
    if isinstance(node, str):
        _scan_text(node, path, out)
    elif isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            # 认证头键值成对回显：键名命中规则 + 字符串值达阈值（值本身不回显）
            if (
                isinstance(value, str)
                and len(value.strip()) >= _HEADER_PAIR_MIN_LEN
                and _HEADER_KEY_RE.match(str(key))
            ):
                _emit_header_pair(_ECHO_RULE, child, out)
            _walk(value, child, out)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            _walk(item, f"{path}[{i}]", out)


def scan_body(body: object) -> list[dict[str, Any]]:
    """响应体（JSON 反序列化后或字符串预览）→ findings 列表（确定性、脱敏）。

    遍历顺序（dict 插入序 × 规则表序）确定；同 (rule, path) 去重（叶文本与
    键值对两条通道可能对同一回显双命中）。
    """
    out: list[dict[str, Any]] = []
    _walk(body, "$", out)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for f in out:
        key = (f["rule"], f["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped


def severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    """severity → 计数（决策层 content_scan 人话摘要用）。"""
    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev = str(f.get("severity", "low"))
        if sev in counts:
            counts[sev] += 1
    return counts


# ---------------------------------------------------------------------------
# 公开端点：服务维度最新一次扫描记录（不含凭证值本身）
# ---------------------------------------------------------------------------


class SecurityFinding(BaseModel):
    category: Category = Field(description="leak=凭证泄露 / poison=投毒特征")
    rule: str = Field(description="规则名（稳定标识，见 /services/security/rules）")
    severity: Severity
    path: str = Field(description="命中字段路径（JSONPath 风格）")
    occurrences: int = 1
    match_offset: int = Field(description="命中串内字符偏移（不回显值本身）")
    snippet: str = Field(default="", description="脱敏片段（leak 类恒空）")


class ServiceSecurityResponse(BaseModel):
    service_id: str
    clean: bool = Field(description="无任何命中")
    findings: list[SecurityFinding]
    http_status: int | None = Field(description="被扫描探测的上游 HTTP 状态")
    scanned_at: str | None


class ScanRulesResponse(BaseModel):
    rules: list[dict[str, str]]
    disclaimer: str = Field(description="诚实边界：确定性启发式，不语义判恶")


@router.get("/services/security/rules", response_model=ScanRulesResponse)
def list_scan_rules() -> ScanRulesResponse:
    """规则表自描述（公开：Agent/Provider 可核对扫描口径）。"""
    return ScanRulesResponse(
        rules=[
            {
                "name": r.name,
                "category": r.category,
                "severity": r.severity,
                "description": r.description,
            }
            for r in SCAN_RULES
        ],
        disclaimer=(
            "确定性规则扫描（禁 LLM）：挡剧本化注入与已知凭证形态；"
            "语义级对抗改写可绕过，命中≠恶意，仅作决策层风险标记"
        ),
    )


@router.get("/services/security/{service_id}", response_model=ServiceSecurityResponse)
def get_service_security(service_id: str, request: Request) -> ServiceSecurityResponse:
    """该服务最近一次探测扫描记录（公开面；findings 按构造脱敏，不含凭证值）。"""
    store: CoreStore = request.app.state.store
    row = store.get_service_security(service_id)
    if row is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"该服务从未探测（无扫描记录）: {service_id}",
            code="security_scan_not_found",
        )
    return ServiceSecurityResponse(
        service_id=row["service_id"],
        clean=bool(row["clean"]),
        findings=[SecurityFinding(**f) for f in row["findings"]],
        http_status=row["http_status"],
        scanned_at=row["scanned_at"],
    )
