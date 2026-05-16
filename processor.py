import logging
from typing import Optional

import memory
import llm

logger = logging.getLogger(__name__)


def _matches_rejected(item: dict, patterns: list[dict]) -> Optional[str]:
    title = item.get("title", "").lower()
    text = item.get("text", "").lower()
    for p in patterns:
        ptype = p.get("pattern_type", "")
        pval = p.get("pattern_value", "").lower()
        if not pval:
            continue
        if ptype == "company" and pval in title:
            return f"rejected company: {pval}"
        if ptype == "keyword" and (pval in title or pval in text):
            return f"rejected keyword: {pval}"
        if ptype == "industry" and (pval in title or pval in text):
            return f"rejected industry: {pval}"
        if ptype == "topic" and (pval in title or pval in text):
            return f"rejected topic: {pval}"
    return None


async def process_batch(raw_results: list[dict], session_id: str) -> list[dict]:
    rejected_patterns = memory.get_rejected_patterns()
    passed = []

    for item in raw_results:
        url = item.get("url", "")
        title = item.get("title", "")

        if not url or not title:
            continue

        # Step 1: URL deduplication
        if memory.is_duplicate_url(url):
            logger.debug("Duplicate URL, skipping: %s", url)
            continue

        # Step 2: Rejection pattern filter (fast, before LLM)
        reject_reason = _matches_rejected(item, rejected_patterns)
        if reject_reason:
            logger.debug("Pattern filter skip (%s): %s", reject_reason, url)
            continue

        # Step 3: LLM relevance scoring
        score_result = await llm.score_relevance(title, item.get("text", ""))
        if score_result is None:
            continue

        score = score_result.get("score", 0)
        is_real_case = score_result.get("is_real_case", False)

        if score < 6 or not is_real_case:
            logger.debug(
                "Low relevance (score=%s, real_case=%s): %s",
                score, is_real_case, title[:60],
            )
            continue

        # Step 4: Semantic deduplication
        embedding = await llm.get_embedding(title)
        if embedding and memory.is_duplicate_semantic(embedding):
            logger.debug("Semantic duplicate, skipping: %s", title[:60])
            continue

        enriched = {
            **item,
            "score": score,
            "is_real_case": is_real_case,
            "has_results": score_result.get("has_results", False),
            "company": score_result.get("company"),
            "industry": score_result.get("industry"),
            "summary_ru": score_result.get("summary_ru", ""),
            "embedding": embedding,
            "session_id": session_id,
        }
        passed.append(enriched)
        logger.info("Passed relevance filter (score=%s): %s", score, title[:60])

    # Step 5: Sort by score desc, cap at 10
    passed.sort(key=lambda x: x.get("score", 0), reverse=True)
    result = passed[:10]
    logger.info("Processing complete: %d/%d articles passed", len(result), len(raw_results))
    return result
