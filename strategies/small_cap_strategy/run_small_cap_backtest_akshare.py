# -*- coding: utf-8 -*-
"""小市值策略（small_cap）回测脚本 - 使用 akshare 还原历史成分股

- 历史成分股：akshare（OpenDataProcessor）按纳入/剔除日期还原中证1000历史成分股，
  避免幸存者偏差（不用当前成分股）
- 行情/财务：QMT 本地缓存（离线可读）
- 回测区间：近 1 年（2025-04-01 ~ 2026-04-17）
"""
import os
import sys
import json
import traceback

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
# 项目根目录加入路径（脚本位于 strategies/small_cap_strategy/ 下）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config
from core.data.index_constituent import IndexConstituentManager
from core.data.opendata import OpenDataProcessor

START_DATE = '2025-04-01'
END_DATE = '2026-04-17'
SECTOR = '中证1000'


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
    print('  小市值策略（small_cap）回测 - akshare 历史成分股')
    print(f'  回测区间: {START_DATE} ~ {END_DATE}')
    print(f'  股票池: {SECTOR} 历史成分股 (akshare还原)')
    print(f'  初始资金: {config.get("cash")}')
    print('=' * 60)
    sys.stdout.flush()

    # 用 akshare 还原回测起始日的历史成分股（避免幸存者偏差）
    opendata = OpenDataProcessor(fallback_to_simulated=False)
    stock_list = opendata.get_historical_stock_list(SECTOR, START_DATE)
    print(f'akshare 还原 {SECTOR} {START_DATE} 历史成分股: {len(stock_list)} 只')
    if not stock_list:
        print('历史成分股还原失败，无法回测')
        return
    sys.stdout.flush()

    api = BacktestAPI(data_source='open')
    api.set_ai_mode(True)
    api.set_no_record(True)
    api.configure(**config)
    print('  [debug] 开始加载财务数据...')
    sys.stdout.flush()

    # 传入 akshare 还原的历史成分股 + sector（用于基准与动态成分股设置）
    api.load_financial_data(stock_list=stock_list, sector=SECTOR)
    print('  [debug] 财务数据加载完成')
    sys.stdout.flush()

    api.add_stock_selection_strategy(strategy_class, stock_pool=stock_list, **default_kwargs)
    print('  [debug] 选股策略添加完成，开始回测')
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
            'constituent_source': 'akshare',
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
        print('  回测结果（akshare 历史成分股，近1年）')
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
    else:
        print('回测无结果')
    sys.stdout.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'回测失败: {e}')
        traceback.print_exc()
