with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1667줄 근처 찾아서 수정
for i in range(len(lines)):
    if "vp_rr = getattr(self, 'volume_profile', {}).get(market, {}).get('rr', 0)" in lines[i]:
        print(f'✅ {i+1}줄 발견')
        
        # VolumeProfile 객체에서 RR 가져오는 방식으로 수정
        indent = len(lines[i]) - len(lines[i].lstrip())
        lines[i] = ' ' * indent + "# 2. VolumeProfile RR 필터 (v2.1.0)\n"
        lines.insert(i+1, ' ' * indent + "try:\n")
        lines.insert(i+2, ' ' * (indent+4) + "if hasattr(self, 'volume_profile') and hasattr(self.volume_profile, 'calculate'):\n")
        lines.insert(i+3, ' ' * (indent+8) + "vp_result = self.volume_profile.calculate(df)\n")
        lines.insert(i+4, ' ' * (indent+8) + "vp_rr = vp_result.get('rr', 0) if isinstance(vp_result, dict) else 0\n")
        lines.insert(i+5, ' ' * (indent+4) + "else:\n")
        lines.insert(i+6, ' ' * (indent+8) + "vp_rr = 999  # VolumeProfile 없으면 통과\n")
        lines.insert(i+7, ' ' * indent + "except Exception as e:\n")
        lines.insert(i+8, ' ' * (indent+4) + "logger.debug(f'{market} VolumeProfile 계산 실패: {e}')\n")
        lines.insert(i+9, ' ' * (indent+4) + "vp_rr = 999  # 에러 시 통과\n")
        
        # 기존 1668~1670줄 (if vp_rr < 1.0 ...) 유지
        
        with open('core/engine.py', 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        print('✅ VolumeProfile 접근 방식 수정 완료')
        exit(0)

print('❌ VolumeProfile 라인을 찾을 수 없음')
exit(1)
