"""Main trading bot loop."""

from __future__ import annotations

import csv
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv

from .robinhood import RobinhoodClient
from .strategy import Signal, registry

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TRADE_LOG = Path("trades.csv")

CRYPTO_WATCHLIST = [
    "BTC-USD", "ETH-USD", "XRP-USD", "SOL-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "POL-USD",
]

# Stocks that do not support fractional share trading on Robinhood
WHOLE_SHARES_ONLY = {"SPCX"}

def _notify(msg: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=5)
    except Exception as e:
        log.warning("Telegram notify failed: %s", e)


def _log_trade(symbol: str, asset_type: str, side: str, quantity: float,
               price: float, reason: str, dry_run: bool) -> None:
    write_header = not TRADE_LOG.exists()
    with TRADE_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "symbol", "asset_type", "side", "quantity", "price", "reason", "dry_run"])
        w.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            symbol, asset_type, side, quantity, price, reason, dry_run,
        ])


def _suggest_crypto(balance: float) -> None:
    """Scan crypto watchlist for momentum and send top 3 to Telegram always."""
    results = []
    for symbol in CRYPTO_WATCHLIST:
        try:
            hist = yf.Ticker(symbol).history(period="1mo", interval="1d")
            if len(hist) < 6:
                continue
            prices = hist["Close"].tolist()
            recent = prices[-1]
            past = prices[-6]
            momentum = (recent - past) / past * 100
            results.append((symbol, recent, momentum))
        except Exception:
            continue

    if not results:
        log.info("Crypto scan: no data available")
        return

    results.sort(key=lambda x: x[2], reverse=True)
    top = results[:3]
    best = top[0]
    lines = [f"Crypto update (balance: ${balance:.2f}):\n"]
    for symbol, price, mom in top:
        arrow = "+" if mom >= 0 else ""
        lines.append(f"  {symbol}: ${price:.4f} ({arrow}{mom:.1f}% / 5d)")
    lines.append(f"\nTop pick: BUY {best[0]} — ${best[1]:.4f}, could get {balance/best[1]:.4f} units")
    msg = "\n".join(lines)
    log.info("Crypto scan: top=%s mom=%.1f%%", best[0], best[2])
    _notify(msg)


def _parse_assets(raw: str) -> list[dict]:
    """
    Parse ASSETS env var. Format:
      SYMBOL:TYPE  e.g.  AAPL:stock,BTC-USD:crypto,SPY240620C00540000:option
    Defaults to stock if type is omitted.
    """
    assets = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        assets.append({"symbol": parts[0], "asset_type": parts[1] if len(parts) > 1 else "stock"})
    return assets


def _parse_strategy_params(raw: str) -> dict:
    """Parse KEY=VALUE,KEY=VALUE into a dict."""
    params = {}
    for item in raw.split(","):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            params[k.strip()] = v.strip()
    return params


def run():
    assets = _parse_assets(os.getenv("ASSETS", "AAPL:stock"))
    strategy_name = os.getenv("STRATEGY", "sma-crossover")
    strategy_params = _parse_strategy_params(os.getenv("STRATEGY_PARAMS", ""))
    dry_run = os.getenv("DRY_RUN", "true").lower() != "false"
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    data_span = os.getenv("DATA_SPAN", "week")
    trade_amount_usd = float(os.getenv("TRADE_AMOUNT_USD", "50"))

    strategy = registry.build(strategy_name, strategy_params)
    client = RobinhoodClient()

    log.info(
        "Bot started | strategy=%s params=%s assets=%s dry_run=%s trade_amount=$%.2f",
        strategy_name, strategy_params,
        [a["symbol"] for a in assets], dry_run, trade_amount_usd,
    )
    log.info("Available strategies: %s", registry.list())

    positions: dict = {}
    cycle = 0

    while True:
        cycle += 1
        # Suggest crypto every 6 cycles (~30 min at 5-min intervals)
        if cycle % 6 == 1:
            try:
                balance = float(os.getenv("TRADE_AMOUNT_USD", "5")) * 5
                _suggest_crypto(balance)
            except Exception as exc:
                log.warning("Crypto scan failed: %s", exc)

        for asset in assets:
            symbol, asset_type = asset["symbol"], asset["asset_type"]
            try:
                data = client.get_market_data(symbol, asset_type, span=data_span)

                if len(data.prices) < strategy.min_bars():
                    log.info("%s: need %d bars, have %d", symbol, strategy.min_bars(), len(data.prices))
                    continue

                trade = strategy.generate_signal(data)
                if trade is None:
                    log.info("%s: no signal", symbol)
                    continue

                quantity = round(trade_amount_usd / trade.price, 6) if trade.price > 0 else trade.quantity
                if symbol in WHOLE_SHARES_ONLY:
                    quantity = max(1, int(quantity))
                    if quantity * trade.price > trade_amount_usd * 3:
                        log.info("%s: skipping — 1 share ($%.2f) exceeds budget", symbol, trade.price)
                        continue
                log.info("%s [%s] price=%.4f signal=%s qty=%.4f ($%.2f) — %s",
                         symbol, asset_type, trade.price, trade.signal.value,
                         quantity, quantity * trade.price, trade.reason)

                if trade.signal == Signal.SELL and symbol not in positions:
                    log.info("%s: skipping sell — no position held", symbol)
                    continue

                if trade.signal in (Signal.BUY, Signal.SELL):
                    client.place_order(
                        symbol=symbol,
                        asset_type=asset_type,
                        side=trade.signal.value,
                        quantity=quantity,
                        dry_run=dry_run,
                    )
                    _log_trade(symbol, asset_type, trade.signal.value,
                               quantity, trade.price, trade.reason, dry_run)
                    if trade.signal == Signal.BUY:
                        positions[symbol] = quantity
                    elif symbol in positions:
                        del positions[symbol]

            except Exception as exc:
                log.error("%s: %s", symbol, exc)

        log.info("Sleeping %ds …", poll_interval)
        time.sleep(poll_interval)
