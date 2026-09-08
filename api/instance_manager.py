import json
import os
import time
import threading
from typing import Dict, List, Optional
from collections import defaultdict
import logging

from api.qmt_api import QMTAPI
from core.virtual_book import VirtualBook
from core.reconciler import Reconciler, ReconcileResult
from core.record_manager import RecordManager
from strategies import get_strategy, get_strategy_default_kwargs
from utils.logger import Logger

try:
    from monitor.alerter import get_alerter
    HAS_ALERTER = True
except ImportError:
    HAS_ALERTER = False


class StrategyInstanceManager:
    """策略实例管理器 - 管理多个策略实例的启动、运行和停止

    支持从 JSON 配置文件加载多个策略实例，按账户分组初始化，
    自动创建 VirtualBook 实现策略级隔离，并协调对账。
    """

    def __init__(self):
        self._configs: List[dict] = []
        self._apis: Dict[str, QMTAPI] = {}
        self._books: Dict[str, VirtualBook] = {}
        self._reconcilers: Dict[str, Reconciler] = {}
        self._running = False
        self._reconcile_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._instance_meta: Dict[str, dict] = {}
        self._record_managers: Dict[str, RecordManager] = {}
        self._heartbeat_interval: int = 60
        self._max_restart_attempts: int = 3
        self._restart_counts: Dict[str, int] = defaultdict(int)
        self.logger = logging.getLogger(self.__class__.__module__ + '.' + self.__class__.__name__)

        # 告警推送器（双通道，进程内单例）
        self._alerter = get_alerter() if HAS_ALERTER else None
        self._drawdown_thread: Optional[threading.Thread] = None

    def load_config(self, config_path: str):
        """加载策略实例配置文件

        Args:
            config_path: JSON 配置文件路径
        """
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        instances = config.get('instances', [])
        if not instances:
            raise ValueError(f'配置文件中没有策略实例: {config_path}')

        instance_ids = set()
        for inst in instances:
            instance_id = inst.get('instance_id')
            if not instance_id:
                raise ValueError(f'策略实例缺少 instance_id: {inst}')
            if instance_id in instance_ids:
                raise ValueError(f'重复的 instance_id: {instance_id}')
            instance_ids.add(instance_id)

            strategy_name = inst.get('strategy_name')
            if not strategy_name:
                raise ValueError(f'策略实例缺少 strategy_name: {instance_id}')

            strategy_class = get_strategy(strategy_name)
            if strategy_class is None:
                raise ValueError(f'未知策略: {strategy_name} (实例: {instance_id})')

        self._configs = instances
        self.logger.info(f'加载配置完成: {len(instances)} 个策略实例')

    def start_all(self):
        """启动所有策略实例

        流程：
        1. 按账户分组
        2. 每个账户初始化一个 QMTAPI
        3. 查询账户实际状态
        4. 按顺序初始化 VirtualBook
        5. 校验簿记总和
        6. 创建策略实例
        7. 启动策略运行
        """
        account_groups = self._group_by_account()

        for account_key, instances in account_groups.items():
            first_config = instances[0]
            is_sim = first_config.get('mode', 'sim') == 'sim'
            account_id = first_config.get('account_id')
            qmt_path = first_config.get('qmt_path', r'D:\qmt\userdata_mini')
            trade_mode = first_config.get('trade_mode', 'miniqmt')
            bridge_url = first_config.get('bridge_url', 'http://127.0.0.1:8888')

            self.logger.info(f'初始化账户: {account_key} (模式={"模拟" if is_sim else "实盘"}, 交易={trade_mode})')

            api = QMTAPI(is_sim=is_sim, path=qmt_path, account_id=account_id,
                         trade_mode=trade_mode, bridge_url=bridge_url)
            self._apis[account_key] = api

            actual_positions = self._query_positions(api)
            actual_cash = self._query_cash(api)

            self.logger.info(
                f'账户 {account_key} 实际状态: '
                f'持仓={len(actual_positions)}只, 现金={actual_cash:.2f}'
            )

            claimed_symbols = set()
            persisted_states: Dict[str, dict] = {}
            for config in instances:
                instance_id = config['instance_id']
                initial_capital = config.get('initial_capital', 0)
                cash_ratio = config.get('cash_ratio', 1.0)

                book = VirtualBook(
                    strategy_id=instance_id,
                    initial_capital=initial_capital,
                    cash_ratio=cash_ratio
                )

                # 创建 RecordManager 并尝试加载持久化状态
                rm = RecordManager(instance_id)
                self._record_managers[instance_id] = rm
                persisted_state = rm.load_state()
                state_loaded = False
                if persisted_state:
                    book.set_state(persisted_state.get('virtual_book', {}))
                    persisted_states[instance_id] = persisted_state
                    state_loaded = True

                if state_loaded:
                    # 已从持久化恢复簿记，不覆盖持仓和现金，
                    # 只登记已认领标的防止其他策略重复认领
                    if config.get('claim_existing_positions', True):
                        claimed_symbols.update(book._positions.keys())
                    self.logger.info(f'[{instance_id}] 已从持久化恢复，跳过账户初始化')
                elif config.get('claim_existing_positions', True):
                    book.initialize_from_account(
                        actual_positions, actual_cash, claimed_symbols,
                        cash_ratio=cash_ratio
                    )
                    claimed_symbols.update(book._positions.keys())
                else:
                    # 不认领持仓的策略：虚拟资金独立簿记，不受账户实际现金约束
                    if initial_capital > 0:
                        book._cash = initial_capital
                        book._is_virtual = True
                    elif cash_ratio < 1.0:
                        book._cash = actual_cash * cash_ratio
                    book._last_sync_time = time.time()

                self._books[instance_id] = book
                self.logger.info(
                    f'VirtualBook 初始化: {instance_id}, '
                    f'持仓={len(book._positions)}只, 现金={book.get_cash():.2f}'
                )

            # 模拟盘（如国金模拟）账户状态可能异常（现金为负等），
            # 不做账户一致性校验，避免误判退出；实盘仍校验。
            if not is_sim:
                self._validate_books(account_key, actual_positions, actual_cash)
            else:
                self.logger.info(f'模拟盘模式，跳过账户一致性校验: {account_key}')

            for config in instances:
                instance_id = config['instance_id']
                strategy_name = config['strategy_name']
                book = self._books[instance_id]

                strategy_class = get_strategy(strategy_name)
                kwargs = dict(get_strategy_default_kwargs(strategy_name))
                kwargs.update(config.get('kwargs', {}))

                api.add_strategy(
                    strategy_class,
                    instance_id=instance_id,
                    virtual_book=book,
                    **kwargs
                )
                # 恢复策略逻辑状态（如 _last_trade_date 等防重复字段）
                if instance_id in persisted_states:
                    strategy = api._strategies.get(instance_id)
                    if strategy:
                        strategy.set_state(
                            persisted_states[instance_id].get('strategy', {})
                        )
                self.logger.info(f'策略实例已创建: {instance_id} ({strategy_name})')

            books_for_account = [
                self._books[c['instance_id']] for c in instances
            ]
            self._reconcilers[account_key] = Reconciler(books_for_account, api.trader)

        for account_key, api in self._apis.items():
            api.run_loop()
            self.logger.info(f'策略运行已启动: {account_key}')

        self._running = True
        self._start_reconcile_timer()
        self._register_all_instances()
        self._start_heartbeat()
        self._register_trade_callbacks()
        self._start_drawdown_monitor()

    def _register_trade_callbacks(self):
        """为每个账户的 QMTAPI 注册成交后状态保存回调及断线告警回调"""
        for account_key, api in self._apis.items():
            def _make_callback():
                # 闭包捕获 manager 引用，instance_id 由回调参数传入
                def _callback(instance_id: str, trade_info):
                    self._save_instance_state(instance_id, trade_info)
                return _callback
            api.set_on_trade_filled_callback(_make_callback())

            # 断线 / 重连耗尽告警
            account_label = account_key
            def _make_reconnect_callbacks(label):
                def _on_disconnect():
                    if self._alerter and self._alerter.is_enabled('reconnect'):
                        self._alerter.send(
                            f'交易通道断开 ({label})',
                            f'账户 **{label}** 与 MiniQMT 交易服务器连接断开，'
                            '正在后台自动重连。',
                            dedup_key=f'reconnect_disconnect:{label}',
                        )
                def _on_exhausted():
                    if self._alerter and self._alerter.is_enabled('reconnect'):
                        self._alerter.send(
                            f'重连耗尽告警 ({label})',
                            f'账户 **{label}** MiniQMT 重连次数已用尽，交易通道未恢复，'
                            '请人工检查 QMT 客户端！',
                            dedup_key=f'reconnect_exhausted:{label}',
                        )
                return _on_disconnect, _on_exhausted
            _on_disconnect, _on_exhausted = _make_reconnect_callbacks(account_key)
            api.set_reconnect_callback(_on_disconnect, _on_exhausted)
        self.logger.info('已为所有账户注册成交状态保存回调及断线告警回调')

    def _save_instance_state(self, instance_id: str, trade_info):
        """保存指定实例的状态

        Args:
            instance_id: 策略实例ID
            trade_info: 成交信息，None 时跳过成交记录追加
        """
        rm = self._record_managers.get(instance_id)
        book = self._books.get(instance_id)
        if not rm or not book:
            return
        # 查找策略实例（遍历所有 api 的 _strategies）
        strategy = None
        for api in self._apis.values():
            strategy = api._strategies.get(instance_id)
            if strategy:
                break
        strategy_state = strategy.get_state() if strategy else {}
        rm.save_state(book.get_state(), strategy_state)
        if trade_info is not None:
            import datetime as _dt
            rm.append_trade({
                'time': _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'instance_id': instance_id,
                'symbol': trade_info.symbol,
                'direction': trade_info.direction,
                'price': trade_info.price,
                'volume': trade_info.volume,
                'commission': trade_info.commission,
                'order_id': trade_info.order_id,
            })
            self._check_volume_surge(instance_id, trade_info)

    def _check_volume_surge(self, instance_id: str, trade_info):
        """成交量异常检测：单笔/单订单成交回报 vs 近期历史成交基准

        基准 = trades_<id>.csv 中同标的最近 base_window 笔成交的平均成交量，
        当当前成交显著放大时推送告警。拆单成交（同 order_id 多笔回报）按订单聚合，
        避免因拆单造成的单笔量小误判。
        """
        if not self._alerter:
            return
        mon = self._alerter.get_config('volume_surge')
        if not self._alerter.is_enabled('volume_surge'):
            return
        factor = float(mon.get('factor', 3.0))
        base_window = int(mon.get('base_window', 20))

        rm = self._record_managers.get(instance_id)
        if not rm or not os.path.isfile(rm.trades_file):
            return
        import csv as _csv
        recent_volumes = []
        try:
            with open(rm.trades_file, 'r', encoding='utf-8-sig', newline='') as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    # 只统计同标的、非当前订单的历史成交作为基准
                    if row.get('标的') == trade_info.symbol:
                        recent_volumes.append(int(float(row.get('数量', 0) or 0)))
        except (IOError, ValueError) as e:
            self.logger.warning(f'读取成交基准失败: {e}')
            return

        if not recent_volumes or len(recent_volumes) < 2:
            return
        baseline = recent_volumes[:-base_window] if len(recent_volumes) > base_window else recent_volumes
        # 去掉最大/最小，避免极值污染基准
        work = sorted(baseline)
        if len(work) > 2:
            work = work[1:-1]
        avg_baseline = sum(work) / len(work) if work else 0.0
        if avg_baseline <= 0:
            return
        if trade_info.volume > avg_baseline * factor:
            message = (
                f"策略 **{instance_id}** 标的 **{trade_info.symbol}** 成交异常放大：\n"
                f"- 当前成交: {trade_info.volume} 股 @ {trade_info.price}\n"
                f"- 近期基准(均值): {avg_baseline:.0f} 股\n"
                f"- 放大倍数: {trade_info.volume / avg_baseline:.1f}x (阈值 {factor}x)"
            )
            self._alerter.send(
                '成交量异常提醒', message,
                dedup_key=f'volume_surge:{instance_id}:{trade_info.symbol}'
            )

    def stop_all(self):
        self._running = False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=10)
        if self._reconcile_thread and self._reconcile_thread.is_alive():
            self._reconcile_thread.join(timeout=10)
        if self._drawdown_thread and self._drawdown_thread.is_alive():
            self._drawdown_thread.join(timeout=10)
        # 退出前保存所有实例状态
        for instance_id in list(self._record_managers.keys()):
            self._save_instance_state(instance_id, None)
        for account_key, api in self._apis.items():
            api.close()
            self.logger.info(f'策略已停止: {account_key}')

    def reconcile_all(self) -> Dict[str, ReconcileResult]:
        """对所有账户执行对账

        Returns:
            账户ID → 对账结果的映射
        """
        results = {}
        for account_key, reconciler in self._reconcilers.items():
            result = reconciler.reconcile()
            if not result.is_clean:
                self.logger.warning(f'账户 {account_key} 对账发现偏差:\n{result}')
                self._notify_reconcile_drift(account_key, result)
                reconciler.auto_correct(result)
            else:
                self.logger.debug(f'账户 {account_key} 对账一致 ✅')
            results[account_key] = result
        return results

    def _notify_reconcile_drift(self, account_key: str, result):
        """对账出现偏差时推送告警"""
        if not self._alerter:
            return
        mon = self._alerter.get_config('reconcile')
        if not (self._alerter.is_enabled('reconcile') and mon.get('notify_on_drift', True)):
            return
        drift_lines = []
        for d in result.position_drifts:
            drift_lines.append(
                f'- {d.symbol}: 簿记 {d.book_volume} vs 实际 {d.actual_volume}'
            )
        cash_part = ""
        if result.cash_drift and abs(result.cash_drift.diff) > 1.0:
            cash_part = f"\n现金偏差: {result.cash_drift.diff:.2f}"
        message = (
            f"账户 **{account_key}** 每日对账发现偏差，已自动修正：\n"
            + ("\n".join(drift_lines) if drift_lines else "- 持仓一致")
            + cash_part
        )
        self._alerter.send('对账偏差告警', message, dedup_key=f'reconcile:{account_key}')

    def list_instances(self) -> List[dict]:
        """列出所有策略实例及其状态"""
        result = []
        for config in self._configs:
            instance_id = config['instance_id']
            book = self._books.get(instance_id)
            result.append({
                'instance_id': instance_id,
                'strategy_name': config.get('strategy_name'),
                'mode': config.get('mode'),
                'account_id': config.get('account_id'),
                'initial_capital': config.get('initial_capital', 0),
                'current_positions': len(book._positions) if book else 0,
                'current_cash': book.get_cash() if book else 0,
            })
        return result

    def get_virtual_book(self, instance_id: str) -> Optional[VirtualBook]:
        """获取指定实例的 VirtualBook"""
        return self._books.get(instance_id)

    def _group_by_account(self) -> Dict[str, List[dict]]:
        """按账户分组策略实例配置"""
        groups: Dict[str, List[dict]] = defaultdict(list)
        for config in self._configs:
            account_id = config.get('account_id', 'default')
            mode = config.get('mode', 'sim')
            key = f"{account_id}_{mode}"
            groups[key].append(config)
        return dict(groups)

    def _query_positions(self, api: QMTAPI) -> Dict[str, int]:
        """查询账户实际持仓"""
        result: Dict[str, int] = {}
        if not api.trader:
            return result
        try:
            positions = api.trader.get_position()
            if positions:
                for pos in positions:
                    symbol = getattr(pos, 'stock_code', str(pos))
                    volume = api.trader.get_position_volume(pos)
                    if volume > 0:
                        result[symbol] = volume
        except Exception as e:
            self.logger.error(f'查询账户持仓失败: {e}')
        return result

    def _query_cash(self, api: QMTAPI) -> float:
        """查询账户实际现金"""
        if not api.trader:
            return 0.0
        try:
            account = api.trader.get_account()
            if account:
                for attr in ('cash', 'm_dAvailable'):
                    value = getattr(account, attr, None)
                    if value is not None:
                        return value
        except Exception as e:
            self.logger.error(f'查询账户现金失败: {e}')
        return 0.0

    def _validate_books(self, account_key: str, actual_positions: Dict[str, int], actual_cash: float):
        """校验所有 VirtualBook 之和不超过账户实际"""
        aggregated_positions: Dict[str, int] = {}
        aggregated_cash = 0.0
        virtual_cash_instances = []
        for config in self._configs:
            account_id = config.get('account_id', 'default')
            mode = config.get('mode', 'sim')
            key = f"{account_id}_{mode}"
            if key != account_key:
                continue
            book = self._books.get(config['instance_id'])
            if book:
                # 虚拟资金实例独立簿记，不参与账户实际状态校验
                if getattr(book, '_is_virtual', False):
                    virtual_cash_instances.append((config['instance_id'], book._cash))
                    continue
                for symbol, volume in book._positions.items():
                    aggregated_positions[symbol] = aggregated_positions.get(symbol, 0) + volume
                aggregated_cash += book._cash

        for symbol, agg_vol in aggregated_positions.items():
            actual_vol = actual_positions.get(symbol, 0)
            if agg_vol > actual_vol:
                self.logger.error(
                    f'校验失败: {symbol} 簿记合计={agg_vol} > 实际={actual_vol}'
                )
                raise ValueError(
                    f'VirtualBook 校验失败: {symbol} 簿记合计({agg_vol}) '
                    f'超过账户实际持仓({actual_vol})'
                )

        # 现金校验：使用虚拟资金的实例不纳入合计
        virtual_cash_total = sum(c for _, c in virtual_cash_instances)
        non_virtual_cash = aggregated_cash - virtual_cash_total
        if non_virtual_cash > 0 and non_virtual_cash > actual_cash + 1.0:
            self.logger.error(
                f'校验失败: 现金簿记合计(非虚拟)={non_virtual_cash:.2f} > 实际={actual_cash:.2f}'
            )
            raise ValueError(
                f'VirtualBook 校验失败: 现金簿记合计({non_virtual_cash:.2f}) '
                f'超过账户实际现金({actual_cash:.2f})'
            )
        if virtual_cash_total > 0:
            self.logger.info(
                f'虚拟资金实例: {[(i, f"{c:.2f}") for i, c in virtual_cash_instances]}, '
                f'合计={virtual_cash_total:.2f} (不参与账户现金校验)'
            )

        self.logger.info(f'账户 {account_key} VirtualBook 校验通过 ✅')

    def _start_reconcile_timer(self):
        """启动定时对账线程（每日开盘前对账）"""
        def _reconcile_loop():
            last_date = None
            while self._running:
                now = time.localtime()
                current_date = f'{now.tm_year}-{now.tm_mon:02d}-{now.tm_mday:02d}'

                if current_date != last_date and now.tm_hour == 9 and now.tm_min < 30:
                    self.logger.info('定时对账: 每日开盘前')
                    try:
                        self.reconcile_all()
                    except Exception as e:
                        self.logger.error(f'定时对账异常: {e}')
                    last_date = current_date

                time.sleep(60)

        self._reconcile_thread = threading.Thread(target=_reconcile_loop, daemon=True)
        self._reconcile_thread.start()
        self.logger.info('定时对账线程已启动')

    # ------------------------------------------------------------------
    # 账户级回撤监控（按整个账户权益计算）
    # ------------------------------------------------------------------
    def _start_drawdown_monitor(self):
        """启动账户回撤监控线程（按整个账户权益 vs 历史峰值）"""
        if not self._alerter or not self._alerter.is_enabled('drawdown_account'):
            self.logger.info('账户回撤监控未启用，跳过')
            return

        def _drawdown_loop():
            while self._running:
                try:
                    self._check_account_drawdown()
                except Exception as e:
                    self.logger.error(f'账户回撤监控异常: {e}')
                cfg = self._alerter.get_config('drawdown_account')
                interval = float(cfg.get('check_interval_sec', 60))
                time.sleep(interval)

        self._drawdown_thread = threading.Thread(target=_drawdown_loop, daemon=True)
        self._drawdown_thread.start()
        self.logger.info('账户回撤监控线程已启动')

    def _account_total_equity(self) -> Optional[float]:
        """计算当前账户总权益（现金 + 持仓市值）

        按整个账户口径求和：优先使用 QMT 账户资产字段（含现金+市值）作为
        账户总权益；若不可得则退化为账户可用现金作为近似。
        返回 None 表示无法估值，本次跳过。
        """
        total = 0.0
        any_value = False
        for account_key, api in self._apis.items():
            # 优先取 QMT 账户资产字段（含现金+持仓市值，避免重复计算）
            asset = None
            if api.trader:
                try:
                    account = api.trader.get_account()
                    if account:
                        for attr in ('asset_value', 'm_dAsset', 'total_asset'):
                            val = getattr(account, attr, None)
                            if val is not None:
                                asset = float(val)
                                break
                except Exception:
                    asset = None
            if asset is not None and asset > 0:
                total += asset
                any_value = True
                continue
            # 退化：无资产字段时用可用现金近似（无实时价格，不做持仓估值）
            cash = self._query_cash(api)
            if cash > 0:
                total += cash
                any_value = True
        return total if any_value else None

    def _check_account_drawdown(self):
        """按整个账户权益计算当前回撤并推送告警

        重设（re-arm）逻辑：
        - 真实峰值（peak_equity）保留，仅创新高时上移，从不下降
        - 触发告警后置 armed=false：权益恢复到新高前不重复推送
        - 权益重新创新高时 armed=true，允许再次告警
        """
        if not self._alerter:
            return
        cfg = self._alerter.get_config('drawdown_account')
        threshold = float(cfg.get('threshold', 0.10))
        peak_file = cfg.get('peak_file', 'trading_records/account_equity_peak.json')

        equity = self._account_total_equity()
        if not equity or equity <= 0:
            return

        # 读取历史峰值与 armed 状态
        peaks = {}
        if os.path.isfile(peak_file):
            try:
                with open(peak_file, 'r', encoding='utf-8') as f:
                    peaks = json.load(f)
            except (json.JSONDecodeError, IOError):
                peaks = {}

        previous_peak = float(peaks.get('peak_equity', equity))
        armed = bool(peaks.get('armed', True))

        def _persist(peak_value: float, arm_value: bool, time_str: str):
            data = {
                'peak_equity': peak_value,
                'peak_time': time_str,
                'armed': arm_value,
            }
            try:
                os.makedirs(os.path.dirname(peak_file), exist_ok=True)
                with open(peak_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except IOError as e:
                self.logger.warning(f'写入回撤峰值失败: {e}')

        now_str = time.strftime('%Y-%m-%d %H:%M:%S')

        # 创新高：上移峰值，重置 armed
        if equity >= previous_peak:
            if equity > previous_peak:
                _persist(equity, True, now_str)
                self.logger.info(
                    f'账户权益创新高: {equity:,.2f} (前次峰值 {previous_peak:,.2f})'
                )
            elif not armed:
                # 首次达到原峰值，解除告警锁
                _persist(previous_peak, True, peaks.get('peak_time', now_str))
            return

        # 权益低于峰值
        drawdown = (previous_peak - equity) / previous_peak
        if drawdown < threshold:
            return

        # armed=false：已告警但权益未恢复/未创新高，避免重复推送
        if not armed:
            return

        message = (
            f"账户 **总权益** 回撤超过阈值（整个账户口径）：\n"
            f"- 当前权益: {equity:,.2f}\n"
            f"- 历史峰值: {previous_peak:,.2f}\n"
            f"- 回撤幅度: {drawdown * 100:.2f}% (阈值 {threshold * 100:.0f}%)\n"
            f"- 峰值时间: {peaks.get('peak_time', '未知')}"
        )
        # 回撤告警：群机器人模式（飞书/企微 webhook 都可；私人群只拉自己即等同个人）
        self._alerter.send(
            '账户回撤超阈值告警', message,
            dedup_key=f'drawdown_alert'
        )
        # 真实峰值不变，仅 disarm，直到权益恢复/创新高后再允许触发
        _persist(previous_peak, False, peaks.get('peak_time', now_str))

    def _register_all_instances(self):
        for config in self._configs:
            instance_id = config['instance_id']
            self._instance_meta[instance_id] = {
                'pid': os.getpid(),
                'start_time': time.time(),
                'start_time_str': time.strftime('%Y-%m-%d %H:%M:%S'),
                'strategy_name': config.get('strategy_name', ''),
                'status': 'running',
            }
            self._write_heartbeat(instance_id)
        self.logger.info(f'已注册 {len(self._instance_meta)} 个实例的心跳信息')

    def _write_heartbeat(self, instance_id: str):
        heartbeat_dir = os.path.join('logs', 'instances', instance_id)
        os.makedirs(heartbeat_dir, exist_ok=True)
        heartbeat_file = os.path.join(heartbeat_dir, 'heartbeat.json')
        meta = self._instance_meta.get(instance_id, {})
        data = {
            'instance_id': instance_id,
            'pid': meta.get('pid'),
            'start_time': meta.get('start_time_str', ''),
            'last_heartbeat': time.strftime('%Y-%m-%d %H:%M:%S'),
            'status': meta.get('status', 'unknown'),
        }
        try:
            with open(heartbeat_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            self.logger.warning(f'写入心跳文件失败: {instance_id}, {e}')

    def _check_instance_alive(self, instance_id: str) -> bool:
        meta = self._instance_meta.get(instance_id)
        if not meta:
            return False
        pid = meta.get('pid')
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _restart_instance(self, instance_id: str):
        if self._restart_counts[instance_id] >= self._max_restart_attempts:
            self.logger.error(
                f'实例 {instance_id} 已达最大重试次数 '
                f'({self._max_restart_attempts})，停止重启'
            )
            if instance_id in self._instance_meta:
                self._instance_meta[instance_id]['status'] = 'failed'
            return

        self._restart_counts[instance_id] += 1
        self.logger.info(
            f'尝试重启实例 {instance_id} '
            f'(第{self._restart_counts[instance_id]}次)'
        )

        config = None
        for c in self._configs:
            if c['instance_id'] == instance_id:
                config = c
                break
        if not config:
            return

        try:
            strategy_name = config['strategy_name']
            strategy_class = get_strategy(strategy_name)
            kwargs = dict(get_strategy_default_kwargs(strategy_name))
            kwargs.update(config.get('kwargs', {}))

            account_id = config.get('account_id', 'default')
            mode = config.get('mode', 'sim')
            account_key = f"{account_id}_{mode}"
            api = self._apis.get(account_key)

            if api:
                book = self._books.get(instance_id)
                if book:
                    api.add_strategy(
                        strategy_class,
                        instance_id=instance_id,
                        virtual_book=book,
                        **kwargs
                    )
                    self._instance_meta[instance_id]['status'] = 'running'
                    self._instance_meta[instance_id]['start_time'] = time.time()
                    self._instance_meta[instance_id]['start_time_str'] = time.strftime('%Y-%m-%d %H:%M:%S')
                    self._write_heartbeat(instance_id)
                    self.logger.info(f'实例 {instance_id} 重启成功')
        except Exception as e:
            self.logger.error(f'实例 {instance_id} 重启失败: {e}')
            if instance_id in self._instance_meta:
                self._instance_meta[instance_id]['status'] = 'failed'

    def _start_heartbeat(self):
        def _heartbeat_loop():
            while self._running:
                for instance_id in list(self._instance_meta.keys()):
                    meta = self._instance_meta[instance_id]
                    if meta.get('status') != 'running':
                        continue
                    self._write_heartbeat(instance_id)
                    if not self._check_instance_alive(instance_id):
                        self.logger.warning(f'实例 {instance_id} 心跳检测异常，尝试重启')
                        meta['status'] = 'dead'
                        self._restart_instance(instance_id)
                time.sleep(self._heartbeat_interval)

        self._heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        self.logger.info(f'心跳检测线程已启动 (间隔{self._heartbeat_interval}秒)')

    def get_instance_health(self) -> List[dict]:
        result = []
        for instance_id, meta in self._instance_meta.items():
            heartbeat_dir = os.path.join('logs', 'instances', instance_id)
            heartbeat_file = os.path.join(heartbeat_dir, 'heartbeat.json')
            heartbeat_data = {}
            if os.path.isfile(heartbeat_file):
                try:
                    with open(heartbeat_file, 'r', encoding='utf-8') as f:
                        heartbeat_data = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass
            result.append({
                'instance_id': instance_id,
                'strategy_name': meta.get('strategy_name', ''),
                'pid': meta.get('pid'),
                'start_time': meta.get('start_time_str', ''),
                'status': meta.get('status', 'unknown'),
                'restart_count': self._restart_counts.get(instance_id, 0),
                'last_heartbeat': heartbeat_data.get('last_heartbeat', ''),
            })
        return result
