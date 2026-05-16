import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import feedparser

from config import EXA_API_KEY

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "AI agent implementation real business case results 2024 2025",
    "artificial intelligence ROI enterprise deployment case study",
    "LLM automation company productivity gains measurable",
    "AI agents workflow automation business success story",
    "machine learning implementation manufacturing retail finance results",
    "generative AI business process automation case study",
    "AI customer service operations cost reduction results",
    "computer vision AI industrial automation success",
]

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=AI+agents+business+implementation&hl=ru&gl=RU",
    "https://news.google.com/rss/search?q=artificial+intelligence+enterprise+case+study&hl=en",
]


def _exa_client():
    from exa_py import Exa
    return Exa(api_key=EXA_API_KEY)


async def _search_exa_query(exa, query: str, start_date: str) -> list[dict]:
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: exa.search(
                query,
                type="auto",
                category="news",
                num_results=5,
                start_published_date=start_date,
                contents={"highlights": True},
            ),
        )
        items = []
        for r in result.results:
            highlights = getattr(r, "highlights", None) or []
            text = " ".join(highlights) if highlights else ""
            items.append({
                "url": r.url,
                "title": r.title or "",
                "text": text[:3000],
                "published_date": getattr(r, "published_date", "") or "",
                "source": "exa",
            })
        return items
    except Exception as e:
        logger.warning("Exa query failed (%s): %s", query[:50], e)
        return []


async def search_exa() -> list[dict]:
    start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")
    try:
        exa = _exa_client()
    except Exception as e:
        logger.error("Failed to init Exa client: %s", e)
        return []

    tasks = [_search_exa_query(exa, q, start_date) for q in SEARCH_QUERIES]
    results_nested = await asyncio.gather(*tasks)
    results = [item for sublist in results_nested for item in sublist]

    # deduplicate by URL within this batch
    seen = set()
    unique = []
    for item in results:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


def _fetch_rss_feed(url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:10]:
            items.append({
                "url": entry.get("link", ""),
                "title": entry.get("title", ""),
                "text": entry.get("summary", ""),
                "published_date": entry.get("published", ""),
                "source": "rss",
            })
        return items
    except Exception as e:
        logger.warning("RSS feed failed (%s): %s", url, e)
        return []


async def search_rss() -> list[dict]:
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, _fetch_rss_feed, url) for url in RSS_FEEDS]
    results_nested = await asyncio.gather(*tasks)
    return [item for sublist in results_nested for item in sublist]


async def search_all(queries: Optional[list[str]] = None) -> list[dict]:
    exa_results = await search_exa()
    logger.info("Exa returned %d results", len(exa_results))

    all_results = list(exa_results)

    if len(exa_results) < 10:
        logger.info("Exa results < 10, falling back to RSS")
        rss_results = await search_rss()
        logger.info("RSS returned %d results", len(rss_results))
        all_results.extend(rss_results)

    # final dedup by URL
    seen = set()
    unique = []
    for item in all_results:
        if item["url"] and item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    logger.info("Total unique results after dedup: %d", len(unique))
    return unique
