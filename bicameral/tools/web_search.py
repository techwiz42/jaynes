"""web_search: search the web through Tavily. Registered only when SEARCH_API_KEY is set. The query leaves this machine for the provider, so the private-content guard checks it."""
from __future__ import annotations

from typing import Optional

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult

ENDPOINT = "https://api.tavily.com/search"
SNIPPET_CHARS = 300


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="What to search for.")
    max_results: int = Field(default=5, ge=1, le=8, description="How many results to return.")


class WebSearch(Tool):
    name = "web_search"
    description = ("Search the web for current information. Returns titles, addresses and short snippets; use web_fetch to read a page in full. The query is sent to a search provider, "
                   "so never put private project names, repository names or file paths in it.")
    args_model = WebSearchArgs
    timeout_seconds = 15.0
    outgoing = True

    def __init__(self, api_key: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._api_key = api_key
        self._transport = transport

    def outgoing_text(self, args: WebSearchArgs) -> str:
        return args.query

    async def run(self, args: WebSearchArgs) -> ToolResult:
        body = {"query": args.query, "max_results": args.max_results, "search_depth": "basic", "include_answer": False, "include_raw_content": False}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=12.0) as client:
                response = await client.post(ENDPOINT, json=body, headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx.HTTPError as error:
            return ToolResult.failure(f"the search provider could not be reached ({type(error).__name__})")
        if response.status_code == 401:
            return ToolResult.failure("the search provider rejected the API key")
        if response.status_code in (429, 432, 433):
            return ToolResult.failure("the search provider's limit was reached; try again later")
        if response.status_code >= 400:
            return ToolResult.failure(f"the search provider answered {response.status_code}")
        try:
            results = response.json().get("results", [])
        except ValueError:
            return ToolResult.failure("the search provider's answer could not be read")
        lines = []
        for number, item in enumerate(results[: args.max_results], 1):
            snippet = " ".join(str(item.get("content", "")).split())[:SNIPPET_CHARS]
            lines.append(f"{number}. {str(item.get('title', '')).strip()[:200]}\n   {item.get('url', '')}\n   {snippet}")
        return ToolResult(ok=True, content="\n".join(lines) or "(no results)", summary=f"{len(lines)} results for “{args.query[:50]}”", meta={"query": args.query, "results": len(lines)})
