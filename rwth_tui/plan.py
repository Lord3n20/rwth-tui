"""Studienplanung: Module aus „Mein Studium“ (RWTHonline) + eigene Semesterzuordnung (nur lokal gespeichert)."""

import json
import re
from itertools import combinations

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
        self.rec_terms = []      # Fachsemester laut Semesterplan der Uni (manche Module über zwei)
        self.planned = None      # Semesterschlüssel, z. B. '27S'
        self.suggested = False   # planned stammt aus dem Uni-Plan, nicht von Hand
        self.attempts = 0        # Fehlversuche (aus der zugeordneten Leistung), 0 = unbekannt/keine
        self.max_attempts = None
        self.done_term = None    # Semester der (letzten) bestandenen Leistung, falls zuordenbar
        self.courses = {}        # {Semesterschlüssel: [LV-Anmeldungen]} (Vorlesungen, Seminare … dem Modul zugeordnet)

    @property
    def words(self):
        """Wörter aus Modul-, Teilprüfungs- und LV-Namen für den Abgleich (einmal berechnet)."""
        if not hasattr(self, "_words"):
            self._words = words(self.name + " " + " ".join(element_names(self.raw)))
        return self._words

    @property
    def recommended(self):
        return self.rec_terms[0] if self.rec_terms else None

    @property
    def remaining(self):
        return max(self.total - self.achieved, 0)

    @property
    def done(self):
        return self.status == "COMPLETED_SUCCESSFULLY"

    def exams(self):
        def walk(children):
            for c in children:
                if c.get("elementType") == "EXAM":
                    yield c
                yield from walk(c.get("children") or [])
        return list(walk(self.raw.get("children") or []))

    @property
    def failed(self):
        """Bisher nicht bestanden: eine Teilprüfung hat eine Note, ist aber nicht erfolgreich (z. B. 5,0)."""
        return not self.done and any(
            (e.get("progressInfo") or {}).get("grade") not in (None, "-")
            and not (e.get("progressInfo") or {}).get("success") for e in self.exams())


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
            if m and _valid(raw) and int(ref.rstrip(".")) not in m.rec_terms:
                m.rec_terms = sorted(m.rec_terms + [int(ref.rstrip("."))])
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

    def explicit(self, study_id):
        return set(self.data.get(str(study_id), {}))

    def course_overrides(self, study_id):
        """{LV-id: Modulnummer} – von Hand zugeordnete LV-Anmeldungen, "" = keinem Modul."""
        return self.data.get("_courses", {}).get(str(study_id), {})

    def set_course(self, study_id, course_id, code):
        self.data.setdefault("_courses", {}).setdefault(str(study_id), {})[str(course_id)] = code
        self._save()

    def set(self, study_id, module, key):
        module.planned, module.suggested = key, False
        # None auch merken: sonst gälte wieder das aktuelle Semester (Laufendes) bzw. der Uni-Vorschlag
        self.data.setdefault(str(study_id), {})[module.code] = key
        self._save()

    def _save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(self.data, indent=1))


def exact_choice(modules, need, limit=14):
    """Die eine Auswahl, deren restliche ECTS genau need ergeben – None, wenn es keine oder mehrere gibt
    (bei mehr als limit Kandidaten wird nicht gesucht)."""
    if len(modules) > limit:
        return None
    found = None
    for n in range(1, len(modules) + 1):
        for combo in combinations(modules, n):
            if sum(m.remaining for m in combo) == need:
                if found is not None:
                    return None
                found = combo
    return list(found) if found else None


def suggest(modules, areas, explicit, start, current):
    """Ungeplante Module ins empfohlene Semester des Uni-Plans legen (liegt es zurück: nächstes gleicher
    Art, WiSe bzw. SoSe). In Bereichen mit Auswahl nur, wenn genau eine Kombination die fehlenden ECTS
    füllt (Ergänzung: noch 8 ECTS, nur das Praktikum hat 8); sonst bleibt die Wahl dem Nutzer."""
    open_ = [m for m in modules if not m.done and not m.planned and m.code not in explicit]
    chosen = set(open_)
    for area in areas:
        info = area.get("progressInfo") or {}
        if not info.get("totalCredits"):
            continue
        name = val(area["name"])
        need = info["totalCredits"] - (info.get("achievedCredits") or 0) - sum(
            m.remaining for m in modules if m.area == name and not m.done and m.planned)
        candidates = [m for m in open_ if m.area == name]
        if sum(m.remaining for m in candidates) > need:
            chosen -= set(candidates) - set(exact_choice(candidates, need) or [] if need > 0 else [])
    for m in open_:
        if m not in chosen or not m.rec_terms:
            continue
        key = add_terms(start, m.rec_terms[0] - 1)
        while key < current:
            key = add_terms(key, 2)
        m.planned, m.suggested = key, True


GENERIC_WORDS = {"klausur", "prüfung", "modul", "modulprüfung", "vorlesung", "hausarbeit", "einführung",
                 "mündliche", "übung", "seminar", "oder", "inhalten", "teilprüfung", "modulteilprüfung"}


ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


def words(text):
    """Bedeutungstragende Wörter plus Teilnummern („Höhere Mathematik 2“, „Physik II“ → #2)."""
    numbers = {f"#{ROMAN.get(n, n)}" for n in re.findall(r"\b([1-9]|I{1,3}|IV|V)\b", text)}
    return ({w for w in re.findall(r"\w{4,}", text.lower())} - GENERIC_WORDS) | numbers


def element_names(raw):
    """Namen aller Teilprüfungen und Lehrveranstaltungs-Angebote unterhalb eines Moduls."""
    for c in raw.get("children") or []:
        yield val(c.get("name"))
        yield from element_names(c)


class Matcher:
    """Ordnet Leistungen bzw. LV-Anmeldungen Modulen zu. RWTHonline verknüpft das nicht, also nach
    Wortüberschneidung mit Modul-, Teilprüfungs- und LV-Namen; seltene Wörter zählen mehr (Fachnamen wie
    „Soziologie“ stehen in vielen Modulen)."""

    def __init__(self, modules):
        self.names = {m: m.words for m in modules}
        self.df = {}
        for ws in self.names.values():
            for w in ws:
                self.df[w] = self.df.get(w, 0) + 1

    def score(self, m, title):
        mine = words(title)
        return sum(1 / self.df[w] for w in self.names[m] & mine)

    def ranked(self, title):
        """Module nach Passung, beste zuerst (für die Auswahl von Hand)."""
        return sorted(self.names, key=lambda m: -self.score(m, title))

    def best(self, title, among=None):
        """Bestes Modul (aus among, sonst allen) mit mindestens einem gemeinsamen Wort, sonst None."""
        best = max(among or self.names, key=lambda m: self.score(m, title), default=None)
        return best if best and self.score(best, title) else None

    def by_name(self, name):
        """Modul mit genau diesem Namen (Anmeldeverfahren heißen oft wie das Modul)."""
        key = normalize(name)
        return next((m for m in self.names if normalize(m.name) == key), None)


def normalize(name):
    return re.sub(r"[\W_]+", " ", name.lower()).strip()


def module_credits(m):
    """ECTS, die eine Leistung zu diesem Modul haben kann: das ganze Modul oder eine Teilprüfung."""
    return {m.total} | {(e.get("progressInfo") or {}).get("totalCredits") for e in m.exams()}


def match_achievements(modules, achievements, title):
    """{Modul: [Leistungen]}; jede Leistung nur beim am besten passenden Modul. Passen die ECTS der Leistung
    zu einem Modul oder seiner Teilprüfung, kommen nur solche in Frage („Seminar Theologie“ mit 10 ECTS
    gehört dann zu „Wissenschaft, Technik und Gesellschaft“, nicht zu „Einführung in die Theologie“ mit 8)."""
    matcher, result = Matcher(modules), {}
    for a in achievements:
        credits = a.get("credits")
        among = [m for m in modules if credits and credits in module_credits(m)]
        best = matcher.best(title(a), among) or (None if among else matcher.best(title(a)))
        if best:
            result.setdefault(best, []).append(a)
    return result


def match_registration(matcher, r, title):
    """Modul zu einer LV-Anmeldung: Name des Anmeldeverfahrens = Modulname, sonst Abgleich über Verfahrens-
    und LV-Namen zusammen („Vorlesung Neuzeit: …“ allein passte zum falschen Modul)."""
    procedure = val(r.get("registrationProcedureName"))
    return matcher.by_name(procedure) or matcher.best(procedure + " " + title(r))


def match_failed(modules, achievements, title):
    """{nicht bestandenes Modul: erste passende nicht bestandene Leistung}."""
    return {m: found[0] for m, found in match_achievements([m for m in modules if m.failed], achievements, title).items()}
