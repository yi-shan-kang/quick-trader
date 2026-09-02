"""
三层阁投资策略系统 - 主入口

两种运行模式：

1. 回测模式（backtest）：
   python main.py backtest
   python main.py backtest --start 2020-01-01 --end 2026-08-14 --mock
   python main.py backtest --withdraw 5000  # 模拟每月提取5000养老金

2. 实盘信号模式（live）：
   python main.py live
   python main.py live --holdings 161040,506003,162720,...
   输出：当前选基排名、轮动信号、再平衡建议

依赖安装：
   pip install -r requirements.txt
"""

import argparse
import sys
import os
from datetime import datetime

import pandas as pd

# 确保能导入同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    StrategyConfig, ALL_FUNDS, FUND_LOOKUP, get_rotatable_funds,
    get_bucket_funds, BENCHMARK_CODE, BENCHMARK_NAME,
)
from data_fetcher import DataFetcher, WeeklyDataAggregator
from indicators import compute_all_indicators, indicators_to_dataframe
from fund_selector import FundSelector
from portfolio import Portfolio
from backtest import BacktestEngine, BacktestResult
from visualize import generate_html_report


def run_backtest(args):
    """回测模式"""
    config = StrategyConfig()
    config.backtest_start = args.start
    config.backtest_end = args.end
    config.initial_capital = args.capital
    config.monthly_withdrawal = args.withdraw
    config.use_cache = not args.no_cache

    output_dir = args.output or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    os.makedirs(output_dir, exist_ok=True)

    print("\n" + "=" * 55)
    print("  三层阁策略量化回测系统")
    print("=" * 55)
    print(f"  回测区间:   {config.backtest_start} ~ {config.backtest_end}")
    print(f"  初始资金:   {config.initial_capital:,.0f}")
    print(f"  月度提取:   {config.monthly_withdrawal:,.0f}" if config.monthly_withdrawal > 0 else "  月度提取:   不提取")
    print(f"  数据源:     {'模拟数据' if args.mock else 'akshare实盘'}")
    print(f"  输出目录:   {output_dir}")
    print("=" * 55)

    fetcher = DataFetcher(config)

    if args.mock:
        fetcher.use_cache = False
        fetcher._ak = None
        original_fetch = fetcher.fetch_etf_history

        def mock_fetch(symbol, start_date, end_date):
            return fetcher._generate_mock_data(symbol, start_date, end_date)
        fetcher.fetch_etf_history = mock_fetch

    engine = BacktestEngine(config, fetcher)
    result = engine.run(use_mock=args.mock)

    if result.nav_series.empty:
        print("\n[错误] 回测失败，无有效数据")
        return

    print("\n" + result.summary())

    print("\n[生成图表...]")
    report_path = generate_html_report(result, output_dir)
    print(f"\n回测报告已生成: {report_path}")
    print(f"图表目录: {output_dir}")

    trade_path = os.path.join(output_dir, "trade_log.csv")
    if not result.trade_log.empty:
        result.trade_log.to_csv(trade_path, index=False, encoding="utf-8-sig")
        print(f"交易记录已保存: {trade_path}")

    nav_path = os.path.join(output_dir, "nav_series.csv")
    nav_df = pd.DataFrame({
        "date": result.nav_series.index,
        "portfolio_nav": result.nav_series.values,
        "benchmark_nav": result.benchmark_series.values,
    })
    nav_df.to_csv(nav_path, index=False, encoding="utf-8-sig")
    print(f"净值序列已保存: {nav_path}")


def run_live(args):
    """实盘信号模式"""
    config = StrategyConfig()
    fetcher = DataFetcher(config)
    selector = FundSelector(config)

    print("\n" + "=" * 55)
    print("  三层阁策略 - 实盘信号生成")
    print("=" * 55)

    # 获取最近的数据（3个月用于计算4周/10周净增）
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - pd.Timedelta(days=120)).strftime("%Y-%m-%d")

    print(f"\n[1/3] 获取基金数据 ({start_date} ~ {end_date})...")

    all_data = {}
    for fund in ALL_FUNDS:
        try:
            df = fetcher.fetch_etf_history(fund.code, start_date, end_date)
            if df is not None and len(df) > 10:
                all_data[fund.code] = df
                print(f"  OK {fund.code} {fund.name} ({len(df)}条)")
        except Exception as e:
            print(f"  FAIL {fund.code} {fund.name}: {e}")

    bench_data = fetcher.fetch_etf_history(BENCHMARK_CODE, start_date, end_date)
    if bench_data is not None:
        all_data[BENCHMARK_CODE] = bench_data

    if not all_data:
        print("\n[错误] 无法获取任何基金数据")
        print("提示: 请检查网络连接或使用 --mock 参数测试")
        return

    print(f"\n[2/3] 计算指标与排名...")
    price_matrix = WeeklyDataAggregator.build_price_matrix(
        all_data, start_date, end_date
    )

    if price_matrix.empty:
        print("[错误] 无法构建价格矩阵")
        return

    nav_matrix = price_matrix.copy()
    volume_matrix = price_matrix.copy() * 0
    for code, df in all_data.items():
        if code in price_matrix.columns:
            weekly_df = WeeklyDataAggregator.to_weekly(df)
            if "volume" in weekly_df.columns:
                vol = weekly_df.set_index("date")["volume"]
                for idx in vol.index:
                    if idx in volume_matrix.index and code in volume_matrix.columns:
                        volume_matrix.loc[idx, code] = vol[idx]

    current_date = price_matrix.index[-1]
    indicators = compute_all_indicators(
        price_matrix, nav_matrix, volume_matrix, current_date, config
    )

    # 解析当前持仓
    current_holdings = set()
    if args.holdings:
        current_holdings = {c.strip() for c in args.holdings.split(",") if c.strip()}

    print(f"\n[3/3] 生成交易信号 (日期: {current_date.strftime('%Y-%m-%d')})")

    selection = selector.select(indicators, current_holdings, current_date)
    report = selector.format_selection_report(selection)
    print(report)

    # 模拟组合状态
    if current_holdings:
        print("\n" + "-" * 55)
        print("当前持仓与组合状态")
        print("-" * 55)

        current_prices = price_matrix.loc[current_date].to_dict()
        portfolio = Portfolio(config)
        portfolio.cash = args.capital

        # 构建虚拟持仓
        per_fund_value = args.capital * 0.7 / max(len(current_holdings), 1)
        for code in current_holdings:
            fund_info = FUND_LOOKUP.get(code)
            if fund_info is None:
                continue
            price = current_prices.get(code, 1.0)
            shares = per_fund_value / price
            from portfolio import Position
            portfolio.positions[code] = Position(
                code=code,
                name=fund_info.name,
                bucket=fund_info.bucket,
                shares=shares,
                cost=price,
            )

        total_value = portfolio.get_value(current_prices)
        bucket_weights = portfolio.get_bucket_weights(current_prices)
        deviations = portfolio.get_deviations(current_prices)

        print(f"\n总市值: {total_value:,.0f}")
        print(f"\n各桶配置:")
        bucket_names = {"A_stock": "A股权益", "US_stock": "海外权益",
                        "bond": "债券收益", "gold": "黄金"}
        for bucket, target in config.target_allocation.items():
            actual = bucket_weights.get(bucket, 0)
            dev = deviations.get(bucket, 0)
            status = "OK" if abs(dev) <= config.rebalance_threshold else "需再平衡"
            print(f"  {bucket_names.get(bucket, bucket):8s}: "
                  f"目标{target*100:>4.0f}% | 实际{actual*100:>5.1f}% | "
                  f"偏离{dev*100:>+5.1f}% | {status}")

        if portfolio.needs_rebalance(current_prices):
            print("\n>>> 需要执行再平衡！")
            orders = portfolio.generate_rebalance_orders(current_prices,
                {"sell": [{"code": s.code, "name": s.name, "reason": s.reason}
                          for s in selection.sell_candidates],
                 "buy": [{"code": s.code, "name": s.name, "reason": s.reason}
                         for s in selection.buy_candidates]})
            if orders:
                print(f"\n再平衡指令 ({len(orders)}条):")
                for o in orders:
                    action_cn = "买入" if o.diff_value > 0 else "卖出"
                    print(f"  {action_cn} {o.code} {o.name:<12} "
                          f"{abs(o.diff_value):>10,.0f}元 | {o.reason}")
        else:
            print("\n>>> 各桶配置正常，无需再平衡。")

    # 保存完整排名到CSV
    if not selection.full_ranking.empty:
        csv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"ranking_{current_date.strftime('%Y%m%d')}.csv"
        )
        selection.full_ranking.to_csv(csv_path, encoding="utf-8-sig")
        print(f"\n完整排名已保存: {csv_path}")

    print("\n" + "=" * 55)
    print("提示:")
    print("  1. 本信号基于4周净增+年化折价排名，仅供参考")
    print("  2. 实际操作需结合个人风险承受能力")
    print("  3. 三层阁策略核心是纪律执行，不是精准选基")
    print("=" * 55)


def run_info(args):
    """显示策略信息"""
    print("""
╔═══════════════════════════════════════════════════════════╗
║         三层阁（三姑）投资策略量化系统                      ║
╠═════════════════════════════════════════════════════════════╣
║                                                           ║
║  策略核心：                                                ║
║  1. 四桶配置：A股35% + 美股35% + 债券15% + 黄金15%        ║
║  2. 2%动态再平衡：偏离基准超2%触发高抛低吸                 ║
║  3. 封基选基：4周净增+年化折价排名买入                     ║
║              10周净增恶化排名卖出                          ║
║  4. 永远满仓，不择时                                      ║
║  5. 弃弱留强，不追求选最优                                 ║
║                                                           ║
║  使用方式：                                                ║
║  回测:  python main.py backtest                           ║
║  回测(模拟数据): python main.py backtest --mock            ║
║  实盘信号: python main.py live                            ║
║  带持仓: python main.py live --holdings 161040,506003     ║
║                                                           ║
║  基金池：                                                  ║
║  A股桶:  {}只（定开基+ETF）                               ║
║  美股桶:  {}只（纳指+标普ETF）                             ║
║  债券桶:  {}只（债基+转债+城投）                           ║
║  黄金桶:  {}只（黄金ETF+黄金LOF）                          ║
║                                                           ║
╚════════════════════════════════════════════════════════════╝
""".format(
        len(get_rotatable_funds()),
        len(get_bucket_funds("US_stock")),
        len(get_bucket_funds("bond")),
        len(get_bucket_funds("gold")),
    ))


def main():
    parser = argparse.ArgumentParser(
        description="三层阁投资策略量化系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="运行模式")

    # 回测模式
    bt_parser = subparsers.add_parser("backtest", help="运行历史回测")
    bt_parser.add_argument("--start", default="2020-01-01", help="回测开始日期")
    bt_parser.add_argument("--end", default="2026-08-14", help="回测结束日期")
    bt_parser.add_argument("--capital", type=float, default=1_000_000, help="初始资金（元）")
    bt_parser.add_argument("--withdraw", type=float, default=0, help="每月提取金额（0=不提取）")
    bt_parser.add_argument("--mock", action="store_true", help="使用模拟数据（无需网络）")
    bt_parser.add_argument("--no-cache", action="store_true", help="不使用缓存")
    bt_parser.add_argument("--output", default=None, help="输出目录")

    # 实盘信号模式
    live_parser = subparsers.add_parser("live", help="生成实盘交易信号")
    live_parser.add_argument("--holdings", default=None,
                             help="当前持仓代码（逗号分隔，如 161040,506003,162720）")
    live_parser.add_argument("--capital", type=float, default=1_000_000, help="总资金（元）")

    # 信息模式
    subparsers.add_parser("info", help="显示策略信息")

    args = parser.parse_args()

    if args.command is None:
        run_info(args)
        return

    if args.command == "backtest":
        run_backtest(args)
    elif args.command == "live":
        run_live(args)
    elif args.command == "info":
        run_info(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
