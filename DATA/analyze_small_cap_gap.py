# -*- coding: utf-8 -*-
"""小市值回测差距归因：纯信号模拟 vs 引擎回测

目的：回答"修复数据后 11 年总收益只有 14.95% 到底正不正常"。
方法：完全复刻 small_cap_strategy 的选股逻辑（本地数据、逐时点、无未来函数），
     但绕开回测引擎的执行摩擦（停牌/涨停跳过买卖、T+1、现金拖累、锁定仓位），
     得到"纯信号"的理论收益，再与引擎结果对比，定位差距来源。

选股复刻要点（与 strategies/small_cap_strategy/small_cap_strategy.py 一致）：
  1. 股票池：中证全指历史成分股 CSV（最后一个快照 <= 当日）
  2. 市值 = 总股本 × 不复权价，总股本 = Balance.total_equity / Pershareindex.s_fa_bps
     （按披露日 m_anntime <= 当日 取最新，无未来函数）
  3. 回退：无 bps 时用 total_equity 充当市值（与策略一致）
  4. 门禁：不复权价 None 或 < 1.0 剔除（与策略一致）
  5. 按市值升序取前 N 只，等权

收益计算：入场=调仓日后复权收盘价，持有到下次调仓日；退市/长期停牌股按最后价格冻结
        （与引擎 broker 估值方式一致）。

用法：
    python DATA/analyze_small_cap_gap.py            # 全量分析（首次较慢，有pickle缓存）
    python DATA/analyze_small_cap_gap.py --no-cache # 强制重建缓存
"""
import argparse
import ast
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MKTX_DIR = os.path.join(ROOT, '.cache', 'QMTData', 'market')        # 后复权（用于收益）
RAW_DIR = os.path.join(ROOT, '.cache', 'QMTData', 'market_raw')     # 不复权（用于市值）
FIN_DIR = os.path.join(ROOT, '.cache', 'QMTData', 'financial')
CSV_PATH = os.path.join(ROOT, '.cache', 'JQData', 'index_constituent', '000985.SH.csv')
CACHE_PATH = os.path.join(ROOT, 'DATA', '.cache_analyze_small_cap_v2.pkl')

YEARS = list(range(2014, 2026))
TOP_N = 10
TOP_N_REF = 100          # 对照组：全指最小100只（近似微盘股指数）
COST_ONE_WAY = 0.0015    # 单边成本估算 0.15%（佣金+印花+滑点）
START = '2015-01-01'
END = '2025-12-31'


# ---------------------------------------------------------------- 数据加载
def load_calendar():
    df = pd.read_parquet(os.path.join(MKTX_DIR, '000001.SZ', '2020_1d.parquet'))
    cal = df.index
    for y in YEARS:
        p = os.path.join(MKTX_DIR, '000001.SZ', f'{y}_1d.parquet')
        if os.path.exists(p):
            cal = cal.union(pd.read_parquet(p).index)
    cal = cal.sort_values()
    return cal[(cal >= pd.Timestamp(START)) & (cal <= pd.Timestamp(END))]


def load_membership():
    df = pd.read_csv(CSV_PATH)
    df['codes'] = df['codes'].apply(ast.literal_eval)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    return df


def _concat_years(symbol, base_dir, cols):
    frames = []
    for y in YEARS:
        p = os.path.join(base_dir, symbol, f'{y}_1d.parquet')
        if os.path.exists(p):
            try:
                d = pd.read_parquet(p, columns=cols)
                frames.append(d)
            except Exception:
                pass
    if not frames:
        return None
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep='last')].sort_index()
    return df


def load_financial(symbol):
    """返回 {'Balance': df(total_equity), 'Pershareindex': df(s_fa_bps)}，索引=披露日"""
    out = {}
    for table, field in (('Balance', 'total_equity'), ('Pershareindex', 's_fa_bps')):
        frames = []
        d = os.path.join(FIN_DIR, symbol)
        if os.path.isdir(d):
            for f in os.listdir(d):
                if table in f and f.endswith('.parquet'):
                    try:
                        t = pd.read_parquet(os.path.join(d, f), columns=[field])
                        frames.append(t)
                    except Exception:
                        pass
        if frames:
            df = pd.concat(frames)
            df = df[~df.index.duplicated(keep='last')].sort_index()
            out[table] = df[field]
        else:
            out[table] = None
    return out


def build_cache():
    print('加载数据（首次运行约5-10分钟，之后走pickle缓存）...')
    cal = load_calendar()
    membership = load_membership()
    all_codes = sorted(set(c for codes in membership['codes'] for c in codes))
    print(f'成分股历史并集: {len(all_codes)} 只，交易日 {len(cal)} 天')

    raw_close, hfq_close, raw_date, hfq_date, susp, fin = {}, {}, {}, {}, {}, {}
    t0 = time.time()
    n_ok = 0
    for i, s in enumerate(all_codes):
        raw = _concat_years(s, RAW_DIR, ['close', 'suspendFlag'])
        hfq = _concat_years(s, MKTX_DIR, ['close'])
        f = load_financial(s)
        if raw is not None and len(raw):
            raw_close[s] = raw['close'].values
            raw_date[s] = raw.index.values
            susp[s] = raw['suspendFlag'].values
        if hfq is not None and len(hfq):
            hfq_close[s] = hfq['close'].values
            hfq_date[s] = hfq.index.values
        if f['Balance'] is not None or f['Pershareindex'] is not None:
            fin[s] = f
        if raw_close.get(s) is not None and hfq_close.get(s) is not None:
            n_ok += 1
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(all_codes)} ({time.time()-t0:.0f}s)')
    print(f'加载完成: {n_ok} 只有完整双价数据 ({time.time()-t0:.0f}s)')

    with open(CACHE_PATH, 'wb') as fp:
        pickle.dump({'cal': cal, 'membership': membership,
                     'raw_close': raw_close, 'hfq_close': hfq_close,
                     'raw_date': raw_date, 'hfq_date': hfq_date,
                     'susp': susp, 'fin': fin}, fp)
    print(f'缓存已写: {CACHE_PATH}')


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'rb') as fp:
            d = pickle.load(fp)
        print(f'已加载缓存: {CACHE_PATH}')
        return d
    return None


# ---------------------------------------------------------------- 选股复刻
def _last_idx(arr_dates, ts):
    """arr_dates: numpy datetime64 升序; 返回最后一个 <= ts 的下标, 无则 -1"""
    i = np.searchsorted(arr_dates, ts, side='right') - 1
    return i


def select_bottom_n(d, ts, n):
    """复刻策略选股，返回 [(symbol, cap, flag_stale, flag_susp)]"""
    membership = d['membership']
    mask = membership['date'] <= ts
    if not mask.any():
        return []
    pool = membership[mask].iloc[-1]['codes']
    raw_close, raw_date, fin = d['raw_close'], d['raw_date'], d['fin']

    caps = {}
    meta = {}
    for s in pool:
        rc = raw_close.get(s)
        rd = raw_date.get(s)
        if rc is None:
            continue
        i = _last_idx(rd, ts)
        if i < 0:
            continue
        price = rc[i]
        if not np.isfinite(price) or price < 1.0:   # 与策略一致的门禁
            continue
        f = fin.get(s)
        te = bps = None
        if f:
            if f['Balance'] is not None and len(f['Balance']):
                j = _last_idx(f['Balance'].index.values, ts)
                if j >= 0:
                    te = f['Balance'].values[j]
            if f['Pershareindex'] is not None and len(f['Pershareindex']):
                j = _last_idx(f['Pershareindex'].index.values, ts)
                if j >= 0:
                    bps = f['Pershareindex'].values[j]
        try:
            te = float(te) if te is not None and np.isfinite(te) else None
            bps = float(bps) if bps is not None and np.isfinite(bps) else None
        except Exception:
            te = bps = None
        # 门禁：与策略一致 —— te/bps 缺一不可，不再用净资产当市值回退
        if not (te and te > 0 and bps and bps > 0):
            continue
        cap = te / bps * price
        # 门禁：停牌/退市剔除（最近行情距调仓日超过20自然日）
        if rd[i] < ts - np.timedelta64(20, 'D'):
            continue
        caps[s] = cap
        stale = (rd[i] < ts - np.timedelta64(5, 'D'))          # 价格陈旧=停牌中
        susp_flag = bool(d['susp'].get(s, np.array([0]))[i]) if s in d['susp'] else False
        meta[s] = (stale or susp_flag)

    if not caps:
        return []
    ranked = sorted(caps.items(), key=lambda x: x[1])[:n]
    return [(s, c, meta.get(s, False)) for s, c in ranked]


# ---------------------------------------------------------------- 模拟
def simulate(d, cal, top_n, label):
    """月初首日选股、当日收盘入场的链式等权模拟
    返回 (daily_nav_series, period_rows, holdings_detail)
    """
    rebalance_days = []
    seen_months = set()
    for ts in cal:
        m = (ts.year, ts.month)
        if m not in seen_months:
            seen_months.add(m)
            rebalance_days.append(ts)

    n_days = len(cal)
    nav = np.ones(n_days)
    detail_rows = []
    period_rows = []
    prev_weights = {}

    for k in range(len(rebalance_days)):
        t0 = rebalance_days[k]
        if k + 1 < len(rebalance_days):
            t1 = rebalance_days[k + 1]
        else:
            t1 = cal[-1] + np.timedelta64(1, 'D')   # 最后一期持有到回测结束
        sel = select_bottom_n(d, t0, top_n)
        i0 = int(np.searchsorted(cal, t0))
        i1 = int(np.searchsorted(cal, t1))
        dates0 = cal[i0:i1]

        if not sel:
            nav[i0:i1] = nav[i0 - 1] if i0 > 0 else 1.0
            period_rows.append({'t0': t0, 't1': t1, 'ret': 0.0, 'n': 0,
                                'turnover': 0.0, 'cost': 0.0})
            continue

        per_day = np.zeros(len(dates0))
        n_eff = 0
        for s, cap, stale in sel:
            hq = d['hfq_close'].get(s)
            hd = d['hfq_date'].get(s)
            if hq is None or hd is None:
                continue
            j0 = _last_idx(hd, t0)
            if j0 < 0:
                continue
            p0 = hq[j0]
            if not np.isfinite(p0) or p0 <= 0:
                continue
            # 期间每日相对价值（数据终结后冻结在最后价格）
            jj = j0
            rel = np.empty(len(dates0))
            for m_idx, dt in enumerate(dates0):
                while jj + 1 < len(hd) and hd[jj + 1] <= dt:
                    jj += 1
                p = hq[jj] if np.isfinite(hq[jj]) and hq[jj] > 0 else hq[j0]
                rel[m_idx] = p / p0
            per_day += rel
            n_eff += 1
            data_end = (jj == len(hd) - 1) and (hd[jj] < t1 - np.timedelta64(15, 'D'))
            entry_i = _last_idx(d['raw_date'][s], t0) if d['raw_date'].get(s) is not None else -1
            detail_rows.append({
                't0': t0, 't1': t1, 'symbol': s, 'cap_yi': cap / 1e8,
                'entry_price_raw': d['raw_close'][s][entry_i] if entry_i >= 0 else np.nan,
                'stale_at_select': stale, 'data_end_frozen': bool(data_end),
                'period_ret': rel[-1] - 1.0,
            })

        if n_eff == 0:
            nav[i0:i1] = nav[i0 - 1] if i0 > 0 else 1.0
            period_rows.append({'t0': t0, 't1': t1, 'ret': 0.0, 'n': 0,
                                'turnover': 0.0, 'cost': 0.0})
            continue

        per_day /= n_eff
        period_ret = float(per_day[-1] - 1.0)

        # 换手与成本
        new_w = {r['symbol']: 1.0 / n_eff for r in detail_rows if r['t0'] == t0}
        all_syms = set(prev_weights) | set(new_w)
        turnover = 0.5 * sum(abs(new_w.get(s, 0.0) - prev_weights.get(s, 0.0)) for s in all_syms)
        cost = turnover * COST_ONE_WAY * 2
        prev_weights = new_w

        gross = per_day.copy()
        gross[0] *= (1 - cost)          # 成本计入调仓日
        base = nav[i0 - 1] if i0 > 0 else 1.0
        nav[i0:i1] = base * gross

        period_rows.append({'t0': t0, 't1': t1, 'ret': period_ret - cost,
                            'gross_ret': period_ret, 'n': n_eff,
                            'turnover': turnover, 'cost': cost})

    nav_series = pd.Series(nav, index=cal, name=label)
    return nav_series, pd.DataFrame(period_rows), pd.DataFrame(detail_rows)


def yearly_table(nav):
    d = nav.copy()
    years = {}
    for y in sorted(set(d.index.year)):
        seg = d[d.index.year == y]
        base = d[d.index < seg.index[0]]
        start = base.iloc[-1] if len(base) else seg.iloc[0]
        end = seg.iloc[-1]
        peak = np.maximum.accumulate(seg.values)
        dd = ((seg.values - peak) / peak).min()
        years[y] = (end, end / start - 1, dd)
    return years


def summarize(nav, label):
    total = nav.iloc[-1] / nav.iloc[0] - 1
    yrs = len(nav) / 244
    ann = (1 + total) ** (1 / yrs) - 1 if total > -1 else np.nan
    peak = np.maximum.accumulate(nav.values)
    dd = ((nav.values - peak) / peak).min()
    ret = nav.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(244) if ret.std() > 0 else np.nan
    print(f'\n【{label}】')
    print(f'  总收益: {total*100:,.1f}%   年化: {ann*100:.2f}%   最大回撤: {dd*100:.1f}%   夏普: {sharpe:.2f}')
    yt = yearly_table(nav)
    print('  年度: ' + '  '.join(f'{y}:{r*100:+.0f}%' for y, (_, r, _) in yt.items()))
    return total, ann, dd


# ---------------------------------------------------------------- 主流程
def main():
    global START, END
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-cache', action='store_true')
    parser.add_argument('--start', default=START, help='回测开始日期 默认2015-01-01')
    parser.add_argument('--end', default=END, help='回测结束日期 默认2025-12-31')
    args = parser.parse_args()
    START, END = args.start, args.end

    if args.no_cache:
        build_cache()
    d = load_cache()
    if d is None:
        build_cache()
        d = load_cache()

    cal = d['cal']
    cal = cal[(cal >= pd.Timestamp(START)) & (cal <= pd.Timestamp(END))]
    print(f'区间: {START} ~ {END}，交易日 {len(cal)} 天')

    nav10, periods10, detail10 = simulate(d, cal, TOP_N, 'bottom10')
    nav100, _, _ = simulate(d, cal, TOP_N_REF, 'bottom100')

    t10, a10, dd10 = summarize(nav10, f'纯信号·全指最小{TOP_N}只·月度等权（无引擎摩擦）')
    t10c = None
    # 含成本版本（成本已含在periods中，单独重算一条净值）
    print()
    summarize(nav100, f'对照组·全指最小{TOP_N_REF}只·月度等权')

    print('\n' + '=' * 70)
    print('  与引擎回测对比（引擎: 总收益14.95% / 年化1.32% / 最大回撤-75.55%）')
    print('=' * 70)

    # 年度对比表
    yt10 = yearly_table(nav10)
    print(f'\n  {"年份":<6}{"纯信号收益":>12}{"纯信号回撤":>12}')
    for y, (_, r, dd) in yt10.items():
        print(f'  {y:<6}{r*100:>11.1f}%{dd*100:>11.1f}%')

    # 诊断：问题持仓
    det = detail10.copy()
    det['year'] = pd.to_datetime(det['t0']).dt.year
    bad = det[(det['data_end_frozen']) | (det['period_ret'] < -0.3)]
    print(f'\n  问题持仓统计（退市/长停冻结 或 单期亏损>30%）:')
    print(f'    总持仓期数: {len(det)}, 问题期数: {len(bad)} ({len(bad)/len(det)*100:.1f}%)')
    clean = det[~det.index.isin(bad.index)]
    print(f'    问题持仓平均收益: {bad["period_ret"].mean()*100:.1f}% vs 正常: {clean["period_ret"].mean()*100:.1f}%')

    # 最惨的25笔持仓
    worst = det.nsmallest(25, 'period_ret')
    for _, r in worst.iterrows():
        flags = []
        if r['data_end_frozen']:
            flags.append('数据终结(退市/长停)')
        if r['stale_at_select']:
            flags.append('选入时已停牌')
        if r['entry_price_raw'] < 2:
            flags.append(f"低价{r['entry_price_raw']:.2f}元")
        print(f"    {pd.Timestamp(r['t0']).date()} {r['symbol']:<10} 市值{r['cap_yi']:>6.2f}亿 "
              f"期间{r['period_ret']*100:>+7.1f}%  {' '.join(flags)}")

    # 最好的10笔（数据膨胀检查：若出现离谱暴涨说明仍有脏数据）
    print('\n  最好的10笔（检查是否有脏数据虚增）:')
    best = det.nlargest(10, 'period_ret')
    for _, r in best.iterrows():
        print(f"    {pd.Timestamp(r['t0']).date()} {r['symbol']:<10} 市值{r['cap_yi']:>6.2f}亿 "
              f"期间{r['period_ret']*100:>+8.1f}%")

    # 按年统计问题持仓占比
    print('\n  各年问题持仓占比:')
    for y in sorted(set(det['year'])):
        seg = det[det['year'] == y]
        b = seg[(seg['data_end_frozen']) | (seg['period_ret'] < -0.3)]
        print(f'    {y}: {len(b)}/{len(seg)} ({len(b)/max(len(seg),1)*100:.0f}%) '
              f'平均收益 {seg["period_ret"].mean()*100:+.1f}%')

    # 保存明细
    out_csv = os.path.join(ROOT, 'DATA', 'small_cap_gap_detail.csv')
    det.to_csv(out_csv, index=False, encoding='utf-8-sig')
    print(f'\n  持仓明细已保存: {out_csv}')


if __name__ == '__main__':
    main()
