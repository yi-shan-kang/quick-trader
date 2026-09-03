# -*- coding: utf-8 -*-
"""七星高照ETF轮动策略 - 2025年度专业回测分析

从回测记录读取净值/基准曲线与交易明细，计算月度收益，
生成净值曲线图、回撤走势图、月度收益柱状图。
"""
import os
import sys
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# 中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_2025')
os.makedirs(OUT_DIR, exist_ok=True)

RUN_ID = '20260903_164212_qixing_gaozhao'


def load_record(run_id):
    idx = json.load(open('backtest_results/index.json', encoding='utf-8'))
    recs = idx if isinstance(idx, list) else idx.get('records', [])
    entry = [r for r in recs if r['run_id'] == run_id][0]
    with open(os.path.join('backtest_results', entry['file']), encoding='utf-8') as f:
        return json.load(f)


def main():
    rec = load_record(RUN_ID)
    meta = rec['meta']
    metrics = rec['metrics']
    eq = pd.DataFrame(rec['equity_curve'])
    bench = pd.DataFrame(rec['benchmark_curve']) if rec.get('benchmark_curve') else None
    trades = rec['trade_log']

    # ---- 净值序列 ----
    eq['date'] = pd.to_datetime(eq['date'] if 'date' in eq else eq['datetime'])
    eq = eq.sort_values('date').set_index('date')
    # 净值列：portfolio_value（date 已移入 index，勿用 columns[1] 以免取到 pnl）
    nav_col = next((c for c in ('portfolio_value', 'value', 'net_value', 'nav') if c in eq.columns), eq.columns[0])
    nav = eq[nav_col].astype(float)
    # 归一化净值
    nav0 = nav / nav.iloc[0]

    # ---- 回撤 ----
    running_max = nav0.cummax()
    drawdown = nav0 / running_max - 1

    # ---- 月度收益 ----
    monthly = nav0.resample('ME').last().pct_change().dropna() * 100
    # 首月从月初开始，单独处理
    first_close = nav0.resample('ME').last()
    first_open = nav0.resample('ME').first()
    monthly_full = (first_close / first_open - 1) * 100
    monthly_full = monthly_full.dropna()

    # ---- 基准月度 ----
    bench_monthly = None
    if bench is not None and not bench.empty and len(bench) > 2:
        bench = bench.copy()
        bench['date'] = pd.to_datetime(bench.iloc[:, 0])
        bench = bench.set_index('date').sort_index()
        bcol = bench.columns[0]
        bnav = bench[bcol].astype(float).dropna()
        if len(bnav) > 2 and bnav.iloc[0] > 0:
            bnav0 = bnav / bnav.iloc[0]
            bench_monthly = (bnav0.resample('ME').last() / bnav0.resample('ME').first() - 1) * 100

    # ============ 图1: 净值曲线 ============
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    ax.plot(nav0.index, nav0.values, color='#c0392b', linewidth=1.8, label='七星高照策略净值')
    ax.axhline(1.0, color='gray', linestyle='--', linewidth=0.8)
    ax.set_title('七星高照ETF轮动策略 2025年度净值曲线', fontsize=15, fontweight='bold')
    ax.set_xlabel('日期')
    ax.set_ylabel('净值（初始=1.0）')
    ax.legend(loc='upper left', fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, '01_净值曲线.png'))
    plt.close(fig)

    # ============ 图2: 回撤曲线 ============
    fig, ax = plt.subplots(figsize=(12, 5), dpi=150)
    ax.fill_between(drawdown.index, drawdown.values * 100, 0, color='#e74c3c', alpha=0.45, label='回撤')
    ax.set_title('七星高照ETF轮动策略 2025年度回撤走势', fontsize=15, fontweight='bold')
    ax.set_xlabel('日期')
    ax.set_ylabel('回撤（%）')
    ax.legend(loc='lower left', fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, '02_回撤走势.png'))
    plt.close(fig)

    # ============ 图3: 月度收益柱状图 ============
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    m_labels = [d.strftime('%Y-%m') for d in monthly_full.index]
    colors = ['#c0392b' if v >= 0 else '#27ae60' for v in monthly_full.values]
    ax.bar(m_labels, monthly_full.values, color=colors, alpha=0.85, label='策略月度收益')
    if bench_monthly is not None and len(bench_monthly) == len(monthly_full):
        ax.plot(m_labels, bench_monthly.values,
                color='#2980b9', marker='o', markersize=4, linewidth=1.4,
                label='沪深300月度收益（参考）')
    ax.axhline(0, color='black', linewidth=0.8)
    ax.set_title('七星高照ETF轮动策略 2025年度月度收益', fontsize=15, fontweight='bold')
    ax.set_xlabel('月份')
    ax.set_ylabel('月度收益率（%）')
    ax.legend(loc='upper left', fontsize=11)
    ax.grid(True, axis='y', alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=45)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, '03_月度收益.png'))
    plt.close(fig)

    # ============ 输出统计 ============
    print('=' * 70)
    print('  七星高照ETF轮动策略 2025年度专业回测分析')
    print('=' * 70)
    print(f'  回测区间: {meta["timestamp"][:10] and "2025-01-01 ~ 2025-12-31"}')
    print(f'  初始资金: {metrics["initial_capital"]:,.2f}')
    print(f'  最终资金: {metrics["final_value"]:,.2f}')
    print(f'  累计收益率: {metrics["total_return_pct"]:.2f}%')
    print(f'  年化收益率: {metrics["annual_return_pct"]:.2f}%')
    print(f'  夏普比率: {metrics["sharpe_ratio"]:.4f}')
    print(f'  最大回撤: {metrics["max_drawdown_pct"]:.2f}%')
    print(f'  总交易天数: {metrics["total_trading_days"]}')
    print(f'  交易笔数: {len(trades)}')
    print(f'  盈利天数: {metrics["win_days"]}, 亏损天数: {metrics["loss_days"]}')
    print(f'  总手续费: {metrics["fee"]:.2f}')
    print(f'  换手率: {metrics["turnover"]}')
    print(f'  总成交量: {metrics["total_volume"]}')
    print()
    print('  月度收益明细:')
    for m, v in monthly_full.items():
        print(f'    {m.strftime("%Y-%m")}: {v:+.2f}%')
    print()
    print(f'  正收益月数: {(monthly_full > 0).sum()} / {len(monthly_full)}')
    print(f'  胜率(月): {(monthly_full > 0).mean() * 100:.1f}%')
    print(f'  图表已保存至: {OUT_DIR}')

    # 保存月度明细
    with open(os.path.join(OUT_DIR, 'monthly_returns.json'), 'w', encoding='utf-8') as f:
        json.dump({'monthly': {k.strftime('%Y-%m'): round(float(v), 2) for k, v in monthly_full.items()}},
                  f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
