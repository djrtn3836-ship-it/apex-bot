"""Apex Bot -  v2 (M5)
FastAPI + Plotly"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import pathlib
from loguru import logger

try:
    import plotly.graph_objects as go
    import plotly.utils
    import json as _json
    PLOTLY_OK = True
except ImportError:
    PLOTLY_OK = False
    logger.warning("plotly  — pip install plotly")


def create_app(engine_ref=None) -> FastAPI:
    app = FastAPI(title="Apex Bot Dashboard v2", version="3.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return _render_dashboard(engine_ref)

    @app.get("/api/status")
    async def api_status():
        if engine_ref is None:
            return {"status": "disconnected"}
        try:
            portfolio = engine_ref.portfolio
            return {
                "status":       "running",
                "positions":    portfolio.position_count,
                "total_assets": portfolio.total_value(),
                "cash":         portfolio.available_cash,
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    @app.get("/api/positions")
    async def api_positions():
        if engine_ref is None:
            return []
        try:
            pos = engine_ref.portfolio.open_positions
            return [
                {
                    "market":      m,
                    "entry_price": p.entry_price,
                    "amount_krw":  p.amount_krw,
                    "stop_loss":   p.stop_loss,
                    "take_profit": p.take_profit,
                    "strategy":    p.strategy,
                }
                for m, p in pos.items()
            ]
        except Exception as e:
            return []

    @app.get("/api/trades")
    async def api_trades():
        if engine_ref is None:
            return []
        try:
            trades = await engine_ref.db_manager.get_trades(limit=50)
            return trades
        except Exception:
            return []

    logger.info(" Dashboard v2   ")
    return app


def _render_dashboard(engine_ref) -> str:
    return """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Apex Bot Dashboard v2</title>
<meta http-equiv="refresh" content="30">
<style>
  body { font-family: 'Segoe UI', sans-serif; background:#0d1117; color:#e6edf3; margin:0; padding:20px; }
  h1   { color:#58a6ff; border-bottom:1px solid #30363d; padding-bottom:10px; }
  .card{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin:10px 0; }
  .grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; }
  .stat{ text-align:center; }
  .stat .val{ font-size:2em; font-weight:bold; color:#58a6ff; }
  .stat .lbl{ color:#8b949e; font-size:0.85em; }
  table{ width:100%; border-collapse:collapse; }
  th   { background:#21262d; padding:8px; text-align:left; color:#8b949e; }
  td   { padding:8px; border-bottom:1px solid #21262d; }
  .up  { color:#3fb950; } .dn { color:#f85149; }
</style>
</head>
<body>
<h1> Apex Bot Dashboard v2</h1>
<div class="card">
  <div class="grid">
    <div class="stat"><div class="val" id="pos">-</div><div class="lbl"></div></div>
    <div class="stat"><div class="val" id="asset">-</div><div class="lbl"> </div></div>
    <div class="stat"><div class="val" id="cash">-</div><div class="lbl"></div></div>
    <div class="stat"><div class="val" id="status">-</div><div class="lbl"></div></div>
  </div>
</div>
<div class="card">
  <h3>  </h3>
  <table><thead><tr>
    <th></th><th></th><th></th><th></th><th></th><th></th>
  </tr></thead><tbody id="pos-table"></tbody></table>
</div>
<div class="card">
  <h3>  </h3>
  <table><thead><tr>
    <th></th><th></th><th></th><th></th><th></th><th></th>
  </tr></thead><tbody id="trade-table"></tbody></table>
</div>
<script>
async function update() {
  try {
    const s = await fetch('/api/status').then(r=>r.json());
    document.getElementById('pos').textContent    = s.positions ?? '-';
    document.getElementById('asset').textContent  = s.total_assets ? '₩'+Math.round(s.total_assets).toLocaleString() : '-';
    document.getElementById('cash').textContent   = s.cash ? '₩'+Math.round(s.cash).toLocaleString() : '-';
    document.getElementById('status').textContent = s.status ?? '-';
  } catch(e) {}

  try {
    const positions = await fetch('/api/positions').then(r=>r.json());
    const tbody = document.getElementById('pos-table');
    tbody.innerHTML = positions.map(p => `
      <tr>
        <td>${p.market.replace('KRW-','')}</td>
        <td>${Math.round(p.entry_price).toLocaleString()}</td>
        <td>₩${Math.round(p.amount_krw).toLocaleString()}</td>
        <td class="dn">${Math.round(p.stop_loss).toLocaleString()}</td>
        <td class="up">${Math.round(p.take_profit).toLocaleString()}</td>
        <td>${p.strategy}</td>
      </tr>`).join('');
  } catch(e) {}

  try {
    const trades = await fetch('/api/trades').then(r=>r.json());
    const tbody  = document.getElementById('trade-table');
    tbody.innerHTML = trades.slice(0,20).map(t => {
      const pnl   = t.profit_rate ? (t.profit_rate*100).toFixed(2) : '0.00';
      const cls   = parseFloat(pnl) >= 0 ? 'up' : 'dn';
      const ts    = String(t.timestamp||'').slice(0,16);
      return `<tr>
        <td>${ts}</td>
        <td>${String(t.market||'').replace('KRW-','')}</td>
        <td class="${t.side==='BUY'?'up':'dn'}">${t.side||''}</td>
        <td>₩${Math.round(t.amount_krw||0).toLocaleString()}</td>
        <td class="${cls}">${pnl}%</td>
        <td>${t.strategy||''}</td>
      </tr>`;
    }).join('');
  } catch(e) {}
}
update();
setInterval(update, 10000);
</script>
</body></html>"""
