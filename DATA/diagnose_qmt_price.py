# -*- coding: utf-8 -*-
"""定位异常价格根源：直接调 QMT 接口，对比三种复权方式返回的原始数据"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xtquant import xtdata

STOCKS = ['300023.SZ', '603003.SH', '000001.SZ']  # 000001 作为正常对照


def main():
    for symbol in STOCKS:
        print('=' * 70)
        print(f'{symbol} 2015年原始数据（QMT get_market_data_ex）')
        print('=' * 70)
        for dtype in ['none', 'back', 'forward']:
            try:
                raw = xtdata.get_market_data_ex(
                    [], [symbol], period='1d',
                    start_time='20150101', end_time='20151231',
                    count=-1, dividend_type=dtype
                )
                df = raw.get(symbol)
                if df is None or df.empty:
                    print(f'  {dtype}: 无数据')
                    continue
                close = df['close']
                print(f'  {dtype:>7}: close min={close.min():.4f} max={close.max():.4f} 唯一值={close.nunique()} 非零占比={(close > 0).mean():.0%}')
                # 打印前3个非零样本
                nz = close[close > 0]
                if len(nz) > 0:
                    print(f'          前3个非零样本: {nz.iloc[:3].tolist()}')
            except Exception as e:
                print(f'  {dtype}: 异常 {e}')


if __name__ == '__main__':
    main()
