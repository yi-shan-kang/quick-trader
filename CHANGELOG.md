# 更新日志

## [0.2.0] - 2026-08-21

### 实盘交易链路风险修复

本次更新针对实盘交易链路（下单、回报、持仓同步）进行了全面代码审查和风险修复，共修复 4 项严重问题、3 项中等问题和 6 项低风险项。

---

### 严重问题修复

#### 1. 废单/拒单时冻结资金永不释放

- **文件**: `api/qmt_api.py`
- **风险**: 买入订单被券商拒单（废单）后，VirtualBook 中待确认占用的资金不会被释放，导致该策略可用资金被永久冻结，当天后续买入全部因"虚拟簿记资金不足"被拒绝。
- **修复**: 委托回调中对所有终态（成交/撤单/废单/拒单）统一调用 `on_order_completed()` 释放冻结；`on_order_error` 回调也同步释放 VirtualBook 占用并清理 OrderRouter 映射。

#### 2. 断线无自动重连机制

- **文件**: `api/qmt_api.py`
- **风险**: QMT 客户端重启或网络抖动导致交易通道断开后，进程心跳正常但交易和行情全部静默失效，策略无任何告警和自愈能力。
- **修复**: 新增 `_schedule_reconnect()` + `_reconnect_loop()` 后台重连机制，采用指数退避策略（5s 起步、封顶 30s、最多 12 次），重连成功后重建交易会话、恢复账户订阅并更新 QMTTrader 持有的 xttrader 引用；`close()` 时设置 `_closed` 标志防止退出误触发重连。

#### 3. sim/real 单策略模式"单次触发即退出"

- **文件**: `main.py`
- **风险**: `--mode sim` / `--mode real` 调用 `api.run()` 只触发一次 on_bar（且无价格数据）后立即 `close()`，无法持续交易。真正的实盘入口只有 `--mode instances`，文档未说明，极易误用。
- **修复**: 新增 `_run_until_market_close()`，通过 `api.run_loop()` 在后台线程持续接收行情并触发策略 `on_bar`，主线程阻塞等待至 15:05 收盘或收到 Ctrl+C 后退出。

#### 4. 重启恢复被覆盖 + 现金永不校准

- **文件**: `api/instance_manager.py`, `core/reconciler.py`
- **风险**: 
  - `set_state()` 从持久化恢复簿记后，`initialize_from_account()` 直接覆盖持仓和现金，持久化恢复形同虚设。
  - `Reconciler.auto_correct` 只校准持仓不校准现金，现金漂移（佣金误差、漏记成交）永远不会被纠正。
- **修复**: 
  - 持久化恢复优先：`state_loaded` 为 True 时跳过 `initialize_from_account` 和现金覆盖，仅登记已认领标的防止重复认领。
  - 对账器新增现金自动校准：单策略独占账户时直接校准到实际值；多策略时按比例分摊偏差（跳过虚拟资金实例和有待确认订单的簿记）。

---

### 中等问题修复

#### 5. 下单注册竞态导致回报丢失

- **文件**: `api/qmt_api.py`, `core/executor.py`
- **风险**: `execute_buy` 先下单再注册 OrderRouter 和 VirtualBook，9:30 竞价单若瞬间成交，回报先于注册到达，多策略模式下被当作"外部成交"忽略，导致簿记漏记。
- **修复**: 新增 `_defer_order` / `_defer_trade` 缓冲机制，未匹配的回报暂存等待注册后重放（`_replay_deferred`），60 秒 TTL 超时视为真外部单丢弃；`executor.execute_buy/sell` 注册后调用重放钩子。

#### 6. 撤单重下竞态导致超买

- **文件**: `api/qmt_api.py`
- **风险**: `check_and_retry` 撤单后不等撤单确认就立即按剩余量重下，撤单生效前原单成交部分未扣除，可能导致超买。
- **修复**: 撤单后轮询等待撤单确认（3 秒超时），以最终成交量计算剩余量再决定是否重下。

#### 7. bridge 模式 order_id 不一致导致簿记完全不更新

- **文件**: `api/bridge_trader.py`
- **风险**: `BridgeTrader.buy` 返回 `m_strOrderRef` 用于注册 router，但 `_make_order_obj` 回调对象优先用 `m_strOrderSysID`。桥接服务返回 SysID 时所有回报路由失败，簿记完全不更新，bridge 模式形同不可用。
- **修复**: `_make_order_obj` 和 `_make_trade_obj` 始终使用 `m_strOrderRef` 作为 order_id，与 `buy()/sell()` 返回值保持一致。

---

### 低风险项修复

#### 8. 集合竞价误触发策略

- **文件**: `api/qmt_api.py`
- **修复**: 9:00-9:29 的集合竞价 tick 不再触发 `on_bar`，避免以未稳定的价格下单。

#### 9. 委托失败策略无感知

- **文件**: `core/strategy_logic.py`
- **修复**: StrategyLogic 基类新增 `on_order_error()` 默认实现，子类可重写以实现自定义的错误处理逻辑（清理内部状态、重试等）。

#### 10. 佣金估算偏低

- **文件**: `api/qmt_api.py`
- **修复**: 佣金兜底从万分之一改为万 2.5（最低 5 元），卖出另收 0.05% 印花税，更接近实际成本。

#### 11. 订单状态映射不完整

- **文件**: `api/qmt_api.py`
- **修复**: `_map_order_status` 补全 `ORDER_WAIT_REPORTING`（活跃状态）和 `ORDER_PART_SUCC`（部分成交状态）。

#### 12. xtquant 新旧版本属性兼容

- **文件**: `core/executor.py`, `core/reconciler.py`, `api/instance_manager.py`
- **修复**: `get_cash` / `get_position_size` / `_query_actual_*` 等方法兼容 `cash`/`m_dAvailable` 和 `volume`/`m_nVolume` 两种属性名，避免老版本 xtquant 静默读 0 导致误清簿记。

#### 13. 死代码清理与线程安全

- **文件**: `api/qmt_api.py`
- **修复**: 删除 `wait_order_completed` 死代码（用字符串比对 int 状态，永远超时）；`pending_orders` 和新增的 deferred 缓冲跨线程访问加锁。

---

### 保留良好的设计

以下机制在审查中确认设计合理，未做改动：

- 下单前完整校验链（代码格式、交易单位、涨跌停、停牌、资金/持仓、T+1）
- 买入重试次数上限
- bridge 模式回报持久化去重
- 状态持久化 + 心跳检测 + 每日对账框架
- 策略实例隔离（VirtualBook + OrderRouter + Reconciler）
