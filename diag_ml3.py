import pathlib, glob

ROOT = pathlib.Path(".")

# ══════════════════════════════════════════════════════════════
# TASK 1: global_regime 관련 파일 찾기
# ══════════════════════════════════════════════════════════════
print("=" * 60)
print("TASK 1: global_regime / get_policy 파일 찾기")
print("=" * 60)
for f in ROOT.rglob("*.py"):
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
        if "get_policy" in text or "GlobalRegime" in text:
            lines = text.splitlines()
            hits = [i+1 for i, l in enumerate(lines) if "get_policy" in l or "min_ml_score" in l]
            if hits:
                print(f"\n  📄 {f} ({len(lines)}줄)")
                for lineno in hits[:10]:
                    print(f"     L{lineno}: {lines[lineno-1].rstrip()}")
    except:
        pass

# ══════════════════════════════════════════════════════════════
# TASK 2: _get_ml_prediction / _get_ppo_prediction 찾기
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TASK 2: _get_ml_prediction / _get_ppo_prediction 찾기")
print("=" * 60)
for f in ROOT.rglob("*.py"):
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
        if "_get_ml_prediction" in text or "_get_ppo_prediction" in text:
            lines = text.splitlines()
            in_func = False
            for i, line in enumerate(lines, 1):
                if "def _get_ml_prediction" in line or "def _get_ppo_prediction" in line:
                    in_func = True
                    print(f"\n  📄 {f}")
                if in_func:
                    print(f"     L{i}: {line.rstrip()}")
                if in_func and i > 5 and line.strip().startswith("def ") and \
                   "_get_ml" not in line and "_get_ppo" not in line:
                    in_func = False
                if in_func and i > 1200:
                    break
    except:
        pass