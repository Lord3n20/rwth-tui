import sys


def main():
    args = sys.argv[1:]
    if args[:1] == ["login"]:
        from .login import login
        return login(args[1:])
    if args[:1] == ["logout"]:
        from .login import logout
        return logout()
    if args[:1] == ["dump"]:
        from .dump import main as dump
        return dump(args[1:])
    if args[:1] == ["secret"]:
        from .credentials import setup
        return setup(args[1:])
    if args[:1] == ["code"]:
        from .credentials import code
        return code(args[1:])
    if not args:
        from .app import main as app
        return app()
    print("Aufruf: rwth [login [--show] | logout | dump [PFAD …] | secret [user|password|totp|token] | code]  (ohne Argument: Ansicht)")
    return 2


sys.exit(main())
