"""APEX BOT - FastAPI  
WebSocket    + REST API"""
import asyncio
import json
from typing import Dict, List, Optional, Set
from datetime import datetime
from contextlib import asynccontextmanager
from loguru import logger

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning("FastAPI  -  ")

from config.settings import get_settings

import socket as _socket

def _find_free_port(start: int = 8888, retries: int = 10) -> int:
    """docstring"""
    for port in range(start, start + retries):
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start  # fallback



# ── WebSocket 연결 관리 ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, data: dict):
        msg = json.dumps(data, ensure_ascii=False, default=str)
        disconnected = set()
        # FIX: 복사본으로 순회 → "Set changed size during iteration" 방지
        for ws in set(self.active_connections):
            try:
                await ws.send_text(msg)
            except Exception:
                disconnected.add(ws)
        self.active_connections -= disconnected


manager = ConnectionManager()


# ── 대시보드 상태 저장소 ────────────────────────────────────────────
class DashboardState:
    def __init__(self):
        self.bot_status = "STOPPED"
        self.mode = "paper"
        self.positions: Dict = {}
        self.portfolio: Dict = {"total_krw": 0, "positions": [], "pnl_today": 0}
        self.recent_trades: List = []
        self.signals: Dict = {}
        self.metrics: Dict = {
            "total_trades": 0, "win_rate": 0.0,
            "fear_greed_index": None, "fear_greed_label": None,
            "kimchi_premium": None, "news_sentiment": None,
            "market_regime": None,
            "daily_pnl": 0.0, "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
        }
        self.alerts: List = []

    def to_dict(self) -> dict:
        return {
            "bot_status": self.bot_status,
            "mode": self.mode,
            "positions": self.positions,
            "portfolio": self.portfolio,
            "recent_trades": self.recent_trades[-20:],
            "signals": self.signals,
            "metrics": self.metrics,
            "alerts": self.alerts[-10:],
            "timestamp": datetime.now().isoformat(),
        }


dashboard_state = DashboardState()


# ── FastAPI 앱 ──────────────────────────────────────────────────────
def create_dashboard_app(engine_ref=None) -> "FastAPI":
    """FastAPI"""
    if not FASTAPI_AVAILABLE:
        return None

    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("   ")
        # 주기적 상태 브로드캐스트 (2초마다)
        task = asyncio.create_task(_broadcast_loop())
        yield
        task.cancel()
        logger.info("   ")

    app = FastAPI(
        title="APEX BOT Dashboard",
        description="Upbit AI Quant Trading Bot",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── REST API ────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTML_DASHBOARD

    @app.get("/api/status")
    async def get_status():
        state = dashboard_state.to_dict()
        # recent_trades가 비어있으면 DB에서 보강
        if not state.get("recent_trades"):
            try:
                import sqlite3, os
                db_path = os.path.join("database", "apex_bot.db")
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT timestamp, market, side, price, volume,
                               amount_krw, fee, profit_rate, strategy, reason
                        FROM trade_history
                        ORDER BY timestamp DESC LIMIT 20
                    """)
                    rows = [dict(r) for r in cur.fetchall()]
                    conn.close()
                    state["recent_trades"] = rows
            except Exception:
                pass
        return state

    @app.get("/api/portfolio")
    async def get_portfolio():
        return dashboard_state.portfolio

    @app.get("/api/trades")
    async def get_trades(limit: int = 50):
        # dashboard_state 먼저 확인, 없으면 DB에서 직접 읽기
        if dashboard_state.recent_trades:
            return dashboard_state.recent_trades[-limit:]
        try:
            import sqlite3, os
            db_path = os.path.join("database", "apex_bot.db")
            if not os.path.exists(db_path):
                return []
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT timestamp, market, side, price, volume, amount_krw,
                       fee, profit_rate, strategy, reason
                FROM trade_history
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            # dashboard_state에도 캐시
            dashboard_state.recent_trades = list(reversed(rows))
            return rows
        except Exception as e:
            logger.debug(f"trades DB  : {e}")
            return []

    @app.get("/api/metrics")
    async def get_metrics():
        return dashboard_state.metrics

    @app.get("/api/signals")
    async def get_signals():
        return dashboard_state.signals

    @app.get("/api/ml-predict")
    async def get_ml_predictions():
        """ML      (     )"""
        return {
            "ml_predictions": dashboard_state.signals.get("ml_predictions", {}),
            "last_updated": dashboard_state.signals.get("ml_last_updated", None),
            "model_loaded": dashboard_state.signals.get("ml_model_loaded", False),
        }

    @app.post("/api/control/{action}")
    async def control_bot(action: str):
        """: pause/resume/stop"""
        valid_actions = ["pause", "resume", "stop", "start"]
        if action not in valid_actions:
            raise HTTPException(400, f"유효하지 않은 액션: {action}")
        if engine_ref:
            if action == "pause":
                engine_ref.pause()
            elif action == "resume":
                engine_ref.resume()
        dashboard_state.bot_status = action.upper()
        await manager.broadcast({"type": "control", "action": action})
        return {"status": "ok", "action": action}

    @app.get("/api/health")
    async def health():
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}

    @app.post("/api/report")
    async def generate_report(hours: int = 24):
        """docstring"""
        import asyncio
        from monitoring.paper_report import generate_paper_report
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: generate_paper_report(hours=hours, output_dir="reports/paper")
            )
            m = data.get("metrics", {})
            return {
                "status": "ok",
                "message": f"reports/paper/ 에 리포트가 저장되었습니다",
                "summary": {
                    "total_pnl_pct":   round(m.get("total_pnl_pct", 0), 2),
                    "win_rate":        round(float(m.get("win_rate", 0)), 1),  # 0~100 단위
                    "total_trades":    m.get("total_trades", 0),
                "fear_greed_index": self.signals.get("fear_greed",
                                   self.signals.get("fear_greed_index", None)),
                "fear_greed_label": self.signals.get("fear_greed_label", None),
                "kimchi_premium":   self.signals.get("kimchi_premium", None),
                "news_sentiment":   self.signals.get("news_sentiment", None),
                "market_regime":    self.signals.get("market_regime", None),
                    "sharpe_ratio":    round(m.get("sharpe_ratio", 0), 3),
                    "max_drawdown_pct": round(m.get("max_drawdown_pct", 0), 2),
                }
            }
        except Exception as e:
            raise HTTPException(500, f"리포트 생성 실패: {e}")

    # ── WebSocket ────────────────────────────────────────────────────
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            # 초기 상태 전송
            await websocket.send_text(json.dumps(dashboard_state.to_dict(), default=str))
            while True:
                # 클라이언트 메시지 수신 (ping/pong)
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    return app


async def _broadcast_loop():
    """2"""
    while True:
        try:
            await asyncio.sleep(2)
            if manager.active_connections:
                await manager.broadcast(dashboard_state.to_dict())
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f" : {e}")


# ── 상태 업데이트 헬퍼 ──────────────────────────────────────────────
async def update_dashboard(data: dict):
    """docstring"""
    update_type = data.get("type", "")

    if update_type == "trade":
        dashboard_state.recent_trades.append(data)
        if len(dashboard_state.recent_trades) > 100:
            dashboard_state.recent_trades = dashboard_state.recent_trades[-100:]

    elif update_type == "portfolio":
        dashboard_state.portfolio.update(data)

    elif update_type == "signal":
        market = data.get("market", "")
        if market == "__global__":
            # 전역 시그널은 signals 최상위에 병합
            for k, v in data.items():
                if k not in ("type", "market"):
                    dashboard_state.signals[k] = v
        elif market:
            dashboard_state.signals[market] = data
    elif update_type == "metrics":
        dashboard_state.metrics.update(data)

    elif update_type == "alert":
        dashboard_state.alerts.append(data)

    elif update_type == "status":
        dashboard_state.bot_status = data.get("status", "UNKNOWN")

    # 브로드캐스트
    await manager.broadcast({"type": update_type, **data})


# ── 대시보드 HTML ────────────────────────────────────────────────────
HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Apex Bot Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  :root{--bg:#0a0e1a;--card:#111827;--card2:#1a2235;--border:#1e2d45;--accent:#3b82f6;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;--text:#e2e8f0;--muted:#64748b;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;}
  .header{display:flex;align-items:center;justify-content:space-between;padding:16px 28px;background:linear-gradient(135deg,#0f172a,#1e1b4b);border-bottom:1px solid var(--border);}
  .logo{font-size:20px;font-weight:700;}.logo span{color:var(--accent);}
  .badge{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;background:rgba(16,185,129,0.15);color:var(--green);border:1px solid rgba(16,185,129,0.3);animation:pulse 2s infinite;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.6}}
  .mode-tag{padding:3px 12px;border-radius:6px;font-size:11px;font-weight:700;background:rgba(245,158,11,0.15);color:var(--yellow);border:1px solid rgba(245,158,11,0.3);}
  .main{padding:20px 28px;display:flex;flex-direction:column;gap:16px;}
  .kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;}
  .kpi-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 20px;position:relative;overflow:hidden;transition:transform .2s;}
  .kpi-card:hover{transform:translateY(-2px);}
  .kpi-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;}
  .kpi-card.blue::before{background:linear-gradient(90deg,#3b82f6,#8b5cf6);}
  .kpi-card.green::before{background:linear-gradient(90deg,#10b981,#06b6d4);}
  .kpi-card.yellow::before{background:linear-gradient(90deg,#f59e0b,#ef4444);}
  .kpi-card.purple::before{background:linear-gradient(90deg,#8b5cf6,#ec4899);}
  .kpi-label{font-size:11px;color:var(--muted);font-weight:500;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px;}
  .kpi-value{font-size:24px;font-weight:700;line-height:1;}
  .kpi-sub{font-size:12px;color:var(--muted);margin-top:5px;}
  .kpi-chip{font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(255,255,255,0.05);margin-top:6px;display:inline-block;}
  .pos{color:var(--green);}.neg{color:var(--red);}.neu{color:var(--muted);}
  .grid-main{display:grid;grid-template-columns:1fr 340px;gap:16px;}
  .grid-mid{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
  .grid-bot{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;}
  .panel{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;}
  .panel-header{padding:12px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:var(--card2);}
  .panel-title{font-size:13px;font-weight:600;}
  .panel-sub{font-size:11px;color:var(--muted);}
  .panel-body{padding:14px 18px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px;font-size:11px;text-transform:uppercase;}
  td{padding:9px 8px;border-bottom:1px solid rgba(30,45,69,0.5);vertical-align:middle;}
  tr:last-child td{border-bottom:none;}
  tr:hover td{background:rgba(59,130,246,0.04);}
  .coin-tag{display:inline-flex;align-items:center;gap:6px;font-weight:600;}
  .dot{width:7px;height:7px;border-radius:50%;display:inline-block;}
  .tag{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;}
  .tag-buy{background:rgba(16,185,129,0.15);color:var(--green);}
  .tag-sell{background:rgba(239,68,68,0.15);color:var(--red);}
  .tag-hold{background:rgba(100,116,139,0.15);color:var(--muted);}
  .gauge-wrap{display:flex;flex-direction:column;align-items:center;padding:12px 8px 4px;}
  .fg-num{font-size:34px;font-weight:700;text-align:center;margin-top:4px;}
  .fg-label{font-size:12px;color:var(--muted);text-align:center;}
  .info-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;}
  .info-item{background:var(--card2);border-radius:8px;padding:9px 11px;}
  .info-label{font-size:11px;color:var(--muted);margin-bottom:3px;}
  .info-value{font-size:13px;font-weight:600;}
  .ml-item{display:flex;flex-direction:column;gap:4px;padding:8px 0;border-bottom:1px solid rgba(30,45,69,0.4);}
  .ml-item:last-child{border-bottom:none;}
  .ml-row{display:flex;align-items:center;justify-content:space-between;}
  .ml-bar-wrap{flex:1;height:5px;background:rgba(255,255,255,0.07);border-radius:3px;margin:0 8px;}
  .ml-bar{height:5px;border-radius:3px;background:linear-gradient(90deg,var(--accent),#8b5cf6);transition:width .5s;}
  .stat-row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid rgba(30,45,69,0.4);}
  .stat-row:last-child{border-bottom:none;}
  .stat-label{font-size:12px;color:var(--muted);}
  .stat-value{font-size:14px;font-weight:600;}
  .risk-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
  .risk-item{background:var(--card2);border-radius:8px;padding:10px;}
  .risk-label{font-size:11px;color:var(--muted);margin-bottom:3px;}
  .risk-value{font-size:15px;font-weight:600;}
  .strat-item{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid rgba(30,45,69,0.4);}
  .strat-item:last-child{border-bottom:none;}
  .strat-name{font-size:12px;font-weight:500;}
  .strat-stats{display:flex;gap:10px;font-size:12px;}
  .footer{text-align:center;padding:12px;font-size:11px;color:var(--muted);border-top:1px solid var(--border);}
  ::-webkit-scrollbar{width:4px;}::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
</style>
</head>
<body>
<div class="header">
  <div style="display:flex;align-items:center;gap:12px;">
    <div class="logo">Apex <span>Bot</span></div>
    <div class="badge" id="statusBadge">LIVE</div>
  </div>
  <div style="display:flex;align-items:center;gap:14px;">
    <div style="font-size:13px;color:var(--muted)" id="lastUpdate">--:--:--</div>
    <div class="mode-tag" id="modeTag">PAPER</div>
  </div>
</div>
<div class="main">
  <div class="kpi-grid">
    <div class="kpi-card blue">
      <div class="kpi-label">총 자산</div>
      <div class="kpi-value" id="totalAssets">0</div>
      <div class="kpi-sub">초기자본 1,000,000</div>
      <div class="kpi-chip" id="totalPnlChip">+0.00%</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">투자 중</div>
      <div class="kpi-value" id="invested">0</div>
      <div class="kpi-sub">투자비율 <span id="investRatio">0%</span></div>
      <div class="kpi-chip" id="posCountChip">0 포지션</div>
    </div>
    <div class="kpi-card yellow">
      <div class="kpi-label">현금 잔고</div>
      <div class="kpi-value" id="cashBalance">0</div>
      <div class="kpi-sub">현금비율 <span id="cashRatio">0%</span></div>
    </div>
    <div class="kpi-card purple">
      <div class="kpi-label">오늘 수익</div>
      <div class="kpi-value" id="todayPnl">+0</div>
      <div class="kpi-sub" id="todayPct">+0.00%</div>
    </div>
  </div>
  <div class="grid-main">
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">보유 포지션 <span id="posCountBadge" style="font-size:11px;color:var(--muted)">(0)</span></div>
        <div class="panel-sub" id="posUpdate"></div>
      </div>
      <div style="overflow-x:auto;">
        <table>
          <thead><tr><th>코인</th><th>전략</th><th>매수가</th><th>현재가</th><th>투자금</th><th>수익률</th><th>TP/SL</th></tr></thead>
          <tbody id="posTable"></tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><div class="panel-title">시장 온도</div></div>
      <div class="panel-body">
        <div class="gauge-wrap">
          <svg width="160" height="88" viewBox="0 0 160 88" style="overflow:visible">
            <path d="M20,78 A60,60 0 0,1 140,78" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="12" stroke-linecap="round"/>
            <path id="gaugeArc" d="M20,78 A60,60 0 0,1 140,78" fill="none" stroke="url(#gg)" stroke-width="12" stroke-linecap="round" stroke-dasharray="220" stroke-dashoffset="110"/>
            <defs><linearGradient id="gg" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#ef4444"/><stop offset="50%" stop-color="#f59e0b"/><stop offset="100%" stop-color="#10b981"/></linearGradient></defs>
            <line id="gaugeNeedle" x1="80" y1="76" x2="80" y2="28" stroke="white" stroke-width="2.5" stroke-linecap="round" transform="rotate(-90,80,76)"/>
            <circle cx="80" cy="76" r="4" fill="white"/>
          </svg>
          <div class="fg-num" id="fgNum">--</div>
          <div class="fg-label" id="fgLabel">--</div>
        </div>
        <div class="info-grid">
          <div class="info-item"><div class="info-label">김치 프리미엄</div><div class="info-value" id="kimchi">--</div></div>
          <div class="info-item"><div class="info-label">뉴스 감정</div><div class="info-value" id="newsSentiment">--</div></div>
          <div class="info-item" style="grid-column:span 2"><div class="info-label">시장 국면</div><div class="info-value" id="marketRegime">--</div></div>
        </div>
      </div>
    </div>
  </div>
  <div class="grid-mid">
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">ML 예측 신호</div>
        <div class="panel-sub" id="mlUpdate">--</div>
      </div>
      <div class="panel-body" id="mlPanel" style="max-height:280px;overflow-y:auto;">
        <div style="color:var(--muted);text-align:center;padding:20px">로딩 중...</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">최근 거래</div>
        <div class="panel-sub" id="tradeCount">--</div>
      </div>
      <div style="overflow-x:auto;max-height:280px;overflow-y:auto;">
        <table>
          <thead><tr><th>시각</th><th>코인</th><th>구분</th><th>금액</th><th>수익률</th></tr></thead>
          <tbody id="tradeTable"></tbody>
        </table>
      </div>
    </div>
  </div>
  <div class="grid-bot">
    <div class="panel">
      <div class="panel-header"><div class="panel-title">거래 통계</div></div>
      <div class="panel-body">
        <div class="stat-row"><span class="stat-label">총 거래</span><span class="stat-value" id="statTrades">--</span></div>
        <div class="stat-row"><span class="stat-label">승률</span><span class="stat-value" id="statWin">--</span></div>
        <div class="stat-row"><span class="stat-label">평균 PnL</span><span class="stat-value" id="statAvgPnl">--</span></div>
        <div class="stat-row"><span class="stat-label">수익 팩터</span><span class="stat-value" id="statPF">--</span></div>
        <div class="stat-row"><span class="stat-label">샤프 지수</span><span class="stat-value" id="statSharpe">--</span></div>
        <div class="stat-row"><span class="stat-label">최고 수익</span><span class="stat-value pos" id="statBest">--</span></div>
        <div class="stat-row"><span class="stat-label">최악 손실</span><span class="stat-value neg" id="statWorst">--</span></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><div class="panel-title">리스크 현황</div></div>
      <div class="panel-body">
        <div class="risk-grid">
          <div class="risk-item"><div class="risk-label">MDD</div><div class="risk-value" id="riskMDD">--</div></div>
          <div class="risk-item"><div class="risk-label">포지션</div><div class="risk-value" id="riskPos">0/10</div></div>
          <div class="risk-item"><div class="risk-label">일 수익률</div><div class="risk-value" id="riskDailyPnl">--</div></div>
          <div class="risk-item"><div class="risk-label">봇 상태</div><div class="risk-value pos" id="riskStatus">--</div></div>
        </div>
        <div class="stat-row" style="margin-top:10px"><span class="stat-label">역발상 횟수</span><span class="stat-value" id="riskBear">--</span></div>
        <div class="stat-row"><span class="stat-label">BTC 쇼크</span><span class="stat-value" id="riskBtcShock">--</span></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><div class="panel-title">전략별 성과</div></div>
      <div class="panel-body" id="stratPanel">
        <div style="color:var(--muted);text-align:center;padding:20px">데이터 없음</div>
      </div>
    </div>
  </div>
</div>
<div class="footer" id="footerInfo">Apex Bot v2.0.0</div>
<script>
function W(v,d){d=d||0;return '\u20a9'+(parseFloat(v)||0).toLocaleString('ko-KR',{maximumFractionDigits:d});}
function P(v,d){d=d||2;var n=parseFloat(v)||0;return(n>=0?'+':'')+n.toFixed(d)+'%';}
function C(v){return parseFloat(v)>0?'pos':parseFloat(v)<0?'neg':'neu';}
var CL=['#3b82f6','#10b981','#f59e0b','#8b5cf6','#ef4444','#06b6d4','#ec4899','#14b8a6'];
function D(s){var c=CL[s.charCodeAt(0)%CL.length];return '<span class="dot" style="background:'+c+'"></span>';}
setInterval(function(){document.getElementById('lastUpdate').textContent=new Date().toLocaleTimeString('ko-KR');},1000);
try{var ws=new WebSocket('ws://'+location.host+'/ws');ws.onmessage=function(e){try{U(JSON.parse(e.data));}catch(_){}};}catch(_){}
function U(d){
  var p=d.portfolio||{};var s=d.signals||{};var m=d.metrics||{};
  var mr={};
  if(s.ml_predictions&&!Array.isArray(s.ml_predictions))mr=s.ml_predictions;
  else if(d.ml_predictions&&!Array.isArray(d.ml_predictions))mr=d.ml_predictions;
  var ml=Object.entries(mr).map(function(e){return Object.assign({market:e[0]},e[1]);});
  var tr=Array.isArray(d.recent_trades)?d.recent_trades:(d.recent_trades&&d.recent_trades.value?d.recent_trades.value:[]);
  var tot=parseFloat(p.total_assets||p.total_krw||0);
  var csh=parseFloat(p.cash||p.krw_balance||0);
  var inv=parseFloat(p.invested||Math.max(tot-csh,0));
  var ini=1000000;var pp=tot>0?(tot-ini)/ini*100:0;
  var ir=tot>0?(inv/tot*100).toFixed(1):'0';var cr=tot>0?(csh/tot*100).toFixed(1):'0';
  var dp=parseFloat(p.pnl_today||p.pnl||m.daily_pnl||0);
  document.getElementById('totalAssets').textContent=W(tot);
  document.getElementById('invested').textContent=W(inv);
  document.getElementById('cashBalance').textContent=W(csh);
  document.getElementById('investRatio').textContent=ir+'%';
  document.getElementById('cashRatio').textContent=cr+'%';
  var ch=document.getElementById('totalPnlChip');ch.textContent=P(pp);ch.style.color=pp>=0?'var(--green)':'var(--red)';
  var pe=document.getElementById('todayPnl');var pte=document.getElementById('todayPct');
  pe.textContent=(dp>=0?'+':'')+W(dp*ini);pe.className='kpi-value '+(dp>=0?'pos':'neg');
  pte.textContent=P(dp*100);pte.className='kpi-sub '+(dp>=0?'pos':'neg');
  var st=(d.bot_status||'running').toLowerCase();
  var bg=document.getElementById('statusBadge');
  bg.textContent=st==='running'?'LIVE':st.toUpperCase();bg.style.color=st==='running'?'var(--green)':'var(--yellow)';
  document.getElementById('modeTag').textContent=(p.mode||'PAPER').toUpperCase();
  var pos=[];
  if(Array.isArray(p.positions_detail)&&p.positions_detail.length)pos=p.positions_detail;
  else if(p.positions&&typeof p.positions==='object'&&typeof p.positions!=='number'&&!Array.isArray(p.positions))
    pos=Object.entries(p.positions).map(function(e){var v=e[1];return{market:e[0],strategy:v.strategy||'-',entry_price:v.entry_price||0,current_price:v.current_price||v.entry_price||0,amount_krw:(v.entry_price||0)*(v.volume||0),profit_rate:(v.unrealized_pnl_pct||0)/100,take_profit:v.take_profit||null,stop_loss:v.stop_loss||null};});
  document.getElementById('posCountChip').textContent=pos.length+' \ud3ec\uc9c0\uc158';
  document.getElementById('posCountBadge').textContent='('+pos.length+')';
  document.getElementById('posUpdate').textContent=new Date().toLocaleTimeString('ko-KR');
  var tb=document.getElementById('posTable');
  if(!pos.length)tb.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:20px">\ud3ec\uc9c0\uc158 \uc5c6\uc74c</td></tr>';
  else tb.innerHTML=pos.map(function(x){var sym=(x.market||'').replace('KRW-','');var pr=parseFloat(x.profit_rate||0)*100;return'<tr><td><span class="coin-tag">'+D(sym)+'<b>'+sym+'</b></span></td><td style="font-size:11px;color:var(--muted)">'+(x.strategy||'-')+'</td><td>'+W(x.entry_price)+'</td><td>'+W(x.current_price||x.entry_price)+'</td><td>'+W(x.amount_krw)+'</td><td class="'+C(pr)+'"><b>'+P(pr)+'</b></td><td style="font-size:11px;color:var(--muted)">'+(x.take_profit?W(x.take_profit):'-')+' / '+(x.stop_loss?W(x.stop_loss):'-')+'</td></tr>';}).join('');
  var fg=parseInt(s.fear_greed||m.fear_greed_index||50);
  document.getElementById('fgNum').textContent=fg;
  var FL=['\uadf9\ub2e8\uacf5\ud3ec','\uacf5\ud3ec','\uc911\ub9bd','\ud0d0\uc695','\uadf9\ub2e8\ud0d0\uc695'];
  var FC=['var(--red)','#f97316','var(--muted)','var(--green)','#06b6d4'];
  var fi=fg<20?0:fg<40?1:fg<60?2:fg<80?3:4;
  document.getElementById('fgLabel').textContent=FL[fi];document.getElementById('fgNum').style.color=FC[fi];
  document.getElementById('gaugeArc').style.strokeDashoffset=220-(fg/100*220);
  document.getElementById('gaugeNeedle').setAttribute('transform','rotate('+((-90)+(fg/100*180))+',80,76)');
  var ki=s.kimchi_premium!=null?s.kimchi_premium:(m.kimchi_premium!=null?m.kimchi_premium:null);
  document.getElementById('kimchi').textContent=ki!=null?P(ki,2):'--';
  document.getElementById('newsSentiment').textContent=s.news_sentiment||m.news_sentiment||'--';
  var rg=s.market_regime||m.market_regime;
  if(!rg||rg==='--'){if(fg<=20)rg='BEAR (\uadf9\ub2e8\uacf5\ud3ec)';else if(fg<=35)rg='BEAR_WATCH';else if(fg<=55)rg='NEUTRAL';else if(fg<=75)rg='BULL_WATCH';else rg='BULL (\uacfc\uc5f4)';}
  document.getElementById('marketRegime').textContent=rg;
  var mp=document.getElementById('mlPanel');
  if(s.ml_last_updated)document.getElementById('mlUpdate').textContent=s.ml_last_updated.slice(11,19);
  if(ml.length){mp.innerHTML=ml.slice(0,10).map(function(r){var cf=parseFloat(r.confidence||0)*100;var tc=r.signal==='BUY'?'tag-buy':r.signal==='SELL'?'tag-sell':'tag-hold';var sy=(r.market||'').replace('KRW-','');var bp=parseFloat(r.buy_prob||0)*100;var sp=parseFloat(r.sell_prob||0)*100;return'<div class="ml-item"><div class="ml-row"><span class="coin-tag">'+D(sy)+' '+sy+'</span><span class="tag '+tc+'">'+(r.signal||'HOLD')+'</span></div><div class="ml-row"><span style="font-size:11px;color:var(--muted)">\uc2e0\ub8b0\ub3c4</span><div class="ml-bar-wrap"><div class="ml-bar" style="width:'+cf+'%"></div></div><span style="font-size:12px;color:var(--muted);width:36px;text-align:right">'+cf.toFixed(0)+'%</span></div><div class="ml-row" style="font-size:11px;color:var(--muted)"><span>\ub9e4\uc218 '+bp.toFixed(1)+'%</span><span>\ub9e4\ub3c4 '+sp.toFixed(1)+'%</span></div></div>';}).join('');}
  else mp.innerHTML='<div style="color:var(--muted);text-align:center;padding:20px">ML \uc2e0\ud638 \ub300\uae30 \uc911...</div>';
  document.getElementById('tradeCount').textContent='\uc624\ub298 '+tr.length+'\uac74';
  var tt=document.getElementById('tradeTable');
  if(!tr.length)tt.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">\uac70\ub798 \ub0b4\uc5ed \uc5c6\uc74c</td></tr>';
  else tt.innerHTML=tr.slice(0,12).map(function(x){var sy=(x.market||'').replace('KRW-','');var ti=(x.timestamp||'').slice(11,16);var tc=x.side==='BUY'?'tag-buy':'tag-sell';var pn=parseFloat(x.profit_rate||0);return'<tr><td style="color:var(--muted);font-size:12px">'+ti+'</td><td>'+D(sy)+'<b style="margin-left:4px">'+sy+'</b></td><td><span class="tag '+tc+'">'+x.side+'</span></td><td>'+W(x.amount_krw)+'</td><td class="'+C(pn)+'"><b>'+(x.side==='SELL'?P(pn):'-')+'</b></td></tr>';}).join('');
  document.getElementById('statTrades').textContent=m.total_trades||'--';
  document.getElementById('statWin').textContent=m.win_rate!=null?parseFloat(m.win_rate).toFixed(1)+'%':'--';
  document.getElementById('statPF').textContent=m.profit_factor!=null?parseFloat(m.profit_factor).toFixed(3):'--';
  document.getElementById('statSharpe').textContent=m.sharpe_ratio!=null?parseFloat(m.sharpe_ratio).toFixed(3):'--';
  document.getElementById('statBest').textContent=m.best_pnl!=null?P(m.best_pnl):'--';
  document.getElementById('statWorst').textContent=m.worst_pnl!=null?P(m.worst_pnl):'--';
  if(m.avg_pnl!=null){var ae=document.getElementById('statAvgPnl');ae.textContent=P(m.avg_pnl);ae.className='stat-value '+(parseFloat(m.avg_pnl)>=0?'pos':'neg');}
  var mdd=parseFloat(m.max_drawdown||0)*100;
  document.getElementById('riskMDD').textContent=P(mdd);document.getElementById('riskMDD').className='risk-value '+(mdd>5?'neg':'pos');
  document.getElementById('riskPos').textContent=pos.length+'/10';
  document.getElementById('riskBear').textContent=(s.bear_reversal_count||0)+'/3';
  document.getElementById('riskBtcShock').textContent=s.btc_shock_blocked?'\u26a0\ufe0f \ucc28\ub2e8':'\u2705 \uc815\uc0c1';
  var se=document.getElementById('riskStatus');se.textContent=st==='running'?'RUNNING':st.toUpperCase();se.className='risk-value '+(st==='running'?'pos':'neg');
  var dpv=parseFloat(m.daily_pnl||0)*100;var de=document.getElementById('riskDailyPnl');de.textContent=P(dpv);de.className='risk-value '+(dpv>=0?'pos':'neg');
  var sd=m.strategy_stats||[];var sp=document.getElementById('stratPanel');
  if(sd.length)sp.innerHTML=sd.slice(0,6).map(function(x){var wr=parseFloat(x.win_rate||0);var pf=parseFloat(x.profit_factor||0);return'<div class="strat-item"><span class="strat-name">'+(x.strategy||'?')+'</span><div class="strat-stats"><span>'+(x.trades||0)+'\uac74</span><span class="'+(wr>=50?'pos':'neg')+'">'+wr.toFixed(0)+'%</span><span class="'+(pf>=1?'pos':'neg')+'">PF '+pf.toFixed(2)+'</span></div></div>';}).join('');
  else sp.innerHTML='<div style="color:var(--muted);text-align:center;padding:20px">\ub370\uc774\ud130 \uc5c6\uc74c</div>';
  document.getElementById('footerInfo').textContent='Apex Bot v2.0.0 \u00b7 '+(p.mode||'PAPER')+' \u00b7 RTX 5060 \u00b7 '+new Date().toLocaleTimeString('ko-KR');
}
async function poll(){
  try{
    var r=await Promise.all([fetch('/api/status').then(function(r){return r.json();}),fetch('/api/portfolio').then(function(r){return r.json();}),fetch('/api/metrics').then(function(r){return r.json();}),fetch('/api/trades?limit=12').then(function(r){return r.json();}).then(function(d){return Array.isArray(d)?d:(d&&d.value?d.value:[]);})]);
    var mg=Object.assign({},r[0]);
    mg.portfolio=Object.assign({},r[0].portfolio||{},r[1]||{});
    mg.metrics=Object.assign({},r[0].metrics||{},r[2]||{});
    mg.recent_trades=r[3]||r[0].recent_trades||[];
    U(mg);
  }catch(e){console.warn(e);}
}
poll();setInterval(poll,8000);
</script>
</body>
</html>
"""


class DashboardServer:
    """docstring"""

    def __init__(self):
        self.settings = get_settings()
        self.app = None
        self._server_task = None

    def setup(self, engine_ref=None):
        self.app = create_dashboard_app(engine_ref)

    async def start(self):
        if not FASTAPI_AVAILABLE or not self.app:
            return

        config = uvicorn.Config(
            self.app,
            host=self.settings.monitoring.dashboard_host,
            port=_find_free_port(self.settings.monitoring.dashboard_port),
            log_level="warning",
        )
        server = uvicorn.Server(config)
        logger.info(
            f"  : http://{self.settings.monitoring.dashboard_host}"
            f":{self.settings.monitoring.dashboard_port}"
        )
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self):
        if self._server_task:
            self._server_task.cancel()