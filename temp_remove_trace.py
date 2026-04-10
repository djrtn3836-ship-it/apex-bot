with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1655줄의 TRACE 로그 제거
for i, line in enumerate(lines):
    if '🚨 [TRACE] _evaluate_entry_signals' in line:
        lines[i] = ''  # 빈 줄로 교체
        print(f'✅ {i+1}줄 TRACE 로그 제거')
        break

with open('core/engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
