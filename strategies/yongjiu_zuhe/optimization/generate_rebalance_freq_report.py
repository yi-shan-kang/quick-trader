# -*- coding: utf-8 -*-
"""再平衡频率对比可视化报告生成器

复用 run_rebalance_freq_test 的 NAV 计算与结果 JSON，输出：
  1. freq_nav_lines.png   关键窗口 NAV 对比（PP_US 30Y / PP_CN 20Y × 年/季/月）
  2. freq_annual_bars.png 年化收益 分组合×窗口 分组柱状图
  3. freq_risk_bars.png   年化波动 + 最大回撤 双面板
  4. freq_reb_counts.png  再平衡触发次数对比
输出 HTML 报告（内嵌 base64 图）与 PNG 到 charts/。
"""
import base64
import datetime as dt
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import run_index_fit_backtest as rb
import run_rebalance_freq_test as rf

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 110

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'optimization_results')
CHART_DIR = os.path.join(OUT_DIR, 'charts')
os.makedirs(CHART_DIR, exist_ok=True)

CAD_NAME = {'annual': '年频', 'quarterly': '季频', 'monthly': '月频'}
CAD_COLOR = {'annual': '#2E86AB', 'quarterly': '#F18F01', 'monthly': '#C73E1D'}
CNAME = {
    'PP_CN': 'PP_CN (A股线)',
    'PP_CN_SZ': 'PP_CN_SZ (A股+中盘)',
    'PP_US': 'PP_US (美股线)',
}
WINDOWS = [('30Y', 30), ('20Y', 20), ('15Y', 15), ('10Y', 10)]

PY = os.path.dirname(os.path.abspath(__file__))


def save_fig(fig, fname):
    fp = os.path.join(CHART_DIR, fname)
    fig.savefig(fp, bbox_inches='tight')
    plt.close(fig)
    return fp


def b64(path):
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode('ascii')


# ---------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------
def load_results():
    fp = os.path.join(PY, 'optimization_results/rebalance_freq_comparison.json')
    with open(fp, encoding='utf-8') as f:
        return json.load(f)


def navs_for(key, label_years):
    """返回 {cadence: nav}。复用 run_cadence，起点与频率测试脚本口径一致"""
    weights = rb.PP_WEIGHTS if key != 'PP_CN_SZ' else rb.PP_CN_SZ_WEIGHTS
    notes = rf.CACHED[key]
    starts = []
    for name, src, kw in rb.COMBOS[key]:
        if src == 'cash':
            continue
        starts.append(pd.read_csv(os.path.join(rb.DATA_DIR, kw['file']),
                                  parse_dates=['date'])['date'].iloc[0])
    latest_start = max(starts)
    end = rf.END
    win_start = max(latest_start, end - pd.DateOffset(years=label_years))
    out = {}
    for cadence in rf.CADENCES:
        nav, _ = rf.run_nav_cadence(notes, weights, win_start, end, cadence)
        if nav is not None:
            out[cadence] = nav
    return out


# ---------------------------------------------------------------
# 图表
# ---------------------------------------------------------------
def chart_nav_lines(pairs):
    """pairs: [(title, {cadence: nav}), ...]"""
    n = len(pairs)
    fig, axes = plt.subplots(1, n, figsize=(6.2 * n, 5.2))
    if n == 1:
        axes = [axes]
    for ax, (title, navs) in zip(axes, pairs):
        for cadence, nav in navs.items():
            ax.plot(nav.index, nav.values, lw=1.6, label=CAD_NAME[cadence],
                    color=CAD_COLOR[cadence])
        ax.set_yscale('log')
        ax.set_title(f'{title} 净值（对数，起点=100）')
        ax.grid(True, which='both', alpha=0.3)
        ax.legend(fontsize=9)
    fig.suptitle('再平衡频率 × 净值轨迹', fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig


def chart_annual_by_cadence(piv_by_combo):
    combos = list(piv_by_combo.keys())
    fig, axes = plt.subplots(1, len(combos), figsize=(6.2 * len(combos), 5.2))
    if len(combos) == 1:
        axes = [axes]
    for ax, combo in zip(axes, combos):
        df = piv_by_combo[combo]['annual']
        wins = df.columns.tolist()
        x = np.arange(len(wins))
        width = 0.24
        for i, cad in enumerate(df.index):
            vals = df.loc[cad].values * 100
            ax.bar(x + (i - 1) * width, vals, width, color=CAD_COLOR[cad],
                   label=CAD_NAME[cad])
            for xi, v in zip(x + (i - 1) * width, vals):
                ax.text(xi, v + 0.12, f'{v:.2f}', ha='center', fontsize=7.5)
        ax.set_xticks(x)
        ax.set_xticklabels(wins)
        ax.set_title(f'{CNAME[combo]} 年化收益')
        ax.axhline(0, color='k', lw=0.8)
        ax.grid(axis='y', alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle('再平衡频率 × 年化收益（月频口径）', fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    return fig


def chart_risk_by_cadence(piv_by_combo):
    combos = list(piv_by_combo.keys())
    fig, axes = plt.subplots(len(combos), 2, figsize=(12, 4.3 * len(combos)))
    for i, combo in enumerate(combos):
        wins = piv_by_combo[combo]['annual'].columns.tolist()
        x = np.arange(len(wins))
        width = 0.24
        for j, metric in enumerate(['vol', 'mdd']):
            df = piv_by_combo[combo][metric]
            ax = axes[i, j] if len(combos) > 1 else axes[j]
            for k, cad in enumerate(df.index):
                vals = df.loc[cad].values * 100
                ax.bar(x + (k - 1) * width, vals, width, color=CAD_COLOR[cad],
                       label=CAD_NAME[cad], alpha=0.9)
                for xi, v in zip(x + (k - 1) * width, vals):
                    if not np.isnan(v):
                        ax.text(xi, v + (0.15 if v >= 0 else -0.55), f'{v:.1f}',
                                ha='center', fontsize=7)
            ax.axhline(0, color='k', lw=0.8)
            ax.set_xticks(x)
            ax.set_xticklabels(wins)
            ax.set_title(f'{CNAME[combo]}  {"年化波动率" if metric=="vol" else "最大回撤"}',
                         fontsize=10)
            ax.grid(axis='y', alpha=0.3)
            if i == 0:
                ax.legend(fontsize=8)
    fig.suptitle('再平衡频率 × 风险指标', fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


def chart_reb_counts(piv_by_combo):
    combos = list(piv_by_combo.keys())
    labels = []
    flat = []
    for combo in combos:
        for w in piv_by_combo[combo]['annual'].columns:
            labels.append(f'{combo} {w}')
            flat.append((combo, w))
    fig, ax = plt.subplots(figsize=(11, 5.2))
    x = np.arange(len(labels))
    width = 0.24
    for k, cad in enumerate(['annual', 'quarterly', 'monthly']):
        vals = [piv_by_combo[c]['reb_count'].loc[cad, w] for c, w in flat]
        ax.bar(x + (k - 1) * width, vals, width, color=CAD_COLOR[cad],
               label=CAD_NAME[cad])
        for xi, v in zip(x + (k - 1) * width, vals):
            ax.text(xi, v + 0.2, f'{v:.0f}', ha='center', fontsize=7.5)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.set_ylabel('触发调仓次数')
    ax.set_title('再平衡频率 × 实际触发次数（阈值 6%）')
    ax.grid(axis='y', alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------
def main():
    results = load_results()

    # 每个 combo: {metric: DataFrame(cadence × window)}，metric ∈ annual/vol/mdd/reb_count
    piv_by_combo = {}
    for combo in ['PP_CN', 'PP_CN_SZ', 'PP_US']:
        raw = {}
        for cadence in rf.CADENCES:
            d = {}
            for label in ['10Y', '15Y', '20Y', '30Y']:
                st = results.get(combo, {}).get(label, {}).get(cadence)
                if st:
                    d[label] = st
            raw[cadence] = d
        wins = [w for w in ['30Y', '20Y', '15Y', '10Y'] if w in raw['annual']]
        piv_by_combo[combo] = {
            m: pd.DataFrame({c: {w: raw[c][w][m] for w in wins if w in raw[c]}
                             for c in rf.CADENCES}).T[wins]
            for m in ['annual', 'vol', 'mdd', 'reb_count']
        }

    figs = []
    # 1) NAV 对比：PP_US 30Y、PP_CN 20Y、PP_CN_SZ 20Y
    pairs = []
    for combo, yr, label in [('PP_US', 30, 'PP_US 30Y'), ('PP_CN', 20, 'PP_CN 20Y'),
                             ('PP_CN_SZ', 20, 'PP_CN_SZ 20Y')]:
        navs = navs_for(combo, yr)
        if navs:
            pairs.append((label, navs))
    f = save_fig(chart_nav_lines(pairs), 'freq_nav_lines.png')
    figs.append(('nav', '净值轨迹对比（年/季/月）', f))

    # 2) 年化收益
    f = save_fig(chart_annual_by_cadence(piv_by_combo), 'freq_annual_bars.png')
    figs.append(('annual', '年化收益对比', f))

    # 3) 风险双面板
    f = save_fig(chart_risk_by_cadence(piv_by_combo), 'freq_risk_bars.png')
    figs.append(('risk', '波动/回撤对比', f))

    # 4) 触发次数
    f = save_fig(chart_reb_counts(piv_by_combo), 'freq_reb_counts.png')
    figs.append(('reb', '再平衡触发次数', f))

    build_html(figs, results, piv_by_combo)
    print('report saved to', os.path.join(OUT_DIR, 'rebalance_freq_report.html'))


def build_html(figs, results, piv_by_combo):
    imgs = ''.join(
        f'<div class="chart"><h3>{t}</h3><img src="data:image/png;base64,{b64(p)}" '
        f'alt="{k}"/></div>' for k, t, p in figs)

    rows = []
    for combo in ['PP_CN', 'PP_CN_SZ', 'PP_US']:
        for label in ['10Y', '15Y', '20Y', '30Y']:
            for cadence in rf.CADENCES:
                st = results.get(combo, {}).get(label, {}).get(cadence)
                if not st:
                    continue
                rows.append(
                    f'<tr><td>{CNAME[combo]}</td><td>{label}</td><td>{CAD_NAME[cadence]}</td>'
                    f'<td>{st["annual"]*100:.2f}%</td><td>{st["vol"]*100:.2f}%</td>'
                    f'<td>{st["sharpe"]}</td><td>{st["mdd"]*100:.2f}%</td>'
                    f'<td>{st["reb_count"]}</td></tr>')
    table = '\n'.join(rows)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>永久组合 · 再平衡频率对比报告（年/季/月）</title>
<style>
  body {{ font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; margin: 24px auto;
         max-width: 1100px; color: #222; line-height: 1.6; }}
  h1 {{ color: #1a3e60; border-bottom: 3px solid #1a3e60; padding-bottom: 8px; }}
  h2 {{ color: #1a3e60; margin-top: 36px; }}
  h3 {{ color: #333; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 9px; text-align: right; }}
  th {{ background: #1a3e60; color: #fff; }}
  td:first-child, th:first-child, td:nth-child(2), th:nth-child(2),
  td:nth-child(3), th:nth-child(3) {{ text-align: left; }}
  tr:nth-child(even) {{ background: #f5f8fb; }}
  .chart {{ margin: 26px 0; text-align: center; }}
  .chart img {{ max-width: 100%; border: 1px solid #e3e3e3; border-radius: 6px; }}
  .note {{ background: #fff8e6; border-left: 4px solid #f0ad4e; padding: 10px 14px;
          font-size: 13px; }}
  .good {{ color: #1a7f37; font-weight: 600; }}
  .kpi {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 18px 0; }}
  .kpi-box {{ flex: 1; min-width: 190px; background: #f0f6fb; border-radius: 8px;
             padding: 14px 18px; }}
  .kpi-box .v {{ font-size: 24px; font-weight: 700; color: #1a3e60; }}
  .kpi-box .l {{ font-size: 13px; color: #555; }}
</style></head><body>
<h1>永久组合 · 再平衡频率对比报告（年 / 季 / 月）</h1>
<p>生成日期：{dt.date.today().isoformat()} ｜ 数据范围：长历史指数拟合（10 / 15 / 20 / 30 年）<br>
口径：阈值固定 6%（偏离才触发，非固定日历再平衡）；未计交易费用；检查口径与正式回测器一致。</p>

<div class="kpi">
  <div class="kpi-box"><div class="v">7.42%</div><div class="l">PP_US 30Y 年频年化（最优）</div></div>
  <div class="kpi-box"><div class="v">7.17%</div><div class="l">PP_US 30Y 月频年化（略降）</div></div>
  <div class="kpi-box"><div class="v">12→17</div><div class="l">PP_US 30Y 触发次数（年→月）</div></div>
  <div class="kpi-box"><div class="v">7.78%</div><div class="l">PP_CN_SZ 20Y 年频年化（最优）</div></div>
</div>

<h2>一、净值轨迹（年 / 季 / 月）</h2>
{imgs}

<h2>二、统计明细表</h2>
<table>
<tr><th>组合</th><th>窗口</th><th>频率</th><th>年化</th><th>波动(月频)</th>
<th>夏普</th><th>最大回撤</th><th>触发次数</th></tr>
{table}
</table>

<h2>三、核心结论</h2>
<ul>
<li><b>频率提升不创造收益，反而略降</b>：美股线 30Y 年频 7.42% → 季频 7.27% → 月频 7.17%；
A股线 20Y 亦为年频最优（7.23% / 7.78% vs 月频 6.64% / 7.26%）。长牛区间更频繁的再平衡 = 更早
卖出趋势资产。</li>
<li><b>6% 阈值下频率意义有限</b>：美股线 30 年，季频仅比年频多触发 3 次、月频仅多 5 次——资产
漂移到 6% 很慢，提高检查频率大多「空转」。</li>
<li><b>季频是 A 股线的稳健折中</b>：PP_CN 15Y 季频最优（4.58%）且波动/回撤普遍收窄 0.5-2pct；
但 20Y 维度年频更好。</li>
<li><b>结论</b>：维持<b>年度再平衡（默认）</b>即可；若求更平滑可改季频，月频无必要且实盘换手成本更高。</li>
</ul>

<h2>四、口径与近似</h2>
<div class="note">
1) 未计费用：频率差异 = 纯再平衡时机差异；真实 ETF 实盘月频会叠加交易成本。2) 波动/回撤为月频口径；
美股线标普/黄金为月度数据均摊，波动率（~5%）低于真实值（~8-9%）。3) A 股线最长 20Y（伦敦金 2006-09 起），
美股线 30Y 完整。4) 触发次数为该窗口内实际超 6% 阈值调仓次数，非检查次数。
</div>

<p style="margin-top:30px;color:#888;font-size:12px">数据与脚本：
optimization_results/rebalance_freq_comparison.json · run_rebalance_freq_test.py · generate_rebalance_freq_report.py</p>
</body></html>"""
    out = os.path.join(OUT_DIR, 'rebalance_freq_report.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    main()