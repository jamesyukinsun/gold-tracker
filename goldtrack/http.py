"""HTTP layer: stdlib-only fetch with retry, gzip, and a TTL disk cache.

No third-party dependencies on purpose — this must run anywhere Python 3.9+
exists (the host has no `requests` installed).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import zlib

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "cache")

_last_call: dict[str, float] = {}
# Per-host minimum seconds between requests. Yahoo is the strictest; be a good citizen.
HOST_MIN_INTERVAL = {
    "query1.finance.yahoo.com": 0.6,
    "query2.finance.yahoo.com": 0.6,
    "www.sge.com.cn": 1.0,
    "publicreporting.cftc.gov": 0.4,
}

# The live engine polls from several threads at once, so the throttle and the
# cache must be guarded or two pollers can clobber each other's bookkeeping.
_throttle_lock = threading.Lock()
_stats_lock = threading.Lock()

# Rolling request counters, surfaced in the monitor's feed-health panel.
STATS = {"requests": 0, "errors": 0, "cache_hits": 0, "bytes": 0}


def bump(key: str, n: int = 1) -> None:
    with _stats_lock:
        STATS[key] = STATS.get(key, 0) + n


def stats_snapshot() -> dict:
    with _stats_lock:
        return dict(STATS)


class FetchError(RuntimeError):
    pass


def _throttle(host: str) -> None:
    """Serialise requests per host. Must hold the lock across the sleep so
    concurrent pollers queue up instead of all firing at once."""
    gap = HOST_MIN_INTERVAL.get(host, 0.15)
    with _throttle_lock:
        prev = _last_call.get(host)
        now = time.time()
        if prev is not None:
            wait = gap - (now - prev)
            if wait > 0:
                time.sleep(wait)
        _last_call[host] = time.time()


def _cache_path(url: str, tag: str = "") -> str:
    h = hashlib.sha256((url + "|" + tag).encode()).hexdigest()[:20]
    return os.path.join(CACHE_DIR, h + ".bin")


def _cache_get(url: str, ttl: float, tag: str = "") -> bytes | None:
    if ttl <= 0:
        return None
    p = _cache_path(url, tag)
    try:
        st = os.stat(p)
    except OSError:
        return None
    if (time.time() - st.st_mtime) > ttl:
        return None
    with open(p, "rb") as fh:
        return fh.read()


def _cache_put(url: str, body: bytes, tag: str = "") -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _cache_path(url, tag)
    tmp = p + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(body)
    os.replace(tmp, p)


def fetch_bytes(url: str, *, ttl: float = 0.0, retries: int = 3,
                timeout: float = 30.0, tag: str = "",
                headers: dict | None = None) -> bytes:
    """Fetch a URL, returning raw bytes. Serves from disk cache within `ttl`."""
    cached = _cache_get(url, ttl, tag)
    if cached is not None:
        bump("cache_hits")
        return cached

    from urllib.parse import urlparse

    host = urlparse(url).netloc
    hdrs = {
        "User-Agent": UA,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "*/*",
        "Connection": "close",
    }
    if headers:
        hdrs.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries):
        _throttle(host)
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            if ttl > 0:
                _cache_put(url, raw, tag)
            bump("requests")
            bump("bytes", len(raw))
            return raw
        except urllib.error.HTTPError as e:
            last_err = e
            bump("errors")
            body = b""
            try:
                body = e.read()[:200]
            except Exception:
                pass
            # 4xx other than 429 will not fix themselves.
            if 400 <= e.code < 500 and e.code != 429:
                raise FetchError(f"HTTP {e.code} for {url} :: {body[:160]!r}") from e
            time.sleep((2 ** attempt) * 0.8 + random.random() * 0.4)
        except Exception as e:  # noqa: BLE001 - network layer, retry on anything
            last_err = e
            bump("errors")
            time.sleep((2 ** attempt) * 0.8 + random.random() * 0.4)

    bump("errors")
    # Last resort: serve stale cache rather than fail outright.
    p = _cache_path(url, tag)
    if os.path.exists(p):
        with open(p, "rb") as fh:
            return fh.read()
    raise FetchError(f"failed after {retries} attempts: {url} :: {last_err}")


def fetch_json(url: str, **kw):
    raw = fetch_bytes(url, **kw)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise FetchError(f"bad JSON from {url}: {e} :: {raw[:200]!r}") from e


def fetch_text(url: str, encoding: str = "utf-8", **kw) -> str:
    return fetch_bytes(url, **kw).decode(encoding, "replace")
