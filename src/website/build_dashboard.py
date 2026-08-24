# -*- coding: utf-8 -*-
"""
阶段F：实时监控网站 — 数据生成 + 静态页面

读回测结果 → 算 dashboard 全部数据 → 生成自包含 website/index.html。
展示: 三因子介绍 + 关键指标 + 净值/回撤/月度/年度图 + 因子绩效表 +
      Newey-West t统计量(收益显著性) + 最新持仓/换手。

图表用 ECharts(CDN), 数据内嵌为 JSON, 单文件即可上 GitHub Pages。
"实时更新": 收盘后重跑数据管线 + 本脚本 → git push(见 website/README.md)。

用法: python src/website/build_dashboard.py
"""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dashboard")

ROOT = Path(__file__).resolve().parent.parent.parent
DC = ROOT / "data_clean"
FAC = ROOT / "factors"
BT = ROOT / "backtest"
WEB = ROOT / "docs"          # GitHub Pages 从 /docs 发布
WEB.mkdir(exist_ok=True)

SPLIT = "20201231"
PPY = 12

FACTORS = {
    "composite":              {"label": "合成因子", "dir": 1,  "color": "#4c9be8"},
    "ideal_amplitude_neutral": {"label": "理想振幅", "dir": -1, "color": "#3fb27f"},
    "ideal_reversal_neutral":  {"label": "理想反转", "dir": -1, "color": "#e8a33f"},
}

INTROS = {
    "理想振幅": "高价态振幅承载负向alpha、低价态是噪声。取20日中收盘价最高/最低各25%交易日的"
                "振幅均值作差 V=V_high−V_low。负向因子(振幅越大未来越跌)。",
    "理想反转": "反转之力源于大单成交。按每日大单成交占比(替代原口径的平均单笔成交金额)对20日"
                "切割, 大单主导日涨跌幅和 M_high 减小单主导日 M_low。负向因子。",
    "合成因子": "理想振幅与理想反转在行业内去极值标准化、按过去12期ICIR滚动加权合成。分散化后"
                "IR高于任一单因子、回撤更低。正向因子(值越大未来越涨)。",
}


def nw_tstat(r, lags=6):
    """Newey-West(HAC) t统计量。"""
    r = pd.Series(r).dropna().values
    n = len(r)
    if n < 12:
        return np.nan
    e = r - r.mean()
    s = (e @ e) / n
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    return r.mean() / np.sqrt(s / n)


def _load_ls(name):
    df = pd.read_parquet(BT / name / "ls_returns.parquet")
    return df["ls"]


def _perf(r):
    r = r.dropna()
    nav = (1 + r).cumprod()
    return {
        "ann": float(nav.iloc[-1] ** (PPY / len(r)) - 1),
        "vol": float(r.std() * np.sqrt(PPY)),
        "ir": float((nav.iloc[-1] ** (PPY / len(r)) - 1) / (r.std() * np.sqrt(PPY))),
        "mdd": float((nav / nav.cummax() - 1).min()),
        "win": float((r > 0).mean()),
    }


def build_data():
    mask = pd.read_parquet(DC / "investable_mask.parquet")
    basic = pd.read_parquet(ROOT / "data_raw/basic/stock_basic.parquet").set_index("ts_code")["name"]

    comp_ls = _load_ls("composite")
    nav = (1 + comp_ls.dropna()).cumprod()
    dd = (nav / nav.cummax() - 1)

    # 年度收益
    yr = comp_ls.dropna().groupby([d[:4] for d in comp_ls.dropna().index]).apply(lambda s: (1 + s).prod() - 1)

    # 关键指标(合成)
    perf = _perf(comp_ls)
    ic_comp = pd.read_parquet(BT / "composite" / "ic_series.parquet")["ic"]

    # 因子绩效表 + t统计量(全/样本内/样本外)
    rows = []
    for name, meta in FACTORS.items():
        ls = _load_ls(name)
        ic = pd.read_parquet(BT / name / "ic_series.parquet")["ic"]
        def seg(s, lo=None, hi=None):
            if lo: s = s[s.index >= lo]
            if hi: s = s[s.index <= hi]
            return s
        rows.append({
            "label": meta["label"],
            "ic": float(ic.mean()),
            "icir_y": float(ic.mean() / ic.std() * np.sqrt(12)),
            "ir": _perf(ls)["ir"],
            "t_full": float(nw_tstat(ls)),
            "t_in": float(nw_tstat(seg(ls, hi=SPLIT))),
            "t_out": float(nw_tstat(seg(ls, lo=SPLIT))),
            "ann": _perf(ls)["ann"],
            "win": _perf(ls)["win"],
        })

    # 最新持仓(合成: 正向, 多=最高分组 空=最低)
    comp_fac = pd.read_parquet(FAC / "composite.parquet")
    last = comp_fac.index[-1]
    m = mask.reindex(comp_fac.index).loc[last]
    s = comp_fac.loc[last].where(m).dropna().sort_values(ascending=False)
    n_side = max(1, len(s) // 5)
    longs = s.head(15); shorts = s.tail(15)[::-1]
    def hold_rows(sr):
        return [{"code": c, "name": str(basic.get(c, "")), "score": round(float(v), 3)} for c, v in sr.items()]

    # 换手(合成多头组, 最近两期)
    prev = comp_fac.index[-2]
    mp = mask.reindex(comp_fac.index).loc[prev]
    sp = comp_fac.loc[prev].where(mp).dropna().sort_values(ascending=False)
    long_now = set(s.head(len(s)//5).index); long_prev = set(sp.head(len(sp)//5).index)
    turnover = len(long_now - long_prev) / max(len(long_now), 1)

    data = {
        "updated": last,
        "nav": {"dates": list(nav.index), "vals": [round(v, 4) for v in nav.values]},
        "dd": {"dates": list(dd.index), "vals": [round(v * 100, 2) for v in dd.values]},
        "monthly": {"dates": list(comp_ls.dropna().index)[-36:],
                    "vals": [round(v * 100, 2) for v in comp_ls.dropna().values[-36:]]},
        "annual": {"years": list(yr.index), "vals": [round(v * 100, 1) for v in yr.values]},
        "perf": {k: round(v, 4) for k, v in perf.items()},
        "ic_mean": round(float(ic_comp.mean()), 4),
        "icir_y": round(float(ic_comp.mean() / ic_comp.std() * np.sqrt(12)), 2),
        "factor_table": rows,
        "longs": hold_rows(longs), "shorts": hold_rows(shorts),
        "n_long": int((s > s.median()).sum()), "turnover": round(float(turnover), 3),
        "split": SPLIT, "intros": INTROS,
    }

    # Long-Only 组合
    lo_nav = pd.read_parquet(BT / "long_only" / "nav.parquet")
    lo_stats = json.loads((BT / "long_only" / "stats.json").read_text(encoding="utf-8"))
    data["longonly"] = {
        "dates": list(lo_nav.index),
        "series": {c: [round(float(v), 4) for v in lo_nav[c].values] for c in lo_nav.columns},
        "stats": lo_stats,
    }
    return data


def _jsonable(o):
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    raise TypeError(type(o))


def render(data):
    js = json.dumps(data, ensure_ascii=False, default=_jsonable)
    html = _TEMPLATE.replace("__DATA__", js)
    (WEB / "index.html").write_text(html, encoding="utf-8")
    logger.info("网站已生成: %s (数据截至 %s)", WEB / "index.html", data["updated"])


# --- HTML 模板(dark, ECharts) ---
_TEMPLATE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>开源金工交易行为因子 · 实时监控</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
:root{--bg:#0b0d10;--card:#15181d;--bd:#242830;--tx:#e6e8ea;--mut:#8a919b;--grn:#3fb27f;--red:#e05c5c;--blu:#4c9be8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:-apple-system,"PingFang SC",Segoe UI,Roboto,sans-serif;font-size:14px;line-height:1.5;padding:28px;max-width:1280px;margin:0 auto}
h1{font-size:22px;font-weight:700} h2{font-size:15px;font-weight:600;margin:26px 0 12px}
.sub{color:var(--mut);font-size:13px;margin-top:4px}
.grid{display:grid;gap:14px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px}
.tiles{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.tile .k{color:var(--mut);font-size:12px} .tile .v{font-size:24px;font-weight:700;margin-top:6px}
.tile .n{color:var(--mut);font-size:12px;margin-top:4px}
.g2{grid-template-columns:1fr 1fr} .g3{grid-template-columns:1fr 1fr 1fr}
.chart{height:300px} .intro .t{font-weight:600;margin-bottom:6px}
.pos{color:var(--grn)} .neg{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--bd)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--mut);font-weight:500}
.foot{color:var(--mut);font-size:12px;margin-top:30px;border-top:1px solid var(--bd);padding-top:14px}
@media(max-width:860px){.g2,.g3{grid-template-columns:1fr}}
</style></head><body>
<h1>开源金工交易行为因子 · 实时监控</h1>
<div class="sub" id="sub"></div>

<h2>因子介绍</h2>
<div class="grid g3" id="intros"></div>

<h2>关键指标（合成因子多空对冲）</h2>
<div class="grid tiles" id="tiles"></div>

<h2>净值与回撤</h2>
<div class="grid g2"><div class="card"><div id="navc" class="chart"></div></div>
<div class="card"><div id="ddc" class="chart"></div></div></div>

<div class="grid g2" style="margin-top:14px"><div class="card"><div id="monc" class="chart"></div></div>
<div class="card"><div id="annc" class="chart"></div></div></div>

<h2>Long-Only 组合（可投资纯多头 · 买最优20% · 月度调仓 · 扣单边成本）</h2>
<div class="card"><div id="loc" class="chart" style="height:360px"></div></div>
<div class="card" style="margin-top:14px"><table id="lotab"></table>
<div class="sub">纯多头组合含市场beta(波动/回撤远大于多空对冲)；EW=等权, VW=市值加权。基准为中证全指。</div></div>

<h2>因子绩效与收益显著性（Newey-West t 统计量）</h2>
<div class="card"><table id="ftab"></table>
<div class="sub">t 统计量基于月度多空收益的 HAC(Newey-West, 6阶) 标准误；|t|≥1.96 显著(5%)。样本内≤2020 / 样本外&gt;2020。</div></div>

<h2>最新持仓（合成因子，截至 <span id="reb"></span>）</h2>
<div class="grid g2">
<div class="card"><div style="color:var(--grn);font-weight:600;margin-bottom:8px">多头 Top 15（合成分最高）</div><table id="longt"></table></div>
<div class="card"><div style="color:var(--red);font-weight:600;margin-bottom:8px">空头 Top 15（合成分最低）</div><table id="shortt"></table></div>
</div>

<div class="foot" id="foot"></div>

<script>
const D=__DATA__;
const pct=x=>(x*100).toFixed(1)+'%', sign=x=>(x>=0?'+':'')+x;
const ax={axisLine:{lineStyle:{color:'#3a3f48'}},axisLabel:{color:'#8a919b',fontSize:11},splitLine:{lineStyle:{color:'#1e222a'}}};
const base={backgroundColor:'transparent',grid:{left:48,right:16,top:24,bottom:34},tooltip:{trigger:'axis'}};
const CHARTS=[];
function mk(id,opt){const c=echarts.init(document.getElementById(id),'dark');c.setOption(Object.assign({},base,opt));CHARTS.push(c);}

document.getElementById('sub').textContent='中国A股全市场 · 月度调仓 · 数据截至 '+D.updated+' · 仅供研究，非投资建议';
document.getElementById('reb').textContent=D.updated;

// 介绍
document.getElementById('intros').innerHTML=Object.entries(D.intros).map(([k,v])=>
 `<div class="card intro"><div class="t">${k}</div><div class="sub">${v}</div></div>`).join('');

// 关键指标
const P=D.perf, tiles=[
 ['回测年化收益',pct(P.ann),'2010–2026 多空'],['信息比率 IR',P.ir.toFixed(2),'年化收益/波动'],
 ['最大回撤',pct(P.mdd),'月度净值'],['月度胜率',pct(P.win),'多空为正月份'],
 ['年化波动',pct(P.vol),''],['IC 均值',D.ic_mean.toFixed(3),'ICIR年化 '+D.icir_y]];
document.getElementById('tiles').innerHTML=tiles.map(([k,v,n])=>{
 const cls=(k.includes('回撤'))?'neg':(k.includes('年化收益')||k.includes('IR')||k.includes('胜率')?'pos':'');
 return `<div class="card tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="n">${n}</div></div>`;}).join('');

// 净值
mk('navc',{title:{text:'合成因子多空净值 (2010–2026)',textStyle:{fontSize:13,color:'#e6e8ea'}},
 xAxis:Object.assign({type:'category',data:D.nav.dates,axisLabel:{color:'#8a919b',fontSize:10,interval:23}},ax),
 yAxis:Object.assign({type:'value'},ax),
 series:[{type:'line',data:D.nav.vals,showSymbol:false,lineStyle:{color:'#4c9be8',width:1.6},areaStyle:{color:'rgba(76,155,232,.12)'}}]});
// 回撤
mk('ddc',{title:{text:'回撤 (%)',textStyle:{fontSize:13,color:'#e6e8ea'}},
 xAxis:Object.assign({type:'category',data:D.dd.dates,axisLabel:{color:'#8a919b',fontSize:10,interval:23}},ax),
 yAxis:Object.assign({type:'value'},ax),
 series:[{type:'line',data:D.dd.vals,showSymbol:false,lineStyle:{color:'#e05c5c',width:1},areaStyle:{color:'rgba(224,92,92,.15)'}}]});
// 月度收益
mk('monc',{title:{text:'月度多空收益 (近36月, %)',textStyle:{fontSize:13,color:'#e6e8ea'}},
 xAxis:Object.assign({type:'category',data:D.monthly.dates,axisLabel:{color:'#8a919b',fontSize:9,interval:5}},ax),
 yAxis:Object.assign({type:'value'},ax),
 series:[{type:'bar',data:D.monthly.vals.map(v=>({value:v,itemStyle:{color:v>=0?'#3fb27f':'#e05c5c'}}))}]});
// 年度收益
mk('annc',{title:{text:'年度多空收益 (%)',textStyle:{fontSize:13,color:'#e6e8ea'}},
 xAxis:Object.assign({type:'category',data:D.annual.years,axisLabel:{color:'#8a919b',fontSize:10}},ax),
 yAxis:Object.assign({type:'value'},ax),
 series:[{type:'bar',data:D.annual.vals.map(v=>({value:v,itemStyle:{color:v>=0?'#3fb27f':'#e05c5c'}}))}]});

// Long-Only 组合
const LC=['#4c9be8','#3fb27f','#e8a33f','#a06be8','#8a919b'];
const loNames=Object.keys(D.longonly.series);
const loSeries=loNames.map((nm,i)=>({name:nm,type:'line',data:D.longonly.series[nm],showSymbol:false,
 lineStyle:{width:i===4?1.3:1.7,color:LC[i],type:i===4?'dashed':'solid'}}));
mk('loc',{color:LC,legend:{data:loNames,textStyle:{color:'#c8ccd2',fontSize:11},top:0},
 grid:{left:48,right:16,top:42,bottom:34},
 title:{text:'Long-Only 净值 (归一, 2011–2026)',textStyle:{fontSize:13,color:'#e6e8ea'},top:22},
 xAxis:Object.assign({type:'category',data:D.longonly.dates,axisLabel:{color:'#8a919b',fontSize:10,interval:17}},ax),
 yAxis:Object.assign({type:'value'},ax),series:loSeries});
let lt='<tr><th>策略</th><th>累计收益</th><th>年化</th><th>年化波动</th><th>Sharpe</th><th>最大回撤</th><th>月数</th></tr>';
loNames.forEach(nm=>{const s=D.longonly.stats[nm];lt+=`<tr><td>${nm}</td><td>${pct(s.total)}</td><td>${pct(s.ann)}</td><td>${pct(s.vol)}</td><td>${s.sharpe}</td><td class="neg">${pct(s.mdd)}</td><td>${s.months}</td></tr>`;});
document.getElementById('lotab').innerHTML=lt;

// 因子绩效表
function tstar(t){const a=Math.abs(t);const s=a>=2.58?'***':a>=1.96?'**':a>=1.65?'*':'';return t.toFixed(2)+s;}
let ft='<tr><th>因子</th><th>IC均值</th><th>ICIR年化</th><th>多空IR</th><th>年化收益</th><th>胜率</th><th>t(全)</th><th>t(样本内)</th><th>t(样本外)</th></tr>';
D.factor_table.forEach(r=>{ft+=`<tr><td>${r.label}</td><td>${r.ic.toFixed(4)}</td><td>${r.icir_y.toFixed(2)}</td><td>${r.ir.toFixed(2)}</td><td>${pct(r.ann)}</td><td>${pct(r.win)}</td><td>${tstar(r.t_full)}</td><td>${tstar(r.t_in)}</td><td>${tstar(r.t_out)}</td></tr>`;});
document.getElementById('ftab').innerHTML=ft;

// 持仓
function ht(rows){let h='<tr><th>代码</th><th>名称</th><th>合成分</th></tr>';rows.forEach(r=>{h+=`<tr><td>${r.code}</td><td>${r.name}</td><td>${r.score}</td></tr>`;});return h;}
document.getElementById('longt').innerHTML=ht(D.longs);
document.getElementById('shortt').innerHTML=ht(D.shorts);

document.getElementById('foot').innerHTML='合成因子多头约 '+D.n_long+' 只 · 多头单边换手 '+pct(D.turnover)+
 ' · 聪明钱/APM 因子需分钟数据，当前不复现 · 数据源 Tushare Pro · 本页为研究复现，非投资建议';

function resizeAll(){CHARTS.forEach(c=>c.resize());}
window.addEventListener('resize',resizeAll);
setTimeout(resizeAll,80); window.addEventListener('load',resizeAll);
// 容器由0变宽(如面板从隐藏变可见)时自动重绘, 彻底解决初始化时序问题
if(window.ResizeObserver){CHARTS.forEach(c=>{try{new ResizeObserver(()=>c.resize()).observe(c.getDom())}catch(e){}});}
</script></body></html>"""


if __name__ == "__main__":
    render(build_data())
