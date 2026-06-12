"""Main trading bot loop."""

import os
import time
import logging

from dotenv import load_dotenv

from .robinhood import RobinhoodClient
from .strategy import Signal, compute_signal

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def run():
    symbols = [s.strip() for s in os.getenv("SYMBOLS", "AAPL").split(",")]
    short_window = int(os.getenv("SHORT_WINDOW", "10"))
    long_window = int(os.getenv("LONG_WINDOW", "30"))
    dry_run = os.getenv("DRY_RUN", "true").lower() != "false"
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

    client = RobinhoodClient()

    log.info("Bot started | symbols=%s short=%d long=%d dry_run=%s", symbols, short_window, long_window, dry_run)

    while True:
        for symbol in symbols:
            try:
                prices = client.get_price_history(symbol)
                current_price = prices[-1] if prices else client.get_quote(symbol)

                trade = compute_signal(symbol, prices, short_window, long_window)
                if trade is None:
                    log.info("%s: not enough data yet (%d prices, need %d)", symbol, len(prices), long_window)
                    continue

                log.info(
                    "%s price=%.2f short_ma=%.2f long_ma=%.2f signal=%s",
                    symbol, trade.price, trade.short_ma, trade.long_ma, trade.signal.value,
                )

                if trade.signal == Signal.BUY:
                    client.place_order(symbol, "buy", quantity=1, dry_run=dry_run)
                elif trade.signal == Signal.SELL:
                    client.place_order(symbol, "sell", quantity=1, dry_run=dry_run)

            except Exception as exc:
                log.error("%s: error during evaluation: %s", symbol, exc)

        log.info("Sleeping %ds until next poll …", poll_interval)
        time.sleep(poll_interval)
