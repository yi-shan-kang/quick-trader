"""
三层阁（三姑）投资策略 - 配置文件

核心方法论：
1. 四桶配置：A股35% + 美股35% + 债券15% + 黄金15%
2. 2%动态再平衡：偏离基准超2%触发调仓
3. 封基选基：4周净增+年化折价排名买入，10周净增恶化卖出
4. 永远满仓，不择时
5. 每月可选提取养老金
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FundInfo:
    code: str
    name: str
    bucket: str  # A_stock / US_stock / bond / gold
    fund_type: str  # etf / lof / closed_end
    maturity_date: str = ""  # 定开基到期/开放日，用于计算年化折价


@dataclass
class StrategyConfig:
    # === 四桶配置基准 ===
    target_allocation: Dict[str, float] = field(default_factory=lambda: {
        "A_stock": 0.35,
        "US_stock": 0.35,
        "bond": 0.15,
        "gold": 0.15,
    })

    # === 再平衡参数 ===
    rebalance_threshold: float = 0.02  # 偏离基准2%触发再平衡

    # === 封基选基参数 ===
    buy_lookback_weeks: int = 4   # 买入看4周净增
    sell_lookback_weeks: int = 10  # 卖出看10周净增
    max_funds_a_stock: int = 15   # A股桶最多持有基金数
    min_funds_a_stock: int = 8    # A股桶最少持有基金数

    # === 回测参数 ===
    backtest_start: str = "2020-01-01"
    backtest_end: str = "2026-08-14"
    initial_capital: float = 1_000_000.0

    # === 养老金提取（可选） ===
    monthly_withdrawal: float = 5000.0  # 每月提取5000元，设为0则不提取
    withdrawal_day: int = 20  # 每月20日提取

    # === 数据缓存 ===
    cache_dir: str = "./data_cache"
    use_cache: bool = True

    # === 交易成本 ===
    commission_rate: float = 0.0003  # 佣金费率万三
    stamp_duty: float = 0.0005     # 印花税万五（卖出时）
    slippage: float = 0.001        # 滑点千一


# === 基金池定义 ===
# A股桶：定开基金为主，辅以ETF作为替代标的
A_STOCK_FUNDS = [
    FundInfo("161040", "创业富国", "A_stock", "closed_end", "2027-12-15"),
    FundInfo("160529", "创业博时", "A_stock", "closed_end", "2027-06-15"),
    FundInfo("161912", "社会责任", "A_stock", "closed_end", "2028-03-15"),
    FundInfo("162720", "广发创业", "A_stock", "closed_end", "2027-09-15"),
    FundInfo("506003", "富国科创", "A_stock", "closed_end", "2027-07-15"),
    FundInfo("506006", "添富科创", "A_stock", "closed_end", "2028-01-15"),
    FundInfo("506000", "科创板基", "A_stock", "closed_end", "2027-11-15"),
    FundInfo("501093", "华夏翔阳", "A_stock", "closed_end", "2028-02-15"),
    FundInfo("501062", "南方瑞合", "A_stock", "closed_end", "2028-06-15"),
    FundInfo("160325", "华夏创业", "A_stock", "closed_end", "2027-05-15"),
    FundInfo("160726", "嘉实瑞享", "A_stock", "closed_end", "2028-04-15"),
    FundInfo("166024", "中欧恒利", "A_stock", "closed_end", "2028-09-15"),
    FundInfo("167508", "安信价值", "A_stock", "closed_end", "2027-10-15"),
    # 三姑实盘补充定开基（2022年后摊大饼配置引入）
    FundInfo("161914", "万家创业", "A_stock", "closed_end", "2027-03-15"),
    FundInfo("160926", "创业大成", "A_stock", "closed_end", "2027-01-15"),
    FundInfo("166025", "中欧远见", "A_stock", "closed_end", "2027-04-15"),
    FundInfo("501070", "广发睿阳", "A_stock", "closed_end", "2028-01-31"),
    FundInfo("506008", "科创长城", "A_stock", "closed_end", "2027-09-15"),
    FundInfo("501088", "嘉实瑞虹", "A_stock", "closed_end", "2028-09-04"),
    # ETF替代标的（当定开基数据不可用时使用）
    FundInfo("159952", "创业板50ETF", "A_stock", "etf"),
    FundInfo("588000", "科创50ETF", "A_stock", "etf"),
    FundInfo("510880", "红利ETF", "A_stock", "etf"),
    FundInfo("510300", "沪深300ETF", "A_stock", "etf"),
    FundInfo("510500", "中证500ETF", "A_stock", "etf"),
    FundInfo("159915", "创业板ETF", "A_stock", "etf"),
]

# 美股桶：纳斯达克+标普500 ETF
US_STOCK_FUNDS = [
    FundInfo("159696", "纳100ETF", "US_stock", "etf"),
    FundInfo("513390", "纳指基金", "US_stock", "etf"),
    FundInfo("513870", "纳指指数", "US_stock", "etf"),
    FundInfo("513650", "标普ETF", "US_stock", "etf"),
    FundInfo("513100", "纳指ETF", "US_stock", "etf"),
    FundInfo("513300", "纳斯达克", "US_stock", "etf"),
    FundInfo("159501", "纳指基金", "US_stock", "etf"),
    FundInfo("159659", "纳指招商", "US_stock", "etf"),
    FundInfo("161128", "标普科技", "US_stock", "lof"),
]

# 债券桶：债基+转债+城投
BOND_FUNDS = [
    FundInfo("161716", "招商双债", "bond", "lof"),
    FundInfo("511220", "城投ETF", "bond", "etf"),
    FundInfo("511380", "转债ETF", "bond", "etf"),
    FundInfo("161119", "易基综债", "bond", "lof"),
    FundInfo("161115", "易基岁丰", "bond", "lof"),
]

# 黄金桶
GOLD_FUNDS = [
    FundInfo("518850", "黄金ETF", "gold", "etf"),
    FundInfo("161116", "易基黄金", "gold", "lof"),
]

# 全部基金池
ALL_FUNDS = A_STOCK_FUNDS + US_STOCK_FUNDS + BOND_FUNDS + GOLD_FUNDS

# 基准指数
BENCHMARK_CODE = "510300"  # 沪深300ETF作为基准
BENCHMARK_NAME = "沪深300"

# 构建基金查找表
FUND_LOOKUP: Dict[str, FundInfo] = {f.code: f for f in ALL_FUNDS}


def get_bucket_funds(bucket: str) -> List[FundInfo]:
    """获取某个桶的全部基金"""
    return [f for f in ALL_FUNDS if f.bucket == bucket]


def get_rotatable_funds() -> List[FundInfo]:
    """获取可轮动的基金池（A股桶的基金，用于4周/10周排名）"""
    return A_STOCK_FUNDS


def get_hold_funds(bucket: str) -> List[FundInfo]:
    """获取某桶中应持有的基金（非轮动桶直接全部持有，轮动桶选排名靠前）"""
    if bucket == "A_stock":
        return get_rotatable_funds()
    return get_bucket_funds(bucket)


# 默认配置实例
DEFAULT_CONFIG = StrategyConfig()
