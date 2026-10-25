"""`rwth login`: öffnet das Login-Fenster (System-Python mit GTK/WebKit) und speichert die Sitzung."""

import json
import subprocess
from pathlib import Path

from .api import Rwth, RwthError, delete_session, save_session

WINDOW = Path(__file__).with_name("login_window.py")


def login():
    print("Im Fenster über RWTH-SSO anmelden – es schließt sich danach von selbst.")
    # /usr/bin/python3 statt venv: GTK/WebKit kommen aus den Arch-Paketen.
    proc = subprocess.run(["/usr/bin/python3", str(WINDOW)], stdout=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        print("Anmeldung abgebrochen.")
        return 1
    session = json.loads(proc.stdout)
    try:
        Rwth(session).get("slc.xm.ac/achievements")  # home ginge auch anonym
    except RwthError as e:
        print(f"Anmeldung fehlgeschlagen: {e}")
        return 1
    save_session(session)
    print("Angemeldet ✓")
    return 0


def logout():
    delete_session()
    print("Abgemeldet – gespeicherte Sitzung gelöscht.")
    return 0
