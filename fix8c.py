from pathlib import Path
import py_compile, shutil
from datetime import datetime

ARCHIVE = Path(f"archive/fx8c_{datetime.now():%Y%m%d_%H%M%S}")
ARCHIVE.mkdir(parents=True, exist_ok=True)

p = Path("core/engine_cycle.py")

# 원본 복원 (fx8b 백업 우선, 없으면 fx8 백업)
for bk in [
    "archive/fx8b_20260506_203014/engine_cycle.py",
    "archive/fx8_20260506_202924/engine_cycle.py",
]:
    bp = Path(bk)
    if bp.exists():
        shutil.copy2(bp, p)
        print(f"원본 복원: {bk}")
        break

shutil.copy2(p, ARCHIVE / "engine_cycle.py")
s = p.read_text(encoding="utf-8")

# ── FX8-1: _surge_cache _ts 최초 등록 시각 보존 ──────────────────
old1 = '                self._surge_cache[_m] = {**_c, "_ts": _now}'
new1 = (
    '                # [FX8-1] 최초 등록 시각 보존 — 매 사이클 덮어쓰면 TTL 리셋됨\n'
    '                _existing_ts = self._surge_cache.get(_m, {}).get("_ts", _now)\n'
    '                self._surge_cache[_m] = {**_c, "_ts": _existing_ts}'
)
if old1 in s and "[FX8-1]" not in s:
    s = s.replace(old1, new1)
    print("OK   FX8-1 _ts 보존")
else:
    print("SKIP FX8-1:", "이미적용" if "[FX8-1]" in s else "패턴없음")

# ── FX8-2: TTL 3600→1800초 ───────────────────────────────────────
old2 = '            _TTL_SEC   = 3600  # 기본 30분'
new2 = '            _TTL_SEC   = 1800  # [FX8-2] 30분(1800초) 수정'
if old2 in s and "[FX8-2]" not in s:
    s = s.replace(old2, new2)
    print("OK   FX8-2 TTL→1800초")
else:
    print("SKIP FX8-2:", "이미적용" if "[FX8-2]" in s else "패턴없음")

# ── FX8-3+4: SURGE-TRIGGER 루프 내부 첫 번째 로직에 쿨다운 삽입 ──
# 방식: for 루프는 건드리지 않고, 루프 내부 if 조건 블록 전체를 교체
# 원본 패턴: for 루프 안 첫 번째 if 블록 (surge_score 체크)
old34 = (
    '            for _sg_m in list(self._surge_cache.keys()):\n'
    '                _sg_data  = self._surge_cache[_sg_m]\n'
)
new34 = (
    '            # [FX8-3] SURGE-TRIGGER 3분 쿨다운 초기화\n'
    '            if not hasattr(self, "_surge_trigger_cd"):\n'
    '                self._surge_trigger_cd = {}\n'
    '            for _sg_m in list(self._surge_cache.keys()):\n'
    '                # [FX8-4] 3분(180초) 내 동일 종목 재트리거 차단\n'
    '                if _now - self._surge_trigger_cd.get(_sg_m, 0) < 180:\n'
    '                    logger.debug(f"[SURGE-CD] {_sg_m} 쿨다운 중, 스킵")\n'
    '                    continue\n'
    '                _sg_data  = self._surge_cache[_sg_m]\n'
)
if old34 in s and "[FX8-3]" not in s:
    s = s.replace(old34, new34)
    print("OK   FX8-3+4 SURGE-TRIGGER 쿨다운 삽입")
else:
    # 패턴 못 찾으면 실제 라인 출력
    lines = s.splitlines()
    for i, l in enumerate(lines):
        if '_sg_m' in l and ('for' in l or '_sg_data' in l):
            print(f"  디버그 라인 {i+1}: {repr(l)}")
    print("SKIP FX8-3+4:", "이미적용" if "[FX8-3]" in s else "패턴없음 — 디버그 확인")

p.write_text(s, encoding="utf-8")

try:
    py_compile.compile(str(p), doraise=True)
    print("컴파일 OK  core/engine_cycle.py")
except Exception as e:
    print("컴파일 FAIL:", e)
    # 자동 복원
    for bk in [
        "archive/fx8b_20260506_203014/engine_cycle.py",
        "archive/fx8_20260506_202924/engine_cycle.py",
    ]:
        bp = Path(bk)
        if bp.exists():
            shutil.copy2(bp, p)
            print(f"자동 복원: {bk}")
            break

print("백업:", ARCHIVE)
