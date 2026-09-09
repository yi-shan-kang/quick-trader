# -*- coding: utf-8 -*-
"""永久组合策略回测脚本

用法:
    python run_yongjiu_zuhe_backtest.py [start_date] [end_date] [--version opt2] [--interval month] [--headless]

示例:
    python run_yongjiu_zuhe_backtest.py                    # 默认区间 + 默认版本 opt2 + 弹 GUI
    python run_yongjiu_zuhe_backtest.py 2020-04-28 2026-04-28 --version opt4
    python run_yongjiu_zuhe_backtest.py 2020-04-28 2026-04-28 --headless   # 无 GUI 仅 HTML
    python run_yongjiu_zuhe_backtest.py ... --version cn_rec --interval month

说明:
- 默认回测结束后自动弹出 PyQt5 图表窗口，关闭窗口后程序退出
- 结果自动记录并生成 Plotly HTML 报告（backtest_results/comparison/）
"""
import os
import sys
import json
import argparse
import traceback

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
# 项目根目录加入路径（脚本位于 strategies/yongjiu_zuhe/ 下）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config
from strategies.yongjiu_zuhe.config import VERSION_NAMES, DEFAULT_VERSION


def parse_args():
    parser = argparse.ArgumentParser(description='永久组合策略回测')
    parser.add_argument('dates', nargs='*', help='回测区间 [start_date] [end_date]')
    parser.add_argument('--version', default=DEFAULT_VERSION,
                        choices=list(VERSION_NAMES.keys()),
                        help=f'配置版本（默认 {DEFAULT_VERSION}）')
    parser.add_argument('--interval', default='year', choices=['year', 'quarter', 'month'],
                        help='再平衡周期（默认 year 年度）')
    parser.add_argument('--rule', default='threshold', choices=['threshold', 'band25'],
                        help='触发规则（默认 threshold 绝对偏离阈值；band25=5/25规则25分支）')
    parser.add_argument('--headless', action='store_true',
                        help='无 GUI 模式（服务器/CI 环境），仅打印结果与生成 HTML 报告')
    return parser.parse_args()


def main():
    args = parse_args()
    strategy_name = 'yongjiu_zuhe'

    if len(args.dates) >= 2:
        start_date, end_date = args.dates[0], args.dates[1]
    else:
        start_date, end_date = '2020-04-28', '2026-04-28'

    strategy_class = get_strategy(strategy_name)
    default_kwargs = get_strategy_default_kwargs(strategy_name)
    backtest_config = get_strategy_backtest_config(strategy_name)

    default_kwargs = dict(default_kwargs)
    default_kwargs['version'] = args.version
    default_kwargs['rebalance_interval'] = args.interval
    default_kwargs['rebalance_rule'] = args.rule

    config = dict(backtest_config)
    config['period'] = '1d'
    config['start_date'] = start_date
    config['end_date'] = end_date

    print('=' * 60)
    print('  永久组合策略回测')
    print(f'  版本: {args.version} ({VERSION_NAMES[args.version]})')
    print(f'  再平衡: {args.interval}')
    print(f'  触发规则: {args.rule}')
    print(f'  回测区间: {start_date} ~ {end_date}')
    print(f'  初始资金: {config.get("cash")}')
    print(f'  GUI 模式: {"关闭(--headless)" if args.headless else "开启（自动弹图）"}')
    print('=' * 60)
    sys.stdout.flush()

    api = BacktestAPI()
    api.set_strategy_name(strategy_name)
    api.set_no_record(False)
    if args.headless:
        api.set_ai_mode(True)
    api.configure(**config)
    api.add_strategy(strategy_class, **default_kwargs)

    print('  [debug] 开始回测...')
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
            'version': args.version,
            'rebalance_interval': args.interval,
            'start_date': start_date,
            'end_date': end_date,
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
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        sys.stdout.flush()

        # 生成 HTML 报告（基于自动记录的回测结果）
        _generate_html_report(strategy_name, metrics)

        # 弹出 GUI 图表窗口（headless 或缺少显示环境时跳过）
        if not args.headless:
            try:
                print('\n  [提示] 正在弹出图表窗口，关闭窗口后程序结束...')
                sys.stdout.flush()
                api.show_report()
            except Exception as e:
                print(f'\n  [警告] GUI 窗口弹出失败（{type(e).__name__}: {e}）')
                print('  请改用 main.py 查看图表，或加 --headless 仅生成 HTML 报告')
    else:
        print('回测无结果')
    sys.stdout.flush()


def _generate_html_report(strategy_name: str, metrics: dict):
    """基于自动记录的结果生成 Plotly HTML 报告"""
    try:
        from utils.backtest_recorder import BacktestRecorder

        recorder = BacktestRecorder()
        records = recorder.list_records(strategy_name=strategy_name)
        if not records:
            print('  [警告] 未找到回测记录，跳过 HTML 报告生成')
            return
        latest = records[-1]['run_id']
        output = recorder.generate_report([latest])
        if output:
            print(f'\n  [报告] HTML 报告已生成: {os.path.abspath(output)}')
            print('  可用浏览器打开查看（净值曲线/回撤/交易统计等）')
        else:
            print('  [警告] HTML 报告生成失败（无有效记录）')
    except Exception as e:
        print(f'  [警告] HTML 报告生成异常: {type(e).__name__}: {e}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'回测失败: {e}')
        traceback.print_exc()
