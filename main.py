import argparse
import asyncio
import logging
import os
import signal
import sys
from logging.handlers import TimedRotatingFileHandler

PID_FILE = "/tmp/news_agent.pid"


def kill_existing_instance():
    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        os.kill(old_pid, signal.SIGTERM)
        import time
        time.sleep(2)
    except (ProcessLookupError, ValueError):
        pass
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

import memory
import bot
import scheduler as sched_module
from scheduler import run_agent_cycle


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = TimedRotatingFileHandler("agent.log", when="midnight", backupCount=7)
    fh.setFormatter(fmt)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(sh)


async def run_raw_mode():
    import searcher
    from searcher import SEARCH_QUERIES

    print("=== RAW EXA OUTPUT: first 5 results, no LLM ===\n")
    raw = await searcher.search_all(queries=SEARCH_QUERIES)
    print(f"Total from Exa: {len(raw)} articles\n")
    print("=" * 60)

    for i, item in enumerate(raw[:5], 1):
        print(f"\n[{i}] {item.get('title')}")
        print(f"  URL:      {item.get('url')}")
        print(f"  Date:     {item.get('published_date') or '—'}")
        print(f"  Source:   {item.get('source')}")
        print(f"  Text ({len(item.get('text', ''))} chars):")
        text = item.get("text", "").strip()
        print(f"    {text[:500]}{'...' if len(text) > 500 else ''}")
        print("-" * 60)


async def run_test_mode():
    import searcher
    import processor
    from searcher import SEARCH_QUERIES

    print("=== TEST MODE: running one cycle without Telegram ===\n")
    raw = await searcher.search_all(queries=SEARCH_QUERIES)
    print(f"Found {len(raw)} raw results\n")

    session_id = "test_run"
    filtered = await processor.process_batch(raw, session_id)
    print(f"\n--- {len(filtered)} articles passed filters ---\n")

    for i, item in enumerate(filtered, 1):
        print(f"{i}. [{item.get('score')}/10] {item.get('title')}")
        print(f"   Company: {item.get('company')} | Industry: {item.get('industry')}")
        print(f"   {item.get('summary_ru')}")
        print(f"   URL: {item.get('url')}\n")


async def main():
    logger = logging.getLogger("main")

    app = bot.setup()
    scheduler = sched_module.setup()

    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    scheduler.start()
    logger.info("Scheduler started")

    await app.initialize()
    await app.start()

    # Terminate any existing polling session before starting our own
    try:
        await app.bot.get_updates(offset=-1, timeout=0)
    except Exception:
        pass
    await asyncio.sleep(1)

    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started")

    await stop_event.wait()

    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass
    logger.info("Shutdown complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI News Monitoring Agent")
    parser.add_argument("--test", action="store_true", help="Run one cycle in test mode (no Telegram)")
    parser.add_argument("--raw", action="store_true", help="Show raw Exa results without LLM processing")
    args = parser.parse_args()

    setup_logging()
    kill_existing_instance()
    write_pid()
    memory.init_db()

    if args.raw:
        asyncio.run(run_raw_mode())
    elif args.test:
        asyncio.run(run_test_mode())
    else:
        asyncio.run(main())
