# -*- coding: utf-8 -*-
"""
阶段D：交易行为合成因子 (2因子)

方法(对齐研报):
  1. 方向对齐: 两个都是负向因子, 乘 −1 使"越大越好"(合成后为正向)。
  2. 行业内标准化: 每期在行业内去极值(1/99分位)+ z-score。
  3. 权重: 过去12期各因子 ICIR(方向对齐后为正) 滚动加权; 负权重截0。
  4. 合成 = Σ 权重 × 标准化因子。

产出: factors/composite.parquet
用法: python src/factors/composite.py
"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("composite")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FACTOR_DIR = ROOT / "factors"

# 参与合成的因子及其方向(都是负向 → -1)。用中性化后的版本。
MEMBERS = {"ideal_amplitude_neutral": -1, "ideal_reversal_neutral": -1}
ICIR_WINDOW = 12


def _winsor_zscore_by_industry(f, industry):
    """行业内去极值(1/99)+ z-score。"""
    df = pd.concat([f.rename("f"), industry.rename("ind")], axis=1).dropna()
    def _std(x):
        x = x.clip(x.quantile(0.01), x.quantile(0.99))
        sd = x.std()
        return (x - x.mean()) / sd if sd > 0 else x * 0.0
    return df.groupby("ind")["f"].transform(_std)


def _ic_series(fac, fwd, mask):
    m = mask.reindex(fac.index)
    ic = {}
    for d in fac.index:
        df = pd.concat([fac.loc[d].where(m.loc[d]), fwd.loc[d]], axis=1).dropna()
        if len(df) > 100:
            ic[d] = df.iloc[:, 0].corr(df.iloc[:, 1])
    return pd.Series(ic)


def build():
    fwd = pd.read_parquet(DC / "fwd_ret_monthly.parquet")
    mask = pd.read_parquet(DC / "investable_mask.parquet")
    industry = pd.read_parquet(DC / "industry_panel.parquet")

    facs, aligned_ic = {}, {}
    for name, sign in MEMBERS.items():
        f = pd.read_parquet(FACTOR_DIR / f"{name}.parquet")
        facs[name] = f * sign                                  # 方向对齐(越大越好)
        aligned_ic[name] = _ic_series(f, fwd, mask) * sign     # 对齐后IC(应为正)

    dates = sorted(set.intersection(*[set(f.index) for f in facs.values()]))
    ind_r = industry.reindex(dates)
    out = {}
    for i, d in enumerate(dates):
        if i < ICIR_WINDOW:                                    # 前12期无足够历史算权重
            continue
        std_facs, weights = {}, {}
        for name in MEMBERS:
            std_facs[name] = _winsor_zscore_by_industry(facs[name].loc[d], ind_r.loc[d])
            past = aligned_ic[name].loc[:d].iloc[-ICIR_WINDOW:]   # 过去12期对齐IC
            icir = past.mean() / past.std() if past.std() > 0 else 0.0
            weights[name] = max(icir, 0.0)                        # 负权重截0
        wsum = sum(weights.values()) or 1.0
        comp = sum((weights[n] / wsum) * std_facs[n] for n in MEMBERS)
        out[d] = comp
    composite = pd.DataFrame(out).T
    composite.index.name = "trade_date"
    composite.astype("float32").to_parquet(FACTOR_DIR / "composite.parquet")
    logger.info("合成因子完成: shape=%s, 平均每期 %.0f 只 (前%d期无权重跳过)",
                composite.shape, composite.notna().sum(axis=1).mean(), ICIR_WINDOW)


if __name__ == "__main__":
    build()
