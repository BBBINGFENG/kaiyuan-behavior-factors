# -*- coding: utf-8 -*-
"""
下载脚本 03: 指数与行业

  1. index_daily       指数日线     -> 回测基准净值、APM 回归中的指数同期收益
  2. index_weight      指数成分权重 -> 沪深300/中证500/中证800/中证1000 分池回测
                                       (月度快照, 用当期成分避免幸存者偏差)
  3. index_classify +  申万一级行业分类与成分(含 in_date/out_date 历史进出)
     index_member_all  -> 行业中性化、合成因子的行业内标准化
                          (研报用中信一级, Tushare 无中信, 以申万一级替代)

输出:
  data_raw/index_daily/<code>.parquet
  data_raw/index_weight/<code>.parquet
  data_raw/industry/sw_l1_classify.parquet
  data_raw/industry/sw_l1_members.parquet

用法: python download_03_index_industry.py
"""
import pandas as pd

import config
from tushare_client import call, logger, PermissionError_


def download_index_daily():
    out_dir = config.DATA_RAW_DIR / "index_daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, code in {**config.INDEX_CODES, **config.INDEX_DAILY_ONLY}.items():
        try:
            df = call("index_daily", ts_code=code,
                      start_date=config.START_DATE, end_date=config.END_DATE)
        except PermissionError_ as e:
            logger.warning("index_daily %s(%s): 无权限, 跳过", name, code)
            continue
        if df is None or df.empty:
            logger.warning("index_daily %s(%s): 无数据", name, code)
            continue
        df = df.sort_values("trade_date").reset_index(drop=True)
        df.to_parquet(out_dir / f"{code.replace('.', '_')}.parquet", index=False)
        logger.info("index_daily %s(%s): %d 行 (%s~%s)",
                    name, code, len(df), df["trade_date"].min(), df["trade_date"].max())


def download_index_weight():
    """index_weight 单次返回上限 5000 行, 按年循环拉取。

    指数成分权重是月度快照。注意各指数发布时间不同(如中证1000 发布于2014年),
    发布前的年份返回空属正常。
    """
    out_dir = config.DATA_RAW_DIR / "index_weight"
    out_dir.mkdir(parents=True, exist_ok=True)
    years = range(int(config.START_DATE[:4]), int(config.END_DATE[:4]) + 1)

    for name, code in config.INDEX_CODES.items():
        frames = []
        for y in years:
            try:
                df = call("index_weight", index_code=code,
                          start_date=f"{y}0101", end_date=f"{y}1231")
            except PermissionError_:
                logger.warning("index_weight %s(%s): 无权限, 跳过", name, code)
                frames = []
                break
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            logger.warning("index_weight %s(%s): 无数据", name, code)
            continue
        all_df = (pd.concat(frames, ignore_index=True)
                  .drop_duplicates()
                  .sort_values(["trade_date", "con_code"])
                  .reset_index(drop=True))
        all_df.to_parquet(out_dir / f"{code.replace('.', '_')}.parquet", index=False)
        logger.info("index_weight %s(%s): %d 行, %d 个月度快照 (%s~%s)",
                    name, code, len(all_df), all_df["trade_date"].nunique(),
                    all_df["trade_date"].min(), all_df["trade_date"].max())


def download_sw_industry():
    out_dir = config.DATA_RAW_DIR / "industry"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 申万一级行业列表
    l1 = call("index_classify", level="L1", src=config.SW_SRC)
    l1 = l1.sort_values("index_code").reset_index(drop=True)
    l1.to_parquet(out_dir / "sw_l1_classify.parquet", index=False)
    logger.info("申万一级行业: %d 个", len(l1))

    # 2) 各行业成分(含历史进出 in_date/out_date, 供按时点还原行业归属)
    #
    # 注意: index_member_all 的 is_new 参数默认为 'Y', 只返回【当前】成分,
    # 已调出该行业的历史记录必须用 is_new='N' 才能拿到(传 '' 无效)。
    # 只用当前归属做历史回测会引入前视偏差 —— 某股票若从 A 行业调整到 B,
    # 整段历史都会被误标成 B。故这里两者都拉再合并。
    frames = []
    for _, row in l1.iterrows():
        code, name = row["index_code"], row.get("industry_name", "")
        n_cur = n_hist = 0
        for is_new in ["Y", "N"]:
            df = call("index_member_all", l1_code=code, is_new=is_new)
            if df is not None and not df.empty:
                frames.append(df)
                if is_new == "Y":
                    n_cur = len(df)
                else:
                    n_hist = len(df)
        logger.info("  %s %s: 当前 %d + 历史 %d 条", code, name, n_cur, n_hist)

    if not frames:
        logger.warning("申万行业成分: 无数据")
        return
    all_df = (pd.concat(frames, ignore_index=True)
              .drop_duplicates()
              .sort_values(["ts_code", "in_date"])
              .reset_index(drop=True))
    all_df.to_parquet(out_dir / "sw_l1_members.parquet", index=False)
    n_hist_total = int(all_df["out_date"].notna().sum())
    logger.info("申万行业成分合计: %d 行, 覆盖 %d 只股票 (其中历史调出记录 %d 条)",
                len(all_df), all_df["ts_code"].nunique(), n_hist_total)


if __name__ == "__main__":
    download_index_daily()
    download_index_weight()
    download_sw_industry()
    logger.info("=== 03 指数与行业下载完成 ===")
