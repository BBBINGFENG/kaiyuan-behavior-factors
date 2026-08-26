# -*- coding: utf-8 -*-
"""
阶段F：dashboard 数据生成

只生成 docs/data/dashboard_data.js（window.DASHBOARD_DATA）。
index.html / assets(style.css, chart.umd.js, app.js) 均为静态文件, 不由此脚本生成。
设计沿用 china-value-dashboard(浅色暖调 + Chart.js 本地打包)。

聚焦今年 live 表现: Live 净值/回撤/关键指标为主, 历史回测为辅。
用法: python src/website/build_dashboard.py
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dashboard")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FAC = ROOT / "factors"
BT = ROOT / "backtest"
DATAJS = ROOT / "docs" / "data" / "dashboard_data.js"
DATAJS.parent.mkdir(parents=True, exist_ok=True)

SPLIT = "20201231"
PPY = 12

FACTORS = [("composite", "合成因子", 1), ("ideal_amplitude_neutral", "理想振幅", -1),
           ("ideal_reversal_neutral", "理想反转", -1)]


def _fmt(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def nw_tstat(r, lags=6):
    r = pd.Series(r).dropna().values
    n = len(r)
    if n < 12:
        return float("nan")
    e = r - r.mean()
    s = (e @ e) / n
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    return float(r.mean() / np.sqrt(s / n))


def _ls(name):
    return pd.read_parquet(BT / name / "ls_returns.parquet")["ls"].dropna()


def _perf(r):
    r = r.dropna()
    nav = (1 + r).cumprod()
    ann = float(nav.iloc[-1] ** (PPY / len(r)) - 1)
    vol = float(r.std() * np.sqrt(PPY))
    return {"ann": ann, "ir": ann / vol if vol else 0.0, "win": float((r > 0).mean())}


def build():
    basic = pd.read_parquet(ROOT / "data_raw/basic/stock_basic.parquet").set_index("ts_code")["name"]
    mask = pd.read_parquet(DC / "investable_mask.parquet")

    # ---- Live(今年每日) ----
    live_nav = pd.read_parquet(BT / "live" / "live_nav.parquet")
    live_stats = json.loads((BT / "live" / "live_stats.json").read_text(encoding="utf-8"))

    # ---- 合成因子月度回测 ----
    comp_ls = _ls("composite")
    hist_nav = (1 + comp_ls).cumprod()
    yr = comp_ls.groupby([d[:4] for d in comp_ls.index]).apply(lambda s: float((1 + s).prod() - 1))

    # ---- 因子绩效表 + t统计量 ----
    ftab = []
    fmonth_names, fmonth_vals = [], []
    for name, label, direction in FACTORS:
        ls = _ls(name)
        ic = pd.read_parquet(BT / name / "ic_series.parquet")["ic"]
        p = _perf(ls)
        ftab.append({"label": label, "ic": round(float(ic.mean()), 4),
                     "icir_y": round(float(ic.mean() / ic.std() * np.sqrt(12)), 2),
                     "ir": round(p["ir"], 2), "ann": round(p["ann"], 4), "win": round(p["win"], 3),
                     "t_full": round(nw_tstat(ls), 2),
                     "t_in": round(nw_tstat(ls[ls.index <= SPLIT]), 2),
                     "t_out": round(nw_tstat(ls[ls.index > SPLIT]), 2)})
        fmonth_names.append(label)
        fmonth_vals.append(round(float(ls.iloc[-1]), 4))

    # ---- 最新持仓(合成) ----
    comp = pd.read_parquet(FAC / "composite.parquet")
    last = comp.index[-1]
    s = comp.loc[last].where(mask.reindex(comp.index).loc[last]).dropna().sort_values(ascending=False)
    def rows(sr):
        return [{"code": c, "name": str(basic.get(c, "")), "score": round(float(v), 3)} for c, v in sr.items()]

    data = {
        "meta": {"latest_date": _fmt(live_stats["end"]),
                 "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
        "live": {"stats": live_stats,
                 "nav": {"dates": [_fmt(d) for d in live_nav.index], "vals": [round(float(v), 4) for v in live_nav["nav"]]},
                 "dd": {"dates": [_fmt(d) for d in live_nav.index], "vals": [round(float(v), 4) for v in live_nav["dd"]]}},
        "backtest": {"ann": round(float(_perf(comp_ls)["ann"]), 4),
                     "nav": {"dates": [_fmt(d) for d in hist_nav.index], "vals": [round(float(v), 3) for v in hist_nav]}},
        "monthly": {"dates": [_fmt(d) for d in comp_ls.index[-24:]], "vals": [round(float(v), 4) for v in comp_ls.values[-24:]]},
        "annual": {"years": list(yr.index), "vals": [round(float(v), 4) for v in yr.values]},
        "factor_month": {"names": fmonth_names, "vals": fmonth_vals},
        "holdings": {"longs": rows(s.head(15)), "shorts": rows(s.tail(15)[::-1]),
                     "meta": f"合成因子，调仓日 {_fmt(last)}"},
        "factor_table": ftab,
    }
    DATAJS.write_text("window.DASHBOARD_DATA = " + json.dumps(data, ensure_ascii=False) + ";",
                      encoding="utf-8")
    logger.info("数据已生成: %s (Live %s~%s, 累计 %.1f%%)", DATAJS,
                live_stats["start"], live_stats["end"], live_stats["total"] * 100)


if __name__ == "__main__":
    build()
