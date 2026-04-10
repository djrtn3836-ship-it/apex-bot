with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Step 1: 기존 v2.1.0 블록 찾아서 삭제
v210_start = None
v210_end = None

for i, line in enumerate(lines):
    if '🔎 [v2.1.0]' in line or '# ===== v2.1.0 시그널 평가 =====' in line:
        v210_start = i
    if v210_start is not None and '# =============================' in line:
        v210_end = i
        break

if v210_start is not None and v210_end is not None:
    print(f'✅ 기존 v2.1.0 블록 삭제: {v210_start+1}~{v210_end+1}줄')
    del lines[v210_start:v210_end+1]
else:
    print('⚠️ 기존 v2.1.0 블록 없음 (정상)')

# Step 2: 삽입 위치 찾기 (except 블록 안, self._ml_batch_cache = {} 다음)
insert_pos = None
for i in range(len(lines)):
    if '_ml_batch_cache = {}' in lines[i]:
        # 이전 5줄 내에 'except Exception' 있는지 확인
        for j in range(max(0, i-5), i):
            if 'except Exception' in lines[j]:
                insert_pos = i + 1
                break
        if insert_pos:
            break

if not insert_pos:
    print('❌ 삽입 위치를 찾을 수 없음')
    exit(1)

print(f'✅ 삽입 위치: {insert_pos+1}줄')

# Step 3: v2.1.0 블록 새로 작성
base_indent = len(lines[insert_pos-1]) - len(lines[insert_pos-1].lstrip())
indent = ' ' * base_indent

v210_block = f'''
{indent}# ===== v2.1.0 시그널 평가 (ML 배치 캐시 기반) =====
{indent}if self._ml_batch_cache:
{indent}    logger.debug(f"🔍 시그널 평가 시작 ({{len(self._ml_batch_cache)}}개 코인)")
{indent}    for market, ml_pred in self._ml_batch_cache.items():
{indent}        try:
{indent}            # 포지션 중복 확인
{indent}            if self.portfolio.is_position_open(market):
{indent}                logger.debug(f"{{market}} 이미 포지션 보유 - 스킵")
{indent}                continue
{indent}            
{indent}            # OHLCV 데이터 확인
{indent}            df = self.cache_manager.get_ohlcv(market)
{indent}            if df is None or len(df) < 60:
{indent}                logger.debug(f"{{market}} 데이터 부족 ({{len(df) if df is not None else 0}}개) - 스킵")
{indent}                continue
{indent}            
{indent}            # ML 점수 추출
{indent}            ml_score = ml_pred.get('confidence', 0)
{indent}            ml_signal = ml_pred.get('signal', 'UNKNOWN')
{indent}            
{indent}            logger.debug(f"{{market}} ML={{ml_score:.3f}} 신호={{ml_signal}}")
{indent}            
{indent}            # ML 점수 임계값 (0.1 이상)
{indent}            if ml_score > 0.1:
{indent}                logger.info(f"🎯 {{market}} 시그널 평가 시작 (ML={{ml_score:.3f}})")
{indent}                
{indent}                # _evaluate_entry_signals 호출
{indent}                signal = await self._evaluate_entry_signals(market, df, ml_score)
{indent}                
{indent}                if signal and signal.get('action') == 'BUY':
{indent}                    logger.info(f"✅ {{market}} 진입 시그널 확정! ML={{ml_score:.3f}}")
{indent}                    await self._execute_buy(market, signal, df)
{indent}                elif signal is None:
{indent}                    logger.debug(f"{{market}} 필터 차단 (ATR/VolumeProfile/MTF 등)")
{indent}                else:
{indent}                    logger.debug(f"{{market}} 신호 약함 또는 조건 미충족")
{indent}            else:
{indent}                logger.debug(f"{{market}} ML 점수 낮음 ({{ml_score:.3f}} <= 0.1)")
{indent}        
{indent}        except Exception as e:
{indent}            logger.error(f"{{market}} 시그널 평가 오류: {{e}}", exc_info=True)
{indent}else:
{indent}    logger.debug("ML 캐시 비어있음 - 시그널 평가 스킵")
{indent}# ===============================

'''

# Step 4: 삽입
lines.insert(insert_pos, v210_block)

with open('core/engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f'✅ v2.1.0 블록 재작성 완료 ({insert_pos+1}줄에 삽입)')
