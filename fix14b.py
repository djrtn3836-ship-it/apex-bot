#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fix14b.py — FX14-1 & FX14-3-check 재패치 (정확한 변수명 news_boost 기반)
FX14-1b: BULL 레짐 news_boost 하한 -0.20 클램핑
FX14-3b: FX13-3 RSI BUY 구제 신호 news_boost 면제
"""
from __future__ import annotations
import shutil, py_compile, pathlib, sys
from datetime import datetime

ROOT         = pathlib.Path(__file__).parent
ENGINE_BUY_F = ROOT / "core/engine_buy.py"

TS     = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = ROOT / f"archive/fx14b_{TS}"
BACKUP.mkdir(parents=True, exist_ok=True)
shutil.copy2(ENGINE_BUY_F, BACKUP / ENGINE_BUY_F.name)
print(f"✅  백업 완료: {BACKUP}")

results = []
src = ENGINE_BUY_F.read_text(encoding="utf-8")

# ════════════════════════════════════════════════════════════════════════
# FX14-1b  news_boost 하한 클램핑 (BULL/TRENDING_UP/RECOVERY)
# 실제 코드 패턴:
#   news_score, news_boost = self.news_analyzer.get_signal_boost(market)
#   if abs(news_boost) > 0.3:
#       original_score = combined.score
#       combined.score = combined.score + news_boost
# ════════════════════════════════════════════════════════════════════════
OLD_NEWS = '''\
            news_score, news_boost = self.news_analyzer.get_signal_boost(market)
            if abs(news_boost) > 0.3:
                original_score = combined.score
                combined.score = combined.score + news_boost  # EB-7: 부정=음수이므로 +가 맞음
                logger.info(
                    f"   ({market}): "
                    f"{original_score:.2f} → {combined.score:.2f} "
                    f"(boost={news_boost:+.2f}, 감성={news_score:+.3f})"
                )'''

NEW_NEWS = '''\
            news_score, news_boost = self.news_analyzer.get_signal_boost(market)
            # [FX14-3b] FX13-3 구제 신호는 news_boost 패널티 면제
            if getattr(combined, "_fx14_rescued", False) and news_boost < 0:
                logger.debug(
                    f"[FX14-3b] {market} RSI BUY 구제 신호 — news_boost 패널티 면제 "
                    f"({news_boost:.2f} → 0.0)"
                )
                news_boost = 0.0
            if abs(news_boost) > 0.3:
                # [FX14-1b] BULL/TRENDING_UP/RECOVERY 레짐 news_boost 하한 클램핑 -0.20
                _fx14b_gr = str(getattr(getattr(self, "_global_regime", None), "value",
                                getattr(self, "_global_regime", "UNKNOWN") or "UNKNOWN")).upper()
                if _fx14b_gr in ("BULL", "TRENDING_UP", "RECOVERY") and news_boost < -0.20:
                    logger.debug(
                        f"[FX14-1b] {market} BULL레짐 news_boost 클램핑 "
                        f"{news_boost:.2f} → -0.20"
                    )
                    news_boost = -0.20
                original_score = combined.score
                combined.score = combined.score + news_boost  # EB-7: 부정=음수이므로 +가 맞음
                logger.info(
                    f"   ({market}): "
                    f"{original_score:.2f} → {combined.score:.2f} "
                    f"(boost={news_boost:+.2f}, 감성={news_score:+.3f})"
                )'''

if OLD_NEWS in src:
    src = src.replace(OLD_NEWS, NEW_NEWS)
    results.append(("FX14-1b", "OK", "news_boost BULL 클램핑 -0.20 삽입"))
    results.append(("FX14-3b", "OK", "RSI BUY 구제 news_boost 면제 삽입"))
else:
    results.append(("FX14-1b", "FAIL", "news_boost 패턴 미매치 — 수동 확인"))
    results.append(("FX14-3b", "FAIL", "패턴 미매치 (FX14-1b 실패로 스킵)"))

# ─── 저장 및 컴파일 ────────────────────────────────────────────────────
ENGINE_BUY_F.write_text(src, encoding="utf-8")
try:
    py_compile.compile(str(ENGINE_BUY_F), doraise=True)
    results.append(("FX14b-compile", "OK", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX14b-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / ENGINE_BUY_F.name, ENGINE_BUY_F)
    print("❌  컴파일 실패 → 백업 복구")

# ─── 결과 출력 ─────────────────────────────────────────────────────────
print()
print("=" * 65)
all_ok = True
for step, status, msg in results:
    icon = "✅" if status == "OK" else ("⚠️ " if status == "SKIP" else "❌")
    print(f"{icon}  {step:<20s}  {status:<10s}  {msg}")
    if status == "FAIL":
        all_ok = False
print("=" * 65)
print("✅  FX14b 전체 패치 성공" if all_ok else "❌  패치 실패 — 위 오류 확인")
sys.exit(0 if all_ok else 1)
