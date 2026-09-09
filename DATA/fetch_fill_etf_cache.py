# -*- coding: utf-8 -*-
"""用 akshare 补齐永久组合回测所需的 ETF 本地缓存

背景：QMT 当前未连接，部分 ETF 在 .cache/QMTData/market/{symbol}/{year}_1d.parquet
没有缓存。本脚本通过 akshare 新浪接口下载行情，写入与 QMTDataProcessor 一致的
缓存格式（命名空间 QMTDataProcessor，列 open/high/low/close/volume，DatetimeIndex），
使回测引擎离线也可取数。

覆盖策略：
- 无缓存标的（510300/515080/511010/515100/513100/161716/511360）：
  补齐 2019~2026 全部年份。
- 已有部分缓存标的（513500/518880/511880/511090，缓存到 2025，QMT 后复权口径）：
  仅补 2026。新浪为不复权价，用『2025 年最后交易日 缓存后复权收盘 / 新浪复权收盘』
  作为比例系数平移至 2026 后复权近似价，保持价格水平连续。
"""
import os
import sys
import warnings

import pandas as pd

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.cache import cache_manager

NS = 'QMTDataProcessor'
SUFFIX = '1d'
FIRST_YEAR = 2019   # 预留缓冲，保证回测起点前有缓存（覆盖校验要求 disk_start <= req_start）
END_YEAR = 2026

# {缓存symbol: (akshare symbol 前缀, 已有缓存年份列表 或 None=未知)}
TARGETS = {
    '510300.SH': 'sh510300',   # 沪深300ETF
    '515080.SH': 'sh515080',   # 中证红利ETF
    '511010.SH': 'sh511010',   # 10年国债ETF
    '515100.SH': 'sh515100',   # 红利低波100ETF
    '513100.SH': 'sh513100',   # 纳指100ETF
    '161716.SZ': 'sz161716',   # 招商双债LOF
    '511360.SH': 'sh511360',   # 短融ETF
}

# 已有缓存但缺 2026 的标的（保留历史后复权口径，仅补 2026）
PARTIAL_TARGETS = {
    '513500.SH': 'sh513500',   # 标普500ETF
    '518880.SH': 'sh518880',   # 黄金ETF
    '511880.SH': 'sh511880',   # 货币ETF
    '511090.SH': 'sh511090',   # 30年国债ETF
}


def _list_years(symbol):
    return cache_manager.disk_cache.list_yearly_files(NS, symbol, SUFFIX)


def _write_year(symbol, year, df):
    cache_manager.disk_cache.put_yearly(NS, symbol, year, SUFFIX, df)


def _load(year_df_path_frames):
    pass


def main():
    ok, fail = [], []
    import akshare as ak

    # 1) 全新补齐（2019-2026）
    for symbol, aks in TARGETS.items():
        try:
            raw = ak.fund_etf_hist_sina(symbol=aks)
            if raw is None or raw.empty:
                raise RuntimeError('空数据')
            df = raw[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df = df[df.index.year >= FIRST_YEAR].astype(float)
            df.index.name = None
            df = df[~df.index.duplicated(keep='last')].sort_index()
            for y in range(FIRST_YEAR, END_YEAR + 1):
                _write_year(symbol, y, df)
            years = _list_years(symbol)
            ok.append(f'{symbol} {aks}: {raw.shape[0]} 行, 缓存年份 {years[0]}~{years[-1]}')
        except Exception as e:
            fail.append(f'{symbol} {aks}: {type(e).__name__}: {e}')

    # 2) 已有缓存仅补 2026（新浪价 -> 后复权近似价）
    for symbol, aks in PARTIAL_TARGETS.items():
        try:
            cached = cache_manager.disk_cache.get_yearly_range(
                NS, symbol, [2024, 2025], SUFFIX)
            if cached is None or cached.empty:
                raise RuntimeError('无 2024-2025 基准缓存')
            last_cached_date = cached.index.max()
            last_cached = cached.loc[last_cached_date, 'close']

            raw = ak.fund_etf_hist_sina(symbol=aks)
            if raw is None or raw.empty:
                raise RuntimeError('空数据')
            df = raw[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df.index.name = None
            df = df[~df.index.duplicated(keep='last')].sort_index()

            # 2025 年末：新浪收盘 vs 缓存后复权收盘，求比例系数
            raw_2025_end = df.loc[:'2025-12-31'].iloc[-1]['close']
            scale = float(last_cached) / float(raw_2025_end)
            y26 = df[df.index.year == END_YEAR].copy()
            for col in ('open', 'high', 'low', 'close'):
                y26[col] = y26[col] * scale
            if y26.empty:
                raise RuntimeError('新浪无 2026 数据')
            _write_year(symbol, END_YEAR, y26)
            years = _list_years(symbol)
            ok.append(f'{symbol} {aks}: 补 2026({len(y26)} 行, scale={scale:.6f}), 缓存年份 {years[0]}~{years[-1]}')
        except Exception as e:
            fail.append(f'{symbol} {aks}: {type(e).__name__}: {e}')

    print('---- 成功 ----')
    for line in ok:
        print(' ', line)
    print('---- 失败 ----')
    for line in fail:
        print(' ', line)
    print(f'TOTALS: ok={len(ok)} fail={len(fail)}')


if __name__ == '__main__':
    main()