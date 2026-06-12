"""Thin async wrapper around the Robinhood MCP server via the Anthropic SDK."""

import os
from typing import Any

import anthropic


class RobinhoodClient:
    def __init__(self, mcp_url: str | None = None):
        self.mcp_url = mcp_url or os.environ["ROBINHOOD_MCP_URL"]
        self._client = anthropic.Anthropic()
        self._mcp_server = {"type": "url", "url": self.mcp_url}

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

    def get_price_history(self, symbol: str, span: str = "month") -> list[float]:
        """Fetch closing price history for a symbol."""
        result = self._call(
            f"Get the historical closing prices for {symbol} over the past {span}. "
            "Return only a JSON array of numbers (prices), no other text."
        )
        import json
        try:
            prices = json.loads(result.strip())
            return [float(p) for p in prices]
        except (json.JSONDecodeError, ValueError):
            raise ValueError(f"Unexpected price history response for {symbol}: {result!r}")

    def get_quote(self, symbol: str) -> float:
        """Get the current bid price for a symbol."""
        result = self._call(
            f"Get the current market price for {symbol}. Return only the number, no other text."
        )
        return float(result.strip())

    def place_order(self, symbol: str, side: str, quantity: float, dry_run: bool = True) -> dict[str, Any]:
        """Place a buy or sell order. dry_run=True logs without submitting."""
        if dry_run:
            print(f"[DRY RUN] {side.upper()} {quantity} shares of {symbol}")
            return {"status": "dry_run", "symbol": symbol, "side": side, "quantity": quantity}

        result = self._call(
            f"Place a market {side} order for {quantity} shares of {symbol}. "
            "Return the order confirmation as JSON."
        )
        import json
        return json.loads(result)

    def get_positions(self) -> dict[str, Any]:
        """Return current portfolio positions."""
        result = self._call(
            "Get my current portfolio positions. Return as JSON with symbol keys and quantity/value fields."
        )
        import json
        return json.loads(result)
