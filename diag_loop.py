print("="*60)
print("_analyze_market 호출 루프 (engine_cycle.py L220~L320)")
print("="*60)
with open("core/engine_cycle.py", encoding="utf-8") as f:
    lines = f.readlines()
for i, l in enumerate(lines[219:320], start=220):
    print(f"L{i}: {l}", end="")
