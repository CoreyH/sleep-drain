#!/usr/bin/env python3
"""ref-sleep check and go, without root, the reference kernel or a suspend.

The script runs as it stands, as uid 0 in a bubblewrap user namespace, with its paths pointed into a scratch directory
through its RS_* variables: files stand in for /sys/module/apple_s2r, /sys/power, the SEP driver's directory, rtc1 and
/dev/kmsg. dmesg, journalctl, rtcwake and sleep-drain-safe are stubs on PATH, and so is cat, which is how the script
reads sysfs: the stub answers for the kernel where a file can't (a written 1 reads back Y, a written deep reads back
"s2idle [deep]") and can be told to answer otherwise.

Each of those stubs also notes, in a second log, whether go's lock was held when it was called, by trying to take it.

The order of what "go" does is read from one log: every stub call, and every read of the enable parameter, mem_sleep
and the xART attribute, is written there with what the three files the script writes (enable, mem_sleep, state) held
at that moment. The write of mem returns at once here: a regular file takes it.

    python3 -I -B tests/ref_sleep_test.py
"""
import fcntl
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, ".."))
TOOL = os.path.join(ROOT, "ref-sleep")
SEP_RO = "apple_sep.xart_writes=0 apple_sep.provision_keybag=0"
MAILBOX = "[    1.234567] apple-s2r: m1n1 mailbox at 0x9ffe000, 10 CPUs, CPU hotplug through m1n1\n"
BASE = MAILBOX + "[    2.000000] PM: an early line, before any attempt\n[    3.000000] apple-s2r: deep sleep stays off until enabled\n"
AFTER = ("[  100.100000] PM: suspend entry (deep)\n[  100.200000] apple-s2r: entering deep sleep\n[  100.300000] nvme nvme0: an unrelated line\n"
         "[  160.000000] apple-s2r: back\n[  160.100000] PM: suspend exit\n")
DEV = "396400000.sep"
fails = 0


def check(ok, what, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails += 1
        if detail:
            print("     " + str(detail).replace("\n", "\n     "))


def write(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    os.chmod(path, mode)


def stub(t, name, body):
    write(os.path.join(t, "bin", name), "#!/bin/bash\n" + body, 0o755)


def setup(t, enable="N", devices=None, klog=BASE, rtc_name="rtc-apple-pmu 23d1f0000.spmi:pmu@f:rtc@1400", rtc_dev=True):
    for sub in ("bin", "calls", "run", "s2r/parameters", "sepdrv/module", "rtc1", "dev"):
        os.makedirs(os.path.join(t, sub))
    write(f"{t}/run/active", "/dev/mapper/root\n")
    write(f"{t}/proc-cmdline", f"root=UUID=aaaa rw quiet {SEP_RO} sleepdrain.safe\n")
    write(f"{t}/s2r/parameters/enable", enable + "\n")
    write(f"{t}/mem_sleep", "[s2idle] deep\n")
    write(f"{t}/state", "")
    write(f"{t}/pm_test", "[none] core processors platform devices freezer\n")
    for name, val in ({DEV: b"disabled\n"} if devices is None else devices).items():
        os.makedirs(f"{t}/sepdrv/{name}/diag", exist_ok=True)
        if val is not None:
            write(f"{t}/sepdrv/{name}/diag/xart", val)
    if rtc_name is not None:
        write(f"{t}/rtc1/name", rtc_name + "\n")
        write(f"{t}/rtc1/wakealarm", "")
    if rtc_dev:
        write(f"{t}/dev/rtc1", "")
    write(f"{t}/klog-base", klog)
    write(f"{t}/klog-after", AFTER)
    c = f"{t}/calls"
    snap = f'''raw() {{ /usr/bin/cat "$1" 2> /dev/null | tr -d '\\n'; }}
ARGS="$*"
snap() {{
  # Whether go's lock is held now: a try on a descriptor of this stub's own.
  if [[ ! -e {t}/lock ]]; then echo "${{0##*/}} $ARGS nolock" >> {c}/locklog
  elif flock -n {t}/lock true; then echo "${{0##*/}} $ARGS free" >> {c}/locklog
  else echo "${{0##*/}} $ARGS held" >> {c}/locklog; fi
  echo "enable=$(raw {t}/s2r/parameters/enable) mem_sleep=$(raw {t}/mem_sleep) state=$(raw {t}/state)"
}}
'''
    # cat: the kernel's side of the three files a regular file can't play.
    stub(t, "cat", snap + f'''(($#)) || exec /usr/bin/cat
f=${{@: -1}}
case $f in
  {t}/s2r/parameters/enable)
    echo "cat enable | $(snap)" >> {c}/log
    [[ -f $f ]] || exit 1
    if [[ $(raw "$f") == 1 ]]; then [[ -e {c}/enable-ignored ]] && echo N || echo Y; else /usr/bin/cat "$f"; fi ;;
  {t}/mem_sleep)
    echo "cat mem_sleep | $(snap)" >> {c}/log
    case $(raw "$f") in
      deep) [[ -e {c}/deep-ignored ]] && echo "[s2idle] deep" || echo "s2idle [deep]" ;;
      s2idle) [[ -e {c}/s2idle-ignored ]] && echo "s2idle [deep]" || echo "[s2idle] deep" ;;
      *) /usr/bin/cat "$f" ;;
    esac ;;
  */diag/xart)
    echo "cat xart | $(snap)" >> {c}/log
    if [[ -e {c}/xart-after-arm && -e {c}/armed ]]; then /usr/bin/cat {c}/xart-after-arm
    elif [[ -e {c}/xart-after-mem && $(raw {t}/state) == mem ]]; then /usr/bin/cat {c}/xart-after-mem
    else /usr/bin/cat "$f"; fi ;;
  *) exec /usr/bin/cat "$@" ;;
esac
''')
    log_out = f'''[[ ! -e {c}/fail-$0 ]] || true
/usr/bin/cat {t}/klog-base
[[ ! -f {t}/kmsg ]] || /usr/bin/cat {t}/kmsg
[[ $(/usr/bin/cat {t}/state 2> /dev/null) != mem ]] || /usr/bin/cat {t}/klog-after
'''
    stub(t, "dmesg", f"[[ ! -e {c}/fail-dmesg ]] || exit 1\n" + log_out)
    stub(t, "journalctl", snap + f'''if [[ $1 == --sync ]]; then echo "journalctl --sync | $(snap)" >> {c}/log; exit 0; fi
[[ -e {c}/journal-works ]] || exit 1
''' + log_out)
    stub(t, "rtcwake", snap + f'''echo "rtcwake $* | $(snap)" >> {c}/log
case " $* " in
  *" -m disable "*) rm -f {c}/armed ;;
  *) [[ ! -e {c}/fail-rtcwake ]] || exit 1; touch {c}/armed; echo "rtcwake: wakeup using rtc1" ;;
esac
''')
    stub(t, "save", snap + f'''echo "save $* | $(snap)" >> {c}/log
n=$(grep -c "^save " {c}/log)
# calls/hold-save: this save waits until calls/release is there (a go held in the middle, for the second go's test).
if [[ -e {c}/hold-save && $n == 1 ]]; then touch {c}/holding; while [[ ! -e {c}/release ]]; do /usr/bin/sleep 0.05; done; fi
[[ ! -e {c}/fail-save-$n ]] || {{ echo "sleep-drain-safe: couldn't write to the boot partition" >&2; exit 1; }}
echo "saved to the boot partition (sleep-drain-safe/saved/x.tar)."
''')


def run(t, *args, root=True, start=False):
    env = {"PATH": f"{t}/bin:/usr/bin", "RS_SAFE_FLAG": f"{t}/run/active", "RS_PROC_CMDLINE": f"{t}/proc-cmdline", "RS_S2R": f"{t}/s2r",
           "RS_MEM_SLEEP": f"{t}/mem_sleep", "RS_STATE": f"{t}/state", "RS_PM_TEST": f"{t}/pm_test", "RS_SEP_DRIVER": f"{t}/sepdrv",
           "RS_RTC_SYS": f"{t}/rtc1", "RS_RTC_DEV": f"{t}/dev/rtc1", "RS_KMSG": f"{t}/kmsg", "RS_SAVE": f"{t}/bin/save", "RS_LOCK": f"{t}/lock"}
    cmd = [TOOL, *args]
    if root:
        cmd = ["bwrap", "--unshare-user", "--uid", "0", "--gid", "0", "--dev-bind", "/", "/"] + cmd
    if start:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)


def events(t):
    """The log: (what, enable, mem_sleep, state) per line, the three as the files held them at that call."""
    p = f"{t}/calls/log"
    out = []
    for l in (open(p).read().splitlines() if os.path.exists(p) else []):
        what, _, s = l.partition(" | ")
        en, _, rest = s.partition(" mem_sleep=")
        ms, _, st = rest.partition(" state=")
        out.append((what, en[len("enable="):], ms, st))
    return out


def raw(t, name):
    p = f"{t}/{name}"
    return open(p).read() if os.path.isfile(p) else None


def untouched(t, enable="N\n"):
    """Nothing written: the three files as they were, no mark in the kernel log, no save, no alarm, no sync."""
    ev = [e[0] for e in events(t)]
    return (raw(t, "s2r/parameters/enable") in (enable, None) and raw(t, "mem_sleep") == "[s2idle] deep\n" and raw(t, "state") in ("", None)
            and not os.path.exists(f"{t}/kmsg") and not any(e.startswith(("save", "rtcwake", "journalctl")) for e in ev))


def flag(t, name, value=""):
    write(f"{t}/calls/{name}", value)


def happy():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        p = run(t, "check")
        o = p.stdout
        check(p.returncode == 0 and o.count("\nok      ") + o.startswith("ok      ") == 5 and "NOT OK" not in o and "all of the preconditions hold" in o
              and "the kernel log has the mailbox line: " + MAILBOX.strip() in o and 'the bound driver\'s diag/xart reads "disabled"' in o
              and "write 1 to" in o and "(one way for this boot)" in o and "write deep to" in o and "sleep-drain-safe" not in o.replace(f"{t}/bin/save", "")
              and f"{t}/bin/save save" in o and "arm the wake: rtcwake -d rtc1 -m no -s SECONDS" in o and "write mem to" in o and "Nothing was changed by this check" in o
              and untouched(t),
              "check with everything in place: the five findings, each ok, and what go would do, in order; nothing written, saved, armed or marked",
              o + p.stderr + str(events(t)))
        p = run(t, "go")
        o, ev = p.stdout, events(t)
        kinds = [e[0] for e in ev]
        # 1. enable before anything else is written.
        i_en = next((i for i, e in enumerate(ev) if e[1] == "1"), None)
        ok_en = i_en is not None and ev[i_en] == ("cat enable", "1", "[s2idle] deep", "") and all(e[1:] == ("N", "[s2idle] deep", "") for e in ev[:i_en])
        check(ok_en, "go: 1 is written to enable first, with mem_sleep and state still as they were, and read back; nothing was written before it", ev)
        # 2. deep after it, read back.
        i_deep = next((i for i, e in enumerate(ev) if e[2] == "deep"), None)
        ok_deep = ok_en and i_deep is not None and i_deep > i_en and ev[i_deep] == ("cat mem_sleep", "1", "deep", "")
        check(ok_deep, "    then deep is written to mem_sleep and read back, with enable on and nothing written to state", ev)
        # 3. the save.
        saves = [i for i, k in enumerate(kinds) if k == "save save"]
        ok_save = ok_deep and len(saves) == 2 and saves[0] > i_deep and ev[saves[0]][1:] == ("1", "deep", "") and not any(k.startswith("rtcwake") for k in kinds[:saves[0]])
        check(ok_save, "    then the save (sleep-drain-safe save), before any alarm is armed and before state is written", ev)
        # 4. the wake.
        arms = [i for i, k in enumerate(kinds) if k.startswith("rtcwake") and "-m no" in k]
        ok_arm = ok_save and len(arms) == 1 and arms[0] > saves[0] and kinds[arms[0]] == "rtcwake -d rtc1 -m no -s 60" and ev[arms[0]][3] == ""
        check(ok_arm, "    then the wake: rtcwake -d rtc1 -m no -s 60 (the default), once, state still unwritten", ev)
        # 5. the xART proof once more, as the last thing before the write.
        before = [i for i, e in enumerate(ev) if e[3] == ""]
        ok_x = ok_arm and ev[before[-1]][0] == "cat xart" and before[-1] > arms[0] and before == list(range(len(before)))
        check(ok_x, "    then the xART attribute is read once more: the last thing logged before state holds mem, and nothing is logged between it and the write", ev)
        # 6. the write, and what follows it.
        after = ev[len(before):]
        ok_after = (ok_x and raw(t, "state") == "mem\n" and all(e[3] == "mem" for e in after) and [e[0] for e in after if e[0] == "cat xart"] == ["cat xart"]
                    and saves[1] >= len(before) and kinds.index("cat xart", len(before)) < saves[1])
        check(ok_after, "    then mem is written to state, once; after it the xART attribute is read again and the log saved again", ev)
        mark = raw(t, "kmsg") or ""
        check(mark.count("\n") == 1 and mark.startswith("ref-sleep: attempt ") and "mem_sleep deep, wake armed 60 s from now" in mark,
              "    the kernel log is marked once, before the write, with what the attempt is", mark)
        check(raw(t, "mem_sleep") == "s2idle\n" and "mem_sleep is back to: [s2idle] deep" in o.replace(f"{t}/", "") and raw(t, "s2r/parameters/enable") == "1\n"
              and kinds[-1] == "cat mem_sleep" and any(k == "rtcwake -d rtc1 -m disable" for k in kinds[saves[1]:]),
              "    at the end mem_sleep is put back to s2idle and read back, and the alarm switched off; enable stays on (one way)", o + str(kinds[-4:]))
        lines = o[o.index("the kernel log since the attempt"):] if "the kernel log since the attempt" in o else ""
        check(p.returncode == 0 and "the write of mem returned without an error after " in o and " s by the clock" in o
              and "PM: suspend entry (deep)" in lines and "apple-s2r: entering deep sleep" in lines and "apple-s2r: back" in lines and "PM: suspend exit" in lines
              and "an unrelated line" not in o and "an early line, before any attempt" not in lines and "deep sleep stays off until enabled" not in lines
              and "ref-sleep: attempt" not in lines and "the xART proof still holds" in lines and "saved this boot's log again" in lines,
              "    it prints the write's result and how long it took, the kernel log's lines since the mark that mention s2r, PM: or suspend (not the earlier ones,"
              " not unrelated ones), that the xART proof still holds, and that it saved again; exit 0", o + p.stderr)
    # SECONDS, and an enable that is on already.
    with tempfile.TemporaryDirectory() as t:
        setup(t, enable="Y")
        p = run(t, "go", "900")
        kinds = [e[0] for e in events(t)]
        check(p.returncode == 0 and "rtcwake -d rtc1 -m no -s 900" in kinds and raw(t, "s2r/parameters/enable") == "Y\n" and "already reads Y: not written" in p.stdout
              and raw(t, "state") == "mem\n" and "wake armed 900 s from now" in raw(t, "kmsg"),
              "go 900 with enable reading Y already: enable isn't written, the wake is armed 900 s out, the attempt is made", p.stdout + p.stderr)
    for enable in ("1",):
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            write(f"{t}/s2r/parameters/enable", "1\n")
            flag(t, "enable-ignored")     # the stub then reads a raw 1 as N: an int parameter that reads 1 is what is wanted here
            os.remove(f"{t}/calls/enable-ignored")
            stub(t, "noop", "exit 0\n")
            p = run(t, "check")
            check(p.returncode == 0 and "leave apple_s2r's enable as it is (it reads Y)" in p.stdout, "check with enable on already: go would leave it", p.stdout + p.stderr)
    for bad in ("abc", "14", "0", "3601", "-5", "1e3", "60s", ""):
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            p = run(t, "go", bad)
            check(p.returncode != 0 and "SECONDS is a whole number from 15 to 3600" in p.stderr and "Nothing written" in p.stderr and untouched(t) and events(t) == [],
                  f"go {bad!r}: refused before anything is read or written", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        for n in ("15", "3600"):
            write(f"{t}/state", "")
            p = run(t, "go", n)
            check(p.returncode == 0 and f"rtcwake -d rtc1 -m no -s {n}" in [e[0] for e in events(t)], f"go {n}: taken", p.stdout + p.stderr)
        for args in (["go", "60", "70"], ["check", "x"], [], ["sleep"], ["go", "--force"]):
            before = len(events(t))
            p = run(t, *args)
            check(p.returncode != 0 and ("usage: ref-sleep check | go [SECONDS]" in p.stderr or "SECONDS is a whole number" in p.stderr) and len(events(t)) == before,
                  f"ref-sleep {' '.join(args)}: refused with the usage, nothing done", p.stdout + p.stderr)


def preconditions():
    def no_flag(t): os.remove(f"{t}/run/active")
    def no_word(t): write(f"{t}/proc-cmdline", f"root=UUID=aaaa rw quiet {SEP_RO}\n")
    def word_after_sep(t): write(f"{t}/proc-cmdline", f"root=UUID=aaaa rw quiet {SEP_RO} -- sleepdrain.safe\n")
    def word_inside(t): write(f"{t}/proc-cmdline", f"root=UUID=aaaa rw x=sleepdrain.safe sleepdrain.safe2 {SEP_RO}\n")
    def no_module(t): os.rename(f"{t}/s2r", f"{t}/s2r.away")
    def no_mailbox(t): write(f"{t}/klog-base", "[    1.2] apple-s2r: no m1n1 mailbox, doing nothing\n[    2.0] PM: something\n")
    def no_log(t): flag(t, "fail-dmesg")
    cases = (("the safe boot's marker isn't there", no_flag, "not a safe boot"),
             ("sleepdrain.safe isn't on the kernel's command line", no_word, "sleepdrain.safe isn't on the kernel's command line"),
             ("sleepdrain.safe is on the command line only after a --", word_after_sep, "sleepdrain.safe isn't on the kernel's command line"),
             ("sleepdrain.safe is only part of other words", word_inside, "sleepdrain.safe isn't on the kernel's command line"),
             ("/sys/module/apple_s2r isn't there", no_module, "this kernel doesn't have the reference's driver"),
             ("the kernel log has no mailbox line", no_mailbox, 'the kernel log has no "apple-s2r: m1n1 mailbox at" line'),
             ("the kernel log can't be read at all", no_log, 'the kernel log has no "apple-s2r: m1n1 mailbox at" line'))
    for what, do, why in cases:
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            do(t)
            for cmd in (["check"], ["go"], ["go", "30"]):
                p = run(t, *cmd)
                o = p.stdout
                check(p.returncode != 0 and f"NOT OK  " in o and why in o and o.count("NOT OK") == 1 and o.count("\nok      ") + o.startswith("ok      ") == 4
                      and untouched(t) and "all of the preconditions hold" not in o
                      and (("would refuse and write nothing" in o) if cmd == ["check"] else ("nothing written, no suspend asked for" in p.stderr)),
                      f"{' '.join(cmd)} when {what}: that one finding NOT OK, the other four ok, and nothing written: no enable, no deep, no save, no alarm, no mem",
                      o + p.stderr + str(events(t)))
    # The xART proof: the strict reading, as sleep-drain-safe status and run-floor make it.
    DEV2 = "396500000.sep"
    xart = (("the device says enabled", {DEV: b"enabled\n"}, "reads $'enabled\\n'"),
            ("no device is bound", {}, "no device is bound to apple_sep"),
            ("the bound device has no such attribute", {DEV: None}, "diag/xart isn't there or can't be read"),
            ("the attribute is empty", {DEV: b""}, "reads '', which isn't exactly"),
            ("the attribute says disabled with no newline", {DEV: b"disabled"}, "reads disabled, which isn't exactly"),
            ("the attribute says disabled and something after it", {DEV: b"disabled\nenabled\n"}, "which isn't exactly"),
            ("the attribute says disabled and a second newline", {DEV: b"disabled\n\n"}, "which isn't exactly"),
            ("the attribute says disabled with a space", {DEV: b"disabled \n"}, "which isn't exactly"),
            ("the attribute says DISABLED", {DEV: b"DISABLED\n"}, "which isn't exactly"),
            ("the attribute says 0", {DEV: b"0\n"}, "which isn't exactly"),
            ("one of two devices says enabled", {DEV: b"disabled\n", DEV2: b"enabled\n"}, DEV2 + "'s diag/xart reads"),
            ("the first of two says enabled", {DEV: b"enabled\n", DEV2: b"disabled\n"}, DEV + "'s diag/xart reads"),
            ("one of two has no attribute", {DEV: b"disabled\n", DEV2: None}, DEV2 + "'s diag/xart isn't there"),
            ("the only thing with the attribute is the driver's link to its module", {"module": b"disabled\n"}, "no device is bound to apple_sep"))
    for what, devices, why in xart:
        with tempfile.TemporaryDirectory() as t:
            setup(t, devices=devices)
            for cmd in (["check"], ["go"]):
                p = run(t, *cmd)
                check(p.returncode != 0 and "NOT OK  the Secure Enclave's store (xART) is NOT shown read-only: " in p.stdout and why in p.stdout and p.stdout.count("NOT OK") == 1 and untouched(t),
                      f"{' '.join(cmd)} when {what}: the store is not shown read-only, and nothing is written", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(f"{t}/x")
        setup(f"{t}/x", devices={})
        os.rmdir(f"{t}/x/sepdrv/module")
        os.rmdir(f"{t}/x/sepdrv")
        p = run(f"{t}/x", "go")
        check(p.returncode != 0 and "no device is bound to apple_sep" in p.stdout and untouched(f"{t}/x"), "go with no apple_sep driver directory at all: refused, nothing written", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t, devices={DEV: b"disabled\n", "396500000.sep": b"disabled\n"})
        p = run(t, "go")
        check(p.returncode == 0 and "for each of its 2 devices" in p.stdout and "the xART proof holds still (2 devices)" in p.stdout and raw(t, "state") == "mem\n",
              "go with two bound devices both saying disabled: taken", p.stdout + p.stderr)
    # All of them at once: every finding is printed, not only the first.
    with tempfile.TemporaryDirectory() as t:
        setup(t, devices={DEV: b"enabled\n"})
        os.remove(f"{t}/run/active")
        write(f"{t}/proc-cmdline", "root=x\n")
        os.rename(f"{t}/s2r", f"{t}/s2r.away")
        write(f"{t}/klog-base", "nothing\n")
        p = run(t, "go")
        check(p.returncode != 0 and p.stdout.count("NOT OK") == 5 and "5 of the preconditions don't hold" in p.stderr and untouched(t, enable=None),
              "go with none of the five holding: all five are said, and nothing is written", p.stdout + p.stderr)
    # The mailbox line only in the journal: the ring buffer has wrapped.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "fail-dmesg")
        flag(t, "journal-works")
        p = run(t, "check")
        check(p.returncode == 0 and "the kernel log has the mailbox line" in p.stdout, "check when dmesg gives nothing and the journal has the mailbox line: ok", p.stdout + p.stderr)
        p = run(t, "go")
        check(p.returncode == 0 and "PM: suspend entry (deep)" in p.stdout and "an early line" not in p.stdout[p.stdout.index("the kernel log since"):],
              "    and go there takes the lines since the mark from the journal", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        for cmd in (["check"], ["go"]):
            p = run(t, *cmd, root=False)
            check(p.returncode != 0 and "run as root" in p.stderr and untouched(t) and events(t) == [], f"{cmd[0]} without root: refused, nothing read or written", p.stdout + p.stderr)


def go_stops():
    """Each step of go failing: what was written before it stays said, nothing after it happens, and mem_sleep is put back."""
    def no_suspend(t):
        return raw(t, "state") == "" and "writing mem" not in open(f"{t}/out").read()

    def go(t, *args):
        p = run(t, "go", *args)
        write(f"{t}/out", p.stdout)
        return p, [e[0] for e in events(t)]

    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "enable-ignored")
        p, kinds = go(t)
        check(p.returncode != 0 and "wrote 1 to" in p.stderr and "it reads N: deep sleep is not enabled" in p.stderr and no_suspend(t) and raw(t, "mem_sleep") == "[s2idle] deep\n"
              and not any(k.startswith(("save", "rtcwake")) for k in kinds) and not os.path.exists(f"{t}/kmsg"),
              "go when enable doesn't take the 1: it stops; mem_sleep is never written, nothing saved, armed, marked or suspended", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        os.remove(f"{t}/s2r/parameters/enable")
        p, kinds = go(t)
        check(p.returncode != 0 and "can't read" in p.stderr and no_suspend(t) and raw(t, "mem_sleep") == "[s2idle] deep\n" and not any(k.startswith(("save", "rtcwake")) for k in kinds),
              "go when the enable parameter isn't there: it stops, nothing written", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "deep-ignored")
        p, kinds = go(t)
        check(p.returncode != 0 and "[deep] isn't selected" in p.stderr and no_suspend(t) and raw(t, "mem_sleep") == "s2idle\n" and "mem_sleep is back to: [s2idle] deep" in p.stdout
              and not any(k.startswith(("save", "rtcwake")) for k in kinds) and not os.path.exists(f"{t}/kmsg"),
              "go when mem_sleep doesn't select deep: it stops; s2idle is written back; nothing saved, armed, marked or suspended", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        write(f"{t}/mem_sleep", "s2idle deep\n")
        p, kinds = go(t)
        check(p.returncode != 0 and "with nothing selected" in p.stderr and no_suspend(t) and raw(t, "mem_sleep") == "s2idle deep\n" and not any(k.startswith(("save", "rtcwake")) for k in kinds),
              "go when mem_sleep shows no selection to put back later: it stops before writing deep", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "fail-save-1")
        p, kinds = go(t)
        check(p.returncode != 0 and "the save failed" in p.stderr and "No suspend asked for" in p.stderr and no_suspend(t) and raw(t, "mem_sleep") == "s2idle\n"
              and "mem_sleep is back to: [s2idle] deep" in p.stdout and not any(k.startswith("rtcwake") for k in kinds) and not os.path.exists(f"{t}/kmsg") and kinds.count("save save") == 1,
              "go when the save fails: no record, no attempt; no alarm armed, nothing marked, mem never written, and mem_sleep is put back to s2idle", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "fail-rtcwake")
        p, kinds = go(t)
        check(p.returncode != 0 and "rtcwake couldn't arm rtc1" in p.stderr and no_suspend(t) and raw(t, "mem_sleep") == "s2idle\n" and not os.path.exists(f"{t}/kmsg"),
              "go when rtcwake fails on a PMU RTC that is there: it stops (a timed wake was expected); mem never written, mem_sleep put back", p.stdout + p.stderr)
    # The proof gone between the first reading and the last: the attribute changes once the alarm is armed.
    for val, why in ((b"enabled\n", "reads $'enabled\\n'"), (b"disabled", "reads disabled, which isn't exactly"), (b"", "reads ''")):
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            flag(t, "xart-after-arm", val)
            p, kinds = go(t)
            check(p.returncode != 0 and "the Secure Enclave's store (xART) is no longer shown read-only" in p.stderr and why in p.stderr and "No suspend asked for" in p.stderr
                  and no_suspend(t) and raw(t, "mem_sleep") == "s2idle\n" and kinds.count("rtcwake -d rtc1 -m no -s 60") == 1 and "rtcwake -d rtc1 -m disable" in kinds
                  and kinds.count("save save") == 1,
                  f"go when the xART attribute reads {val!r} at the last check, after reading disabled at the first: mem is never written; the alarm is switched off and mem_sleep put back",
                  p.stdout + p.stderr + str(kinds))
    # No timed wake: said, and the attempt goes on.
    for what, kw, why in (("/dev/rtc1 isn't there", dict(rtc_dev=False), "NO TIMED WAKE IS ARMED: rtc1 isn't there"),
                          ("rtc1 is another RTC", dict(rtc_name="rtc-macsmc macsmc-rtc"), 'NO TIMED WAKE IS ARMED: rtc1 is named "rtc-macsmc macsmc-rtc", not the PMU\'s RTC'),
                          ("rtc1 has no name", dict(rtc_name=None), "NO TIMED WAKE IS ARMED")):
        with tempfile.TemporaryDirectory() as t:
            setup(t, **kw)
            p = run(t, "check")
            check(p.returncode == 0 and "no timed wake" in p.stdout and "arm NO timed wake" in p.stdout, f"check when {what}: it says go would arm no timed wake", p.stdout + p.stderr)
            p, kinds = go(t)
            check(p.returncode == 0 and why in p.stdout and "The power button, a key or the lid are the wakes" in p.stdout and not any(k.startswith("rtcwake") for k in kinds)
                  and raw(t, "state") == "mem\n" and "no timed wake" in raw(t, "kmsg"),
                  f"go when {what}: it says no timed wake is armed and what the wakes are, calls rtcwake not at all, and makes the attempt", p.stdout + p.stderr)
    # The write refused by the kernel (here: state is a directory, so the write fails).
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        os.remove(f"{t}/state")
        os.makedirs(f"{t}/state")
        p = run(t, "go")
        kinds = [e[0] for e in events(t)]
        check(p.returncode != 0 and "THE WRITE OF mem FAILED after " in p.stdout and "The kernel refused or aborted the suspend" in p.stdout and "returned without an error" not in p.stdout
              and kinds.count("save save") == 2 and "the xART proof still holds" in p.stdout and open(f"{t}/mem_sleep").read() == "s2idle\n" and "rtcwake -d rtc1 -m disable" in kinds,
              "go when the write of mem fails: it says so with the error, still prints the log's lines and the xART reading, saves again, puts mem_sleep back, and exits non-zero",
              p.stdout + p.stderr)
    # After the wake: the proof gone, the second save failing, mem_sleep not going back.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "xart-after-mem", b"enabled\n")
        p, kinds = go(t)
        check(p.returncode != 0 and "THE xART PROOF NO LONGER HOLDS" in p.stdout and "reads $'enabled\\n'" in p.stdout and kinds.count("save save") == 2 and raw(t, "state") == "mem\n",
              "go when the xART attribute reads enabled after the wake: said in capitals, saved all the same, exit non-zero", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "fail-save-2")
        p, kinds = go(t)
        check(p.returncode != 0 and "the save after the attempt failed" in p.stderr and "saved this boot's log again" not in p.stdout and raw(t, "mem_sleep") == "s2idle\n",
              "go when the save after the attempt fails: said, exit non-zero, mem_sleep still put back", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "s2idle-ignored")
        p, kinds = go(t)
        check(p.returncode != 0 and "COULD NOT put" in p.stderr and "restart before closing the lid" in p.stderr,
              "go when mem_sleep doesn't go back to s2idle afterwards: said in capitals with what to do, exit non-zero", p.stdout + p.stderr)
    # The mark can't be written: the attempt goes on, and the lines are the last that match.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        os.makedirs(f"{t}/kmsg")
        p, kinds = go(t)
        check(p.returncode == 0 and "couldn't mark the kernel log" in p.stderr and "the mark isn't in the kernel log" in p.stdout and "PM: suspend exit" in p.stdout and raw(t, "state") == "mem\n",
              "go when the kernel log can't be marked: said; the attempt is made, and the last matching lines are shown as such", p.stdout + p.stderr)
    # pm_test set: a note, in both commands.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        write(f"{t}/pm_test", "none core processors platform [devices] freezer\n")
        for cmd in ("check", "go"):
            p = run(t, cmd)
            check("NOTE:" in p.stdout and "pm_test reads: none core processors platform [devices] freezer" in p.stdout and "not a real one" in p.stdout,
                  f"{cmd} with pm_test set to devices: a note that the suspend would be the kernel's test of one", p.stdout + p.stderr)


def one_at_a_time():
    """Pullfrog on #136: two go's at once would each put back the other's mem_sleep and switch off the other's alarm."""
    def locklog(t):
        p = f"{t}/calls/locklog"
        return open(p).read().splitlines() if os.path.exists(p) else []

    # The lock held by somebody else: go ends at once, with nothing read of the sleep state and nothing written.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        with open(f"{t}/lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = run(t, "go")
            check(p.returncode != 0 and "another ref-sleep go is running" in p.stderr and "wrote nothing and asked for no suspend" in p.stderr and untouched(t)
                  and events(t) == [] and p.stdout == "",
                  "go while another holds the lock: it ends at once; nothing read, nothing written, no save, no alarm, no suspend", p.stdout + p.stderr)
            p = run(t, "check")
            check(p.returncode == 0 and "all of the preconditions hold" in p.stdout and untouched(t), "    check meanwhile: it changes nothing, and needs no lock", p.stdout + p.stderr)
        p = run(t, "go")
        check(p.returncode == 0 and raw(t, "state") == "mem\n", "    go once the lock is free: the attempt is made", p.stdout + p.stderr)
    # The lock is held from before the first reading to the end of the putting-back: every stub call of a go finds it held.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        p = run(t, "go")
        ll = locklog(t)
        check(p.returncode == 0 and len(ll) >= 12 and all(l.endswith(" held") for l in ll) and ll[-1].startswith("cat ") and any(l.startswith("rtcwake -d rtc1 -m disable") for l in ll[-3:])
              and any("cat" in l and "xart" in l for l in ll[:2]),
              "go: the lock is held at every step, from the first reading of the preconditions to the last of the EXIT trap (the alarm switched off, mem_sleep put back and read)",
              "\n".join(ll))
        with open(f"{t}/lock", "w") as f:
            got = True
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                got = False
        check(got, "    and it is free again once go has ended")
    # And when go stops early: still held through the putting-back.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "xart-after-arm", b"enabled\n")
        p = run(t, "go")
        ll = locklog(t)
        check(p.returncode != 0 and all(l.endswith(" held") for l in ll) and ll[-1].startswith("cat ") and raw(t, "mem_sleep") == "s2idle\n",
              "go that stops at the last xART check: the lock is held until mem_sleep is put back", "\n".join(ll))
    # Two at once: the first is held in the middle of its save, with deep selected and nothing armed yet.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        flag(t, "hold-save")
        first = run(t, "go", "30", start=True)
        deadline = time.time() + 30
        while not os.path.exists(f"{t}/calls/holding") and time.time() < deadline:
            time.sleep(0.05)
        mid = raw(t, "mem_sleep")
        n = len(events(t))
        second = run(t, "go", "40")
        same = len(events(t)) == n and raw(t, "mem_sleep") == mid == "deep\n" and raw(t, "state") == ""
        flag(t, "release")
        out, err = first.communicate(timeout=60)
        kinds = [e[0] for e in events(t)]
        check(os.path.exists(f"{t}/calls/holding") and second.returncode != 0 and "another ref-sleep go is running" in second.stderr and second.stdout == "" and same,
              "a second go while the first is in the middle (deep selected, its save running): the second ends at once and touches nothing: mem_sleep still deep, no call of its own",
              second.stdout + second.stderr)
        check(first.returncode == 0 and raw(t, "mem_sleep") == "s2idle\n" and raw(t, "state") == "mem\n" and kinds.count("rtcwake -d rtc1 -m no -s 30") == 1
              and not any("-s 40" in k for k in kinds) and kinds.count("rtcwake -d rtc1 -m disable") == 1 and "mem_sleep is back to: [s2idle] deep" in out.replace(f"{t}/", ""),
              "    the first then finishes as if alone: its own alarm armed once and switched off once, and mem_sleep back to s2idle, the selection from before either of them",
              out + err + str(kinds))
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        os.makedirs(f"{t}/lock")
        p = run(t, "go")
        check(p.returncode != 0 and "couldn't open the lock" in p.stderr and untouched(t) and events(t) == [], "go when the lock can't be opened: refused, nothing written", p.stdout + p.stderr)


def source():
    src = open(TOOL).read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    check(not any(w in code for w in ("insmod", "modprobe", "rmmod", "boot.bin", "ref-loader", "/boot", "grub")),
          "ref-sleep's code never loads or unloads a module and names nothing of the loader or the boot partition")
    check(code.count('> "$STATE"') == 1 and code.count('> "$ENABLE"') == 1 and code.count('> "$MEM_SLEEP"') == 2,
          "    it has one write to state, one to enable, and two to mem_sleep (deep, and the selection put back)")
    inst, un = open(os.path.join(ROOT, "install.sh")).read(), open(os.path.join(ROOT, "uninstall.sh")).read()
    check("ref-sleep" not in inst and "ref-sleep" not in un, "install.sh doesn't install ref-sleep: it is run by its full path")


def main():
    happy()
    one_at_a_time()
    preconditions()
    go_stops()
    source()
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
