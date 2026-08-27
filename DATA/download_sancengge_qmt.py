# -*- coding: utf-8 -*-
"""用 QMT 数据为三层阁策略预填 10 年数据缓存

三层阁的 DataFetcher 从 data_cache/*.pkl 读取缓存(key: hist_/nav_)。
此脚本用 QMT 日线填充缓存：
  - hist_{code}_{start}_{end}.pkl : date/open/close/high/low/volume
  - nav_{code}_{start}_{end}.pkl  : date/nav (QMT无基金净值, 用close近似)

注意: 必须用不复权(dividend_type='none')价格，与 akshare 单位净值口径一致，
否则分红基金会因后复权价>>单位净值出现虚假"溢价"，破坏折价计算。
真实 NAV 由 DATA/download_sancengge_nav.py (akshare) 提供，本脚本不会覆盖。
"""
import os
import sys
import time
import pickle
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
# 项目根目录加入路径（脚本位于 DATA/ 下，父目录即项目根）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from xtquant import xtdata
from example.sancengge_strategy.config import ALL_FUNDS, BENCHMARK_CODE

START = '2016-01-01'
END = '2026-06-30'
CACHE_DIR = os.path.join(PROJECT_ROOT,
                         'example', 'sancengge_strategy', 'data_cache')
os.makedirs(CACHE_DIR, exist_ok=True)


def to_qmt_code(code: str) -> str:
    """6位基金代码 → QMT代码(带交易所后缀)"""
    if code.startswith(('5', '6')):
        return f"{code}.SH"
    return f"{code}.SZ"


def download_one(code: str):
    qmt_code = to_qmt_code(code)
    start_time = START.replace('-', '')
    end_time = END.replace('-', '')
    try:
        xtdata.download_history_data(
            stock_code=qmt_code, period='1d',
            start_time='19900101', end_time='',
            incrementally=False,
        )
        history = xtdata.get_market_data_ex(
            [], [qmt_code], period='1d',
            start_time=start_time, end_time=end_time,
            count=-1, dividend_type='none',   # 不复权，与单位净值口径一致
        )
        if qmt_code not in history:
            return None
        df = history[qmt_code]
        if df is None or df.empty:
            return None
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, format='%Y%m%d')

        df = df[(df.index >= pd.Timestamp(START)) & (df.index <= pd.Timestamp(END))]
        if df.empty:
            return None

        out = pd.DataFrame({
            'date': df.index,
            'open': df['open'].astype(float),
            'close': df['close'].astype(float),
            'high': df['high'].astype(float),
            'low': df['low'].astype(float),
        })
        # volume 归一化为「手」: volume = amount / (close * 100)
        amount = df['amount'].astype(float)
        close = df['close'].astype(float)
        raw_volume = df['volume'].astype(float)
        with np.errstate(divide='ignore', invalid='ignore'):
            normalized = amount / (close * 100.0)
        out['volume'] = np.where(
            (amount > 0) & (close > 0) & np.isfinite(normalized),
            normalized, raw_volume,
        )
        out = out.reset_index(drop=True)
        return out
    except Exception as e:
        print(f'  {code}: FAIL {type(e).__name__}: {e}', flush=True)
        return None


def save_cache(key, df):
    path = os.path.join(CACHE_DIR, f'{key}.pkl')
    with open(path, 'wb') as f:
        pickle.dump(df, f)


def main():
    codes = [f.code for f in ALL_FUNDS] + [BENCHMARK_CODE]
    total = len(codes)
    ok_hist = 0
    ok_nav = 0

    for i, code in enumerate(codes, 1):
        hist_key = f'hist_{code}_{START}_{END}'
        nav_key = f'nav_{code}_{START}_{END}'
        hist_path = os.path.join(CACHE_DIR, f'{hist_key}.pkl')
        nav_path = os.path.join(CACHE_DIR, f'{nav_key}.pkl')

        if os.path.exists(hist_path) and os.path.exists(nav_path):
            print(f'[{i}/{total}] {code} 已缓存', flush=True)
            ok_hist += 1
            ok_nav += 1
            continue

        df = download_one(code)
        if df is None or len(df) == 0:
            print(f'[{i}/{total}] {code}: 无数据', flush=True)
            continue

        save_cache(hist_key, df)
        ok_hist += 1

        # nav 缓存: 已存在真实NAV则保留(akshare)，缺失才用close近似
        if not os.path.exists(nav_path):
            nav_df = df[['date', 'close']].rename(columns={'close': 'nav'}).copy()
            save_cache(nav_key, nav_df)
            ok_nav += 1

        print(f'[{i}/{total}] {code}: OK ({len(df)} bars, '
              f'{df["date"].iloc[0].date()}~{df["date"].iloc[-1].date()})', flush=True)
        time.sleep(0.05)

    print(f'\n完成: hist {ok_hist}/{total}, nav {ok_nav}/{total}', flush=True)
    print(f'缓存目录: {CACHE_DIR}', flush=True)


if __name__ == '__main__':
    main()
