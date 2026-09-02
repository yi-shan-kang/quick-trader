"""
回测引擎 - 模拟三层阁策略的历史运行

回测流程（每周循环）：
1. 获取截至当周的基金数据
2. 计算全部指标（折价、4周/10周净增、综合评分）
3. A股桶：运行选基模块，生成轮动信号
4. 检查各桶偏离：超过2%触发再平衡
5. 执行交易（含交易成本）
6. 每月提取养老金（可选）
7. 记录组合快照

输出：
- 周度净值序列
- 各桶权重变化序列
- 交易记录
- 绩效指标（年化收益、最大回撤、夏普比率等）
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import numpy as np
import pandas as pd

from config import (
    StrategyConfig, FUND_LOOKUP, get_bucket_funds,
    get_rotatable_funds, BENCHMARK_CODE,
)
from data_fetcher import DataFetcher, WeeklyDataAggregator
from indicators import compute_all_indicators, FundIndicator
from fund_selector import FundSelector, SelectionResult
from portfolio import Portfolio, PortfolioSnapshot


@dataclass
class BacktestResult:
    """回测结果"""
    config: StrategyConfig
    nav_series: pd.Series          # 组合周度净值
    benchmark_series: pd.Series     # 基准周度净值
    bucket_weights: pd.DataFrame   # 各桶权重变化
    trade_log: pd.DataFrame         # 交易记录
    snapshots: List[PortfolioSnapshot]  # 组合快照列表

    @property
    def total_return(self) -> float:
        if len(self.nav_series) < 2:
            return 0.0
        return self.nav_series.iloc[-1] / self.nav_series.iloc[0] - 1

    @property
    def benchmark_return(self) -> float:
        if len(self.benchmark_series) < 2:
            return 0.0
        return self.benchmark_series.iloc[-1] / self.benchmark_series.iloc[0] - 1

    @property
    def annual_return(self) -> float:
        if len(self.nav_series) < 2:
            return 0.0
        days = (self.nav_series.index[-1] - self.nav_series.index[0]).days
        if days <= 0:
            return 0.0
        years = days / 365.25
        return (self.nav_series.iloc[-1] / self.nav_series.iloc[0]) ** (1 / years) - 1

    @property
    def max_drawdown(self) -> float:
        peak = self.nav_series.expanding().max()
        drawdown = (self.nav_series - peak) / peak
        return drawdown.min()

    @property
    def sharpe_ratio(self) -> float:
        if len(self.nav_series) < 2:
            return 0.0
        weekly_returns = self.nav_series.pct_change().dropna()
        if len(weekly_returns) < 2 or weekly_returns.std() == 0:
            return 0.0
        annual_factor = 52 ** 0.5
        return (weekly_returns.mean() / weekly_returns.std()) * annual_factor

    @property
    def volatility(self) -> float:
        if len(self.nav_series) < 2:
            return 0.0
        weekly_returns = self.nav_series.pct_change().dropna()
        if len(weekly_returns) < 2:
            return 0.0
        return weekly_returns.std() * np.sqrt(52)

    @property
    def num_trades(self) -> int:
        return len(self.trade_log) if hasattr(self.trade_log, '__len__') else 0

    def summary(self) -> str:
        """生成绩效摘要"""
        lines = []
        lines.append("=" * 55)
        lines.append("           回测绩效报告")
        lines.append("=" * 55)
        lines.append(f"回测区间: {self.nav_series.index[0].strftime('%Y-%m-%d')} ~ {self.nav_series.index[-1].strftime('%Y-%m-%d')}")
        lines.append(f"初始资金: {self.config.initial_capital:,.0f}")
        lines.append(f"期末市值: {self.nav_series.iloc[-1]:,.0f}")
        lines.append(f"")
        lines.append(f"累计收益:     {self.total_return*100:>8.2f}%")
        lines.append(f"基准收益:     {self.benchmark_return*100:>8.2f}% (沪深300)")
        lines.append(f"超额收益:     {(self.total_return - self.benchmark_return)*100:>8.2f}%")
        lines.append(f"年化收益:     {self.annual_return*100:>8.2f}%")
        lines.append(f"最大回撤:     {self.max_drawdown*100:>8.2f}%")
        lines.append(f"夏普比率:     {self.sharpe_ratio:>8.2f}")
        lines.append(f"年化波动:     {self.volatility*100:>8.2f}%")
        lines.append(f"交易次数:     {self.num_trades:>8d}")
        if self.config.monthly_withdrawal > 0:
            months = len(self.nav_series) // 4
            total_withdrawn = months * self.config.monthly_withdrawal
            lines.append(f"累计提取:     {total_withdrawn:>12,.0f} ({months}个月)")
            lines.append(f"实际总收益:   {(self.nav_series.iloc[-1] + total_withdrawn - self.config.initial_capital):>12,.0f}")
        lines.append("=" * 55)
        return "\n".join(lines)


class BacktestEngine:
    """回测引擎"""

    def __init__(self, config: StrategyConfig = None, fetcher: DataFetcher = None):
        self.config = config or StrategyConfig()
        self.fetcher = fetcher or DataFetcher(self.config)
        self.selector = FundSelector(self.config)
        self.portfolio = Portfolio(self.config)
        self.snapshots: List[PortfolioSnapshot] = []
        self.weekly_nav: List[Tuple[pd.Timestamp, float]] = []
        self.weekly_bucket_weights: List[dict] = []
        self.last_withdrawal_month = None
        self.buy_dates: Dict[str, pd.Timestamp] = {}  # 追踪A股桶基金买入日期

    def run(self, use_mock: bool = False) -> BacktestResult:
        """
        执行回测

        参数:
            use_mock: True=使用模拟数据（无需网络），False=使用akshare实盘数据
        """
        print(f"\n{'='*55}")
        print(f"  三层阁策略回测启动")
        print(f"  区间: {self.config.backtest_start} ~ {self.config.backtest_end}")
        print(f"  初始资金: {self.config.initial_capital:,.0f}")
        print(f"  数据源: {'模拟数据' if use_mock else 'akshare实盘'}")
        print(f"{'='*55}\n")

        # 1. 获取全部基金数据
        print("[1/4] 获取基金数据...")
        all_data = self.fetcher.fetch_all_data(
            self.config.backtest_start, self.config.backtest_end
        )
        if not all_data:
            print("  [错误] 无数据可用，回测终止")
            return self._empty_result()

        # 2. 构建周度价格/净值矩阵
        print("[2/4] 构建周度数据矩阵...")
        price_matrix = WeeklyDataAggregator.build_price_matrix(
            all_data, self.config.backtest_start, self.config.backtest_end
        )
        if price_matrix.empty:
            print("  [错误] 价格矩阵为空")
            return self._empty_result()

        nav_matrix = WeeklyDataAggregator.build_nav_matrix(
            all_data, self.config.backtest_start, self.config.backtest_end
        )
        if nav_matrix.empty:
            nav_matrix = price_matrix.copy()

        volume_matrix = price_matrix.copy() * 0
        for code in all_data:
            if code in price_matrix.columns:
                weekly_df = WeeklyDataAggregator.to_weekly(all_data[code])
                if "volume" in weekly_df.columns:
                    vol_series = weekly_df.set_index("date")["volume"]
                    for idx in vol_series.index:
                        if idx in volume_matrix.index and code in volume_matrix.columns:
                            volume_matrix.loc[idx, code] = vol_series[idx]

        # 统计折价信息
        closed_end_codes = [c for c in price_matrix.columns
                           if c in FUND_LOOKUP and FUND_LOOKUP[c].fund_type == "closed_end"]
        if closed_end_codes and not nav_matrix.equals(price_matrix):
            sample_code = closed_end_codes[0]
            sample_nav = nav_matrix[sample_code].iloc[-1]
            sample_price = price_matrix[sample_code].iloc[-1]
            sample_disc = (sample_nav - sample_price) / sample_nav * 100
            print(f"  定开基金折价示例: {sample_code} "
                  f"NAV={sample_nav:.4f} Price={sample_price:.4f} 折价={sample_disc:.1f}%")
        else:
            print("  [注意] 净值=价格（无折价数据），封基折价收益未体现")

        all_weekly_dates = price_matrix.index.tolist()
        print(f"  共 {len(all_weekly_dates)} 个交易周")

        # 3. 初始建仓
        print("[3/4] 初始建仓...")
        first_date = all_weekly_dates[0]
        first_prices = price_matrix.iloc[0].to_dict()

        def _available_priced(codes, limit):
            """从候选代码中筛选出建仓日有有效价格(>0且非NaN)的，取前limit只"""
            picked = []
            for c in codes:
                p = first_prices.get(c, 0)
                if p is not None and not (isinstance(p, float) and p != p) and p > 0:
                    picked.append(c)
                if len(picked) >= limit:
                    break
            return picked

        # 摊大饼建仓：各桶持有全部当日可交易标的等权（与三姑实盘一致）
        a_stock_codes = _available_priced([f.code for f in get_bucket_funds("A_stock")], 999)
        us_codes = _available_priced([f.code for f in get_bucket_funds("US_stock")], 999)
        bond_codes = _available_priced([f.code for f in get_bucket_funds("bond")], 999)
        gold_codes = _available_priced([f.code for f in get_bucket_funds("gold")], 999)

        self.portfolio.initialize(
            prices=first_prices,
            a_stock_codes=a_stock_codes,
            us_stock_codes=us_codes,
            bond_codes=bond_codes,
            gold_codes=gold_codes,
            date=first_date,
        )

        for code in a_stock_codes:
            self.buy_dates[code] = first_date

        initial_value = self.portfolio.get_value(first_prices)
        self.weekly_nav.append((first_date, initial_value))
        snap = self.portfolio.snapshot(first_prices, first_date)
        self.snapshots.append(snap)
        self.weekly_bucket_weights.append({
            "date": first_date,
            **self.portfolio.get_bucket_weights(first_prices),
        })
        print(f"  建仓完成，总市值: {initial_value:,.0f}")

        # 4. 周度循环
        print("[4/4] 开始周度回测...")
        for i, current_date in enumerate(all_weekly_dates[1:], 1):
            current_prices = price_matrix.loc[current_date].to_dict()

            # 各桶当前可交易标的（用于动态纳入新上市基金，摊大饼）
            tradable_funds = {}
            for bucket in ("A_stock", "US_stock", "bond", "gold"):
                tradable_funds[bucket] = []
                for f in get_bucket_funds(bucket):
                    p = current_prices.get(f.code)
                    if pd.notna(p) and p > 0:
                        tradable_funds[bucket].append(f.code)

            # 每月提取养老金
            if self.config.monthly_withdrawal > 0:
                current_month = current_date.strftime("%Y-%m")
                if current_month != self.last_withdrawal_month:
                    self.portfolio.withdraw(
                        self.config.monthly_withdrawal,
                        current_prices, current_date
                    )
                    self.last_withdrawal_month = current_month

            # 检查再平衡
            needs_rebal = self.portfolio.needs_rebalance(current_prices)

            # 是否有新上市/新可交易标的（动态纳入，摊大饼）
            new_fund_available = any(
                any(
                    f.code not in self.portfolio.positions
                    for f in get_bucket_funds(bucket)
                    if f.code in tradable_funds[bucket]
                )
                for bucket in ("A_stock", "US_stock", "bond", "gold")
            )

            if needs_rebal or new_fund_available:
                orders = self.portfolio.generate_rebalance_orders(
                    current_prices, None, tradable_funds=tradable_funds
                )
                self.portfolio.execute_orders(orders, current_prices, current_date)

                # 更新买入日期追踪
                for order in orders:
                    if order.bucket != "A_stock":
                        continue
                    if order.action == "buy" and order.code not in self.buy_dates:
                        self.buy_dates[order.code] = current_date
                    elif order.action == "sell" and order.code in self.buy_dates:
                        if order.code not in self.portfolio.positions:
                            del self.buy_dates[order.code]

            # 记录快照
            total_value = self.portfolio.get_value(current_prices)
            self.weekly_nav.append((current_date, total_value))
            snap = self.portfolio.snapshot(current_prices, current_date)
            self.snapshots.append(snap)
            self.weekly_bucket_weights.append({
                "date": current_date,
                **self.portfolio.get_bucket_weights(current_prices),
            })

            if (i + 1) % 20 == 0:
                print(f"  第 {i+1}/{len(all_weekly_dates)-1} 周 | "
                      f"净值: {total_value:,.0f} | "
                      f"持仓: {len(self.portfolio.positions)}只")

        # 构建结果
        return self._build_result(price_matrix)

    def _compute_indicators_at(
        self, date, price_matrix, nav_matrix, volume_matrix
    ) -> Dict[str, FundIndicator]:
        """计算某一时点的全部指标"""
        return compute_all_indicators(
            price_matrix=price_matrix,
            nav_matrix=nav_matrix,
            volume_matrix=volume_matrix,
            current_date=date,
            config=self.config,
        )

    def _build_result(self, price_matrix: pd.DataFrame) -> BacktestResult:
        """构建回测结果对象"""
        nav_series = pd.Series(
            [v for _, v in self.weekly_nav],
            index=[d for d, _ in self.weekly_nav],
            name="portfolio_nav"
        )

        bench_col = BENCHMARK_CODE if BENCHMARK_CODE in price_matrix.columns else None
        if bench_col:
            bench_prices = price_matrix[bench_col]
            initial_bench = bench_prices.iloc[0]
            benchmark_series = (bench_prices / initial_bench) * self.config.initial_capital
            benchmark_series.name = "benchmark_nav"
        else:
            benchmark_series = pd.Series(
                [self.config.initial_capital] * len(nav_series),
                index=nav_series.index, name="benchmark_nav"
            )

        bucket_weights_df = pd.DataFrame(self.weekly_bucket_weights).set_index("date")

        trade_log_df = pd.DataFrame(self.portfolio.trade_log)

        return BacktestResult(
            config=self.config,
            nav_series=nav_series,
            benchmark_series=benchmark_series,
            bucket_weights=bucket_weights_df,
            trade_log=trade_log_df,
            snapshots=self.snapshots,
        )

    def _empty_result(self) -> BacktestResult:
        """空结果"""
        return BacktestResult(
            config=self.config,
            nav_series=pd.Series(dtype=float),
            benchmark_series=pd.Series(dtype=float),
            bucket_weights=pd.DataFrame(),
            trade_log=pd.DataFrame(),
            snapshots=[],
        )
