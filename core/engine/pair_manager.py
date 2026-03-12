"""Pair manager — orchestrates multiple symbol strategy instances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from core.data.binance_rest import BinanceRest
from core.data.binance_ws import BinanceWS
from core.engine.simulator import Simulator
from core.strategy.signals import Signal, SignalEngine

logger = logging.getLogger(__name__)

# Minimum candles required before generating signals
_MIN_CANDLES = 200


class PairInstance:
    """Holds candle buffer + strategy engine for a single symbol."""

    def __init__(self, symbol: str, config: dict) -> None:
        self.symbol = symbol
        self.signal_engine = SignalEngine(config)
        self.candles: list[dict[str, Any]] = []

    def add_candle(self, candle: dict[str, Any]) -> None:
        """Append a closed candle to the buffer."""
        self.candles.append(candle)
        # Keep buffer bounded
        if len(self.candles) > 1000:
            self.candles = self.candles[-800:]

    def get_dataframe(self) -> pd.DataFrame:
        if not self.candles:
            return pd.DataFrame()
        df = pd.DataFrame(self.candles)
        df["symbol"] = self.symbol
        return df

    def generate_signal(self) -> Signal | None:
        df = self.get_dataframe()
        if len(df) < _MIN_CANDLES:
            return None
        return self.signal_engine.process(df)


class PairManager:
    """Manages active pair subscriptions, WS streams, and strategy instances."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._rest = BinanceRest()
        self._simulator = Simulator(config)
        self._pairs: dict[str, PairInstance] = {}
        self._ws: BinanceWS | None = None
        self._interval = config["strategy"]["timeframe"]
        self._all_symbols: list[dict[str, Any]] = []
        # Callbacks for dashboard
        self.on_signal: Any = None
        self.on_trade: Any = None

    @property
    def simulator(self) -> Simulator:
        return self._simulator

    @property
    def active_pairs(self) -> list[str]:
        return list(self._pairs.keys())

    @property
    def all_symbols(self) -> list[dict[str, Any]]:
        return self._all_symbols

    async def initialize(self) -> None:
        """Fetch symbol list and start WS connection."""
        self._all_symbols = await self._rest.fetch_futures_symbols()
        self._ws = BinanceWS(on_candle=self._on_candle)
        await self._ws.connect()
        logger.info("PairManager initialized — %d symbols available", len(self._all_symbols))

    async def shutdown(self) -> None:
        if self._ws:
            await self._ws.close()
        await self._rest.close()

    async def add_pair(self, symbol: str) -> bool:
        """Add a pair: load historical candles, create strategy instance, subscribe WS."""
        symbol = symbol.upper()
        if symbol in self._pairs:
            return False

        # Load historical candles for buffer
        try:
            klines = await self._rest.fetch_klines(symbol, self._interval, limit=500)
        except Exception:
            logger.exception("Failed to fetch klines for %s", symbol)
            return False

        instance = PairInstance(symbol, self._config)
        for k in klines:
            instance.add_candle(k)

        self._pairs[symbol] = instance

        # Subscribe to WS stream
        if self._ws:
            await self._ws.subscribe(symbol, self._interval)

        logger.info("Added pair %s (%d historical candles)", symbol, len(klines))
        return True

    async def remove_pair(self, symbol: str) -> bool:
        """Remove a pair: unsubscribe WS, clean up."""
        symbol = symbol.upper()
        if symbol not in self._pairs:
            return False

        if self._ws:
            await self._ws.unsubscribe(symbol, self._interval)

        del self._pairs[symbol]
        logger.info("Removed pair %s", symbol)
        return True

    async def _on_candle(self, candle: dict[str, Any]) -> None:
        """Callback from WS — route candle to the correct pair instance."""
        symbol = candle.get("symbol", "")
        if symbol not in self._pairs:
            return

        instance = self._pairs[symbol]

        # Only process closed candles for signals
        if candle.get("is_closed"):
            instance.add_candle(candle)

            # Generate signal
            signal = instance.generate_signal()
            if signal:
                self._simulator.process_signal(signal)
                if self.on_signal:
                    self.on_signal(signal)
                logger.info("Signal: %s %s @ %.4f", signal.side, signal.symbol, signal.price)

        # Check TP/SL on every tick
        if self._simulator.has_position(symbol):
            trades = self._simulator.process_candle(
                symbol, candle["high"], candle["low"], candle.get("close_time", 0)
            )
            for t in trades:
                if self.on_trade:
                    self.on_trade(t)
                logger.info(
                    "Trade: %s %s %s PnL=%.2f USDT",
                    t.side, t.symbol, t.exit_reason, t.pnl_usdt,
                )

    def get_pair_status(self, symbol: str) -> dict[str, Any] | None:
        """Return status info for a pair (for dashboard)."""
        if symbol not in self._pairs:
            return None

        instance = self._pairs[symbol]
        df = instance.get_dataframe()
        last_price = df["close"].iloc[-1] if len(df) > 0 else 0.0

        pos = self._simulator.positions.get(symbol)
        pos_info = None
        if pos and pos.condition != 0.0:
            pnl_pct = 0.0
            if pos.side == "LONG":
                pnl_pct = (last_price - pos.entry_price) / pos.entry_price * 100
            else:
                pnl_pct = (pos.entry_price - last_price) / pos.entry_price * 100
            pos_info = {
                "side": pos.side,
                "entry_price": pos.entry_price,
                "tp1": pos.tp1_line,
                "tp2": pos.tp2_line,
                "tp3": pos.tp3_line,
                "sl": pos.sl_line,
                "unrealized_pnl_pct": round(pnl_pct, 4),
                "condition": pos.condition,
            }

        return {
            "symbol": symbol,
            "last_price": last_price,
            "candle_count": len(instance.candles),
            "position": pos_info,
        }
