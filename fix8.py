from pathlib import Path
import py_compile, shutil
from datetime import datetime

ARCHIVE = Path(f"archive/fx8_{datetime.now():%Y%m%d_%H%M%S}")
ARCHIVE.mkdir(parents=True, exist_ok=True)

p = Path("core/engine_cycle.py")
shutil.copy2(p, ARCHIVE / "engine_cycle.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
s = "".join(lines)

# ── FX8-1: _surge_cache 최초 등록 시각 보호 ──────────────────────
# 문제: 매 사이클마다 _ts를 갱신해 TTL이 리셋됨
# 수정: 이미 캐시에 존재하면 _ts를 유지(최초 등록 시각 보존)
old1 = '                self._surge_cache[_m] = {**_c, "_ts": _now}'
new1 = (
    '                # [FX8-1] 최초 등록 시각 보존 — 덮어쓰면 TTL 리셋됨\n'
    '                _existing_ts = self._surge_cache.get(_m, {}).get("_ts", _now)\n'
    '                self._surge_cache[_m] = {**_c, "_ts": _existing_ts}'
)
if old1 in s and "[FX8-1]" not in s:
    s = s.replace(old1, new1)
    print("OK   FX8-1 _surge_cache _ts 최초 등록 시각 보존")
else:
    print("SKIP FX8-1:", "이미적용" if "[FX8-1]" in s else "패턴없음")

# ── FX8-2: TTL 주석 오류 수정 (3600초=1시간, 주석은 30분이라고 잘못 표기) ──
old2 = '            _TTL_SEC   = 3600  # 기본 30분'
new2 = '            _TTL_SEC   = 1800  # [FX8-2] 30분(1800초) — 기존 3600은 오기'
if old2 in s and "[FX8-2]" not in s:
    s = s.replace(old2, new2)
    print("OK   FX8-2 TTL 3600→1800초(30분) 수정")
else:
    print("SKIP FX8-2:", "이미적용" if "[FX8-2]" in s else "패턴없음")

# ── FX8-3: SURGE-TRIGGER 중복 방어 쿨다운 ────────────────────────
# 문제: SURGE-TRIGGER가 매 사이클 재발동
# 수정: _surge_trigger_cooldown dict로 마지막 트리거 시각 기록,
#        3분(180초) 이내 동일 종목 재트리거 차단
old3 = '            for _sg_m in list(self._surge_cache.keys()):'
new3 = (
    '            # [FX8-3] SURGE-TRIGGER 중복 방어 (3분 쿨다운)\n'
    '            if not hasattr(self, "_surge_trigger_cd"):\n'
    '                self._surge_trigger_cd = {}\n'
    '            _stg_now = _now  # 재사용\n'
    '            for _sg_m in list(self._surge_cache.keys()):'
)
if old3 in s and "[FX8-3]" not in s:
    s = s.replace(old3, new3)
    print("OK   FX8-3 _surge_trigger_cd 초기화 삽입")
else:
    print("SKIP FX8-3:", "이미적용" if "[FX8-3]" in s else "패턴없음")

# ── FX8-4: SURGE-TRIGGER 내부 조건에 쿨다운 체크 추가 ─────────────
old4 = "                    logger.info(f'[SURGE-TRIGGER] {_sg_m} 즉시 분석 트리거')"
new4 = (
    "                    # [FX8-4] 3분 쿨다운 체크\n"
    "                    _last_tg = self._surge_trigger_cd.get(_sg_m, 0)\n"
    "                    if _stg_now - _last_tg < 180:\n"
    "                        logger.debug(f'[SURGE-TRIGGER] {_sg_m} 쿨다운 중 ({int(_stg_now-_last_tg)}s/180s) 스킵')\n"
    "                        continue  # noqa\n"
    "                    self._surge_trigger_cd[_sg_m] = _stg_now\n"
    "                    logger.info(f'[SURGE-TRIGGER] {_sg_m} 즉시 분석 트리거')"
)
if old4 in s and "[FX8-4]" not in s:
    s = s.replace(old4, new4)
    print("OK   FX8-4 SURGE-TRIGGER 3분 쿨다운 체크 삽입")
else:
    print("SKIP FX8-4:", "이미적용" if "[FX8-4]" in s else "패턴없음")

p.write_text(s, encoding="utf-8")

try:
    py_compile.compile(str(p), doraise=True)
    print("컴파일 OK  core/engine_cycle.py")
except Exception as e:
    print("컴파일 FAIL:", e)

print("백업:", ARCHIVE)
