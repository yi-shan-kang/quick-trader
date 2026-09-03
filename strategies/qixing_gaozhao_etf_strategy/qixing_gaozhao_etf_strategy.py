# -*- coding: utf-8 -*-
"""七星高照ETF轮动策略（优化版移植）

来源：聚宽文章《【策略分享】近1年5.4倍-七星高照ETF轮动策略》
作者：屌丝逆袭量化  https://www.joinquant.com/post/67371
优化版参考：example/七星高照ETF轮动策略/七星高照_优化版.py

策略逻辑：
1. 每日调仓：加权线性回归动量 + R²趋势稳定性筛选强势ETF
   - 动量得分 = 年化收益率 × R²（指数衰减加权线性回归，半衰期10天）
   - 动量周期：60天（优化版）
2. 多因子过滤：
   - 流动性过滤：近20日日均成交额 < 1亿 剔除
   - 近3日单日跌幅 > 3.5% 剔除
   - 高位放量（量比>2.5 且 年化收益>100%）剔除
   - R² < 0.4 剔除（趋势不稳定）
3. 持仓：前5只合格ETF等权（优化版，原版1只），不足时货币ETF(511880)防御补位
4. 风控：持仓相对成本价跌幅 > 8% 止损卖出（日线框架近似盘中止损）

说明：
- QDII/跨境ETF存在场内溢价，原版使用基金净值计算动量。本项目回测数据源
  （QMT行情）暂无基金净值序列接口，统一使用市场收盘价，结果会略受溢价扰动。
- 原版盘中每分钟止损，日线回测中改为每日开盘检查一次（近似）。
"""

import numpy as np

from core.strategy_logic import StrategyLogic, BarData, OrderInfo, TradeInfo
from strategies import register_strategy
from .config import ETF_POOL, QDII_ETFS, DEFENSIVE_ETF


@register_strategy('qixing_gaozhao',
                   backtest_config={'cash': 100000, 'commission': 0.0002,
                                    'open_commission': 0.0002,
                                    'close_commission': 0.0002,
                                    'close_tax': 0.0,
                                    'min_commission': 5.0,
                                    'start_date': '2020-04-28', 'end_date': '2026-04-28',
                                    'period': '1d',
                                    'benchmark': '000300.SH'})
class QixingGaozhaoStrategy(StrategyLogic):
    """七星高照ETF轮动策略（优化版）

    核心：加权线性回归动量（年化收益×R²）每日排名，前5只等权持仓，
    配合流动性/大跌/放量/R²多因子过滤与止损风控。
    """

    params = (
        ('holdings_num', 5),             # 持仓ETF数量（优化版：1→5）
        ('lookback_days', 60),           # 动量计算周期（优化版：24→60）
        ('defensive_etf', DEFENSIVE_ETF),  # 防御性ETF（货币ETF）
        # 流动性过滤
        ('liquidity_min_amount', 1e8),   # 日均成交额阈值（元），QMT volume 归一化为「手」，成交额≈close×volume×100
        ('liquidity_lookback', 20),      # 流动性检查回看天数
        # 风控
        ('stop_loss', 0.92),             # 固定止损线（下跌8%触发）
        ('loss', 0.965),                 # 近3日单日跌幅止损线（-3.5%）
        # 高位放量过滤
        ('enable_volume_check', True),
        ('volume_lookback', 5),
        ('volume_threshold', 2.5),
        ('volume_return_limit', 1.0),
        # R²筛选
        ('use_r2_filter', True),
        ('r2_min_threshold', 0.4),
        # 得分阈值
        ('min_score_threshold', 0.0),
        ('max_score_threshold', 5.0),
        # 调仓容差
        ('rebalance_threshold', 0.05),   # 偏离目标仓位5%以内不调仓
    )

    def __init__(self, executor=None, **kwargs):
        super().__init__(executor, **kwargs)
        self.etf_pool = list(ETF_POOL.values())
        self.qdii_etfs = QDII_ETFS
        self.etf_names = {code: name for name, code in ETF_POOL.items()}
        # 持仓成本跟踪（用于止损）
        self._cost_price = {}
        self._cost_size = {}
        # ETF设为T+0
        for symbol in self.etf_pool + [self.params.defensive_etf]:
            self.set_t_plus_1(symbol, False)

    def get_symbols(self):
        return list(self.etf_pool) + [self.params.defensive_etf]

    # ================================================================
    # 主流程
    # ================================================================

    def on_bar(self, bar: BarData):
        current_date = self.get_current_date()
        if current_date is None:
            return
        date_str = current_date.strftime('%Y-%m-%d') if hasattr(current_date, 'strftime') else str(current_date)

        # 1. 止损检查（每日开盘，近似原版盘中止损）
        self._check_stop_loss(date_str)

        # 2. 排名与调仓
        ranked = self._get_ranked_etfs()
        self._log_top_rank(ranked, date_str)
        self._rebalance(ranked, date_str)

    # ================================================================
    # 止损
    # ================================================================

    def _check_stop_loss(self, date_str: str):
        for symbol in list(self._cost_price.keys()):
            pos_size = self.get_position_size(symbol)
            if pos_size <= 0:
                self._clear_cost(symbol)
                continue
            cost = self._cost_price.get(symbol, 0)
            if cost <= 0:
                continue
            price = self.get_current_price(symbol)
            if price is None or price <= 0:
                continue
            if price <= cost * self.params.stop_loss:
                sellable = self.get_sellable_volume(symbol)
                if sellable > 0:
                    self.sell(symbol, price, sellable)
                    name = self.etf_names.get(symbol, symbol)
                    self.log(f'[{date_str}] 止损卖出: {name} ({symbol}) 成本{cost:.3f} 现价{price:.3f} '
                             f'跌幅{(price / cost - 1) * 100:.2f}%')
                else:
                    self.log(f'[{date_str}] 止损触发但无可卖持仓: {symbol}', level='warning')

    # ================================================================
    # 排名计算
    # ================================================================

    def _get_ranked_etfs(self):
        """计算全部ETF动量得分并降序排列"""
        results = []
        for etf in self.etf_pool:
            metrics = self._calc_momentum_metrics(etf)
            if metrics is not None:
                score = metrics['score']
                if 0 < score < self.params.max_score_threshold:
                    results.append(metrics)
        results.sort(key=lambda x: x['score'], reverse=True)
        return results

    def _calc_momentum_metrics(self, etf):
        """计算单只ETF动量指标

        Returns:
            {'etf','annualized_returns','r_squared','score'} 或 None
        """
        closes = self.get_close_prices(etf)
        if len(closes) < self.params.lookback_days + 1:
            return None
        price_series = np.array(closes[-(self.params.lookback_days + 1):], dtype=float)
        if np.any(price_series <= 0):
            return None

        # 流动性过滤（成交额≈收盘价×成交量×100，QMT volume 归一化为「手」）
        if self.params.liquidity_min_amount > 0:
            ohlcv = self.get_ohlcv_data(etf, period=self.params.liquidity_lookback + 5)
            if len(ohlcv) >= self.params.liquidity_lookback:
                amounts = [b['close'] * b['volume'] * 100 for b in ohlcv[-self.params.liquidity_lookback:]
                           if b.get('volume', 0) > 0 and b.get('close', 0) > 0]
                if amounts:
                    avg_amount = np.mean(amounts)
                    if avg_amount < self.params.liquidity_min_amount:
                        return None

        # 近3日单日大跌过滤
        if len(price_series) >= 4:
            r1 = price_series[-1] / price_series[-2]
            r2 = price_series[-2] / price_series[-3]
            r3 = price_series[-3] / price_series[-4]
            if min(r1, r2, r3) < self.params.loss:
                return None

        # 长期动量：指数衰减加权线性回归
        y = np.log(price_series)
        x = np.arange(len(y), dtype=float)
        weights = self._exp_decay_weights(len(y))
        slope, intercept = np.polyfit(x, y, 1, w=weights)
        annualized_returns = np.exp(slope * 250) - 1

        ss_res = np.sum(weights * (y - (slope * x + intercept)) ** 2)
        y_wmean = np.average(y, weights=weights)
        ss_tot = np.sum(weights * (y - y_wmean) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot else 0

        # R²趋势稳定性过滤
        if self.params.use_r2_filter:
            if not (self.params.r2_min_threshold <= r_squared <= 1):
                return None

        # 高位放量过滤（仅非QDII，QDII成交量过滤意义不大）
        if self.params.enable_volume_check and etf not in self.qdii_etfs:
            ohlcv = self.get_ohlcv_data(etf, period=self.params.volume_lookback + 2)
            if len(ohlcv) >= self.params.volume_lookback + 1:
                vols = [b.get('volume', 0) for b in ohlcv[-(self.params.volume_lookback + 1):]]
                today_vol = vols[-1]
                avg_vol = np.mean(vols[:-1]) if len(vols[:-1]) > 0 else 0
                if avg_vol > 0 and today_vol / avg_vol > self.params.volume_threshold:
                    if annualized_returns > self.params.volume_return_limit:
                        return None

        score = annualized_returns * r_squared
        return {
            'etf': etf,
            'annualized_returns': annualized_returns,
            'r_squared': r_squared,
            'score': score,
        }

    @staticmethod
    def _exp_decay_weights(n, half_life=10):
        """指数衰减权重：最新数据权重=1，半衰期 half_life 天"""
        age = np.arange(n)[::-1]  # 最新=0，最旧=n-1
        return np.exp(-np.log(2) / half_life * age)

    def _log_top_rank(self, ranked, date_str: str):
        """记录排名前5（调试信息）"""
        if not ranked:
            return
        parts = []
        for m in ranked[:5]:
            name = self.etf_names.get(m['etf'], m['etf'])
            tag = ' [QDII]' if m['etf'] in self.qdii_etfs else ''
            parts.append(f"{name}:{m['score']:.3f}{tag}")
        self.log(f'[{date_str}] 动量排名前5: ' + ' | '.join(parts), level='info')

    # ================================================================
    # 调仓
    # ================================================================

    def _rebalance(self, ranked, date_str: str):
        """卖出非目标持仓，等权买入目标持仓"""
        # 目标列表：前N只合格ETF + 防御ETF补位
        target = []
        for m in ranked[:self.params.holdings_num]:
            if m['score'] >= self.params.min_score_threshold:
                target.append(m['etf'])
        defensive_count = self.params.holdings_num - len(target)
        if defensive_count > 0:
            for _ in range(defensive_count):
                target.append(self.params.defensive_etf)
        target = list(dict.fromkeys(target))
        target_set = set(target)

        # 卖出不在目标中的持仓
        for symbol in self.get_symbols():
            pos_size = self.get_position_size(symbol)
            if pos_size > 0 and symbol not in target_set:
                sellable = self.get_sellable_volume(symbol)
                if sellable <= 0:
                    continue
                price = self.get_current_price(symbol)
                if price is None or price <= 0:
                    continue
                self.sell(symbol, price, sellable)
                name = self.etf_names.get(symbol, symbol)
                self.log(f'[{date_str}] 卖出非目标: {name} ({symbol}) {sellable}股@{price:.3f}')

        # 等权调仓
        total_value = self._get_total_value()
        if total_value <= 0:
            return
        per_target = total_value / len(target)

        for symbol in target:
            price = self.get_current_price(symbol)
            if price is None or price <= 0:
                continue
            current_value = self.get_position_size(symbol) * price
            diff = per_target - current_value

            # 容差内不调仓
            if per_target > 0 and abs(diff) / per_target <= self.params.rebalance_threshold:
                continue

            if diff > 0:
                self._buy_amount(symbol, price, diff)
            else:
                self._sell_amount(symbol, price, -diff)

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
        name = self.etf_names.get(symbol, symbol)
        self.log(f'买入: {name} ({symbol}) {volume}股@{price:.3f}')

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
        name = self.etf_names.get(symbol, symbol)
        self.log(f'卖出: {name} ({symbol}) {volume}股@{price:.3f}')

    def _get_total_value(self):
        """总资产 = 现金 + 持仓市值"""
        cash = self.get_cash()
        position_value = 0
        for symbol in self.get_symbols():
            pos_size = self.get_position_size(symbol)
            if pos_size > 0:
                price = self.get_current_price(symbol)
                if price and price > 0:
                    position_value += pos_size * price
        return cash + position_value

    # ================================================================
    # 持仓成本跟踪（用于止损）
    # ================================================================

    def on_trade(self, trade: TradeInfo):
        super().on_trade(trade)
        symbol = trade.symbol
        if trade.is_buy:
            old_size = self._cost_size.get(symbol, 0)
            old_cost = self._cost_price.get(symbol, 0.0)
            new_size = old_size + trade.volume
            if new_size > 0:
                self._cost_price[symbol] = (old_cost * old_size + trade.price * trade.volume) / new_size
                self._cost_size[symbol] = new_size
        else:
            new_size = max(0, self._cost_size.get(symbol, 0) - trade.volume)
            if new_size <= 0:
                self._clear_cost(symbol)
            else:
                self._cost_size[symbol] = new_size

    def _clear_cost(self, symbol):
        self._cost_price.pop(symbol, None)
        self._cost_size.pop(symbol, None)

    def on_order(self, order: OrderInfo):
        super().on_order(order)
