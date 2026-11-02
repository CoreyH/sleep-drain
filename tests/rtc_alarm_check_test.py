#!/usr/bin/python3 -I
"""Hardware-free checks for rtc-alarm-test/check: the real script, as root in a
bubblewrap sandbox, against a fake /sys and /proc/interrupts.

  python3 -I tests/rtc_alarm_check_test.py      (from the repo root)

A stub rtcwake plays one outcome per call, as the kernel would leave things:
an alarm wake (the RTC's IRQ counts once and is pm_wakeup_irq), a Touch ID key
wake (macsmc-input counts events, no wakeup IRQ), a failure before suspending
(nothing changes, so pm_wakeup_irq still names the previous sleep's wake), or
a failed suspend (suspend_stats fail goes up). Each case checks the tally.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "rtc-alarm-test", "check")
IRQ = 144

RTCWAKE = r'''#!/bin/bash
# Fake rtcwake: plays the next line of $FAKE/outcomes.
S=/sys; P=$S/power
read -r what < "$FAKE/outcomes"; sed -i 1d "$FAKE/outcomes"
bump() { echo $(( $(cat "$1") + ${2:-1} )) > "$1"; }
case $what in
  alarm)
    awk -v irq="IRQ:" '$1 == irq { $2 += 1 } { print }' "$FAKE/interrupts" > "$FAKE/interrupts.new"
    cat "$FAKE/interrupts.new" > "$FAKE/interrupts"  # in place: it's bind-mounted
    echo IRQ > $P/pm_wakeup_irq; bump $S/class/wakeup/wakeup1/event_count; bump $P/suspend_stats/success ;;
  key)
    rm -f $P/pm_wakeup_irq; bump $S/class/wakeup/wakeup0/event_count 2; bump $P/suspend_stats/success ;;
  fail-setup)
    echo "rtcwake: set rtc wake alarm failed: Device or resource busy" >&2; exit 1 ;;
  fail-suspend)
    bump $P/suspend_stats/fail; echo "rtcwake: write error" >&2; exit 1 ;;
  *) echo "fake rtcwake: no outcome left" >&2; exit 2 ;;
esac
'''.replace("IRQ", str(IRQ))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def make_world(tmp, outcomes):
    """The fake /sys, /proc/interrupts, stubs, and a copy of check."""
    s = os.path.join(tmp, "sys")
    rtc = os.path.join(s, "class/rtc/rtc0")
    dev = os.path.join(s, "bus/platform/devices/macsmc-rtc")
    drv = os.path.join(s, "bus/platform/drivers/rtc_macsmc_alarm")
    os.makedirs(os.path.join(s, "module/rtc_macsmc_alarm"))
    os.makedirs(drv)
    os.makedirs(dev)
    os.symlink("/sys/module/rtc_macsmc_alarm", os.path.join(drv, "module"))
    os.symlink("/sys/bus/platform/drivers/rtc_macsmc_alarm", os.path.join(dev, "driver"))
    write(os.path.join(rtc, "wakealarm"), "")
    write(os.path.join(rtc, "since_epoch"), "1000\n")
    os.symlink("/sys/bus/platform/devices/macsmc-rtc", os.path.join(rtc, "device"))
    for i, name in enumerate(("macsmc-input", "macsmc-rtc")):
        write(os.path.join(s, f"class/wakeup/wakeup{i}/name"), name + "\n")
        write(os.path.join(s, f"class/wakeup/wakeup{i}/event_count"), "0\n")
    write(os.path.join(s, "power/suspend_stats/success"), "0\n")
    write(os.path.join(s, "power/suspend_stats/fail"), "0\n")

    zeros = "          0" * 8
    write(os.path.join(tmp, "interrupts"),
          "           CPU0       CPU1       CPU2       CPU3       CPU4       CPU5       CPU6       CPU7\n"
          f" 11:{zeros}  AIC2   0 Level     arch_timer\n"
          f"{IRQ}:{zeros}  apple_spmi   2 Edge      macsmc-rtc\n")
    write(os.path.join(tmp, "outcomes"), "".join(o + "\n" for o in outcomes))

    stubs = os.path.join(tmp, "stubs")
    write(os.path.join(stubs, "rtcwake"), RTCWAKE)
    write(os.path.join(stubs, "sleep"), "#!/bin/sh\n")
    write(os.path.join(tmp, "pmu-alarm"), "#!/bin/sh\necho 'ctrl 0x01 event 0x00'\n")
    for f in ("stubs/rtcwake", "stubs/sleep", "pmu-alarm"):
        os.chmod(os.path.join(tmp, f), 0o755)

    test = os.path.join(tmp, "rtc-alarm-test")
    os.makedirs(test)
    shutil.copy(CHECK, test)
    return os.path.join(test, "check")


def run(outcomes, check=None):
    tmp = tempfile.mkdtemp(prefix="check-test-")
    try:
        script = make_world(tmp, outcomes)
        if check:
            shutil.copy(check, script)
        cmd = ["bwrap", "--unshare-user", "--uid", "0", "--ro-bind", "/", "/",
               "--bind", os.path.join(tmp, "sys"), "/sys", "--dev", "/dev",
               "--tmpfs", "/proc", "--bind", os.path.join(tmp, "interrupts"), "/proc/interrupts",
               "--bind", tmp, tmp, "--setenv", "PATH", os.path.join(tmp, "stubs") + ":/usr/bin",
               "--setenv", "FAKE", tmp,
               "/bin/bash", script, str(len(outcomes)), "10"]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return p.returncode, p.stdout
    finally:
        shutil.rmtree(tmp)


def tally(out):
    m = re.search(r"== (\d+) attempts: (\d+) ended on the alarm, (\d+) on the Touch ID/power key, "
                  r"(\d+) on something else; (\d+) didn't suspend", out)
    return tuple(int(x) for x in m.groups()) if m else None


CASES = [
    # outcomes, (attempts, alarm, key, other, didn't suspend), exit 0?, also in the output
    (["alarm", "alarm", "alarm"], (3, 3, 0, 0, 0), True, None),
    (["alarm", "key"], (2, 1, 1, 0, 0), True, "on the Touch ID/power key"),
    (["alarm", "fail-setup"], (2, 1, 0, 0, 1), False, "rtcwake said: rtcwake: set rtc wake alarm failed"),
    (["alarm", "fail-suspend"], (2, 1, 0, 0, 1), False, "suspend_stats success 1 -> 1"),
]

if __name__ == "__main__":
    probe = subprocess.run(["bwrap", "--unshare-user", "--uid", "0", "--ro-bind", "/", "/", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) \
        if shutil.which("bwrap") else None
    if not probe or probe.returncode != 0:
        print("skip: needs bwrap with unprivileged user namespaces")
        sys.exit(0)

    failed = 0
    for outcomes, want, exit_ok, needle in CASES:
        rc, out = run(outcomes, *sys.argv[1:2])
        ok = tally(out) == want and (rc == 0) == exit_ok and (needle is None or needle in out)
        print(("ok   " if ok else "FAIL ") + ", ".join(outcomes) + f": {want[1]} alarm, {want[4]} didn't suspend")
        if not ok:
            failed += 1
            print(f"     exit {rc}, tally {tally(out)}")
            print("".join("     " + l for l in out.splitlines(True)))
    sys.exit(1 if failed else 0)
