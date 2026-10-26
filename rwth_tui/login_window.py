"""Login-Fenster für RWTHonline (eigener Prozess, System-Python mit GTK/WebKit).

Öffnet den OIDC-Login (der Shibboleth-Link liefert bei der RWTH 404). Nach dem SSO leitet RWTHonline auf die Web-App
zurück, die den OAuth-Code selbst gegen ein Zugangs-Token tauscht und es im
localStorage ablegt. Sobald es dort liegt, geben wir es als JSON auf stdout aus
und schließen das Fenster.

Sind Zugangsdaten gespeichert (`rwth secret`), läuft das Ganze unsichtbar: die
SSO-Seiten werden selbst ausgefüllt, inklusive TOTP-Code vom Token „tui“ (bzw. `rwth secret token`).
Hakt etwas, erscheint das Fenster und man macht von Hand weiter. `--show` zeigt
es von Anfang an.
"""

import json
import os
import re
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

from credentials import lookup, token_name, totp  # noqa: E402  (liegt neben diesem Skript)

START = "https://online.rwth-aachen.de/RWTHonline/co/co-desktop/app/login?post_login_base=ee&post_login_route=%2Flogin&onboarding=false"
READ_STORAGE = "JSON.stringify(Object.assign({}, localStorage))"
DEBUG = bool(os.environ.get("RWTH_LOGIN_DEBUG"))
GIVE_UP_AFTER = 45  # Sekunden ohne Erfolg → Fenster zeigen
# Derselbe Schritt öfter → vermutlich falsches Passwort/Code. Passwort nur einmal, sonst droht Sperre;
# der Code darf zweimal (Zeitfenster-Grenze).
MAX_FILLS = {"password": 1, "otp": 2, "other": 1, "select": 1}

# Erkennt die SSO-Seite am Feldtyp statt an festen Namen. Gibt zurück, was es getan hat.
FILL = """(function (c) {
  // Beschreibung des Tokens im IdM-Selfservice (Standard „tui“)
  const TOKEN = new RegExp('\\\\b' + c.token + '\\\\b', 'i');  // c.token schon per re.escape maskiert
  const vis = e => e && e.offsetParent !== null && !e.disabled;
  const ins = [...document.querySelectorAll('input')].filter(vis);
  const fields = ins.map(e => [e.type, e.name || e.id, e.autocomplete].join(':'));
  const out = step => JSON.stringify({step, fields});
  const set = (e, v) => {
    e.focus(); e.value = v;
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const submit = e => {
    const f = e.form;
    const b = f && f.querySelector('button[type=submit], input[type=submit], button:not([type])');
    if (b) b.click(); else if (f) f.requestSubmit();
  };
  for (const s of document.querySelectorAll('select')) {
    const o = [...s.options].find(o => TOKEN.test(o.text));
    if (o) {
      s.value = o.value; s.dispatchEvent(new Event('change', {bubbles: true}));
      submit(s);
      return out('select');
    }
  }
  const pw = ins.find(e => e.type === 'password');
  if (pw && !/otp|totp|token|code/i.test(pw.name + ' ' + pw.id)) {
    if (c.dry || c.skip.includes('password')) return JSON.stringify({step: 'dry', buttons: [...document.querySelectorAll('button, input[type=submit]')]
      .map(b => [b.type, b.name, b.value, b.textContent.trim(), b.form === pw.form].join('|')),
      errors: [...document.querySelectorAll('.error, .alert, [role=alert], .output--error, .form-error')].map(e => e.innerText.trim()).filter(Boolean)});
    const u = ins.find(e => e !== pw && /^(text|email)$/.test(e.type));
    if (u) set(u, c.user);
    set(pw, c.password); submit(pw);
    return out('password');
  }
  if (c.code && !c.skip.includes('otp')) {
    const otp = ins.find(e => e.autocomplete === 'one-time-code'
      || (/otp|totp|token|code/i.test(e.name + ' ' + e.id) && /^(text|number|tel|password)$/.test(e.type)));
    // Das SSO wählt gern ein anderes Token vor (z. B. Mail) – dann erst zur Token-Auswahl.
    const other = [...document.querySelectorAll('a, button')].find(e => vis(e) && /andere auswahl|other selection/i.test(e.textContent));
    if (otp && !TOKEN.test(document.body.innerText) && other) { other.click(); return out('other'); }
    if (otp) { set(otp, c.code); submit(otp); return out('otp'); }
  }
  const broker = document.querySelector('a[href*="/broker/rwth-sso/"]');  // Keycloak: „RWTH Single Sign On“
  if (broker) { location.href = broker.href; return out('broker'); }
  const pick = [...document.querySelectorAll('label, li, button, a')].find(e => vis(e) && TOKEN.test(e.textContent));
  if (pick) { pick.click(); return out('pick'); }
  return JSON.stringify({step: 'none', fields, title: document.title,
    options: [...document.querySelectorAll('select option, [role=option], input[type=radio], input[type=checkbox]')].map(o => [o.tagName, o.value, o.textContent.trim()].join('|')),
    text: (document.body ? document.body.innerText : '').slice(0, 200),
    links: [...document.querySelectorAll('a[href], button')].slice(0, 8).map(e => (e.textContent.trim() + ' ' + (e.getAttribute('href') || '')).slice(0, 90))});
})(%s)"""


def on_app(uri):
    """RWTHonline selbst (nicht Keycloak unter /sec/auth/, nicht das SSO) – dort liegt irgendwann das Token."""
    return "online.rwth-aachen.de/RWTHonline/" in uri and "/sec/auth/" not in uri


def main():
    creds = {k: lookup(k) for k in ("user", "password", "totp")}
    auto = all(creds.values()) and "--show" not in sys.argv
    ctx = WebKit2.WebContext.new_ephemeral()
    view = WebKit2.WebView.new_with_context(ctx)
    win = Gtk.Window(title="RWTHonline – Anmeldung")
    win.set_default_size(900, 760)
    win.add(view)
    hidden = auto  # Fenster bleibt ungezeigt (ein Offscreen-Fenster hätte kein OpenGL für WebKit)
    result = {}
    fills = {}
    ready = False  # erst nach fertig geladener Seite ausfüllen, nie zweimal dieselbe
    started = time.monotonic()
    stuck = 0  # Takte auf der nackten Startseite – Keycloak springt sporadisch dorthin statt zum SSO
    restarts = 0

    def show(reason):
        nonlocal hidden
        if not hidden:
            return
        print(f"Automatische Anmeldung hakt ({reason}) – bitte im Fenster weitermachen.", file=sys.stderr)
        hidden = False
        win.show_all()

    def on_storage(obj, res):
        try:
            value = view.evaluate_javascript_finish(res)
        except GLib.Error:
            return
        storage = json.loads(value.to_string() or "{}")
        if DEBUG:
            print("storage keys:", sorted(storage), file=sys.stderr)
        token = next((v for k, v in storage.items() if k.endswith("co.login.accessToken")), None)
        if token and "access_token" not in result:
            result["access_token"] = token.strip('"')
            # Die REST-API will zum Token auch das Session-Cookie.
            ctx.get_website_data_manager().get_cookie_manager().get_all_cookies(None, on_cookies)

    def on_cookies(manager, res):
        cookies = manager.get_all_cookies_finish(res)
        result["cookies"] = [
            {"name": c.get_name(), "value": c.get_value(), "domain": c.get_domain(), "path": c.get_path()}
            for c in cookies
            if c.get_domain().lstrip(".").endswith("online.rwth-aachen.de")
        ]
        if DEBUG:
            print("cookies:", [(c["name"], c["path"]) for c in result["cookies"]], file=sys.stderr)
        Gtk.main_quit()

    def on_filled(obj, res):
        try:
            info = json.loads(view.evaluate_javascript_finish(res).to_string())
        except GLib.Error:
            return
        if DEBUG:
            print("fill:", info, file=sys.stderr)
        nonlocal ready
        step = info["step"]
        if step in ("broker", "password", "otp", "other", "select"):
            ready = False
        if step != "none":
            fills[step] = fills.get(step, 0) + 1
            if fills[step] > MAX_FILLS.get(step, 3):
                show(f"Schritt „{step}“ wiederholt sich")

    def on_load(_view, event):
        nonlocal ready
        if event == WebKit2.LoadEvent.FINISHED:
            ready = True
            # Sofort statt erst beim nächsten poll(): die Keycloak-Seite springt sonst gelegentlich weiter.
            if hidden and not on_app(view.get_uri() or ""):
                fill()

    def fill():
        # Einen Code nicht kurz vor Ablauf abschicken – sonst ist er beim Server schon alt.
        left = 30 - int(time.time()) % 30
        if left < 4:
            return
        c = dict(creds, token=re.escape(token_name()), code=totp(creds["totp"]), dry=bool(os.environ.get("RWTH_LOGIN_DRYRUN")),
                 skip=[k for k, n in fills.items() if n >= MAX_FILLS.get(k, 3)])
        del c["totp"]
        view.evaluate_javascript(FILL % json.dumps(c), -1, None, None, None, on_filled)

    def poll():
        uri = view.get_uri() or ""
        if DEBUG:
            print("uri:", uri.split("?")[0], file=sys.stderr)
        nonlocal stuck, restarts
        if on_app(uri):
            view.evaluate_javascript(READ_STORAGE, -1, None, None, None, on_storage)
            stuck = stuck + 1 if uri.rstrip("/").endswith("/RWTHonline") else 0
            if hidden and stuck > 4 and restarts < 3:
                restarts += 1
                stuck = 0
                if DEBUG:
                    print("Neustart", restarts, file=sys.stderr)
                view.load_uri(START)
        elif hidden and ready and not view.is_loading():
            fill()
        if hidden and time.monotonic() - started > GIVE_UP_AFTER:
            show("dauert zu lange")
        return "access_token" not in result

    view.connect("load-changed", on_load)
    win.connect("delete-event", lambda *_: Gtk.main_quit())
    GLib.timeout_add(700, poll)
    view.load_uri(START)
    view.show()
    if not hidden:
        win.show_all()
    Gtk.main()
    if "cookies" not in result:
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
