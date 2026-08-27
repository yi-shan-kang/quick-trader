# 七星高照ETF轮动策略 - 优化版（项目原生移植版）
# 基于原策略的四项核心改造：
#   1. 降低集中度：持仓1只 → 5只，等分仓位
#   2. 延长回看周期：24天 → 60天，降低短期噪声敏感度
#   3. 清理低流动性标的 + 动态流动性过滤（日均成交额≥1亿）
#   4. QDII溢价处理：回测中直接使用二级市场价格（不获取基金净值）

import numpy as np
import math
from typing import Dict, List, Optional, Tuple
from core.strategy_logic import StrategyLogic, BarData, OrderInfo, TradeInfo
from strategies import register_strategy

# ================== 【ETF池定义】 ==================

ETF_POOL = [
    # 境外（QDII）
    '159941.SZ',  # 纳指ETF
    '159509.SZ',  # 纳指科技ETF
    '513500.SH',  # 标普500ETF
    '513520.SH',  # 日经ETF
    '513030.SH',  # 德国ETF
    '513080.SH',  # 法国ETF
    # 商品
    '518880.SH',  # 黄金ETF
    '159980.SZ',  # 有色ETF
    '161226.SZ',  # 白银ETF
    '159985.SZ',  # 豆粕ETF
    '159981.SZ',  # 能源化工ETF
    '501018.SH',  # 南方原油LOF
    # 债券
    '511090.SH',  # 30年国债ETF
    # 国内
    '513130.SH',  # 恒生科技ETF
    '520500.SH',  # 恒生创新药ETF
    '513970.SH',  # 消费ETF
    '513690.SH',  # 港股红利ETF
    '159915.SZ',  # 创业板ETF
    '563300.SH',  # 中证2000ETF
    '563360.SH',  # 中证A500ETF
    '510410.SH',  # 资源ETF
    '515210.SH',  # 钢铁ETF
    '562800.SH',  # 稀有金属ETF
    '159928.SZ',  # 中证消费ETF
    '512690.SH',  # 中证酒ETF
    '159992.SZ',  # 创新药ETF
    '588220.SH',  # 科创100ETF
    '159819.SZ',  # 人工智能ETF
    '159851.SZ',  # 金融科技ETF
    '515030.SH',  # 新能源车ETF
    '516160.SH',  # 新能源ETF
    '512710.SH',  # 军工ETF
    '515220.SH',  # 煤炭ETF
    '512880.SH',  # 证券ETF
    '516510.SH',  # 云计算ETF
    '515050.SH',  # 5GETF
    '512170.SH',  # 医疗ETF
    '159870.SZ',  # 化工ETF
    '159611.SZ',  # 电力ETF
    '159995.SZ',  # 芯片ETF
    '515790.SH',  # 光伏ETF
    '159755.SZ',  # 电池ETF
    '515000.SH',  # 科技ETF
    '562500.SH',  # 机器人ETF
    '159326.SZ',  # 电网设备ETF
]

# QDII/跨境ETF集合：回测中直接使用二级市场价格计算动量
QDII_ETFS = {
    '159941.SZ',  # 纳指ETF
    '159509.SZ',  # 纳指科技ETF
    '513500.SH',  # 标普500ETF
    '513520.SH',  # 日经ETF
    '513030.SH',  # 德国ETF
    '513080.SH',  # 法国ETF
    '501018.SH',  # 南方原油LOF
    '513130.SH',  # 恒生科技ETF
    '520500.SH',  # 恒生创新药ETF
    '513690.SH',  # 港股红利ETF
}


@register_strategy('seven_star_etf',
                   backtest_config={'cash': 1000000, 'commission': 0.0002,
                                    'open_commission': 0.0002,
                                    'close_commission': 0.0002,
                                    'close_tax': 0.0,
                                    'min_commission': 5.0,
                                    'slippage': 0.001,
                                    'start_date': '2025-01-01', 'end_date': '2025-12-31',
                                    'period': '1d'})
class SevenStarETFRotationStrategy(StrategyLogic):
    """七星高照ETF轮动策略 - 优化版

    基于聚宽七星高照ETF轮动策略移植，四项核心改造：
    1. 降低集中度：持仓1只 → 5只，等分仓位
    2. 延长回看周期：24天 → 60天，降低短期噪声敏感度
    3. 动态流动性过滤：日均成交额≥1亿元
    4. QDII在回测中直接用二级市场价格

    交易逻辑：
    1. 每日调仓，计算所有ETF的动量排名
    2. 选前N只合格ETF + 防御ETF补位
    3. 卖出不在目标列表中的持仓
    4. 等分仓位买入目标ETF（先减仓释放资金，再加仓）
    5. 持仓触发止损（成本价×stop_loss）时卖出并当天不买入
    """

    params = (
        ('holdings_num', 5),
        ('lookback_days', 60),
        ('defensive_etf', '511880.SH'),
        ('min_money', 5000),
        ('liquidity_min_amount', 1e8),
        ('liquidity_lookback', 20),
        ('stop_loss', 0.92),
        ('loss', 0.965),
        ('enable_volume_check', True),
        ('volume_lookback', 5),
        ('volume_threshold', 2.5),
        ('volume_return_limit', 1.0),
        ('use_r2_filter', True),
        ('r2_min_threshold', 0.4),
        ('min_score_threshold', 0.0),
        ('max_score_threshold', 5.0),
        ('rebalance_threshold', 0.05),
        ('half_life', 10),
        ('t_plus_1', False),
    )

    def __init__(self, executor=None, **kwargs):
        super().__init__(executor, **kwargs)

        self.etf_pool = list(ETF_POOL)
        self.qdii_etfs = set(QDII_ETFS)
        self.stopped_etfs: set = set()
        self._cost_cache: Dict[str, float] = {}

        # ETF设为T+0
        for symbol in self.etf_pool:
            self.set_t_plus_1(symbol, False)
        self.set_t_plus_1(self.params.defensive_etf, False)

    def get_symbols(self) -> List[str]:
        """返回ETF池列表（含防御ETF），供回测引擎加载数据"""
        return list(self.etf_pool) + [self.params.defensive_etf]

    # ================== on_bar 主逻辑 ==================

    def on_bar(self, bar: BarData):
        """每日调仓主入口"""
        current_date = self.get_current_date()
        if current_date is None:
            return

        # 1. 止损检查：卖出触发止损的持仓，加入stopped_etfs
        self._check_stop_loss()

        # 2. 计算ETF动量排名
        ranked_etfs = self._get_ranked_etfs()

        # 3. 选目标ETF列表（前N只合格 + 防御ETF补位）
        target_etfs = self._select_target_etfs(ranked_etfs)

        # 4. 卖出不在目标列表中的持仓
        self._sell_non_target(set(target_etfs))

        # 5. 等分仓位调仓（先减仓后加仓）
        self._rebalance_to_target(target_etfs)

    # ================== 止损逻辑 ==================

    def _check_stop_loss(self):
        """检查持仓是否触发止损，触发则卖出并加入stopped_etfs"""
        self.stopped_etfs.clear()

        check_symbols = list(
            set(self.etf_pool) | {self.params.defensive_etf}
        )

        for symbol in check_symbols:
            try:
                pos_size = self.get_position_size(symbol)
                if pos_size <= 0:
                    continue

                current_price = self.get_current_price(symbol)
                if not current_price or current_price <= 0:
                    continue

                cost_price = self._get_avg_cost(symbol)
                if not cost_price or cost_price <= 0:
                    continue

                sellable = self.get_sellable_volume(symbol)
                if sellable <= 0:
                    continue

                stop_loss_price = cost_price * self.params.stop_loss * 1.001
                loss_pct = (current_price / cost_price - 1) * 100

                if current_price <= stop_loss_price:
                    sell_volume = (sellable // 100) * 100
                    if sell_volume <= 0:
                        sell_volume = sellable
                    self.sell(symbol, current_price, sell_volume)
                    self.stopped_etfs.add(symbol)
                    self.log(
                        f'【止损】{symbol} | 成本:{cost_price:.3f} | '
                        f'当前价:{current_price:.3f} | 亏损:{loss_pct:.2f}%',
                        level='info'
                    )
            except Exception as e:
                self.log(f'【止损检查失败】{symbol}: {e}', level='warning')

    # ================== 动量排名 ==================

    def _get_ranked_etfs(self) -> List[Dict]:
        """计算所有ETF的动量排名，返回按得分降序排列的指标列表"""
        etf_metrics = []

        for etf in self.etf_pool:
            if self.is_suspended(etf):
                continue

            metrics = self.calculate_momentum_metrics(etf)
            if metrics is not None:
                if 0 < metrics['score'] < self.params.max_score_threshold:
                    etf_metrics.append(metrics)

        etf_metrics.sort(key=lambda x: x['score'], reverse=True)
        return etf_metrics

    def calculate_momentum_metrics(self, etf: str) -> Optional[Dict]:
        """计算单个ETF的动量指标

        包含：加权对数线性回归年化收益率、R²过滤、近3日大跌过滤、
        流动性过滤、成交量异常放大过滤

        Returns:
            {'etf', 'current_price', 'slope', 'annualized_returns',
             'r_squared', 'score'} 或 None
        """
        try:
            lookback = self.params.lookback_days + 20  # 80天历史 + 当天
            ohlcv = self.get_ohlcv_data(etf, lookback + 1)

            if not ohlcv or len(ohlcv) < self.params.lookback_days:
                return None

            closes = [bar['close'] for bar in ohlcv]
            volumes = [bar['volume'] for bar in ohlcv]
            # money（成交额）= close * volume * 100（volume单位为手=100份）
            moneys = [c * v * 100 for c, v in zip(closes, volumes)]

            # 流动性过滤：日均成交额低于阈值则跳过
            if self.params.liquidity_min_amount > 0:
                # 取最近 liquidity_lookback 天的历史成交额（排除当天）
                hist_moneys = moneys[:-1] if len(moneys) > 1 else moneys
                recent_moneys = hist_moneys[-self.params.liquidity_lookback:]
                if recent_moneys:
                    avg_money = float(np.mean(recent_moneys))
                    if avg_money < self.params.liquidity_min_amount:
                        return None

            current_price = self.get_current_price(etf)
            if not current_price or current_price <= 0:
                current_price = closes[-1]
            if not current_price or current_price <= 0:
                return None

            # 价格序列：历史收盘价 + 当天价格（日线回测中closes[-1]即为当天收盘）
            price_series = np.array(closes, dtype=float)

            # 近3日单日大跌过滤
            if len(price_series) >= 4:
                day1_prev = price_series[-2] if price_series[-2] > 0 else 1
                day2_prev = price_series[-3] if price_series[-3] > 0 else 1
                day3_prev = price_series[-4] if price_series[-4] > 0 else 1

                day1_ratio = price_series[-1] / day1_prev
                day2_ratio = price_series[-2] / day2_prev
                day3_ratio = price_series[-3] / day3_prev

                min_ratio = min(day1_ratio, day2_ratio, day3_ratio)
                if min_ratio < self.params.loss:
                    return None

            # 成交量异常放大过滤（仅对非QDII使用二级市场成交量）
            if (self.params.enable_volume_check
                    and etf not in self.qdii_etfs
                    and len(price_series) > self.params.lookback_days):
                volume_ratio = self._get_volume_ratio(etf)
                volume_annualized = self._get_annualized_returns(
                    price_series, self.params.lookback_days
                )
                if volume_ratio is not None:
                    if volume_annualized > self.params.volume_return_limit:
                        return None

            # 指数衰减加权对数线性回归计算年化收益率
            recent_series = price_series[-(self.params.lookback_days + 1):]
            y = np.log(recent_series)
            x = np.arange(len(y))
            weights = self._get_momentum_weights(len(y))

            slope, intercept = np.polyfit(x, y, 1, w=weights)
            annualized_returns = math.exp(slope * 250) - 1

            ss_res = np.sum(weights * (y - (slope * x + intercept)) ** 2)
            y_wmean = np.average(y, weights=weights)
            ss_tot = np.sum(weights * (y - y_wmean) ** 2)
            r_squared = 1 - ss_res / ss_tot if ss_tot else 0

            # R²过滤
            if self.params.use_r2_filter:
                if not (self.params.r2_min_threshold <= r_squared <= 1):
                    return None

            score = annualized_returns * r_squared

            # 短期风控：近3日有单日大跌则得分清零
            if len(price_series) >= 4:
                day1_ratio = price_series[-1] / price_series[-2]
                day2_ratio = price_series[-2] / price_series[-3]
                day3_ratio = price_series[-3] / price_series[-4]

                if min(day1_ratio, day2_ratio, day3_ratio) < self.params.loss:
                    score = 0

            return {
                'etf': etf,
                'current_price': current_price,
                'slope': slope,
                'annualized_returns': annualized_returns,
                'r_squared': r_squared,
                'score': score,
            }

        except Exception as e:
            self.log(f'计算{etf}动量指标时出错: {e}', level='warning')
            return None

    def _get_volume_ratio(self, symbol: str) -> Optional[float]:
        """计算成交量比率：当日成交量 / 前N日平均成交量

        Returns:
            超过阈值时返回比率值，否则返回None
        """
        try:
            lookback = self.params.volume_lookback
            ohlcv = self.get_ohlcv_data(symbol, lookback + 1)
            if not ohlcv or len(ohlcv) < lookback + 1:
                return None

            volumes = [bar['volume'] for bar in ohlcv]
            # 前N日平均成交量（排除当天）
            hist_volumes = volumes[:-1]
            if len(hist_volumes) < lookback:
                return None

            avg_volume = float(np.mean(hist_volumes[-lookback:]))
            current_volume = volumes[-1]

            if avg_volume <= 0:
                return None

            volume_ratio = current_volume / avg_volume

            if volume_ratio > self.params.volume_threshold:
                return volume_ratio
            return None

        except Exception as e:
            self.log(f'成交量检测失败 {symbol}: {e}', level='warning')
            return None

    @staticmethod
    def _exp_decay_weights(n: int, half_life: int = 10) -> np.ndarray:
        """指数衰减权重：最新数据权重=1，半衰期 half_life 天

        替代原 linspace(1, 2) 线性权重。线性权重对近期数据的偏好极其有限
        （等效样本量≈等权），指数衰减能真正突出近期趋势，同时让远期数据
        的权重自然衰减。
        """
        age = np.arange(n)[::-1]  # 最新=0，最旧=n-1
        return np.exp(-np.log(2) / half_life * age)

    def _get_momentum_weights(self, n: int) -> np.ndarray:
        """获取动量计算权重（指数衰减，半衰期由 params.half_life 控制）"""
        return self._exp_decay_weights(n, half_life=self.params.half_life)

    def _get_annualized_returns(self, price_series: np.ndarray,
                                lookback_days: int) -> float:
        """计算年化收益率（指数衰减加权对数线性回归）"""
        recent = price_series[-(lookback_days + 1):]
        y = np.log(recent)
        x = np.arange(len(y))
        weights = self._get_momentum_weights(len(y))

        slope, _ = np.polyfit(x, y, 1, w=weights)
        return math.exp(slope * 250) - 1

    # ================== 目标ETF选择 ==================

    def _select_target_etfs(self, ranked_etfs: List[Dict]) -> List[str]:
        """选择目标ETF列表：前N只合格ETF + 防御ETF补位"""
        target_etfs = []

        for m in ranked_etfs[:self.params.holdings_num]:
            if m['score'] >= self.params.min_score_threshold:
                etf = m['etf']
                # 排除当天触发止损的ETF
                if etf not in self.stopped_etfs:
                    target_etfs.append(etf)

        # 合格标的不够N只时，用防御ETF补位
        defensive_count = self.params.holdings_num - len(target_etfs)
        if defensive_count > 0 and self._check_defensive_etf_available():
            defensive = self.params.defensive_etf
            if defensive not in self.stopped_etfs:
                for _ in range(defensive_count):
                    target_etfs.append(defensive)

        # 去重（防御ETF可能重复）
        target_etfs = list(dict.fromkeys(target_etfs))

        if target_etfs:
            self.log(
                f'目标ETF列表（{len(target_etfs)}只）: {target_etfs}',
                level='info'
            )
        else:
            self.log('无目标ETF，清仓所有持仓', level='info')

        return target_etfs

    def _check_defensive_etf_available(self) -> bool:
        """检查防御性ETF是否可交易"""
        defensive = self.params.defensive_etf

        if self.is_suspended(defensive):
            self.log(f'防御性ETF {defensive} 今日停牌', level='info')
            return False

        if self.is_limit_up(defensive):
            self.log(f'防御性ETF {defensive} 当前涨停', level='info')
            return False

        if self.is_limit_down(defensive):
            self.log(f'防御性ETF {defensive} 当前跌停', level='info')
            return False

        return True

    # ================== 卖出非目标持仓 ==================

    def _sell_non_target(self, target_set: set):
        """卖出不在目标列表中的持仓"""
        all_symbols = set(self.etf_pool) | {self.params.defensive_etf}

        for symbol in all_symbols:
            if symbol in target_set:
                continue

            pos_size = self.get_position_size(symbol)
            if pos_size <= 0:
                continue

            self._smart_order_target_value(symbol, 0)

    # ================== 等分仓位调仓 ==================

    def _rebalance_to_target(self, target_etfs: List[str]):
        """等分仓位调仓：先减仓释放资金，再加仓"""
        if not target_etfs:
            return

        total_value = self._calculate_total_value()
        if total_value <= 0:
            return

        num_holdings = len(target_etfs)
        target_value_per_etf = total_value / num_holdings

        self.log(
            f'持仓目标：{num_holdings}只，每只目标仓位：'
            f'{target_value_per_etf:.2f}（总资产{total_value:.2f}）',
            level='info'
        )

        # 分离需要减仓和加仓的标的
        sell_first = []
        buy_list = []
        hold_list = []

        for etf in target_etfs:
            pos_size = self.get_position_size(etf)
            current_value = 0
            if pos_size > 0:
                price = self.get_current_price(etf)
                if price and price > 0:
                    current_value = pos_size * price

            if target_value_per_etf > 0:
                diff_ratio = abs(current_value - target_value_per_etf) / target_value_per_etf
            else:
                diff_ratio = 1

            if diff_ratio <= self.params.rebalance_threshold:
                hold_list.append(etf)
            elif current_value > target_value_per_etf:
                sell_first.append(etf)
            else:
                buy_list.append(etf)

        if hold_list:
            self.log(f'仓位接近目标，无需调仓：{hold_list}', level='debug')

        # 先执行减仓（释放资金）
        for etf in sell_first:
            success = self._smart_order_target_value(etf, target_value_per_etf)
            if success:
                self.log(f'减仓至目标: {etf} → {target_value_per_etf:.2f}', level='info')

        # 再执行加仓（使用释放的资金）
        for etf in buy_list:
            success = self._smart_order_target_value(etf, target_value_per_etf)
            if success:
                self.log(f'加仓至目标: {etf} → {target_value_per_etf:.2f}', level='info')
            else:
                # 现金不足时尝试用剩余现金部分买入
                available_cash = self.get_cash()
                if available_cash > self.params.min_money:
                    self.log(
                        f'现金不足全额买入{etf}，尝试用剩余现金'
                        f'{available_cash:.2f}部分买入', level='info'
                    )
                    self._smart_order_target_value(etf, available_cash * 0.999)

    # ================== 智能下单 ==================

    def _smart_order_target_value(self, symbol: str, target_value: float) -> bool:
        """按目标金额调整持仓

        - 下单数量按100股取整
        - 买入前检查现金是否足够（含佣金）
        - 卖出前检查可卖数量（T+0 ETF不受限制）
        """
        if self.is_suspended(symbol):
            self.log(f'{symbol}: 今日停牌，跳过交易', level='info')
            return False

        current_price = self.get_current_price(symbol)
        if not current_price or current_price <= 0:
            self.log(f'{symbol}: 当前价格异常，跳过交易', level='info')
            return False

        # 计算目标数量（按100股取整）
        target_amount = int(target_value / current_price)
        target_amount = (target_amount // 100) * 100
        if target_amount <= 0 and target_value > 0:
            target_amount = 100

        current_amount = self.get_position_size(symbol)
        amount_diff = target_amount - current_amount

        if amount_diff == 0:
            return False

        trade_value = abs(amount_diff) * current_price
        if 0 < trade_value < self.params.min_money:
            self.log(
                f'{symbol}: 交易金额{trade_value:.2f} < '
                f'最小交易额{self.params.min_money}，跳过', level='info'
            )
            return False

        if amount_diff < 0:
            # 卖出
            sellable = self.get_sellable_volume(symbol)
            if sellable <= 0:
                self.log(
                    f'{symbol}: 当天买入不可卖出(T+1)或无持仓',
                    level='info'
                )
                return False

            sell_volume = min(abs(amount_diff), sellable)
            sell_volume = (sell_volume // 100) * 100
            if sell_volume <= 0:
                sell_volume = min(abs(amount_diff), sellable)

            if sell_volume <= 0:
                return False

            self.sell(symbol, current_price, sell_volume)
            self.log(
                f'卖出 {symbol}，数量: {sell_volume}，价格: {current_price:.3f}',
                level='info'
            )
            return True

        else:
            # 买入：检查现金（含佣金）
            required_cash = amount_diff * current_price
            estimated_commission = max(5, required_cash * 0.0002)
            total_required = required_cash + estimated_commission
            available_cash = self.get_cash()

            if total_required > available_cash + 1e-6:
                self.log(
                    f'{symbol}: 现金不足（含佣金），需要'
                    f'{total_required:.2f}，可用{available_cash:.2f}',
                    level='info'
                )
                return False

            self.buy(symbol, current_price, amount_diff)
            self.log(
                f'买入 {symbol}，数量: {amount_diff}，价格: {current_price:.3f}',
                level='info'
            )
            return True

    # ================== 辅助方法 ==================

    def _calculate_total_value(self) -> float:
        """计算总资产（现金 + 持仓市值）"""
        cash = self.get_cash()
        position_value = 0

        all_symbols = set(self.etf_pool) | {self.params.defensive_etf}
        for symbol in all_symbols:
            pos_size = self.get_position_size(symbol)
            if pos_size > 0:
                price = self.get_current_price(symbol)
                if price and price > 0:
                    position_value += pos_size * price

        return cash + position_value

    def _get_avg_cost(self, symbol: str) -> Optional[float]:
        """获取持仓均价 - 优先使用成本缓存，回退到executor查询"""
        if symbol in self._cost_cache:
            cost = self._cost_cache[symbol]
            if cost and cost > 0:
                return cost

        if self.executor and hasattr(self.executor, 'get_position'):
            try:
                pos = self.executor.get_position(symbol)
                if pos:
                    if hasattr(pos, 'avg_price') and pos.avg_price and pos.avg_price > 0:
                        return pos.avg_price
                    if hasattr(pos, 'avg_cost') and pos.avg_cost and pos.avg_cost > 0:
                        return pos.avg_cost
                    if hasattr(pos, 'price') and pos.price and pos.price > 0:
                        return pos.price
            except Exception:
                pass
        return None

    # ================== 事件回调 ==================

    def on_order(self, order: OrderInfo):
        super().on_order(order)

        # 买入成交时更新成本缓存（加权平均）
        if hasattr(order, 'direction') and order.direction == 'buy':
            symbol = order.symbol
            exec_price = getattr(order, 'executed_price', None) or getattr(order, 'price', None)
            if exec_price and exec_price > 0:
                exec_vol = getattr(order, 'executed_volume', 0) or getattr(order, 'volume', 0)
                if exec_vol > 0:
                    old_cost = self._cost_cache.get(symbol, 0)
                    old_size = self.get_position_size(symbol) - exec_vol
                    if old_size > 0 and old_cost > 0:
                        self._cost_cache[symbol] = (
                            (old_cost * old_size + exec_price * exec_vol)
                            / (old_size + exec_vol)
                        )
                    else:
                        self._cost_cache[symbol] = exec_price

        # 卖出清仓时移除成本缓存
        elif hasattr(order, 'direction') and order.direction == 'sell':
            symbol = order.symbol
            remaining = self.get_position_size(symbol)
            if remaining <= 0:
                self._cost_cache.pop(symbol, None)

    def on_trade(self, trade: TradeInfo):
        super().on_trade(trade)

    def on_backtest_end(self):
        """回测结束时清仓"""
        all_symbols = set(self.etf_pool) | {self.params.defensive_etf}
        for symbol in all_symbols:
            pos_size = self.get_position_size(symbol)
            if pos_size > 0:
                price = self.get_current_price(symbol)
                if price and price > 0:
                    sellable = self.get_sellable_volume(symbol)
                    if sellable > 0:
                        self.sell(symbol, price, sellable)
        self.log('回测结束，已清仓所有持仓', level='info')
