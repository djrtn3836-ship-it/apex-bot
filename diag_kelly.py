with open("core/engine_buy.py", encoding="utf-8") as f:
    lines = f.readlines()
print("="*60)
print("Kelly 포지션 산정 구간 (L1148~L1230)")
print("="*60)
for i, l in enumerate(lines[1147:1230], start=1148):
    print(f"L{i}: {l}", end="")
