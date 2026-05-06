from pathlib import Path
import py_compile, shutil
from datetime import datetime

ARCHIVE = Path(f"archive/fx8b_{datetime.now():%Y%m%d_%H%M%S}")
ARCHIVE.mkdir(parents=True, exist_ok=True)

p = Path("core/engine_cycle.py")

# ── 백업에서 원본 복원 ──────────────────────────────────────────
backup = Path("archive/fx8_20260506_202924/engine_cycle.py")
if backup.exists():
    shutil.copy2(backup, p)
    print("원본 복원 완료 (fx8 백업)")
else:
    print("백업 없음 — 현재 파일로 진행")

shutil.copy2(p, ARCHIVE / "engine_cycle.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
s = "".join(lines)

# ── FX8-1: _surge_cache 최초 등록 시각 보존 ──────────────────────
old1 = '                self._surge_cache[_m] = {**_c, "_ts": _now}'
new1 = (
    '                # [FX8-1] 최초 등록 시각 보존\n'
    '                _existing_ts = self._surge_cache.get(_m, {}).get("_ts", _now)\n'
    '                self._surge_cache[_m] = {**_c, "_ts": _existing_ts}\n'
)
if old1 in s and "[FX8-1]" not in s:
    s = s.replace(old1, new1)
    print("OK   FX8-1 _surge_cache _ts 보존")
else:
    print("SKIP FX8-1:", "이미적용" if "[FX8-1]" in s else "패턴없음")

# ── FX8-2: TTL 3600→1800초 ───────────────────────────────────────
old2 = '            _TTL_SEC   = 3600  # 기본 30분'
new2 = '            _TTL_SEC   = 1800  # [FX8-2] 30분(1800초)\n'
if old2 in s and "[FX8-2]" not in s:
    s = s.replace(old2, new2)
    print("OK   FX8-2 TTL 1800초")
else:
    print("SKIP FX8-2:", "이미적용" if "[FX8-2]" in s else "패턴없음")

# ── FX8-3+4: SURGE-TRIGGER 루프 앞에 쿨다운 초기화 + 내부 체크 ───
# 실제 패턴 확인 후 정확히 교체
old3 = (
    '            for _sg_m in list(self._surge_cache.keys()):\n'
)
new3 = (
    '            # [FX8-3] SURGE-TRIGGER 쿨다운 초기화\n'
    '            if not hasattr(self, "_surge_trigger_cd"):\n'
    '                self._surge_trigger_cd = {}\n'
    '            _stg_now = _now\n'
    '            for _sg_m in list(self._surge_cache.keys()):\n'
)

# FX8-4: 루프 내부 트리거 로그 직전에 쿨다운 체크 삽입
# 패턴: SURGE-TRIGGER 로그 줄을 찾아 앞에 guard 삽입
old4 = (
    "                    logger.info(f'[SURGE-TRIGGER] {_sg_m} 즉시 분석 트리거')\n"
    "                    logger.info(f'[PENDING-QUEUE] {_sg_m} 대기열 추가"
)
new4 = (
    "                    # [FX8-4] 3분 쿨다운\n"
    "                    if _stg_now - self._surge_trigger_cd.get(_sg_m, 0) < 180:\n"
    "                        logger.debug(f'[SURGE-TRIGGER] {_sg_m} 쿨다운 스킵')\n"
    "                        continue\n"
    "                    self._surge_trigger_cd[_sg_m] = _stg_now\n"
    "                    logger.info(f'[SURGE-TRIGGER] {_sg_m} 즉시 분석 트리거')\n"
    "                    logger.info(f'[PENDING-QUEUE] {_sg_m} 대기열 추가"
)

if old3 in s and "[FX8-3]" not in s:
    s = s.replace(old3, new3)
    print("OK   FX8-3 쿨다운 초기화")
else:
    print("SKIP FX8-3:", "이미적용" if "[FX8-3]" in s else "패턴없음")

if old4 in s and "[FX8-4]" not in s:
    s = s.replace(old4, new4)
    print("OK   FX8-4 트리거 쿨다운 체크")
else:
    print("SKIP FX8-4:", "이미적용" if "[FX8-4]" in s else "패턴없음")

p.write_text(s, encoding="utf-8")

# ── 컴파일 검증 ──────────────────────────────────────────────────
try:
    py_compile.compile(str(p), doraise=True)
    print("컴파일 OK  core/engine_cycle.py")
except Exception as e:
    print("컴파일 FAIL:", e)
    # 실패 시 자동 복원
    shutil.copy2(backup if backup.exists() else ARCHIVE / "engine_cycle.py", p)
    print("자동 복원 완료")

print("백업:", ARCHIVE)
