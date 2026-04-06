"""
integrate_orderbook.py
engine.py에 OrderBookAnalyzer + VolumeProfile 연동
"""
import shutil, py_compile, re
from pathlib import Path

p = Path("core/engine.py")
shutil.copy(p, "core/engine.py.bak_ob")
text = p.read_text(encoding="utf-8", errors="ignore")

print("=== 현재 __init__ 필터 초기화 위치 확인 ===")
lines = text.splitlines()
for i, line in enumerate(lines):
    if any(k in line for k in ["kimchi_monitor", "correlation_filter", 
                                 "volume_spike", "fear_greed", "news_sentiment"]):
        if "self." in line and "=" in line:
            print(f"  L{i+1}: {line.strip()}")

print("\n=== _analyze_market 호가창 데이터 수집 위치 확인 ===")
for i, line in enumerate(lines):
    if "get_orderbook" in line or "orderbook" in line.lower():
        print(f"  L{i+1}: {line.strip()}")
