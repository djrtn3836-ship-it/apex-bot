"""
APEX BOT - 로깅 시스템
Loguru 기반 구조화된 로깅 + 파일 로테이션 + 컬러 출력
"""
import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_level: str = "INFO", log_dir: Path = None) -> None:
    """전역 로거 설정"""
    # 기존 핸들러 제거
    logger.remove()

    # ── 콘솔 출력 (컬러) ────────────────────────────────────
    logger.add(
        sys.stdout,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # ── 전체 로그 파일 (일별 로테이션) ──────────────────
        logger.add(
            log_dir / "apex_bot_{time:YYYY-MM-DD}.log",
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
            rotation="00:00",       # 자정마다 새 파일
            retention="30 days",    # 30일 보관
            compression="zip",      # 압축 보관
            encoding="utf-8",
        )

        # ── 에러 전용 로그 ────────────────────────────────────
        logger.add(
            log_dir / "error_{time:YYYY-MM-DD}.log",
            level="ERROR",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}\n{exception}",
            rotation="00:00",
            retention="60 days",
            compression="zip",
            encoding="utf-8",
        )

        # ── 거래 전용 로그 ────────────────────────────────────
        logger.add(
            log_dir / "trades_{time:YYYY-MM}.log",
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | TRADE | {message}",
            filter=lambda record: "TRADE" in record["message"],
            rotation="1 month",
            retention="12 months",
            compression="zip",
            encoding="utf-8",
        )

    logger.info("✅ 로거 초기화 완료")


def get_logger(name: str):
    """모듈별 로거 반환"""
    return logger.bind(module=name)


# 거래 전용 로그 헬퍼
def log_trade(action: str, market: str, price: float, amount: float,
              reason: str = "", profit_rate: float = None):
    """거래 실행 로그 (전용 포맷)"""
    profit_str = f" | 수익률={profit_rate:.2f}%" if profit_rate is not None else ""
    logger.info(
        f"TRADE | {action} | {market} | 가격={price:,.0f} | "
        f"금액={amount:,.0f}KRW | 사유={reason}{profit_str}"
    )


def log_signal(market: str, signal_type: str, score: float, strategies: list):
    """신호 생성 로그"""
    logger.info(
        f"SIGNAL | {market} | {signal_type} | 점수={score:.2f} | "
        f"전략={', '.join(strategies)}"
    )


def log_risk(event: str, detail: str):
    """리스크 이벤트 로그"""
    logger.warning(f"RISK | {event} | {detail}")
