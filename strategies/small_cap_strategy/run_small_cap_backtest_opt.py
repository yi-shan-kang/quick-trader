# -*- coding: utf-8 -*-
"""小市值策略回测 - 按优化报告条件（可配置股票池与风控参数）

优化报告条件：
- 回测区间: 2020-04-28 ~ 2026-04-28
- 股票池: 中证1000（可用 --sector 切换）
- 调仓频率: 月度，等权
- 组合优化: max_volatility=0.04 + stop_loss_pct=0.08

用法：
    python run_small_cap_backtest_opt.py                     # 报告默认条件（中证1000 + 波动率+止损）
    python run_small_cap_backtest_opt.py --sector 中证全指   # 全指历史成分股对照
    python run_small_cap_backtest_opt.py --no-vol --no-stop  # 关闭两项风控（纯基线）
    python run_small_cap_backtest_opt.py --headless          # 无 GUI（服务器/CI），仅生成 HTML 报告
"""
import argparse
import json
import os
import sys
import traceback

os.environ['QMT_LOG_LEVEL'] = 'WARNING'
# 项目根目录加入路径（脚本位于 strategies/small_cap_strategy/ 下）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import pandas as pd

from api.backtest_api import BacktestAPI
from strategies import get_strategy, get_strategy_default_kwargs, get_strategy_backtest_config
from core.data.index_constituent import IndexConstituentManager


def print_annual_breakdown(result):
    df = result.df
    if df is None or df.empty or 'PortfolioValue' not in df.columns:
        return
    d = df.copy()
    if 'datetime' in d.columns:
        d['datetime'] = pd.to_datetime(d['datetime'])
        d['year'] = d['datetime'].dt.year
    elif isinstance(d.index, pd.DatetimeIndex):
        d['year'] = d.index.year
    else:
        d['year'] = pd.to_datetime(d.index).year

    print('\n' + '=' * 70)
    print('  每年度策略表现明细')
    print('=' * 70)
    print(f'  {"年份":<6} {"年末净值":>14} {"年度收益率":>12} {"年度最大回撤":>12}')
    print('  ' + '-' * 60)
    for year, grp in d.groupby('year'):
        start_val = grp['PortfolioValue'].iloc[0]
        end_val = grp['PortfolioValue'].iloc[-1]
        yret = end_val / start_val - 1
        equity = grp['PortfolioValue'].values
        peak = pd.Series(equity).cummax().values
        dd = ((equity - peak) / peak).min()
        print(f'  {year:<6} {end_val:>14,.2f} {yret * 100:>11.2f}% {dd * 100:>11.2f}%')


def main():
    parser = argparse.ArgumentParser(description='小市值策略回测（优化报告条件）')
    parser.add_argument('--sector', default='中证1000', help='股票池（中证1000/中证全指/中证500等）')
    parser.add_argument('--start', default='2020-04-28')
    parser.add_argument('--end', default='2026-04-28')
    parser.add_argument('--max-volatility', type=float, default=0.04, help='日波动率上限，None关闭')
    parser.add_argument('--stop-loss', type=float, default=0.08, help='止损阈值，None关闭')
    parser.add_argument('--label', default='opt11', help='输出label')
    parser.add_argument('--headless', action='store_true', help='无 GUI（服务器/CI），仅生成 HTML 报告')
    args = parser.parse_args()

    strategy_name = 'small_cap'
    strategy_class = get_strategy(strategy_name)
    default_kwargs = get_strategy_default_kwargs(strategy_name)
    backtest_config = get_strategy_backtest_config(strategy_name)

    config = dict(backtest_config)
    config['period'] = '1d'
    config['start_date'] = args.start
    config['end_date'] = args.end
    benchmark = IndexConstituentManager.SECTOR_TO_INDEX.get(args.sector, '000300.SH')
    config.setdefault('benchmark', benchmark)

    merged_kwargs = dict(default_kwargs)
    if args.max_volatility is not None:
        merged_kwargs['max_volatility'] = args.max_volatility
    if args.stop_loss is not None:
        merged_kwargs['stop_loss_pct'] = args.stop_loss

    print('=' * 60)
    print('  小市值策略回测（优化报告条件）')
    print(f'  回测区间: {args.start} ~ {args.end}')
    print(f'  股票池:   {args.sector} ({benchmark})')
    print(f'  风控参数: max_volatility={merged_kwargs.get("max_volatility")}, '
          f'stop_loss_pct={merged_kwargs.get("stop_loss_pct")}')
    print(f'  GUI 模式: {"关闭(--headless)" if args.headless else "开启（自动弹图）"}')
    print('=' * 60)
    sys.stdout.flush()

    api = BacktestAPI()
    api.set_strategy_name(strategy_name)
    if args.headless:
        api.set_ai_mode(True)
    api.configure(**config)
    print('  [debug] 加载财务数据(Balance+Pershareindex)...')
    sys.stdout.flush()
    api.load_financial_data(sector=args.sector, table_list=['Balance', 'Pershareindex'])
    print('  [debug] 财务数据加载完成')
    sys.stdout.flush()

    api.add_stock_selection_strategy(strategy_class, **merged_kwargs)
    print('  [debug] 策略添加完成，开始回测')
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
            'sector': args.sector,
            'start_date': args.start,
            'end_date': args.end,
            'max_volatility': merged_kwargs.get('max_volatility'),
            'stop_loss_pct': merged_kwargs.get('stop_loss_pct'),
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

        print_annual_breakdown(result)

        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports',
                           f'small_cap_{args.label}.json')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        print(f'\n  结果已保存: {out}')
        sys.stdout.flush()

        # 生成 HTML 报告（基于自动记录的回测结果）
        _generate_html_report(strategy_name)

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


def _generate_html_report(strategy_name: str):
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
