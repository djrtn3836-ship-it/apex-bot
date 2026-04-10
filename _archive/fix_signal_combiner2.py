# fix_signal_combiner2.py
import shutil

shutil.copy('signals/signal_combiner.py', 'signals/signal_combiner.py.bak_sc2')

with open('signals/signal_combiner.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"  : {len(lines)}")

# ── 수정 1: L48~L63 STRATEGY_WEIGHTS 교체 (index 47~62) ──────────────
new_weights = \
'''    STRATEGY_WEIGHTS = {
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
        "OrderBlock_SMC":    1.8,
        # ── ML/AI 레이어 ─────────────────────────────
        "ML_Ensemble":       2.5,
        "BEAR_REVERSAL":     2.0,
    }
'''

# L48=index47 ~ L63=index62 교체
new_lines = lines[:47] + [new_weights] + lines[63:]
lines = new_lines
print(f"  1: STRATEGY_WEIGHTS L48~L63   (  )")

# ── 수정 2: L120 ML confidence 임계값 0.35→0.50 상향 (index 119) ─────
# buy_threshold가 1.50으로 올라갔으므로 ML 단독 기여값도 재조정
# ML_Ensemble(2.5) × 0.50 = 1.25 → 전략 없이는 임계값 1.50 미달
# 전략 1개(최소 1.0) + ML(1.25) = 2.25 → 임계값 통과
for i, line in enumerate(lines):
    if 'ml_confidence > 0.35' in line:
        lines[i] = line.replace(
            'ml_confidence > 0.35',
            'ml_confidence > 0.50  # 상향: 0.35→0.50 (ML 단독 매수 방지)'
        )
        print(f"  2: L{i+1} ML confidence  0.35→0.50 ")
        break

# ── 수정 3: BUY agreement_rate 필터 복원 (L136~L137 주석 해제) ────────
for i, line in enumerate(lines):
    if '# if agreement_rate < self.min_agreement:' in line:
        lines[i] = line.replace(
            '# if agreement_rate < self.min_agreement:',
            'if agreement_rate < self.min_agreement:'
        )
        print(f"  3a: L{i+1} BUY agreement_rate   ")
    if '#     return None' in line and i > 130 and i < 145:
        lines[i] = line.replace(
            '#     return None',
            '    return None  # BUY 동의율 미달 → HOLD'
        )
        print(f"  3b: L{i+1} BUY return None  ")

# ── 수정 4: L164 SELL pass 제거 + min_agreement 필터 삽입 ─────────────
for i, line in enumerate(lines):
    stripped = line.strip()
    # "pass  # ..." 패턴 탐지 (L164, index 163)
    if stripped.startswith('pass') and i > 158 and i < 170:
        indent = len(line) - len(line.lstrip())
        ind = ' ' * indent
        new_sell_filter = (
            f"{ind}# SELL 신호 품질 검증 (min_agreement 필터)\n"
            f"{ind}if agreement_rate < self.min_agreement and not (\n"
            f"{ind}    ml_signal == 'SELL' and ml_confidence > 0.55\n"
            f"{ind}):\n"
            f"{ind}    return None  # SELL 동의율 미달 + ML SELL 미확인 → HOLD\n"
        )
        lines[i] = new_sell_filter
        print(f"  4: L{i+1} SELL pass → min_agreement  ")
        break

with open('signals/signal_combiner.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# ── 최종 검증 ──────────────────────────────────────────────────────────
with open('signals/signal_combiner.py', 'r', encoding='utf-8') as f:
    final = f.read()

print("\n   ")
ob_count = final.count('"OrderBlock_SMC"')
print(f"OrderBlock_SMC  : {ob_count} (=1, REGIME_PREFERRED  2→1)")
print(f"fear_greed :    {' ' if '\"fear_greed\"'    in final else ' '}")
print(f"news_sentiment : {' ' if '\"news_sentiment\"' in final else ' '}")
print(f" pass :      {' ' if 'pass  #' in final else ' '}")
print(f"ml_confidence>0.35:  {' ' if '> 0.35' in final else ' '}")
print(f"agreement_rate : {' ' if 'if agreement_rate < self.min_agreement' in final else ' '}")
print("\n fix_signal_combiner2.py ")
