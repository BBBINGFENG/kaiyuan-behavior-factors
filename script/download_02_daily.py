# -*- coding: utf-8 -*-
"""
下载脚本 02: 日频数据 (按交易日循环, 支持断点续传)  —— 最耗时的一步

接口 -> 因子用途:
  daily        日线行情(open/high/low/close/pre_close/vol/amount)
               -> 理想振幅因子、隔夜收益、Ret20、分组收益
  adj_factor   复权因子 -> 复权收益率
  daily_basic  每日指标(turnover_rate/total_mv/circ_mv)
               -> 市值中性化、理想换手率因子
  suspend_d    停复牌 -> 有效交易日过滤
  stk_limit    涨跌停价 -> 识别一字涨跌停(买不进/卖不出, 需剔除)
  moneyflow    资金流(特大/大/中/小单拆分)
               -> 理想反转因子的"大单切割"替代口径
                  (Tushare 无成交笔数, 无法直接算平均单笔成交金额)

输出: data_raw/<接口名>/<年份>.parquet

断点续传:
  每 FLUSH_EVERY 个交易日落盘一次, 中断后最多丢失最近一批(<50天);
  重跑时从各年份文件里已保存的最大日期之后继续, 已完整的年份直接跳过。
  落盘用"先写 .tmp 再原子替换", 避免写入过程被打断导致 parquet 损坏。

用法:
  python download_02_daily.py                  # 全部接口
  python download_02_daily.py daily adj_factor # 只跑指定接口
"""
import sys
import time

import pandas as pd

import config
from tushare_client import call, get_trade_dates, logger, PermissionError_

APIS = ["daily", "adj_factor", "daily_basic", "suspend_d", "stk_limit", "moneyflow"]

FLUSH_EVERY = 50   # 每多少个交易日落盘一次(兼作进度日志节点)

# 这些接口在正常交易日必然有数千行数据, 返回【空表】几乎必是服务端瞬时抖动。
# 若静默接受空表, max_date 推进后会留下永久空洞(需 download_05 补), 故这里对空
# 返回重试若干次; 仍空才落定并告警(交给 download_05 复核)。
# suspend_d 是事件型(很多交易日无停牌), 空表正常, 不在此列。
DENSE_APIS = {"daily", "adj_factor", "daily_basic", "stk_limit", "moneyflow"}
EMPTY_RETRY = 3
EMPTY_RETRY_WAIT = 3   # 秒


def _out_dir(api_name):
    d = config.DATA_RAW_DIR / api_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch_day(api_name, td):
    """取某接口某交易日的数据; 对 DENSE_APIS 的异常空返回做有限重试。

    返回 (df_or_None, is_anomalous_empty)。
    """
    df = call(api_name, trade_date=td)
    if df is not None and not df.empty:
        return df, False
    if api_name not in DENSE_APIS:
        return df, False        # 事件型接口, 空属正常
    for _ in range(EMPTY_RETRY):
        time.sleep(EMPTY_RETRY_WAIT)
        df = call(api_name, trade_date=td)
        if df is not None and not df.empty:
            return df, False
    return None, True           # 重试后仍空, 记为异常


def _existing_max_date(fp):
    """已保存文件中的最大 trade_date; 文件不存在或为空返回 None。"""
    if not fp.exists():
        return None
    try:
        df = pd.read_parquet(fp, columns=["trade_date"])
    except Exception as e:      # 极端情况: 文件损坏 -> 当作没下过, 重新下
        logger.warning("%s 读取失败(%s), 将重新下载该年份", fp.name, e)
        return None
    return df["trade_date"].max() if len(df) else None


def _flush(fp, frames):
    """把内存中的 frames 合并进已有文件并落盘, 返回落盘后总行数。"""
    new_df = pd.concat(frames, ignore_index=True)
    if fp.exists():
        new_df = pd.concat([pd.read_parquet(fp), new_df], ignore_index=True)
    new_df = (new_df.drop_duplicates()
              .sort_values("trade_date")
              .reset_index(drop=True))
    tmp = fp.with_suffix(".parquet.tmp")
    new_df.to_parquet(tmp, index=False)
    tmp.replace(fp)            # 原子替换
    return len(new_df)


def download_api_by_date(api_name: str, trade_dates: list):
    """按交易日循环下载某接口, 按年份落盘。"""
    out_dir = _out_dir(api_name)
    start = config.API_START_DATES.get(api_name, config.START_DATE)
    dates = [d for d in trade_dates if d >= start]
    if not dates:
        logger.warning("%s: 无可下载日期", api_name)
        return

    for year in sorted({d[:4] for d in dates}):
        fp = out_dir / f"{year}.parquet"
        year_dates = [d for d in dates if d[:4] == year]
        max_saved = _existing_max_date(fp)

        if max_saved is not None:
            todo = [d for d in year_dates if d > max_saved]
            if not todo:
                logger.info("%s %s: 已完整(至%s), 跳过", api_name, year, max_saved)
                continue
            logger.info("%s %s: 已有数据至%s, 续传剩余 %d 天",
                        api_name, year, max_saved, len(todo))
        else:
            todo = year_dates

        frames, n_saved, anomalies = [], 0, []
        for i, td in enumerate(todo, 1):
            df, empty_anom = _fetch_day(api_name, td)
            if df is not None and not df.empty:
                frames.append(df)
            elif empty_anom:
                anomalies.append(td)
                logger.warning("%s %s: %s 重试后仍为空, 稍后可用 download_05 复核",
                               api_name, year, td)
            if i % FLUSH_EVERY == 0 or i == len(todo):
                if frames:
                    n_saved = _flush(fp, frames)
                    frames = []
                logger.info("%s %s: %d/%d 天, 已落盘 %d 行",
                            api_name, year, i, len(todo), n_saved)

        if n_saved == 0:
            logger.info("%s %s: 本次无新数据", api_name, year)
        else:
            logger.info("%s %s: 完成, 累计 %d 行 -> %s",
                        api_name, year, n_saved, fp.name)


if __name__ == "__main__":
    apis = sys.argv[1:] if len(sys.argv) > 1 else APIS
    unknown = [a for a in apis if a not in APIS]
    if unknown:
        sys.exit(f"未知接口 {unknown}, 可选: {APIS}")

    trade_dates = get_trade_dates(config.START_DATE, config.END_DATE)
    logger.info("交易日: %d 天 (%s ~ %s)",
                len(trade_dates), trade_dates[0], trade_dates[-1])

    # 单个接口失败不应拖垮整个数小时的任务: 记录下来继续跑其他接口。
    # 已下载的部分都已落盘, 重跑本脚本会自动从断点续传。
    failed = {}
    for api in apis:
        logger.info("=== 开始下载 %s ===", api)
        try:
            download_api_by_date(api, trade_dates)
        except PermissionError_ as e:
            logger.error("%s 权限不足, 跳过: %s", api, e)
            failed[api] = "权限不足"
        except Exception as e:
            logger.error("%s 下载中断: %s", api, e)
            logger.error("   -> 已下载部分已落盘, 稍后重跑本脚本可从断点续传")
            failed[api] = str(e)[:80]

    if failed:
        logger.warning("=== 完成, 但以下接口未下全, 重跑可续传 ===")
        for api, why in failed.items():
            logger.warning("    %s: %s", api, why)
        sys.exit(1)      # 非零退出码, 便于外层脚本/监控察觉
    logger.info("=== 02 日频数据下载完成 ===")
