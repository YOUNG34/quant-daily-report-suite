"""数据源容错层：包装腾讯接口 + 重试 + 指数退避 + 失败告警。

设计:
- 现有 fetch_tencent_quotes(codes) → dict 保持不变
- 新增 fetch_tencent_quotes_with_retry(codes) → dict 加重试
- 重试策略: 指数退避 base * 2^attempt，attempt=0..max_retries-1
- 失败标记: 连续 fail_threshold 次失败 → 写 portfolio['last_data_error']
- 空响应检测: raw 为空 / 不含 v_ 前缀 / 全 batch 失败
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from quant_core.config import get_config
from quant_core.logger import get_logger

_log = get_logger("data_source")

# 失败计数（模块级，进程内持久）
_fail_counter: dict[str, int] = {}


def _curl_fetch(url: str, timeout: int, user_agent: str) -> str:
    """curl 单次拉取，返回解码后的文本。"""
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", user_agent, url],
        capture_output=True,
        timeout=timeout + 2,
    )
    raw = r.stdout
    if not raw:
        return ""
    for enc in ["gbk", "gb18030", "utf-8"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("gbk", errors="replace")


def _parse_tencent_response(text: str) -> dict[str, dict]:
    """解析腾讯响应 → {code: {price, prev_close, ...}}。空响应或格式错误返回 {}。"""
    if not text or "v_" not in text:
        return {}
    results = {}
    for line in text.strip().split("\n"):
        if '="' not in line:
            continue
        if line.startswith("v_"):
            var_and_data = line[2:]
        else:
            var_and_data = line
        parts = var_and_data.split('="', 1)
        if len(parts) < 2:
            continue
        var_name = parts[0]
        data = parts[1].rstrip('";')
        fields = data.split("~")
        if len(fields) < 33:
            continue
        try:
            code = var_name[2:]
            name = fields[1]
            price = float(fields[3])
            prev_close = float(fields[4])
            open_price = float(fields[5])  # 今开（非 trade_status！腾讯接口无此字段）
            pct_chg = float(fields[32])
            change_amt = float(fields[33])  # 涨跌额
            results[code] = {
                "name": name,
                "price": price,
                "prev_close": prev_close,
                "open": open_price,
                "pct_chg": pct_chg,
                "change_amt": change_amt,
            }
        except (ValueError, IndexError):
            continue
    return results


def _to_tencent_code(code: str) -> str:
    """002463 → sz002463, 600519 → sh600519"""
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    return f"sz{code}"


def fetch_tencent_quotes_with_retry(
    codes: list[str],
    portfolio_file: Optional[Path] = None,
    context: str = "",
) -> dict[str, dict]:
    """带重试的腾讯行情拉取。失败累计 → 写 portfolio['last_data_error']。

    Args:
        codes: 6 位代码列表
        portfolio_file: portfolio.json 路径，可选；提供时失败会写 last_data_error
        context: 调用上下文（用于日志，如 'rebalance' / 'update_nav'）

    Returns:
        {code: {price, prev_close, trade_status, pct_chg, name}}
    """
    cfg = get_config()
    ds = cfg.data_source
    if not codes:
        return {}

    tenc_codes = [_to_tencent_code(c) for c in codes]
    final: dict[str, dict] = {}
    batch_size = ds.batch_size

    for i in range(0, len(tenc_codes), batch_size):
        batch = tenc_codes[i:i + batch_size]
        url = f"https://qt.gtimg.cn/q={','.join(batch)}"
        batch_codes = codes[i:i + batch_size]

        # 重试循环
        batch_result: dict[str, dict] = {}
        last_error = ""
        for attempt in range(ds.max_retries + 1):
            try:
                text = _curl_fetch(url, ds.timeout_sec, ds.user_agent)
                batch_result = _parse_tencent_response(text)
                if batch_result:
                    if attempt > 0 and _log:
                        _log.info("data_source: batch[{}] 第 {} 次重试成功", i // batch_size, attempt)
                    break
                else:
                    last_error = "空响应 / 解析失败"
            except subprocess.TimeoutExpired:
                last_error = f"timeout (>{ds.timeout_sec}s)"
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
            if attempt < ds.max_retries:
                delay = ds.retry_base_delay_sec * (2 ** attempt)
                if _log:
                    _log.warning("data_source: batch[{}] 第 {} 次失败 ({}), 等待 {:.1f}s 后重试",
                                 i // batch_size, attempt + 1, last_error, delay)
                time.sleep(delay)

        if not batch_result:
            if _log:
                _log.error("data_source: batch[{}] 全部 {} 次尝试均失败: {}",
                           i // batch_size, ds.max_retries + 1, last_error)
            _record_failure(portfolio_file, context, last_error, len(batch))
        else:
            _record_success(portfolio_file, context)
            # 合并结果（用原 code 作 key）
            for tc, oc in zip(batch, batch_codes):
                if tc[2:] in batch_result:
                    final[oc] = batch_result[tc[2:]]

    if _log:
        success_n = len(final)
        fail_n = len(codes) - success_n
        if fail_n > 0:
            _log.warning("data_source: {} 成功 / {} 失败 (context={})", success_n, fail_n, context or 'N/A')
        else:
            _log.info("data_source: {} 只全部拉取成功 (context={})", success_n, context or 'N/A')

    return final


def _record_failure(portfolio_file: Optional[Path], context: str, error: str, failed_codes: int = 0) -> None:
    """记录一次失败：累加计数 → 超过阈值则写 portfolio['last_data_error']。

    阈值来源 cfg.data_source.fail_threshold，达到后才落盘（避免偶尔抖动就污染 portfolio）。
    """
    _fail_counter["count"] = _fail_counter.get("count", 0) + 1
    threshold = get_config().data_source.fail_threshold
    if _fail_counter["count"] < threshold:
        if _log:
            _log.debug("data_source: 失败计数 {}/{} (未达阈值不写盘)", _fail_counter["count"], threshold)
        return
    if not portfolio_file or not Path(portfolio_file).exists():
        return
    try:
        with open(portfolio_file) as f:
            p = json.load(f)
        p["last_data_error"] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "context": context,
            "error": error,
            "failed_codes": failed_codes,
            "consecutive_failures": _fail_counter["count"],
        }
        with open(portfolio_file, "w") as f:
            json.dump(p, f, indent=2, ensure_ascii=False)
    except Exception as e:
        if _log:
            _log.error("写 last_data_error 失败: {}", e)


def _record_success(portfolio_file: Optional[Path], context: str) -> None:
    """成功时清零失败计数 + 清除 last_data_error。"""
    if _fail_counter.get("count", 0) > 0 and _log:
        _log.info("data_source: 恢复拉取 (此前连续失败 {} 次)", _fail_counter["count"])
    _fail_counter["count"] = 0
    if portfolio_file is None:
        return
    if not Path(portfolio_file).exists():
        return
    try:
        with open(portfolio_file) as f:
            p = json.load(f)
        if p.pop("last_data_error", None) is not None:
            with open(portfolio_file, "w") as f:
                json.dump(p, f, indent=2, ensure_ascii=False)
    except Exception as e:
        if _log:
            _log.warning("清 last_data_error 失败: {}", e)


# ============== K 线接口（ATR 止损用） ==============
#
# 腾讯 fqkline 接口：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
#   param 格式：{code1,code2,...},{period},{start},{end},{count},{adj}
#   period: day/week/month/m5/m15/m30/m60
#   adj: qfq(前复权) / hfq(后复权) / 留空(non-qfq)
#
# **用 non-qfq**（避免除权日 gap 干扰 ATR 计算）
# **注意**：fqkline 一次性只支持单 code（多 code → "param error"）
#   10 只持仓 = 10 次 HTTP 请求（每只 < 100ms，~1s 总耗时）
#
# 响应格式（去掉外层包装）：
#   {"code":0, "data": {"sh600584": {"day": [[date, open, close, high, low, vol], ...]}}}
KLINE_BASE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _parse_kline_response(text: str) -> dict[str, list]:
    """解析腾讯 K 线响应 → {code: [[date, o, c, h, l, vol], ...]}。

    失败/空响应 → {}。per-code 空 data 也跳过。
    """
    if not text or "data" not in text:
        return {}
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if outer.get("code") != 0:
        return {}
    data = outer.get("data") or {}
    out: dict[str, list] = {}
    for code, payload in data.items():
        if not isinstance(payload, dict):
            continue
        # 优先 day 字段（non-qfq），缺失则降级到 qfqday
        rows = payload.get("day") or payload.get("qfqday") or []
        if not rows:
            continue
        # 腾讯字段顺序: [date, open, close, high, low, volume, ...]
        # 统一规整为 6 元组
        normalized: list[list[float]] = []
        for r in rows:
            if len(r) < 5:
                continue
            try:
                normalized.append([
                    str(r[0]),
                    float(r[1]),  # open
                    float(r[2]),  # close
                    float(r[3]),  # high
                    float(r[4]),  # low
                    float(r[5]) if len(r) > 5 else 0.0,  # volume
                ])
            except (ValueError, TypeError):
                continue
        if normalized:
            out[code] = normalized
    return out


def _build_kline_url(code: str, count: int) -> str:
    """拼腾讯 K 线 URL（单 code）。"""
    return f"{KLINE_BASE_URL}?param={code},day,,,{count},"


def fetch_klines(codes: list[str], period: int = 14) -> dict[str, list]:
    """拉多只 code 的 period 日 K 线（无复权）。

    Args:
        codes: 完整代码（含 sh/sz/bj 前缀），如 ["sh600584", "sz000001"]
               也支持无前缀 ["600584", "000001"] — 内部自动补
        period: 取最近 N 根日 K（默认 14）

    Returns:
        {code: [[date, o, c, h, l, vol], ...]} — 失败/空响应时该 code 不在结果里
        返回的 key 是**带前缀**的（与传入一致）

    Note:
        腾讯 fqkline 单次只支持单 code → 循环拉 N 次
    """
    if not codes:
        return {}
    cfg = get_config()
    result: dict[str, list] = {}
    for code in codes:
        # 兼容无前缀 code（"600584" / "001965"）
        prefixed = code if code.startswith(("sh", "sz", "bj")) else _to_tencent_code(code)
        url = _build_kline_url(prefixed, period)
        try:
            raw = _curl_fetch(url, cfg.data_source.timeout_sec, cfg.data_source.user_agent)
        except subprocess.TimeoutExpired:
            if _log:
                _log.warning("kline: 拉取超时 code={}", code)
            continue
        parsed = _parse_kline_response(raw)
        # parsed 的 key 是 prefixed（带前缀），用 prefixed 取
        if prefixed in parsed:
            result[code] = parsed[prefixed]  # 返回用原 code（与传入一致）
    return result


def fetch_klines_with_retry(
    codes: list[str],
    period: int = 14,
    max_retries: int = 2,
) -> dict[str, list]:
    """fetch_klines + 指数退避重试 + 失败告警。

    边界：拉 K 线失败 → 返回 {}（不抛），由调用方决定 fallback。
    **不写 portfolio.last_data_error** — 该字段是给快照拉取设计的，
    K 线拉取失败仅打 warning log（避免污染主流程告警）。
    """
    if not codes:
        return {}
    cfg = get_config()
    base_delay = cfg.data_source.retry_base_delay_sec
    last_err: str | None = None
    for attempt in range(max_retries + 1):
        result = fetch_klines(codes, period)
        if result:
            if _log and _fail_counter.get("count", 0) > 0:
                _log.info("kline: 恢复拉取 (此前连续失败 {} 次)", _fail_counter["count"])
            _fail_counter["count"] = 0
            return result
        last_err = f"kline 拉取空响应 (attempt {attempt + 1}/{max_retries + 1}, codes={len(codes)})"
        if _log:
            _log.warning(last_err)
        if attempt < max_retries:
            time.sleep(base_delay * (2 ** attempt))
    # 全部失败 — 仅打 log，不写 portfolio（边界见 docstring）
    _fail_counter["count"] = _fail_counter.get("count", 0) + 1
    if _log:
        _log.warning("kline 全部失败 (累计 {} 次, 调方应 fallback): {}",
                     _fail_counter["count"], last_err or "")
    return {}


__all__ = [
    "KLINE_BASE_URL",
    "fetch_klines", "fetch_klines_with_retry",
    "_curl_fetch", "_parse_tencent_response", "_parse_kline_response",
    "_build_kline_url", "_record_failure", "_record_success",
]
