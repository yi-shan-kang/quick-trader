#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""下载ETF历史数据并保存为pickle文件"""
import os
import sys
import time
import pickle
import pandas as pd
import akshare as ak

ETFS = [
    '159941','159509','513500','513520','513030','513080',
    '518880','159980','161226','159985','159981','501018',
    '511090','513130','520500','513970','513690','159915',
    '563300','563360','510410','515210','562800','159928',
    '512690','159992','588220','159819','159851','515030',
    '516160','512710','515220','512880','516510','515050',
    '512170','159870','159611','159995','515790','159755',
    '515000','562500','159326','511880',
]

COL_MAP = {
    '日期': 'datetime', '开盘': 'open', '最高': 'high',
    '最低': 'low', '收盘': 'close', '成交量': 'volume', '成交额': 'amount',
}

all_data = {}
total = len(ETFS)

for i, code in enumerate(ETFS):
    for retry in range(3):
        try:
            df = ak.fund_etf_hist_em(
                symbol=code, period='daily',
                start_date='20240801', end_date='20251231', adjust='hfq'
            )
            if df is not None and not df.empty:
                df = df.rename(columns=COL_MAP)
                df['datetime'] = pd.to_datetime(df['datetime'])
                df = df.set_index('datetime').sort_index()
                for c in ['open', 'high', 'low', 'close', 'volume']:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
                df = df.dropna(subset=['open', 'high', 'low', 'close'])
                suffix = '.SZ' if code.startswith(('1', '0', '3')) else '.SH'
                all_data[code + suffix] = df
                print(f'[{i+1}/{total}] OK {code} ({len(df)} bars)', flush=True)
                break
            else:
                print(f'[{i+1}/{total}] EMPTY {code}', flush=True)
                break
        except Exception as e:
            if retry < 2:
                time.sleep(3)
            else:
                print(f'[{i+1}/{total}] FAIL {code}: {type(e).__name__}', flush=True)
    time.sleep(1)

cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
os.makedirs(cache_dir, exist_ok=True)
out_path = os.path.join(cache_dir, 'etf_data_cache.pkl')
with open(out_path, 'wb') as f:
    pickle.dump(all_data, f)
print(f'Saved {len(all_data)} ETFs to {out_path}', flush=True)
