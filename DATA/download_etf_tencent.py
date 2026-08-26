#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""使用腾讯财经API下载ETF历史数据"""
import os
import sys
import time
import json
import pickle
import requests
import pandas as pd
import numpy as np

ETFS = [
    ('159941', 'SZ'), ('159509', 'SZ'), ('513500', 'SH'), ('513520', 'SH'),
    ('513030', 'SH'), ('513080', 'SH'), ('518880', 'SH'), ('159980', 'SZ'),
    ('161226', 'SZ'), ('159985', 'SZ'), ('159981', 'SZ'), ('501018', 'SH'),
    ('511090', 'SH'), ('513130', 'SH'), ('520500', 'SH'), ('513970', 'SH'),
    ('513690', 'SH'), ('159915', 'SZ'), ('563300', 'SH'), ('563360', 'SH'),
    ('510410', 'SH'), ('515210', 'SH'), ('562800', 'SH'), ('159928', 'SZ'),
    ('512690', 'SH'), ('159992', 'SZ'), ('588220', 'SH'), ('159819', 'SZ'),
    ('159851', 'SZ'), ('515030', 'SH'), ('516160', 'SH'), ('512710', 'SH'),
    ('515220', 'SH'), ('512880', 'SH'), ('516510', 'SH'), ('515050', 'SH'),
    ('512170', 'SH'), ('159870', 'SZ'), ('159611', 'SZ'), ('159995', 'SZ'),
    ('515790', 'SH'), ('159755', 'SZ'), ('515000', 'SH'), ('562500', 'SH'),
    ('159326', 'SZ'), ('511880', 'SH'),
]


def download_etf(code, exchange, start='2024-08-01', end='2025-12-31'):
    """从腾讯财经下载ETF日线数据（前复权）"""
    symbol = f"{exchange.lower()}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{start},{end},640,qfq"

    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None

    data = r.json()
    if 'data' not in data:
        return None

    symbol_data = data['data'].get(symbol, {})
    klines = symbol_data.get('qfqday') or symbol_data.get('day')

    if not klines:
        return None

    rows = []
    for k in klines:
        if len(k) >= 6:
            rows.append({
                'datetime': pd.to_datetime(k[0]),
                'open': float(k[1]),
                'close': float(k[2]),
                'high': float(k[3]),
                'low': float(k[4]),
                'volume': float(k[5]) if k[5] else 0.0,
            })

    if not rows:
        return None

    df = pd.DataFrame(rows).set_index('datetime').sort_index()
    return df


def main():
    all_data = {}
    total = len(ETFS)

    for i, (code, exchange) in enumerate(ETFS):
        symbol = f"{code}.{exchange}"
        try:
            df = download_etf(code, exchange)
            if df is not None and len(df) > 0:
                all_data[symbol] = df
                print(f'[{i+1}/{total}] OK {symbol} ({len(df)} bars)', flush=True)
            else:
                print(f'[{i+1}/{total}] EMPTY {symbol}', flush=True)
        except Exception as e:
            print(f'[{i+1}/{total}] FAIL {symbol}: {type(e).__name__}', flush=True)
        time.sleep(0.2)

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, 'etf_data_cache.pkl')
    with open(out_path, 'wb') as f:
        pickle.dump(all_data, f)
    print(f'Saved {len(all_data)} ETFs to {out_path}', flush=True)


if __name__ == '__main__':
    main()
