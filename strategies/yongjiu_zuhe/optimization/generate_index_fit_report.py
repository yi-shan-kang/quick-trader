# -*- coding: utf-8 -*-
"""指数拟合回测可视化报告生成器

复用 run_index_fit_backtest 的数据加载与组合定义，重算各组合 NAV 序列，
输出图表（PNG）与 HTML 报告（内嵌 base64 图）。

图表清单：
  1. nav_20y.png          20Y 窗口各组合净值对比（对数坐标）
  2. nav_ppus_windows.png PP_US 各窗口净值（10/15/20/30Y）
  3. nav_ppcn_windows.png PP_CN 各窗口净值（10/15/20Y）
  4. annual_bars.png      年化收益分组条形图
  5. risk_bars.png        年化波动 + 最大回撤双面板条形图
  6. corr_heatmaps.png    PP_US / PP_CN 资产月度收益相关性热图
"""
import base64
import datetime as dt
import io
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import run_index_fit_backtest as rb

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 110

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'optimization_results')
CHART_DIR = os.path.join(OUT_DIR, 'charts')
os.makedirs(CHART_DIR, exist_ok=True)

CNAME = {
    'PP_CN': 'PP_CN (A股 股/债/金/货币)',
    'PP_CN_SZ': 'PP_CN_SZ (A股+中证500)',
    'PP_US': 'PP_US (美股线)',
    'SP500_ALONE': 'S&P500 单资产',
    'HS300_ALONE': '沪深300 单资产',
    'GOLD_ALONE': '黄金 单资产',
}
COLORS = {
    'PP_CN': '#2E86AB', 'PP_CN_SZ': '#A23B72', 'PP_US': '#F18F01',
    'SP500_ALONE': '#C73E1D', 'HS300_ALONE': '#3B1F2B', 'GOLD_ALONE': '#5Fad56',
}


# ---------------------------------------------------------------
# 数据与 NAV 计算（输出带系数的值序列，供图表与相关性使用）
# ---------------------------------------------------------------
def load_all_dates():
    all_dates = None
    for key, assets in rb.COMBOS.items():
        for name, src, kw in assets:
            if src == 'cash':
                continue
            fp = os.path.join(rb.DATA_DIR, kw['file'])
            if os.path.exists(fp):
                d = pd.DatetimeIndex(pd.read_csv(fp, parse_dates=['date'])['date'])
                all_dates = all_dates.union(d) if all_dates is not None else d
    return pd.DatetimeIndex(sorted(all_dates))


def notes_for(key):
    assets = rb.COMBOS[key]
    notes = []
    for name, src, kw in assets:
        if src == 'csv' and not os.path.exists(os.path.join(rb.DATA_DIR, kw['file'])):
            return None
        n, s = rb.build_price_series((name, src, kw), ALL_DATES)
        notes.append({'name': n, 'series': s})
    return notes


def weights_for(key):
    if key == 'PP_CN_SZ':
        return rb.PP_CN_SZ_WEIGHTS
    if key in ('PP_CN', 'PP_US'):
        return rb.PP_WEIGHTS
    assets = rb.COMBOS[key]
    return [1.0 / len(assets)] * len(assets)


def window_for(key, years):
    """返回 (start, end)，与回测器一致：最晚资产起点与窗口推演取较晚者"""
    starts = []
    for name, src, kw in rb.COMBOS[key]:
        if src == 'cash':
            continue
        fp = os.path.join(rb.DATA_DIR, kw['file'])
        s = pd.read_csv(fp, parse_dates=['date'])['date']
        starts.append(s.iloc[0])
    latest_start = max(starts)
    end = ALL_DATES[-1]
    return max(latest_start, end - pd.DateOffset(years=years)), end


def run_nav(notes, weights, start, end):
    """返回 (nav Series, 各资产价值 DataFrame)；与回测器逻辑一致"""
    idx = notes[0]['series'].index
    idx = idx[(idx >= start) & (idx <= end)]
    if len(idx) < 60:
        return None, None
    mat = pd.DataFrame({n['name']: n['series'].reindex(idx).ffill() for n in notes}).dropna()
    if len(mat) < 60:
        return None, None
    init_price = mat.iloc[0]
    target = pd.Series(weights, index=mat.columns)
    shares = (target * 100.0 / init_price).astype(float)
    values = []
    last_reb_year = None
    for date, row in mat.iterrows():
        values.append((date, shares * row))
        if date.month == rb.REBALANCE_MONTH and date.year != last_reb_year:
            total = float((shares * row).sum())
            cur_w = shares * row / total
            if float((cur_w - target).abs().max()) > rb.REBALANCE_THRESHOLD:
                # 最终版逻辑：部分再平衡——只调偏离超阈值资产，未越界保持不变
                for name in mat.columns:
                    cur_v = float(shares[name] * row[name])
                    tgt_v = target[name] * total
                    if abs(cur_v - tgt_v) > rb.REBALANCE_THRESHOLD * total:
                        shares[name] = tgt_v / row[name]
                last_reb_year = date.year
    df = pd.DataFrame({d: v for d, v in values}).T
    df.columns = mat.columns
    return df.sum(axis=1), df


def monthly_ret(series):
    m = series.resample('ME').last()
    return m.pct_change().dropna()


# ---------------------------------------------------------------
# 图表
# ---------------------------------------------------------------
def chart_nav_20y(navs):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for key, nav in navs.items():
        if nav is None:
            continue
        ax.plot(nav.index, nav.values, label=CNAME[key], lw=2.0, color=COLORS[key])
    ax.set_yscale('log')
    ax.set_title('20Y 窗口净值对比（起点均为 2006-09，对数坐标，起点=100）')
    ax.set_ylabel('净值（对数）')
    ax.grid(True, which='both', alpha=0.3)
    ax.legend(loc='upper left', fontsize=10)
    fig.tight_layout()
    return fig


def chart_nav_windows(key, series_list):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for label, nav in series_list:
        ax.plot(nav.index, nav.values, lw=2.0, label=label)
    ax.set_yscale('log')
    ax.set_title(f'{CNAME[key]} 各窗口净值对比（对数坐标，起点=100）')
    ax.set_ylabel('净值（对数）')
    ax.grid(True, which='both', alpha=0.3)
    ax.legend(loc='upper left', fontsize=10)
    fig.tight_layout()
    return fig


def chart_annual_bars(piv):
    fig, ax = plt.subplots(figsize=(11, 6.5))
    keys = piv.index.tolist()
    wins = piv.columns.tolist()
    x = np.arange(len(keys))
    width = 0.82 / max(len(wins), 1)
    for i, w in enumerate(wins):
        vals = piv[w] * 100
        ax.bar(x + i * width, vals, width, label=w, color=COLORS.get(w, '#888'))
        for xi, v in zip(x + i * width, vals):
            if not np.isnan(v):
                ax.text(xi, v + 0.15, f'{v:.1f}', ha='center', fontsize=8)
    ax.set_xticks(x + width * (len(wins) - 1) / 2)
    ax.set_xticklabels([CNAME.get(k, k) for k in keys], rotation=20, ha='right', fontsize=9)
    ax.set_ylabel('年化收益 (%)')
    ax.set_title('各组合各窗口 年化收益（月频口径）')
    ax.axhline(0, color='k', lw=0.8)
    ax.grid(axis='y', alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig


def chart_risk_bars(piv_vol, piv_mdd):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    keys = piv_vol.index.tolist()
    wins = piv_vol.columns.tolist()
    x = np.arange(len(keys))
    width = 0.82 / max(len(wins), 1)

    for ax, piv, title in [(axes[0], piv_vol, '年化波动率 (%，月频口径)'),
                           (axes[1], piv_mdd, '最大回撤 (%)')]:
        for i, w in enumerate(wins):
            vals = piv[w] * 100
            color = '#C73E1D' if title.startswith('最大') else '#2E86AB'
            bars = ax.bar(x + i * width, vals, width, label=w, color=color, alpha=0.85)
            for xi, v in zip(x + i * width, vals):
                if not np.isnan(v):
                    ax.text(xi, v + (0.3 if v >= 0 else -0.6), f'{v:.1f}', ha='center', fontsize=8)
        ax.axhline(0, color='k', lw=0.8)
        ax.set_xticks(x + width * (len(wins) - 1) / 2)
        ax.set_xticklabels([CNAME.get(k, k) for k in keys], rotation=20, ha='right', fontsize=9)
        ax.set_title(title)
        ax.grid(axis='y', alpha=0.3)
        ax.legend(fontsize=9)
    fig.tight_layout()
    return fig


def chart_corr_heatmaps(corr_dict):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, (key, corr) in zip(axes, corr_dict.items()):
        if corr is None or corr.empty:
            ax.set_visible(False)
            continue
        im = ax.imshow(corr.values, cmap='RdYlGn', vmin=-1, vmax=1)
        ax.set_xticks(range(len(corr.columns)))
        ax.set_yticks(range(len(corr.index)))
        ax.set_xticklabels(corr.columns, rotation=45, ha='right', fontsize=9)
        ax.set_yticklabels(corr.index, fontsize=9)
        ax.set_title(f'{CNAME[key]} 资产相关性（月度收益）')
        for i in range(len(corr.index)):
            for j in range(len(corr.columns)):
                ax.text(j, i, f'{corr.values[i, j]:.2f}', ha='center', va='center', fontsize=8,
                        color='black')
        fig.colorbar(im, ax=ax, shrink=0.85)
    fig.tight_layout()
    return fig


def save_fig(fig, fname):
    fp = os.path.join(CHART_DIR, fname)
    fig.savefig(fp, bbox_inches='tight')
    plt.close(fig)
    return fp


def b64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------
ALL_DATES = load_all_dates()
END = ALL_DATES[-1]


def main():
    stat_json = rb.main()  # 重新生成统计（含控制台输出），保证与图表同源
    results = None
    import json
    with open(os.path.join(OUT_DIR, 'index_fit_results.json'), encoding='utf-8') as f:
        results = json.load(f)

    # 1) 收集各组合 NAV（含对照），用于互相对比
    navs_20y = {}
    nav_series_by_key = {}
    for key in rb.COMBOS:
        notes = notes_for(key)
        if notes is None:
            continue
        weights = weights_for(key)
        start, end = window_for(key, 20)
        nav, _ = run_nav(notes, weights, start, end)
        if nav is not None and (end - start).days / 365.25 > 18:
            navs_20y[key] = nav
        nav_series_by_key[key] = {
            lab: run_nav(notes, weights, *window_for(key, yrs))
            for lab, yrs in [('10Y', 10), ('15Y', 15), ('20Y', 20), ('30Y', 30)]
        }

    # 2) 图表
    figs = []
    f = save_fig(chart_nav_20y(navs_20y), 'nav_20y.png')
    figs.append(('nav_20y', '20Y 窗口组合净值对比', f))

    ppus = [(lab, nav) for lab, (nav, _) in
            nav_series_by_key['PP_US'].items() if nav is not None]
    f = save_fig(chart_nav_windows('PP_US', ppus), 'nav_ppus_windows.png')
    figs.append(('nav_ppus', 'PP_US 各窗口净值', f))

    ppcn = [(lab, nav) for lab, (nav, _) in
            nav_series_by_key['PP_CN'].items() if nav is not None]
    f = save_fig(chart_nav_windows('PP_CN', ppcn), 'nav_ppcn_windows.png')
    figs.append(('nav_ppcn', 'PP_CN 各窗口净值', f))

    # 年化/波动/回撤 透视表：组合 × 窗口
    keys_order = ['PP_US', 'PP_CN', 'PP_CN_SZ', 'SP500_ALONE', 'HS300_ALONE', 'GOLD_ALONE']
    wins_order = ['win_30Y', 'win_20Y', 'win_15Y', 'win_10Y']

    def piv(metric):
        rows = {}
        for key in keys_order:
            r = results.get(key, {})
            rows[key] = {w.replace('win_', ''): r.get(w, {}).get(metric, np.nan)
                         for w in wins_order}
        return pd.DataFrame(rows).T[['30Y', '20Y', '15Y', '10Y']]

    piv_annual = piv('annual')
    piv_vol = piv('vol')
    piv_mdd = piv('mdd')
    f = save_fig(chart_annual_bars(piv_annual), 'annual_bars.png')
    figs.append(('annual', '年化收益对比', f))
    f = save_fig(chart_risk_bars(piv_vol, piv_mdd), 'risk_bars.png')
    figs.append(('risk', '波动/回撤对比', f))

    # 相关性热图（直接使用资产价格 20Y 窗口月度收益）
    corr_dict = {}
    for key in ['PP_US', 'PP_CN']:
        notes = notes_for(key)
        start, end = window_for(key, 20)
        cols = [n['name'] for n in notes]
        pr = pd.DataFrame({n['name']: n['series'] for n in notes})
        pr = pr[(pr.index >= start) & (pr.index <= end)].dropna()
        if len(pr) > 60:
            rs = pd.DataFrame({c: monthly_ret(pr[c].dropna()) for c in cols}).dropna()
            corr_dict[key] = rs.corr()
    f = save_fig(chart_corr_heatmaps(corr_dict), 'corr_heatmaps.png')
    figs.append(('corr', '资产相关性热图', f))

    # 3) HTML 报告
    build_html(figs, results, piv_annual, piv_vol, piv_mdd)
    print('report saved to', os.path.join(OUT_DIR, 'index_fit_report.html'))


def build_html(figs, results, piv_annual, piv_vol, piv_mdd):
    imgs = ''.join(
        f'<div class="chart"><h3>{t}</h3><img src="data:image/png;base64,{b64(p)}" '
        f'alt="{k}"/></div>' for k, t, p in figs)

    # ---- 动态 KPI 卡片（1=PP_US 30Y, 2=PP_CN 20Y, 3=S&P500 30Y 对照, 4=沪深300 20Y 对照）----
    def g(key, w, m):
        return results.get(key, {}).get('win_' + w, {}).get(m)
    k1 = g('PP_US', '30Y', 'annual'); k1d = g('PP_US', '30Y', 'mdd')
    k2 = g('PP_CN', '20Y', 'annual'); k2d = g('PP_CN', '20Y', 'mdd')
    k3 = g('SP500_ALONE', '30Y', 'annual'); k3d = g('SP500_ALONE', '30Y', 'mdd')
    k4q = g('HS300_ALONE', '20Y', 'annual'); k4qd = g('HS300_ALONE', '20Y', 'mdd')

    def pc(x):
        return '--' if x is None or (isinstance(x, float) and np.isnan(x)) else f'{x*100:.2f}%'

    def gen_table(prefix):
        """生成一张指标对比表：行=组合(PP_CN/PP_CN_SZ/PP_US/SP500/HS300/GOLD)，列=10/20/30Y 各指标"""
        rows_out = []
        col_metrics = [('annual', '年化收益'),
                       ('vol', '年化波动'),
                       ('sharpe', '夏普'),
                       ('mdd', '最大回撤')]
        for key in ['PP_US', 'PP_CN', 'PP_CN_SZ', 'SP500_ALONE', 'HS300_ALONE', 'GOLD_ALONE']:
            for w in ['30Y', '20Y', '10Y']:
                s = results.get(key, {}).get('win_' + w)
                if not s:
                    continue
                def fmt_cell(m):
                    v = s.get(m)
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        return '--'
                    if m == 'sharpe':
                        return f'{v:.2f}'
                    return pc(v)
                cells = ''.join(f'<td>{fmt_cell(m)}</td>' for m, _ in col_metrics)
                rows_out.append(
                    f'<tr><td>{CNAME[key]}</td><td>{w}</td><td>{s["start"]}~{s["end"]}</td>'
                    f'<td>{s["years"]:.1f}</td>{cells}'
                    f'<td>{s["total"]*100:.1f}%</td><td>{s["end_value"]:.1f}</td></tr>')
        return rows_out

    detail_rows = gen_table('detail')
    detail_rows_html = '\n'.join(detail_rows)

    # ---- 三张独立 10/20/30 对比表（仅看永久组合两线 + 单资产对照，快速浏览）----
    def small_table(w):
        head = ('<tr><th>组合</th><th>年化收益</th><th>夏普</th>'
                '<th>最大回撤</th><th>总收益</th></tr>')
        body = ''
        for key in ['PP_US', 'PP_CN', 'SP500_ALONE', 'HS300_ALONE']:
            s = results.get(key, {}).get('win_' + w)
            if not s:
                continue
            body += (f'<tr><td>{CNAME[key]}</td>'
                     f'<td>{pc(s["annual"])}</td><td>{s["sharpe"]}</td>'
                     f'<td>{pc(s["mdd"])}</td><td>{s["total"]*100:.1f}%</td></tr>')
        return f'<table>{head}{body}</table>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>永久组合 · 最终版逻辑 长历史指数拟合报告（10/20/30 年）</title>
<style>
  body {{ font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; margin: 24px auto;
         max-width: 1100px; color: #222; line-height: 1.7; }}
  h1 {{ color: #1a3e60; border-bottom: 3px solid #1a3e60; padding-bottom: 8px; }}
  h2 {{ color: #1a3e60; margin-top: 38px; border-left: 6px solid #1a3e60;
        padding-left: 10px; }}
  h3 {{ color: #333; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 6px 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 9px; text-align: right; }}
  th {{ background: #1a3e60; color: #fff; }}
  td:first-child, th:first-child, td:nth-child(2), th:nth-child(2) {{ text-align: left; }}
  tr:nth-child(even) {{ background: #f5f8fb; }}
  .chart {{ margin: 26px 0; text-align: center; }}
  .chart img {{ max-width: 100%; border: 1px solid #e3e3e3; border-radius: 6px; }}
  .note {{ background: #fff8e6; border-left: 4px solid #f0ad4e; padding: 10px 14px;
          font-size: 13px; }}
  .card {{ background: #f0f6fb; border-left: 5px solid #1a3e60; border-radius: 6px;
          padding: 8px 14px; margin: 8px 0; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
           gap: 14px; margin: 18px 0; }}
  .kpi-box {{ background: linear-gradient(135deg,#eef4fb,#dbe9f7); border-radius: 10px;
             padding: 16px 18px; border: 1px solid #cfe0f0; }}
  .kpi-box .v {{ font-size: 27px; font-weight: 800; color: #1a3e60; }}
  .kpi-box .s {{ font-size: 18px; color: #8a2b2b; font-weight: 700; }}
  .kpi-box .l {{ font-size: 13px; color: #555; margin-top: 4px; }}
  .win-tabs {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .win-tabs > div {{ flex: 1; min-width: 260px; }}
  ul.tight li {{ margin: 5px 0; }}
</style></head><body>
<h1>永久组合 · 最终版逻辑 长历史指数拟合报告</h1>
<p>生成日期：{dt.date.today().isoformat()} ｜ 依据：最终策略设计（cn_rec 主推 + 年频 6% 阈值 <b>部分再平衡</b>）<br>
再平衡模型与策略实现一致：年度检查、任一腿绝对偏离 &gt; 6% 触发、只调偏离超阈值资产（先卖超配再买低配）。本报告以四资产 25/25/25/25 长历史指数验证策略长期画像。</p>

<div class="cards">
  <div class="kpi-box"><div class="v">{pc(k1)}</div><div class="s">最大回撤 {pc(k1d)}</div>
      <div class="l">PP_US（美股线）30 年 年化收益</div></div>
  <div class="kpi-box"><div class="v">{pc(k2)}</div><div class="s">最大回撤 {pc(k2d)}</div>
      <div class="l">PP_CN（A 股线）20 年 年化收益</div></div>
  <div class="kpi-box"><div class="v">{pc(k3)}</div><div class="s">最大回撤 {pc(k3d)}</div>
      <div class="l">S&amp;P500 30 年（单资产对照）</div></div>
  <div class="kpi-box"><div class="v">{pc(k4q)}</div><div class="s">最大回撤 {pc(k4qd)}</div>
      <div class="l">沪深300 20 年（单资产对照）</div></div>
</div>

<h2>一、数据拟合曲线（净值走势）</h2>
<p><b>如何读图</b>：净值曲线以窗口起点=100，纵轴为对数刻度（同一垂直间距代表的涨幅一致）。曲线越陡越高代表长期收益越好；回撤体现为曲线从高点滑落的幅度。拟合方式为逐日价格 × 目标份额，越权即年度再平衡，先卖高再买低。</p>
{imgs}

<h2>二、关键指标对比表</h2>
<p>完整明细（组合 × 窗口）：</p>
<table>
<tr><th>组合</th><th>窗口</th><th>区间</th><th>年数</th><th>年化</th><th>波动(月频)</th>
<th>夏普</th><th>最大回撤</th><th>总收益</th><th>期末净值</th></tr>
{detail_rows_html}
</table>

<h3>10 / 20 / 30 年核心对比（永久组合两线 vs 单资产）</h3>
<div class="win-tabs">
  <div><h3>10 年</h3>{small_table('10Y')}</div>
  <div><h3>20 年</h3>{small_table('20Y')}</div>
  <div><h3>30 年</h3>{small_table('30Y')}</div>
</div>

<h2>三、关键指标详解</h2>
<div class="card"><b>年化收益（Annual Return）</b>：将窗口期总收益按持有年数折算为“每年复合增长率”，即 CAGR = (期末净值/期初净值)^(1/年数) − 1。它剔除了持有期长短差异，便于不同窗口横向比较。</div>
<div class="card"><b>夏普比率（Sharpe）</b>：每承担 1 单位波动所获得的超额收益，= 年化收益 − 无风险利率(2%)，再除以年化波动率。大于 1 代表“风险调整后回报较好”；永久组合目标中枢为 0.5~1.0。</div>
<div class="card"><b>最大回撤（Max Drawdown）</b>：任意时点净值从历史最高点到次低点的最大跌幅，衡量最坏情况下的账面亏损，也即组合“撑得住的底线”。永久组合的设计目标是把回撤压到 −15% 以内。</div>
<div class="card"><b>年化波动率（Volatility）</b>：月度收益的年化标准差，衡量净值起伏程度。本报告统一用月末净值计算，口径一致可比（月频量化，未真实包含日内波动）。</div>

<h2>四、趋势分析（基于拟合结果）</h2>
<ul class="tight">
<li><b>跨周期稳定性</b>：PP_US 在 10/15/20/30 年窗内年化落在 5.3%~7.0%，PP_CN 落在 4.0%~7.0%，均没有出现“短窗口暴涨、长窗口塌方”的背离——说明永久组合“配置+纪律”带来的回报在时间上是收敛且可预期的，符合 6-9% 长期中枢。</li>
<li><b>回撤大幅收窄</b>：S&amp;P500 30 年单资产最大回撤 −{pc(k3d)}，组合内压到 −{pc(k1d)}；沪深300 20 年单资产回撤 −{pc(k4qd)}，组合内压到 −{pc(k2d)}。四类低相关资产互相对冲是回撤收窄的核心来源。</li>
<li><b>股/债/金/货币轮动</b>：通胀期黄金与商品贡献、衰退通缩期长债贡献、繁荣期股票贡献、危机期货币缓冲——四类资产天然错峰，再平衡“卖高买低”等于被动地在轮动中兑现波动收益（均值回归收益）。</li>
<li><b>美股线 vs A 股线</b>：美股线（全收益标普+美债+现金+黄金）整体风险收益占优（30Y 夏普约 0.5 vs A 股 20Y 约 0.3-0.5），主因美股长期慢牛 + 数据含股息全收益；A 股线为价格指数不含股息，实际全收益更高约 2%/年。</li>
<li><b>对实盘的启示</b>：真实 ETF 回测（cn_rec，2023-06~2026-04）年化高达 14%+，远高于长历史中枢——该区间恰逢 30 年国债大牛市与黄金上行，属于顺风局；长期应按 6-9% 预期，切勿把短期区间运气当长期常态。</li>
</ul>

<h2>五、数据源与近似假设</h2>
<table>
<tr><th>资产</th><th>数据源</th><th>起点</th></tr>
<tr><td>标普 500（含股息全收益）</td><td>Shiller 数据集</td><td>1871</td></tr>
<tr><td>伦敦金月均价 / COMEX 黄金</td><td>datasets gold-prices · FRED（月度）</td><td>1833 / 长历史</td></tr>
<tr><td>美债 10Y</td><td>FRED DGS10（久期≈9 合成价格）</td><td>1965</td></tr>
<tr><td>美债 3M 现金</td><td>FRED TB3MS（月利率按日摊）</td><td>1965</td></tr>
<tr><td>沪深300 / 中证500 / 国债指数</td><td>新浪指数日频</td><td>2002 / 2005 / 2003</td></tr>
<tr><td>现金（A 股线）</td><td>固定年化 2.2% 近似</td><td>—</td></tr>
</table>
<div class="note">
<b>请务必知悉的近似：</b>
1) 未计任何费用（无手续费/滑点/税收）；2) A 股为价格指数未含股息（全收益会更高约 2%/年）；
3) S&amp;P500 为 Shiller 全收益（含股息），口径与 A 股不对等；4) 美股线标普/黄金为月度数据按日均摊，
<b>抹平日内波动 → 波动率低于真实组合</b>（收益/夏普基本不受影响，仅波动与回撤统计偏乐观）；
5) A 股线最长 20 年（受伦敦金 2006-09 起限制），30 年仅美股线可得。
</div>
<div class="note" style="background:#eef6ff;border-left-color:#1a3e60;">
<b>最终版逻辑说明</b>：本报告与最终设计与策略实现（<code>yongjiu_zuhe_strategy.py</code>）采用同一再平衡模型——
年频检查、6% 绝对偏离阈值触发、<code>partial_rebalance</code> 只调越界资产。指数层面为四资产 25/25/25/25 拟合；
真实 ETF 落地见 <a href="../../最终策略设计.md">最终策略设计.md</a>（cn_rec 7 只 ETF）。
</div>

<p style="margin-top:30px;color:#888;font-size:12px">数据与脚本：
optimization_results/index_fit_results.json · run_index_fit_backtest.py · index_fit_data/</p>
</body></html>"""
    out = os.path.join(OUT_DIR, 'index_fit_report.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    main()