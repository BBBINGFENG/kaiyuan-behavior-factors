# -*- coding: utf-8 -*-
"""
阶段A：数据清洗层 (data_clean)

把 data_raw/ 的原始逐行数据，整理成回测直接能用的"日期×股票"宽表(panel)。
所有因子共用这一层。对应《回测执行方案_逐步详解.md》阶段A。

产出(全部落到 data_clean/):
  A.1 复权价格   px_{open,high,low,close}_hfq.parquet  (后复权)
                 px_{high,low,close}_raw.parquet        (原始, 供涨跌停判断)
                 pct_chg.parquet / amount.parquet
  A.2 可投资掩码 investable_mask.parquet                (日期×股票 布尔)
  A.3 月度面板   rebalance_dates.parquet / fwd_ret_monthly.parquet
  A.4 行业/成分  industry_panel.parquet / index_members/<code>.parquet

用法: python src/data_clean.py            # 全跑
      python src/data_clean.py A1         # 只跑某一步 (A1/A2/A3/A4)
"""
import sys
import glob
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("data_clean")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data_raw"
DATA_CLEAN = PROJECT_ROOT / "data_clean"
DATA_CLEAN.mkdir(parents=True, exist_ok=True)

BACKTEST_START = "20100101"   # 回测起点; 更早的数据只用于回看窗口的缓冲


# ------------------------------------------------------------------ 工具函数
def _load_daily_api(api_name, cols=None):
    """把某个按年份分片的日频接口全部读进来, 合并成一张长表。"""
    files = sorted((DATA_RAW / api_name).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{api_name} 无数据, 请先运行下载脚本")
    return pd.concat([pd.read_parquet(f, columns=cols) for f in files],
                     ignore_index=True)


def _load_basic(name):
    return pd.read_parquet(DATA_RAW / "basic" / f"{name}.parquet")


def _to_panel(df, value, dtype="float32"):
    """长表 → 宽表(日期×股票)。这是全流程最核心的一次形变。"""
    p = df.pivot(index="trade_date", columns="ts_code", values=value)
    p = p.sort_index()
    return p.astype(dtype)


def _save(panel, name):
    fp = DATA_CLEAN / name
    panel.to_parquet(fp)
    logger.info("  saved %-26s shape=%s", name, panel.shape)


# ================================================================== A.1
def build_adjusted_panels():
    """A.1 复权价格面板。

    为什么: 20日窗口内若除权除息, 原始价会假跳空, 算错收益/排序; 后复权抹平它。
    做什么: 后复权价 = 原始价 × adj_factor; 转成 日期×股票 宽表。
    """
    logger.info("[A.1] 构建复权价格面板 ...")
    daily = _load_daily_api("daily", ["ts_code", "trade_date", "open", "high",
                                      "low", "close", "pct_chg", "amount"])
    adj = _load_daily_api("adj_factor", ["ts_code", "trade_date", "adj_factor"])
    df = daily.merge(adj, on=["ts_code", "trade_date"], how="left")
    df["adj_factor"] = df["adj_factor"].fillna(1.0)   # 个别缺失 → 视作无复权

    # 原始价(涨跌停判断要用真实价, 不能用复权价)
    for col in ["high", "low", "close"]:
        _save(_to_panel(df, col), f"px_{col}_raw.parquet")

    # 后复权价(收益/排序用)
    for col in ["open", "high", "low", "close"]:
        df[col + "_hfq"] = df[col] * df["adj_factor"]
        _save(_to_panel(df, col + "_hfq"), f"px_{col}_hfq.parquet")

    # 其他常用面板
    _save(_to_panel(df, "pct_chg"), "pct_chg.parquet")
    _save(_to_panel(df, "amount"), "amount.parquet")
    logger.info("[A.1] 完成")


# ================================================================== A.2
def _mask_st(dates, stocks):
    """名称含 ST 的区间标 False。基于 namechange 的 [start_date, end_date]。"""
    nc = _load_basic("namechange")
    st = nc[nc["name"].str.contains("ST", na=False)].copy()
    st["end_date"] = st["end_date"].fillna("20991231")   # 空=至今仍是该名
    di = pd.Index(dates)
    col_pos = {c: i for i, c in enumerate(stocks)}
    flag = np.zeros((len(dates), len(stocks)), dtype=bool)   # True=当日是ST
    for tc, sd, ed in zip(st["ts_code"], st["start_date"], st["end_date"]):
        j = col_pos.get(tc)
        if j is None:
            continue
        lo = di.searchsorted(sd, "left")
        hi = di.searchsorted(ed, "right")
        if hi > lo:
            flag[lo:hi, j] = True
    return pd.DataFrame(~flag, index=dates, columns=stocks)


def _mask_new(dates, stocks, n=60):
    """上市不满 n 个交易日标 False。"""
    sb = _load_basic("stock_basic")[["ts_code", "list_date"]].dropna()
    list_date = dict(zip(sb["ts_code"], sb["list_date"]))
    di = pd.Index(dates)
    thr = np.full(len(stocks), "99999999")   # 默认永不可投(无上市日的怪票)
    for j, c in enumerate(stocks):
        ld = list_date.get(c)
        if ld is None:
            continue
        pos = di.searchsorted(ld, "left") + n     # 上市后第 n 个交易日
        thr[j] = di[pos] if pos < len(di) else "99999999"
    # 日期(yyyymmdd 字符串)可直接按字典序比较
    ok = dates.values[:, None] >= thr[None, :]
    return pd.DataFrame(ok, index=dates, columns=stocks)


def _mask_suspend(dates, stocks):
    """停牌日标 False(基于 suspend_d 的 S 记录; 与'价格NaN'部分冗余, 但更显式)。"""
    s = _load_daily_api("suspend_d", ["ts_code", "trade_date", "suspend_type"])
    s = s[s["suspend_type"] == "S"]
    susp = (s.assign(v=True)
            .pivot_table(index="trade_date", columns="ts_code", values="v", aggfunc="any")
            .reindex(index=dates, columns=stocks)
            .fillna(False))
    return ~susp.astype(bool)


def _mask_limit(close_raw, high_raw, low_raw):
    """一字涨跌停(买不进/卖不出)标 False。signature: high==low 且 close 贴着涨/跌停价。"""
    lim = _load_daily_api("stk_limit", ["ts_code", "trade_date", "up_limit", "down_limit"])
    up = _to_panel(lim, "up_limit").reindex(index=close_raw.index, columns=close_raw.columns)
    dn = _to_panel(lim, "down_limit").reindex(index=close_raw.index, columns=close_raw.columns)
    one_word = (high_raw - low_raw).abs() < 1e-3            # 全天一个价
    at_up = (close_raw - up).abs() < 5e-3                   # 收在涨停价 → 一字涨停
    at_dn = (close_raw - dn).abs() < 5e-3                   # 收在跌停价 → 一字跌停
    locked = one_word & (at_up | at_dn)
    return ~locked.fillna(False)


def build_investable_mask():
    """A.2 可投资域掩码。True=当日该股可选入组合。

    base(有K线) & 非ST & 上市满60日 & 未停牌 & 非一字板。
    """
    logger.info("[A.2] 构建可投资掩码 ...")
    close_raw = pd.read_parquet(DATA_CLEAN / "px_close_raw.parquet")
    high_raw = pd.read_parquet(DATA_CLEAN / "px_high_raw.parquet")
    low_raw = pd.read_parquet(DATA_CLEAN / "px_low_raw.parquet")
    dates, stocks = close_raw.index, close_raw.columns

    mask = close_raw.notna()                       # base: 有价格(已排除未上市/全天停牌)
    logger.info("  base(有K线): 平均每日 %.0f 只", mask.sum(axis=1).mean())
    # 只保留沪深A股: 研报口径是全A(沪深), 北交所/新三板(.BJ)不在其中,
    # 且 .BJ 早期数据有异常低价(振幅可达数十倍), 必须剔除。
    is_hs = pd.Series([c.endswith((".SH", ".SZ")) for c in stocks], index=stocks)
    mask &= is_hs
    logger.info("  限沪深A股(剔.BJ)后: 平均每日 %.0f 只", mask.sum(axis=1).mean())
    for name, m in [("非ST", _mask_st(dates, stocks)),
                    ("满60日", _mask_new(dates, stocks)),
                    ("未停牌", _mask_suspend(dates, stocks)),
                    ("非一字板", _mask_limit(close_raw, high_raw, low_raw))]:
        mask &= m
        logger.info("  叠加 %-8s 后: 平均每日 %.0f 只", name, mask.sum(axis=1).mean())

    _save(mask, "investable_mask.parquet")
    logger.info("[A.2] 完成")


# ================================================================== A.3
def get_rebalance_dates(save=True):
    """A.3-1 调仓日 = 每月最后一个交易日(>= 回测起点)。"""
    cal = _load_basic("trade_cal")
    open_d = pd.Series(sorted(cal.loc[cal["is_open"] == 1, "cal_date"]))
    last_of_month = open_d.groupby(open_d.str[:6]).last()
    rebal = last_of_month[last_of_month >= BACKTEST_START].tolist()
    if save:
        pd.Series(rebal, name="rebalance_date").to_frame().to_parquet(
            DATA_CLEAN / "rebalance_dates.parquet")
        logger.info("  调仓日 %d 个 (%s ~ %s)", len(rebal), rebal[0], rebal[-1])
    return rebal


def build_forward_returns():
    """A.3-2 未来一期(下个调仓日)收益, 作为检验因子的标签。★防前视关键★"""
    logger.info("[A.3] 构建调仓日与未来收益 ...")
    rebal = get_rebalance_dates()
    close_hfq = pd.read_parquet(DATA_CLEAN / "px_close_hfq.parquet")
    px_reb = close_hfq.reindex(rebal)                 # 只取调仓日的复权收盘
    fwd = px_reb.shift(-1) / px_reb - 1.0             # 下一调仓日/本调仓日 − 1
    _save(fwd.astype("float32"), "fwd_ret_monthly.parquet")
    logger.info("[A.3] 完成 (最后一期无未来收益, 为 NaN, 正常)")


# ================================================================== A.4
def build_industry_panel():
    """A.4-1 行业时点表: 调仓日×股票 → 申万一级行业代码(按 in/out_date 还原)。"""
    logger.info("[A.4] 构建行业时点表 ...")
    mem = pd.read_parquet(DATA_RAW / "industry" / "sw_l1_members.parquet").copy()
    mem["out_date"] = mem["out_date"].fillna("20991231")
    rebal = get_rebalance_dates(save=False)
    stocks = pd.read_parquet(DATA_CLEAN / "px_close_raw.parquet").columns
    ri = pd.Index(rebal)
    col_pos = {c: i for i, c in enumerate(stocks)}
    panel = np.full((len(rebal), len(stocks)), None, dtype=object)
    for tc, l1, ind, outd in zip(mem["ts_code"], mem["l1_code"],
                                 mem["in_date"], mem["out_date"]):
        j = col_pos.get(tc)
        if j is None:
            continue
        lo = ri.searchsorted(ind, "left")
        hi = ri.searchsorted(outd, "left")            # [in_date, out_date)
        if hi > lo:
            panel[lo:hi, j] = l1
    df = pd.DataFrame(panel, index=rebal, columns=stocks)
    df.to_parquet(DATA_CLEAN / "industry_panel.parquet")
    cov = df.notna().sum(axis=1).mean()
    logger.info("  行业覆盖: 平均每个调仓日 %.0f 只有行业归属", cov)


def build_index_members():
    """A.4-2 指数成分: 每个指数一张 调仓日×股票 布尔面板(用当期最近快照)。"""
    logger.info("[A.4] 构建指数成分面板 ...")
    out_dir = DATA_CLEAN / "index_members"
    out_dir.mkdir(exist_ok=True)
    rebal = get_rebalance_dates(save=False)
    stocks = pd.read_parquet(DATA_CLEAN / "px_close_raw.parquet").columns
    files = sorted((DATA_RAW / "index_weight").glob("*.parquet"))
    for fp in files:
        w = pd.read_parquet(fp, columns=["con_code", "trade_date"])
        snaps = sorted(w["trade_date"].unique())
        members = {d: set(w.loc[w["trade_date"] == d, "con_code"]) for d in snaps}
        snap_idx = pd.Index(snaps)
        rows = []
        for d in rebal:
            pos = snap_idx.searchsorted(d, "right") - 1   # 最近的 <= d 的快照
            cons = members[snaps[pos]] if pos >= 0 else set()
            rows.append(pd.Series(stocks.isin(cons), index=stocks))
        panel = pd.DataFrame(rows, index=rebal)
        code = fp.stem
        panel.to_parquet(out_dir / f"{code}.parquet")
        logger.info("  %s: 平均每期 %.0f 只成分", code, panel.sum(axis=1).mean())
    logger.info("[A.4] 完成")


# ================================================================== main
STEPS = {
    "A1": build_adjusted_panels,
    "A2": build_investable_mask,
    "A3": build_forward_returns,
    "A4": lambda: (build_industry_panel(), build_index_members()),
}

if __name__ == "__main__":
    todo = sys.argv[1:] or ["A1", "A2", "A3", "A4"]
    for step in todo:
        if step not in STEPS:
            sys.exit(f"未知步骤 {step}, 可选 {list(STEPS)}")
        STEPS[step]()
    logger.info("=== 阶段A 数据清洗完成 ===")
