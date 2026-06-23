"""分析 watchlist 10 只股票：市值、流动性、散户/机构属性"""
import warnings
# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

warnings.filterwarnings("ignore")
import baostock as bs
import pandas as pd
import sqlite3
import json
from datetime import date, timedelta

DB = BASE / "data" / "indicators.db"
db = sqlite3.connect(DB)
stocks = db.execute("SELECT code, name FROM watchlist ORDER BY code").fetchall()
db.close()

bs.login()
end = date.today().strftime("%Y-%m-%d")
start = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")

results = []
for code, name in stocks:
    sym = f"sh.{code}" if code.startswith(("6", "9", "5")) else f"sz.{code}"
    rs = bs.query_history_k_data_plus(
        sym,
        "date,close,amount,turn,peTTM,pbMRQ",
        start_date=start, end_date=end, frequency="d", adjustflag="2"
    )
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        continue
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ["close", "amount", "turn", "peTTM", "pbMRQ"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    latest = df.iloc[-1]
    avg_amount = df["amount"].tail(20).mean()
    avg_turn = df["turn"].tail(20).mean()
    # 流通市值 = 成交额 / (换手率/100)  按交易日反推
    # turn 是换手率(%), amount 是成交额(元)
    # 流通股 = amount / (turn/100 * close)
    # 流通市值 = 流通股 * close = amount / (turn/100) = amount*100/turn
    # 取最近 20 日均值更稳健
    df["impl_float_mv"] = df["amount"] * 100 / df["turn"]
    # 过滤无效值
    valid = df["impl_float_mv"].replace([float("inf"), -float("inf")], float("nan")).dropna()
    if len(valid) == 0:
        continue
    float_mv_yuan = valid.tail(20).median()
    float_mv = float_mv_yuan / 1e8  # 转亿
    total_mv = float_mv  # 简化处理：A股大多数公司流通≈总市值（老股全流通后），无 totalShare 字段近似

    pe = latest["peTTM"]
    pb = latest["pbMRQ"]
    results.append({
        "code": code, "name": name,
        "price": round(float(latest["close"]), 2),
        "total_mv": round(float(total_mv), 1),
        "float_mv": round(float(float_mv), 1),
        "avg_amount": round(float(avg_amount) / 1e8, 2),
        "avg_turn": round(float(avg_turn), 2),
        "pe": round(float(pe), 1) if pd.notna(pe) else None,
        "pb": round(float(pb), 2) if pd.notna(pb) else None,
    })

bs.logout()

for s in results:
    mv, amt = s["float_mv"], s["avg_amount"]
    if mv > 1000: s["mv_tier"] = "超大盘"
    elif mv > 500: s["mv_tier"] = "大盘"
    elif mv > 100: s["mv_tier"] = "中盘"
    elif mv > 50: s["mv_tier"] = "中小盘"
    else: s["mv_tier"] = "小盘"
    if amt > 10: s["liquidity"] = "极佳"
    elif amt > 5: s["liquidity"] = "良好"
    elif amt > 2: s["liquidity"] = "一般"
    elif amt > 1: s["liquidity"] = "偏低"
    else: s["liquidity"] = "偏紧"
    if mv > 300 and amt > 5: s["zone"] = "机构票"
    elif mv > 100: s["zone"] = "机构主导"
    elif mv > 50: s["zone"] = "混合区"
    else: s["zone"] = "散户优势"

print("__JSON_START__")
print(json.dumps(results, ensure_ascii=False))
print("__JSON_END__")
