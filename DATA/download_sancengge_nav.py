# -*- coding: utf-8 -*-
"""用 akshare 下载三层阁基金池的真实净值(NAV)，更新 nav_*.pkl 缓存

QMT 无基金净值数据，之前 nav 用 close 近似(折价恒0)。
本脚本用 akshare 下载真实净值：
  - 定开/封闭基金: fund_open_fund_info_em(symbol=code) 单位净值走势
  - ETF/LOF: fund_etf_fund_info_em(fund=code)
只更新 A股桶定开基的真实净值(它们有折价)；ETF/LOF 折价极小，保持 close 近似即可。
"""
import os
import sys
import time
import pickle

sys.stdout.reconfigure(encoding='utf-8')
# 项目根目录加入路径（脚本位于 DATA/ 下，父目录即项目根）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import akshare as ak

from example.sancengge_strategy.config import ALL_FUNDS

START = '2016-01-01'
END = '2026-06-30'
CACHE_DIR = os.path.join(PROJECT_ROOT,
                         'example', 'sancengge_strategy', 'data_cache')

# 只更新定开/封闭基金(有折价)与黄金LOF；纯ETF折价可忽略
TARGET_CODES = [f.code for f in ALL_FUNDS if f.fund_type == 'closed_end'] + \
               ['161116', '161716', '161119', '161115', '161128']  # LOF

# 完整基金池(为兜底)
ALL_CODES = [f.code for f in ALL_FUNDS]


def fetch_open_nav(code):
    """定开/场外基金净值: fund_open_fund_info_em"""
    df = ak.fund_open_fund_info_em(
        symbol=code, indicator='单位净值走势', period='成立来'
    )
    if df is None or df.empty:
        return None
    df = df.rename(columns={df.columns[0]: 'date', df.columns[1]: 'nav'})
    df['date'] = pd.to_datetime(df['date'])
    df['nav'] = pd.to_numeric(df['nav'], errors='coerce')
    df = df.dropna(subset=['nav']).sort_values('date').reset_index(drop=True)
    return df[['date', 'nav']]


def fetch_etf_nav(code):
    """ETF/LOF 净值: fund_etf_fund_info_em"""
    df = ak.fund_etf_fund_info_em(
        fund=code, start_date=START.replace('-', ''), end_date=END.replace('-', '')
    )
    if df is None or df.empty:
        return None
    df = df.rename(columns={df.columns[0]: 'date', df.columns[1]: 'nav'})
    df['date'] = pd.to_datetime(df['date'])
    df['nav'] = pd.to_numeric(df['nav'], errors='coerce')
    df = df.dropna(subset=['nav']).sort_values('date').reset_index(drop=True)
    return df[['date', 'nav']]


def load_nav_cache(code):
    path = os.path.join(CACHE_DIR, f'nav_{code}_{START}_{END}.pkl')
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return pickle.load(f)
    return None


def save_nav_cache(code, df):
    path = os.path.join(CACHE_DIR, f'nav_{code}_{START}_{END}.pkl')
    with open(path, 'wb') as f:
        pickle.dump(df, f)


def main():
    fund_info_map = {f.code: f for f in ALL_FUNDS}
    ok, fail, skip = 0, 0, 0

    for i, code in enumerate(ALL_CODES, 1):
        fund_info = fund_info_map.get(code)
        try:
            if code in TARGET_CODES:
                if fund_info and fund_info.fund_type == 'closed_end':
                    nav_df = fetch_open_nav(code)
                else:
                    nav_df = fetch_etf_nav(code)
            else:
                # ETF: 折价极小，跳过（保持close近似）
                skip += 1
                print(f'[{i}/{len(ALL_CODES)}] {code}: 跳过(纯ETF无折价)', flush=True)
                continue

            if nav_df is None or len(nav_df) == 0:
                print(f'[{i}/{len(ALL_CODES)}] {code}: 无净值数据', flush=True)
                fail += 1
                continue

            # 与价格缓存对齐范围
            nav_df = nav_df[(nav_df['date'] >= pd.Timestamp(START)) & (nav_df['date'] <= pd.Timestamp(END))]
            if len(nav_df) == 0:
                print(f'[{i}/{len(ALL_CODES)}] {code}: 范围内无数据', flush=True)
                fail += 1
                continue

            save_nav_cache(code, nav_df)
            print(f'[{i}/{len(ALL_CODES)}] {code}: OK {len(nav_df)}条 '
                  f'({nav_df["date"].iloc[0].date()}~{nav_df["date"].iloc[-1].date()})', flush=True)
            ok += 1
        except Exception as e:
            print(f'[{i}/{len(ALL_CODES)}] {code}: FAIL {type(e).__name__} {e}', flush=True)
            fail += 1
        time.sleep(0.5)

    print(f'\n完成: OK={ok} FAIL={fail} SKIP={skip}', flush=True)


if __name__ == '__main__':
    main()
