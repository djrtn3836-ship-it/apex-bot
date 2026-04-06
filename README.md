# ⚡ APEX BOT v1.0.0

> **업비트 전용 AI 퀀트 자동매매봇**  
> Asyncio + 멀티전략 앙상블 + ML 예측 + 실시간 리스크 관리

---

## 📋 목차
1. [개요](#개요)
2. [아키텍처](#아키텍처)
3. [빠른 시작](#빠른-시작)
4. [설정](#설정)
5. [전략 목록](#전략-목록)
6. [리스크 관리](#리스크-관리)
7. [ML 모델](#ml-모델)
8. [백테스트](#백테스트)
9. [모니터링](#모니터링)
10. [프로젝트 구조](#프로젝트-구조)

---

## 개요

APEX BOT은 Python 3.11+, asyncio 기반의 완전 비동기 자동매매 시스템입니다.

| 항목 | 사양 |
|------|------|
| 거래소 | 업비트 (KRW 마켓) |
| 타겟 코인 | BTC, ETH, XRP, SOL, ADA, DOGE, AVAX, DOT, LINK, ATOM |
| 주 타임프레임 | 1시간봉 (신호), 일봉 (추세) |
| 전략 수 | 8개 (모멘텀 3 + 평균회귀 2 + 변동성 2 + 시장구조 1) |
| ML 모델 | Bi-LSTM + TFT + CNN-LSTM 앙상블 |
| 최대 동시 포지션 | 5개 |
| 리스크 | Kelly 기준 반분, 거래당 최대 2% |

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    APEX BOT Engine                       │
│   P코어 0-1: asyncio 이벤트 루프 + 주문 실행           │
│   P코어 2-3: 전략 병렬 처리 (ProcessPoolExecutor)      │
│   E코어: 데이터 수집 + 모니터링                        │
└─────────────────────────────────────────────────────────┘
         │                    │                  │
    ┌────┴────┐         ┌─────┴────┐       ┌────┴────┐
    │  Data   │         │Strategy  │       │  Risk   │
    │ Pipeline│         │ Engine   │       │Manager  │
    │ WS+REST │         │ 8 strats │       │Kelly+ATR│
    └────┬────┘         └─────┬────┘       └────┬────┘
         │                    │                  │
    ┌────┴──────────────────┐ │           ┌─────┴─────┐
    │    Signal Combiner    │◄┘           │ Execution │
    │  Weighted Ensemble    │─────────────│  Engine   │
    │  + ML Prediction      │             │  Upbit API│
    └───────────────────────┘             └───────────┘
```

### 매수 조건 (5가지 AND)
1. 일봉 EMA200 상향 (상승 추세)
2. 레짐 감지: 하락추세 제외
3. ≥3개 전략 BUY 신호, 결합 점수 ≥ 4.5
4. 거래량 ≥ 20일 평균의 1.5배
5. 드로다운 < 10% & 가용 자본 충분

### 매도 조건 (우선순위 OR)
1. 🚨 긴급 손절: 가격 ≤ 진입가 × (1 - 1.5×ATR)
2. 일일 손실 > 5%
3. 수익 3% 초과 후 트레일링 스탑 발동
4. ATR 기반 목표가 도달 (3×ATR)
5. 반전 신호 (≥3 전략 SELL)

---

## 빠른 시작

### 1. 설치

```bash
# 저장소 클론
git clone <your-repo>
cd apex_bot

# 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 패키지 설치
pip install -r requirements.txt

# TA-Lib 설치 (필수)
# Ubuntu/Debian:
sudo apt-get install -y libta-lib-dev
pip install TA-Lib

# 초기 설정
python main.py --setup
```

### 2. .env 파일 설정

```bash
# .env 파일 편집
UPBIT_ACCESS_KEY=your_access_key_here
UPBIT_SECRET_KEY=your_secret_key_here
TELEGRAM_TOKEN=your_telegram_bot_token    # 선택사항
TELEGRAM_CHAT_ID=your_chat_id            # 선택사항
```

### 3. 페이퍼 트레이딩 (추천: 처음 시작)

```bash
python main.py --mode paper
```

### 4. 백테스트

```bash
python main.py --mode backtest --days 90
python main.py --mode backtest --market KRW-BTC --days 180
```

### 5. 실거래 (주의!)

```bash
python main.py --mode live
```

### Docker 실행

```bash
docker-compose up -d
docker-compose logs -f apex_bot
```

---

## 설정

`config/settings.py` 또는 `.env` 파일로 모든 설정 변경 가능:

### 거래 설정

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `target_markets` | BTC,ETH 등 10개 | 거래 대상 코인 |
| `primary_timeframe` | 60 (1시간) | 주 타임프레임 (분) |
| `max_positions` | 5 | 최대 동시 포지션 |
| `min_order_amount` | 5,000 KRW | 최소 주문금액 |
| `fee` | 0.05% | 업비트 수수료 |

### 리스크 설정

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `risk_per_trade` | 2% | 거래당 리스크 |
| `kelly_fraction` | 0.5 | Kelly 반분 |
| `atr_stop_multiplier` | 1.5 | ATR 손절 배수 |
| `atr_target_multiplier` | 3.0 | ATR 목표 배수 (RR 2:1) |
| `daily_loss_limit` | 5% | 일일 손실 한도 |
| `total_drawdown_limit` | 10% | 최대 드로다운 |

---

## 전략 목록

| 전략 | 모듈 | 설명 | 가중치 |
|------|------|------|--------|
| MACD Cross | `momentum/macd_cross` | MACD 골든/데드 크로스 | 1.5 |
| RSI Divergence | `momentum/rsi_divergence` | RSI 다이버전스 | 1.0 |
| Supertrend | `momentum/supertrend` | ATR 기반 추세 추종 | 1.1 |
| Bollinger Squeeze | `mean_reversion/bollinger_squeeze` | BB 수축 후 돌파 | 1.0 |
| VWAP Reversion | `mean_reversion/vwap_reversion` | VWAP 이격 역회귀 | 1.2 |
| Vol Breakout | `volatility/vol_breakout` | 변동성 돌파 | 1.3 |
| ATR Channel | `volatility/atr_channel` | ATR 채널 추세 | 1.0 |
| Order Block | `market_structure/order_block` | 스마트머니 오더블록 | 1.5 |

### 커스텀 전략 추가

```python
from strategies.base_strategy import BaseStrategy, StrategySignal, SignalType

class MyStrategy(BaseStrategy):
    NAME = "my_strategy"
    MIN_CANDLES = 50

    def generate_signal(self, df, market, params=None):
        # 신호 로직 구현
        if buy_condition:
            return self._make_signal(
                df, market, SignalType.BUY,
                score=0.8, confidence=0.7, reason="내 전략 매수"
            )
        return None
```

---

## 리스크 관리

### Kelly Criterion (반분)

```
포지션 크기 = 자본 × (승률 - (1-승률)/손익비) × 0.5
```

### 서킷브레이커

| 레벨 | 조건 | 중단 시간 |
|------|------|-----------|
| L1 | 드로다운 ≥ 7% | 24시간 |
| L2 | 드로다운 ≥ 10% | 48시간 |
| L3 | 연속 손실 ≥ 5회 | 24시간 |
| L4 | 일일 손실 ≥ 5% | 당일 종료 |

### 트레일링 스탑

수익 3% 초과 시 자동 활성화:
- 고점 갱신 시 손절선 상향
- 고점 대비 1.5% 이상 하락 시 청산

---

## ML 모델

### 앙상블 구성

| 모델 | 비중 | 특징 |
|------|------|------|
| Bi-LSTM | 30% | 시퀀스 패턴 학습 |
| TFT (Temporal Fusion Transformer) | 40% | 멀티스케일 시계열 |
| CNN-LSTM | 30% | 로컬 패턴 + 시계열 |

### 사용 방법

```bash
# 모델 학습 (첫 실행 시 필요)
python -m models.train.trainer

# 추론만 (학습된 모델 필요)
# main.py 실행 시 자동으로 GPU 로드
```

### 하드웨어 요구사항

| 구분 | 권장 사양 |
|------|-----------|
| GPU | RTX 4060 이상 (CUDA 12+) |
| RAM | 16GB 이상 |
| CPU | 8코어 이상 |
| Storage | SSD 10GB 이상 |

---

## 백테스트

```bash
# 기본 백테스트 (BTC/ETH/XRP, 90일)
python main.py --mode backtest

# 특정 코인 장기 백테스트
python main.py --mode backtest --market KRW-BTC --days 365

# Walk-Forward Analysis 자동 포함
# → 5-fold 시간순 분할 검증
```

### 주요 성과 지표

- 총 수익률 / 연환산 수익률
- Sharpe Ratio / Sortino Ratio
- 최대 드로다운 / MDD 기간
- 승률 / Profit Factor
- 평균 보유기간

---

## 모니터링

### 웹 대시보드

```
http://localhost:8888
```

- 실시간 포트폴리오 현황
- PnL 차트 (일별/월별)
- 전략별 성과 분석
- 리스크 지표 모니터링

### 텔레그램 알림

- 매수/매도 실행 알림
- 일일 성과 리포트
- 손실/드로다운 경고
- 서킷브레이커 발동 알림

### Loguru 로그

```
logs/apex_bot_YYYYMMDD.log
logs/trades_YYYYMMDD.log
logs/errors.log
```

---

## 프로젝트 구조

```
apex_bot/
├── main.py                    # 진입점
├── config/
│   └── settings.py            # 모든 설정 (dataclass)
├── core/
│   ├── engine.py              # 메인 트레이딩 엔진
│   ├── event_bus.py           # 비동기 이벤트 버스
│   ├── state_machine.py       # 봇 상태 관리
│   └── portfolio_manager.py   # 포트폴리오 추적
├── data/
│   ├── collectors/
│   │   ├── ws_collector.py    # WebSocket 실시간 수집
│   │   └── rest_collector.py  # REST API 수집
│   ├── processors/
│   │   ├── candle_processor.py  # 캔들 처리 + 지표
│   │   └── feature_engineer.py  # ML 피처 120개
│   └── storage/
│       ├── db_manager.py      # SQLite/TimescaleDB
│       └── cache_manager.py   # Redis/메모리 캐시
├── strategies/
│   ├── base_strategy.py       # 전략 베이스 클래스
│   ├── momentum/              # MACD, RSI, Supertrend
│   ├── mean_reversion/        # Bollinger, VWAP
│   ├── volatility/            # VolBreakout, ATR
│   ├── market_structure/      # OrderBlock (SMC)
│   └── ml/                    # ML 전략 플러그인
├── signals/
│   ├── signal_combiner.py     # 가중 앙상블
│   └── filters/
│       └── regime_detector.py  # 시장 레짐 감지
├── risk/
│   ├── risk_manager.py        # 서킷브레이커 + 한도
│   ├── position_sizer.py      # Kelly 포지션 사이징
│   └── stop_loss/
│       └── trailing_stop.py   # 트레일링 스탑
├── execution/
│   ├── upbit_adapter.py       # 업비트 API 어댑터
│   ├── executor.py            # 주문 실행 + 재시도
│   └── order_manager.py       # 주문 관리
├── models/
│   ├── architectures/
│   │   ├── lstm_model.py      # Bi-LSTM
│   │   ├── transformer_model.py  # TFT
│   │   └── ensemble.py        # 앙상블 통합
│   ├── train/
│   │   └── trainer.py         # 학습 루프
│   └── inference/
│       └── predictor.py       # GPU 추론
├── backtesting/
│   ├── backtester.py          # 벡터화 백테스트
│   ├── optimizer.py           # Optuna 하이퍼파라미터
│   └── report/
│       └── performance_report.py  # 성과 분석
├── monitoring/
│   ├── dashboard.py           # FastAPI + WebSocket
│   ├── alert_manager.py       # 알림 관리
│   └── telegram_bot.py        # 텔레그램 봇
├── utils/
│   ├── indicators.py          # 순수 Python 지표
│   ├── helpers.py             # 유틸리티
│   └── logger.py              # Loguru 설정
├── tests/
│   ├── test_core.py           # 핵심 모듈 35개 테스트
│   ├── test_strategies.py     # 전략 테스트
│   └── test_backtester.py     # 백테스터 테스트
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 주의사항

> ⚠️ **실거래 전 반드시 페이퍼 트레이딩으로 충분히 검증하세요.**
>
> 이 봇은 금융 조언을 제공하지 않습니다. 모든 투자 결정에 대한 책임은 사용자 본인에게 있습니다.
> 암호화폐 거래는 원금 손실 위험이 있습니다.

---

## 라이선스

MIT License - 자유롭게 사용, 수정, 배포 가능.
