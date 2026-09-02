"""
技术指标模块

实现三层阁选基的核心指标：
1. 年化折价率（防守的盾）- 封基到期折价收敛的安全垫
2. N周净值增长（进攻的矛）- 4周买入信号 / 10周卖出信号
3. 综合评分 - 折价 + 净增的加权排名

遛狗理论：净值是主人（向前走），价格是狗（跑前跑后），折价是两者距离。
当主人跑得快、狗跑得慢（折价扩大）→ 买入机会。
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from config import FundInfo, FUND_LOOKUP


@dataclass
class FundIndicator:
    """单只基金在某一时刻的全部指标快照"""
    code: str
    name: str
    date: pd.Timestamp
    nav: float          # 当前净值
    price: float        # 当前交易价格
    discount_rate: float       # 折价率 = (NAV - Price) / NAV
    annualized_discount: float # 年化折价 = 折价率 / 剩余年限
    net_growth_4w: float      # 近4周净值增长
    net_growth_10w: float     # 近10周净值增长
    buy_score: float    # 买入综合评分 = 4周净增 + 年化折价
    sell_score: float   # 卖出综合评分 = 10周净增 + 年化折价
    volume: float       # 成交量（交易活跃度）
    remaining_years: float  # 剩余年限


def calculate_discount(nav: float, price: float) -> float:
    """计算折价率"""
    if nav <= 0 or price <= 0:
        return 0.0
    return (nav - price) / nav


def calculate_annualized_discount(discount_rate: float, remaining_years: float) -> float:
    """计算年化折价率 = 折价率 / 剩余年限"""
    if remaining_years <= 0 or remaining_years > 10:
        return discount_rate  # 无到期日的基金（ETF），直接用折价率
    return discount_rate / remaining_years


def calculate_remaining_years(maturity_date: str, current_date: pd.Timestamp) -> float:
    """计算定开基到期剩余年限"""
    if not maturity_date:
        return 5.0  # 默认5年
    try:
        mat = pd.Timestamp(maturity_date)
        delta = mat - current_date
        return max(delta.days / 365.25, 0.1)
    except Exception:
        return 5.0


def calculate_net_growth(nav_series: pd.Series, weeks: int) -> float:
    """
    计算N周净值增长
    nav_series: 按时间排序的周度净值序列
    weeks: 回看周数（4或10）
    """
    if len(nav_series) <= weeks:
        if len(nav_series) <= 1:
            return 0.0
        weeks = len(nav_series) - 1
    current_nav = nav_series.iloc[-1]
    past_nav = nav_series.iloc[-1 - weeks]
    if past_nav <= 0:
        return 0.0
    return (current_nav - past_nav) / past_nav


def compute_indicators(
    code: str,
    weekly_nav: pd.Series,
    weekly_price: pd.Series,
    weekly_volume: pd.Series,
    current_date: pd.Timestamp,
    config,
) -> Optional[FundIndicator]:
    """
    计算单只基金的全部指标

    参数:
        code: 基金代码
        weekly_nav: 周度净值序列（index为日期）
        weekly_price: 周度价格序列
        weekly_volume: 周度成交量序列
        current_date: 当前日期
        config: 策略配置
    """
    fund_info = FUND_LOOKUP.get(code)
    if fund_info is None:
        return None

    nav_up_to_date = weekly_nav[weekly_nav.index <= current_date]
    price_up_to_date = weekly_price[weekly_price.index <= current_date]
    vol_up_to_date = weekly_volume[weekly_volume.index <= current_date] if weekly_volume is not None else pd.Series(dtype=float)

    if len(nav_up_to_date) < 2 or len(price_up_to_date) < 2:
        return None

    current_nav = nav_up_to_date.iloc[-1]
    current_price = price_up_to_date.iloc[-1]
    current_vol = vol_up_to_date.iloc[-1] if len(vol_up_to_date) > 0 else 0.0

    # NaN 防护：未上市/无数据基金（矩阵中为 NaN）直接跳过，避免幽灵持仓
    if pd.isna(current_nav) or pd.isna(current_price):
        return None
    if current_nav <= 0 or current_price <= 0:
        return None

    discount = calculate_discount(current_nav, current_price)
    remaining_years = calculate_remaining_years(
        fund_info.maturity_date, current_date
    )
    annual_disc = calculate_annualized_discount(discount, remaining_years)

    growth_4w = calculate_net_growth(nav_up_to_date, config.buy_lookback_weeks)
    growth_10w = calculate_net_growth(nav_up_to_date, config.sell_lookback_weeks)

    buy_score = growth_4w + annual_disc
    sell_score = growth_10w + annual_disc

    return FundIndicator(
        code=code,
        name=fund_info.name,
        date=current_date,
        nav=current_nav,
        price=current_price,
        discount_rate=discount,
        annualized_discount=annual_disc,
        net_growth_4w=growth_4w,
        net_growth_10w=growth_10w,
        buy_score=buy_score,
        sell_score=sell_score,
        volume=current_vol,
        remaining_years=remaining_years,
    )


def compute_all_indicators(
    price_matrix: pd.DataFrame,
    nav_matrix: pd.DataFrame,
    volume_matrix: pd.DataFrame,
    current_date: pd.Timestamp,
    config,
) -> Dict[str, FundIndicator]:
    """计算基金池中所有基金在某一日期的指标"""
    results = {}
    for code in price_matrix.columns:
        if code not in nav_matrix.columns:
            continue
        try:
            ind = compute_indicators(
                code=code,
                weekly_nav=nav_matrix[code],
                weekly_price=price_matrix[code],
                weekly_volume=volume_matrix[code] if volume_matrix is not None and code in volume_matrix.columns else None,
                current_date=current_date,
                config=config,
            )
            if ind is not None:
                results[code] = ind
        except Exception as e:
            print(f"  [指标计算] {code} 出错: {e}")
    return results


def indicators_to_dataframe(indicators: Dict[str, FundIndicator]) -> pd.DataFrame:
    """将指标字典转为DataFrame，方便排序展示"""
    if not indicators:
        return pd.DataFrame()
    rows = []
    for ind in indicators.values():
        rows.append({
            "code": ind.code,
            "name": ind.name,
            "nav": round(ind.nav, 4),
            "price": round(ind.price, 4),
            "discount%": round(ind.discount_rate * 100, 2),
            "ann_disc%": round(ind.annualized_discount * 100, 2),
            "4w_growth%": round(ind.net_growth_4w * 100, 2),
            "10w_growth%": round(ind.net_growth_10w * 100, 2),
            "buy_score": round(ind.buy_score * 100, 2),
            "sell_score": round(ind.sell_score * 100, 2),
            "volume": int(ind.volume) if ind.volume > 0 else 0,
        })
    return pd.DataFrame(rows)
