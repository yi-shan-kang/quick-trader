import ast
import logging
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd


class IndustryConstituentManager:
    """申万行业历史成分股管理器

    基于聚宽下载的CSV文件获取历史行业成分股数据，
    当查询日期超出CSV文件范围时，自动从QMT获取最新成分股并更新文件。

    CSV文件格式（与指数成分股一致）:
        date,codes
        2016-04-28,"['000001.SZ', '002142.SZ', ...]"
        2016-06-23,"['000001.SZ', '002142.SZ', ...]"

    文件命名: SW1{行业名}.csv，如 SW1银行.csv, SW1电子.csv
    QMT板块名: SW1银行, SW1电子 等

    性能优化:
        调用 preload() 后，所有行业CSV数据预构建为:
        - 排序变更日期列表 + bisect查找
        - 每个变更日期对应的 {stock: industry} 完整映射
        后续查询全部走内存字典，无需重复遍历31个行业CSV。
    """

    DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent / '.cache' / 'JQData' / 'industry_constituent'

    SW1_INDUSTRIES = [
        '交通运输', '传媒', '公用事业', '农林牧渔', '医药生物',
        '商贸零售', '国防军工', '基础化工', '家用电器', '建筑材料',
        '建筑装饰', '房地产', '有色金属', '机械设备', '汽车',
        '采掘', '煤炭', '环保', '电力设备', '电子', '石油石化',
        '社会服务', '纺织服饰', '综合', '美容护理', '计算机',
        '轻工制造', '通信', '钢铁', '银行', '非银金融', '食品饮料',
    ]

    def __init__(self, data_dir: Optional[str] = None, xtdata=None):
        self.data_dir = Path(data_dir) if data_dir else self.DEFAULT_DATA_DIR
        self.xtdata = xtdata
        self.logger = logging.getLogger(self.__class__.__module__ + '.' + self.__class__.__name__)
        self._cache: Dict[str, pd.DataFrame] = {}
        self._preloaded_dates: Optional[List[pd.Timestamp]] = None
        self._preloaded_mappings: Optional[List[Dict[str, str]]] = None

    def _csv_key(self, industry: str) -> str:
        if industry.startswith('SW'):
            return industry
        return f'SW1{industry}'

    def _qmt_sector_name(self, industry: str) -> str:
        if industry.startswith('SW'):
            return industry
        return f'SW1{industry}'

    def _load_csv(self, industry: str) -> Optional[pd.DataFrame]:
        csv_key = self._csv_key(industry)
        if csv_key in self._cache:
            return self._cache[csv_key]

        csv_path = self.data_dir / f"{csv_key}.csv"
        if not csv_path.exists():
            self.logger.debug(f"行业成分股CSV文件不存在: {csv_path}")
            return None

        try:
            df = pd.read_csv(csv_path)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            df['codes'] = df['codes'].apply(self._parse_codes)
            self._cache[csv_key] = df
            self.logger.info(f"加载 {csv_key} 行业成分股: {len(df)} 条变更记录, "
                             f"日期范围 {df['date'].min().strftime('%Y-%m-%d')} ~ "
                             f"{df['date'].max().strftime('%Y-%m-%d')}")
            return df
        except Exception as e:
            self.logger.error(f"加载 {csv_key} 行业成分股CSV失败: {e}")
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

    def preload(self) -> bool:
        """预加载所有行业CSV数据到内存，构建日期索引的行业映射

        核心逻辑:
        1. 加载31个行业CSV，收集所有变更日期的并集
        2. 按日期排序，为每个变更日期计算完整的 {stock: industry} 映射
        3. 后续查询通过 bisect 定位日期，O(logN) + O(1) 字典查找

        Returns:
            是否预加载成功
        """
        if self._preloaded_dates is not None:
            return True

        industry_data: Dict[str, List[Tuple[pd.Timestamp, List[str]]]] = {}
        all_change_dates: set = set()

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _load_industry(industry):
            df = self._load_csv(industry)
            if df is None or df.empty:
                return (industry, None)
            changes = []
            industry_dates = set()
            for _, row in df.iterrows():
                dt = row['date']
                codes = row['codes'] if isinstance(row['codes'], list) else []
                changes.append((dt, codes))
                industry_dates.add(dt)
            return (industry, changes, industry_dates)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_load_industry, ind): ind for ind in self.SW1_INDUSTRIES}
            for future in as_completed(futures):
                result = future.result()
                if result[1] is None:
                    continue
                industry, changes, industry_dates = result
                industry_data[industry] = changes
                all_change_dates.update(industry_dates)

        if not all_change_dates:
            self.logger.warning("预加载行业数据: 无可用数据")
            return False

        sorted_dates = sorted(all_change_dates)

        date_to_mapping: Dict[pd.Timestamp, Dict[str, str]] = {}
        for industry, changes in industry_data.items():
            change_idx = 0
            current_stocks: List[str] = []

            for target_date in sorted_dates:
                while change_idx < len(changes) and changes[change_idx][0] <= target_date:
                    current_stocks = changes[change_idx][1]
                    change_idx += 1

                if current_stocks:
                    if target_date not in date_to_mapping:
                        date_to_mapping[target_date] = {}
                    for stock in current_stocks:
                        date_to_mapping[target_date][stock] = industry

        sorted_dates_with_mapping = sorted(date_to_mapping.keys())
        self._preloaded_dates = sorted_dates_with_mapping
        self._preloaded_mappings = [date_to_mapping[d] for d in sorted_dates_with_mapping]

        self.logger.info(f"预加载行业数据: {len(industry_data)} 个行业, "
                         f"{len(sorted_dates_with_mapping)} 个变更日期, "
                         f"日期范围 {sorted_dates_with_mapping[0].strftime('%Y-%m-%d')} ~ "
                         f"{sorted_dates_with_mapping[-1].strftime('%Y-%m-%d')}")
        return True

    def get_industry_mapping_fast(self, date: str) -> Dict[str, str]:
        """快速获取指定日期的股票→行业映射（使用预加载的内存数据）

        通过 bisect 二分查找定位日期，O(logN) 复杂度。
        如果未预加载，自动回退到 get_industry_mapping()。

        Args:
            date: 日期，格式 'YYYY-MM-DD'

        Returns:
            { stock_code: industry_name, ... }
        """
        if self._preloaded_dates is None:
            stock_list = []
            for industry in self.SW1_INDUSTRIES:
                stocks = self.get_industry_stocks(industry, date)
                stock_list.extend(stocks)
            return self.get_industry_mapping(stock_list, date)

        target_date = pd.Timestamp(date)
        idx = bisect_right(self._preloaded_dates, target_date)
        if idx == 0:
            return {}

        return self._preloaded_mappings[idx - 1]

    def get_industry_fast(self, stock_code: str, date: str) -> Optional[str]:
        """快速获取指定日期的单只股票行业分类

        Args:
            stock_code: 股票代码
            date: 日期，格式 'YYYY-MM-DD'

        Returns:
            行业名称，无数据返回None
        """
        mapping = self.get_industry_mapping_fast(date)
        return mapping.get(stock_code)

    def get_industry_stocks(self, industry: str, date: str) -> List[str]:
        """获取指定日期的某行业成分股

        查询逻辑:
        1. 从CSV文件中查找 <= date 的最近一条记录
        2. 若 date 超出CSV覆盖范围，尝试从QMT按历史时点查询（real_timetag）
        3. 若仍无法获取，明确报错并返回空，
           绝不回退到当前成分股（否则造成幸存者偏差/未来函数）

        Args:
            industry: 行业名称，如 '银行', '电子', 'SW1银行' 均可
            date: 日期，格式 'YYYY-MM-DD'

        Returns:
            股票代码列表
        """
        df = self._load_csv(industry)
        target_date = pd.Timestamp(date)

        if df is not None and not df.empty:
            mask = df['date'] <= target_date
            if mask.any():
                latest_row = df[mask].iloc[-1]
                latest_date = latest_row['date']

                # 查询日期超出CSV最新记录 → 尝试从QMT按历史时点补充
                if target_date > latest_date:
                    updated = self._try_update_from_qmt(industry, date, df)
                    if updated:
                        csv_key = self._csv_key(industry)
                        self._cache.pop(csv_key, None)
                        self._preloaded_dates = None
                        self._preloaded_mappings = None
                        df = self._load_csv(industry)
                        if df is not None:
                            mask = df['date'] <= target_date
                            if mask.any():
                                return list(df[mask].iloc[-1]['codes'])

                return list(latest_row['codes'])

        # CSV无覆盖 → 尝试从QMT按历史时点查询
        updated = self._try_update_from_qmt(industry, date, df)
        if updated:
            csv_key = self._csv_key(industry)
            self._cache.pop(csv_key, None)
            self._preloaded_dates = None
            self._preloaded_mappings = None
            df = self._load_csv(industry)
            if df is not None and not df.empty:
                mask = df['date'] <= target_date
                if mask.any():
                    return list(df[mask].iloc[-1]['codes'])

        # 历史行业成分股缺失：绝不能静默用当前成分股（幸存者偏差）
        self.logger.error(
            f"缺少行业 {industry} 在 {date} 的历史成分股数据，无法正确回测。"
            f"请先补齐 .cache/JQData/industry_constituent 下的历史行业成分股CSV，"
            f"或在QMT客户端【历史数据下载】中下载“板块成分股历史变动信息”。"
        )
        return []

    def get_industry_mapping(self, stock_list: List[str],
                              date: Optional[str] = None) -> Dict[str, str]:
        """获取股票→行业的映射字典

        如果已预加载，直接使用 bisect 快速路径（O(logN)）。
        否则遍历所有行业，查找每只股票所属的行业。

        Args:
            stock_list: 股票代码列表（QMT格式）
            date: 日期，格式 'YYYY-MM-DD'，默认为当前日期

        Returns:
            { stock_code: industry_name, ... }
            industry_name 不含 'SW1' 前缀，如 '银行', '电子'
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        # 快速路径：如果已预加载，直接使用 bisect 查找
        if self._preloaded_dates is not None:
            full_mapping = self.get_industry_mapping_fast(date)
            stock_set = set(stock_list)
            return {s: industry for s, industry in full_mapping.items() if s in stock_set}

        stock_set = set(stock_list)
        mapping: Dict[str, str] = {}

        for industry in self.SW1_INDUSTRIES:
            stocks = self.get_industry_stocks(industry, date)
            industry_name = industry
            for stock in stocks:
                if stock in stock_set:
                    mapping[stock] = industry_name

        return mapping

    def get_all_industry_stocks(self, date: Optional[str] = None) -> Dict[str, List[str]]:
        """获取所有行业的成分股

        Args:
            date: 日期，格式 'YYYY-MM-DD'，默认为当前日期

        Returns:
            { industry_name: [stock_code, ...], ... }
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        if self._preloaded_dates is not None:
            mapping = self.get_industry_mapping_fast(date)
            result: Dict[str, List[str]] = {}
            for stock, industry in mapping.items():
                if industry not in result:
                    result[industry] = []
                result[industry].append(stock)
            return result

        result: Dict[str, List[str]] = {}
        for industry in self.SW1_INDUSTRIES:
            stocks = self.get_industry_stocks(industry, date)
            if stocks:
                result[industry] = stocks

        return result

    def _try_update_from_qmt(self, industry: str, date: str,
                              existing_df: Optional[pd.DataFrame] = None) -> bool:
        if not self.xtdata:
            self.logger.debug(f"QMT不可用，无法更新 {industry} 行业成分股")
            return False

        sector_name = self._qmt_sector_name(industry)

        try:
            # QMT 支持按历史时点查询成分股（real_timetag），
            # 前提：客户端【历史数据下载】已下载“板块成分股历史变动信息”。
            stock_list = self.xtdata.get_stock_list_in_sector(sector_name, real_timetag=date.replace('-', ''))
            if not stock_list or len(stock_list) < 1:
                self.logger.warning(
                    f"QMT返回 {sector_name} 在 {date} 的成分股数量异常: "
                    f"{len(stock_list) if stock_list else 0}，可能是未下载板块成分股历史变动数据"
                )
                return False

            # 防护：查询历史日期却返回与当前成分股完全一致，说明本地未下载历史变动数据
            # （QMT 回退到当前成分股），该结果不可信，拒绝写入以避免幸存者偏差。
            today = datetime.now().strftime('%Y-%m-%d')
            if date[:10] < today:
                current = self.xtdata.get_stock_list_in_sector(sector_name)
                if current and set(stock_list) == set(current):
                    self.logger.warning(
                        f"QMT查询 {sector_name} {date} 历史成分股与当前完全一致，"
                        f"疑似未下载板块成分股历史变动数据，跳过QMT结果"
                    )
                    return False

            if existing_df is not None and not existing_df.empty:
                latest_row = existing_df.iloc[-1]
                latest_codes = set(latest_row['codes'])
                new_codes = set(stock_list)

                if new_codes == latest_codes:
                    self.logger.debug(f"{sector_name} 行业成分股无变化，无需更新")
                    return False

                diff_in = new_codes - latest_codes
                diff_out = latest_codes - new_codes
                self.logger.info(f"{sector_name} 行业成分股有变化: "
                                 f"新纳入 {len(diff_in)} 只, 剔除 {len(diff_out)} 只")

            self._append_to_csv(industry, date, stock_list)
            return True

        except Exception as e:
            self.logger.warning(f"从QMT获取 {sector_name} {date} 行业成分股失败: {e}")
            return False

    def _append_to_csv(self, industry: str, date: str, stock_list: List[str]) -> None:
        csv_key = self._csv_key(industry)
        csv_path = self.data_dir / f"{csv_key}.csv"
        sorted_stocks = sorted(stock_list)
        codes_str = str(sorted_stocks)

        if csv_path.exists():
            existing = pd.read_csv(csv_path)
            existing['date'] = pd.to_datetime(existing['date'])
            new_row = pd.DataFrame({'date': [pd.Timestamp(date)], 'codes': [codes_str]})
            combined = pd.concat([existing, new_row], ignore_index=True)
            # 允许插入历史日期（可能早于已有记录），去重后按日期排序
            combined = combined.drop_duplicates(subset='date', keep='last')
            combined = combined.sort_values('date').reset_index(drop=True)
            combined.to_csv(csv_path, index=False)
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            new_row = pd.DataFrame({'date': [date], 'codes': [codes_str]})
            new_row.to_csv(csv_path, index=False)

        self.logger.info(f"已更新 {csv_path.name}: 记录 {date}, {len(stock_list)} 只成分股")

    def get_available_industries(self) -> List[str]:
        """获取本地CSV文件中可用的行业列表"""
        result = []
        if self.data_dir.exists():
            for csv_file in self.data_dir.glob('SW1*.csv'):
                name = csv_file.stem
                if name.startswith('SW1'):
                    result.append(name[3:])
        return sorted(result)
