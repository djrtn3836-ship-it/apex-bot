"""
Apex Bot - 텔레그램 설정 가이드 + 명령어 인터페이스 (M6)
python monitoring/telegram_setup.py 로 실행
"""
import os, pathlib, asyncio
from loguru import logger

ENV_PATH = pathlib.Path(".env")


def setup_telegram():
    """텔레그램 토큰/채팅ID .env 설정 가이드"""
    print("""
╔══════════════════════════════════════════════╗
║     텔레그램 봇 설정 가이드                  ║
╚══════════════════════════════════════════════╝

[Step 1] 봇 토큰 발급
  1. 텔레그램에서 @BotFather 검색
  2. /newbot 명령어 입력
  3. 봇 이름 입력 (예: ApexTradingBot)
  4. 봇 사용자명 입력 (예: apex_trading_bot)
  5. 발급된 토큰 복사 (예: 1234567890:ABCdefGHI...)

[Step 2] 채팅 ID 확인
  1. 방금 만든 봇에게 아무 메시지 전송
  2. 브라우저에서 아래 주소 접속:
     https://api.telegram.org/bot<토큰>/getUpdates
  3. "chat":{"id": 숫자} 에서 숫자 복사
""")

    token   = input("텔레그램 봇 토큰 입력 (없으면 Enter 스킵): ").strip()
    chat_id = input("텔레그램 채팅 ID 입력 (없으면 Enter 스킵): ").strip()

    if not token or not chat_id:
        print("⚠️  스킵 — 나중에 .env 파일에 직접 추가하세요:")
        print("   TELEGRAM_TOKEN=your_token")
        print("   TELEGRAM_CHAT_ID=your_chat_id")
        return False

    # .env 읽기
    env_content = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""

    # 기존 값 교체 또는 추가
    for key, val in [("TELEGRAM_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)]:
        if key in env_content:
            lines = env_content.splitlines()
            env_content = "\n".join(
                f"{key}={val}" if l.startswith(f"{key}=") else l
                for l in lines
            )
        else:
            env_content += f"\n{key}={val}"

    ENV_PATH.write_text(env_content.strip() + "\n", encoding="utf-8")
    print(f"\n✅ .env 저장 완료: {ENV_PATH.absolute()}")
    print("   봇 재시작 시 텔레그램 알림이 활성화됩니다.")
    return True


async def test_telegram(token: str, chat_id: str):
    """텔레그램 연결 테스트"""
    try:
        import aiohttp
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": "✅ Apex Bot 텔레그램 연결 테스트 성공!"}
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=data) as resp:
                if resp.status == 200:
                    print("✅ 텔레그램 메시지 전송 성공!")
                    return True
                else:
                    print(f"❌ 전송 실패: {resp.status}")
                    return False
    except Exception as e:
        print(f"❌ 오류: {e}")
        return False


# 텔레그램 명령어 핸들러 확장
TELEGRAM_COMMANDS = {
    "/status":    "봇 현재 상태 및 포트폴리오 요약",
    "/positions": "현재 보유 포지션 목록",
    "/pnl":       "오늘 손익 현황",
    "/report":    "24시간 성과 리포트",
    "/stop":      "⚠️  봇 일시 정지 (포지션 유지)",
    "/resume":    "봇 재개",
    "/emergency": "🚨 긴급 전체 청산",
    "/help":      "명령어 목록",
}


def get_commands_text() -> str:
    lines = ["📋 Apex Bot 명령어 목록\n"]
    for cmd, desc in TELEGRAM_COMMANDS.items():
        lines.append(f"  {cmd:<12} — {desc}")
    return "\n".join(lines)


if __name__ == "__main__":
    setup_telegram()
