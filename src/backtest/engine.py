# -*- coding: utf-8 -*-
"""
阶段C：单因子回测引擎 (可复用, 四个因子共用)

对应《回测执行方案_逐步详解.md》阶段C:
  C.1 compute_ic / ic_summary        — IC / rankIC / ICIR
  C.2 quantile_returns / long_short  — 5分组 / 多空对冲 / long-only
  C.3 perf_metrics / turnover        — 净值 / 绩效指标 / 换手 / 扣费

设计原则:
  - 所有函数输入统一为"调仓日×股票"宽表(因子/未来收益/掩码), 形状一致。
  - 方向(direction)显式传入: 负向因子(振幅/反转/聪明钱)=-1, 正向(APM)=+1。
    ★永远不写死买哪组★, 跟因子符号走, 四因子共用不翻车。
"""
import numpy as np
import pandas as pd

MIN_STOCKS = 100          # 某调仓日可投资票太少则跳过该期
FEE_RATE = 0.003          # 双边千三(见 net_of_cost 的口径说明)


# ============================================================ C.1 IC
def compute_ic(factor, fwd_ret, mask):
    """逐调仓日算横截面 IC(Pearson) 与 rankIC(Spearman)。

    ★节奏: 每期先横截面算, 再沿时间成序列★ (不是把所有数据倒一起算一个)
    返回: DataFrame(index=调仓日, columns=['ic','rankic'])
    """
    rows, idx = [], []
    for d in factor.index:
        fac = factor.loc[d].where(mask.loc[d])          # 只留当期可投资的票
        df = pd.concat([fac, fwd_ret.loc[d]], axis=1).dropna()
        if len(df) < MIN_STOCKS:
            continue
        a, b = df.iloc[:, 0], df.iloc[:, 1]
        rows.append((a.corr(b), a.corr(b, method="spearman")))
        idx.append(d)
    return pd.DataFrame(rows, columns=["ic", "rankic"], index=idx)


def ic_summary(ic_df):
    ic, ric = ic_df["ic"], ic_df["rankic"]
    sign = np.sign(ic.mean())
    return {
        "IC均值": ic.mean(),
        "rankIC均值": ric.mean(),
        "ICIR_月度": ic.mean() / ic.std(),
        "ICIR_年化": ic.mean() / ic.std() * np.sqrt(12),   # ★研报口径是年化(×√12)★
        "IC_t值": ic.mean() / ic.std() * np.sqrt(len(ic)),
        "IC同向占比": (np.sign(ic) == sign).mean(),
        "月份数": len(ic),
    }


# ============================================================ C.2 分组/多空
def quantile_returns(factor, fwd_ret, mask, n_groups=5):
    """按因子值把可投资票分 n 组(Q1=因子最小), 算各组等权未来收益。

    返回:
      group_ret : DataFrame(index=调仓日, columns=[Q1..Qn]) 各组收益
      holdings  : dict{调仓日: {Qi: set(股票)}}  供算换手
    """
    labels = [f"Q{i+1}" for i in range(n_groups)]
    group_ret, holdings = {}, {}
    for d in factor.index:
        fac = factor.loc[d].where(mask.loc[d])
        df = pd.concat([fac, fwd_ret.loc[d]], axis=1, keys=["f", "r"]).dropna()
        if len(df) < MIN_STOCKS:
            continue
        # 按因子值升序切 n 组; 先 rank 再 qcut 避免重复值切割报错
        df["g"] = pd.qcut(df["f"].rank(method="first"), n_groups, labels=labels)
        gr = df.groupby("g", observed=True)["r"].mean()
        gr.index = gr.index.astype(str)          # 去掉 categorical, 便于落盘
        group_ret[d] = gr
        holdings[d] = {q: set(df.index[df["g"] == q]) for q in labels}
    res = pd.DataFrame(group_ret).T
    res.columns = [str(c) for c in res.columns]
    return res[labels], holdings


def long_short(group_ret, direction):
    """多空对冲收益。负向因子(direction<0): 多Q1空Qn; 正向: 多Qn空Q1。"""
    lo, hi = group_ret.columns[0], group_ret.columns[-1]
    return group_ret[lo] - group_ret[hi] if direction < 0 else group_ret[hi] - group_ret[lo]


def long_only(group_ret, direction):
    """long-only 视角: 多头组 − 各组均值 (研报的'多头-各组均值'口径)。"""
    long_col = group_ret.columns[0] if direction < 0 else group_ret.columns[-1]
    return group_ret[long_col] - group_ret.mean(axis=1)


def turnover(holdings, direction, n_groups):
    """多头组的单边换手率序列 = 每期新买入占比。"""
    long_q = "Q1" if direction < 0 else f"Q{n_groups}"
    dates = sorted(holdings)
    out = {}
    for i in range(1, len(dates)):
        prev, cur = holdings[dates[i-1]][long_q], holdings[dates[i]][long_q]
        if cur:
            out[dates[i]] = len(cur - prev) / len(cur)     # 新进入占比(单边)
    return pd.Series(out)


# ============================================================ C.3 绩效
def net_of_cost(ls_ret, turnover_ser, fee=FEE_RATE):
    """扣交易成本。口径: 多空双腿、单边换手×费率, 近似双边千三。

    成本_t = 2(多+空两腿) × 单边换手_t × (fee/2 每边) = 单边换手_t × fee
    """
    cost = turnover_ser.reindex(ls_ret.index).fillna(0) * fee
    return ls_ret - cost


def perf_metrics(ret, ppy=12):
    """从月度收益序列算绩效。ppy=每年期数(月频=12)。"""
    ret = ret.dropna()
    nav = (1 + ret).cumprod()
    ann_ret = nav.iloc[-1] ** (ppy / len(ret)) - 1        # 几何年化
    ann_vol = ret.std() * np.sqrt(ppy)
    max_dd = (nav / nav.cummax() - 1).min()
    return {
        "年化收益": ann_ret,
        "年化波动": ann_vol,
        "信息比率IR": ann_ret / ann_vol,
        "最大回撤": max_dd,
        "月度胜率": (ret > 0).mean(),
    }


def nav_curve(ret):
    """净值曲线(从1开始)。"""
    return (1 + ret.dropna()).cumprod()
