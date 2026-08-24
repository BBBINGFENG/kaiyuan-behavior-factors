# -*- coding: utf-8 -*-
"""
下载脚本 01: 基础信息 (体量小, 每次全量覆盖)

  1. trade_cal     交易日历      -> 回看窗口、调仓日
  2. stock_basic   股票列表      -> 样本池; 含退市(D)/暂停(P), 避免幸存者偏差
  3. namechange    历史更名      -> 识别历史 ST 区间(剔除 ST 股)

输出:
  data_raw/basic/trade_cal.parquet
  data_raw/basic/stock_basic.parquet
  data_raw/basic/namechange.parquet

用法: python download_01_basic.py
"""
import pandas as pd

import config
from tushare_client import call, call_paged, logger

OUT_DIR = config.DATA_RAW_DIR / "basic"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def download_trade_cal():
    df = call("trade_cal", exchange="SSE",
              start_date=config.START_DATE, end_date=config.END_DATE)
    df = df.sort_values("cal_date").reset_index(drop=True)
    df.to_parquet(OUT_DIR / "trade_cal.parquet", index=False)
    n_open = int((df["is_open"] == 1).sum())
    logger.info("trade_cal: %d 行 (其中交易日 %d 天) 已保存", len(df), n_open)


def download_stock_basic():
    """分别拉 上市L / 退市D / 暂停上市P 并合并。

    退市股必须保留: 回测时若只用当前still-listed的股票会产生幸存者偏差
    (survivorship bias), 高估策略收益。
    """
    frames = []
    for status in ["L", "D", "P"]:
        df = call("stock_basic", exchange="", list_status=status,
                  fields="ts_code,symbol,name,area,industry,market,exchange,"
                         "list_status,list_date,delist_date")
        frames.append(df)
        logger.info("stock_basic status=%s: %d 只", status, len(df))
    all_df = (pd.concat(frames, ignore_index=True)
              .drop_duplicates("ts_code")
              .sort_values("ts_code")
              .reset_index(drop=True))
    all_df.to_parquet(OUT_DIR / "stock_basic.parquet", index=False)
    logger.info("stock_basic 合计: %d 只 已保存", len(all_df))


def download_namechange():
    df = call_paged("namechange", page_size=5000,
                    fields="ts_code,name,start_date,end_date,ann_date,change_reason")
    if df.empty:
        logger.warning("namechange: 无数据")
        return
    df = df.drop_duplicates().sort_values(["ts_code", "start_date"]).reset_index(drop=True)
    df.to_parquet(OUT_DIR / "namechange.parquet", index=False)
    n_st = int(df["name"].str.contains("ST", na=False).sum())
    logger.info("namechange: %d 行 (含 ST 相关 %d 行) 已保存", len(df), n_st)


if __name__ == "__main__":
    download_trade_cal()
    download_stock_basic()
    download_namechange()
    logger.info("=== 01 基础信息下载完成 ===")
