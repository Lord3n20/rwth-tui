# rwth-tui

Inoffizielle Terminal-Oberfläche (Textual) für RWTHonline: Leistungen und Noten, LV-Anmeldungen,
Prüfungsanmeldungen und eine Studienplanung, ohne Browser.

- **Leistungen**: Noten, ECTS, Schnitt; nicht bestanden rot, ungültig durchgestrichen.
- **Anmeldungen**: LV-Anmeldungen des gewählten Semesters samt Abmeldefrist und zugehörigem Modul.
- **Prüfungen**: angemeldete Prüfungen.
- **Plan**: Module aus „Mein Studium“ auf Semester verteilen und mit dem Semesterplan der Uni
  (30 ECTS je Semester) vergleichen. Ungeplante Module stehen als Vorschlag im empfohlenen Semester
  (Wahlbereiche ausgenommen). Abgeschlossene Semester zeigen, was dort bestanden und belegt wurde
  und was aus dem Uni-Plan noch fehlt.

Die Planung liegt nur lokal in `~/.config/rwth-tui/plan.json`. Gegenüber RWTHonline ist das Programm
rein lesend (nur GET, plus Token-Erneuerung).

## Installation

```sh
python -m venv ~/.local/share/rwth-tui/venv
~/.local/share/rwth-tui/venv/bin/pip install git+https://github.com/Lord3n20/rwth-tui
```

Startskript `~/.local/bin/rwth`:

```sh
#!/bin/sh
exec "$HOME/.local/share/rwth-tui/venv/bin/python" -m rwth_tui "$@"
```

Für den Rückfall-Login im Fenster (`rwth login --show`) braucht das System-Python GTK 3 und
WebKit2GTK 4.1 (Arch: `python-gobject webkit2gtk-4.1`).

## Anmeldung

Der Login läuft ohne Browser per HTTP über das RWTH-SSO und braucht ein TOTP-Token:

1. Im IdM-Selfservice der RWTH ein zusätzliches TOTP-Token anlegen,
   z. B. mit der Beschreibung `tui`.
2. `rwth secret` – fragt Benutzername, Passwort, TOTP-Geheimnis (Base32 oder `otpauth://`-Link) und
   die Token-Beschreibung (leer = `tui`) ab. `rwth code` zeigt den aktuellen Code zum Bestätigen des Tokens.
3. `rwth login`

> **Achtung:** Die Zugangsdaten liegen **unverschlüsselt** in `~/.config/rwth-tui/credentials.json`
> (Rechte 600). Wer die Datei hat, hat Passwort und zweiten Faktor. Ohne gespeicherte Daten öffnet
> `rwth login` stattdessen ein Anmeldefenster.

Eine abgelaufene Sitzung meldet das Programm still neu an. `rwth logout` löscht die Sitzung.

## Benutzung

`rwth` startet die Ansicht. Links wählt man ein Semester (oder „Gesamt“), rechts die Reiter.

| Taste | |
|---|---|
| `s` | Studiengang wechseln |
| `o` | Lehrveranstaltung im Browser öffnen |
| `+` / `-` | Modul ein Semester später / früher planen (Plan-Reiter) |
| `x` | Modul aus dem Plan nehmen |
| `u` | Vorschläge aus dem Semesterplan der Uni einblenden |
| `e` | Erledigte Module einblenden |
| `z` | LV-Anmeldung von Hand einem Modul zuordnen (Reiter Anmeldungen) |
| `r` | Neu laden |

Farben im Plan: grün bestanden, lila belegt (LV-Anmeldung), blau läuft, orange/rot nach 1/≥2
Fehlversuchen, grau ungeplant.

RWTHonline verknüpft LV-Anmeldungen und Leistungen nicht direkt mit Modulen. Anmeldungen werden über
ihr Anmeldeverfahren zugeordnet (heißt oft wie das Modul), Leistungen über die angemeldete LV, sonst
über Namen und ECTS. Liegt eine Zuordnung daneben, korrigiert `z` sie (gespeichert in `plan.json`, ✎).

`python tests/fake_run.py ORDNER` spielt die Ansicht offline mit den zwischengespeicherten Antworten
aus `~/.cache/rwth-tui/json` durch (Screenshots + Plan als Text).

`rwth dump [PFAD …]` speichert Rohantworten der REST-API nach `~/.cache/rwth-tui/dump/`, um weitere
Endpunkte zu erkunden.

Diagnose beim Login: `RWTH_LOGIN_DEBUG=1`, `RWTH_LOGIN_DRYRUN=1` (nichts abschicken).

## Hinweis

Kein offizielles Projekt der RWTH Aachen. Es nutzt dieselbe REST-Schnittstelle wie die Web-Oberfläche
von RWTHonline und kann bei Änderungen dort jederzeit brechen.
