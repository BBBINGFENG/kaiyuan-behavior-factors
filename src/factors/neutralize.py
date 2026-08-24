# -*- coding: utf-8 -*-
"""
阶段C.4：行业市值中性化

为什么: 裸因子可能只是变相的市值/行业暴露。中性化=把因子对 log流通市值+行业哑变量
        做横截面回归, 取【残差】作为中性化因子——剔除已知风格后剩下的才是纯 alpha。
        研报月报即"行业市值中性"口径, 中性化后我方 IC 应更贴月报。

产出: factors/<因子>_neutral.parquet
用法: python src/factors/neutralize.py ideal_amplitude ideal_reversal
"""
import sys
import glob
import logging
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")   # 抑制 lstsq 的 rank 警告刷屏

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("neutralize")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FACTOR_DIR = ROOT / "factors"


def build_mv_at_rebal(rebal):
    """流通市值面板, 采样到调仓日。"""
    files = sorted((ROOT / "data_raw" / "daily_basic").glob("*.parquet"))
    db = pd.concat([pd.read_parquet(f, columns=["ts_code", "trade_date", "circ_mv"])
                    for f in files], ignore_index=True)
    mv = db.pivot(index="trade_date", columns="ts_code", values="circ_mv").sort_index()
    return mv.reindex(rebal)


def _winsor(s, lo=0.01, hi=0.99):
    ql, qh = s.quantile(lo), s.quantile(hi)
    return s.clip(ql, qh)


def neutralize(factor, mv, industry, mask):
    """逐调仓日: 因子(去极值) 对 [log流通市值 + 行业哑变量] 回归, 残差=中性化因子。"""
    out = {}
    for d in factor.index:
        f = _winsor(factor.loc[d].where(mask.loc[d]).dropna())
        lmv = np.log(mv.loc[d])
        ind = industry.loc[d]
        df = pd.concat([f.rename("f"), lmv.rename("lmv"), ind.rename("ind")],
                       axis=1).dropna()
        if len(df) < 100:
            continue
        X = pd.get_dummies(df["ind"], drop_first=True).astype(float)
        X["lmv"] = df["lmv"].values
        X["const"] = 1.0
        beta, *_ = np.linalg.lstsq(X.values, df["f"].values, rcond=None)
        resid = df["f"].values - X.values @ beta
        out[d] = pd.Series(resid, index=df.index)
    return pd.DataFrame(out).T


def run(factor_name):
    factor = pd.read_parquet(FACTOR_DIR / f"{factor_name}.parquet")
    mask = pd.read_parquet(DC / "investable_mask.parquet").reindex(factor.index)
    industry = pd.read_parquet(DC / "industry_panel.parquet").reindex(factor.index)
    mv = build_mv_at_rebal(factor.index.tolist())
    neut = neutralize(factor, mv, industry, mask)
    neut.astype("float32").to_parquet(FACTOR_DIR / f"{factor_name}_neutral.parquet")
    logger.info("%s 中性化完成: shape=%s, 平均每期 %.0f 只",
                factor_name, neut.shape, neut.notna().sum(axis=1).mean())


if __name__ == "__main__":
    for name in (sys.argv[1:] or ["ideal_amplitude", "ideal_reversal"]):
        run(name)
