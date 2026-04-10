with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1658줄 찾아서 수정
for i in range(len(lines)):
    if "atr = df['atr'].iloc[-1] if 'atr' in df.columns else 0" in lines[i]:
        print(f'✅ 1658줄 발견: {i+1}줄')
        
        # 기존 1658~1660줄 삭제
        del lines[i:i+3]
        
        # 새 ATR 계산 로직 삽입
        indent = '            '
        new_code = f'''{indent}# 1. ATR 변동성 필터 (v2.1.0) - 자동 계산 추가
{indent}if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]):
{indent}    atr = df['atr'].iloc[-1]
{indent}else:
{indent}    # ATR 없으면 고가-저가 범위의 2% 추정
{indent}    if 'high' in df.columns and 'low' in df.columns:
{indent}        recent_range = (df['high'].iloc[-14:].mean() - df['low'].iloc[-14:].mean())
{indent}        atr = recent_range
{indent}        logger.debug(f"{{market}} ATR 컬럼 없음 → 수동 계산: {{atr:.2f}}")
{indent}    else:
{indent}        atr = df['close'].iloc[-1] * 0.02  # 폴백: 현재가의 2%
{indent}        logger.debug(f"{{market}} ATR 폴백: 현재가의 2%")
{indent}
{indent}price = df['close'].iloc[-1]
{indent}volatility = (atr / price) * 100 if price > 0 else 0
'''
        lines.insert(i, new_code)
        
        with open('core/engine.py', 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        print('✅ ATR 자동 계산 로직 추가 완료')
        exit(0)

print('❌ 1658줄을 찾을 수 없음')
exit(1)
