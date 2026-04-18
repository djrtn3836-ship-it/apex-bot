"""APEX BOT Backtester -   
JSON + HTML(Plotly) +"""
import json
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
import numpy as np
import pandas as pd
from loguru import logger

from backtesting.backtester import BacktestResult


class PerformanceReporter:
    """PerformanceReporter 클래스"""

    def __init__(self, output_dir: str = "./reports/backtest"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, result: BacktestResult, strategy_name: str = "") -> Dict:
        """(JSON + HTML)"""
        name   = strategy_name or result.strategy
        report = self._build_report(result, name)
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")

        json_path = self.output_dir / f"report_{result.market}_{name}_{ts}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f" JSON  : {json_path}")

        html_path = self.output_dir / f"report_{result.market}_{name}_{ts}.html"
        self._generate_html(result, report, html_path, name)

        result.print_summary()
        return report

    def generate_comparison(
        self,
        results: List[BacktestResult],
        strategy_names: List[str] = None,
    ) -> Dict:
        """구현부"""
        comparison = {
            "generated_at": datetime.now().isoformat(),
            "strategies": [],
        }
        for i, r in enumerate(results):
            name = strategy_names[i] if strategy_names else r.strategy
            comparison["strategies"].append(self._build_report(r, name))

        # 비교 랭킹 출력
        print("\n" + "="*65)
        print("     ")
        print("="*65)
        header = f"  {'전략':<25} {'수익률':>8} {'샤프':>7} {'승률':>7} {'낙폭':>8} {'거래수':>6}"
        print(header)
        print("-"*65)

        ranked = sorted(
            comparison["strategies"],
            key=lambda x: x.get("sharpe_ratio", 0),
            reverse=True,
        )
        for s in ranked:
            print(
                f"  {s['strategy']:<25} "
                f"{s.get('total_return', 0):>7.1f}% "
                f"{s.get('sharpe_ratio', 0):>7.3f} "
                f"{s.get('win_rate', 0):>6.1f}% "
                f"{s.get('max_drawdown', 0):>7.1f}% "
                f"{s.get('total_trades', 0):>6}"
            )
        print("="*65)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.output_dir / f"comparison_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(comparison, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"   : {path}")

        return comparison

    def _build_report(self, result: BacktestResult, name: str) -> Dict:
        return {
            "strategy":      name,
            "market":        result.market,
            "start_date":    result.start_date,
            "end_date":      result.end_date,
            "initial_capital": result.initial_capital,
            "final_capital":   result.final_capital,
            "total_return":  round(result.total_return, 4),
            "annual_return": round(result.annual_return, 4),
            "sharpe_ratio":  round(result.sharpe_ratio, 4),
            "sortino_ratio": round(result.sortino_ratio, 4),
            "max_drawdown":  round(result.max_drawdown, 4),
            "calmar_ratio":  round(result.calmar_ratio, 4),
            "win_rate":      round(result.win_rate, 2),
            "profit_factor": round(result.profit_factor, 4),
            "avg_win":       round(result.avg_win, 4),
            "avg_loss":      round(result.avg_loss, 4),
            "expectancy":    round(result.expectancy, 4),
            "total_trades":  result.total_trades,
            "total_fees":    round(result.total_fees, 2),
            "trades": [
                {
                    "entry_time":  str(t.entry_time),
                    "exit_time":   str(t.exit_time),
                    "entry_price": t.entry_price,
                    "exit_price":  t.exit_price,
                    "net_return":  round(t.net_return * 100, 4),
                    "profit_krw":  round(t.profit_krw, 2),
                    "exit_reason": t.reason_exit,
                    "duration_h":  round(t.duration_hours, 1),
                }
                for t in result.trades
            ],
        }

    def _generate_html(
        self,
        result: BacktestResult,
        report: Dict,
        path: Path,
        name: str,
    ):
        """Plotly    HTML"""
        equity_labels = [str(ts.date()) for ts in result.equity_curve.index[::max(1, len(result.equity_curve)//100)]]
        equity_values = [round(v, 2) for v in result.equity_curve.values[::max(1, len(result.equity_curve)//100)]]

        trades_rows = ""
        for t in result.trades[-50:]:
            color  = "#2ecc71" if t.net_return > 0 else "#e74c3c"
            pct    = f"{t.net_return*100:+.2f}%"
            trades_rows += f"""
            <tr>
              <td>{str(t.entry_time)[:16]}</td>
              <td>{str(t.exit_time)[:16]}</td>
              <td>₩{t.entry_price:,.0f}</td>
              <td>₩{t.exit_price:,.0f}</td>
              <td style="color:{color};font-weight:bold">{pct}</td>
              <td>₩{t.profit_krw:+,.0f}</td>
              <td>{t.reason_exit}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>APEX BOT : {name} / {result.market}</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;margin:0;padding:20px}}
    h1{{color:#f0b429;text-align:center}}
    .kpi{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}
    .card{{background:#1e2230;border-radius:8px;padding:16px;text-align:center}}
    .card .val{{font-size:1.6em;font-weight:bold;margin:6px 0}}
    .card .lbl{{font-size:.75em;color:#8892a4}}
    .pos{{color:#2ecc71}} .neg{{color:#e74c3c}} .neu{{color:#f0b429}}
    table{{width:100%;border-collapse:collapse;margin-top:20px;font-size:.85em}}
    th{{background:#1e2230;padding:8px;text-align:left;color:#8892a4}}
    td{{padding:7px 8px;border-bottom:1px solid #2a2e3e}}
    tr:hover{{background:#1e2230}}
    canvas{{background:#1e2230;border-radius:8px;margin-top:20px;width:100%;height:250px}}
  </style>
</head>
<body>
<h1> APEX BOT  </h1>
<h2 style="text-align:center;color:#8892a4">{name} &nbsp;|&nbsp; {result.market} &nbsp;|&nbsp; {result.start_date} ~ {result.end_date}</h2>
<div class="kpi">
  <div class="card"><div class="lbl"> </div>
    <div class="val {'pos' if report['total_return']>=0 else 'neg'}">{report['total_return']:+.2f}%</div></div>
  <div class="card"><div class="lbl"> </div>
    <div class="val neu">{report['sharpe_ratio']:.3f}</div></div>
  <div class="card"><div class="lbl"> </div>
    <div class="val neg">-{report['max_drawdown']:.2f}%</div></div>
  <div class="card"><div class="lbl"></div>
    <div class="val {'pos' if report['win_rate']>=50 else 'neg'}">{report['win_rate']:.1f}%</div></div>
  <div class="card"><div class="lbl"> </div>
    <div class="val {'pos' if report['annual_return']>=0 else 'neg'}">{report['annual_return']:+.2f}%</div></div>
  <div class="card"><div class="lbl"></div>
    <div class="val neu">{report['profit_factor']:.2f}</div></div>
  <div class="card"><div class="lbl"> </div>
    <div class="val neu">{report['total_trades']}</div></div>
  <div class="card"><div class="lbl"> </div>
    <div class="val neg">-₩{report['total_fees']:,.0f}</div></div>
</div>

<canvas id="equity"></canvas>
<script>
const ctx = document.getElementById('equity').getContext('2d');
const labels = {equity_labels};
const values = {equity_values};
const max = Math.max(...values), min = Math.min(...values);
const W = ctx.canvas.offsetWidth, H = 240;
ctx.canvas.width = W; ctx.canvas.height = H;
ctx.fillStyle = '#1e2230'; ctx.fillRect(0,0,W,H);
ctx.beginPath(); ctx.strokeStyle = '#f0b429'; ctx.lineWidth = 1.5;
values.forEach((v,i) => {{
  const x = (i/(values.length-1))*W;
  const y = H - ((v-min)/(max-min||1))*(H-20)-10;
  i===0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
}});
ctx.stroke();
ctx.fillStyle='rgba(240,180,41,0.08)';
ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath(); ctx.fill();
ctx.fillStyle='#8892a4'; ctx.font='11px sans-serif';
ctx.fillText('₩'+min.toLocaleString(),4,H-4);
ctx.fillText('₩'+max.toLocaleString(),4,14);
</script>

<h3>   ( 50)</h3>
<table>
  <thead><tr><th></th><th></th><th></th><th></th><th></th><th></th><th></th></tr></thead>
  <tbody>{trades_rows}</tbody>
</table>
</body></html>"""

        path.write_text(html, encoding="utf-8")
        logger.info(f" HTML  : {path}")
