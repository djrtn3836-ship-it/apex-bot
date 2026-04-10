# auto_report.py
import os
from datetime import datetime
from pathlib import Path

def generate_daily_report():
    """docstring"""
    
    # 리포트 저장 폴더
    report_dir = Path("reports/daily")
    report_dir.mkdir(parents=True, exist_ok=True)
    
    # 현재 시각
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")
    date_str = now.strftime("%Y-%m-%d %H:%M")
    
    # 로그 파일 읽기
    log_files = list(Path("logs").glob("apex_bot_*.log"))
    if not log_files:
        print("   ")
        return
    
    latest_log = max(log_files, key=os.path.getmtime)
    
    with open(latest_log, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 최근 12시간 로그만 추출 (약 5000줄)
    recent_lines = lines[-10000:] if len(lines) > 10000 else lines
    
    # ===== 1. 에러 로그 =====
    error_file = report_dir / f"{timestamp}_errors.txt"
    errors = [line for line in recent_lines if 'ERROR' in line or 'Exception' in line or 'Traceback' in line]
    
    with open(error_file, 'w', encoding='utf-8') as f:
        f.write(f"{'='*70}\n")
        f.write(f"🚨 에러 로그 리포트\n")
        f.write(f"📅 생성 시각: {date_str}\n")
        f.write(f"📊 총 에러 수: {len(errors)}건\n")
        f.write(f"{'='*70}\n\n")
        
        if errors:
            for line in errors[-50:]:  # 최근 50개만
                f.write(line)
        else:
            f.write("✅ 에러 없음!\n")
    
    # ===== 2. 매수 로그 =====
    buy_file = report_dir / f"{timestamp}_buy.txt"
    buy_signals = [line for line in recent_lines if '진입 시그널 생성' in line or '체결 완료' in line and 'BUY' in line]
    buy_executions = [line for line in recent_lines if '매수 체결' in line or '포지션 오픈' in line]
    
    with open(buy_file, 'w', encoding='utf-8') as f:
        f.write(f"{'='*70}\n")
        f.write(f"💰 매수 로그 리포트\n")
        f.write(f"📅 생성 시각: {date_str}\n")
        f.write(f"📊 진입 신호: {len(buy_signals)}개 | 체결 완료: {len(buy_executions)}건\n")
        f.write(f"{'='*70}\n\n")
        
        f.write(f"1️⃣ 진입 신호 ({len(buy_signals)}개)\n")
        f.write(f"{'-'*70}\n")
        for line in buy_signals:
            f.write(line)
        
        f.write(f"\n2️⃣ 체결 완료 ({len(buy_executions)}건)\n")
        f.write(f"{'-'*70}\n")
        for line in buy_executions:
            f.write(line)
    
    # ===== 3. 매도 로그 =====
    sell_file = report_dir / f"{timestamp}_sell.txt"
    sell_signals = [line for line in recent_lines if '청산' in line or '손절' in line or '익절' in line or 'SELL' in line]
    
    with open(sell_file, 'w', encoding='utf-8') as f:
        f.write(f"{'='*70}\n")
        f.write(f"📤 매도 로그 리포트\n")
        f.write(f"📅 생성 시각: {date_str}\n")
        f.write(f"📊 매도 신호: {len(sell_signals)}건\n")
        f.write(f"{'='*70}\n\n")
        
        if sell_signals:
            for line in sell_signals:
                f.write(line)
        else:
            f.write("✅ 매도 없음 (포지션 보유 중)\n")
    
    # ===== 4. 필터 차단 로그 =====
    filter_file = report_dir / f"{timestamp}_filters.txt"
    atr_blocks = [line for line in recent_lines if 'ATR 변동성 차단' in line or 'ATR 필터' in line]
    ml_blocks = [line for line in recent_lines if 'ML 점수 낮음' in line or 'ML 신호 약함' in line]
    vp_blocks = [line for line in recent_lines if 'VolumeProfile' in line and '미달' in line]
    
    with open(filter_file, 'w', encoding='utf-8') as f:
        f.write(f"{'='*70}\n")
        f.write(f"🚧 필터 차단 로그\n")
        f.write(f"📅 생성 시각: {date_str}\n")
        f.write(f"{'='*70}\n\n")
        
        f.write(f"• ATR 필터 차단: {len(atr_blocks)}건\n")
        f.write(f"• ML 임계값 차단: {len(ml_blocks)}건\n")
        f.write(f"• VolumeProfile 차단: {len(vp_blocks)}건\n")
        
        total_blocks = len(atr_blocks) + len(ml_blocks) + len(vp_blocks)
        total_signals = len(buy_signals)
        pass_rate = total_signals / (total_signals + total_blocks) * 100 if (total_signals + total_blocks) > 0 else 0
        
        f.write(f"\n📊 필터 통과율: {pass_rate:.1f}%\n")
        f.write(f"   (신호 {total_signals}개 / 총 평가 {total_signals + total_blocks}건)\n")
    
    # ===== 5. 성능 요약 =====
    summary_file = report_dir / f"{timestamp}_summary.txt"
    ml_inferences = [line for line in recent_lines if '배치 ML 추론 완료' in line]
    positions = [line for line in recent_lines if 'PnL' in line or '수익률' in line]
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write(f"{'='*70}\n")
        f.write(f"📊 성능 요약 리포트\n")
        f.write(f"📅 생성 시각: {date_str}\n")
        f.write(f"{'='*70}\n\n")
        
        f.write(f"1️⃣ 시스템 안정성\n")
        f.write(f"{'-'*70}\n")
        f.write(f"• ML 추론 실행: {len(ml_inferences)}회\n")
        f.write(f"• 에러 발생: {len(errors)}건\n")
        f.write(f"• 안정성: {'✅ 양호' if len(errors) == 0 else '⚠️ 점검 필요'}\n\n")
        
        f.write(f"2️⃣ 거래 활동\n")
        f.write(f"{'-'*70}\n")
        f.write(f"• 진입 신호 생성: {len(buy_signals)}개\n")
        f.write(f"• 매수 체결: {len(buy_executions)}건\n")
        f.write(f"• 매도 체결: {len(sell_signals)}건\n")
        f.write(f"• 체결 성공률: {len(buy_executions)/len(buy_signals)*100:.1f}%\n\n" if len(buy_signals) > 0 else "• 체결 성공률: N/A\n\n")
        
        f.write(f"3️⃣ 포지션 현황\n")
        f.write(f"{'-'*70}\n")
        if positions:
            for line in positions[-10:]:  # 최근 10개 포지션
                f.write(line)
        else:
            f.write("• 포지션 없음\n")
        
        f.write(f"\n4️⃣ 다음 점검 항목\n")
        f.write(f"{'-'*70}\n")
        f.write(f"• 대시보드: http://localhost:8888\n")
        f.write(f"• DB 파일: data/apex_bot.db\n")
        f.write(f"• 로그 파일: {latest_log.name}\n")
    
    print(f"   !")
    print(f"   - : {error_file.name}")
    print(f"   - : {buy_file.name}")
    print(f"   - : {sell_file.name}")
    print(f"   - : {filter_file.name}")
    print(f"   - : {summary_file.name}")

if __name__ == "__main__":
    generate_daily_report()
