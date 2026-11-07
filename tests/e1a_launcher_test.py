#!/usr/bin/env python3
"""e1-hook/run-e1a's and run-floor's arming and disarming, without root or a module.

Each case runs a copy of a launcher as uid 0 in a bubblewrap user namespace,
next to a fake e1_hook.ko, with its paths (E1A_* for run-e1a, FLOOR_* for
run-floor) pointed into a temp directory and stub insmod, rmmod, modinfo and
sleep-drain-reading that record their calls. insmod fills in the module's
parameters in the fake /sys/module. The module must stay loaded only after a
reading that saved and asked for the suspend; every other way out unloads it.
Both launchers get the same cases. run-floor's own: it sets kernel.panic and
panic_on_oops (in a fake /proc/sys) before the load, refuses with pm_test set
or an older build, and unloads a build that ignored its parameters.

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
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))
RELEASE = os.uname().release

# Each launcher: its script, its environment's prefix, what it loads e1_hook
# with, and its reading's label.
LAUNCHERS = (
    ("run-e1a", "E1A", "replay=5", "e1a"),
    ("run-floor", "FLOOR", "replay=15 oneway=1", "floor"),
)

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


PM_TEST = "[none] core processors platform devices freezer\n"
# modinfo -F parm, as for the floor's build; E1a's has no oneway.
PARMS = {
    "floor": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\nreplay:LAB ONLY (uint)\n",
    "e1a": "replay_done: (uint)\nreplay:LAB ONLY, safe boot only (uint)\n",
}


def sysctl(tmp):
    return tuple(open(os.path.join(tmp, "sysctl", k)).read().strip() for k in ("panic", "panic_on_oops"))


def setup(tmp, launcher, reading="ok", safe=True, ac="0", loaded=False, pm_test=PM_TEST, build="floor",
          takes=True):
    script = launcher[0]
    calls = os.path.join(tmp, "calls")
    moddir = os.path.join(tmp, "module")
    d = os.path.join(tmp, "e1-hook")
    os.makedirs(d)
    shutil.copy(os.path.join(E1_HOOK, script), os.path.join(d, script))
    write(os.path.join(d, "e1_hook.ko"), "not a module\n")
    if safe:
        write(os.path.join(tmp, "run", "active"), "")
    write(os.path.join(tmp, "ac", "online"), ac + "\n")
    write(os.path.join(tmp, "pm_test"), pm_test)
    write(os.path.join(tmp, "sysctl", "panic"), "0\n")
    write(os.path.join(tmp, "sysctl", "panic_on_oops"), "0\n")
    if loaded:
        os.makedirs(moddir)
    stubs = os.path.join(tmp, "stubs")
    # The module's directory and its parameters, as the kernel shows them; a
    # build that doesn't know replay and oneway ignores them.
    params = (f'for a; do case $a in replay=*) echo "${{a#replay=}}" > {moddir}/parameters/replay ;;'
              f' oneway=1) echo Y > {moddir}/parameters/oneway ;; esac; done\n') if takes else ""
    write(os.path.join(stubs, "insmod"),
          f'#!/bin/sh\necho "insmod $*" >> {calls}\nmkdir -p {moddir}/parameters\n{params}', 0o755)
    write(os.path.join(stubs, "rmmod"), f'#!/bin/sh\necho "rmmod $*" >> {calls}\n', 0o755)
    write(os.path.join(stubs, "modinfo"),
          f'#!/bin/sh\ncase "$2" in\nvermagic) echo "{RELEASE} SMP preempt mod_unload aarch64" ;;\n'
          f'parm) printf "%s" "{PARMS[build]}" ;;\nesac\n', 0o755)
    body = {
        "ok": "exit 0",
        "fail": "exit 1",
        # Like the real reading: a long child, then the request for the suspend
        # as its last step. The child's output doesn't hold the test's pipes.
        "wait": f'echo "$PPID $$" > {tmp}/pids; sleep 30 > /dev/null 2>&1; echo "suspend" >> {calls}',
    }[reading]
    write(os.path.join(stubs, "reading"), f'#!/bin/bash\necho "reading $*" >> {calls}\n{body}\n', 0o755)
    return d


def env(tmp, launcher):
    prefix = launcher[1]
    return {
        "PATH": os.path.join(tmp, "stubs") + ":/usr/bin:/bin",
        prefix + "_SAFE_FLAG": os.path.join(tmp, "run", "active"),
        prefix + "_AC": os.path.join(tmp, "ac"),
        prefix + "_MODDIR": os.path.join(tmp, "module"),
        prefix + "_READING": os.path.join(tmp, "stubs", "reading"),
        prefix + "_PM_TEST": os.path.join(tmp, "pm_test"),
        prefix + "_SYSCTL": os.path.join(tmp, "sysctl"),
    }


def cmd(d, launcher):
    return ["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", os.path.join(d, launcher[0])]


def calls(tmp):
    try:
        with open(os.path.join(tmp, "calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def run(tmp, d, launcher, **kw):
    return subprocess.run(cmd(d, launcher), env=env(tmp, launcher), capture_output=True, text=True, timeout=60, **kw)


def test_handoff(launcher):
    script, _, args, label = launcher
    # run-floor makes a fault restart the Mac; run-e1a leaves both alone.
    panic = ("10", "1") if script == "run-floor" else ("0", "0")
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="ok")
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 0 and c == ["insmod " + os.path.join(d, "e1_hook.ko") + " " + args,
                                          f"reading take --label {label} --then suspend"]
              and sysctl(tmp) == panic,
              f"{script}: a reading that hands off to the suspend leaves the replay armed ({args});"
              f" kernel.panic={panic[0]} panic_on_oops={panic[1]}",
              r.stdout + r.stderr + "\n".join(c) + f"\nsysctl {sysctl(tmp)}")


def test_failed_reading(launcher):
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="fail")
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 1 and c[-1:] == ["rmmod e1_hook"] and "unloaded" in r.stderr,
              f"{launcher[0]}: a failed reading or save unloads the module, exit 1", r.stdout + r.stderr + "\n".join(c))


def test_signals(launcher):
    for sig, code in ((signal.SIGHUP, 129), (signal.SIGTERM, 143), (signal.SIGINT, 130)):
        # A hangup or ^C reaches the whole job, reading and launcher; a kill
        # may reach the launcher only. Either way: no suspend, then the unload.
        for both in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                d = setup(tmp, launcher, reading="wait")
                p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
                pids = os.path.join(tmp, "pids")
                for _ in range(200):
                    if os.path.exists(pids) and os.path.getsize(pids):
                        break
                    time.sleep(0.05)
                launcher_pid, reading = (int(x) for x in open(pids).read().split())
                if both:
                    os.kill(reading, sig)
                os.kill(launcher_pid, sig)
                t0 = time.monotonic()
                out, err = p.communicate(timeout=30)
                c = calls(tmp)
                who = "the reading and the launcher" if both else "the launcher only"
                check(p.returncode == code and c[-1:] == ["rmmod e1_hook"] and "suspend" not in c
                      and time.monotonic() - t0 < 10,
                      f"{launcher[0]}: {signal.Signals(sig).name} to {who}: no suspend, the module unloaded, exit {code}",
                      f"exit {p.returncode}\n" + out + err + "\n".join(c))


def test_broken_stdout(launcher):
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="ok")
        rfd, wfd = os.pipe()
        os.close(rfd)
        try:
            r = subprocess.run(cmd(d, launcher), env=env(tmp, launcher), stdout=wfd, stderr=subprocess.PIPE,
                               text=True, timeout=60)
        finally:
            os.close(wfd)
        c = calls(tmp)
        check(r.returncode != 0 and c[-1:] == ["rmmod e1_hook"] and not any(x.startswith("reading") for x in c),
              f"{launcher[0]}: a broken stdout after insmod unloads the module before any reading",
              f"exit {r.returncode}\n" + r.stderr + "\n".join(c))


def test_refusals(launcher):
    for what, kw, msg in (("outside a safe boot", dict(safe=False), "safe boot only"),
                          ("on the charger", dict(ac="1"), "charger"),
                          ("with e1_hook loaded already", dict(loaded=True), "loaded already")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and sysctl(tmp) == ("0", "0"),
                  f"{launcher[0]}: refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))


def test_floor_refusals(launcher):
    for what, kw, msg in (("with pm_test set", dict(pm_test="none core processors [platform] devices freezer\n"),
                           "pm_test isn't none"),
                          ("an older build, without oneway (E1a's)", dict(build="e1a"), "older build")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and sysctl(tmp) == ("0", "0"),
                  f"{launcher[0]}: refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))
    # A build that loads but doesn't know the parameters, which the kernel
    # only warns about: unloaded at once, before any reading.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, takes=False)
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 1 and "didn't take" in r.stderr and c[-1:] == ["rmmod e1_hook"]
              and not any(x.startswith("reading") for x in c),
              f"{launcher[0]}: a build that ignored replay and oneway is unloaded before any reading",
              r.stdout + r.stderr + "\n".join(c))


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0
    for launcher in LAUNCHERS:
        test_handoff(launcher)
        test_failed_reading(launcher)
        test_signals(launcher)
        test_broken_stdout(launcher)
        test_refusals(launcher)
        if launcher[0] == "run-floor":
            test_floor_refusals(launcher)
    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
