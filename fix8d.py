from pathlib import Path
import py_compile, shutil
from datetime import datetime

ARCHIVE = Path(f"archive/fx8d_{datetime.now():%Y%m%d_%H%M%S}")
ARCHIVE.mkdir(parents=True, exist_ok=True)

# 가장 깨끗한 원본(fx8b 백업) 복원
bp = Path("archive/fx8b_20260506_203014/engine_cycle.py")
p  = Path("core/engine_cycle.py")
shutil.copy2(bp, p)
shutil.copy2(bp, ARCHIVE / "engine_cycle.py")
print("원본 복원 완료")

lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

# ── FX8-1: 1022번(index 1021) _ts 최초 시각 보존 ─────────────────
# 원본: '                    self._surge_cache[_m] = {**_c, "_ts": _now}\n'
# if _m: 블록 안에 있으므로 들여쓰기 20칸
i1 = 1021
if '_surge_cache[_m]' in lines[i1] and '[FX8-1]' not in lines[i1]:
    lines[i1] = (
        '                    # [FX8-1] 최초 등록 시각 보존\n'
        '                    _existing_ts = self._surge_cache.get(_m, {}).get("_ts", _now)\n'
        '                    self._surge_cache[_m] = {**_c, "_ts": _existing_ts}\n'
    )
    print("OK   FX8-1 _ts 보존 (index 1021)")
else:
    print("SKIP FX8-1:", repr(lines[i1].strip()[:60]))

# ── FX8-2: TTL 변수 수정 (PENDING-QUEUE 블록, 약 207번 라인) ───────
for idx, ln in enumerate(lines):
    if '_TTL_SEC   = 3600' in ln and '[FX8-2]' not in ln:
        lines[idx] = ln.replace(
            '_TTL_SEC   = 3600  # 기본 30분',
            '_TTL_SEC   = 1800  # [FX8-2] 30분(1800초)'
        )
        print(f"OK   FX8-2 TTL→1800초 (line {idx+1})")
        break
else:
    print("SKIP FX8-2: 패턴없음")

# ── FX8-3+4: 1028번(index 1027) for 루프 앞에 쿨다운 초기화 삽입 ──
# 1027: '            for _sg_m in list(self._surge_cache.keys()):\n'
# 1028: '                if _sg_m not in _open_pos_now ...\n'
# 1029: '                    logger.info(f"[SURGE-TRIGGER] ...'
# 1030: '                    _sg_aio.ensure_future(...'
i3 = 1027  # for _sg_m 라인
i4 = 1029  # logger.info SURGE-TRIGGER 라인

if 'for _sg_m in list(self._surge_cache' in lines[i3] and '[FX8-3]' not in lines[i3]:
    # for 루프 앞에 쿨다운 dict 초기화 삽입
    cd_init = (
        '            # [FX8-3] SURGE-TRIGGER 3분 쿨다운\n'
        '            if not hasattr(self, "_surge_trigger_cd"):\n'
        '                self._surge_trigger_cd = {}\n'
    )
    lines.insert(i3, cd_init)
    print("OK   FX8-3 쿨다운 초기화 삽입 (index 1027 앞)")
    # 삽입 후 인덱스 +1 밀림
    i4 += 1
else:
    print("SKIP FX8-3:", repr(lines[i3].strip()[:60]))

# logger.info SURGE-TRIGGER 라인 앞에 쿨다운 체크 삽입
if '[SURGE-TRIGGER]' in lines[i4] and '[FX8-4]' not in lines[i4]:
    cd_check = (
        '                    # [FX8-4] 3분 쿨다운 체크\n'
        '                    if _now - self._surge_trigger_cd.get(_sg_m, 0) < 180:\n'
        '                        logger.debug(f"[SURGE-CD] {_sg_m} 스킵")\n'
        '                        continue\n'
        '                    self._surge_trigger_cd[_sg_m] = _now\n'
    )
    lines.insert(i4, cd_check)
    print("OK   FX8-4 트리거 쿨다운 체크 삽입")
else:
    print("SKIP FX8-4:", repr(lines[i4].strip()[:60]))

p.write_text("".join(lines), encoding="utf-8")

try:
    py_compile.compile(str(p), doraise=True)
    print("컴파일 OK  core/engine_cycle.py")
except Exception as e:
    print("컴파일 FAIL:", e)
    shutil.copy2(bp, p)
    print("자동 복원 완료")

print("백업:", ARCHIVE)
