"""
数据获取模块

支持三种数据源：
1. akshare 实盘数据（ETF/LOF历史K线）
2. 本地缓存（避免重复请求）
3. 模拟数据生成（无网络时用于测试策略逻辑）

三层阁策略需要两类数据：
- 交易价格（二级市场收盘价）→ 用于计算折价、收益率
- 基金净值（NAV）→ 用于计算4周/10周净增
"""

import os
import json
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import FundInfo, ALL_FUNDS, BENCHMARK_CODE, FUND_LOOKUP, StrategyConfig


class DataFetcher:
    """基金数据获取器，支持akshare和本地缓存"""

    def __init__(self, config: StrategyConfig = None):
        self.config = config or StrategyConfig()
        self.cache_dir = config.cache_dir if config else "./data_cache"
        self.use_cache = config.use_cache if config else True
        self._ak = None
        os.makedirs(self.cache_dir, exist_ok=True)

    @property
    def ak(self):
        """延迟加载akshare"""
        if self._ak is None:
            import akshare as ak
            self._ak = ak
        return self._ak

    def _cache_path(self, key: str) -> str:
        safe_key = key.replace("/", "_").replace("\\", "_")
        return os.path.join(self.cache_dir, f"{safe_key}.pkl")

    def _load_cache(self, key: str) -> Optional[object]:
        if not self.use_cache:
            return None
        path = self._cache_path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def _save_cache(self, key: str, data: object):
        if not self.use_cache:
            return
        path = self._cache_path(key)
        try:
            with open(path, "wb") as f:
                pickle.dump(data, f)
        except Exception:
            pass

    def fetch_etf_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取ETF/LOF历史K线数据
        返回 DataFrame: date, open, close, high, low, volume[, nav]
        若存在同区间净值缓存(nav_*.pkl)，自动合并 nav 列（封基折价计算需要）
        """
        cache_key = f"hist_{symbol}_{start_date}_{end_date}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            df = cached
        else:
            import time
            df = None
            for attempt in range(3):
                try:
                    fund_info = FUND_LOOKUP.get(symbol)
                    if fund_info and fund_info.fund_type == "lof":
                        df = self.ak.fund_lof_hist_em(
                            symbol=symbol, period="daily",
                            start_date=start_date.replace("-", ""),
                            end_date=end_date.replace("-", ""),
                        )
                    else:
                        df = self.ak.fund_etf_hist_em(
                            symbol=symbol, period="daily",
                            start_date=start_date.replace("-", ""),
                            end_date=end_date.replace("-", ""),
                        )
                    df = self._normalize_akshare_df(df, symbol)
                    if len(df) > 0:
                        self._save_cache(cache_key, df)
                        break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    print(f"[警告] 获取 {symbol} 数据失败: {e}，使用模拟数据")
                    df = None
            if df is None:
                df = self._generate_mock_data(symbol, start_date, end_date)

        # 合并净值缓存（封基折价收益）
        try:
            nav_key = f"nav_{symbol}_{start_date}_{end_date}"
            nav_cached = self._load_cache(nav_key)
            if nav_cached is not None and "nav" in nav_cached.columns and len(nav_cached) > 0:
                nav_df = nav_cached[["date", "nav"]].copy()
                nav_df["date"] = pd.to_datetime(nav_df["date"])
                if "nav" not in df.columns:
                    df = df.merge(nav_df, on="date", how="left")
        except Exception:
            pass

        return df

    def fetch_fund_nav(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取基金净值数据
        返回 DataFrame: date, nav (单位净值)
        """
        cache_key = f"nav_{symbol}_{start_date}_{end_date}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        try:
            df = self.ak.fund_etf_fund_info_em(
                fund=symbol,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is not None and len(df) > 0:
                col_map = {col: col.lower() for col in df.columns}
                df = df.rename(columns=col_map)
                if "净值日期" in df.columns:
                    df["date"] = pd.to_datetime(df["净值日期"])
                elif "日期" in df.columns:
                    df["date"] = pd.to_datetime(df["日期"])
                nav_col = None
                for c in df.columns:
                    if "净值" in c and "累计" not in c:
                        nav_col = c
                        break
                if nav_col is None:
                    nav_col = df.columns[1] if len(df.columns) > 1 else None
                if nav_col:
                    df["nav"] = pd.to_numeric(df[nav_col], errors="coerce")
                df = df[["date", "nav"]].dropna().sort_values("date").reset_index(drop=True)
                self._save_cache(cache_key, df)
                return df
        except Exception as e:
            print(f"[警告] 获取 {symbol} 净值失败: {e}")

        price_df = self.fetch_etf_history(symbol, start_date, end_date)
        nav_df = price_df[["date", "close"]].rename(columns={"close": "nav"}).copy()
        return nav_df

    def fetch_all_data(self, start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        """获取全部基金池的价格数据"""
        all_data = {}
        symbols = [f.code for f in ALL_FUNDS] + [BENCHMARK_CODE]
        for sym in symbols:
            print(f"  获取 {sym} ({FUND_LOOKUP.get(sym, FundInfo(sym, '', '', '')).name})...")
            df = self.fetch_etf_history(sym, start_date, end_date)
            if df is not None and len(df) > 0:
                all_data[sym] = df
        return all_data

    def _normalize_akshare_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """统一akshare返回的DataFrame格式"""
        col_map = {}
        for col in df.columns:
            lower = col.lower().strip()
            if lower in ("日期", "date"):
                col_map[col] = "date"
            elif lower in ("开盘", "open"):
                col_map[col] = "open"
            elif lower in ("收盘", "close"):
                col_map[col] = "close"
            elif lower in ("最高", "high"):
                col_map[col] = "high"
            elif lower in ("最低", "low"):
                col_map[col] = "low"
            elif lower in ("成交量", "volume"):
                col_map[col] = "volume"

        df = df.rename(columns=col_map)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "close", "high", "low"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        return df

    def _generate_mock_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        生成模拟数据用于无网络环境测试

        关键：净值(NAV)和交易价格(price)分开生成
        - 定开基金：price = NAV * (1 - discount)，折价随到期日临近收敛
        - ETF：price ≈ NAV（微小折溢价）
        """
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        dates = pd.bdate_range(start, end)

        fund_info = FUND_LOOKUP.get(symbol)
        if fund_info:
            if fund_info.bucket == "A_stock":
                annual_return, annual_vol = 0.10, 0.25
            elif fund_info.bucket == "US_stock":
                annual_return, annual_vol = 0.18, 0.22
            elif fund_info.bucket == "bond":
                annual_return, annual_vol = 0.045, 0.035
            elif fund_info.bucket == "gold":
                annual_return, annual_vol = 0.10, 0.14
            else:
                annual_return, annual_vol = 0.06, 0.15
        else:
            annual_return, annual_vol = 0.06, 0.15

        np.random.seed(hash(symbol) % (2**31))
        daily_return = annual_return / 252
        daily_vol = annual_vol / np.sqrt(252)
        returns = np.random.normal(daily_return, daily_vol, len(dates))
        nav = 1.0 * np.cumprod(1 + returns)

        is_closed_end = fund_info and fund_info.fund_type == "closed_end"
        if is_closed_end and fund_info.maturity_date:
            mat_date = pd.Timestamp(fund_info.maturity_date)
            total_days = (mat_date - start).days
            remaining_days = (mat_date - dates[0]).days
            remaining_ratio = np.clip(remaining_days / total_days, 0.05, 1.0)
            base_discount = 0.12
            discount = base_discount * remaining_ratio
            discount = np.clip(discount, 0.01, 0.15)
        elif is_closed_end:
            discount = np.linspace(0.10, 0.02, len(dates))
        else:
            discount = np.random.normal(0.0, 0.005, len(dates))
            discount = np.clip(discount, -0.01, 0.01)

        price = nav * (1 - discount)

        df = pd.DataFrame({
            "date": dates,
            "open": price * 0.998,
            "close": price,
            "nav": nav,
            "high": price * 1.005,
            "low": price * 0.995,
            "volume": np.random.randint(10000, 1000000, len(dates)),
            "discount": discount,
        })

        return df


class WeeklyDataAggregator:
    """将日线数据聚合为周线数据（三姑周末复盘）"""

    @staticmethod
    def to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
        """日线转周线（以周五收盘为周收盘）"""
        df = daily_df.copy()
        df["week"] = df["date"].dt.to_period("W-FRI")
        agg_dict = {
            "date": ("date", "last"),
            "open": ("open", "first"),
            "close": ("close", "last"),
            "high": ("high", "max"),
            "low": ("low", "min"),
            "volume": ("volume", "sum"),
        }
        if "nav" in df.columns:
            agg_dict["nav"] = ("nav", "last")
        if "discount" in df.columns:
            agg_dict["discount"] = ("discount", "last")
        weekly = df.groupby("week").agg(**agg_dict).reset_index(drop=True)
        return weekly

    @staticmethod
    def to_weekly_close(daily_df: pd.DataFrame) -> pd.Series:
        """提取周收盘价序列"""
        weekly = WeeklyDataAggregator.to_weekly(daily_df)
        s = weekly.set_index("date")["close"]
        s.name = daily_df.columns[-1] if "close" not in daily_df.columns else "close"
        return s

    @staticmethod
    def build_price_matrix(
        all_data: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """构建全部基金的周收盘价矩阵"""
        weekly_dict = {}
        for sym, df in all_data.items():
            mask = (df["date"] >= pd.Timestamp(start_date)) & (df["date"] <= pd.Timestamp(end_date))
            filtered = df.loc[mask].copy()
            if len(filtered) == 0:
                continue
            weekly = WeeklyDataAggregator.to_weekly(filtered)
            if len(weekly) > 0:
                weekly_dict[sym] = weekly.set_index("date")["close"]

        if not weekly_dict:
            return pd.DataFrame()

        price_matrix = pd.DataFrame(weekly_dict)
        # 注意: 仅 ffill（同一标的内部前向填充），不做 bfill（否则会用未来数据
        # 把未上市基金的价格向前填充，造成"幽灵持仓"前视偏差）
        price_matrix = price_matrix.sort_index().ffill()
        return price_matrix

    @staticmethod
    def build_nav_matrix(
        all_data: Dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        构建全部基金的周度净值矩阵

        关键：从"nav"列提取净值，如果数据中没有nav列（akshare实盘），
        则回退使用close作为净值近似（ETF折溢价极小）
        """
        weekly_dict = {}
        for sym, df in all_data.items():
            mask = (df["date"] >= pd.Timestamp(start_date)) & (df["date"] <= pd.Timestamp(end_date))
            filtered = df.loc[mask].copy()
            if len(filtered) == 0:
                continue
            weekly = WeeklyDataAggregator.to_weekly(filtered)
            if len(weekly) > 0:
                if "nav" in weekly.columns:
                    weekly_dict[sym] = weekly.set_index("date")["nav"]
                else:
                    weekly_dict[sym] = weekly.set_index("date")["close"]

        if not weekly_dict:
            return pd.DataFrame()

        nav_matrix = pd.DataFrame(weekly_dict)
        # 注意: 仅 ffill，不做 bfill（避免未上市基金净值被未来数据向前填充）
        nav_matrix = nav_matrix.sort_index().ffill()
        return nav_matrix
