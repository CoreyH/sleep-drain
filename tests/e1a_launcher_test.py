#!/usr/bin/env python3
"""e1-hook/run-e1a's arming and disarming, without root or a module.

Each case runs a copy of the launcher as uid 0 in a bubblewrap user namespace,
next to a fake e1_hook.ko, with its paths (E1A_*) pointed into a temp
directory and stub insmod, rmmod, modinfo and sleep-drain-reading that record
their calls. The module must stay loaded only after a reading that saved and
asked for the suspend; every other way out unloads it.

  python3 -I tests/e1a_launcher_test.py
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.realpath(os.path.join(HERE, "..", "e1-hook", "run-e1a"))
RELEASE = os.uname().release

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("".join("     " + l for l in detail.splitlines(True)))


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def setup(tmp, reading="ok", safe=True, ac="0", loaded=False):
    calls = os.path.join(tmp, "calls")
    d = os.path.join(tmp, "e1-hook")
    os.makedirs(d)
    shutil.copy(SCRIPT, os.path.join(d, "run-e1a"))
    write(os.path.join(d, "e1_hook.ko"), "not a module\n")
    if safe:
        write(os.path.join(tmp, "run", "active"), "")
    write(os.path.join(tmp, "ac", "online"), ac + "\n")
    if loaded:
        os.makedirs(os.path.join(tmp, "module"))
    stubs = os.path.join(tmp, "stubs")
    write(os.path.join(stubs, "insmod"), f'#!/bin/sh\necho "insmod $*" >> {calls}\n', 0o755)
    write(os.path.join(stubs, "rmmod"), f'#!/bin/sh\necho "rmmod $*" >> {calls}\n', 0o755)
    write(os.path.join(stubs, "modinfo"), f'#!/bin/sh\necho "{RELEASE} SMP preempt mod_unload aarch64"\n', 0o755)
    body = {
        "ok": "exit 0",
        "fail": "exit 1",
        # Like the real reading: a long child, then the request for the suspend
        # as its last step. The child's output doesn't hold the test's pipes.
        "wait": f'echo "$PPID $$" > {tmp}/pids; sleep 30 > /dev/null 2>&1; echo "suspend" >> {calls}',
    }[reading]
    write(os.path.join(stubs, "reading"), f'#!/bin/bash\necho "reading $*" >> {calls}\n{body}\n', 0o755)
    return d


def env(tmp):
    return {
        "PATH": os.path.join(tmp, "stubs") + ":/usr/bin:/bin",
        "E1A_SAFE_FLAG": os.path.join(tmp, "run", "active"),
        "E1A_AC": os.path.join(tmp, "ac"),
        "E1A_MODDIR": os.path.join(tmp, "module"),
        "E1A_READING": os.path.join(tmp, "stubs", "reading"),
    }


def cmd(d):
    return ["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", os.path.join(d, "run-e1a")]


def calls(tmp):
    try:
        with open(os.path.join(tmp, "calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def run(tmp, d, **kw):
    return subprocess.run(cmd(d), env=env(tmp), capture_output=True, text=True, timeout=60, **kw)


def test_handoff():
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, reading="ok")
        r = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 0 and c == ["insmod " + os.path.join(d, "e1_hook.ko") + " replay=5",
                                          "reading take --label e1a --then suspend"],
              "a reading that hands off to the suspend leaves the replay armed", r.stdout + r.stderr + "\n".join(c))


def test_failed_reading():
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, reading="fail")
        r = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 1 and c[-1:] == ["rmmod e1_hook"] and "unloaded" in r.stderr,
              "a failed reading or save unloads the module, exit 1", r.stdout + r.stderr + "\n".join(c))


def test_signals():
    for sig, code in ((signal.SIGHUP, 129), (signal.SIGTERM, 143), (signal.SIGINT, 130)):
        # A hangup or ^C reaches the whole job, reading and launcher; a kill
        # may reach the launcher only. Either way: no suspend, then the unload.
        for both in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                d = setup(tmp, reading="wait")
                p = subprocess.Popen(cmd(d), env=env(tmp), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pids = os.path.join(tmp, "pids")
                for _ in range(200):
                    if os.path.exists(pids) and os.path.getsize(pids):
                        break
                    time.sleep(0.05)
                launcher, reading = (int(x) for x in open(pids).read().split())
                if both:
                    os.kill(reading, sig)
                os.kill(launcher, sig)
                t0 = time.monotonic()
                out, err = p.communicate(timeout=30)
                c = calls(tmp)
                who = "the reading and the launcher" if both else "the launcher only"
                check(p.returncode == code and c[-1:] == ["rmmod e1_hook"] and "suspend" not in c
                      and time.monotonic() - t0 < 10,
                      f"{signal.Signals(sig).name} to {who}: no suspend, the module unloaded, exit {code}",
                      f"exit {p.returncode}\n" + out + err + "\n".join(c))


def test_broken_stdout():
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, reading="ok")
        rfd, wfd = os.pipe()
        os.close(rfd)
        try:
            r = subprocess.run(cmd(d), env=env(tmp), stdout=wfd, stderr=subprocess.PIPE, text=True, timeout=60)
        finally:
            os.close(wfd)
        c = calls(tmp)
        check(r.returncode != 0 and c[-1:] == ["rmmod e1_hook"] and not any(x.startswith("reading") for x in c),
              "a broken stdout after insmod unloads the module before any reading", f"exit {r.returncode}\n" + r.stderr + "\n".join(c))


def test_refusals():
    for what, kw, msg in (("outside a safe boot", dict(safe=False), "safe boot only"),
                          ("on the charger", dict(ac="1"), "charger"),
                          ("with e1_hook loaded already", dict(loaded=True), "loaded already")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, **kw)
            r = run(tmp, d)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [],
                  f"refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0
    test_handoff()
    test_failed_reading()
    test_signals()
    test_broken_stdout()
    test_refusals()
    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
