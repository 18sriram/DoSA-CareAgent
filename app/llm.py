"""One place that talks to the language model (Groq, OpenAI-compatible API).
To switch provider later, only this file changes."""
import json
import os
import requests
from dotenv import load_dotenv

from app.monitor import monitored

load_dotenv()
GROQ_KEY = os.getenv("GROQ_API_KEY")
MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# If the chosen model is retired or unavailable, try these next.
FALLBACK_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
BASE = "https://api.groq.com/openai/v1"


class LLMError(Exception):
    pass


def _headers():
    if not GROQ_KEY:
        raise LLMError("GROQ_API_KEY is not set (check your .env file and that you run from the project folder)")
    return {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}


def list_models() -> list:
    r = requests.get(f"{BASE}/models", headers=_headers(), timeout=30)
    if r.status_code != 200:
        raise LLMError(f"Could not list models ({r.status_code}): {r.text[:200]}")
    return sorted(m["id"] for m in r.json().get("data", []))


@monitored("groq", "llm")
def ask_json(system: str, user: str) -> dict:
    """Send a prompt and return the model's answer parsed as a JSON object."""
    headers = _headers()
    tried, errors = [], []
    for model in [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]:
        tried.append(model)
        r = requests.post(
            f"{BASE}/chat/completions", headers=headers, timeout=60,
            json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        if r.status_code == 200:
            try:
                return json.loads(r.json()["choices"][0]["message"]["content"])
            except (KeyError, ValueError) as e:
                raise LLMError(f"Could not read output of {model}: {e}")
        errors.append(f"{model}: {r.status_code} {r.text[:160]}")
        # Only a model problem is worth retrying with another model; a bad key or rate limit is not.
        if r.status_code not in (400, 404):
            break
    raise LLMError(" | ".join(errors))
