"""
APEX BOT - FastAPI 실시간 대시보드
WebSocket 기반 실시간 업데이트 + REST API
"""
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
    logger.warning("FastAPI 미설치 - 대시보드 비활성화")

from config.settings import get_settings

import socket as _socket

def _find_free_port(start: int = 8888, retries: int = 10) -> int:
    """사용 가능한 포트를 찾아 반환"""
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
    """대시보드 FastAPI 앱 생성"""
    if not FASTAPI_AVAILABLE:
        return None

    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("🚀 대시보드 서버 시작")
        # 주기적 상태 브로드캐스트 (2초마다)
        task = asyncio.create_task(_broadcast_loop())
        yield
        task.cancel()
        logger.info("🛑 대시보드 서버 종료")

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
            logger.debug(f"trades DB 조회 오류: {e}")
            return []

    @app.get("/api/metrics")
    async def get_metrics():
        return dashboard_state.metrics

    @app.get("/api/signals")
    async def get_signals():
        return dashboard_state.signals

    @app.get("/api/ml-predict")
    async def get_ml_predictions():
        """ML 앙상블 최신 예측 결과 반환 (엔진 실행 중일 때 실시간 갱신)"""
        return {
            "ml_predictions": dashboard_state.signals.get("ml_predictions", {}),
            "last_updated": dashboard_state.signals.get("ml_last_updated", None),
            "model_loaded": dashboard_state.signals.get("ml_model_loaded", False),
        }

    @app.post("/api/control/{action}")
    async def control_bot(action: str):
        """봇 제어: pause/resume/stop"""
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
        """페이퍼 트레이딩 리포트 즉시 생성"""
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
    """2초마다 상태 브로드캐스트"""
    while True:
        try:
            await asyncio.sleep(2)
            if manager.active_connections:
                await manager.broadcast(dashboard_state.to_dict())
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"브로드캐스트 오류: {e}")


# ── 상태 업데이트 헬퍼 ──────────────────────────────────────────────
async def update_dashboard(data: dict):
    """외부에서 대시보드 상태 업데이트"""
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
<title>⚡ Apex Bot Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  :root {
    --bg:       #0a0e1a;
    --card:     #111827;
    --card2:    #1a2235;
    --border:   #1e2d45;
    --accent:   #3b82f6;
    --green:    #10b981;
    --red:      #ef4444;
    --yellow:   #f59e0b;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --glow:     0 0 20px rgba(59,130,246,0.15);
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'Inter',sans-serif; background:var(--bg); color:var(--text); min-height:100vh; }

  /* ── Header ── */
  .header {
    display:flex; align-items:center; justify-content:space-between;
    padding:18px 28px;
    background:linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
    border-bottom:1px solid var(--border);
  }
  .header-left { display:flex; align-items:center; gap:12px; }
  .logo { font-size:22px; font-weight:700; letter-spacing:-0.5px; }
  .logo span { color:var(--accent); }
  .badge {
    padding:4px 10px; border-radius:20px; font-size:11px; font-weight:600;
    background:rgba(16,185,129,0.15); color:var(--green);
    border:1px solid rgba(16,185,129,0.3);
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
  .header-right { display:flex; align-items:center; gap:16px; }
  .clock { font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .mode-tag {
    padding:4px 12px; border-radius:6px; font-size:11px; font-weight:700;
    background:rgba(245,158,11,0.15); color:var(--yellow);
    border:1px solid rgba(245,158,11,0.3); letter-spacing:1px;
  }

  /* ── Main Layout ── */
  .main { padding:24px 28px; display:flex; flex-direction:column; gap:20px; }

  /* ── KPI Cards ── */
  .kpi-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }
  .kpi-card {
    background:var(--card); border:1px solid var(--border);
    border-radius:14px; padding:20px 22px;
    position:relative; overflow:hidden; transition:transform .2s;
  }
  .kpi-card:hover { transform:translateY(-2px); box-shadow:var(--glow); }
  .kpi-card::before {
    content:''; position:absolute; top:0; left:0; right:0; height:3px;
  }
  .kpi-card.blue::before  { background:linear-gradient(90deg,#3b82f6,#8b5cf6); }
  .kpi-card.green::before { background:linear-gradient(90deg,#10b981,#06b6d4); }
  .kpi-card.yellow::before{ background:linear-gradient(90deg,#f59e0b,#ef4444); }
  .kpi-card.purple::before{ background:linear-gradient(90deg,#8b5cf6,#ec4899); }
  .kpi-label { font-size:12px; color:var(--muted); font-weight:500; margin-bottom:8px; text-transform:uppercase; letter-spacing:.5px; }
  .kpi-value { font-size:26px; font-weight:700; line-height:1; }
  .kpi-sub { font-size:12px; color:var(--muted); margin-top:6px; }
  .pos { color:var(--green); }
  .neg { color:var(--red); }
  .neu { color:var(--muted); }

  /* ── Two-column ── */
  .two-col { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .three-col { display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; }

  /* ── Panel ── */
  .panel {
    background:var(--card); border:1px solid var(--border);
    border-radius:14px; overflow:hidden;
  }
  .panel-header {
    padding:14px 20px; border-bottom:1px solid var(--border);
    display:flex; align-items:center; justify-content:space-between;
    background:var(--card2);
  }
  .panel-title { font-size:13px; font-weight:600; display:flex; align-items:center; gap:8px; }
  .panel-body { padding:16px 20px; }

  /* ── Table ── */
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--muted); font-weight:500; padding:8px 10px; font-size:11px; text-transform:uppercase; letter-spacing:.5px; border-bottom:1px solid var(--border); }
  td { padding:10px 10px; border-bottom:1px solid rgba(30,45,69,0.5); vertical-align:middle; }
  tr:last-child td { border-bottom:none; }
  tr:hover td { background:rgba(59,130,246,0.04); }
  .coin-tag {
    display:inline-flex; align-items:center; gap:6px;
    font-weight:600; font-size:13px;
  }
  .dot { width:8px; height:8px; border-radius:50%; display:inline-block; }

  /* ── Gauge (Fear & Greed) ── */
  .gauge-wrap { display:flex; flex-direction:column; align-items:center; padding:10px 0; }
  .gauge-arc { position:relative; width:160px; height:80px; overflow:hidden; }
  .gauge-arc svg { width:160px; height:80px; }
  .gauge-num { font-size:36px; font-weight:700; margin-top:8px; }
  .gauge-label { font-size:12px; color:var(--muted); margin-top:4px; }
  .gauge-bar-wrap { width:100%; background:var(--border); border-radius:4px; height:8px; margin-top:12px; overflow:hidden; }
  .gauge-bar { height:100%; border-radius:4px; transition:width .8s; }

  /* ── Mini stat ── */
  .mini-stat { display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid rgba(30,45,69,0.5); }
  .mini-stat:last-child { border-bottom:none; }
  .mini-label { font-size:13px; color:var(--muted); }
  .mini-value { font-size:13px; font-weight:600; }

  /* ── Badge tags ── */
  .tag-buy  { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700; background:rgba(16,185,129,.15); color:var(--green); }
  .tag-sell { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700; background:rgba(239,68,68,.15); color:var(--red); }
  .tag-hold { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700; background:rgba(100,116,139,.15); color:var(--muted); }

  /* ── ML panel ── */
  .ml-item { display:flex; align-items:center; justify-content:space-between; padding:9px 0; border-bottom:1px solid rgba(30,45,69,.5); }
  .ml-item:last-child { border-bottom:none; }
  .ml-bar-wrap { width:120px; background:var(--border); border-radius:4px; height:6px; overflow:hidden; }
  .ml-bar { height:100%; border-radius:4px; background:linear-gradient(90deg,#3b82f6,#8b5cf6); }

  /* ── Footer ── */
  .footer { text-align:center; padding:16px; color:var(--muted); font-size:12px; border-top:1px solid var(--border); }

  /* ── Responsive ── */
  @media(max-width:1100px) {
    .kpi-grid { grid-template-columns:repeat(2,1fr); }
    .three-col { grid-template-columns:1fr 1fr; }
  }
  @media(max-width:640px) {
    .kpi-grid { grid-template-columns:1fr; }
    .two-col,.three-col { grid-template-columns:1fr; }
  }
</style>
</head>
<body>

<!-- ── HEADER ── -->
<div class="header">
  <div class="header-left">
    <div class="logo">⚡ <span>Apex</span> Bot</div>
    <div class="badge" id="statusBadge">● LIVE</div>
  </div>
  <div class="header-right">
    <div class="mode-tag" id="modeTag">PAPER</div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
</div>

<!-- ── MAIN ── -->
<div class="main">

  <!-- KPI Row -->
  <div class="kpi-grid">
    <div class="kpi-card blue">
      <div class="kpi-label">💰 총 자산</div>
      <div class="kpi-value" id="totalAssets">₩--</div>
      <div class="kpi-sub">초기 ₩1,000,000</div>
    </div>
    <div class="kpi-card green">
      <div class="kpi-label">📊 투자금</div>
      <div class="kpi-value" id="invested">₩--</div>
      <div class="kpi-sub" id="investRatio">--% 투자 중</div>
    </div>
    <div class="kpi-card yellow">
      <div class="kpi-label">💵 현금 잔고</div>
      <div class="kpi-value" id="cashBalance">₩--</div>
      <div class="kpi-sub" id="cashRatio">--% 여유</div>
    </div>
    <div class="kpi-card purple">
      <div class="kpi-label">📈 오늘 손익</div>
      <div class="kpi-value" id="todayPnl">₩--</div>
      <div class="kpi-sub" id="todayPct">---%</div>
    </div>
  </div>

  <!-- Row 2: Positions + Fear&Greed + ML -->
  <div class="three-col">

    <!-- Positions Table -->
    <div class="panel" style="grid-column:span 2">
      <div class="panel-header">
        <div class="panel-title">📦 보유 포지션 <span id="posCount" style="color:var(--muted);font-size:12px;font-weight:400"></span></div>
        <div style="font-size:12px;color:var(--muted)" id="lastUpdate">업데이트 중...</div>
      </div>
      <div class="panel-body" style="padding:0">
        <table>
          <thead>
            <tr>
              <th>코인</th><th>전략</th><th>매수가</th><th>현재가</th>
              <th>평가금</th><th>손익</th><th>목표/손절</th>
            </tr>
          </thead>
          <tbody id="posTable">
            <tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">데이터 로딩 중...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Fear & Greed -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">🧠 시장 감성</div>
      </div>
      <div class="panel-body">
        <div class="gauge-wrap">
          <svg viewBox="0 0 160 80" width="160" height="80">
            <defs>
              <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%"   stop-color="#ef4444"/>
                <stop offset="33%"  stop-color="#f59e0b"/>
                <stop offset="66%"  stop-color="#10b981"/>
                <stop offset="100%" stop-color="#3b82f6"/>
              </linearGradient>
            </defs>
            <path d="M 10 75 A 70 70 0 0 1 150 75" fill="none" stroke="#1e2d45" stroke-width="12" stroke-linecap="round"/>
            <path d="M 10 75 A 70 70 0 0 1 150 75" fill="none" stroke="url(#gaugeGrad)" stroke-width="12" stroke-linecap="round" stroke-dasharray="220" stroke-dashoffset="220" id="gaugeArc"/>
            <line id="gaugeNeedle" x1="80" y1="75" x2="80" y2="20" stroke="#e2e8f0" stroke-width="2.5" stroke-linecap="round" transform-origin="80 75"/>
            <circle cx="80" cy="75" r="5" fill="var(--text)"/>
          </svg>
          <div class="gauge-num" id="fgNum">--</div>
          <div class="gauge-label" id="fgLabel">공포탐욕지수</div>
        </div>
        <div style="margin-top:12px">
          <div class="mini-stat">
            <span class="mini-label">김치 프리미엄</span>
            <span class="mini-value" id="kimchi">--%</span>
          </div>
          <div class="mini-stat">
            <span class="mini-label">뉴스 감성</span>
            <span class="mini-value" id="newsSentiment">--</span>
          </div>
          <div class="mini-stat">
            <span class="mini-label">시장 국면</span>
            <span class="mini-value" id="marketRegime">--</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Row 3: ML Predictions + Recent Trades + Stats -->
  <div class="two-col">

    <!-- ML Ensemble -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">🤖 ML 앙상블 예측</div>
        <div style="font-size:11px;color:var(--muted)" id="mlUpdate">--</div>
      </div>
      <div class="panel-body" id="mlPanel">
        <div style="color:var(--muted);text-align:center;padding:20px">분석 대기 중...</div>
      </div>
    </div>

    <!-- Recent Trades -->
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">🔄 최근 거래</div>
      </div>
      <div class="panel-body" style="padding:0">
        <table>
          <thead>
            <tr><th>시간</th><th>코인</th><th>구분</th><th>금액</th><th>손익</th></tr>
          </thead>
          <tbody id="tradeTable">
            <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">거래 데이터 없음</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Row 4: Stats -->
  <div class="three-col">
    <div class="panel">
      <div class="panel-header"><div class="panel-title">📊 성과 요약</div></div>
      <div class="panel-body">
        <div class="mini-stat"><span class="mini-label">총 거래 수</span><span class="mini-value" id="statTrades">--</span></div>
        <div class="mini-stat"><span class="mini-label">승률</span><span class="mini-value" id="statWin">--%</span></div>
        <div class="mini-stat"><span class="mini-label">손익비</span><span class="mini-value" id="statPF">--</span></div>
        <div class="mini-stat"><span class="mini-label">샤프 비율</span><span class="mini-value" id="statSharpe">--</span></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><div class="panel-title">⚠️ 리스크 현황</div></div>
      <div class="panel-body">
        <div class="mini-stat"><span class="mini-label">최대 낙폭</span><span class="mini-value" id="riskMDD">--%</span></div>
        <div class="mini-stat"><span class="mini-label">포지션 수</span><span class="mini-value" id="riskPos">-- / 10</span></div>
        <div class="mini-stat"><span class="mini-label">BEAR_REVERSAL</span><span class="mini-value" id="riskBear">오늘 -- / 3</span></div>
        <div class="mini-stat"><span class="mini-label">봇 상태</span><span class="mini-value" id="riskStatus">--</span></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><div class="panel-title">🏆 전략 성과</div></div>
      <div class="panel-body" id="stratPanel">
        <div style="color:var(--muted);text-align:center;padding:20px">전략 데이터 없음</div>
      </div>
    </div>
  </div>

</div>

<div class="footer">⚡ Apex Bot v2.0.0 · PAPER Trading · RTX 5060 (CUDA 12.8) · <span id="footerTime">--</span></div>

<script>
// ── WebSocket ──────────────────────────────────────────────
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen  = () => console.log('WS connected');
ws.onclose = () => { document.getElementById('statusBadge').textContent='● OFFLINE'; document.getElementById('statusBadge').style.color='var(--red)'; };
ws.onmessage = e => {
  try { updateAll(JSON.parse(e.data)); } catch(err){}
};
setInterval(() => { if(ws.readyState===1) ws.send(JSON.stringify({type:'ping'})); }, 5000);

// ── Clock ──────────────────────────────────────────────────
function pad(n){ return String(n).padStart(2,'0'); }
function tick(){
  const n=new Date();
  const t=`${pad(n.getHours())}:${pad(n.getMinutes())}:${pad(n.getSeconds())}`;
  document.getElementById('clock').textContent=t;
  document.getElementById('footerTime').textContent=t;
}
setInterval(tick,1000); tick();

// ── Formatters ─────────────────────────────────────────────
function fmt(n){ return '₩'+Math.round(n||0).toLocaleString('ko-KR'); }
function fmtPct(n,dec=2){ const v=parseFloat(n||0); return (v>=0?'+':'')+v.toFixed(dec)+'%'; }
function colorClass(n){ return parseFloat(n)>0?'pos':parseFloat(n)<0?'neg':'neu'; }
const COIN_COLORS = {
  'BTC':'#f7931a','ETH':'#627eea','XRP':'#00aae4','SOL':'#9945ff',
  'DOT':'#e6007a','ADA':'#0033ad','LINK':'#2a5ada','DOGE':'#c3a634',
  'MATIC':'#8247e5','ATOM':'#2e3148'
};
function coinDot(sym){
  const c=COIN_COLORS[sym]||'#64748b';
  return `<span class="dot" style="background:${c}"></span>`;
}

// ── Main updater ───────────────────────────────────────────
// ── Main updater ──────────────────────────────────────────────
function updateAll(d){
  const p  = d.portfolio  || {};
  const s  = d.signals    || {};
  const m  = d.metrics    || {};
  const ml = s.ml_predictions || d.ml_predictions || {};
  // ML 앙상블 단건 예측 표시 (signals.ml_prediction 단수)
  (function() {
    var pred = s.ml_prediction || null;
    var el = document.getElementById('mlSignal') || document.querySelector('.ml-signal');
    // 기존 ML 예측 컨테이너 찾기
    var container = document.querySelector('[id*="ml"]');
    var mlText = '--';
    if (pred && pred.signal) {
      var arrow = pred.signal === 'BUY' ? '🟢' : pred.signal === 'SELL' ? '🔴' : '🟡';
      mlText = arrow + ' ' + pred.signal + ' (' + (pred.confidence * 100).toFixed(1) + '%)';
      mlText += ' | 매수확률: ' + (pred.buy_prob * 100).toFixed(1) + '%';
    } else if (Object.keys(ml).length > 0) {
      // ml_predictions 형식 처리
      var markets = Object.keys(ml);
      if (markets.length > 0) {
        var first = ml[markets[0]];
        if (first && first.signal) {
          var arrow2 = first.signal === 'BUY' ? '🟢' : first.signal === 'SELL' ? '🔴' : '🟡';
          mlText = arrow2 + ' ' + first.signal;
        }
      }
    }
    // ML 예측 텍스트 표시할 요소 찾기
    var mlEls = document.querySelectorAll('[id*="Ml"],[id*="ml"],[class*="ml-pred"]');
    mlEls.forEach(function(e) {
      if (e.tagName !== 'SCRIPT' && e.id && e.id.toLowerCase().includes('ml')) {
        if (mlText !== '--') e.textContent = mlText;
      }
    });
  })();

  // KPI
  const total  = p.total_assets || p.total_krw   || 0;
  const cash   = p.cash         || p.krw_balance  || 0;
  const inv    = p.invested     || Math.max(total - cash, 0);
  const pnl    = p.pnl          || p.pnl_today    || m.daily_pnl || 0;
  const initial = 1000000;
  const pnlPct  = total > 0 ? ((total - initial) / initial * 100) : 0;
  const invR    = total > 0 ? (inv  / total * 100).toFixed(1) : 0;
  const cashR   = total > 0 ? (cash / total * 100).toFixed(1) : 0;

  document.getElementById('totalAssets').textContent = fmt(total);
  document.getElementById('invested').textContent    = fmt(inv);
  document.getElementById('cashBalance').textContent = fmt(cash);
  document.getElementById('investRatio').textContent = invR  + '% 투자 중';
  document.getElementById('cashRatio').textContent   = cashR + '% 여유';

  const pnlEl = document.getElementById('todayPnl');
  const pctEl = document.getElementById('todayPct');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl);
  pnlEl.className   = 'kpi-value ' + (pnl >= 0 ? 'pos' : 'neg');
  pctEl.textContent = fmtPct(pnlPct);
  pctEl.className   = 'kpi-sub '  + (pnlPct >= 0 ? 'pos' : 'neg');

  document.getElementById('lastUpdate').textContent = '⏱ ' + new Date().toLocaleTimeString('ko-KR');

  document.getElementById('modeTag').textContent = (p.mode || 'PAPER').toUpperCase();
  const st    = (d.bot_status || 'running').toLowerCase();
  const badge = document.getElementById('statusBadge');
  badge.textContent = st === 'running' ? '● LIVE' : '● ' + st.toUpperCase();
  badge.style.color = st === 'running' ? 'var(--green)' : 'var(--yellow)';

  // Positions: dict이면 배열로 변환
  let positions = p.positions_detail || [];
  if (positions.length === 0 && p.positions && typeof p.positions === 'object' && !Array.isArray(p.positions)) {
    positions = Object.entries(p.positions).map(function([market, pos]) {
      return {
        market:        market,
        strategy:      pos.strategy      || '-',
        entry_price:   pos.entry_price   || 0,
        current_price: pos.current_price || pos.entry_price || 0,
        amount_krw:    (pos.entry_price  || 0) * (pos.volume || 0),
        profit_rate:   (pos.unrealized_pnl_pct || 0) / 100,
        take_profit:   pos.take_profit   || null,
        stop_loss:     pos.stop_loss     || null,
      };
    });
  }
  document.getElementById('posCount').textContent = '(' + positions.length + '개)';
  const tbody = document.getElementById('posTable');
  if (positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">보유 포지션 없음</td></tr>';
  } else {
    tbody.innerHTML = positions.map(function(pos) {
      const sym  = (pos.market || '').replace('KRW-', '');
      const pnlR = parseFloat(pos.profit_rate || 0) * 100;
      const tp   = pos.take_profit ? fmt(pos.take_profit) : '-';
      const sl   = pos.stop_loss   ? fmt(pos.stop_loss)   : '-';
      return '<tr>'
        + '<td><span class="coin-tag">' + coinDot(sym) + '<b>' + sym + '</b></span></td>'
        + '<td><span style="font-size:11px;color:var(--muted)">' + (pos.strategy || '-') + '</span></td>'
        + '<td>' + fmt(pos.entry_price) + '</td>'
        + '<td>' + fmt(pos.current_price || pos.entry_price) + '</td>'
        + '<td>' + fmt(pos.amount_krw) + '</td>'
        + '<td class="' + colorClass(pnlR) + '">' + fmtPct(pnlR) + '</td>'
        + '<td style="font-size:11px;color:var(--muted)">' + tp + ' / ' + sl + '</td>'
        + '</tr>';
    }).join('');
  }

  // Fear & Greed
  const fg = parseFloat(s.fear_greed || m.fear_greed_index || 11);
  document.getElementById('fgNum').textContent = fg;
  const fgLabels = ['극단적 공포','공포','중립','탐욕','극단적 탐욕'];
  const fgIdx    = fg<20?0:fg<40?1:fg<60?2:fg<80?3:4;
  const fgColors = ['var(--red)','#f97316','var(--muted)','var(--green)','#06b6d4'];
  document.getElementById('fgLabel').textContent = fgLabels[fgIdx];
  document.getElementById('fgNum').style.color   = fgColors[fgIdx];
  const arc    = document.getElementById('gaugeArc');
  const offset = 220 - (fg / 100 * 220);
  arc.style.strokeDashoffset = offset;
  const needle = document.getElementById('gaugeNeedle');
  const deg    = -90 + (fg / 100 * 180);
  needle.setAttribute('transform', 'rotate(' + deg + ',80,75)');

  const kimchi = (s.kimchi_premium != null) ? s.kimchi_premium : (m.kimchi_premium != null ? m.kimchi_premium : null);
  document.getElementById('kimchi').textContent        = kimchi != null ? fmtPct(kimchi, 1) : '--%';
  document.getElementById('newsSentiment').textContent = s.news_sentiment || m.news_sentiment || '--';
  // 시장 국면: Fear&Greed 기반 직접 계산
  (function() {
    // fear_greed: signals.fear_greed → metrics.fear_greed_index → 기본값 순으로 탐색
    var fg = null;
    if (s && s.fear_greed != null)        fg = parseInt(s.fear_greed);
    else if (m && m.fear_greed_index != null) fg = parseInt(m.fear_greed_index);
    if (fg === null || isNaN(fg))         fg = 50;
    var regime = s.market_regime || m.market_regime;
    if (!regime || regime === '--') {
      if      (fg <= 20) regime = '🔴 BEAR (극단공포)';
      else if (fg <= 35) regime = '🟠 BEAR_WATCH';
      else if (fg <= 55) regime = '🟡 NEUTRAL';
      else if (fg <= 75) regime = '🟢 BULL_WATCH';
      else               regime = '🟢 BULL (극단탐욕)';
    }
    document.getElementById('marketRegime').textContent = regime;
  })();

  // ML Panel
  const mlBody  = document.getElementById('mlPanel');
  const mlPreds = (ml && ml.predictions) ? ml.predictions : (Array.isArray(ml) ? ml : []);
  if (mlPreds.length > 0) {
    document.getElementById('mlUpdate').textContent = (ml && ml.last_update) ? ml.last_update : '--';
    mlBody.innerHTML = mlPreds.slice(0, 6).map(function(r) {
      const conf     = parseFloat(r.confidence || 0) * 100;
      const tagClass = r.signal === 'BUY' ? 'tag-buy' : r.signal === 'SELL' ? 'tag-sell' : 'tag-hold';
      const sym      = (r.market || '').replace('KRW-', '');
      return '<div class="ml-item">'
        + '<span style="font-weight:600;font-size:13px">' + coinDot(sym) + ' ' + sym + '</span>'
        + '<span class="' + tagClass + '">' + (r.signal || 'HOLD') + '</span>'
        + '<div style="display:flex;align-items:center;gap:8px">'
        + '<div class="ml-bar-wrap"><div class="ml-bar" style="width:' + conf + '%"></div></div>'
        + '<span style="font-size:12px;color:var(--muted);width:36px;text-align:right">' + conf.toFixed(0) + '%</span>'
        + '</div></div>';
    }).join('');
  } else {
    mlBody.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px">분석 대기 중...</div>';
  }

  // Recent Trades
  const trades = (d.recent_trades || []).slice(0, 8);
  const ttbody = document.getElementById('tradeTable');
  if (trades.length === 0) {
    ttbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">거래 데이터 없음</td></tr>';
  } else {
    ttbody.innerHTML = trades.map(function(t) {
      const sym      = (t.market || '').replace('KRW-', '');
      const time     = (t.timestamp || '').slice(11, 16);
      const tagClass = t.side === 'BUY' ? 'tag-buy' : 'tag-sell';
      const pnl2     = parseFloat(t.profit_rate || 0) * 100;
      return '<tr>'
        + '<td style="color:var(--muted);font-size:12px">' + time + '</td>'
        + '<td>' + coinDot(sym) + '<b style="margin-left:4px">' + sym + '</b></td>'
        + '<td><span class="' + tagClass + '">' + t.side + '</span></td>'
        + '<td>' + fmt(t.amount_krw) + '</td>'
        + '<td class="' + colorClass(pnl2) + '">' + (t.side === 'SELL' ? fmtPct(pnl2) : '-') + '</td>'
        + '</tr>';
    }).join('');
  }

  // Stats
  document.getElementById('statTrades').textContent = m.total_trades || '--';
  document.getElementById('statWin').textContent    = m.win_rate != null ? parseFloat(m.win_rate).toFixed(1) + '%' : '--';
  document.getElementById('statPF').textContent     = m.profit_factor != null ? parseFloat(m.profit_factor).toFixed(2) : '--';
  document.getElementById('statSharpe').textContent = m.sharpe_ratio  != null ? parseFloat(m.sharpe_ratio).toFixed(3)  : '--';
  document.getElementById('riskMDD').textContent    = m.max_drawdown  != null ? fmtPct(parseFloat(m.max_drawdown) * 100, 2) : '--';
  document.getElementById('riskPos').textContent    = positions.length + ' / 10';
  document.getElementById('riskBear').textContent   = '오늘 ' + (s.bear_reversal_count || m.bear_reversal_count || 0) + ' / 3';
  const stEl = document.getElementById('riskStatus');
  stEl.textContent = st === 'running' ? '정상 작동' : '일시정지';
  stEl.className   = 'mini-value ' + (st === 'running' ? 'pos' : 'yellow');

  // Strategy panel
  const stratData = m.strategy_stats || [];
  const sPanel    = document.getElementById('stratPanel');
  if (stratData.length > 0) {
    sPanel.innerHTML = stratData.slice(0, 5).map(function(s2) {
      const wr = parseFloat(s2.win_rate || 0);
      return '<div class="mini-stat">'
        + '<span class="mini-label" style="font-size:12px">' + (s2.strategy || '?') + '</span>'
        + '<span class="mini-value" style="font-size:12px">' + (s2.trades || 0) + '건 / ' + wr.toFixed(0) + '% 승</span>'
        + '</div>';
    }).join('');
  } else {
    sPanel.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px">전략 데이터 없음</div>';
  }
}


// ── Fallback REST poll (if WS slow) ───────────────────────
async function poll(){
  try{
    const [s,p,m,ml,t] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/portfolio').then(r=>r.json()),
      fetch('/api/metrics').then(r=>r.json()),
      fetch('/api/ml-predict').then(r=>r.json()),
      fetch('/api/trades?limit=8').then(r=>r.json())
    ]);
    updateAll({...s, portfolio:p, metrics:m, ml_predictions:ml, recent_trades:t});
  }catch(e){}
}
poll();
setInterval(poll, 10000);
</script>
</body>
</html>
"""


class DashboardServer:
    """대시보드 서버 관리 클래스"""

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
            port=self.settings.monitoring.dashboard_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        logger.info(
            f"🌐 대시보드 시작: http://{self.settings.monitoring.dashboard_host}"
            f":{self.settings.monitoring.dashboard_port}"
        )
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self):
        if self._server_task:
            self._server_task.cancel()