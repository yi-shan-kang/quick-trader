# -*- coding: utf-8 -*-
"""逐只下载中证全指(000985)历史成分股的财务数据（Balance + Pershareindex）

- 从本地 CSV 读取中证全指历史成分股并集
- 逐只下载（不走 download_financial_data2 批量，避免静默中断）
- 跳过已有缓存（已有的约1000只中证1000成分股会自动命中缓存）
"""
import os
import sys
import ast
import logging
import time

os.environ['QMT_LOG_LEVEL'] = 'WARNING'

import pandas as pd

from core.data.qmt import QMTDataProcessor

CSV_PATH = os.path.join('.cache', 'JQData', 'index_constituent', '000985.SH.csv')
START = '20200101'
END = '20260831'
TABLES = ['Balance', 'Pershareindex']


def main():
    # 1. 读中证全指历史成分股并集
    df = pd.read_csv(CSV_PATH)
    all_stocks = set()
    for codes_str in df['codes']:
        codes = ast.literal_eval(codes_str)
        all_stocks.update(codes)
    stock_list = sorted(all_stocks)
    print(f'中证全指历史成分股并集: {len(stock_list)} 只')
    sys.stdout.flush()

    # 2. 逐只下载财务数据
    proc = QMTDataProcessor(fallback_to_simulated=False)
    proc.logger.setLevel(logging.INFO)
    # 让 logger 输出到 stdout
    for h in list(proc.logger.handlers):
        proc.logger.removeHandler(h)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    proc.logger.addHandler(ch)

    print(f'开始逐只下载财务数据: {len(stock_list)} 只, 表={TABLES}, 范围={START}~{END}')
    sys.stdout.flush()

    t0 = time.time()
    result = proc.get_financial_data(stock_list, TABLES, START, END)
    elapsed = time.time() - t0

    n = len(result)
    print(f'\n下载完成: 返回 {n} 只有数据, 耗时 {elapsed/60:.1f} 分钟')
    sys.stdout.flush()


if __name__ == '__main__':
    main()
