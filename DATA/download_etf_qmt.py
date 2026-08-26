#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""使用 MiniQMT (xtquant) 下载 ETF 历史日线数据（后复权）

与 download_etf_tencent.py 的区别：
- 数据源为 MiniQMT (xtquant.get_market_data_ex, dividend_type='back')
- 使用 QMT 权威的 amount（成交额）字段反推统一的「手」成交量：
    volume = amount / close / 100
  这样策略中 close * volume * 100 能精确还原成交额 amount，
  避免 QMT 不同 ETF（尤其部分 QDII）volume 单位不一致导致的流动性估算偏差。
"""
import os
import sys
import time
import pickle
import pandas as pd
import numpy as np

from xtquant import xtdata

sys.stdout.reconfigure(encoding='utf-8')

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

START = '2014-08-01'
END = '2025-12-31'


def download_etf(code, exchange):
    """从 MiniQMT 下载 ETF 后复权日线，volume 归一化为「手」"""
    symbol = f"{code}.{exchange}"
    start_time = START.replace('-', '')
    end_time = END.replace('-', '')

    xtdata.download_history_data(
        stock_code=symbol, period='1d',
        start_time='19900101', end_time='',
        incrementally=False,
    )
    history = xtdata.get_market_data_ex(
        [], [symbol], period='1d',
        start_time=start_time, end_time=end_time,
        count=-1, dividend_type='back',
    )

    if symbol not in history:
        return None

    df = history[symbol]
    if df is None or df.empty:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, format='%Y%m%d')

    df = df[(df.index >= pd.Timestamp(START)) & (df.index <= pd.Timestamp(END))]
    if df.empty:
        return None

    out = pd.DataFrame(index=df.index)
    out['open'] = df['open'].astype(float)
    out['high'] = df['high'].astype(float)
    out['low'] = df['low'].astype(float)
    out['close'] = df['close'].astype(float)

    # 归一化成交量：用 amount（成交额）反推统一「手」单位
    # 使 close * volume * 100 == amount（精确）
    amount = df['amount'].astype(float)
    close = df['close'].astype(float)
    raw_volume = df['volume'].astype(float)
    with np.errstate(divide='ignore', invalid='ignore'):
        normalized = amount / (close * 100.0)
    out['volume'] = np.where(
        (amount > 0) & (close > 0) & np.isfinite(normalized),
        normalized,
        raw_volume,
    )

    out = out.sort_index()
    return out


def main():
    all_data = {}
    total = len(ETFS)

    for i, (code, exchange) in enumerate(ETFS, 1):
        symbol = f"{code}.{exchange}"
        try:
            df = download_etf(code, exchange)
            if df is not None and len(df) > 0:
                all_data[symbol] = df
                print(f'[{i}/{total}] OK {symbol} ({len(df)} bars)', flush=True)
            else:
                print(f'[{i}/{total}] EMPTY {symbol}', flush=True)
        except Exception as e:
            print(f'[{i}/{total}] FAIL {symbol}: {type(e).__name__} {e}', flush=True)
        time.sleep(0.1)

    if not all_data:
        print('错误: 无可用数据', flush=True)
        return

    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, 'etf_data_cache_qmt_10y.pkl')
    with open(out_path, 'wb') as f:
        pickle.dump(all_data, f)
    print(f'Saved {len(all_data)} ETFs to {out_path}', flush=True)


if __name__ == '__main__':
    main()
