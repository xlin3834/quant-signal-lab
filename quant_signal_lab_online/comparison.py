"""Report per-field differences for identical, caller-selected instrument identities."""
import numpy as np
import pandas as pd

from indicators import Parameters, calculate

FIELDS = ("open", "high", "low", "close", "volume", "close_time")
INDICATORS = ("ema7", "ema20", "ema50", "ema200", "boll_mid", "boll_up", "boll_low",
              "rsi", "macd", "macd_signal", "atr", "vol_avg")


def compare(left, reference, params=Parameters(), compare_indicators=True):
    required = {"time", *FIELDS}
    datasets = []
    for dataset in (left, reference):
        if not required.issubset(dataset.columns):
            raise ValueError("行情参考必须包含 time、close_time、OHLC 和 volume")
        dataset = dataset.copy()
        for column in ("time", "close_time"):
            values = dataset[column].map(pd.Timestamp)
            if any(stamp.tzinfo is None for stamp in values if not pd.isna(stamp)):
                raise ValueError("参考行情时间须包含时区")
            dataset[column] = pd.to_datetime(values, utc=True, errors="raise")
        if dataset.time.isna().any() or dataset.close_time.isna().any() or dataset.time.duplicated().any():
            raise ValueError("参考 K 线包含重复或缺失时间")
        for column in FIELDS[:-1]:
            dataset[column] = pd.to_numeric(dataset[column], errors="raise")
        dataset = dataset.sort_values("time")
        if compare_indicators:
            computed = calculate(dataset, params)
            if datasets:
                for field in INDICATORS:
                    if field in dataset:
                        computed[field] = pd.to_numeric(dataset[field], errors="raise")
            dataset = computed
        datasets.append(dataset)
    fields = FIELDS + (INDICATORS if compare_indicators else ())
    joined = datasets[0][["time", *fields]].merge(
        datasets[1][["time", *fields]], on="time", suffixes=("_本地", "_参考"), how="outer", indicator=True)
    reports = []
    for _, row in joined[joined["_merge"] != "both"].iterrows():
        reports.append({"时间 UTC": row.time, "字段": "开盘时间", "原因": "本地缺少" if row["_merge"] == "right_only" else "参考缺少"})
    common = joined[joined["_merge"] == "both"]
    for field in fields:
        local, external = common[f"{field}_本地"], common[f"{field}_参考"]
        same = local.eq(external) if field == "close_time" else np.isclose(
            local, external, rtol=1e-8, atol=1e-10, equal_nan=True)
        for i in common.index[~same]:
            reports.append({"时间 UTC": common.at[i, "time"], "字段": field, "本地": local.at[i],
                            "参考": external.at[i], "原因": "指标公式或预热差异" if field in INDICATORS else
                            "已对齐时间，需核对数据源、时段、复权及成交量单位"})
    return len(common), pd.DataFrame(reports, columns=["时间 UTC", "字段", "本地", "参考", "原因"])
