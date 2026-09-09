# -*- coding: utf-8 -*-
"""cn_rec vs opt2/b2 实回测对比驱动

对指定版本/再平衡周期/区间逐个跑真实回测（QMT 离线，走本地缓存），
打印紧凑对比表，并自动记录到 backtest_results（与 CLI 一致，no_record=False）。
"""
import os
import sys
import json

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config
from strategies.yongjiu_zuhe.config import VERSION_NAMES

# (版本, 再平衡周期, 起始, 截止, 备注)
RUNS = [
    ('cn_rec', 'month', '2023-06-13', '2026-04-28', '主推·月频'),
    ('cn_rec', 'year',  '2023-06-13', '2026-04-28', '主推·年频'),
    ('opt2',   'month', '2023-06-13', '2026-04-28', '对比·月频'),
    ('b2',     'month', '2023-06-13', '2026-04-28', '对比·月频'),
    ('opt2',   'month', '2020-04-28', '2026-04-28', '参考·月频 2020起'),
    ('b2',     'month', '2020-04-28', '2026-04-28', '参考·月频 2020起'),
    ('opt2',   'year',  '2020-04-28', '2026-04-28', '参考·年频 2020起'),
    ('b2',     'year',  '2020-04-28', '2026-04-28', '参考·年频 2020起'),
]

STRATEGY = 'yongjiu_zuhe'


def run_one(version, interval, start, end):
    strategy_class = get_strategy(STRATEGY)
    default_kwargs = dict(get_strategy_default_kwargs(STRATEGY))
    backtest_config = dict(get_strategy_backtest_config(STRATEGY))
    default_kwargs.update({'version': version, 'rebalance_interval': interval})
    backtest_config.update({'period': '1d', 'start_date': start, 'end_date': end})

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
        'start': start, 'end': end,
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
    for version, interval, start, end, note in RUNS:
        print(f'==> {version} {interval} {start} ~ {end} ({note})', flush=True)
        try:
            m = run_one(version, interval, start, end)
            if m is None:
                print('    <无结果>', flush=True)
                continue
            rows.append(m)
            print(f"    {m}", flush=True)
        except Exception as e:
            print(f'    FAIL {type(e).__name__}: {e}', flush=True)
        sys.stdout.flush()

    print('\n' + '=' * 100)
    print('  对比结果汇总（真实ETF数据，离线缓存回测）')
    print('=' * 100)
    hdr = f"{'版本':<7}{'周期':<6}{'区间':<24}{'交易日':<7}{'总收益%':<9}{'年化%':<9}{'夏普':<7}{'最大回撤%':<9}"
    print(hdr)
    print('-' * 100)
    for m in rows:
        print(f"{m['version']:<7}{m['interval']:<6}{m['start']+'~'+m['end']:<24}"
              f"{m['days']:<7}{m['total_return_pct']:<9}{str(m['annual_return_pct']):<9}"
              f"{m['sharpe']:<7}{m['max_dd_pct']:<9}")
    # 输出 JSON 供后续引用
    out = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'yongjiu_zuhe',
                       'optimization', 'optimization_results', 'cn_rec_vs_opt2_b2.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\nsaved: {out}')


if __name__ == '__main__':
    main()