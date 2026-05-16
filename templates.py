NEWS_TEMPLATE = """
🏢 *{company}* | {industry}

📰 *{title}*

{summary_ru}

🔗 [Читать источник]({url})

📅 {published_date} | Оценка релевантности: {score}/10
"""

_VENDOR_RULE = """СТРОГОЕ ПРАВИЛО — НАРУШАТЬ НЕЛЬЗЯ:
Никогда не упоминай названия ИИ-продуктов, платформ и вендоров: Microsoft Copilot, Copilot, ChatGPT, Gemini, Claude, ServiceNow, Salesforce Einstein, OpenAI, Google AI, AWS AI, Azure AI, IBM Watson и любые другие бренды ИИ-инструментов.
Вместо них всегда пиши: "ИИ-агент", "ИИ-решение" или "ИИ-инструмент".
Пример замены: "внедрила Microsoft 365 Copilot" → "внедрила ИИ-агентов"."""

POST_PROMPT = """{vendor_rule}

Напиши пост для Telegram-канала об ИИ в бизнесе на основе этой новости.

Структура поста:
- Эмодзи + броский заголовок (1 строка)
- Суть кейса: что за компания, какую задачу решили с помощью ИИ (2-3 предложения)
- Конкретный результат: цифры, проценты, факты (1-2 предложения)
- Вывод или инсайт для бизнеса (1 предложение)
- Хэштеги: #ИИвбизнесе #кейс #{industry}
- Ссылка на источник

Тон: деловой, конкретный, без воды. Длина: 800-1200 символов.
Новость: {title}
{text}"""

ARTICLE_PROMPT = """{vendor_rule}

Напиши структурированную статью-кейс об этом внедрении ИИ для публикации на платформе.

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА:

# {company}: краткая суть внедрения

## Контекст и задача
[Кто компания, какую проблему решали, почему обратились к ИИ]

## Решение
[Что именно внедрили — технология, инструменты, подход]

## Результаты
[Конкретные цифры и факты: ROI, экономия времени, рост выручки и т.д.]

## Что можно взять в свою практику
[3-5 конкретных вывода для других компаний]

## Теги
Компания: {company}
Отрасль: {industry}
Технология: [ИИ-агенты / LLM / Computer Vision / ML / другое]
Размер компании: [стартап / SMB / enterprise]

Источник: {url}

Тон: экспертный, структурированный. Объём: 600-900 слов.
Новость: {title}
{text}"""

REJECT_PATTERN_PROMPT = """Из комментария пользователя определи паттерн для фильтрации новостей.

URL новости: {url}
Комментарий пользователя: {comment}

Ответь ТОЛЬКО JSON без пояснений:
{{
  "type": "company | industry | topic | keyword",
  "value": "конкретное значение паттерна (название компании, отрасли, темы или ключевого слова)"
}}

Примеры:
- "эта компания не интересна" → {{"type": "company", "value": "название компании из URL"}}
- "слишком общо, нет конкретики" → {{"type": "keyword", "value": "общие слова без конкретики"}}
- "не интересует ритейл" → {{"type": "industry", "value": "retail"}}
"""

RELEVANCE_PROMPT = """Ты эксперт по корпоративному применению ИИ. Оцени эту новость по критериям.

Статья: {title}
Текст: {text}

Ответь ТОЛЬКО JSON без пояснений:
{{
  "score": 0,
  "is_real_case": false,
  "has_results": false,
  "company": null,
  "industry": null,
  "summary_ru": "краткое описание",
  "reject_reason": null
}}

Где:
- score: 0-10 (насколько релевантна для темы реального внедрения ИИ в бизнесе)
- is_real_case: есть ли реальный бизнес, не просто мнение эксперта
- has_results: есть ли конкретные результаты (%, числа, факты)
- company: название компании или null
- industry: отрасль или null
- summary_ru: 1-2 предложения о сути кейса на русском
- reject_reason: причина если score < 6, иначе null
"""


def format_news(news: dict) -> str:
    return NEWS_TEMPLATE.format(
        company=news.get("company") or "Неизвестная компания",
        industry=news.get("industry") or "Неизвестная отрасль",
        title=news.get("title", ""),
        summary_ru=news.get("summary_ru", ""),
        url=news.get("url", ""),
        published_date=news.get("published_date", "")[:10] if news.get("published_date") else "н/д",
        score=news.get("score", "?"),
    )


def split_message(text: str, max_len: int = 4096) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
