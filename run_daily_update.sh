#!/bin/zsh
# run_daily_update.sh — 每日更新入口
#
# 依次: 增量下载日频数据 → 清洗 → 因子 → 中性化 → 合成 → 回测 → live盯市
#       → 重生成 dashboard 数据 → 推送 GitHub。
# 由 launchd(com.kaiyuan.daily-update) 每天 08:00 调用, 也可手动运行。
# 任一步失败即停止, 已发布网站保持不变。
#
# 用法: ./run_daily_update.sh

set -e
set -u
set -o pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="/usr/bin/python3"                 # 系统 python3(已装 pandas/pyarrow/tushare/scipy)
LOG="$ROOT/live_update.log"
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }
run() { local n="$1"; shift; log "START $n"; if "$@" >>"$LOG" 2>&1; then log "OK    $n"; else log "FAIL  $n"; exit 1; fi; }

log "===== daily update begin ====="
run "下载日频增量"   "$PY" script/download_02_daily.py
run "下载指数行业"   "$PY" script/download_03_index_industry.py
run "清洗层"         "$PY" src/data_clean.py
run "理想振幅"       "$PY" src/factors/ideal_amplitude.py
run "理想反转"       "$PY" src/factors/ideal_reversal.py
run "中性化"         "$PY" src/factors/neutralize.py
run "合成因子"       "$PY" src/factors/composite.py
run "回测-合成"      "$PY" src/backtest/run.py composite 1
run "回测-振幅中性"  "$PY" src/backtest/run.py ideal_amplitude_neutral -1
run "回测-反转中性"  "$PY" src/backtest/run.py ideal_reversal_neutral -1
run "live盯市"       "$PY" src/backtest/live_track.py
run "生成dashboard"  "$PY" src/website/build_dashboard.py

# 推送到 GitHub(非致命: 失败不中断, 只记日志)
git add docs/data/dashboard_data.js
if git commit -m "live update $(date +%F)" >>"$LOG" 2>&1; then
    if git push >>"$LOG" 2>&1; then log "OK    已推送 GitHub"; else log "WARN  推送失败(检查网络/凭证)"; fi
else
    log "INFO  数据无变化, 跳过提交"
fi
log "===== daily update done ====="
