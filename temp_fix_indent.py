with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 653줄 찾기
for i in range(len(lines)):
    if '🚨 [TRACE]' in lines[i]:
        # 다음 줄(654줄)의 들여쓰기 확인
        next_indent = len(lines[i+1]) - len(lines[i+1].lstrip())
        # 현재 줄(653줄)을 다음 줄과 같은 들여쓰기로 수정
        lines[i] = ' ' * next_indent + lines[i].lstrip()
        print(f'✅ {i+1}줄 들여쓰기 수정: {next_indent}칸')
        break

with open('core/engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
