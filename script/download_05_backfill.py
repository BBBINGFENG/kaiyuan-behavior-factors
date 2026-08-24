# -*- coding: utf-8 -*-
"""
下载脚本 05: 补洞 (backfill missing trading days)

为什么需要它:
  download_02 按交易日循环时, 若某天 Tushare 偶发返回【空表】(服务端瞬时抖动,
  返回空而非报错), 代码会把它当"无数据"跳过; 而断点续传只从 max_date 往后续,
  于是这些位于中间的空洞会永久残留。对 daily/adj_factor/daily_basic/stk_limit
  这类接口, 一个正常交易日返回空几乎必然是瞬时故障 —— 当天有数千只股票在交易。

本脚本做什么:
  1. 对每个日频接口, 算出"交易日历中应有、但本地文件里没有"的交易日;
  2. 逐个重新请求这些日期, 若这次拿到了数据就合并回对应年份文件;
  3. 报告重试后仍为空的日期(极少数可能是官方确实无数据, 需人工确认)。

用法:
  python download_05_backfill.py                 # 补所有日频接口
  python download_05_backfill.py daily stk_limit  # 只补指定接口
"""
import sys

import pandas as pd

import config
from tushare_client import call, get_trade_dates, logger, PermissionError_

# 补洞候选接口。补洞逻辑是"重新请求缺失日, 有数据才合并, 仍空才报告",
# 对下列接口都成立。区别只在于对"重试后仍空"的解读:
#   - 稠密接口(daily 等): 仍空是异常, 需人工确认;
#   - 事件型 suspend_d:    仍空多为正常(当天确无停牌), 不必担心。
# 实测 suspend_d 也会因瞬时抖动漏下本有数据的交易日, 故一并纳入。
DATE_APIS = ["daily", "adj_factor", "daily_basic", "suspend_d", "stk_limit", "moneyflow"]
EVENT_APIS = {"suspend_d"}


def _missing_days(api_name, trade_dates):
    """返回该接口应有却缺失的交易日列表(升序)。"""
    start = config.API_START_DATES.get(api_name, config.START_DATE)
    expect = [d for d in trade_dates if d >= start]
    d = config.DATA_RAW_DIR / api_name
    files = sorted(d.glob("*.parquet"))
    if not files:
        return expect        # 整个接口都没下过
    got = set(pd.concat(
        [pd.read_parquet(f, columns=["trade_date"]) for f in files]
    )["trade_date"].unique())
    return [d for d in expect if d not in got]


def _merge_into_year(api_name, year, new_frames):
    """把补到的数据合并进 <year>.parquet(原子写)。"""
    fp = config.DATA_RAW_DIR / api_name / f"{year}.parquet"
    df = pd.concat(new_frames, ignore_index=True)
    if fp.exists():
        df = pd.concat([pd.read_parquet(fp), df], ignore_index=True)
    df = df.drop_duplicates().sort_values("trade_date").reset_index(drop=True)
    tmp = fp.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(fp)
    return len(df)


def backfill_api(api_name, trade_dates):
    missing = _missing_days(api_name, trade_dates)
    if not missing:
        logger.info("%s: 无缺失, 跳过", api_name)
        return []

    logger.info("%s: 发现 %d 个缺失交易日, 开始补洞", api_name, len(missing))
    by_year, still_empty = {}, []
    for i, td in enumerate(missing, 1):
        df = call(api_name, trade_date=td)
        if df is not None and not df.empty:
            by_year.setdefault(td[:4], []).append(df)
        else:
            still_empty.append(td)      # 重试后仍空
        if i % 20 == 0 or i == len(missing):
            logger.info("%s: %d/%d", api_name, i, len(missing))

    for year, frames in sorted(by_year.items()):
        total = _merge_into_year(api_name, year, frames)
        logger.info("%s %s: 补入 %d 天, 该年现 %d 行",
                    api_name, year, len(frames), total)

    if still_empty:
        note = "属正常(当天确无记录)" if api_name in EVENT_APIS else "可能官方确无数据, 建议人工确认"
        logger.warning("%s: %d 个日期重试后仍为空(%s): %s",
                       api_name, len(still_empty), note,
                       ",".join(still_empty[:10]) + ("..." if len(still_empty) > 10 else ""))
    return still_empty


if __name__ == "__main__":
    apis = sys.argv[1:] if len(sys.argv) > 1 else DATE_APIS
    unknown = [a for a in apis if a not in DATE_APIS]
    if unknown:
        sys.exit(f"未知接口 {unknown}, 可补: {DATE_APIS}")

    trade_dates = get_trade_dates(config.START_DATE, config.END_DATE)
    report = {}
    for api in apis:
        logger.info("=== 补洞 %s ===", api)
        try:
            report[api] = backfill_api(api, trade_dates)
        except PermissionError_ as e:
            logger.error("%s 权限不足, 跳过: %s", api, e)

    logger.info("=== 05 补洞完成 ===")
    for api, empties in report.items():
        if empties:
            logger.info("  %s 仍有 %d 天为空", api, len(empties))
