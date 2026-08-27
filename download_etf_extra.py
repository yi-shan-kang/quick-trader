# -*- coding: utf-8 -*-
"""下载聚宽池缺失的4只ETF数据并合并到10年缓存

聚宽52只池 vs 我们45只池 的差异:
- 159100.SZ 巴西ETF
- 159329.SZ 沙特ETF
- 159378.SZ 通用航空ETF
- 159206.SZ 卫星ETF
"""
import os
import sys
import time
import pickle
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xtquant import xtdata

NEW_ETFS = [('159100', 'SZ'), ('159329', 'SZ'), ('159378', 'SZ'), ('159206', 'SZ')]
START = '2014-08-01'
END = '2025-12-31'


def download_etf(code, exchange):
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
    amount = df['amount'].astype(float)
    close = df['close'].astype(float)
    raw_volume = df['volume'].astype(float)
    with np.errstate(divide='ignore', invalid='ignore'):
        normalized = amount / (close * 100.0)
    out['volume'] = np.where(
        (amount > 0) & (close > 0) & np.isfinite(normalized),
        normalized, raw_volume,
    )
    out = out.sort_index()
    return out


def main():
    # 加载现有缓存
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'DATA', 'cache', 'etf_data_cache_qmt_10y.pkl')
    with open(cache_path, 'rb') as f:
        all_data = pickle.load(f)
    print(f'现有缓存: {len(all_data)} 只', flush=True)

    for code, exchange in NEW_ETFS:
        symbol = f"{code}.{exchange}"
        if symbol in all_data:
            print(f'{symbol} 已存在，跳过', flush=True)
            continue
        try:
            df = download_etf(code, exchange)
            if df is not None and len(df) > 0:
                all_data[symbol] = df
                print(f'OK {symbol} ({len(df)} bars, {df.index[0].date()}~{df.index[-1].date()})', flush=True)
            else:
                print(f'EMPTY {symbol}', flush=True)
        except Exception as e:
            print(f'FAIL {symbol}: {type(e).__name__} {e}', flush=True)
        time.sleep(0.2)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'DATA', 'cache', 'etf_data_cache_qmt_10y.pkl')
    with open(out_path, 'wb') as f:
        pickle.dump(all_data, f)
    print(f'缓存已更新: {len(all_data)} 只 -> {out_path}', flush=True)


if __name__ == '__main__':
    main()
