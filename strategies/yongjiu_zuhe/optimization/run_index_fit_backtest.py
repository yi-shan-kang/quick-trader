# -*- coding: utf-8 -*-
"""指数拟合回测器：用长历史指数模拟永久组合再平衡，输出 10/20/30 年收益

方法学（参照 example/永久组合/布朗组合_1970起_指数回测.html）：
- 以指数日收盘价模拟资产价格，年度再平衡 + 6% 偏离阈值（同策略实现）
- A股股票指数为价格指数（未含股息），报告中注明
- 货币端无长历史货基指数，按固定年化收益近似（默认 2.2%），报告中注明
- 不计算交易费用与滑点（指数层面验证）
"""
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index_fit_data')

# ---------------------------------------------------------------
# 组合定义（映射到已验证数据源）
# ---------------------------------------------------------------
# 资产规范: (name, source, kwargs)
#   source='csv' -> kwargs={'file': 'hs300.csv'}
#   source='cash' -> kwargs={'yield': 0.022}  固定年化收益（货币端近似）
COMBOS = {
    # A股线：经典 4 资产等权（沪深300 价格指数 + 国债指数 + 货币 + 伦敦金）
    'PP_CN': [
        ('沪深300指数', 'csv', {'file': 'hs300.csv'}),
        ('国债指数', 'csv', {'file': 'bond.csv'}),
        ('货币(2.2%)', 'cash', {'yield': 0.022}),
        ('伦敦金XAU', 'csv', {'file': 'gold_london.csv'}),
    ],
    # A股线变体：股票端 15% 沪深300 + 10% 中证500（更贴近小盘暴露）
    'PP_CN_SZ': [
        ('沪深300指数', 'csv', {'file': 'hs300.csv'}),
        ('中证500指数', 'csv', {'file': 'zz500.csv'}),
        ('国债指数', 'csv', {'file': 'bond.csv'}),
        ('货币(2.2%)', 'cash', {'yield': 0.022}),
        ('伦敦金XAU', 'csv', {'file': 'gold_london.csv'}),
    ],
    # 美股线（若 us 数据可用）：标普500 总回报 + 10Y 国债合成 + 现金(3M国库券) + COMEX黄金
    'PP_US': [
        ('标普500', 'csv', {'file': 'us_sp500.csv'}),
        ('美债10Y合成', 'csv', {'file': 'us_bond10.csv'}),
        ('美债3M现金', 'csv', {'file': 'us_cash3m.csv'}),
        ('COMEX黄金', 'csv', {'file': 'us_gold.csv'}),
    ],
    # 对照/单资产
    'SP500_ALONE': [('标普500', 'csv', {'file': 'us_sp500.csv'})],
    'HS300_ALONE': [('沪深300指数', 'csv', {'file': 'hs300.csv'})],
    'GOLD_ALONE': [
        ('伦敦金XAU', 'csv', {'file': 'gold_london.csv'}),
        ('COMEX黄金', 'csv', {'file': 'us_gold.csv'}),
    ],
}

PP_WEIGHTS = [0.25, 0.25, 0.25, 0.25]
PP_CN_SZ_WEIGHTS = [0.15, 0.10, 0.25, 0.25, 0.25]

REBALANCE_MONTH = 12
REBALANCE_THRESHOLD = 0.06
CASH_DEFAULT_YIELD = 0.022
RF = 0.02
TRADING_DAYS = 252


# ---------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------
def load_csv(name):
    fp = os.path.join(DATA_DIR, name)
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, parse_dates=['date'])
    df = df[['date', 'close']].dropna().sort_values('date')
    return df


def build_price_series(asset, index_dates):
    """返回 dict: name -> Series(index=date, value=price)"""
    name, src, kw = asset
    if src == 'cash':
        y = kw.get('yield', CASH_DEFAULT_YIELD)
        days = (index_dates[-1] - index_dates[0]).days
        steps = np.arange(len(index_dates))
        close = 100.0 * (1.0 + y) ** (steps / 365.25)
        return name, pd.Series(close, index=index_dates)
    df = load_csv(kw['file'])
    df = df.set_index('date')['close']
    df = df.reindex(index_dates).ffill()
    return name, df


def run_combo(notes, weights, start, end):
    """对单个组合跑回测。notes: [{name, series}]（已按区间截取并日期对齐）"""
    idx = notes[0]['series'].index
    idx = idx[(idx >= start) & (idx <= end)]
    if len(idx) < 60:
        return None
    # 价格矩阵
    mat = pd.DataFrame({n['name']: n['series'].reindex(idx).ffill() for n in notes}).dropna()
    if len(mat) < 60:
        return None
    # 份额
    init_price = mat.iloc[0]
    target = pd.Series(weights, index=mat.columns)
    shares = (target * 100.0 / init_price).astype(float)

    values = []
    last_reb_year = None
    for date, row in mat.iterrows():
        values.append((date, shares * row))
        if date.month == REBALANCE_MONTH and date.year != last_reb_year:
            total = float((shares * row).sum())
            cur_w = shares * row / total
            max_dev = float((cur_w - target).abs().max())
            if max_dev > REBALANCE_THRESHOLD:
                # 最终版逻辑：部分再平衡——只调偏离超阈值(>6%)的资产至目标值，
                # 未越界资产保持不变（先卖超配、再买低配在指数层面等价，见策略实现）
                for name in mat.columns:
                    cur_v = float(shares[name] * row[name])
                    tgt_v = target[name] * total
                    if abs(cur_v - tgt_v) > REBALANCE_THRESHOLD * total:
                        shares[name] = tgt_v / row[name]
                last_reb_year = date.year
    df = pd.DataFrame({d: v for d, v in values}).T
    df.columns = mat.columns
    nav = df.sum(axis=1)
    return compute_stats(nav, start, end)


def compute_stats(nav, start, end):
    nav = nav.dropna()
    total = nav.iloc[-1] / nav.iloc[0] - 1
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    ann = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    # 波动/夏普/回撤统一用月频收益计算：
    # 美股线（标普/黄金）原始数据为月度，按日均摊会抹平日内波动；
    # 为两线口径一致且不被数据频率扭曲，统一以月末净值计算。
    m = nav.resample('ME').last()
    mret = m.pct_change().dropna()
    vol = float(mret.std() * np.sqrt(12)) if len(mret) > 2 else np.nan
    sharpe = (ann - RF) / vol if vol and vol > 0 else np.nan
    roll_max = m.cummax()
    mdd = float((m / roll_max - 1).min())
    return {
        'start': str(nav.index[0].date()), 'end': str(nav.index[-1].date()),
        'years': round(years, 1), 'total': total, 'annual': ann,
        'vol': vol, 'sharpe': round(sharpe, 2), 'mdd': mdd,
        'end_value': round(float(nav.iloc[-1]), 1),
    }


def fmt_pct(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return '--'
    return f'{x * 100:.2f}%'


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------
def main():
    # 主交易日历 = 沪深300（有则用，否则用第一个组合第一个资产）
    all_dates = None
    cache = {}
    for key, assets in COMBOS.items():
        for name, src, kw in assets:
            if src == 'cash':
                continue
            fp = os.path.join(DATA_DIR, kw['file'])
            if os.path.exists(fp):
                d = pd.DatetimeIndex(pd.read_csv(fp, parse_dates=['date'])['date'])
                all_dates = all_dates.union(d) if all_dates is not None else d
    all_dates = pd.DatetimeIndex(sorted(all_dates))

    results = {}
    for key, assets in COMBOS.items():
        if key == 'PP_CN_SZ':
            weights = PP_CN_SZ_WEIGHTS
        elif key in ('PP_CN', 'PP_US'):
            weights = PP_WEIGHTS
        else:
            # 对照/单资产组合：等权满仓（权重归一化到100%）
            weights = [1.0 / len(assets)] * len(assets)
        notes = []
        ok = True
        for name, src, kw in assets:
            if src == 'csv' and not os.path.exists(os.path.join(DATA_DIR, kw['file'])):
                ok = False
                break
            n, s = build_price_series((name, src, kw), all_dates)
            notes.append({'name': n, 'series': s})
        if not ok:
            print(f'{key}: 数据缺失，跳过')
            continue
        cache[key] = notes
        print(f'== {key} == 资产: {[n["name"] for n in notes]}')

        # 各窗口（以组合内最晚有效数据的月末为起点）
        starts = {}
        for name, src, kw in assets:
            if src == 'cash':
                continue
            s = pd.read_csv(os.path.join(DATA_DIR, kw['file']), parse_dates=['date'])['date']
            starts[name] = s.iloc[0]
        latest_start = max(starts.values()) if starts else all_dates[0]
        end = all_dates[-1]
        for label, years_target in [('10Y', 10), ('15Y', 15), ('20Y', 20), ('30Y', 30)]:
            win_start = max(latest_start, end - pd.DateOffset(years=years_target))
            stat = run_combo(notes, weights, win_start, end)
            if stat is None:
                continue
            actual_years = stat['years']
            key_stats = abs(actual_years - years_target)
            if key_stats > 0.6:
                continue
            results.setdefault(key, {})['win_' + label] = stat
            print(f'  [{label}]  起始 {stat["start"]}  年数 {stat["years"]}  '
                  f'年化 {fmt_pct(stat["annual"])}  波动 {fmt_pct(stat["vol"])}  '
                  f'夏普 {stat["sharpe"]}  回撤 {fmt_pct(stat["mdd"])}  期末 {stat["end_value"]}')

    # 保存
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'optimization_results/index_fit_results.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print('saved:', out)


if __name__ == '__main__':
    main()