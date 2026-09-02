# -*- coding: utf-8 -*-
"""将 etf_data_cache_qmt_10y.pkl 转换为 QMT 磁盘缓存分片

用途：QMT 未连接时，回测引擎通过 .cache/QMTData/market/{symbol}/{year}_1d.parquet
磁盘缓存读取行情。此脚本把已下载好的 46 只 ETF 后复权日线写入该缓存格式，
使七星高照ETF轮动策略可在 QMT 离线状态下直接回测。

用法:
    python DATA/seed_etf_qmt_cache.py
"""
import os
import sys
import pickle

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from core.cache import cache_manager

PKL_PATH = os.path.join(PROJECT_ROOT, 'DATA', 'cache', 'etf_data_cache_qmt_10y.pkl')
NAMESPACE = 'QMTDataProcessor'


def main():
    if not os.path.exists(PKL_PATH):
        print(f'缓存文件不存在: {PKL_PATH}')
        return 1

    with open(PKL_PATH, 'rb') as f:
        all_data = pickle.load(f)

    total = len(all_data)
    ok = 0
    for i, (symbol, df) in enumerate(all_data.items(), 1):
        if not isinstance(df.index, __import__('pandas').DatetimeIndex):
            df.index = __import__('pandas').to_datetime(df.index)
        written = cache_manager.disk_cache.put_yearly_from_df(
            NAMESPACE, symbol, '1d', df, skip_existing=True
        )
        if written:
            ok += 1
            print(f'[{i}/{total}] {symbol}: 写入 {len(written)} 个年份分片', flush=True)
        else:
            print(f'[{i}/{total}] {symbol}: 无新分片写入（可能已存在）', flush=True)

    print(f'完成: {ok}/{total} 只ETF已写入磁盘缓存')
    return 0


if __name__ == '__main__':
    sys.exit(main())
