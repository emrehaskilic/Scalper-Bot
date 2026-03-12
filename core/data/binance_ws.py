"""Binance Futures WebSocket — combined kline + bookTicker streams with dynamic subscribe/unsubscribe."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

_WS_BASE = "wss://fstream.binance.com/ws"

OnCandleCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
OnBookTickerCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class BinanceWS:
    """Manages a single WS connection with dynamic stream subscriptions."""

    def __init__(
        self,
        on_candle: OnCandleCallback,
        on_book_ticker: OnBookTickerCallback | None = None,
    ) -> None:
        self._on_candle = on_candle
        self._on_book_ticker = on_book_ticker
        self._ws: ClientConnection | None = None
        self._subscriptions: set[str] = set()
        self._running = False
        self._req_id = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WS connection and start the read loop."""
        self._running = True
        asyncio.create_task(self._run_loop())

    async def close(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _run_loop(self) -> None:
        """Auto-reconnect read loop."""
        while self._running:
            try:
                async with websockets.connect(_WS_BASE, ping_interval=20) as ws:
                    self._ws = ws
                    # Re-subscribe on reconnect
                    if self._subscriptions:
                        await self._send_subscribe(list(self._subscriptions))
                    logger.info("WS connected to %s", _WS_BASE)
                    async for raw_msg in ws:
                        await self._handle_message(raw_msg)
            except websockets.ConnectionClosed:
                logger.warning("WS connection closed, reconnecting in 3s...")
                await asyncio.sleep(3)
            except Exception:
                logger.exception("WS error, reconnecting in 5s...")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Subscribe / Unsubscribe
    # ------------------------------------------------------------------

    async def subscribe(self, symbol: str, interval: str = "15m") -> None:
        """Subscribe to a kline stream for the given symbol."""
        stream = f"{symbol.lower()}@kline_{interval}"
        async with self._lock:
            if stream in self._subscriptions:
                return
            self._subscriptions.add(stream)
            if self._ws:
                await self._send_subscribe([stream])
        logger.info("Subscribed: %s", stream)

    async def unsubscribe(self, symbol: str, interval: str = "15m") -> None:
        """Unsubscribe from a kline stream."""
        stream = f"{symbol.lower()}@kline_{interval}"
        async with self._lock:
            self._subscriptions.discard(stream)
            if self._ws:
                await self._send_unsubscribe([stream])
        logger.info("Unsubscribed: %s", stream)

    async def subscribe_book_ticker(self, symbol: str) -> None:
        """Subscribe to bookTicker stream for real-time bid/ask."""
        stream = f"{symbol.lower()}@bookTicker"
        async with self._lock:
            if stream in self._subscriptions:
                return
            self._subscriptions.add(stream)
            if self._ws:
                await self._send_subscribe([stream])
        logger.info("Subscribed bookTicker: %s", stream)

    async def unsubscribe_book_ticker(self, symbol: str) -> None:
        """Unsubscribe from bookTicker stream."""
        stream = f"{symbol.lower()}@bookTicker"
        async with self._lock:
            self._subscriptions.discard(stream)
            if self._ws:
                await self._send_unsubscribe([stream])
        logger.info("Unsubscribed bookTicker: %s", stream)

    async def _send_subscribe(self, streams: list[str]) -> None:
        self._req_id += 1
        msg = {"method": "SUBSCRIBE", "params": streams, "id": self._req_id}
        await self._ws.send(json.dumps(msg))

    async def _send_unsubscribe(self, streams: list[str]) -> None:
        self._req_id += 1
        msg = {"method": "UNSUBSCRIBE", "params": streams, "id": self._req_id}
        await self._ws.send(json.dumps(msg))

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_message(self, raw: str) -> None:
        data = json.loads(raw)

        # Skip subscription confirmations
        if "result" in data or "id" in data:
            return

        event_type = data.get("e")

        # Kline event
        if event_type == "kline":
            k = data["k"]
            candle = {
                "symbol": data["s"],
                "interval": k["i"],
                "open_time": k["t"],
                "close_time": k["T"],
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "is_closed": k["x"],
            }
            await self._on_candle(candle)

        # BookTicker event — real-time best bid/ask
        elif event_type == "bookTicker" and self._on_book_ticker:
            ticker = {
                "symbol": data["s"],
                "bid": float(data["b"]),
                "ask": float(data["a"]),
                "bid_qty": float(data["B"]),
                "ask_qty": float(data["A"]),
                "time": data.get("T", 0),
            }
            await self._on_book_ticker(ticker)
