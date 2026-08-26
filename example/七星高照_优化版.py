# 七星高照ETF轮动策略 - 优化版
# 基于原策略的四项核心改造：
#   1. 降低集中度：持仓1只 → 5只，等分仓位
#   2. 延长回看周期：24天 → 60天，降低短期噪声敏感度
#   3. 清理低流动性标的 + 动态流动性过滤（日均成交额≥1亿）
#   4. QDII溢价处理：跨境ETF使用基金净值计算动量，避免溢价干扰

import numpy as np
import math
import pandas as pd
from jqdata import *
from datetime import time, timedelta

# ================== 【全局静态常量】==================

ETF_POOL_DEF = [
    # 境外（QDII）
    "159941.XSHE", #纳指ETF
    "159509.XSHE", #纳指科技ETF
    "513500.XSHG", #标普500ETF
    "513520.XSHG", #日经ETF
    "513030.XSHG", #德国ETF
    "513080.XSHG", #法国ETF
    # 商品
    "518880.XSHG", #黄金ETF
    "159980.XSHE", #有色ETF
    "161226.XSHE", #白银ETF
    "159985.XSHE", #豆粕ETF
    "159981.XSHE", #能源化工ETF
    "501018.XSHG", #南方原油LOF
    # 债券
    "511090.XSHG", #30年国债ETF
    # 国内
    "513130.XSHG", #恒生科技ETF
    "520500.XSHG", #恒生创新药ETF
    "513970.XSHG", #消费ETF
    "513690.XSHG", #港股红利ETF
    "159915.XSHE", #创业板ETF
    "563300.XSHG", #中证2000ETF
    "563360.XSHG", #中证A500ETF
    "510410.XSHG", #资源ETF
    "515210.XSHG", #钢铁ETF
    "562800.XSHG", #稀有金属ETF
    "159928.XSHE", #中证消费ETF
    "512690.XSHG", #中证酒ETF
    "159992.XSHE", #创新药ETF
    "588220.XSHG", #科创100ETF
    "159819.XSHE", #人工智能ETF
    "159851.XSHE", #金融科技ETF
    "515030.XSHG", #新能源车ETF
    "516160.XSHG", #新能源ETF
    "512710.XSHG", #军工ETF
    "515220.XSHG", #煤炭ETF
    "512880.XSHG", #证券ETF
    "516510.XSHG", #云计算ETF
    "515050.XSHG", #5GETF
    "512170.XSHG", #医疗ETF
    "159870.XSHE", #化工ETF
    "159611.XSHE", #电力ETF
    "159995.XSHE", #芯片ETF
    "515790.XSHG", #光伏ETF
    "159755.XSHE", #电池ETF
    "515000.XSHG", #科技ETF
    "562500.XSHG", #机器人ETF
    "159326.XSHE", #电网设备ETF
]

# QDII/跨境ETF集合：这些ETF存在场内溢价，动量计算使用基金净值
QDII_ETFS_DEF = {
    "159941.XSHE", #纳指ETF
    "159509.XSHE", #纳指科技ETF
    "513500.XSHG", #标普500ETF
    "513520.XSHG", #日经ETF
    "513030.XSHG", #德国ETF
    "513080.XSHG", #法国ETF
    "501018.XSHG", #南方原油LOF
    "513130.XSHG", #恒生科技ETF
    "520500.XSHG", #恒生创新药ETF
    "513690.XSHG", #港股红利ETF
}

# ============== 策略参数默认值（_DEF后缀） ==============

# 动量计算参数
HOLDINGS_NUM_DEF = 5            # 【改造1】持仓ETF数量：1→5，降低集中度
LOOKBACK_DAYS_DEF = 60          # 【改造2】动量计算周期：24→60，降低短期噪声敏感度
DEFENSIVE_ETF_DEF = "511880.XSHG"  # 防御性ETF（货币ETF）
MIN_MONEY_DEF = 5000            # 最小交易金额

# 【改造3】流动性过滤参数
LIQUIDITY_MIN_AMOUNT_DEF = 1e8  # 日均成交额最低阈值：1亿元
LIQUIDITY_LOOKBACK_DEF = 20     # 流动性检查回看天数

# 风险控制参数
STOP_LOSS_DEF = 0.92            # 固定止损线（下调至8%，适配多标的+长周期）
LOSS_DEF = 0.965                # 近3日单日跌幅止损线

# 成交量过滤参数
ENABLE_VOLUME_CHECK_DEF = True
VOLUME_LOOKBACK_DEF = 5
VOLUME_THRESHOLD_DEF = 2.5
VOLUME_RETURN_LIMIT_DEF = 1

# R²筛选参数
USE_R2_FILTER_DEF = True
R2_MIN_THRESHOLD_DEF = 0.4

# 得分阈值
MIN_SCORE_THRESHOLD_DEF = 0.0
MAX_SCORE_THRESHOLD_DEF = 5.0

# 仓位调整容差
REBALANCE_THRESHOLD_DEF = 0.05  # 偏离目标仓位5%以上才调仓

# =================== 【初始化函数】 =====================

def initialize(context):

    g.context = context

    g.etf_pool = ETF_POOL_DEF
    g.qdii_etfs = QDII_ETFS_DEF

    log.set_level('order', 'error')
    log.set_level('system', 'error')
    log.set_level('strategy', 'info')

    set_option("avoid_future_data", True)
    set_option("use_real_price", True)

    # 【改造】提高滑点至0.1%，更接近实盘
    set_slippage(PriceRelatedSlippage(0.001), type="fund")

    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=0,
            open_commission=0.0002,
            close_commission=0.0002,
            close_today_commission=0,
            min_commission=5,
        ),
        type="fund",
    )

    set_benchmark("000300.XSHG")

    # 动量计算参数
    g.lookback_days = LOOKBACK_DAYS_DEF
    g.holdings_num = HOLDINGS_NUM_DEF
    g.defensive_etf = DEFENSIVE_ETF_DEF
    g.min_money = MIN_MONEY_DEF

    # 流动性过滤参数
    g.liquidity_min_amount = LIQUIDITY_MIN_AMOUNT_DEF
    g.liquidity_lookback = LIQUIDITY_LOOKBACK_DEF

    # 风险控制参数
    g.stop_loss = STOP_LOSS_DEF
    g.loss = LOSS_DEF
    g.stopped_etfs = set()

    # 成交量过滤参数
    g.enable_volume_check = ENABLE_VOLUME_CHECK_DEF
    g.volume_lookback = VOLUME_LOOKBACK_DEF
    g.volume_threshold = VOLUME_THRESHOLD_DEF
    g.volume_return_limit = VOLUME_RETURN_LIMIT_DEF

    # R²筛选参数
    g.use_r2_filter = USE_R2_FILTER_DEF
    g.r2_min_threshold = R2_MIN_THRESHOLD_DEF

    # 得分阈值
    g.min_score_threshold = MIN_SCORE_THRESHOLD_DEF
    g.max_score_threshold = MAX_SCORE_THRESHOLD_DEF

    # 仓位调整容差
    g.rebalance_threshold = REBALANCE_THRESHOLD_DEF

    # 持仓管理
    g.positions = {}

    # 【改造】排名缓存：卖出函数计算的排名供买入函数复用，避免1分钟内排名变化导致不一致
    g.cached_ranked_etfs = None
    g.cached_rank_dt = None

    # 交易调度
    run_daily(check_positions, time='09:25')
    run_daily(check_stop_loss_minutely, time='every_bar')
    run_daily(etf_sell_trade, time='14:00')
    run_daily(etf_buy_trade, time='14:01')

    log.info(f"""策略参数初始化完成（优化版）:
    - ETF池大小: {len(g.etf_pool)} 只 | QDII标的: {len(g.qdii_etfs)} 只
    - 持仓数量: {g.holdings_num} 只 | 动量周期: {g.lookback_days} 天
    - 流动性门槛: {g.liquidity_min_amount/1e4:.0f}万/日
    - 止损阈值: 下跌{(1-g.stop_loss)*100:.0f}%触发
""")

# ============ 持仓检查 ===============
def check_positions(context):
    current_data = get_current_data()
    for security in context.portfolio.positions:
        position = context.portfolio.positions[security]
        if position.total_amount > 0:
            security_name = get_security_name(security)
            log.debug(f"持仓检查: {security} {security_name}, 数量: {position.total_amount}, 成本: {position.avg_cost:.3f}, 当前价: {position.price:.3f}")
            if current_data[security].paused:
                log.info(f"{security} {security_name} 今日停牌")

# ================== 盘中实时止损 ==================
def check_stop_loss_minutely(context):
    current_time = context.current_dt.time()
    if not ((time(9, 30) <= current_time <= time(11, 30)) or
        (time(13, 00) <= current_time <= time(15, 00))):
        return

    check_secs = [s for s in context.portfolio.positions if s in g.etf_pool or s == g.defensive_etf]

    for security in check_secs:
        try:
            pos = context.portfolio.positions[security]
            if pos.total_amount <= 0:
                continue

            current_price = pos.price
            cost_price = pos.avg_cost
            security_name = get_security_name(security)

            if cost_price <= 0:
                continue

            if pos.closeable_amount <= 0:
                continue

            stop_loss_price = cost_price * g.stop_loss * 1.001
            loss_pct = (current_price / cost_price - 1) * 100

            if current_price <= stop_loss_price:
                success = smart_order_target_value(security, 0, context)
                if success:
                    g.stopped_etfs.add(security)
                    log.info(f"【实时止损】{security} {security_name} | 成本：{cost_price:.3f} | 当前价：{current_price:.3f} | 亏损：{loss_pct:.2f}%")
                else:
                    log.error(f"【止损失败】{security} {security_name} | 亏损：{loss_pct:.2f}%")

        except Exception as e:
            log.error(f"【止损检查失败】{security}：{e}")

# ==================== 卖出函数 ====================
def etf_sell_trade(context):
    log.info("======================== 卖出操作开始 ========================")

    # 计算排名并缓存，供买入函数复用
    ranked_etfs = get_ranked_etfs(context)
    g.cached_ranked_etfs = ranked_etfs
    g.cached_rank_dt = context.current_dt

    # 【改造】多标的：选前N只合格ETF作为目标
    target_etfs = []
    for m in ranked_etfs[:g.holdings_num]:
        if m['score'] >= g.min_score_threshold:
            target_etfs.append(m['etf'])

    # 合格标的不够N只时，用防御ETF补位
    defensive_count = g.holdings_num - len(target_etfs)
    if defensive_count > 0 and check_defensive_etf_available(context):
        for _ in range(defensive_count):
            target_etfs.append(g.defensive_etf)

    # 去重（防御ETF可能重复）
    target_etfs_unique = list(dict.fromkeys(target_etfs))
    target_etfs_set = set(target_etfs_unique)

    if target_etfs_unique:
        log.info(f"目标ETF列表（{len(target_etfs_unique)}只）：{[get_security_name(e) for e in target_etfs_unique]}")
    else:
        log.info("无目标ETF，清仓所有持仓")

    # 卖出不在目标列表中的持仓
    current_positions = list(context.portfolio.positions.keys())
    for security in current_positions:
        if (security in g.etf_pool or security == g.defensive_etf) and security not in target_etfs_set:
            position = context.portfolio.positions[security]
            if position.total_amount > 0:
                security_name = get_security_name(security)
                success = smart_order_target_value(security, 0, context)
                if success:
                    log.debug(f"卖出非目标持仓: {security} {security_name}")
                else:
                    log.warning(f"卖出失败：{security} {security_name}")

    log.info("======================== 卖出操作完成 ========================")

# ==================== 获取ETF排名函数 ====================
def get_ranked_etfs(context):
    etf_metrics = []
    filtered_pool = g.etf_pool
    current_data = get_current_data()

    for etf in filtered_pool:
        if current_data[etf].paused:
            continue

        metrics = calculate_momentum_metrics(context, etf)
        if metrics is not None:
            if 0 < metrics['score'] < g.max_score_threshold:
                etf_metrics.append(metrics)
            else:
                log.debug(f"{etf}: 得分{metrics['score']:.4f}不在有效范围")

    etf_metrics.sort(key=lambda x: x['score'], reverse=True)
    return etf_metrics

# ==================== 动量指标计算函数 ====================
def calculate_momentum_metrics(context, etf):
    try:
        lookback = g.lookback_days + 20
        prices = attribute_history(etf, lookback, '1d', ['close', 'high', 'money'])

        if prices.empty or len(prices) < g.lookback_days:
            log.debug(f"{etf}: 历史数据不足（{len(prices)}天）")
            return None

        # 【改造3】流动性过滤：日均成交额低于阈值则跳过
        if g.liquidity_min_amount > 0 and 'money' in prices.columns:
            avg_money = prices['money'].tail(g.liquidity_lookback).mean()
            if avg_money < g.liquidity_min_amount:
                log.debug(f"{etf}: 日均成交额{avg_money/1e4:.0f}万 < {g.liquidity_min_amount/1e4:.0f}万，流动性不足")
                return None

        current_data = get_current_data()
        current_price = current_data[etf].last_price
        if current_price <= 0:
            log.debug(f"{etf}: 实时价格异常")
            return None

        # 【改造4】QDII ETF使用基金净值计算动量，避免场内溢价干扰
        # 交易下单仍使用二级市场实时价格（在 smart_order_target_value 中处理）
        if etf in g.qdii_etfs:
            nav_series = get_qdii_nav_series(context, etf, lookback)
            if nav_series is not None and len(nav_series) >= g.lookback_days:
                price_series = nav_series
            else:
                # 基金净值获取失败，回退到二级市场价格
                price_series = np.append(prices["close"].values, current_price)
        else:
            price_series = np.append(prices["close"].values, current_price)

        # 近3日单日大跌过滤
        if len(price_series) >= 4:
            day1_prev = price_series[-2] if price_series[-2] > 0 else 1
            day2_prev = price_series[-3] if price_series[-3] > 0 else 1
            day3_prev = price_series[-4] if price_series[-4] > 0 else 1

            day1_ratio = price_series[-1] / day1_prev
            day2_ratio = price_series[-2] / day2_prev
            day3_ratio = price_series[-3] / day3_prev

            min_ratio = min(day1_ratio, day2_ratio, day3_ratio)
            if min_ratio < g.loss:
                threshold_percent = (1 - g.loss) * 100
                max_loss_percent = (1 - min_ratio) * 100
                log.debug(f"{etf}: 近3日最大跌幅{max_loss_percent:.2f}% > {threshold_percent:.2f}%阈值，过滤")
                return None

        # 成交量过滤（仅对非QDII使用二级市场成交量；QDII的成交量过滤意义不大，跳过）
        if g.enable_volume_check and etf not in g.qdii_etfs and len(price_series) > g.lookback_days:
            volume_ratio = get_volume_ratio(context, etf)
            volume_annualized = get_annualized_returns(price_series, g.lookback_days)
            if volume_ratio is not None:
                if volume_annualized > g.volume_return_limit:
                    log.debug(f"{etf}: 高位放量（{volume_ratio:.2f}倍），年化{volume_annualized:.2f}，过滤")
                    return None

        # 长期动量计算
        recent_price_series = price_series[-(g.lookback_days + 1):]
        y = np.log(recent_price_series)
        x = np.arange(len(y))
        weights = np.linspace(1, 2, len(y))

        slope, intercept = np.polyfit(x, y, 1, w=weights)
        annualized_returns = math.exp(slope * 250) - 1

        ss_res = np.sum(weights * (y - (slope * x + intercept)) ** 2)
        ss_tot = np.sum(weights * (y - np.mean(y)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot else 0

        if g.use_r2_filter:
            if not (g.r2_min_threshold <= r_squared <= 1):
                log.debug(f"{etf}: R²={r_squared:.4f} < {g.r2_min_threshold}，过滤")
                return None

        score = annualized_returns * r_squared

        # 短期风控：近3日有单日大跌则得分清零
        if len(price_series) >= 4:
            day1_ratio = price_series[-1] / price_series[-2]
            day2_ratio = price_series[-2] / price_series[-3]
            day3_ratio = price_series[-3] / price_series[-4]

            if min(day1_ratio, day2_ratio, day3_ratio) < g.loss:
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
        log.warning(f"计算{etf}动量指标时出错: {e}")
        return None

# ==================== 【改造4】QDII基金净值获取 ====================
def get_qdii_nav_series(context, security, lookback_days):
    """
    获取QDII ETF的基金净值序列，替代二级市场价格计算动量。
    基金净值不包含场内溢价/折价，能更真实反映标的资产的趋势。
    当天净值可能尚未公布，最新数据为T-1日净值，对动量计算影响可忽略。
    """
    try:
        end_date = context.current_dt.date()
        start_date = end_date - timedelta(days=lookback_days + 60)

        nav_df = get_extras('unit_net_value', [security],
                            start_date=start_date, end_date=end_date, df=True)

        if nav_df is not None and not nav_df.empty and security in nav_df.columns:
            nav_series = nav_df[security].dropna()
            if len(nav_series) >= g.lookback_days:
                return nav_series.values
        return None
    except Exception as e:
        log.debug(f"获取{security}基金净值失败，回退到二级市场价格: {e}")
        return None

# ==================== 成交量过滤函数 ====================
def get_volume_ratio(context, security, lookback_days=None, threshold=None):
    if lookback_days is None:
        lookback_days = g.volume_lookback
    if threshold is None:
        threshold = g.volume_threshold

    try:
        hist_data = attribute_history(security, lookback_days, '1d', ['volume'])
        if hist_data.empty or len(hist_data) < lookback_days:
            return None

        avg_volume = hist_data['volume'].mean()

        today = context.current_dt.date()
        df_vol = get_price(
            security,
            start_date=today,
            end_date=context.current_dt,
            frequency='1m',
            fields=['volume'],
            skip_paused=False,
            fq='pre',
            panel=True,
            fill_paused=False
        )

        if df_vol is None or df_vol.empty:
            return None

        current_volume = df_vol['volume'].sum()
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

        if volume_ratio > threshold:
            return volume_ratio
        else:
            return None

    except Exception as e:
        log.warning(f"成交量检测失败 {security}: {e}")
        return None

# =================== 计算年化收益 ===================
def get_annualized_returns(price_series, lookback_days):
    recent_price_series = price_series[-(lookback_days + 1):]
    y = np.log(recent_price_series)
    x = np.arange(len(y))
    weights = np.linspace(1, 2, len(y))

    slope, intercept = np.polyfit(x, y, 1, w=weights)
    annualized_returns = math.exp(slope * 250) - 1
    return annualized_returns

# ==================== 【改造1】多标的买入函数 ====================
def etf_buy_trade(context):
    log.info("======================== 买入操作开始 ========================")

    # 复用卖出阶段缓存的排名，避免1分钟内排名变化导致不一致
    if g.cached_ranked_etfs is not None and g.cached_rank_dt is not None:
        time_diff = context.current_dt - g.cached_rank_dt
        if time_diff.total_seconds() < 300:
            ranked_etfs = g.cached_ranked_etfs
        else:
            ranked_etfs = get_ranked_etfs(context)
    else:
        ranked_etfs = get_ranked_etfs(context)

    if ranked_etfs:
        log.info("========== 排名前五 ==========")
        for idx, metrics in enumerate(ranked_etfs[:5], start=1):
            etf_name = get_security_name(metrics['etf'])
            annualized_pct = metrics['annualized_returns'] * 100
            tag = " [QDII-净值]" if metrics['etf'] in g.qdii_etfs else ""
            log.info(f"{idx}. {metrics['etf']} {etf_name}{tag}: 得分={metrics['score']:.4f} | 年化={annualized_pct:.2f}% | R²={metrics['r_squared']:.4f}")

    # 确定目标ETF列表（前N只合格 + 防御ETF补位）
    target_etfs = []
    for m in ranked_etfs[:g.holdings_num]:
        if m['score'] >= g.min_score_threshold:
            target_etfs.append(m['etf'])

    defensive_count = g.holdings_num - len(target_etfs)
    if defensive_count > 0 and check_defensive_etf_available(context):
        for _ in range(defensive_count):
            target_etfs.append(g.defensive_etf)

    # 去重
    target_etfs_unique = list(dict.fromkeys(target_etfs))

    if not target_etfs_unique:
        log.info("无目标ETF，保持空仓")
        log.info("======================== 买入操作完成 ========================")
        return

    # 【改造1】等分仓位：每只目标ETF分配总资产的 1/N
    total_value = context.portfolio.total_value
    num_holdings = len(target_etfs_unique)
    target_value_per_etf = total_value / num_holdings

    log.info(f"持仓目标：{num_holdings}只，每只目标仓位：{target_value_per_etf:.2f}（总资产{total_value:.2f}）")

    # 确保非目标持仓已清空（卖出函数已处理，这里做二次检查）
    current_positions = list(context.portfolio.positions.keys())
    for security in current_positions:
        if (security in g.etf_pool or security == g.defensive_etf) and security not in set(target_etfs_unique):
            position = context.portfolio.positions[security]
            if position.total_amount > 0:
                log.info(f"二次检查：{get_security_name(security)} 仍有持仓，跳过买入等待卖出完成")
                log.info("======================== 买入操作完成 ========================")
                return

    # 分离需要减仓和加仓的标的：先减仓释放资金，再加仓使用资金
    sell_first = []
    buy_list = []
    hold_list = []

    for etf in target_etfs_unique:
        current_value = 0
        if etf in context.portfolio.positions:
            pos = context.portfolio.positions[etf]
            if pos.total_amount > 0:
                current_value = pos.total_amount * pos.price

        diff_ratio = abs(current_value - target_value_per_etf) / target_value_per_etf if target_value_per_etf > 0 else 1

        if diff_ratio <= g.rebalance_threshold:
            hold_list.append(etf)
        elif current_value > target_value_per_etf:
            sell_first.append(etf)
        else:
            buy_list.append(etf)

    if hold_list:
        log.info(f"仓位接近目标，无需调仓：{[get_security_name(e) for e in hold_list]}")

    # 先执行减仓（释放资金）
    for etf in sell_first:
        etf_name = get_security_name(etf)
        success = smart_order_target_value(etf, target_value_per_etf, context)
        if success:
            log.info(f"减仓至目标: {etf} {etf_name} → {target_value_per_etf:.2f}")

    # 再执行加仓（使用释放的资金）
    for etf in buy_list:
        etf_name = get_security_name(etf)
        success = smart_order_target_value(etf, target_value_per_etf, context)
        if success:
            log.info(f"加仓至目标: {etf} {etf_name} → {target_value_per_etf:.2f}")
        else:
            # 现金不足时尝试用剩余现金买入
            available_cash = context.portfolio.available_cash
            if available_cash > g.min_money:
                log.info(f"现金不足全额买入{etf_name}，尝试用剩余现金{available_cash:.2f}部分买入")
                smart_order_target_value(etf, available_cash * 0.999, context)

    log.info("======================== 买入操作完成 ========================")

# ==================== 辅助函数 ====================
def get_security_name(security):
    current_data = get_current_data()
    return current_data[security].name

def check_defensive_etf_available(context):
    current_data = get_current_data()
    defensive_etf = g.defensive_etf

    if current_data[defensive_etf].paused:
        log.info(f"防御性ETF {defensive_etf} 今日停牌")
        return False

    if current_data[defensive_etf].last_price >= current_data[defensive_etf].high_limit:
        log.info(f"防御性ETF {defensive_etf} 当前涨停")
        return False

    if current_data[defensive_etf].last_price <= current_data[defensive_etf].low_limit:
        log.info(f"防御性ETF {defensive_etf} 当前跌停")
        return False

    return True

def smart_order_target_value(security, target_value, context):
    current_data = get_current_data()

    if current_data[security].paused:
        log.info(f"{security} {get_security_name(security)}: 今日停牌，跳过交易")
        return False

    if current_data[security].last_price >= current_data[security].high_limit:
        log.info(f"{security} {get_security_name(security)}: 当前涨停，跳过买入")
        return False

    if current_data[security].last_price <= current_data[security].low_limit:
        log.info(f"{security} {get_security_name(security)}: 当前跌停，跳过卖出")
        return False

    current_price = current_data[security].last_price
    if current_price == 0:
        log.info(f"{security} {get_security_name(security)}: 当前价格为0，跳过交易")
        return False

    target_amount = int(target_value / current_price)
    target_amount = (target_amount // 100) * 100
    if target_amount <= 0 and target_value > 0:
        target_amount = 100

    current_position = context.portfolio.positions.get(security, None)
    current_amount = current_position.total_amount if current_position else 0

    amount_diff = target_amount - current_amount

    trade_value = abs(amount_diff) * current_price
    if 0 < trade_value < g.min_money:
        log.info(f"{security} {get_security_name(security)}: 交易金额{trade_value:.2f} < 最小交易额{g.min_money}，跳过")
        return False

    # T+1限制
    if amount_diff < 0:
        closeable_amount = current_position.closeable_amount if current_position else 0
        if closeable_amount == 0:
            log.info(f"{security} {get_security_name(security)}: 当天买入不可卖出(T+1)")
            return False
        amount_diff = -min(abs(amount_diff), closeable_amount)

    # 买入现金检查（含佣金）
    if amount_diff > 0:
        required_cash = amount_diff * current_price
        estimated_commission = max(5, required_cash * 0.0002)
        total_required = required_cash + estimated_commission
        available_cash = context.portfolio.available_cash
        if total_required > available_cash + 1e-6:
            log.info(f"{security} {get_security_name(security)}: 现金不足（含佣金），需要{total_required:.2f}，可用{available_cash:.2f}")
            return False

    # 执行下单
    if amount_diff != 0:
        order_result = order(security, amount_diff)
        if order_result:
            g.positions[security] = target_amount
            security_name = get_security_name(security)
            if amount_diff > 0:
                log.info(f"买入 {security} {security_name}，数量: {amount_diff}，价格: {current_price:.3f}")
            else:
                log.info(f"卖出 {security} {security_name}，数量: {abs(amount_diff)}，价格: {current_price:.3f}")
            return True
        else:
            log.warning(f"下单失败: {security} {get_security_name(security)}，数量: {amount_diff}")
            return False

    return False
