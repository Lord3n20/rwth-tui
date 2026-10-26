"""Schlanker, rein lesender Client für die REST-Schnittstelle von RWTHonline (CAMPUSonline).

Zugang = Token + Session-Cookie aus sso.py (oder dem Login-Fenster); läuft die
Sitzung ab, meldet sich der Client mit den gespeicherten Zugangsdaten neu an.
Absichtlich nur GET (plus Token-Erneuerung): Das Programm meldet nie etwas an
oder ab, auch wenn das Token es dürfte.
"""

import hashlib
import json
import os
from pathlib import Path

import requests

SITE = "https://online.rwth-aachen.de/RWTHonline"
REST = f"{SITE}/ee/rest"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rwth-tui"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rwth-tui"
SESSION_FILE = CONFIG_DIR / "session.json"
CTX = "design=ca;lang=de"


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
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": "rwth-tui", "Accept": "application/json"})
        self._use(session or load_session() or self._relogin("nosession"))
        (CACHE_DIR / "json").mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "json").chmod(0o700)  # enthält Noten

    def _use(self, session):
        self.data = session
        self.http.cookies.clear()
        for c in session["cookies"]:
            self.http.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])

    def _relogin(self, code):
        """Mit gespeicherten Zugangsdaten still neu anmelden (sso.py), sonst an `rwth login` verweisen."""
        from .sso import Login, LoginError
        try:
            session = Login().run()
        except LoginError as e:
            raise NotLoggedIn(f"Nicht angemeldet ({e}) – `rwth login` ausführen.", code)
        save_session(session)
        return session

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
            self._use(self._relogin("expired"))
            return
        self.data["access_token"] = token
        self.data["cookies"] = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                                for c in self.http.cookies]
        save_session(self.data)

    def get(self, path, **params):
        """GET auf einen REST-Pfad; bei Netzfehlern gibt es die letzte Antwort aus dem Cache."""
        name = path.strip("/").replace("/", "_")
        if params:
            name += "_" + hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
        cache = CACHE_DIR / "json" / (name + ".json")
        try:
            r = self._get(path, params)
            if r.status_code == 401:
                self._refresh()
                r = self._get(path, params)
        except (requests.ConnectionError, requests.Timeout):
            if cache.exists():
                return json.loads(cache.read_text())
            raise RwthError("Keine Verbindung zu RWTHonline.", "offline")
        if r.status_code >= 400:
            raise RwthError(f"RWTHonline antwortet {r.status_code} auf {path}", str(r.status_code))
        result = r.json()
        cache.write_text(json.dumps(result))
        return result

    # Die Abfragen der Web-App nachgebaut (Filter/Sortierung aus dem Frontend-Code ee/ui/ca2).
    # Ohne vollständigen Filter antwortet myCourses mit 500.

    def studies(self):
        return [r["content"]["CmStudyInfoDto"] for r in self.get("slc.cm.cs/student/myStudies/chooser")["resource"]]

    def study_progress(self, study):
        """Fortschritt laut „Mein Studium“: ECTS erreicht/gesamt und der offizielle Schnitt."""
        path = study["myStudyLink"]["href"].split("/ee/rest/", 1)[1]
        return self.get(path)["resource"][0]["content"]["myCurriculumElementDto"].get("progressInfo") or {}

    def current_term(self):
        return self.get("slc.lib.sm/currentTerm")["resource"][0]["content"]["semesterLibDto"]

    def semesters(self):
        return self.get("slc.lib.tm/semesters/student")["semesters"]

    def achievements(self):
        return [r["content"]["achievementDto"] for r in self.get("slc.xm.ac/achievements")["resource"]]

    def registrations(self, term_id):
        return self.get("slc.tm.cp/student/myCourses", **{
            "$ctx": CTX, "$filter": f"termId-eq={term_id};filterTerm-like=;courseGroupRegistrationStatusIds-in=",
            "$orderBy": "title=ascnf", "$skip": "0", "$top": "200"})["registrations"]

    def registered_exams(self):
        return self.get("slc.xm.exr/registered-exams", **{"$ctx": CTX})["examOffers"]
