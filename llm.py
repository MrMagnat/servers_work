import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

import templates
from config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, usage):
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

    def format(self) -> str:
        return (
            f"📊 Токены: {self.total:,} "
            f"(вход: {self.prompt_tokens:,} / выход: {self.completion_tokens:,})"
        )


# Счётчик для текущего цикла — сбрасывается в начале каждого цикла
_cycle_usage = TokenUsage()


def reset_cycle_usage():
    global _cycle_usage
    _cycle_usage = TokenUsage()


def get_cycle_usage() -> TokenUsage:
    return _cycle_usage


async def _chat(model: str, prompt: str, timeout: float = 30.0, retries: int = 3) -> tuple[str, TokenUsage]:
    for attempt in range(1, retries + 1):
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                ),
                timeout=timeout,
            )
            usage = TokenUsage()
            usage.add(response.usage)
            _cycle_usage.add(response.usage)
            return response.choices[0].message.content.strip(), usage
        except asyncio.TimeoutError:
            logger.warning("LLM timeout on attempt %d/%d (model=%s)", attempt, retries, model)
        except Exception as e:
            logger.warning("LLM error on attempt %d/%d (model=%s): %s", attempt, retries, model, e)
        if attempt < retries:
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed after {retries} attempts (model={model})")


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


async def score_relevance(title: str, text: str) -> Optional[dict]:
    prompt = templates.RELEVANCE_PROMPT.format(title=title, text=text[:800])
    try:
        raw, _ = await _chat("google/gemini-2.5-flash", prompt)
        return _parse_json(raw)
    except json.JSONDecodeError:
        logger.error("Invalid JSON from score_relevance, skipping article: %s", title[:60])
        return None
    except Exception:
        logger.exception("score_relevance failed for: %s", title[:60])
        return None


async def get_embedding(text: str) -> Optional[list[float]]:
    try:
        response = await asyncio.wait_for(
            client.embeddings.create(
                model="openai/text-embedding-3-small",
                input=text[:512],
            ),
            timeout=15.0,
        )
        return response.data[0].embedding
    except Exception:
        logger.warning("Failed to get embedding for text: %s", text[:60])
        return None


async def generate_post(news: dict) -> tuple[str, TokenUsage]:
    industry = news.get("industry") or "AI"
    prompt = templates.POST_PROMPT.format(
        vendor_rule=templates._VENDOR_RULE,
        title=news.get("title", ""),
        text=news.get("text", "")[:1500],
        industry=industry,
    )
    text, usage = await _chat("google/gemini-2.5-flash", prompt, timeout=45.0)
    return text, usage


async def generate_article(news: dict) -> tuple[str, TokenUsage]:
    prompt = templates.ARTICLE_PROMPT.format(
        vendor_rule=templates._VENDOR_RULE,
        company=news.get("company") or "Компания",
        industry=news.get("industry") or "Технологии",
        url=news.get("url", ""),
        title=news.get("title", ""),
        text=news.get("text", "")[:3000],
    )
    text, usage = await _chat("google/gemini-2.5-flash", prompt, timeout=90.0)
    return text, usage


async def extract_reject_pattern(url: str, comment: str) -> dict:
    prompt = templates.REJECT_PATTERN_PROMPT.format(url=url, comment=comment)
    try:
        raw, _ = await _chat("openai/gpt-4o-mini", prompt)
        return _parse_json(raw)
    except (json.JSONDecodeError, Exception):
        logger.exception("extract_reject_pattern failed, using fallback")
        return {"type": "keyword", "value": comment[:50]}
