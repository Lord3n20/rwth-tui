"""`rwth dump [PFAD …]`: Rohantworten (JSON) speichern, um die Datenstruktur zu erkunden. Rein lesend."""

import json

from .api import CACHE_DIR, Rwth, RwthError

DUMP_DIR = CACHE_DIR / "dump"
# Aus dem Frontend-Code (ee/ui/ca2) erschlossen.
DEFAULT_PATHS = [
    "brm.sys.dtop.home/v1/home",
    "slc.xm.ac/achievements",
    "slc.tm.cp/student/myCourses",
    "slc.xm.exr/registered-exams",
    "slc.cm.cs/student/myStudies/chooser",
]


def _links(obj, out):
    """Alle Links (name/key/rel → href) einsammeln – zeigt, wohin man weiter abfragen kann."""
    if isinstance(obj, dict):
        if "href" in obj and isinstance(obj["href"], str):
            out.add((obj.get("name") or obj.get("key") or obj.get("rel") or "", obj["href"]))
        for v in obj.values():
            _links(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _links(v, out)


def main(args):
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    DUMP_DIR.chmod(0o700)
    try:
        rwth = Rwth()
    except RwthError as e:
        print(e)
        return 1
    failed = 0
    for path in args or DEFAULT_PATHS:
        name = path.strip("/").replace("/", "_").replace("?", "_")
        try:
            data = rwth.get(path)
        except RwthError as e:
            print(f"✗ {path}: {e}")
            failed += 1
            continue
        (DUMP_DIR / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
        links = set()
        _links(data, links)
        (DUMP_DIR / f"{name}.links").write_text("".join(f"{n}\t{h}\n" for n, h in sorted(links)))
        print(f"✓ {path} → {DUMP_DIR / name}.json ({len(links)} Links)")
    return 1 if failed else 0
