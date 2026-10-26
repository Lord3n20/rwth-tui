"""Ansicht offline durchspielen: Antworten kommen aus ~/.cache/rwth-tui/json (legt jeder echte Lauf an).

    python tests/fake_run.py AUSGABE_ORDNER

Nutzt einen leeren Konfigurationsordner unter AUSGABE_ORDNER (eigene plan.json bleibt unberührt),
schreibt Screenshots (SVG) und gibt die Plan-Tabelle als Text aus. Prüft außerdem die Zuordnung per z.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = str(OUT / "config")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rwth_tui import api  # noqa: E402
from rwth_tui.app import ModulePicker, RwthApp  # noqa: E402

CACHE = Path.home() / ".cache/rwth-tui/json"


def fake_get(self, path, **params):
    name = path.strip("/").replace("/", "_")
    if params:
        name += "_" + hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
    f = CACHE / (name + ".json")
    if not f.exists():
        raise api.RwthError(f"nicht im Cache: {name}")
    return json.loads(f.read_text())


def fake_init(self, session=None, offline=False):
    self.offline, self.lock = offline, None


api.Rwth.__init__ = fake_init
api.Rwth.get = fake_get


def plain(cell):
    return cell.plain if hasattr(cell, "plain") else re.sub(r"\[/?[^\]]*\]", "", str(cell))


def dump(table):
    for i in range(table.row_count):
        print(" | ".join(plain(c) for c in table.get_row_at(i)))


async def main():
    app = RwthApp()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause(2)
        tabs = app.query_one("#tabs")
        tabs.active = "tab-plan"
        await pilot.pause(0.5)
        app.query_one("#plan").focus()
        app.save_screenshot("plan.svg", str(OUT))
        dump(app.query_one("#plan"))

        tabs.active = "tab-registrations"
        await pilot.pause(1)
        regs = app.query_one("#registrations")
        regs.focus()
        await pilot.pause(0.5)
        app.save_screenshot("anmeldungen.svg", str(OUT))
        if app.regs:
            await pilot.press("z")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ModulePicker), "z öffnet keine Auswahl"
            app.save_screenshot("auswahl.svg", str(OUT))
            await pilot.press("down", "enter")  # zweitbestes Modul
            await pilot.pause(0.3)
            store = json.loads((OUT / "config/rwth-tui/plan.json").read_text())["_courses"]
            print("von Hand zugeordnet:", store)
            assert plain(regs.get_row_at(0)[3]).endswith("✎"), "Spalte Modul zeigt die Zuordnung nicht"
            await pilot.press("p")  # in der ersten LV die Prüfung ablegen
            await pilot.pause(0.3)
            exams = json.loads((OUT / "config/rwth-tui/plan.json").read_text())["_exams"]
            print("Prüfung markiert:", exams)
            assert plain(regs.get_row_at(0)[3]).endswith("P"), "p markiert keine Prüfung"
            dump(app.query_one("#plan"))
    print("Screenshots in", OUT)


asyncio.run(main())
