import pathlib

ROOT = pathlib.Path(".")

# ══════════════════════════════════════════════════════════════
# TASK 1: engine_buy.py ML 조건 관련 전체 라인
# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("TASK 1: engine_buy.py ML 조건 관련 라인")
print("=" * 60)
p = pathlib.Path("core/engine_buy.py")
lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"총 {len(lines)}줄")
for i, line in enumerate(lines, 1):
    if any(k in line for k in [
        "ml_pred", "ML_", "ml_score", "confidence",
        "MIN_CONFIDENCE", "HOLD", "BUY", "PPO",
        "ppo", "_evaluate_entry", "allow_normal_buy",
        "allow_surge_buy", "min_ml_score", "ML+PPO"
    ]):
        print(f"  L{i}: {line.rstrip()}")

# ══════════════════════════════════════════════════════════════
# TASK 2: engine_buy.py _evaluate_entry_signals 전체
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 2: _evaluate_entry_signals 전체")
print("=" * 60)
in_func = False
for i, line in enumerate(lines, 1):
    if "def _evaluate_entry_signals" in line:
        in_func = True
    if in_func:
        print(f"  L{i}: {line.rstrip()}")
    if in_func and i > 5 and line.strip().startswith("async def ") and "evaluate" not in line:
        break
    if in_func and i > 950:
        break

# ══════════════════════════════════════════════════════════════
# TASK 3: engine_buy.py ML 추론 호출 위치
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 3: ML 추론 호출 위치 (predictor.predict)")
print("=" * 60)
for i, line in enumerate(lines, 1):
    if any(k in line for k in [
        "predictor.predict", "self.predictor",
        "ml_pred =", "ml_result", "PPOTrainer",
        "ppo_action", "ppo_pred"
    ]):
        # 앞뒤 2줄 포함
        start = max(0, i-3)
        end = min(len(lines), i+3)
        for j in range(start, end):
            print(f"  L{j+1}: {lines[j].rstrip()}")
        print("  ---")

# ══════════════════════════════════════════════════════════════
# TASK 4: config/settings.py ML 관련 설정값
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 4: settings.py ML/전략 관련 설정")
print("=" * 60)
p4 = pathlib.Path("config/settings.py")
lines4 = p4.read_text(encoding="utf-8", errors="replace").splitlines()
for i, line in enumerate(lines4, 1):
    if any(k in line for k in [
        "ml_", "ML_", "confidence", "min_", "strategy",
        "allow_", "ppo", "PPO", "surge", "SURGE",
        "position", "ratio", "score"
    ]):
        print(f"  L{i}: {line.rstrip()}")

# ══════════════════════════════════════════════════════════════
# TASK 5: GlobalRegimeDetector policy 구조
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 5: GlobalRegimeDetector get_policy()")
print("=" * 60)
for fname in ["core/engine_buy.py", "core/engine_cycle.py",
              "signals/filters/regime_detector.py",
              "core/global_regime.py"]:
    fp = pathlib.Path(fname)
    if not fp.exists():
        continue
    txt = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(txt, 1):
        if "get_policy" in line or "allow_normal_buy" in line or \
           "allow_surge_buy" in line or "min_ml_score" in line:
            start = max(0, i-1)
            end = min(len(txt), i+3)
            for j in range(start, end):
                print(f"  [{fname}] L{j+1}: {txt[j].rstrip()}")
            print("  ---")