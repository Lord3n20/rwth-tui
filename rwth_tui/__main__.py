import sys


def main():
    args = sys.argv[1:]
    if args[:1] == ["login"]:
        from .login import login
        return login()
    if args[:1] == ["logout"]:
        from .login import logout
        return logout()
    if args[:1] == ["dump"]:
        from .dump import main as dump
        return dump(args[1:])
    print("Aufruf: rwth [login | logout | dump [PFAD …]]  (Ansicht folgt)")
    return 2


sys.exit(main())
