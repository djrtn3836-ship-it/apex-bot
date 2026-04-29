import os

base = r"C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
targets = {
    "engine_sell.py":  (1, 200),
    "engine_buy.py":   (1100, 1180),
    "engine_db.py":    (55, 110),
    "engine_cycle.py": (425, 470),
}

for fname, (start, end) in targets.items():
    found = False
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ["__pycache__", ".git"]]
        for f in files:
            if f == fname:
                fpath = os.path.join(root, f)
                lines = open(fpath, "r", encoding="utf-8").readlines()
                print(f"\n{'='*60}")
                print(f"[{fname}] L{start}~L{end}  ({fpath})")
                print(f"{'='*60}")
                for i, line in enumerate(lines[start-1:end], start=start):
                    print(f"L{i}: {line.rstrip()}")
                found = True
    if not found:
        print(f"\n[{fname}] 파일 없음")

print("\n완료")
