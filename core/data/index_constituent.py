import ast
import logging
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


class IndexConstituentManager:
    """指数历史成分股管理器

    基于聚宽下载的CSV文件获取历史成分股数据，
    当回测日期超出CSV文件范围时，自动从QMT获取最新成分股并更新文件。

    CSV文件格式:
        date,codes
        2016-04-28,"['000001.SZ', '000002.SZ', ...]"
        2016-06-13,"['000001.SZ', '000008.SZ', ...]"

    每行代表成分股发生变更的日期，两个日期之间的成分股不变。
    查询时取 <= 查询日期 的最近一行。

    性能优化:
        调用 preload() 后，CSV数据预构建为排序日期列表 + bisect查找，
        后续查询全部走内存，无需重复DataFrame操作。
    """

    DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent / '.cache' / 'JQData' / 'index_constituent'

    SECTOR_TO_INDEX = {
        '沪深300': '000300.SH',
        '中证500': '000905.SH',
        '中证1000': '000852.SH',
        '上证50': '000016.SH',
        '中小综指': '399101.SZ',
        '中证全指': '000985.SH',
    }

    INDEX_TO_SECTOR = {v: k for k, v in SECTOR_TO_INDEX.items()}

    def __init__(self, data_dir: Optional[str] = None, xtdata=None):
        self.data_dir = Path(data_dir) if data_dir else self.DEFAULT_DATA_DIR
        self.xtdata = xtdata
        self.logger = logging.getLogger(self.__class__.__module__ + '.' + self.__class__.__name__)
        self._cache: Dict[str, pd.DataFrame] = {}
        self._preloaded: Dict[str, Tuple[List[pd.Timestamp], List[List[str]]]] = {}

    def _load_csv(self, index_code: str) -> Optional[pd.DataFrame]:
        if index_code in self._cache:
            return self._cache[index_code]

        csv_path = self.data_dir / f"{index_code}.csv"
        if not csv_path.exists():
            self.logger.debug(f"成分股CSV文件不存在: {csv_path}")
            return None

        try:
            df = pd.read_csv(csv_path)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            df['codes'] = df['codes'].apply(self._parse_codes)
            self._cache[index_code] = df
            self.logger.info(f"加载 {index_code} 成分股: {len(df)} 条变更记录, "
                             f"日期范围 {df['date'].min().strftime('%Y-%m-%d')} ~ "
                             f"{df['date'].max().strftime('%Y-%m-%d')}")
            return df
        except Exception as e:
            self.logger.error(f"加载 {index_code} 成分股CSV失败: {e}")
            return None

    @staticmethod
    def _parse_codes(codes_str) -> List[str]:
        if isinstance(codes_str, list):
            return codes_str
        try:
            result = ast.literal_eval(codes_str)
            if isinstance(result, list):
                return result
        except (ValueError, SyntaxError):
            pass
        if isinstance(codes_str, str):
            cleaned = codes_str.strip("[]").replace("'", "").replace('"', '')
            if cleaned:
                return [c.strip() for c in cleaned.split(',') if c.strip()]
        return []

    def preload(self, index_code: str) -> bool:
        """预加载指数成分股数据到内存，构建日期排序列表

        预加载后，get_constituent_stocks_fast() 可通过 bisect 查找，
        无需重复 DataFrame 操作。

        Args:
            index_code: 指数代码，如 '000300.SH'

        Returns:
            是否预加载成功
        """
        if index_code in self._preloaded:
            return True

        df = self._load_csv(index_code)
        if df is None or df.empty:
            return False

        dates = df['date'].tolist()
        codes_list = [list(row['codes']) if isinstance(row['codes'], list) else []
                      for _, row in df.iterrows()]

        self._preloaded[index_code] = (dates, codes_list)
        self.logger.info(f"预加载 {index_code} 成分股: {len(dates)} 个变更日期, "
                         f"日期范围 {dates[0].strftime('%Y-%m-%d')} ~ {dates[-1].strftime('%Y-%m-%d')}")
        return True

    def get_constituent_stocks_fast(self, index_code: str, date: str) -> List[str]:
        """快速获取指定日期的指数成分股（使用预加载的内存数据）

        通过 bisect 二分查找定位日期，O(logN) 复杂度。
        如果未预加载，自动回退到 get_constituent_stocks()。

        Args:
            index_code: 指数代码
            date: 日期，格式 'YYYY-MM-DD'

        Returns:
            股票代码列表
        """
        if index_code not in self._preloaded:
            return self.get_constituent_stocks(index_code, date)

        dates, codes_list = self._preloaded[index_code]
        target_date = pd.Timestamp(date)

        idx = bisect_right(dates, target_date)
        if idx == 0:
            return []

        return codes_list[idx - 1]

    def get_constituent_stocks(self, index_code: str, date: str) -> List[str]:
        """获取指定日期的指数成分股

        查询逻辑:
        1. 从CSV文件中查找 <= date 的最近一条记录
        2. 若 date 超出CSV覆盖范围，尝试从QMT按历史时点查询（real_timetag）
        3. 若仍无法获取，明确报错并返回空，
           绝不回退到当前成分股（否则造成幸存者偏差/未来函数）

        Args:
            index_code: 指数代码，如 '000300.SH'
            date: 日期，格式 'YYYY-MM-DD'

        Returns:
            股票代码列表
        """
        df = self._load_csv(index_code)
        target_date = pd.Timestamp(date)

        if df is not None and not df.empty:
            mask = df['date'] <= target_date
            if mask.any():
                latest_row = df[mask].iloc[-1]
                latest_date = latest_row['date']

                # 查询日期超出CSV最新记录 → 尝试从QMT按历史时点补充
                if target_date > latest_date:
                    updated = self._try_update_from_qmt(index_code, date, df)
                    if updated:
                        self._cache.pop(index_code, None)
                        self._preloaded.pop(index_code, None)
                        df = self._load_csv(index_code)
                        if df is not None:
                            mask = df['date'] <= target_date
                            if mask.any():
                                return list(df[mask].iloc[-1]['codes'])

                return list(latest_row['codes'])

        # CSV无覆盖 → 尝试从QMT按历史时点查询
        updated = self._try_update_from_qmt(index_code, date, df)
        if updated:
            self._cache.pop(index_code, None)
            self._preloaded.pop(index_code, None)
            df = self._load_csv(index_code)
            if df is not None and not df.empty:
                mask = df['date'] <= target_date
                if mask.any():
                    return list(df[mask].iloc[-1]['codes'])

        # 历史成分股缺失：绝不能静默用当前成分股（幸存者偏差）
        self.logger.error(
            f"缺少 {index_code} 在 {date} 的历史成分股数据，无法正确回测。"
            f"请先补齐 .cache/JQData/index_constituent/{index_code}.csv，"
            f"或在QMT客户端【历史数据下载】中下载“板块成分股历史变动信息”。"
        )
        return []

    def _try_update_from_qmt(self, index_code: str, date: str,
                              existing_df: Optional[pd.DataFrame] = None) -> bool:
        if not self.xtdata:
            self.logger.debug(f"QMT不可用，无法更新 {index_code} 成分股")
            return False

        sector = self.INDEX_TO_SECTOR.get(index_code)
        if not sector:
            self.logger.warning(f"未知的指数代码: {index_code}，无法映射到板块名称")
            return False

        try:
            # QMT 支持按历史时点查询成分股（real_timetag），
            # 前提：客户端【历史数据下载】已下载“板块成分股历史变动信息”。
            stock_list = self.xtdata.get_stock_list_in_sector(sector, real_timetag=date.replace('-', ''))
            if not stock_list or len(stock_list) < 10:
                self.logger.warning(
                    f"QMT返回 {sector} 在 {date} 的成分股数量异常: "
                    f"{len(stock_list) if stock_list else 0}，可能是未下载板块成分股历史变动数据"
                )
                return False

            # 防护：查询历史日期却返回与当前成分股完全一致，说明本地未下载历史变动数据
            # （QMT 回退到当前成分股），该结果不可信，拒绝写入以避免幸存者偏差。
            today = datetime.now().strftime('%Y-%m-%d')
            if date[:10] < today:
                current = self.xtdata.get_stock_list_in_sector(sector)
                if current and set(stock_list) == set(current):
                    self.logger.warning(
                        f"QMT查询 {sector} {date} 历史成分股与当前完全一致，"
                        f"疑似未下载板块成分股历史变动数据，跳过QMT结果"
                    )
                    return False

            if existing_df is not None and not existing_df.empty:
                latest_row = existing_df.iloc[-1]
                latest_codes = set(latest_row['codes'])
                new_codes = set(stock_list)

                if new_codes == latest_codes:
                    self.logger.debug(f"{index_code} 成分股无变化，无需更新")
                    return False

                diff_in = new_codes - latest_codes
                diff_out = latest_codes - new_codes
                self.logger.info(f"{index_code} 成分股有变化: "
                                 f"新纳入 {len(diff_in)} 只, 剔除 {len(diff_out)} 只")

            self._append_to_csv(index_code, date, stock_list)
            return True

        except Exception as e:
            self.logger.warning(f"从QMT获取 {index_code} {date} 成分股失败: {e}")
            return False

    def _append_to_csv(self, index_code: str, date: str, stock_list: List[str]) -> None:
        csv_path = self.data_dir / f"{index_code}.csv"
        sorted_stocks = sorted(stock_list)
        codes_str = str(sorted_stocks)
        new_row = pd.DataFrame({'date': [pd.Timestamp(date)], 'codes': [codes_str]})

        if csv_path.exists():
            existing = pd.read_csv(csv_path)
            existing['date'] = pd.to_datetime(existing['date'])
            combined = pd.concat([existing, new_row], ignore_index=True)
            # 允许插入历史日期（可能早于已有记录），去重后按日期排序，
            # 保证查询时始终能取到 <= 查询日期 的最近一条记录
            combined = combined.drop_duplicates(subset='date', keep='last')
            combined = combined.sort_values('date').reset_index(drop=True)
            combined.to_csv(csv_path, index=False)
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            new_row.to_csv(csv_path, index=False)

        self.logger.info(f"已更新 {csv_path.name}: 记录 {date}, {len(stock_list)} 只成分股")

    def get_all_constituent_stocks_in_range(self, index_code: str,
                                             start_date: str,
                                             end_date: str) -> List[str]:
        """获取指定时间范围内所有历史成分股的并集

        指数成分股会定期调整，回测期间涉及的股票数量远超单次成分股数量。
        此方法收集时间范围内所有变更记录的成分股并集，
        确保回测时能获取到所有曾经属于该指数的股票数据。

        Args:
            index_code: 指数代码，如 '000300.SH'
            start_date: 起始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'

        Returns:
            去重的股票代码列表
        """
        df = self._load_csv(index_code)
        if df is None or df.empty:
            return self.get_constituent_stocks(index_code, start_date)

        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)

        mask = (df['date'] >= start_ts) & (df['date'] <= end_ts)
        period_df = df[mask]

        mask_before = df['date'] < start_ts
        if mask_before.any():
            before_row = df[mask_before].iloc[-1]
            all_stocks = set(before_row['codes'])
        else:
            all_stocks = set()

        change_count = 0
        for _, row in period_df.iterrows():
            codes = row['codes']
            if isinstance(codes, list):
                all_stocks.update(codes)
                change_count += 1

        self.logger.info(f"{index_code} 在 {start_date}~{end_date} 期间: "
                         f"{change_count} 次成分股变更, 共涉及 {len(all_stocks)} 只股票")

        return sorted(list(all_stocks))

    @classmethod
    def sector_to_index_code(cls, sector: str) -> Optional[str]:
        return cls.SECTOR_TO_INDEX.get(sector)

    @classmethod
    def index_code_to_sector(cls, index_code: str) -> Optional[str]:
        return cls.INDEX_TO_SECTOR.get(index_code)
