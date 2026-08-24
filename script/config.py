# -*- coding: utf-8 -*-
"""
项目全局配置 (configuration)
所有下载脚本从这里读取 token、路径、时间范围等参数。
"""
import os
from pathlib import Path

# ---------------------------------------------------------------- 1. Token
# token 不写在代码里(一旦提交进 git 历史很难彻底清除)。按优先级读取:
#   1) 环境变量 TUSHARE_TOKEN
#   2) 本地文件 script/.tushare_token  (已在 .gitignore 中忽略)
# 已为你写好 .tushare_token, 开箱即用; 换 token 时改该文件即可。


def get_token() -> str:
    """惰性读取 token —— 只在真正要联网时调用。

    做成函数而非模块级常量, 是为了让纯本地脚本(如 download_04_check.py)
    在没有 token 的环境里也能正常 import。
    """
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    token_file = Path(__file__).resolve().parent / ".tushare_token"
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    raise RuntimeError(
        "未找到 Tushare token。请设置环境变量 TUSHARE_TOKEN, "
        "或在 script/.tushare_token 中写入 token(单独一行)。"
    )


# ---------------------------------------------------------------- 2. 路径
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_RAW_DIR = PROJECT_ROOT / "data_raw"        # 原始数据(本脚本的输出)
DATA_CLEAN_DIR = PROJECT_ROOT / "data_clean"    # 清洗后数据(后续步骤)
FACTOR_DIR = PROJECT_ROOT / "factors"           # 因子值
BACKTEST_DIR = PROJECT_ROOT / "backtest"        # 回测结果

for _folder in [DATA_RAW_DIR, DATA_CLEAN_DIR, FACTOR_DIR, BACKTEST_DIR]:
    _folder.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 3. 时间范围
# 回测重点区间 2010-04 ~ 2026-06(对齐研报)。因子需要 20 日回看窗口,
# 这里统一从 2009 年起下载, 留足 1 年缓冲。
# 若日后想要更早的历史, 直接调小下面的日期重跑即可 —— 缺失年份会自动补下载。
START_DATE = "20090101"
END_DATE = "20260630"

# 各接口官方数据的实际起始时间不同, 提前设好避免大量空请求
API_START_DATES = {
    "daily": START_DATE,          # 日线行情
    "adj_factor": START_DATE,     # 复权因子
    "daily_basic": START_DATE,    # 每日指标(换手率/市值)
    "suspend_d": "20100101",      # 停复牌
    "stk_limit": "20100101",      # 涨跌停价
    "moneyflow": "20100101",      # 个股资金流(大单切割用)
}

# ---------------------------------------------------------------- 4. 限速
# Tushare 按积分限制每分钟调用次数。0.35s/次 ≈ 170 次/分钟。
# 若频繁出现"每分钟最多访问该接口 xxx 次"的报错, 调大此值。
SLEEP_SECONDS = 0.35

# ---------------------------------------------------------------- 5. 指数与行业
# 需要成分权重的指数(分池回测用, 月度快照)
INDEX_CODES = {
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "中证800": "000906.SH",
    "中证1000": "000852.SH",
}
# 仅取日线做基准净值(成分权重接口多数无权限)
INDEX_DAILY_ONLY = {
    "国证2000": "399303.SZ",
    "上证指数": "000001.SH",
    "中证全指": "000985.CSI",
}
# 研报用中信一级行业, Tushare 无中信数据, 用申万一级替代(报告中需注明)
SW_SRC = "SW2021"
