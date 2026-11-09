"""URL 校验与 SSRF 防护：静态规则、DNS 解析、重定向逐跳校验。"""

from __future__ import annotations

import httpx
import pytest
from NeoBot_RSS_News.errors import FetchError, UnsafeURLError
from NeoBot_RSS_News.net import fetch_feed_bytes, is_global_ip, validate_feed_url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/feed",
        "file:///etc/passwd",
        "http://localhost/feed",
        "http://127.0.0.1/feed",
        "http://10.0.0.1/feed",
        "http://192.168.1.1/feed",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/feed",
        "https://user:pass@example.com/feed",
        "http:///feed",
        "http://example.com:99999/feed",
    ],
)
async def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        await validate_feed_url(url, allow_unresolved=True)


async def test_accepts_public_literal_ip() -> None:
    assert (
        await validate_feed_url("https://93.184.216.34/feed", allow_unresolved=False)
        == "https://93.184.216.34/feed"
    )


async def test_dns_result_to_private_address_is_rejected() -> None:
    async def resolver(_host: str, _port: int) -> list[str]:
        return ["10.0.0.8"]

    with pytest.raises(UnsafeURLError):
        await validate_feed_url(
            "https://feeds.example.com/rss", allow_unresolved=False, resolver=resolver
        )


async def test_unresolved_dns_can_be_saved_but_not_fetched() -> None:
    async def resolver(_host: str, _port: int) -> list[str]:
        raise OSError("synthetic dns failure")

    url = "https://feeds.example.com/rss"
    assert await validate_feed_url(url, allow_unresolved=True, resolver=resolver) == url
    with pytest.raises(FetchError) as info:
        await validate_feed_url(url, allow_unresolved=False, resolver=resolver)
    assert info.value.retryable is True


async def test_redirect_to_private_address_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        return httpx.Response(302, headers={"location": "http://127.0.0.1/feed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnsafeURLError):
            await fetch_feed_bytes("https://93.184.216.34/feed", client=client)


async def test_http_500_is_retryable_and_404_is_not() -> None:
    async def status_client(status: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(status))
        )

    client = await status_client(500)
    async with client:
        with pytest.raises(FetchError) as info:
            await fetch_feed_bytes("https://93.184.216.34/feed", client=client)
    assert info.value.retryable is True

    client = await status_client(404)
    async with client:
        with pytest.raises(FetchError) as info:
            await fetch_feed_bytes("https://93.184.216.34/feed", client=client)
    assert info.value.retryable is False


async def test_oversized_declared_content_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"content-length": "1000"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FetchError):
            await fetch_feed_bytes(
                "https://93.184.216.34/feed", client=client, max_bytes=100
            )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("93.184.216.34", True),
        ("8.8.8.8", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.0.1", False),
        ("169.254.169.254", False),
        ("0.0.0.0", False),
        ("::1", False),
        ("fd00::1", False),
        ("2001:4860:4860::8888", True),
        ("not-an-ip", False),
    ],
)
def test_is_global_ip(address: str, expected: bool) -> None:
    assert is_global_ip(address) is expected
