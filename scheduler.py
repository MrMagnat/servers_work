import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import bot
import memory
import processor
import searcher
from config import TELEGRAM_CHAT_ID
from searcher import SEARCH_QUERIES

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


async def run_agent_cycle():
    session_id = datetime.now().strftime("%Y%m%d_%H%M")
    logger.info("Starting agent cycle: %s", session_id)

    try:
        await bot.send_message(TELEGRAM_CHAT_ID, "🔎 Ищу свежие новости об ИИ в бизнесе...")

        raw_results = await searcher.search_all(queries=SEARCH_QUERIES)
        await bot.send_message(
            TELEGRAM_CHAT_ID,
            f"📥 Найдено {len(raw_results)} статей — оцениваю релевантность и фильтрую дубли..."
        )

        filtered = await processor.process_batch(raw_results, session_id)

        if not filtered:
            await bot.send_message(
                TELEGRAM_CHAT_ID,
                "😔 Новых релевантных новостей не найдено.\n"
                "Все найденные статьи либо уже были отправлены ранее, либо не прошли фильтр качества."
            )
            return

        await bot.send_message(
            TELEGRAM_CHAT_ID,
            f"✅ Прошли фильтр: {len(filtered)} статей. Отправляю..."
        )

        sent_count = 0
        for news in filtered[:10]:
            try:
                await bot.send_news(news)
                memory.save_sent_news(
                    url=news["url"],
                    title=news["title"],
                    company=news.get("company"),
                    industry=news.get("industry"),
                    embedding=news.get("embedding"),
                    session_id=session_id,
                    text=news.get("text"),
                    summary_ru=news.get("summary_ru"),
                    score=news.get("score"),
                    published_date=news.get("published_date"),
                )
                sent_count += 1
            except Exception:
                logger.exception("Failed to send news: %s", news.get("url"))

        await bot.send_message(
            TELEGRAM_CHAT_ID,
            f"📊 Готово! Отправлено {sent_count} новостей."
        )
        logger.info("Cycle %s complete: sent %d news items", session_id, sent_count)

    except Exception:
        logger.exception("Agent cycle %s failed", session_id)
        await bot.send_message(TELEGRAM_CHAT_ID, "⚠️ Произошла ошибка во время цикла поиска. Смотри логи.")


def setup():
    scheduler.add_job(
        run_agent_cycle,
        trigger="cron",
        hour="8,12,16,20",
        minute=0,
        id="news_agent",
        replace_existing=True,
    )
    logger.info("Scheduler configured: runs at 08:00, 12:00, 16:00, 20:00 MSK")
    return scheduler
