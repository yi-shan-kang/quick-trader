"""
组合管理模块 - 四桶配置与2%动态再平衡

三层阁核心组合管理：
1. 四桶配置：A股35% + 美股35% + 债券15% + 黄金15%
2. 2%阈值：某桶偏离基准超2% → 触发再平衡
3. 永远满仓：现金接近0，不择时
4. A股桶内部：用选基模块进行弃弱留强轮动
5. 其他桶：持有固定标的，随再平衡调整仓位
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import pandas as pd
import numpy as np

from config import StrategyConfig, FundInfo, FUND_LOOKUP, get_bucket_funds


@dataclass
class Position:
    """单只基金持仓"""
    code: str
    name: str
    bucket: str
    shares: float     # 持有份额
    cost: float       # 持仓成本


@dataclass
class RebalanceOrder:
    """再平衡指令"""
    action: str         # "buy" / "sell"
    code: str
    name: str
    bucket: str
    target_value: float
    current_value: float
    diff_value: float    # 正数=买入金额，负数=卖出金额
    reason: str


@dataclass
class PortfolioSnapshot:
    """组合在某一时点的快照"""
    date: pd.Timestamp
    total_value: float
    bucket_values: Dict[str, float]
    bucket_weights: Dict[str, float]
    positions: Dict[str, Position]
    cash: float
    deviation: Dict[str, float]  # 各桶偏离基准的幅度


class Portfolio:
    """四桶组合管理器"""

    def __init__(self, config: StrategyConfig = None):
        self.config = config or StrategyConfig()
        self.cash: float = config.initial_capital if config else 1_000_000.0
        self.positions: Dict[str, Position] = {}
        self.target_allocation = config.target_allocation if config else {
            "A_stock": 0.35, "US_stock": 0.35, "bond": 0.15, "gold": 0.15
        }
        self.threshold = config.rebalance_threshold if config else 0.02
        self.trade_log: List[dict] = []

    @property
    def total_value(self) -> float:
        return self.cash + sum(
            p.shares * p.cost for p in self.positions.values()
        )

    def get_value(self, prices: Dict[str, float]) -> float:
        """根据最新价格计算总市值"""
        stock_value = sum(
            p.shares * prices.get(p.code, p.cost)
            for p in self.positions.values()
        )
        return self.cash + stock_value

    def get_bucket_values(self, prices: Dict[str, float]) -> Dict[str, float]:
        """计算各桶市值"""
        bucket_vals = {b: 0.0 for b in self.target_allocation}
        for pos in self.positions.values():
            price = prices.get(pos.code, pos.cost)
            val = pos.shares * price
            if pos.bucket in bucket_vals:
                bucket_vals[pos.bucket] += val
        bucket_vals["cash"] = self.cash
        return bucket_vals

    def get_bucket_weights(self, prices: Dict[str, float]) -> Dict[str, float]:
        """计算各桶权重"""
        total = self.get_value(prices)
        if total <= 0:
            return {b: 0.0 for b in self.target_allocation}
        bucket_vals = self.get_bucket_values(prices)
        return {b: v / total for b, v in bucket_vals.items() if b != "cash"}

    def get_deviations(self, prices: Dict[str, float]) -> Dict[str, float]:
        """计算各桶偏离基准的幅度"""
        weights = self.get_bucket_weights(prices)
        return {
            b: weights.get(b, 0) - self.target_allocation[b]
            for b in self.target_allocation
        }

    def needs_rebalance(self, prices: Dict[str, float]) -> bool:
        """判断是否需要再平衡（任一桶偏离超2%）"""
        deviations = self.get_deviations(prices)
        return any(abs(d) > self.threshold for d in deviations.values())

    def generate_rebalance_orders(
        self,
        prices: Dict[str, float],
        a_stock_rotation: Optional[dict] = None,
        tradable_funds: Optional[Dict[str, List[str]]] = None,
    ) -> List[RebalanceOrder]:
        """
        生成再平衡指令

        参数:
            prices: 各基金最新价格
            a_stock_rotation: A股桶轮动信号 {"sell": [...], "buy": [...]}
            tradable_funds: 非轮动桶当前可交易标的 {bucket: [code, ...]}，
                            用于动态纳入新上市基金（摊大饼：桶内全部标的等权）
        """
        orders = []
        total = self.get_value(prices)
        if total <= 0:
            return orders

        deviations = self.get_deviations(prices)
        bucket_values = self.get_bucket_values(prices)

        overweight_buckets = sorted(
            [(b, d) for b, d in deviations.items() if d > self.threshold],
            key=lambda x: x[1], reverse=True,
        )
        underweight_buckets = sorted(
            [(b, d) for b, d in deviations.items() if d < -self.threshold],
            key=lambda x: x[1],
        )

        # 1. 先处理A股桶内部的轮动（弃弱留强）——仅 live/轮动模式使用
        if a_stock_rotation:
            for sell_info in a_stock_rotation.get("sell", []):
                code = sell_info["code"]
                if code in self.positions:
                    pos = self.positions[code]
                    price = prices.get(code, pos.cost)
                    sell_value = pos.shares * price
                    orders.append(RebalanceOrder(
                        action="sell",
                        code=code,
                        name=pos.name,
                        bucket="A_stock",
                        target_value=0,
                        current_value=sell_value,
                        diff_value=-sell_value,
                        reason=f"轮动卖出：{sell_info.get('reason', '')}",
                    ))

            remaining_a_value = bucket_values.get("A_stock", 0)
            for sell_info in a_stock_rotation.get("sell", []):
                code = sell_info["code"]
                if code in self.positions:
                    remaining_a_value -= self.positions[code].shares * prices.get(code, 0)

            for buy_info in a_stock_rotation.get("buy", []):
                code = buy_info["code"]
                buy_value = buy_info.get("value", 0)
                if buy_value <= 0:
                    buy_value = remaining_a_value / max(len(a_stock_rotation.get("buy", [])), 1)
                orders.append(RebalanceOrder(
                    action="buy",
                    code=code,
                    name=buy_info.get("name", FUND_LOOKUP.get(code, FundInfo(code, "", "", "")).name),
                    bucket="A_stock",
                    target_value=buy_value,
                    current_value=0,
                    diff_value=buy_value,
                    reason=f"轮动买入：{buy_info.get('reason', '')}",
                ))

        # 2. 处理桶间再平衡（超配桶→低配桶）
        for over_bucket, over_dev in overweight_buckets:
            over_value = bucket_values[over_bucket]
            target_value = total * self.target_allocation[over_bucket]
            excess = over_value - target_value

            if excess <= 0:
                continue

            bucket_positions = [
                (code, pos) for code, pos in self.positions.items()
                if pos.bucket == over_bucket
                and code not in {s["code"] for s in (a_stock_rotation or {}).get("sell", [])}
            ]

            if not bucket_positions:
                continue

            if len(bucket_positions) == 1:
                code, pos = bucket_positions[0]
                price = prices.get(code, pos.cost)
                sell_shares = min(excess / price, pos.shares)
                sell_value = sell_shares * price
                orders.append(RebalanceOrder(
                    action="sell",
                    code=code,
                    name=pos.name,
                    bucket=over_bucket,
                    target_value=target_value,
                    current_value=pos.shares * price,
                    diff_value=-sell_value,
                    reason=f"桶间再平衡：{over_bucket}超配{over_dev*100:.1f}%",
                ))
            else:
                per_fund_excess = excess / len(bucket_positions)
                for code, pos in bucket_positions:
                    price = prices.get(code, pos.cost)
                    sell_shares = min(per_fund_excess / price, pos.shares)
                    sell_value = sell_shares * price
                    if sell_value > 1:
                        orders.append(RebalanceOrder(
                            action="sell",
                            code=code,
                            name=pos.name,
                            bucket=over_bucket,
                            target_value=target_value,
                            current_value=pos.shares * price,
                            diff_value=-sell_value,
                            reason=f"桶间再平衡：{over_bucket}超配{over_dev*100:.1f}%",
                        ))

        # 3. 低配桶加仓：只加仓已有持仓，不新增基金
        for under_bucket, under_dev in underweight_buckets:
            target_value = total * self.target_allocation[under_bucket]
            current = bucket_values[under_bucket]
            pending_buys = sum(
                o.diff_value for o in orders
                if o.bucket == under_bucket and o.action == "buy"
            )
            deficit = target_value - current - pending_buys
            if deficit <= 0:
                continue

            existing_positions = [
                (code, pos) for code, pos in self.positions.items()
                if pos.bucket == under_bucket
                and code not in {o.code for o in orders if o.action == "sell"}
            ]
            if not existing_positions:
                continue

            per_fund = deficit / len(existing_positions)
            for code, pos in existing_positions:
                if code in {o.code for o in orders if o.action == "buy"}:
                    continue
                orders.append(RebalanceOrder(
                    action="buy",
                    code=code,
                    name=pos.name,
                    bucket=under_bucket,
                    target_value=target_value,
                    current_value=pos.shares * prices.get(code, pos.cost),
                    diff_value=per_fund,
                    reason=f"桶间再平衡：{under_bucket}低配{under_dev*100:.1f}%",
                ))

        # 4. 各桶动态纳入新上市/新可交易标的（摊大饼：桶内全部标的等权）
        #    A股桶也走摊大饼：持有全部可交易定开基+ETF，与三姑实盘一致
        if tradable_funds:
            for bucket in ("A_stock", "US_stock", "bond", "gold"):
                available = [
                    c for c in tradable_funds.get(bucket, [])
                    if c in prices and pd.notna(prices[c]) and prices[c] > 0
                ]
                if not available:
                    continue
                held = {code for code, pos in self.positions.items() if pos.bucket == bucket}
                new_codes = [c for c in available if c not in held]
                if not new_codes:
                    continue

                bucket_target = total * self.target_allocation[bucket]
                per_fund = bucket_target / len(available)
                add_orders = []

                # 现有持仓超出等权目标的部分先卖出（为新增标的腾出资金）
                for code, pos in self.positions.items():
                    if pos.bucket != bucket:
                        continue
                    price = prices.get(code, pos.cost)
                    cur = pos.shares * price
                    if cur > per_fund:
                        sell_value = cur - per_fund
                        if sell_value > 1:
                            add_orders.append(RebalanceOrder(
                                action="sell",
                                code=code,
                                name=pos.name,
                                bucket=bucket,
                                target_value=per_fund,
                                current_value=cur,
                                diff_value=-sell_value,
                                reason=f"动态纳入新标的：{bucket}桶摊平至{len(available)}只",
                            ))

                # 再买入新上市标的
                for code in new_codes:
                    price = prices[code]
                    if pd.notna(price) and price > 0:
                        fund_info = FUND_LOOKUP.get(code, FundInfo(code, code, bucket, "etf"))
                        add_orders.append(RebalanceOrder(
                            action="buy",
                            code=code,
                            name=fund_info.name,
                            bucket=bucket,
                            target_value=per_fund,
                            current_value=0,
                            diff_value=per_fund,
                            reason=f"动态纳入新标的：{bucket}桶新增{fund_info.name}",
                        ))

                orders.extend(add_orders)

        return self._deduplicate_orders(orders)

    def _deduplicate_orders(self, orders: List[RebalanceOrder]) -> List[RebalanceOrder]:
        """合并同一基金的多条指令"""
        if not orders:
            return []
        merged = {}
        for o in orders:
            if o.code not in merged:
                merged[o.code] = o
            else:
                existing = merged[o.code]
                existing.diff_value += o.diff_value
                if existing.diff_value > 0:
                    existing.action = "buy"
                elif existing.diff_value < 0:
                    existing.action = "sell"
                else:
                    existing.action = "hold"
        return list(merged.values())

    def execute_orders(
        self,
        orders: List[RebalanceOrder],
        prices: Dict[str, float],
        date: pd.Timestamp,
    ):
        """执行交易指令"""
        for order in orders:
            if abs(order.diff_value) < 100:  # 小于100元不操作
                continue

            price = prices.get(order.code, 0)
            if price <= 0:
                continue

            # 计算交易成本
            trade_value = abs(order.diff_value)
            commission = trade_value * self.config.commission_rate
            stamp = trade_value * self.config.stamp_duty if order.diff_value < 0 else 0
            slippage_cost = trade_value * self.config.slippage
            total_cost = commission + stamp + slippage_cost

            if order.diff_value > 0:  # 买入
                exec_price = price * (1 + self.config.slippage)
                shares = order.diff_value / exec_price
                self.cash -= order.diff_value + commission
                if order.code in self.positions:
                    pos = self.positions[order.code]
                    total_shares = pos.shares + shares
                    total_cost_basis = pos.shares * pos.cost + shares * exec_price
                    pos.shares = total_shares
                    pos.cost = total_cost_basis / total_shares
                else:
                    self.positions[order.code] = Position(
                        code=order.code,
                        name=order.name,
                        bucket=order.bucket,
                        shares=shares,
                        cost=exec_price,
                    )
            else:  # 卖出
                exec_price = price * (1 - self.config.slippage)
                sell_value = abs(order.diff_value)
                if order.code not in self.positions:
                    continue
                pos = self.positions[order.code]
                sell_shares = min(sell_value / exec_price, pos.shares)
                self.cash += sell_shares * exec_price - commission - stamp
                pos.shares -= sell_shares
                if pos.shares < 0.01:  # 清仓
                    del self.positions[order.code]

            self.trade_log.append({
                "date": date,
                "action": order.action,
                "code": order.code,
                "name": order.name,
                "bucket": order.bucket,
                "price": price,
                "value": order.diff_value,
                "reason": order.reason,
                "cost": total_cost,
            })

    def snapshot(self, prices: Dict[str, float], date: pd.Timestamp) -> PortfolioSnapshot:
        """生成组合快照"""
        total = self.get_value(prices)
        bucket_vals = self.get_bucket_values(prices)
        bucket_weights = self.get_bucket_weights(prices)
        deviations = self.get_deviations(prices)

        return PortfolioSnapshot(
            date=date,
            total_value=total,
            bucket_values=bucket_vals,
            bucket_weights=bucket_weights,
            positions=dict(self.positions),
            cash=self.cash,
            deviation=deviations,
        )

    def initialize(
        self,
        prices: Dict[str, float],
        a_stock_codes: List[str],
        us_stock_codes: List[str],
        bond_codes: List[str],
        gold_codes: List[str],
        date: pd.Timestamp,
    ):
        """初始建仓：按四桶比例买入"""
        total = self.cash

        for bucket_name, codes, target_pct in [
            ("A_stock", a_stock_codes, self.target_allocation["A_stock"]),
            ("US_stock", us_stock_codes, self.target_allocation["US_stock"]),
            ("bond", bond_codes, self.target_allocation["bond"]),
            ("gold", gold_codes, self.target_allocation["gold"]),
        ]:
            target_value = total * target_pct
            per_fund = target_value / len(codes) if codes else 0
            for code in codes:
                price = prices.get(code, 0)
                if price <= 0:
                    continue
                exec_price = price * (1 + self.config.slippage)
                shares = per_fund / exec_price
                commission = per_fund * self.config.commission_rate
                self.cash -= per_fund + commission
                fund_info = FUND_LOOKUP.get(code, FundInfo(code, code, bucket_name, "etf"))
                self.positions[code] = Position(
                    code=code,
                    name=fund_info.name,
                    bucket=bucket_name,
                    shares=shares,
                    cost=exec_price,
                )
                self.trade_log.append({
                    "date": date,
                    "action": "buy",
                    "code": code,
                    "name": fund_info.name,
                    "bucket": bucket_name,
                    "price": price,
                    "value": per_fund,
                    "reason": "初始建仓",
                    "cost": commission,
                })

    def withdraw(self, amount: float, prices: Dict[str, float], date: pd.Timestamp):
        """提取养老金：按比例从各桶卖出"""
        total = self.get_value(prices)
        if total <= 0:
            return

        for bucket in self.target_allocation:
            ratio = self.target_allocation[bucket]
            sell_value = amount * ratio
            bucket_positions = [
                (code, pos) for code, pos in self.positions.items()
                if pos.bucket == bucket
            ]
            if not bucket_positions:
                continue
            per_fund = sell_value / len(bucket_positions)
            for code, pos in bucket_positions:
                price = prices.get(code, pos.cost)
                exec_price = price * (1 - self.config.slippage)
                sell_shares = min(per_fund / exec_price, pos.shares)
                commission = sell_shares * exec_price * self.config.commission_rate
                stamp = sell_shares * exec_price * self.config.stamp_duty
                self.cash += sell_shares * exec_price - commission - stamp
                pos.shares -= sell_shares
                if pos.shares < 0.01:
                    del self.positions[code]
