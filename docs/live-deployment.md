# 实盘配置部署指南（全策略）

> 适用范围：本仓库所有 `strategies/` 内置策略的模拟盘/实盘部署
> 核心原则：**同一套策略代码无缝切换回测 / 模拟 / 实盘**，仅靠运行模式与 `--qmt-path` 指向的 QMT 端区分
> 更新日期：2026-09-02

---

## 目录

1. [运行架构与模式总览](#1-运行架构与模式总览)
2. [环境准备要求](#2-环境准备要求)
3. [依赖组件清单](#3-依赖组件清单)
4. [配置参数说明](#4-配置参数说明)
5. [部署步骤流程（完整生命周期）](#5-部署步骤流程完整生命周期)
6. [策略目录部署要点速查](#6-策略目录部署要点速查)
7. [安全验证措施](#7-安全验证措施)
8. [常见问题解决方案（FAQ）](#8-常见问题解决方案faq)
9. [回滚机制](#9-回滚机制)
10. [风险提示与合规](#10-风险提示与合规)

---

## 1. 运行架构与模式总览

### 1.1 入口与四种模式

所有运行模式统一由 `main.py` 驱动（`--mode` 四选一）：

| 模式 | 说明 | 停止方式 | 适合场景 |
|------|------|---------|---------|
| `backtest` | 回测（自研引擎，QMT/OpenData/富途数据源） | 跑完即停 | 研究与验证 |
| `sim` | 单策略模拟交易（当日进程） | Ctrl+C 或 **15:05 自动退出** | 模拟盘验证 |
| `real` | 单策略实盘交易（当日进程） | Ctrl+C 或 **15:05 自动退出** | 实盘部署 |
| `instances` | 多策略常驻实例 | **5 秒内连按两次 Ctrl+C** | 多策略同时运行 |

> **关键认知**：`sim` 与 `real` 在代码上几乎相同（仅 `is_sim` 布尔不同，不改变下单语义）。
> MiniQMT 一律真实下单（限价单 `FIX_PRICE`）。所谓"模拟盘/实盘"，本质是
> `--qmt-path` 指向**模拟交易端**还是**实盘交易端**的 userdata 目录。

### 1.2 核心组件链路

```
策略 on_bar（行情驱动）
  → StrategyLogic.buy/sell（风控拦截：停牌/涨跌停/T+1）
  → QMTExecutor（VirtualBook 资金/持仓校验）
  → QMTTrader（xttrader.order_stock 限价单 + 待确认跟踪）
  → OrderRouter（订单归属 instance_id 注册，防回报串线）
  → QMT 主推回报 on_stock_order/on_stock_trade
  → 路由到对应实例 strategy.on_order/on_trade + VirtualBook 记账
  → 超时未成交 → 自动撤单重试（买重下 ≤3 次 / 卖不重下，等下一信号）
```

关键模块：

| 模块 | 职责 |
|------|------|
| `core/virtual_book.py` | **策略级独立持仓/资金账本（唯一真相）**，基于成交记录而非账户反推；废单自动释放冻结 |
| `core/order_router.py` | 多实例下订单归属路由，保证回报回到正确的策略簿记 |
| `core/reconciler.py` | 盘前（9:00-9:30）自动对账，偏差自动校准 |
| `api/qmt_api.py` | MiniQMT 封装：连接、断线自动重连（5s 起步指数退避，最多 12 次）、撤单重试、回报处理 |
| `api/instance_manager.py` | `instances` 模式管理：状态持久化、心跳、对账线程 |
| `api/qmt_bridge_client.py` | bridge 模式（轮询大 QMT server），非默认 |

---

## 2. 环境准备要求

### 2.1 硬件与系统

| 项 | 要求 |
|----|------|
| 操作系统 | Windows（QMT 客户端仅支持 Windows） |
| 网络 | 能访问行情/交易服务器；建议有线网络或稳定无线 |
| 运行时长 | 交易时段 9:30-15:00 需保持运行，建议部署机常驻 |
| Python | 3.9+（项目内虚拟环境 `.venv/`） |

### 2.2 软件安装步骤

```powershell
# 1) 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 2) 安装依赖
pip install -r requirements.txt

# 3) 安装 QMT 客户端（券商版）
#    - 从券商官网/营业部获取 QMT 安装包
#    - 安装并登录
#    - 勾选"独立交易"进入 MiniQMT 模式（极简模式）
#    - 记录 userdata_mini 目录（如 D:\国金QMT交易端模拟New\userdata_mini）

# 4) 安装 XtQuant 库（随 QMT 提供，不在 PyPI）
#    - 从 QMT 官网或安装目录获取，解压后
pip install <XtQuant路径>\xtquant

# 5) 验证（关键）
.venv\Scripts\python.exe -u -c "from xtquant import xtdata; print('xtquant OK')"
```

### 2.3 双环境 QMT（强烈建议）

| 环境 | userdata 目录 | 用途 |
|------|--------------|------|
| 模拟交易端 | 如 `D:\国金QMT交易端模拟New\userdata_mini` | 模拟盘验证 |
| 实盘交易端 | 如 `D:\国金QMT交易端\userdata_mini` | 实盘运行 |

两份代码相同，仅 `--qmt-path` 不同。**先模拟端跑通，再切实盘端。**

---

## 3. 依赖组件清单

### 3.1 `requirements.txt` 依赖分级

| 依赖 | 级别 | 用途 | 实盘必需 |
|------|------|------|:---:|
| `xtquant` | 交易核心 | 下单/回报/持仓/行情主推（**需随 QMT 安装，不在 PyPI**） | ✅ |
| `pandas>=2.2.0` | 核心 | 行情 DataFrame / 策略计算 | ✅ |
| `numpy>=1.26.0` | 核心 | 数值计算 | ✅ |
| `requests>=2.31.0` | 可选 | bridge 模式轮询大 QMT server | 仅 bridge |
| `psutil>=5.9.6` | 监控(可选) | 进程存活/心跳监控 | 建议 |
| `PyQt5` / `pyqtgraph` | 回测 GUI | 回测报告窗口 | 回测用 |
| `matplotlib` / `plotly` | 可视化 | HTML 报告 | 回测用 |
| `backtrader` | 旧引擎 | 兼容旧策略 | 仅旧策略 |
| `akshare>=1.12.0` | 数据 | 补充数据源 | 数据校验用 |
| `baostock` / `yahooquery` | 数据 | 备用行情源 | 可选 |
| `pyarrow>=24.0.0` | 缓存 | parquet 磁盘缓存 | ✅ |
| `flask` / `dash` | Web | Web 查看器 / 实时监控 | 可选 |
| `tqdm` | 工具 | 进度条 | 可选 |

> 模拟盘与实盘**无依赖差异**——差异只在于连接的 QMT 客户端环境。

### 3.2 非 pip 组件

| 组件 | 获取方式 |
|------|---------|
| QMT 客户端 | 券商提供，需登录 + MiniQMT 模式 |
| XtQuant（xtquant） | QMT 配套目录，`pip install` 其 wheel |

---

## 4. 配置参数说明

### 4.1 命令行参数（`main.py`）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--mode` | `backtest` | `backtest`/`sim`/`real`/`instances` |
| `--strategy` | `double_ma` | 策略注册名（见第 6 章速查表） |
| `--period` | `1d` | `1d`/`1m`/`5m`/`15m`/`30m`/`60m`/`tick` |
| `--pool` | 策略默认 | 股票池板块（沪深300/中证1000/中证全指/中小综指等） |
| `--start` / `--end` | 策略默认 | 回测区间 |
| `--data-source` | `qmt` | `qmt`/`open`(OpenData)/`futu` |
| `--config` | 无 | YAML 配置文件（命令行覆盖 YAML） |
| `--qmt-path` | `D:\qmt\userdata_mini` | **QMT userdata_mini 目录（区分模拟/实盘）** |
| `--account` | 自动取第一个 | QMT 资金账号 |
| `--instances` | 无 | instances JSON 路径或简名（`sim`） |
| `--cache-dir` | 项目 `.cache` | 缓存目录 |
| `--mem-limit` | 500 | 内存缓存对象上限 |
| `--debug` | False | 详细日志（文件恒有 DEBUG，控制台需环境变量） |
| `--ai-mode` | False | 跳过 GUI（自动化） |
| `--no-record` | False | 不记录回测结果 |
| `--slippage` | 策略默认 | 滑点百分比 |
| `--strategy-params` | 无 | 动态覆盖策略参数，如 `"stop_loss=0.90,holdings_num=8"` 或 JSON |

### 4.2 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `QMT_LOG_LEVEL` | `INFO` | 控制台日志级别；sim/real 下 `--debug` 只设置此变量，需 `set QMT_LOG_LEVEL=DEBUG` 才在控制台显示 DEBUG（文件恒为 DEBUG） |
| `QMT_LOG_FORMAT` | 文本 | 设为 `json` 输出结构化日志 |

### 4.3 日志与状态目录（本机生成，不提交）

| 目录 | 内容 |
|------|------|
| `logs/` | `<时间戳>_<策略名>.log`（全局）；`logs/instances/<instance_id>/`（实例级日志 + heartbeat.json） |
| `trading_records/<instance_id>/` | `state_*.json`（原子写状态）、`trades_*.csv`（成交流水）、`daily_stats_*.csv` |
| `config/` | instances JSON（**被 .gitignore 忽略，需本机自建**） |

> 部署相关 `.gitignore`：`logs/*`、`config/`、`trading_records` 均不上传仓库。

### 4.4 instances JSON 模板

先创建 `config/instances_sim_config.json`（目录被 gitignore，需本机自建）：

```json
{
  "instances": [
    {
      "instance_id": "qixing_gaozhao_sim",
      "strategy_name": "qixing_gaozhao",
      "mode": "sim",
      "account_id": "80000123",
      "initial_capital": 100000,
      "claim_existing_positions": true,
      "cash_ratio": 0.5,
      "kwargs": {}
    },
    {
      "instance_id": "bank_rotation_sim",
      "strategy_name": "bank_rotation",
      "mode": "sim",
      "account_id": "80000123",
      "initial_capital": 200000,
      "claim_existing_positions": true,
      "cash_ratio": 0.5,
      "kwargs": {"spread_threshold": 0.004}
    }
  ]
}
```

字段说明：

| 字段 | 含义 |
|------|------|
| `instance_id` | 实例唯一标识（日志/订单路由/持久化隔离） |
| `strategy_name` | 策略注册名 |
| `mode` | `sim` 或 `real`（同账户可混跑） |
| `account_id` | QMT 资金账号；相同账号共享一个 QMTAPI |
| `initial_capital` | 虚拟初始资金（仅 `claim_existing_positions=false` 或无法认领时用虚拟资金） |
| `claim_existing_positions` | **首次启动**是否认领账户未归属持仓为策略簿记；持久化状态存在时以持久化恢复为准，此字段只防重复认领 |
| `cash_ratio` | 多实例拆分现金比例（同账户多策略） |
| `kwargs` | 覆盖策略默认参数 |

---

## 5. 部署步骤流程（完整生命周期）

### 阶段 0：数据准备（回测/上线通用）

```powershell
# 行情预下载（QMT 源；pool 视策略而定，ETF/固定池策略可跳过）
.venv\Scripts\python.exe -u DATA\download_qmt_market_data.py --pool 中证1000 --start 2015-01-01 --end 2026-04-28

# 财务预下载（依赖财务的策略：small_cap / undervalued / rothman_value 等）
.venv\Scripts\python.exe -u DATA\download_qmt_financial_data.py --pool 中证1000 --start 2015-01-01

# 历史成分股（QMT 客户端需下载"板块成分股历史变动信息"；QMT 免费版仅覆盖上证50/沪深300/中证500）
# 见 DATA\download_csindex_constituent.py 与 docs/data-download.md

# ETF 策略离线缓存种子（QMT 未连接时；已有 DATA\cache\etf_data_cache_qmt_10y.pkl）
.venv\Scripts\python.exe -u DATA\seed_etf_qmt_cache.py
```

> **数据质量门禁**：回测/实盘前先做数据质量校验，过滤价格 ≤1 元或长期恒定的异常标的，
> 避免 QMT 对退市股/停牌股价格缺陷导致假信号。参考 `DATA/check_price_quality.py`。

### 阶段 1：回测复核（每策略必做）

先跑一遍目标策略回测确认基线指标与文档一致（命令见第 6 章），推荐与实盘相同区间验证。

### 阶段 2：模拟盘验证（必须，1-4 周）

```powershell
# 单策略模拟盘
.venv\Scripts\python.exe -u main.py --mode sim --strategy qixing_gaozhao --qmt-path "D:\国金QMT交易端模拟New\userdata_mini" --account 80000123 --debug
```

验证清单：

- [ ] 日志出现「动量排名前5」/ 选股信号（对应策略的 info 日志）
- [ ] 买入/卖出成交，数量为 100 股整数倍
- [ ] 模拟账户持仓与 VirtualBook 一致
- [ ] 止损/换仓/空仓规则按预期触发
- [ ] 无 `交易接口未初始化` / `未找到账户` 报错
- [ ] 15:05 自动退出干净

### 阶段 3：小资金实盘（建议首期 ≤ 10 万/策略）

```powershell
.venv\Scripts\python.exe -u main.py --mode real --strategy qixing_gaozhao --qmt-path "D:\国金QMT交易端\userdata_mini" --account 80000123 --debug
```

- 确认 `--qmt-path` 指向**实盘端**（`is_sim` 不改变下单，务必人工核对目录）
- 首次运行会从账户认领持仓（`claim_existing_positions` 默认开启）

### 阶段 4：多策略常驻（instances）

```powershell
.venv\Scripts\python.exe -u main.py --mode instances --instances config\instances_sim_config.json --debug
```

- 无收盘自动退出，需 **5 秒内连按两次 Ctrl+C** 退出
- 盘前 9:00-9:30 自动对账
- 建议用批处理封装（参考 `start_sim_strategies.bat`，**注意修正其中的旧路径**）

### 阶段 5：日常运维

| 操作 | 方式 |
|------|------|
| 次日自动启动 | Windows 计划任务，交易日 9:15 启动对应命令 |
| 日志清理 | `clean_old_logs.bat`（删除 logs/ 下 1 天前文件） |
| 断线恢复 | QMTAPI 自动重连（指数退避 5s→30s，≤12 次）；超限需人工检查 QMT 客户端 |
| 监控 | `monitor/realtime_monitor.py`（可选，Dash） |

---

## 6. 策略目录部署要点速查

> 完整扫描结论：`strategies/` 下 16 个策略包、17 个注册策略；`strategies_my/` 不存在。

### 6.1 总览表

| 注册名 | 标的类型 | 调仓 | 数据需求 | 关键风控参数（默认） | 默认回测区间 |
|--------|---------|------|---------|---------------------|-------------|
| `all_weather_rotation` | 股票+海外ETF | 月 | 财务+沪深300/中小综指**历史**成分股 | position_ratio=0.95, stop_loss 8%, max_stocks=9 | 2020-04-28~2026-04-28 |
| `arbr_small_cap` | 股票小市值 | 周(4月空仓) | EPS 财务+中证1000 历史成分股 | max_stocks=3, stoploss 9%, 止盈2倍 | 2020-04-28~2026-04-28 |
| `bank_rotation` | 四大行固定池 | 每日/可1m | 仅 4 只银行行情 | spread/switch_threshold=0.004 | 2020-04-28~2026-04-28 |
| `dividend_value_growth` | 高股息价值 | 月 | 财务(股息/PE/PEG/ROE)+中证全指历史成分股 | max_stocks=10, PE≤25, 无止损 | 2020-04-28~2026-04-28 |
| `etf_momentum_epo` | ETF 13 只 | 月 | 自持 ETF 行情 | top_n=3, EPO+风险平价, 无止损 | 2020-04-28~2026-04-28 |
| `first_board_low_open` | 股票首板低开 | 日(T+1) | 中证全指历史成分股 | max_stocks=10, 涨停开板卖出 | — |
| `first_board_low_open_1m` | 股票（分钟版） | 日(09:31买/14:50卖) | 自持约200只+1m 数据 | max_stocks=10, 日内持有 | — |
| `guojiu_small_cap` | 中小板小市值 | 周(1/4月空仓) | 财务+中小综指历史成分股 | max_stocks=8, stoploss 9%, market_stoploss | 2020-04-28~2026-04-28 |
| `ivff3` | 特质波动率因子 | 月 | 财务(ROE/负债/净资产)+中证1000 历史成分股 | max_stocks=50 | 2015-01-01~2026-04-28 |
| `jq_small_cap` | 中小综指小市值 | 日 | Balance/Pershareindex 财务+中小综指历史成分股 | max_stocks=5 | — |
| `medical_multi_factor` | 医疗多因子 | 月 | 财务(ROE/质量分)+中证全指历史成分股 | max_stocks=20 | 2020-04-28~2026-04-28 |
| `qixing_gaozhao` | ETF 38+防御 1 | 日 | 自持 ETF 行情(无财务) | holdings_num=5, 止损8%, 流动性≥1亿, R²≥0.4 | 2020-04-28~2026-04-28 |
| `rothman_value` | 价值蓝筹 | 月 | 多期财务(ROE/FCF/增速)+中证全指历史成分股 | max_stocks=20, 无止损 | — |
| `small_cap` | 全A小市值 | 月(1/4月空仓) | Balance/Pershareindex 财务+中证全指历史成分股 | max_stocks=30, 市值≤30亿, 停牌/ST/科创北交过滤 | 2016-01-01~2026-04-17 |
| `small_cap_roe` | 小市值ROE/ROA | 月 | 财务(ROE/ROA)+中证全指历史成分股 | max_stocks=20, ROE≥15%, ROA≥10%, ≤10元 | 2019-01-01~2026-04-28 |
| `twenty_eight_rotation` | 小市值+指数择时 | 每5交易日 | ETF 行情(择时)+财务+中证全指成分股 | max_stocks=3, 二八止损(必需) | — |
| `undervalued` | 低估值价值 | 月 | 财务(PB/负债/流动比率)+沪深300 历史成分股 | max_stocks=50, PB≤1.8 | 2020-04-28~2026-04-28 |

### 6.2 部署分组要点

**A 组：纯行情策略（无财务依赖，最易上线）**
- `bank_rotation`、`etf_momentum_epo`、`qixing_gaozhao`、`first_board_low_open`（两版）
- 只需行情数据，标的池自持/固定；部署命令简单，无需 `--pool`/财务下载

**B 组：财务选股策略（需财务 + 历史成分股）**
- `small_cap`、`undervalued`、`rothman_value`、`ivff3`、`dividend_value_growth` 等
- 上线前**必须**预下载财务数据与历史成分股，并跑数据质量校验；
- 注意：QMT 免费版历史成分股仅覆盖上证50/沪深300/中证500，中证1000/中证全指需聚宽等其他源（见项目记忆与 `docs/data-download.md`）

**C 组：分钟级策略（需 1m 数据）**
- `first_board_low_open_1m`：需 `--period 1m`，`--pool` 无（自持池），盘中 09:31 买/11:28 卖/14:50 卖

### 6.3 运行脚本（各策略目录内 run*.py）

| 策略 | 顶层 run 脚本 |
|------|--------------|
| `small_cap` | `strategies/small_cap_strategy/run_small_cap_backtest*.py`（4 个，区分中证1000/中证全指985/Opt/AkShare） |
| `qixing_gaozhao` | `strategies/qixing_gaozhao_etf_strategy/run_qixing_gaozhao_backtest.py` |
| 其余策略 | `strategies/<策略>/optimization/run_*.py`（回测优化用，非实盘入口） |

> 实盘入口统一为 `main.py --mode sim/real/instances --strategy <注册名>`，不要直接用策略目录的 run 脚本跑实盘。

---

## 7. 安全验证措施

### 7.1 部署前静态验证（一键自检）

```powershell
# 1) 环境与连接
.venv\Scripts\python.exe -u -c "from core.data.qmt import QMTDataProcessor; p=QMTDataProcessor(); print('QMT connected:', p.check_connection())"

# 2) 策略可被发现与实例化
.venv\Scripts\python.exe -u -c "from strategies import get_strategy; s=get_strategy('qixing_gaozhao'); print('strategy found:', s is not None)"

# 3) 参数可被动态覆盖（示例）
.venv\Scripts\python.exe -u main.py --mode backtest --strategy qixing_gaozhao --period 1d --start 2024-01-01 --end 2024-12-31 --strategy-params "stop_loss=0.90" --ai-mode
```

### 7.2 运行中人工核对（首日必做）

| 时间 | 检查项 |
|------|--------|
| 09:25 | 程序启动日志无 ERROR；持仓认领日志与账户一致 |
| 09:35 | 首笔 on_bar 触发；信号输出正常 |
| 09:45 | 若当日有信号，核对委托/成交回报与策略意图一致 |
| 11:30/14:00 | 抽看 VirtualBook 与账户持仓一致 |
| 15:00 | 收盘清算；15:05 自动退出（sim/real） |
| 盘后 | 核对 `trading_records/<id>/trades_*.csv` 与账户流水 |

### 7.3 安全机制（框架内置，需知晓）

- **涨跌停/停牌拦截**：`buy` 拦截涨停、`sell` 拦截跌停，停牌不交易
- **T+0/T+1**：ETF 已设 T+0（可当日买卖）；股票默认 T+1（可卖=总持仓-当日买入）
- **资金/持仓前置校验**：VirtualBook 不足直接拒绝，防超买超卖
- **撤单重试**：超时 30s 未成交 → 撤单；撤单确认后（≤3s）买单自动重下（收敛价，≤3 次），卖单不重下
- **断线重连**：指数退避自动重连 ≤12 次
- **失败安全**：xtquant 缺失/账户缺失时不崩进程，下单统一返回 None 并 ERROR 日志
- **废单冻结释放**：所有终态统一 `on_order_completed` 释放占用（0.2.0 修复）

---

## 8. 常见问题解决方案（FAQ）

| # | 现象 | 原因 | 解决 |
|---|------|------|------|
| 1 | `QMT未连接且本地缓存未完整覆盖` | QMT 客户端未启动/未登录 | 启动并登录 QMT MiniQMT；或先执行数据预下载/缓存种子脚本 |
| 2 | `xtquant 未安装` | XtQuant 未装 | 从 QMT 配套目录 `pip install xtquant` |
| 3 | `交易接口未初始化`（下单返回 None） | xtquant 缺失或未找到账户 | 检查 QMT 登录与 `--account`；未指定账户时确认可自动取到 |
| 4 | `未找到账户` | 账户查询失败 | 显式传 `--account` |
| 5 | `自动发现策略模块失败: strategies.etf_momentum_epo_strategy, No module named 'scipy'` | 该策略 import scipy，环境未装 | 属非致命告警，仅该策略不可用；安装 scipy 或忽略 |
| 6 | `--mode sim/real` 不退出 / 无法下单 | 旧版"单次触发即退出" | 已由 `_run_until_market_close` 修复（0.2.0）；升级代码 |
| 7 | instances 模式无法退出 | 需双击 Ctrl+C | 5 秒内连按两次 Ctrl+C |
| 8 | 双击 `start_sim_now.bat` 失败 | bat 内 `cd` 到不存在的 E 盘旧目录 | 改用 `start_sim_strategies.bat` 或手动命令行，先建 `config/instances_sim_config.json` |
| 9 | 控制台看不到 DEBUG | `--debug` 在 sim/real 只设环境变量 | `set QMT_LOG_LEVEL=DEBUG` 后再运行；文件日志恒为 DEBUG |
| 10 | 废单后资金永久冻结 | 旧 bug | 升级至 ≥0.2.0（终态统一释放占用） |
| 11 | 断线后交易静默失效 | 旧版无重连 | ≥0.2.0 自动重连；超 12 次需人工查 QMT |
| 12 | 可用资金恒为负 | 重启后旧待确认订单残留 | ≥0.2.0 `set_state` 清空旧 pending_orders |
| 13 | 撤单后重复买入（超买） | 撤单未确认即重下（旧 bug） | ≥0.2.0 撤单确认后按最终成交量重下 |
| 14 | 实盘结果与回测偏差大 | 滑点/成交时点/QDII 溢价/止损粒度差异 | 见各策略 readme「注意事项」；用模拟盘校准 |

---

## 9. 回滚机制

### 9.1 代码回滚（git）

```powershell
# 查看历史
git log --oneline -10
# 回滚到指定提交（保留工作区，可反悔）
git checkout <commit_hash> -- main.py strategies/
# 硬回滚（谨慎，本地未推送提交会丢失）
git reset --hard <commit_hash>
```

### 9.2 状态回滚（instances 运行中）

- 每个实例状态原子保存在 `trading_records/<instance_id>/state_*.json`
- 重启时若检测到持久化状态，**优先恢复而非覆盖**（避免重启丢失簿记）
- 回滚方式：停止实例 → 删除/备份对应 `state_*.json` → 重启（将从账户重新认领）
- 如需"清空重来"：`claim_existing_positions` 决定是否认领账户持仓

### 9.3 参数回滚（无需改代码）

```powershell
# 用 --strategy-params / instances 的 kwargs 覆盖，改回即回滚
--strategy-params "stop_loss=0.92,holdings_num=5"     # 恢复默认
--strategy-params "stop_loss=0.98"                    # 临时保守
```

### 9.4 紧急停盘（人工干预）

| 情形 | 操作 |
|------|------|
| 策略异常连续错单 | 立即 Ctrl+C 停程序 → 人工撤 QMT 端未成交单 → 核对成交流水 |
| 仅需暂停买入 | 临时改参 `position_ratio=0` 重启，只出不进 |
| 需全清仓 | 停程序后，在 QMT 客户端手动市价清仓，勿依赖策略 |
| 服务器断电 | 重启后重跑实例；VirtualBook 从持久化/账户自动恢复 |

### 9.5 数据回滚

```powershell
# 缓存数据异常：删除对应缓存目录强制重下
Remove-Item .cache\QMTData\market\<symbol> -Recurse -Force
Remove-Item .cache\QMTData\financial\<symbol> -Recurse -Force
```

---

## 10. 风险提示与合规

1. **市场风险**：过往回测不代表未来收益；ETF 动量策略在长期震荡/单边下跌中仍会回撤（如 qixing_gaozhao 5.7 年最大回撤 -20.6%，其他股票策略更大）。
2. **技术风险**：自动化交易依赖 QMT 客户端常驻；断线超过重连窗口（约 6 分钟指数退避 ×12 次）需人工介入。建议 QMT 开机自启 + 每日计划任务重启策略。
3. **模拟先行**：**未经至少 1-4 周模拟盘验证，禁止直接上实盘**。
4. **小资金起步**：首期实盘单策略建议 ≤ 总资金 20%，≤10 万元。
5. **佣金口径**：实盘佣金默认按万 2.5（最低 5 元）估算、卖出 0.05% 印花税，与回测 `backtest_config` 有差异时以实盘实际为准。
6. **合规**：请确保策略与交易行为符合当地法律法规及券商规定；本仓库仅用于学习研究，不构成投资建议。
7. **成分股口径**：回测中依赖"当前成分股"冒充历史会产生幸存者偏差，务必使用历史成分股数据（QMT"板块成分股历史变动信息"或聚宽 CSV）。

---

## 附录 A：相关文档索引

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 框架总览、环境搭建、免责声明 |
| [usage.md](usage.md) | 回测/模拟/实盘/多实例/数据下载/Web 查看器 |
| [strategy-development.md](strategy-development.md) | 策略开发规范、内置策略、优化工作流 |
| [architecture.md](architecture.md) | 引擎/策略/数据/执行器/多实例架构 |
| [data-download.md](data-download.md) | 多数据源、数据下载、完整性校验、幸存者偏差说明 |
| [cache.md](cache.md) | 缓存架构与清理 |
| [backtest-recorder.md](backtest-recorder.md) | 回测结果记录 |
| [project-structure.md](project-structure.md) | 目录结构说明 |
| [CHANGELOG.md](../CHANGELOG.md) | 版本记录与实盘链路修复（0.2.0） |
| `strategies/qixing_gaozhao_etf_strategy/部署指南.md` | 单策略部署示例（含止损粒度/QDII 差异说明） |
