"""
fix_ws_orderbook.py
– engine.py의 WebSocket 수집기에 orderbook 구독 및 캐시 저장 연동
– _on_ws_message에 orderbook 타입 라우팅 추가
– subscribe_orderbook() 호출 추가
"""
import shutil, py_compile
from pathlib import Path

engine_path = Path("core/engine.py")
shutil.copy(engine_path, "core/engine.py.bak_ws")
text = engine_path.read_text(encoding="utf-8", errors="ignore")
lines = text.splitlines()

# ── 수정 대상 블록 탐색 ──────────────────────────────────────────────────
# L306~L320 구간을 찾아서 교체
# 기준: "_on_ws_message" 정의 줄 탐색

target_start = None   # async def _on_ws_message 줄 인덱스
target_end   = None   # subscribe_ticker() 줄 인덱스

for i, ln in enumerate(lines):
    if "async def _on_ws_message" in ln and target_start is None:
        target_start = i
    if target_start and "subscribe_ticker()" in ln:
        target_end = i
        break

if target_start is None or target_end is None:
    print(f"⚠️ 대상 블록을 찾지 못했습니다.")
    print(f"  target_start={target_start}, target_end={target_end}")
    print("  수동으로 engine.py L306~L320 구간을 확인하세요.")
    exit(1)

print(f"✅ 수정 대상 확인: L{target_start+1} ~ L{target_end+1}")

# ── 기존 블록의 들여쓰기 추출 ────────────────────────────────────────────
indent = len(lines[target_start]) - len(lines[target_start].lstrip())
sp = " " * indent  # 기존 코드와 동일한 들여쓰기

# ── 교체할 새 블록 ───────────────────────────────────────────────────────
new_block = f"""{sp}async def _on_ws_message(data):
{sp}    msg_type = data.get('ty', data.get('type', ''))
{sp}    market   = data.get('cd', data.get('code', ''))

{sp}    # ── ticker: 현재가 업데이트 ──────────────────────────────────
{sp}    if msg_type == 'ticker':
{sp}        price = data.get('tp', data.get('trade_price', 0))
{sp}        if market and price:
{sp}            self._market_prices[market] = price
{sp}            self.correlation_filter.update_price(market, price)
{sp}            self.kimchi_monitor.update_upbit_price(market, price)

{sp}    # ── orderbook: 호가창 캐시 저장 ─────────────────────────────
{sp}    elif msg_type == 'orderbook':
{sp}        if market:
{sp}            # SIMPLE 포맷 → orderbook_units 변환
{sp}            raw_units = data.get('obu', data.get('orderbook_units', []))
{sp}            normalized = {{
{sp}                "market": market,
{sp}                "timestamp": data.get('tms', 0),
{sp}                "total_ask_size": data.get('tas', 0.0),
{sp}                "total_bid_size": data.get('tbs', 0.0),
{sp}                "orderbook_units": [
{sp}                    {{
{sp}                        "ask_price": u.get('ap', u.get('ask_price', 0)),
{sp}                        "bid_price": u.get('bp', u.get('bid_price', 0)),
{sp}                        "ask_size":  u.get('as', u.get('ask_size',  0)),
{sp}                        "bid_size":  u.get('bs', u.get('bid_size',  0)),
{sp}                    }}
{sp}                    for u in raw_units
{sp}                ],
{sp}            }}
{sp}            self.cache_manager.set_orderbook(market, normalized)

{sp}self.ws_collector = WebSocketCollector(
{sp}    markets=self.settings.trading.target_markets,
{sp}    on_message=_on_ws_message
{sp})
{sp}self.ws_collector.subscribe_ticker()
{sp}self.ws_collector.subscribe_orderbook()
{sp}logger.info(f"✅ WebSocket 호가창 구독 시작 | {{len(self.settings.trading.target_markets)}}개 코인")"""

# ── 기존 블록 교체 ───────────────────────────────────────────────────────
new_lines = lines[:target_start] + new_block.splitlines() + lines[target_end + 1:]
new_text  = "\n".join(new_lines)

engine_path.write_text(new_text, encoding="utf-8")

try:
    py_compile.compile(str(engine_path), doraise=True)
    print("✅ engine.py 문법 OK – WebSocket 호가창 연동 완료")
    print(f"   수정 범위: L{target_start+1} ~ L{target_end+1}")
    print("\n다음: python start_paper.py")
except py_compile.PyCompileError as e:
    print(f"❌ 문법 오류: {e}")
    import re
    m = re.search(r"line (\d+)", str(e))
    if m:
        n = int(m.group(1))
        err_lines = new_text.splitlines()
        for j in range(max(0, n-3), min(len(err_lines), n+4)):
            print(f"  L{j+1}: {err_lines[j]}")
    shutil.copy("core/engine.py.bak_ws", engine_path)
    print("🔄 원본 복구 완료")
