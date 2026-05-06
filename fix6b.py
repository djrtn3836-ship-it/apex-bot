from pathlib import Path
import py_compile, shutil
from datetime import datetime

ARCHIVE = Path("archive/fx6b_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
ARCHIVE.mkdir(parents=True, exist_ok=True)
p = Path("core/engine_sell.py")
shutil.copy2(p, ARCHIVE / "engine_sell.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
s = "".join(lines)

i1 = 555
old = '"profit_rate": profit_rate,'
fix = '"profit_rate": profit_rate / 100.0,  # [FX6-3],'
if old in lines[i1] and "FX6-3" not in lines[i1]:
    lines[i1] = lines[i1].replace(old, fix)
    print("OK   FX6-3 partial 556줄")
else:
    print("SKIP FX6-3 partial:", repr(lines[i1].strip()))

i2 = 1197
if old in lines[i2] and "FX6-3" not in lines[i2]:
    lines[i2] = lines[i2].replace(old, fix)
    print("OK   FX6-3 sell 1198줄")
else:
    print("SKIP FX6-3 sell:", repr(lines[i2].strip()))

i3 = 1428
cd = (
    "\n"
    "        # [FX6-5] 익절 후 30분 재진입 쿨다운\n"
    "        if profit_rate > 0 and not _is_sl:\n"
    "            if not hasattr(self, '_sl_cooldown'):\n"
    "                self._sl_cooldown = {}\n"
    "            self._sl_cooldown[market] = _dt.datetime.now() + _dt.timedelta(minutes=30)\n"
    "            logger.info('[SELL] 익절쿨다운 %s 30min', market)\n"
    "\n"
)
if "[FX6-5]" in s:
    print("SKIP FX6-5: 이미 적용")
elif "LiveGuard" in lines[i3]:
    lines.insert(i3, cd)
    print("OK   FX6-5 익절 쿨다운 30min")
else:
    print("SKIP FX6-5:", repr(lines[i3].strip()))

p.write_text("".join(lines), encoding="utf-8")

try:
    py_compile.compile("core/engine_sell.py", doraise=True)
    print("컴파일 OK")
except Exception as e:
    print("컴파일 FAIL:", e)

print("백업:", ARCHIVE)
