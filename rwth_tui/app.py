"""Textual-Oberfläche: Semester links, rechts Leistungen (Noten/ECTS), LV-Anmeldungen und Prüfungen."""

import subprocess
from datetime import datetime

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Label, ListItem, ListView, Markdown, TabbedContent, TabPane

from .api import SITE, NotLoggedIn, Rwth, RwthError

ALL = "alle"
STATUS = {"FIX": "Fixplatz", "BW": "beworben", "VOE": "vorgemerkt"}


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
    #left { width: 26; border-right: solid $primary-darken-2; }
    .title { padding: 0 1; text-style: bold; color: $accent; }
    #study { padding: 0 1; height: auto; }
    #tabs { height: 1fr; }
    #detail-box { height: 40%; border-top: solid $primary-darken-2; }
    ListView { height: 1fr; }
    """
    BINDINGS = [
        Binding("r", "refresh", "Neu laden"),
        Binding("s", "next_study", "Studiengang"),
        Binding("o", "open_browser", "Im Browser"),
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
                with VerticalScroll(id="detail-box"):
                    yield Markdown(id="detail")
        yield Footer()

    def on_mount(self):
        self.api = None
        self.studies, self.study_index = [], 0
        self.all_achievements, self.terms, self.current = [], [], None
        self.shown_achievements, self.regs, self.exams = [], [], []
        self.reg_cache, self.progress = {}, {}
        self.query_one("#achievements", DataTable).add_columns("Semester", "Datum", "Note", "ECTS", "Leistung")
        self.query_one("#registrations", DataTable).add_columns("Art", "Status", "Abmelden bis", "Gruppe", "Lehrveranstaltung")
        self.query_one("#exams", DataTable).add_columns("Datum", "Prüfung", "Status")
        self.query_one("#terms", ListView).focus()
        self.action_refresh()

    def fail(self, error):
        self.query_one("#study", Label).update(f"[b red]{escape(str(error))}[/]")
        if isinstance(error, NotLoggedIn):
            self.notify("Nicht angemeldet – `rwth login` ausführen.", severity="error", timeout=10)

    # ------------------------------------------------------------------ Laden

    def action_refresh(self):
        self.reg_cache.clear()
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
        except RwthError as e:
            self.call_from_thread(self.fail, e)

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
            self.study_index = next((i for i, s in enumerate(studies) if s.get("lastAccessed")), 0)
        self.studies, self.all_achievements, self.current = studies, achievements, current
        self.all_semesters = semesters
        self.show_study()

    def show_study(self):
        mine = self.study_achievements()
        # Semester: vom ersten mit Leistungen bis zum nächsten nach dem aktuellen.
        keys = {a["semesterLibDto"]["key"] for a in mine} | {self.current["key"]}
        first = min(keys)
        upper = max(s["key"] for s in self.all_semesters if s["key"] <= self.current["key"] or
                    s["id"] == self.current["id"] + 1) if self.all_semesters else self.current["key"]
        self.terms = sorted((s for s in self.all_semesters if first <= s["key"] <= upper),
                            key=lambda s: s["key"], reverse=True) or [self.current]
        ects, _ = summary(mine)
        self.show_header()

        lv = self.query_one("#terms", ListView)
        index = lv.index
        lv.clear()
        lv.append(ListItem(Label(f"[b]{'Gesamt':<12}{ects:>3} ECTS[/]")))
        for t in self.terms:
            e, _ = summary([a for a in mine if a["semesterLibDto"]["key"] == t["key"]])
            mark = " [b yellow]●[/]" if t["id"] == self.current["id"] else ""
            lv.append(ListItem(Label(f"{term_label(t['key']):<12}{e:>3} ECTS{mark}" if e else
                                     f"{term_label(t['key']):<12}   –{mark}")))
        lv.index = min(index or 0, len(self.terms))  # Start: Gesamt (Anmeldungen dann vom aktuellen Semester)
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
        return None if index == 0 else self.terms[index - 1]

    def show_term(self):
        term = self.selected_term()
        mine = self.study_achievements()
        if term:
            mine = [a for a in mine if a["semesterLibDto"]["key"] == term["key"]]
        self.show_achievements(sorted(mine, key=lambda a: val(a.get("achievementDate")), reverse=True))
        target = term or self.current
        self.query_one("#tabs", TabbedContent).get_tab("tab-registrations").label = \
            f"Anmeldungen {term_label(target['key'])}"
        self.load_registrations(target)

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
        if term["id"] != (self.selected_term() or self.current)["id"]:
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

    # ------------------------------------------------------------------ Detail

    def active_table(self):
        return self.query_one("#" + self.query_one("#tabs", TabbedContent).active.removeprefix("tab-"), DataTable)

    def refresh_detail(self):
        table = self.active_table()
        md = self.query_one("#detail", Markdown)
        row = table.cursor_row if table.row_count else None
        if table.id == "achievements":
            if row is None:
                md.update("_Keine Leistungen in diesem Semester._")
            else:
                md.update(self.achievement_md(self.shown_achievements[row]))
        elif table.id == "registrations":
            md.update(self.registration_md(self.regs[row]) if row is not None else "_Keine Anmeldungen._")
        else:
            md.update(f"```\n{self.exams[row]}\n```" if row is not None else "_Keine Prüfungsanmeldungen._")

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

    # ------------------------------------------------------------------ Ereignisse

    def on_list_view_highlighted(self, event: ListView.Highlighted):
        if self.terms:
            self.show_term()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        if event.data_table is self.active_table():
            self.refresh_detail()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated):
        self.refresh_detail()

    def action_next_study(self):
        if len(self.studies) > 1:
            self.study_index = (self.study_index + 1) % len(self.studies)
            self.show_study()

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
