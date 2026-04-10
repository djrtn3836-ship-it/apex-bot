with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

modified = False
for i in range(len(lines)):
    # 664줄: ml_score 키 수정
    if 'ml_score = ml_pred.get(' in lines[i] and 'v2.1.0' in ''.join(lines[max(0,i-20):i]):
        lines[i] = lines[i].replace(
            "ml_pred.get('score', 0)",
            "ml_pred.get('confidence', 0)  # 🔧 수정: score → confidence"
        )
        print(f'✅ {i+1}줄: ml_score 키 수정 (score → confidence)')
        modified = True
    
    # 669줄: 로그 수정 (이스케이프 제거)
    if 'logger.debug(f"{market} ML=' in lines[i] and i > 660 and i < 680:
        # 기존 줄을 완전히 교체
        indent = len(lines[i]) - len(lines[i].lstrip())
        new_line = ' ' * indent + 'logger.debug(f"{market} ML={ml_score:.3f} | 신호={ml_pred.get(\'signal\', \'UNKNOWN\')}")\n'
        # Python에서 작은따옴표 처리
        new_line = ' ' * indent + 'logger.debug(f"{market} ML={ml_score:.3f} | 신호={ml_pred.get(\'signal\')}")\n'.replace("\\'", "'")
        lines[i] = new_line
        print(f'✅ {i+1}줄: 로그 형식 수정')
        modified = True

if modified:
    with open('core/engine.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('✅ 파일 저장 완료')
else:
    print('⚠️ 수정할 내용을 찾지 못함')
