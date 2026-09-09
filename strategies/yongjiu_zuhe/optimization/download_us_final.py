# -*- coding: utf-8 -*-
"""美股线长历史数据（最终版）
- us_sp500.csv  Shiller S&P500 月度(1871起) 价格+股息 -> 全收益, 按月均摊转日频
- us_bond10.csv FRED DGS10 10Y美债(1965起, DGS10 1962起日频) 久期9合成 -> 已有保留
- us_cash3m.csv FRED TB3MS 3M国库券 -> 已有保留
- us_gold.csv   datasets/gold-prices 月度伦敦金(1833起) -> 均摊转日频
"""
import os

import numpy as np
import pandas as pd
import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index_fit_data')
UA = {'User-Agent': 'Mozilla/5.0'}


def get_retry(url, tries=5):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=90)
            if r.status_code == 200:
                return r
            last = f'http={r.status_code}'
        except Exception as e:
            last = f'{type(e).__name__} {str(e)[:50]}'
        print('retry', i + 1, url[:60], last)
        time.sleep(8)
    raise RuntimeError('download failed: ' + url)


def monthly_to_daily(monthly_df, date_col, val_col):
    """月度序列按日展频: 月末值 -> 日频; 月内日均摊月度收益"""
    df = monthly_df[[date_col, val_col]].copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col)
    # 月度收益
    df['ret'] = df[val_col].pct_change()
    # 构建日频日历: 从首月1日到末月末
    days = pd.date_range(df[date_col].iloc[0].replace(day=1), df[date_col].iloc[-1], freq='D')
    # 每个交易日属于哪个月
    mo = days.to_period('M').to_timestamp(how='end')  # 月末日期 -> 与月度数据的月末对齐
    # 构建月末->收益 映射
    d = df.set_index(pd.to_datetime(df[date_col]).dt.to_period('M'))
    ret_map = d['ret'].to_dict()
    # 对齐该月收益(用当月实际收益), 逐日均摊: (1+r)^(1/m) - 1
    mkey = days.to_period('M')
    rfull = pd.Series([ret_map.get(k, 0.0) for k in mkey], index=days)
    # 每月的交易日数(此处用日数近似)
    md = mkey.to_timestamp(how='end')
    cnt = md.value_counts()
    denom = md.map(cnt).astype(float)
    rdaily = (1.0 + rfull) ** (1.0 / denom) - 1.0
    close = 100.0 * (1.0 + rdaily).cumprod()
    out = pd.DataFrame({'date': days.strftime('%Y-%m-%d'), 'close': close})
    return out


# ---- 1. Shiller SP500 => 全收益月度 ----
r = get_retry('https://raw.githubusercontent.com/datasets/s-and-p-500/master/data/data.csv')
sh = pd.read_csv(pd.io.common.BytesIO(r.content))
sh['Date'] = pd.to_datetime(sh['Date'])
price = sh['SP500'].astype(float)
div = sh['Dividend'].astype(float)          # 年化每股股息
# 全收益月度杠杆: 月度总收益 = (P_t + D_t/12) / P_{t-1}
lev = price / price.shift(1) + div / 12.0 / price.shift(1)
lev.iloc[0] = 1.0
nav = 100.0 * lev.cumprod()
sp_m = pd.DataFrame({'date': sh['Date'], 'value': nav})
sp_d = monthly_to_daily(sp_m, 'date', 'value')
sp_d.to_csv(os.path.join(DATA_DIR, 'us_sp500.csv'), index=False, encoding='utf-8-sig')
print(f'us_sp500: OK {len(sp_d)} rows {sp_d["date"].iloc[0]} ~ {sp_d["date"].iloc[-1]}')

# ---- 2. gold monthly -> daily ----
r = requests.get('https://raw.githubusercontent.com/datasets/gold-prices/master/data/monthly.csv',
                 headers=UA, timeout=90)
gd = pd.read_csv(pd.io.common.BytesIO(r.content))
gd.columns = ['date', 'price']
print('gold raw rows', len(gd), gd['date'].iloc[0], gd['date'].iloc[-1])
g_d = monthly_to_daily(gd, 'date', 'price')
g_d.to_csv(os.path.join(DATA_DIR, 'us_gold.csv'), index=False, encoding='utf-8-sig')
print(f'us_gold: OK {len(g_d)} rows {g_d["date"].iloc[0]} ~ {g_d["date"].iloc[-1]}')

print('done')