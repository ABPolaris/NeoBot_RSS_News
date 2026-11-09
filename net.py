"""带 SSRF 防护的 RSS 下载。

用户填写的 RSS 地址是不可信输入：只允许 http/https，拒绝凭据、内网地址、
回环、链路本地、保留地址与非公网 IPv6；每一跳重定向都会重新校验。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import FetchError, UnsafeURLError

MAX_REDIRECTS = 5
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0

_BLOCKED_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".intranet",
    ".lan",
    ".home.arpa",
    ".in-addr.arpa",
    ".ip6.arpa",
)
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.google.com",
        "instance-data",
    }
)
_HEADERS = {
    "User-Agent": "NeoBot_RSS_News/0.1 (+https://github.com/; RSS reader plugin)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}

Resolver = Callable[[str, int], Awaitable[list[str]]]


def _host_key(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


def is_global_ip(value: str) -> bool:
    """判断解析结果是否属于公网地址。"""

    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.is_site_local:
        return False
    return bool(address.is_global)


async def _default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _static_host_check(host: str) -> str:
    """只做不联网的检查，返回用于解析的 ASCII 主机名。"""

    if not host:
        raise UnsafeURLError("RSS 地址缺少主机名")
    if any(character.isspace() for character in host) or any(
        ord(character) < 0x20 for character in host
    ):
        raise UnsafeURLError("RSS 地址包含非法字符")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise UnsafeURLError("RSS 地址的主机名无效") from None
    key = _host_key(ascii_host)
    if key in _BLOCKED_HOSTS or key.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURLError("RSS 地址指向本机或内网域名，已拒绝")
    return ascii_host


async def validate_feed_url(
    url: str,
    *,
    allow_unresolved: bool = False,
    resolver: Resolver | None = None,
) -> str:
    """校验一个 RSS 地址，返回去掉首尾空白的地址。

    ``allow_unresolved=True`` 时，域名暂时解析失败不会拒绝保存，
    但实际抓取仍会重新解析并要求全部地址为公网。
    """

    text = (url or "").strip()
    if not text:
        raise UnsafeURLError("RSS 地址不能为空")
    if len(text) > 2048:
        raise UnsafeURLError("RSS 地址不能超过 2048 个字符")
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError("RSS 地址必须以 http:// 或 https:// 开头")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("RSS 地址不能包含用户名或密码")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("RSS 地址缺少主机名")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeURLError("RSS 地址的端口无效") from None
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    if not 1 <= port <= 65535:
        raise UnsafeURLError("RSS 地址的端口无效")
    ascii_host = _static_host_check(host)
    try:
        ipaddress.ip_address(ascii_host)
    except ValueError:
        try:
            addresses = await (resolver or _default_resolver)(ascii_host, port)
        except (OSError, TimeoutError) as exc:
            if allow_unresolved:
                return text
            raise FetchError("RSS 地址的域名解析失败", retryable=True) from exc
        if not addresses:
            if allow_unresolved:
                return text
            raise FetchError("RSS 地址的域名解析失败", retryable=True)
        unsafe = [address for address in addresses if not is_global_ip(address)]
        if unsafe:
            raise UnsafeURLError("RSS 地址解析到内网或保留地址，已拒绝")
    else:
        if not is_global_ip(ascii_host):
            raise UnsafeURLError("RSS 地址指向内网或保留地址，已拒绝")
    return text


async def fetch_feed_bytes(
    url: str,
    *,
    client: httpx.AsyncClient,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_BYTES,
    resolver: Resolver | None = None,
) -> tuple[bytes, str, str]:
    """下载一个 RSS 地址，返回（内容、Content-Type、最终地址）。

    重定向手动跟随，每一跳都重新做 SSRF 校验。
    """

    current = url
    try:
        # 整个重定向链共用一次 30 秒超时，避免逐跳超时叠加成数分钟。
        async with asyncio.timeout(timeout):
            for _ in range(MAX_REDIRECTS + 1):
                current = await validate_feed_url(
                    current, allow_unresolved=False, resolver=resolver
                )
                async with client.stream("GET", current, headers=_HEADERS) as response:
                    status = response.status_code
                    if status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("RSS 源返回了没有目标地址的重定向")
                        current = urljoin(current, location)
                        continue
                    if status == 304:
                        return b"", response.headers.get("content-type", ""), current
                    if status >= 400:
                        retryable = status in {408, 425, 429} or status >= 500
                        raise FetchError(
                            f"RSS 源返回 HTTP {status}", retryable=retryable
                        )
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError("RSS 内容超过 5MB 上限")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError("RSS 内容超过 5MB 上限")
                        chunks.append(chunk)
                    return (
                        b"".join(chunks),
                        response.headers.get("content-type", ""),
                        current,
                    )
    except FetchError:
        raise
    except TimeoutError as exc:
        raise FetchError("RSS 请求超时", retryable=True) from exc
    except httpx.TimeoutException as exc:
        raise FetchError("RSS 请求超时", retryable=True) from exc
    except httpx.TransportError as exc:
        raise FetchError(
            f"RSS 网络错误（{type(exc).__name__}）", retryable=True
        ) from exc
    raise FetchError("RSS 重定向次数过多", retryable=True)


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_BYTES",
    "MAX_REDIRECTS",
    "fetch_feed_bytes",
    "is_global_ip",
    "validate_feed_url",
]
