# 路径派生 — 通过环境变量覆盖,默认走 ~/Projects/quant-dashboard
BASE = Path(os.environ.get("QUANT_DASHBOARD_HOME", str(Path.home() / "Projects" / "quant-dashboard")))

"""中小盘扫描器 v3 - 双源混合
1. 东财行情快照（一次性拉 5534 只）：拿到 流通市值/成交额/PE/PB 做粗筛
2. baostock 拉粗筛后的 K 线（通常 100-300 只）：算评分
"""
import warnings
warnings.filterwarnings("ignore")
import json
import time
import urllib.request
import urllib.parse
from datetime import date, timedelta

OUT = BASE / "data" / "small_cap_candidates.json"
PROGRESS = BASE / "data" / "scan_progress.txt"
END = date.today().strftime("%Y-%m-%d")
START = (date.today() - timedelta(days=120)).strftime("%Y-%m-%d")

def log(msg):
    print(msg, flush=True)
    with open(PROGRESS, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

open(PROGRESS, "w").close()

# === Step 1: 东财全市场快照 ===
log("=== Step 1: 拉取东财全市场快照 ===")

def fetch_em_page(pn, pz=200):
    """拉一页东财数据"""
    url = (
        f"https://82.push2.eastmoney.com/api/qt/clist/get"
        f"?pn={pn}&pz={pz}&fid=f3&po=1"
        f"&fs=m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23"  # 主板+中小板，不含创业板/科创板/北交所
        f"&fields=f12,f14,f2,f6,f9,f20,f21,f23"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

all_stocks = []
page = 1
while True:
    try:
        data = fetch_em_page(page)
        diff = data.get("data", {}).get("diff", {})
        if not diff:
            break
        for k, v in diff.items():
            all_stocks.append(v)
        total = data.get("data", {}).get("total", 0)
        log(f"  page={page} 累计={len(all_stocks)}/{total}")
        if len(all_stocks) >= total:
            break
        page += 1
        time.sleep(0.3)
    except Exception as e:
        log(f"  page={page} error: {e}")
        break

log(f"=== 东财获取完成: {len(all_stocks)} 只 ===")

# === Step 2: 粗筛 ===
log("=== Step 2: 粗筛 (流通市值/成交额/PE/PB/ST过滤) ===")

# f2=价格(分*100), f6=成交额(元), f9=PE_TTM(整数*100), f20=总市值(元), f21=流通市值(元), f23=PB(整数*100)
filtered = []
for s in all_stocks:
    name = s.get("f14", "")
    code = s.get("f12", "")
    if "ST" in name or "退" in name:
        continue
    price = s.get("f2")
    amount = s.get("f6")
    pe = s.get("f9")
    total_mv = s.get("f20")
    float_mv = s.get("f21")
    pb = s.get("f23")

    # 检查类型
    if any(x is None or isinstance(x, str) for x in [price, amount, pe, total_mv, float_mv, pb]):
        continue
    if amount == "-" or float_mv == "-":
        continue

    # 转换（东财用 1/100 比例存价格/PE/PB）
    try:
        price = float(price) / 100
        amount = float(amount)  # 元
        pe = float(pe) / 100
        total_mv = float(total_mv) / 1e8  # 亿
        float_mv = float(float_mv) / 1e8
        pb = float(pb) / 100
    except (TypeError, ValueError):
        continue

    # 过滤
    if pe <= 0 or pe > 50:
        continue
    if pb <= 0 or pb > 8:
        continue
    if not (50 <= float_mv <= 200):
        continue
    if amount < 5e7:  # 单日成交 < 5000 万（用最近一日近似）
        continue

    filtered.append({
        "code": code,
        "name": name,
        "price": round(price, 2),
        "amount": amount / 1e8,  # 亿
        "pe": round(pe, 1),
        "pb": round(pb, 2),
        "total_mv": round(total_mv, 1),
        "float_mv": round(float_mv, 1),
    })

log(f"=== 粗筛后: {len(filtered)} 只 ===")

# === Step 3: baostock 精算评分 ===
log("=== Step 3: 用 baostock 拉 K 线算评分 ===")

import baostock as bs
import pandas as pd
import numpy as np

bs.login()

candidates = []
errors = 0
t0 = time.time()

for i, s in enumerate(filtered):
    if i % 50 == 0 and i > 0:
        elapsed = time.time() - t0
        rate = i / elapsed
        eta = (len(filtered) - i) / rate / 60
        log(f"  精算 {i}/{len(filtered)} 候选={len(candidates)} ETA={eta:.1f}min")
    code = s["code"]
    sym = f"sh.{code}" if code.startswith(("6", "9")) else f"sz.{code}"
    try:
        rs = bs.query_history_k_data_plus(
            sym,
            "date,close,amount,turn,pctChg",
            start_date=START, end_date=END, frequency="d", adjustflag="2"
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if len(rows) < 60:
            continue
        df = pd.DataFrame(rows, columns=rs.fields)
        for c in ["close", "amount", "turn", "pctChg"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        close = float(df["close"].iloc[-1])

        # 20 日均成交额（亿）
        avg_amount = df["amount"].tail(20).mean() / 1e8
        if avg_amount < 0.5:
            continue

        # 60 日跌停次数
        ld = int((df["pctChg"].tail(60) < -9.7).sum())
        if ld >= 2:
            continue

        ma20 = df["close"].tail(20).mean()
        ma60 = df["close"].tail(60).mean()
        trend_pct = (ma20 / ma60 - 1) * 100
        above_ma20 = (close / ma20 - 1) * 100
        roc20 = (close / df["close"].iloc[-21] - 1) * 100 if len(df) >= 21 else 0
        vol5 = df["amount"].tail(5).mean()
        vol20 = df["amount"].tail(20).mean()
        vol_ratio = vol5 / vol20 if vol20 > 0 else 1
        ret = df["close"].pct_change().tail(20)
        vol_pct = float(ret.std() * 100) if pd.notna(ret.std()) else 0

        trend_score = max(0, min(100, 50 + trend_pct * 5))
        momentum_score = max(0, min(100, 50 + roc20 * 1.5))
        volume_score = max(0, min(100, 50 + (vol_ratio - 1) * 100))
        val_score = max(0, min(100, 100 - s["pe"] * 2))
        vol_penalty = max(0, (vol_pct - 3) * 5)
        total = (trend_score * 0.30 + momentum_score * 0.30 + volume_score * 0.15 + val_score * 0.25) - vol_penalty

        candidates.append({
            **s,
            "avg_amount_20d": round(avg_amount, 2),
            "trend_pct": round(trend_pct, 2),
            "above_ma20": round(above_ma20, 2),
            "roc20": round(roc20, 2),
            "vol_ratio": round(vol_ratio, 2),
            "vol_pct": round(vol_pct, 2),
            "limit_down_60d": ld,
            "trend_score": round(trend_score, 1),
            "momentum_score": round(momentum_score, 1),
            "volume_score": round(volume_score, 1),
            "val_score": round(val_score, 1),
            "total_score": round(total, 1),
        })
    except Exception as e:
        errors += 1
        if errors <= 3:
            log(f"  ERR {sym}: {e}")

bs.logout()
total_time = (time.time() - t0) / 60
log(f"=== 完成 用时={total_time:.1f}min 候选={len(candidates)} 错误={errors} ===")

candidates.sort(key=lambda x: x["total_score"], reverse=True)
top30 = candidates[:30]

with open(OUT, "w") as f:
    json.dump({
        "scan_date": END,
        "em_total": len(all_stocks),
        "filtered": len(filtered),
        "scored": len(candidates),
        "elapsed_min": round(total_time, 1),
        "top30": top30
    }, f, ensure_ascii=False, indent=2)

log(f"=== 写入 {OUT} ===")
log("Top 15:")
for i, s in enumerate(top30[:15], 1):
    log(f"  {i:2d}. {s['code']} {s['name']:10s} mv={s['float_mv']}亿 amt20={s['avg_amount_20d']}亿 pe={s['pe']} vol={s['vol_pct']}% trend={s['trend_pct']:+.1f}% roc20={s['roc20']:+.1f}% score={s['total_score']}")
