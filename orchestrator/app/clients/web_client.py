import httpx

from ..config import settings


async def search(query: str, max_results: int = 5) -> list[dict]:
    """Run a web search against our self-hosted SearXNG instance (see
    orchestrator/docker-compose.yml and orchestrator/searxng/settings.yml)
    and return a short, cleaned-up list of results. This is the only place
    in the whole project that talks to the open internet.
    """
    async with httpx.AsyncClient(timeout=settings.web_timeout) as client:
        response = await client.get(
            f"{settings.searxng_url}/search",
            # format=json is required — SearXNG only returns an HTML page
            # by default, and we enabled the "json" format explicitly in
            # orchestrator/searxng/settings.yml.
            params={"q": query, "format": "json"},
        )
        response.raise_for_status()
        # SearXNG's JSON response has a "results" list among other fields
        # (suggestions, answers, etc) that we don't need here.
        results = response.json().get("results", [])

    # Trim each result down to just what we actually want to hand to the
    # LLM as context, and cap how many we keep — SearXNG can return dozens
    # of results per query, most of which would just be wasted context.
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in results[:max_results]
    ]
