from __future__ import annotations
import json
import os
from typing import Optional, Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "groq",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama3-8b-8192",
    },
    {
        "name": "openrouter",
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-3.5-turbo",
    },
]


def ask_llm(
    system: str,
    user: str,
    max_tokens: int = 500,
    temperature: float = 0,
    json_mode: bool = False,
) -> Optional[str]:
    for p in PROVIDERS:
        api_key = os.getenv(p["env_key"])
        if not api_key:
            continue
        try:
            client = OpenAI(api_key=api_key, base_url=p["base_url"])
            kwargs = dict(model=p["model"], messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ], max_tokens=max_tokens, temperature=temperature)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            return client.chat.completions.create(**kwargs).choices[0].message.content
        except Exception:
            continue
    return None


def ask_llm_json(system: str, user: str, max_tokens: int = 200, temperature: float = 0) -> Optional[dict]:
    raw = ask_llm(system, user, max_tokens=max_tokens, temperature=temperature, json_mode=True)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
