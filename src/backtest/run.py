# -*- coding: utf-8 -*-
"""
阶段C 编排: 跑单个因子的完整回测, 产出绩效表 + 净值/分组图。

用法: python src/backtest/run.py ideal_amplitude -1
      参数: <因子文件名(不含.parquet)> <方向: -1负向/+1正向>
产出: backtest/<因子>/ic_series.parquet, group_returns.parquet,
                        ls_returns.parquet, metrics.json, *.png
"""
import sys
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("run")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_CLEAN = PROJECT_ROOT / "data_clean"
FACTOR_DIR = PROJECT_ROOT / "factors"
BACKTEST_DIR = PROJECT_ROOT / "backtest"

PPY = 12   # 月频


def run(factor_name, direction):
    out_dir = BACKTEST_DIR / factor_name
    out_dir.mkdir(parents=True, exist_ok=True)

    factor = pd.read_parquet(FACTOR_DIR / f"{factor_name}.parquet")
    fwd = pd.read_parquet(DATA_CLEAN / "fwd_ret_monthly.parquet")
    mask = pd.read_parquet(DATA_CLEAN / "investable_mask.parquet").reindex(factor.index)
    fwd = fwd.reindex(index=factor.index, columns=factor.columns)
    mask = mask.reindex(columns=factor.columns).fillna(False)

    # ---- C.1 IC ----
    ic_df = engine.compute_ic(factor, fwd, mask)
    ic_sum = engine.ic_summary(ic_df)
    ic_df.to_parquet(out_dir / "ic_series.parquet")

    # ---- C.2 分组/多空 ----
    group_ret, holdings = engine.quantile_returns(factor, fwd, mask, n_groups=5)
    ls = engine.long_short(group_ret, direction)
    lo = engine.long_only(group_ret, direction)
    to = engine.turnover(holdings, direction, n_groups=5)
    ls_net = engine.net_of_cost(ls, to)
    group_ret.to_parquet(out_dir / "group_returns.parquet")
    ls.rename("ls").to_frame().assign(ls_net=ls_net, long_only=lo).to_parquet(
        out_dir / "ls_returns.parquet")

    # ---- C.3 绩效 ----
    m_gross = engine.perf_metrics(ls)
    m_net = engine.perf_metrics(ls_net)
    m_lo = engine.perf_metrics(lo)
    metrics = {
        "因子": factor_name, "方向": direction,
        "IC": ic_sum,
        "多空对冲_扣费前": m_gross,
        "多空对冲_扣费后": m_net,
        "long_only": m_lo,
        "多头单边换手_均值": float(to.mean()),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=float))

    _print_report(factor_name, metrics)
    _plot(factor_name, out_dir, group_ret, ls, ls_net, direction)
    return metrics


def _print_report(name, m):
    print(f"\n{'='*60}\n  {name}  绩效报告\n{'='*60}")
    print("[IC]")
    for k, v in m["IC"].items():
        print(f"    {k:12s} {v:+.4f}" if isinstance(v, float) else f"    {k:12s} {v}")
    for blk in ["多空对冲_扣费前", "多空对冲_扣费后", "long_only"]:
        print(f"[{blk}]")
        for k, v in m[blk].items():
            print(f"    {k:10s} {v:+.4f}")
    print(f"[换手] 多头单边换手均值 {m['多头单边换手_均值']:.1%}")


def _plot(name, out_dir, group_ret, ls, ls_net, direction):
    # 图1: 多空对冲净值
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
    engine_navs = {"long-short (gross)": engine.nav_curve(ls),
                   "long-short (net of cost)": engine.nav_curve(ls_net)}
    for lbl, nav in engine_navs.items():
        ax[0].plot(pd.to_datetime(nav.index), nav.values, label=lbl, lw=1.5)
    ax[0].set_title(f"{name}: long-short hedged net value"); ax[0].legend(); ax[0].grid(alpha=.3)
    ax[0].tick_params(axis="x", rotation=30)

    # 图2: 5分组年化收益
    ann = (1 + group_ret).prod() ** (PPY / len(group_ret)) - 1
    colors = ["#d62728" if (direction < 0 and q == "Q1") or (direction > 0 and q == "Q5")
              else "#1f77b4" for q in ann.index]
    ax[1].bar(ann.index, ann.values * 100, color=colors)
    ax[1].set_title(f"{name}: 5-group annualized return (%)  [red=long group]")
    ax[1].axhline(0, color="k", lw=.6); ax[1].grid(alpha=.3, axis="y")
    plt.tight_layout()
    fp = out_dir / f"{name}_backtest.png"
    plt.savefig(fp, dpi=130, bbox_inches="tight")
    logger.info("图已保存: %s", fp)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("用法: python src/backtest/run.py <因子名> <方向-1或+1>")
    run(sys.argv[1], int(sys.argv[2]))
