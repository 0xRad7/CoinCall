"""配置卫生：合约地址字段必须是合法 EVM 地址（防补丁把注释写进值内——2026-10-07 实例）。"""

import re

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.unit

_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


@pytest.mark.parametrize(
    "field",
    ["pay_vault_address", "platform_custodian_address", "gateway_base_url"],
)
def test_address_fields_clean(field: str) -> None:
    value = getattr(Settings(), field)
    if field.endswith("_address"):
        assert _ADDR.match(value), f"{field} 含非法字符/注释残留: {value!r}"
    else:
        assert value.startswith("http"), f"{field} 非法 URL: {value!r}"
