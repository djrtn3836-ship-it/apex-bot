#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fix14.py — FX14-1~3 패치
FX14-1: BULL 레짐 감성 패널티 하한 클램핑 (-0.54 → min -0.20)
FX14-2: FX13-2 Surge override _market_change_rates null-safe + ML 조건 완화
FX14-3: FX13-3 RSI BUY 구제 후 감성 패널티 면제
"""
from __future__ import annotations
import re, shutil, py_compile, pathlib, sys
from datetime import datetime

ROOT         = pathlib.Path(__file__).parent
ENGINE_BUY_F = ROOT / "core/engine_buy.py"

TS     = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = ROOT / f"archive/fx14_{TS}"
BACKUP.mkdir(parents=True, exist_ok=True)
shutil.copy2(ENGINE_BUY_F, BACKUP / ENGINE_BUY_F.name)
print(f"✅  백업 완료: {BACKUP}")

results = []
src = ENGINE_BUY_F.read_text(encoding="utf-8")

# ════════════════════════════════════════════════════════════════════════
# FX14-1  BULL 레짐 감성 패널티 하한 클램핑
# 대상 패턴: 감성 boost 적용 직후 로그 라인
#   (KRW-ENA): 0.77 → -0.10  (boost=-0.54, 감성=+0.267)
# 원인 코드: combined.score += sentiment_boost  (무제한 음수 허용)
# 수정: BULL/TRENDING_UP 레짐에서 boost 하한을 -0.20으로 클램핑
# ════════════════════════════════════════════════════════════════════════
OLD_SENTIMENT = '''\
            if combined is not None and sentiment_boost != 0:
                combined.score += sentiment_boost
                logger.info(
                    f"   ({market}): "
                    f"{combined.score - sentiment_boost:.2f} → {combined.score:.2f} "
                    f"(boost={sentiment_boost:.2f}, 감성=+{news_conf:.3f})"
                )'''

NEW_SENTIMENT = '''\
            if combined is not None and sentiment_boost != 0:
                # [FX14-1] BULL/TRENDING_UP 레짐에서 감성 패널티 하한 클램핑
                _fx14_gr = str(getattr(getattr(self, "_global_regime", None), "value",
                               getattr(self, "_global_regime", "UNKNOWN") or "UNKNOWN")).upper()
                _fx14_bull = _fx14_gr in ("BULL", "TRENDING_UP", "RECOVERY")
                if _fx14_bull and sentiment_boost < -0.20:
                    _orig_boost = sentiment_boost
                    sentiment_boost = -0.20
                    logger.debug(
                        f"[FX14-1] {market} BULL레짐 감성패널티 클램핑 "
                        f"{_orig_boost:.2f} → {sentiment_boost:.2f}"
                    )
                combined.score += sentiment_boost
                logger.info(
                    f"   ({market}): "
                    f"{combined.score - sentiment_boost:.2f} → {combined.score:.2f} "
                    f"(boost={sentiment_boost:.2f}, 감성=+{news_conf:.3f})"
                )'''

if OLD_SENTIMENT in src:
    src = src.replace(OLD_SENTIMENT, NEW_SENTIMENT)
    results.append(("FX14-1", "OK", "BULL 감성 패널티 클램핑 -0.20 삽입"))
else:
    # 정규식 fallback — sentiment_boost 적용 구간 탐색
    _pat1 = re.compile(
        r'(if combined is not None and sentiment_boost != 0:\s*\n'
        r'\s+combined\.score \+= sentiment_boost\s*\n'
        r'\s+logger\.info\(\s*\n'
        r'\s+f".*?감성.*?"\s*\n'
        r'\s+\))',
        re.DOTALL
    )
    _m1 = _pat1.search(src)
    if _m1:
        src = src[:_m1.start()] + NEW_SENTIMENT + src[_m1.end():]
        results.append(("FX14-1", "OK(regex)", "BULL 감성 패널티 클램핑 삽입 (regex)"))
    else:
        results.append(("FX14-1", "SKIP", "감성 패널티 패턴 미매치 — 수동 확인 필요"))

# ════════════════════════════════════════════════════════════════════════
# FX14-2  FX13-2 Surge override 조건 강화
#   - _market_change_rates null-safe 처리 (속성 없을 경우 WebSocket SCR 사용)
#   - ML confidence 임계값 0.52 → 0.48 완화
#   - NEAR (12.6%), STORJ (24.3%) 감지 가능하도록
# ════════════════════════════════════════════════════════════════════════
OLD_FX132 = '''\
                _fx13_surge = 0.0
                if hasattr(self, "_market_change_rates"):
                    _fx13_surge = self._market_change_rates.get(market, 0.0) * 100'''

NEW_FX132 = '''\
                _fx13_surge = 0.0
                # [FX14-2] _market_change_rates null-safe + WebSocket SCR fallback
                if hasattr(self, "_market_change_rates") and self._market_change_rates:
                    _fx13_surge = self._market_change_rates.get(market, 0.0) * 100
                if _fx13_surge == 0.0:
                    # WebSocket SCR 캐시에서 직접 조회 (분석 시점 최신값)
                    _scr_cache = getattr(self, "_scr_cache", {})
                    _fx13_surge = float(_scr_cache.get(market, {}).get("scr", 0.0))
                    if _fx13_surge == 0.0:
                        # surge_cache score × 100 fallback
                        _s_cache = getattr(self, "_surge_cache", {})
                        _fx13_surge = float(_s_cache.get(market, {}).get("change_rate", 0.0)) * 100'''

if OLD_FX132 in src:
    src = src.replace(OLD_FX132, NEW_FX132)
    results.append(("FX14-2-surge", "OK", "Surge null-safe + fallback 삽입"))
else:
    results.append(("FX14-2-surge", "SKIP", "FX13-2 surge 패턴 미매치"))

# ML 임계값 0.52 → 0.48
OLD_ML_THR = "and _fx13_ml_conf >= 0.52"
NEW_ML_THR = "and _fx13_ml_conf >= 0.48  # [FX14-2] 0.52→0.48 완화"
if OLD_ML_THR in src:
    src = src.replace(OLD_ML_THR, NEW_ML_THR)
    results.append(("FX14-2-ml", "OK", "ML 임계값 0.52→0.48 완화"))
else:
    results.append(("FX14-2-ml", "SKIP", "ML 임계값 패턴 미매치"))

# ════════════════════════════════════════════════════════════════════════
# FX14-3  FX13-3 RSI BUY 구제 후 감성 패널티 면제 플래그
#   구제된 combined에 _fx13_rescued=True 플래그 추가
#   이후 sentiment 적용 시 rescued 플래그 확인 → 패널티 스킵
# ════════════════════════════════════════════════════════════════════════
OLD_FX133_COMBINED = '''\
                    combined = _CS13r(
                        market=market,
                        signal_type=_ST13r.BUY,
                        score=float(getattr(_rs, "score", 0.6)),
                        confidence=float(getattr(_rs, "confidence", 0.65)),
                        agreement_rate=0.7,
                        contributing_strategies=["RSI_Divergence"],
                        reasons=[f"[FX13-3] RSI_Divergence BUY 단독 구제 (score={getattr(_rs,\'score\',0):.2f})"],
                    )'''

NEW_FX133_COMBINED = '''\
                    combined = _CS13r(
                        market=market,
                        signal_type=_ST13r.BUY,
                        score=float(getattr(_rs, "score", 0.6)),
                        confidence=float(getattr(_rs, "confidence", 0.65)),
                        agreement_rate=0.7,
                        contributing_strategies=["RSI_Divergence"],
                        reasons=[f"[FX13-3] RSI_Divergence BUY 단독 구제 (score={getattr(_rs,\'score\',0):.2f})"],
                    )
                    # [FX14-3] 구제된 신호는 감성 패널티 면제
                    combined._fx14_rescued = True'''

if OLD_FX133_COMBINED in src:
    src = src.replace(OLD_FX133_COMBINED, NEW_FX133_COMBINED)
    results.append(("FX14-3-flag", "OK", "RSI BUY 구제 플래그 삽입"))
else:
    results.append(("FX14-3-flag", "SKIP", "FX13-3 combined 패턴 미매치"))

# 감성 패널티 적용 시 rescued 플래그 체크 삽입
OLD_RESCUED_CHK = "if combined is not None and sentiment_boost != 0:"
NEW_RESCUED_CHK = '''\
            # [FX14-3] FX13-3 구제 신호는 감성 패널티 면제
            if combined is not None and getattr(combined, "_fx14_rescued", False):
                logger.debug(f"[FX14-3] {market} RSI BUY 구제 신호 — 감성 패널티 면제")
                sentiment_boost = 0.0
            if combined is not None and sentiment_boost != 0:'''

# 이미 FX14-1 패치 이후의 패턴과 겹치지 않도록 NEW_SENTIMENT 이후 구간에만 적용
# NEW_SENTIMENT에 이미 "if combined is not None and sentiment_boost != 0:" 포함됨
# → 해당 줄 바로 위에 rescued 체크 삽입
if OLD_RESCUED_CHK in src:
    # 첫 번째 occurrence만 교체 (FX14-1 패치 후 NEW_SENTIMENT 포함된 버전에서 동작)
    src = src.replace(OLD_RESCUED_CHK, NEW_RESCUED_CHK, 1)
    results.append(("FX14-3-check", "OK", "감성 패널티 면제 체크 삽입"))
else:
    results.append(("FX14-3-check", "SKIP", "rescued 체크 패턴 미매치"))

# ─── 저장 및 컴파일 ────────────────────────────────────────────────────
ENGINE_BUY_F.write_text(src, encoding="utf-8")
try:
    py_compile.compile(str(ENGINE_BUY_F), doraise=True)
    results.append(("FX14-compile", "OK", "engine_buy.py 컴파일 성공"))
except py_compile.PyCompileError as e:
    results.append(("FX14-compile", "FAIL", str(e)))
    shutil.copy2(BACKUP / ENGINE_BUY_F.name, ENGINE_BUY_F)
    print("❌  engine_buy.py 컴파일 실패 → 백업 복구")

# ─── 결과 출력 ─────────────────────────────────────────────────────────
print()
print("=" * 68)
all_ok = True
for step, status, msg in results:
    icon = "✅" if status in ("OK", "OK(regex)") else ("⚠️ " if status == "SKIP" else "❌")
    print(f"{icon}  {step:<22s}  {status:<12s}  {msg}")
    if status == "FAIL":
        all_ok = False
print("=" * 68)
if all_ok:
    print("✅  FX14 전체 패치 성공")
else:
    print("❌  일부 실패 — 위 오류 확인 후 수동 적용")
import sys; sys.exit(0 if all_ok else 1)
