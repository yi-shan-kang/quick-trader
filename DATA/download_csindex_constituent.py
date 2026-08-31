# -*- coding: utf-8 -*-
"""从中证指数官网下载指数成分股

功能：下载中证指数官网公开的「当前」成分股文件（{symbol}cons.xls），
      解析成分券代码并转成项目约定格式。

⚠️ 重要局限：
中证指数官网的免费公开接口只提供「当前」成分股（最新一期样本列表）。
「历史」成分股（含已剔除股票）属于付费数据（资讯商专用 FTP），
免费 HTTP 接口不提供，直接下载带日期的历史文件会返回 404。

因此本脚本只能抓取当前成分股，无法抓取完整历史成分股。
要获取历史成分股（避免幸存者偏差），可靠途径是：
  1. 聚宽 get_index_stocks('000852.XSHG', date) —— 需聚宽账号
  2. tushare index_weight / index_member —— 需积分
  3. 中证指数官网付费 FTP 数据服务

用法：
    python download_csindex_constituent.py 000852
"""
import os
import sys
import datetime

import pandas as pd
import requests
from io import BytesIO


CSINDEX_CONS_URL = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/"
    "file/autofile/cons/{symbol}cons.xls"
)

OUTPUT_DIR = os.path.join('.cache', 'JQData', 'index_constituent')

# 交易所 -> QMT 后缀
EXCHANGE_SUFFIX = {
    '上海证券交易所': 'SH',
    '深圳证券交易所': 'SZ',
    '北京证券交易所': 'BJ',
}


def download_constituent(symbol: str) -> pd.DataFrame:
    """下载中证指数官网当前成分股文件，返回解析后的 DataFrame

    Args:
        symbol: 指数代码（6位，如 '000852'）

    Returns:
        DataFrame，含 'date' 和 'codes' 两列（codes 为 QMT 格式代码列表）
    """
    url = CSINDEX_CONS_URL.format(symbol=symbol)
    print(f'下载: {url}')
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
    r.raise_for_status()

    df = pd.read_excel(BytesIO(r.content))
    print(f'  原始数据: {len(df)} 行, 列: {list(df.columns)}')

    # 定位关键列
    date_col = next((c for c in df.columns if '日期' in str(c)), None)
    code_col = next((c for c in df.columns if '成份券代码' in str(c) or '成分券代码' in str(c)), None)
    exch_col = next((c for c in df.columns if '交易所' in str(c) and '英文' not in str(c)), None)

    if code_col is None:
        raise ValueError(f'未找到成分券代码列: {list(df.columns)}')

    codes = []
    for _, row in df.iterrows():
        code = str(row[code_col]).strip()
        if not code or code == 'nan':
            continue
        code = code.zfill(6)
        suffix = 'SH' if code.startswith(('6', '5', '9')) else 'SZ'
        if exch_col:
            exch = str(row[exch_col])
            suffix = EXCHANGE_SUFFIX.get(exch, suffix)
        codes.append(f'{code}.{suffix}')

    codes = sorted(set(codes))
    date_str = '20260101'
    if date_col:
        try:
            d = pd.to_datetime(df[date_col].iloc[0])
            date_str = d.strftime('%Y-%m-%d')
        except Exception:
            pass

    result = pd.DataFrame({'date': [date_str], 'codes': [codes]})
    return result


def save_csv(df: pd.DataFrame, symbol: str):
    """保存为项目约定格式的 CSV（date, codes）"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    index_code = symbol.zfill(6)
    # 指数代码后缀：中证/上证指数(000/930/931开头) -> .SH，深证指数(399开头) -> .SZ
    suffix = 'SZ' if index_code.startswith('399') else 'SH'
    out_path = os.path.join(OUTPUT_DIR, f'{index_code}.{suffix}.csv')
    df.to_csv(out_path, index=False, encoding='utf-8')
    print(f'已保存: {out_path} ({len(df.iloc[0]["codes"])} 只成分股)')


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else '000852'
    symbol = symbol.split('.')[0].zfill(6)
    df = download_constituent(symbol)
    save_csv(df, symbol)
    print('完成。注意：这是「当前」成分股，不含历史已剔除股票。')


if __name__ == '__main__':
    main()
