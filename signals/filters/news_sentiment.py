"""APEX BOT -    (NLP )
/   

 :
  1. CryptoPanic API  (  )
  2. CoinDesk RSS 
  3.  
  4. Google Trends (pytrends)

 :
  -     (  )
  - VADER   (nltk, )
  - FinBERT  (transformers, GPU )  ← 

:
  sentiment_score:  -1.0 ( ) ~ +1.0 ( )
  signal_boost:     BUY   (-2.0 ~ +2.0)
  hot_topics:        /"""
from __future__ import annotations
import os

import asyncio
import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
from loguru import logger

# ── 감성 분석 라이브러리 ─────────────────────────────────────
try:
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    import nltk
    VADER_AVAILABLE = True
except ImportError:
    VADER_AVAILABLE = False

try:
    from transformers import pipeline as hf_pipeline
    FINBERT_AVAILABLE = True
except ImportError:
    FINBERT_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
#  키워드 사전 (규칙 기반)
# ──────────────────────────────────────────────────────────────

BULLISH_KEYWORDS = {
    # 제도/기관
    "etf": 3.0, "spot etf": 4.0, "approval": 2.5, "approved": 2.5,
    "institutional": 2.0, "adoption": 2.0, "partnership": 1.5,
    "blackrock": 2.5, "fidelity": 2.0, "microstrategy": 1.5,
    # 기술
    "halving": 3.0, "upgrade": 1.5, "mainnet": 1.5, "launch": 1.0,
    "scaling": 1.0, "layer 2": 1.5,
    # 규제
    "legal tender": 3.0, "regulation clarity": 2.0, "pro-crypto": 2.0,
    # 시장
    "all-time high": 2.0, "ath": 2.0, "bull run": 2.0, "rally": 1.5,
    "accumulate": 1.5, "whale buy": 2.0,
    # 한국어
    "etf 승인": 4.0, "기관 투자": 2.5, "반감기": 3.0, "상승": 1.0,
    "호재": 2.0, "채택": 1.5, "제도권": 2.0,
}

BEARISH_KEYWORDS = {
    # 규제 리스크
    "ban": -3.0, "banned": -3.5, "crackdown": -3.0, "sec lawsuit": -3.5,
    "lawsuit": -2.5, "investigate": -2.0, "kyc aml": -1.5,
    "china ban": -4.0, "korea ban": -4.0,
    # 해킹/사기
    "hack": -3.5, "hacked": -4.0, "exploit": -3.5, "rug pull": -4.0,
    "scam": -3.0, "fraud": -3.0, "ponzi": -3.5,
    # 시장 붕괴
    "crash": -3.0, "collapse": -3.5, "bear market": -2.0,
    "capitulation": -2.5, "liquidation": -2.5, "whale dump": -2.5,
    "exchange collapse": -4.0, "ftx": -3.0, "bankruptcy": -3.5,
    # 한국어
    "규제": -2.0, "해킹": -4.0, "사기": -3.5, "하락": -1.0,
    "악재": -2.0, "금지": -3.5, "압수": -3.0,
}

# 코인별 특수 키워드
COIN_KEYWORDS: Dict[str, Dict[str, float]] = {
    "KRW-BTC": {
        "bitcoin etf": 4.0, "bitcoin ban": -5.0,
        "satoshi": 0.5, "lightning network": 1.5,
    },
    "KRW-ETH": {
        "ethereum etf": 4.0, "merge": 2.0, "eip": 1.0,
        "vitalik": 1.0, "defi": 1.5, "nft": 0.5,
    },
    "KRW-SOL": {
        "solana": 0.5, "sol ecosystem": 1.5,
        "sol hack": -4.0, "solana outage": -3.5,
    },
    "KRW-XRP": {
        "ripple": 0.5, "sec ripple": -3.0,
        "ripple win": 3.0, "xrp etf": 3.5,
    },
}


# ──────────────────────────────────────────────────────────────
#  뉴스 아이템 컨테이너
# ──────────────────────────────────────────────────────────────

class NewsItem:
    __slots__ = ("title", "url", "published_at", "source", "sentiment_score",
                 "coins", "hash_id")

    def __init__(
        self,
        title: str,
        url: str = "",
        published_at: Optional[float] = None,
        source: str = "unknown",
    ):
        self.title = title
        self.url = url
        self.published_at = published_at or time.time()
        self.source = source
        self.sentiment_score: float = 0.0
        self.coins: List[str] = []
        self.hash_id = hashlib.md5(title.encode()).hexdigest()[:8]


# ──────────────────────────────────────────────────────────────
#  뉴스 감성 분석기
# ──────────────────────────────────────────────────────────────

class NewsSentimentAnalyzer:
    """+   

     :
      analyzer = NewsSentimentAnalyzer()
      await analyzer.fetch_news()           #  
      score, boost = analyzer.get_signal_boost("KRW-BTC")  #  
      can_buy, reason = analyzer.can_buy("KRW-BTC")         #"""

    CACHE_DIR = Path("data/news_cache")
    CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
    COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"

    def __init__(
        self,
        use_finbert: bool = False,  # True = GPU 감성 모델 (느림)
        cache_hours: int = 1,
        api_key: str = "",          # CryptoPanic API Key (없으면 RSS만)
    ):
        self.use_finbert = use_finbert and FINBERT_AVAILABLE
        self.cache_hours = cache_hours
        self.api_key = api_key or os.environ.get("CRYPTOPANIC_API_KEY", "")

        self._news_cache: deque = deque(maxlen=200)  # 최근 200개
        self._market_scores: Dict[str, float] = defaultdict(float)
        self._last_fetch: float = 0
        self._seen_ids: set = set()

        # 감성 분석기 초기화
        self._vader = None
        if VADER_AVAILABLE:
            try:
                nltk.download("vader_lexicon", quiet=True)
                self._vader = SentimentIntensityAnalyzer()
            except Exception:
                pass

        self._finbert = None
        if self.use_finbert and FINBERT_AVAILABLE:
            try:
                device = 0 if self._is_gpu_available() else -1
                self._finbert = hf_pipeline(
                    "text-classification",
                    model="ProsusAI/finbert",
                    device=device,
                )
                logger.info(" FinBERT    ")
            except Exception as e:
                logger.warning(f"FinBERT   (  ): {e}")

        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"     | "
            f"VADER={'' if self._vader else ''} | "
            f"FinBERT={'' if self._finbert else ''}"
        )

    # ── Public API ──────────────────────────────────────────────

    async def fetch_news(self) -> int:
        """(   )"""
        now = time.time()
        if now - self._last_fetch < self.cache_hours * 3600:
            return 0

        items = []
        tasks = [self._fetch_cryptopanic(), self._fetch_coindesk_rss()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, list):
                items.extend(r)

        # 중복 제거
        new_items = [i for i in items if i.hash_id not in self._seen_ids]
        for item in new_items:
            self._seen_ids.add(item.hash_id)
            self._score_item(item)
            self._news_cache.append(item)

        self._update_market_scores()
        self._last_fetch = now

        logger.info(f"  {len(new_items)}건 수집 / 분석 완료")
        return len(new_items)

    def get_signal_boost(self, market: str) -> Tuple[float, float]:
        """Returns:
            (sentiment_score, signal_boost)
            sentiment_score: -1.0 ~ +1.0
            signal_boost:      (-2.0 = /, +2.0 = /)"""
        score = self._market_scores.get(market, 0.0)
        # 전체 시장 점수도 반영
        global_score = self._market_scores.get("GLOBAL", 0.0)
        combined = score * 0.7 + global_score * 0.3

        # signal_boost: 양수 감성 → 임계값 낮춤 (매수 쉬워짐)
        #               음수 감성 → 임계값 높임 (매수 어려워짐)
        boost = combined * -2.0  # -2.0 ~ +2.0
        boost = max(-2.0, min(2.0, boost))  # 클리핑

        return round(combined, 3), round(boost, 3)

    def can_buy(self, market: str) -> Tuple[bool, str]:
        """Returns:
            (can_buy, reason)"""
        score, _ = self.get_signal_boost(market)
        if score < -0.6:
            reason = f"뉴스 감성 매우 부정 ({score:.2f}): {self._get_top_negative(market)}"
            return False, reason
        return True, "OK"

    def get_recent_news(
        self, market: Optional[str] = None, n: int = 5
    ) -> List[Dict]:
        """docstring"""
        items = list(self._news_cache)
        if market:
            coin = market.replace("KRW-", "").lower()
            items = [i for i in items if coin in i.title.lower() or market in i.coins]
        items = sorted(items, key=lambda x: x.published_at, reverse=True)[:n]
        return [
            {
                "title": i.title,
                "source": i.source,
                "sentiment": i.sentiment_score,
                "time": datetime.fromtimestamp(i.published_at).strftime("%H:%M"),
            }
            for i in items
        ]

    def get_dashboard_summary(self) -> Dict:
        """docstring"""
        positive = sum(1 for i in self._news_cache if i.sentiment_score > 0.3)
        negative = sum(1 for i in self._news_cache if i.sentiment_score < -0.3)
        neutral  = len(self._news_cache) - positive - negative

        global_score = self._market_scores.get("GLOBAL", 0.0)
        return {
            "total_news": len(self._news_cache),
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "global_sentiment": global_score,
            "market_scores": dict(self._market_scores),
        }

    # ── 뉴스 수집 ───────────────────────────────────────────────

    async def _fetch_cryptopanic(self) -> List[NewsItem]:
        """CryptoPanic API"""
        if not self.api_key:
            return []
        try:
            params = {
                "auth_token": self.api_key,
                "kind": "news",
                "filter": "important",
                "public": "true",
            }
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(self.CRYPTOPANIC_URL, params=params) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()

            items = []
            for post in data.get("results", []):
                title = post.get("title", "")
                url   = post.get("url", "")
                ts    = post.get("published_at", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_unix = dt.timestamp()
                except Exception:
                    ts_unix = time.time()

                item = NewsItem(title=title, url=url, published_at=ts_unix,
                                source="cryptopanic")
                # 관련 코인
                for currency in post.get("currencies", []):
                    code = currency.get("code", "")
                    if code:
                        item.coins.append(f"KRW-{code}")
                items.append(item)
            return items

        except Exception as e:
            logger.debug(f"CryptoPanic  : {e}")
            return []

    async def _fetch_coindesk_rss(self) -> List[NewsItem]:
        """CoinDesk RSS"""
        try:
            import xml.etree.ElementTree as ET
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(self.COINDESK_RSS) as resp:
                    if resp.status != 200:
                        return []
                    content = await resp.text()

            root = ET.fromstring(content)
            items = []
            for entry in root.iter("item"):
                title = entry.findtext("title", "")
                link  = entry.findtext("link", "")
                pub   = entry.findtext("pubDate", "")
                try:
                    from email.utils import parsedate_to_datetime
                    ts = parsedate_to_datetime(pub).timestamp()
                except Exception:
                    ts = time.time()

                item = NewsItem(title=title, url=link, published_at=ts,
                                source="coindesk")
                items.append(item)
            return items[:20]  # 최신 20개

        except Exception as e:
            logger.debug(f"CoinDesk RSS  : {e}")
            return []

    # ── 감성 분석 ───────────────────────────────────────────────

    def _score_item(self, item: NewsItem):
        """docstring"""
        title_lower = item.title.lower()

        # 1단계: FinBERT (고정밀)
        if self._finbert:
            try:
                result = self._finbert(item.title[:512])[0]
                label = result["label"].lower()
                conf  = result["score"]
                if label == "positive":
                    item.sentiment_score = conf
                elif label == "negative":
                    item.sentiment_score = -conf
                else:
                    item.sentiment_score = 0.0
                return
            except Exception:
                pass

        # 2단계: VADER
        if self._vader:
            try:
                vs = self._vader.polarity_scores(item.title)
                item.sentiment_score = vs["compound"]  # -1 ~ +1
                # 규칙 기반으로 보정
                item.sentiment_score = self._apply_keywords(
                    title_lower, item.sentiment_score
                )
                return
            except Exception:
                pass

        # 3단계: 순수 키워드 규칙
        item.sentiment_score = self._keyword_score(title_lower)

    def _keyword_score(self, text: str) -> float:
        """docstring"""
        score = 0.0
        hits = 0
        for kw, val in BULLISH_KEYWORDS.items():
            if kw in text:
                score += val
                hits += 1
        for kw, val in BEARISH_KEYWORDS.items():
            if kw in text:
                score += val
                hits += 1
        if hits == 0:
            return 0.0
        return max(-1.0, min(1.0, score / (hits * 4)))  # 정규화

    def _apply_keywords(self, text: str, base_score: float) -> float:
        """VADER"""
        kw_score = self._keyword_score(text)
        # 70% VADER + 30% 키워드 가중 평균
        return base_score * 0.7 + kw_score * 0.3

    def _update_market_scores(self):
        """1"""
        cutoff = time.time() - 3600  # 1시간 이내만
        market_sums = defaultdict(list)
        global_sums = []

        for item in self._news_cache:
            if item.published_at < cutoff:
                continue
            # 글로벌 영향
            global_sums.append(item.sentiment_score)
            # 코인별 영향
            for market in item.coins:
                market_sums[market].append(item.sentiment_score)
            # 코인 키워드 검색
            title_lower = item.title.lower()
            for market, kws in COIN_KEYWORDS.items():
                for kw in kws:
                    if kw in title_lower and market not in item.coins:
                        market_sums[market].append(item.sentiment_score)

        # 평균 계산
        self._market_scores = defaultdict(float)
        if global_sums:
            self._market_scores["GLOBAL"] = sum(global_sums) / len(global_sums)
        for market, scores in market_sums.items():
            if scores:
                self._market_scores[market] = sum(scores) / len(scores)

    def _get_top_negative(self, market: str) -> str:
        """docstring"""
        coin = market.replace("KRW-", "").lower()
        neg_items = [
            i for i in self._news_cache
            if i.sentiment_score < -0.3 and (
                coin in i.title.lower() or market in i.coins
            )
        ]
        if neg_items:
            worst = min(neg_items, key=lambda x: x.sentiment_score)
            return worst.title[:60]
        return "부정 뉴스 다수"

    def _is_gpu_available(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False


# ──────────────────────────────────────────────────────────────
#  엔진 통합용 편의 함수
# ──────────────────────────────────────────────────────────────


_global_analyzer: Optional[NewsSentimentAnalyzer] = None


def get_news_analyzer() -> NewsSentimentAnalyzer:
    """docstring"""
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = NewsSentimentAnalyzer(
            use_finbert=True,   # FinBERT GPU 감성 모델 활성화
            cache_hours=1,
            api_key=os.environ.get("CRYPTOPANIC_API_KEY", ""),
        )
    return _global_analyzer


# ──────────────────────────────────────────────────────────────
#  CLI 테스트
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    async def _test():
        analyzer = NewsSentimentAnalyzer()
        n = await analyzer.fetch_news()
        print(f"\n : {n}")

        for market in ["KRW-BTC", "KRW-ETH", "KRW-XRP"]:
            score, boost = analyzer.get_signal_boost(market)
            can_buy, reason = analyzer.can_buy(market)
            print(
                f"\n{market}: ={score:+.3f} | ={boost:+.2f} | "
                f"{' ' if can_buy else ' ' + reason}"
            )

        print("\n===   ===")
        for news in analyzer.get_recent_news(n=5):
            print(
                f"[{news['time']}] {news['sentiment']:+.2f} | "
                f"{news['title'][:60]}... ({news['source']})"
            )

    asyncio.run(_test())
