"""
APEX BOT - engine.py 핵심 버그 패치 코드
이 파일은 직접 사용하지 않고 engine.py에 적용할 패치 내용을 보여줍니다.

수정 이력:
  v2.0.1 - __init__ 에서 self._ppo_agent = None 명시 (AttributeError 방지)
          - fear_greed.is_valid() → fear_greed.is_valid (property 호출)
          - fg_adj["block_buy"] 체크 추가 (Extreme Greed 90+ 매수 차단 완전 동작)
          - fg_adj["mode"] 체크 완전 동작
"""

# ──────────────────────────────────────────────────────────────
# [패치 1] TradingEngine.__init__() 에 추가
# self._ml_predictor = None 바로 아래에 추가:
# ──────────────────────────────────────────────────────────────
PATCH_1_INIT = """
        # ✅ FIX: _ppo_agent 미초기화 AttributeError 방지
        self._ppo_agent = None   # _init_ppo_agent() 에서 설정됨
"""

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

PATCH_3_AFTER  = """
            # ✅ FIX: block_buy 체크 추가 (Extreme Greed 90+ 차단)
            fg_adj = self.fear_greed.get_signal_adjustment()

            # block_buy 플래그 체크 (fg_adj["mode"] == "suppressed" 와 동일)
            if fg_adj.get("block_buy", False):
                logger.info(
                    f"공포탐욕 매수 차단 ({market}): "
                    f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                )
                return

            # 극단 탐욕 모드에서 낮은 신뢰도 신호 억제
            if ml_pred and fg_adj.get("mode") == "suppressed":
                if ml_pred.get("confidence", 0) < 0.8:
                    logger.debug(
                        f"공포탐욕 억제 ({market}): "
                        f"지수={self.fear_greed.index} ({self.fear_greed.label})"
                    )
                    return
"""

print("engine_patch.py 생성 완료 — engine.py에 위 패치를 수동 적용하거나")
print("아래 auto_apply_engine_patch() 함수를 호출하세요.")


def auto_apply_engine_patch(engine_path: str = "core/engine.py"):
    """engine.py 에 패치 자동 적용"""
    from pathlib import Path
    p = Path(engine_path)
    if not p.exists():
        print(f"  ⚠️  {engine_path} 파일 없음 — 건너뜀")
        return

    code = p.read_text(encoding="utf-8")

    # 패치 1: _ppo_agent 미초기화 수정
    target1 = "self._ml_predictor = None"
    if target1 in code and "self._ppo_agent = None" not in code:
        code = code.replace(
            target1,
            target1 + "\n        self._ppo_agent = None   # ✅ FIX: AttributeError 방지"
        )
        print("  ✅ 패치 1 적용: _ppo_agent 초기화")
    else:
        print("  ⏩ 패치 1 이미 적용됨")

    # 패치 2: is_valid() → is_valid (property)
    if "self.fear_greed.is_valid():" in code:
        code = code.replace(
            "self.fear_greed.is_valid():",
            "self.fear_greed.is_valid:   # ✅ FIX: property 호출"
        )
        print("  ✅ 패치 2 적용: is_valid() → is_valid")
    else:
        print("  ⏩ 패치 2 이미 적용됨")

    # 패치 3: block_buy 체크 추가
    old_fg = 'fg_adj = self.fear_greed.get_signal_adjustment()\n            if ml_pred and fg_adj.get("mode") == "suppressed":'
    new_fg = (
        'fg_adj = self.fear_greed.get_signal_adjustment()\n'
        '            # ✅ FIX: Extreme Greed 90+ 매수 차단\n'
        '            if fg_adj.get("block_buy", False):\n'
        '                logger.info(f"공포탐욕 매수 차단 ({market}): '
        'idx={self.fear_greed.index}")\n'
        '                return\n'
        '            if ml_pred and fg_adj.get("mode") == "suppressed":'
    )
    if old_fg.replace("\n", "\n") in code:
        code = code.replace(old_fg, new_fg)
        print("  ✅ 패치 3 적용: block_buy 체크 추가")
    else:
        print("  ⏩ 패치 3 이미 적용됨 (또는 코드 구조 다름)")

    p.write_text(code, encoding="utf-8")
    print(f"  💾 {engine_path} 저장 완료")


if __name__ == "__main__":
    auto_apply_engine_patch()
