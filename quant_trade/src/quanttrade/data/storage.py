from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

from quanttrade.common.models import Bar
from quanttrade.config import DataConfig

logger = logging.getLogger(__name__)


class BarStorage:
    def __init__(self, root: str = "data/cache", cfg: Optional[DataConfig] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg

    def save(self, bars: Iterable[Bar], name: str = "bars") -> Path:
        df = pd.DataFrame([b.model_dump() for b in bars])
        path = self.root / f"{name}.parquet"
        df.to_parquet(path, index=False)
        logger.info("Saved %d bars to %s", len(df), path)
        if self.cfg and self.cfg.clickhouse_url:
            self._write_clickhouse(df)
        return path

    def load(self, name: str = "bars") -> Iterable[Bar]:
        path = self.root / f"{name}.parquet"
        if not path.exists():
            logger.error("Parquet file %s not found", path)
            return []
        df = pd.read_parquet(path)
        return [Bar.model_validate(row._asdict()) for row in df.itertuples(index=False)]

    def _write_clickhouse(self, df: pd.DataFrame) -> None:
        if df.empty:
            logger.warning("No bars to write to ClickHouse")
            return
        url = f"{self.cfg.clickhouse_url}/?query="  # type: ignore[union-attr]
        table = self.cfg.clickhouse_table  # type: ignore[union-attr]
        database = self.cfg.clickhouse_database  # type: ignore[union-attr]
        auth = None
        if getattr(self.cfg, "clickhouse_user", None):
            auth = HTTPBasicAuth(self.cfg.clickhouse_user, getattr(self.cfg, "clickhouse_password", None))
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            symbol String,
            ts DateTime64(3),
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Float64
        ) ENGINE = MergeTree ORDER BY (symbol, ts)
        """
        insert_sql = f"INSERT INTO {database}.{table} FORMAT Parquet"

        resp = requests.post(url + requests.utils.quote(create_sql), auth=auth)
        if resp.status_code >= 300:
            logger.error("ClickHouse create table failed: %s", resp.text)
            return
        parquet_bytes = df.to_parquet(index=False)
        resp = requests.post(url + requests.utils.quote(insert_sql), data=parquet_bytes, auth=auth)
        if resp.status_code >= 300:
            logger.error("ClickHouse insert failed: %s", resp.text)
        else:
            logger.info("Wrote %d rows to ClickHouse %s.%s", len(df), database, table)
