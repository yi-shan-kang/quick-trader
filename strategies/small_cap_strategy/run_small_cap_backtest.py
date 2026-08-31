# -*- coding: utf-8 -*-
"""小市值策略（small_cap）回测脚本

- 数据源：MiniQMT (xtquant)
- 股票池：中证1000（000852.SH）历史成分股
- 回测区间：2016-01-01 ~ 2026-04-17（策略默认配置）
- 财务数据：QMT 8 张财报表（Balance/Income/CashFlow/Capital/Pershareindex 等）
"""
import os
import sys
import json
import datetime
import traceback

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
# 项目根目录加入路径（脚本位于 strategies/small_cap_strategy/ 下）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config
from core.data.index_constituent import IndexConstituentManager


def main():
    strategy_name = 'small_cap'
    strategy_class = get_strategy(strategy_name)
    default_kwargs = get_strategy_default_kwargs(strategy_name)
    backtest_config = get_strategy_backtest_config(strategy_name)

    config = dict(backtest_config)
    config['period'] = '1d'
    benchmark = IndexConstituentManager.SECTOR_TO_INDEX.get('中证1000', '000300.SH')
    config.setdefault('benchmark', benchmark)

    print('=' * 60)
    print('  小市值策略（small_cap）回测')
    print(f'  回测区间: {config.get("start_date")} ~ {config.get("end_date")}')
    print(f'  股票池: 中证1000 ({benchmark})')
    print(f'  初始资金: {config.get("cash")}')
    print('=' * 60)
    sys.stdout.flush()

    api = BacktestAPI()
    api.set_ai_mode(True)
    api.configure(**config)

    # 下载/加载中证1000历史成分股的财务数据
    api.load_financial_data(sector='中证1000')

    # 添加选股策略（内部会加载股票池行情数据）
    api.add_stock_selection_strategy(strategy_class, **default_kwargs)

    api.run()

    result = api.get_result()
    if result:
        sr = result.sharpe_ratio()
        dd = result.max_drawdown()
        acc = result.account
        metrics = {
            'strategy': strategy_name,
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
        print('  回测结果')
        print('=' * 60)
        print(f'  初始资金:   {metrics["initial_capital"]:,.2f}')
        print(f'  最终资金:   {metrics["final_value"]:,.2f}')
        print(f'  总收益率:   {metrics["total_return_pct"]:.2f}%')
        print(f'  年化收益率: {metrics.get("annual_return_pct", 0):.2f}%')
        print(f'  夏普比率:   {metrics["sharpe_ratio"]:.4f}')
        print(f'  最大回撤:   {metrics["max_drawdown_pct"]:.2f}%')
        if 'trading_days' in metrics:
            print(f'  交易日数:   {metrics["trading_days"]}')

        out_file = os.path.join('reports', 'small_cap_backtest.json')
        os.makedirs('reports', exist_ok=True)
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        print(f'\n结果已保存: {out_file}')
    else:
        print('回测无结果')
    sys.stdout.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'回测失败: {e}')
        traceback.print_exc()
