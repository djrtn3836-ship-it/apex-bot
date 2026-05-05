# APEX BOT — CHANGELOG.md

---

## [v3.0.0] 2026-05-03 — 대규모 안정화 패치 (39개)

### 핵심 변경사항
- VWAP_Reversion / VolBreakout 전략 영구 제거
- SmartWallet epsilon 통일 (1e-06)
- ws_collector HTTP 429 근본 해결
- dust 합산 자동 청산 로직 구현

---

### 패치 목록 (시간순)

#### 08:02 | fix_c4_only.py
- **대상**: core/engine_buy.py
- **내용**: C-4 매수 로직 수정

#### 08:07 | fix_engine_surge_v1.py
- **대상**: core/engine.py, surge_detector.py, core/engine_buy.py
- **내용**: Surge 감지 및 TTL 처리 개선

#### 08:12 | fix_ec2_final2.py
- **대상**: core/engine.py
- **내용**: _dm_max_cached hot-fix

#### 08:14 | fix_v2layer_enm3.py
- **대상**: strategies/v2/v2_layer.py, core/engine_buy.py
- **내용**: EN-M3 fallback 처리

#### 08:19 | fix_telegram_v1.py
- **대상**: monitoring/telegram_bot.py, core/engine.py
- **내용**: Telegram pause 명령 처리

#### 08:24 | fix_resume_final.py
- **대상**: core/engine.py
- **내용**: T-3+E-H3 resume 로직

#### 08:33 | fix_smartwallet_enc3.py
- **대상**: core/smart_wallet.py, strategies/v2/ensemble_engine.py
- **내용**: SW-B1, EN-C3 수정

#### 08:46 | fix_settings_strategies_v2.py
- **대상**: strategies/v2/ensemble_engine.py, core/engine_buy.py, config/settings.py
- **내용**: VWAP_Reversion / VolBreakout 제거 (A-E)
- **효과**: 연간 +₩6,679 손실 방지

#### 08:52 | fix_vwap_final.py
- **대상**: strategies/v2/ensemble_engine.py
- **내용**: MAP precision 제거 (A-E 전항목 OK)
- **백업**: nsemble_engine.py.bak_vwap_final_20260503_085245

#### 09:06 | fix_vwap_cycle.py
- **대상**: core/engine_cycle.py, core/engine_buy.py, core/engine_sell.py, signals/signal_combiner.py
- **내용**: VWAP_Reversion engine_cycle 레지스트리에서 완전 제거 (19항목)
- **효과**: engine_cycle 전략 수 7→6, 두 레지스트리 완전 동기화
- **백업**: *.bak_vwap_cycle_20260503_090618

#### 09:16 | fix_epsilon_final.py
- **대상**: core/smart_wallet.py
- **내용**: epsilon 전체 통일 1e-08→1e-06 (11항목, EP-A~F)
- **효과**: KAVA 1e-08 잔존 버그 해결, 전량 청산 정확도 향상
- **백업**: smart_wallet.py.bak_epsilon_20260503_091655

#### 09:22 | fix_dust_sellable.py
- **대상**: core/smart_wallet.py
- **내용**: DS-2 refresh_dust_state epsilon 통일
- **백업**: smart_wallet.py.bak_dustsell_20260503_092233

#### 09:23 | fix_dust_sellable_v2.py
- **대상**: core/smart_wallet.py
- **내용**: dust 합산 매도 로직 완성 (DS-1~2)
  - bot>0 & 합산≥₩5,000 → dust 포함 전량 청산
  - bot>0 & 합산<₩5,000 → dust 제외 (API 최소주문 보호)
  - bot=0 & dust≥₩500 → dust 단독 매도
  - bot=0 & dust<₩500 → 재매수 시 자동 합산 예약
- **백업**: smart_wallet.py.bak_dustv2_20260503_092348

#### 09:29 | fix_ws_429.py
- **대상**: data/collectors/ws_collector.py
- **내용**: HTTP 429 근본 해결 (WS-1~4, 5항목)
  - WS-1: target_markets 파라미터 추가
  - WS-2: 연결 간격 0.5s, trade 구독 제거, orderbook 10코인 제한
  - WS-3: _running 프로퍼티 정확도 개선
  - WS-4: restart 시 _started 리셋, collector 재초기화
- **효과**: 스트림 14개(ticker×13+orderbook×1), 데이터량 33%↓, 429 완전 제거
- **백업**: ws_collector.py.bak_ws429_20260503_092939

---

### 제거된 전략
| 전략 | 제거일 | 사유 | 연간 효과 |
|---|---|---|---|
| VWAP_Reversion | 2026-05-03 | 누적 손실 | +₩3,158/년 |
| VolBreakout | 2026-05-03 | 누적 손실 | +₩3,521/년 |

### 수정된 파일 목록
| 파일 | 수정 횟수 | 주요 내용 |
|---|---|---|
| strategies/v2/ensemble_engine.py | 3회 | VWAP/VolBreakout 제거, boost 5개 |
| core/engine_buy.py | 4회 | C4, Surge, VWAP, VolBreakout 제거 |
| core/engine.py | 3회 | EC2, Telegram, Resume |
| core/engine_cycle.py | 1회 | VWAP 레지스트리 제거 |
| core/engine_sell.py | 1회 | VWAP 매도 경로 차단 |
| core/smart_wallet.py | 3회 | epsilon 1e-06 통일, dust 합산 |
| signals/signal_combiner.py | 1회 | VWAP 가중치 제거 |
| data/collectors/ws_collector.py | 1회 | HTTP 429 해결 |
| config/settings.py | 1회 | VWAP/VolBreakout 설정 제거 |

---

## [v2.x] 이전 버전
> 2026-05-03 이전 변경사항은 git log 참조
