"""Web search for the Runtime Investigator: Tavily, then Brave, then keyless Tavily."""

import os

import httpx

from ..llm import Tool

LIMIT = 5
TAVILY = "https://api.tavily.com/search"
BRAVE = "https://api.search.brave.com/res/v1/web/search"


async def _tavily(client: httpx.AsyncClient, key: str | None, query: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {key}"} if key else {"X-Tavily-Access-Mode": "keyless"}
    response = await client.post(
        TAVILY,
        headers=headers,
        json={"query": query, "max_results": LIMIT},
    )
    response.raise_for_status()
    return [
        {"title": item["title"], "url": item["url"], "snippet": item["content"][:400]}
        for item in response.json()["results"][:LIMIT]
    ]


async def _brave(client: httpx.AsyncClient, key: str, query: str) -> list[dict]:
    response = await client.get(
        BRAVE, headers={"X-Subscription-Token": key}, params={"q": query, "count": LIMIT}
    )
    response.raise_for_status()
    return [
        {"title": item["title"], "url": item["url"], "snippet": item.get("description", "")[:400]}
        for item in response.json().get("web", {}).get("results", [])[:LIMIT]
    ]


async def web_search(query: str) -> list[dict] | dict:
    problems = []
    # Three tries must fit in the 20s tool limit.
    async with httpx.AsyncClient(timeout=6) as client:
        for name, variable, search in (
            ("Tavily", "TAVILY_API_KEY", _tavily),
            ("Brave", "BRAVE_API_KEY", _brave),
            # Last resort: rate-limited per IP address, so a key is always tried first.
            ("Keyless Tavily", None, _tavily),
        ):
            key = os.getenv(variable) if variable else None
            if variable and not key:
                continue
            try:
                if results := await search(client, key, query):
                    return results
                problems.append(f"{name} found nothing")
            except httpx.HTTPStatusError as error:
                problems.append(f"{name} answered {error.response.status_code}")
            except (httpx.RequestError, ValueError, KeyError, TypeError) as error:
                problems.append(f"{name} failed: {type(error).__name__}")
    return {"error": "; ".join(problems)}


SEARCH_TOOL = Tool(
    "web_search",
    "Search the web. Returns up to 5 results with title, url and snippet.",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    web_search,
)
