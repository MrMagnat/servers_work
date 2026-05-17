import asyncio
import hashlib
import logging
import time

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import llm
import memory
import templates
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, EXA_API_KEY, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

app: Application = None  # initialised in setup()

# short_id -> full url, kept in memory for button lookups
_url_registry: dict[str, str] = {}


def _short_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def resolve_url(short_id: str) -> str | None:
    return _url_registry.get(short_id)


def setup():
    global app
    # Restore URL registry from DB so old buttons keep working after restart
    for url in memory.get_all_sent_urls():
        _url_registry[_short_id(url)] = url

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("news", handle_news_command))
    app.add_handler(CommandHandler("status", handle_status_command))
    app.add_handler(CallbackQueryHandler(handle_reject, pattern=r"^reject\|"))
    app.add_handler(CallbackQueryHandler(handle_post, pattern=r"^post\|"))
    app.add_handler(CallbackQueryHandler(handle_article, pattern=r"^article\|"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    return app


async def send_news(news_item: dict):
    url = news_item["url"]
    sid = _short_id(url)
    _url_registry[sid] = url

    text = templates.format_news(news_item)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ Не актуально", callback_data=f"reject|{sid}"),
            InlineKeyboardButton("📢 Пост в ТГ", callback_data=f"post|{sid}"),
            InlineKeyboardButton("📄 Статья", callback_data=f"article|{sid}"),
        ]
    ])
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )
    await asyncio.sleep(2)


async def send_message(chat_id, text: str):
    await app.bot.send_message(chat_id=chat_id, text=text)


async def _safe_reply(message, text: str):
    """Send with Markdown, fall back to plain text if Telegram rejects the formatting."""
    try:
        await message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await message.reply_text(text)


async def _keep_typing(bot, chat_id: int, stop_event: asyncio.Event):
    """Repeatedly sends typing action every 4s until stop_event is set."""
    while not stop_event.is_set():
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=4.0)
        except asyncio.TimeoutError:
            pass


async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    sid = query.data.split("|", 1)[1]
    url = resolve_url(sid) or sid
    context.user_data["pending_reject_url"] = url
    await query.answer()
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await query.message.reply_text(
        "Напиши комментарий — что именно не подходит?\n"
        "(например: 'слишком общо', 'эта компания не интересна', 'не тот масштаб')"
    )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = context.user_data.get("pending_reject_url")
    if not url:
        return

    await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
    comment = update.message.text
    try:
        pattern = await llm.extract_reject_pattern(url, comment)
        memory.save_rejected(pattern["type"], pattern["value"], comment, url)
        memory.log_feedback(url, "rejected", comment=comment)
        await update.message.reply_text(
            f"✅ Запомнил. Буду фильтровать похожие новости.\n"
            f"Паттерн: {pattern['type']} → «{pattern['value']}»"
        )
    except Exception:
        logger.exception("Failed to process reject comment")
        await update.message.reply_text("⚠️ Не удалось обработать комментарий, попробуй снова.")
    finally:
        context.user_data.pop("pending_reject_url", None)


async def handle_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    sid = query.data.split("|", 1)[1]
    url = resolve_url(sid) or sid

    await query.answer("Генерирую пост...")
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await query.message.reply_text("⏳ Генерирую пост для канала...")

    news = memory.get_news_by_url(url)
    if not news:
        await query.message.reply_text("⚠️ Новость не найдена в базе.")
        return

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context.bot, query.message.chat_id, stop_typing))
    try:
        post_text, usage = await llm.generate_post(news)
        memory.log_feedback(url, "post_tg", generated=post_text)
        await _safe_reply(query.message, f"📢 *Готовый пост:*\n\n{post_text}")
        await query.message.reply_text(usage.format())
    except Exception:
        logger.exception("Failed to generate post for %s", url)
        await query.message.reply_text("⚠️ Ошибка при генерации поста.")
    finally:
        stop_typing.set()
        typing_task.cancel()


async def handle_article(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    sid = query.data.split("|", 1)[1]
    url = resolve_url(sid) or sid

    await query.answer("Генерирую статью...")
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await query.message.reply_text("⏳ Пишу статью-кейс, это займёт ~30 секунд...")

    news = memory.get_news_by_url(url)
    if not news:
        await query.message.reply_text("⚠️ Новость не найдена в базе.")
        return

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context.bot, query.message.chat_id, stop_typing))
    try:
        article_text, usage = await llm.generate_article(news)
        memory.log_feedback(url, "article", generated=article_text)
        for chunk in templates.split_message(article_text, 4096):
            await _safe_reply(query.message, chunk)
            await asyncio.sleep(0.5)
        await query.message.reply_text(usage.format())
    except Exception:
        logger.exception("Failed to generate article for %s", url)
        await query.message.reply_text("⚠️ Ошибка при генерации статьи.")
    finally:
        stop_typing.set()
        typing_task.cancel()


async def _check_url(client: httpx.AsyncClient, url: str, **kwargs) -> tuple[bool, int]:
    t = time.monotonic()
    try:
        r = await client.get(url, timeout=5.0, **kwargs)
        ms = int((time.monotonic() - t) * 1000)
        return r.status_code < 500, ms
    except Exception:
        return False, -1


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
        return

    await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
    msg = await update.message.reply_text("🔄 Проверяю сервисы...")

    async with httpx.AsyncClient() as client:
        tg_ok, tg_ms = await _check_url(client, "https://api.telegram.org")
        exa_ok, exa_ms = await _check_url(
            client,
            "https://api.exa.ai/search",
            headers={"x-api-key": EXA_API_KEY},
        )
        or_ok, or_ms = await _check_url(
            client,
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )

    def line(name, ok, ms):
        icon = "✅" if ok else "❌"
        ping = f"{ms} мс" if ms >= 0 else "недоступен"
        return f"{icon} {name}: {ping}"

    text = (
        "🖥 *Статус сервисов*\n\n"
        f"🟢 Сервер: работает\n"
        f"{line('Telegram API', tg_ok, tg_ms)}\n"
        f"{line('Exa', exa_ok, exa_ms)}\n"
        f"{line('OpenRouter', or_ok, or_ms)}"
    )

    await msg.edit_text(text, parse_mode="Markdown")


async def handle_news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # avoid circular import — scheduler imports bot, so we import here
    from scheduler import run_agent_cycle

    if str(update.effective_user.id) != str(TELEGRAM_CHAT_ID):
        return  # ignore requests from strangers

    await context.bot.send_chat_action(chat_id=update.message.chat_id, action=ChatAction.TYPING)
    await update.message.reply_text("🔍 Запускаю поиск новостей...")
    logger.info("Manual /news command triggered by user")
    asyncio.create_task(run_agent_cycle())
