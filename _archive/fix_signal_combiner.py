# fix_signal_combiner.py
import shutil

shutil.copy('signals/signal_combiner.py', 'signals/signal_combiner.py.bak_sc')
print("백업 완료: signals/signal_combiner.py.bak_sc")

with open('signals/signal_combiner.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ── 수정 1: STRATEGY_WEIGHTS 중복/불필요 항목 정리 ──────────────────
old_weights = '''    STRATEGY_WEIGHTS = {
        "MACD_Cross":        1.5,
        "RSI_Divergence":    1.0,
        "Supertrend":        1.1,
        "Bollinger_Squeeze": 1.0,
        "VWAP_Reversion":    1.2,
        "VolBreakout":       1.3,
        "ATR_Channel":       1.0,
        "OrderBlock_SMC":    1.5,
        "ML_Ensemble":       2.5,
        # ?? Layer 2 異붽? ?꾨왂 媛以묒튂 ????????????????????
        "BEAR_REVERSAL":     2.0,
        "OrderBlock_SMC":    1.8,
        "fear_greed":        0.8,
        "news_sentiment":    0.7,
    }'''

new_weights = '''    STRATEGY_WEIGHTS = {
        # ── 모멘텀 전략 ──────────────────────────────
        "MACD_Cross":        1.5,
        "RSI_Divergence":    1.0,
        "Supertrend":        1.1,
        # ── 평균회귀 전략 ─────────────────────────────
        "Bollinger_Squeeze": 1.0,
        "VWAP_Reversion":    1.2,
        # ── 변동성 전략 ──────────────────────────────
        "VolBreakout":       1.3,
        "ATR_Channel":       1.0,
        # ── 시장구조 전략 ─────────────────────────────
        "OrderBlock_SMC":    1.8,   # 중복 제거: 1.5→1.8 단일값
        # ── ML/AI 레이어 ─────────────────────────────
        "ML_Ensemble":       2.5,
        "BEAR_REVERSAL":     2.0,
    }'''

if old_weights in content:
    content = content.replace(old_weights, new_weights, 1)
    print("✅ 수정 1: STRATEGY_WEIGHTS 중복/불필요 항목 정리 완료")
else:
    print("⚠️  수정 1: 패턴 미발견 - 수동 확인 필요")

# ── 수정 2: buy_signal_threshold 적정값으로 조정 ─────────────────────
# ML_Ensemble(2.5) × confidence(0.45) = 1.125
# 전략 1개(1.0~1.5) + ML(1.125) = 2.125~2.625
# 의미있는 임계값: 최소 전략 1개 + ML 동의 필요 → 1.5 권장
old_threshold = 'buy_signal_threshold: float = 0.20  # 완화: 1.2→0.20'
new_threshold = 'buy_signal_threshold: float = 1.50  # 복원: ML+전략1개 동의 필요'

with open('config/settings.py', 'r', encoding='utf-8') as f:
    settings_content = f.read()

if old_threshold in settings_content:
    settings_content = settings_content.replace(old_threshold, new_threshold, 1)
    print("✅ 수정 2: buy_signal_threshold 0.20 → 1.50 조정 완료")
    print("   (ML_Ensemble 단독으로는 매수 불가, 최소 전략 1개 동의 필요)")
else:
    print("⚠️  수정 2: threshold 패턴 미발견 - 수동 확인 필요")

with open('config/settings.py', 'w', encoding='utf-8') as f:
    f.write(settings_content)

# ── 수정 3: SELL 신호 min_agreement 필터 복원 ────────────────────────
old_sell = '''        elif net_score <= self.sell_threshold:
            n_sell         = len(sell_strategies)
            agreement_rate = n_sell / max(total_strategies, 1)
            pass  # ?숈쓽??泥댄겕 鍮꾪솢?깊솕'''

new_sell = '''        elif net_score <= self.sell_threshold:
            n_sell         = len(sell_strategies)
            agreement_rate = n_sell / max(total_strategies, 1)
            # SELL 신호 품질 검증 복원 (min_agreement 필터 활성화)
            if agreement_rate < self.min_agreement and not (
                ml_signal == "SELL" and ml_confidence > 0.55
            ):
                return None  # 동의율 미달 + ML SELL 미확인 → HOLD 유지'''

if old_sell in content:
    content = content.replace(old_sell, new_sell, 1)
    print("✅ 수정 3: SELL 신호 min_agreement 필터 복원 완료")
    print("   (agreement_rate < 0.20 이고 ML SELL 미확인 시 HOLD 유지)")
else:
    print("⚠️  수정 3: SELL 패턴 미발견 - 수동 확인 필요")

with open('signals/signal_combiner.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n── 최종 검증 ─────────────────────────────────────────")
with open('signals/signal_combiner.py', 'r', encoding='utf-8') as f:
    final = f.read()
weight_count = final.count('"OrderBlock_SMC"')
print(f"OrderBlock_SMC 키 개수: {weight_count}개 (정상=1)")
print(f"fear_greed 잔존 여부: {'있음 ⚠️' if '\"fear_greed\"' in final else '없음 ✅'}")
print(f"news_sentiment 잔존 여부: {'있음 ⚠️' if '\"news_sentiment\"' in final else '없음 ✅'}")

with open('config/settings.py', 'r', encoding='utf-8') as f:
    s = f.read()
import re
m = re.search(r'buy_signal_threshold.*', s)
print(f"buy_signal_threshold: {m.group() if m else '미발견'}")
print("\n✅ fix_signal_combiner.py 완료")
