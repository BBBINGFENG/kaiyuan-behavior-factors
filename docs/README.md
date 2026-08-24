# 交易行为因子 · 实时监控网站

理想振幅 / 理想反转 / 合成因子的监控 dashboard。单文件 `index.html`（数据内嵌，
图表用 ECharts CDN），可直接托管到 GitHub Pages。

## 内容

- 三因子介绍
- 关键指标（合成因子：年化收益/IR/最大回撤/胜率/波动/IC）
- 净值曲线、回撤、月度收益、年度收益图
- 因子绩效表 + **Newey-West t 统计量**（全 / 样本内 / 样本外，收益显著性）
- 最新持仓（合成因子多空 Top 15）

## 本地预览

```bash
cd docs && python3 -m http.server 8000
# 浏览器打开 http://localhost:8000
```
（直接双击 index.html 也能看，图表走 CDN 需联网。）

## 重新生成（数据更新后）

```bash
python src/website/build_dashboard.py   # 读最新回测结果 → 重写 docs/index.html
```

## 「实时更新」完整流程（收盘后）

每日收盘后跑一遍完整管线，再重生成网站并推送：

```bash
# 1. 增量下载当日日频数据(断点续传, 已有的跳过)
python script/download_02_daily.py
python script/download_03_index_industry.py

# 2. 重建清洗层 → 因子 → 中性化 → 合成 → 回测
python src/data_clean.py
python src/factors/ideal_amplitude.py
python src/factors/ideal_reversal.py
python src/factors/neutralize.py
python src/factors/composite.py
python src/backtest/run.py ideal_amplitude_neutral -1
python src/backtest/run.py ideal_reversal_neutral -1
python src/backtest/run.py composite 1

# 3. 重生成网站并推送
python src/website/build_dashboard.py
git add docs/index.html && git commit -m "update $(date +%F)" && git push
```

可把以上写进一个 `update.sh`，用 macOS launchd 或 GitHub Actions 定时（如每日 18:00）触发。

## 部署到 GitHub Pages

```bash
# 若尚未建仓库
git init && git add . && git commit -m "init"
git remote add origin https://github.com/<用户名>/<仓库名>.git
git push -u origin main
```

然后在 GitHub 仓库 **Settings → Pages** 里：
- Source 选 `main` 分支
- 目录选 `/docs`

几分钟后网站上线：`https://<用户名>.github.io/<仓库名>/`

> ⚠️ **别把凭证推上去**：`.gitignore` 已忽略 `script/.tushare_token`。推之前 `git status`
> 确认没有它。`data_raw/`（2.4G）也已忽略，不会入库。
