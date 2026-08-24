# -*- coding: utf-8 -*-
"""
Tushare 客户端封装 (client wrapper)
统一处理: 初始化、限速 (rate limiting)、失败重试 (retry with backoff)、分页。
所有下载脚本通过 call() / call_paged() 调接口, 不直接调 pro.xxx()。
"""
import time
import logging

import tushare as ts

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tushare")

_pro = None


def get_pro():
    """惰性初始化 pro api, 全局只建一次连接。"""
    global _pro
    if _pro is None:
        ts.set_token(config.get_token())
        _pro = ts.pro_api()
    return _pro


class PermissionError_(Exception):
    """积分/权限不足 —— 重试没有意义, 直接跳过该接口。"""


def _is_permission_error(err: Exception) -> bool:
    msg = str(err)
    return any(k in msg for k in ["权限", "积分", "没有接口访问权限", "尚未开通"])


def _is_rate_limit_error(err: Exception) -> bool:
    """Tushare 的限流报错, 如"访问接口(daily)频率超限(500次/分钟)"。"""
    msg = str(err)
    return any(k in msg for k in ["频率超限", "每分钟", "最多访问该接口", "超过访问频次"])


# 限流是【按分钟】计的窗口 —— 短退避(3/6/12/24s)会全部撞在同一个窗口里,
# 必须等满一个整窗口才有意义。故限流单独处理, 且不消耗普通重试次数。
RATE_LIMIT_WAIT = 65        # 秒, 略大于 60 确保跨过窗口
RATE_LIMIT_MAX_WAITS = 10   # 最多等 10 次(约 11 分钟), 足以熬过持续性限流

# 自适应降速: 每撞一次限流就永久放慢一点, 让脚本自己收敛到可持续速率。
# (实测 Tushare 的实际可用频次会随服务端负载波动, 固定 sleep 值调不准)
_extra_sleep = 0.0
_EXTRA_SLEEP_STEP = 0.10
_EXTRA_SLEEP_MAX = 1.50


def call(api_name: str, max_retries: int = 4, **kwargs):
    """调用 Tushare 接口, 带限速、限流退避与失败重试。

    三类异常分开处理:
      - 权限/积分不足  -> 立即抛 PermissionError_ (重试无意义)
      - 限流(按分钟)   -> 等满一个分钟窗口再试, 不消耗普通重试次数
      - 其他(网络抖动) -> 指数退避 3/6/12/24s

    Returns
    -------
    pandas.DataFrame  (可能为空表, 调用方需自行判断)
    """
    global _extra_sleep
    pro = get_pro()
    last_err = None
    attempt = 0          # 普通错误的重试计数
    rl_waits = 0         # 限流等待计数(独立于 attempt)

    while attempt < max_retries:
        try:
            df = getattr(pro, api_name)(**kwargs)
            time.sleep(config.SLEEP_SECONDS + _extra_sleep)   # 常规限速
            return df
        except Exception as e:
            if _is_permission_error(e):
                raise PermissionError_(f"{api_name}: {e}") from e

            if _is_rate_limit_error(e):
                rl_waits += 1
                if rl_waits > RATE_LIMIT_MAX_WAITS:
                    raise RuntimeError(
                        f"{api_name}({kwargs}) 持续限流, 已等待 "
                        f"{RATE_LIMIT_MAX_WAITS} 个窗口仍失败: {e}")
                if _extra_sleep < _EXTRA_SLEEP_MAX:     # 自适应降速
                    _extra_sleep = min(_extra_sleep + _EXTRA_SLEEP_STEP, _EXTRA_SLEEP_MAX)
                logger.warning("%s 触发限流, 等待%ds跨过窗口 (第%d/%d次); "
                               "后续限速降至 %.2fs/次",
                               api_name, RATE_LIMIT_WAIT, rl_waits,
                               RATE_LIMIT_MAX_WAITS, config.SLEEP_SECONDS + _extra_sleep)
                time.sleep(RATE_LIMIT_WAIT)
                continue   # 限流不算普通重试

            last_err = e
            attempt += 1
            wait = 2 ** attempt * 3            # 3s, 6s, 12s, 24s
            logger.warning("%s(%s) 第%d次失败: %s, %ds后重试",
                           api_name, kwargs, attempt, e, wait)
            time.sleep(wait)

    raise RuntimeError(f"{api_name}({kwargs}) 重试{max_retries}次仍失败: {last_err}")


def call_paged(api_name: str, page_size: int = 5000, **kwargs):
    """对有单次返回行数上限的接口做 offset 分页, 返回合并后的 DataFrame。"""
    import pandas as pd

    frames, offset = [], 0
    while True:
        df = call(api_name, limit=page_size, offset=offset, **kwargs)
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < page_size:                # 最后一页
            break
        offset += page_size
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_trade_dates(start_date: str, end_date: str) -> list:
    """返回 [start, end] 区间内所有交易日 (yyyymmdd 字符串, 升序)。"""
    df = call("trade_cal", exchange="SSE", start_date=start_date,
              end_date=end_date, is_open="1")
    return sorted(df["cal_date"].tolist())
