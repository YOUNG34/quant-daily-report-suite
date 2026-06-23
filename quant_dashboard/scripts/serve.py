#!/usr/bin/env python3
"""零依赖 HTTP server (stdlib http.server)，serve dashboard + JSON API。

# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

启动: python3 scripts/serve.py [--port 8765]
浏览器打开: http://127.0.0.1:8765/
"""
from __future__ import annotations

import sys
import json
import sqlite3
import argparse
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = BASE
DB_PATH = ROOT / "data" / "indicators.db"
WEB = ROOT / "web"


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def api_latest() -> dict:
    """最新一天所有股票的指标"""
    db = _connect()
    latest_date = db.execute("SELECT MAX(date) FROM daily_indicators").fetchone()[0]
    if not latest_date:
        db.close()
        return {"date": None, "stocks": [], "market": None}

    rows = db.execute("""
        SELECT d.*, w.name, w.industry, w.is_holding
        FROM daily_indicators d
        JOIN watchlist w ON w.code = d.code
        WHERE d.date = ?
        ORDER BY d.score_total DESC
    """, (latest_date,)).fetchall()

    market_row = db.execute(
        "SELECT * FROM market_context WHERE date = ?", (latest_date,)
    ).fetchone()

    fetch_row = db.execute(
        "SELECT * FROM fetch_log WHERE date = ? ORDER BY run_id DESC LIMIT 1", (latest_date,)
    ).fetchone()

    db.close()
    return {
        "date": latest_date,
        "stocks": [_row_to_dict(r) for r in rows],
        "market": _row_to_dict(market_row) if market_row else None,
        "fetch_log": _row_to_dict(fetch_row) if fetch_row else None,
    }


def api_history(code: str, days: int = 60) -> dict:
    """单股历史指标"""
    db = _connect()
    rows = db.execute("""
        SELECT date, close, pct_chg, ma5, ma20, ma60,
               adx14, rsi14, macd_diff, macd_dea, macd_hist,
               vol_ratio, score_total, verdict
        FROM daily_indicators
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
    """, (code, days)).fetchall()
    info = db.execute("SELECT * FROM watchlist WHERE code = ?", (code,)).fetchone()
    db.close()
    return {
        "code": code,
        "info": _row_to_dict(info) if info else None,
        "history": list(reversed([_row_to_dict(r) for r in rows])),
    }


def api_market_history(days: int = 60) -> dict:
    """大盘历史"""
    db = _connect()
    rows = db.execute("""
        SELECT date, sh_close, sh_pct_chg, sh_ma60, cyb_close, cyb_pct_chg,
               volatility20, regime, north_inflow
        FROM market_context
        ORDER BY date DESC LIMIT ?
    """, (days,)).fetchall()
    db.close()
    return {"history": list(reversed([_row_to_dict(r) for r in rows]))}


def api_watchlist() -> dict:
    db = _connect()
    rows = db.execute("SELECT * FROM watchlist ORDER BY is_holding DESC, code").fetchall()
    db.close()
    return {"stocks": [_row_to_dict(r) for r in rows]}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404, f"Not found: {path.name}")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        try:
            if path == "/" or path == "/index.html":
                self._send_file(WEB / "index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._send_file(WEB / "app.js", "application/javascript")
            elif path == "/style.css":
                self._send_file(WEB / "style.css", "text/css")
            elif path == "/api/latest":
                self._send_json(api_latest())
            elif path == "/api/history":
                code = params.get("code", [""])[0]
                days = int(params.get("days", ["60"])[0])
                if not code:
                    self._send_json({"error": "missing code"}, 400)
                else:
                    self._send_json(api_history(code, days))
            elif path == "/api/market":
                days = int(params.get("days", ["60"])[0])
                self._send_json(api_market_history(days))
            elif path == "/api/watchlist":
                self._send_json(api_watchlist())
            else:
                self.send_error(404, f"Unknown path: {path}")
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # 简洁日志
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"📊 Dashboard server listening on {url}")
    print(f"   DB: {DB_PATH}")
    print("   Ctrl+C to stop")

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 stopped")
        server.server_close()


if __name__ == "__main__":
    main()
