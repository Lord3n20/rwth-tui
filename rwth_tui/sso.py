"""Anmeldung bei RWTHonline rein über HTTP – ohne Browser.

Kette: Keycloak CAMPUSonline_SP (Client co-desktop-user, PKCE wie keycloak-js)
→ Keycloak RWTHprod_IDP → Shibboleth sso.rwth-aachen.de (Passwort, dann TOTP
vom Token „tui“ bzw. `rwth secret token`) → SAML zurück → Session-Cookie von RWTHonline, mit dem
auth/token/refresh das Zugangs-Token liefert.

Jede Seite wird an ihren Formularen erkannt, nicht an der URL. Passwort und Code
gehen höchstens einmal raus; ein falsches Passwort führt so nicht zur Sperre.
"""

import base64
import hashlib
import os
import re
import secrets
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin

import requests

from .credentials import lookup, token_name, totp

SITE = "https://online.rwth-aachen.de/RWTHonline"
REALM = f"{SITE}/co/public/sec/auth/realms/CAMPUSonline_SP/protocol/openid-connect"
CLIENT = "co-desktop-user"
REDIRECT = f"{SITE}/ee/ui/ca2/app/desktop/#/login"
DEBUG = bool(os.environ.get("RWTH_LOGIN_DEBUG"))


class LoginError(Exception):
    pass


def log(*args):
    if DEBUG:
        print(*args, file=sys.stderr)


class Page(HTMLParser):
    """Sammelt Formulare (Felder, Auswahllisten, Knöpfe), Links und Text einer Seite."""

    def __init__(self, html):
        super().__init__()
        self.forms, self.links, self.text = [], [], []
        self._form = self._select = self._button = self._link = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get").lower(),
                          "inputs": [], "selects": [], "buttons": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            kind = a.get("type", "text").lower()
            if kind == "submit":
                self._form["buttons"].append({"name": a.get("name", ""), "value": a.get("value", ""), "text": a.get("value", "")})
            else:
                self._form["inputs"].append({"name": a.get("name", ""), "type": kind, "value": a.get("value", ""),
                                             "checked": "checked" in a})
        elif tag == "select" and self._form is not None:
            self._select = {"name": a.get("name", ""), "options": []}
            self._form["selects"].append(self._select)
        elif tag == "option" and self._select is not None:
            self._select["options"].append({"value": a.get("value", ""), "text": ""})
        elif tag == "button" and self._form is not None and a.get("type", "submit").lower() == "submit":
            self._button = {"name": a.get("name", ""), "value": a.get("value", ""), "text": ""}
            self._form["buttons"].append(self._button)
        elif tag == "a" and "href" in a:
            self._link = {"href": a["href"], "text": ""}
            self.links.append(self._link)

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "button":
            self._button = None
        elif tag == "a":
            self._link = None

    def handle_data(self, data):
        self.text.append(data)
        for open_ in (self._button, self._link):
            if open_ is not None:
                open_["text"] += data.strip()
        if self._select is not None and self._select["options"]:
            self._select["options"][-1]["text"] += data.strip()

    def plain(self):
        return " ".join(" ".join(self.text).split())


def fields(form):
    """Was ein Browser beim Abschicken mitsendet (ohne Knopf)."""
    data = {}
    for i in form["inputs"]:
        if not i["name"] or (i["type"] in ("checkbox", "radio") and not i["checked"]):
            continue
        data[i["name"]] = i["value"]
    for s in form["selects"]:
        if s["options"]:
            data[s["name"]] = s["options"][0]["value"]
    return data


def describe(page):
    return [{"action": f["action"].split("?")[0], "inputs": [(i["type"], i["name"]) for i in f["inputs"]],
             "selects": [(s["name"], [o["text"] for o in s["options"]]) for s in f["selects"]],
             "buttons": [(b["name"], b["text"]) for b in f["buttons"]]} for f in page.forms]


class Login:
    def __init__(self):
        self.creds = {k: lookup(k) for k in ("user", "password", "totp")}
        if not all(self.creds.values()):
            raise LoginError("Zugangsdaten fehlen – erst `rwth secret`.")
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64) rwth-tui"
        self.done = set()

    def go(self, method, url, **kw):
        """Anfrage mit Weiterleitungen von Hand – die letzte zeigt mit #…code= auf die App."""
        for _ in range(20):
            r = self.http.request(method, url, allow_redirects=False, timeout=20, **kw)
            log(f"{r.status_code} {method} {url.split('?')[0]}  Cookies: {sorted({c.name for c in self.http.cookies})}")
            if r.status_code not in (301, 302, 303, 307, 308):
                return r, url
            url = urljoin(url, r.headers["Location"])
            if url.startswith(REDIRECT) or "/co-desktop/app/post-login" in url:
                return None, url  # angemeldet; die Seiten dahinter sind reines JavaScript
            method, kw = ("GET", {}) if r.status_code in (301, 302, 303) else (method, kw)
        raise LoginError("Zu viele Weiterleitungen.")

    def submit(self, url, form, extra=None, button=None):
        data = fields(form)
        if button and button["name"]:
            data[button["name"]] = button["value"]
        data.update(extra or {})
        target = urljoin(url, form["action"]) if form["action"] else url
        if form["method"] == "post":
            return self.go("POST", target, data=data)
        return self.go("GET", target + ("&" if "?" in target else "?") + urlencode(data))

    def once(self, step):
        if step in self.done:
            raise LoginError(f"Schritt „{step}“ kam zweimal – Anmeldung abgelehnt?")
        self.done.add(step)

    def step(self, r, url):
        page = Page(r.text)
        token = re.compile(rf"\b{re.escape(token_name())}\b", re.I)  # Beschreibung des Tokens im IdM-Selfservice
        log("  Seite:", (re.search(r"<title>(.*?)</title>", r.text, re.S) or [None, "?"])[1].strip(), describe(page))
        for form in page.forms:
            names = {i["name"] for i in form["inputs"]}
            visible = [i for i in form["inputs"] if i["type"] not in ("hidden", "checkbox", "radio")]
            proceed = next((b for b in form["buttons"] if b["name"] == "_eventId_proceed"), None)
            if "j_password" in names:
                self.once("password")
                return self.submit(url, form, {"j_username": self.creds["user"], "j_password": self.creds["password"]},
                                   proceed or {"name": "_eventId_proceed", "value": ""})
            for s in form["selects"]:
                o = next((o for o in s["options"] if token.search(o["text"])), None)
                if o:
                    self.once("select")
                    return self.submit(url, form, {s["name"]: o["value"]}, form["buttons"][0] if form["buttons"] else None)
            otp = next((i for i in visible if re.search(r"otp|totp|token|code", i["name"], re.I)), None)
            if otp:
                if not token.search(page.plain()):
                    other = next((b for b in form["buttons"] if re.search(r"andere auswahl|other", b["text"], re.I)), None)
                    if not other:
                        raise LoginError("Code-Seite für ein anderes Token, ohne „Andere Auswahl“.")
                    self.once("other")
                    return self.submit(url, form, None, other)
                self.once("otp")
                if 30 - int(time.time()) % 30 < 4:
                    time.sleep(5)  # Code nicht kurz vor Ablauf abschicken
                ok = next((b for b in form["buttons"] if not re.search(r"andere auswahl|other", b["text"], re.I)), None)
                return self.submit(url, form, {otp["name"]: totp(self.creds["totp"])}, ok)
            if not visible and ({"SAMLRequest", "SAMLResponse"} & names or proceed or len(page.forms) == 1):
                # Selbst abschickende Zwischenseiten (SAML-POST, Shibboleth-Speichertest)
                return self.submit(url, form, None, proceed)
        broker = next((a for a in page.links if "/broker/rwth-sso/" in a["href"]), None)
        if broker:
            self.once("broker")
            return self.go("GET", urljoin(url, broker["href"].replace("&amp;", "&")))
        raise LoginError(f"Unbekannte Seite: {page.plain()[:200]}")

    def run(self):
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_hex(16)
        r, url = self.go("GET", f"{REALM}/auth?" + urlencode({
            "client_id": CLIENT, "redirect_uri": REDIRECT, "state": state, "response_mode": "fragment",
            "response_type": "code", "scope": "openid", "nonce": secrets.token_hex(16),
            "code_challenge": challenge, "code_challenge_method": "S256"}))
        for _ in range(15):
            if r is None:
                break
            r, url = self.step(r, url)
        else:
            raise LoginError("Anmeldung kommt nicht zum Ende.")
        # Den Keycloak-Code braucht es nicht: RWTHonline hat beim Rücksprung schon das Session-Cookie
        # (PSESSIONID) gesetzt, und damit gibt auth/token/refresh das Zugangs-Token heraus.
        r = self.http.post(f"{SITE}/ee/rest/auth/token/refresh", data="", timeout=20,
                           headers={"Content-Type": "application/json", "Accept": "application/json"})
        token = r.json().get("accessToken") if r.status_code == 201 else None
        if not token:
            raise LoginError(f"Angemeldet, aber kein Token (auth/token/refresh → {r.status_code}).")
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                   for c in self.http.cookies if c.domain.lstrip(".").endswith("online.rwth-aachen.de")]
        log("Cookies:", [(c["name"], c["path"]) for c in cookies])
        return {"access_token": token, "cookies": cookies}
