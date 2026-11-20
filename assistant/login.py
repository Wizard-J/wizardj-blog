"""Password verification and short-lived browser sessions for the assistant."""
import argparse
from collections import deque
from getpass import getpass
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import os
from pathlib import Path
import secrets
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit

COOKIE = "__Secure-wizardj_session"
SESSION_TTL = 8 * 60 * 60
ENTRY_PATHS = {
    "/assistant", "/assistant/", "/assistant/weixin", "/assistant/weixin/", "/assistant/cf-select/",
}


def return_target(value):
    if not value.startswith("/") or value.startswith("//") or len(value) > 2048:
        return "/assistant/"
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        return "/assistant/"
    url = urlsplit(value)
    if url.path not in ENTRY_PATHS:
        return "/assistant/"
    path = url.path.rstrip("/") + "/"
    personas = parse_qs(url.query).get("persona", [])
    if path == "/assistant/" and len(personas) == 1 and personas[0] in {"munger", "wizard"}:
        return f"{path}?persona={personas[0]}"
    return path


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)


def session_cookie(token="", max_age=0):
    return f"{COOKIE}={token}; Path=/assistant; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"


class Login:
    def __init__(self, username, hash_file):
        self.username = username.encode()
        stored_hash = Path(hash_file).read_text().strip()
        if not self.username:
            raise ValueError("Invalid assistant login username")
        if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
            import bcrypt

            digest = stored_hash.encode("ascii")
            bcrypt.checkpw(b"configuration check", digest)
            self.verify_password = lambda password: (
                len(password.encode()) <= 72 and bcrypt.checkpw(password.encode(), digest)
            )
        else:
            salt_hex, digest_hex = stored_hash.split(":")
            salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
            if len(salt) != 16 or len(digest) != 32:
                raise ValueError("Invalid assistant login password hash")
            self.verify_password = lambda password: hmac.compare_digest(password_hash(password, salt), digest)
        self.lock = Lock()
        self.attempts = deque()
        self.sessions = {}

    def sign_in(self, username, password, cookie_header):
        with self.lock:
            now = monotonic()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 12:
                return 429, None
            self.attempts.append(now)
            valid_user = hmac.compare_digest(username.encode(), self.username)
            valid_password = self.verify_password(password)
            if not (valid_user and valid_password):
                return 401, None
            self.sessions = {token: expires for token, expires in self.sessions.items() if expires > now}
            self.sessions.pop(self.token(cookie_header), None)
            if len(self.sessions) >= 64:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + SESSION_TTL
            return 303, token

    @staticmethod
    def token(cookie_header):
        try:
            cookie = SimpleCookie(cookie_header).get(COOKIE)
        except CookieError:
            return None
        return cookie.value if cookie else None

    def signed_in(self, cookie_header):
        with self.lock:
            return self.sessions.get(self.token(cookie_header), 0) > monotonic()

    def sign_out(self, cookie_header):
        with self.lock:
            self.sessions.pop(self.token(cookie_header), None)


def main():
    parser = argparse.ArgumentParser(description="Create a private assistant password hash file.")
    parser.add_argument("hash_file", type=Path)
    args = parser.parse_args()
    password = getpass("Password: ")
    if not 12 <= len(password) <= 256 or password != getpass("Confirm password: "):
        parser.error("Use a matching password of 12–256 characters.")
    salt = secrets.token_bytes(16)
    digest = password_hash(password, salt)
    with open(args.hash_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
        output.write(f"{salt.hex()}:{digest.hex()}\n")


if __name__ == "__main__":
    main()
