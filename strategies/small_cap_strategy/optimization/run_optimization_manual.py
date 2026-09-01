# -*- coding: utf-8 -*-
"""小市值策略优化 - 按《提示词_优化版.md》口径批量回测

统一口径（提示词）：
- 调仓频率：月度（默认）
- 标的池：中证全指历史成分股（000985，含退市股）
- 选股：总市值升序取最小20只，等权
- 区间：2015-01-01 ~ 2025-12-31
- 基线夏普：1.29（实测）

用法：
    python run_optimization_manual.py 0          # 只跑基线
    python run_optimization_manual.py 1          # 只跑优化1
    python run_optimization_manual.py all        # 全部跑（含基线+10项+组合）
"""
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

os.environ['QMT_LOG_LEVEL'] = 'WARNING'

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config

POOL = '中证全指'
START = '2015-01-01'
END = '2025-12-31'
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'optimization_results')
os.makedirs(RESULTS_DIR, exist_ok=True)

# 10 项优化方案
OPTIMIZATIONS = {
    'baseline': ('基线', None),
    'opt01_volatility': ('波动率过滤 vol=0.04', {'max_volatility': 0.04}),
    'opt02_stop_loss': ('止损 stop_loss=0.08', {'stop_loss_pct': 0.08}),
    'opt03_momentum': ('动量过滤 mom>=0', {'min_momentum': 0.0}),
    'opt04_roe': ('ROE过滤 roe>=0.05', {'min_roe': 0.05}),
    'opt05_maxcap': ('市值上限 30亿', {'max_market_cap': 30}),
    'opt06_biweekly': ('双周调仓', {'rebalance_freq': 'biweekly'}),
    'opt07_skip6': ('空仓1,4,6月', {'skip_months': (1, 4, 6)}),
    'opt08_industry': ('行业分散 每行业<=3', {'industry_limit': 3}),
    'opt09_vol_stop': ('波动率+止损组合', {'max_volatility': 0.04, 'stop_loss_pct': 0.08}),
    'opt10_stocks30': ('持仓30只', {'max_stocks': 30}),
    # 组合测试：有效项 opt05 + 回撤改善项 opt07 / opt10
    'combo_maxcap_skip6': ('市值上限30亿+空仓1,4,6月', {'max_market_cap': 30, 'skip_months': (1, 4, 6)}),
    'combo_maxcap_stocks30': ('市值上限30亿+持仓30只', {'max_market_cap': 30, 'max_stocks': 30}),
    # 样本外验证：优化前参数(20只,无上限) vs 优化后参数(30只+上限30亿) 在验证集表现
    'oos_baseline': ('样本外基线(旧参数20只)', {'max_market_cap': None, 'max_stocks': 20}),
    'oos_combo': ('样本外组合(新参数)', {'max_market_cap': 30, 'max_stocks': 30}),
    # 参数敏感性：max_market_cap / max_stocks ±10% ±20%
    'sens_cap24': ('市值上限24亿', {'max_market_cap': 24, 'max_stocks': 30}),
    'sens_cap27': ('市值上限27亿', {'max_market_cap': 27, 'max_stocks': 30}),
    'sens_cap33': ('市值上限33亿', {'max_market_cap': 33, 'max_stocks': 30}),
    'sens_cap36': ('市值上限36亿', {'max_market_cap': 36, 'max_stocks': 30}),
    'sens_stk24': ('持仓24只', {'max_market_cap': 30, 'max_stocks': 24}),
    'sens_stk27': ('持仓27只', {'max_market_cap': 30, 'max_stocks': 27}),
    'sens_stk33': ('持仓33只', {'max_market_cap': 30, 'max_stocks': 33}),
    'sens_stk36': ('持仓36只', {'max_market_cap': 30, 'max_stocks': 36}),
}

# 验证集区间（样本外）
OOS_START = '2024-04-28'
OOS_END = '2026-04-28'
OOS_LABELS = {'oos_baseline', 'oos_combo'}


def run_backtest(label, extra_params=None, start=None, end=None):
    strategy_class = get_strategy('small_cap')
    default_kwargs = get_strategy_default_kwargs('small_cap')
    backtest_config = get_strategy_backtest_config('small_cap')

    config = dict(backtest_config)
    config['period'] = '1d'
    config['start_date'] = start or START
    config['end_date'] = end or END
    config.setdefault('benchmark', '000985.SH')

    merged_kwargs = dict(default_kwargs)
    if extra_params:
        merged_kwargs.update(extra_params)

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
        metrics['initial_capital'] = acc.initial_capital
        metrics['final_value'] = acc.dynamic_rights
        metrics['total_return_pct'] = acc.rate * 100
        metrics['sharpe_ratio'] = sr
        metrics['max_drawdown_pct'] = dd * 100
        if result.df is not None and len(result.df) > 0:
            days = len(result.df)
            years = days / 252
            annual_ret = (1 + acc.rate) ** (1 / years) - 1 if years > 0 else 0
            if isinstance(annual_ret, complex):
                annual_ret = annual_ret.real
            metrics['annual_return_pct'] = float(annual_ret) * 100
            metrics['trading_days'] = days
        metrics['label'] = label
        metrics['extra_params'] = extra_params
    else:
        metrics['label'] = label
        metrics['error'] = 'No result'

    with open(os.path.join(RESULTS_DIR, f'{label}.json'), 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f'[{label}] 夏普={metrics.get("sharpe_ratio")} 年化={metrics.get("annual_return_pct")} '
          f'回撤={metrics.get("max_drawdown_pct")} 总收益={metrics.get("total_return_pct")}')
    sys.stdout.flush()
    return metrics


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if which == 'all':
        labels = list(OPTIMIZATIONS.keys())
    else:
        labels = [which]

    baseline_sharpe = None
    if 'baseline' in labels:
        b = run_backtest('baseline', None)
        baseline_sharpe = b.get('sharpe_ratio')
        labels = [l for l in labels if l != 'baseline']

    # 先跑基线再跑优化（若基线没跑则加载已有结果）
    if baseline_sharpe is None:
        base_file = os.path.join(RESULTS_DIR, 'baseline.json')
        if os.path.exists(base_file):
            with open(base_file, 'r', encoding='utf-8') as f:
                baseline_sharpe = json.load(f).get('sharpe_ratio')

    for label in labels:
        name, params = OPTIMIZATIONS[label]
        # 样本外验证用验证集区间
        if label in OOS_LABELS:
            m = run_backtest(label, params, start=OOS_START, end=OOS_END)
        else:
            m = run_backtest(label, params)
        if baseline_sharpe and m.get('sharpe_ratio') is not None:
            imp = (m['sharpe_ratio'] - baseline_sharpe) / abs(baseline_sharpe) * 100
            verdict = '✅保留' if imp >= 5 else '❌放弃'
            print(f'  → {name}: 夏普变化 {imp:+.1f}% [{verdict}]')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
