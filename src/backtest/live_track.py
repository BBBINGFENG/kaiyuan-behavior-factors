# -*- coding: utf-8 -*-
"""
Live 每日盯市 — 合成因子多空组合今年(2026)的每日净值

月度回测是"月末→月末"复利; 这里把合成因子的月度持仓【按日盯市】, 得到今年以来
的每日净值/回撤/实时统计, 供 dashboard 的"Live"主视图。

策略: 合成因子多空(买最高20%、卖最低20%, 等权), 月末调仓、次日生效, 组合间持仓不变。
产出: backtest/live/live_nav.parquet, live_stats.json
用法: python src/backtest/live_track.py
"""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("live")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FAC = ROOT / "factors"
OUT = ROOT / "backtest" / "live"
OUT.mkdir(parents=True, exist_ok=True)

LIVE_YEAR = "2026"
TOP = 0.20
FEE = 0.002          # 单边 20bps(对齐参考站口径)
DPY = 252


def compute():
    px = pd.read_parquet(DC / "px_close_hfq.parquet")
    ret = px / px.shift(1) - 1.0                       # 每日收益
    comp = pd.read_parquet(FAC / "composite.parquet")
    mask_d = pd.read_parquet(DC / "investable_mask.parquet")
    mask_r = mask_d.reindex(comp.index)                # 掩码采样到调仓日

    # 各调仓日的多/空持仓(从形成今年首个持仓的上年末调仓日起)
    rebals = [d for d in comp.index if d >= f"{int(LIVE_YEAR)-1}1201"]
    hold = {}
    for d in rebals:
        s = comp.loc[d].where(mask_r.loc[d]).dropna()
        if len(s) < 100:
            continue
        k = len(s) // 5
        hold[d] = (list(s.nlargest(k).index), list(s.nsmallest(k).index))
    reb_arr = sorted(hold)

    days = [d for d in px.index if d >= f"{LIVE_YEAR}0101"]
    navdates, rets = [], []
    prev_active = None
    for day in days:
        active = max([r for r in reb_arr if r < day], default=reb_arr[0])
        longs, shorts = hold[active]
        rl = ret.loc[day, longs].mean()
        rs = ret.loc[day, shorts].mean()
        r = float((rl - rs))
        if np.isnan(r):
            continue
        # 调仓生效日(active 变化)扣双腿换手成本
        if prev_active is not None and active != prev_active:
            pl, ps = hold[prev_active]
            to = (len(set(longs) - set(pl)) / max(len(longs), 1)
                  + len(set(shorts) - set(ps)) / max(len(shorts), 1))
            r -= to * FEE
        prev_active = active
        navdates.append(day)
        rets.append(r)

    rs = pd.Series(rets, index=navdates)
    nav = (1 + rs).cumprod()
    dd = nav / nav.cummax() - 1
    n = len(rs)
    stats = {
        "total": float(nav.iloc[-1] - 1),
        "ann": float(nav.iloc[-1] ** (DPY / n) - 1),
        "vol": float(rs.std() * np.sqrt(DPY)),
        "sharpe": round(float((nav.iloc[-1] ** (DPY / n) - 1) / (rs.std() * np.sqrt(DPY))), 2),
        "mdd": float(dd.min()),
        "win": float((rs > 0).mean()),
        "days": n,
        "start": navdates[0], "end": navdates[-1],
    }
    out = pd.DataFrame({"nav": nav, "dd": dd})
    out.to_parquet(OUT / "live_nav.parquet")
    (OUT / "live_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    logger.info("Live 完成: %s~%s (%d日) 累计%.1f%% 年化%.1f%% Sharpe%.2f 回撤%.1f%%",
                stats["start"], stats["end"], n, stats["total"]*100,
                stats["ann"]*100, stats["sharpe"], stats["mdd"]*100)
    return out, stats


if __name__ == "__main__":
    compute()
