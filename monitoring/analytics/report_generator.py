"""Apex Bot - /    (M7-B)"""
import json
import pathlib
from datetime import datetime, timedelta
from typing import Dict, Any
from loguru import logger


class ReportGenerator:
    """/"""

    def __init__(self, report_dir: str = "reports"):
        self.report_dir = pathlib.Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f" ReportGenerator  | ={self.report_dir}")

    def generate_weekly(self, stats: Dict[str, Any]) -> pathlib.Path:
        return self._generate(stats, "weekly")

    def generate_monthly(self, stats: Dict[str, Any]) -> pathlib.Path:
        return self._generate(stats, "monthly")

    def _generate(self, stats: Dict, period: str) -> pathlib.Path:
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.report_dir / f"apex_{period}_report_{ts}.html"

        html = self._render_html(stats, period)
        filename.write_text(html, encoding="utf-8")

        # JSON 저장
        json_path = filename.with_suffix(".json")
        json_path.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )

        logger.info(f" {period}  : {filename}")
        return filename

    def _render_html(self, stats: Dict, period: str) -> str:
        now        = datetime.now().strftime("%Y-%m-%d %H:%M")
        period_kr  = "주간" if period == "weekly" else "월간"
        ret        = stats.get("total_return", 0)
        ret_color  = "#3fb950" if ret >= 0 else "#f85149"
        ret_str    = f"{ret*100:+.2f}%"

        strategy_rows = ""
        for s in stats.get("strategies", []):
            grade_color = {
                "S": "#ffd700", "A": "#3fb950",
                "B": "#58a6ff", "C": "#e3b341", "F": "#f85149"
            }.get(s.get("grade", "F"), "#8b949e")
            strategy_rows += f"""
            <tr>
              <td style="color:{grade_color};font-weight:bold">[{s.get('grade','?')}] {s.get('strategy','')}</td>
              <td>{s.get('total_trades',0)}</td>
              <td>{s.get('win_rate',0)*100:.1f}%</td>
              <td style="color:{'#3fb950' if s.get('expectancy',0)>=0 else '#f85149'}">
                {s.get('expectancy',0):+.4f}
              </td>
              <td>{s.get('sharpe_ratio',0):.2f}</td>
            </tr>"""

        return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<title>Apex Bot {period_kr} </title>
<style>
  body{{font-family:'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:30px}}
  h1{{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:10px}}
  h2{{color:#8b949e;font-size:1em;margin-top:20px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin:15px 0}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
  .stat{{text-align:center;padding:10px}}
  .stat .val{{font-size:2em;font-weight:bold}}
  .stat .lbl{{color:#8b949e;font-size:0.85em;margin-top:4px}}
  table{{width:100%;border-collapse:collapse;margin-top:10px}}
  th{{background:#21262d;padding:10px;text-align:left;color:#8b949e;font-size:0.9em}}
  td{{padding:10px;border-bottom:1px solid #21262d;font-size:0.9em}}
  .footer{{color:#8b949e;font-size:0.8em;text-align:center;margin-top:30px}}
</style>
</head><body>
<h1> Apex Bot {period_kr}  </h1>
<p style="color:#8b949e">: {now}</p>

<div class="card">
  <h2>  </h2>
  <div class="grid">
    <div class="stat">
      <div class="val" style="color:{ret_color}">{ret_str}</div>
      <div class="lbl"> </div>
    </div>
    <div class="stat">
      <div class="val">{stats.get('total_trades',0)}</div>
      <div class="lbl"> </div>
    </div>
    <div class="stat">
      <div class="val">{stats.get('win_rate',0)*100:.1f}%</div>
      <div class="lbl"></div>
    </div>
    <div class="stat">
      <div class="val">{stats.get('sharpe_ratio',0):.2f}</div>
      <div class="lbl"></div>
    </div>
    <div class="stat">
      <div class="val" style="color:#f85149">{stats.get('max_drawdown',0)*100:.1f}%</div>
      <div class="lbl"></div>
    </div>
    <div class="stat">
      <div class="val">₩{stats.get('total_fee',0):,.0f}</div>
      <div class="lbl"> </div>
    </div>
  </div>
</div>

<div class="card">
  <h2>  </h2>
  <table>
    <thead><tr>
      <th></th><th></th><th></th><th></th><th></th>
    </tr></thead>
    <tbody>{strategy_rows}</tbody>
  </table>
</div>

<div class="footer">
  Apex Bot v3.0.0 —   
</div>
</body></html>"""
