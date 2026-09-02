"""
可视化模块 - 回测结果图表

生成图表：
1. 净值曲线（组合 vs 基准）
2. 各桶权重变化堆叠图
3. 回撤曲线
4. 月度收益热力图
5. 交易记录统计

支持两种输出：
- matplotlib 静态图（保存PNG）
- HTML 交互式报告
"""

import os
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from backtest import BacktestResult


def _setup_chinese_font():
    """设置中文字体"""
    for font_name in ["Microsoft YaHei", "SimHei", "PingFang SC", "Arial Unicode MS"]:
        try:
            font_manager.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def plot_nav_curve(result: BacktestResult, save_path: str = None) -> str:
    """净值曲线：组合 vs 基准"""
    _setup_chinese_font()

    fig, ax = plt.subplots(figsize=(14, 6))

    nav_pct = (result.nav_series / result.nav_series.iloc[0] - 1) * 100
    bench_pct = (result.benchmark_series / result.benchmark_series.iloc[0] - 1) * 100

    ax.plot(nav_pct.index, nav_pct.values, linewidth=2, color="#c8862a", label="三层阁策略组合")
    ax.plot(bench_pct.index, bench_pct.values, linewidth=1.5, color="#1a3a5c",
            label="沪深300基准", alpha=0.7)

    ax.fill_between(nav_pct.index, 0, nav_pct.values, alpha=0.1, color="#c8862a")

    ax.set_title("三层阁策略回测净值曲线", fontsize=16, fontweight="bold")
    ax.set_xlabel("日期", fontsize=11)
    ax.set_ylabel("累计收益率 (%)", fontsize=11)
    ax.legend(loc="upper left", fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)

    # 标注最终收益
    final_nav = nav_pct.iloc[-1]
    final_bench = bench_pct.iloc[-1]
    ax.annotate(f"策略: {final_nav:.1f}%", xy=(nav_pct.index[-1], final_nav),
                fontsize=10, color="#c8862a", fontweight="bold",
                xytext=(-80, 10), textcoords="offset points")
    ax.annotate(f"基准: {final_bench:.1f}%", xy=(bench_pct.index[-1], final_bench),
                fontsize=10, color="#1a3a5c",
                xytext=(-80, -20), textcoords="offset points")

    plt.tight_layout()
    path = save_path or "nav_curve.png"
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def plot_bucket_allocation(result: BacktestResult, save_path: str = None) -> str:
    """各桶权重变化堆叠图"""
    _setup_chinese_font()

    bw = result.bucket_weights.copy()
    bucket_names = {
        "A_stock": "A股权益",
        "US_stock": "海外权益",
        "bond": "债券收益",
        "gold": "黄金",
    }
    cols = [c for c in bw.columns if c in bucket_names]
    bw_plot = bw[cols].rename(columns=bucket_names) * 100

    fig, ax = plt.subplots(figsize=(14, 5))
    colors = ["#c0392b", "#2980b9", "#27ae60", "#f39c12"]

    ax.stackplot(bw_plot.index, bw_plot.T.values, labels=bw_plot.columns,
                 colors=colors, alpha=0.8)

    # 基准线
    targets = result.config.target_allocation
    for i, (bucket, target) in enumerate(targets.items()):
        name = bucket_names.get(bucket, bucket)
        ax.axhline(y=sum(list(targets.values())[:i+1]) * 100,
                   color="white", linestyle="--", alpha=0.5, linewidth=1)

    ax.set_title("四桶配置权重变化", fontsize=16, fontweight="bold")
    ax.set_xlabel("日期", fontsize=11)
    ax.set_ylabel("权重 (%)", fontsize=11)
    ax.legend(loc="upper right", fontsize=10)
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.2, axis="y")

    plt.tight_layout()
    path = save_path or "bucket_allocation.png"
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def plot_drawdown(result: BacktestResult, save_path: str = None) -> str:
    """回撤曲线"""
    _setup_chinese_font()

    nav = result.nav_series
    peak = nav.expanding().max()
    drawdown = (nav - peak) / peak * 100

    bench = result.benchmark_series
    bench_peak = bench.expanding().max()
    bench_dd = (bench - bench_peak) / bench_peak * 100

    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(drawdown.index, drawdown.values, 0, color="#c0392b", alpha=0.4, label="策略回撤")
    ax.plot(bench_dd.index, bench_dd.values, color="#1a3a5c", linewidth=1, label="基准回撤", alpha=0.7)

    ax.set_title(f"回撤曲线（最大回撤: {result.max_drawdown*100:.1f}%）", fontsize=14, fontweight="bold")
    ax.set_xlabel("日期", fontsize=11)
    ax.set_ylabel("回撤 (%)", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    path = save_path or "drawdown.png"
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def plot_monthly_returns(result: BacktestResult, save_path: str = None) -> str:
    """月度收益热力图"""
    _setup_chinese_font()

    nav = result.nav_series
    monthly = nav.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna() * 100

    if len(monthly_ret) < 2:
        return ""

    years = monthly_ret.index.year.unique()
    months = range(1, 13)

    data = np.full((len(years), 12), np.nan)
    for i, year in enumerate(years):
        for j, month in enumerate(months):
            mask = (monthly_ret.index.year == year) & (monthly_ret.index.month == month)
            if mask.any():
                data[i, j] = monthly_ret[mask].iloc[0]

    fig, ax = plt.subplots(figsize=(12, max(3, len(years) * 0.6)))
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=-8, vmax=8)

    ax.set_xticks(range(12))
    ax.set_xticklabels(["1月", "2月", "3月", "4月", "5月", "6月",
                        "7月", "8月", "9月", "10月", "11月", "12月"])
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels(years)

    for i in range(len(years)):
        for j in range(12):
            if not np.isnan(data[i, j]):
                color = "white" if abs(data[i, j]) > 5 else "black"
                ax.text(j, i, f"{data[i, j]:.1f}", ha="center", va="center",
                        fontsize=8, color=color)

    ax.set_title("月度收益热力图 (%)", fontsize=14, fontweight="bold")
    plt.colorbar(im, ax=ax, shrink=0.8)

    plt.tight_layout()
    path = save_path or "monthly_returns.png"
    plt.savefig(path, dpi=150)
    plt.close()
    return path


def generate_html_report(result: BacktestResult, output_dir: str = ".") -> str:
    """生成HTML回测报告"""
    os.makedirs(output_dir, exist_ok=True)

    nav_img = plot_nav_curve(result, os.path.join(output_dir, "nav_curve.png"))
    bucket_img = plot_bucket_allocation(result, os.path.join(output_dir, "bucket_allocation.png"))
    dd_img = plot_drawdown(result, os.path.join(output_dir, "drawdown.png"))
    monthly_img = plot_monthly_returns(result, os.path.join(output_dir, "monthly_returns.png"))

    # 交易统计
    trade_df = result.trade_log.copy() if not result.trade_log.empty else pd.DataFrame()

    # 各桶绩效
    bucket_perf = {}
    for bucket in result.config.target_allocation:
        bucket_positions = [
            (s.date, s.bucket_values.get(bucket, 0))
            for s in result.snapshots
        ]
        if bucket_positions:
            vals = pd.Series([v for _, v in bucket_positions], index=[d for d, _ in bucket_positions])
            if len(vals) > 1 and vals.iloc[0] > 0:
                ret = vals.iloc[-1] / vals.iloc[0] - 1
            else:
                ret = 0
            bucket_perf[bucket] = ret

    bucket_names = {"A_stock": "A股权益", "US_stock": "海外权益", "bond": "债券收益", "gold": "黄金"}

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>三层阁策略回测报告</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 0; padding: 20px; background: #f8f6f1; color: #2c2c2c; }}
.container {{ max-width: 1000px; margin: 0 auto; }}
h1 {{ text-align: center; color: #1a3a5c; border-bottom: 3px solid #c8862a; padding-bottom: 15px; }}
h2 {{ color: #1a3a5c; border-left: 4px solid #c8862a; padding-left: 12px; margin-top: 35px; }}
.stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 20px 0; }}
.stat {{ background: white; padding: 18px; border-radius: 8px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.stat .val {{ font-size: 1.6em; font-weight: 700; color: #1a3a5c; }}
.stat .lbl {{ font-size: 0.85em; color: #666; margin-top: 4px; }}
img {{ width: 100%; border-radius: 8px; margin: 15px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
table {{ width: 100%; border-collapse: collapse; margin: 15px 0; background: white; border-radius: 8px; overflow: hidden; }}
th {{ background: #1a3a5c; color: white; padding: 10px; text-align: left; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
tr:nth-child(even) {{ background: #f9f7f2; }}
.summary {{ background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin: 20px 0; }}
</style>
</head>
<body>
<div class="container">
<h1>三层阁策略回测报告</h1>
<p style="text-align:center; color:#666;">
回测区间: {result.nav_series.index[0].strftime('%Y-%m-%d')} ~ {result.nav_series.index[-1].strftime('%Y-%m-%d')}
</p>

<h2>核心绩效指标</h2>
<div class="stats">
<div class="stat"><div class="val">{result.total_return*100:.1f}%</div><div class="lbl">累计收益</div></div>
<div class="stat"><div class="val">{result.benchmark_return*100:.1f}%</div><div class="lbl">基准收益(沪深300)</div></div>
<div class="stat"><div class="val" style="color:#c0392b;">+{(result.total_return-result.benchmark_return)*100:.1f}%</div><div class="lbl">超额收益</div></div>
<div class="stat"><div class="val">{result.annual_return*100:.1f}%</div><div class="lbl">年化收益</div></div>
<div class="stat"><div class="val" style="color:#c0392b;">{result.max_drawdown*100:.1f}%</div><div class="lbl">最大回撤</div></div>
<div class="stat"><div class="val">{result.sharpe_ratio:.2f}</div><div class="lbl">夏普比率</div></div>
<div class="stat"><div class="val">{result.volatility*100:.1f}%</div><div class="lbl">年化波动</div></div>
<div class="stat"><div class="val">{result.num_trades}</div><div class="lbl">交易次数</div></div>
<div class="stat"><div class="val">{len(result.nav_series)}周</div><div class="lbl">回测周期</div></div>
</div>

<h2>净值曲线</h2>
<img src="{os.path.basename(nav_img)}" alt="净值曲线">

<h2>四桶配置变化</h2>
<img src="{os.path.basename(bucket_img)}" alt="配置变化">

<h2>回撤分析</h2>
<img src="{os.path.basename(dd_img)}" alt="回撤曲线">

<h2>月度收益热力图</h2>
<img src="{os.path.basename(monthly_img)}" alt="月度收益">

<h2>各桶收益贡献</h2>
<table>
<tr><th>配置桶</th><th>基准比例</th><th>期间收益</th></tr>
"""

    for bucket, ret in bucket_perf.items():
        html += f'<tr><td>{bucket_names.get(bucket, bucket)}</td><td>{result.config.target_allocation[bucket]*100:.0f}%</td><td>{ret*100:.1f}%</td></tr>'

    html += """
</table>
"""

    if not trade_df.empty:
        html += """
<h2>交易记录统计</h2>
<table>
<tr><th>操作</th><th>次数</th><th>平均金额</th></tr>
"""
        for action in ["buy", "sell"]:
            subset = trade_df[trade_df["action"] == action] if "action" in trade_df.columns else pd.DataFrame()
            if not subset.empty:
                avg_val = subset["value"].abs().mean() if "value" in subset.columns else 0
                html += f'<tr><td>{"买入" if action=="buy" else "卖出"}</td><td>{len(subset)}</td><td>{avg_val:,.0f}元</td></tr>'

        html += "</table>"

        html += """
<h2>最近10笔交易</h2>
<table>
<tr><th>日期</th><th>操作</th><th>代码</th><th>名称</th><th>桶</th><th>金额</th><th>原因</th></tr>
"""
        recent = trade_df.tail(10)
        for _, row in recent.iterrows():
            action_cn = "买入" if row.get("action") == "buy" else "卖出"
            html += f'<tr><td>{row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else row["date"]}</td><td>{action_cn}</td><td>{row.get("code", "")}</td><td>{row.get("name", "")}</td><td>{row.get("bucket", "")}</td><td>{row.get("value", 0):,.0f}</td><td>{row.get("reason", "")}</td></tr>'
        html += "</table>"

    html += """
<div class="summary">
<h2 style="border:none; padding:0;">策略说明</h2>
<p><strong>核心方法论：</strong></p>
<ul>
<li><strong>四桶配置</strong>：A股35% + 美股35% + 债券15% + 黄金15%</li>
<li><strong>2%动态再平衡</strong>：偏离基准超2%触发高抛低吸</li>
<li><strong>封基选基</strong>：4周净增+年化折价排名买入，10周恶化卖出</li>
<li><strong>永远满仓</strong>：不择时，不空仓</li>
<li><strong>弃弱留强</strong>：不追求选最优，只求不持有最差</li>
</ul>
<p style="color:#999; font-size:0.85em; margin-top:15px;">本报告由三层阁策略回测系统自动生成，仅供学习研究参考，不构成投资建议。</p>
</div>

</div>
</body>
</html>
"""

    report_path = os.path.join(output_dir, "backtest_report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)

    return report_path
