# fix_scheduler_name.py
from pathlib import Path
import shutil, py_compile

p = Path("core/engine.py")
shutil.copy(p, "core/engine.py.bak_sched2")
text = p.read_text(encoding="utf-8", errors="ignore")

# hourly 스케줄 블록에서만 scheduler → self.scheduler 수정
OLD = (
    "            # 1시간 텔레그램 자동 현황 요약\n"
    "            scheduler.add_job(\n"
    "                self.telegram.send_hourly_summary,\n"
    "                'interval', hours=1,\n"
    "                id='hourly_telegram_summary',\n"
    "                name='1시간 텔레그램 요약',\n"
    "                misfire_grace_time=60\n"
    "            )"
)
NEW = (
    "            # 1시간 텔레그램 자동 현황 요약\n"
    "            self.scheduler.add_job(\n"
    "                self.telegram.send_hourly_summary,\n"
    "                'interval', hours=1,\n"
    "                id='hourly_telegram_summary',\n"
    "                name='1시간 텔레그램 요약',\n"
    "                misfire_grace_time=60\n"
    "            )"
)

if OLD in text:
    text = text.replace(OLD, NEW, 1)
    print("✅ scheduler → self.scheduler 수정 완료")
else:
    # fallback: 전체에서 hourly 블록 주변 scheduler. 패턴 교체
    import re
    # hourly_telegram_summary 근처의 standalone scheduler.add_job 만 교체
    lines = text.splitlines()
    fixed = False
    for i, ln in enumerate(lines):
        if "hourly_telegram_summary" in ln:
            # 앞뒤 10줄에서 scheduler.add_job → self.scheduler.add_job
            for j in range(max(0,i-5), min(len(lines),i+5)):
                if "scheduler.add_job" in lines[j] and "self.scheduler" not in lines[j]:
                    lines[j] = lines[j].replace("scheduler.add_job", "self.scheduler.add_job")
                    fixed = True
            break
    if fixed:
        text = "\n".join(lines)
        print("✅ fallback: scheduler → self.scheduler 수정 완료")
    else:
        print("⚠️ 패턴을 찾지 못했습니다 – 현재 hourly 블록:")
        for i, ln in enumerate(lines):
            if "hourly" in ln:
                for j in range(max(0,i-3), min(len(lines),i+6)):
                    print(f"  L{j+1}: {lines[j]}")
                break

p.write_text(text, encoding="utf-8")
try:
    py_compile.compile(str(p), doraise=True)
    print("✅ engine.py 문법 OK")
    print("   다음: python start_paper.py")
except py_compile.PyCompileError as e:
    print(f"❌ 문법 오류: {e}")
    shutil.copy("core/engine.py.bak_sched2", p)
    print("🔄 원본 복구")
