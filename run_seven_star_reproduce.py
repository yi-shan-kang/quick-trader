# -*- coding: utf-8 -*-
"""聚宽七星5.0 收益差异归因复现

逐步隔离变量，定位「2年7.7倍」与我们结果的差距来源:
  A. 聚宽全复刻: 1只/24天/线性权重/R²普通均值/滑点万1
  B. 仅滑点差异: 同上但滑点千1
  C. 仅权重差异: 1只/24天/指数衰减/R²普通均值/滑点千1
  D. 仅R²差异:   1只/24天/线性权重/R²加权均值/滑点千1
  E. 全改进版:   1只/24天/指数衰减/R²加权均值/滑点千1
回测窗口: 2024-01-01 ~ 2025-12-31
"""
import os
import sys
import json
import pickle

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from api.backtest_api import BacktestAPI
from strategies.seven_star_etf_strategy.seven_star_etf_strategy import SevenStarETFRotationStrategy

TRADE_START = '2024-01-01'
TRADE_END = '2025-12-31'
LOOKBACK = 150


def load_data():
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'DATA', 'cache', 'etf_data_cache_qmt_10y.pkl')
    with open(cache_path, 'rb') as f:
        return pickle.load(f)


def run_one(data_dict, label, slippage, weight_mode, r2_wmean):
    api = BacktestAPI(data_source='open')
    api.configure(
        cash=1000000, commission=0.0002,
        open_commission=0.0002, close_commission=0.0002,
        close_tax=0.0, min_commission=5.0, slippage=slippage,
        start_date=TRADE_START, end_date=TRADE_END,
        data_lookback_days=LOOKBACK, period='1d', benchmark='000300.SH',
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

    api.add_strategy(SevenStarETFRotationStrategy,
                     holdings_num=1, lookback_days=24,
                     weight_mode=weight_mode, r2_use_weighted_mean=r2_wmean)
    result = api.run()

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
    win_rate = len(win_trades) / max(len(sell_trades), 1)

    m = {
        'label': label,
        'slippage': slippage,
        'weight_mode': weight_mode,
        'r2_wmean': r2_wmean,
        'total_return': round(total_return, 4),
        'annual_return': round(annual_return, 4),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_drawdown, 4),
        'trades': len(sell_trades),
        'win_rate': round(win_rate, 4),
    }
    print(f"[{label}] 总收益: {m['total_return']:.2%} | 年化: {m['annual_return']:.2%} | "
          f"夏普: {m['sharpe']:.2f} | 回撤: {m['max_drawdown']:.2%} | 交易: {m['trades']}", flush=True)
    return m


def main():
    data_dict = load_data()
    print(f'数据加载完成: {len(data_dict)} 只ETF', flush=True)

    runs = [
        ('A聚宽全复刻(万1/线性/普通R²)', 0.0001, 'linear', False),
        ('B滑点千1(线性/普通R²)',       0.001,  'linear', False),
        ('C指数衰减(千1/普通R²)',       0.001,  'exp_decay', False),
        ('D加权R²(千1/线性)',           0.001,  'linear', True),
        ('E全改进(千1/指数/加权R²)',    0.001,  'exp_decay', True),
        ('F万1+线性+加权R²',            0.0001, 'linear', True),
    ]

    results = []
    for label, slip, wm, r2 in runs:
        try:
            m = run_one(data_dict, label, slip, wm, r2)
            results.append(m)
        except Exception as e:
            print(f'[{label}] FAIL: {e}', flush=True)

    print('\n' + '=' * 110)
    print('聚宽5.0 复现归因 (2024-01-01~2025-12-31, 1只/24天)')
    print('=' * 110)
    header = f"{'配置':>30} | {'滑点':>6} | {'权重':>9} | {'R²':>7} | {'总收益':>8} | {'年化':>8} | {'夏普':>6} | {'回撤':>8} | {'交易':>5}"
    print(header)
    print('-' * 110)
    for m in results:
        print(f"{m['label']:>30} | {m['slippage']:>6} | {m['weight_mode']:>9} | "
              f"{'加权' if m['r2_wmean'] else '普通':>7} | {m['total_return']:>7.2%} | "
              f"{m['annual_return']:>7.2%} | {m['sharpe']:>6.2f} | "
              f"{m['max_drawdown']:>7.2%} | {m['trades']:>5}")

    out_path = os.path.join('reports', 'seven_star_reproduce.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {out_path}')


if __name__ == '__main__':
    main()
