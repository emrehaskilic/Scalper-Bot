"""Scalper Bot — main entry point for the async engine."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from config import load_config
from core.engine.pair_manager import PairManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scalper-bot")


async def run() -> None:
    config = load_config()
    manager = PairManager(config)

    await manager.initialize()
    logger.info(
        "Scalper Bot started — %d symbols available. "
        "Run the dashboard with: streamlit run dashboard/app.py",
        len(manager.all_symbols),
    )

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        await manager.shutdown()
        logger.info("Scalper Bot stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
