# diag_phase3.py
import os

base = os.path.dirname(os.path.abspath(__file__))

targets = [
    os.path.join("core", "engine_buy.py"),
    os.path.join("core", "engine_db.py"),
    os.path.join("core", "portfolio_manager.py"),
]

keywords = [
    "amount", "krw", "position_size", "invest",
    "20000", "base_amount", "kelly", "size",
    "budget", "capital", "trade_amount", "order_amount",
    "max_trade", "per_trade", "trade_size"
]

for fpath in targets:
    if not os.path.exists(fpath):
        print(f"없음: {fpath}")
        continue
    lines = open(fpath, "r", encoding="utf-8").readlines()
    matches = []
    for i, line in enumerate(lines, start=1):
        if any(k in line for k in keywords):
            matches.append((i, line.rstrip()))
    print(f"\n=== {fpath} ({len(lines)}줄) ===")
    for i, line in matches:
        print(f"L{i}: {line}")
