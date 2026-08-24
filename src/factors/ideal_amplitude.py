# -*- coding: utf-8 -*-
"""
阶段B.1：理想振幅因子 V(λ=25%)

因子逻辑: 高价态振幅承载负向alpha, 低价态振幅是噪声, 作差提纯。
  V = V_high − V_low
  V_high = 收盘价最高的25%有效交易日的振幅均值
  V_low  = 收盘价最低的25%有效交易日的振幅均值
负向因子(IC为负): V越大 → 未来收益越低。

关键口径(对齐月报生产版):
  - 回看窗口 = 最近20个交易日
  - 日振幅 amp = high/low − 1  (复权无关, 分子分母抵消)
  - "有效交易日" = 剔除停牌(振幅NaN)和一字涨跌停(振幅=0)后的天 → 即 amp > 0
  - 排序用【后复权】收盘价 (否则除权日假跳空会污染高低价分组)
  - λ = 25%: 取有效日中收盘价最高/最低各 round(0.25×有效日数) 天
  - 有效日 < 10 → 该股当期因子置 NaN

产出: factors/ideal_amplitude.parquet  (调仓日 × 股票)

用法: python src/factors/ideal_amplitude.py          # 计算并落盘
      python src/factors/ideal_amplitude.py check    # 抽样手算核对
"""
import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("ideal_amplitude")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_CLEAN = PROJECT_ROOT / "data_clean"
FACTOR_DIR = PROJECT_ROOT / "factors"
FACTOR_DIR.mkdir(parents=True, exist_ok=True)

LAMBDA = 0.25
WINDOW = 20
MIN_VALID = 10


def _load_inputs():
    high = pd.read_parquet(DATA_CLEAN / "px_high_raw.parquet")
    low = pd.read_parquet(DATA_CLEAN / "px_low_raw.parquet")
    close_hfq = pd.read_parquet(DATA_CLEAN / "px_close_hfq.parquet")
    rebal = pd.read_parquet(DATA_CLEAN / "rebalance_dates.parquet")["rebalance_date"].tolist()
    return high, low, close_hfq, rebal


def compute(save=True):
    high, low, close_hfq, rebal = _load_inputs()
    amp = high / low - 1.0                      # 日振幅(复权无关)
    dates = amp.index
    pos = {d: i for i, d in enumerate(dates)}

    out = {}
    for d in rebal:
        i = pos.get(d)
        if i is None or i < WINDOW - 1:
            continue
        win = dates[i - WINDOW + 1: i + 1]      # 截止本调仓日的20个交易日
        amp_w = amp.loc[win]                    # 20 × 股票
        cls_w = close_hfq.loc[win]

        valid = amp_w > 0                        # 有效日: 剔除停牌(NaN)与一字板(amp=0)
        n_valid = valid.sum(axis=0)             # 每只股票的有效日数

        # 在有效日内按复权收盘价排名, 取最高/最低各 k 天
        cls_v = cls_w.where(valid)              # 无效日置NaN, 不参与排序
        rank_hi = cls_v.rank(axis=0, ascending=False, method="first")   # 1=收盘价最高
        rank_lo = cls_v.rank(axis=0, ascending=True, method="first")    # 1=收盘价最低
        k = (LAMBDA * n_valid).round().clip(lower=1)                    # 每股取几天(≥1)

        v_high = amp_w.where(rank_hi.le(k, axis=1)).mean(axis=0)        # 高价态振幅均值
        v_low = amp_w.where(rank_lo.le(k, axis=1)).mean(axis=0)         # 低价态振幅均值
        v = v_high - v_low
        v[n_valid < MIN_VALID] = np.nan          # 有效日不足10 → 置空
        out[d] = v

    factor = pd.DataFrame(out).T
    factor.index.name = "trade_date"
    if save:
        factor.astype("float32").to_parquet(FACTOR_DIR / "ideal_amplitude.parquet")
        cov = factor.notna().sum(axis=1)
        logger.info("理想振幅已落盘: shape=%s, 平均每期有效因子值 %.0f 只",
                    factor.shape, cov.mean())
        logger.info("因子值分布: mean=%.4f std=%.4f (V=V_high−V_low, 应集中在0附近)",
                    factor.stack().mean(), factor.stack().std())
    return factor


def manual_check(code="000001.SZ", date="20200731"):
    """抽一只股票、一个调仓日, 把20天明细拉出来手算, 和 compute() 对比。"""
    high, low, close_hfq, _ = _load_inputs()
    amp = high / low - 1.0
    dates = amp.index
    i = list(dates).index(date)
    win = dates[i - WINDOW + 1: i + 1]

    tbl = pd.DataFrame({
        "close_hfq": close_hfq.loc[win, code],
        "high": high.loc[win, code],
        "low": low.loc[win, code],
        "amp": amp.loc[win, code],
    })
    tbl["valid"] = tbl["amp"] > 0
    valid = tbl[tbl["valid"]].copy()
    n = len(valid)
    k = max(1, round(LAMBDA * n))
    hi_days = valid.nlargest(k, "close_hfq")
    lo_days = valid.nsmallest(k, "close_hfq")
    v_high, v_low = hi_days["amp"].mean(), lo_days["amp"].mean()

    print(f"=== 手算核对 {code} @ {date} ===")
    print(tbl.round(4).to_string())
    print(f"\n有效日 n={n}, k=round(0.25×{n})={k}")
    print(f"高价{k}天(收盘价最高)的振幅: {hi_days['amp'].round(4).tolist()} → V_high={v_high:.5f}")
    print(f"低价{k}天(收盘价最低)的振幅: {lo_days['amp'].round(4).tolist()} → V_low ={v_low:.5f}")
    print(f"手算 V = {v_high:.5f} − {v_low:.5f} = {v_high - v_low:.5f}")
    prog = compute(save=False).loc[date, code]
    print(f"程序 V = {prog:.5f}   →  一致: {np.isclose(v_high - v_low, prog, atol=1e-5)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        manual_check()
    else:
        compute()
