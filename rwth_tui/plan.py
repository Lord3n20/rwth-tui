"""Studienplanung: Module aus „Mein Studium“ (RWTHonline) + eigene Semesterzuordnung (nur lokal gespeichert)."""

import json

from .api import CONFIG_DIR

PLAN_FILE = CONFIG_DIR / "plan.json"
STATUS_ORDER = {"IN_PROGRESS": 0, "OPEN": 1, "BOOKMARKED": 1, "COMPLETED_SUCCESSFULLY": 2}


def val(obj):
    return (obj or {}).get("value") or "" if isinstance(obj, dict) else (obj or "")


def next_key(key):
    """'26W' → '27S', '27S' → '27W'."""
    year, kind = int(key[:-1]), key[-1]
    return f"{year:02d}W" if kind == "S" else f"{year + 1:02d}S"


def prev_key(key):
    year, kind = int(key[:-1]), key[-1]
    return f"{year:02d}S" if kind == "W" else f"{year - 1:02d}W"


def add_terms(key, n):
    for _ in range(n):
        key = next_key(key)
    return key


class Module:
    def __init__(self, raw, status):
        self.raw = raw
        self.status = status
        self.code = raw.get("designation") or str(raw["elementId"])
        self.name = val(raw["name"])
        self.paths = [[val(n["name"]) for n in raw.get("nodePath", [])[1:]]]
        self.area = self.paths[0][0] if self.paths[0] else ""
        info = raw.get("progressInfo") or {}
        self.achieved = info.get("achievedCredits") or 0
        self.total = info.get("totalCredits") or 0
        self.grade = info.get("grade") if info.get("grade") not in (None, "-") else ""
        self.recommended = None  # Fachsemester laut Studienverlaufsplan
        self.planned = None      # Semesterschlüssel, z. B. '27S'

    @property
    def remaining(self):
        return max(self.total - self.achieved, 0)

    @property
    def done(self):
        return self.status == "COMPLETED_SUCCESSFULLY"


def _valid(raw):
    return not any(t.get("tagType") == "CURRENTLY_NOT_VALID" for t in raw.get("tags", []))


def build_modules(open_modules, semester_plan):
    """Module zusammenführen: gleiche Modulnummer in mehreren Wahlpflicht-Zweigen nur einmal,
    alte SPO-Versionen („derzeit nicht gültig“) nur, wenn schon etwas daran hängt."""
    modules = {}
    for group in open_modules["groups"]:
        status = group["groupInformation"].get("stylingReference") or "OPEN"
        for raw in group["list"]:
            if status == "OPEN" and not _valid(raw):
                continue
            m = Module(raw, status)
            if m.code in modules:
                modules[m.code].paths.append(m.paths[0])
            else:
                modules[m.code] = m
    for group in semester_plan["groups"]:
        ref = group["groupInformation"].get("stylingReference") or ""
        if not ref.rstrip(".").isdigit():
            continue
        for raw in group["list"]:
            m = modules.get(raw.get("designation"))
            if m and (m.recommended is None or int(ref.rstrip(".")) < m.recommended):
                m.recommended = int(ref.rstrip("."))
    return list(modules.values())


def sort_modules(modules):
    """Feste Reihenfolge (nicht nach geplantem Semester), damit +/- die Zeilen nicht umsortiert."""
    return sorted(modules, key=lambda m: (STATUS_ORDER.get(m.status, 1), m.recommended or 99, m.area, m.name))


class PlanStore:
    """{studyId: {Modulnummer: Semesterschlüssel}} in ~/.config/rwth-tui/plan.json."""

    def __init__(self):
        try:
            self.data = json.loads(PLAN_FILE.read_text())
        except (FileNotFoundError, ValueError):
            self.data = {}

    def apply(self, study_id, modules, current_key):
        mine = self.data.get(str(study_id), {})
        for m in modules:
            if m.code in mine:
                m.planned = mine[m.code]
            elif m.status == "IN_PROGRESS":
                m.planned = current_key  # Laufendes zählt erst mal fürs aktuelle Semester

    def set(self, study_id, module, key):
        module.planned = key
        mine = self.data.setdefault(str(study_id), {})
        if key is None and module.status != "IN_PROGRESS":
            mine.pop(module.code, None)
        else:
            mine[module.code] = key  # bei Laufendem auch None merken, sonst gilt wieder das aktuelle Semester
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(self.data, indent=1))
