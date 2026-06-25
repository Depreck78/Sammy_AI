from typing import Any, Dict, List
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from app.tooling import BaseTool


class WebSearchTool(BaseTool):
    name = "web_search"
    display_name = "Web Search"
    description = "Search the web with DuckDuckGo and fetch readable page text."
    icon = "Search"
    requires_auth = False

    def get_functions(self) -> List[Dict[str, Any]]:
        return [
            self.function(
                "web_search_search",
                "Search the web using DuckDuckGo.",
                {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["query"],
            ),
            self.function(
                "web_search_fetch_url",
                "Fetch a URL and extract readable text.",
                {"url": {"type": "string"}},
                ["url"],
            ),
        ]

    def _clean_duckduckgo_url(self, href: str) -> str:
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            return unquote(target) if target else href
        return href

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            if function_name == "web_search_search":
                query = parameters.get("query", "")
                limit = int(parameters.get("num_results") or 5)
                url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
                response = requests.get(url, timeout=12, headers={"User-Agent": "Sammy/1.0"})
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                results = []
                for result in soup.select(".result")[:limit]:
                    link = result.select_one(".result__a")
                    snippet = result.select_one(".result__snippet")
                    if not link:
                        continue
                    results.append(
                        {
                            "title": " ".join(link.get_text(" ").split()),
                            "url": self._clean_duckduckgo_url(link.get("href", "")),
                            "snippet": " ".join(snippet.get_text(" ").split()) if snippet else "",
                        }
                    )
                return "\n".join(
                    f"{idx + 1}. {item['title']}\n{item['url']}\n{item['snippet']}"
                    for idx, item in enumerate(results)
                ) or "No search results found."

            if function_name == "web_search_fetch_url":
                url = parameters.get("url", "")
                response = requests.get(url, timeout=12, headers={"User-Agent": "Sammy/1.0"})
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for tag in soup(["script", "style", "noscript", "svg"]):
                    tag.decompose()
                text = " ".join(soup.get_text(" ").split())
                title = soup.title.get_text(" ").strip() if soup.title else url
                return f"Title: {title}\nURL: {url}\n\n{text[:12000]}"
        except Exception as exc:
            return f"Web search tool error: {exc}"
        return f"Unknown web search function: {function_name}"
