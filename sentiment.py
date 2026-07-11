from __future__ import annotations
import json
from typing import Optional

from pydantic import BaseModel

from projects import SentimentEntry, SentimentScore
from llm import ask_llm


class SentimentResponse(BaseModel):
    sentiment: SentimentScore


_SYSTEM = (
    "Classify the sentiment of a PM status comment as positive, neutral, or negative. "
    "Hedged concern or diplomatic criticism counts as negative. "
    'Respond with ONLY JSON: {"sentiment": "positive"}'
)


def _llm_sentiment(text: str) -> Optional[SentimentScore]:
    raw = ask_llm(_SYSTEM, text, max_tokens=20, temperature=0, json_mode=True)
    if not raw:
        return None
    try:
        return SentimentResponse.model_validate(json.loads(raw)).sentiment
    except Exception:
        return None


_NEG = {"frustrated", "concerned", "worried", "delay", "behind",
        "issue", "problem", "poor", "failed", "risk", "blocked"}
_POS = {"pleased", "happy", "confident", "great", "smooth", "ahead",
        "impressed", "satisfied", "strong", "positive"}


def _keyword_sentiment(text: str) -> SentimentScore:
    t = text.lower()
    neg = sum(w in t for w in _NEG)
    pos = sum(w in t for w in _POS)
    if neg > pos:
        return SentimentScore.NEGATIVE
    if pos > neg:
        return SentimentScore.POSITIVE
    return SentimentScore.NEUTRAL


def analyze_sentiment(text: Optional[str], mode: str = "auto") -> SentimentScore:
    if not text or not text.strip():
        return SentimentScore.UNKNOWN
    if mode in ("auto", "llm"):
        result = _llm_sentiment(text)
        if result is not None:
            return result
    if mode == "auto":
        pass
    return _keyword_sentiment(text)


def make_sentiment_entry(source: str, comment: str, date_recorded,
                          mode: str = "auto", notes: Optional[str] = None) -> SentimentEntry:
    return SentimentEntry(source=source, date_recorded=date_recorded, comment=comment,
                           score=analyze_sentiment(comment, mode), notes=notes)
