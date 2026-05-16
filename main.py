import argparse
import asyncio
import logging
import signal
import sys
from logging.handlers import TimedRotatingFileHandler

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

    memory.init_db()

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
    await app.updater.start_polling()
    logger.info("Telegram bot started")

    # Run first cycle immediately on startup
    logger.info("Running initial agent cycle on startup")
    asyncio.create_task(run_agent_cycle())

    await stop_event.wait()

    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI News Monitoring Agent")
    parser.add_argument("--test", action="store_true", help="Run one cycle in test mode (no Telegram)")
    args = parser.parse_args()

    setup_logging()
    memory.init_db()

    if args.test:
        asyncio.run(run_test_mode())
    else:
        asyncio.run(main())
