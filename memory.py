import sqlite3
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "database", "agent.db")

logger = logging.getLogger(__name__)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sent_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                url_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                title_embedding TEXT,
                company TEXT,
                industry TEXT,
                text TEXT,
                summary_ru TEXT,
                score INTEGER,
                published_date TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT
            );

            CREATE TABLE IF NOT EXISTS rejected_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_value TEXT NOT NULL,
                user_comment TEXT,
                rejected_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                times_seen INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS feedback_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_url TEXT NOT NULL,
                action TEXT NOT NULL,
                user_comment TEXT,
                generated_content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    logger.info("Database initialized at %s", DB_PATH)


def save_sent_news(url: str, title: str, company: Optional[str], industry: Optional[str],
                   embedding=None, session_id: Optional[str] = None,
                   text: Optional[str] = None, summary_ru: Optional[str] = None,
                   score: Optional[int] = None, published_date: Optional[str] = None, **kwargs):
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    embedding_json = json.dumps(embedding) if embedding is not None else None
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO sent_news
                   (url, url_hash, title, title_embedding, company, industry,
                    text, summary_ru, score, published_date, session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (url, url_hash, title, embedding_json, company, industry,
                 text, summary_ru, score, published_date, session_id),
            )
    except Exception:
        logger.exception("Failed to save sent news: %s", url)


def is_duplicate_url(url: str) -> bool:
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_news WHERE url_hash = ? OR url = ?", (url_hash, url)
        ).fetchone()
    return row is not None


def is_duplicate_semantic(embedding: list, threshold: float = 0.92) -> bool:
    if not embedding:
        return False
    cutoff = datetime.utcnow() - timedelta(days=7)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT title_embedding FROM sent_news WHERE title_embedding IS NOT NULL AND sent_at >= ?",
            (cutoff.isoformat(),),
        ).fetchall()

    if not rows:
        return False

    vec = np.array(embedding, dtype=float)
    norm = np.linalg.norm(vec)
    if norm == 0:
        return False
    vec = vec / norm

    for row in rows:
        try:
            stored = np.array(json.loads(row["title_embedding"]), dtype=float)
            stored_norm = np.linalg.norm(stored)
            if stored_norm == 0:
                continue
            similarity = float(np.dot(vec, stored / stored_norm))
            if similarity > threshold:
                return True
        except Exception:
            continue
    return False


def save_rejected(pattern_type: str, pattern_value: str, comment: str, url: str):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, times_seen FROM rejected_patterns WHERE pattern_type = ? AND pattern_value = ?",
            (pattern_type, pattern_value),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE rejected_patterns SET times_seen = ?, user_comment = ? WHERE id = ?",
                (existing["times_seen"] + 1, comment, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO rejected_patterns (pattern_type, pattern_value, user_comment, rejected_url)
                   VALUES (?, ?, ?, ?)""",
                (pattern_type, pattern_value, comment, url),
            )


def get_rejected_patterns() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM rejected_patterns ORDER BY times_seen DESC").fetchall()
    return [dict(r) for r in rows]


def log_feedback(url: str, action: str, comment: Optional[str] = None, generated: Optional[str] = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback_log (news_url, action, user_comment, generated_content) VALUES (?, ?, ?, ?)",
            (url, action, comment, generated),
        )


def get_sent_urls_today() -> list[str]:
    today = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT url FROM sent_news WHERE DATE(sent_at) = ?", (today,)
        ).fetchall()
    return [r["url"] for r in rows]


def get_news_by_url(url: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sent_news WHERE url = ?", (url,)).fetchone()
    return dict(row) if row else None


def get_all_sent_urls() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT url FROM sent_news").fetchall()
    return [r["url"] for r in rows]
