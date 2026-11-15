"""Thin wrapper around Gnani's Prisma (speech-to-text) and Timbre (text-to-speech) REST APIs.

Everything else in the project talks to Gnani only through these two functions,
so if an endpoint or parameter changes, this is the one file to fix.
"""
import os
import time
import requests
from dotenv import load_dotenv

from app.monitor import monitored

load_dotenv()

BASE_URL = "https://api.vachana.ai"
API_KEY = os.getenv("GNANI_API_KEY")
LANGUAGE = os.getenv("PATIENT_LANGUAGE", "hi-IN")
VOICE = os.getenv("TTS_VOICE", "Nalini")
STT_MODEL = os.getenv("STT_MODEL", "")  # empty = default Prisma v2.5


class GnaniError(Exception):
    pass


RETRY_STATUS = {429, 500, 502, 503, 504}   # temporary problems on Gnani's side
ATTEMPTS = 3


def _post_with_retry(make_request, what):
    """Run make_request() up to ATTEMPTS times when Gnani is briefly overloaded or a timeout occurs."""
    last = ""
    for attempt in range(ATTEMPTS):
        try:
            r = make_request()
        except requests.exceptions.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code not in RETRY_STATUS:
                return r
            last = f"{r.status_code}: {r.text[:120]!r}"
        print(f"[Gnani {what}] attempt {attempt + 1}/{ATTEMPTS} failed ({last[:100]})")
        if attempt < ATTEMPTS - 1:
            time.sleep(1.5 * (attempt + 1))
    raise GnaniError(f"{what} failed after {ATTEMPTS} attempts: {last[:200]}")


def _headers():
    if not API_KEY:
        raise GnaniError("GNANI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return {"X-API-Key-ID": API_KEY}


@monitored("gnani", "stt")
def transcribe(audio_path: str, language_code: str = None) -> str:
    """Speech to text. Audio must be WAV/MP3/OGG/FLAC/AAC/M4A, 8-44.1 kHz, max 30 seconds."""
    data = {"language_code": language_code or LANGUAGE}
    if STT_MODEL:
        data["model"] = STT_MODEL
    def request():
        with open(audio_path, "rb") as f:
            return requests.post(
                f"{BASE_URL}/stt/v3",
                headers=_headers(),
                files={"audio_file": (os.path.basename(audio_path), f)},
                data=data,
                timeout=45,
            )
    r = _post_with_retry(request, "speech-to-text")
    if r.status_code != 200:
        raise GnaniError(f"STT failed ({r.status_code}): {r.text[:300]}")
    body = r.json()
    if not body.get("success", False):
        raise GnaniError(f"STT returned an error: {body}")
    return body.get("transcript", "").strip()


@monitored("gnani", "tts")
def speak(text: str, out_path: str, language: str = None) -> str:
    """Text to speech. Saves a WAV file at out_path and returns the path."""
    payload = {
        "text": text,
        "voice": VOICE,
        "model": "timbre-v2.5",
        "language": language or LANGUAGE,
        "speed": 1.0,
        "audio_config": {
            "sample_rate": 24000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }
    r = _post_with_retry(lambda: requests.post(
        f"{BASE_URL}/api/v1/tts/inference",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    ), "text-to-speech")
    if r.status_code != 200:
        raise GnaniError(f"TTS failed ({r.status_code}): {r.text[:300]}")
    if r.content[:4] != b"RIFF":
        raise GnaniError(f"TTS did not return a WAV file: {r.content[:200]!r}")
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path
