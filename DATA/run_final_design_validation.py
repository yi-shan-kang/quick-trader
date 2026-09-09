# -*- coding: utf-8 -*-
"""cn_rec 最终设计决胜回测

在 cn_rec 真实可回测区间（2023-06-13 ~ 2026-04-28）内，对候选再平衡机制决胜：
  检查周期: year / quarter / month
  触发规则: threshold(6%绝对偏离+partial) / band25(相对±25%触发,全额回归)

指标含成交笔数（trade_count 含初始建仓），用于评估换手成本。
"""
import os
import sys
import json

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config

STRATEGY = 'yongjiu_zuhe'
VERSION = 'cn_rec'
START = '2023-06-13'
END = '2026-04-28'

# (周期, 规则, 说明)
RUNS = [
    ('year',    'threshold', '年检 + 6%阈值 partial（现状推荐）'),
    ('quarter', 'threshold', '季检 + 6%阈值 partial'),
    ('month',   'threshold', '月检 + 6%阈值 partial'),
    ('year',    'band25',    '年检 + 相对±25%触发 全额回归'),
    ('quarter', 'band25',    '季检 + 相对±25%触发 全额回归'),
    ('month',   'band25',    '月检 + 相对±25%触发 全额回归（v525）'),
]


def run_one(interval, rule):
    strategy_class = get_strategy(STRATEGY)
    default_kwargs = dict(get_strategy_default_kwargs(STRATEGY))
    backtest_config = dict(get_strategy_backtest_config(STRATEGY))
    default_kwargs.update({'version': VERSION, 'rebalance_interval': interval,
                           'rebalance_rule': rule})
    backtest_config.update({'period': '1d', 'start_date': START, 'end_date': END})

    api = BacktestAPI()
    api.set_strategy_name(STRATEGY)
    api.set_no_record(False)
    api.set_ai_mode(True)
    api.configure(**backtest_config)
    api.add_strategy(strategy_class, **default_kwargs)
    engine_result = api.run()

    result = api.get_result()
    if not result:
        return None
    sr = result.sharpe_ratio()
    dd = result.max_drawdown()
    acc = result.account
    m = {
        'version': VERSION,
        'interval': interval,
        'rule': rule,
        'start': START, 'end': END,
        'days': len(result.df) if result.df is not None else 0,
        'total_return_pct': round(acc.rate * 100, 2),
        'sharpe': round(sr, 3),
        'max_dd_pct': round(dd * 100, 2),
    }
    if engine_result is not None:
        m['trade_count'] = len(getattr(engine_result, 'trade_records', []) or [])
    else:
        m['trade_count'] = None
    if result.df is not None and len(result.df) > 0:
        years = len(result.df) / 252
        m['annual_return_pct'] = round(((1 + acc.rate) ** (1 / years) - 1) * 100, 2)
    return m


def main():
    rows = []
    for interval, rule, note in RUNS:
        print(f'==> cn_rec {interval} {rule} ({note})', flush=True)
        try:
            m = run_one(interval, rule)
            if m is None:
                print('    <无结果>', flush=True)
                continue
            rows.append(m)
            print(f'    {m}', flush=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'    FAIL {type(e).__name__}: {e}', flush=True)
        sys.stdout.flush()

    print('\n' + '=' * 100)
    print('  cn_rec 最终设计决胜（真实 ETF 2023-06-13 ~ 2026-04-28）')
    print('=' * 100)
    hdr = f"{'周期':<5}{'规则':<10}{'交易日':<7}{'总收益%':<9}{'年化%':<9}{'夏普':<7}{'最大回撤%':<9}{'成交笔数':<8}"
    print(hdr)
    print('-' * 100)
    for m in rows:
        tc = m.get('trade_count')
        tc_s = str(tc) if tc is not None else '-'
        print(f"{m['interval']:<5}{m['rule']:<10}{m['days']:<7}"
              f"{m['total_return_pct']:<9}{str(m.get('annual_return_pct')):<9}"
              f"{m['sharpe']:<7}{m['max_dd_pct']:<9}{tc_s:<8}")
    out = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'yongjiu_zuhe',
                       'optimization', 'optimization_results', 'final_design_validation.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f'\nsaved: {out}')


if __name__ == '__main__':
    main()
