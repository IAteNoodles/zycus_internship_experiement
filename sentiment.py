from __future__ import annotations
import json
import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel

from projects import SentimentEntry, SentimentScore

load_dotenv()


class SentimentResponse(BaseModel):
    sentiment: SentimentScore


_SYSTEM = ('Classify the sentiment of a PM status comment as positive, neutral, or negative. '
           'Hedged concern or diplomatic criticism counts as negative. '
           'Respond with ONLY JSON: {"sentiment": "positive"}')

_client = None
_client_unavailable = False
_model = None


def _get_client():
    global _client, _client_unavailable, _model
    if _client or _client_unavailable:
        return _client
    api_key, base_url, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_BASE_URL"), os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        _client_unavailable = True
        return None
    from openai import OpenAI
    _client = OpenAI(api_key=api_key, base_url=base_url or None)
    _model = model
    return _client


def _llm_sentiment(text: str) -> Optional[SentimentScore]:
    client = _get_client()
    if not client:
        return None
    try:
        resp = client.chat.completions.create(
            model=_model,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": text}],
            max_tokens=20, temperature=0, response_format={"type": "json_object"},
        )
        return SentimentResponse.model_validate(json.loads(resp.choices[0].message.content)).sentiment
    except Exception:
        return None


_hf = None
_hf_unavailable = False


def _get_hf():
    global _hf, _hf_unavailable
    if _hf or _hf_unavailable:
        return _hf
    try:
        from transformers import pipeline
        _hf = pipeline("sentiment-analysis")
    except Exception:
        _hf_unavailable = True
    return _hf


def _pipeline_sentiment(text: str) -> Optional[SentimentScore]:
    clf = _get_hf()
    if not clf:
        return None
    try:
        label = clf(text[:512])[0]["label"].lower()
        return SentimentResponse(sentiment=label).sentiment
    except Exception:
        return None


_NEG = {"frustrated", "concerned", "worried", "delay", "delayed", "blocked",
        "risk", "escalate", "behind", "issue", "problem", "poor", "failed"}
_POS = {"pleased", "happy", "confident", "great", "smooth", "on track",
        "ahead", "impressed", "satisfied", "strong", "positive"}


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
    if mode == "llm":
        result = _llm_sentiment(text)
    elif mode == "pipeline":
        result = _pipeline_sentiment(text)
    else:
        result = _llm_sentiment(text) or _pipeline_sentiment(text)
    return result or _keyword_sentiment(text)


def make_sentiment_entry(source: str, comment: str, date_recorded,
                          mode: str = "auto", notes: Optional[str] = None) -> SentimentEntry:
    return SentimentEntry(source=source, date_recorded=date_recorded, comment=comment,
                           score=analyze_sentiment(comment, mode), notes=notes)


if __name__ == "__main__":
    for s in ["Frustrated with the pace of legal approvals.",
              "Really pleased with how the team has handled the migration.",
              "The kickoff meeting is scheduled for next Tuesday.", ""]:
        print(f"{analyze_sentiment(s).value:10s} | {s!r}")