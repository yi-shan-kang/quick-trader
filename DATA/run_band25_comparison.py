# -*- coding: utf-8 -*-
"""5/25 规则（25 分支）再平衡对比驱动

验证 v525 逻辑：月频检查 + 单腿相对目标偏离 ±25% 触发 + 全额回归 25%。
与基线（年频 6% 阈值）、季度（季度 6% 阈值）对比。

四腿 25% 等权的经典永久组合用 base 版本（510300/511010/511880/518880），
四标的均 2020 年前上市，可回测 2020-04-28 起。
"""
import os
import sys
import json

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config

# (版本, 再平衡周期, 触发规则, 备注)
RUNS = [
    ('base', 'year',    'threshold', '基线·年频6%阈值'),
    ('base', 'quarter', 'threshold', '季度·季度6%阈值'),
    ('base', 'month',   'band25',    'v525·月频25分支'),
    ('base', 'month',   'threshold', '月频·6%阈值(对照)'),
]

STRATEGY = 'yongjiu_zuhe'
START = '2020-04-28'
END = '2026-04-28'


def run_one(version, interval, rule):
    strategy_class = get_strategy(STRATEGY)
    default_kwargs = dict(get_strategy_default_kwargs(STRATEGY))
    backtest_config = dict(get_strategy_backtest_config(STRATEGY))
    default_kwargs.update({'version': version, 'rebalance_interval': interval,
                           'rebalance_rule': rule})
    backtest_config.update({'period': '1d', 'start_date': START, 'end_date': END})

    api = BacktestAPI()
    api.set_strategy_name(STRATEGY)
    api.set_no_record(False)
    api.set_ai_mode(True)
    api.configure(**backtest_config)
    api.add_strategy(strategy_class, **default_kwargs)
    api.run()

    result = api.get_result()
    if not result:
        return None
    sr = result.sharpe_ratio()
    dd = result.max_drawdown()
    acc = result.account
    m = {
        'strategy': STRATEGY,
        'version': version,
        'interval': interval,
        'rule': rule,
        'start': START, 'end': END,
        'days': len(result.df) if result.df is not None else 0,
        'total_return_pct': round(acc.rate * 100, 2),
        'annual_return_pct': None,
        'sharpe': round(sr, 3),
        'max_dd_pct': round(dd * 100, 2),
    }
    if result.df is not None and len(result.df) > 0:
        years = len(result.df) / 252
        m['annual_return_pct'] = round(((1 + acc.rate) ** (1 / years) - 1) * 100, 2)
    return m


def main():
    rows = []
    for version, interval, rule, note in RUNS:
        print(f'==> {version} {interval} {rule} ({note})', flush=True)
        try:
            m = run_one(version, interval, rule)
            if m is None:
                print('    <无结果>', flush=True)
                continue
            rows.append(m)
            print(f'    {m}', flush=True)
        except Exception as e:
            print(f'    FAIL {type(e).__name__}: {e}', flush=True)
        sys.stdout.flush()

    print('\n' + '=' * 96)
    print('  5/25 规则（25 分支）再平衡对比（真实 ETF，离线缓存）')
    print('=' * 96)
    hdr = f"{'版本':<6}{'周期':<7}{'规则':<10}{'交易日':<7}{'总收益%':<9}{'年化%':<9}{'夏普':<7}{'最大回撤%':<9}"
    print(hdr)
    print('-' * 96)
    for m in rows:
        print(f"{m['version']:<6}{m['interval']:<7}{m['rule']:<10}{m['days']:<7}"
              f"{m['total_return_pct']:<9}{str(m['annual_return_pct']):<9}"
              f"{m['sharpe']:<7}{m['max_dd_pct']:<9}")
    out = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'yongjiu_zuhe',
                       'optimization', 'optimization_results', 'band25_vs_baseline.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\nsaved: {out}')


if __name__ == '__main__':
    main()
