"""冻结契约 #1：ServiceManifest 模型与校验（01 节 schema 正文）。

> 契约冻结（08 §2）：本文件的模型签名一经测试冻结不得修改；
> 其他任务只 import 不改动，发现问题上报而非绕过。

规范要点：
- pricing.amount_raw 是**权威**十进制字符串（最小单位），amount 仅展示；
  两者按 token 精度（V1: USDT=6）做一致性校验；
- endpoint.type V1 仅 http_json | internal（02 §6：Provider 超时硬顶 60s）；
- chain.network V1 恒 968（BOT Chain 测试网），字段为多链迁移预留（P1-2）；
- input/output_schema 必须是含 "type" 关键字的 JSON Schema（网关转发前校验依据）。
"""

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SERVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
DECIMAL_RAW_PATTERN = re.compile(r"^[0-9]+$")
AMOUNT_PATTERN = re.compile(r"^[0-9]+(\.[0-9]+)?$")

#: V1 支持的计价 token 及精度（最小单位位数）；P2 扩展时只改此表
TOKEN_DECIMALS: dict[str, int] = {"USDT": 6}

#: 02 §6：Provider 端点超时上限硬顶 60s（manifest 可配更短）
ENDPOINT_TIMEOUT_HARD_CAP_MS = 60_000
DEFAULT_ENDPOINT_TIMEOUT_MS = 30_000

CHAIN_ID_BOT_TESTNET = 968


class EndpointType(StrEnum):
    """V1 仅两种端点形态（09 P1-2）。"""

    HTTP_JSON = "http_json"
    INTERNAL = "internal"


class ServiceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DELISTED = "delisted"


class ServiceCategory(StrEnum):
    """决策层类目（10 §0.5 冻结契约 v1.2）：受控词表，默认 other。"""

    TRANSLATION = "translation"
    DATA_FEED = "data-feed"
    ON_CHAIN_QUERY = "on-chain-query"
    ANALYSIS = "analysis"
    AGENT_TOOL = "agent-tool"
    OTHER = "other"


#: 受控词表（顺序即展示顺序）；决策分区与 /decision/categories 词表与此同源
CATEGORIES: list[str] = [c.value for c in ServiceCategory]

#: tags 上限与单条长度（10 §0.5：tags ≤5）
TAGS_MAX_COUNT = 5
TAG_MAX_LEN = 32


class ManifestProvider(BaseModel):
    """provider 段：8004 身份 + 收款钱包（发布时绑定校验在 P1-2，经 bot-chain-api）。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: int = Field(ge=1, description="ERC-8004 tokenId")
    wallet: str = Field(description="agentWallet（收款地址）")
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("wallet")
    @classmethod
    def _wallet_is_address(cls, v: str) -> str:
        if not ADDRESS_PATTERN.match(v):
            raise ValueError(f"provider.wallet 不是合法 EVM 地址: {v!r}")
        return v


class ManifestEndpoint(BaseModel):
    """endpoint 段：http_json 必须带 url；internal 免 url（网关本地 handler）。"""

    model_config = ConfigDict(extra="allow")

    type: EndpointType
    url: str | None = None
    method: Literal["GET", "POST"] = Field(
        default="POST", description="上游请求方式；GET=参数映射 query"
    )
    timeout_ms: int = Field(
        default=DEFAULT_ENDPOINT_TIMEOUT_MS, ge=100, le=ENDPOINT_TIMEOUT_HARD_CAP_MS
    )

    @field_validator("url")
    @classmethod
    def _url_shape(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://", "internal://")):
            raise ValueError(f"endpoint.url 非法: {v!r}")
        return v

    @model_validator(mode="after")
    def _http_json_requires_url(self) -> "ManifestEndpoint":
        if self.type is EndpointType.HTTP_JSON and not self.url:
            raise ValueError("endpoint.type=http_json 必须提供 url")
        return self

    def requires_flat_scalars(self, input_schema: dict) -> None:
        """GET 型：消费者 JSON 参数映射为上游 query——只允许标量与标量数组。"""
        if self.method != "GET":
            return
        for name, spec in (input_schema.get("properties") or {}).items():
            itype = spec.get("type")
            if itype == "array":
                items = spec.get("items") or {}
                if items.get("type") not in ("string", "number", "integer", "boolean"):
                    raise ValueError(f"GET 参数 {name} 的数组元素必须是标量")
            elif itype not in ("string", "number", "integer", "boolean"):
                raise ValueError(f"GET 参数 {name} 不支持类型 {itype!r}（嵌套对象请改用 POST）")


class ManifestPricing(BaseModel):
    """pricing 段：amount_raw 权威；amount 展示值须与精度一致（铁律 P3）。"""

    model_config = ConfigDict(extra="forbid")

    token: str = "USDT"  # noqa: S105 —— 计价 token 符号，非凭据
    model: str = "per_call"
    amount: str = Field(description="人类可读金额（仅展示）")
    amount_raw: str = Field(description="最小单位权威十进制字符串")

    @field_validator("token")
    @classmethod
    def _known_token(cls, v: str) -> str:
        if v not in TOKEN_DECIMALS:
            raise ValueError(f"未知计价 token: {v!r}（支持: {sorted(TOKEN_DECIMALS)}）")
        return v

    @field_validator("model")
    @classmethod
    def _per_call_only(cls, v: str) -> str:
        if v != "per_call":
            raise ValueError("V1 仅支持 pricing.model=per_call")
        return v

    @field_validator("amount")
    @classmethod
    def _amount_shape(cls, v: str) -> str:
        if not AMOUNT_PATTERN.match(v):
            raise ValueError(f"pricing.amount 非十进制形态: {v!r}")
        return v

    @field_validator("amount_raw")
    @classmethod
    def _amount_raw_shape(cls, v: str) -> str:
        if not DECIMAL_RAW_PATTERN.match(v) or int(v) <= 0:
            raise ValueError(f"pricing.amount_raw 必须为正整数十进制字符串: {v!r}")
        return v

    @model_validator(mode="after")
    def _amount_consistency(self) -> "ManifestPricing":
        decimals = TOKEN_DECIMALS[self.token]
        whole, _, frac = self.amount.partition(".")
        unit = frac[:decimals].ljust(decimals, "0")
        expected = whole + unit
        if int(expected) != int(self.amount_raw):
            raise ValueError(
                f"amount 与 amount_raw 不一致: {self.amount}→{expected} != {self.amount_raw}"
            )
        return self


class ManifestChain(BaseModel):
    """chain 段：V1 恒 968；接口为多链（Base Sepolia 等）预留。"""

    model_config = ConfigDict(extra="forbid")

    network: int = CHAIN_ID_BOT_TESTNET

    @field_validator("network")
    @classmethod
    def _v1_only_968(cls, v: int) -> int:
        if v != CHAIN_ID_BOT_TESTNET:
            raise ValueError("V1 仅支持 chain.network=968（BOT Chain 测试网）")
        return v


def _require_json_schema(v: dict[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(v, dict) or "type" not in v:
        raise ValueError(f"{field_name} 必须是含 'type' 关键字的 JSON Schema")
    return v


class ServiceManifest(BaseModel):
    """ServiceManifest：01 节核心契约，全族共享（网关/SDK/排行均按此读取）。"""

    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(description="服务方自定义 slug，全局唯一")
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    provider: ManifestProvider
    endpoint: ManifestEndpoint
    pricing: ManifestPricing
    chain: ManifestChain = Field(default_factory=ManifestChain)
    input_schema: dict[str, Any] = Field(description="Consumer 请求体 JSON Schema")
    output_schema: dict[str, Any] = Field(description="Provider 响应体 JSON Schema")
    status: ServiceStatus = Field(default=ServiceStatus.ACTIVE)
    category: ServiceCategory = Field(
        default=ServiceCategory.OTHER,
        description="受控词表类目（决策层分区排序用，10 §0.5）",
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=TAGS_MAX_COUNT,
        description="自由标签，≤5 条、每条 1~32 字符",
    )
    created_at: datetime | None = None

    @field_validator("tags")
    @classmethod
    def _tags_shape(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for tag in v:
            stripped = tag.strip()
            if not stripped or len(stripped) > TAG_MAX_LEN:
                raise ValueError(f"tag 非法（1~{TAG_MAX_LEN} 字符）: {tag!r}")
            out.append(stripped)
        return out

    @field_validator("service_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SERVICE_ID_PATTERN.match(v):
            raise ValueError(f"service_id 必须匹配 {SERVICE_ID_PATTERN.pattern}: {v!r}")
        return v

    @model_validator(mode="after")
    def _get_flat_scalars(self) -> "ServiceManifest":
        self.endpoint.requires_flat_scalars(self.input_schema)
        return self

    @field_validator("input_schema")
    @classmethod
    def _input_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _require_json_schema(v, "input_schema")

    @field_validator("output_schema")
    @classmethod
    def _output_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _require_json_schema(v, "output_schema")

    def touch(self) -> None:
        """服务端发布时间戳（首次发布时调用）。"""
        if self.created_at is None:
            self.created_at = datetime.now(UTC)


def canonical_json(manifest: ServiceManifest) -> str:
    """规范化序列化（排序键、无空格）——manifest_hash 的输入。"""
    return manifest.model_dump_json(exclude={"created_at"}, by_alias=True)


def manifest_hash(manifest: ServiceManifest) -> str:
    """sha256(canonical json)，上链锚定与增量比对共用（01 §4）。"""
    return "sha256:" + hashlib.sha256(canonical_json(manifest).encode()).hexdigest()


def manifest_category(manifest: dict[str, Any]) -> str:
    """读取侧类目（发布兼容旧数据）：缺省/非法值一律 other（10 §0.5）。"""
    value = manifest.get("category")
    return value if isinstance(value, str) and value in CATEGORIES else ServiceCategory.OTHER.value


def manifest_tags(manifest: dict[str, Any]) -> list[str]:
    """读取侧标签（发布兼容旧数据）：非 list 缺省 []。"""
    value = manifest.get("tags")
    if not isinstance(value, list):
        return []
    return [str(t) for t in value]
