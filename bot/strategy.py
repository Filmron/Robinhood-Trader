"""
Strategy base class and built-in strategies.

To add a new strategy:
  1. Subclass BaseStrategy and implement `generate_signal`
  2. Register it with @registry.register("my-strategy")
  3. Set STRATEGY=my-strategy in .env
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class MarketData:
    symbol: str
    asset_type: str          # "stock" | "crypto" | "option"
    prices: list[float]
    volume: list[float] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # options greeks, etc.

    @property
    def current_price(self) -> float:
        return self.prices[-1]


@dataclass
class TradeSignal:
    symbol: str
    asset_type: str
    signal: Signal
    price: float
    quantity: float = 1.0
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class BaseStrategy(abc.ABC):
    """All strategies must implement this interface."""

    def __init__(self, params: dict[str, Any]):
        self.params = params

    @abc.abstractmethod
    def generate_signal(self, data: MarketData) -> TradeSignal | None:
        """Return a TradeSignal or None if there is not enough data."""

    def min_bars(self) -> int:
        """Minimum number of price bars required."""
        return 2


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

class _Registry:
    def __init__(self):
        self._strategies: dict[str, type[BaseStrategy]] = {}

    def register(self, name: str):
        def decorator(cls: type[BaseStrategy]):
            self._strategies[name] = cls
            return cls
        return decorator

    def build(self, name: str, params: dict[str, Any]) -> BaseStrategy:
        if name not in self._strategies:
            available = ", ".join(self._strategies)
            raise ValueError(f"Unknown strategy {name!r}. Available: {available}")
        return self._strategies[name](params)

    def list(self) -> list[str]:
        return list(self._strategies)


registry = _Registry()


# ---------------------------------------------------------------------------
# Built-in strategies
# ---------------------------------------------------------------------------

import numpy as np
import pandas as pd


@registry.register("sma-crossover")
class SmaCrossover(BaseStrategy):
    """
    Classic golden/death cross.
    Params: short_window (default 5), long_window (default 20)
    """

    def min_bars(self):
        return int(self.params.get("long_window", 20)) + 1

    def generate_signal(self, data: MarketData) -> TradeSignal | None:
        short_w = int(self.params.get("short_window", 5))
        long_w = int(self.params.get("long_window", 20))
        if len(data.prices) < self.min_bars():
            return None

        s = pd.Series(data.prices)
        short = s.rolling(short_w).mean()
        long_ = s.rolling(long_w).mean()

        if short.iloc[-2] <= long_.iloc[-2] and short.iloc[-1] > long_.iloc[-1]:
            sig = Signal.BUY
            reason = f"Golden cross: short_ma={short.iloc[-1]:.2f} > long_ma={long_.iloc[-1]:.2f}"
        elif short.iloc[-2] >= long_.iloc[-2] and short.iloc[-1] < long_.iloc[-1]:
            sig = Signal.SELL
            reason = f"Death cross: short_ma={short.iloc[-1]:.2f} < long_ma={long_.iloc[-1]:.2f}"
        else:
            sig = Signal.HOLD
            reason = "No crossover"

        return TradeSignal(
            symbol=data.symbol, asset_type=data.asset_type,
            signal=sig, price=data.current_price, reason=reason,
            meta={"short_ma": short.iloc[-1], "long_ma": long_.iloc[-1]},
        )


@registry.register("rsi-reversal")
class RsiReversal(BaseStrategy):
    """
    Buy on oversold RSI, sell on overbought.
    Params: period (default 14), oversold (default 30), overbought (default 70)
    """

    def min_bars(self):
        return int(self.params.get("period", 14)) + 2

    def generate_signal(self, data: MarketData) -> TradeSignal | None:
        period = int(self.params.get("period", 14))
        oversold = float(self.params.get("oversold", 30))
        overbought = float(self.params.get("overbought", 70))
        if len(data.prices) < self.min_bars():
            return None

        s = pd.Series(data.prices)
        delta = s.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]

        if current_rsi < oversold:
            sig, reason = Signal.BUY, f"RSI oversold: {current_rsi:.1f} < {oversold}"
        elif current_rsi > overbought:
            sig, reason = Signal.SELL, f"RSI overbought: {current_rsi:.1f} > {overbought}"
        else:
            sig, reason = Signal.HOLD, f"RSI neutral: {current_rsi:.1f}"

        return TradeSignal(
            symbol=data.symbol, asset_type=data.asset_type,
            signal=sig, price=data.current_price, reason=reason,
            meta={"rsi": current_rsi},
        )


@registry.register("momentum")
class Momentum(BaseStrategy):
    """
    Buy when price is up N% over lookback, sell when down N%.
    Params: lookback (default 5), threshold_pct (default 2.0)
    """

    def min_bars(self):
        return int(self.params.get("lookback", 5)) + 1

    def generate_signal(self, data: MarketData) -> TradeSignal | None:
        lookback = int(self.params.get("lookback", 5))
        threshold = float(self.params.get("threshold_pct", 2.0))
        if len(data.prices) < self.min_bars():
            return None

        past = data.prices[-(lookback + 1)]
        now = data.current_price
        change_pct = (now - past) / past * 100

        if change_pct >= threshold:
            sig, reason = Signal.BUY, f"Momentum +{change_pct:.1f}% over {lookback} bars"
        elif change_pct <= -threshold:
            sig, reason = Signal.SELL, f"Momentum {change_pct:.1f}% over {lookback} bars"
        else:
            sig, reason = Signal.HOLD, f"Momentum {change_pct:.1f}% — within threshold"

        return TradeSignal(
            symbol=data.symbol, asset_type=data.asset_type,
            signal=sig, price=data.current_price, reason=reason,
            meta={"change_pct": change_pct},
        )


@registry.register("breakout")
class Breakout(BaseStrategy):
    """
    Buy on new N-bar high (breakout), sell on new N-bar low.
    Params: window (default 20)
    """

    def min_bars(self):
        return int(self.params.get("window", 20)) + 1

    def generate_signal(self, data: MarketData) -> TradeSignal | None:
        window = int(self.params.get("window", 20))
        if len(data.prices) < self.min_bars():
            return None

        recent = data.prices[-(window + 1):-1]
        price = data.current_price

        if price > max(recent):
            sig, reason = Signal.BUY, f"Breakout above {window}-bar high ({max(recent):.2f})"
        elif price < min(recent):
            sig, reason = Signal.SELL, f"Breakdown below {window}-bar low ({min(recent):.2f})"
        else:
            sig, reason = Signal.HOLD, "No breakout"

        return TradeSignal(
            symbol=data.symbol, asset_type=data.asset_type,
            signal=sig, price=data.current_price, reason=reason,
            meta={"window_high": max(recent), "window_low": min(recent)},
        )


@registry.register("vwap-reversion")
class VwapReversion(BaseStrategy):
    """
    Buy when price is significantly below VWAP, sell when above.
    Requires volume data.
    Params: band_pct (default 1.0)
    """

    def min_bars(self):
        return 5

    def generate_signal(self, data: MarketData) -> TradeSignal | None:
        if not data.volume or len(data.volume) != len(data.prices):
            return None
        band_pct = float(self.params.get("band_pct", 1.0))

        prices = np.array(data.prices)
        volumes = np.array(data.volume)
        vwap = np.sum(prices * volumes) / np.sum(volumes)
        price = data.current_price
        diff_pct = (price - vwap) / vwap * 100

        if diff_pct <= -band_pct:
            sig, reason = Signal.BUY, f"Price {diff_pct:.1f}% below VWAP ({vwap:.2f})"
        elif diff_pct >= band_pct:
            sig, reason = Signal.SELL, f"Price +{diff_pct:.1f}% above VWAP ({vwap:.2f})"
        else:
            sig, reason = Signal.HOLD, f"Price within VWAP band ({diff_pct:.1f}%)"

        return TradeSignal(
            symbol=data.symbol, asset_type=data.asset_type,
            signal=sig, price=data.current_price, reason=reason,
            meta={"vwap": vwap, "diff_pct": diff_pct},
        )
