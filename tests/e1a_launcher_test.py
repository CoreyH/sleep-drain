#!/usr/bin/env python3
"""e1-hook/run-e1a's and run-floor's arming and disarming, without root or a module.

Each case runs a copy of a launcher as uid 0 in a bubblewrap user namespace,
next to a fake e1_hook.ko, with its paths (E1A_* for run-e1a, FLOOR_* for
run-floor) pointed into a temp directory and stub insmod, rmmod, modinfo and
sleep-drain-reading that record their calls. insmod fills in the module's
parameters in the fake /sys/module. The module must stay loaded only after a
reading that saved and asked for the suspend; every other way out unloads it.
Both launchers get the same cases, but for the hand-off. run-floor's own: it
sets kernel.panic and panic_on_oops (in a fake /proc/sys) before the load,
refuses with pm_test set, an older build or an unknown argument, and unloads
a build that ignored its parameters; --quiet adds quiet=1 and its own label. And after the hand-off it stays and watches a fake
/sys/power/suspend_stats: a floor leaves it waiting with the replay armed; a
suspend that fails (before any PM notifier, too) or comes back, no suspend at
all, or a signal, calls the replay off (cancel) without an unload, and
all but the signal light the keyboard.

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
# with, its reading's label, and its own arguments.
LAUNCHERS = (
    ("run-e1a", "E1A", "replay=5", "e1a", ()),
    ("run-floor", "FLOOR", "replay=15 oneway=1", "floor", ()),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1", "floor-quiet", ("--quiet",)),
)

failed = 0


def name(launcher):
    return " ".join((launcher[0],) + launcher[4])


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
# modinfo -F parm, as for the floor's build; the floor's first build had no
# cancel, the next no quiet, and E1a's no oneway either.
PARMS = {
    "floor": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
             "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-noquiet": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                     "cancel:Write 1 to call the replay off (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-notifier": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                      "replay:LAB ONLY (uint)\n",
    "e1a": "replay_done: (uint)\nreplay:LAB ONLY, safe boot only (uint)\n",
}


def sysctl(tmp):
    return tuple(open(os.path.join(tmp, "sysctl", k)).read().strip() for k in ("panic", "panic_on_oops"))


def setup(tmp, launcher, reading="ok", safe=True, ac="0", loaded=False, pm_test=PM_TEST, build="floor",
          takes=True, takes_quiet=True):
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
    for k, v in (("success", "0"), ("fail", "0"), ("last_failed_errno", "0"), ("last_failed_step", "")):
        write(os.path.join(tmp, "stats", k), v + "\n")
    os.makedirs(os.path.join(tmp, "log"))
    write(os.path.join(tmp, "kbd", "max_brightness"), "255\n")
    write(os.path.join(tmp, "kbd", "brightness"), "0\n")
    if loaded:
        os.makedirs(moddir)
    stubs = os.path.join(tmp, "stubs")
    # The module's directory and its parameters, as the kernel shows them; a
    # build that doesn't know replay and oneway (or quiet) ignores them.
    quiet = f' quiet=1) echo Y > {moddir}/parameters/quiet ;;' if takes_quiet else ""
    params = (f'for a; do case $a in replay=*) echo "${{a#replay=}}" > {moddir}/parameters/replay ;;'
              f' oneway=1) echo Y > {moddir}/parameters/oneway ;;{quiet} esac; done\n') if takes else ""
    # The rest of the floor's parameters, as before any suspend; cancel is
    # write-only there, an empty file here.
    params += (f'cd {moddir}/parameters && for kv in attempts=0 prepare_late_calls=0 replay_done=0'
               f' replay_ret=1,1,1,1 eh_writes=0 post_writes=0 late_ret=1; do echo "${{kv#*=}}" > "${{kv%%=*}}";'
               f' done && : > cancel\n')
    write(os.path.join(stubs, "insmod"),
          f'#!/bin/sh\necho "insmod $*" >> {calls}\nmkdir -p {moddir}/parameters\n{params}', 0o755)
    write(os.path.join(stubs, "rmmod"), f'#!/bin/sh\necho "rmmod $*" >> {calls}\n', 0o755)
    write(os.path.join(stubs, "save"), f'#!/bin/sh\necho "sleep-drain-safe $*" >> {calls}\n', 0o755)
    write(os.path.join(stubs, "dmesg"), '#!/bin/sh\necho "[   12.345678] e1_hook: replay: a stub line"\n', 0o755)
    write(os.path.join(stubs, "modinfo"),
          f'#!/bin/sh\ncase "$2" in\nvermagic) echo "{RELEASE} SMP preempt mod_unload aarch64" ;;\n'
          f'parm) printf "%s" "{PARMS[build]}" ;;\nesac\n', 0o755)
    mp = f"{moddir}/parameters"
    # What the kernel does with the suspend the reading asked for, a second
    # after it exits: in the background, so that the launcher is watching.
    later = {
        # It fails before PM_SUSPEND_PREPARE (a sync cut by a wakeup): only the
        # probe saw it begin, and only suspend_stats' fail moves.
        "abort": f"echo 1 > {mp}/attempts; echo -16 > {tmp}/stats/last_failed_errno; echo 1 > {tmp}/stats/fail",
        # The replay runs, stops short at MBSE=slpw, and fails the suspend.
        "eio": (f"echo 1 > {mp}/attempts; echo 1 > {mp}/prepare_late_calls; echo 1 > {mp}/replay_done;"
                f" echo -110,1,1,1 > {mp}/replay_ret; echo 31 > {mp}/eh_writes; echo -5 > {tmp}/stats/last_failed_errno;"
                f" echo 1 > {tmp}/stats/fail"),
    }
    body = {
        "ok": "exit 0",
        "fail": "exit 1",
        # Like the real reading: a long child, then the request for the suspend
        # as its last step. The child's output doesn't hold the test's pipes.
        "wait": f'echo "$PPID $$" > {tmp}/pids; sleep 30 > /dev/null 2>&1; echo "suspend" >> {calls}',
        # The suspend asked for: then the launcher watches. "handoff" leaves
        # suspend_stats alone (the test stops the launcher, or it times out).
        "handoff": f'echo "$PPID $$" > {tmp}/pids; exit 0',
        "abort": f'( sleep 1; {later["abort"]} ) > /dev/null 2>&1 & exit 0',
        "eio": f'( sleep 1; {later["eio"]} ) > /dev/null 2>&1 & exit 0',
    }[reading]
    write(os.path.join(stubs, "reading"), f'#!/bin/bash\necho "reading $*" >> {calls}\n{body}\n', 0o755)
    return d


def env(tmp, launcher, watch_s=3):
    prefix = launcher[1]
    e = {
        "PATH": os.path.join(tmp, "stubs") + ":/usr/bin:/bin",
        prefix + "_SAFE_FLAG": os.path.join(tmp, "run", "active"),
        prefix + "_AC": os.path.join(tmp, "ac"),
        prefix + "_MODDIR": os.path.join(tmp, "module"),
        prefix + "_READING": os.path.join(tmp, "stubs", "reading"),
        prefix + "_PM_TEST": os.path.join(tmp, "pm_test"),
        prefix + "_SYSCTL": os.path.join(tmp, "sysctl"),
    }
    if prefix == "FLOOR":
        e.update(FLOOR_SAVE=os.path.join(tmp, "stubs", "save"), FLOOR_STATS=os.path.join(tmp, "stats"),
                 FLOOR_LOGDIR=os.path.join(tmp, "log"), FLOOR_RUNDIR=tmp, FLOOR_WATCH_S=str(watch_s),
                 FLOOR_KBD=os.path.join(tmp, "kbd"))
    return e


def cmd(d, launcher, argv=None):
    return (["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", os.path.join(d, launcher[0])]
            + list(launcher[4] if argv is None else argv))


def calls(tmp):
    try:
        with open(os.path.join(tmp, "calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def run(tmp, d, launcher, watch_s=3, argv=None, **kw):
    return subprocess.run(cmd(d, launcher, argv), env=env(tmp, launcher, watch_s), capture_output=True, text=True,
                          timeout=60, **kw)


def cancelled(tmp):
    try:
        with open(os.path.join(tmp, "module", "parameters", "cancel")) as f:
            return f.read()
    except FileNotFoundError:
        return None


def kbd(tmp):
    return open(os.path.join(tmp, "kbd", "brightness")).read().strip()


def wait_for(path):
    for _ in range(200):
        if os.path.exists(path) and os.path.getsize(path):
            return True
        time.sleep(0.05)
    return False


def test_handoff(launcher):
    script, _, args, label, _ = launcher
    script = name(launcher)
    # run-floor makes a fault restart the Mac; run-e1a leaves both alone.
    panic = ("10", "1") if launcher[0] == "run-floor" else ("0", "0")
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


def test_floor_watch(launcher):
    script, _, args, label, _ = launcher
    script = name(launcher)

    def insmod_reading(d):
        return ["insmod " + os.path.join(d, "e1_hook.ko") + " " + args, f"reading take --label {label} --then suspend"]

    # The floor: the suspend began and nothing runs again. Killing the
    # launcher mid-watch stands in for that; the replay must still be armed.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        alive = p.poll() is None
        try:
            os.kill(launcher_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        c = calls(tmp)
        check(alive and c == insmod_reading(d) and cancelled(tmp) == "" and sysctl(tmp) == ("10", "1")
              and kbd(tmp) == "0",
              f"{script}: after the hand-off it waits for the suspend with the replay armed: no unload, no cancel,"
              " the keyboard dark; kernel.panic=10 panic_on_oops=1",
              f"alive {alive}\n" + out + err + "\n".join(c) + f"\ncancel {cancelled(tmp)!r} sysctl {sysctl(tmp)}")

    for reading, what, want in (
            ("abort", "a suspend that fails before any PM notifier (a sync cut by a wakeup)",
             ("the suspend failed or came back", "attempts=1", "errno -16")),
            ("eio", "a replay that stops short and fails its suspend",
             ("the suspend failed or came back", "replay_done=1", "replay_ret=-110,1,1,1", "eh_writes=31")),
            ("handoff", "no suspend within the watch",
             ("no suspend began within 3 s",))):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, reading=reading)
            t0 = time.monotonic()
            r = run(tmp, d, launcher)
            took = time.monotonic() - t0
            c = calls(tmp)
            logs = os.listdir(os.path.join(tmp, "log"))
            logged = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
            check(r.returncode == 2 and cancelled(tmp) == "1\n" and c[:2] == insmod_reading(d)
                  and not any(x.startswith("rmmod") for x in c)
                  and c[2:] == ["sleep-drain-safe save " + os.path.join(tmp, "log", logs[0])]
                  and "NO FLOOR" in r.stdout and all(w in r.stdout for w in want)
                  and "e1_hook: replay: a stub line" in r.stdout and "NO FLOOR" in logged
                  and (reading != "handoff" or took >= 3) and kbd(tmp) == "255",
                  f"{script}: {what}: the replay is called off, not unloaded; the keyboard lit; NO FLOOR printed,"
                  " logged and saved; exit 2",
                  f"exit {r.returncode}, {took:.1f} s\n" + r.stdout + r.stderr + "\n".join(c)
                  + f"\ncancel {cancelled(tmp)!r}")

    # A signal while it watches: the replay called off, the module left loaded.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        try:
            os.kill(launcher_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        c = calls(tmp)
        check(p.returncode == 143 and cancelled(tmp) == "1\n" and c == insmod_reading(d),
              f"{script}: SIGTERM while it watches: the replay called off, no unload, exit 143",
              f"exit {p.returncode}\n" + out + err + "\n".join(c) + f"\ncancel {cancelled(tmp)!r}")


def test_failed_reading(launcher):
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="fail")
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 1 and c[-1:] == ["rmmod e1_hook"] and "unloaded" in r.stderr,
              f"{name(launcher)}: a failed reading or save unloads the module, exit 1", r.stdout + r.stderr + "\n".join(c))


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
                      f"{name(launcher)}: {signal.Signals(sig).name} to {who}: no suspend, the module unloaded, exit {code}",
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
              f"{name(launcher)}: a broken stdout after insmod unloads the module before any reading",
              f"exit {r.returncode}\n" + r.stderr + "\n".join(c))


def test_refusals(launcher):
    for what, kw, msg in (("outside a safe boot", dict(safe=False), "safe boot only"),
                          ("on the charger", dict(ac="1"), "charger"),
                          ("with e1_hook loaded already", dict(loaded=True), "loaded already")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and sysctl(tmp) == ("0", "0"),
                  f"{name(launcher)}: refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))


def test_floor_refusals(launcher):
    quiet = "--quiet" in launcher[4]
    cases = [("with pm_test set", dict(pm_test="none core processors [platform] devices freezer\n"), None,
              "pm_test isn't none"),
             ("an older build, without oneway (E1a's)", dict(build="e1a"), None, "older build"),
             ("the floor's first build, without cancel", dict(build="floor-notifier"), None, "older build")]
    if quiet:
        cases.append(("a build without quiet", dict(build="floor-noquiet"), None, "without quiet"))
    else:
        # The floor's build before quiet still runs the floor itself.
        cases += [("an unknown argument", {}, ["--loud"], "usage: run-floor [--quiet]"),
                  ("a second argument", {}, ["--quiet", "--quiet"], "usage: run-floor [--quiet]")]
    for what, kw, argv, msg in cases:
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher, argv=argv)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and sysctl(tmp) == ("0", "0"),
                  f"{name(launcher)}: refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))
    # A build that loads but doesn't know the parameters, which the kernel
    # only warns about: unloaded at once, before any reading.
    ignored = [("replay and oneway", dict(takes=False))]
    if quiet:
        ignored.append(("quiet", dict(takes_quiet=False)))
    for what, kw in ignored:
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher)
            c = calls(tmp)
            check(r.returncode == 1 and "didn't take" in r.stderr and c[-1:] == ["rmmod e1_hook"]
                  and not any(x.startswith("reading") for x in c),
                  f"{name(launcher)}: a build that ignored {what} is unloaded before any reading",
                  r.stdout + r.stderr + "\n".join(c))
    if not quiet:
        # The floor itself loads the build from before quiet. No suspend
        # follows here, so it ends in the watch's NO FLOOR.
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, reading="ok", build="floor-noquiet")
            r = run(tmp, d, launcher)
            c = calls(tmp)
            check(r.returncode == 2 and c[:2] == ["insmod " + os.path.join(d, "e1_hook.ko") + " replay=15 oneway=1",
                                                  "reading take --label floor --then suspend"]
                  and "no suspend began" in r.stdout,
                  f"{name(launcher)}: without --quiet, the build from before quiet loads as before",
                  r.stdout + r.stderr + "\n".join(c))


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0
    for launcher in LAUNCHERS:
        if launcher[0] == "run-floor":
            test_floor_watch(launcher)
        else:
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
