"""Thin wrapper around the Robinhood MCP server via the Anthropic SDK."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import anthropic
import yfinance as yf

from .strategy import MarketData


class RobinhoodClient:
    def __init__(self, mcp_url: str | None = None):
        self.mcp_url = mcp_url or os.environ["ROBINHOOD_MCP_URL"]
        self._client = anthropic.Anthropic()
        token = os.environ.get("ROBINHOOD_API_TOKEN", "")
        url = self.mcp_url
        if token:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}token={token}"
        self._account_number = None
        self._mcp_server = {
            "type": "url",
            "name": "robinhood",
            "url": url,
        }

    def _call(self, prompt: str) -> str:
        response = self._client.beta.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            mcp_servers=[self._mcp_server],
            messages=[{"role": "user", "content": prompt}],
            betas=["mcp-client-2025-04-04"],
        )
        return next(
            (block.text for block in response.content if hasattr(block, "text")),
            "",
        )

    def _get_account_number(self) -> str:
        if self._account_number:
            return self._account_number
        raw = self._call("Call get_accounts and return ONLY the account number as plain text.")
        self._account_number = raw.strip().split()[0]
        return self._account_number

    def _parse_json(self, raw: str, context: str) -> Any:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        text = match.group(1) if match else raw.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Could not parse JSON for {context}: {raw!r}") from exc

    # ------------------------------------------------------------------
    # Market data — free via Yahoo Finance, no API credits used
    # ------------------------------------------------------------------

    def get_market_data(self, symbol: str, asset_type: str, span: str = "week") -> MarketData:
        """Fetch OHLCV history from Yahoo Finance (free, no AI credits)."""
        yf_symbol = symbol  # XRP-USD, AAPL, SPY, BBAI all work natively in yfinance
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="1mo", interval="1d")
        if hist.empty:
            raise ValueError(f"No data returned from Yahoo Finance for {symbol}")
        prices = [float(p) for p in hist["Close"].tolist()]
        volume = [float(v) for v in hist["Volume"].tolist()]
        return MarketData(
            symbol=symbol,
            asset_type=asset_type,
            prices=prices,
            volume=volume,
        )

    def get_option_data(self, symbol: str, expiry: str, strike: float, option_type: str) -> MarketData:
        """Fetch option chain data including greeks."""
        raw = self._call(
            f"Get market data for {symbol} {option_type} option, strike {strike}, expiry {expiry}. "
            "Return JSON: {\"prices\": [...], \"volume\": [...], \"delta\": 0.0, \"gamma\": 0.0, "
            "\"theta\": 0.0, \"iv\": 0.0}"
        )
        data = self._parse_json(raw, f"option data for {symbol}")
        return MarketData(
            symbol=f"{symbol}_{option_type}_{strike}_{expiry}",
            asset_type="option",
            prices=[float(p) for p in data.get("prices", [])],
            volume=[float(v) for v in data.get("volume", [])],
            extra={k: data[k] for k in ("delta", "gamma", "theta", "iv") if k in data},
        )

    # ------------------------------------------------------------------
    # Order management — only uses AI credits when actually placing a trade
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        asset_type: str,
        side: str,
        quantity: float,
        dry_run: bool = True,
        order_type: str = "market",
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        tag = "[DRY RUN] " if dry_run else ""
        print(f"{tag}{side.upper()} {quantity}x {symbol} ({asset_type}) @ {order_type}"
              + (f" limit={limit_price}" if limit_price else ""))

        if dry_run:
            return {"status": "dry_run", "symbol": symbol, "side": side,
                    "quantity": quantity, "asset_type": asset_type}

        price_clause = f", limit_price={limit_price}" if limit_price else ""
        acct = self._get_account_number()
        raw = self._call(
            f"Account: {acct}. Use place_order to submit a {order_type} {side} order: "
            f"symbol={symbol}, quantity={quantity}{price_clause}. "
            f"The account is agentic_allowed. Return ONLY JSON: "
            f'{{\"order_id\": \"...\", \"status\": \"...\", \"symbol\": \"{symbol}\", '
            f'\"side\": \"{side}\", \"quantity\": {quantity}}}.'
        )
        return self._parse_json(raw, f"order confirmation for {symbol}")

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        raw = self._call(f"Cancel order {order_id}. Return the result as JSON.")
        return self._parse_json(raw, f"cancel order {order_id}")

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_positions(self) -> dict[str, Any]:
        raw = self._call(
            "Get my current portfolio positions across stocks, crypto, and options. "
            "Return JSON: {symbol: {quantity, value, asset_type}}"
        )
        return self._parse_json(raw, "positions")

    def get_buying_power(self) -> float:
        raw = self._call("What is my current buying power? Return only the number.")
        return float(raw.strip())
