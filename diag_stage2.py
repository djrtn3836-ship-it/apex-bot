import py_compile

print("="*60)
print("Stage2 진입 조건 확인 (engine_cycle.py L800~L870)")
print("="*60)
with open("core/engine_cycle.py", encoding="utf-8") as f:
    lines = f.readlines()
for i, l in enumerate(lines[799:870], start=800):
    print(f"L{i}: {l}", end="")

print()
print("="*60)
print("_analyze_market 호출 위치 (engine_cycle.py 전체 검색)")
print("="*60)
for i, l in enumerate(lines, start=1):
    if "_analyze_market" in l or "surge_cache" in l.lower() or "_surge_cache" in l:
        print(f"L{i}: {l}", end="")
