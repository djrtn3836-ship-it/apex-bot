print("="*60)
print("_execute_buy 전체 구조 (engine_buy.py)")
print("="*60)
with open("core/engine_buy.py", encoding="utf-8") as f:
    lines = f.readlines()

# _execute_buy 함수 시작 위치 찾기
start = 0
for i, l in enumerate(lines):
    if "async def _execute_buy" in l:
        start = i
        print(f"함수 시작: L{i+1}")
        break

# 함수 내용 출력 (최대 80줄)
for i, l in enumerate(lines[start:start+80], start=start+1):
    print(f"L{i}: {l}", end="")
