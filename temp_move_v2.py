with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# v2.1.0 블록 찾기 및 추출
v210_lines = []
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if '🔎 [v2.1.0] ML 캐시' in line:
        start_idx = i
    if start_idx is not None and '# =============================' in line:
        end_idx = i
        break

if start_idx and end_idx:
    print(f'✅ v2.1.0 블록: {start_idx+1}~{end_idx+1}줄')
    v210_lines = lines[start_idx:end_idx+1]
    
    # 기존 위치에서 삭제
    del lines[start_idx:end_idx+1]
    
    # 651줄 패턴 찾기 (더 유연하게)
    insert_idx = None
    for i in range(len(lines)):
        # 'self._ml_batch_cache = {}' 줄 찾기 (except 블록 안)
        if '_ml_batch_cache = {}' in lines[i] and i > 0:
            # 이전 몇 줄 중에 'except' 있는지 확인
            for j in range(max(0, i-5), i):
                if 'except Exception' in lines[j]:
                    insert_idx = i + 1
                    break
            if insert_idx:
                break
    
    if not insert_idx:
        # 대체 방법: 'logger.debug(f"배치 ML 추론 스킵' 다음 줄
        for i, line in enumerate(lines):
            if '배치 ML 추론 스킵' in line:
                insert_idx = i + 2  # logger 줄 + _ml_batch_cache = {} 줄 다음
                break
    
    if insert_idx:
        print(f'✅ 삽입 위치: {insert_idx+1}줄')
        
        # 빈 줄 추가 후 삽입
        lines.insert(insert_idx, '\n')
        for j, vline in enumerate(v210_lines):
            lines.insert(insert_idx + 1 + j, vline)
        
        with open('core/engine.py', 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        print('✅ v2.1.0 블록 이동 완료')
    else:
        print('❌ 삽입 위치를 찾을 수 없음')
        print('수동 확인 필요:')
        for i in range(max(0, start_idx-10), min(len(lines), start_idx+5)):
            print(f'{i+1}: {lines[i][:60]}...')
        exit(1)
else:
    print('❌ v2.1.0 블록을 찾을 수 없음')
    exit(1)
