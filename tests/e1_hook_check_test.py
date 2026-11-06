#!/usr/bin/python3 -I
"""Hardware-free checks for e1-hook/check: the real script, as root in a
bubblewrap sandbox, against a fake /sys, /proc, /run and /var/log.

  python3 -I tests/e1_hook_check_test.py      (from the repo root)

This file is also the fake kernel (--sim). /sys/power/state and /dev/kmsg
are FIFOs it serves: a write of "freeze" plays one pm_test=platform suspend,
and the stub systemctl plays one alarm sleep, each with e1_hook's ops logging
in the kernel's order (prepare, the noirq pass, prepare_late), into the fake
kernel log and into each trace buffer that's on and has the event enabled,
with the module's parameters counting. Stubs stand in for insmod, rmmod,
rtcwake, dmesg, sleep and sleep-drain-safe, and for mkdir and rmdir in
/sys/kernel/tracing/instances/, where tracefs makes and removes an instance's
files. /run is a fake too, so nothing in the sandbox can reach the real D-Bus
(logind would suspend for the real systemctl).

The main trace buffer holds an earlier capture, and the session that made it
is still on (suspend_resume enabled). Each case checks the exit status, the
answers, that everything check changed is put back (pm_test,
pm_debug_messages, pm_print_times, e1_hook unloaded, its trace instance
removed), that the main buffer is as it was (its settings, and its capture
with only its own session's records after it), and the saves: one after run 1
with the results as a PATH, one at the end with them in /var/log/sleep-drain/.
Some cases play a fault: insmod failing, or a Ctrl-C during it once the module
has loaded; systemctl suspend failing; a long sleep, with the hook's post line
coming seconds after the resume; a Ctrl-C during the final save; stdout
closed, as when the terminal's reader has gone; the save after run 1 failing,
which must keep run 2's sleep from starting; the kernel log wrapping, when the
printk:console copy in the instance's trace stands in; the trace instance
failing to be made, or being there already; and the results directory failing
to be made once the instance has been.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "e1-hook", "check")
SELF = os.path.abspath(__file__)

# --- the fake kernel, run inside the sandbox -------------------------------

T = "/sys/kernel/tracing"
INSTANCE = "sleep-drain-e1-hook"  # check's own trace instance, in T/instances/
P = "/sys/module/e1_hook/parameters"
OPS = ("begin", "prepare", "prepare_late", "restore_early", "restore", "end")
EVENTS = ("power/suspend_resume", "power/device_pm_callback_start",
          "power/device_pm_callback_end", "printk/console")
PM_TEST = "[none] core processors platform devices freezer\n"
TRACE_HEAD = """# tracer: nop
#
# entries-in-buffer/entries-written: 0/0   #P:8
#
#           TASK-PID     CPU#  |||||  TIMESTAMP  FUNCTION
#              | |         |   |||||     |         |
"""
# What the main buffer holds before check runs: someone's earlier capture.
MAIN_CAPTURE = TRACE_HEAD + (
    "            bash-1802    [004] .....    42.000100: tracing_mark_write: an earlier session\n"
    "   kworker/u16:2-88      [000] .....    42.000200: suspend_resume: dpm_suspend[2] begin\n")
# The hook's lines for run 2's sleep.
PRE = "2026-11-06T11:30:00+01:00 pre  ts=1 safe=yes snap=20261106T113000\n"
POST = "2026-11-06T11:31:05+01:00 post ts=66 snap=20261106T113000 slept_s=65 valid=no-short\n"


def main_tracing():
    """The main buffer's files, relative to T, as check finds them and must
    leave them: its session is on, recording suspend_resume, on the global
    clock."""
    files = {"trace_clock": "local [global] counter uptime perf mono mono_raw boot tai\n",
             "buffer_size_kb": "1408\n", "tracing_on": "1\n", "trace": MAIN_CAPTURE,
             "trace_marker": "", "per_cpu/cpu0/stats": "entries: 2\noverrun: 0\n"}
    for e in EVENTS:
        files[f"events/{e}/enable"] = "1\n" if e == "power/suspend_resume" else "0\n"
    return files


def ts(us):
    return f"{us // 1_000_000:5d}.{us % 1_000_000:06d}"


def sim_clock(advance):
    path = os.path.join(os.environ["FAKE"], "clock")
    now = int(open(path).read()) if os.path.exists(path) else 100_000_000
    open(path, "w").write(str(now + advance))
    return now + advance


def sim_call(line):
    with open(os.path.join(os.environ["FAKE"], "calls"), "a") as f:
        f.write(line + "\n")


def sim_read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def sim_buffers():
    """The trace buffers: the main one, then each instance."""
    inst = f"{T}/instances"
    names = sorted(os.listdir(inst)) if os.path.isdir(inst) else []
    return [T] + [f"{inst}/{n}" for n in names]


def sim_on(buf, event):
    return sim_read(f"{buf}/tracing_on") == "1" and sim_read(f"{buf}/events/{event}/enable") == "1"


def sim_emit(items):
    """items: (us, "log", text) or (us, "trace", event, msg), into dmesg and
    into each trace buffer that's on and has the event enabled."""
    items.sort(key=lambda i: i[0])
    with open(os.path.join(os.environ["FAKE"], "kmsg.log"), "a") as k:
        for it in items:
            if it[1] == "log":
                k.write(f"[{ts(it[0])}] {it[2]}\n")
    for buf in sim_buffers():
        rows = []
        for it in items:
            if it[1] == "log" and sim_on(buf, "printk/console"):
                rows.append(f"              sh-2063    [003] ..... {ts(it[0])}: console: {it[2]}\n")
            elif it[1] == "trace" and sim_on(buf, "power/" + it[2]):
                rows.append(f"   kworker/u16:7-123     [001] ..... {ts(it[0])}: {it[2]}: {it[3]}\n")
        if rows:
            old = open(f"{buf}/trace").read()
            with open(f"{buf}/trace", "a") as t:
                t.write(("" if "# tracer" in old else TRACE_HEAD) + "".join(rows))


def sim_mkdir_instance(path):
    """mkdir in tracefs's instances/: a new buffer, with its own files as the
    kernel makes them (tracing on, the local clock, no events)."""
    if os.path.exists(os.path.join(os.environ["FAKE"], "instance-fails")):
        sys.exit(f"mkdir: cannot create directory '{path}': Cannot allocate memory")
    try:
        os.mkdir(path)
    except FileExistsError:
        sys.exit(f"mkdir: cannot create directory '{path}': File exists")
    files = {"trace_clock": "[local] global counter uptime perf mono mono_raw boot tai\n",
             "buffer_size_kb": "1408\n", "tracing_on": "1\n", "trace": "", "trace_marker": "",
             "per_cpu/cpu0/stats": "entries: 0\noverrun: 0\n"}
    files.update({f"events/{e}/enable": "0\n" for e in EVENTS})
    for rel, text in files.items():
        write(os.path.join(path, rel), text)
    sim_call(f"mkdir-instance {os.path.basename(path)}")


def sim_rmdir_instance(path):
    """rmdir in instances/: the instance goes, all its files with it. A copy
    of them as they were is kept in $FAKE/removed-instance for the checks."""
    if not os.path.isdir(path):
        sys.exit(f"rmdir: failed to remove '{path}': No such file or directory")
    keep = os.path.join(os.environ["FAKE"], "removed-instance")
    shutil.rmtree(keep, ignore_errors=True)
    shutil.copytree(path, keep)
    shutil.rmtree(path)
    sim_call(f"rmdir-instance {os.path.basename(path)}")


def sim_suspend(kind, post_later=False):
    """post_later: the hook's post line is left to the caller, to come later."""
    kv = {f: open(f"{P}/{f}").read().strip() for f in os.listdir(P)}
    n = {op: int(kv[f"{op}_calls"]) + 1 for op in OPS}
    t = sim_clock(1000)

    def op(us, name, at, read=None):
        s = f"e1_hook: op={name} n={n[name]} t={ts(at).strip()} t_ns={at * 1000 + 321}"
        if read:
            s += " key=B0AV ret={} mv={} read_ns={}".format(*read)
        return (us, "log", s)

    prep, lread = (0, 12640, 450000), (0, 12634, 812000)
    it = [(t, "log", "PM: suspend entry (s2idle)"), op(t + 10, "begin", t + 9),
          (t + 20, "trace", "suspend_resume", "dpm_suspend_late[2] begin"),
          (t + 30, "trace", "suspend_resume", "dpm_suspend_late[2] end"),
          (t + 31, "log", "PM: late suspend of devices complete after 0.011 msecs"),
          op(t + 510, "prepare", t + 60, prep)]
    p = t + 600
    it.append((p, "trace", "suspend_resume", "dpm_suspend_noirq[2] begin"))
    for k, dev in enumerate(("28e080000.power-management:power-controller@1c0", "393cc0000.nvme")):
        s = p + 10 + 100 * k
        it += [(s, "trace", "device_pm_callback_start", f"apple-pmgr {dev}, parent: soc, noirq power domain [suspend]"),
               (s, "log", f"apple-pmgr {dev}: PM: calling genpd_suspend_noirq @ 2063, parent: soc"),
               (s + 40, "trace", "device_pm_callback_end", f"apple-pmgr {dev}, err=0"),
               (s + 40, "log", f"apple-pmgr {dev}: PM: genpd_suspend_noirq returned 0 after 40 usecs")]
    it += [(p + 300, "log", "PM: noirq suspend of devices complete after 0.300 msecs"),
           (p + 302, "trace", "suspend_resume", "dpm_suspend_noirq[2] end")]
    late = p + 305
    it.append(op(late + 820, "prepare_late", late, lread))
    if kind == "platform":
        it.append((late + 900, "log", "PM: suspend debug: Waiting for 5 second(s)."))
        back = late + 5_000_000
    else:
        it += [(late + 900, "log", "PM: suspend-to-idle"),
               (late + 950, "trace", "suspend_resume", "machine_suspend[1] begin"),
               (late + 60_000_000, "trace", "suspend_resume", "machine_suspend[1] end")]
        back = late + 60_000_100
    it += [op(back + 1, "restore_early", back),
           (back + 10, "trace", "suspend_resume", "dpm_resume_noirq[16] begin"),
           op(back + 30, "restore", back + 29), op(back + 40, "end", back + 39),
           (back + 50, "log", "PM: suspend exit")]
    sim_clock(back + 60 - t)
    sim_emit(it)

    kv.update({f"{o}_calls": n[o] for o in OPS})
    kv.update(prepare_t_ns=(t + 60) * 1000 + 321, prepare_ret=prep[0], prepare_mv=prep[1],
              prepare_read_ns=prep[2], late_t_ns=late * 1000 + 321, late_ret=lread[0],
              late_mv=lread[1], late_read_ns=lread[2])
    for k, v in kv.items():
        open(f"{P}/{k}", "w").write(f"{v}\n")
    success = int(open("/sys/power/suspend_stats/success").read()) + 1
    open("/sys/power/suspend_stats/success", "w").write(f"{success}\n")
    if kind == "alarm":
        open("/sys/class/rtc/rtc0/wakealarm", "w").write("")
        open("/sys/power/pm_wakeup_irq", "w").write("145\n")
        with open("/var/log/sleep-drain.log", "a") as f:
            f.write(PRE + ("" if post_later else POST))
        os.makedirs("/var/log/sleep-drain/20261106T113000/pre", exist_ok=True)


def sim(argv):
    what = argv[0]
    if what == "kmsg":
        with open(os.path.join(os.environ["FAKE"], "kmsg.log"), "a") as k:
            k.write(f"[{ts(sim_clock(10))}] {argv[1]}\n")
    elif what == "insmod":
        if os.path.exists(os.path.join(os.environ["FAKE"], "insmod-fails")):
            sys.exit("insmod: ERROR: could not insert module e1_hook.ko: No such device")
        os.makedirs(P)
        kv = {f"{o}_calls": 0 for o in OPS}
        for pre in ("prepare", "late"):
            kv.update({f"{pre}_t_ns": 0, f"{pre}_ret": 1, f"{pre}_mv": 0, f"{pre}_read_ns": 0})
        for k, v in kv.items():
            open(f"{P}/{k}", "w").write(f"{v}\n")
        t = sim_clock(10)
        sim_emit([(t, "log", "e1_hook: op=load key=B0AV ret=0 mv=12650 read_ns=400000"),
                  (t + 5, "log", "e1_hook: loaded: s2idle ops set through s2idle_set_ops+0x0/0x48")])
    elif what == "rmmod":
        shutil.rmtree(os.path.dirname(P))
        sim_emit([(sim_clock(10), "log", "e1_hook: unloaded")])
    elif what == "suspend":
        sim_suspend(argv[1], argv[2:3] == ["late"])
    elif what == "mkdir-instance":
        sim_mkdir_instance(argv[1])
    elif what == "rmdir-instance":
        sim_rmdir_instance(argv[1])


# --- the sandbox ----------------------------------------------------------

def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(text, bytes) else "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def make_world(tmp, case):
    s = os.path.join(tmp, "sys")
    files = {
        "power/mem_sleep": "[s2idle]\n",
        "power/pm_test": PM_TEST,
        "power/pm_debug_messages": "0\n",
        "power/pm_print_times": "0\n",
        "power/suspend_stats/success": "5\n",
        "power/suspend_stats/fail": "0\n",
        "module/suspend/parameters/pm_test_delay": "5\n",
        "module/rtc_macsmc_alarm/refcnt": "1\n",
        "class/rtc/rtc0/wakealarm": "",
        "bus/platform/devices/macsmc-rtc/power/wakeup": "enabled\n",
        "class/wakeup/wakeup0/name": "macsmc-input\n",
        "class/wakeup/wakeup0/event_count": "0\n",
        "class/power_supply/macsmc-battery/capacity": "88\n",
        "class/power_supply/macsmc-battery/status": "Discharging\n",
    }
    files.update({f"kernel/tracing/{rel}": text for rel, text in main_tracing().items()})
    for rel, text in files.items():
        write(os.path.join(s, rel), text)
    os.makedirs(os.path.join(s, "kernel/tracing/instances"))
    if case == "instance-exists":
        # Another check's, or one a SIGKILL left behind: not this check's to remove.
        write(os.path.join(s, "kernel/tracing/instances", INSTANCE, "trace"), "someone else's\n")
    os.symlink("/sys/bus/platform/devices/macsmc-rtc", os.path.join(s, "class/rtc/rtc0/device"))
    os.mkfifo(os.path.join(s, "power/state"))
    os.mkfifo(os.path.join(tmp, "kmsg"))
    write(os.path.join(tmp, "interrupts"),
          "           CPU0       CPU1\n 145:          0          0  apple_spmi   2 Edge      macsmc-rtc\n")
    write(os.path.join(tmp, "device-tree/compatible"), b"apple,j314s\0apple,t6000\0apple,arm-platform\0")
    write(os.path.join(tmp, "run/sleep-drain-safe/active"), "/dev/mapper/root\n")
    write(os.path.join(tmp, "varlog/sleep-drain.log"), "2026-11-05T13:31:38+01:00 post ts=0 an older line\n")
    os.makedirs(os.path.join(tmp, "varlog/sleep-drain/20261105T131638"))
    write(os.path.join(tmp, "kmsg.log"), "[   99.000000] an older line\n")
    if case in ("insmod-fails", "instance-fails"):
        write(os.path.join(tmp, case), "")

    me = f'python3 -I "{SELF}" --sim'
    calls = os.path.join(tmp, "calls")
    suspend = f"/usr/bin/sleep 0.3\n{me} suspend alarm\n"
    if case == "suspend-fails":
        suspend = 'echo "Call to Suspend failed: Access denied" >&2\nexit 1\n'
    elif case == "late-post":
        # A long sleep: date +%s is 400 s on once it's over, and the hook
        # writes its post line 1.5 s after the thaw.
        suspend = (f'/usr/bin/sleep 0.3\n{me} suspend alarm late\n: > "{tmp}/slept"\n'
                   f"( /usr/bin/sleep 1.5; echo '{POST.strip()}' >> /var/log/sleep-drain.log ) > /dev/null 2>&1 &\n")
    # check's pid, for the Ctrl-Cs played below: insmod is its child.
    insmod = f'#!/bin/bash\necho "insmod $*" >> "{calls}"\necho $PPID > "{tmp}/check-pid"\n/usr/bin/sleep 0.3\n'
    if case == "insmod-interrupted":
        # The module loads, then a Ctrl-C reaches check and insmod.
        insmod += f"{me} insmod\nkill -INT $PPID\nexit 130\n"
    else:
        insmod += f"exec {me} insmod\n"
    save = "#!/bin/bash\n"
    if case == "save-interrupted":
        # A Ctrl-C during the final save (no PATH) reaches check and the save.
        save += f'[[ $# -gt 1 ]] || {{ kill -INT "$(< "{tmp}/check-pid")" $$; /usr/bin/sleep 0.3; }}\n'
    save += (f'echo "$* | module $([[ -d /sys/module/e1_hook ]] && echo loaded || echo gone)'
             f' | instance $([[ -e {T}/instances/{INSTANCE} ]] && echo there || echo gone)'
             f' | $(ls /var/log/sleep-drain | tr "\\n" " ")" >> "{calls}"\n')
    if case == "save1-fails":
        # The save after run 1 (the one with a PATH) writes its tar, then
        # can't unmount /boot.
        save += ('if [[ $# -gt 1 ]]; then\n'
                 '  echo "sleep-drain-safe: saved, but /boot is still mounted at'
                 ' /run/sleep-drain-safe/boot: restart now" >&2\n  exit 1\nfi\n')
    save += 'echo "saved to the boot partition (stub)"\n'
    dmesg = f'#!/bin/bash\ncat "{tmp}/kmsg.log"\n'
    if case == "dmesg-wraps":
        # pm_print_times has overrun the kernel's log: only its last lines are
        # left, and never a run's mark.
        dmesg = f'#!/bin/bash\ntail -n 4 "{tmp}/kmsg.log"\n'
    # tracefs: a mkdir or rmdir in instances/ makes or removes a whole instance.
    tracefs = '#!/bin/bash\nif [[ ${{@: -1}} == {T}/instances/* ]]; then exec {me} {op}-instance "${{@: -1}}"; fi\n'
    mkdir = tracefs.format(T=T, me=me, op="mkdir")
    if case == "results-dir-fails":
        # Once the instance is made, the results directory can't be.
        mkdir += ("if [[ ${@: -1} == /var/log/e1-hook-* ]]; then\n"
                  "  echo \"mkdir: cannot create directory '${@: -1}': No space left on device\" >&2\n"
                  "  exit 1\nfi\n")
    stubs = {
        "insmod": insmod,
        "rmmod": f"#!/bin/bash\nexec {me} rmmod\n",
        "dmesg": dmesg,
        "rtcwake": (f'#!/bin/bash\necho "rtcwake $*" >> "{calls}"\necho "rtcwake (stub): $*"\n'
                    'echo 1793960000 > /sys/class/rtc/rtc0/wakealarm\n'),
        "systemctl": f'#!/bin/bash\necho "systemctl $*" >> "{calls}"\n[[ $1 == suspend ]] || exit 0\n{suspend}',
        "sleep": "#!/bin/bash\nexec /usr/bin/sleep 0.6\n",
        "sleep-drain-safe": save,
        "mkdir": mkdir + 'exec /usr/bin/mkdir "$@"\n',
        "rmdir": tracefs.format(T=T, me=me, op="rmdir") + 'exec /usr/bin/rmdir "$@"\n',
    }
    if case == "late-post":
        stubs["date"] = (f'#!/bin/bash\nif [[ $* == "+%s" && -e "{tmp}/slept" ]]; then\n'
                         '  echo $(($(/usr/bin/date +%s) + 400))\nelse\n  exec /usr/bin/date "$@"\nfi\n')
    for name, text in stubs.items():
        write(os.path.join(tmp, "stubs", name), text, 0o755)


def run(case):
    tmp = tempfile.mkdtemp(prefix="e1-check-test-")
    make_world(tmp, case)
    me = f'python3 -I "{SELF}" --sim'
    script = (f'( while :; do while IFS= read -r l; do {me} kmsg "$l"; done < /dev/kmsg; done ) &\n'
              f'( while :; do if read -r c < /sys/power/state; then {me} suspend platform; fi; done ) &\n'
              f'exec /bin/bash "{CHECK}"\n')
    cmd = ["bwrap", "--unshare-user", "--uid", "0", "--unshare-pid", "--die-with-parent", "--clearenv",
           "--ro-bind", "/", "/",
           "--bind", os.path.join(tmp, "sys"), "/sys",
           "--dev", "/dev", "--bind", os.path.join(tmp, "kmsg"), "/dev/kmsg",
           "--tmpfs", "/proc", "--bind", os.path.join(tmp, "interrupts"), "/proc/interrupts",
           "--bind", os.path.join(tmp, "device-tree"), "/proc/device-tree",
           "--bind", os.path.join(tmp, "run"), "/run",
           "--bind", os.path.join(tmp, "varlog"), "/var/log",
           "--ro-bind", os.path.join(tmp, "stubs", "sleep-drain-safe"), "/usr/local/bin/sleep-drain-safe",
           "--bind", tmp, tmp,
           "--setenv", "PATH", os.path.join(tmp, "stubs") + ":/usr/bin",
           "--setenv", "FAKE", tmp, "--setenv", "HOME", tmp,
           "/bin/bash", "-c", script]
    if case == "closed-stdout":
        # A pipe whose reader has gone: every write to it is a SIGPIPE.
        r, w = os.pipe()
        os.close(r)
        try:
            p = subprocess.run(cmd, stdout=w, stderr=w, timeout=120)
        finally:
            os.close(w)
        return tmp, p.returncode, ""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    return tmp, p.returncode, p.stdout


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def main_changes(s):
    """What check changed in the main trace buffer: [] if its settings are as
    they were, and its trace still holds the earlier capture, followed only by
    what its own session records (suspend_resume)."""
    t = os.path.join(s, "kernel/tracing")
    got = {}
    for d, dirs, names in os.walk(t):
        if d == t and "instances" in dirs:
            dirs.remove("instances")
        got.update({os.path.relpath(os.path.join(d, n), t): read(os.path.join(d, n)) for n in names})
    want = main_tracing()
    bad = [f"main buffer: {k} is new" for k in sorted(set(got) - set(want))]
    bad += [f"main buffer: {k} has gone" for k in sorted(set(want) - set(got))]
    bad += [f"main buffer: {k} is {got[k]!r}, not {want[k]!r}" for k in sorted(set(want) & set(got))
            if k != "trace" and got[k] != want[k]]
    trace = got.get("trace", "")
    if not trace.startswith(MAIN_CAPTURE):
        bad.append(f"main buffer: its earlier capture has gone: {trace[:200]!r}")
    elif any(": suspend_resume: " not in l for l in trace[len(MAIN_CAPTURE):].splitlines()):
        bad.append("main buffer: its trace has records its own session didn't make")
    return bad


def restored(tmp, leftover=()):
    """What check left changed: [] if everything is back. leftover: the trace
    instances that were there before check ran, and must still be."""
    s = os.path.join(tmp, "sys")
    want = {"power/pm_test": ("none", PM_TEST.strip()), "power/pm_debug_messages": ("0",),
            "power/pm_print_times": ("0",)}
    bad = [f"{k} is {read(os.path.join(s, k)).strip()!r}" for k, ok in want.items()
           if read(os.path.join(s, k)).strip() not in ok]
    bad += main_changes(s)
    left = sorted(os.listdir(os.path.join(s, "kernel/tracing/instances")))
    if left != sorted(leftover):
        bad.append(f"trace instances afterwards: {left}, not {sorted(leftover)}")
    if os.path.exists(os.path.join(s, "module/e1_hook")):
        bad.append("e1_hook is still loaded")
    if read(os.path.join(s, "class/rtc/rtc0/wakealarm")).strip() not in ("", "0"):
        bad.append("the alarm is still armed")
    return bad


def results(tmp):
    d = os.path.join(tmp, "varlog/sleep-drain")
    found = [n for n in os.listdir(d) if n.startswith("e1-hook-")]
    return os.path.join(d, found[0]) if len(found) == 1 else None


def calls_of(tmp):
    return read(os.path.join(tmp, "calls")).splitlines()


def instance_used(tmp, res, runs):
    """Whether check traced into its instance, set up as it should be: the
    instance as it was when removed, and each run's trace.txt."""
    gone = os.path.join(tmp, "removed-instance")
    problems = []
    if read(os.path.join(gone, "trace_clock")).strip() != "local":
        problems.append("the instance's clock wasn't set to local")
    if read(os.path.join(gone, "buffer_size_kb")).strip() != "8192":
        problems.append("the instance's buffer wasn't set to 8192 KB")
    if "e1-hook check" not in read(os.path.join(gone, "trace_marker")):
        problems.append("the marks didn't go to the instance's trace_marker")
    for run in runs:
        trace = read(os.path.join(res, run, "trace.txt")) if res else ""
        if "an earlier session" in trace or ": console: " not in trace or ": device_pm_callback_end: " not in trace:
            problems.append(f"{run}/trace.txt isn't the instance's trace")
    return problems


def check_pass(tmp, rc, out):
    calls = calls_of(tmp)
    res, saves = results(tmp), [l for l in calls if l.startswith("save")]
    summary = read(os.path.join(res, "summary.txt")) if res else ""
    problems = restored(tmp)
    if rc != 0:
        problems.append(f"exit {rc}")
    if summary.count("=> PASS") != 2 or "Every answer is yes" not in summary:
        problems.append("the summary doesn't have two passes")
    for f in ("log.txt", "params-load.txt", "run1/dmesg.txt", "run1/trace.txt", "run1/verdict.txt",
              "run2/dmesg.txt", "run2/trace.txt", "run2/hook.txt", "run2/params-after.txt"):
        if not res or not os.path.exists(os.path.join(res, f)):
            problems.append(f"no {f}")
    first = (read(os.path.join(res, "run1/dmesg.txt")).splitlines() or [""])[0] if res else ""
    if not first.endswith(": load"):
        problems.append(f"run 1's dmesg doesn't start at the load mark: {first}")
    problems += instance_used(tmp, res, ("run1", "run2"))
    if calls[:1] != [f"mkdir-instance {INSTANCE}"] or calls.count(f"rmdir-instance {INSTANCE}") != 1:
        problems.append(f"the instance wasn't made first and removed once: {calls}")
    if len(saves) != 2 or not saves[0].startswith("save /var/log/e1-hook-") or "module loaded" not in saves[0]:
        problems.append(f"the first save isn't run 1's PATH save: {saves[:1]}")
    elif not (saves[1].startswith("save |") and "module gone" in saves[1] and "instance gone" in saves[1]
              and "e1-hook-" in saves[1]):
        problems.append(f"the last save isn't the whole directory, after the unload and the instance: {saves[1]}")
    if not os.path.exists(os.path.join(tmp, "varlog/sleep-drain/20261106T113000")):
        problems.append("the hook's snapshot isn't there")
    if "pkexec sleep-drain-safe collect" not in out:
        problems.append("it doesn't say to collect")
    return problems


def check_insmod_fails(tmp, rc, out):
    calls = calls_of(tmp)
    res, saves = results(tmp), [l for l in calls if l.startswith("save")]
    problems = restored(tmp)
    if rc == 0:
        problems.append("exit 0")
    if "stopped before both runs were done" not in out:
        problems.append("it doesn't say it stopped early")
    if not res or not os.path.exists(os.path.join(res, "dmesg-at-exit.txt")):
        problems.append("no dmesg-at-exit.txt")
    if not res or not os.path.exists(os.path.join(res, "trace-at-exit.txt")) or \
            "an earlier session" in read(os.path.join(res, "trace-at-exit.txt")):
        problems.append("no trace-at-exit.txt from the instance")
    if "systemctl suspend" in calls:
        problems.append("it suspended anyway")
    if len(saves) != 1 or "e1-hook-" not in saves[0] or "instance gone" not in saves[0]:
        problems.append(f"not one save of what it has, once the instance had gone: {saves}")
    return problems


def check_insmod_interrupted(tmp, rc, out):
    # restored() has it that e1_hook, loaded before the Ctrl-C, is gone.
    problems = check_insmod_fails(tmp, rc, out)
    if rc != 130:
        problems.append(f"exit {rc}, not 130")
    res = results(tmp)
    if "console: e1_hook: loaded" not in (read(os.path.join(res, "trace-at-exit.txt")) if res else ""):
        problems.append("trace-at-exit.txt doesn't have the instance's console copy of the load")
    return problems


def check_closed_stdout(tmp, rc, out):
    # Nothing to read on stdout: the same as a pass, by the files.
    problems = [p for p in check_pass(tmp, rc, out) if p != "it doesn't say to collect"]
    res = results(tmp)
    if "save after run 1 failed" in (read(os.path.join(res, "log.txt")) if res else ""):
        problems.append("log.txt has the save after run 1 failing")
    return problems


def check_suspend_fails(tmp, rc, out):
    res = results(tmp)
    summary = read(os.path.join(res, "summary.txt")) if res else ""
    problems = restored(tmp)
    if rc != 1:
        problems.append(f"exit {rc}, not 1")
    if summary.count("=> PASS") != 1 or "no sleep: systemctl suspend failed" not in summary:
        problems.append("the summary doesn't show run 1 passing and run 2 not sleeping")
    if "Not every answer is yes" not in summary:
        problems.append("the summary claims success")
    return problems


def check_save1_fails(tmp, rc, out):
    calls = calls_of(tmp)
    res, saves = results(tmp), [l for l in calls if l.startswith("save")]
    summary = read(os.path.join(res, "summary.txt")) if res else ""
    problems = restored(tmp)
    if rc != 1:
        problems.append(f"exit {rc}, not 1")
    run2 = [l for l in calls if l.startswith(("rtcwake", "systemctl"))]
    if run2:
        problems.append(f"run 2 started all the same: {run2}")
    if summary.count("=> PASS") != 1 or "Run 2 didn't start" not in summary or "Every answer is yes" in summary:
        problems.append("the summary doesn't show run 1 passing and run 2 not starting")
    if len(saves) != 2 or not saves[0].startswith("save /var/log/e1-hook-") or not (
            saves[1].startswith("save |") and "module gone" in saves[1] and "instance gone" in saves[1]):
        problems.append(f"not the failed save, then the final one once everything was put back: {saves}")
    if "stopped before both runs were done" not in out:
        problems.append("it doesn't say it stopped early")
    return problems


def check_dmesg_wraps(tmp, rc, out):
    # Each run's dmesg.txt is flagged as wrapped, so it doesn't start at the mark.
    problems = [p for p in check_pass(tmp, rc, out) if not p.startswith("run 1's dmesg doesn't start")]
    res = results(tmp)
    summary = read(os.path.join(res, "summary.txt")) if res else ""
    if summary.count("by the kernel log's order: yes: the trace's printk:console copy") != 2:
        problems.append("the trace's printk:console copy didn't stand in for the wrapped log in both runs")
    return problems


def refused(tmp, rc, out, why, leftover=()):
    """check said no before it changed anything: nothing loaded, run or saved."""
    problems = restored(tmp, leftover)
    if rc != 1:
        problems.append(f"exit {rc}, not 1")
    if why not in out:
        problems.append(f"it doesn't say {why!r}")
    if calls_of(tmp):
        problems.append(f"it went on: {calls_of(tmp)}")
    varlog = os.path.join(tmp, "varlog")
    if any(n.startswith("e1-hook-") for d in (varlog, os.path.join(varlog, "sleep-drain")) for n in os.listdir(d)):
        problems.append("it started a results directory")
    return problems


def check_instance_fails(tmp, rc, out):
    return refused(tmp, rc, out, "couldn't create the trace instance")


def check_instance_exists(tmp, rc, out):
    problems = refused(tmp, rc, out, f"{INSTANCE} exists", leftover=(INSTANCE,))
    if read(os.path.join(tmp, "sys/kernel/tracing/instances", INSTANCE, "trace")) != "someone else's\n":
        problems.append("the instance that was there has changed")
    return problems


def check_results_dir_fails(tmp, rc, out):
    # Before finish is in place: the instance is all there is to undo.
    problems = restored(tmp)
    if rc == 0:
        problems.append("exit 0")
    if calls_of(tmp) != [f"mkdir-instance {INSTANCE}", f"rmdir-instance {INSTANCE}"]:
        problems.append(f"not just the instance made and removed again: {calls_of(tmp)}")
    return problems


CASES = [
    ("both runs, every answer yes", None, check_pass),
    ("insmod fails: stops, puts back, saves what it has", "insmod-fails", check_insmod_fails),
    ("Ctrl-C during insmod: unloads what loaded, saves", "insmod-interrupted", check_insmod_interrupted),
    ("systemctl suspend fails: run 2 doesn't count", "suspend-fails", check_suspend_fails),
    ("a long sleep: waits for the post line after the resume", "late-post", check_pass),
    ("Ctrl-C during the final save: it finishes and says so", "save-interrupted", check_pass),
    ("stdout closed: both saves happen all the same", "closed-stdout", check_closed_stdout),
    ("the save after run 1 fails: no run 2, puts back, saves at the end", "save1-fails", check_save1_fails),
    ("the kernel log wraps: the instance's console copy stands in", "dmesg-wraps", check_dmesg_wraps),
    ("no trace instance: refuses before loading anything", "instance-fails", check_instance_fails),
    ("its trace instance is there already: refuses, leaves it", "instance-exists", check_instance_exists),
    ("no results directory: removes the instance it made", "results-dir-fails", check_results_dir_fails),
]

if __name__ == "__main__":
    if sys.argv[1:2] == ["--sim"]:
        sim(sys.argv[2:])
        sys.exit(0)
    probe = subprocess.run(["bwrap", "--unshare-user", "--uid", "0", "--ro-bind", "/", "/", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) \
        if shutil.which("bwrap") else None
    if not probe or probe.returncode != 0:
        print("skip: needs bwrap with unprivileged user namespaces")
        sys.exit(0)
    ko = os.path.join(ROOT, "e1-hook", "e1_hook.ko")
    magic = subprocess.run(["modinfo", "-F", "vermagic", ko], capture_output=True, text=True).stdout.split()
    if magic[:1] != [os.uname().release]:
        print("skip: e1_hook.ko isn't built for this kernel (make -C e1-hook); check refuses without it")
        sys.exit(0)
    missing = [p for p in ("/usr/lib/systemd/system-sleep/sleep-drain-log", "/usr/local/bin/sleep-drain-safe")
               if not os.path.exists(p)]
    if missing:
        print(f"skip: check needs {' and '.join(missing)} (install.sh)")
        sys.exit(0)

    failed = 0
    for name, case, judge in CASES:
        tmp, rc, out = run(case)
        try:
            problems = judge(tmp, rc, out)
        finally:
            shutil.rmtree(tmp)
        print(("ok   " if not problems else "FAIL ") + name)
        if problems:
            failed += 1
            print("     " + "\n     ".join(problems))
            print("".join("     | " + l for l in out.splitlines(True)))
    sys.exit(1 if failed else 0)
