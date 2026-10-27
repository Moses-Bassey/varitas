from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.logging_config import RunTrace
from app.security import UnsafeUrlError, is_public_ip, validate_public_url
from app.services.http_client import Budget

USER_AGENT = "VERITAS-Forensics/0.1 (+fact-checking research; respects robots.txt)"
TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
MAX_REDIRECTS = 3


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self.skip_depth += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr", "section", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line), " ".join(parser.title.split())


async def _get_once(client: httpx.AsyncClient, url: str, budget: Budget, max_bytes: int, timeout: float) -> tuple[httpx.Response, bytes]:
    validate_public_url(url)
    per_try = budget.timeout_for(timeout)
    async with client.stream("GET", url, headers={"User-Agent": USER_AGENT, "Accept": ", ".join(TEXT_TYPES)}, timeout=per_try, follow_redirects=False) as response:
        stream = response.extensions.get("network_stream")
        peer = stream.get_extra_info("server_addr") if stream is not None else None
        if peer and not is_public_ip(peer[0]):
            raise FetchError("unsafe_url", "connection resolved to a non-public address")
        if response.is_redirect:
            return response, b""
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise FetchError("too_large", f"content exceeds {max_bytes} bytes")
            chunks.append(chunk)
        return response, b"".join(chunks)


async def _robots_allows(client: httpx.AsyncClient, url: str, budget: Budget) -> bool:
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response, body = await _get_once(client, robots_url, budget, 256 * 1024, 10.0)
    except (FetchError, UnsafeUrlError, httpx.HTTPError):
        return True
    if response.status_code >= 400 or response.is_redirect:
        return True
    parser = RobotFileParser()
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    return parser.can_fetch(USER_AGENT, url)


async def fetch_text(client: httpx.AsyncClient, url: str, budget: Budget, max_bytes: int, trace: RunTrace, timeout: float = 20.0) -> tuple[str, str]:
    """Fetch a user-supplied URL as text. Rejects private targets at every redirect hop and never executes content."""
    try:
        validate_public_url(url)
    except UnsafeUrlError as error:
        trace.emit("input_ingestion", "url_rejected", "failed", reason=str(error))
        raise FetchError("unsafe_url", str(error)) from error
    if not await _robots_allows(client, url, budget):
        trace.emit("input_ingestion", "url_rejected", "failed", reason="disallowed by robots.txt")
        raise FetchError("robots_disallowed", "the site's robots.txt disallows fetching this URL")
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            response, body = await _get_once(client, current, budget, max_bytes, timeout)
        except UnsafeUrlError as error:
            trace.emit("input_ingestion", "url_rejected", "failed", reason=str(error))
            raise FetchError("unsafe_url", str(error)) from error
        except httpx.TimeoutException as error:
            raise FetchError("timeout", "URL fetch timed out") from error
        except httpx.HTTPError as error:
            raise FetchError("unreachable", f"URL fetch failed ({type(error).__name__})") from error
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise FetchError("bad_redirect", "redirect without a location")
            current = urljoin(current, location)
            continue
        if response.status_code >= 400:
            raise FetchError("http_error", f"URL returned HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in TEXT_TYPES:
            raise FetchError("unsupported_type", f"content type '{content_type or 'unknown'}' is not supported; only HTML and plain text are fetched")
        text = body.decode(response.encoding or "utf-8", errors="replace")
        title = ""
        if content_type != "text/plain":
            text, title = html_to_text(text)
        trace.emit("input_ingestion", "url_fetched", final_url=current, content_type=content_type, bytes=len(body), chars=len(text))
        return text, title or current
    raise FetchError("too_many_redirects", f"more than {MAX_REDIRECTS} redirects")
