"""Long recordings: Gnani's REST speech-to-text takes up to 30 seconds per clip, so a consultation is cut into
clips of at most 25 seconds. Each cut is placed at the quietest moment near the limit so words are not split."""
import array
import io
import os
import sys
import tempfile
import wave

from app.gnani_client import GnaniError, transcribe


def _wav_bytes(samples, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        s = array.array("h", samples)
        if sys.byteorder == "big":
            s.byteswap()
        w.writeframes(s.tobytes())
    return buf.getvalue()


def split_wav(data: bytes, max_seconds=25, search_seconds=4):
    """Return a list of WAV byte strings (16-bit mono). Raises ValueError if the audio is not 16-bit mono WAV."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
            frames = w.readframes(w.getnframes())
    except wave.Error as e:
        raise ValueError(f"Not a readable WAV file: {e}")
    if width != 2 or ch != 1:
        raise ValueError("Recording must be 16-bit mono WAV")
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    max_len, search, step = int(max_seconds * rate), int(search_seconds * rate), max(rate // 10, 1)
    chunks, start, n = [], 0, len(samples)
    while n - start > max_len:
        lo, hi = start + max_len - search, start + max_len
        best_pos, best_energy = hi, None
        for pos in range(lo, hi - step, step):
            energy = sum(abs(x) for x in samples[pos:pos + step])
            if best_energy is None or energy < best_energy:
                best_pos, best_energy = pos + step // 2, energy
        chunks.append(samples[start:best_pos])
        start = best_pos
    chunks.append(samples[start:])
    return [_wav_bytes(c, rate) for c in chunks if len(c) >= rate // 2]


def transcribe_long(data: bytes, language_code="te-IN", transcribe_fn=transcribe):
    """Transcribe a long recording. Returns (text, failed_part_numbers). A failed part does not stop the others."""
    parts, failed = [], []
    for i, chunk in enumerate(split_wav(data), start=1):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(chunk)
            path = tmp.name
        try:
            text = transcribe_fn(path, language_code=language_code)
            if text:
                parts.append(text)
        except GnaniError as e:
            print(f"[consultation] part {i} failed: {e}")
            failed.append(i)
        finally:
            os.remove(path)
    return " ".join(parts).strip(), failed
