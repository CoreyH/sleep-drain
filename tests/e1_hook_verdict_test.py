#!/usr/bin/python3 -I
"""Hardware-free checks for e1-hook/verdict, the answers e1-hook/check gives
for each run: made-up run directories (info, the module's parameters, dmesg,
an ftrace dump in the kernel's own format) for a pm_test=platform run and an
alarm sleep, with one thing wrong per case.

  python3 -I tests/e1_hook_verdict_test.py      (from the repo root)

The good runs follow suspend_enter()'s order: prepare, the noirq pass (three
genpd callbacks, logged by pm_print_times and traced), "noirq suspend of
devices complete", dpm_suspend_noirq's end event, then prepare_late 3 us
later, then suspend_test's wait or s2idle_loop. The bad ones move
prepare_late into the pass, fail the SMC read, lose trace events, wrap the
kernel log (the trace's printk:console copy then stands in), and so on.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERDICT = os.path.join(ROOT, "e1-hook", "verdict")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


verdict = load("e1_verdict", VERDICT)

S = 1_000_000  # us per second
BASE = 1000 * S
DEVICES = ["28e080000.power-management:power-controller@1c0",
           "39b028000.pinctrl", "393cc0000.nvme"]
TRACE_HEAD = """# tracer: nop
#
# entries-in-buffer/entries-written: {n}/{w}   #P:8
#
#                                _-----=> irqs-off/BH-disabled
#                               / _----=> need-resched
#                              | / _---=> hardirq/softirq
#                              || / _--=> preempt-depth
#                              ||| / _-=> migrate-disable
#                              |||| /     delay
#           TASK-PID     CPU#  |||||  TIMESTAMP  FUNCTION
#              | |         |   |||||     |         |
"""


def ts(us):
    return f"{us // S:5d}.{us % S:06d}"


class Run:
    """One run's kernel log and trace, built in time order."""

    def __init__(self):
        self.items = []  # (us, "log", text) or (us, "trace", event, msg, task)

    def log(self, us, text):
        self.items.append((us, "log", text))

    def ev(self, us, event, msg, task="sh-2063"):
        self.items.append((us, "trace", event, msg, task))

    def op(self, us, op, n, t_us, read=None):
        line = f"e1_hook: op={op} n={n} t={ts(t_us).strip()} t_ns={t_us * 1000 + 123}"
        if read:
            line += " key=B0AV ret={} mv={} read_ns={}".format(*read)
        self.log(us, line)

    def dmesg(self, since=None, wrapped=False):
        out = ["# e1-hook check: \"...\" isn't in the kernel log any more (it wrapped); all of it follows"] \
            if wrapped else []
        for it in sorted(self.items, key=lambda i: i[0]):
            if it[1] == "log" and (since is None or it[0] >= since):
                out.append(f"[{ts(it[0])}] {it[2]}")
        return "\n".join(out) + "\n"

    def trace(self, console=True, lost=0):
        rows = []
        for it in sorted(self.items, key=lambda i: i[0]):
            if it[1] == "trace":
                us, _, event, msg, task = it
            elif console:
                us, _, msg = it
                event, task = "console", "sh-2063"
            else:
                continue
            name, pid = task.rsplit("-", 1)
            rows.append(f"{name:>16}-{pid:<7} [{us % 7:03d}] ..... {ts(us)}: {event}: {msg}")
        n = len(rows)
        return TRACE_HEAD.format(n=n, w=n + lost) + "\n".join(rows) + "\n"


def build(kind="platform", late_at=None, smc=(0, 12634, 812345), stray=False, early_s2idle=False):
    """A run, and prepare_late's time in us. late_at moves prepare_late."""
    r = Run()
    t = BASE
    r.log(t, "e1-hook check 20261106T113000: load")
    r.ev(t, "tracing_mark_write", "e1-hook check 20261106T113000: load")
    r.op(t + 10, "load", 0, t + 9)
    r.log(t + 20, "PM: suspend entry (s2idle)")
    r.ev(t + 21, "suspend_resume", "suspend_enter[1] begin")
    r.op(t + 30, "begin", 1, t + 29)
    r.ev(t + 40, "suspend_resume", "dpm_suspend[2] begin")
    r.ev(t + 50, "device_pm_callback_start", "apple-dcp 38bc00000.dcp, parent: soc, driver [suspend]")
    r.log(t + 50, "apple-dcp 38bc00000.dcp: PM: calling dcp_platform_suspend [apple_dcp] @ 2063, parent: soc")
    r.ev(t + 90, "device_pm_callback_end", "apple-dcp 38bc00000.dcp, err=0")
    r.log(t + 90, "apple-dcp 38bc00000.dcp: PM: dcp_platform_suspend [apple_dcp] returned 0 after 40 usecs")
    r.ev(t + 100, "suspend_resume", "dpm_suspend[2] end")
    r.log(t + 100, "PM: suspend of devices complete after 0.060 msecs")
    r.ev(t + 110, "suspend_resume", "dpm_suspend_late[2] begin")
    r.ev(t + 120, "suspend_resume", "dpm_suspend_late[2] end")
    r.log(t + 121, "PM: late suspend of devices complete after 0.011 msecs")
    r.op(t + 600, "prepare", 1, t + 130, (0, 12640, 470000))

    p = t + 1000  # the noirq pass
    r.ev(p, "suspend_resume", "dpm_suspend_noirq[2] begin")
    for k, dev in enumerate(DEVICES):
        s = p + 100 + 200 * k
        task = "kworker/u16:7-123" if k == 1 else "sh-2063"
        r.ev(s, "device_pm_callback_start", f"apple-pmgr {dev}, parent: soc, noirq power domain [suspend]", task)
        r.log(s, f"apple-pmgr {dev}: PM: calling genpd_suspend_noirq @ 2063, parent: soc")
        r.ev(s + 50, "device_pm_callback_end", f"apple-pmgr {dev}, err=0", task)
        r.log(s + 50, f"apple-pmgr {dev}: PM: genpd_suspend_noirq returned 0 after 50 usecs")
    done = p + 800
    r.log(done, "PM: noirq suspend of devices complete after 0.800 msecs")
    r.ev(done + 2, "suspend_resume", "dpm_suspend_noirq[2] end")
    if stray:
        r.ev(done + 3, "device_pm_callback_end", "apple-pmgr 39b028000.pinctrl, err=0")

    late = done + 5 if late_at is None else late_at
    r.op(late + 800 if late_at is None else late + 1, "prepare_late", 1, late, smc)
    if kind == "platform":
        r.log(late + 900, "PM: suspend debug: Waiting for 5 second(s).")
        back = late + 5 * S
    else:
        r.log(late + 900, "PM: suspend-to-idle")
        s2i = late + 950 if not early_s2idle else p - 10
        r.ev(s2i, "suspend_resume", "machine_suspend[1] begin", "<idle>-0")
        r.ev(late + 60 * S, "suspend_resume", "machine_suspend[1] end", "<idle>-0")
        r.log(late + 60 * S + 10, "PM: resume from suspend-to-idle")
        back = late + 60 * S + 20
    r.op(back + 1, "restore_early", 1, back)
    r.ev(back + 10, "suspend_resume", "dpm_resume_noirq[16] begin")
    r.ev(back + 20, "device_pm_callback_start", f"apple-pmgr {DEVICES[0]}, parent: soc, noirq power domain [resume]")
    r.ev(back + 30, "device_pm_callback_end", f"apple-pmgr {DEVICES[0]}, err=0")
    r.ev(back + 40, "suspend_resume", "dpm_resume_noirq[16] end")
    r.log(back + 41, "PM: noirq resume of devices complete after 0.031 msecs")
    r.op(back + 50, "restore", 1, back + 49)
    r.op(back + 90, "end", 1, back + 89)
    r.log(back + 100, "PM: suspend exit")
    return r, late, smc


def params(calls, late=None, smc=None):
    lines = [f"{op}_calls={calls}" for op in verdict.OPS]
    if late is None:
        lines += ["late_t_ns=0", "late_ret=1", "late_mv=0", "late_read_ns=0",
                  "prepare_t_ns=0", "prepare_ret=1", "prepare_mv=0", "prepare_read_ns=0"]
    else:
        lines += [f"late_t_ns={late * 1000 + 123}", f"late_ret={smc[0]}", f"late_mv={smc[1]}",
                  f"late_read_ns={smc[2]}", f"prepare_t_ns={(BASE + 130) * 1000}", "prepare_ret=0",
                  "prepare_mv=12640", "prepare_read_ns=470000"]
    return "\n".join(lines) + "\n"


def write_run(d, kind, run, late, smc, ran=True, dmesg=None, trace=None):
    os.makedirs(d)
    files = {
        "info.txt": f"kind={kind}\nlabel=test {kind}\n",
        "params-before.txt": params(0),
        "params-after.txt": params(1, late, smc) if ran else params(0),
        "dmesg.txt": run.dmesg() if dmesg is None else dmesg,
        "trace.txt": run.trace() if trace is None else trace,
    }
    for name, text in files.items():
        with open(os.path.join(d, name), "w") as f:
            f.write(text)


def case_dirs(tmp):
    """(name, dir, {question number: outcome}, passes)"""
    cases = []

    def add(name, want, passes, kind="platform", ran=True, dmesg=None, trace=None, **kw):
        run, late, smc = build(kind, **kw)
        d = os.path.join(tmp, str(len(cases)))
        write_run(d, kind, run, late, smc, ran,
                  dmesg(run, late) if dmesg else None, trace(run) if trace else None)
        cases.append((name, d, want, passes))

    yes = {"1": "yes", "2a": "yes", "2b": "yes", "3": "yes", "4": "yes"}
    add("pm_test=platform, prepare_late after the noirq pass", yes, True)
    add("alarm sleep, prepare_late after the noirq pass, then s2idle", yes, True, kind="alarm")
    add("prepare_late inside the noirq pass, before its last callback",
        {"1": "yes", "2a": "no", "2b": "no"}, False, late_at=BASE + 1000 + 300)
    add("prepare_late before the noirq pass began", {"2a": "no", "2b": "no"}, False,
        late_at=BASE + 900)
    add("a device callback between the pass's end and prepare_late", {"2a": "no", "2b": "yes"}, False,
        stray=True)
    add("the SMC read timed out", {"1": "yes", "2a": "yes", "4": "no"}, False, smc=(-110, 0, 500000000))
    add("the SMC read returned nonsense", {"4": "no"}, False, smc=(0, 65535, 800000))
    add("prepare_late didn't run", {"1": "no", "2a": "unknown", "4": "unknown"}, False, ran=False)
    add("dmesg wrapped: the trace's console copy stands in", yes, True,
        dmesg=lambda run, late: run.dmesg(since=late + 500, wrapped=True))
    add("dmesg wrapped and no console copy", {"2a": "yes", "2b": "unknown"}, False,
        dmesg=lambda run, late: run.dmesg(since=late + 500, wrapped=True),
        trace=lambda run: run.trace(console=False))
    add("the trace lost events", {"2a": "unknown", "2b": "yes"}, False,
        trace=lambda run: run.trace(lost=40))
    add("alarm sleep with s2idle entered before prepare_late", {"2a": "yes", "3": "no"}, False,
        kind="alarm", early_s2idle=True)
    add("no pm_print_times lines in the noirq pass", {"2a": "yes", "2b": "unknown"}, False,
        dmesg=lambda run, late: "".join(l + "\n" for l in run.dmesg().splitlines() if "PM: calling" not in l
                                        and "returned" not in l),
        trace=lambda run: run.trace(console=False))
    return cases


def main():
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, d, want, passes in case_dirs(tmp):
            info, answers = verdict.judge(d)
            got = {a.question.split(".")[0]: a.outcome for a in answers}
            text, passed = verdict.report(info, answers)
            ok = passed == passes and all(got.get(k) == v for k, v in want.items())
            print(("ok   " if ok else "FAIL ") + name)
            if not ok:
                failed += 1
                print(f"     want {want}, pass {passes}\n     got  {got}, pass {passed}")
                print("     " + text.replace("\n", "\n     "))

        # The command line, whose exit status check relies on: a pass, a
        # failure, and no directory.
        for name, d, _, passes in case_dirs(os.path.join(tmp, "cli"))[:3:2]:
            p = subprocess.run([sys.executable, "-I", VERDICT, d], capture_output=True, text=True)
            want = 0 if passes else 1
            ok = p.returncode == want and ("=> PASS" in p.stdout) == passes and not p.stderr
            print(("ok   " if ok else "FAIL ") + f"verdict exits {want}: {name}")
            if not ok:
                failed += 1
                print(f"     exit {p.returncode}\n{p.stdout}{p.stderr}")
        p = subprocess.run([sys.executable, "-I", VERDICT], capture_output=True, text=True)
        ok = p.returncode == 2 and "usage" in p.stderr
        print(("ok   " if ok else "FAIL ") + "verdict without a directory exits 2")
        failed += not ok
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
