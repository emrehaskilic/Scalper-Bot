"""FastAPI backend — REST API + WS status for the Scalper Bot dashboard."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config
from core.data.binance_rest import BinanceRest
from core.data.binance_ws import BinanceWS
from core.strategy.signals import SignalEngine
from core.engine.simulator import Simulator, Trade
from core.strategy.risk_manager import RiskManager

import pandas as pd
import asyncio
import json
import logging
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI(title="Scalper Bot API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global State ──
state: dict[str, Any] = {
    "config": load_config(),
    "rest": BinanceRest(),
    "simulator": None,
    "bot_running": False,
    "active_symbols": [],
    "scan_results": {},
    "signal_log": [],
    "ws_connected": False,
    "ws_last_ping": 0,
}

# ── WebSocket BookTicker — real-time bid/ask fed by Binance WS ──
_ws_book_data: dict[str, dict[str, float]] = {}  # {SYMBOL: {bid, ask, bid_qty, ask_qty, time}}
_ws_book_lock = threading.Lock()
_ws_instance: BinanceWS | None = None
_ws_loop: asyncio.AbstractEventLoop | None = None


async def _on_candle_noop(candle: dict) -> None:
    """Placeholder — kline handling not used in this WS instance."""
    pass


async def _on_book_ticker(ticker: dict) -> None:
    """Update in-memory book ticker cache from WS stream."""
    with _ws_book_lock:
        _ws_book_data[ticker["symbol"]] = ticker
    state["ws_connected"] = True
    state["ws_last_ping"] = time.time()


def _start_ws_loop(symbols: list[str]) -> None:
    """Start the WS event loop in a background thread."""
    global _ws_instance, _ws_loop

    loop = asyncio.new_event_loop()
    _ws_loop = loop

    async def _run():
        global _ws_instance
        ws = BinanceWS(on_candle=_on_candle_noop, on_book_ticker=_on_book_ticker)
        _ws_instance = ws
        await ws.connect()
        for sym in symbols:
            await ws.subscribe_book_ticker(sym)
        logger.info("WS bookTicker subscribed for %d symbols", len(symbols))
        # Keep loop alive
        while state["bot_running"]:
            await asyncio.sleep(1)
        try:
            await ws.close()
        except Exception:
            pass
        _ws_instance = None

    loop.run_until_complete(_run())
    loop.close()


def _stop_ws() -> None:
    """Signal WS loop to stop."""
    global _ws_instance, _ws_loop
    _ws_book_data.clear()
    _ws_instance = None
    _ws_loop = None


# ── Periodic Signal Scanner — detects new crossovers while bot is running ──

_TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


def _signal_scanner_loop() -> None:
    """Background thread: re-check crossovers every candle close."""
    logger.info("Signal scanner started")
    last_scan_bucket = 0

    while state["bot_running"]:
        time.sleep(5)  # check every 5s if a new candle closed

        if not state["bot_running"] or not state["active_symbols"]:
            continue

        cfg = state["config"]
        tf = cfg["strategy"]["timeframe"]
        interval_s = _TIMEFRAME_SECONDS.get(tf, 900)

        # Only scan when a new candle bucket starts
        now = int(time.time())
        current_bucket = now // interval_s
        if current_bucket == last_scan_bucket:
            continue
        last_scan_bucket = current_bucket

        # Wait a few seconds for candle to finalize on Binance
        time.sleep(10)

        logger.info("Signal scanner: new %s candle — rescanning %d symbols",
                     tf, len(state["active_symbols"]))

        sim = state["simulator"]
        if not sim:
            continue

        rest: BinanceRest = state["rest"]

        for sym in list(state["active_symbols"]):
            if not state["bot_running"]:
                break
            try:
                klines = rest.fetch_klines_sync(sym, tf, limit=1500)
                if len(klines) < 200:
                    continue

                df = pd.DataFrame(klines)
                df["symbol"] = sym

                engine = SignalEngine(cfg)
                signal = engine.process(df)

                if signal:
                    # Check if we need to reverse or open a new position
                    has_pos = sim.has_position(sym)
                    if has_pos:
                        existing = sim.positions[sym]
                        if existing.side == signal.side:
                            continue  # same direction — skip
                    # New signal or reversal
                    reversal_trades = sim.process_signal(signal)
                    for rt in reversal_trades:
                        state["signal_log"].append({
                            "time": time.strftime("%H:%M:%S"),
                            "symbol": sym,
                            "side": rt.side,
                            "price": rt.exit_price,
                            "rsi": 0,
                            "source": f"EXIT_{rt.exit_reason}",
                        })
                    state["signal_log"].append({
                        "time": time.strftime("%H:%M:%S"),
                        "symbol": sym,
                        "side": signal.side,
                        "price": signal.price,
                        "rsi": round(signal.rsi_value, 2),
                        "source": "LIVE_SCAN",
                    })
                    logger.info("Signal scanner: %s %s @ %.4f",
                                sym, signal.side, signal.price)

                    # Update scan_results
                    state["scan_results"][sym] = {
                        "status": "signal",
                        "side": signal.side,
                        "price": signal.price,
                        "rsi": round(signal.rsi_value, 2),
                        "atr": round(signal.atr_value, 4),
                        "last_price": float(df["close"].iloc[-1]),
                    }

            except Exception as e:
                logger.error("Signal scanner error for %s: %s", sym, str(e)[:100])


def _get_sim() -> Simulator:
    if state["simulator"] is None:
        state["simulator"] = Simulator(state["config"])
    return state["simulator"]


# ── REST Endpoints ──

@app.get("/api/symbols")
def get_symbols():
    """Return all Binance Futures USDT-M perpetual symbols."""
    rest: BinanceRest = state["rest"]
    symbols = rest.fetch_futures_symbols_sync()
    return {"symbols": [s["symbol"] for s in symbols], "count": len(symbols)}


@app.get("/api/config")
def get_config():
    return state["config"]


@app.post("/api/config")
def update_config(body: dict):
    """Update config from frontend."""
    cfg = state["config"]
    if "trading" in body:
        cfg["trading"].update(body["trading"])
    if "strategy" in body:
        cfg["strategy"].update(body["strategy"])
    if "risk" in body:
        cfg["risk"].update(body["risk"])
    # Reset simulator with new config
    state["simulator"] = Simulator(cfg)
    return {"status": "ok"}


@app.post("/api/bot/start")
def start_bot(body: dict):
    """Start bot: run initial scan on selected pairs."""
    symbols = body.get("symbols", [])
    if not symbols:
        return {"error": "No symbols provided"}

    state["active_symbols"] = symbols
    state["bot_running"] = True
    state["ws_connected"] = True
    state["ws_last_ping"] = time.time()

    # Reset simulator
    cfg = state["config"]
    state["simulator"] = Simulator(cfg)
    sim = _get_sim()

    rest: BinanceRest = state["rest"]
    scan_results = {}
    signal_log = []

    for sym in symbols:
        try:
            klines = rest.fetch_klines_sync(sym, cfg["strategy"]["timeframe"], limit=1500)
            if len(klines) < 200:
                scan_results[sym] = {
                    "status": "insufficient_data",
                    "candles": len(klines),
                    "last_price": klines[-1]["close"] if klines else 0,
                }
                continue

            df = pd.DataFrame(klines)
            df["symbol"] = sym

            engine = SignalEngine(cfg)
            signal = engine.process_backfill(df)

            last_price = float(df["close"].iloc[-1])

            if signal:
                reversal_trades = sim.process_signal(signal)
                for rt in reversal_trades:
                    signal_log.append({
                        "time": time.strftime("%H:%M:%S"),
                        "symbol": sym,
                        "side": rt.side,
                        "price": rt.exit_price,
                        "rsi": 0,
                        "source": f"EXIT_{rt.exit_reason}",
                    })
                entry = {
                    "time": time.strftime("%H:%M:%S"),
                    "symbol": sym,
                    "side": signal.side,
                    "price": signal.price,
                    "rsi": round(signal.rsi_value, 2),
                    "source": "INITIAL_SCAN",
                }
                signal_log.append(entry)
                scan_results[sym] = {
                    "status": "signal",
                    "side": signal.side,
                    "price": signal.price,
                    "rsi": round(signal.rsi_value, 2),
                    "atr": round(signal.atr_value, 4),
                    "last_price": last_price,
                }
            else:
                from core.strategy.indicators import variant, rsi as calc_rsi
                close_ma = variant(
                    cfg["strategy"]["ma_type"], df["close"],
                    cfg["strategy"]["ma_period"],
                    cfg["strategy"]["alma_sigma"],
                    cfg["strategy"]["alma_offset"],
                )
                open_ma = variant(
                    cfg["strategy"]["ma_type"], df["open"],
                    cfg["strategy"]["ma_period"],
                    cfg["strategy"]["alma_sigma"],
                    cfg["strategy"]["alma_offset"],
                )
                trend = "BULLISH" if close_ma.iloc[-1] > open_ma.iloc[-1] else "BEARISH"
                rsi_val = calc_rsi(df["close"], 28).iloc[-1]
                scan_results[sym] = {
                    "status": "monitoring",
                    "trend": trend,
                    "last_price": last_price,
                    "rsi": round(float(rsi_val), 2),
                }
        except Exception as e:
            scan_results[sym] = {"status": "error", "message": str(e)[:100], "last_price": 0}

    state["scan_results"] = scan_results
    state["signal_log"] = signal_log

    # Start WS bookTicker stream in background thread
    _ws_book_data.clear()
    ws_thread = threading.Thread(target=_start_ws_loop, args=(symbols,), daemon=True)
    ws_thread.start()

    # Start periodic signal scanner in background thread
    scanner_thread = threading.Thread(target=_signal_scanner_loop, daemon=True)
    scanner_thread.start()

    return {
        "status": "started",
        "pairs": len(symbols),
        "immediate_signals": len(signal_log),
        "scan_results": scan_results,
    }


@app.post("/api/bot/stop")
def stop_bot():
    state["bot_running"] = False
    state["ws_connected"] = False
    state["active_symbols"] = []
    state["scan_results"] = {}
    state["simulator"] = None
    state["signal_log"] = []
    _stop_ws()
    return {"status": "stopped"}


# Orderbook: WS-fed real-time data with REST fallback
_rest_orderbook_cache: dict[str, Any] = {"data": {}, "ts": 0}
_REST_ORDERBOOK_CACHE_TTL = 5  # seconds — only used as fallback now


def _get_orderbook(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Get live bid/ask — prefers WS bookTicker, falls back to REST."""
    # Try WS data first (real-time, no latency)
    with _ws_book_lock:
        ws_data = {sym: _ws_book_data[sym] for sym in symbols if sym in _ws_book_data}

    if len(ws_data) == len(symbols):
        return ws_data

    # Partial or no WS data — fill gaps from REST
    now = time.time()
    missing = [s for s in symbols if s not in ws_data]
    if missing:
        if now - _rest_orderbook_cache["ts"] >= _REST_ORDERBOOK_CACHE_TTL or not _rest_orderbook_cache["data"]:
            try:
                rest: BinanceRest = state["rest"]
                book = rest.fetch_book_tickers_sync(symbols)
                _rest_orderbook_cache["data"] = book
                _rest_orderbook_cache["ts"] = now
            except Exception as e:
                logger.error("REST orderbook fallback failed: %s", e)

        rest_data = _rest_orderbook_cache.get("data", {})
        for sym in missing:
            if sym in rest_data:
                ws_data[sym] = rest_data[sym]

    return ws_data


def _mark_price_from_book(book_entry: dict[str, float], side: str) -> float:
    """Realistic mark price: LONG uses ask (you buy at ask), SHORT uses bid (you sell at bid)."""
    if side == "LONG":
        return book_entry["ask"]
    else:
        return book_entry["bid"]


@app.get("/api/status")
def get_status():
    """Main polling endpoint — returns full dashboard state with LIVE prices."""
    sim = state["simulator"]
    cfg = state["config"]

    # Wallet / stats
    stats = sim.get_stats() if sim else {
        "initial_balance": cfg["trading"]["initial_balance"],
        "current_balance": cfg["trading"]["initial_balance"],
        "total_pnl": 0, "total_pnl_pct": 0,
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "win_rate": 0, "total_fees": 0, "leverage": cfg["trading"]["leverage"],
    }

    # Fetch LIVE orderbook (bid/ask) — WS bookTicker preferred, REST fallback
    orderbook = {}
    live_prices = {}
    ws_symbols_count = 0
    if state["bot_running"] and state["active_symbols"]:
        orderbook = _get_orderbook(state["active_symbols"])
        # Count how many symbols come from WS vs REST
        with _ws_book_lock:
            ws_symbols_count = sum(1 for s in state["active_symbols"] if s in _ws_book_data)
        # Derive mid prices for backward compat
        for sym, ob in orderbook.items():
            live_prices[sym] = (ob["bid"] + ob["ask"]) / 2
        state["ws_connected"] = len(orderbook) > 0
        state["ws_last_ping"] = time.time()

    # Positions with LIVE PnL breakdown
    positions = []
    margin = cfg["trading"]["margin_per_trade"]
    leverage = cfg["trading"]["leverage"]
    fee_rate = cfg["trading"]["fee_rate"]

    if sim:
        # IMPORTANT: iterate over a snapshot — process_candle may delete from the dict
        position_snapshot = list(sim.positions.items())
        for sym, pos in position_snapshot:
            if pos.condition == 0.0:
                continue

            # LIVE mark price from orderbook (realistic: LONG=ask, SHORT=bid)
            ob = orderbook.get(sym)
            if ob:
                mark_price = _mark_price_from_book(ob, pos.side)
                bid = ob["bid"]
                ask = ob["ask"]
                spread = ask - bid
            else:
                mark_price = state["scan_results"].get(sym, {}).get("last_price", pos.entry_price)
                bid = mark_price
                ask = mark_price
                spread = 0.0

            # TP/SL check on live price FIRST (before PnL calc)
            exit_trades = sim.process_candle(sym, mark_price, mark_price, int(time.time() * 1000))
            for t in exit_trades:
                state["signal_log"].append({
                    "time": time.strftime("%H:%M:%S"),
                    "symbol": sym,
                    "side": t.side,
                    "price": t.exit_price,
                    "rsi": 0,
                    "source": f"EXIT_{t.exit_reason}",
                })

            # If position fully closed, skip adding to open positions
            if sym not in sim.positions or sim.positions[sym].condition == 0.0:
                continue

            # Re-read pos after possible partial exits (remaining_qty may have changed)
            pos = sim.positions[sym]

            # Notional size
            notional = margin * leverage
            position_notional = notional * pos.remaining_qty

            # Unrealized PnL (LIVE)
            if pos.side == "LONG":
                upnl_pct = (mark_price - pos.entry_price) / pos.entry_price * 100
            else:
                upnl_pct = (pos.entry_price - mark_price) / pos.entry_price * 100
            upnl_usdt = position_notional * upnl_pct / 100

            # Break-even price (entry + entry fee + exit fee estimate)
            total_fee_pct = fee_rate * 2  # entry + exit
            if pos.side == "LONG":
                break_even = pos.entry_price * (1 + total_fee_pct)
            else:
                break_even = pos.entry_price * (1 - total_fee_pct)

            # Realized PnL from partial exits
            realized = sum(t.pnl_usdt for t in sim.trades if t.symbol == sym)
            realized_fees = sum(t.fee_usdt for t in sim.trades if t.symbol == sym)

            # Entry fee for this position (always paid on open)
            full_notional = margin * leverage
            entry_fee = full_notional * fee_rate

            # Total fees = entry fee + any exit fees from partial TPs
            total_fees_for_pos = entry_fee + realized_fees

            positions.append({
                "symbol": sym,
                "side": pos.side,
                "entry_price": pos.entry_price,
                "mark_price": round(mark_price, 4),
                "bid": round(bid, 4),
                "ask": round(ask, 4),
                "spread": round(spread, 6),
                "break_even": round(break_even, 4),
                "notional_usdt": round(position_notional, 2),
                "tp1": pos.tp1_line,
                "tp2": pos.tp2_line,
                "tp3": pos.tp3_line,
                "sl": pos.sl_line,
                "condition": pos.condition,
                "remaining_qty": pos.remaining_qty,
                "unrealized_pnl_usdt": round(upnl_usdt, 4),
                "unrealized_pnl_pct": round(upnl_pct, 4),
                "realized_pnl_usdt": round(realized, 4),
                "total_pnl_usdt": round(upnl_usdt + realized, 4),
                "fees_usdt": round(total_fees_for_pos, 4),
            })

    # Refresh stats after possible TP/SL exits
    if sim:
        stats = sim.get_stats()

    # Fee breakdown
    total_fees = stats["total_fees"] if sim else 0
    fee_breakdown = {
        "maker": 0.0,
        "taker": round(total_fees, 4),
        "total": round(total_fees, 4),
    }

    # Per-pair summary with LIVE prices
    pair_summaries = {}
    for sym in state["active_symbols"]:
        sym_trades = [t for t in (sim.trades if sim else []) if t.symbol == sym]
        sym_realized = sum(t.pnl_usdt for t in sym_trades)
        sym_fees = sum(t.fee_usdt for t in sym_trades)

        pos_match = next((p for p in positions if p["symbol"] == sym), None)
        sym_unrealized = pos_match["unrealized_pnl_usdt"] if pos_match else 0.0
        current_price = live_prices.get(sym, state["scan_results"].get(sym, {}).get("last_price", 0))

        ob = orderbook.get(sym, {})
        scan = state["scan_results"].get(sym, {})
        pair_summaries[sym] = {
            "last_price": round(current_price, 4),
            "bid": round(ob.get("bid", current_price), 4),
            "ask": round(ob.get("ask", current_price), 4),
            "spread": round(ob.get("ask", 0) - ob.get("bid", 0), 6) if ob else 0,
            "status": scan.get("status", "waiting"),
            "trend": scan.get("trend", ""),
            "side": scan.get("side", ""),
            "rsi": scan.get("rsi", 0),
            "unrealized_pnl": round(sym_unrealized, 4),
            "realized_pnl": round(sym_realized, 4),
            "total_pnl": round(sym_unrealized + sym_realized, 4),
            "fees": round(sym_fees, 4),
            "trade_count": len(sym_trades),
        }

    # Total unrealized
    total_unrealized = sum(p["unrealized_pnl_usdt"] for p in positions)
    total_realized = stats["total_pnl"]

    return {
        "bot_running": state["bot_running"],
        "ws_connected": state["ws_connected"],
        "ws_last_ping": state["ws_last_ping"],
        "price_source": "websocket" if ws_symbols_count == len(state["active_symbols"]) else (
            f"mixed (ws:{ws_symbols_count}/rest:{len(state['active_symbols']) - ws_symbols_count})"
            if ws_symbols_count > 0 else "rest"
        ) if state["bot_running"] else "none",
        "active_symbols": state["active_symbols"],
        "stats": stats,
        "positions": positions,
        "pair_summaries": pair_summaries,
        "fees": fee_breakdown,
        "signal_log": state["signal_log"][-50:],  # last 50
        "trade_log": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl_usdt": t.pnl_usdt,
                "pnl_pct": t.pnl_percent,
                "fee_usdt": t.fee_usdt,
                "leverage": t.leverage,
            }
            for t in (sim.trades if sim else [])
        ],
        "totals": {
            "unrealized_pnl": round(total_unrealized, 4),
            "realized_pnl": round(total_realized, 4),
            "total_pnl": round(total_unrealized + total_realized, 4),
            "total_fees": fee_breakdown["total"],
            "net_pnl": round(total_unrealized + total_realized - fee_breakdown["total"], 4),
        },
    }
