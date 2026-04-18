"""Apex Bot -    +   (M6)
python monitoring/telegram_setup.py"""
import os, pathlib, asyncio
from loguru import logger

ENV_PATH = pathlib.Path(".env")


def setup_telegram():
    """/ID .env"""
    print("""[Step 1]   
  1.  @BotFather 
  2. /newbot  
  3.    (: ApexTradingBot)
  4.    (: apex_trading_bot)
  5.    (: 1234567890:ABCdefGHI...)

[Step 2]  ID 
  1.      
  2.    :
     https://api.telegram.org/bot<>/getUpdates
  3. "chat":{"id": }""")

    token   = input("텔레그램 봇 토큰 입력 (없으면 Enter 스킵): ").strip()
    chat_id = input("텔레그램 채팅 ID 입력 (없으면 Enter 스킵): ").strip()

    if not token or not chat_id:
        print("   —  .env   :")
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
    print(f"\n .env  : {ENV_PATH.absolute()}")
    print("        .")
    return True


async def test_telegram(token: str, chat_id: str):
    """test_telegram 실행"""
    try:
        import aiohttp
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": "✅ Apex Bot 텔레그램 연결 테스트 성공!"}
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=data) as resp:
                if resp.status == 200:
                    print("    !")
                    return True
                else:
                    print(f"  : {resp.status}")
                    return False
    except Exception as e:
        print(f" : {e}")
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
