"""`rwth login`: meldet per HTTP an (Rückfall: Fenster mit GTK/WebKit) und speichert die Sitzung."""

import json
import subprocess
from pathlib import Path

from .api import Rwth, RwthError, delete_session, save_session

WINDOW = Path(__file__).with_name("login_window.py")


def login(args=()):
    """Ohne Browser per HTTP (sso.py); mit --show oder ohne gespeicherte Zugangsdaten im Fenster."""
    from .credentials import lookup
    from .sso import Login, LoginError
    if "--show" in args or not all(lookup(k) for k in ("user", "password", "totp")):
        session = window_login(args)
    else:
        print("Melde an …")
        try:
            session = Login().run()
        except LoginError as e:
            print(f"Anmeldung fehlgeschlagen: {e}")
            return 1
    if not session:
        print("Anmeldung abgebrochen.")
        return 1
    try:
        Rwth(session).get("slc.xm.ac/achievements")  # home ginge auch anonym
    except RwthError as e:
        print(f"Anmeldung fehlgeschlagen: {e}")
        return 1
    save_session(session)
    print("Angemeldet ✓")
    return 0


def window_login(args):
    print("Im Fenster über RWTH-SSO anmelden – es schließt sich danach von selbst.")
    # /usr/bin/python3 statt venv: GTK/WebKit kommen aus den Arch-Paketen.
    proc = subprocess.run(["/usr/bin/python3", str(WINDOW), *args], stdout=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def logout():
    delete_session()
    print("Abgemeldet – gespeicherte Sitzung gelöscht.")
    return 0
