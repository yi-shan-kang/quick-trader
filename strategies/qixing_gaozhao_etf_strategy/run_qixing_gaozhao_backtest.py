# -*- coding: utf-8 -*-
"""七星高照ETF轮动策略回测脚本

用法:
    python run_qixing_gaozhao_backtest.py [start_date] [end_date] [--headless]

示例:
    python run_qixing_gaozhao_backtest.py 2020-04-28 2026-04-28        # 弹出 GUI 图表 + 生成 HTML 报告
    python run_qixing_gaozhao_backtest.py 2020-04-28 2026-04-28 --headless  # 无 GUI（服务器/CI），仅生成 HTML 报告

说明:
- 默认回测结束后自动弹出 PyQt5 图表窗口（7 个标签页），关闭窗口后程序退出
- 无论是否弹出 GUI，回测结果都会自动记录到 backtest_results/ 并生成 Plotly HTML 报告
  （输出位置: backtest_results/comparison/<日期>_qixing_gaozhao_report.html）
"""
import os
import sys
import json
import argparse
import traceback

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
# 项目根目录加入路径（脚本位于 strategies/qixing_gaozhao_etf_strategy/ 下）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config


def parse_args():
    parser = argparse.ArgumentParser(description='七星高照ETF轮动策略回测')
    parser.add_argument('dates', nargs='*', help='回测区间 [start_date] [end_date]')
    parser.add_argument('--headless', action='store_true',
                        help='无 GUI 模式（服务器/CI 环境），仅打印结果与生成 HTML 报告')
    return parser.parse_args()


def main():
    args = parse_args()
    strategy_name = 'qixing_gaozhao'

    if len(args.dates) >= 2:
        start_date, end_date = args.dates[0], args.dates[1]
    else:
        start_date, end_date = '2020-04-28', '2026-04-28'

    strategy_class = get_strategy(strategy_name)
    default_kwargs = get_strategy_default_kwargs(strategy_name)
    backtest_config = get_strategy_backtest_config(strategy_name)

    config = dict(backtest_config)
    config['period'] = '1d'
    config['start_date'] = start_date
    config['end_date'] = end_date

    print('=' * 60)
    print('  七星高照ETF轮动策略（优化版）回测')
    print(f'  回测区间: {start_date} ~ {end_date}')
    print(f'  初始资金: {config.get("cash")}')
    print(f'  GUI 模式: {"关闭(--headless)" if args.headless else "开启（自动弹图）"}')
    print('=' * 60)
    sys.stdout.flush()

    api = BacktestAPI()
    # 设置策略名（决定回测记录的命名空间，main.py 同样调用）
    api.set_strategy_name(strategy_name)
    # 默认允许记录回测结果（生成 HTML 报告依赖记录数据）
    api.set_no_record(False)
    # 仅 headless 模式开启 ai_mode（跳过 GUI 渲染）
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
        # 取最新一次记录生成报告
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
