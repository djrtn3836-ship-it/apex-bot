import re

with open('core/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# _evaluate_entry_signals 함수 영역 찾기
func_start = content.find('async def _evaluate_entry_signals')
if func_start == -1:
    print('❌ _evaluate_entry_signals 함수를 찾을 수 없습니다')
    exit(1)

# 다음 함수 시작점 찾기 (함수 종료 지점)
next_func = content.find('\n    async def ', func_start + 1)
if next_func == -1:
    next_func = content.find('\n    def ', func_start + 1)

func_body = content[func_start:next_func] if next_func != -1 else content[func_start:]

# return None 패턴 찾기 및 로그 추가
lines = func_body.split('\n')
modified_lines = []
line_num = content[:func_start].count('\n') + 1

for i, line in enumerate(lines):
    modified_lines.append(line)
    
    # return None 직전에 로그 삽입
    if 'return None' in line and '# 🔍 TRACE' not in line:
        indent = len(line) - len(line.lstrip())
        
        # 이전 줄에서 조건문 찾기
        reason = 'unknown'
        for j in range(i-1, max(0, i-5), -1):
            prev_line = lines[j].strip()
            if 'if' in prev_line:
                # 조건문에서 키워드 추출
                if 'df is None' in prev_line or 'len(df)' in prev_line:
                    reason = 'df None or insufficient'
                elif 'atr' in prev_line.lower():
                    reason = 'ATR check failed'
                elif 'volume' in prev_line.lower():
                    reason = 'VolumeProfile check failed'
                elif 'strategy' in prev_line.lower() or 'consensus' in prev_line.lower():
                    reason = 'strategy consensus failed'
                break
        
        log_line = ' ' * indent + f'logger.debug(f"{{market}} 조기 종료: {reason}")  # 🔍 TRACE\n'
        modified_lines.insert(-1, log_line)  # return None 바로 위에 삽입

# 수정된 함수 본문을 원본에 병합
new_func_body = '\n'.join(modified_lines)
new_content = content[:func_start] + new_func_body + content[next_func:] if next_func != -1 else content[:func_start] + new_func_body

with open('core/engine.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('✅ 조기 종료 추적 로그 추가 완료')
print(f'📝 수정된 함수: _evaluate_entry_signals (약 {len(lines)}줄)')
