"""
封基选基模块 - 三层阁核心选基逻辑

选基规则：
1. 买入：4周净增 + 年化折价 综合排名靠前 → 买入首选
2. 卖出：10周净增持续恶化 + 折价缩小 → 排名靠后卖出
3. 弃弱留强：不追求选最优，只求不持有最差

操作频率：每周一次（周末复盘，周一执行）
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from config import StrategyConfig, FundInfo, get_rotatable_funds, FUND_LOOKUP
from indicators import FundIndicator, compute_all_indicators, indicators_to_dataframe


@dataclass
class TradeSignal:
    """单笔交易信号"""
    action: str        # "buy" / "sell" / "hold"
    code: str
    name: str
    bucket: str
    reason: str        # 信号产生的原因
    score: float       # 评分
    rank: int          # 排名


@dataclass
class SelectionResult:
    """选基结果"""
    date: pd.Timestamp
    buy_candidates: List[TradeSignal]    # 买入候选（排名靠前）
    sell_candidates: List[TradeSignal]   # 卖出候选（排名靠后）
    full_ranking: pd.DataFrame           # 完整排名表
    min_volume_threshold: float = 10000  # 最低成交量阈值


class FundSelector:
    """封基选基器 - 实现4周轮入、10周轮出"""

    def __init__(self, config: StrategyConfig = None):
        self.config = config or StrategyConfig()
        self.rotatable_codes = [f.code for f in get_rotatable_funds()]
        self.min_volume = 5000  # 最低周成交量（手），低于此不考虑

    def select(
        self,
        indicators: Dict[str, FundIndicator],
        current_holdings: Set[str],
        date: pd.Timestamp,
        buy_dates: Dict[str, pd.Timestamp] = None,
    ) -> SelectionResult:
        """
        执行选基逻辑

        参数:
            indicators: 当前所有基金的指标快照
            current_holdings: 当前持有的A股桶基金代码集合
            date: 当前日期
            buy_dates: 各基金买入日期，用于最小持有期判断
        """
        if buy_dates is None:
            buy_dates = {}

        min_hold_weeks = 4  # 最小持有期：买入后至少持有4周才可轮出
        max_rotation_per_week = 2  # 每周最多轮动2只

        rotatable = {
            code: ind for code, ind in indicators.items()
            if code in self.rotatable_codes
        }

        for code, ind in list(rotatable.items()):
            if ind.volume < self.min_volume:
                del rotatable[code]

        if not rotatable:
            return SelectionResult(
                date=date,
                buy_candidates=[],
                sell_candidates=[],
                full_ranking=pd.DataFrame(),
            )

        buy_ranking = sorted(
            rotatable.values(),
            key=lambda x: x.buy_score,
            reverse=True,
        )
        sell_ranking = sorted(
            rotatable.values(),
            key=lambda x: x.sell_score,
        )

        # 卖出候选：排名后30%，且满足最小持有期
        bottom_threshold = max(len(sell_ranking) // 3, 2)
        bottom_ranking = sell_ranking[:bottom_threshold]

        sell_eligible = []
        for ind in bottom_ranking:
            if ind.code not in current_holdings:
                continue
            buy_date = buy_dates.get(ind.code)
            if buy_date is not None:
                weeks_held = (date - buy_date).days / 7
                if weeks_held < min_hold_weeks:
                    continue  # 未达最小持有期，跳过
            sell_eligible.append(ind)

        max_hold = self.config.max_funds_a_stock
        available_slots = max_hold - len(current_holdings)

        # 买入候选：排名靠前，且不在当前持仓中
        buy_candidates = []
        for rank, ind in enumerate(buy_ranking):
            if ind.code in current_holdings:
                continue
            if len(buy_candidates) >= available_slots:
                break
            if len(buy_candidates) + len(current_holdings) >= max_hold:
                break
            if len(buy_candidates) >= max_rotation_per_week:
                break
            buy_candidates.append(TradeSignal(
                action="buy",
                code=ind.code,
                name=ind.name,
                bucket="A_stock",
                reason=f"4周净增{ind.net_growth_4w*100:.1f}%+年化折价{ind.annualized_discount*100:.1f}%",
                score=ind.buy_score,
                rank=rank + 1,
            ))

        # 卖出候选：最多轮出与买入数量匹配，且不超过每周上限
        slots_needed = min(len(buy_candidates), max_rotation_per_week)
        sell_candidates = []
        for rank, ind in enumerate(sell_eligible):
            if len(sell_candidates) >= slots_needed:
                break
            sell_candidates.append(TradeSignal(
                action="sell",
                code=ind.code,
                name=ind.name,
                bucket="A_stock",
                reason=f"10周净增{ind.net_growth_10w*100:.1f}%排名后1/3",
                score=ind.sell_score,
                rank=rank + 1,
            ))

        full_df = indicators_to_dataframe(rotatable)
        if not full_df.empty:
            full_df = full_df.sort_values("buy_score", ascending=False).reset_index(drop=True)
            full_df.index = full_df.index + 1
            full_df.index.name = "rank"

        return SelectionResult(
            date=date,
            buy_candidates=buy_candidates,
            sell_candidates=sell_candidates,
            full_ranking=full_df,
        )

    def select_top_n(
        self,
        indicators: Dict[str, FundIndicator],
        n: int,
        date: pd.Timestamp,
    ) -> List[str]:
        """选出排名前N的基金代码（用于初始化建仓）"""
        rotatable = {
            code: ind for code, ind in indicators.items()
            if code in self.rotatable_codes and ind.volume >= self.min_volume
        }
        if not rotatable:
            return []
        ranked = sorted(rotatable.values(), key=lambda x: x.buy_score, reverse=True)
        return [ind.code for ind in ranked[:n]]

    def format_selection_report(self, result: SelectionResult) -> str:
        """生成选基报告文本"""
        lines = []
        lines.append(f"\n{'='*60}")
        lines.append(f"选基报告 - {result.date.strftime('%Y-%m-%d')}")
        lines.append(f"{'='*60}")

        if result.buy_candidates:
            lines.append("\n【买入候选】")
            for s in result.buy_candidates:
                lines.append(f"  {s.rank:>2}. {s.code} {s.name:<12} | {s.reason}")
        else:
            lines.append("\n【买入候选】无")

        if result.sell_candidates:
            lines.append("\n【卖出候选】")
            for s in result.sell_candidates:
                lines.append(f"  {s.rank:>2}. {s.code} {s.name:<12} | {s.reason}")
        else:
            lines.append("\n【卖出候选】无")

        if not result.full_ranking.empty:
            lines.append(f"\n【完整排名】共{len(result.full_ranking)}只基金")
            lines.append(result.full_ranking.head(10).to_string())

        lines.append(f"\n{'='*60}\n")
        return "\n".join(lines)
