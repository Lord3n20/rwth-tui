"""Login-Fenster für RWTHonline (eigener Prozess, System-Python mit GTK/WebKit).

Öffnet den OIDC-Login (der Shibboleth-Link liefert bei der RWTH 404). Nach dem SSO leitet RWTHonline auf die Web-App
zurück, die den OAuth-Code selbst gegen ein Zugangs-Token tauscht und es im
localStorage ablegt. Sobald es dort liegt, geben wir es als JSON auf stdout aus
und schließen das Fenster. Passwort und 2FA bleiben im Fenster.
"""

import json
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

START = "https://online.rwth-aachen.de/RWTHonline/co/co-desktop/app/login?post_login_base=ee&post_login_route=%2Flogin&onboarding=false"
READ_STORAGE = "JSON.stringify(Object.assign({}, localStorage))"
DEBUG = bool(os.environ.get("RWTH_LOGIN_DEBUG"))


def main():
    ctx = WebKit2.WebContext.new_ephemeral()
    view = WebKit2.WebView.new_with_context(ctx)
    win = Gtk.Window(title="RWTHonline – Anmeldung")
    win.set_default_size(900, 760)
    win.add(view)
    result = {}

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
        win.close()

    def poll():
        uri = view.get_uri() or ""
        if DEBUG:
            print("uri:", uri.split("?")[0], file=sys.stderr)
        if "online.rwth-aachen.de/RWTHonline/" in uri and "/sec/auth/" not in uri:
            view.evaluate_javascript(READ_STORAGE, -1, None, None, None, on_storage)
        return "access_token" not in result

    win.connect("destroy", lambda *_: Gtk.main_quit())
    GLib.timeout_add(700, poll)
    view.load_uri(START)
    win.show_all()
    Gtk.main()
    if "cookies" not in result:
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
