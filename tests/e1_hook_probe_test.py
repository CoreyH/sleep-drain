#!/usr/bin/python3 -I
"""tests/e1_hook_probe_test.py: e1-hook/probe, the launcher of the probe that isn't a run, as uid 0 in a bubblewrap
user namespace against stubs: insmod, rmmod, modinfo, dmesg, m1-crumbs and sleep-drain-safe, with files standing in for
/sys/power and /sys/module.

The one thing it must never do is ask for a suspend with a module that hasn't said it's in probe mode: the parameters
it loads are a one-way run's, and probe=1 is all that keeps the replay from running. So every case checks what was, or
wasn't, written to the suspend file.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))
RELEASE = os.uname().release
# run-floor --m1-mark's parameters (tests/e1a_launcher_test.py has them for the launcher itself), and probe=1.
PARAMS = ("replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1"
          " smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1 cluster_ack=1 fabric_up=1 wd1_mark=1 probe=1")
SOURCES = ("e1_hook.c", "asc_probe.h", "fabric_up.h", "wd1_mark.h", "wd2_bite.h", "recfg_bite.h", "m1_position.h")
KLOG = ("[  100.1] e1_hook: m1: probe: no replay with this load; awake: SIO_CPU 1400024f ANS2 0f0000ff ps_pmp 1f0f03ff\n"
        "[  100.2] e1_hook: probe: SIO, awake, at load: power word 1400024f; ASC+0x8000 read 00000002 at first and 00000002 0 ms on: bit 1 SET\n"
        "[  130.1] e1_hook: probe: SIO, at prepare, its firmware running: power word 1400024f; ASC+0x8000 read 00000000 at first and 00000000 100 ms on: bit 1 clear\n"
        "[  131.4] e1_hook: probe: SUMMARY: bit 1 in the state B2 finds: SIO clear, ANS SET, PMP SET; bytes c0 ff 8d, written to the PMU: 0\n"
        "[  131.5] e1_hook: probe: done; this suspend fails with -EIO: with probe=1 loaded nothing sleeps and nothing replays\n"
        "[  131.6] some other driver: unrelated\n")

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        for line in detail.splitlines()[-40:]:
            print("     " + line)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def setup(tmp, safe=True, loaded=False, ko=True, vermagic=RELEASE, newer=None, parms=True, takes="Y", insmod_rc=0, pm_test="[none] freezer",
          mem_sleep="[s2idle]", crumbs="stage 0x6004 = 0x00: none: no M1 run since the last clear", save_rc=0, klog=KLOG, pc=1, plc=1,
          pc_after_wait=None, rmmod_rc=0):
    """pc and plc are the module's prepare_calls and prepare_late_calls once loaded: the stub suspend changes nothing, so they stand for
    how far the first suspend got. pc_after_wait is prepare_calls after the launcher's first wait between tries."""
    d = os.path.join(tmp, "e1-hook")
    stubs = os.path.join(tmp, "stubs")
    moddir = os.path.join(tmp, "module")
    calls = os.path.join(tmp, "calls")
    os.makedirs(d)
    shutil.copy(os.path.join(E1_HOOK, "probe"), os.path.join(d, "probe"))
    for n in SOURCES:
        write(os.path.join(d, n), "")
        os.utime(os.path.join(d, n), (3000, 3000) if newer == n else (1000, 1000))
    if ko:
        write(os.path.join(d, "e1_hook.ko"), "not a module\n")
        os.utime(os.path.join(d, "e1_hook.ko"), (2000, 2000))
    if safe:
        write(os.path.join(tmp, "run", "active"), "")
    if loaded:
        os.makedirs(os.path.join(moddir, "parameters"))
    write(os.path.join(tmp, "pm_test"), pm_test + "\n")
    write(os.path.join(tmp, "mem_sleep"), mem_sleep + "\n")
    plist = "replay:LAB ONLY (uint)\nwd1_mark:With fabric_up=1 (bool)\n" + ("probe:With wd1_mark=1: no replay, ever (bool)\n" if parms else "")
    write(os.path.join(stubs, "modinfo"),
          f'#!/bin/sh\ncase "$1" in -F) echo "{vermagic} SMP preempt mod_unload aarch64" ;; -p) printf "%s" "{plist}" ;; esac\n', 0o755)
    made = (f'mkdir -p {moddir}/parameters && echo 0 > {moddir}/parameters/replay_done && echo {pc} > {moddir}/parameters/prepare_calls'
            f' && echo {plc} > {moddir}/parameters/prepare_late_calls')
    if takes is not None:
        made += f' && echo {takes} > {moddir}/parameters/probe'
    write(os.path.join(stubs, "insmod"), f'#!/bin/sh\necho "insmod $*" >> {calls}\n[ {insmod_rc} = 0 ] || exit {insmod_rc}\n{made}\n', 0o755)
    write(os.path.join(stubs, "rmmod"), f'#!/bin/sh\necho "rmmod $*" >> {calls}\n[ {rmmod_rc} = 0 ] || exit {rmmod_rc}\nrm -rf {moddir}\n', 0o755)
    bump = f'[ "$1" = 0 ] || echo {pc_after_wait} > {moddir}/parameters/prepare_calls\n' if pc_after_wait is not None else ""
    write(os.path.join(stubs, "sleep"), f'#!/bin/sh\necho "sleep $*" >> {calls}\n{bump}', 0o755)
    write(os.path.join(stubs, "dmesg"), f'#!/bin/sh\ncat {tmp}/klog\n', 0o755)
    write(os.path.join(tmp, "klog"), klog)
    write(os.path.join(stubs, "m1-crumbs"), f'#!/bin/sh\necho "0x6000: 00 30 00 50"\necho "{crumbs}"\n', 0o755)
    write(os.path.join(stubs, "save"), f'#!/bin/sh\necho "sleep-drain-safe $*" >> {calls}\nexit {save_rc}\n', 0o755)
    return d


def run(tmp, d, argv=()):
    env = dict(os.environ, PATH=os.path.join(tmp, "stubs") + ":" + os.environ["PATH"],
               PROBE_SAFE_FLAG=os.path.join(tmp, "run", "active"), PROBE_MODDIR=os.path.join(tmp, "module"),
               PROBE_STATE=os.path.join(tmp, "state"), PROBE_PM_TEST=os.path.join(tmp, "pm_test"),
               PROBE_MEM_SLEEP=os.path.join(tmp, "mem_sleep"), PROBE_LOGDIR=os.path.join(tmp, "log"),
               PROBE_SAVE=os.path.join(tmp, "stubs", "save"), PROBE_CRUMBS=os.path.join(tmp, "stubs", "m1-crumbs"))
    return subprocess.run(["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", os.path.join(d, "probe")] + list(argv),
                          env=env, capture_output=True, text=True, timeout=60)


def calls(tmp):
    try:
        return open(os.path.join(tmp, "calls")).read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def state(tmp):
    """What was written to the suspend file: None if nothing ever was."""
    try:
        return open(os.path.join(tmp, "state")).read()
    except FileNotFoundError:
        return None


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0

    # The probe as it's meant to go: the load with the recipe's parameters and probe=1, one suspend asked for, the module's lines
    # printed for a photo and saved.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        r = run(tmp, d)
        c = calls(tmp)
        logs = os.listdir(os.path.join(tmp, "log")) if os.path.isdir(os.path.join(tmp, "log")) else []
        saved = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
        check(r.returncode == 0 and c[:2] == ["sleep 0", "insmod " + os.path.join(d, "e1_hook.ko") + " " + PARAMS] and state(tmp) == "mem"
              and len(c) == 3 and c[2] == "sleep-drain-safe save " + os.path.join(tmp, "log", logs[0] if logs else "") and "rmmod" not in "".join(c)
              and r.stdout.count("asking for the suspend") == 1 and "(1 of 3 at most)" in r.stdout,
              "the load is --m1-mark's parameters with probe=1, one suspend is asked for, and the record is saved; the module stays loaded",
              r.stdout + r.stderr + "\n".join(c))
        check("==================== TAKE A PHOTO OF THIS SCREEN ====================" in r.stdout
              and "replay_done=0 (0 is right: nothing replayed) prepare_calls=1 prepare_late_calls=1" in r.stdout
              and "e1_hook: probe: SUMMARY: bit 1 in the state B2 finds: SIO clear, ANS SET, PMP SET; bytes c0 ff 8d" in r.stdout
              and r.stdout.count("e1_hook: probe: SIO, awake, at load:") == 1 and "some other driver" not in r.stdout
              and "Saved. Now restart the Mac" in r.stdout and "NOT SAVED" not in r.stdout and "ONLY PART" not in r.stdout
              and r.stdout.index("asking for the suspend") < r.stdout.index("TAKE A PHOTO") < r.stdout.index("SUMMARY") < r.stdout.index("Saved."),
              "it prints the module's probe lines, the load's among them, once each, between the photo's two rules, and says it's saved",
              r.stdout + r.stderr)
        check(saved and all(line in saved for line in r.stdout.splitlines() if "SUMMARY" in line or "TAKE A PHOTO" in line or "loading e1_hook" in line),
              "the saved file has what the screen had", saved)

    # The one that matters: a module that doesn't say probe=Y is a one-way run waiting for its suspend. Unloaded, and no suspend asked for.
    for takes, what in ((None, "has no probe parameter once loaded"), ("N", "reads probe=N once loaded")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, takes=takes)
            r = run(tmp, d)
            c = calls(tmp)
            check(r.returncode == 1 and state(tmp) is None and c[-1:] == ["rmmod e1_hook"] and "didn't take probe=1, and was unloaded" in r.stderr
                  and "TAKE A PHOTO" not in r.stdout and not any(x.startswith("sleep-drain-safe") for x in c),
                  f"a module that {what}: unloaded, and no suspend is ever asked for", r.stdout + r.stderr + "\n".join(c))

    # A load that fails: the kernel's lines shown and saved, no suspend.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, insmod_rc=1)
        r = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 1 and state(tmp) is None and "e1_hook didn't load; nothing was probed" in r.stdout and len(c) == 3
              and c[2].startswith("sleep-drain-safe save "),
              "a module that refuses to load: said, its lines saved, and no suspend", r.stdout + r.stderr + "\n".join(c))

    # Everything it refuses before loading anything.
    refusals = [("outside a safe boot", dict(safe=False), "safe boot only"),
                ("with e1_hook loaded already", dict(loaded=True), "e1_hook is loaded already"),
                ("without a built module", dict(ko=False), "make -C"),
                ("with a module built for another kernel", dict(vermagic="6.1.0-other"), "isn't built for"),
                ("with a build that has no probe parameter", dict(parms=False), "an older build, without probe"),
                ("with pm_test set", dict(pm_test="none [platform]"), "pm_test isn't none"),
                ("when s2idle isn't the sleep mode", dict(mem_sleep="s2idle [deep]"), "s2idle isn't the sleep mode"),
                ("with breadcrumbs left in the PMU", dict(crumbs="stage 0x6004 = 0xd3 (pmp_out=1: bit 7, so the stage is 0x53)"),
                 "the PMU's breadcrumbs aren't clear")]
    refusals += [(f"with a module older than {n}", dict(newer=n), f"e1_hook.ko is older than {n}") for n in SOURCES]
    for what, kw, msg in refusals:
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, **kw)
            r = run(tmp, d)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and state(tmp) is None,
                  f"refused {what}: nothing loaded, no suspend", r.stdout + r.stderr + "\n".join(calls(tmp)))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        r = run(tmp, d, ["--now"])
        check(r.returncode == 1 and "usage: probe (no arguments)" in r.stderr and calls(tmp) == [] and state(tmp) is None,
              "refused with an argument: nothing loaded, no suspend", r.stdout + r.stderr)

    # A suspend that ends before the module's prepare has probed and stopped nothing: asked for again, twice at most, and only
    # while prepare hasn't run. Here the second try gets there.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, pc=0, pc_after_wait=1)
        r = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 0 and r.stdout.count("asking for the suspend") == 2 and c.count("sleep 3") == 1 and "rmmod" not in "".join(c)
              and r.stdout.count("ended before the module's prepare") == 1 and "prepare_calls=1 prepare_late_calls=1" in r.stdout
              and "TAKE A PHOTO" in r.stdout and "ONLY PART" not in r.stdout and "Saved. Now restart the Mac" in r.stdout,
              "a first suspend that ends before the module: one wait, a second suspend, and then the probe's record as usual",
              r.stdout + r.stderr + "\n".join(c))
    # None of three gets there: said, the module unloaded, so that the command can be run again in the same boot.
    for rmmod_rc, said in ((0, "e1_hook is unloaded again. Run the same command once more: no restart is needed."),
                           (1, "e1_hook didn't unload: restart into a fresh safe boot before trying again.")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, pc=0, rmmod_rc=rmmod_rc, klog="[  9.0] PM: Some devices failed to suspend, or early wake event detected\n")
            r = run(tmp, d)
            c = calls(tmp)
            check(r.returncode == 1 and r.stdout.count("asking for the suspend") == 3 and c.count("sleep 3") == 2
                  and "NOTHING WAS PROBED: three suspends ended before the module ran, and nothing was stopped" in r.stdout
                  and "PM: Some devices failed to suspend" in r.stdout and said in r.stdout and "TAKE A PHOTO" not in r.stdout
                  and c[-2:] == ["rmmod e1_hook", c[-1]] and c[-1].startswith("sleep-drain-safe save "),
                  f"three suspends that end before the module (rmmod returns {rmmod_rc}): no fourth, nothing claimed, the module unloaded, the record saved",
                  r.stdout + r.stderr + "\n".join(c))
    # Once prepare has run there is no second suspend, whatever the first did after it: the first half's lines are the record.
    for pc, plc in ((1, 0), (2, 1)):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, pc=pc, plc=plc)
            r = run(tmp, d)
            c = calls(tmp)
            check(r.returncode == 0 and r.stdout.count("asking for the suspend") == 1 and "sleep 3" not in c and "rmmod" not in "".join(c)
                  and "ONLY PART OF THE PROBE RAN (1 and 1 is a whole one)" in r.stdout and f"prepare_calls={pc} prepare_late_calls={plc}" in r.stdout
                  and "TAKE A PHOTO" in r.stdout,
                  f"prepare_calls {pc} and prepare_late_calls {plc}: one suspend only, and it says only part of the probe ran",
                  r.stdout + r.stderr + "\n".join(c))

    # A save that fails, and a kernel log with nothing of the probe's in it: both said.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, save_rc=1)
        r = run(tmp, d)
        check(r.returncode == 0 and state(tmp) == "mem" and "NOT SAVED: the photo is the result" in r.stdout and "Saved. Now" not in r.stdout,
              "a save that fails: the photo is the result, and it says so", r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, klog="[  1.0] nothing of ours\n")
        r = run(tmp, d)
        check(r.returncode == 0 and '(no line with "e1_hook: probe:" in the kernel log, or dmesg failed:' in r.stdout,
              "a kernel log without the probe's lines: said, not left blank", r.stdout + r.stderr)

    # The parameters are the launcher's of the latest recipe, and probe=1 is among them, last.
    src = open(os.path.join(E1_HOOK, "probe")).read()
    floor = open(os.path.join(E1_HOOK, "run-floor")).read()
    check(PARAMS.split()[-1] == "probe=1" and all(f"params+=({p})" in floor or p in ("replay=15", "oneway=1", "one_commit=1", "smc_idle=1")
                                                   or "params=(replay=15 oneway=1)" in floor and p.split("=")[0] in floor
                                                   for p in PARAMS.split()[:-1])
          and 'if [[ $(get "$MODDIR/parameters/probe") != Y ]]; then\n  rmmod e1_hook 2> /dev/null\n  die ' in src
          and src.index('!= Y ]]; then') < src.index('printf mem > "$STATE"') and src.count('> "$STATE"') == 1
          and '  printf mem > "$STATE" 2> /dev/null\n  rc=$?\n  [[ $(get "$MODDIR/parameters/prepare_calls") == 0 ]] || break\n' in src
          and src.index("dmesg > /dev/null; grep -F x /dev/null; sleep 0\n") < src.index('insmod "$ko"'),
          "probe's parameters are run-floor's and probe=1; the suspend is asked for in one place, after the check that the module took probe=1, "
          "and again only while the module's prepare hasn't run; dmesg, grep and sleep are in memory before the load")

    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
