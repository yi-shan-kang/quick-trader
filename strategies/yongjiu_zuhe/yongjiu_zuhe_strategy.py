# -*- coding: utf-8 -*-
"""永久组合策略（Permanent Portfolio）

来源：example/永久组合/ 目录资料（聚宽永久组合系列，Harry Browne 经典配置）

核心思想：
    用最简单的资产配置，在任何经济环境（繁荣/衰退/通胀/通缩）下都能保住
    购买力，长期跑赢通胀、回撤可控。不预测市场、不择时，只做配置与纪律。

四大类资产对冲四种经济情景：
    股票（繁荣） | 债券（衰退/通缩） | 黄金（通胀） | 货币（危机/现金为王）

调仓规则（聚宽原版口径）：
- 首日按目标权重建仓（order_target_value 语义）
- 按 rebalance_interval 周期检查（year/quarter/month，默认 year=每年12月）：
  - 任一资产占比偏离目标超过 rebalance_threshold（默认6%）→ 触发再平衡
  - 同一周期内不重复再平衡
- rebalance_rule 触发规则：
  - threshold（默认）：绝对偏离 > rebalance_threshold 触发（partial 可选）
  - band25：5/25 规则的"25"分支——单腿相对目标偏离 ±25%
    （ratio ≥ target×1.25 或 ≤ target×0.75）触发，触发后全额回归目标权重
- 使用成交价而非信号价的目标权重再平衡（先卖后买）

版本：config.VERSIONS 内置 5 个版本（base/b2/opt2/opt4/cn_rec），默认 opt2
"""
import datetime as dt_module

from core.strategy_logic import StrategyLogic, BarData
from strategies import register_strategy
from .config import VERSIONS, VERSION_NAMES, DEFAULT_VERSION


@register_strategy('yongjiu_zuhe',
                   default_kwargs={'version': DEFAULT_VERSION},
                   backtest_config={'cash': 100000, 'commission': 0.0002,
                                    'open_commission': 0.0002,
                                    'close_commission': 0.0002,
                                    'close_tax': 0.0,
                                    'min_commission': 5.0,
                                    'slippage': 0.002,
                                    'start_date': '2020-04-28', 'end_date': '2026-04-28',
                                    'period': '1d',
                                    'benchmark': '000300.SH'})
class YongjiuZuheStrategy(StrategyLogic):
    """永久组合策略

    固定权重资产配置 + 周期阈值再平衡。参数:
    - version: base/b2/opt2/opt4/cn_rec
    - rebalance_interval: 再平衡周期 year/quarter/month（默认 year）
    - rebalance_month: 年度再平衡检查月份（默认 12，仅 year 周期生效）
    - rebalance_threshold: 偏离触发阈值（默认 0.06 = 6%，仅 threshold 规则生效）
    - partial_rebalance: #7 部分再平衡（仅 threshold 规则生效）
    - rebalance_rule: 触发规则 threshold=绝对偏离阈值 / band25=5/25规则"25"分支
    """

    params = (
        ('version', DEFAULT_VERSION),        # 配置版本
        ('rebalance_interval', 'year'),      # 再平衡周期: year/quarter/month
        ('rebalance_month', 12),            # 年度再平衡检查月份
        ('rebalance_threshold', 0.06),      # 偏离目标权重阈值（触发再平衡）
        ('partial_rebalance', True),        # #7 部分再平衡：True=只调偏离超限资产；False=全部调回目标
        ('rebalance_rule', 'threshold'),    # 触发规则: threshold=绝对偏离阈值 / band25=5/25规则(相对±25%触发,全额回归)
    )

    def __init__(self, executor=None, **kwargs):
        super().__init__(executor, **kwargs)
        # 兼容 SimpleParams：默认值已在 params 定义，kwargs 覆盖
        version = getattr(self.params, 'version', DEFAULT_VERSION)
        if version not in VERSIONS:
            raise ValueError(f'未知版本: {version}, 可选: {list(VERSIONS.keys())}')
        self.version = version
        self.weights = dict(VERSIONS[version])
        self.symbols = list(self.weights.keys())
        # 建仓/再平衡状态跟踪
        self._initialized = False
        self._last_rebalance_key = None

    def get_symbols(self):
        return list(self.symbols)

    # ================================================================
    # 主流程
    # ================================================================

    def on_bar(self, bar: BarData):
        current_date = self.get_current_date()
        if current_date is None:
            return
        year = current_date.year if hasattr(current_date, 'year') else None
        month = current_date.month if hasattr(current_date, 'month') else None
        if year is None or month is None:
            return

        # 1. 首日建仓
        if not self._initialized:
            self._initial_buy()
            self._initialized = True
            self._last_rebalance_key = self._cycle_key(year, month)
            return

        # 2. 周期再平衡检查（年/季/月，由 rebalance_interval 决定）
        if not self._is_check_month(month):
            return
        key = self._cycle_key(year, month)
        if self._last_rebalance_key == key:
            return

        # 计算偏离，超阈值才再平衡
        self._check_and_rebalance(year, month)

    # ================================================================
    # 再平衡周期辅助
    # ================================================================

    def _interval(self) -> str:
        return getattr(self.params, 'rebalance_interval', 'year')

    def _is_check_month(self, month: int) -> bool:
        """当前月份是否为再平衡检查月（年=rebalance_month，季=3/6/9/12，月=每月）"""
        interval = self._interval()
        if interval == 'month':
            return True
        if interval == 'quarter':
            return month in (3, 6, 9, 12)
        return month == getattr(self.params, 'rebalance_month', 12)

    def _cycle_key(self, year: int, month: int):
        """当前周期标识（同一周期最多触发一次再平衡）"""
        interval = self._interval()
        if interval == 'month':
            return (year, month)
        if interval == 'quarter':
            return (year, (month - 1) // 3 + 1)
        return year

    def _cycle_label(self, year: int, month: int) -> str:
        """周期标签（用于日志）"""
        interval = self._interval()
        if interval == 'month':
            return f'{year}-{month:02d}'
        if interval == 'quarter':
            return f'{year}Q{(month - 1) // 3 + 1}'
        return str(year)

    # ================================================================
    # 建仓
    # ================================================================

    def _initial_buy(self):
        """首日按目标权重建仓"""
        date_str = self._date_str()
        total = self._get_total_value()
        if total <= 0:
            self.log(f'[{date_str}] 建仓失败: 总资产<=0', level='warning')
            return
        for symbol, weight in self.weights.items():
            price = self.get_current_price(symbol)
            if price is None or price <= 0:
                self.log(f'[{date_str}] 建仓跳过(无价格): {symbol}', level='warning')
                continue
            self._buy_amount(symbol, price, total * weight)
        self.log(f'[{date_str}] 初始建仓完成: {VERSION_NAMES.get(self.version, self.version)}', level='info')

    # ================================================================
    # 周期再平衡
    # ================================================================

    def _check_and_rebalance(self, year: int, month: int):
        """周期偏离检查 + 再平衡

        触发规则由 rebalance_rule 决定：
        - threshold：任一腿绝对偏离 > rebalance_threshold（默认6%）
        - band25：任一腿相对目标偏离 ±25%（ratio >= target*1.25 或 <= target*0.75）
        """
        key = self._cycle_key(year, month)
        label = self._cycle_label(year, month)
        date_str = self._date_str()
        total = self._get_total_value()
        if total <= 0:
            return

        # 计算当前各资产占比
        current_ratio = {}
        max_deviation = 0.0
        for symbol in self.symbols:
            pos_size = self.get_position_size(symbol)
            price = self.get_current_price(symbol)
            value = pos_size * price if (pos_size > 0 and price and price > 0) else 0.0
            ratio = value / total if total > 0 else 0.0
            current_ratio[symbol] = ratio
            target = self.weights[symbol]
            max_deviation = max(max_deviation, abs(ratio - target))

        # 记录偏离明细
        parts = [f'{symbol}:{current_ratio.get(symbol, 0):.1%}' for symbol in self.symbols]
        self.log(f'[{date_str}] 周期检查 {label}: ' + ' '.join(parts), level='info')

        # 触发判断
        rule = getattr(self.params, 'rebalance_rule', 'threshold')
        if rule == 'band25':
            # 5/25 规则的"25"分支：任一腿相对目标偏离 ±25% 即触发
            triggered = False
            for symbol in self.symbols:
                ratio = current_ratio[symbol]
                target = self.weights[symbol]
                if target > 0 and (ratio >= target * 1.25 or ratio <= target * 0.75):
                    triggered = True
                    break
            if not triggered:
                self.log(f'[{date_str}] band25 未越界（无腿相对偏离±25%），无需再平衡', level='info')
                self._last_rebalance_key = key
                return
            self.log(f'[{date_str}] band25 越界，触发再平衡（全额回归目标权重）', level='info')
        else:
            if max_deviation <= self.params.rebalance_threshold:
                self.log(f'[{date_str}] 偏离 {max_deviation:.2%} <= 阈值 {self.params.rebalance_threshold:.2%}, 无需再平衡', level='info')
                self._last_rebalance_key = key
                return
            self.log(f'[{date_str}] 偏离 {max_deviation:.2%} > 阈值, 触发再平衡', level='info')

        # 计算每个资产的当前/目标值与偏离
        diffs = []  # [(symbol, cur_value, target_value, deviation)]
        for symbol in self.symbols:
            pos_size = self.get_position_size(symbol)
            price = self.get_current_price(symbol)
            if price is None or price <= 0:
                continue
            cur_value = pos_size * price if pos_size > 0 else 0.0
            target_value = total * self.weights[symbol]
            deviation = abs(cur_value - target_value)
            diffs.append((symbol, cur_value, target_value, deviation))

        if rule == 'band25':
            # 全额回归目标权重（order_target_value 语义，不区分越界腿）
            to_trade = [(s, c, t) for s, c, t, d in diffs if abs(c - t) > 0]
            mode = 'band25-full'
        elif self.params.partial_rebalance:
            # 只动偏离超阈值的资产
            to_trade = [(s, c, t) for s, c, t, d in diffs if d > self.params.rebalance_threshold * total]
            mode = 'partial'
        else:
            # 全量再平衡（传统模式）
            to_trade = [(s, c, t) for s, c, t, d in diffs if abs(c - t) > 0]
            mode = 'full'

        # 先卖超配部分
        for symbol, cur_value, target_value in to_trade:
            diff = cur_value - target_value
            if diff <= 0:
                continue
            price = self.get_current_price(symbol)
            if price is None or price <= 0:
                continue
            self._sell_amount(symbol, price, diff)

        # 再买低配部分
        for symbol, cur_value, target_value in to_trade:
            diff = target_value - cur_value
            if diff <= 0:
                continue
            price = self.get_current_price(symbol)
            if price is None or price <= 0:
                continue
            self._buy_amount(symbol, price, diff)

        self._last_rebalance_key = key
        self.log(f'[{date_str}] {label} 再平衡完成（{mode}，交易 {len(to_trade)} 只资产）', level='info')

    # ================================================================
    # 交易辅助（目标权重模式）
    # ================================================================

    def _buy_amount(self, symbol, price, amount):
        """按金额买入（100股整数倍，现金约束）"""
        volume = int(amount / price / 100) * 100
        if volume <= 0:
            return
        cash = self.get_cash()
        if volume * price > cash:
            volume = int(cash * 0.999 / price / 100) * 100
            if volume <= 0:
                return
        self.buy(symbol, price, volume)

    def _sell_amount(self, symbol, price, amount):
        """按金额卖出（100股整数倍，不超过可卖量）"""
        volume = int(amount / price / 100) * 100
        if volume <= 0:
            return
        sellable = self.get_sellable_volume(symbol)
        volume = min(volume, sellable)
        if volume <= 0:
            return
        self.sell(symbol, price, volume)

    def _get_total_value(self):
        """总资产 = 现金 + 持仓市值"""
        cash = self.get_cash()
        position_value = 0.0
        for symbol in self.symbols:
            pos_size = self.get_position_size(symbol)
            if pos_size > 0:
                price = self.get_current_price(symbol)
                if price and price > 0:
                    position_value += pos_size * price
        return cash + position_value

    def _date_str(self) -> str:
        current_date = self.get_current_date()
        if current_date is None:
            return ''
        return current_date.strftime('%Y-%m-%d') if hasattr(current_date, 'strftime') else str(current_date)

    # ================================================================
    # 状态持久化（实盘重启恢复）
    # ================================================================

    def get_state(self) -> dict:
        state = super().get_state()
        state.update({
            'initialized': self._initialized,
            'last_rebalance_key': self._last_rebalance_key,
        })
        return state

    def set_state(self, state: dict):
        super().set_state(state)
        self._initialized = bool(state.get('initialized', False))
        # 兼容旧状态字段 last_rebalance_year
        self._last_rebalance_key = state.get('last_rebalance_key', state.get('last_rebalance_year'))
