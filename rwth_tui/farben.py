"""Darstellung: Textual-Theme merken und ein Theme „custom“ mit eigenen Farben aus ~/.config/rwth-tui/farben.json.

Datei (alles optional): {"theme": "custom", "farben": {"hintergrund": "#05090d", "flaechen": "#0b141a",
"text": "#d6eeee", "akzent": "#00ffff", "kontrast": "#ff8a1f", "kritisch": "#ff3b30"}}
Das Theme lässt sich auch in der Befehlspalette (Strg+P → Theme) wechseln; die Wahl wird hier gespeichert.
"""

import json
import re

from textual.theme import Theme

from .api import CONFIG_DIR

DATEI = CONFIG_DIR / "farben.json"
VORGABE = {"hintergrund": "#05090d", "flaechen": "#0b141a", "text": "#d6eeee", "akzent": "#00ffff",
           "kontrast": "#ff8a1f", "kritisch": "#ff3b30"}


def lesen():
    try:
        d = json.loads(DATEI.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def custom_theme(farben=None):
    f = dict(VORGABE)
    for k, v in (farben or {}).items():
        if k in VORGABE and isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            f[k] = v
    return Theme(name="custom", dark=True, primary=f["akzent"], secondary=f["akzent"], accent=f["akzent"],
                 warning=f["kontrast"], error=f["kritisch"], success=f["akzent"], foreground=f["text"],
                 background=f["hintergrund"], surface=f["flaechen"], panel=f["flaechen"])


def theme_merken(name):
    d = lesen()
    if d.get("theme") == name:
        return
    d["theme"] = name
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        DATEI.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass
