"""Textual-Oberfläche: Semester links, rechts Leistungen (Noten/ECTS), LV-Anmeldungen, Prüfungen und Studienplan."""

import subprocess
from datetime import datetime

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Label, ListItem, ListView, Markdown, TabbedContent, TabPane

from .api import SITE, NotLoggedIn, Rwth, RwthError
from .plan import PlanStore, add_terms, build_modules, match_achievements, suggest, match_failed, next_key, prev_key, sort_modules

ALL = "alle"
STATUS = {"FIX": "Fixplatz", "BW": "beworben", "VOE": "vorgemerkt"}
MODULE_STATUS = {"COMPLETED_SUCCESSFULLY": "abgeschlossen", "IN_PROGRESS": "läuft", "OPEN": "offen",
                 "BOOKMARKED": "vorgemerkt"}
AREA_SHORT = {"Pflichtbereich": "Pflicht", "Ergänzungsbereich": "Ergänzung", "Wahlpflichtbereich": "Wahlpflicht",
              "Bachelorarbeit": "BA", "Masterarbeit": "MA"}
PLAN_ACTIONS = {"plan_later", "plan_earlier", "plan_remove", "toggle_uni", "toggle_done"}
AREA_PREFIXES = ("Wahlpflicht", "Pflicht", "Ergänzung", "Wahl", "Zusatz")
ECTS_PER_TERM = 30  # Empfehlung der Uni: 30 ECTS je Semester
BOOKED = "#d787ff"  # lila: Vorlesung/Seminar im Semester belegt
INDENT = "    "  # Module stehen eingerückt unter ihrer Semester-Überschrift


def val(obj):
    """Sprachfelder (langdata) kommen als {"value": …, "translations": …}."""
    return (obj or {}).get("value") or "" if isinstance(obj, dict) else (obj or "")


def fmt_dt(obj, time=True):
    raw = val(obj)
    if not raw:
        return ""
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return d.strftime("%d.%m.%y %H:%M" if time and (d.hour or d.minute) else "%d.%m.%y")


def term_label(key):
    """'26S' → 'SoSe 26', '26W' → 'WiSe 26/27'."""
    year, kind = int(key[:-1]), key[-1]
    return f"SoSe {year:02d}" if kind == "S" else f"WiSe {year:02d}/{(year + 1) % 100:02d}"


def area_short(name):
    """Bereichsnamen kurz: „Pflichtmodule“, „Wahlpflichtfächer des jeweiligen …“ → „Pflicht“, „Wahlpflicht“."""
    if name in AREA_SHORT:
        return AREA_SHORT[name]
    return next((p for p in AREA_PREFIXES if name.startswith(p)), name[:11])


def fmt_grade(x):
    return f"{x:.1f}".replace(".", ",")


def open_external(url):
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def course_url(course_id):
    return f"{SITE}/pages/slc.tm.cp/course/{course_id}"


def counts(a):
    """Zählt für ECTS: endgültig und bestanden (auch unbenotet „B“ oder anerkannt)."""
    grade = a.get("gradeDto") or {}
    return a.get("achievementStatusType") == "FINAL" and grade.get("isPositive")


def numeric_grade(a):
    key = (a.get("gradeDto") or {}).get("nameShort") or ""
    try:
        return float(key.replace(",", "."))
    except ValueError:
        return None


def summary(achievements):
    """ECTS-Summe und ECTS-gewichteter Schnitt der benoteten, bestandenen Leistungen."""
    passed = [a for a in achievements if counts(a)]
    ects = sum(a.get("credits") or 0 for a in passed)
    graded = [(numeric_grade(a), a.get("credits") or 0) for a in passed if numeric_grade(a) is not None]
    weight = sum(c for _, c in graded)
    avg = sum(g * c for g, c in graded) / weight if weight else None
    return ects, avg


class RwthApp(App):
    TITLE = "RWTHonline"
    CSS = """
    #left { width: 30; border-right: solid $primary-darken-2; }
    .title { padding: 0 1; text-style: bold; color: $accent; }
    #study { padding: 0 1; height: auto; }
    #tabs { height: 1fr; }
    #detail-box { height: 40%; border-top: solid $primary-darken-2; }
    #plan-sum { padding: 0 1; height: auto; }
    ListView { height: 1fr; }
    """
    BINDINGS = [
        Binding("r", "refresh", "Neu laden"),
        Binding("s", "next_study", "Studiengang"),
        Binding("o", "open_browser", "Im Browser"),
        Binding("plus", "plan_later", "Später"),
        Binding("minus", "plan_earlier", "Früher"),
        Binding("x", "plan_remove", "Aus Plan"),
        Binding("u", "toggle_uni", "Uni-Vorschläge"),
        Binding("e", "toggle_done", "Erledigte"),
        Binding("tab", "focus_next", "Bereich wechseln", show=False),
        Binding("q", "quit", "Beenden"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                yield Label("Semester", classes="title")
                yield ListView(id="terms")
            with Vertical(id="right"):
                yield Label("Lade …", id="study")
                with TabbedContent(id="tabs"):
                    with TabPane("Leistungen", id="tab-achievements"):
                        yield DataTable(id="achievements", cursor_type="row", zebra_stripes=True)
                    with TabPane("Anmeldungen", id="tab-registrations"):
                        yield DataTable(id="registrations", cursor_type="row", zebra_stripes=True)
                    with TabPane("Prüfungen", id="tab-exams"):
                        yield DataTable(id="exams", cursor_type="row", zebra_stripes=True)
                    with TabPane("Plan", id="tab-plan"):
                        yield Label("", id="plan-sum")
                        yield DataTable(id="plan", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="detail-box"):
                    yield Markdown(id="detail")
        yield Footer()

    def on_mount(self):
        self.api = None
        self.studies, self.study_index = [], 0
        self.all_achievements, self.terms, self.current = [], [], None
        self.shown_achievements, self.regs, self.exams = [], [], []
        self.reg_cache, self.progress, self.loose_courses = {}, {}, {}
        self.modules, self.areas, self.plan_errors = {}, {}, {}
        self.plan_rows, self.plan_store = [], PlanStore()
        self.show_uni, self.show_done = False, False
        self.detail_text, self.detail_timer = None, None
        self.query_one("#achievements", DataTable).add_columns("Semester", "Datum", "Note", "ECTS", "Leistung")
        self.query_one("#registrations", DataTable).add_columns("Art", "Status", "Abmelden bis", "Gruppe", "Lehrveranstaltung")
        self.query_one("#exams", DataTable).add_columns("Datum", "Prüfung", "Status")
        self.query_one("#plan", DataTable).add_columns("", "ECTS", "Uni", "Modul")
        self.query_one("#terms", ListView).focus()
        self.action_refresh()

    def fail(self, error):
        self.query_one("#study", Label).update(f"[b red]{escape(str(error))}[/]")
        if isinstance(error, NotLoggedIn):
            self.notify("Nicht angemeldet – `rwth login` ausführen.", severity="error", timeout=10)

    # ------------------------------------------------------------------ Laden

    def action_refresh(self):
        self.reg_cache.clear()
        self.modules.clear()
        self.plan_errors.clear()
        self.load_all()

    @work(thread=True, exclusive=True, group="load")
    def load_all(self):
        try:
            if self.api is None:
                self.api = Rwth()
            studies = self.api.studies()
            achievements = self.api.achievements()
            current = self.api.current_term()
            semesters = self.api.semesters()
            self.call_from_thread(self.show_overview, studies, achievements, current, semesters)
            progress = {s["studyId"]: self.api.study_progress(s) for s in studies if s.get("myStudyLink")}
            self.call_from_thread(self.show_progress, progress)
            exams = self.api.registered_exams()
            self.call_from_thread(self.show_exams, exams)
            self.fetch_plan(self.study())
        except RwthError as e:
            self.call_from_thread(self.fail, e)

    @work(thread=True, exclusive=True, group="plan")
    def load_plan(self, study):
        self.fetch_plan(study)

    def fetch_plan(self, study):
        """Studienplan einer Studienrichtung laden (läuft im Worker-Thread, ~2 MB beim ersten Mal)."""
        if not study or not study.get("myStudyLink") or study["studyId"] in self.modules:
            return
        try:
            open_modules, semester_plan, areas = self.api.curriculum(study)
        except RwthError as e:
            self.plan_errors[study["studyId"]] = str(e)
            self.call_from_thread(self.show_plan)
            return
        modules = build_modules(open_modules, semester_plan)
        self.count_attempts(modules, study["studyId"])
        self.assign_done_terms(modules, study["studyId"])
        self.assign_courses(modules, study["studyId"])
        self.plan_store.apply(study["studyId"], modules, self.current["key"])
        suggest(modules, areas, self.plan_store.explicit(study["studyId"]), self.start_of(study["studyId"]),
                self.current["key"])
        self.modules[study["studyId"]], self.areas[study["studyId"]] = modules, areas
        self.call_from_thread(self.fill_terms)

    def count_attempts(self, modules, study_id):
        """Fehlversuche für bisher nicht bestandene Module aus der passenden Leistung holen (Worker-Thread)."""
        negative = [a for a in self.all_achievements
                    if (a.get("studyBasicInfoLibDto") or {}).get("id") == study_id
                    and a.get("achievementStatusType") == "FINAL" and (a.get("gradeDto") or {}).get("isNegative")]
        title = lambda a: val((a.get("cpCourseLibDto") or {}).get("courseTitle")) or val(a.get("subject"))
        for m, a in match_failed(modules, negative, title).items():
            try:
                result = self.api.achievement(a["id"])["attemptCountResult"]
                m.attempts, m.max_attempts = result["attemptCount"], result["parameters"]["maxAttempts"]
            except (RwthError, KeyError, TypeError):
                m.attempts = 1

    def assign_done_terms(self, modules, study_id):
        """Bestandenen Modulen das Semester ihrer passenden bestandenen Leistung geben (für den Rückblick im Plan)."""
        passed = [a for a in self.all_achievements
                  if (a.get("studyBasicInfoLibDto") or {}).get("id") == study_id and counts(a)]
        title = lambda a: val((a.get("cpCourseLibDto") or {}).get("courseTitle")) or val(a.get("subject"))
        for m, found in match_achievements([m for m in modules if m.done], passed, title).items():
            m.done_term = max(a["semesterLibDto"]["key"] for a in found)

    def start_of(self, study_id):
        """Erstes Semester mit Leistungen in diesem Studiengang (sonst das aktuelle)."""
        keys = [a["semesterLibDto"]["key"] for a in self.all_achievements
                if (a.get("studyBasicInfoLibDto") or {}).get("id") == study_id]
        return min(keys + [self.current["key"]])

    def assign_courses(self, modules, study_id):
        """LV-Anmeldungen vom Studienbeginn bis zum aktuellen Semester laden und den Modulen zuordnen
        (Worker-Thread). RWTHonline sagt nicht, zu welchem Studiengang eine Anmeldung gehört; was zu keinem
        Modul passt, landet in self.loose_courses und erscheint nur im Detail der Semester-Überschrift."""
        start = self.start_of(study_id)
        loose = self.loose_courses[study_id] = {}
        title = lambda r: val(r["course"]["courseTitle"])
        for term in self.all_semesters:
            if not start <= term["key"] <= self.current["key"]:
                continue
            try:
                regs = self.reg_cache.get(term["id"])
                if regs is None:
                    regs = self.reg_cache[term["id"]] = self.api.registrations(term["id"])
            except RwthError:
                continue
            # schon in einem früheren Semester bestandene Module kommen nicht mehr in Frage
            candidates = [m for m in modules if not (m.done and m.done_term and m.done_term < term["key"])]
            found = match_achievements(candidates, regs, title)
            for m, rs in found.items():
                m.courses[term["key"]] = rs
            matched = {id(r) for rs in found.values() for r in rs}
            loose[term["key"]] = [r for r in regs if id(r) not in matched]

    @work(thread=True, exclusive=True, group="regs")
    def load_registrations(self, term):
        try:
            regs = self.reg_cache.get(term["id"])
            if regs is None:
                regs = self.reg_cache[term["id"]] = self.api.registrations(term["id"])
            self.call_from_thread(self.show_registrations, term, regs)
        except RwthError as e:
            self.call_from_thread(self.fail, e)

    # ------------------------------------------------------------------ Anzeigen

    def study(self):
        return self.studies[self.study_index] if self.studies else None

    def study_achievements(self):
        study = self.study()
        if not study:
            return self.all_achievements
        return [a for a in self.all_achievements if (a.get("studyBasicInfoLibDto") or {}).get("id") == study["studyId"]]

    def show_overview(self, studies, achievements, current, semesters):
        if not self.studies:
            # Standard: der laufende Studiengang (beendete sind nur noch VIEWING_ALLOWED), sonst der zuletzt geöffnete
            self.study_index = next((i for i, s in enumerate(studies) if s.get("studyStatus") == "STUDYING_ALLOWED"),
                                    next((i for i, s in enumerate(studies) if s.get("lastAccessed")), 0))
        self.studies, self.all_achievements, self.current = studies, achievements, current
        self.all_semesters = semesters
        self.show_study()

    def show_study(self):
        self.show_header()
        self.fill_terms()

    def study_start(self):
        keys = [a["semesterLibDto"]["key"] for a in self.study_achievements()]
        return min(keys + [self.current["key"]])

    def study_modules(self):
        study = self.study()
        return self.modules.get(study["studyId"]) if study else None

    def fachsemester(self, key):
        n, k = 1, self.study_start()
        while k < key:
            k, n = next_key(k), n + 1
        return n

    def uni_keys(self, m):
        """Semester, in die der Semesterplan der Uni das Modul legt (Fachsemester → echtes Semester)."""
        start = self.study_start()
        return [add_terms(start, n - 1) for n in m.rec_terms]

    def ects_until(self, key):
        """Erreicht bis einschließlich Semester key plus dort und vorher Verplantes (Laufendes nur der Rest)."""
        done, _ = summary([a for a in self.study_achievements() if a["semesterLibDto"]["key"] <= key])
        return done, sum(m.remaining for m in self.study_modules() or []
                         if not m.done and m.planned and m.planned <= key)

    def planned_ects(self, key):
        return sum(m.remaining for m in self.study_modules() or [] if not m.done and m.planned == key)

    def fill_terms(self):
        """Semester vom ersten mit Leistungen bis zum nächsten nach dem aktuellen, dazu künftige
        bis Ende der Regelstudienzeit bzw. bis zum letzten verplanten (die haben noch keine id)."""
        mine = self.study_achievements()
        first = self.study_start()
        upper = max(s["key"] for s in self.all_semesters if s["key"] <= self.current["key"] or
                    s["id"] == self.current["id"] + 1) if self.all_semesters else self.current["key"]
        terms = [s for s in self.all_semesters if first <= s["key"] <= upper] or [self.current]
        modules = self.study_modules() or []
        last = max([add_terms(first, max(m.rec_terms[-1] if m.rec_terms else 1 for m in modules) - 1)]
                   if modules else [upper])
        last = max([last] + [m.planned for m in modules if m.planned and not m.done])
        key = next_key(upper)
        while key <= last:
            terms.append({"key": key, "id": None})
            key = next_key(key)
        terms.sort(key=lambda s: s["key"], reverse=True)

        lv = self.query_one("#terms", ListView)
        old = (self.selected_term() or {}).get("key") if self.terms else None
        self.terms = terms
        ects, _ = summary(mine)
        lv.clear()
        regular = max((m.rec_terms[-1] for m in modules if m.rec_terms), default=6)  # Regelstudienzeit laut Uni-Plan
        lv.append(ListItem(Label(f"[b]{'Gesamt':<16}{ects:>3}[/][dim]/{regular * ECTS_PER_TERM}[/]")))
        for t in self.terms:
            e, _ = summary([a for a in mine if a["semesterLibDto"]["key"] == t["key"]])
            planned = self.planned_ects(t["key"])
            mark = " [b yellow]●[/]" if t["id"] == self.current["id"] else ""
            if t["key"] < self.current["key"]:  # abgeschlossenes Semester
                right = f"[green]{e:>3}[/][dim]/{ECTS_PER_TERM}[/]"
            elif e or planned:
                number = f"{e + planned:>3}"
                right = (f"[cyan]{number}[/]" if planned else number) + f"[dim]/{ECTS_PER_TERM}[/]"
            else:
                right = "  –"
            fs = self.fachsemester(t["key"])
            name = f"[green]{term_label(t['key']):<12}[/]" if t["key"] < self.current["key"] else f"{term_label(t['key']):<12}"
            lv.append(ListItem(Label(f"[dim]{fs:>2}.[/] {name}{right}{mark}")))
        # Start: Gesamt (Anmeldungen dann vom aktuellen Semester); sonst gewähltes Semester behalten
        lv.index = next((i + 1 for i, t in enumerate(self.terms) if t["key"] == old), 0)
        self.show_term()

    def show_progress(self, progress):
        self.progress = progress
        self.show_header()

    def show_header(self):
        """Schnitt und ECTS wie in „Mein Studium“; solange das lädt, selbst gerechnet (ECTS-gewichtet)."""
        study = self.study()
        ects, avg = summary(self.study_achievements())
        name = val(study["intendedDegree"]) + " · " + val(study["name"]) if study else "RWTHonline"
        info = self.progress.get(study["studyId"], {}) if study else {}
        if info.get("totalCredits"):
            text = f"{info.get('achievedCredits', ects)}/{info['totalCredits']} ECTS"
        else:
            text = f"{ects} ECTS"
        if info.get("grade"):
            text += f" · Ø {info['grade']}"
        elif avg:
            text += f" · Ø ≈{fmt_grade(avg)}"
        more = f"  [dim](s: {len(self.studies)} Studiengänge)[/]" if len(self.studies) > 1 else ""
        self.query_one("#study", Label).update(f"[b]{escape(name)}[/] · {text}{more}")

    def selected_term(self):
        index = self.query_one("#terms", ListView).index or 0
        return None if index == 0 or index > len(self.terms) else self.terms[index - 1]

    def show_term(self):
        term = self.selected_term()
        mine = self.study_achievements()
        if term:
            mine = [a for a in mine if a["semesterLibDto"]["key"] == term["key"]]
        self.show_achievements(sorted(mine, key=lambda a: val(a.get("achievementDate")), reverse=True))
        target = term or self.current
        self.query_one("#tabs", TabbedContent).get_tab("tab-registrations").label = \
            f"Anmeldungen {term_label(target['key'])}"
        if target["id"] is None:  # künftiges Semester, gibt es in RWTHonline noch nicht
            self.show_registrations(target, [])
        else:
            self.load_registrations(target)
        self.show_plan()

    def show_achievements(self, achievements):
        self.shown_achievements = achievements
        table = self.query_one("#achievements", DataTable)
        table.clear()
        for a in achievements:
            grade = (a.get("gradeDto") or {}).get("nameShort") or ""
            if a.get("achievementStatusType") != "FINAL":
                grade = f"[dim strike]{grade}[/]"
            elif not counts(a):
                grade = f"[b red]{grade}[/]"
            title = val((a.get("cpCourseLibDto") or {}).get("courseTitle")) or val(a.get("subject"))
            table.add_row(term_label(a["semesterLibDto"]["key"]), fmt_dt(a.get("achievementDate"), time=False),
                          grade, str(a.get("credits") or ""), escape(title))
        self.refresh_detail()

    def show_registrations(self, term, regs):
        if term["key"] != (self.selected_term() or self.current)["key"]:
            return
        self.regs = regs
        table = self.query_one("#registrations", DataTable)
        table.clear()
        now = datetime.now().isoformat()
        for r in regs:
            c = r["course"]
            status = r.get("courseGroupRegistrationStatusType", {}).get("key", "")
            dereg = val(r.get("deregistrationEnd"))
            dereg_text = fmt_dt(r.get("deregistrationEnd"))
            if dereg and dereg >= now and r.get("deregistrationFlag"):
                dereg_text = f"[b yellow]{dereg_text}[/]"
            elif dereg:
                dereg_text = f"[dim]{dereg_text}[/]"
            table.add_row(c["courseTypeDto"]["key"], STATUS.get(status, status), dereg_text,
                          escape(r.get("courseGroupName") or ""), escape(val(c["courseTitle"])))
        self.refresh_detail()

    def show_exams(self, exams):
        self.exams = exams
        table = self.query_one("#exams", DataTable)
        table.clear()
        for e in exams:
            table.add_row(fmt_dt(e.get("examDate") or e.get("date") or e.get("examStart")),
                          escape(val(e.get("courseTitle")) or val(e.get("title")) or val(e.get("examName")) or str(e.get("id", ""))),
                          escape(str(e.get("registrationStatus") or e.get("status") or "")))
        tab = self.query_one("#tabs", TabbedContent).get_tab("tab-exams")
        tab.label = f"Prüfungen ({len(exams)})" if exams else "Prüfungen"
        self.refresh_detail()

    def show_plan(self, keep=None):
        """Semesterplan: je Semester eine Überschrift mit ECTS/30, danach „nicht geplant“. Abgeschlossene
        Semester (grün) zeigen, was dort bestanden und belegt wurde und was aus dem Uni-Plan dafür noch fehlt;
        ab dem aktuellen die verplanten und belegten Module. u blendet Uni-Vorschläge ein, e Erledigtes;
        bei gewähltem Semester nur dessen Abschnitt. keep = Cursor-Modul."""
        table = self.query_one("#plan", DataTable)
        total_label = self.query_one("#plan-sum", Label)
        if keep is None and table.row_count and table.cursor_row < len(self.plan_rows):
            keep = self.plan_rows[table.cursor_row][1]  # Modul oder Semester-Überschrift unter dem Cursor
        table.clear()
        self.plan_rows = []
        study, modules = self.study(), self.study_modules()
        if modules is None:
            error = self.plan_errors.get(study["studyId"]) if study else None
            total_label.update(f"[red]{escape(error)}[/]" if error else
                               "Lade Studienplan …" if study and study.get("myStudyLink") else
                               "Für diese Studienrichtung gibt es keinen Studienplan.")
            self.refresh_detail()
            return
        total_label.update(self.plan_sum(modules))
        modules = sort_modules(modules)
        term = self.selected_term()
        open_ = [m for m in modules if not m.done]

        def add_module(m, uni_only=False, key=None, note=""):
            mark, color = self.module_style(m, key)
            ects = f"{m.achieved}/{m.total}" if m.achieved and not m.done else str(m.total)
            uni = self.uni_keys(m)
            uni_text = f"{m.rec_terms[0]}." + ("+" if len(uni) > 1 else "") if uni else ""
            if m.planned and uni and m.planned > uni[-1] and not m.done:
                uni_text = f"[#ffd700]{uni_text}[/]"  # später geplant als die Uni vorschlägt
            name = f"[{color}]{escape(m.name)}[/]" + (f"  [dim]Note {m.grade}[/]" if m.grade and m.done else "")
            if key and m.courses.get(key) and not (m.done and m.done_term == key):
                types = sorted({r["course"]["courseTypeDto"]["key"] for r in m.courses[key]})
                name += f"  [{BOOKED}]({'/'.join(types)})[/]"
            if key and m.done and m.done_term != key:
                note = f"(bestanden {term_label(m.done_term)})" if m.done_term else "(bestanden)"
            if m.suggested and not uni_only and m.planned == key:
                note = (note + " " if note else "") + "(Vorschlag)"
            if note:
                name += f"  [dim]{note}[/]"
            if uni_only:
                name = f"[dim]{escape(m.name)}  (Uni)[/]"
            table.add_row(f"[{color}]{mark}[/]", ects, uni_text, INDENT + name)  # eingerückt unter die Überschrift
            self.plan_rows.append(("module", m))

        def heading(text, right="", row=("info", None)):
            if table.row_count:  # Leerzeile trennt die Abschnitte
                table.add_row("", "", "", "")
                self.plan_rows.append(("info", None))
            table.add_row("", right, "", f"[b]── {text} ──[/]")
            self.plan_rows.append(row)

        if self.show_done and not term:
            done = [m for m in modules if m.done]
            heading(f"erledigt ({len(done)})", str(sum(m.achieved for m in done)))
            for m in done:
                add_module(m)

        keys = [t["key"] for t in sorted(self.terms, key=lambda t: t["key"])]
        for key in ([term["key"]] if term else keys):
            if key < self.current["key"]:
                self.add_past_term(key, modules, heading, add_module)
                continue
            ects = self.planned_ects(key)
            color = "yellow" if ects > ECTS_PER_TERM else "cyan" if ects else "dim"
            heading(f"{self.fachsemester(key)}. {term_label(key)}", f"[{color}]{ects}[/][dim]/{ECTS_PER_TERM}[/]",
                    ("term", key))
            mine = [m for m in open_ if m.planned == key]
            mine += [m for m in modules if m.courses.get(key) and m not in mine]  # belegt, aber anders geplant
            for m in mine:
                add_module(m, key=key)
            if self.show_uni:
                for m in modules:
                    if key in self.uni_keys(m) and m not in mine:
                        add_module(m, uni_only=True)

        if not term:
            loose = [m for m in open_ if not m.planned and self.current["key"] not in m.courses]  # Belegtes steht oben
            heading(f"nicht geplant ({len(loose)})")
            for area, need in self.area_needs(modules):
                choice = [m for m in loose if m.area == area]
                if not choice:
                    continue
                if sum(m.remaining for m in choice) > need:  # Auswahl: nur ein Teil davon nötig
                    table.add_row("", "", "", f"{INDENT}[i]{escape(area_short(area))}: noch {need} ECTS wählen[/]")
                    self.plan_rows.append(("info", None))
                for m in choice:
                    add_module(m)
            for m in loose:
                if m.area not in {a for a, _ in self.area_needs(modules)}:
                    add_module(m)

        rows = [r[1] for r in self.plan_rows]
        if keep is not None and keep in rows:
            table.move_cursor(row=rows.index(keep))
        elif self.current["key"] in rows:  # Start: aktuelles Semester, abgeschlossene darüber
            table.move_cursor(row=rows.index(self.current["key"]))
        self.refresh_detail()

    def add_past_term(self, key, modules, heading, add_module):
        """Abgeschlossenes Semester: dort Bestandenes, dort Belegtes, dann der Uni-Plan fürs Fachsemester."""
        e, _ = summary([a for a in self.study_achievements() if a["semesterLibDto"]["key"] == key])
        heading(f"{self.fachsemester(key)}. {term_label(key)}  [green]✓ abgeschlossen[/]",
                f"[green]{e}[/][dim]/{ECTS_PER_TERM}[/]",
                ("term", key))
        shown = [m for m in modules if m.done and m.done_term == key]
        shown += [m for m in modules if m.courses.get(key) and m not in shown]
        for m in shown:
            add_module(m, key=key)
        for m in modules:
            if key in self.uni_keys(m) and m not in shown:
                add_module(m, key=key, note="" if m.done else
                           "fehlt" + (f" → {term_label(m.planned)}" if m.planned else ""))

    @staticmethod
    def module_style(m, key=None):
        """Symbol und Farbe: grün bestanden, orange/rot 1/≥2 Fehlversuche, lila in Semester key belegt
        (LV-Anmeldung), blau läuft (Teilleistung/Prüfung), geplant normal, sonst grau."""
        if m.done:
            return "✓", "green"
        if m.failed:  # orange nach einem Fehlversuch, rot ab dem zweiten
            return "✗", "red" if m.attempts >= 2 else "#ff8c00"
        if key and m.courses.get(key):
            return "◆", BOOKED
        if m.status == "IN_PROGRESS" or m.achieved:
            return "▶", "#5fafff"
        return "○", "default" if m.planned else "dim"

    def area_needs(self, modules):
        """[(Bereich, noch fehlende ECTS abzüglich Verplantem)] in der Reihenfolge von RWTHonline."""
        result = []
        for area in self.areas.get(self.study()["studyId"], []):
            info = area.get("progressInfo") or {}
            if info.get("totalCredits"):
                name = val(area["name"])
                planned = sum(m.remaining for m in modules if m.area == name and m.planned and not m.done)
                result.append((name, max(info["totalCredits"] - (info.get("achievedCredits") or 0) - planned, 0)))
        return result

    def term_md(self, key):
        """Semester-Überschrift: eigener Plan gegen 30 ECTS und den Semesterplan der Uni."""
        fs = self.fachsemester(key)
        modules = self.study_modules() or []
        mine = [m for m in modules if m.planned == key and not m.done]
        uni = [m for m in modules if key in self.uni_keys(m)]
        done, planned = self.ects_until(key)
        if key < self.current["key"]:
            e, _ = summary([a for a in self.study_achievements() if a["semesterLibDto"]["key"] == key])
            lines = [f"## {term_label(key)} – {fs}. Fachsemester (abgeschlossen)", "",
                     f"- Erreicht: **{e} ECTS** (Uni empfiehlt {ECTS_PER_TERM})",
                     f"- Bis hier: du **{done}** ECTS, Uni {fs * ECTS_PER_TERM}"]
        else:
            lines = None
        lines = lines or [f"## {term_label(key)} – {fs}. Fachsemester", "",
                 f"- Geplant: **{sum(m.remaining for m in mine)} ECTS** in {len(mine)} Modulen "
                 f"(Uni empfiehlt {ECTS_PER_TERM})",
                 f"- Bis hier: du **{done + planned}** ECTS ({done} erreicht + {planned} geplant), "
                 f"Uni {fs * ECTS_PER_TERM}"]
        if uni:
            lines += ["", "### Semesterplan der Uni", ""]
            for m in uni:
                where = ("erledigt" if m.done else "hier geplant" if m.planned == key else
                         f"geplant {term_label(m.planned)}" if m.planned else "nicht geplant")
                lines.append(f"- {escape(m.name)} ({m.total} ECTS) – _{where}_")
        booked = [(m.name, r) for m in modules for r in m.courses.get(key, [])]
        loose = self.loose_courses.get(self.study()["studyId"], {}).get(key, [])
        if booked or loose:
            lines += ["", "### Belegte Lehrveranstaltungen", ""]
            lines += [f"- {r['course']['courseTypeDto']['key']} {escape(val(r['course']['courseTitle']))} "
                      f"→ _{escape(name)}_" for name, r in booked]
            lines += [f"- {r['course']['courseTypeDto']['key']} {escape(val(r['course']['courseTitle']))} "
                      f"→ _kein Modul erkannt_" for r in loose]
        return "\n".join(lines)

    def plan_sum(self, modules):
        """Je Bereich: erreicht + verplant / gefordert (grün, wenn der Plan reicht)."""
        parts, done_sum, plan_sum, total_sum = [], 0, 0, 0
        for area in self.areas.get(self.study()["studyId"], []):
            info = area.get("progressInfo") or {}
            if not info.get("totalCredits"):
                continue
            name = val(area["name"])
            achieved, total = info.get("achievedCredits") or 0, info["totalCredits"]
            planned = sum(m.remaining for m in modules if m.area == name and m.planned and not m.done)
            color = "green" if achieved + planned >= total else "default"
            text = f"{area_short(name)} [{color}]{achieved}" + (f"[cyan]+{planned}[/]" if planned else "")
            parts.append(text + f"/{total}[/]")
            done_sum, plan_sum, total_sum = done_sum + achieved, plan_sum + planned, total_sum + total
        parts.insert(0, f"[b]Σ {done_sum}" + (f"[cyan]+{plan_sum}[/]" if plan_sum else "") + f"/{total_sum}[/]")
        return " · ".join(parts)

    # ------------------------------------------------------------------ Detail

    def active_table(self):
        return self.query_one("#" + self.query_one("#tabs", TabbedContent).active.removeprefix("tab-"), DataTable)

    def refresh_detail(self):
        table = self.active_table()
        row = table.cursor_row if table.row_count else None
        if table.id == "achievements":
            if row is None:
                self.set_detail("_Keine Leistungen in diesem Semester._")
            else:
                self.set_detail(self.achievement_md(self.shown_achievements[row]))
        elif table.id == "registrations":
            self.set_detail(self.registration_md(self.regs[row]) if row is not None else "_Keine Anmeldungen._")
        elif table.id == "plan":
            kind, obj = self.plan_rows[row] if row is not None and row < len(self.plan_rows) else ("info", None)
            self.set_detail(self.module_md(obj) if kind == "module" else self.term_md(obj) if kind == "term" else
                            "_+/- verschiebt ein Modul ins nächste/vorige Semester, x nimmt es aus dem Plan._")
        else:
            self.set_detail(f"```\n{self.exams[row]}\n```" if row is not None else "_Keine Prüfungsanmeldungen._")

    def set_detail(self, text):
        """Erst kurz nach der letzten Änderung zeichnen: überlappende Markdown-Updates
        (schnelles Scrollen, +/-) brechen sich sonst gegenseitig ab und hinterlassen Reste."""
        if text == self.detail_text:
            return
        self.detail_text = text
        if self.detail_timer:
            self.detail_timer.stop()
        self.detail_timer = self.set_timer(0.08, lambda: self.query_one("#detail", Markdown).update(self.detail_text))

    def achievement_md(self, a):
        c = a.get("cpCourseLibDto") or {}
        grade = a.get("gradeDto") or {}
        title = val(c.get("courseTitle")) or val(a.get("subject"))
        lines = [f"## {title}", ""]
        number = (c.get("courseNumber") or {}).get("courseNumber")
        if number:
            lines.append(f"**{number}** · {val((c.get('courseTypeDto') or {}).get('courseTypeName'))} · "
                         f"{c.get('semesterHours', '?')} SWS")
        lines += ["", f"- Note: **{grade.get('nameShort', '')}** ({val(grade.get('name'))})",
                  f"- ECTS: **{a.get('credits') or 0}**" + ("" if counts(a) else " (zählt nicht)"),
                  f"- Datum: {fmt_dt(a.get('achievementDate'), time=False)} · {val(a['semesterLibDto']['semesterDesignation'])}",
                  f"- Art: {val(a.get('achievementDetailTypeName'))}"]
        if a.get("achievementStatusType") != "FINAL":
            lines.append(f"- Status: **{a.get('achievementStatusType')}** (ungültig/ersetzt)")
        if c.get("id"):
            lines += ["", "_o: Lehrveranstaltung im Browser_"]
        return "\n".join(lines)

    def registration_md(self, r):
        c = r["course"]
        status = r.get("courseGroupRegistrationStatusType", {}).get("key", "")
        sws = next((n.get("value") for n in r.get("courseNormConfigs", []) if n.get("key") == "SST"), "?")
        lines = [f"## {val(c['courseTitle'])}", "",
                 f"**{c['courseNumber']['courseNumber']}** · {val(c['courseTypeDto']['courseTypeName'])} · {sws} SWS", "",
                 f"- Status: **{STATUS.get(status, status)}** – {r.get('requirementsCheckInfo') or ''}",
                 f"- Gruppe: {r.get('courseGroupName') or ''}",
                 f"- Verfahren: {val(r.get('registrationProcedureName'))}",
                 f"- Anmeldung bis: {fmt_dt(r.get('registrationEnd'))}",
                 f"- Abmeldung bis: {fmt_dt(r.get('deregistrationEnd'))}"
                 + ("" if r.get("deregistrationFlag") else " (nicht mehr möglich)"),
                 f"- Platzvergabe: {fmt_dt(r.get('allocationDate'))}",
                 f"- Studium: {val((r.get('studyProgramme') or {}).get('displayName'))}",
                 "", "_o: im Browser (dort ab- oder ummelden)_"]
        return "\n".join(lines)

    def module_md(self, m):
        lines = [f"## {m.name}", "", f"**{m.code}** · {escape(' › '.join(m.paths[0]))}"]
        lines += [f"- auch: {' › '.join(p)}" for p in m.paths[1:]]
        status = MODULE_STATUS.get(m.status, m.status)
        if m.failed:
            status = "bisher nicht bestanden" + (f" – {m.attempts}. Fehlversuch" if m.attempts else "") + \
                     (f" von {m.max_attempts} Versuchen" if m.max_attempts else "")
        lines += ["", f"- Status: **{status}**",
                  f"- ECTS: **{m.achieved}/{m.total}**" + (f" · Note **{m.grade}**" if m.grade else "")]
        if m.rec_terms:
            lines.append(f"- Uni-Semesterplan: {' + '.join(f'{n}.' for n in m.rec_terms)} Fachsemester "
                         f"({', '.join(term_label(k) for k in self.uni_keys(m))})")
        if not m.done:
            lines.append(f"- Geplant: {term_label(m.planned) if m.planned else '–'}")
        elif m.done_term:
            lines.append(f"- Bestanden: {term_label(m.done_term)}")
        for key, regs in sorted(m.courses.items()):
            for r in regs:
                lines.append(f"- Belegt {term_label(key)}: {r['course']['courseTypeDto']['key']} "
                             f"{escape(val(r['course']['courseTitle']))}")

        def parts(children, depth):
            for c in children:
                info = c.get("progressInfo") or {}
                name = val(c.get("name"))
                if c.get("elementType") == "EXAM":
                    grade = info.get("grade") if info.get("grade") not in (None, "-") else ""
                    ok = "✓" if info.get("success") else "✗ (nicht bestanden)" if grade else "○"
                    yield (f"{'  ' * depth}- {ok} {name} – {info.get('achievedCredits') or 0}/"
                           f"{info.get('totalCredits') or 0} ECTS" + (f", Note **{grade}**" if grade else ""))
                elif name:
                    yield f"{'  ' * depth}- _LV: {name}_"
                yield from parts(c.get("children") or [], depth + 1)

        children = list(parts(m.raw.get("children") or [], 0))
        if children:
            lines += ["", "### Bestandteile", *children]
        if not m.done:
            lines += ["", "_+/-: Semester verschieben · x: aus dem Plan nehmen_"]
        return "\n".join(lines)

    # ------------------------------------------------------------------ Planen

    def check_action(self, action, parameters):
        if action in PLAN_ACTIONS:
            try:
                return self.query_one("#tabs", TabbedContent).active == "tab-plan"
            except NoMatches:
                return False
        return True

    def selected_module(self):
        table = self.query_one("#plan", DataTable)
        if not table.row_count or table.cursor_row >= len(self.plan_rows):
            return None
        kind, obj = self.plan_rows[table.cursor_row]
        return obj if kind == "module" else None

    def plan_module(self, m, key):
        self.plan_store.set(self.study()["studyId"], m, key)
        self.fill_terms()
        self.show_plan(keep=m)
        self.notify(f"{m.name} → {term_label(key) if key else 'nicht geplant'}", timeout=3)

    def shift(self, step):
        m = self.selected_module()
        if not m:
            return
        if m.done:
            self.notify("Schon abgeschlossen.", severity="warning", timeout=3)
            return
        now = self.current["key"]
        if m.planned is None:  # erst mal ins empfohlene Semester, frühestens das aktuelle
            key = add_terms(self.study_start(), m.recommended - 1) if m.recommended else next_key(now)
        else:
            key = step(m.planned)
        self.plan_module(m, max(key, now))

    def action_plan_later(self):
        self.shift(next_key)

    def action_plan_earlier(self):
        self.shift(prev_key)

    def action_toggle_uni(self):
        self.show_uni = not self.show_uni
        self.show_plan()

    def action_toggle_done(self):
        self.show_done = not self.show_done
        self.show_plan()

    def action_plan_remove(self):
        m = self.selected_module()
        if m and not m.done and m.planned:
            self.plan_module(m, None)

    # ------------------------------------------------------------------ Ereignisse

    def on_list_view_highlighted(self, event: ListView.Highlighted):
        if self.terms:
            self.show_term()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.data_table is self.active_table():
            self.refresh_detail()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated):
        self.refresh_bindings()
        self.refresh_detail()

    def action_next_study(self):
        if len(self.studies) > 1:
            self.study_index = (self.study_index + 1) % len(self.studies)
            self.show_study()
            self.load_plan(self.study())

    def action_open_browser(self):
        table = self.active_table()
        row = table.cursor_row if table.row_count else None
        if row is None:
            open_external(f"{SITE}/ee/ui/ca2/app/desktop/")
        elif table.id == "achievements" and (self.shown_achievements[row].get("cpCourseLibDto") or {}).get("id"):
            open_external(course_url(self.shown_achievements[row]["cpCourseLibDto"]["id"]))
        elif table.id == "registrations":
            open_external(course_url(self.regs[row]["course"]["id"]))


def main():
    RwthApp().run()
    return 0
