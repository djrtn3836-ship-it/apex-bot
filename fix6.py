import re, shutil, py_compile
from datetime import datetime
from pathlib import Path

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
ARCHIVE = Path("archive/fx6_" + TIMESTAMP)
ARCHIVE.mkdir(parents=True, exist_ok=True)
results = []

def backup(path):
    src = Path(path)
    if src.exists():
        dst = ARCHIVE / Path(path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

# FX6-1: OrderBlock regime 파라미터 추가
p1 = Path("strategies/v2/order_block_v2.py")
if p1.exists():
    backup("strategies/v2/order_block_v2.py")
    s1 = p1.read_text(encoding="utf-8")
    old1 = "def generate_signal(self, df: pd.DataFrame, market: str = \"\") -> Optional[Signal]:"
    new1 = "def generate_signal(self, df: pd.DataFrame, market: str = \"\", regime: str = \"\") -> Optional[Signal]:"
    if old1 in s1:
        p1.write_text(s1.replace(old1, new1), encoding="utf-8")
        results.append("OK   FX6-1: OrderBlock regime 파라미터")
    else:
        results.append("SKIP FX6-1: 패턴 없음 (시그니처 확인 필요)")
else:
    results.append("SKIP FX6-1: 파일 없음")

# FX6-2: PPO shape mismatch 방어
p2 = Path("models/rl/ppo_agent.py")
if p2.exists():
    backup("models/rl/ppo_agent.py")
    s2 = p2.read_text(encoding="utf-8")
    old2 = "        try:\n            action, _states = self._model.predict("
    guard = (
        "        # [FX6-2] shape mismatch 방어\n"
        "        if self._model is not None:\n"
        "            _exp = self._model.observation_space.shape[0]\n"
        "            _act = int(state.reshape(-1).shape[0])\n"
        "            if _act != _exp:\n"
        "                logger.debug(\"PPO shape %d != %d skip\", _act, _exp)\n"
        "                return None, 0.0\n"
    )
    if "[FX6-2]" in s2:
        results.append("SKIP FX6-2: 이미 적용")
    elif old2 in s2:
        p2.write_text(s2.replace(old2, guard + old2), encoding="utf-8")
        results.append("OK   FX6-2: PPO shape guard")
    else:
        results.append("SKIP FX6-2: 위치 없음")
else:
    results.append("SKIP FX6-2: 파일 없음")

# FX6-3: profit_rate /100 수정 (engine_sell.py)
p3 = Path("core/engine_sell.py")
if p3.exists():
    backup("core/engine_sell.py")
    s3 = p3.read_text(encoding="utf-8")
    old3a = "                \"profit_rate\": profit_rate,\n                \"strategy\":    _strat,"
    new3a = "                \"profit_rate\": profit_rate / 100.0,  # [FX6-3]\n                \"strategy\":    _strat,"
    old3b = "                \"profit_rate\": profit_rate,\n                \"strategy\":    _strat_name,"
    new3b = "                \"profit_rate\": profit_rate / 100.0,  # [FX6-3]\n                \"strategy\":    _strat_name,"
    n3a = s3.count(old3a)
    n3b = s3.count(old3b)
    s3 = s3.replace(old3a, new3a, 1).replace(old3b, new3b, 1)
    p3.write_text(s3, encoding="utf-8")
    results.append("OK   FX6-3: profit_rate /100 partial=" + str(n3a) + " sell=" + str(n3b))
else:
    results.append("SKIP FX6-3: 파일 없음")

# FX6-5: 익절 후 30분 쿨다운 (engine_sell.py)
p5 = Path("core/engine_sell.py")
if p5.exists():
    s5 = p5.read_text(encoding="utf-8")
    cd = (
        "\n        # [FX6-5] 익절 후 30분 재진입 쿨다운\n"
        "        if profit_rate > 0 and not _is_sl:\n"
        "            if not hasattr(self, \"_sl_cooldown\"):\n"
        "                self._sl_cooldown = {}\n"
        "            self._sl_cooldown[market] = _dt.datetime.now() + _dt.timedelta(minutes=30)\n"
        "            logger.info(\"[SELL] 익절쿨다운 %s 30min\", market)\n"
        "\n"
    )
    t5 = "        # \u2500\u2500 LiveGuard \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    if "[FX6-5]" in s5:
        results.append("SKIP FX6-5: 이미 적용")
    elif t5 in s5:
        p5.write_text(s5.replace(t5, cd + t5), encoding="utf-8")
        results.append("OK   FX6-5: 익절 쿨다운 30min")
    else:
        results.append("SKIP FX6-5: 위치 없음")
else:
    results.append("SKIP FX6-5: 파일 없음")

print(chr(10).join(results))
print(chr(10) + "--- 컴파일 검증 ---")
for f in ["strategies/v2/order_block_v2.py", "models/rl/ppo_agent.py", "core/engine_sell.py"]:
    if Path(f).exists():
        try:
            py_compile.compile(f, doraise=True)
            print("OK   " + f)
        except Exception as e:
            print("FAIL " + f + ": " + str(e))
    else:
        print("SKIP " + f)
print("백업:", ARCHIVE)