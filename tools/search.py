"""网页搜索工具。

实现一个 *最小化* 的、无需 API key 的搜索后端 —— 走 DuckDuckGo 的 HTML
端点。这样设计是有意为之：无需密钥就能跑、离线状态下能优雅失败、未来想
切换到付费后端只需重写 :meth:`WebSearchTool.fetch_html` 即可。
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
            "在公开网络上检索一段查询，返回前 k 条结果。"
            "每条结果包含 title / url / snippet 三个字段。"
        ),
        args_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的查询文本"},
                "k": {
                    "type": "integer",
                    "description": "最多返回多少条结果（默认 5，上限 20）。",
                },
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
            raise ToolError("query 不能为空")
        k = max(1, min(int(args.get("k", 5)), 20))

        html_text = self.fetch_html(query)
        results = _parse_duckduckgo_html(html_text, limit=k)
        return {"query": query, "results": results}

    # ------------------- 可被子类覆盖的切口 ------------------- #

    def fetch_html(self, query: str) -> str:
        """执行 HTTP 请求；任何失败都抛 :class:`ToolError`。"""
        try:
            import httpx  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ToolError("web_search 工具需要 httpx 依赖") from exc

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
            raise ToolError(f"web_search HTTP 调用失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# HTML 解析
# --------------------------------------------------------------------------- #

def _parse_duckduckgo_html(html_text: str, *, limit: int) -> List[Dict[str, str]]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ToolError("web_search 工具需要 beautifulsoup4 依赖") from exc

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
    """DuckDuckGo 的 HTML 经常把真实 URL 包在一个重定向链接里，剥掉它。"""
    m = re.search(r"uddg=([^&]+)", url)
    if not m:
        return url
    try:
        from urllib.parse import unquote

        return unquote(m.group(1))
    except Exception:  # pragma: no cover - 防御性
        return url


__all__ = ["WebSearchTool"]
