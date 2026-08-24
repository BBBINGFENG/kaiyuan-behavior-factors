# -*- coding: utf-8 -*-
"""
Long-Only 组合回测

多空对冲是市场中性的研究口径; 这里做【可投资的纯多头组合】: 只买每个因子排名最优的
一篮子股票(不做空), 等权/市值加权, 月度调仓, 扣单边成本。含市场基准(中证全指)对比。

策略:
  合成因子 Top20% (EW / VW)   —— 买合成分最高的20%
  理想振幅 Top20% (EW)         —— 买振幅因子最优(值最低)的20%
  理想反转 Top20% (EW)         —— 买反转因子最优(值最低)的20%
  基准: 中证全指

产出: backtest/long_only/nav.parquet, stats.json
用法: python src/backtest/long_only.py
"""
import sys
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factors.neutralize import build_mv_at_rebal   # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("long_only")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FAC = ROOT / "factors"
OUT = ROOT / "backtest" / "long_only"
OUT.mkdir(parents=True, exist_ok=True)

TOP = 0.20            # 买排名最优的前20%
FEE = 0.003          # 双边成本(单边换手×费率, 近似双边千三)
PPY = 12


def long_only_ret(factor, fwd, mask, direction, mv=None, vw=False):
    """纯多头组合的月度收益 + 单边换手。"""
    rets, prev = {}, set()
    to = {}
    for d in factor.index:
        f = factor.loc[d].where(mask.loc[d])
        df = pd.concat([f.rename("f"), fwd.loc[d].rename("r")], axis=1).dropna()
        if len(df) < 100:
            continue
        k = int(len(df) * TOP)
        sel = (df["f"] * direction).nlargest(k).index          # 方向对齐后取最高分
        if vw and mv is not None:
            w = mv.loc[d, sel].reindex(sel).fillna(0.0)
            w = w / w.sum() if w.sum() > 0 else pd.Series(1.0 / len(sel), index=sel)
            rets[d] = float((df.loc[sel, "r"] * w).sum())
        else:
            rets[d] = float(df.loc[sel, "r"].mean())
        cur = set(sel)
        to[d] = len(cur - prev) / len(cur) if cur else 0.0
        prev = cur
    r = pd.Series(rets)
    cost = pd.Series(to).reindex(r.index).fillna(0) * FEE
    return r - cost


def _stats(r):
    r = r.dropna()
    nav = (1 + r).cumprod()
    ann = float(nav.iloc[-1] ** (PPY / len(r)) - 1)
    vol = float(r.std() * np.sqrt(PPY))
    return {"total": float(nav.iloc[-1] - 1), "ann": ann, "vol": vol,
            "sharpe": round(ann / vol, 2) if vol else 0.0,
            "mdd": float((nav / nav.cummax() - 1).min()),
            "months": int(len(r))}


def benchmark_ret(dates):
    """中证全指月度收益(与因子同口径: 本调仓日→下调仓日)。"""
    fp = ROOT / "data_raw" / "index_daily" / "000985_CSI.parquet"
    idx = pd.read_parquet(fp).set_index("trade_date")["close"].sort_index()
    px = idx.reindex(dates)
    return (px.shift(-1) / px - 1).dropna()


def main():
    fwd = pd.read_parquet(DC / "fwd_ret_monthly.parquet")
    mask = pd.read_parquet(DC / "investable_mask.parquet")
    comp = pd.read_parquet(FAC / "composite.parquet")
    amp = pd.read_parquet(FAC / "ideal_amplitude_neutral.parquet")
    rev = pd.read_parquet(FAC / "ideal_reversal_neutral.parquet")
    mv = build_mv_at_rebal(comp.index.tolist())

    strat = {
        "合成因子 Top20% EW": long_only_ret(comp, fwd, mask, +1),
        "合成因子 Top20% VW": long_only_ret(comp, fwd, mask, +1, mv=mv, vw=True),
        "理想振幅 Top20% EW": long_only_ret(amp, fwd, mask, -1),
        "理想反转 Top20% EW": long_only_ret(rev, fwd, mask, -1),
    }
    # 对齐到合成因子的公共区间(可比)
    start = comp.index[0]
    for k in strat:
        strat[k] = strat[k][strat[k].index >= start]
    bench = benchmark_ret(sorted(set().union(*[s.index for s in strat.values()])))
    bench = bench[bench.index >= start]
    strat["中证全指(基准)"] = bench

    # NAV(归一到1) + 统计
    navs, stats = {}, {}
    common = sorted(set.intersection(*[set(s.index) for s in strat.values()]))
    for name, r in strat.items():
        rr = r.reindex(common).dropna()
        navs[name] = (1 + rr).cumprod()
        stats[name] = _stats(rr)

    nav_df = pd.DataFrame(navs)
    nav_df.to_parquet(OUT / "nav.parquet")
    (OUT / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    logger.info("Long-Only 完成: %d 个策略, 区间 %s~%s", len(strat), common[0], common[-1])
    for name, st in stats.items():
        logger.info("  %-20s 年化%.1f%% 波动%.1f%% Sharpe%.2f 回撤%.1f%%",
                    name, st["ann"] * 100, st["vol"] * 100, st["sharpe"], st["mdd"] * 100)


if __name__ == "__main__":
    main()
