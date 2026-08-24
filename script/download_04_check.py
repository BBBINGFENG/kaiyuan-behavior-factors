# -*- coding: utf-8 -*-
"""
下载脚本 04: 数据完整性检查 (sanity check)

纯本地脚本, 不联网、不需要 token。下载完成后运行, 逐个数据集报告:
行数、日期覆盖范围、相对交易日历缺失的天数, 并落地 CSV 报告。

输出: data_raw/_check_report.csv
用法: python download_04_check.py
"""
import pandas as pd

import config
from tushare_client import logger

DATE_APIS = ["daily", "adj_factor", "daily_basic", "suspend_d", "stk_limit", "moneyflow"]

# 停牌/涨跌停是"事件型"数据 —— 某些交易日天然无记录, 缺失不代表下载不全
EVENT_APIS = {"suspend_d"}


def load_open_dates():
    fp = config.DATA_RAW_DIR / "basic" / "trade_cal.parquet"
    if not fp.exists():
        raise SystemExit("未找到 trade_cal.parquet, 请先运行 download_01_basic.py")
    cal = pd.read_parquet(fp)
    return set(cal.loc[cal["is_open"] == 1, "cal_date"])


def check():
    open_dates = load_open_dates()
    rows = []

    for api in DATE_APIS:
        d = config.DATA_RAW_DIR / api
        files = sorted(d.glob("*.parquet")) if d.exists() else []
        if not files:
            rows.append({"dataset": api, "status": "缺失(未下载)", "rows": 0})
            logger.warning("%-12s 未找到任何文件", api)
            continue

        dates = pd.concat([pd.read_parquet(f, columns=["trade_date"]) for f in files])
        got = set(dates["trade_date"].unique())
        start = config.API_START_DATES.get(api, config.START_DATE)
        expect = {dt for dt in open_dates if start <= dt <= config.END_DATE}
        missing = sorted(expect - got)

        if not missing:
            status = "OK"
        elif api in EVENT_APIS:
            status = f"缺{len(missing)}天(事件型, 多为正常)"
        else:
            status = f"缺{len(missing)}天"

        rows.append({
            "dataset": api,
            "status": status,
            "rows": len(dates),
            "date_min": dates["trade_date"].min(),
            "date_max": dates["trade_date"].max(),
            "missing_days": len(missing),
            "missing_sample": ",".join(missing[:5]),
        })
        logger.info("%-12s %9d 行  %s~%s  缺 %d 天",
                    api, len(dates), dates["trade_date"].min(),
                    dates["trade_date"].max(), len(missing))

    # 基础/行业数据: 只报行数
    for rel in ["basic/trade_cal.parquet", "basic/stock_basic.parquet",
                "basic/namechange.parquet", "industry/sw_l1_classify.parquet",
                "industry/sw_l1_members.parquet"]:
        fp = config.DATA_RAW_DIR / rel
        n = len(pd.read_parquet(fp)) if fp.exists() else 0
        rows.append({"dataset": rel, "status": "OK" if n else "缺失", "rows": n})
        logger.info("%-12s %9d 行", rel.split("/")[-1].replace(".parquet", ""), n)

    # 指数数据: 报文件数与行数
    for sub in ["index_daily", "index_weight"]:
        d = config.DATA_RAW_DIR / sub
        files = sorted(d.glob("*.parquet")) if d.exists() else []
        n = sum(len(pd.read_parquet(f, columns=["trade_date"])) for f in files)
        rows.append({"dataset": sub, "status": f"{len(files)}个指数" if files else "缺失",
                     "rows": n})
        logger.info("%-12s %9d 行 (%d 个指数文件)", sub, n, len(files))

    report = pd.DataFrame(rows)
    out = config.DATA_RAW_DIR / "_check_report.csv"
    report.to_csv(out, index=False, encoding="utf-8-sig")   # utf-8-sig: Excel 中文不乱码
    print()
    print(report.to_string(index=False))
    print(f"\n报告已保存: {out}")


if __name__ == "__main__":
    check()
