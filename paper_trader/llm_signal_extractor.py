#!/usr/bin/env python3
"""
LLM 信号提取器 — Phase 1 最小版

流程:
  读 portfolio.json 持仓 → 拉每只近 N 天公告 → 喂 GLM-5.1 → 输出结构化信号 → 落 JSONL

输出路径: $HOME/Projects/<your-knowledge-base>/Agent/quant/llm_signals/YYYY-MM-DD.jsonl
          (用 QUANT_KB_NAME 环境变量或 quant_config.yaml 配置)
每行: { stock_code, stock_name, analysis_date, announcements[], sentiment, signal, ... }

用法:
  python3 llm_signal_extractor.py                 # 处理持仓 + 默认 3 天
  python3 llm_signal_extractor.py --days 1         # 只看当日
  python3 llm_signal_extractor.py --codes 600519,000858  # 临时分析候选池
  python3 llm_signal_extractor.py --dry-run        # 拉公告但不调 LLM（调试）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import httpx
import pandas as pd

# ── 路径与配置 ────────────────────────────
PAPER_TRADER_DIR = Path(__file__).parent
PORTFOLIO_FILE = PAPER_TRADER_DIR / "portfolio.json"

# 信号落盘点:优先走 quant_config.yaml,失败时用环境变量 QUANT_KB_NAME,最后兜底占位符
try:
    sys.path.insert(0, str(PAPER_TRADER_DIR.parent))
    from quant_core.config import get_config
    _cfg = get_config()
    SIG_DIR = Path(_cfg.report.output_dir).parent / "llm_signals"
except Exception:
    _KB_NAME = os.environ.get("QUANT_KB_NAME", "<your-knowledge-base>")
    SIG_DIR = Path.home() / "Projects" / _KB_NAME / "Agent" / "quant" / "llm_signals"

# GLM 凭据从 daily_stock_analysis/.env 读,可用 DSA_ENV_FILE 环境变量覆盖
_DSA_ENV = Path(os.environ.get("DSA_ENV_FILE", Path.home() / "Projects" / "daily_stock_analysis" / ".env"))

DEFAULT_MODEL = "GLM-5.1"
DEFAULT_DAYS = 3
MAX_TOKENS = 4096
HTTP_TIMEOUT = 30  # 秒


# ── 股票名查表 ────────────────────────────
_NAME_CACHE_FILE = PAPER_TRADER_DIR / ".stock_name_cache.json"
_NAME_CACHE: dict[str, str] = {}


def _lookup_stock_name(code: str) -> str:
    """通过 akshare 获取股票名（小缓存减少重复请求）"""
    if code in _NAME_CACHE:
        return _NAME_CACHE[code]
    if _NAME_CACHE_FILE.exists():
        try:
            _NAME_CACHE.update(json.loads(_NAME_CACHE_FILE.read_text(encoding="utf-8")))
            if code in _NAME_CACHE:
                return _NAME_CACHE[code]
        except Exception:
            pass
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        for _, r in df.iterrows():
            _NAME_CACHE[str(r["code"])] = str(r["name"])
        _NAME_CACHE_FILE.write_text(
            json.dumps(_NAME_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"[WARN] 拉股票名失败: {e}", file=sys.stderr)
        _NAME_CACHE[code] = code  # fallback
    return _NAME_CACHE.get(code, code)


def load_glm_credentials() -> tuple[str, str, str]:
    """从 daily_stock_analysis/.env 读取 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / LITELLM_MODEL"""
    if not _DSA_ENV.exists():
        raise FileNotFoundError(f"凭据源不存在: {_DSA_ENV}")
    env = {}
    for line in _DSA_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    api_key = env.get("ANTHROPIC_API_KEY", "")
    base_url = env.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    model = env.get("LITELLM_MODEL", "").split("/")[-1] or DEFAULT_MODEL
    if not api_key or not base_url:
        raise RuntimeError(".env 缺 ANTHROPIC_API_KEY 或 ANTHROPIC_BASE_URL")
    return api_key, base_url, model


# ── 数据加载 ──────────────────────────────
def load_portfolio() -> dict[str, dict]:
    if not PORTFOLIO_FILE.exists():
        raise FileNotFoundError(f"组合文件不存在: {PORTFOLIO_FILE}")
    with open(PORTFOLIO_FILE, encoding="utf-8") as f:
        p = json.load(f)
    return p.get("holdings", {})


def fetch_announcements(code: str, days: int) -> list[dict]:
    """拉某只股票近 N 天公告（akshare 单股接口）"""
    import akshare as ak  # 延迟导入，dry-run 也可单独跑
    end_d = date.today()
    start_d = end_d - timedelta(days=days)
    try:
        df = ak.stock_individual_notice_report(
            security=code,
            begin_date=start_d.isoformat(),
            end_date=end_d.isoformat(),
        )
    except Exception as e:
        print(f"[WARN] {code} 拉公告失败: {e}", file=sys.stderr)
        return []

    if df is None or df.empty:
        return []

    # 标准化字段
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "code": str(r["代码"]),
            "title": str(r["公告标题"]),
            "type": str(r.get("公告类型", "-")),
            "date": str(r.get("公告日期", "")),
            "url": str(r.get("网址", "")),
        })
    return rows


# ── LLM 调用 ──────────────────────────────
def build_prompt(code: str, name: str, anns: list[dict]) -> list[dict]:
    """构造 Anthropic Messages 格式的 messages"""
    ann_text_lines = []
    for i, a in enumerate(anns, 1):
        ann_text_lines.append(
            f"{i}. [{a['date']}] [{a['type']}] {a['title']}"
        )
    ann_text = "\n".join(ann_text_lines)

    system = """你是一位专业的 A 股分析师，负责把上市公司公告转成结构化的交易信号。

严格规则:
1. 只基于下面提供的公告做判断，禁止引入未提供的财务数据、行业新闻、外部知识
2. 不得编造任何具体数字（营收、利润、价格、市值等）。如有数字必须来自公告原文
3. 情感打分 sentiment: -1.0 (强烈负面) ~ +1.0 (强烈正面)，0 为中性
4. 影响强度 impact_strength: 0.0 ~ 1.0，仅看公告标题无法判断就给 0.2 默认值
5. 持续时间 duration_days: 信号在公告日之后预计影响股价的天数，1~30
6. 交易信号 trading_signal: 只能是 "positive_bias" | "negative_bias" | "neutral" 之一
7. 置信度 confidence: 0.0 ~ 1.0，公告越多越明确则越高
8. 输出必须是 JSON，前缀无文字、无数值、后缀无解释。"""

    user = f"""股票: {name}（{code}）
近期公告（最近 {len(anns)} 条）:
{ann_text}

请输出 JSON（仅 JSON，不含其他文字）:
{{
  "stock_code": "{code}",
  "stock_name": "{name}",
  "announcement_count": {len(anns)},
  "key_events": [
    {{"title": "公告原标题", "type": "分类", "impact": "positive|negative|neutral", "summary": "一句话摘要"}}
  ],
  "overall_sentiment": 0.0,
  "impact_strength": 0.5,
  "duration_days": 5,
  "trading_signal": "neutral",
  "confidence": 0.5,
  "reasoning": "一段话解释综合判断"
}}"""

    return [
        {"role": "user", "content": system + "\n\n" + user},
    ]


def call_glm(messages: list[dict], api_key: str, base_url: str, model: str) -> str:
    """调 GLM-5.1（Anthropic Messages 协议），返回 text 块内容"""
    url = f"{base_url}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "messages": messages,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                continue
            data = r.json()
            texts = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
            return "\n".join(texts).strip()
        except Exception as e:
            last_err = e
    raise RuntimeError(f"GLM 调用失败（重试 2 次）: {last_err}")


# ── JSON 解析 ─────────────────────────────
_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def parse_llm_json(raw: str) -> dict[str, Any] | None:
    """从 LLM 输出提取 JSON 对象。返回 None 表示解析失败"""
    if not raw:
        return None
    # 优先找第一个 {...} 块
    m = _JSON_BLOCK.search(raw)
    if not m:
        return None
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # 尝试清洗 markdown fence / 多余逗号
        cleaned = blob.replace("```json", "").replace("```", "").strip()
        # 去掉尾随逗号
        cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


# ── 写入 ──────────────────────────────────
def truncate_signal_file(target: Path) -> None:
    """开始时清空当日 JSONL（保证幂等：重跑只看到最新结果）"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")


def write_signal(signal: dict, target: Path, mode: str = "a") -> None:
    """追加一行 JSON 到 JSONL。默认 mode='a'，由 truncate_signal_file 先清空。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, mode, encoding="utf-8") as f:
        f.write(json.dumps(signal, ensure_ascii=False) + "\n")


# ── 单只分析 ──────────────────────────────
def analyze_one(
    code: str,
    name: str,
    days: int,
    api_key: str,
    base_url: str,
    model: str,
    dry_run: bool,
) -> dict | None:
    """分析单只股票，返回标准化信号 dict（None 表示无公告）"""
    anns = fetch_announcements(code, days=days)
    if not anns:
        return None

    if dry_run:
        return {
            "stock_code": code,
            "stock_name": name,
            "analysis_date": date.today().isoformat(),
            "announcement_count": len(anns),
            "announcements_preview": [a["title"] for a in anns[:3]],
            "dry_run": True,
        }

    messages = build_prompt(code, name, anns)
    raw = call_glm(messages, api_key, base_url, model)
    parsed = parse_llm_json(raw)
    if parsed is None:
        print(f"[WARN] {code} LLM 输出解析失败，原始:\n{raw[:200]}", file=sys.stderr)
        return {
            "stock_code": code,
            "stock_name": name,
            "analysis_date": date.today().isoformat(),
            "announcement_count": len(anns),
            "llm_raw_excerpt": raw[:500],
            "parse_error": True,
        }

    # 标准化 + 元数据注入
    parsed["stock_code"] = code
    parsed["stock_name"] = name
    parsed["analysis_date"] = date.today().isoformat()
    parsed["lookback_days"] = days
    parsed["model"] = model
    parsed["announcement_titles"] = [a["title"] for a in anns]  # 全文留底
    return parsed


# ── 主流程 ────────────────────────────────
def iter_target_codes(args_codes: list[str] | None, portfolio: dict) -> Iterable[tuple[str, str]]:
    """根据参数决定要分析哪些股票"""
    if args_codes:
        # 临时分析候选池（CLI 参数）
        for c in args_codes:
            yield c, f"({c})"
    else:
        # 默认：持仓
        for std_code, h in portfolio.items():
            code = std_code.split(".")[0]
            name = h.get("name", "-")
            yield code, name


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 信号提取器（Phase 1）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"回看天数（默认 {DEFAULT_DAYS}）")
    parser.add_argument("--codes", type=str, help="逗号分隔的股票代码列表（不传则用持仓）")
    parser.add_argument("--dry-run", action="store_true", help="只拉公告不调 LLM")
    parser.add_argument("--out", type=str, help="自定义输出 JSONL 路径（默认按当日日期）")
    parser.add_argument("--append", action="store_true", help="追加而非覆盖 JSONL（默认覆盖当日文件）")
    args = parser.parse_args()

    api_key, base_url, model = load_glm_credentials()
    portfolio = load_portfolio()

    if args.codes:
        target_codes = [c.strip() for c in args.codes.split(",")]
        targets = [(c, _lookup_stock_name(c)) for c in target_codes]
    else:
        targets = [(c.split(".")[0], h.get("name", "-")) for c, h in portfolio.items()]  # type: ignore[index]  # noqa

    today_str = date.today().isoformat()
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = SIG_DIR / f"{today_str}.jsonl"

    write_mode = "a" if args.append else "a"  # write_signal 永远是 append
    if not args.append and out_path.exists():
        print(f"[INFO] truncate 当日 JSONL: {out_path}")
        truncate_signal_file(out_path)

    print(f"[INFO] 模型: {model} | 回看: {args.days} 天 | 目标: {len(targets)} 只")
    print(f"[INFO] 输出: {out_path}")

    success = 0
    skipped = 0
    failed = 0
    for code, name in targets:
        print(f"\n→ {code} {name}", end=" ")
        try:
            sig = analyze_one(
                code=code,
                name=name,
                days=args.days,
                api_key=api_key,
                base_url=base_url,
                model=model,
                dry_run=args.dry_run,
            )
            if sig is None:
                print("(无公告，跳过)")
                skipped += 1
                continue
            write_signal(sig, out_path, mode=write_mode)
            sentiment = sig.get("overall_sentiment", sig.get("dry_run", "-"))
            signal = sig.get("trading_signal", "n/a" if not args.dry_run else "n/a(dry)")
            print(f"OK  sentiment={sentiment}  signal={signal}")
            success += 1
        except Exception as e:
            print(f"FAIL  {e}")
            failed += 1

    print(f"\n[SUMMARY] 成功 {success} | 无公告 {skipped} | 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
