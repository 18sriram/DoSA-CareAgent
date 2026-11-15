"""Records every call to an external model (speech-to-text, text-to-speech, language model): did it work, how long
did it take. The admin 'System and models' page reads this. Monitoring must never break a patient call."""
import functools
import time

sink = None   # set at startup to db.record_model_call


def record(provider, kind, ok, ms, detail=""):
    if sink is None:
        return
    try:
        sink(provider, kind, ok, ms, detail)
    except Exception:
        pass


def monitored(provider, kind):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                record(provider, kind, False, int((time.time() - t0) * 1000), f"{type(e).__name__}: {str(e)[:160]}")
                raise
            record(provider, kind, True, int((time.time() - t0) * 1000))
            return result
        return wrapper
    return deco
