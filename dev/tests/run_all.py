"""Run every suite in this folder, one subprocess each (they monkeypatch
app modules and set env, so they must not share an interpreter).

    py dev/tests/run_all.py

Flake armor (2026-09-14, after test_batch6/test_backorder failed
in-batch but always passed standalone, printing NOTHING): each suite
gets its OWN fresh temp directory (TEMP/TMP overridden), so the fixed-
name sqlite file a suite deletes on startup never exists - the bare
"FAIL with no output" was that delete dying on a transient Windows
file lock, with the traceback going to the stderr nobody printed.
stderr now prints on failure, and a failed suite runs once more (a
pass on retry is reported as FLAKY, and still counts as a failure of
the runner's determinism worth reading).
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
suites = sorted(
    p for p in glob.glob(os.path.join(HERE, "test_*.py"))
)


def run_suite(path: str) -> subprocess.CompletedProcess:
    """One suite in its own interpreter AND its own temp dir."""
    tmp = tempfile.mkdtemp(prefix="rfid_suite_")
    env = {**os.environ, "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp}
    try:
        return subprocess.run(
            [sys.executable, path], capture_output=True, env=env
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def tail(r: subprocess.CompletedProcess) -> list[str]:
    out = (r.stdout or b"").decode("utf-8", "replace").splitlines()
    err = (r.stderr or b"").decode("utf-8", "replace").splitlines()
    lines = out[-15:]
    if err:
        lines += ["--- stderr ---", *err[-15:]]
    return lines


failed = []
flaky = []
for path in suites:
    name = os.path.basename(path)
    r = run_suite(path)
    if r.returncode == 0:
        print("PASS  " + name)
        continue
    first = r
    r = run_suite(path)
    if r.returncode == 0:
        print("FLAKY " + name + "  (failed once, passed on retry)")
        flaky.append(name)
        for line in tail(first):
            print("      " + line)
        continue
    print("FAIL  " + name)
    failed.append(name)
    for line in tail(r):
        print("      " + line)

print()
print(f"{len(suites) - len(failed)}/{len(suites)} suites passed"
      + (f" — FAILED: {', '.join(failed)}" if failed else "")
      + (f" — FLAKY: {', '.join(flaky)}" if flaky else ""))
sys.exit(1 if failed else 0)
