#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""七星高照ETF轮动策略 - 2015~2025年回测脚本

使用 AkShare 下载 ETF 历史行情数据，通过项目自研回测引擎执行回测。
"""

import sys
import os
import time
import datetime
import logging
import json
import numpy as np
import pandas as pd
from pathlib import Path

# 项目根目录加入路径
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

# 设置 matplotlib 后端为 Agg（非交互式）
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def setup_logging():
    """配置日志"""
    # 确保日志目录存在
    log_dir = project_root / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                str(log_dir / 'seven_star_backtest.log'),
                encoding='utf-8'
            )
        ]
    )
    # 降低引擎日志级别
    logging.getLogger('engine').setLevel(logging.WARNING)
    logging.getLogger('core').setLevel(logging.WARNING)


def download_etf_data(symbols, start_date, end_date):
    """使用 AkShare 下载 ETF 历史日线数据

    Args:
        symbols: ETF 代码列表（如 '159941.SZ'）
        start_date: 起始日期 'YYYY-MM-DD'
        end_date: 结束日期 'YYYY-MM-DD'

    Returns:
        Dict[str, pd.DataFrame]: {symbol: DataFrame}
    """
    import akshare as ak

    # 转换日期格式
    start_str = start_date.replace('-', '')
    end_str = end_date.replace('-', '')

    data_dict = {}
    failed = []
    total = len(symbols)

    for i, symbol in enumerate(symbols, 1):
        code = symbol.split('.')[0]
        success = False

        for retry in range(3):
            try:
                # 使用东财接口获取后复权数据
                df = ak.fund_etf_hist_em(
                    symbol=code,
                    period='daily',
                    start_date=start_str,
                    end_date=end_str,
                    adjust='hfq'
                )

                if df is not None and not df.empty:
                    # 统一列名
                    col_map = {
                        '日期': 'datetime',
                        '开盘': 'open',
                        '最高': 'high',
                        '最低': 'low',
                        '收盘': 'close',
                        '成交量': 'volume',
                        '成交额': 'amount',
                    }
                    df = df.rename(columns=col_map)

                    # 转换 datetime
                    df['datetime'] = pd.to_datetime(df['datetime'])
                    df = df.set_index('datetime')
                    df = df.sort_index()

                    # 确保数值类型
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')

                    # 去除无效行
                    df = df.dropna(subset=['open', 'high', 'low', 'close'])

                    if len(df) > 0:
                        data_dict[symbol] = df
                        print(f"  [{i}/{total}] OK  {symbol} ({len(df)} bars)")
                        success = True
                        break
                    else:
                        failed.append(symbol)
                        print(f"  [{i}/{total}] EMPTY {symbol} (空数据)")
                        success = True  # 不重试空数据
                        break
                else:
                    failed.append(symbol)
                    print(f"  [{i}/{total}] EMPTY {symbol} (无数据)")
                    success = True
                    break

            except Exception as e:
                if retry < 2:
                    print(f"  [{i}/{total}] RETRY {symbol} (attempt {retry+1}/3)")
                    time.sleep(2)
                else:
                    failed.append(symbol)
                    print(f"  [{i}/{total}] FAIL {symbol}: {type(e).__name__}")

        if not success:
            failed.append(symbol)

        # 避免请求过快
        time.sleep(0.5)

    print(f"\n下载完成: 成功 {len(data_dict)}/{total}, 失败 {len(failed)}")
    if failed:
        print(f"失败标的: {failed}")

    return data_dict, failed


def run_backtest(data_dict, cash=1000000, start_date='2015-01-01', end_date='2025-12-31'):
    """执行回测

    Args:
        data_dict: {symbol: DataFrame} 数据字典
        cash: 初始资金
        start_date: 回测交易起始日
        end_date: 回测结束日

    Returns:
        回测结果对象
    """
    from api.backtest_api import BacktestAPI
    from strategies.seven_star_etf_strategy.seven_star_etf_strategy import SevenStarETFRotationStrategy

    # 创建回测API，使用 open 数据源（但我们手动加载数据）
    api = BacktestAPI(data_source='open')

    # 配置回测参数
    api.configure(
        cash=cash,
        commission=0.0002,
        open_commission=0.0002,
        close_commission=0.0002,
        close_tax=0.0,
        min_commission=5.0,
        slippage=0.001,
        start_date=start_date,
        end_date=end_date,
        data_lookback_days=120,  # 前移120天获取动量计算所需历史
        period='1d',
        benchmark='000300.SH',
    )

    # 手动加载数据到引擎（绕过数据处理器）
    for symbol, df in data_dict.items():
        # 确保列名正确
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            print(f"  跳过 {symbol}: 缺少必要列")
            continue

        # 只保留 OHLCV
        ohlcv = df[required_cols].copy()
        ohlcv.index = pd.to_datetime(ohlcv.index)

        api._engine.add_data(symbol, ohlcv)
        api._symbols.append(symbol)
        api._data_cache[symbol] = ohlcv

    print(f"已加载 {len(api._symbols)} 只标的数据")

    # 设置策略
    api.add_strategy(SevenStarETFRotationStrategy)

    # 运行回测
    print("\n开始回测...")
    result = api.run()

    return result, api


def generate_report(result, api, output_dir='reports'):
    """生成回测报告

    Args:
        result: EngineResult 回测结果
        api: BacktestAPI 实例
        output_dir: 报告输出目录
    """
    report_dir = project_root / output_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    # 获取回测数据
    equity_history = result.equity_history
    trade_records = result.trade_records
    initial_cash = result.initial_cash
    final_value = result.final_value

    if not equity_history:
        print("错误: 无权益历史数据")
        return

    # 构建权益曲线 DataFrame
    equity_df = pd.DataFrame(equity_history, columns=['date', 'value'])
    equity_df['date'] = pd.to_datetime(equity_df['date'])
    equity_df = equity_df.set_index('date')

    # 计算绩效指标
    total_return = (final_value - initial_cash) / initial_cash
    days = len(equity_df)
    annual_return = (1 + total_return) ** (250 / max(days, 1)) - 1

    # 日收益率
    daily_returns = equity_df['value'].pct_change().dropna()

    # 夏普比率（无风险利率2%）
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() - 0.02/250) / daily_returns.std() * np.sqrt(250)
    else:
        sharpe = 0.0

    # 最大回撤
    cummax = equity_df['value'].cummax()
    drawdown = (equity_df['value'] - cummax) / cummax
    max_drawdown = drawdown.min()

    # 卡尔马比率
    calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

    # 交易统计
    total_trades = len(trade_records)
    buy_trades = [t for t in trade_records if t.get('direction') == 'buy']
    sell_trades = [t for t in trade_records if t.get('direction') == 'sell']

    # 胜率（按卖出交易盈亏）
    win_trades = [t for t in sell_trades if t.get('pnl', 0) > 0]
    loss_trades = [t for t in sell_trades if t.get('pnl', 0) <= 0]
    win_rate = len(win_trades) / max(len(sell_trades), 1)

    # 盈亏比
    avg_win = np.mean([t['pnl'] for t in win_trades]) if win_trades else 0
    avg_loss = abs(np.mean([t['pnl'] for t in loss_trades])) if loss_trades else 0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    # 总佣金
    total_commission = sum(t.get('commission', 0) for t in trade_records)

    # 交易频率
    if days > 0:
        trade_frequency = total_trades / days * 250
    else:
        trade_frequency = 0

    # 按标的统计交易
    symbol_stats = {}
    for t in trade_records:
        sym = t.get('symbol', '')
        if sym not in symbol_stats:
            symbol_stats[sym] = {'buy_count': 0, 'sell_count': 0, 'buy_value': 0, 'sell_value': 0}
        if t.get('direction') == 'buy':
            symbol_stats[sym]['buy_count'] += 1
            symbol_stats[sym]['buy_value'] += abs(t.get('size', 0) * t.get('price', 0))
        else:
            symbol_stats[sym]['sell_count'] += 1
            symbol_stats[sym]['sell_value'] += abs(t.get('size', 0) * t.get('price', 0))

    # 月度收益
    equity_df['month'] = equity_df.index.to_period('M')
    monthly_returns = equity_df.groupby('month')['value'].agg(['first', 'last'])
    monthly_returns['return'] = (monthly_returns['last'] - monthly_returns['first']) / monthly_returns['first']

    # === 输出报告 ===
    print("\n" + "=" * 60)
    print("           七星高照ETF轮动策略 - 2015~2025年回测报告")
    print("=" * 60)

    print(f"\n【回测参数】")
    print(f"  回测区间: {equity_df.index[0].date()} ~ {equity_df.index[-1].date()}")
    print(f"  交易天数: {days}")
    print(f"  初始资金: RMB {initial_cash:,.2f}")
    print(f"  最终资金: RMB {final_value:,.2f}")
    print(f"  佣金费率: 0.02% (最低5元)")
    print(f"  滑点: 0.1%")

    print(f"\n【核心绩效指标】")
    print(f"  总收益率:   {total_return:>10.2%}")
    print(f"  年化收益率: {annual_return:>10.2%}")
    print(f"  夏普比率:   {sharpe:>10.2f}")
    print(f"  最大回撤:   {max_drawdown:>10.2%}")
    print(f"  卡尔马比率: {calmar:>10.2f}")

    print(f"\n【交易统计】")
    print(f"  总交易次数: {total_trades}")
    print(f"  买入次数:   {len(buy_trades)}")
    print(f"  卖出次数:   {len(sell_trades)}")
    print(f"  胜率:       {win_rate:>10.2%}")
    print(f"  盈亏比:     {profit_loss_ratio:>10.2f}")
    print(f"  平均盈利:   RMB {avg_win:>10,.2f}")
    print(f"  平均亏损:   RMB {avg_loss:>10,.2f}")
    print(f"  总佣金:     RMB {total_commission:>10,.2f}")
    print(f"  年化交易频率: {trade_frequency:.1f}次/年")

    print(f"\n【月度收益】")
    for month, row in monthly_returns.iterrows():
        ret = row['return']
        bar = '█' * int(abs(ret) * 100) if ret > 0 else '▒' * int(abs(ret) * 100)
        sign = '+' if ret >= 0 else ''
        print(f"  {month}  {sign}{ret:>7.2%}  {bar}")

    print(f"\n【交易最活跃的标的】")
    sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]['buy_count'] + x[1]['sell_count'], reverse=True)
    for sym, stats in sorted_symbols[:10]:
        total_count = stats['buy_count'] + stats['sell_count']
        print(f"  {sym}: 买{stats['buy_count']}次 卖{stats['sell_count']}次 "
              f"买入额RMB {stats['buy_value']:,.0f} 卖出额RMB {stats['sell_value']:,.0f}")

    print("\n" + "=" * 60)

    # === 生成资金曲线图 ===
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), gridspec_kw={'height_ratios': [3, 1, 1]})

    # 资金曲线
    ax1 = axes[0]
    ax1.plot(equity_df.index, equity_df['value'], label='策略净值', color='#2196F3', linewidth=1.5)
    ax1.axhline(y=initial_cash, color='gray', linestyle='--', alpha=0.5, label=f'初始资金 {initial_cash:,.0f}')
    ax1.fill_between(equity_df.index, initial_cash, equity_df['value'],
                     where=equity_df['value'] >= initial_cash, alpha=0.1, color='green')
    ax1.fill_between(equity_df.index, initial_cash, equity_df['value'],
                     where=equity_df['value'] < initial_cash, alpha=0.1, color='red')
    ax1.set_title('七星高照ETF轮动策略 - 2015~2025年资金曲线', fontsize=14, fontweight='bold')
    ax1.set_ylabel('净值 (RMB)', fontsize=12)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)

    # 标注关键指标
    textstr = (f'总收益: {total_return:.2%}\n'
               f'年化: {annual_return:.2%}\n'
               f'夏普: {sharpe:.2f}\n'
               f'最大回撤: {max_drawdown:.2%}')
    props = dict(boxstyle='round', facecolor='wheat', alpha=0.8)
    ax1.text(0.98, 0.02, textstr, transform=ax1.transAxes, fontsize=10,
             verticalalignment='bottom', horizontalalignment='right', bbox=props)

    # 回撤曲线
    ax2 = axes[1]
    ax2.fill_between(drawdown.index, drawdown.values, 0, color='red', alpha=0.3)
    ax2.plot(drawdown.index, drawdown.values, color='red', linewidth=0.8)
    ax2.set_title('回撤曲线', fontsize=12)
    ax2.set_ylabel('回撤', fontsize=11)
    ax2.grid(True, alpha=0.3)

    # 月度收益柱状图
    ax3 = axes[2]
    months = [str(m) for m in monthly_returns.index]
    returns = monthly_returns['return'].values
    colors = ['#4CAF50' if r >= 0 else '#F44336' for r in returns]
    ax3.bar(months, returns, color=colors, alpha=0.7)
    ax3.set_title('月度收益率', fontsize=12)
    ax3.set_ylabel('收益率', fontsize=11)
    ax3.axhline(y=0, color='gray', linewidth=0.5)
    ax3.grid(True, alpha=0.3)
    plt.setp(ax3.get_xticklabels(), rotation=45, ha='right')

    plt.tight_layout()
    chart_path = str(report_dir / 'seven_star_equity_curve.png')
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\n资金曲线图已保存: {chart_path}")

    # === 保存交易记录 ===
    trades_df = pd.DataFrame(trade_records)
    trades_path = str(report_dir / 'seven_star_trades.csv')
    trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
    print(f"交易记录已保存: {trades_path}")

    # === 保存权益曲线 ===
    equity_path = str(report_dir / 'seven_star_equity.csv')
    equity_df.to_csv(equity_path, encoding='utf-8-sig')
    print(f"权益曲线已保存: {equity_path}")

    # === 保存JSON报告 ===
    report_data = {
        'strategy': '七星高照ETF轮动策略-优化版',
        'backtest_period': f"{equity_df.index[0].date()} ~ {equity_df.index[-1].date()}",
        'trading_days': days,
        'initial_cash': initial_cash,
        'final_value': final_value,
        'metrics': {
            'total_return': f'{total_return:.2%}',
            'annual_return': f'{annual_return:.2%}',
            'sharpe_ratio': round(sharpe, 2),
            'max_drawdown': f'{max_drawdown:.2%}',
            'calmar_ratio': round(calmar, 2),
        },
        'trades': {
            'total_trades': total_trades,
            'buy_trades': len(buy_trades),
            'sell_trades': len(sell_trades),
            'win_rate': f'{win_rate:.2%}',
            'profit_loss_ratio': round(profit_loss_ratio, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'total_commission': round(total_commission, 2),
        },
        'monthly_returns': {str(m): f'{r:.2%}' for m, r in monthly_returns['return'].items()},
        'methodology': {
            'data_source': 'MiniQMT (xtquant) ETF 后复权日线数据',
            'backtest_engine': '自研 numpy 数组驱动引擎 (COC 即时成交)',
            'commission_model': '0.02% 买入/卖出, 最低5元, ETF免印花税',
            'slippage_model': '0.1% 价格相关滑点',
            'position_sizing': '等分仓位 (1/N), 偏离5%触发调仓',
            'momentum_calculation': '60日加权对数线性回归, 年化收益×R²',
            'filters': [
                '流动性过滤: 日均成交额≥1亿(用close×volume近似)',
                'R²过滤: R²≥0.4',
                '近3日单日跌幅>3.5%过滤',
                '成交量异常放大过滤(高位放量)',
            ],
            'stop_loss': '持仓亏损≥8%触发止损',
            'qdit_treatment': '回测中QDII ETF直接使用二级市场价格(不获取基金净值)',
        },
        'limitations': [
            'QDII ETF未使用基金净值计算动量, 存在溢价干扰',
            '成交额使用QMT amount字段反推统一手成交量, 与原始volume单位存在换算',
            'COC(当前bar收盘价)即时成交模型, 未模拟排队/部分成交',
            '滑点为固定比例, 未考虑实际冲击成本',
            '回测结果不代表未来收益, 实盘交易存在额外风险',
        ],
    }

    json_path = str(report_dir / 'seven_star_report.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"JSON报告已保存: {json_path}")


def main():
    setup_logging()

    print("=" * 60)
    print("  七星高照ETF轮动策略 - 2015~2025年回测")
    print("=" * 60)

    # ETF 池（与策略文件一致）
    from strategies.seven_star_etf_strategy.seven_star_etf_strategy import ETF_POOL

    defensive_etf = '511880.SH'
    all_symbols = list(ETF_POOL) + [defensive_etf]

    # 数据下载范围（前移约半年用于动量计算预热）
    data_start = '2014-08-01'
    data_end = '2025-12-31'

    print(f"\n【数据下载】")
    print(f"  标的数量: {len(all_symbols)}")
    print(f"  数据范围: {data_start} ~ {data_end}")

    # 加载 ETF 数据（优先使用 MiniQMT 缓存）
    cache_path = project_root / 'DATA' / 'cache' / 'etf_data_cache_qmt_10y.pkl'
    if cache_path.exists():
        import pickle
        with open(cache_path, 'rb') as f:
            data_dict = pickle.load(f)
        print(f"  从 MiniQMT 缓存加载 {len(data_dict)} 只ETF数据")
        failed = []
    else:
        data_dict, failed = download_etf_data(all_symbols, data_start, data_end)

    if not data_dict:
        print("错误: 无可用数据，终止回测")
        return

    # 执行回测
    print(f"\n【回测执行】")
    result, api = run_backtest(data_dict, cash=1000000, start_date='2015-01-01', end_date='2025-12-31')

    # 生成报告
    print(f"\n【报告生成】")
    generate_report(result, api)


if __name__ == '__main__':
    main()
