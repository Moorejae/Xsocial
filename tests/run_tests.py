"""Run the project's tests with no third-party dependency.

Why not just pytest: this suite is executed by `scripts/x_daily.cmd` on every scheduled pass, on the
machine that publishes the videos. Adding pytest to that path adds a way for the pipeline to stop
("No module named pytest") that has nothing to do with publishing. The tests are plain `test_*`
functions with bare asserts, so pytest collects them too when someone has it.

    python tests\\run_tests.py            # everything
    python tests\\run_tests.py gemini     # modules whose name contains 'gemini'

Exit code 0 means every test passed, 1 means at least one failed — and `x_daily.cmd` refuses to
publish on a 1, because a broken fallback is exactly the state that caused the 2026-11-03 outage.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))


def modules(pattern: str = "") -> list[Path]:
    return sorted(p for p in HERE.glob("test_*.py") if pattern.lower() in p.name.lower())


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    pattern = argv[0] if argv else ""
    files = modules(pattern)
    if not files:
        print(f"no test modules matched {pattern!r} in {HERE}")
        return 1

    passed = failed = 0
    failures: list[tuple[str, str, str]] = []
    for path in files:
        mod = load(path)
        names = sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n)))
        if not names:
            continue
        print(f"\n{path.name}  ({len(names)} test(s))")
        for name in names:
            buf_out, buf_err = io.StringIO(), io.StringIO()
            try:
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    getattr(mod, name)()
                passed += 1
                print(f"  ok   {name}")
            except Exception:
                failed += 1
                detail = traceback.format_exc()
                failures.append((path.name, name, detail))
                print(f"  FAIL {name}")

    for fname, name, detail in failures:
        print(f"\n=== {fname}::{name} ===")
        print(detail)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
