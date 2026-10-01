"""RPC/HTTP 客户端工厂（UA 伪装 + 代理分流 + 重试，移植自 bot_chain_scripts/rpc.py 实测技巧）。

代理分流（铁律 A7）：*.bohr.life 直连；botchain.ai 域仅在显式配置 PROXY 时走代理。
"""

from urllib.parse import urlsplit

from httpx import Client, HTTPTransport, Timeout
from requests import HTTPError, Session
from requests import Timeout as RequestsTimeout
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers import HTTPProvider
from web3.providers.rpc.utils import ExceptionRetryConfiguration

from app.core.chains import ChainSpec

# python-urllib 默认 UA 会被部分 WAF 拒绝（BOT_CHAIN_REPORT 实测），保留伪装 UA
DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DIRECT_DOMAIN_SUFFIX = "bohr.life"
PROXY_DOMAIN_SUFFIX = "botchain.ai"

RPC_TIMEOUT_S = 15.0
RPC_RETRIES = 2
HTTP_CONNECT_S = 5.0
HTTP_READ_S = 15.0
HTTP_RETRIES = 2


def resolve_proxy(url: str, configured: str | None) -> str | None:
    """bohr.life 直连；botchain.ai 走配置代理；未配置代理时返回 None（调用方自负）。"""
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(DIRECT_DOMAIN_SUFFIX):
        return None
    if host.endswith(PROXY_DOMAIN_SUFFIX):
        return configured
    return None


def make_web3(spec: ChainSpec, proxy: str | None = None) -> Web3:
    """构建 968/677 的 web3 客户端：UA 伪装 + 超时 + 网络错误重试 + POA 适配。

    BOT Chain 为 POA 链，块头 extraData 达 277 字节（C-04 实测发现），
    必须注入 ExtraDataToPOAMiddleware，否则 get_block 全系失败。
    """
    session = Session()
    session.headers.update(
        {"User-Agent": DEFAULT_UA, "Accept": "application/json", "Content-Type": "application/json"}
    )
    effective = resolve_proxy(spec.rpc_url, proxy)
    if effective:
        session.proxies = {"http": effective, "https": effective}
    provider = HTTPProvider(
        endpoint_uri=spec.rpc_url,
        request_kwargs={"timeout": RPC_TIMEOUT_S},
        session=session,
        # web3 7.16 的 ExceptionRetryConfiguration 不显式传 errors 会触发 pydantic 校验错误（C-03）
        exception_retry_configuration=ExceptionRetryConfiguration(
            errors=(ConnectionError, HTTPError, RequestsTimeout),
            retries=RPC_RETRIES,
        ),
    )
    w3 = Web3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def make_http_client(base_url: str, *, proxy: str | None = None) -> Client:
    """构建通用 httpx 客户端（Bundler/Blockscout）：重试 2 次 + 统一超时。"""
    transport = HTTPTransport(
        retries=HTTP_RETRIES,
        proxy=resolve_proxy(base_url, proxy),
    )
    return Client(
        base_url=base_url,
        headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
        timeout=Timeout(HTTP_CONNECT_S, read=HTTP_READ_S, write=HTTP_READ_S, pool=HTTP_READ_S),
        transport=transport,
        follow_redirects=True,
    )
