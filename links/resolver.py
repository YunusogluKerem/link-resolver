"""Following a link to its final URL.

Redirects are followed by hand (one request per hop) instead of with
`follow_redirects=True`, because per hop we need to:
  * keep the chain even when a later hop fails or times out,
  * limit concurrency per host (ty.gl -> adj.st -> trendyol.com are three hosts),
  * retry 429/503 with backoff,
  * avoid downloading the body of the final page.
"""

import asyncio
import html
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from links.models import LinkDetails

Status = LinkDetails.Status

# A normal browser's headers. `Accept-Language` matters: Hepsiburada's short
# link domain (app.hb.biz) answers 403 to any request without it.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

MAX_HOPS = 10
BLOCKED_CODES = {401, 403, 429}  # "you are a bot / slow down", not "page is broken"
RETRY_CODES = {429, 503}
MAX_RETRY_DELAY = 10.0

# For meta-refresh / JS redirects we only peek at the start of an HTML page.
PEEK_BYTES = 64 * 1024
# A JS redirect is trusted only on a tiny page (an interstitial). On a real
# page `location.href = ...` is usually a click handler, not a redirect.
SMALL_PAGE_BYTES = 10 * 1024
MAX_META_DELAY = 5.0  # news sites use <meta refresh content="300"> to reload themselves


@dataclass
class ResolveResult:
    status: str
    final_url: str  # empty when the very first request failed
    status_code: int | None
    chain: list[dict] = field(default_factory=list)


def build_client(timeout: float = 10.0, max_connections: int = 100) -> httpx.AsyncClient:
    """One shared client: connection pooling and HTTP/2 make repeated hosts cheap."""
    return httpx.AsyncClient(
        http2=True,
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(timeout),
        limits=httpx.Limits(max_connections=max_connections),
        follow_redirects=False,
    )


class LinkResolver:
    def __init__(self, client: httpx.AsyncClient, per_host: int = 8, retries: int = 2, backoff: float = 1.0):
        self.client = client
        self.retries = retries
        self.backoff = backoff
        # One semaphore per host, created on first use.
        self._host_limits: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))

    async def resolve(self, url: str) -> ResolveResult:
        """Never raises: every failure becomes a status so one bad link cannot stop the run."""
        chain: list[dict] = []
        try:
            request = self.client.build_request("GET", url)
            for _ in range(MAX_HOPS + 1):
                if request.url.scheme not in ("http", "https"):
                    if not chain:
                        raise ValueError(f"unsupported scheme: {request.url.scheme or 'none'}")
                    # Redirect into an app (whatsapp://, intent://): the web part ends here.
                    chain[-1]["app_link"] = str(request.url)
                    return _finished(chain)
                try:
                    response, page = await self._fetch(request)
                except httpx.TimeoutException as exc:
                    return _failed(Status.TIMEOUT, chain, request, exc)
                except httpx.HTTPError as exc:
                    return _failed(Status.ERROR, chain, request, exc)

                hop = {"url": str(response.url), "status": response.status_code}
                chain.append(hop)

                request = response.next_request  # set by httpx for 3xx + Location
                if request is None:
                    target = find_client_redirect(page, str(response.url))
                    if target is None or any(target == seen["url"] for seen in chain):
                        # The final page: keep what it calls itself (used by brand detection).
                        if site_name := find_site_name(page):
                            hop["site_name"] = site_name
                        return _finished(chain)
                    hop["client_redirect"] = True  # meta refresh / JS, not an HTTP redirect
                    request = self.client.build_request("GET", target)

            return _failed(Status.ERROR, chain, request, RuntimeError("too many redirects"))
        except Exception as exc:  # malformed URL, bad Location header, ...
            return _failed(Status.ERROR, chain, None, exc, url=url)

    async def _fetch(self, request: httpx.Request) -> tuple[httpx.Response, str]:
        """Send one request (retrying 429/503) and return the response plus a peek at its HTML."""
        for attempt in range(self.retries + 1):
            async with self._host_limits[request.url.host]:
                response = await self.client.send(request, stream=True)
                try:
                    page = await _peek_html(response)
                finally:
                    await response.aclose()  # drops the rest of the body
            if response.status_code not in RETRY_CODES or attempt == self.retries:
                return response, page
            # Sleep outside the semaphore so other links to this host keep moving.
            await asyncio.sleep(_retry_delay(response, attempt, self.backoff))
        raise AssertionError("unreachable")


async def _peek_html(response: httpx.Response) -> str:
    """Read at most PEEK_BYTES of a successful HTML page; nothing for anything else."""
    if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
        return ""
    data = b""
    async for chunk in response.aiter_bytes():
        data += chunk
        if len(data) >= PEEK_BYTES:
            break
    return data[:PEEK_BYTES].decode(response.encoding or "utf-8", errors="replace")


def _retry_delay(response: httpx.Response, attempt: int, backoff: float) -> float:
    """Honour Retry-After when the server sends seconds, else exponential backoff with jitter."""
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isdigit():
        return min(float(retry_after), MAX_RETRY_DELAY)
    return min(backoff * 2**attempt + random.uniform(0, backoff / 2), MAX_RETRY_DELAY)


def _finished(chain: list[dict]) -> ResolveResult:
    """Classify a chain whose last hop gave a final HTTP answer."""
    last = chain[-1]
    code = last["status"]
    followed = len(chain) > 1
    if code < 400:
        status = Status.OK
    elif code in BLOCKED_CODES:
        # After a redirect the final URL is known even if the page refuses us;
        # on the first request we learned nothing.
        status = Status.RESOLVED if followed else Status.BLOCKED
    else:
        status = Status.ERROR
    final_url = last["url"] if (status == Status.OK or followed) else ""
    return ResolveResult(status, final_url, code, chain)


def _failed(status, chain, request, exc, url: str = "") -> ResolveResult:
    """A request got no HTTP answer. The chain keeps the hop that failed and why."""
    failed_url = str(request.url) if request is not None else url
    followed = len(chain) > 0
    chain = [*chain, {"url": failed_url, "status": None, "error": f"{type(exc).__name__}: {exc}"[:200]}]
    return ResolveResult(status, failed_url if followed else "", None, chain)


# --- page metadata ----------------------------------------------------------

_OG_SITE_NAME = re.compile(r"<meta\b[^>]*\b(?:property|name)\s*=\s*[\"']og:site_name[\"'][^>]*>", re.I)
_CONTENT_ATTR = re.compile(r"\bcontent\s*=\s*([\"'])(.*?)\1", re.I | re.S)


def find_site_name(page: str) -> str:
    """Return the page's <meta property="og:site_name">, or "". Costs no extra request:
    it is read from the same first bytes that are checked for client-side redirects."""
    meta = _OG_SITE_NAME.search(page)
    content = _CONTENT_ATTR.search(meta.group(0)) if meta else None
    if not content:
        return ""
    return " ".join(html.unescape(content.group(2)).split())[:100]


# --- client-side redirects (best effort) ------------------------------------

_NOSCRIPT = re.compile(r"<noscript\b.*?</noscript>", re.I | re.S)
_META_REFRESH = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[\"']?[^>]*>", re.I)
_META_CONTENT = re.compile(r"content\s*=\s*[\"']\s*([\d.]+)\s*[;,]\s*url\s*=\s*[\"']?([^\"'>\s]+)", re.I)
_JS_REDIRECT = re.compile(
    r"location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']\s*(?:;|\n|</script>|$)"
    r"|location\.(?:replace|assign)\(\s*[\"']([^\"']+)[\"']\s*\)",
    re.I,
)


def find_client_redirect(page: str, base_url: str) -> str | None:
    """Return the absolute target of a meta-refresh or simple JS redirect, if the page has one."""
    if not page:
        return None
    # <noscript> fallbacks (instagram, x.com) are not what a browser would follow.
    page = _NOSCRIPT.sub("", page)

    target = None
    meta = _META_REFRESH.search(page)
    if meta:
        content = _META_CONTENT.search(meta.group(0))
        if content and float(content.group(1)) <= MAX_META_DELAY:
            target = content.group(2)
    if target is None and len(page) <= SMALL_PAGE_BYTES:
        js = _JS_REDIRECT.search(page)
        if js:
            target = js.group(1) or js.group(2)
    if not target:
        return None

    absolute = urljoin(base_url, html.unescape(target.strip()))
    if not absolute.startswith(("http://", "https://")):
        return None
    # A page that sends us back to itself with a new query string is a cookie or
    # bot check (zara.com: ?bm-verify=...), not a destination. Stay where we are.
    if urlsplit(absolute)[:3] == urlsplit(base_url)[:3]:
        return None
    return absolute
