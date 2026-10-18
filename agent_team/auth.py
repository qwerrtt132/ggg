"""Owner login for the public site: one password, a signed session cookie, and a limit on wrong guesses.

The password is kept only as a salted scrypt hash. A session is a timestamp and an HMAC over it, so any instance that
knows the session secret can check it; nothing is stored per session. A session cannot be revoked before it expires,
so the lifetime is short. Changing the session secret signs every session out at once.
"""

import hashlib
import hmac
import secrets
import threading
import time

COOKIE_NAME = "agent_team_session"
SESSION_SECONDS = 12 * 3600
FAILURE_LIMIT = 10  # wrong passwords allowed in the window, across everyone, since there is one owner
FAILURE_WINDOW = 15 * 60
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
MIN_SECRET_LENGTH = 32


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest)
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


class OwnerAuth:
    def __init__(self, password_hash, secret, public_host, clock=time.time):
        self.password_hash = password_hash
        self.secret = secret
        self.public_host = public_host
        self.clock = clock
        self.failures = []  # when each recent wrong password was tried
        self.lock = threading.Lock()

    def check_password(self, password):
        return verify_password(password, self.password_hash)

    def new_session(self):
        expires = int(self.clock()) + SESSION_SECONDS
        return f"{expires}.{self._sign(expires)}"

    def valid_session(self, cookie):
        try:
            expires_text, signature = cookie.split(".")
            expires = int(expires_text)
        except (ValueError, AttributeError):
            return False
        return hmac.compare_digest(signature, self._sign(expires)) and expires > self.clock()

    def blocked(self):
        with self.lock:
            self._forget_old_failures()
            return len(self.failures) >= FAILURE_LIMIT

    def record_failure(self):
        with self.lock:
            self._forget_old_failures()
            self.failures.append(self.clock())

    def _forget_old_failures(self):
        cutoff = self.clock() - FAILURE_WINDOW
        self.failures = [when for when in self.failures if when > cutoff]

    def _sign(self, expires):
        return hmac.new(self.secret.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


def public_settings(environ):
    """The settings public mode needs, or None when the server should run privately on this machine."""
    host = environ.get("AGENT_TEAM_PUBLIC_HOST")
    if not host:
        return None
    missing = [name for name in ("AGENT_TEAM_OWNER_PASSWORD_HASH", "AGENT_TEAM_SESSION_SECRET") if not environ.get(name)]
    if missing:
        raise ValueError(f"public mode needs these settings: {', '.join(missing)}")
    secret = environ["AGENT_TEAM_SESSION_SECRET"]
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"AGENT_TEAM_SESSION_SECRET must be at least {MIN_SECRET_LENGTH} characters")
    return {"host": host, "password_hash": environ["AGENT_TEAM_OWNER_PASSWORD_HASH"], "secret": secret}
