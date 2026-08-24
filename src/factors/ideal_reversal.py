# -*- coding: utf-8 -*-
"""
阶段B.2：理想反转因子 M (W式切割) —— moneyflow 大单占比【替代口径】

原口径: 按"平均单笔成交金额 = 成交额/成交笔数"给20天排序, 大单主导的10天涨跌幅
        加总=M_high, 小单主导的10天=M_low, M=M_high−M_low。
替代口径(Tushare 无成交笔数, manager已批准): 用 moneyflow 的"大单成交占比"替代
        "平均单笔成交金额"作为切割标准。逻辑一致: 反转之力源于大单。
  大单占比 = (buy_lg+sell_lg+buy_elg+sell_elg) / Σ8档买卖额   (分母不含 net_mf_amount)

负向因子(IC为负): M越大 → 未来收益越低。
★这是替代口径, 复现结果注定与研报有偏差, 属预期内; 另有 iFinD 2022-2026 真成交
  笔数可做重叠窗口验证(见 notebooks)。★

产出: factors/ideal_reversal.parquet  (调仓日 × 股票)
用法: python src/factors/ideal_reversal.py [check]
"""
import sys
import glob
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("ideal_reversal")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_RAW = PROJECT_ROOT / "data_raw"
DATA_CLEAN = PROJECT_ROOT / "data_clean"
FACTOR_DIR = PROJECT_ROOT / "factors"
FACTOR_DIR.mkdir(parents=True, exist_ok=True)

WINDOW = 20
MIN_VALID = 15          # 20日窗口内有效日(大单占比与涨跌幅都有)至少15天
BIG = ["buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"]
ALL8 = ["buy_sm_amount", "sell_sm_amount", "buy_md_amount", "sell_md_amount",
        "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"]


def _build_big_ratio():
    """构建"大单成交占比"面板(日期×股票)。切割标准, 替代平均单笔成交金额。"""
    files = sorted((DATA_RAW / "moneyflow").glob("*.parquet"))
    mf = pd.concat([pd.read_parquet(f, columns=["ts_code", "trade_date"] + ALL8)
                    for f in files], ignore_index=True)
    big = mf[BIG].sum(axis=1)
    tot = mf[ALL8].sum(axis=1)
    mf["ratio"] = np.where(tot > 0, big / tot, np.nan)
    return mf.pivot(index="trade_date", columns="ts_code", values="ratio").sort_index()


def _load_inputs():
    ratio = _build_big_ratio()
    pct = pd.read_parquet(DATA_CLEAN / "pct_chg.parquet")
    rebal = pd.read_parquet(DATA_CLEAN / "rebalance_dates.parquet")["rebalance_date"].tolist()
    # 对齐两张面板的股票列(moneyflow 与 daily 股票集可能略有出入)
    cols = ratio.columns.union(pct.columns)
    ratio = ratio.reindex(columns=cols)
    pct = pct.reindex(columns=cols)
    return ratio, pct, rebal


def compute(save=True, _inputs=None):
    ratio, pct, rebal = _inputs or _load_inputs()
    dates = pct.index
    pos = {d: i for i, d in enumerate(dates)}
    out = {}
    for d in rebal:
        i = pos.get(d)
        if i is None or i < WINDOW - 1:
            continue
        win = dates[i - WINDOW + 1: i + 1]
        r_w = ratio.reindex(win)            # 20 × 股票 大单占比
        p_w = pct.reindex(win)              # 20 × 股票 涨跌幅
        valid = r_w.notna() & p_w.notna()
        n_valid = valid.sum(axis=0)

        r_v = r_w.where(valid)
        rank_hi = r_v.rank(axis=0, ascending=False, method="first")  # 1=大单占比最高
        rank_lo = r_v.rank(axis=0, ascending=True, method="first")   # 1=大单占比最低
        k = (n_valid // 2).clip(lower=1)     # 上下各取一半(n=20→10, 对齐原口径)

        m_high = p_w.where(rank_hi.le(k, axis=1)).sum(axis=0)   # 大单主导日 涨跌幅加总
        m_low = p_w.where(rank_lo.le(k, axis=1)).sum(axis=0)    # 小单主导日 涨跌幅加总
        m = m_high - m_low
        m[n_valid < MIN_VALID] = np.nan
        out[d] = m

    factor = pd.DataFrame(out).T
    factor.index.name = "trade_date"
    if save:
        factor.astype("float32").to_parquet(FACTOR_DIR / "ideal_reversal.parquet")
        logger.info("理想反转已落盘: shape=%s, 平均每期有效 %.0f 只",
                    factor.shape, factor.notna().sum(axis=1).mean())
        hs = [c for c in factor.columns if c.endswith((".SH", ".SZ"))]
        s = factor[hs].stack()
        logger.info("因子值分布(沪深): mean=%.3f std=%.3f", s.mean(), s.std())
    return factor


def manual_check(code="000001.SZ", date="20200731"):
    ratio, pct, rebal = _load_inputs()
    dates = pct.index
    i = list(dates).index(date)
    win = dates[i - WINDOW + 1: i + 1]
    tbl = pd.DataFrame({"big_ratio": ratio.reindex(win)[code],
                        "pct_chg": pct.reindex(win)[code]})
    tbl["valid"] = tbl["big_ratio"].notna() & tbl["pct_chg"].notna()
    v = tbl[tbl["valid"]].copy()
    k = len(v) // 2
    hi = v.nlargest(k, "big_ratio")
    lo = v.nsmallest(k, "big_ratio")
    m_hand = hi["pct_chg"].sum() - lo["pct_chg"].sum()
    print(f"=== 手算核对 {code} @ {date} ===")
    print(tbl.round(4).to_string())
    print(f"\n有效日 n={len(v)}, 上下各取 k={k}")
    print(f"大单占比最高{k}天 涨跌幅和 M_high = {hi['pct_chg'].sum():.4f}")
    print(f"大单占比最低{k}天 涨跌幅和 M_low  = {lo['pct_chg'].sum():.4f}")
    print(f"手算 M = {m_hand:.4f}")
    prog = compute(save=False, _inputs=(ratio, pct, rebal)).loc[date, code]
    print(f"程序 M = {prog:.4f}   →  一致: {np.isclose(m_hand, prog, atol=1e-3)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        manual_check()
    else:
        compute()
