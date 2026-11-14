"""web_fetch: read a public web page as text.

The model chooses the URL, so this is the tool an attacker (through a page the model read, or a prompt) would aim at an internal address. The defences:
  - https only, port 443 only, no credentials in the URL;
  - the host is resolved here, every address it resolves to must be public, and the connection is made to that checked address (with the host name kept for TLS and the Host
    header), so the name cannot resolve to something else a moment later;
  - at most three redirects, each target checked the same way;
  - a ten second limit for the whole fetch, and a cap on bytes read.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Awaitable, Callable, List, Optional, Sequence
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult

MAX_REDIRECTS = 3
MAX_BYTES = 1_000_000
TIMEOUT_SECONDS = 10.0
TEXT_TYPES = ("text/", "application/xhtml+xml", "application/json", "application/xml")
SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "template", "iframe", "button", "select"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "li", "ul", "ol", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "dd", "dt"}


class FetchRefused(Exception):
    """The address or the redirect is not allowed. The message is safe to show the model."""


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


Resolver = Callable[[str], Awaitable[Sequence[str]]]


async def system_resolver(host: str) -> Sequence[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def check_url(url: str):
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchRefused("only https:// addresses are allowed")
    if not parts.hostname:
        raise FetchRefused("the address has no host")
    if parts.username or parts.password:
        raise FetchRefused("addresses with credentials are not allowed")
    try:
        port = parts.port
    except ValueError:
        raise FetchRefused("the address has an invalid port") from None
    if port not in (None, 443):
        raise FetchRefused("only the standard https port is allowed")
    return parts


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._parts: List[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS and not self._skip:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS and not self._skip:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in joined.split("\n"))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = parser.text()
    title = parser.title.strip()
    return f"Title: {title}\n\n{text}" if title else text


class WebFetchArgs(BaseModel):
    url: str = Field(description="An https:// address of a public web page.", max_length=2000)
    max_chars: int = Field(default=8000, ge=500, le=20000, description="The most characters of text to return.")


class WebFetch(Tool):
    name = "web_fetch"
    description = ("Fetch a public web page (https only) and return its readable text, without scripts, styles or navigation. Use it to read a page you found by search or "
                   "that the user named. It cannot reach private or internal addresses.")
    args_model = WebFetchArgs
    timeout_seconds = TIMEOUT_SECONDS
    outgoing = True

    def __init__(self, resolver: Resolver = system_resolver, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._resolver = resolver
        self._transport = transport

    def outgoing_text(self, args: WebFetchArgs) -> str:
        return args.url

    async def _address_for(self, host: str) -> str:
        try:
            ipaddress.ip_address(host)
            addresses: Sequence[str] = [host]
        except ValueError:
            try:
                addresses = await self._resolver(host)
            except OSError:
                raise FetchRefused("the host name could not be resolved") from None
        if not addresses:
            raise FetchRefused("the host name could not be resolved")
        if not all(is_public_ip(a) for a in addresses):
            raise FetchRefused("that address is private, internal or otherwise not public, so it is not fetched")
        return addresses[0]

    async def run(self, args: WebFetchArgs) -> ToolResult:
        url = args.url
        try:
            async with httpx.AsyncClient(transport=self._transport, follow_redirects=False, timeout=TIMEOUT_SECONDS) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    parts = check_url(url)
                    host = parts.hostname or ""
                    ip = await self._address_for(host)
                    target = parts._replace(netloc=f"[{ip}]" if ":" in ip else ip).geturl()
                    async with client.stream("GET", target, headers={"Host": host, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1", "User-Agent": "jaynes/0.1"},
                                             extensions={"sni_hostname": host}) as response:
                        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                            url = urljoin(url, response.headers["location"])
                            continue
                        if response.status_code >= 400:
                            return ToolResult.failure(f"the server answered {response.status_code}", url=url)
                        kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                        if not kind.startswith(TEXT_TYPES):
                            return ToolResult.failure(f"not a text page (content type {kind or 'unknown'})", url=url)
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) >= MAX_BYTES:
                                break
                        charset = response.charset_encoding or "utf-8"
                    raw = bytes(body[:MAX_BYTES]).decode(charset, errors="replace")
                    text = html_to_text(raw) if kind in ("text/html", "application/xhtml+xml") else raw.strip()
                    truncated = len(text) > args.max_chars or len(body) >= MAX_BYTES
                    text = text[: args.max_chars]
                    return ToolResult(ok=True, content=text, truncated=truncated, summary=f"read {urlsplit(url).netloc}{urlsplit(url).path[:40]} ({len(text)} characters)",
                                      meta={"url": url, "chars": len(text)})
                return ToolResult.failure(f"more than {MAX_REDIRECTS} redirects", url=url)
        except FetchRefused as refusal:
            return ToolResult.failure(f"refused: {refusal}", url=url[:300])
        except httpx.TimeoutException:
            return ToolResult.failure("the server did not answer in time", url=url[:300])
        except httpx.HTTPError as error:
            return ToolResult.failure(f"the request failed ({type(error).__name__})", url=url[:300])
