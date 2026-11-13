#!/usr/bin/python3 -I
"""tests/e1_hook_probe_test.py: e1-hook/probe, the launcher of the probe that isn't a run, as uid 0 in a bubblewrap
user namespace against stubs: insmod, rmmod, modinfo, dmesg, sleep, m1-crumbs and sleep-drain-safe, with files standing
in for /sys/power and /sys/module.

The one thing it must never do is ask for a suspend with a module that hasn't said it's in probe mode: the parameters
it loads are a one-way run's, and probe=1 is all that keeps the replay from running. So every case checks what was, or
wasn't, written to the suspend file.

The suspend file and the module's count that the launcher goes by (prepare_calls; prepare_late_calls with --look) are
FIFOs with a thread here at their other ends, standing in for the kernel: what the count reads depends on how many
suspends the launcher has asked for by then, as it does on the Mac, where the write to /sys/power/state returns only
when the suspend is over.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))
RELEASE = os.uname().release
# run-floor --m1-mark's parameters (tests/e1a_launcher_test.py has them for the launcher itself), and probe=1.
PARAMS = ("replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1"
          " smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1 cluster_ack=1 fabric_up=1 wd1_mark=1 probe=1")
SOURCES = ("e1_hook.c", "asc_probe.h", "look.h", "gate_test.h", "pre_test.h", "sio_steps.h", "sio_down.h", "pcie_down.h", "a2_pre.h", "gates_mark.h", "fabric_up.h", "wd1_mark.h", "wd2_bite.h", "recfg_bite.h", "m1_position.h")
KLOG = ("[  100.1] e1_hook: m1: probe: no replay with this load; awake: SIO_CPU 1400024f ANS2 0f0000ff ps_pmp 1f0f03ff\n"
        "[  100.2] e1_hook: probe: ANS, awake, at load, its firmware running: power word 0f0000ff; ASC+0x8000 read 00000002 at first and 00000002 after 1 reads in 0 ms: bit 1 SET\n"
        "[  130.1] e1_hook: probe: SIO, at prepare, its firmware running: power word 0400024f; ASC+0x8000 read 00000000 at first and 00000000 after 250 reads in 100 ms: bit 1 clear\n"
        "[  131.4] e1_hook: probe: SUMMARY: bit 1 in the state B2 finds: SIO clear, ANS SET, PMP SET; bytes f0 ff fd, written to the PMU: 0\n"
        "[  131.5] e1_hook: probe: done; this suspend fails with -EIO: with probe=1 loaded nothing sleeps and nothing replays\n"
        "[  131.6] some other driver: unrelated\n"
        "[  100.3] e1_hook: look: awake, at load: gates 0x28e5a0000: +050 8004030f +064 c0040302 +30c 80040308; 0x28e09c000: +310 8004031d +338 8004030f +34c 8004030e\n"
        "[  131.2] e1_hook: look: after noirq: AON 0x2922c0000: +038 00000000 +03c 00000101 +040 00000003 +044 00000008 +10c 80000000 +110 00000000\n"
        "[  131.3] e1_hook: look: after noirq: done\n"
        "[  131.31] e1_hook: look: gate: as found: UART2,3,4,6,7 00000244 00000244 00000244 00000244 00000244; AFNC2_LW1 1f0000ff\n"
        "[  131.35] e1_hook: look: gate: SUMMARY: SIO's subtree all off; bit 30 of 0x28e5a0050 before AFNC2_LW1's write: clear; AFNC2_LW1 took macOS's value,"
        " and bit 30 after that: SET; put back: AFNC2_LW1 as found, the UARTs as found; byte fd, written to the PMU: 0\n"
        "[  100.4] e1_hook: look: pre: awake, at load: 0x28e0a0000 02000000, 0x28e5b0000 02000000\n"
        "[  131.41] e1_hook: look: pre: as found: 0x28e03801c 80100100; from 0x28e0a0004: 00000002\n"
        "[  131.45] e1_hook: look: pre: SUMMARY: 0x28e03801c took B2's value, bit 30 clear within 100 ms; 21 of the 21 read 1, as B2 writes them, 100 ms after"
        " their writes; put back: 0x28e03801c as found, the 21 as found; byte c0, written to the PMU: 0\n")

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
          mem_sleep="[s2idle]", crumbs="stage 0x6004 = 0x00: none: no M1 run since the last clear", save_rc=0, klog=KLOG, plc=1, rmmod_rc=0,
          pmp_rc=0, look_takes="Y", look=False, parms_look=True, pmpmod=False, pc_file=1, gate_takes="Y", parms_gate=True, pre_takes="Y", parms_pre=True,
          crumbs10="0x6010: 00 00 00 00 00 00 00 5a  00 00 00 00 00 00 00 00"):
    """look: the launcher will be run with --look, so the count it goes by, the FIFO, is prepare_late_calls, and prepare_calls is a
    file with pc_file in it; otherwise the FIFO is prepare_calls and prepare_late_calls a file with plc. The FIFO's values are run()'s."""
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
    os.mkfifo(os.path.join(tmp, "state"))
    plist = ("replay:LAB ONLY (uint)\nwd1_mark:With fabric_up=1 (bool)\n" + ("probe:With wd1_mark=1: no replay, ever (bool)\n" if parms else "")
             + ("look:With probe=1: none of the probe's steps (bool)\n" if parms_look else "")
             + ("gate:With look=1: after the look that follows noirq (bool)\n" if parms_gate else "")
             + ("pre_test:With look=1, never with gate=1 (bool)\n" if parms_pre else ""))
    if pmpmod:
        os.makedirs(os.path.join(tmp, "pmpmod"))
    write(os.path.join(stubs, "modinfo"),
          f'#!/bin/sh\ncase "$1" in -F) echo "{vermagic} SMP preempt mod_unload aarch64" ;; -p) printf "%s" "{plist}" ;; esac\n', 0o755)
    fifo, other, value = ("prepare_late_calls", "prepare_calls", pc_file) if look else ("prepare_calls", "prepare_late_calls", plc)
    made = (f'mkdir -p {moddir}/parameters && echo 0 > {moddir}/parameters/replay_done && mkfifo {moddir}/parameters/{fifo}'
            f' && echo {value} > {moddir}/parameters/{other}')
    if takes is not None:
        made += f' && echo {takes} > {moddir}/parameters/probe'
    if look_takes is not None:
        # The look parameter says Y only if look=1 was among the load's.
        made += f' && case " $* " in *" look=1 "*) echo {look_takes} ;; *) echo N ;; esac > {moddir}/parameters/look'
    if gate_takes is not None:
        made += f' && case " $* " in *" gate=1 "*) echo {gate_takes} ;; *) echo N ;; esac > {moddir}/parameters/gate'
    if pre_takes is not None:
        made += f' && case " $* " in *" pre_test=1 "*) echo {pre_takes} ;; *) echo N ;; esac > {moddir}/parameters/pre_test'
    write(os.path.join(stubs, "insmod"), f'#!/bin/sh\necho "insmod $*" >> {calls}\n[ {insmod_rc} = 0 ] || exit {insmod_rc}\n{made}\n', 0o755)
    # rmmod says what the module's cancel read when it was called: the replay has to be called off before the unload is tried.
    write(os.path.join(stubs, "rmmod"), f'#!/bin/sh\necho "rmmod $* cancel=$(cat {moddir}/parameters/cancel 2> /dev/null)" >> {calls}\n'
          f'[ {rmmod_rc} = 0 ] || exit {rmmod_rc}\nrm -rf {moddir}\n', 0o755)
    write(os.path.join(stubs, "sleep"), f'#!/bin/sh\necho "sleep $*" >> {calls}\n', 0o755)
    write(os.path.join(stubs, "dmesg"), f'#!/bin/sh\necho "dmesg" >> {calls}\ncat {tmp}/klog\n', 0o755)
    write(os.path.join(tmp, "klog"), klog)
    write(os.path.join(stubs, "m1-crumbs"), f'#!/bin/sh\necho "0x6000: 00 30 00 50"\necho "{crumbs10}"\necho "{crumbs}"\n', 0o755)
    # The saver also notes every file in the log directory as it stands at that save: sleep-drain-safe save archives each new one
    # as it finds it, and collect never replaces a file with a different copy of itself, so none may change between two saves.
    write(os.path.join(stubs, "save"), f'#!/bin/sh\necho "sleep-drain-safe $*" >> {calls}\n'
          f'for f in {tmp}/log/*; do [ -f "$f" ] && echo "$(basename "$f") $(md5sum < "$f")"; done >> {tmp}/saves\necho --- >> {tmp}/saves\n'
          f'exit {save_rc}\n', 0o755)
    os.makedirs(os.path.join(tmp, "rundir"))
    write(os.path.join(stubs, "pmp"), f'#!/bin/sh\necho "sleep-drain-pmp $*" >> {calls}\nexit {pmp_rc}\n', 0o755)
    return d


def kernel(tmp, pc, stop, seen, name):
    """The kernel's side of the two FIFOs. A read of the count called name gets pc[n], n being how many times "mem" has
    been written to the suspend file by then (pc's last value from there on). seen gets everything written to the suspend file."""
    param = os.path.join(tmp, "module", "parameters", name)
    sfd = os.open(os.path.join(tmp, "state"), os.O_RDONLY | os.O_NONBLOCK)
    data = b""

    def drain():
        got = b""
        while True:
            try:
                more = os.read(sfd, 4096)
            except BlockingIOError:
                return got
            if not more:
                return got
            got += more

    seen.append(None)   # the suspend file has its reader: the launcher may start
    while not stop.is_set():
        try:
            wfd = os.open(param, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:  # no reader waiting, or no module loaded
            time.sleep(0.001)
            continue
        # A reader has the FIFO open. The launcher does one thing at a time, so whatever it wrote to the suspend file is in the
        # pipe by now. The value is written and the FIFO closed at once: holding it open until the reader has gone could hold
        # the next reader's open too. If this reader has had its line already, it leaves this one unread, and it goes with the pipe.
        data += drain()
        try:
            os.write(wfd, f"{pc[min(data.count(b'mem'), len(pc) - 1)]}\n".encode())
        except OSError:
            pass
        os.close(wfd)
        time.sleep(0.001)
    data += drain()
    os.close(sfd)
    seen.append(data.decode())


def run(tmp, d, argv=(), pc=(0, 1)):
    """Runs the launcher. pc: the count it goes by (prepare_calls; with --look, prepare_late_calls) after 0, 1, 2... suspends asked
    for. Returns its result and what it wrote to the suspend file."""
    env = dict(os.environ, PATH=os.path.join(tmp, "stubs") + ":" + os.environ["PATH"],
               PROBE_SAFE_FLAG=os.path.join(tmp, "run", "active"), PROBE_MODDIR=os.path.join(tmp, "module"),
               PROBE_STATE=os.path.join(tmp, "state"), PROBE_PM_TEST=os.path.join(tmp, "pm_test"),
               PROBE_MEM_SLEEP=os.path.join(tmp, "mem_sleep"), PROBE_LOGDIR=os.path.join(tmp, "log"),
               PROBE_SAVE=os.path.join(tmp, "stubs", "save"), PROBE_CRUMBS=os.path.join(tmp, "stubs", "m1-crumbs"),
               PROBE_PMP=os.path.join(tmp, "stubs", "pmp"), PROBE_PMPMOD=os.path.join(tmp, "pmpmod"),
               PROBE_RUNDIR=os.path.join(tmp, "rundir"))
    stop, seen = threading.Event(), []
    t = threading.Thread(target=kernel, args=(tmp, pc, stop, seen, "prepare_late_calls" if set(argv) & {"--look", "--gate", "--pre"} else "prepare_calls"))
    t.start()
    while not seen:
        time.sleep(0.001)
    try:
        r = subprocess.run(["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", os.path.join(d, "probe")] + list(argv),
                           env=env, capture_output=True, text=True, timeout=60)
    finally:
        stop.set()
        t.join()
    return r, seen[-1]


def calls(tmp):
    try:
        return open(os.path.join(tmp, "calls")).read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def saves(tmp):
    """The log directory at each save: a list of {file name: its hash then}."""
    try:
        text = open(os.path.join(tmp, "saves")).read()
    except FileNotFoundError:
        return []
    return [dict(line.split(" ", 1) for line in part.splitlines()) for part in text.split("---\n")[:-1]]


def changed(tmp):
    """Files of the log directory that a later save found different from an earlier one: there must be none."""
    seen, bad = {}, []
    for snap in saves(tmp):
        for name, h in snap.items():
            if seen.setdefault(name, h) != h:
                bad.append(name)
    return bad


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0

    # The probe as it's meant to go: the load with the recipe's parameters and probe=1, one suspend asked for, the module's lines
    # printed for a photo and saved.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        r, state = run(tmp, d)
        c = calls(tmp)
        logs = os.listdir(os.path.join(tmp, "log")) if os.path.isdir(os.path.join(tmp, "log")) else []
        saved = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
        check(r.returncode == 0 and c[:4] == ["sleep-drain-pmp release 0xc 0xd", "insmod " + os.path.join(d, "e1_hook.ko") + " " + PARAMS, "dmesg", "sleep 0"]
              and state == "mem" and c[4:] == ["dmesg", "sleep-drain-safe save " + os.path.join(tmp, "log", logs[0] if logs else "")]
              and r.stdout.count("asking for the suspend") == 1 and "(1 of 3 at most)" in r.stdout,
              "the PMP's display report requests are set to be withdrawn, as run-floor does; the load is --m1-mark's parameters with probe=1; dmesg,"
              " grep and sleep run once before the suspend; one suspend is asked for; the record is saved; the module stays loaded",
              r.stdout + r.stderr + "\n".join(c))
        check("==================== TAKE A PHOTO OF THIS SCREEN ====================" in r.stdout
              and "replay_done=0 (0 is right: nothing replayed) prepare_calls=1 prepare_late_calls=1" in r.stdout
              and "this script's last suspend returned 0 " in r.stdout
              and "e1_hook: probe: SUMMARY: bit 1 in the state B2 finds: SIO clear, ANS SET, PMP SET; bytes f0 ff fd" in r.stdout
              and r.stdout.count("e1_hook: probe: ANS, awake, at load, its firmware running:") == 1 and "some other driver" not in r.stdout
              and "Saved. Now restart the Mac" in r.stdout and "NOT SAVED" not in r.stdout and "ONLY PART" not in r.stdout
              and r.stdout.index("asking for the suspend") < r.stdout.index("TAKE A PHOTO") < r.stdout.index("SUMMARY") < r.stdout.index("Saved."),
              "it prints the module's probe lines, the load's among them, once each, between the photo's two rules, and says it's saved",
              r.stdout + r.stderr)
        check(saved and all(line in saved for line in r.stdout.splitlines() if "SUMMARY" in line or "TAKE A PHOTO" in line or "loading e1_hook" in line)
              and len(saves(tmp)) == 1 and list(saves(tmp)[0]) == logs and not changed(tmp)
              and os.listdir(os.path.join(tmp, "rundir")) == logs,
              "the saved file has what the screen had; the log grows outside the log directory and goes into it whole, once, for the save", saved)

    # The one that matters: a module that doesn't say probe=Y is a one-way run waiting for its suspend. Its replay is called off,
    # it's unloaded, and no suspend is asked for.
    for takes, what in ((None, "has no probe parameter once loaded"), ("N", "reads probe=N once loaded")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, takes=takes)
            r, state = run(tmp, d)
            c = calls(tmp)
            check(r.returncode == 1 and not state and c[2:] == ["rmmod e1_hook cancel=1"] and "didn't take probe=1, and was unloaded" in r.stderr
                  and "TAKE A PHOTO" not in r.stdout,
                  f"a module that {what}: its replay called off, then unloaded, and no suspend is ever asked for", r.stdout + r.stderr + "\n".join(c))
    # And if it won't unload: said in capitals, with the replay called off all the same, and still no suspend.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, takes="N", rmmod_rc=1)
        r, state = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 1 and not state and c[2:] == ["rmmod e1_hook cancel=1"]
              and "didn't take probe=1 and DID NOT UNLOAD (its replay is called off): restart the Mac now, and don't let it suspend first" in r.stderr
              and "was unloaded" not in r.stderr and open(os.path.join(tmp, "module", "parameters", "cancel")).read() == "1",
              "a module without probe=Y that won't unload: its replay called off, no suspend, and it says the module is still loaded",
              r.stdout + r.stderr + "\n".join(c))

    # A load that fails: the kernel's lines shown and saved, no suspend.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, insmod_rc=1)
        r, state = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 1 and not state and "e1_hook didn't load; nothing was probed" in r.stdout and len(c) == 4
              and c[3].startswith("sleep-drain-safe save "),
              "a module that refuses to load: said, its lines saved, and no suspend", r.stdout + r.stderr + "\n".join(c))
    # The release module that doesn't load: e1_hook isn't loaded either, and no suspend.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, pmp_rc=1)
        r, state = run(tmp, d)
        c = calls(tmp)
        check(r.returncode == 1 and not state and c == ["sleep-drain-pmp release 0xc 0xd"]
              and "couldn't load the PMP report-release module" in r.stderr and "nothing loaded, nothing probed" in r.stderr,
              "the PMP's report-release module doesn't load: e1_hook isn't loaded, and no suspend", r.stdout + r.stderr + "\n".join(c))

    # Everything it refuses before loading anything.
    refusals = [("outside a safe boot", dict(safe=False), "safe boot only"),
                ("with e1_hook loaded already", dict(loaded=True), "e1_hook is loaded already"),
                ("without a built module", dict(ko=False), "make -C"),
                ("with a module built for another kernel", dict(vermagic="6.1.0-other"), "isn't built for"),
                ("with a build that has no probe parameter", dict(parms=False), "an older build, without probe"),
                ("with pm_test set", dict(pm_test="none [platform]"), "pm_test isn't none"),
                ("when s2idle isn't the sleep mode", dict(mem_sleep="s2idle [deep]"), "s2idle isn't the sleep mode"),
                ("with breadcrumbs left in the PMU", dict(crumbs="stage 0x6004 = 0xd3 (pmp_out=1: bit 7, so the stage is 0x53)"),
                 "the PMU's breadcrumbs aren't clear"),
                ("with a probe's breadcrumbs left in the PMU", dict(crumbs="stage 0x6004 = 0x0e: probe=1 (e1-hook/probe), not a run"),
                 "the PMU's breadcrumbs aren't clear"),
                # A clear that stopped halfway: the stage zero, an earlier gate test's reads of the lane still there (Pullfrog on #80).
                ("with bytes left beside a zero stage", dict(crumbs10="0x6010: 00 ff 1f 0f 10 0f 10 00  00 00 00 00 00 00 00 00"),
                 "the PMU's 0x6010-0x6016 aren't clear, though the stage is"),
                ("with the last of those bytes left", dict(crumbs10="0x6010: 00 00 00 00 00 00 10 00  00 00 00 00 00 00 00 00"),
                 "the PMU's 0x6010-0x6016 aren't clear, though the stage is"),
                ("when m1-crumbs prints no 0x6010 row", dict(crumbs10="0x6018: 00"), "the PMU's 0x6010-0x6016 aren't clear, though the stage is")]
    refusals += [(f"with a module older than {n}", dict(newer=n), f"e1_hook.ko is older than {n}") for n in SOURCES]
    for what, kw, msg in refusals:
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, **kw)
            r, state = run(tmp, d)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and not state,
                  f"refused {what}: nothing loaded, no suspend", r.stdout + r.stderr + "\n".join(calls(tmp)))
    for argv in (["--now"], ["--look", "again"], ["x", "--look"], ["--gate", "--look"], ["--look", "--gate"], ["--pre", "--gate"], ["--gate", "--pre"], ["--pre", "x"]):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, look=True)
            r, state = run(tmp, d, argv)
            check(r.returncode == 1 and "usage: probe [--look | --gate | --pre]" in r.stderr and calls(tmp) == [] and not state,
                  f"refused with {' '.join(argv)}: nothing loaded, no suspend", r.stdout + r.stderr)

    # --look: look=1 on top of the probe's parameters, no PMP release, the load's look printed and saved before the one suspend,
    # the look after noirq printed and saved after it.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--look"])
        c = calls(tmp)
        check(r.returncode == 0 and c[:3] == ["insmod " + os.path.join(d, "e1_hook.ko") + " " + PARAMS + " look=1", "dmesg", "sleep 0"] and state == "mem"
              and c[3] == "dmesg" and c[4].startswith("sleep-drain-safe save ") and c[4].endswith("-load.txt") and c[5] == "dmesg"
              and c[6] == c[4].replace("-load.txt", ".txt") and len(c) == 7
              and not any(x.startswith("sleep-drain-pmp") for x in c) and r.stdout.count("asking for the suspend") == 1,
              "--look: the probe's parameters and look=1, the PMP's report requests left alone, the load's look saved before the one suspend,"
              " and the record saved again after it", r.stdout + r.stderr + "\n".join(c))
        check(r.stdout.count("e1_hook: look: after noirq: AON 0x2922c0000: +038 00000000") == 1 and "e1_hook: look: after noirq: done" in r.stdout
              and r.stdout.count("e1_hook: look: awake, at load: gates 0x28e5a0000") == 1 and "e1_hook: probe: SUMMARY" not in r.stdout
              and r.stdout.index("look: awake, at load: gates") < r.stdout.index("(saved: the look at the load is on the boot partition)")
              < r.stdout.index("asking for the suspend") < r.stdout.index("TAKE A PHOTO") < r.stdout.index("look: after noirq: AON")
              < r.stdout.index("=====\n\n") < r.stdout.index("Saved.") and "ANOTHER SUSPEND" not in r.stdout and "ONLY PART" not in r.stdout,
              "--look prints the load's look and says it's saved before it asks for the suspend, then the look after noirq between the photo's"
              " two rules, and none of the probe's lines", r.stdout + r.stderr)
        files = sorted(os.listdir(os.path.join(tmp, "log")))
        early = open(os.path.join(tmp, "log", files[0])).read() if len(files) == 2 else ""
        final = open(os.path.join(tmp, "log", files[1])).read() if len(files) == 2 else ""
        check(len(files) == 2 and files[0] == files[1].replace(".txt", "-load.txt") and "look: awake, at load: gates" in early
              and "after noirq" not in early and "look: awake, at load: gates" in final and "look: after noirq: done" in final
              and len(saves(tmp)) == 2 and list(saves(tmp)[0]) == [files[0]] and sorted(saves(tmp)[1]) == files and not changed(tmp),
              "--look's two saves are of two files, each whole when it's saved and never changed after: the load's look, then the whole"
              " record (Pullfrog on #78: one file saved at two lengths would be collected at its first)", str(saves(tmp)) + early[:300])
    # A module that loads with look=1 and doesn't say so would be the probe that stops the firmware: unloaded, no suspend.
    for look_takes, rmmod_rc, said in (("N", 0, "didn't take look=1, and was unloaded"), (None, 0, "didn't take look=1, and was unloaded"),
                                       ("N", 1, "didn't take look=1 and DID NOT UNLOAD: restart the Mac now")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, look=True, look_takes=look_takes, rmmod_rc=rmmod_rc)
            r, state = run(tmp, d, ["--look"])
            c = calls(tmp)
            check(r.returncode == 1 and not state and c[1:] == ["rmmod e1_hook cancel="] and said in r.stderr and "TAKE A PHOTO" not in r.stdout
                  and ("was unloaded" in r.stderr) == (rmmod_rc == 0),
                  f"--look with a module whose look reads {look_takes} (rmmod returns {rmmod_rc}): no suspend, and it says what became of the module",
                  r.stdout + r.stderr + "\n".join(c))
    # --look refuses a build without look, and a boot in which pmp_sleep_release is loaded: nothing loaded, no suspend.
    for kw, msg in ((dict(parms_look=False), "an older build, without look"),
                    (dict(pmpmod=True), "pmp_sleep_release is loaded, so the look's suspend wouldn't be an ordinary one")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, look=True, **kw)
            r, state = run(tmp, d, ["--look"])
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and not state, f"--look refused ({msg}): nothing loaded, no suspend",
                  r.stdout + r.stderr + "\n".join(calls(tmp)))
    # Neither matters without --look.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, parms_look=False, pmpmod=True, look_takes=None)
        r, state = run(tmp, d)
        check(r.returncode == 0 and state == "mem", "without --look a build that has no look, and pmp_sleep_release loaded, don't stop the probe",
              r.stdout + r.stderr)
    # The look's suspend is asked for again while the look after noirq hasn't run, whatever prepare did; three times at most.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--look"], pc=(0, 0, 1))
        c = calls(tmp)
        check(r.returncode == 0 and state == "memmem" and r.stdout.count("asking for the suspend") == 2 and c.count("sleep 3") == 1
              and "look: after noirq: done" in r.stdout and "ANOTHER SUSPEND" not in r.stdout and "rmmod" not in "".join(c),
              "--look, the first suspend ending before the look after noirq: one wait, a second suspend, then the look's record",
              r.stdout + r.stderr + "\n".join(c))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--look"], pc=(0,))
        c = calls(tmp)
        check(r.returncode == 1 and state == "memmemmem" and "NOTHING WAS PROBED" in r.stdout and "TAKE A PHOTO" not in r.stdout
              and c[-2] == "rmmod e1_hook cancel=" and c[-1].startswith("sleep-drain-safe save ")
              and "e1_hook: look: awake, at load: gates" in r.stdout,
              "--look, three suspends that end before the look after noirq: no fourth, the module unloaded, and the load's look still"
              " printed and saved", r.stdout + r.stderr + "\n".join(c))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--look"], pc=(0, 2))
        check(r.returncode == 0 and state == "mem" and "THE LOOK AFTER NOIRQ RAN AT ANOTHER SUSPEND THAN THIS SCRIPT'S LAST" in r.stdout
              and "prepare_late_calls=2" in r.stdout and "TAKE A PHOTO" in r.stdout,
              "--look with prepare_late_calls 2 after its suspend: said", r.stdout + r.stderr)
    # --gate: --look's load with gate=1 as well; the look after noirq above the photo block, the gate test's lines in it.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--gate"])
        c = calls(tmp)
        check(r.returncode == 0 and c[0] == "insmod " + os.path.join(d, "e1_hook.ko") + " " + PARAMS + " look=1 gate=1" and state == "mem"
              and not any(x.startswith("sleep-drain-pmp") for x in c) and c[4].endswith("-load.txt") and c[-1] == c[4].replace("-load.txt", ".txt")
              and r.stdout.count("asking for the suspend") == 1 and not changed(tmp),
              "--gate: the probe's parameters with look=1 and gate=1, no PMP release, the load's look saved before the one suspend, the record"
              " after it", r.stdout + r.stderr + "\n".join(c))
        check(r.stdout.count("e1_hook: look: gate: SUMMARY: SIO's subtree all off; bit 30 of 0x28e5a0050 before AFNC2_LW1's write: clear; AFNC2_LW1 took") == 1
              and r.stdout.index("kernel log, the look after noirq, which came before the gate test:") < r.stdout.index("look: after noirq: AON")
              < r.stdout.index("look: after noirq: done") < r.stdout.index("TAKE A PHOTO") < r.stdout.index("look: gate: as found")
              < r.stdout.index("look: gate: SUMMARY") < r.stdout.index("=====\n\n") < r.stdout.index("Saved.")
              and r.stdout.count("look: after noirq: AON") == 1 and r.stdout.count("look: gate: as found") == 1 and "e1_hook: probe: SUMMARY" not in r.stdout
              # What to do if the screen stays dark, said before the suspend is asked for.
              and r.stdout.index("(saved: the look at the load is on the boot partition)") < r.stdout.index("THE GATE TEST MAY NOT COME BACK. If the screen goes dark")
              < r.stdout.index("asking for the suspend") and "hold the power key until the Mac restarts" in r.stdout,
              "--gate prints the look after noirq above the photo's block, and between the block's two rules the gate test's lines and no"
              " other of the kernel's, its SUMMARY among them: the result fits the photo",
              r.stdout + r.stderr)
    # A module that doesn't say gate=Y would run a plain look and leave the test undone: unloaded, no suspend. And a build without it.
    for kw, said, loaded in ((dict(gate_takes="N"), "didn't take gate=1, and was unloaded", True),
                             (dict(gate_takes="N", rmmod_rc=1), "didn't take gate=1 and DID NOT UNLOAD", True),
                             (dict(parms_gate=False), "an older build, without gate", False)):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, look=True, **kw)
            r, state = run(tmp, d, ["--gate"])
            c = calls(tmp)
            check(r.returncode == 1 and not state and said in r.stderr and "TAKE A PHOTO" not in r.stdout
                  and (c[1:] == ["rmmod e1_hook cancel="] if loaded else c == []),
                  f"--gate refused ({said}): no suspend", r.stdout + r.stderr + "\n".join(c))
    # --look doesn't ask for the gate test, and a module of --look's doesn't run it.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--look"])
        check(r.returncode == 0 and "gate=1" not in calls(tmp)[0] and open(os.path.join(tmp, "module", "parameters", "gate")).read().strip() == "N"
              and "kernel log, the gate test" not in r.stdout and "MAY NOT COME BACK" not in r.stdout
              and "pre_test=1" not in calls(tmp)[0] and open(os.path.join(tmp, "module", "parameters", "pre_test")).read().strip() == "N"
              and "look: pre:" not in r.stdout and "22 words" not in r.stdout,
              "--look loads no gate=1 and no pre_test=1, and prints neither test", r.stdout + "\n".join(calls(tmp)))
    # --pre: --look's load with pre_test=1 as well; the look after noirq above the photo block, the 22 words' test's lines in it.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--pre"])
        c = calls(tmp)
        check(r.returncode == 0 and c[0] == "insmod " + os.path.join(d, "e1_hook.ko") + " " + PARAMS + " look=1 pre_test=1" and state == "mem"
              and not any(x.startswith("sleep-drain-pmp") for x in c) and c[5].endswith("-load.txt") and c[-1] == c[5].replace("-load.txt", ".txt")
              and [x.split()[0] for x in c].count("sleep-drain-safe") == 2
              and r.stdout.count("asking for the suspend") == 1 and not changed(tmp),
              "--pre: the probe's parameters with look=1 and pre_test=1, no PMP release, the load's look saved before the one suspend, the"
              " record after it", r.stdout + r.stderr + "\n".join(c))
        early = open(c[5].split()[-1]).read()
        check(r.stdout.count("e1_hook: look: pre: SUMMARY: 0x28e03801c took B2's value, bit 30 clear within 100 ms; 21 of the 21 read 1") == 1
              and r.stdout.index("look: awake, at load: gates") < r.stdout.index("look: pre: awake, at load: 0x28e0a0000 02000000")
              < r.stdout.index("(saved: the look at the load is on the boot partition)")
              < r.stdout.index("THIS TEST MAY NOT COME BACK. If the screen goes dark or freezes") < r.stdout.index("asking for the suspend")
              < r.stdout.index("kernel log, the look after noirq, which came before the 22 words' test:") < r.stdout.index("look: after noirq: AON")
              < r.stdout.index("look: after noirq: done") < r.stdout.index("TAKE A PHOTO") < r.stdout.index("kernel log, the 22 words' test")
              < r.stdout.index("look: pre: as found") < r.stdout.index("look: pre: SUMMARY") < r.stdout.index("=====\n\n") < r.stdout.index("Saved.")
              and r.stdout.count("look: after noirq: AON") == 1 and r.stdout.count("look: pre: as found") == 1 and "look: gate:" not in r.stdout
              and "e1_hook: probe: SUMMARY" not in r.stdout and "hold the power key until the Mac restarts" in r.stdout
              and "look: pre: awake, at load" in early and "after noirq" not in early and "SUMMARY" not in early,
              "--pre prints the load's read of the two + 0 words with the load's look, says before the suspend what to do if the Mac doesn't"
              " come back, prints the look after noirq above the photo's block, and in the block the test's lines, its SUMMARY among them",
              r.stdout + r.stderr)
    for kw, said, loaded in ((dict(pre_takes="N"), "didn't take pre_test=1, and was unloaded", True),
                             (dict(pre_takes="N", rmmod_rc=1), "didn't take pre_test=1 and DID NOT UNLOAD", True),
                             (dict(parms_pre=False), "an older build, without pre_test", False)):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, look=True, **kw)
            r, state = run(tmp, d, ["--pre"])
            c = calls(tmp)
            check(r.returncode == 1 and not state and said in r.stderr and "TAKE A PHOTO" not in r.stdout
                  and (c[1:] == ["rmmod e1_hook cancel="] if loaded else c == []),
                  f"--pre refused ({said}): no suspend", r.stdout + r.stderr + "\n".join(c))
    # --gate doesn't ask for the 22 words' test.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, look=True)
        r, state = run(tmp, d, ["--gate"])
        check(r.returncode == 0 and "pre_test=1" not in calls(tmp)[0] and "22 words" not in r.stdout and "THIS TEST MAY NOT COME BACK" not in r.stdout
              and r.stdout.count("look: pre:") == 0,
              "--gate loads no pre_test=1 and prints nothing of the 22 words' test", r.stdout + "\n".join(calls(tmp)))

    # Without --look nothing of the look's: no look=1, and the module's look reads N.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        r, state = run(tmp, d)
        check(r.returncode == 0 and "look=1" not in calls(tmp)[1] and "e1_hook: look:" not in r.stdout
              and open(os.path.join(tmp, "module", "parameters", "look")).read().strip() == "N",
              "without --look: no look=1 in the load, and none of the look's lines printed", r.stdout + "\n".join(calls(tmp)))

    # A suspend that ends before the module's prepare has probed and stopped nothing: asked for again, twice at most, and only
    # while prepare hasn't run. Here the second try gets there.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        r, state = run(tmp, d, pc=(0, 0, 1))
        c = calls(tmp)
        check(r.returncode == 0 and state == "memmem" and r.stdout.count("asking for the suspend") == 2 and c.count("sleep 3") == 1
              and "rmmod" not in "".join(c) and r.stdout.count("ended before the module's part of it") == 1
              and "prepare_calls=1 prepare_late_calls=1" in r.stdout and "TAKE A PHOTO" in r.stdout and "ONLY PART" not in r.stdout
              and "Saved. Now restart the Mac" in r.stdout,
              "a first suspend that ends before the module: one wait, a second suspend, and then the probe's record as usual",
              r.stdout + r.stderr + "\n".join(c))
    # None of three gets there: said, the module unloaded, so that the command can be run again in the same boot.
    for rmmod_rc, said in ((0, "e1_hook is unloaded again. Run the same command once more: no restart is needed."),
                           (1, "e1_hook didn't unload: restart into a fresh safe boot before trying again.")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, rmmod_rc=rmmod_rc, klog="[  9.0] PM: Some devices failed to suspend, or early wake event detected\n")
            r, state = run(tmp, d, pc=(0,))
            c = calls(tmp)
            check(r.returncode == 1 and state == "memmemmem" and r.stdout.count("asking for the suspend") == 3 and c.count("sleep 3") == 2
                  and "NOTHING WAS PROBED: three suspends ended before the module ran, and nothing was stopped" in r.stdout
                  and "PM: Some devices failed to suspend" in r.stdout and said in r.stdout and "TAKE A PHOTO" not in r.stdout
                  and c[-2] == "rmmod e1_hook cancel=" and c[-1].startswith("sleep-drain-safe save "),
                  f"three suspends that end before the module (rmmod returns {rmmod_rc}): no fourth, nothing claimed, the module unloaded, the record saved",
                  r.stdout + r.stderr + "\n".join(c))
    # Once prepare has run there is no second suspend, whatever the first did after it: the first half's lines are the record.
    for pc, plc in (((0, 1), 0), ((0, 2), 1)):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, plc=plc)
            r, state = run(tmp, d, pc=pc)
            c = calls(tmp)
            check(r.returncode == 0 and state == "mem" and r.stdout.count("asking for the suspend") == 1 and "sleep 3" not in c
                  and "rmmod" not in "".join(c) and "ONLY PART OF THE PROBE RAN (1 and 1 is a whole one)" in r.stdout
                  and f"prepare_calls={pc[1]} prepare_late_calls={plc}" in r.stdout and "TAKE A PHOTO" in r.stdout,
                  f"prepare_calls {pc[1]} and prepare_late_calls {plc}: one suspend only, and it says only part of the probe ran",
                  r.stdout + r.stderr + "\n".join(c))
    # A suspend that something else asked for, and that ran the probe: before this script's first ask, or during its wait after a
    # first that ended early. Either way it asks for no other.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        r, state = run(tmp, d, pc=(1,))
        check(r.returncode == 0 and not state and "asking for the suspend" not in r.stdout and "this script's last suspend returned none " in r.stdout
              and "prepare_calls=1 prepare_late_calls=1" in r.stdout and "TAKE A PHOTO" in r.stdout and "ONLY PART" not in r.stdout,
              "the probe already run by another suspend before this script's first: none is asked for, and the record is printed as a whole one",
              r.stdout + r.stderr)

    # A record's name that's taken already (a launcher run in the same second, saved before): it isn't written over, since the
    # copy on the boot partition would then differ from the file; said as not saved.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp)
        write(os.path.join(tmp, "stubs", "date"), "#!/bin/sh\necho 20261110T200000\n", 0o755)
        write(os.path.join(tmp, "log", "asc-probe-20261110T200000.txt"), "an earlier run's record\n")
        r, state = run(tmp, d)
        check(r.returncode == 0 and state == "mem" and "NOT SAVED: the photo is the result" in r.stdout and "Saved. Now" not in r.stdout
              and open(os.path.join(tmp, "log", "asc-probe-20261110T200000.txt")).read() == "an earlier run's record\n"
              and not any(x.startswith("sleep-drain-safe") for x in calls(tmp)),
              "a record whose name exists already: left as it is, nothing saved over it, and it says so", r.stdout + r.stderr)

    # A save that fails, and a kernel log with nothing of the probe's in it: both said.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, save_rc=1)
        r, state = run(tmp, d)
        check(r.returncode == 0 and state == "mem" and "NOT SAVED: the photo is the result" in r.stdout and "Saved. Now" not in r.stdout,
              "a save that fails: the photo is the result, and it says so", r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, klog="[  1.0] nothing of ours\n")
        r, state = run(tmp, d)
        check(r.returncode == 0 and '(no line with "e1_hook: probe:" in the kernel log, or dmesg failed:' in r.stdout,
              "a kernel log without the probe's lines: said, not left blank", r.stdout + r.stderr)

    # The parameters are the launcher's of the latest recipe, and probe=1 is among them, last.
    src = open(os.path.join(E1_HOOK, "probe")).read()
    floor = open(os.path.join(E1_HOOK, "run-floor")).read()
    check(PARAMS.split()[-1] == "probe=1" and all(f"params+=({p})" in floor or p in ("replay=15", "oneway=1", "one_commit=1", "smc_idle=1")
                                                   or "params=(replay=15 oneway=1)" in floor and p.split("=")[0] in floor
                                                   for p in PARAMS.split()[:-1])
          and 'if [[ $(get "$MODDIR/parameters/probe") != Y ]]; then\n  if printf 1 > "$MODDIR/parameters/cancel" 2> /dev/null; then' in src
          and src.index('!= Y ]]; then') < src.index('printf mem > "$STATE"') and src.count('> "$STATE"') == 1
          and ('  [[ $(get "$MODDIR/parameters/$count") == 0 ]] || break\n  log "$(now) asking for the suspend' in src)
          and '  printf mem > "$STATE" 2> /dev/null\n  rc=$?\n  [[ $(get "$MODDIR/parameters/$count") == 0 ]] || break\n' in src
          and "count=prepare_calls\n((look)) && count=prepare_late_calls\n" in src and src.count("count=") == 2
          and src.index('!= Y ]]; then') < src.index('dmesg 2>&1 | grep -F -- "e1_hook: probe:" > /dev/null; sleep 0\n') < src.index("for try in 1 2 3; do")
          and 'if lines=$(dmesg 2>&1 | grep -F -- "$1"); then' in src
          and '"$PMP" release 0xc 0xd > /dev/null' in floor and src.index('"$PMP" release 0xc 0xd > /dev/null 2>&1 || die ') < src.index('insmod "$ko"'),
          "probe's parameters are run-floor's and probe=1; the suspend is asked for in one place, after the check that the module took probe=1, "
          "and only while the module's prepare hasn't run, checked before and after each; klog's own pipeline and sleep are in memory before it")

    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
