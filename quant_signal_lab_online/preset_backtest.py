"""Shared-sample historical comparison of the five strategy presets.

The built-in values are initial test settings, not an optimized or recommended
set. This runner never searches thresholds, never overwrites user_presets.json,
and uses the same raw candles and the same 70/30 time split for every preset.
Official candidates stay close-confirmed and are filled at the next bar's open,
with stops processed first on the same bar. Lookahead is not used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import Costs, simulate
from market_data import MarketDataError
from presets import BUILTINS, PRESET_PATH, resolved_custom
from signals import aligned_frames, score_with_config
from swap_data import fetch_swap_history

BEIJING = "Asia/Shanghai"
CACHE = Path(__file__).resolve().parent / "data" / "cache"
COUNTS = {"15m": 4000, "5m": 12000, "1H": 2500, "4H": 1200}
COMPARE_NAMES = ("敏捷型", "均衡型", "稳健型", "震荡行情型", "自定义")
COMPLETE_COLUMNS = ("ema7", "rsi", "macd_signal", "rsi_5m", "vol_avg", "atr",
                    "direction_4H", "direction_1H")


def comparison_presets(path=PRESET_PATH):
    presets = {name: BUILTINS[name] for name in ("敏捷型", "均衡型", "稳健型", "震荡行情型")}
    custom, source = resolved_custom(path)
    presets["自定义"] = custom
    return presets, source


def fetch_shared_frames(symbol, counts=COUNTS, now=None):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    return {interval: fetch_swap_history(symbol, interval, counts[interval], now)
            for interval in ("15m", "4H", "1H", "5m")}, now


def cache_path(symbol):
    return CACHE / f"{symbol}_usdt_perp.pkl"


def save_cache(symbol, frames, fetched_at):
    import pickle
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = {"symbol": symbol, "fetched_at": fetched_at, "frames": frames,
               "counts": {key: len(value) for key, value in frames.items()}}
    with cache_path(symbol).open("wb") as handle:
        pickle.dump(payload, handle, protocol=4)
    return cache_path(symbol)


def load_cache(symbol):
    import pickle
    path = cache_path(symbol)
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("rb") as handle:
        return pickle.load(handle)


def complete_mask(frame):
    columns = [column for column in COMPLETE_COLUMNS if column in frame.columns]
    return frame[columns].notna().all(axis=1)


def classify_regimes(frame):
    atr_pct = frame.atr / frame.close
    width = (frame.boll_up - frame.boll_low) / frame.close
    mid_width = width.shift().rolling(100, min_periods=20).median()
    slope = frame.ema20 - frame.ema20.shift(5)
    return pd.Series(np.select(
        [atr_pct >= 0.03,
         atr_pct <= 0.008,
         (slope.abs() / frame.close < 0.002) & (width <= mid_width * 1.2),
         (frame.ema20 - frame.ema50) * slope < 0,
         (frame.ema20 > frame.ema50) & (frame.close > frame.ema50),
         (frame.ema20 < frame.ema50) & (frame.close < frame.ema50)],
        ["高波动行情", "低波动行情", "横盘震荡", "趋势转换阶段", "上涨趋势", "下跌趋势"],
        default="横盘震荡",
    ), index=frame.index)


def _profit_factor(outcomes):
    wins = [value for value in outcomes if value > 0]
    losses = [value for value in outcomes if value < 0]
    if not wins or not losses:
        return None
    return sum(wins) / abs(sum(losses))


def _hold_text(delta):
    if delta is None or pd.isna(delta):
        return None
    hours = delta.total_seconds() / 3600
    if hours < 24:
        return f"{hours:.1f} 小时"
    return f"{hours / 24:.1f} 天"


def regime_breakdown(trades, scored):
    if trades is None or trades.empty or "entry_time" not in trades.columns or scored.empty:
        return {}
    regimes = classify_regimes(scored).shift().fillna("未分类")
    stamp_to_regime = pd.Series(regimes.values, index=pd.DatetimeIndex(scored.time))
    counted = {}
    for entry_time, group in trades.groupby("entry_time"):
        if entry_time in stamp_to_regime.index:
            regime = stamp_to_regime.loc[entry_time]
            if isinstance(regime, pd.Series):
                regime = regime.iloc[-1]
        else:
            earlier = scored[scored.time <= entry_time]
            regime = classify_regimes(earlier).iloc[-1] if len(earlier) else "未分类"
        bucket = counted.setdefault(str(regime), {"笔数": 0, "净盈亏 USDT": 0.0})
        bucket["笔数"] += 1
        bucket["净盈亏 USDT"] += float(group.pnl.sum())
    return counted


def run_preset(frames, preset, costs, range_mode=False):
    base = aligned_frames(frames, preset.parameters())
    scored = score_with_config(base, preset.rules(), costs.fee, costs.slip,
                               market_ok=True, range_mode=range_mode)
    return scored


def slice_by_time(frame, start, end=None):
    mask = frame.time >= start
    if end is not None:
        mask &= frame.time < end
    return frame.loc[mask].reset_index(drop=True)


def metrics(stats, extra, trades, scored, costs):
    outcomes = extra.get("outcomes") or []
    row = {
        "净收益 USDT": stats["净收益 USDT"],
        "最大回撤 %": stats["最大回撤 %"],
        "交易次数": stats["交易次数"],
        "胜率 %": stats["胜率 %"],
        "平均盈亏比": stats["平均盈亏比"],
        "盈利因子": _profit_factor(outcomes),
        "官方信号数": extra["官方信号数"],
        "取消次数": extra["取消次数"],
        "期望收益 USDT": extra["期望收益 USDT"],
        "假信号比例 %": extra["假信号比例 %"],
        "平均持仓": _hold_text(extra.get("平均持仓")),
        "期末权益 USDT": costs.capital + stats["净收益 USDT"],
        "regime": regime_breakdown(trades, scored),
    }
    return row


def compare_frames(frames, costs=None, path=PRESET_PATH):
    costs = Costs() if costs is None else costs
    presets, custom_source = comparison_presets(path)
    scored = {name: run_preset(frames, preset, costs, range_mode=(name == "震荡行情型"))
              for name, preset in presets.items()}
    # Shared 15m calendar: rows where every preset has finished warmup.
    mask = complete_mask(next(iter(scored.values())))
    for frame in scored.values():
        mask &= complete_mask(frame)
    reference = next(iter(scored.values())).loc[mask]
    if len(reference) < 50:
        raise MarketDataError("预热后可用于对比的已收盘 15 分钟 K 线不足 50 根")
    times = reference.time.reset_index(drop=True)
    split_i = int(len(times) * 0.7)
    split_time = times.iloc[split_i]
    start_time, end_time = times.iloc[0], times.iloc[-1]
    results = {"presets": {}, "custom_source": custom_source,
               "window": {"start": start_time, "split": split_time, "end": end_time,
                          "bars": int(len(times)), "is_bars": split_i,
                          "oos_bars": int(len(times) - split_i)}}
    for name, preset in presets.items():
        frame = scored[name].loc[mask].reset_index(drop=True)
        is_frame = slice_by_time(frame, start_time, split_time)
        oos_frame = slice_by_time(frame, split_time)
        full_stats, _, full_trades, full_extra = simulate(frame, costs, preset.rules().minimum_rr, detail=True)
        is_stats, _, is_trades, is_extra = simulate(is_frame, costs, preset.rules().minimum_rr, detail=True)
        oos_stats, _, oos_trades, oos_extra = simulate(oos_frame, costs, preset.rules().minimum_rr, detail=True)
        results["presets"][name] = {
            "preset": preset,
            "full": metrics(full_stats, full_extra, full_trades, frame, costs),
            "is": metrics(is_stats, is_extra, is_trades, is_frame, costs),
            "oos": metrics(oos_stats, oos_extra, oos_trades, oos_frame, costs),
        }
    return results, scored, mask


def _num(value, digits=2, suffix=""):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if isinstance(value, (int, np.integer)):
        return f"{int(value)}{suffix}"
    return f"{value:.{digits}f}{suffix}"


def _beijing(stamp):
    if stamp is None:
        return "—"
    value = pd.Timestamp(stamp)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    return value.tz_convert(BEIJING).strftime("%Y-%m-%d %H:%M")


def _regime_text(breakdown):
    if not breakdown:
        return "—"
    parts = []
    for name, row in breakdown.items():
        parts.append(f"{name} {row['笔数']} 笔 / {_num(row['净盈亏 USDT'])} USDT")
    return "；".join(parts)


def render_report(jobs, costs, counts, notes=None):
    """jobs: list of {symbol, venue, results, raw_counts, fetched_at}."""
    lines = [
        "# 五种策略预设历史对比回测",
        "",
        "本报告比较五种预设在同一段已收盘历史上的模拟表现。内置参数来自规格与截图中的**初始测试值**，",
        "未经寻优，也不得称为最优。自定义一行在没有已保存配置时沿用均衡型默认值，二者应对齐。",
        "",
        "## 样本与规则",
        "",
        f"- 数据源：Bitget 官方公开 `USDT-FUTURES` REST 历史 K 线，接口费用 0。未使用 Alpaca、Twelve Data 或其他付费源。",
        f"- 标的：{('、'.join(job['symbol'] for job in jobs))}。现货与永续未混合。",
        f"- 决策周期：15 分钟已收盘 K 线；对齐 5 分钟、1 小时、4 小时已收盘数据（`merge_asof` 只使用决策时刻之前或同时收盘的高周期）。",
        f"- 目标样本：15 分钟 {counts['15m']} 根、5 分钟 {counts['5m']} 根、1 小时 {counts['1H']} 根、4 小时 {counts['4H']} 根（另含指标预热）。",
        f"- 资金与成本假设：初始资金 {costs.capital:g} USDT，单笔风险 {costs.risk:.2%}，单边手续费 {costs.fee:.2%}，单边滑点 {costs.slip:.2%}。未计入资金费率、强平、深度不足或超出假设的跳空。",
        "- 成交规则：正式候选默认收盘确认，下一根开盘价成交；同一根 K 线先处理止损，再处理止盈。不再次使用未来 K 线重绘指标或信号。",
        "- 分割方式：全部预设共用同一时间窗，按时间 70% / 30% 划分为样本内与样本外。**没有**在样本内搜索阈值或其他参数，样本外也不回看调参。",
        "- 震荡行情型启用 `range_mode`：带宽显著扩张且 1 小时与 4 小时同向时，不再按趋势方向机械给出正式候选。其他预设不启用该过滤。",
        "- 环境识别与宏观周期结论只用于对照，不自动改写预设或杠杆、单笔风险。",
        "",
        "### 各标的实际窗口",
        "",
    ]
    for job in jobs:
        window = job["results"]["window"]
        raw = job["raw_counts"]
        lines += [
            f"- **{job['symbol']}**（{job['venue']}）：抓取于 {_beijing(job['fetched_at'])}（北京时间）。"
            f"原始已收盘根数 15 分钟 {raw['15m']}、5 分钟 {raw['5m']}、1 小时 {raw['1H']}、4 小时 {raw['4H']}。"
            f"预热并对齐后进入对比的 15 分钟 K 线 {window['bars']} 根，"
            f"自 {_beijing(window['start'])} 至 {_beijing(window['end'])}（北京时间）。"
            f"样本内 {window['is_bars']} 根（至 {_beijing(window['split'])}），"
            f"样本外 {window['oos_bars']} 根。自定义来源：{('已保存配置' if job['results']['custom_source'] == 'saved' else '无已保存配置，沿用均衡型默认值')}。",
        ]
    lines += ["", "## 结果总表", ""]

    for job in jobs:
        symbol = job["symbol"]
        lines += [f"### {symbol}", ""]
        for section, title in (("full", "全样本"), ("is", "样本内（70%）"), ("oos", "样本外（30%）")):
            lines += [
                f"#### {title}",
                "",
                "| 预设 | 官方信号 | 成交笔数 | 取消次数 | 净收益 USDT | 最大回撤 % | 胜率 % | 平均盈亏比 | 盈利因子 | 期望收益 USDT | 假信号比例 % | 平均持仓 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for name in COMPARE_NAMES:
                row = job["results"]["presets"][name][section]
                lines.append(
                    f"| {name} | {_num(row['官方信号数'], 0)} | {_num(row['交易次数'], 0)} | {_num(row['取消次数'], 0)} | "
                    f"{_num(row['净收益 USDT'])} | {_num(row['最大回撤 %'])} | {_num(row['胜率 %'])} | "
                    f"{_num(row['平均盈亏比'])} | {_num(row['盈利因子'])} | {_num(row['期望收益 USDT'])} | "
                    f"{_num(row['假信号比例 %'])} | {row['平均持仓'] or '—'} |"
                )
            lines.append("")
        lines += ["样本外按入场时环境分类（仅描述，不用于调参）：", ""]
        for name in COMPARE_NAMES:
            lines.append(f"- {name}：{_regime_text(job['results']['presets'][name]['oos']['regime'])}")
        lines.append("")

    # Integrity check
    lines += ["## 完整性核对", "",]
    for job in jobs:
        balanced = job["results"]["presets"]["均衡型"]["full"]
        custom = job["results"]["presets"]["自定义"]["full"]
        same = (job["results"]["custom_source"] == "default_balanced"
                and balanced["净收益 USDT"] == custom["净收益 USDT"]
                and balanced["交易次数"] == custom["交易次数"]
                and balanced["官方信号数"] == custom["官方信号数"])
        if job["results"]["custom_source"] == "default_balanced":
            lines.append(f"- {job['symbol']}：无已保存自定义配置时，自定义与均衡型全样本净收益、成交笔数、官方信号数"
                         f"{'一致' if same else '不一致，需检查实现'}。")
        else:
            lines.append(f"- {job['symbol']}：使用了已保存的自定义配置，因此不必与均衡型相同。")
    lines += [
        "",
        "## 局限与不应得出的结论",
        "",
        "- 这是单一时间窗、单一拆分的研究回测，不是走步优化，也不是实盘成交记录。",
        "- 样本主要覆盖抓取日前约一个多月的 BTC/ETH USDT 永续行情，不能代表全部市场状态，也不平衡上涨、下跌、震荡与高波动的出现次数。",
        "- 参数未经网格或贝叶斯搜索，报告中较好的一行不表示该预设在其他区间或品种上更好。",
        "- 成本模型是固定比例手续费与滑点，未包含资金费率、盘口冲击、部分成交或限价单。",
        "- 每次独立模拟从空仓开始；样本末未平仓按末根收盘并扣假设退出成本计入权益，但不计入已完成交易次数、胜率和期望收益。因此全样本不等于两段结果相加。",
        "- 官方信号数包含持仓期间重复候选，不等于实际进场数；假信号比例仅定义为（取消入场 + 已平仓亏损笔数）/（取消入场 + 已平仓笔数），不是预测错误率。",
        "- 持仓时长按开盘到退出所在 K 线开盘时间估算，存在最多一根 15 分钟 K 线的时间误差。盈利因子在无盈利或无亏损时显示空值。",
        "- 回测只评估主评分与结构止盈止损计划，不叠加 EMA×布林、回踩、动态止损五大模块的独立计划，也不使用宏观或斐波那契过滤。",
        "- 不得根据本表自动提高杠杆或单笔风险，也不得把结果写成实盘收益。",
        "",
        "## 如何复现",
        "",
        "```bash",
        "cd quant_signal_lab_online",
        "python preset_backtest.py",
        "```",
        "",
        "公开 REST 只读，不需要交易密钥。默认会把原始 K 线缓存到 `data/cache/`（该目录不入库），可用 `--refresh` 强制重新抓取。",
    ]
    if notes:
        lines += ["", "## 运行附记", ""] + [f"- {note}" for note in notes]
    return "\n".join(lines) + "\n"


def results_to_json(jobs, costs):
    payload = {"costs": {"capital": costs.capital, "risk": costs.risk, "fee": costs.fee, "slip": costs.slip},
               "jobs": []}
    for job in jobs:
        window = job["results"]["window"]
        item = {
            "symbol": job["symbol"], "venue": job["venue"],
            "fetched_at": str(job["fetched_at"]),
            "raw_counts": job["raw_counts"],
            "custom_source": job["results"]["custom_source"],
            "window": {key: (str(value) if hasattr(value, "isoformat") else value)
                       for key, value in window.items()},
            "presets": {},
        }
        for name, block in job["results"]["presets"].items():
            item["presets"][name] = {section: {k: (v if not isinstance(v, dict) else v)
                                               for k, v in block[section].items() if k != "regime"}
                                     | {"regime": block[section]["regime"]}
                                     for section in ("full", "is", "oos")}
        payload["jobs"].append(item)
    return payload


def run(symbols=("BTCUSDT", "ETHUSDT"), costs=None, counts=COUNTS, refresh=False, path=PRESET_PATH):
    costs = Costs() if costs is None else costs
    jobs, notes = [], []
    for symbol in symbols:
        used_cache = False
        if not refresh:
            try:
                cached = load_cache(symbol)
                frames, fetched_at = cached["frames"], cached["fetched_at"]
                used_cache = True
                notes.append(f"{symbol} 使用本地缓存（抓取时间 {_beijing(fetched_at)} 北京时间）。")
            except FileNotFoundError:
                frames, fetched_at = fetch_shared_frames(symbol, counts)
                save_cache(symbol, frames, fetched_at)
        else:
            frames, fetched_at = fetch_shared_frames(symbol, counts)
            save_cache(symbol, frames, fetched_at)
        results, _, _ = compare_frames(frames, costs, path)
        jobs.append({
            "symbol": symbol,
            "venue": "Bitget USDT 本位永续",
            "results": results,
            "raw_counts": {key: int(len(value)) for key, value in frames.items()},
            "fetched_at": fetched_at,
            "cached": used_cache,
        })
    return jobs, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description="五种预设历史对比回测（不寻优，不交易）")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存并重新抓取公开历史")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "PRESET_COMPARISON.md"))
    args = parser.parse_args(argv)
    costs = Costs()
    jobs, notes = run(refresh=args.refresh)
    report = render_report(jobs, costs, COUNTS, notes)
    output = Path(args.output)
    output.write_text(report, encoding="utf-8")
    sidecar = output.with_suffix(".json")
    sidecar.write_text(json.dumps(results_to_json(jobs, costs), ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
    print(report)
    print(f"wrote {output} and {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
