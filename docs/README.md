# docs/ — 实时监控 dashboard（GitHub Pages）

理想振幅 / 理想反转 / 合成因子的监控页，**聚焦今年 live 表现**。设计沿用
china-value-dashboard（浅色暖调 + 深色模式 + Chart.js 本地打包）。

## 结构

```
docs/
├── index.html                 静态骨架
├── assets/
│   ├── style.css              样式(浅色/深色)
│   ├── chart.umd.js           Chart.js(本地打包, 不走CDN → 画质稳定)
│   └── app.js                 渲染层
└── data/
    └── dashboard_data.js      ← 唯一每日重生成的文件(window.DASHBOARD_DATA)
```

## 内容

- **关键指标（今年 live）**：今年累计/年化/Sharpe/最大回撤/波动 + 回测年化(参考)
- **Live 净值 / Live 回撤**：今年每日盯市（主视图）
- 历史回测净值（月度对数）、月度/年度多空收益、各因子最新月
- 最新持仓（合成多空 Top 15）
- 因子绩效 + Newey-West t 统计量（全/样本内/样本外）

## 每日自动更新（每天 8:00）

由 launchd `com.kaiyuan.daily-update` 每天 8 点调用项目根目录的
`run_daily_update.sh`：增量下载 → 因子/回测/live 盯市 → 重生成
`data/dashboard_data.js` → `git push`。

```bash
# 手动跑一次
./run_daily_update.sh
# 查看日志
tail -f live_update.log
# 停用/重启定时
launchctl unload ~/Library/LaunchAgents/com.kaiyuan.daily-update.plist
launchctl load -w ~/Library/LaunchAgents/com.kaiyuan.daily-update.plist
```

## 本地预览

```bash
cd docs && python3 -m http.server 8000
# 打开 http://localhost:8000
```

## 部署（GitHub Pages）

仓库 **Settings → Pages** → Source: `main` 分支 + **`/docs`** 目录 → Save。
网址：`https://BBBINGFENG.github.io/<仓库名>/`。每次 `git push` 后自动重新部署。
