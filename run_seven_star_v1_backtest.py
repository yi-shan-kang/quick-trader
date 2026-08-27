# -*- coding: utf-8 -*-
"""七星高照 - 聚宽原版参数2年回测对比

覆盖参数: holdings_num=1 (单只满仓), lookback_days=24, slippage=0.001
回测窗口: 2024-01-01 ~ 2025-12-31 (最近2年)
"""
import sys
import os
import json
import pickle
import datetime

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from api.backtest_api import BacktestAPI
from strategies.seven_star_etf_strategy.seven_star_etf_strategy import SevenStarETFRotationStrategy

TRADE_START = '2024-01-01'
TRADE_END = '2025-12-31'
LOOKBACK = 150  # 数据前移天数


def load_data():
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'DATA', 'cache', 'etf_data_cache_qmt_10y.pkl')
    with open(cache_path, 'rb') as f:
        return pickle.load(f)


def run_one(data_dict, holdings, lookback, label):
    api = BacktestAPI(data_source='open')
    api.configure(
        cash=1000000, commission=0.0002,
        open_commission=0.0002, close_commission=0.0002,
        close_tax=0.0, min_commission=5.0, slippage=0.001,
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

    api.add_strategy(SevenStarETFRotationStrategy, holdings_num=holdings, lookback_days=lookback)
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
    loss_trades = [t for t in sell_trades if t.get('pnl', 0) <= 0]
    win_rate = len(win_trades) / max(len(sell_trades), 1)

    m = {
        'label': label,
        'holdings_num': holdings,
        'lookback_days': lookback,
        'final_value': round(final_value, 2),
        'total_return': round(total_return, 4),
        'annual_return': round(annual_return, 4),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_drawdown, 4),
        'calmar': round(annual_return / abs(max_drawdown), 3) if max_drawdown != 0 else 0,
        'trades': len(sell_trades),
        'win_rate': round(win_rate, 4),
    }
    print(f"[{label}] 总收益: {m['total_return']:.2%} | 年化: {m['annual_return']:.2%} | "
          f"夏普: {m['sharpe']:.2f} | 回撤: {m['max_drawdown']:.2%} | "
          f"胜率: {m['win_rate']:.2%} | 交易: {m['trades']}", flush=True)
    return api, result, m


def main():
    data_dict = load_data()
    print(f'数据加载完成: {len(data_dict)} 只ETF', flush=True)

    results = []

    # 1. 聚宽原版参数: 1只/24天
    _, _, m1 = run_one(data_dict, 1, 24, '聚宽原版参数(1只/24天)')
    results.append(m1)

    # 2. 当前优化版参数: 5只/60天 (对照)
    _, _, m2 = run_one(data_dict, 5, 60, '优化版参数(5只/60天)')
    results.append(m2)

    # 3. 单只/60天 (隔离持仓数影响)
    _, _, m3 = run_one(data_dict, 1, 60, '单只/60天')
    results.append(m3)

    # 4. 5只/24天 (隔离周期影响)
    _, _, m4 = run_one(data_dict, 5, 24, '5只/24天')
    results.append(m4)

    print('\n' + '=' * 100)
    print('2年回测对比 (2024-01-01 ~ 2025-12-31, 滑点千1)')
    print('=' * 100)
    header = f"{'配置':>22} | {'总收益':>8} | {'年化':>8} | {'夏普':>6} | {'回撤':>8} | {'卡尔马':>7} | {'交易':>5} | {'胜率':>7}"
    print(header)
    print('-' * 100)
    for m in results:
        print(f"{m['label']:>22} | {m['total_return']:>7.2%} | {m['annual_return']:>7.2%} | "
              f"{m['sharpe']:>6.2f} | {m['max_drawdown']:>7.2%} | {m['calmar']:>7.2f} | "
              f"{m['trades']:>5} | {m['win_rate']:>6.2%}")

    out_path = os.path.join('reports', 'seven_star_v1_vs_opt.json')
    os.makedirs('reports', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {out_path}')


if __name__ == '__main__':
    main()
