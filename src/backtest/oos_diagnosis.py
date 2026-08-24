# -*- coding: utf-8 -*-
"""
阶段E：样本外诊断

1. 分样本内(≤2020)/样本外(>2020)统计绩效, 看衰减。
2. 定位 2020 后疲软期(近12月)。
3. 市值风格归因: 合成因子多空收益 vs 市值溢价(大盘−小盘)的相关性,
   检验"大盘风格占优时, 中小盘属性的行为因子失效"。

产出: backtest/oos_diagnosis/  (report.txt, *.png)
用法: python src/backtest/oos_diagnosis.py
"""
import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import engine                      # noqa: E402
from factors.neutralize import build_mv_at_rebal  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("oos")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FACTOR_DIR = ROOT / "factors"
OUT = ROOT / "backtest" / "oos_diagnosis"
OUT.mkdir(parents=True, exist_ok=True)

SPLIT = "20201231"          # 样本内/外分界
RECENT = 12                 # 疲软期 = 最近12个月


def _ls_and_ic(name, direction, fwd, mask):
    fac = pd.read_parquet(FACTOR_DIR / f"{name}.parquet")
    m = mask.reindex(fac.index)
    fwd2 = fwd.reindex(index=fac.index, columns=fac.columns)
    ic = engine.compute_ic(fac, fwd2, m)["ic"]
    gr, _ = engine.quantile_returns(fac, fwd2, m, n_groups=5)
    ls = engine.long_short(gr, direction)
    return ic, ls


def _period_stats(ic, ls, lo=None, hi=None):
    if lo:
        ic, ls = ic[(ic.index >= lo)], ls[(ls.index >= lo)]
    if hi:
        ic, ls = ic[(ic.index <= hi)], ls[(ls.index <= hi)]
    icir_y = ic.mean() / ic.std() * np.sqrt(12)
    m = engine.perf_metrics(ls)
    return (f"IC={float(ic.mean()):+.4f}  ICIR年化={float(icir_y):+.2f}  "
            f"多空IR={float(m['信息比率IR']):.2f}  多空年化={float(m['年化收益']):.1%}  "
            f"胜率={float(m['月度胜率']):.1%}  月数={len(ic)}")


def size_premium(fwd, mask):
    """市值溢价: 大市值组 − 小市值组 的未来收益。正=大盘占优。"""
    rebal = pd.read_parquet(DC / "rebalance_dates.parquet")["rebalance_date"].tolist()
    mv = build_mv_at_rebal(rebal)
    out = {}
    for d in mv.index:
        if d not in fwd.index:
            continue
        m = mask.loc[d] if d in mask.index else None
        s = mv.loc[d].where(m) if m is not None else mv.loc[d]
        df = pd.concat([s.rename("mv"), fwd.loc[d].rename("r")], axis=1).dropna()
        if len(df) < 100:
            continue
        df["g"] = pd.qcut(df["mv"].rank(method="first"), 5, labels=False)
        out[d] = df.loc[df.g == 4, "r"].mean() - df.loc[df.g == 0, "r"].mean()
    return pd.Series(out)


def main():
    fwd = pd.read_parquet(DC / "fwd_ret_monthly.parquet")
    mask = pd.read_parquet(DC / "investable_mask.parquet")

    factors = {"composite": 1, "ideal_amplitude_neutral": -1, "ideal_reversal_neutral": -1}
    lines = ["阶段E 样本外诊断", "=" * 64, ""]

    # 1) 分样本内外
    comp_ic = comp_ls = None
    lines.append("[1] 分样本内外绩效 (合成 + 两单因子中性化版)")
    for name, direction in factors.items():
        ic, ls = _ls_and_ic(name, direction, fwd, mask)
        if name == "composite":
            comp_ic, comp_ls = ic, ls
        full = _period_stats(ic, ls)
        ins = _period_stats(ic, ls, hi=SPLIT)
        oos = _period_stats(ic, ls, lo=SPLIT)
        lines.append(f"  {name}")
        lines.append(f"    全历史  {full}")
        lines.append(f"    样本内  {ins}")
        lines.append(f"    样本外  {oos}")

    # 2) 疲软期(近12月)
    recent = comp_ls.iloc[-RECENT:]
    lines += ["", f"[2] 疲软期(最近{RECENT}月 {recent.index[0]}~{recent.index[-1]})",
              f"    合成多空月度胜率 = {(recent > 0).mean():.1%}  (对比全历史 {(comp_ls>0).mean():.1%})",
              f"    合成多空累计收益 = {((1+recent).prod()-1):.1%}"]

    # 3) 市值风格归因
    sp = size_premium(fwd, mask)
    j = pd.concat([comp_ls.rename("comp"), sp.rename("size")], axis=1).dropna()
    corr_full = j["comp"].corr(j["size"])
    corr_oos = j[j.index > SPLIT]["comp"].corr(j[j.index > SPLIT]["size"])
    lines += ["", "[3] 市值风格归因: 合成多空收益 vs 市值溢价(大−小)",
              f"    全历史相关 = {corr_full:+.2f}   样本外相关 = {corr_oos:+.2f}",
              "    (负相关 → 大盘占优时行为因子失效, 印证'中小盘属性')"]

    report = "\n".join(lines)
    (OUT / "report.txt").write_text(report, encoding="utf-8")
    print(report)
    _plot(comp_ls, comp_ic, sp)


def _plot(comp_ls, comp_ic, sp):
    fig, ax = plt.subplots(1, 3, figsize=(18, 4.5))
    nav = engine.nav_curve(comp_ls)
    x = pd.to_datetime(nav.index)
    ax[0].plot(x, nav.values, color="#1f77b4", lw=1.5)
    ax[0].axvline(pd.Timestamp(SPLIT), ls="--", color="red")
    ax[0].text(pd.Timestamp(SPLIT), nav.max()*0.6, " 样本外→", color="red")
    ax[0].set_title("Composite long-short net value (split at 2020)"); ax[0].grid(alpha=.3)

    roll = comp_ic.rolling(12).mean()
    ax[1].plot(pd.to_datetime(roll.index), roll.values, color="#2ca02c", lw=1.3)
    ax[1].axhline(0, color="k", lw=.6); ax[1].axvline(pd.Timestamp(SPLIT), ls="--", color="red")
    ax[1].set_title("Rolling 12M IC (weakening after 2020)"); ax[1].grid(alpha=.3)

    j = pd.concat([comp_ls.rename("c"), sp.rename("s")], axis=1).dropna()
    ax[2].scatter(j["s"]*100, j["c"]*100, s=14, alpha=.5)
    ax[2].set_xlabel("size premium big-small (%)"); ax[2].set_ylabel("composite LS return (%)")
    ax[2].set_title(f"Style attribution (corr={j['c'].corr(j['s']):+.2f})")
    ax[2].grid(alpha=.3); ax[2].axhline(0, color="k", lw=.5); ax[2].axvline(0, color="k", lw=.5)
    plt.tight_layout(); plt.savefig(OUT / "oos_diagnosis.png", dpi=130, bbox_inches="tight")
    logger.info("图已保存: %s", OUT / "oos_diagnosis.png")


if __name__ == "__main__":
    main()
