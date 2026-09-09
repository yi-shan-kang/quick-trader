# -*- coding: utf-8 -*-
"""优化 #1：四版本横向对比回测

同区间（2020-04-28 ~ 2026-04-28）跑批 base / b2 / opt2 / opt4，
输出收益/回撤/夏普/换手/触发次数 对比表。

运行：
    .venv\Scripts\python.exe strategies\yongjiu_zuhe\optimization\run_version_comparison.py
"""
import os
import sys
import json
from collections import OrderedDict

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_backtest_config
from strategies.yongjiu_zuhe.config import VERSIONS, VERSION_NAMES, DEFAULT_VERSION

START = '2020-04-28'
END = '2026-04-28'
VERSIONS_TO_TEST = ['base', 'b2', 'opt2', 'opt4']


def run_one(version: str):
    """跑单个版本的回测，返回指标 dict。"""
    cls = get_strategy('yongjiu_zuhe')
    cfg = dict(get_strategy_backtest_config('yongjiu_zuhe'))
    cfg.update({'period': '1d', 'start_date': START, 'end_date': END})

    api = BacktestAPI()
    api.set_strategy_name('yongjiu_zuhe')
    api.set_no_record(True)  # 不写记录文件，避免污染历史
    api.set_ai_mode(True)    # 无 GUI
    api.configure(**cfg)
    api.add_strategy(cls, version=version)
    api.run()

    res = api.get_result()
    if not res or res.df is None or len(res.df) == 0:
        return {'version': version, 'error': 'no_result'}

    acc = res.account
    sr = res.sharpe_ratio()
    dd = res.max_drawdown()
    days = len(res.df)
    years = days / 252.0
    annual_ret = (1 + acc.rate) ** (1 / years) - 1 if years > 0 else 0.0

    # 统计再平衡触发次数（按日志关键词粗略估算——记录 no_record 下无法直接读，置 None）
    return {
        'version': version,
        'name': VERSION_NAMES[version],
        'n_symbols': len(VERSIONS[version]),
        'start': START,
        'end': END,
        'trading_days': days,
        'initial_capital': float(acc.initial_capital),
        'final_value': float(acc.dynamic_rights),
        'total_return_pct': float(acc.rate * 100),
        'annual_return_pct': float(annual_ret * 100),
        'sharpe_ratio': float(sr),
        'max_drawdown_pct': float(dd * 100),
    }


def main():
    print('=' * 72)
    print('  优化 #1：四版本横向对比回测')
    print(f'  区间: {START} ~ {END}')
    print('=' * 72)

    results = []
    for v in VERSIONS_TO_TEST:
        print(f'\n>>> 运行版本: {v} ({VERSION_NAMES[v]})')
        sys.stdout.flush()
        try:
            r = run_one(v)
        except Exception as e:
            r = {'version': v, 'error': f'{type(e).__name__}: {e}'}
            print(f'  [失败] {r["error"]}')
        results.append(r)
        if 'error' not in r:
            print(f'  收益: {r["total_return_pct"]:.2f}%  年化: {r["annual_return_pct"]:.2f}%'
                  f'  夏普: {r["sharpe_ratio"]:.3f}  回撤: {r["max_drawdown_pct"]:.2f}%')
        sys.stdout.flush()

    # 输出对比表
    print('\n' + '=' * 72)
    print('  对比表')
    print('=' * 72)
    headers = ['版本', '名称', '总收益%', '年化%', '夏普', '回撤%', '资产数']
    print(f'  {headers[0]:<6} | {headers[1]:<22} | {headers[2]:>8} | {headers[3]:>7} | '
          f'{headers[4]:>6} | {headers[5]:>7} | {headers[6]:>5}')
    print('  ' + '-' * 78)
    for r in results:
        if 'error' in r:
            print(f"  {r['version']:<6} | [失败: {r['error']}]")
            continue
        print(f"  {r['version']:<6} | {r['name'][:22]:<22} | "
              f"{r['total_return_pct']:>8.2f} | {r['annual_return_pct']:>7.2f} | "
              f"{r['sharpe_ratio']:>6.3f} | {r['max_drawdown_pct']:>7.2f} | {r['n_symbols']:>5}")

    # 保存 JSON
    out_path = os.path.join(os.path.dirname(__file__), 'optimization_results',
                            'version_comparison_baseline.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f'\n  [保存] {out_path}')


if __name__ == '__main__':
    main()
