# -*- coding: utf-8 -*-
"""七星高照ETF轮动策略 - 半衰期敏感性测试

遍历 half_life ∈ [8, 9, 10, 11, 12, 13, 14, 15]，使用本地 QMT 10年数据
分别执行回测，对比各半衰期下的绩效指标，评估权重参数鲁棒性。
"""
import sys
import os
import json
import time
import pickle
import datetime
import logging

import numpy as np
import pandas as pd

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.backtest_api import BacktestAPI
from strategies.seven_star_etf_strategy.seven_star_etf_strategy import SevenStarETFRotationStrategy

DATA_START = '2014-08-01'
DATA_END = '2025-12-31'
TRADE_START = '2015-01-01'
TRADE_END = '2025-12-31'
HALF_LIVES = [8, 9, 10, 11, 12, 13, 14, 15]


def load_data():
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'DATA', 'cache', 'etf_data_cache_qmt_10y.pkl')
    with open(cache_path, 'rb') as f:
        return pickle.load(f)


def run_one(data_dict, half_life):
    api = BacktestAPI(data_source='open')
    api.configure(
        cash=1000000, commission=0.0002,
        open_commission=0.0002, close_commission=0.0002,
        close_tax=0.0, min_commission=5.0, slippage=0.001,
        start_date=TRADE_START, end_date=TRADE_END,
        data_lookback_days=150, period='1d', benchmark='000300.SH',
    )
    for symbol, df in data_dict.items():
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        if not all(c in df.columns for c in required_cols):
            continue
        ohlcv = df[required_cols].copy()
        ohlcv.index = pd.to_datetime(ohlcv.index)
        api._engine.add_data(symbol, ohlcv)
        api._symbols.append(symbol)
        api._data_cache[symbol] = ohlcv

    api.add_strategy(SevenStarETFRotationStrategy, half_life=half_life)
    result = api.run()
    return api, result


def calc_metrics(api, result):
    equity_history = result.equity_history
    trade_records = result.trade_records
    initial_cash = result.initial_cash
    final_value = result.final_value

    equity_df = pd.DataFrame(equity_history, columns=['date', 'value'])
    equity_df['date'] = pd.to_datetime(equity_df['date'])
    equity_df = equity_df.set_index('date')

    total_return = (final_value - initial_cash) / initial_cash
    days = len(equity_df)
    annual_return = (1 + total_return) ** (250 / max(days, 1)) - 1
    daily_returns = equity_df['value'].pct_change().dropna()
    sharpe = (daily_returns.mean() - 0.02 / 250) / daily_returns.std() * np.sqrt(250) \
        if len(daily_returns) > 1 and daily_returns.std() > 0 else 0.0
    cummax = equity_df['value'].cummax()
    drawdown = (equity_df['value'] - cummax) / cummax
    max_drawdown = drawdown.min()

    sell_trades = [t for t in trade_records if t.get('direction') == 'sell']
    win_trades = [t for t in sell_trades if t.get('pnl', 0) > 0]
    loss_trades = [t for t in sell_trades if t.get('pnl', 0) <= 0]
    win_rate = len(win_trades) / max(len(sell_trades), 1)
    avg_win = float(np.mean([t['pnl'] for t in win_trades])) if win_trades else 0
    avg_loss = abs(float(np.mean([t['pnl'] for t in loss_trades]))) if loss_trades else 0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        'half_life': None,  # 外部填充
        'final_value': round(final_value, 2),
        'total_return': round(total_return, 4),
        'annual_return': round(annual_return, 4),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_drawdown, 4),
        'calmar': round(annual_return / abs(max_drawdown), 3) if max_drawdown != 0 else 0,
        'trades': len(sell_trades),
        'win_rate': round(win_rate, 4),
        'profit_loss_ratio': round(profit_loss_ratio, 3),
    }


def main():
    data_dict = load_data()
    print(f'数据加载完成: {len(data_dict)} 只ETF', flush=True)

    results = []
    for hl in HALF_LIVES:
        print(f'\n=== half_life = {hl} ===', flush=True)
        t0 = time.time()
        try:
            api, result = run_one(data_dict, hl)
            m = calc_metrics(api, result)
            m['half_life'] = hl
            m['elapsed_s'] = round(time.time() - t0, 1)
            results.append(m)
            print(f"  总收益: {m['total_return']:.2%} | 年化: {m['annual_return']:.2%} | "
                  f"夏普: {m['sharpe']:.2f} | 回撤: {m['max_drawdown']:.2%} | "
                  f"胜率: {m['win_rate']:.2%} | 盈亏比: {m['profit_loss_ratio']:.2f} | "
                  f"耗时: {m['elapsed_s']}s", flush=True)
        except Exception as e:
            print(f"  FAIL: {e}", flush=True)
            results.append({'half_life': hl, 'error': str(e)})

    print('\n' + '=' * 80)
    print('半衰期敏感性测试结果汇总')
    print('=' * 80)
    header = f"{'HL':>4} | {'总收益':>8} | {'年化':>8} | {'夏普':>6} | {'回撤':>8} | {'卡尔马':>7} | {'交易':>5} | {'胜率':>7} | {'盈亏比':>6}"
    print(header)
    print('-' * 80)
    for m in results:
        if 'error' in m:
            print(f"{m['half_life']:>4} | ERROR: {m['error']}")
        else:
            print(f"{m['half_life']:>4} | {m['total_return']:>7.2%} | {m['annual_return']:>7.2%} | "
                  f"{m['sharpe']:>6.2f} | {m['max_drawdown']:>7.2%} | {m['calmar']:>7.2f} | "
                  f"{m['trades']:>5} | {m['win_rate']:>6.2%} | {m['profit_loss_ratio']:>6.2f}")

    out_path = os.path.join('reports', 'seven_star_half_life_sweep.json')
    os.makedirs('reports', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {out_path}')


if __name__ == '__main__':
    main()
