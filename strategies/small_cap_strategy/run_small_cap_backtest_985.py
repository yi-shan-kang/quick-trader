# -*- coding: utf-8 -*-
"""小市值策略（small_cap）回测 - 中证全指(000985)历史成分股

- 股票池：中证全指（000985.SH）历史成分股（本地 CSV，完整历史，含退市股）
- 行情：QMT 本地缓存（后复权/不复权）
- 财务：QMT 本地缓存（仅 Balance + Pershareindex 两张表）
- 回测区间：默认 2015-01-01 ~ 2025-12-31，可用命令行参数覆盖
"""
import os
import sys
import json
import traceback

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
# 项目根目录加入路径（脚本位于 strategies/small_cap_strategy/ 下）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import pandas as pd

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config
from core.data.index_constituent import IndexConstituentManager

# 支持命令行参数指定回测区间: python run_small_cap_backtest_985.py [start_date] [end_date]
START_DATE = sys.argv[1] if len(sys.argv) > 1 else '2015-01-01'
END_DATE = sys.argv[2] if len(sys.argv) > 2 else '2025-12-31'
SECTOR = '中证全指'


def print_annual_breakdown(result):
    """输出每年度策略表现明细"""
    df = result.df
    if df is None or df.empty or 'PortfolioValue' not in df.columns:
        return
    d = df.copy()
    # 日期在 'datetime' 列（而非 index），需显式提取
    if 'datetime' in d.columns:
        d['datetime'] = pd.to_datetime(d['datetime'])
        d['year'] = d['datetime'].dt.year
    elif isinstance(d.index, pd.DatetimeIndex):
        d['year'] = d.index.year
    else:
        d['year'] = pd.to_datetime(d.index).year

    print('\n' + '=' * 70)
    print('  每年度策略表现明细')
    print('=' * 70)
    print(f'  {"年份":<6} {"年末净值":>14} {"年度收益率":>12} {"年度最大回撤":>12}')
    print('  ' + '-' * 60)

    rows = []
    for year, grp in d.groupby('year'):
        start_val = grp['PortfolioValue'].iloc[0]
        end_val = grp['PortfolioValue'].iloc[-1]
        yret = end_val / start_val - 1
        # 年度最大回撤
        equity = grp['PortfolioValue'].values
        peak = pd.Series(equity).cummax().values
        dd = ((equity - peak) / peak).min()
        rows.append({'year': year, 'end_val': end_val, 'yret': yret, 'dd': dd})
        print(f'  {year:<6} {end_val:>14,.2f} {yret * 100:>11.2f}% {dd * 100:>11.2f}%')

    print('  ' + '-' * 60)
    return rows


def print_rebalance_log(result):
    """输出调仓记录（每次调仓日的持仓明细）"""
    trade_log = result.trade_log
    if not trade_log:
        print('\n  [提示] 无交易记录')
        return

    # 提取买入记录，按日期分组
    buys = {}
    for t in trade_log:
        direction = str(getattr(t, 'direction', ''))
        if direction == '0':  # 买入
            dt = getattr(t, 'trade_time', None)
            if dt is None:
                continue
            if hasattr(dt, 'strftime'):
                day = dt.strftime('%Y-%m-%d')
            else:
                day = str(dt)[:10]
            symbol = getattr(t, 'instrument_id', '')
            buys.setdefault(day, []).append(symbol)

    if not buys:
        print('\n  [提示] 无买入记录')
        return

    print('\n' + '=' * 70)
    print('  调仓记录（月度买入）')
    print('=' * 70)
    for day in sorted(buys.keys()):
        symbols = sorted(set(buys[day]))
        print(f'  {day}: 买入 {len(symbols)} 只 -> {" ".join(symbols)}')
    print(f'  （共 {len(buys)} 次调仓）')


def main():
    strategy_name = 'small_cap'
    strategy_class = get_strategy(strategy_name)
    default_kwargs = get_strategy_default_kwargs(strategy_name)
    backtest_config = get_strategy_backtest_config(strategy_name)

    config = dict(backtest_config)
    config['period'] = '1d'
    config['start_date'] = START_DATE
    config['end_date'] = END_DATE
    benchmark = IndexConstituentManager.SECTOR_TO_INDEX.get(SECTOR, '000300.SH')
    config.setdefault('benchmark', benchmark)

    print('=' * 60)
    print('  小市值策略（small_cap）回测 - 中证全指历史成分股')
    print(f'  回测区间: {START_DATE} ~ {END_DATE}')
    print(f'  股票池: {SECTOR} ({benchmark}) 历史成分股')
    print(f'  初始资金: {config.get("cash")}')
    print('=' * 60)
    sys.stdout.flush()

    api = BacktestAPI()
    api.set_ai_mode(True)
    api.set_no_record(True)
    api.configure(**config)

    print('  [debug] 加载财务数据...')
    sys.stdout.flush()
    # 小市值策略仅依赖 Balance.total_equity 与 Pershareindex.s_fa_bps 两张表，
    # 只加载这两张表可避免10年区间触发海量无关财务数据下载
    api.load_financial_data(sector=SECTOR, table_list=['Balance', 'Pershareindex'])
    print('  [debug] 财务数据加载完成')
    sys.stdout.flush()

    api.add_stock_selection_strategy(strategy_class, **default_kwargs)
    print('  [debug] 策略添加完成，开始回测')
    sys.stdout.flush()

    api.run()
    print('  [debug] 回测完成')
    sys.stdout.flush()

    result = api.get_result()
    if result:
        sr = result.sharpe_ratio()
        dd = result.max_drawdown()
        acc = result.account
        metrics = {
            'strategy': strategy_name,
            'constituent_source': '中证全指历史成分股CSV',
            'start_date': START_DATE,
            'end_date': END_DATE,
            'initial_capital': acc.initial_capital,
            'final_value': acc.dynamic_rights,
            'total_return_pct': acc.rate * 100,
            'sharpe_ratio': sr,
            'max_drawdown_pct': dd * 100,
        }
        if result.df is not None and len(result.df) > 0:
            days = len(result.df)
            years = days / 252
            annual_ret = (1 + acc.rate) ** (1 / years) - 1 if years > 0 else 0
            metrics['annual_return_pct'] = annual_ret * 100
            metrics['trading_days'] = days

        print('\n' + '=' * 60)
        print('  回测结果（中证全指历史成分股）')
        print('=' * 60)
        print(f'  初始资金:   {metrics["initial_capital"]:,.2f}')
        print(f'  最终资金:   {metrics["final_value"]:,.2f}')
        print(f'  总收益率:   {metrics["total_return_pct"]:.2f}%')
        print(f'  年化收益率: {metrics.get("annual_return_pct", 0):.2f}%')
        print(f'  夏普比率:   {metrics["sharpe_ratio"]:.4f}')
        print(f'  最大回撤:   {metrics["max_drawdown_pct"]:.2f}%')
        if 'trading_days' in metrics:
            print(f'  交易日数:   {metrics["trading_days"]}')
        print(json.dumps(metrics, ensure_ascii=False, indent=2))

        # 每年度策略表现明细
        print_annual_breakdown(result)
        # 调仓记录（月度持仓明细）
        print_rebalance_log(result)
    else:
        print('回测无结果')
    sys.stdout.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'回测失败: {e}')
        traceback.print_exc()
