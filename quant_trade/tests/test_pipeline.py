import pandas as pd

from quanttrade.backtest.engine import BacktestEngine, simple_moving_average_strategy
from quanttrade.backtest.metrics import performance_stats
from quanttrade.clearing.clearing import ClearingEngine
from quanttrade.config import QuantConfig
from quanttrade.data.etl import quotes_to_bars
from quanttrade.data.ingestion import DataIngestion
from quanttrade.data.quality import basic_quality_checks


def test_end_to_end(tmp_path):
    cfg = QuantConfig()
    cfg.clearing.report_path = tmp_path / "report.csv"
    ingestion = DataIngestion(cfg.data.sources)
    quotes = list(ingestion.load())
    assert len(quotes) > 0
    assert basic_quality_checks(quotes) == []

    bars = quotes_to_bars(quotes, bar_seconds=60)
    assert len(bars) > 0

    engine = BacktestEngine(cfg.backtest)
    hist = []
    pnl, fills = engine.run(bars, lambda bar, ctx: simple_moving_average_strategy(bar, 3, 5, hist))
    assert isinstance(pnl, pd.DataFrame)
    assert len(fills) >= 0

    stats = performance_stats(pnl["equity"]) if not pnl.empty else {}
    assert "return" in stats

    clearing = ClearingEngine(cfg.clearing)
    path = clearing.generate_report(fills)
    assert path.exists()
