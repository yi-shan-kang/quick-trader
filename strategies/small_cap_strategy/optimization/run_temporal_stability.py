# -*- coding: utf-8 -*-
"""时间稳定性测试：按年分段对比 基线(旧参数20只) vs 组合(新参数30只+上限30亿)"""
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

os.environ['QMT_LOG_LEVEL'] = 'WARNING'

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config

POOL = '中证全指'
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'optimization_results')

YEARS = [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

BASE_PARAMS = {'max_market_cap': None, 'max_stocks': 20}
COMBO_PARAMS = {'max_market_cap': 30, 'max_stocks': 30}


def run_year(year, params, label):
    strategy_class = get_strategy('small_cap')
    default_kwargs = get_strategy_default_kwargs('small_cap')
    backtest_config = get_strategy_backtest_config('small_cap')

    config = dict(backtest_config)
    config['period'] = '1d'
    config['start_date'] = f'{year}-01-01'
    config['end_date'] = f'{year}-12-31'
    config.setdefault('benchmark', '000985.SH')

    merged_kwargs = dict(default_kwargs)
    merged_kwargs.update(params)

    api = BacktestAPI()
    api.set_ai_mode(True)
    api.set_no_record(True)
    api.configure(**config)
    api.load_financial_data(sector=POOL, table_list=['Balance', 'Pershareindex'])
    api.add_stock_selection_strategy(strategy_class, **merged_kwargs)
    api.run()

    result = api.get_result()
    metrics = {}
    if result:
        sr = result.sharpe_ratio()
        dd = result.max_drawdown()
        acc = result.account
        metrics['sharpe_ratio'] = sr
        metrics['total_return_pct'] = acc.rate * 100
        metrics['max_drawdown_pct'] = dd * 100
        if result.df is not None and len(result.df) > 0:
            days = len(result.df)
            years = days / 252
            annual_ret = (1 + acc.rate) ** (1 / years) - 1 if years > 0 else 0
            if isinstance(annual_ret, complex):
                annual_ret = annual_ret.real
            metrics['annual_return_pct'] = float(annual_ret) * 100
    else:
        metrics['error'] = 'No result'

    with open(os.path.join(RESULTS_DIR, f'temporal_{label}_{year}.json'), 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f'[{label} {year}] 夏普={metrics.get("sharpe_ratio"):.3f} 总收益={metrics.get("total_return_pct"):.1f}% 回撤={metrics.get("max_drawdown_pct"):.1f}%')
    sys.stdout.flush()
    return metrics


def main():
    rows = []
    for year in YEARS:
        base = run_year(year, BASE_PARAMS, 'base')
        combo = run_year(year, COMBO_PARAMS, 'combo')
        diff = combo.get('sharpe_ratio', 0) - base.get('sharpe_ratio', 0)
        rows.append({'year': year, 'base_sharpe': base.get('sharpe_ratio', 0),
                     'combo_sharpe': combo.get('sharpe_ratio', 0),
                     'improvement': diff, 'is_positive': diff > 0})
        print(f'  → {year}: 组合夏普改善 {diff:+.3f} {"✅" if diff > 0 else "❌"}')
        sys.stdout.flush()

    pos = sum(1 for r in rows if r['is_positive'])
    consistency = pos / len(rows)
    print(f'\n时间稳定性: {pos}/{len(rows)} 年正改进, 一致性比率 = {consistency:.2f}')
    with open(os.path.join(RESULTS_DIR, 'temporal_summary.json'), 'w', encoding='utf-8') as f:
        json.dump({'consistency_ratio': consistency, 'rows': rows}, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
