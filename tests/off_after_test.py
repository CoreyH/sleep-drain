#!/usr/bin/env python3
"""sleep-drain-off-after, sleep-drain-nights and sleep-drain-alarm.service, without hardware.

The hook runs as it is, with every path and command it uses pointed (OFF_AFTER_*) into a temp
directory: the flag, the state file, the charger, two RTCs (rtc0 is a decoy that rtc_macsmc
drives, rtc1 is the alarm RTC), the lid (a file that `cat` prints), and stubs for the power-off,
the sleep between checks and what starts "finish". The stubs record their calls. The sleep stub
can change the lid or the charger at its Nth one-second sleep, which is how a lid that opens
during the re-check is played. The fake wakealarm is a plain file: it keeps what was written, so a
cleared alarm reads 0 where the kernel's reads empty.

Then the unit file's conditions against what the safe boot's initramfs hook really does, and
install.sh and uninstall.sh against the files this adds. sleep-drain-nights runs with stub sudo,
systemctl and journalctl.

  python3 -I -B tests/off_after_test.py
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "sleep-drain-off-after")
NIGHTS = os.path.join(ROOT, "sleep-drain-nights")
UNIT = os.path.join(ROOT, "sleep-drain-alarm.service")
T = 1_800_000_000

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("".join("     " + l for l in str(detail).splitlines(True)))


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return None


class World:
    """A fake Mac in a temp directory, on battery with the lid closed and the flag at 25."""

    def __init__(self, tmp, minutes="25\n", ac="0", lid="true", now=T):
        self.tmp = tmp
        self.flag = os.path.join(tmp, "etc/off-after")
        self.state_path = os.path.join(tmp, "run/state")
        self.safe = os.path.join(tmp, "run/safe/active")
        self.ac = os.path.join(tmp, "ac/online")
        self.lid = os.path.join(tmp, "lid")
        self.log = os.path.join(tmp, "log/off-after.log")
        self.calls_path = os.path.join(tmp, "calls")
        self.decoy = os.path.join(tmp, "rtc/rtc0")
        self.rtc = os.path.join(tmp, "rtc/rtc1")
        self.extra_env = {}
        if minutes is not None:
            write(self.flag, minutes)
        os.makedirs(os.path.join(tmp, "run"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "log"), exist_ok=True)
        write(self.ac, ac + "\n")
        write(self.lid, lid + "\n")
        for rtc, module, epoch in ((self.decoy, "rtc_macsmc", 12345), (self.rtc, "rtc_macsmc_alarm", now)):
            os.makedirs(os.path.join(tmp, "module", module), exist_ok=True)
            os.makedirs(os.path.join(rtc, "device/driver"))
            os.symlink(os.path.join(tmp, "module", module), os.path.join(rtc, "device/driver/module"))
            write(os.path.join(rtc, "wakealarm"), "")
            write(os.path.join(rtc, "since_epoch"), f"{epoch}\n")
        stubs = os.path.join(tmp, "stubs")
        write(os.path.join(stubs, "sleep"), f"""#!/bin/sh
echo "sleep $1" >> {self.calls_path}
n=$(grep -c '^sleep 1$' {self.calls_path})
if [ "$1" = 1 ] && [ -f {tmp}/on-sleep-$n ]; then . {tmp}/on-sleep-$n; fi
exit 0
""", 0o755)
        write(os.path.join(stubs, "poweroff"), f"""#!/bin/sh
echo "poweroff$*" >> {self.calls_path}
n=$(grep -c '^poweroff' {self.calls_path})
if [ -f {tmp}/poweroff-fails ]; then echo "Call to PowerOff failed: in progress" >&2; exit 1; fi
if [ -f {tmp}/poweroff-ok-at ] && [ "$n" -lt "$(cat {tmp}/poweroff-ok-at)" ]; then echo refused >&2; exit 1; fi
exit 0
""", 0o755)
        write(os.path.join(stubs, "detach"), f"""#!/bin/sh
echo "detach $*" >> {self.calls_path}
[ -f {tmp}/detach-fails ] && exit 1
[ -f {tmp}/detach-noop ] && exit 0
exec bash "$@"
""", 0o755)
        self.stubs = stubs

    def env(self):
        e = {
            "PATH": "/usr/bin:/bin", "TZ": "UTC",
            "OFF_AFTER_FLAG": self.flag, "OFF_AFTER_STATE": self.state_path, "OFF_AFTER_SAFE": self.safe,
            "OFF_AFTER_AC": self.ac, "OFF_AFTER_RTC_GLOB": os.path.join(self.tmp, "rtc/rtc*"),
            "OFF_AFTER_LOG": self.log, "OFF_AFTER_LID": f"cat {self.lid}",
            "OFF_AFTER_POWEROFF": os.path.join(self.stubs, "poweroff"),
            "OFF_AFTER_SLEEP": os.path.join(self.stubs, "sleep"),
            "OFF_AFTER_DETACH": os.path.join(self.stubs, "detach"),
        }
        e.update(self.extra_env)
        return e

    def run(self, phase, now=None):
        if now is not None:
            write(os.path.join(self.rtc, "since_epoch"), f"{now}\n")
        return subprocess.run(["bash", HOOK, phase, "suspend"], env=self.env(), capture_output=True,
                              text=True, timeout=60)

    def pre(self, now=None):
        return self.run("pre", now)

    def post(self, now=None):
        return self.run("post", now)

    def wake(self):
        w = read(os.path.join(self.rtc, "wakealarm"))
        return None if w is None else w.strip()

    def set_wake(self, value):
        write(os.path.join(self.rtc, "wakealarm"), f"{value}\n" if value != "" else "")

    def fire(self):
        """The alarm went off: the kernel's wakealarm then reads empty."""
        self.set_wake("")

    def state(self):
        s = read(self.state_path)
        if s is None:
            return None
        return dict(l.split("=", 1) for l in s.splitlines() if "=" in l)

    def calls(self):
        c = read(self.calls_path)
        return c.splitlines() if c else []

    def reset_calls(self):
        if os.path.exists(self.calls_path):
            os.remove(self.calls_path)

    def set(self, what, value):
        write({"lid": self.lid, "ac": self.ac}[what], value + "\n")

    def at_sleep(self, n, what, value):
        """At the nth one-second sleep, the lid or the charger changes."""
        target = {"lid": self.lid, "ac": self.ac}[what]
        write(os.path.join(self.tmp, f"on-sleep-{n}"), f"echo {value} > {target}\n")

    def logged(self):
        return read(self.log) or ""

    def dump(self, r=None):
        out = (r.stdout + r.stderr) if r is not None else ""
        return f"{out}\nwakealarm={self.wake()!r}\nstate={self.state()}\ncalls={self.calls()}\nlog:\n{self.logged()}"


def world(**kw):
    tmp = tempfile.TemporaryDirectory()
    w = World(tmp.name, **kw)
    w._tmp = tmp
    return w


FINISH = f"detach {os.path.realpath(HOOK)} finish"
POWERED_OFF = [FINISH] + ["sleep 1"] * 5 + ["poweroff", "sleep 600"]


def test_not_armed():
    w = world(minutes=None)
    r = w.pre()
    check(r.returncode == 0 and w.wake() == "" and w.state() is None and w.logged() == "",
          "no flag: nothing armed, no state, nothing logged", w.dump(r))
    r = w.post(T + 1500)
    check(w.calls() == [] and w.state() is None, "no flag: post does nothing", w.dump(r))

    w = world()
    write(w.safe, "/dev/mapper/root\n")
    r = w.pre()
    check(w.wake() == "" and w.state() is None and w.logged() == "", "safe boot: nothing armed", w.dump(r))
    write(w.state_path, f"deadline={T}\nalarm_at={T}\nlid_pre=true\narmed_at={T - 1500}\nlast_post=\ntries=0\nmode=\n")
    r = w.post(T)
    check(w.calls() == [] and w.state() is not None, "safe boot: post leaves even a state alone, no power-off",
          w.dump(r))

    w = world(ac="1")
    r = w.pre()
    check(w.wake() == "" and w.state() is None and "not-armed why=on-charger" in w.logged(),
          "on the charger: nothing armed", w.dump(r))
    w = world()
    os.remove(w.ac)
    r = w.pre()
    check(w.wake() == "" and w.state() is None, "charger unreadable: nothing armed", w.dump(r))

    w = world()
    os.remove(os.path.join(w.rtc, "device/driver/module"))
    os.symlink(os.path.join(w.tmp, "module/rtc_macsmc"), os.path.join(w.rtc, "device/driver/module"))
    r = w.pre()
    check(w.wake() == "" and w.state() is None and "not-armed why=no-alarm-rtc" in w.logged(),
          "no alarm RTC (the stock driver): nothing armed", w.dump(r))
    w = world()
    os.remove(os.path.join(w.rtc, "wakealarm"))
    r = w.pre()
    check(w.state() is None and "not-armed why=no-alarm-rtc" in w.logged(),
          "an alarm driver without a wakealarm: nothing armed", w.dump(r))
    w = world()
    os.remove(os.path.join(w.rtc, "since_epoch"))
    r = w.pre()
    check(w.wake() == "" and w.state() is None and "rtc-time-unreadable" in w.logged(),
          "the RTC's time unreadable: nothing armed", w.dump(r))


def test_foreign_alarm():
    w = world()
    w.set_wake(T + 900)
    r = w.pre()
    check(w.wake() == str(T + 900) and w.state() is None and "left-alone why=alarm-not-ours" in w.logged(),
          "an alarm that isn't ours (rtcwake -m no): left as it is, no state", w.dump(r))
    r = w.post(T + 900)
    check(w.wake() == str(T + 900) and w.calls() == [], "and post neither clears it nor powers off", w.dump(r))

    # Ours from an early wake is still in the state when somebody arms theirs.
    w = world()
    w.pre()
    w.post(T + 100)
    check(w.state() is not None and w.wake() == str(T + 1500), "(an early wake leaves our state and alarm)",
          w.dump())
    w.set_wake(T + 1000)
    w.reset_calls()
    r = w.pre(T + 110)
    check(w.wake() == str(T + 1000) and w.state() is None,
          "their alarm over our older state: the state goes, their alarm stays", w.dump(r))
    r = w.post(T + 1500)
    check(w.wake() == str(T + 1000) and w.calls() == [],
          "and that sleep's post does nothing at our old deadline", w.dump(r))


def test_arm():
    w = world()
    r = w.pre()
    s = w.state() or {}
    check(r.returncode == 0 and w.wake() == str(T + 1500), "armed: the alarm is 25 minutes ahead", w.dump(r))
    check(s.get("deadline") == str(T + 1500) and s.get("alarm_at") == str(T + 1500)
          and s.get("lid_pre") == "true" and s.get("armed_at") == str(T) and s.get("tries") == "0",
          "armed: the state has the deadline, the alarm, the lid and the time", w.dump(r))
    check(read(os.path.join(w.decoy, "wakealarm")) == "", "armed: the other RTC's wakealarm isn't touched")
    line = w.logged().strip().splitlines()[-1]
    check(re.match(r"\d{4}-\d\d-\d\dT[\d:]+\+00:00 pre armed ", line) is not None and f"alarm_at={T + 1500}" in line,
          "armed: one dated line in the log", line)
    check(w.calls() == [], "armed: no sleep, no power-off")

    w = world(lid="false")
    w.pre()
    check((w.state() or {}).get("lid_pre") == "false", "armed with the lid open: the state says so", w.dump())
    w = world(lid="who knows")
    w.pre()
    check((w.state() or {}).get("lid_pre") == "unknown" and w.wake() == str(T + 1500),
          "armed with the lid unknown: armed, and the state says unknown", w.dump())

    # The clock is the alarm RTC's, not the other RTC's and not the system's.
    w = world(now=8249)
    w.pre()
    check(w.wake() == str(8249 + 1500), "an RTC at 1970: the alarm is in the RTC's own seconds", w.dump())
    w = world()
    w.extra_env["OFF_AFTER_NOW"] = str(T + 5)
    w.pre()
    check(w.wake() == str(T + 5 + 1500), "OFF_AFTER_NOW replaces the clock", w.dump())

    for text, minutes in (("", 25), ("junk\n", 25), ("0\n", 25), ("9999\n", 25), ("721\n", 25), ("-5\n", 25),
                          ("2.5\n", 25), ("30\n", 30), (" 40 \n", 40), ("720\n", 720), ("1\n", 1), ("007\n", 7),
                          ("99999999999999999999999\n", 25)):
        w = world(minutes=text)
        w.pre()
        check(w.wake() == str(T + minutes * 60), f"minutes {text.strip()!r}: {minutes}", w.dump())


def test_write_fails():
    w = world()
    os.remove(os.path.join(w.rtc, "wakealarm"))
    os.makedirs(os.path.join(w.rtc, "wakealarm"))
    r = w.pre()
    check(r.returncode == 0 and w.state() is None and "not-armed why=alarm-write-failed" in w.logged(),
          "the wakealarm write fails: no state, and the log says so", w.dump(r))
    r = w.post(T + 1500)
    check(w.calls() == [], "and post does nothing", w.dump(r))

    w = world()
    os.remove(os.path.join(w.rtc, "wakealarm"))
    os.symlink("/dev/null", os.path.join(w.rtc, "wakealarm"))
    r = w.pre()
    check(w.state() is None and f"wrote={T + 1500} reads=nothing" in w.logged(),
          "the wakealarm reads back empty: no state", w.dump(r))

    # A state of ours from an early wake goes too.
    w = world()
    w.pre()
    w.post(T + 100)
    os.remove(os.path.join(w.rtc, "wakealarm"))
    os.symlink("/dev/null", os.path.join(w.rtc, "wakealarm"))
    r = w.pre(T + 110)
    check(w.state() is None, "the write fails on a sleep after an early wake: the older state goes", w.dump(r))

    w = world()
    os.rmdir(os.path.join(w.tmp, "run"))
    r = w.pre()
    check(w.wake() in ("", "0") and "not-armed why=state-not-written" in w.logged(),
          "the state can't be written: the alarm is cleared again", w.dump(r))


def test_early_wake():
    w = world()
    w.pre()
    r = w.post(T + 600)
    s = w.state() or {}
    check(s.get("last_post") == str(T + 600) and s.get("deadline") == str(T + 1500) and w.wake() == str(T + 1500)
          and w.calls() == ["sleep 1"] * 5,
          "early wake, lid closed for 5 s more: the state and the alarm stay, no power-off", w.dump(r))
    r = w.pre(T + 600 + 120)
    check(w.wake() == str(T + 1500) and (w.state() or {}).get("deadline") == str(T + 1500)
          and "deadline_kept=yes" in w.logged(),
          "a sleep 120 s after the early wake keeps the deadline", w.dump(r))

    w = world()
    w.pre()
    w.post(T + 600)
    r = w.pre(T + 600 + 121)
    check(w.wake() == str(T + 721 + 1500) and (w.state() or {}).get("deadline") == str(T + 721 + 1500),
          "a sleep 121 s after it gets a new deadline", w.dump(r))

    # Three wakes in a row: the deadline never moves.
    w = world()
    w.pre()
    for t in (300, 700, 1100):
        w.post(T + t)
        w.pre(T + t + 20)
    check(w.wake() == str(T + 1500), "three early wakes, each followed by a sleep: the deadline hasn't moved",
          w.dump())

    w = world()
    w.pre()
    w.post(T + 1490)
    r = w.pre(T + 1495)
    s = w.state() or {}
    check(w.wake() == str(T + 1555) and s.get("alarm_at") == str(T + 1555) and s.get("deadline") == str(T + 1500),
          "a sleep 5 s before the deadline: the alarm is 60 s ahead, not 5", w.dump(r))
    w.fire()
    w.reset_calls()
    r = w.post(T + 1555)
    check(w.calls() == POWERED_OFF, "and that alarm ends in the power-off", w.dump(r))

    w = world()
    w.pre()
    w.post(T + 1497)
    r = w.pre(T + 1600)
    check(w.wake() == str(T + 1660), "a sleep after the kept deadline has passed: the alarm is 60 s ahead",
          w.dump(r))

    w = world()
    w.pre()
    w.set("lid", "false")
    r = w.post(T + 600)
    check(w.state() is None and w.wake() == "0" and w.calls() == [],
          "early wake, lid open: our alarm and the state are cleared", w.dump(r))
    r = w.pre(T + 660)
    check(w.wake() == str(T + 660 + 1500), "and the next sleep starts a new deadline", w.dump(r))

    w = world()
    w.pre()
    w.at_sleep(3, "lid", "false")
    r = w.post(T + 600)
    check(w.state() is None and w.wake() == "0" and w.calls() == ["sleep 1"] * 3,
          "early wake, logind sees the lid open 3 s later: cleared, the deadline isn't kept", w.dump(r))

    w = world()
    w.pre()
    r = w.post(T + 1500 - 3)
    check(w.state() is not None and "poweroff" not in w.calls(), "3 s before the alarm's time is an early wake",
          w.dump(r))


def test_power_off():
    w = world()
    w.pre()
    w.fire()
    r = w.post(T + 1500)
    s = w.state() or {}
    check(r.returncode == 0 and w.calls() == POWERED_OFF,
          "the alarm's time, lid closed: 5 one-second checks, then the power-off, once", w.dump(r))
    check(s.get("mode") == "closed" and s.get("tries") == "1", "the state stays", w.dump(r))
    check("post power-off why=alarm-time mode=closed" in w.logged() and "finish power-off-asked" in w.logged(),
          "both steps are in the log", w.logged())

    w = world()
    w.pre()
    w.fire()
    r = w.post(T + 1500 - 2)
    check(w.calls() == POWERED_OFF, "2 s before the alarm's time counts as the alarm's time", w.dump(r))

    w = world()
    w.pre()
    w.fire()
    r = w.post(T + 1500 + 7200)
    check(w.calls() == POWERED_OFF, "lid still closed two hours after the alarm's time: power-off", w.dump(r))

    w = world(lid="false")
    w.pre()
    w.fire()
    w.set("lid", "true")
    r = w.post(T + 1500 + 300)
    check(w.calls() == POWERED_OFF, "lid open before, closed now, 5 minutes late: power-off", w.dump(r))

    w = world()
    w.pre()
    w.fire()
    w.set("lid", "false")
    r = w.post(T + 1500)
    check(w.calls() == [] and w.state() is None,
          "lid closed before the sleep and open now (the user is back): no power-off, cleared", w.dump(r))

    w = world()
    w.pre()
    w.fire()
    w.set("lid", "garbage")
    r = w.post(T + 1500)
    check(w.calls() == [] and w.state() is None, "lid unknown at the alarm's time: no power-off, cleared", w.dump(r))

    w = world(lid="nothing")
    w.pre()
    w.fire()
    w.set("lid", "false")
    r = w.post(T + 1500)
    check(w.calls() == [] and w.state() is None, "lid unknown before, open now: no power-off", w.dump(r))


def test_charger_at_post():
    for when, t in (("before the alarm's time", 600), ("at the alarm's time", 1500)):
        w = world()
        w.pre()
        w.set("ac", "1")
        r = w.post(T + t)
        check(w.calls() == [] and w.state() is None and w.wake() == "0",
              f"on the charger {when}: cleared, no power-off", w.dump(r))
    w = world()
    w.pre()
    os.remove(w.ac)
    r = w.post(T + 1500)
    check(w.calls() == [] and w.state() is None, "charger unreadable at post: cleared, no power-off", w.dump(r))

    # Our alarm is cleared only while it is the one that's set.
    w = world()
    w.pre()
    w.post(T + 600)
    w.set_wake(T + 4000)
    w.set("ac", "1")
    r = w.post(T + 700)
    check(w.state() is None and w.wake() == str(T + 4000), "clearing leaves an alarm that isn't ours", w.dump(r))


def test_stale_state():
    # An early wake kept the state; the next sleep starts on the charger, and
    # the charger is pulled while it sleeps.
    w = world()
    w.pre()
    w.post(T + 600)
    w.set("ac", "1")
    w.reset_calls()
    r = w.pre(T + 630)
    check(w.state() is None and w.wake() == "0", "a sleep that starts on the charger drops the older state and alarm",
          w.dump(r))
    w.set("ac", "0")
    r = w.post(T + 1500)
    check(w.calls() == [], "so its post can't power off at the old deadline", w.dump(r))

    w = world()
    w.pre()
    w.post(T + 600)
    os.remove(w.flag)
    r = w.pre(T + 630)
    check(w.state() is None and w.wake() == "0", "switched off by hand between two sleeps: state and alarm go",
          w.dump(r))

    w = world()
    w.pre()
    write(w.state_path, "alarm_at=soon\n")
    r = w.post(T + 1500)
    check(w.calls() == [] and w.state() is None, "a state that can't be read: cleared, no power-off", w.dump(r))


def test_recheck():
    w = world()
    w.pre()
    w.fire()
    w.at_sleep(3, "lid", "false")
    r = w.post(T + 1500)
    check(w.calls() == [FINISH] + ["sleep 1"] * 3 and w.state() is None and "finish called-off" in w.logged(),
          "the lid opens in the third second of the re-check: no power-off, cleared", w.dump(r))

    w = world()
    w.pre()
    w.fire()
    w.at_sleep(5, "lid", "false")
    r = w.post(T + 1500)
    check("poweroff" not in w.calls() and w.state() is None, "the lid opens in the fifth second: no power-off",
          w.dump(r))

    w = world()
    w.pre()
    w.fire()
    w.at_sleep(2, "ac", "1")
    r = w.post(T + 1500)
    check("poweroff" not in w.calls() and w.state() is None, "the charger comes in the second second: no power-off",
          w.dump(r))

    w = world()
    w.pre()
    w.fire()
    w.at_sleep(4, "lid", "broken")
    r = w.post(T + 1500)
    check("poweroff" not in w.calls() and w.state() is None, "the lid turns unknown during the re-check: no power-off",
          w.dump(r))


def test_idle():
    for late, off in ((0, True), (5, True), (20, True), (21, False), (3600, False)):
        w = world(lid="false")
        w.pre()
        w.fire()
        r = w.post(T + 1500 + late)
        if off:
            check(w.calls() == POWERED_OFF and (w.state() or {}).get("mode") == "idle",
                  f"lid open before and after, {late} s after the alarm's time: power-off", w.dump(r))
        else:
            check(w.calls() == [] and w.state() is None,
                  f"lid open before and after, {late} s after the alarm's time: no power-off, cleared", w.dump(r))

    w = world(lid="false")
    w.pre()
    w.fire()
    w.at_sleep(2, "lid", "true")
    r = w.post(T + 1500 + 5)
    check(w.calls() == POWERED_OFF, "idle, and the lid closes during the re-check: still the power-off", w.dump(r))

    w = world(lid="false")
    w.pre()
    r = w.post(T + 400)
    check(w.calls() == [] and w.state() is None, "idle sleep ended early with the lid open: cleared", w.dump(r))


def test_refused():
    # logind refuses while the suspend job is still open, then takes it.
    w = world()
    w.pre()
    w.fire()
    write(os.path.join(w.tmp, "poweroff-ok-at"), "3\n")
    r = w.post(T + 1500)
    check(w.calls() == [FINISH] + ["sleep 1"] * 5 + ["poweroff", "sleep 1", "poweroff", "sleep 1", "poweroff",
                                                   "sleep 600"],
          "the power-off refused twice: asked again each second until it's taken", w.dump(r))

    w = world()
    w.pre()
    w.fire()
    write(os.path.join(w.tmp, "poweroff-ok-at"), "9\n")
    w.at_sleep(7, "lid", "false")
    r = w.post(T + 1500)
    check(w.calls().count("poweroff") == 2 and w.state() is None,
          "the lid opens while the power-off is being refused: no more asking, cleared", w.dump(r))

    # Never taken: three sleeps of 60 s each try again, then it stops arming.
    w = world()
    write(os.path.join(w.tmp, "poweroff-fails"), "")
    w.pre()
    t = T + 1500
    for attempt in (1, 2, 3):
        w.fire()
        w.reset_calls()
        r = w.post(t)
        s = w.state() or {}
        check(w.calls().count("poweroff") == 30 and s.get("tries") == str(attempt) and s.get("last_post") == str(t),
              f"the power-off refused 30 times (attempt {attempt}): the state stays", w.dump(r))
        r = w.pre(t + 10)
        if attempt < 3:
            check(w.wake() == str(t + 70), "and the next sleep's alarm is 60 s ahead", w.dump(r))
        t += 70
    check(w.state() is None and "not-armed why=power-off-failed tries=3" in w.logged(),
          "after the third, the next sleep isn't armed", w.dump(r))
    check("power-off-refused try=1 asked=30 said=Call to PowerOff failed: in progress" in w.logged(),
          "the log has what the power-off said", w.logged())

    w = world()
    w.pre()
    w.fire()
    write(os.path.join(w.tmp, "detach-fails"), "")
    r = w.post(T + 1500)
    check(w.calls() == [FINISH] and (w.state() or {}).get("tries") == "1" and "finish-not-started" in w.logged(),
          "finish can't be started: no power-off, the state stays for the next sleep", w.dump(r))

    w = world()
    w.pre()
    w.fire()
    write(os.path.join(w.tmp, "detach-noop"), "")
    r = w.post(T + 1500)
    check(w.calls() == [FINISH] + ["sleep 0.2"] * 25 and "finish-not-seen" in w.logged(),
          "finish never shows up: post waits 5 s for it and says so", w.dump(r))

    w = world()
    r = w.run("finish")
    check(w.calls() == [], "finish with no state: nothing", w.dump(r))
    w.pre()
    r = w.run("finish")
    check(w.calls() == [] and w.state() is None, "finish with no power-off decided: nothing, cleared", w.dump(r))


def test_defaults():
    src = read(HOOK)
    for what, text in (
        ("the flag", "FLAG=${OFF_AFTER_FLAG:-/etc/sleep-drain/off-after}"),
        ("the state in /run", "STATE=${OFF_AFTER_STATE:-/run/sleep-drain-off-after}"),
        ("the safe boot's marker", "SAFE=${OFF_AFTER_SAFE:-/run/sleep-drain-safe/active}"),
        ("the charger", "AC=${OFF_AFTER_AC:-/sys/class/power_supply/macsmc-ac/online}"),
        ("the RTCs", "RTC_GLOB=${OFF_AFTER_RTC_GLOB:-/sys/class/rtc/rtc*}"),
        ("its own log", "LOG=${OFF_AFTER_LOG:-/var/log/sleep-drain-off-after.log}"),
        ("the lid from logind", '"${OFF_AFTER_LID:-logind LidClosed}"'),
        ("the power-off", '"${OFF_AFTER_POWEROFF:-systemctl poweroff --no-block}"'),
        ("finish outside the suspend unit", "DETACH=(systemd-run --quiet --collect --unit=sleep-drain-off-after-finish"),
        ("under a lid inhibitor", "systemd-inhibit --what=handle-lid-switch --mode=block --who=sleep-drain-off-after"),
    ):
        check(text in src, f"default: {what}", text)
    check("/var/log/sleep-drain.log" not in src.replace("# /var/log/sleep-drain-off-after.log. /var/log/sleep-drain.log is left alone:", ""),
          "the hook never names sleep-drain.log outside its header")

    def logind_fn(text):
        m = re.search(r"^logind\(\) \{\n(.*?)^\}", text, re.S | re.M)
        return m.group(1) if m else None
    check(logind_fn(src) is not None and logind_fn(src) == logind_fn(read(os.path.join(ROOT, "sleep-guard"))),
          "logind() is sleep-guard's, word for word")
    r = subprocess.run(["bash", "-n", HOOK], capture_output=True, text=True)
    check(r.returncode == 0 and os.access(HOOK, os.X_OK), "the hook parses and is executable", r.stderr)


def test_unit():
    unit = read(UNIT)
    lines = [l.strip() for l in unit.splitlines() if l.strip() and not l.startswith("#")]
    hook = read(os.path.join(ROOT, "safe-boot/sleepdrain-safe.hook"))
    run_dir = re.search(r"^SDS_RUN=(\S+)$", hook, re.M).group(1)
    check('> "$SDS_RUN/active"' in hook and f"ConditionPathExists=!{run_dir}/active" in lines,
          "unit: not where the safe boot's hook has written its marker", run_dir)
    # The marker is written in run_latehook, in the initramfs: before any unit.
    late = hook[hook.index("run_latehook()"):]
    check('> "$SDS_RUN/active"' in late, "unit: that marker is written in the initramfs (run_latehook)")
    word = re.search(r'\*" (\S+) "\*\) return 0', hook).group(1)
    check(f"ConditionKernelCommandLine=!{word}" in lines, "unit: nor with the safe boot's word on the command line",
          word)
    check("ConditionPathExists=/etc/sleep-drain/off-after" in lines, "unit: only with the flag")
    check("Type=oneshot" in lines and "ExecStart=/usr/local/lib/sleep-drain/rtc-alarm/load" in lines,
          "unit: a oneshot that runs the installed load")
    check("WantedBy=multi-user.target" in lines and not any(
        l.startswith(("RequiredBy=", "Requires=", "BindsTo=", "OnFailure=", "FailureAction=")) for l in lines),
          "unit: wanted by multi-user.target, required by nothing")
    pre = [l for l in lines if l.startswith("ExecStartPre=")]
    check(len(pre) == 1 and pre[0].startswith("ExecStartPre=-") and "$" not in pre[0] and "%" not in pre[0],
          "unit: the wait for the stock drivers can't fail the unit, and has nothing systemd would expand", pre)
    load = read(os.path.join(ROOT, "rtc-alarm-test/load"))
    for dev in ("macsmc-rtc", "macsmc-reboot", "2920a1300.spmi"):
        check(f"/sys/bus/platform/devices/{dev}/driver" in pre[0] and dev in load,
              f"unit: waits for {dev}, a device load checks")


def test_install():
    inst = read(os.path.join(ROOT, "install.sh"))
    for line in (
        "install -m 755 sleep-drain-off-after /usr/lib/systemd/system-sleep/sleep-drain-off-after",
        "install -m 755 sleep-drain-nights /usr/local/bin/sleep-drain-nights",
        "install -m 644 sleep-drain-alarm.service /etc/systemd/system/sleep-drain-alarm.service",
        "install -D -m 755 rtc-alarm-test/load /usr/local/lib/sleep-drain/rtc-alarm/load",
        'install -D -m 644 "$build/$ko.ko" "/usr/local/lib/sleep-drain/rtc-alarm/$ko.ko"',
    ):
        check(line in [l.strip() for l in inst.splitlines()], f"install.sh: {line.split()[-1]}")
    for f in ("sleep-drain-off-after", "sleep-drain-nights", "sleep-drain-alarm.service", "rtc-alarm-test/load"):
        check(os.path.isfile(os.path.join(ROOT, f)), f"{f} is in the repo")
    # The modules install.sh builds are the Makefile's, and the ones load asks for.
    make = read(os.path.join(ROOT, "rtc-alarm-test/Makefile"))
    objs = sorted(o[:-2] for o in re.search(r"^obj-m := (.*)$", make, re.M).group(1).split())
    load = read(os.path.join(ROOT, "rtc-alarm-test/load"))
    wanted = sorted(re.search(r"^for ko in (.*); do$", load, re.M).group(1).split())
    built = sorted(re.search(r"^  for ko in (.*); do$", inst, re.M).group(1).split())
    copied = sorted(re.findall(r"rtc-alarm-test/([\w-]+)\.c", inst))
    check(objs == wanted == built == copied, "install.sh copies, builds and installs the three modules load loads",
          f"Makefile {objs}\nload {wanted}\ninstalled {built}\ncopied {copied}")
    check("rtc-alarm-test/Makefile \"$build\"/" in inst, "install.sh builds them in a scratch copy")
    block = inst[inst.index("cp rtc-alarm-test/rtc-macsmc-alarm.c"):inst.index("install -d -m 755 /etc/sleep-drain")]
    check("if make -s -C \"$build\" > /dev/null; then" in block and "else" in block and "exit" not in block,
          "install.sh: a failed build isn't fatal")
    check("/var/log/sleep-drain-off-after.log" in inst, "install.sh makes the hook's log")
    check("systemctl enable sleep-drain-alarm" not in inst and "systemctl start sleep-drain-alarm" not in inst,
          "install.sh doesn't switch it on: sleep-drain-nights does")
    un = read(os.path.join(ROOT, "uninstall.sh"))
    for path in ("/usr/lib/systemd/system-sleep/sleep-drain-off-after", "/usr/local/bin/sleep-drain-nights",
                 "/etc/systemd/system/sleep-drain-alarm.service"):
        check(path in un, f"uninstall.sh removes {path}")
    check("systemctl disable sleep-drain-alarm.service" in un, "uninstall.sh disables the unit")


def nights(w, *args, alarm_rtc=True, start_fails=False):
    stubs = os.path.join(w.tmp, "nstubs")
    write(os.path.join(stubs, "sudo"), '#!/bin/sh\nexec "$@"\n', 0o755)
    write(os.path.join(stubs, "systemctl"), f"""#!/bin/sh
case "$1" in
  is-enabled) echo enabled; exit 0 ;;
  show) case "$*" in *ActiveState*) echo failed ;; *) echo exit-code ;; esac; exit 0 ;;
esac
echo "systemctl $*" >> {w.calls_path}
[ "$1" = start ] && [ -f {w.tmp}/start-fails ] && exit 1
exit 0
""", 0o755)
    write(os.path.join(stubs, "journalctl"),
          "#!/bin/sh\necho \"load: rtc-macsmc-alarm.ko isn't built for 7.9.9: run make again\"\n", 0o755)
    if not alarm_rtc and os.path.exists(os.path.join(w.rtc, "wakealarm")):
        os.remove(os.path.join(w.rtc, "wakealarm"))
    if start_fails:
        write(os.path.join(w.tmp, "start-fails"), "")
    e = w.env()
    e["PATH"] = stubs + ":/usr/bin:/bin"
    return subprocess.run(["bash", NIGHTS, *args], env=e, capture_output=True, text=True, timeout=60)


def test_nights():
    w = world(minutes=None)
    r = nights(w, "on", "30")
    check(r.returncode == 0 and read(w.flag) == "30\n" and w.calls() == ["systemctl enable sleep-drain-alarm.service"],
          "nights on 30: the flag says 30, the unit is enabled, and not started with the alarm RTC there",
          r.stdout + r.stderr + str(w.calls()))
    check("power-off after 30 minutes" in r.stdout and "rtc1 is the alarm RTC; no alarm is set" in r.stdout,
          "nights on: says the setting and the alarm RTC", r.stdout)
    w.pre()
    check(w.wake() == str(T + 1800), "the hook reads the flag nights wrote", w.dump())
    r = nights(w)
    check(f"our alarm is set for {T + 1800}" in r.stdout and "state: " in r.stdout,
          "nights: shows our alarm and the state", r.stdout)
    w.reset_calls()
    r = nights(w, "off")
    check(r.returncode == 0 and not os.path.exists(w.flag) and w.state() is None and w.wake() == "0"
          and w.calls() == ["systemctl disable sleep-drain-alarm.service"],
          "nights off: the flag, our alarm and the state go, the unit is disabled", r.stdout + r.stderr + w.dump())
    check("sleeps last until something wakes the Mac" in r.stdout, "nights off: says so", r.stdout)

    w = world(minutes=None)
    r = nights(w, "on")
    check(read(w.flag) == "25\n", "nights on: 25 minutes by default", r.stdout + r.stderr)
    for bad in ("0", "721", "abc", "2.5", "-3", ""):
        w = world(minutes=None)
        r = nights(w, "on", bad)
        check(r.returncode == 2 and not os.path.exists(w.flag) and w.calls() == [], f"nights on {bad!r}: refused",
              r.stdout + r.stderr)
    w = world(minutes=None)
    r = nights(w, "sideways")
    check(r.returncode == 2 and w.calls() == [], "nights with an unknown word: usage", r.stdout + r.stderr)

    w = world(minutes=None)
    r = nights(w, "on", alarm_rtc=False)
    check(r.returncode == 0 and w.calls() == ["systemctl enable sleep-drain-alarm.service",
                                              "systemctl start sleep-drain-alarm.service"],
          "nights on without the alarm RTC: the unit is started now", r.stdout + r.stderr + str(w.calls()))
    w = world(minutes=None)
    r = nights(w, "on", alarm_rtc=False, start_fails=True)
    check(r.returncode == 0 and read(w.flag) == "25\n" and "didn't load" in r.stderr
          and "no alarm RTC" in r.stdout and "is enabled, failed (result: exit-code)" in r.stdout
          and "it last said: load: rtc-macsmc-alarm.ko isn't built for 7.9.9" in r.stdout,
          "nights on, the drivers don't load: says so, with the unit's state and its last line",
          r.stdout + r.stderr)

    w = world(minutes=None)
    write(w.safe, "x\n")
    r = nights(w, "on")
    check(r.returncode == 1 and not os.path.exists(w.flag) and w.calls() == [], "nights on in a safe boot: refused",
          r.stdout + r.stderr)

    # off leaves an alarm that isn't ours.
    w = world()
    w.pre()
    w.post(T + 100)
    w.set_wake(T + 900)
    r = nights(w, "off")
    check(w.wake() == str(T + 900) and w.state() is None, "nights off leaves an alarm that isn't ours", w.dump(r))
    w = world()
    w.set_wake(T + 900)
    r = nights(w)
    check("an alarm that isn't ours is set" in r.stdout, "nights: names an alarm that isn't ours", r.stdout)
    r = subprocess.run(["bash", "-n", NIGHTS], capture_output=True, text=True)
    check(r.returncode == 0 and os.access(NIGHTS, os.X_OK), "nights parses and is executable", r.stderr)


def main():
    test_not_armed()
    test_foreign_alarm()
    test_arm()
    test_write_fails()
    test_early_wake()
    test_power_off()
    test_charger_at_post()
    test_stale_state()
    test_recheck()
    test_idle()
    test_refused()
    test_defaults()
    test_unit()
    test_install()
    test_nights()
    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
