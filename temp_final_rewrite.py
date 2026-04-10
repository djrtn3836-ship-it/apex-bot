with open('core/engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 기존 v2.1.0 블록 삭제
v210_start = None
v210_end = None
for i, line in enumerate(lines):
    if '# ===== v2.1.0' in line:
        v210_start = i
    if v210_start is not None and '# =============================' in line:
        v210_end = i
        break

if v210_start and v210_end:
    print(f'✅ 기존 블록 삭제: {v210_start+1}~{v210_end+1}줄')
    del lines[v210_start:v210_end+1]

# 삽입 위치: 651줄(self._ml_batch_cache = {}) 다음
insert_pos = None
for i in range(len(lines)):
    if '_ml_batch_cache = {}' in lines[i] and i > 0 and 'except' in lines[i-2]:
        insert_pos = i + 1
        break

if not insert_pos:
    print('❌ 삽입 위치를 찾을 수 없음')
    exit(1)

print(f'✅ 삽입 위치: {insert_pos+1}줄')

# v2.1.0 블록 새로 작성 (8칸 들여쓰기)
v210_new = '''
        # ===== v2.1.0 시그널 평가 (ML 배치 캐시 기반) =====
        if self._ml_batch_cache:
            logger.debug(f"🔍 시그널 평가 시작 ({len(self._ml_batch_cache)}개 코인)")
            for market, ml_pred in self._ml_batch_cache.items():
                try:
                    if self.portfolio.is_position_open(market):
                        logger.debug(f"{market} 이미 포지션 보유 - 스킵")
                        continue
                    df = self.cache_manager.get_ohlcv(market)
                    if df is None or len(df) < 60:
                        logger.debug(f"{market} 데이터 부족 ({len(df) if df is not None else 0}개)")
                        continue
                    ml_score = ml_pred.get('confidence', 0)
                    ml_signal = ml_pred.get('signal', 'UNKNOWN')
                    logger.debug(f"{market} ML={ml_score:.3f} 신호={ml_signal}")
                    if ml_score > 0.1:
                        logger.info(f"🎯 {market} 시그널 평가 시작 (ML={ml_score:.3f})")
                        signal = await self._evaluate_entry_signals(market, df, ml_score)
                        if signal and signal.get('action') == 'BUY':
                            logger.info(f"✅ {market} 진입 시그널 확정! ML={ml_score:.3f}")
                            await self._execute_buy(market, signal, df)
                        elif signal is None:
                            logger.debug(f"{market} 필터 차단")
                    else:
                        logger.debug(f"{market} ML 점수 낮음 ({ml_score:.3f})")
                except Exception as e:
                    logger.error(f"{market} 시그널 평가 오류: {e}", exc_info=True)
        else:
            logger.debug("ML 캐시 비어있음 - 시그널 평가 스킵")
        # ===============================

'''

lines.insert(insert_pos, v210_new)

with open('core/engine.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('✅ v2.1.0 블록 재작성 완료')
