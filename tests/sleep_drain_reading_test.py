#!/usr/bin/env python3
"""sleep-drain-reading's take and v0 against a fake battery, without root.

Each case runs the script as uid 0 in a bubblewrap user namespace, with every
path it reads or writes pointed (SDR_*) into a temp directory: the battery and
AC, the USB and USB-C trees, the log directory, smc-peek's values file and the
safe boot's marker. sleep-drain-safe and systemctl are stubs that record their
calls. Readings, v0's too, last 3 s, sampled every second.

  python3 -I tests/sleep_drain_reading_test.py
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.realpath(os.path.join(HERE, "..", "sleep-drain-reading"))
PEEK = ("# smc-peek key_count=3\n"
        "10 BAAC si32 4 80 10270000\n"
        "11 BACC ui32 4 80 2c010000\n"
        "12 BQCC ui16 2 80 0a00\n"
        "# end keys=3 read=3 errors=0\n")

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


def setup(tmp, ac="0", safe=False, usb=None, partner=False, save_fails=False):
    bat = os.path.join(tmp, "bat")
    for name, val in {"charge_now": "4100000", "voltage_now": "12300000", "current_now": "-310000",
                      "charge_counter": "-123456", "capacity": "80", "status": "Discharging"}.items():
        write(os.path.join(bat, name), val + "\n")
    write(os.path.join(tmp, "ac", "online"), ac + "\n")
    write(os.path.join(tmp, "uptime"), "612.34 4000.00\n")
    write(os.path.join(tmp, "usb", "usb1", "idVendor"), "1d6b\n")
    write(os.path.join(tmp, "usb", "usb1", "idProduct"), "0002\n")
    if usb:
        write(os.path.join(tmp, "usb", "1-1", "idVendor"), usb[0] + "\n")
        write(os.path.join(tmp, "usb", "1-1", "idProduct"), usb[1] + "\n")
    os.makedirs(os.path.join(tmp, "typec", "port0"), exist_ok=True)
    if partner:
        os.makedirs(os.path.join(tmp, "typec", "port1-partner"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "log"), exist_ok=True)
    write(os.path.join(tmp, "peek"), PEEK)
    if safe:
        write(os.path.join(tmp, "run", "active"), "")
    calls = os.path.join(tmp, "calls")
    write(os.path.join(tmp, "stubs", "sleep-drain-safe"),
          f'#!/bin/sh\necho "safe $*" >> {calls}\n'
          + (f'[ "$1" = save ] && exit 1\n' if save_fails else "") + "exit 0\n", 0o755)
    write(os.path.join(tmp, "stubs", "systemctl"), f'#!/bin/sh\necho "systemctl $*" >> {calls}\n', 0o755)


def env(tmp):
    return {
        "PATH": os.path.join(tmp, "stubs") + ":/usr/bin:/bin",
        "SDR_BAT": os.path.join(tmp, "bat"), "SDR_AC": os.path.join(tmp, "ac"),
        "SDR_UPTIME": os.path.join(tmp, "uptime"), "SDR_USB": os.path.join(tmp, "usb"),
        "SDR_TYPEC": os.path.join(tmp, "typec"), "SDR_LOGDIR": os.path.join(tmp, "log"),
        "SDR_PEEK": os.path.join(tmp, "peek"), "SDR_PEEK_KO": os.path.join(tmp, "none.ko"),
        "SDR_SAFE": os.path.join(tmp, "stubs", "sleep-drain-safe"),
        "SDR_SAFE_FLAG": os.path.join(tmp, "run", "active"),
        "SDR_INTERVAL": "1", "SDR_REBOOT_WAIT": "0", "SDR_V0_MINUTES": "0.05", "TZ": "UTC",
    }


def run(tmp, *args):
    cmd = ["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", SCRIPT, *args]
    return subprocess.run(cmd, env=env(tmp), capture_output=True, text=True, timeout=60)


def calls(tmp):
    try:
        with open(os.path.join(tmp, "calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def readings(tmp):
    return sorted(glob.glob(os.path.join(tmp, "log", "reading-*")))


def summary(d):
    with open(os.path.join(d, "summary")) as f:
        return dict(kv.split("=", 1) for kv in f.read().split())


def test_median():
    with tempfile.TemporaryDirectory() as tmp:
        tsv = os.path.join(tmp, "s.tsv")
        write(tsv, "epoch\tx\tv\n" + "".join(f"{100 + i}.5\t-\t{v}\n" for i, v in
                                             enumerate(["9", "1", "", "5", "7", "x", "3", "100"])))
        def med(col, frm):
            r = subprocess.run(["bash", "-c", f'source "$1"; median "$2" {col} {frm}', "-", SCRIPT, tsv],
                               capture_output=True, text=True)
            return r.stdout.strip()
        check(med(3, 0) == "6", "median: even count averages the middle two, skipping non-numbers", med(3, 0))
        check(med(3, 104) == "7", "median: only samples from the window's start on", med(3, 104))
        check(med(3, 200) == "?", "median: no samples gives ?", med(3, 200))


def test_take():
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        r = run(tmp, "take", "--minutes", "0.05", "--label", "t1")
        check(r.returncode == 0, "take: exits 0", r.stdout + r.stderr)
        ds = readings(tmp)
        check(len(ds) == 1, "take: one reading directory")
        if not ds:
            return
        s = summary(ds[0])
        check(s.get("charge_now_uah") == "4100000" and s.get("voltage_uv") == "12300000"
              and s.get("current_ua") == "-310000", "take: the medians are the battery's values", str(s))
        check(s.get("label") == "t1" and s.get("boot") == "regular" and s.get("then") == "none",
              "take: label, boot type and action recorded", str(s))
        check(s.get("charge_counter") == "-123456" and s.get("uptime_s") == "612.34" and s.get("ac") == "0",
              "take: charge_counter, uptime and AC from the last sample", str(s))
        check(int(s.get("samples", "0")) >= 3, "take: a sample a second", str(s))
        with open(os.path.join(ds[0], "smc-start.txt")) as f:
            start = f.read()
        check(start.startswith("# walk ") and " BACC " in start and " BQCC " in start and " BAAC " in start
              and "key_count" not in start, "take: smc-peek's BAAC, BACC and BQCC lines only", start)
        check(calls(tmp) == [], "take --then none: no restart, no save", "\n".join(calls(tmp)))


def test_refusals():
    cases = [
        ("on the charger", dict(ac="1"), ["take", "--minutes", "0.05"], "charger"),
        ("a USB device attached", dict(usb=("05e3", "0751")), ["take", "--minutes", "0.05"], "unplug everything"),
        ("a USB-C partner attached", dict(partner=True), ["take", "--minutes", "0.05"], "unplug everything"),
        ("reset outside a safe boot", {}, ["take", "--minutes", "0.05", "--then", "reset"], "safe boot"),
        ("safe-reboot in a safe boot", dict(safe=True), ["take", "--minutes", "0.05", "--then", "safe-reboot"], "safe boot"),
        ("an unknown action", {}, ["take", "--then", "poweroff"], "--then takes"),
    ]
    for what, kw, args, msg in cases:
        with tempfile.TemporaryDirectory() as tmp:
            setup(tmp, **kw)
            r = run(tmp, *args)
            check(r.returncode != 0 and msg in r.stderr and not readings(tmp) and calls(tmp) == [],
                  f"refuses {what}, and takes nothing", r.stdout + r.stderr)


def test_v0_regular():
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        r = run(tmp, "v0")
        c = calls(tmp)
        check(c == ["safe collect", "safe boot", "systemctl reboot"],
              "v0, regular boot: collect, set up the safe boot, then restart", "\n".join(c))
        ds = readings(tmp)
        check(len(ds) == 1 and summary(ds[0]).get("then") == "safe-reboot" and summary(ds[0]).get("label") == "v0",
              "v0, regular boot: a v0 reading that ends in a restart into a safe boot", r.stdout + r.stderr)
        # The stub restart returns; the real one stops the script first.
        check(r.returncode != 0 and "still running" in r.stderr, "v0: says so if the restart doesn't happen", r.stderr)
        check(ds and os.path.exists(os.path.join(ds[0], "tail.tsv")), "v0: logs the tail until the restart")


def test_v0_safe():
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp, safe=True)
        r = run(tmp, "v0")
        ds = readings(tmp)
        check(r.returncode == 0 and calls(tmp) == ["safe save"] and "hold the power key" in r.stdout,
              "v0, safe boot: save after the reading, then ask for the reset", r.stdout + r.stderr + "\n".join(calls(tmp)))
        check(len(ds) == 1 and summary(ds[0]).get("boot") == "safe" and summary(ds[0]).get("then") == "reset",
              "v0, safe boot: the reading says safe and reset")
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp, safe=True, save_fails=True)
        r = run(tmp, "v0")
        check(r.returncode != 0 and "NOT SAVED" in r.stderr and "hold the power key now" not in r.stdout.lower().replace(">>> saved. now ", ""),
              "v0, safe boot: a failed save says don't reset", r.stdout + r.stderr)


def test_v0_done():
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        for i, (boot, then) in enumerate([("regular", "safe-reboot"), ("safe", "reset")] * 3):
            write(os.path.join(tmp, "log", f"reading-20261105T10{i:02d}00", "summary"),
                  f"time=x ts=1 label=v0 boot={boot} then={then} boot_id=b{i}\n")
        r = run(tmp, "v0")
        c = calls(tmp)
        last = readings(tmp)[-1]
        check(r.returncode == 0 and c == ["safe collect"] and summary(last).get("then") == "none"
              and "V0 is done" in r.stdout, "v0: after three resets, the last reading and no restart",
              r.stdout + r.stderr + "\n".join(c))
        r = run(tmp, "v0")
        check(r.returncode == 0 and "done already" in r.stdout and len(readings(tmp)) == 7,
              "v0: run again, it takes nothing more", r.stdout + r.stderr)


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0
    test_median()
    test_take()
    test_refusals()
    test_v0_regular()
    test_v0_safe()
    test_v0_done()
    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
