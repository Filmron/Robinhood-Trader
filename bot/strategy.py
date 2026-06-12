from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradeSignal:
    symbol: str
    signal: Signal
    short_ma: float
    long_ma: float
    price: float


def compute_signal(symbol: str, prices: list[float], short_window: int, long_window: int) -> TradeSignal | None:
    """Return a trade signal based on SMA crossover, or None if insufficient data."""
    if len(prices) < long_window:
        return None

    series = pd.Series(prices)
    short_ma = series.rolling(short_window).mean().iloc[-1]
    long_ma = series.rolling(long_window).mean().iloc[-1]
    prev_short = series.rolling(short_window).mean().iloc[-2]
    prev_long = series.rolling(long_window).mean().iloc[-2]

    price = prices[-1]

    if prev_short <= prev_long and short_ma > long_ma:
        signal = Signal.BUY
    elif prev_short >= prev_long and short_ma < long_ma:
        signal = Signal.SELL
    else:
        signal = Signal.HOLD

    return TradeSignal(symbol=symbol, signal=signal, short_ma=short_ma, long_ma=long_ma, price=price)
