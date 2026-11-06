#!/usr/bin/python3 -I
"""Hardware-free checks for e1-hook/check: the real script, as root in a
bubblewrap sandbox, against a fake /sys, /proc, /run and /var/log.

  python3 -I tests/e1_hook_check_test.py      (from the repo root)

This file is also the fake kernel (--sim). /sys/power/state and /dev/kmsg
are FIFOs it serves: a write of "freeze" plays one pm_test=platform suspend,
and the stub systemctl plays one alarm sleep, each with e1_hook's ops logging
in the kernel's order (prepare, the noirq pass, prepare_late), into the fake
kernel log and trace, with the module's parameters counting. Stubs stand in
for insmod, rmmod, rtcwake, dmesg, sleep and sleep-drain-safe. /run is a fake
too, so nothing in the sandbox can reach the real D-Bus (logind would suspend
for the real systemctl).

Each case checks the exit status, the answers, that everything check changed
is put back (pm_test, pm_debug_messages, pm_print_times, the trace clock,
buffer and events, e1_hook unloaded) and the saves: one after run 1 with the
results as a PATH, one at the end with them in /var/log/sleep-drain/.
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
P = "/sys/module/e1_hook/parameters"
OPS = ("begin", "prepare", "prepare_late", "restore_early", "restore", "end")
TRACE_HEAD = """# tracer: nop
#
# entries-in-buffer/entries-written: 0/0   #P:8
#
#           TASK-PID     CPU#  |||||  TIMESTAMP  FUNCTION
#              | |         |   |||||     |         |
"""


def ts(us):
    return f"{us // 1_000_000:5d}.{us % 1_000_000:06d}"


def sim_clock(advance):
    path = os.path.join(os.environ["FAKE"], "clock")
    now = int(open(path).read()) if os.path.exists(path) else 100_000_000
    open(path, "w").write(str(now + advance))
    return now + advance


def sim_on(event):
    def read(p):
        return open(p).read().strip()
    return read(f"{T}/tracing_on") == "1" and read(f"{T}/events/{event}/enable") == "1"


def sim_emit(items):
    """items: (us, "log", text) or (us, "trace", event, msg), into dmesg and the trace."""
    items.sort(key=lambda i: i[0])
    with open(os.path.join(os.environ["FAKE"], "kmsg.log"), "a") as k:
        for it in items:
            if it[1] == "log":
                k.write(f"[{ts(it[0])}] {it[2]}\n")
    old = open(f"{T}/trace").read()
    with open(f"{T}/trace", "a") as t:
        if "# tracer" not in old:
            t.write(TRACE_HEAD)
        for it in items:
            if it[1] == "log" and sim_on("printk/console"):
                t.write(f"              sh-2063    [003] ..... {ts(it[0])}: console: {it[2]}\n")
            elif it[1] == "trace" and sim_on("power/" + it[2]):
                t.write(f"   kworker/u16:7-123     [001] ..... {ts(it[0])}: {it[2]}: {it[3]}\n")


def sim_suspend(kind):
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
            f.write("2026-11-06T11:30:00+01:00 pre  ts=1 safe=yes snap=20261106T113000\n"
                    "2026-11-06T11:31:05+01:00 post ts=66 snap=20261106T113000 slept_s=65 valid=no-short\n")
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
        sim_suspend(argv[1])


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
        "power/pm_test": "[none] core processors platform devices freezer\n",
        "power/pm_debug_messages": "0\n",
        "power/pm_print_times": "0\n",
        "power/suspend_stats/success": "5\n",
        "power/suspend_stats/fail": "0\n",
        "kernel/tracing/trace_clock": "local [global] counter uptime perf mono mono_raw boot tai\n",
        "kernel/tracing/buffer_size_kb": "7 (expanded: 1408)\n",
        "kernel/tracing/tracing_on": "1\n",
        "kernel/tracing/trace": "",
        "kernel/tracing/trace_marker": "",
        "kernel/tracing/per_cpu/cpu0/stats": "entries: 0\noverrun: 0\n",
        "module/suspend/parameters/pm_test_delay": "5\n",
        "module/rtc_macsmc_alarm/refcnt": "1\n",
        "class/rtc/rtc0/wakealarm": "",
        "bus/platform/devices/macsmc-rtc/power/wakeup": "enabled\n",
        "class/wakeup/wakeup0/name": "macsmc-input\n",
        "class/wakeup/wakeup0/event_count": "0\n",
        "class/power_supply/macsmc-battery/capacity": "88\n",
        "class/power_supply/macsmc-battery/status": "Discharging\n",
    }
    for e in ("power/suspend_resume", "power/device_pm_callback_start",
              "power/device_pm_callback_end", "printk/console"):
        files[f"kernel/tracing/events/{e}/enable"] = "0\n"
    for rel, text in files.items():
        write(os.path.join(s, rel), text)
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
    if case == "insmod-fails":
        write(os.path.join(tmp, "insmod-fails"), "")

    me = f'python3 -I "{SELF}" --sim'
    calls = os.path.join(tmp, "calls")
    suspend = f"/usr/bin/sleep 0.3\n{me} suspend alarm\n" if case != "suspend-fails" else \
        'echo "Call to Suspend failed: Access denied" >&2\nexit 1\n'
    stubs = {
        "insmod": f"#!/bin/bash\n/usr/bin/sleep 0.3\nexec {me} insmod\n",
        "rmmod": f"#!/bin/bash\nexec {me} rmmod\n",
        "dmesg": f'#!/bin/bash\ncat "{tmp}/kmsg.log"\n',
        "rtcwake": '#!/bin/bash\necho "rtcwake (stub): $*"\necho 1793960000 > /sys/class/rtc/rtc0/wakealarm\n',
        "systemctl": f'#!/bin/bash\necho "systemctl $*" >> "{calls}"\n[[ $1 == suspend ]] || exit 0\n{suspend}',
        "sleep": "#!/bin/bash\nexec /usr/bin/sleep 0.6\n",
        "sleep-drain-safe": f'#!/bin/bash\necho "$* | module $([[ -d /sys/module/e1_hook ]] && echo loaded'
                            f' || echo gone) | $(ls /var/log/sleep-drain | tr "\\n" " ")" >> "{calls}"\n',
    }
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
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    return tmp, p.returncode, p.stdout


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def restored(tmp):
    """What check left changed: [] if everything is back."""
    s = os.path.join(tmp, "sys")
    want = {"power/pm_test": "none", "power/pm_debug_messages": "0", "power/pm_print_times": "0",
            "kernel/tracing/trace_clock": "global", "kernel/tracing/buffer_size_kb": "1408",
            "kernel/tracing/tracing_on": "1"}
    for e in ("power/suspend_resume", "power/device_pm_callback_start",
              "power/device_pm_callback_end", "printk/console"):
        want[f"kernel/tracing/events/{e}/enable"] = "0"
    bad = [f"{k} is {read(os.path.join(s, k)).strip()!r}" for k, v in want.items()
           if read(os.path.join(s, k)).strip() != v]
    if os.path.exists(os.path.join(s, "module/e1_hook")):
        bad.append("e1_hook is still loaded")
    if read(os.path.join(s, "class/rtc/rtc0/wakealarm")).strip() not in ("", "0"):
        bad.append("the alarm is still armed")
    return bad


def results(tmp):
    d = os.path.join(tmp, "varlog/sleep-drain")
    found = [n for n in os.listdir(d) if n.startswith("e1-hook-")]
    return os.path.join(d, found[0]) if len(found) == 1 else None


def check_pass(tmp, rc, out):
    res, saves = results(tmp), [l for l in read(os.path.join(tmp, "calls")).splitlines() if l.startswith("save")]
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
    if len(saves) != 2 or not saves[0].startswith("save /var/log/e1-hook-") or "module loaded" not in saves[0]:
        problems.append(f"the first save isn't run 1's PATH save: {saves[:1]}")
    elif not (saves[1].startswith("save |") and "module gone" in saves[1] and "e1-hook-" in saves[1]):
        problems.append(f"the last save isn't the whole directory, after the unload: {saves[1]}")
    if not os.path.exists(os.path.join(tmp, "varlog/sleep-drain/20261106T113000")):
        problems.append("the hook's snapshot isn't there")
    if "pkexec sleep-drain-safe collect" not in out:
        problems.append("it doesn't say to collect")
    return problems


def check_insmod_fails(tmp, rc, out):
    res, saves = results(tmp), [l for l in read(os.path.join(tmp, "calls")).splitlines() if l.startswith("save")]
    problems = restored(tmp)
    if rc == 0:
        problems.append("exit 0")
    if "stopped before both runs were done" not in out:
        problems.append("it doesn't say it stopped early")
    if not res or not os.path.exists(os.path.join(res, "dmesg-at-exit.txt")):
        problems.append("no dmesg-at-exit.txt")
    if "systemctl suspend" in read(os.path.join(tmp, "calls")):
        problems.append("it suspended anyway")
    if len(saves) != 1 or "e1-hook-" not in saves[0]:
        problems.append(f"not one save of what it has: {saves}")
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


CASES = [
    ("both runs, every answer yes", None, check_pass),
    ("insmod fails: stops, puts back, saves what it has", "insmod-fails", check_insmod_fails),
    ("systemctl suspend fails: run 2 doesn't count", "suspend-fails", check_suspend_fails),
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
