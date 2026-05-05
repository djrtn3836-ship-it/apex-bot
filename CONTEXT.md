# APEX BOT — CONTEXT.md
> 마지막 업데이트: 2026-05-03 09:31 KST

## 프로젝트 개요
- **봇 이름**: APEX BOT v3.0.0
- **거래소**: Upbit (KRW 마켓)
- **모드**: LIVE
- **언어/프레임워크**: Python 3.x, PyTorch 2.12.0.dev (CUDA 12.8)
- **GPU**: NVIDIA GeForce RTX 5060 (8.5 GB VRAM, Blackwell)
- **DB**: SQLite (database/apex_bot.db, 1,312건 거래 이력)
- **대시보드**: http://0.0.0.0:8888
- **텔레그램**: @storm3836bot

---

## 현재 활성 전략 (6개)
| 전략명 | 승률 | 가중치 | 비고 |
|---|---|---|---|
| MACD_Cross | 55.0% | 1.00 | EN-C3 카운터 복원 |
| RSI_Divergence | 63.6% | 1.16 | 정상 |
| Bollinger_Squeeze | 65.0% | 1.18 | 정상 |
| OrderBlock_SMC | 45.0% | 1.64 | EN-C3 카운터 복원 |
| Supertrend | - | 1.00 | 정상 |
| ATR_Channel | - | 1.00 | 정상 |

### 비활성화된 전략 (영구 제거)
| 전략명 | 제거 사유 | 연간 손실 방지 |
|---|---|---|
| VWAP_Reversion | 누적 손실 -₩3,158 | +₩3,158/년 |
| VolBreakout | 누적 손실 -₩3,521 | +₩3,521/년 |

---

## 현재 포지션 (2026-05-03 09:31 기준)
| 코인 | 진입가 | 투자금 | SL | TP | 전략 | 보유 |
|---|---|---|---|---|---|---|
| DOGE | ₩160 | ₩9,187 | ₩156.3 | ₩166.4 | Bollinger_Squeeze | 15.0h |
| ID | ₩47.1 | ₩10,180 | ₩46.2 | ₩49.1 | OrderBlock_SMC | 14.3h |
| ANIME | ₩7.01 | ₩11,798 | ₩6.9 | ₩7.4 | OrderBlock_SMC | 13.8h |
| WAVES | ₩609 | ₩13,396 | ₩596.7 | ₩634.5 | OrderBlock_SMC | 2.9h |
| BTC | ₩116,699,000 | ₩13,230 | ₩115,547,850 | ₩119,228,862 | MACD_Cross | 0.4h |

- **KRW 잔고**: ₩52,916
- **총 투자금**: ₩57,792
- **슬롯**: 5/5 (전체 사용 중)

---

## 시스템 아키텍처

---

## 핵심 설정값 (config/settings.py)
| 설정 | 값 | 설명 |
|---|---|---|
| max_positions | 5 | 전역 최대 포지션 |
| regime_bear_max_positions | 0 | BEAR 체제 시 전면 차단 |
| max_position_ratio | 0.17 | 포지션당 최대 비율 |
| buy_signal_threshold | 0.35 | 매수 신호 임계값 |
| sell_signal_threshold | 0.35 | 매도 신호 임계값 |

---

## SmartWallet 설계 (core/smart_wallet.py)
- **epsilon**: 1e-06 (부동소수점 오차 방지, 2026-05-03 통일)
- **DustState**: PENDING → SELLABLE → ORPHAN
- **dust 합산 로직**:
  - bot > 0 & (bot+dust)×price ≥ ₩5,000 → dust 포함 매도
  - bot > 0 & 합산 < ₩5,000 → dust 제외 (API 최소주문 보호)
  - bot = 0 & dust ≥ ₩500 → dust 단독 매도
  - bot = 0 & dust < ₩500 → 보류 (재매수 시 자동 합산)
- **HOLD 코인**: XRP (봇 완전 차단)
- **DEAD 코인**: ELF, VTHO, BSV, KAVA, LINK, ORCA, BONK, VIRTUAL, IP, HOLO, CPOOL

---

## WebSocket 구조 (data/collectors/ws_collector.py)
- **총 스트림**: 14개 (ticker×13 + orderbook×1)
- **연결 간격**: 0.5초 (HTTP 429 방지)
- **Trade 구독**: 제거 (데이터량 33% 감소)
- **Orderbook**: target 10코인만 구독 (96% 감소)
- **재연결**: engine_schedule._ws_reconnect_loop (30초 감지)

---

## 시간대별 거래 성과 (DB 기준, 674건 SELL)
| 구분 | 거래수 | 승률 | 수익합계 | 평균수익률 |
|---|---|---|---|---|
| ☀️ 주간 (06~18시) | 374건 | 52.4% | ₩28,754 | +0.29% |
| 🌙 야간 (18~06시) | 300건 | 53.0% | ₩20,368 | +0.14% |

> 야간 슬롯 제한 미적용 결정 근거: 야간 승률이 주간보다 0.6%p 높음.
> 위험 구간: 00시(승률 24%), 05시(33.3%), 14시(38.5%), 17시(32.4%)

---

## 전역 시장 체제 (GlobalRegime)
| 체제 | SURGE 임계값 | 설명 |
|---|---|---|
| BULL | 0.40 | 강세장 완화 |
| RECOVERY | 0.45 | 회복장 기본 (현재) |
| BEAR_WATCH | 0.55 | 약세경계 강화 |
| BEAR | 9.99 | 사실상 전면 차단 |

**현재**: RECOVERY (BTC EMA200 +1.35%)

---

## 알려진 이슈 / 모니터링 항목
| 항목 | 상태 | 비고 |
|---|---|---|
| KAVA dust 1e-08 | ✅ 해결 | epsilon 1e-06 통일로 DEAD 처리 |
| VWAP engine_cycle 잔존 | ✅ 해결 | fix_vwap_cycle.py 19항목 제거 |
| ws_collector HTTP 429 | ✅ 해결 | 14스트림, 0.5s 간격 |
| EN-C3 카운터 복원 | ✅ 정상 | MACD/OrderBlock 복원 확인 |
| Walk-Forward OOS=0 | 🟡 모니터링 | 데이터 부족, 정상 동작 |
| FinBERT UNEXPECTED key | 🟢 무시 가능 | 아키텍처 차이, 기능 정상 |
