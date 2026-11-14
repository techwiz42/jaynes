import httpx
import pytest

from bicameral.tools.base import ToolRegistry
from bicameral.tools.guard import PrivateContentGuard
from bicameral.tools.web_fetch import WebFetch, html_to_text, is_public_ip

PUBLIC = "93.184.216.34"


def make(handler, addresses=None):
    """A WebFetch whose DNS is scripted and whose network is a mock that records every request it receives."""
    seen = []
    table = addresses or {}

    async def resolver(host):
        return table.get(host, [PUBLIC])

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return ToolRegistry([WebFetch(resolver=resolver, transport=httpx.MockTransport(wrapped))]), seen


async def fetch(registry, url, **extra):
    return await registry.run("web_fetch", {"url": url, **extra})


@pytest.mark.parametrize("ip,public", [
    ("93.184.216.34", True), ("8.8.8.8", True), ("2606:2800:220:1:248:1893:25c8:1946", True),
    ("127.0.0.1", False), ("10.0.0.5", False), ("172.16.3.4", False), ("192.168.1.1", False), ("169.254.169.254", False),
    ("100.64.0.1", False), ("0.0.0.0", False), ("::1", False), ("fe80::1", False), ("fc00::1", False), ("::ffff:127.0.0.1", False), ("224.0.0.1", False), ("not an ip", False),
])
def test_which_addresses_count_as_public(ip, public):
    assert is_public_ip(ip) is public


async def test_a_public_page_is_returned_as_readable_text():
    html = "<html><head><title>Hi</title><style>p{}</style></head><body><nav>menu</nav><script>x=1</script><h1>Heading</h1><p>Some <b>text</b>.</p><footer>f</footer></body></html>"
    registry, seen = make(lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html))
    result = await fetch(registry, "https://example.com/page")
    assert result.ok and "Title: Hi" in result.content and "Heading" in result.content and "Some text." in result.content
    assert "menu" not in result.content and "x=1" not in result.content and "p{}" not in result.content
    assert seen[0].url.host == PUBLIC and seen[0].headers["host"] == "example.com"          # the checked address is what is connected to


@pytest.mark.parametrize("url", [
    "http://example.com/", "ftp://example.com/", "https://user:pw@example.com/", "https://example.com:8443/", "https://example.com:80/", "file:///etc/passwd", "https:///nohost",
])
async def test_only_plain_https_addresses_are_allowed(url):
    registry, seen = make(lambda r: httpx.Response(200, text="x", headers={"content-type": "text/plain"}))
    result = await fetch(registry, url)
    assert not result.ok and result.error.startswith("refused") and seen == []


@pytest.mark.parametrize("host,address", [
    ("internal.example", "10.1.2.3"), ("loop.example", "127.0.0.1"), ("meta.example", "169.254.169.254"), ("v6.example", "::1"), ("mapped.example", "::ffff:10.0.0.1"),
])
async def test_a_name_that_resolves_to_a_private_address_is_refused_before_connecting(host, address):
    registry, seen = make(lambda r: httpx.Response(200, text="secret", headers={"content-type": "text/plain"}), {host: [address]})
    result = await fetch(registry, f"https://{host}/")
    assert not result.ok and "not public" in result.error and seen == []


async def test_one_private_address_among_several_refuses_the_host():
    registry, seen = make(lambda r: httpx.Response(200, text="x", headers={"content-type": "text/plain"}), {"mixed.example": [PUBLIC, "10.0.0.9"]})
    assert not (await fetch(registry, "https://mixed.example/")).ok and seen == []


@pytest.mark.parametrize("literal", ["https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/latest/meta-data/", "https://10.0.0.1/"])
async def test_an_ip_address_in_the_url_is_checked_too(literal):
    registry, seen = make(lambda r: httpx.Response(200, text="x", headers={"content-type": "text/plain"}))
    assert not (await fetch(registry, literal)).ok and seen == []


async def test_a_redirect_to_a_private_address_is_refused_after_the_first_request():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://internal.example/admin"})
    registry, seen = make(handler, {"internal.example": ["192.168.0.10"]})
    result = await fetch(registry, "https://example.com/")
    assert not result.ok and "not public" in result.error
    assert len(seen) == 1                                                        # the first hop only; the private target was never contacted


async def test_a_redirect_to_plain_http_is_refused():
    registry, seen = make(lambda r: httpx.Response(301, headers={"location": "http://example.com/"}))
    result = await fetch(registry, "https://example.com/")
    assert not result.ok and "https" in result.error and len(seen) == 1


async def test_a_relative_redirect_is_followed_and_at_most_three_are():
    hops = {"n": 0}

    def handler(request):
        hops["n"] += 1
        if hops["n"] <= 2:
            return httpx.Response(302, headers={"location": f"/step{hops['n']}"})
        return httpx.Response(200, text="arrived", headers={"content-type": "text/plain"})

    registry, _ = make(handler)
    result = await fetch(registry, "https://example.com/")
    assert result.ok and result.content == "arrived" and hops["n"] == 3

    registry, seen = make(lambda r: httpx.Response(302, headers={"location": "/again"}))
    result = await fetch(registry, "https://example.com/")
    assert not result.ok and "redirects" in result.error and len(seen) == 4        # the first request and three redirects, then it stops


async def test_output_is_cut_at_max_chars_and_marked():
    registry, _ = make(lambda r: httpx.Response(200, text="word " * 5000, headers={"content-type": "text/plain"}))
    result = await fetch(registry, "https://example.com/", max_chars=600)
    assert result.ok and len(result.content) <= 600 + len("\n[output truncated]") and result.truncated


async def test_non_text_and_error_responses_are_reported():
    registry, _ = make(lambda r: httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"}))
    assert "not a text page" in (await fetch(registry, "https://example.com/a.png")).error
    registry, _ = make(lambda r: httpx.Response(404, text="no"))
    assert "404" in (await fetch(registry, "https://example.com/missing")).error


async def test_the_private_content_guard_blocks_a_url_with_a_private_term():
    registry, seen = make(lambda r: httpx.Response(200, text="x", headers={"content-type": "text/plain"}))
    registry._guard = PrivateContentGuard(["Project-Nightjar"])
    result = await fetch(registry, "https://example.com/search?q=project-nightjar")
    assert not result.ok and "private" in result.error and seen == []


def test_html_to_text_drops_chrome():
    assert html_to_text("<body><header>top</header><main><p>one</p><p>two</p></main><aside>side</aside></body>") == "one\n\ntwo"
