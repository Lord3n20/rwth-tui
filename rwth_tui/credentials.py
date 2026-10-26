"""Zugangsdaten in ~/.config/rwth-tui/credentials.json (600, unverschlüsselt) und TOTP-Codes ohne Fremdpaket."""

import base64
import getpass
import hashlib
import hmac
import struct
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CREDENTIALS = Path.home() / ".config/rwth-tui/credentials.json"

KEYS = {"user": "Benutzername (ab123456)", "password": "RWTH-Passwort", "totp": "TOTP-Geheimnis (Base32 oder otpauth://-Link)"}


def _load():
    try:
        return json.loads(CREDENTIALS.read_text())
    except FileNotFoundError:
        return {}


def store(key, value):
    data = _load()
    data[key] = value
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CREDENTIALS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(CREDENTIALS, 0o600)


def lookup(key):
    return _load().get(key)


def normalize_totp(value):
    value = value.strip()
    if value.startswith("otpauth://"):
        value = parse_qs(urlparse(value).query)["secret"][0]
    value = value.replace(" ", "").upper()
    base64.b32decode(value + "=" * (-len(value) % 8))  # wirft bei Tippfehlern
    return value


def totp(secret, at=None, step=30, digits=6):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10 ** digits).zfill(digits)


def setup(args):
    """`rwth secret [user|password|totp]` – fragt verdeckt ab und speichert in CREDENTIALS."""
    for key in args or KEYS:
        value = getpass.getpass(f"{KEYS[key]}: ")
        if not value.strip():
            print(f"{key}: übersprungen")
            continue
        if key == "totp":
            value = normalize_totp(value)
        store(key, value.strip() if key == "user" else value)
        print(f"{key}: gespeichert ✓")
    return 0


def code(_args):
    """`rwth code` – aktueller TOTP-Code, z. B. zum Bestätigen des neuen Tokens."""
    secret = lookup("totp")
    if not secret:
        print("Kein TOTP-Geheimnis gespeichert – erst `rwth secret totp`.")
        return 1
    print(f"{totp(secret)}  (noch {30 - int(time.time()) % 30} s gültig)")
    return 0
