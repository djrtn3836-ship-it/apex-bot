"""
news_sentiment.py 파일 상단 import 블록에 os 추가 패치
"""

def patch_news_sentiment(path: str = "signals/filters/news_sentiment.py"):
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        print(f"  ⚠️  {path} 없음")
        return
    code = p.read_text(encoding="utf-8")
    # 하단의 단독 import os 제거, 상단에 추가
    if "\nimport os\n" not in code[:500] and "import os" in code:
        code = code.replace(
            "from __future__ import annotations",
            "from __future__ import annotations\nimport os"
        )
        # 하단 단독 import os 제거
        code = code.replace("\n\nimport os\n\n_global", "\n\n_global")
        p.write_text(code, encoding="utf-8")
        print(f"  ✅ news_sentiment.py import os 위치 수정")
    else:
        print("  ⏩ 이미 수정됨")

patch_news_sentiment()
