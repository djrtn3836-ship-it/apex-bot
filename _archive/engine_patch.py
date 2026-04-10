"""APEX BOT - engine.py    
     engine.py    .

 :
  v2.0.1 - __init__  self._ppo_agent = None  (AttributeError )
          - fear_greed.is_valid() → fear_greed.is_valid (property )
          - fg_adj["block_buy"]   (Extreme Greed 90+    )
          - fg_adj["mode"]"""

# ──────────────────────────────────────────────────────────────
# [패치 1] TradingEngine.__init__() 에 추가
# self._ml_predictor = None 바로 아래에 추가:
# ──────────────────────────────────────────────────────────────
PATCH_1_INIT = """#  FIX: _ppo_agent  AttributeError 
        self._ppo_agent = None   # _init_ppo_agent()"""

# ──────────────────────────────────────────────────────────────
# [패치 2] _scheduled_paper_report() 내 is_valid() 호출 수정
# ──────────────────────────────────────────────────────────────
PATCH_2_BEFORE = "if self.fear_greed.is_valid():"
PATCH_2_AFTER  = "if self.fear_greed.is_valid:"   # ✅ property → 괄호 제거

# ──────────────────────────────────────────────────────────────
# [패치 3] _analyze_market() 공포탐욕 억제 로직 수정
# ──────────────────────────────────────────────────────────────
PATCH_3_BEFORE = """
            fg_adj = self.fear_greed.get_signal_adjustment()
            if ml_pred and fg_adj.get("mode") == "suppressed":
                if ml_pred.get("confidence", 0) < 0.8:
                    logger.debug(...)
                    return
"""

PATCH_3_AFTER  = """#  FIX: block_buy   (Extreme Greed 90+ )
            fg_adj = self.fear_greed.get_signal_adjustment()

            # block_buy   (fg_adj["mode"] == "suppressed"  )
            if fg_adj.get("block_buy", False):
                logger.info(
                    f"   ({market}): "
                    f"={self.fear_greed.index} ({self.fear_greed.label})"
                )
                return

            #       
            if ml_pred and fg_adj.get("mode") == "suppressed":
                if ml_pred.get("confidence", 0) < 0.8:
                    logger.debug(
                        f"  ({market}): "
                        f"={self.fear_greed.index} ({self.fear_greed.label})"
                    )
                    return"""

print("engine_patch.py   — engine.py    ")
print(" auto_apply_engine_patch() 함수를 호출하세요.")


def auto_apply_engine_patch(engine_path: str = "core/engine.py"):
    """engine.py"""
    from pathlib import Path
    p = Path(engine_path)
    if not p.exists():
        print(f"    {engine_path}   — ")
        return

    code = p.read_text(encoding="utf-8")

    # 패치 1: _ppo_agent 미초기화 수정
    target1 = "self._ml_predictor = None"
    if target1 in code and "self._ppo_agent = None" not in code:
        code = code.replace(
            target1,
            target1 + "\n        self._ppo_agent = None   # ✅ FIX: AttributeError 방지"
        )
        print("    1 : _ppo_agent ")
    else:
        print("    1  ")

    # 패치 2: is_valid() → is_valid (property)
    if "self.fear_greed.is_valid():" in code:
        code = code.replace(
            "self.fear_greed.is_valid():",
            "self.fear_greed.is_valid:   # ✅ FIX: property 호출"
        )
        print("    2 : is_valid() → is_valid")
    else:
        print("    2  ")

    # 패치 3: block_buy 체크 추가
    old_fg = 'fg_adj = self.fear_greed.get_signal_adjustment()\n            if ml_pred and fg_adj.get("mode") == "suppressed":'
    new_fg = (
        'fg_adj = self.fear_greed.get_signal_adjustment()\n'
        '            # ✅ FIX: Extreme Greed 90+ 매수 차단\n'
        '            if fg_adj.get("block_buy", False):\n'
        '                logger.info(f"   ({market}): '
        'idx={self.fear_greed.index}")\n'
        '                return\n'
        '            if ml_pred and fg_adj.get("mode") == "suppressed":'
    )
    if old_fg.replace("\n", "\n") in code:
        code = code.replace(old_fg, new_fg)
        print("    3 : block_buy  ")
    else:
        print("    3   (   )")

    p.write_text(code, encoding="utf-8")
    print(f"   {engine_path}  ")


if __name__ == "__main__":
    auto_apply_engine_patch()
