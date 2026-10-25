"""Schlanker, rein lesender Client für die REST-Schnittstelle von RWTHonline (CAMPUSonline).

Zugang = Token aus localStorage + Session-Cookie, beides vom Login-Fenster.
Absichtlich nur GET (plus Token-Erneuerung): Das Programm meldet nie etwas an
oder ab, auch wenn das Token es dürfte.
"""

import json
import os
from pathlib import Path

import requests

SITE = "https://online.rwth-aachen.de/RWTHonline"
REST = f"{SITE}/ee/rest"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rwth-tui"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rwth-tui"
SESSION_FILE = CONFIG_DIR / "session.json"


class RwthError(Exception):
    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


class NotLoggedIn(RwthError):
    pass


def load_session():
    try:
        return json.loads(SESSION_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_session(session):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.touch(mode=0o600)
    SESSION_FILE.chmod(0o600)
    SESSION_FILE.write_text(json.dumps(session))


def delete_session():
    SESSION_FILE.unlink(missing_ok=True)


class Rwth:
    def __init__(self, session=None):
        self.data = session or load_session()
        if not self.data:
            raise NotLoggedIn("Nicht angemeldet – erst `rwth login` ausführen.", "nosession")
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "rwth-tui", "Accept": "application/json"})
        for c in self.data["cookies"]:
            self.http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        (CACHE_DIR / "json").mkdir(parents=True, exist_ok=True)

    def _get(self, path, params):
        return self.http.get(f"{REST}/{path.lstrip('/')}", params=params, timeout=20,
                             headers={"Authorization": f"Bearer {self.data['access_token']}"})

    def _refresh(self):
        """Neues Token holen, wie es die Web-App bei 401 tut (läuft über das Session-Cookie)."""
        r = self.http.post(f"{REST}/auth/token/refresh", data="", timeout=20,
                           headers={"Content-Type": "application/json",
                                    "Authorization": f"Bearer {self.data['access_token']}"})
        token = r.json().get("accessToken") if r.status_code == 201 else None
        if not token:
            raise NotLoggedIn("Sitzung abgelaufen – `rwth login` ausführen.", "expired")
        self.data["access_token"] = token
        self.data["cookies"] = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                                for c in self.http.cookies]
        save_session(self.data)

    def get(self, path, **params):
        """GET auf einen REST-Pfad; bei Netzfehlern gibt es die letzte Antwort aus dem Cache."""
        cache = CACHE_DIR / "json" / (path.strip("/").replace("/", "_") + ".json")
        try:
            r = self._get(path, params)
            if r.status_code == 401:
                self._refresh()
                r = self._get(path, params)
        except (requests.ConnectionError, requests.Timeout):
            if cache.exists() and not params:
                return json.loads(cache.read_text())
            raise RwthError("Keine Verbindung zu RWTHonline.", "offline")
        if r.status_code >= 400:
            raise RwthError(f"RWTHonline antwortet {r.status_code} auf {path}", str(r.status_code))
        result = r.json()
        if not params:
            cache.write_text(json.dumps(result))
        return result
