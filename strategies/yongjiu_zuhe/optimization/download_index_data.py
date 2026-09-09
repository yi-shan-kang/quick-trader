# -*- coding: utf-8 -*-
"""下载长历史指数数据（新浪域，已验证可用）供指数拟合回测使用

输出到 index_fit_data/ 目录：
- hs300.csv      沪深300（价格指数，2002起）
- zz500.csv      中证500（价格指数，2005起）
- bond.csv       上证国债指数（财富指数，2003起）
- gold_london.csv  伦敦现货金 XAU（2006-09起）
- gold_shfe.csv    上期所沪金主力 AU0（2008起）
"""
import os
import time

import akshare as ak
import pandas as pd

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index_fit_data')
os.makedirs(OUT_DIR, exist_ok=True)


def norm(df, date_col, close_col, tag):
    df = df[[date_col, close_col]].rename(columns={date_col: 'date', close_col: 'close'})
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna().drop_duplicates('date').sort_values('date').reset_index(drop=True)
    fp = os.path.join(OUT_DIR, tag + '.csv')
    df.to_csv(fp, index=False, encoding='utf-8-sig')
    print(f'{tag}: {len(df)} rows  {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')
    return df


def get_index(symbol, tag, start_hint=''):
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
        if start_hint:
            df = df[df['date'] >= start_hint]
        norm(df, 'date', 'close', tag)
    except Exception as e:
        print(f'{tag}: FAIL {type(e).__name__}: {str(e)[:80]}')


def get_foreign(symbol, tag):
    try:
        df = ak.futures_foreign_hist(symbol=symbol)
        norm(df, 'date', 'close', tag)
    except Exception as e:
        print(f'{tag}: FAIL {type(e).__name__}: {str(e)[:80]}')


get_index('sh000300', 'hs300')
get_index('sh000905', 'zz500')
get_index('sh000012', 'bond')
get_foreign('XAU', 'gold_london')
get_index('sh000922', 'hslt_red')  # 中证红利（新浪只到2019，仅备份）
print('done')