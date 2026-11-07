"""YT Profile Manager — entry point.

  python main.py                     -> opens the app
  python main.py --run <id>          -> (internal) runs one profile's browser
  python main.py --selftest <file>   -> (build check) tests browser + sync pieces
"""
import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fix_std_streams():
    """In the windowed .exe, stdin/stdout can be None even when the parent
    gave us pipes. Re-open them from the OS handles."""
    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w")):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, os.fdopen(fd, mode, encoding="utf-8", buffering=1))
                continue
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    import ctypes
                    import msvcrt
                    h = ctypes.windll.kernel32.GetStdHandle(-10 if fd == 0 else -11)
                    new_fd = msvcrt.open_osfhandle(h, os.O_RDONLY if fd == 0 else os.O_WRONLY)
                    setattr(sys, name, os.fdopen(new_fd, mode, encoding="utf-8", buffering=1))
                except Exception:
                    pass
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _main():
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--run":
        _fix_std_streams()
        from app import runner
        sys.exit(runner.run(args[1], force="--force" in args))
    if args and args[0] == "--echo":
        _fix_std_streams()
        line = sys.stdin.readline().strip()
        sys.stdout.write(f"pong:{line}\n")
        sys.stdout.flush()
        sys.exit(0)
    if len(args) >= 2 and args[0] == "--selftest":
        from app import selftest
        sys.exit(selftest.run(args[1]))
    from app import gui
    gui.main()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    _main()
