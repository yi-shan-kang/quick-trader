from typing import Dict, List
from core.stock_selection import StockSelectionStrategy
from strategies import register_strategy


@register_strategy('small_cap', default_kwargs={'max_stocks': 30},
                   backtest_config={'cash': 1000000, 'commission': 0.0001,
                                    'start_date': '2016-01-01', 'end_date': '2026-04-17'})
class SmallCapStrategy(StockSelectionStrategy):
    """小市值策略 - 纯市值排序 + 基础过滤 + 月历空仓

    按《A股小市值策略手册（公开版）》口径实现：
    1. 基础过滤：上市满1年 + 非ST/*ST + 非科创/北交所 + 换仓日非涨停
    2. 月历效应：1月、4月空仓（小市值财务暴雷/被ST高发月）
    3. 数据质量门禁：价格异常/停牌/股本缺失剔除
    4. 市值上限：30亿（剔除壳资源炒作中的高市值小票）
    5. 等权重持仓30只，月度调仓

    优化记录（2015-2025全指，基线夏普1.29→优化后1.375）：
    - 采用：市值上限30亿(max_market_cap=30) + 持仓30只(max_stocks=30)
    - 放弃：波动率过滤/止损/动量过滤/ROE过滤/双周调仓/行业分散（夏普均<基线+5%）
    """

    params = (
        ('rebalance_freq', 'monthly'),
        ('max_stocks', 30),          # 优化：20→30只（组合夏普+6.3%）
        ('position_ratio', 0.95),
        ('stock_pool', None),
        ('max_market_cap', 30),      # 优化：市值上限30亿（剔除高市值小票，夏普+5.6%）
        # 基础过滤参数（手册口径）
        ('min_list_days', 365),      # 上市满1年
        ('exclude_st', True),        # 剔除ST/*ST
        ('exclude_kcb_bse', True),   # 剔除科创板(688)/北交所(8xx/4xx/920)
        ('exclude_limit_up', True),  # 换仓日非涨停
        # 月历效应参数
        ('skip_months', (1, 4)),     # 1月、4月空仓
        # 数据质量门禁参数
        ('max_stale_days', 20),      # 最近行情距调仓日超过20自然日视为停牌/退市，剔除
    )

    def __init__(self, executor=None, **kwargs):
        super().__init__(executor, **kwargs)
        self._st_cache: Dict[str, bool] = {}
        self._st_cache_date = None

    def is_rebalance_day(self, current_date) -> bool:
        """月历效应：1月、4月空仓（直接不触发调仓，保持空仓状态）"""
        skip = getattr(self.params, 'skip_months', ())
        if skip and current_date.month in skip:
            # 若当前有持仓且刚进入空仓月，清仓
            if self._current_holdings:
                self.log(f'月历空仓: {current_date.month}月，清仓 {len(self._current_holdings)} 只')
                self._sell_all()
            self._last_rebalance_date = current_date
            return False
        return super().is_rebalance_day(current_date)

    def select_stocks(self) -> List[str]:
        pool = self.get_stock_pool()

        # 基础过滤（手册口径）：上市满1年 + 非ST + 非科创北交所 + 非涨停
        pool = self._basic_filter(pool)
        if not pool:
            self.log('基础过滤后无股票')
            return []

        market_caps = self._calc_market_caps(pool)
        if not market_caps:
            self.log('无法计算市值')
            return []
        self.log(f'市值计算: {len(market_caps)} 只有效市值数据')

        if self.params.max_market_cap is not None:
            cap_limit = self.params.max_market_cap * 1e8
            before = len(market_caps)
            market_caps = {s: v for s, v in market_caps.items() if v <= cap_limit}
            self.log(f'市值上限过滤: {before} -> {len(market_caps)} 只 (上限{self.params.max_market_cap}亿)')

        if not market_caps:
            self.log('过滤后无股票')
            return []

        sorted_stocks = sorted(market_caps.items(), key=lambda x: x[1])
        max_stocks = self.params.max_stocks
        selected = [stock for stock, _ in sorted_stocks[:max_stocks]]

        self.log(f'按市值排序选股: 选中 {len(selected)} 只')
        for stock, mc in sorted_stocks[:max_stocks]:
            self.log(f'  {stock} | 市值: {mc / 1e8:.2f}亿')

        return selected

    def _basic_filter(self, pool: List[str]) -> List[str]:
        """基础过滤（手册口径）

        1) 上市满1年（min_list_days）
        2) 非ST/*ST（exclude_st）
        3) 非科创板(688)/北交所(8xx/4xx/920)（exclude_kcb_bse）
        4) 换仓日非涨停（exclude_limit_up）
        """
        import datetime as dt
        from core.stock_lifecycle import get_lifecycle_manager

        min_days = getattr(self.params, 'min_list_days', 365)
        ex_st = getattr(self.params, 'exclude_st', True)
        ex_kcb = getattr(self.params, 'exclude_kcb_bse', True)
        ex_lu = getattr(self.params, 'exclude_limit_up', True)
        current_date = self.get_current_date()
        lifecycle = get_lifecycle_manager()

        kept = []
        n_list = n_st = n_kcb = n_lu = 0

        for s in pool:
            code = s.split('.')[0] if '.' in s else s

            # 3) 科创/北交所（代码前缀即可判断，最优先最便宜）
            if ex_kcb:
                if code.startswith(('688', '689')) or s.endswith('.BJ') or \
                   code.startswith(('8', '4', '920')):
                    n_kcb += 1
                    continue

            # 1) 上市满1年
            if min_days and current_date:
                ld = lifecycle.get_list_date(s)
                if ld:
                    try:
                        list_dt = dt.datetime.strptime(ld, '%Y-%m-%d').date()
                        cur = current_date if isinstance(current_date, dt.date) else current_date.date()
                        if (cur - list_dt).days < min_days:
                            n_list += 1
                            continue
                    except Exception:
                        pass  # 日期解析失败不剔除

            # 2) 非ST/*ST（按日缓存名称判断）
            if ex_st and self._is_st_stock(s):
                n_st += 1
                continue

            # 4) 换仓日非涨停
            if ex_lu and self.is_limit_up(s):
                n_lu += 1
                continue

            kept.append(s)

        self.log(f'基础过滤: {len(pool)} -> {len(kept)} 只 '
                 f'(次新={n_list}, ST={n_st}, 科创北交={n_kcb}, 涨停={n_lu})')
        return kept

    def _is_st_stock(self, symbol: str) -> bool:
        """判断是否ST/*ST（按日缓存，通过QMT instrument_detail的InstrumentName）"""
        current_date = self.get_current_date()
        if current_date != self._st_cache_date:
            self._st_cache.clear()
            self._st_cache_date = current_date

        if symbol in self._st_cache:
            return self._st_cache[symbol]

        is_st = False
        try:
            if self._data_processor and hasattr(self._data_processor, 'get_instrument_detail'):
                detail = self._data_processor.get_instrument_detail(symbol)
                if detail:
                    name = detail.get('InstrumentName', '') or ''
                    is_st = 'ST' in name.upper()
        except Exception:
            pass
        self._st_cache[symbol] = is_st
        return is_st

    def _calc_market_caps(self, stocks: List[str]) -> Dict[str, float]:
        """计算市值 = 总股本 × 当前股价

        总股本 = 所有者权益合计 / 每股净资产（两者缺一不可）。
        数据质量门禁（针对脏票问题）：
        1. 价格异常（None / 非正 / 极小值 <1元）剔除
        2. 停牌/退市股（最近行情距调仓日超过 max_stale_days 天）剔除，避免资金被锁死
        3. 不再使用"净资产当市值"回退：bps缺失/非正的股票直接剔除，
           否则财务垃圾股（净资产几百万、bps为负）会被当成"最小市值"反复选中
        """
        import datetime as dt

        result = {}
        no_price = 0
        no_cap_data = 0
        stale_count = 0
        current_date = self.get_current_date()
        max_stale = dt.timedelta(days=getattr(self.params, 'max_stale_days', 20))

        for stock in stocks:
            # 门禁1：价格异常剔除
            price = self.get_unadjusted_price(stock)
            if price is None or price < 1.0:
                no_price += 1
                continue

            # 门禁2：停牌/退市剔除（最近行情日期陈旧）
            last_td = self.get_last_trade_date(stock)
            if current_date is not None and last_td is not None:
                if (current_date - last_td) > max_stale:
                    stale_count += 1
                    continue

            total_equity = self.get_financial_field(stock, 'Balance', 'total_equity')
            bps = self.get_financial_field(stock, 'Pershareindex', 's_fa_bps')

            # 门禁3：股本数据必须完整有效，否则剔除（不再回退到净资产）
            if total_equity and total_equity > 0 and bps and bps > 0:
                total_shares = total_equity / bps
                market_cap = total_shares * price
                result[stock] = market_cap
            else:
                no_cap_data += 1

        if no_price > 0 or no_cap_data > 0 or stale_count > 0:
            self.log(f'市值计算: {len(stocks)} 只 -> {len(result)} 只有效数据 '
                     f'(无价格={no_price}, 停牌/退市={stale_count}, 无股本数据={no_cap_data})')

        return result
