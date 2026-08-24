# script —— 数据下载脚本

从 Tushare Pro 下载回测所需的全部原始数据，落盘到 `../data_raw/`。
数据与因子的对应关系见本文末尾的表格，因子定义见
`../开源金工交易行为因子_文章总结与回测方案.md`。

## 环境准备

```bash
pip install tushare pandas pyarrow
```

## Tushare token

token 不写在代码里（避免提交进 git 历史）。按优先级读取：
**环境变量 `TUSHARE_TOKEN` > 本地文件 `script/.tushare_token`**。

`.tushare_token` 已经写好，开箱即用，无需额外配置。换 token 时改该文件即可：

```bash
echo '你的新token' > script/.tushare_token   # 该文件已在 .gitignore 中忽略
```

## 运行顺序

```bash
cd script
python download_01_basic.py            # 交易日历/股票列表/历史更名   (约 10 秒)
python download_02_daily.py            # 6 个日频接口, 按交易日循环   (约 4-5 小时)
python download_03_index_industry.py   # 指数日线/成分权重/申万行业   (约 10 分钟)
python download_04_check.py            # 完整性检查, 输出报告         (约 1 分钟)
```

`download_02_daily.py` 最耗时，建议后台跑：

```bash
nohup python3 -u download_02_daily.py > dl.log 2>&1 &
tail -f dl.log
```

## 关键设计

- **断点续传**：`download_02_daily.py` 每 50 个交易日落盘一次（`FLUSH_EVERY`）。
  中断后**直接重跑**即可，最多丢失最近一批（<50 天），会从各年份文件里已保存的
  最大日期之后继续，已完整的年份自动跳过。
- **原子写入**：落盘先写 `.parquet.tmp` 再原子替换，避免写入中途被打断导致
  parquet 文件损坏（半个损坏的文件比没有更麻烦）。
- **只跑指定接口**：`python download_02_daily.py daily adj_factor`
- **权限容错**：某接口积分不足时记录并跳过，不中断其他接口的下载。
- **限速**：`config.py` 的 `SLEEP_SECONDS = 0.35`（≈170 次/分钟）。若频繁报
  "每分钟最多访问该接口 N 次"，调大它。
- **时间范围**：`config.py` 的 `START_DATE = "20090101"`。回测重点区间是
  2010-04 起（对齐研报），留了 1 年回看缓冲。**想要更早的历史，直接调小该值重跑**，
  缺失的年份会自动补下载，已有年份不受影响。

## 数据口径注意事项（影响因子复现）

1. **成交笔数缺不到** —— 理想反转因子原文要用「平均单笔成交金额 = 成交额/成交笔数」
   做 W 式切割，但 Tushare 日线与分钟接口都不提供成交笔数。因此下载了 `moneyflow`
   （特大/大/中/小单金额拆分），用「大单成交占比」作为替代切割标准——逻辑与原文
   「反转之力的微观来源是大单成交」一致。**回测报告中需对该替代口径做敏感性说明。**
2. **行业用申万替代中信** —— 研报用中信一级行业，Tushare 无中信数据，用申万一级
   （SW2021）替代，需在报告中注明。
3. **分钟数据不在本脚本范围** —— 聪明钱因子、APM 因子的下午段收益需要 1 分钟行情
   （`stk_mins`），数据量在千亿行级别，需单独设计增量下载方案（建议先用中证500
   试点跑通 pipeline 再扩到全市场）。

## 各数据集与因子的对应关系

| 数据集 | 因子用途 |
|---|---|
| `daily` + `adj_factor` | 理想振幅因子、隔夜收益、Ret20、分组收益计算 |
| `daily_basic` | 市值中性化（log 流通市值）、理想换手率因子 |
| `suspend_d` + `stk_limit` | 有效交易日过滤（停牌、一字涨跌停买不进/卖不出） |
| `moneyflow` | 理想反转因子（大单切割替代口径） |
| `index_daily` | 回测基准净值、APM 回归中的指数同期收益 |
| `index_weight` | 沪深300/中证500/中证800/中证1000 分池回测 |
| `industry`（申万一级） | 行业中性化、合成因子的行业内标准化 |
| `basic`（stock_basic/namechange） | 样本池、剔除 ST、剔除上市不满 60 日新股 |
