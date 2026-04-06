"""
APEX BOT - 실거래 모드 시작 스크립트
실행 전 반드시 .env 파일에 아래 항목 설정 필요:
    UPBIT_ACCESS_KEY=your_access_key
    UPBIT_SECRET_KEY=your_secret_key
    TRADING_MODE=live
    APEX_LIVE_CONFIRM=yes
"""
import os
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# ── 환경변수 로드 ─────────────────────────────────────────────────
load_dotenv()

BANNER = """
╔══════════════════════════════════════════════════╗
║          ⚡  APEX BOT  v2.0.0  ⚡              ║
║     Upbit AI Quant Auto Trading System           ║
║     모드: 🔴 LIVE (실거래)                        ║
╚══════════════════════════════════════════════════╝
"""

def check_live_requirements() -> bool:
    """실거래 전환 전 필수 요건 검사"""
    print(BANNER)
    print("=" * 52)
    print("  실거래 전환 체크리스트")
    print("=" * 52)

    errors   = []
    warnings = []

    # 1. API 키 확인
    access_key = os.getenv("UPBIT_ACCESS_KEY", "")
    secret_key = os.getenv("UPBIT_SECRET_KEY", "")

    if not access_key or access_key == "your_access_key":
        errors.append("❌ UPBIT_ACCESS_KEY 미설정")
    else:
        masked = access_key[:4] + "*" * (len(access_key) - 8) + access_key[-4:]
        print(f"  ✅ UPBIT_ACCESS_KEY: {masked}")

    if not secret_key or secret_key == "your_secret_key":
        errors.append("❌ UPBIT_SECRET_KEY 미설정")
    else:
        print(f"  ✅ UPBIT_SECRET_KEY: {'*' * 20}")

    # 2. TRADING_MODE 확인
    trading_mode = os.getenv("TRADING_MODE", "paper")
    if trading_mode != "live":
        errors.append("❌ TRADING_MODE=live 미설정 (.env 확인)")
    else:
        print(f"  ✅ TRADING_MODE: live")

    # 3. 실거래 확인 플래그
    live_confirm = os.getenv("APEX_LIVE_CONFIRM", "")
    if live_confirm != "yes":
        errors.append("❌ APEX_LIVE_CONFIRM=yes 미설정")
    else:
        print(f"  ✅ APEX_LIVE_CONFIRM: yes")

    # 4. DB 파일 존재 확인 (페이퍼 거래 기록)
    db_path = Path("database/apex_bot.db")
    if db_path.exists():
        size_kb = db_path.stat().st_size / 1024
        print(f"  ✅ 거래 DB 존재: {size_kb:.1f} KB")
    else:
        warnings.append("⚠️  거래 DB 없음 (페이퍼 트레이딩 기록 없음)")

    # 5. ML 모델 파일 확인
    ensemble_path = Path("models/saved/ensemble_best.pt")
    ppo_path      = Path("models/saved/ppo/best_model.zip")

    if ensemble_path.exists():
        print(f"  ✅ 앙상블 모델 존재")
    else:
        errors.append("❌ 앙상블 모델 없음: models/saved/ensemble_best.pt")

    if ppo_path.exists():
        print(f"  ✅ PPO 모델 존재")
    else:
        warnings.append("⚠️  PPO 모델 없음: models/saved/ppo/best_model.zip")

    # 6. 금지 포트 확인
    forbidden_ports = [5555, 5556, 5557, 5558, 5599]
    import socket
    for port in forbidden_ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                result = s.connect_ex(("127.0.0.1", port))
                if result == 0:
                    errors.append(f"❌ 금지 포트 {port} 사용 중 (키움봇 충돌 위험)")
        except Exception:
            pass
    print(f"  ✅ 금지 포트 확인 완료")

    # 7. 페이퍼 트레이딩 성과 확인
    print()
    print("=" * 52)
    print("  페이퍼 트레이딩 성과 기준")
    print("=" * 52)
    print("  목표: 승률 ≥ 55% | Sharpe ≥ 1.5 | MDD ≤ 10%")
    print()

    # 결과 출력
    if warnings:
        print("  [경고]")
        for w in warnings:
            print(f"  {w}")
        print()

    if errors:
        print("  [오류 - 실거래 시작 불가]")
        for e in errors:
            print(f"  {e}")
        print()
        print("=" * 52)
        print("  위 오류를 해결 후 다시 실행하세요.")
        print("=" * 52)
        return False

    print("  ✅ 모든 체크리스트 통과!")
    print()
    return True


def confirm_live_start() -> bool:
    """실거래 시작 최종 확인"""
    print("=" * 52)
    print("  ⚠️  경고: 실제 자금으로 거래가 시작됩니다!")
    print("=" * 52)
    print()

    # 투자금 확인
    while True:
        try:
            capital_input = input("  투자 자본금을 입력하세요 (원, 예: 1000000): ").strip()
            capital = float(capital_input.replace(",", ""))
            if capital < 100_000:
                print("  ⚠️  최소 투자금은 100,000원입니다.")
                continue
            if capital > 10_000_000:
                confirm = input(f"  {capital:,.0f}원은 큰 금액입니다. 계속하시겠습니까? (yes/no): ")
                if confirm.lower() != "yes":
                    continue
            break
        except ValueError:
            print("  숫자를 입력해 주세요.")

    print()
    print(f"  투자 자본금: ₩{capital:,.0f}")
    print()

    # 최종 확인
    confirm = input("  실거래를 시작하시겠습니까? (yes/no): ").strip().lower()
    if confirm != "yes":
        print()
        print("  실거래 시작이 취소되었습니다.")
        return False

    # 환경변수에 자본금 설정
    os.environ["INITIAL_CAPITAL"] = str(capital)
    os.environ["TRADING_MODE"]    = "live"
    return True


async def main():
    """실거래 메인 실행"""
    # 요건 검사
    if not check_live_requirements():
        sys.exit(1)

    # 최종 확인
    if not confirm_live_start():
        sys.exit(0)

    print()
    print("=" * 52)
    print("  🚀 APEX BOT 실거래 모드 시작!")
    print("=" * 52)
    print()

    # 엔진 시작
    try:
        from core.engine import TradingEngine
        engine = TradingEngine(mode="live")
        await engine.start()
    except KeyboardInterrupt:
        print()
        print("  봇이 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"  ❌ 실행 오류: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
