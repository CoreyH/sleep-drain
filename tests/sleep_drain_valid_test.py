#!/usr/bin/python3 -I
"""Hardware-free checks for the summary sleep-drain-log appends at resume: the
hook's own statement (the awk program single-quoted inside it), taken as it
stands and run through bash on pre/post state lines, so a quoting slip in the
program fails here too.

  python3 -I tests/sleep_drain_valid_test.py      (from the repo root)

The first case is the 2026-11-02 timed sleep on aurora14: on battery the whole
time, the coulomb counter down by 140976 mA·s, and the gauge's energy up by
0.78 Wh (it re-estimates after a rest). It must count as valid.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "sleep-drain-log")


def statement():
    """The hook's line+=" $(awk ... '...')" statement, verbatim."""
    with open(HOOK) as f:
        src = f.read()
    m = re.search(r"""^\s*(line\+=" \$\(awk -v pre="\$pre" -v post="\$now" '.*?'\)")$""", src, re.S | re.M)
    if not m:
        sys.exit("can't find the resume summary in sleep-drain-log")
    return m.group(1)


def summary(pre, post):
    script = 'pre=$1 now=$2 line=""\n' + statement() + '\nprintf "%s\\n" "$line"\n'
    p = subprocess.run(["bash", "-c", script, "bash", pre, post],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    got = dict(kv.split("=", 1) for kv in p.stdout.split() if "=" in kv)
    if p.returncode or p.stderr:
        got["error"] = p.stderr.strip() or f"exit {p.returncode}"
    return got


# From /var/log/sleep-drain.log, 2026-11-02 15:06:38 -> 15:21:37 (trimmed)
PRE = "ts=1793628397 energy_uwh=58710000 charge_uah=5150000 capacity=98 ac=0 cc=4928420 uv=12568000"
POST = "ts=1793629297 energy_uwh=59485200 charge_uah=5218000 capacity=98 ac=0 cc=4787444 uv=12630000"


def edit(line, **kv):
    for k, v in kv.items():
        line = re.sub(rf"\b{k}=\S+", f"{k}={v}", line)
    return line


CASES = [
    ("2026-11-02 aurora14: energy up, counter down, on battery", PRE, POST,
     {"valid": "yes", "slept_s": "900", "avg_mw_cc": "1974"}),
    ("the counter went up: charged while asleep", PRE, edit(POST, cc=4990000), {"valid": "no-charged"}),
    ("no counter: falls back to energy, which went up", edit(PRE, cc="-"), edit(POST, cc="-"),
     {"valid": "no-charged", "avg_mw_cc": "-"}),
    ("no counter, energy down", edit(PRE, cc="-"), edit(POST, cc="-", energy_uwh=58200000),
     {"valid": "yes", "avg_mw_cc": "-"}),
    ("counter on one side only: falls back to energy", PRE, edit(POST, cc="-", energy_uwh=58200000),
     {"valid": "yes", "avg_mw_cc": "-"}),
    ("started on the charger", edit(PRE, ac=1), POST, {"valid": "no-ac"}),
    ("under 600 s", PRE, edit(POST, ts=1793628900), {"valid": "no-short"}),
]

if __name__ == "__main__":
    failed = 0
    for name, pre, post, want in CASES:
        got = summary(pre, post)
        ok = "error" not in got and all(got.get(k) == v for k, v in want.items())
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            failed += 1
            print(f"     want {want}\n     got  {got}")
    sys.exit(1 if failed else 0)
