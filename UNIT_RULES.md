# profit_rate 단위 규칙 (APEX BOT — 절대 불변)

> 최종 확정: 2026-04-26 | 커밋: 46192fe
> profit_rate 관련 코드 수정 전 반드시 이 파일 확인

## 핵심 규칙 요약

| 변수/상황 | 단위 | 예시 |
|---|---|---|
| profit_rate | % 단위 | 1.6 = +1.6% |
| 비교 임계값 | % 기준 | > 0.5 / < -0.5 |
| 포맷 문자열 | :.2f% | f"{profit_rate:.2f}%" |
| _pnl / _ratio | 소수 단위 | 0.016 = +1.6% |
| PPO trainer 전달 | / 100 변환 후 | profit_rate / 100 |
| live_guard 전달 | / 100 변환 후 | profit_rate / 100 |
| DB 저장 | % 그대로 | 변환 없이 저장 |
| close_position() 반환 | % 단위 | * 100 절대 금지 |

## 경로별 단위 흐름

### 경로 A — paper mode (executor 직접 계산)
- engine_sell.py L73~75
- profit_rate = (executed_price - entry_price) / entry_price * 100
- 결과: % 단위 (예: 1.6)

### 경로 B — live mode (portfolio_manager 반환)
- engine_sell.py L235
- proceeds, profit_rate = _close_result
- close_position()이 이미 % 단위로 반환
- * 100 절대 금지

## PPO / live_guard 전달 시 반드시 변환
- _pnl = profit_rate / 100
- live_guard.on_trade_result(profit_rate / 100.0, market)

## 과거 버그 이력

| 날짜 | 버그 | 원인 | 커밋 |
|---|---|---|---|
| 2026-04-26 | ML익절_-100.0% | close_position() % 반환값에 * 100 적용 | 46192fe |
| 2026-04-26 | 포맷 오류 | :.2% 포맷이 % 값을 100배 출력 | 46192fe |
| 2026-04-26 | 임계값 불일치 | -0.005 소수 기준 사용 | 46192fe |

## 수정 전 체크리스트

- profit_rate 비교 임계값이 % 기준인가? (0.5, -0.5, 1.5, -2.5)
- 포맷 문자열이 :.2f% 인가? (:.2% 사용 금지)
- close_position() 반환값에 * 100 하지 않았는가?
- PPO/live_guard 전달 시 / 100 변환했는가?
