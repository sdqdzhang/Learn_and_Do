"""Web search tool.

Implements a *minimal*, key-less search via the DuckDuckGo HTML
endpoint. This is intentionally conservative: it works without API
credentials, gracefully degrades when offline, and is easy to swap for
a paid backend later by overriding :meth:`WebSearchTool.fetch_html`.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

from core.exceptions import ToolError
from core.schema import ToolSpec
from tools.base import Tool

_DUCK_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_USER_AGENT = "Mozilla/5.0 (compatible; TinyDevin/1.0; +https://example.local)"


class WebSearchTool(Tool):
    spec = ToolSpec(
        name="web_search",
        description=(
            "Search the public web for a query and return the top results. "
            "Each result has 'title', 'url' and 'snippet'."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "description": "Max results (default 5, capped at 20)."},
            },
            "required": ["query"],
        },
    )

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    # ------------------- Tool API ------------------- #

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args["query"].strip()
        if not query:
            raise ToolError("query is empty")
        k = max(1, min(int(args.get("k", 5)), 20))

        html_text = self.fetch_html(query)
        results = _parse_duckduckgo_html(html_text, limit=k)
        return {"query": query, "results": results}

    # ------------------- Override-friendly seam ------------------- #

    def fetch_html(self, query: str) -> str:
        """Perform the HTTP request; raise :class:`ToolError` on any failure."""
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ToolError("httpx is required for web_search") from exc

        try:
            with httpx.Client(
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = client.post(_DUCK_HTML_ENDPOINT, data={"q": query})
                resp.raise_for_status()
                return resp.text
        except httpx.HTTPError as exc:
            raise ToolError(f"web_search HTTP failure: {exc}") from exc


# --------------------------------------------------------------------------- #
# HTML parsing
# --------------------------------------------------------------------------- #

def _parse_duckduckgo_html(html_text: str, *, limit: int) -> List[Dict[str, str]]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ToolError("beautifulsoup4 is required for web_search") from exc

    soup = BeautifulSoup(html_text, "html.parser")
    results: List[Dict[str, str]] = []

    for result in soup.select("div.result"):
        if len(results) >= limit:
            break
        link = result.select_one("a.result__a")
        snippet = result.select_one("a.result__snippet, div.result__snippet")
        if not link or not link.get("href"):
            continue
        results.append(
            {
                "title": html.unescape(link.get_text(strip=True)),
                "url": _strip_duck_redirect(link["href"]),
                "snippet": html.unescape(snippet.get_text(strip=True)) if snippet else "",
            }
        )

    return results


def _strip_duck_redirect(url: str) -> str:
    """DuckDuckGo HTML often wraps real URLs in a redirector; unwrap if so."""
    m = re.search(r"uddg=([^&]+)", url)
    if not m:
        return url
    try:
        from urllib.parse import unquote

        return unquote(m.group(1))
    except Exception:  # pragma: no cover - defensive
        return url


__all__ = ["WebSearchTool"]
