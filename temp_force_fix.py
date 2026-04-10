import re

with open('core/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# v2.1.0 블록 전체에서 self.logger → logger 치환
# 정규식으로 블록 범위 추출
pattern = r'(# ===== v2\.1\.0 시그널 평가 =====.*?# ===============================)'
match = re.search(pattern, content, re.DOTALL)

if match:
    block = match.group(1)
    print(f'✅ v2.1.0 블록 발견 ({len(block)}자)')
    
    # self.logger → logger 치환
    original_count = block.count('self.logger')
    modified_block = block.replace('self.logger', 'logger')
    
    # 치환된 블록으로 교체
    content = content.replace(block, modified_block)
    
    print(f'✅ self.logger → logger 치환: {original_count}개')
    
    # 추가 확인: self.cache_manager, self.portfolio 등도 체크
    if 'self.cache_manager' in modified_block:
        print('✅ self.cache_manager 유지 (정상)')
    if 'self.portfolio' in modified_block:
        print('✅ self.portfolio 유지 (정상)')
    
    with open('core/engine.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('✅ 파일 저장 완료')
else:
    print('❌ v2.1.0 블록을 찾을 수 없음')
    exit(1)
