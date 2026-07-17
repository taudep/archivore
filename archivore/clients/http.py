"""Shared HTTP plumbing: a requests session with browser headers and a
TLS 1.2 floor (some hosts mis-negotiate TLS 1.0 and abort the handshake),
plus og:/twitter: meta-tag extraction used by the scraping clients."""

import html
import re
import ssl
from functools import lru_cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class _TLS12Adapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


@lru_cache(maxsize=1)
def session() -> requests.Session:
    """Return the shared session (browser headers, TLS 1.2 minimum)."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.mount("https://", _TLS12Adapter())
    return s


def get_text(url: str, timeout: float = 15) -> str:
    """GET a URL and return its body as text, raising on HTTP errors."""
    resp = session().get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def aiohttp_ssl_ctx() -> ssl.SSLContext:
    """SSL context with a TLS 1.2 floor for aiohttp downloads."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def extract_og(html_text: str, tag: str) -> str | None:
    """Extract an ``og:<tag>`` or ``twitter:<tag>`` meta tag value."""
    for attr in (f"og:{tag}", f"twitter:{tag}"):
        pattern_a = (
            rf'<meta[^>]+(?:property|name)="{re.escape(attr)}"'
            rf'[^>]+content="([^"]*)"'
        )
        pattern_b = (
            rf'<meta[^>]+content="([^"]*)"'
            rf'[^>]+(?:property|name)="{re.escape(attr)}"'
        )
        m = re.search(pattern_a, html_text, re.I) or re.search(
            pattern_b, html_text, re.I
        )
        if m:
            return html.unescape(m.group(1).strip())
    return None
