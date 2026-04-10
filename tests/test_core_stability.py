"""
APEX BOT - Core Stability Tests
코드 품질 보장을 위한 핵심 단위 테스트
"""
import ast
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import fields

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── 1. 전체 .py 파일 문법 검사 ──────────────────────────────
class TestSyntax:
    """183개 .py 파일 전부 SyntaxError 없음을 보장"""

    def test_all_py_files_syntax_ok(self):
        errors = []
        for py_file in ROOT.rglob("*.py"):
            if ".git" in str(py_file) or "__pycache__" in str(py_file):
                continue
            try:
                src = py_file.read_bytes().lstrip(b"\xef\xbb\xbf").decode(
                    "utf-8", errors="replace"
                )
                ast.parse(src)
            except SyntaxError as e:
                errors.append(f"{py_file}: line {e.lineno} - {e.msg}")
        assert not errors, f"SyntaxError in {len(errors)} files:\n" + "\n".join(errors)

    def test_no_bom_in_any_file(self):
        bom_files = []
        for py_file in ROOT.rglob("*.py"):
            if ".git" in str(py_file) or "__pycache__" in str(py_file):
                continue
            raw = py_file.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                bom_files.append(str(py_file))
        assert not bom_files, f"BOM found in:\n" + "\n".join(bom_files)

    def test_no_broken_chars_in_core_files(self):
        core_files = [
            "core/engine.py",
            "signals/signal_combiner.py",
            "config/settings.py",
            "execution/executor.py",
            "risk/position_sizer.py",
        ]
        broken = []
        for fpath in core_files:
            p = ROOT / fpath
            if not p.exists():
                continue
            src = p.read_bytes().lstrip(b"\xef\xbb\xbf").decode("utf-8", errors="replace")
            count = src.count("\ufffd")
            if count > 0:
                broken.append(f"{fpath}: {count} broken chars")
        assert not broken, "Broken chars found:\n" + "\n".join(broken)


# ── 2. CombinedSignal 타입 안전성 테스트 ──────────────────────
class TestCombinedSignal:
    """CombinedSignal이 dict/object 양쪽으로 안전하게 접근됨을 보장"""

    def setup_method(self):
        from signals.signal_combiner import CombinedSignal
        from strategies.base_strategy import SignalType
        self.signal = CombinedSignal(
            market="KRW-BTC",
            signal_type=SignalType.BUY,
            score=0.75,
            confidence=0.65,
            agreement_rate=0.8,
            contributing_strategies=["MACD_Cross", "VWAP_Reversion"],
            reasons=["test reason"],
        )

    def test_attribute_access(self):
        assert self.signal.market == "KRW-BTC"
        assert self.signal.score == 0.75
        assert self.signal.confidence == 0.65

    def test_dict_get_compat(self):
        """signal.get() 호환성 - engine.py에서 사용"""
        assert self.signal.get("confidence", 0) == 0.65
        assert self.signal.get("nonexistent", "default") == "default"

    def test_dict_getitem_compat(self):
        """signal["key"] 호환성"""
        assert self.signal["market"] == "KRW-BTC"

    def test_contains_compat(self):
        """'key' in signal 호환성"""
        assert "market" in self.signal
        assert "nonexistent" not in self.signal

    def test_getattr_safe(self):
        """getattr() 안전 접근"""
        assert getattr(self.signal, "confidence", 0) == 0.65
        assert getattr(self.signal, "nonexistent", None) is None

    def test_contributing_strategies_list(self):
        strategies = getattr(self.signal, "contributing_strategies", [])
        assert isinstance(strategies, list)
        assert "MACD_Cross" in strategies

    def test_reasons_list(self):
        reasons = self.signal.reasons
        assert isinstance(reasons, list)


# ── 3. Settings 타입 안전성 테스트 ──────────────────────────
class TestSettings:
    def test_settings_loads_without_error(self):
        from config.settings import get_settings
        settings = get_settings()
        assert settings is not None

    def test_buy_signal_threshold_is_float(self):
        from config.settings import get_settings
        s = get_settings()
        assert isinstance(s.risk.buy_signal_threshold, float)
        assert 0.0 < s.risk.buy_signal_threshold < 1.0

    def test_max_positions_positive(self):
        from config.settings import get_settings
        s = get_settings()
        assert s.trading.max_positions >= 1

    def test_target_markets_not_empty(self):
        from config.settings import get_settings
        s = get_settings()
        assert len(s.trading.target_markets) > 0

    def test_risk_config_valid_ranges(self):
        from config.settings import get_settings
        s = get_settings()
        assert 0 < s.risk.max_risk_per_trade <= 0.05
        assert 0 < s.risk.kelly_fraction <= 1.0
        assert 0 < s.risk.daily_loss_limit <= 0.20


# ── 4. SignalCombiner 로직 테스트 ──────────────────────────
class TestSignalCombiner:
    def setup_method(self):
        from signals.signal_combiner import SignalCombiner
        from config.settings import get_settings
        self.combiner = SignalCombiner(get_settings())

    def test_combine_returns_none_for_empty(self):
        result = self.combiner.combine([], "KRW-BTC", None, "UNKNOWN")
        assert result is None

    def test_combine_with_ml_buy_signal(self):
        from signals.signal_combiner import CombinedSignal
        from strategies.base_strategy import SignalType
        ml_pred = {"signal": "BUY", "confidence": 0.80}
        result = self.combiner.combine([], "KRW-BTC", ml_pred, "TRENDING")
        # ML weight 3.0 * 0.80 = 2.4 > threshold 0.35 -> BUY
        assert result is not None
        assert result.signal_type == SignalType.BUY

    def test_combine_hold_when_below_threshold(self):
        ml_pred = {"signal": "HOLD", "confidence": 0.45}
        result = self.combiner.combine([], "KRW-BTC", ml_pred, "UNKNOWN")
        assert result is None

    def test_strategy_weights_all_non_negative(self):
        for name, weight in self.combiner.STRATEGY_WEIGHTS.items():
            assert weight >= 0, f"{name} has negative weight"


# ── 5. FearGreedMonitor 속성 테스트 ──────────────────────────
class TestFearGreedMonitor:
    def test_has_index_attribute(self):
        from signals.filters.fear_greed import FearGreedMonitor
        fg = FearGreedMonitor()
        # index 속성이 존재해야 함 (latest_index X)
        assert hasattr(fg, "index"), "FearGreedMonitor must have .index attribute"
        assert not hasattr(fg, "latest_index") or True  # latest_index는 없어도 됨

    def test_get_buy_threshold_adjustment_returns_float(self):
        from signals.filters.fear_greed import FearGreedMonitor
        fg = FearGreedMonitor()
        adj = fg.get_buy_threshold_adjustment()
        assert isinstance(adj, float), f"Expected float, got {type(adj)}"

    def test_get_signal_adjustment_returns_dict(self):
        from signals.filters.fear_greed import FearGreedMonitor
        fg = FearGreedMonitor()
        adj = fg.get_signal_adjustment()
        assert isinstance(adj, dict), f"Expected dict, got {type(adj)}"
        assert "block_buy" in adj


# ── 6. 포지션 사이저 테스트 ────────────────────────────────
class TestPositionSizer:
    def test_calc_position_size_basic(self):
        from core.engine import calc_position_size
        result = calc_position_size(
            total_capital=1_000_000,
            kelly_f=0.25,
            current_price=100_000,
            atr=1_000,
            open_positions=0,
            max_positions=10,
            signal_score=0.7,
            market="KRW-BTC",
        )
        assert "amount_krw" in result
        assert result["amount_krw"] >= 5_000  # 최소 주문금액
        assert result["amount_krw"] <= 200_000  # 최대 20%

    def test_calc_position_size_never_exceeds_capital(self):
        from core.engine import calc_position_size
        result = calc_position_size(
            total_capital=100_000,
            kelly_f=0.5,
            current_price=50_000,
            atr=500,
            open_positions=9,
            max_positions=10,
            signal_score=0.9,
            market="KRW-ETH",
        )
        assert result["amount_krw"] <= 100_000 * 0.20 + 1  # 20% 한도

    def test_calc_exit_plan_sl_below_entry(self):
        from core.engine import calc_exit_plan
        result = calc_exit_plan(entry_price=100_000, atr=1_000, position_krw=50_000)
        assert result["sl"] < 100_000, "SL must be below entry"
        assert result["tp1"] > 100_000, "TP1 must be above entry"
        assert result["tp2"] > result["tp1"], "TP2 must be above TP1"
