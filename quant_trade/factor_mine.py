"""CLI entry: auto factor mining and IC report."""
from __future__ import annotations

from quanttrade.config import QuantConfig
from quanttrade.data.ingestion import DataIngestion
from quanttrade.data.etl import quotes_to_bars
from quanttrade.factors.miner import auto_mine_factors


def main() -> None:
    cfg = QuantConfig()
    quotes = list(DataIngestion(cfg.data.sources).load())
    bars = quotes_to_bars(quotes, cfg.backtest.bar_seconds)
    results = auto_mine_factors(bars, cfg.factors)
    print("IC summary:", results)


if __name__ == "__main__":
    main()
