"""
Backtest report generator.

Creates an HTML report with equity curves, drawdown charts,
trade distribution by setup/time/day, and parameter summary.

Usage:
    from backtest.report import generate_report
    generate_report(backtest_results, "reports/backtest_20260401.html")
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
import json
import logging

logger = logging.getLogger(__name__)


def generate_report(
    results,
    output_path: str = "reports/backtest_report.html",
    monte_carlo_results=None,
    walk_forward_results=None,
) -> str:
    """Generate an HTML backtest report.

    Args:
        results: BacktestResults from backtester
        output_path: Where to save the HTML file
        monte_carlo_results: Optional MonteCarloResults
        walk_forward_results: Optional WalkForwardResults

    Returns:
        Path to generated report
    """
    trades_df = results.to_dataframe()

    # Compute analytics
    equity_data = _equity_curve_data(results)
    drawdown_data = _drawdown_data(results)
    by_setup = _trades_by_setup(trades_df)
    by_hour = _trades_by_hour(trades_df)
    by_weekday = _trades_by_weekday(trades_df)
    by_exit = _trades_by_exit_reason(trades_df)
    streaks = _win_loss_streaks(results.trades)

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MNQ ORB Bot — Backtest Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0a0a0a; color: #e0e0e0; padding: 24px; }}
    .header {{ text-align: center; margin-bottom: 32px; }}
    .header h1 {{ font-size: 28px; color: #fff; }}
    .header p {{ color: #888; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
             gap: 16px; margin-bottom: 32px; }}
    .stat {{ background: #1a1a1a; border-radius: 8px; padding: 16px;
             border: 1px solid #333; }}
    .stat .label {{ font-size: 12px; color: #888; text-transform: uppercase; }}
    .stat .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
    .stat .value.green {{ color: #22c55e; }}
    .stat .value.red {{ color: #ef4444; }}
    .stat .value.neutral {{ color: #f59e0b; }}
    .chart-container {{ background: #1a1a1a; border-radius: 8px; padding: 20px;
                        border: 1px solid #333; margin-bottom: 24px; }}
    .chart-container h2 {{ font-size: 16px; margin-bottom: 12px; color: #ccc; }}
    canvas {{ max-height: 300px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
    th {{ color: #888; font-size: 12px; text-transform: uppercase; }}
    td {{ font-size: 14px; }}
    .verdict {{ text-align: center; padding: 20px; margin: 24px 0; border-radius: 8px;
                font-size: 18px; font-weight: 600; }}
    .verdict.pass {{ background: #052e16; border: 1px solid #22c55e; color: #22c55e; }}
    .verdict.fail {{ background: #2d0a0a; border: 1px solid #ef4444; color: #ef4444; }}
    .verdict.marginal {{ background: #2d1f05; border: 1px solid #f59e0b; color: #f59e0b; }}
</style>
</head>
<body>
<div class="header">
    <h1>MNQ ORB Bot — Backtest Report</h1>
    <p>{trades_df['date'].min()} to {trades_df['date'].max()} · {results.total_trades} trades</p>
</div>

<!-- Summary Stats -->
<div class="grid">
    <div class="stat">
        <div class="label">Total P&L</div>
        <div class="value {'green' if results.total_pnl > 0 else 'red'}">${results.total_pnl:,.2f}</div>
    </div>
    <div class="stat">
        <div class="label">Win Rate</div>
        <div class="value {'green' if results.win_rate >= 0.65 else 'neutral' if results.win_rate >= 0.50 else 'red'}">{results.win_rate:.1%}</div>
    </div>
    <div class="stat">
        <div class="label">Profit Factor</div>
        <div class="value {'green' if results.profit_factor >= 1.8 else 'neutral' if results.profit_factor >= 1.2 else 'red'}">{results.profit_factor:.2f}</div>
    </div>
    <div class="stat">
        <div class="label">Max Drawdown</div>
        <div class="value red">${results.max_drawdown:,.2f}</div>
    </div>
    <div class="stat">
        <div class="label">Avg Winner</div>
        <div class="value green">${results.avg_winner:,.2f}</div>
    </div>
    <div class="stat">
        <div class="label">Avg Loser</div>
        <div class="value red">${results.avg_loser:,.2f}</div>
    </div>
    <div class="stat">
        <div class="label">Max Consec. Losses</div>
        <div class="value neutral">{results.max_consecutive_losses}</div>
    </div>
    <div class="stat">
        <div class="label">Total Trades</div>
        <div class="value neutral">{results.total_trades}</div>
    </div>
</div>

<!-- Verdict -->
<div class="verdict {'pass' if results.win_rate >= 0.65 and results.profit_factor >= 1.8 else 'marginal' if results.profit_factor >= 1.2 else 'fail'}">
    {'✅ STRATEGY VIABLE — Meets target metrics (WR ≥65%, PF ≥1.8)' if results.win_rate >= 0.65 and results.profit_factor >= 1.8
     else '⚠️ MARGINAL — Some edge detected but below targets'  if results.profit_factor >= 1.2
     else '❌ NO EDGE — Strategy does not meet minimum viability criteria'}
</div>

<!-- Equity Curve Chart -->
<div class="chart-container">
    <h2>Equity Curve</h2>
    <canvas id="equityChart"></canvas>
</div>

<!-- Drawdown Chart -->
<div class="chart-container">
    <h2>Drawdown</h2>
    <canvas id="drawdownChart"></canvas>
</div>

<!-- By Setup -->
<div class="chart-container">
    <h2>Performance by Setup</h2>
    <table>
        <thead><tr><th>Setup</th><th>Trades</th><th>Win Rate</th><th>P&L</th><th>Avg Win</th><th>Avg Loss</th></tr></thead>
        <tbody>
        {''.join(f"<tr><td>{s['setup']}</td><td>{s['trades']}</td><td>{s['win_rate']:.0%}</td><td>${s['pnl']:,.0f}</td><td>${s['avg_win']:,.0f}</td><td>${s['avg_loss']:,.0f}</td></tr>" for s in by_setup)}
        </tbody>
    </table>
</div>

<!-- By Hour -->
<div class="chart-container">
    <h2>Performance by Hour (ET)</h2>
    <canvas id="hourChart"></canvas>
</div>

<!-- By Weekday -->
<div class="chart-container">
    <h2>Performance by Weekday</h2>
    <table>
        <thead><tr><th>Day</th><th>Trades</th><th>Win Rate</th><th>P&L</th></tr></thead>
        <tbody>
        {''.join(f"<tr><td>{d['day']}</td><td>{d['trades']}</td><td>{d['win_rate']:.0%}</td><td>${d['pnl']:,.0f}</td></tr>" for d in by_weekday)}
        </tbody>
    </table>
</div>

<!-- By Exit Reason -->
<div class="chart-container">
    <h2>Exit Reasons</h2>
    <table>
        <thead><tr><th>Reason</th><th>Count</th><th>Avg P&L</th></tr></thead>
        <tbody>
        {''.join(f"<tr><td>{e['reason']}</td><td>{e['count']}</td><td>${e['avg_pnl']:,.0f}</td></tr>" for e in by_exit)}
        </tbody>
    </table>
</div>

<script>
const equityData = {json.dumps(equity_data)};
const drawdownData = {json.dumps(drawdown_data)};
const hourData = {json.dumps(by_hour)};

// Equity Curve
new Chart(document.getElementById('equityChart'), {{
    type: 'line',
    data: {{
        labels: equityData.dates,
        datasets: [{{
            data: equityData.equity,
            borderColor: '#22c55e',
            backgroundColor: 'rgba(34,197,94,0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ ticks: {{ color: '#888', maxTicksLimit: 10 }}, grid: {{ color: '#222' }} }},
            y: {{ ticks: {{ color: '#888', callback: v => '$' + v.toLocaleString() }}, grid: {{ color: '#222' }} }}
        }}
    }}
}});

// Drawdown
new Chart(document.getElementById('drawdownChart'), {{
    type: 'line',
    data: {{
        labels: drawdownData.dates,
        datasets: [{{
            data: drawdownData.drawdown,
            borderColor: '#ef4444',
            backgroundColor: 'rgba(239,68,68,0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ ticks: {{ color: '#888', maxTicksLimit: 10 }}, grid: {{ color: '#222' }} }},
            y: {{ ticks: {{ color: '#888', callback: v => '$' + v.toLocaleString() }}, grid: {{ color: '#222' }} }}
        }}
    }}
}});

// By Hour
new Chart(document.getElementById('hourChart'), {{
    type: 'bar',
    data: {{
        labels: hourData.map(h => h.hour + ':00'),
        datasets: [{{
            label: 'P&L',
            data: hourData.map(h => h.pnl),
            backgroundColor: hourData.map(h => h.pnl >= 0 ? '#22c55e' : '#ef4444'),
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }},
            y: {{ ticks: {{ color: '#888', callback: v => '$' + v.toLocaleString() }}, grid: {{ color: '#222' }} }}
        }}
    }}
}});
</script>
</body>
</html>"""

    # Write output
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)

    logger.info(f"Report saved to {output}")
    return str(output)


# --- Data extraction helpers ---

def _equity_curve_data(results) -> dict:
    ec = results.equity_curve
    return {"dates": list(ec.index), "equity": [round(v, 2) for v in ec.values]}


def _drawdown_data(results) -> dict:
    ec = results.equity_curve
    if ec.empty:
        return {"dates": [], "drawdown": []}
    peak = ec.expanding().max()
    dd = ec - peak
    return {"dates": list(dd.index), "drawdown": [round(v, 2) for v in dd.values]}


def _trades_by_setup(df: pd.DataFrame) -> list[dict]:
    out = []
    for setup, group in df.groupby("setup"):
        wins = group[group["pnl_dollars"] > 0]
        losses = group[group["pnl_dollars"] <= 0]
        out.append({
            "setup": setup,
            "trades": len(group),
            "win_rate": len(wins) / len(group) if len(group) > 0 else 0,
            "pnl": group["pnl_dollars"].sum(),
            "avg_win": wins["pnl_dollars"].mean() if len(wins) > 0 else 0,
            "avg_loss": losses["pnl_dollars"].mean() if len(losses) > 0 else 0,
        })
    return out


def _trades_by_hour(df: pd.DataFrame) -> list[dict]:
    if "entry_time" not in df.columns or df["entry_time"].isna().all():
        return []
    df = df.copy()
    df["hour"] = pd.to_datetime(df["entry_time"]).dt.hour
    out = []
    for hour, group in df.groupby("hour"):
        out.append({
            "hour": int(hour),
            "trades": len(group),
            "pnl": round(group["pnl_dollars"].sum(), 2),
            "win_rate": len(group[group["pnl_dollars"] > 0]) / len(group) if len(group) > 0 else 0,
        })
    return sorted(out, key=lambda x: x["hour"])


def _trades_by_weekday(df: pd.DataFrame) -> list[dict]:
    df = df.copy()
    df["weekday"] = pd.to_datetime(df["date"]).dt.day_name()
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    out = []
    for day in day_order:
        group = df[df["weekday"] == day]
        if len(group) == 0:
            continue
        out.append({
            "day": day,
            "trades": len(group),
            "win_rate": len(group[group["pnl_dollars"] > 0]) / len(group),
            "pnl": round(group["pnl_dollars"].sum(), 2),
        })
    return out


def _trades_by_exit_reason(df: pd.DataFrame) -> list[dict]:
    out = []
    for reason, group in df.groupby("exit_reason"):
        out.append({
            "reason": reason,
            "count": len(group),
            "avg_pnl": round(group["pnl_dollars"].mean(), 2),
        })
    return sorted(out, key=lambda x: x["count"], reverse=True)


def _win_loss_streaks(trades) -> dict:
    max_win = 0
    max_loss = 0
    current_win = 0
    current_loss = 0
    for t in trades:
        if t.pnl_dollars > 0:
            current_win += 1
            current_loss = 0
            max_win = max(max_win, current_win)
        else:
            current_loss += 1
            current_win = 0
            max_loss = max(max_loss, current_loss)
    return {"max_win_streak": max_win, "max_loss_streak": max_loss}
