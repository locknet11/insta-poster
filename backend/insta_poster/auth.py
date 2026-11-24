from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 256

# Precomputed dummy hash used to keep failed login timing constant for unknown usernames
_DUMMY_SALT = b"insta-poster-dummy-salt!"
_DUMMY_DK = hashlib.scrypt(
    b"insta-poster-dummy-password",
    salt=_DUMMY_SALT,
    n=SCRYPT_N,
    r=SCRYPT_R,
    p=SCRYPT_P,
    maxmem=SCRYPT_MAXMEM,
    dklen=SCRYPT_DKLEN,
)


def normalize_username(username: str) -> str:
    cleaned = username.strip().lower()
    if not USERNAME_RE.match(cleaned):
        raise ValueError(
            "El nombre de usuario debe tener entre 2 y 64 caracteres, "
            "empezar con letra o número y usar solo letras, números, '.', '_' o '-'."
        )
    return cleaned


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña no puede superar los {MAX_PASSWORD_LENGTH} caracteres.")
    return password


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=SCRYPT_DKLEN,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${dk_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, n_s, r_s, p_s, salt_b64, dk_b64 = stored_hash.split("$", 5)
        if scheme != "scrypt":
            return False
        n = int(n_s)
        r = int(r_s)
        p = int(p_s)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected_dk = base64.b64decode(dk_b64.encode("ascii"))
    except Exception:
        return False

    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=SCRYPT_MAXMEM,
        dklen=len(expected_dk),
    )
    return hmac.compare_digest(derived, expected_dk)


def verify_dummy_password(password: str) -> None:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=_DUMMY_SALT,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=SCRYPT_DKLEN,
    )
    hmac.compare_digest(derived, _DUMMY_DK)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


API_KEY_PREFIX = "ipk_"


def normalize_api_key_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    if not cleaned:
        raise ValueError("El nombre de la API Key es obligatorio.")
    if len(cleaned) > 64:
        raise ValueError("El nombre de la API Key no puede superar los 64 caracteres.")
    return cleaned


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, display_prefix, sha256_hash)."""
    raw = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    prefix = raw[:12]
    key_hash = hash_api_key(raw)
    return raw, prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def sign_media_filename(filename: str, secret: str, *, ttl_seconds: int = 1800) -> tuple[int, str]:
    exp = int(time.time()) + max(60, ttl_seconds)
    msg = f"{filename}:{exp}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return exp, sig


def verify_media_signature(filename: str, exp: int, sig: str, secret: str) -> bool:
    if not filename or not sig or not secret:
        return False
    if int(time.time()) > exp:
        return False
    msg = f"{filename}:{exp}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


class LoginRateLimiter:
    """Simple in-memory sliding-window limiter for failed login attempts."""

    def __init__(
        self,
        *,
        max_attempts_per_pair: int = 10,
        max_attempts_per_ip: int = 30,
        window_seconds: int = 300,
    ) -> None:
        self._max_pair = max_attempts_per_pair
        self._max_ip = max_attempts_per_ip
        self._window = window_seconds
        self._pair_hits: dict[tuple[str, str], deque[float]] = {}
        self._ip_hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()

    def check_allowed(self, ip: str, username: str) -> tuple[bool, int]:
        now = time.monotonic()
        pair_key = (ip, username.strip().lower())
        with self._lock:
            pair_dq = self._pair_hits.get(pair_key)
            if pair_dq:
                self._prune(pair_dq, now)
                if len(pair_dq) >= self._max_pair:
                    retry_after = max(1, int(self._window - (now - pair_dq[0])))
                    return False, retry_after

            ip_dq = self._ip_hits.get(ip)
            if ip_dq:
                self._prune(ip_dq, now)
                if len(ip_dq) >= self._max_ip:
                    retry_after = max(1, int(self._window - (now - ip_dq[0])))
                    return False, retry_after

        return True, 0

    def record_failure(self, ip: str, username: str) -> None:
        now = time.monotonic()
        pair_key = (ip, username.strip().lower())
        with self._lock:
            pair_dq = self._pair_hits.setdefault(pair_key, deque())
            self._prune(pair_dq, now)
            pair_dq.append(now)

            ip_dq = self._ip_hits.setdefault(ip, deque())
            self._prune(ip_dq, now)
            ip_dq.append(now)

    def clear(self, ip: str, username: str) -> None:
        pair_key = (ip, username.strip().lower())
        with self._lock:
            self._pair_hits.pop(pair_key, None)
