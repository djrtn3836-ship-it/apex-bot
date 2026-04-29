print("="*60)
print("_execute_buy L1082~L1160 계속")
print("="*60)
with open("core/engine_buy.py", encoding="utf-8") as f:
    lines = f.readlines()
for i, l in enumerate(lines[1081:1160], start=1082):
    print(f"L{i}: {l}", end="")
