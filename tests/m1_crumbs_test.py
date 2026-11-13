#!/usr/bin/env python3
"""e1-hook/m1-crumbs against a fake PMU nvmem file (M1_CRUMBS_NVMEM).

Each case writes a 64 KiB file shaped like the PMU's nvmem, with chosen bytes
at 0x6000-0x601f and the RTC at 0x1406, runs the tool with a fake SoC counter
(M1_CRUMBS_CNTVCT), and checks what it prints: run 1's stages, the
observer's (observe=1), run 3's (pmgr_late=1), run 4's (alarm=1) and the reset
time after a commit, hold=1's stages (0x31-0x37, 0x39, 0x5a-0x5e) and their
bytes, pmp_out=1's tagged stages and its byte in 0x6015, and position=1's stages
(0x40-0x45) and samples, the samples' bytes made by e1-hook/m1_position.h itself,
all checked against e1_hook.c's source too. --clear must zero 0x6004-0x6007 and 0x6010-0x6016, and
only after a stage that armed the alarm also set 0x1401 back to 0x01, and
nothing else; an unknown argument changes nothing.

  python3 -I tests/m1_crumbs_test.py
"""
import os
import random
import re
import runpy
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.realpath(os.path.join(HERE, "..", "e1-hook", "m1-crumbs"))
HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook", "e1_hook.c"))

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("".join("     " + l for l in detail.splitlines(True)))


# m1-crumbs --clear with one write failing: argv is the offset whose os.pwrite() raises, then the tool.
CLEAR_FAILS = """import os, runpy, sys
real, bad, tool = os.pwrite, int(sys.argv[1], 0), sys.argv[2]
def pwrite(fd, data, off):
    if off == bad:
        raise OSError(5, "injected")
    return real(fd, data, off)
os.pwrite = pwrite
sys.argv = [tool, "--clear"]
runpy.run_path(tool, run_name="__main__")
"""


def fake(path, scratch, rtc_secs=5000, alarm=(0, 0, 0, 0)):
    """alarm: 0x1401, 0x140c (the compare value), 0x14c0, 0x14c2."""
    b = bytearray(0xffff)
    b[0x6000:0x6000 + len(scratch)] = scratch
    b[0x1406:0x140c] = bytes(2) + rtc_secs.to_bytes(4, "little")
    b[0x1401] = alarm[0]
    b[0x140c:0x1410] = alarm[1].to_bytes(4, "little")
    b[0x14c0], b[0x14c2] = alarm[2], alarm[3]
    with open(path, "wb") as f:
        f.write(b)
    return bytes(b)


def run(path, *args, cnt="2400000000 24000000"):
    env = dict(os.environ, M1_CRUMBS_NVMEM=path, M1_CRUMBS_CNTVCT=cnt)
    return subprocess.run([sys.executable, "-I", TOOL, *args], env=env, capture_output=True, text=True, timeout=30)


def scratch(stage, parked=0, left=0, down=0, secs=0, flags=0, isrs=0, drained=0, boot=0x30, err=0x50, prog=None):
    """The breadcrumbs; for the observer's 0x6f, parked, left and down are
    0x6005-0x6007 (off, the watchdogs, the seconds left), isrs and drained
    0x6015 and 0x6016, and prog 0x6012-0x6013 over the second's upper bytes."""
    s = bytearray(0x20)
    s[1], s[3] = boot, err
    s[4:8] = bytes((stage, parked, left, down))
    s[0x10:0x14] = secs.to_bytes(4, "little")
    if prog is not None:
        s[0x12:0x14] = bytes(prog)
    s[0x14:0x17] = bytes((flags, isrs, drained))
    s[0x08:0x0c] = b"iBot"  # BootAppName's bytes: never written
    s[0x17] = 0x5a          # a byte past the breadcrumbs: never written
    return bytes(s)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")

        fake(path, scratch(0))
        r = run(path)
        check(r.returncode == 0 and "stage 0x6004 = 0x00: none" in r.stdout and "parked" not in r.stdout,
              "a clean PMU: no run recorded", r.stdout + r.stderr)

        fake(path, scratch(0x50, parked=0xfe, left=0xfe, down=0xfe, secs=4900, flags=0x80 | 0x30 | 0x02,
                           isrs=0x10, drained=3))
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x50: 8: the last SPMI command" in out
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7" in out
              and "read off within 10 ms (0x6007 = 0xfe)" in out
              and "RTC second at just before the commit: 4900, 100 s before now (5000)" in out
              and "CPU0 had I pending before the commit; PCPM0 off, PCPM1 off; WD1 was off;"
              " K3's kernel-path write after slpa worked" in out
              and "the run got at least that far" in out
              and "interrupt pending at their stop: CPU4" in out and "0x6016 = 3" in out
              and "boot-error stage 0x6003 = 0x50" in out,
              "a run that reached 0x50: every field decoded", out + r.stderr)

        fake(path, scratch(0x26, parked=0xfe))
        r = run(path)
        check(r.returncode == 0 and "6: releasing CPU4, the order's 6 of 7" in r.stdout
              and "or a later breadcrumb failed and CPU0 went on" in r.stdout
              and "parked (0x6005 = 0xfe)" in r.stdout and "left their spin" not in r.stdout,
              "a run last recorded releasing CPU4: stopped there, or its breadcrumbs did", r.stdout + r.stderr)

        fake(path, scratch(0x50, parked=0xfe, left=0xfe, down=0x7e, secs=4999, flags=0x80 | 0x08 | 0x10))
        r = run(path)
        check(r.returncode == 0 and "K3's kernel-path write after slpa FAILED" in r.stdout
              and "PCPM0 off, PCPM1 NOT off" in r.stdout and "CPU0 had nothing pending" in r.stdout
              and "read off within 10 ms (0x6007 = 0x7e): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6" in r.stdout,
              "K3's failure, PCPM1 still on and CPU7 not off, decoded", r.stdout + r.stderr)

        fake(path, scratch(0x02, secs=4990))
        r = run(path)
        check(r.returncode == 0 and "0x6004 = 0x02: K3" in r.stdout
              and "RTC second at the replay's start (K1): 4990, 10 s before now" in r.stdout,
              "a run that stopped after slpa: K1's second", r.stdout + r.stderr)

        before = fake(path, scratch(0x50, parked=0xfe, left=0xfe, down=0xfe, secs=4900, flags=0xb2, isrs=1,
                                    drained=9))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "cleared" in r.stdout and "0x50" in r.stdout,
              "--clear zeroes 0x6004-0x6007 and 0x6010-0x6016 only, after printing them",
              r.stdout + r.stderr + f"\n0x6000: {after[0x6000:0x6020].hex(' ')}")

        before = fake(path, scratch(0x30, parked=0xfe))
        r = run(path, "--clean")
        check(r.returncode == 2 and open(path, "rb").read() == before and r.stdout == "",
              "an unknown argument: usage, nothing read or written", r.stdout + r.stderr)

        r = run(os.path.join(tmp, "missing"))
        check(r.returncode == 2 and "m1-crumbs:" in r.stderr, "a missing nvmem: exit 2", r.stdout + r.stderr)

        # Run 1's reset, from its own numbers (results/m1-run1-20261107/): the
        # commit at RTC second 121544 (c8 da 01 00), read at 121761 with the
        # counter 186.4 s old: about 31 s, within the RTC's whole seconds.
        run1 = f"{4474210727} 24000000"
        fake(path, scratch(0x50, parked=0xfe, left=0xfe, down=0xfe, secs=121544, flags=0xb0), rtc_secs=121761)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "RTC second at just before the commit: 121544, 217 s before now" in r.stdout
              and "the SoC restarted about 31 s after the commit (29.6 to 31.6 s" in r.stdout
              and "186.4 s old" in r.stdout and "PCPM1 off" in r.stdout and "observe=1" not in r.stdout,
              "run 1's crumbs: the reset time from the counter and the RTC, with its bound", r.stdout + r.stderr)
        r = run(path, cnt="none")
        check(r.returncode == 0 and "restarted" not in r.stdout, "no counter: no reset time", r.stdout + r.stderr)
        fake(path, scratch(0x30, parked=0xfe, left=0xfe, down=0xfe, secs=121544), rtc_secs=121761)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "restarted" not in r.stdout and "7: every parked core released" in r.stdout,
              "a run that didn't reach the commit: no reset time", r.stdout + r.stderr)

        # The observer, all of it landed (0x6f): CPU0 and ECPM off, WD2 armed
        # with 29 s left, the commit's second in its low two bytes (c8 da),
        # PACC1 busy, nothing new in the wake status.
        fake(path, scratch(0x6f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x12, 0x34), flags=0x90,
                           isrs=0x04, drained=0x80), rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x6f: observe: CPU7 recorded everything, with nothing pending" in out
              and "CPU0 read off within 500 ms" in out and "(ECPM) read off within 500 ms" in out
              and "read off at their release: CPU1,CPU2,CPU3,CPU4,CPU5,CPU6\n" in out
              and "WD2 control 4 (RESET_EN); WD1 control 0 (off); WD0 not armed" in out
              and "CPU0's last WFI had not returned" in out
              and "0x6007 = 29: 29 s until the first armed watchdog's reset" in out
              and "from its low two bytes: 121544, 217 s before now" in out
              and "0x6012 0x6013 = 12 34" in out and "PCPM1 not checked (CPU7 kept up)" in out
              and "soc clusters still busy: PACC1 (" in out and "SIO_CPU's actual state off" in out
              and "commit: none" in out and "the SoC restarted about 31 s after the commit" in out
              and "parked (" not in out,
              "the observer's 0x6f, on run 1's numbers: every field decoded", out + r.stderr)

        # One half of 0x6005 at a time, WD0 armed again, two wake-status words,
        # AFR and AVEMSR.
        for off, cpu0, ecpm in ((0x01, "CPU0 read off", "(ECPM) did NOT read off"),
                                (0x80, "CPU0 did NOT read off", "(ECPM) read off")):
            fake(path, scratch(0x6f, parked=off, left=0x0c, down=255, secs=121544, prog=(0, 0), flags=0x80,
                               isrs=0x50, drained=0x85), rtc_secs=121761)
            r = run(path)
            out = r.stdout
            check(r.returncode == 0 and cpu0 in out and ecpm in out and "read off at their release: none\n" in out
                  and "WD0 ARMED again" in out and "commit: 0,2" in out
                  and "soc clusters still busy: AFR,AVEMSR (" in out and "no watchdog was armed" in out,
                  f"the observer's 0x6005 = 0x{off:02x}, 0x6006 = 0x0c, 0x6015 = 0x50, 0x6016 = 0x85", out + r.stderr)

        # 0x6e: all landed, but CPU7 had something pending before its WFI.
        fake(path, scratch(0x6e, parked=0xff, left=0x04, down=29, secs=121544, prog=(0, 0), flags=0x90,
                           isrs=0x04, drained=0x80), rtc_secs=121761)
        r = run(path)
        check(r.returncode == 0 and "it likely never powered down" in r.stdout
              and "soc clusters still busy: PACC1" in r.stdout and "AP wake-status words" in r.stdout,
              "the observer's 0x6e: decoded as 0x6f, with CPU7's pending interrupt", r.stdout + r.stderr)

        # 0x62: CPU7 had begun to rewrite 0x6012-0x6016: they aren't decoded,
        # and the commit's second comes from 0x6010-0x6011 alone.
        fake(path, scratch(0x62, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x12, 0x34), flags=0x90,
                           isrs=0x04, drained=0x80), rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x62: observe: CPU7 was rewriting" in out
              and "0x6012 0x6013 = 12 34, 0x6015 0x6016 = 04 80: CPU0's or CPU7's" in out
              and "from its low two bytes: 121544, 217 s before now" in out
              and "the SoC restarted about 31 s after the commit" in out
              and "soc clusters" not in out and "AIC events" not in out and "interrupt pending at their stop" not in out
              and "CPU0 read off" in out,
              "the observer's 0x62: 0x6012-0x6016 left undecoded, the commit's second from two bytes", out + r.stderr)

        # 0x61: CPU7's three bytes landed with their stage; 0x6010-0x6016 are
        # still CPU0's (CPU7 writes 0x62 before it touches them), so the
        # commit's second is all four bytes, above 65536.
        fake(path, scratch(0x61, parked=0x7e, left=0x84, down=30, secs=121544, flags=0x90, isrs=0, drained=2),
             rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "CPU0 did NOT read off" in out and "(ECPM) did NOT read off" in out
              and "HAD RETURNED" in out and "0x6007 = 30" in out
              and "RTC second at just before the commit: 121544, 217 s before now" in out
              and "0x6016 = 2: AIC events" in out and "soc clusters" not in out,
              "the observer's 0x61: CPU7's three bytes, CPU0's others", out + r.stderr)

        # 0x60: 0x6005-0x6007 still say what 0x30 wrote, with the observe mark.
        fake(path, scratch(0x60, parked=0xff, left=0x7e, down=0x7e, secs=121544, flags=0x90), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x60: observe: CPU7 began" in out
              and "parked (0x6005 = 0xff): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "read off within 10 ms (0x6007 = 0x7e)" in out and "observe=1 (0x6005 bit 0)" in out
              and "PCPM1 not checked" in out,
              "the observer's 0x60: run 1's meanings for 0x6005-0x6007, marked observe", out + r.stderr)

        # An observe run whose last crumb is CPU0's: 0x50 and 0x30, marked.
        fake(path, scratch(0x50, parked=0xff, left=0x7e, down=0x7e, secs=121544, flags=0x90), rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "Nothing CPU7 sent after the commit landed" in out
              and "observe=1 (0x6005 bit 0)" in out and "PCPM1 not checked (CPU7 kept up)" in out
              and "parked (0x6005 = 0xff): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "the SoC restarted about 31 s after the commit" in out,
              "an observe run that stopped at 0x50: marked, not read as run 1", out + r.stderr)
        fake(path, scratch(0x30, parked=0xff, left=0x7e, down=0x7e, secs=121544), rtc_secs=121761)
        r = run(path)
        check(r.returncode == 0 and "every parked core but CPU7 released" in r.stdout,
              "an observe run that stopped at 0x30: marked", r.stdout + r.stderr)

        # 0x6f with 0x6016's bit 7 lost; and the commit's second across a
        # 16-bit wrap (0x2fff0, read at 0x30005), WD1 armed again, DISPLAY
        # and SIO_CPU on.
        fake(path, scratch(0x6f, parked=0x81, left=0x40, down=255, secs=0x2fff0, prog=(0, 0), flags=0x80,
                           isrs=0xa0, drained=0x00), rtc_secs=0x30005)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "didn't survive the reset" in out and "AP wake-status" not in out
              and f"from its low two bytes: {0x2fff0}, 21 s before now" in out
              and "no watchdog was armed" in out and "WD1 control 4 (RESET_EN): armed again" in out
              and "soc clusters still busy: DISPLAY (" in out and "SIO_CPU's actual state NOT off" in out,
              "the observer's 0x6f: a lost 0x6016, a 16-bit wrap, WD1 armed again, DISPLAY and SIO_CPU on",
              out + r.stderr)

        # pmgr_late=1 (M1 run 3). 0x28: step 5 and its waits done, with qto in
        # 0x6006 (DISPLAY's), marked observe; 0x6007 isn't written yet.
        fake(path, scratch(0x28, parked=0xff, left=0x10, secs=121500), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x28 (pmgr_late=1): 5: the 68 post-slpa writes done" in out
              and "0x6006 = 0x10: macOS's waits that timed out after 10 ms: DISPLAY's power-down (58)\n" in out
              and "observe=1 (0x6005 bit 0): CPU7 was kept up for the observer, so it isn't in 0x6007 and" in out
              and "parked (0x6005 = 0xff)" in out
              and "left their spin" not in out and "read off within" not in out
              and "RTC second at the replay's start (K1): 121500" in out,
              "pmgr_late's 0x28: qto in 0x6006, nothing in 0x6007", out + r.stderr)

        # 0x2b: releasing CPU3, the order's third, with qto still in 0x6006.
        fake(path, scratch(0x2b, parked=0xfe, left=0x00))
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x2b (pmgr_late=1): 6: releasing CPU3, the order's 3 of 7" in out
              and "0x6006 = 0x00: every one of macOS's waits was met within 10 ms" in out
              and "observe=1" not in out and "left their spin" not in out,
              "pmgr_late's 0x2b: releasing CPU3, every wait met", out + r.stderr)

        # 0x38: released, observe; qto AFR's power state and DISPLAY's; 0x6007
        # the cores that read off (no "left" byte).
        fake(path, scratch(0x38, parked=0xff, left=0x12, down=0x7e, secs=121500), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x38 (pmgr_late=1): 7: every parked core but CPU7 released" in out
              and "after 10 ms: AFR's power state (51); DISPLAY's power-down (58)\n" in out
              and "read off within 10 ms (0x6007 = 0x7e): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6\n" in out
              and "left their spin" not in out and "restarted" not in out,
              "pmgr_late's 0x38, observe: qto and the cores read off", out + r.stderr)

        # 0x58 without the observer: the commit's whole second, every wait met,
        # the reset time; PCPM1 checked.
        fake(path, scratch(0x58, parked=0xfe, left=0x00, down=0xfe, secs=121544, flags=0xb0, isrs=0, drained=1),
             rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x58 (pmgr_late=1): 8: the last SPMI command" in out
              and "every one of macOS's waits was met within 10 ms" in out
              and "read off within 10 ms (0x6007 = 0xfe)" in out
              and "RTC second at just before the commit: 121544, 217 s before now" in out
              and "PCPM0 off, PCPM1 off" in out and "0x6016 = 1: AIC events" in out
              and "the SoC restarted about 31 s after the commit" in out and "observe=1" not in out,
              "pmgr_late's 0x58 without the observer: decoded as 0x50, with qto", out + r.stderr)

        # 0x70: CPU7 began; 0x6005-0x6007 still CPU0's 0x58 bytes, marked.
        fake(path, scratch(0x70, parked=0xff, left=0x50, down=0x7e, secs=121544, flags=0x90), rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x70 (pmgr_late=1): observe: CPU7 began" in out
              and "after 10 ms: DISPLAY's power-down (58); SIO_CPU's power state (61)\n" in out
              and "observe=1 (0x6005 bit 0)" in out and "PCPM1 not checked" in out
              and "the SoC restarted about 31 s after the commit" in out,
              "pmgr_late's 0x70: CPU0's 0x58 bytes, marked observe", out + r.stderr)

        # 0x71: CPU7's three bytes, as 0x61; the rest still CPU0's.
        fake(path, scratch(0x71, parked=0xff, left=0x04, down=30, secs=121544, flags=0x90, isrs=0, drained=2),
             rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x71 (pmgr_late=1): observe: CPU7 recorded CPU0's" in out
              and "CPU0 read off within 500 ms" in out and "WD2 control 4 (RESET_EN)" in out
              and "0x6007 = 30: 30 s" in out and "RTC second at just before the commit: 121544" in out
              and "0x6016 = 2: AIC events" in out and "qto" not in out and "waits" not in out,
              "pmgr_late's 0x71: CPU7's three bytes, CPU0's others", out + r.stderr)

        # 0x72: CPU7 was rewriting 0x6012-0x6016: not decoded.
        fake(path, scratch(0x72, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x10, 0xa9), flags=0x90,
                           isrs=0x24, drained=0x95), rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x72 (pmgr_late=1): observe: CPU7 was rewriting" in out
              and "0x6012 0x6013 = 10 a9, 0x6015 0x6016 = 24 95: CPU0's or CPU7's" in out
              and "from its low two bytes: 121544, 217 s before now" in out
              and "waits" not in out and "soc clusters" not in out,
              "pmgr_late's 0x72: 0x6012-0x6016 left undecoded", out + r.stderr)

        # 0x7f, the expected case: DISPLAY's wait timed out; EACC done in 5-20
        # ms, PACC0 in 200-500 ms and done before the commit; PACC1 and DISPLAY
        # busy at the end; DCS settled at 5; SIO off.
        fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x10, 0x80 | 5 << 3 | 1),
                           flags=0x90, isrs=0x24, drained=0x95), rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x7f (pmgr_late=1): observe: CPU7 recorded everything" in out
              and "0x6012 = 0x10: macOS's waits that timed out after 10 ms: DISPLAY's power-down (58)\n" in out
              and "EACC's power-down first read clear 5-20 ms after the hand-over; PACC0's first read clear 200-500 ms after the hand-over;"
              " PACC0's was done just before the commit" in out
              and "still busy at the end of CPU7's watch: PACC1,DISPLAY (" in out
              and "SIO_CPU's actual state off" in out
              and "actual 5, target 5, busy bit 31 clear (settled at 5); SIO's actual state off after the commit" in out
              and "from its low two bytes: 121544" in out and "the SoC restarted about 31 s after the commit" in out
              and "wdt page" not in out and "wake-status" not in out,
              "pmgr_late's 0x7f, the expected case: qto, the buckets, the clusters and DCS decoded", out + r.stderr)

        # 0x7e: everything wrong: four waits timed out, EACC and PACC0 still
        # pending at the end and PACC0 before the commit, DCS at 7 and busy,
        # SIO and SIO_CPU on.
        fake(path, scratch(0x7e, parked=0x7f, left=0x04, down=29, secs=121544, prog=(0xd1, 0x80 | 0x40 | 7 << 3 | 7),
                           flags=0x90, isrs=0xa7, drained=0x80 | 0x40 | 0x20 | 0x10 | 7), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "it likely never powered down" in out
              and "after 10 ms: DCS's p-state after entry 49 (to 7), after 50 (to 5), or both" in out
              and "; DISPLAY's power-down (58); SIO_CPU's power state (61); a ClkGen mux (62-66) or PLL-off"
              " (69, 72)\n" in out and "was the one after entry 49" not in out
              and "EACC's power-down STILL PENDING at the end of CPU7's watch" in out
              and "PACC0's STILL PENDING at the end" in out and "PACC0's was STILL PENDING just before" in out
              and "EACC,PACC0,PACC1,DISPLAY (" in out and "SIO_CPU's actual state NOT off" in out
              and "actual 7, target 5, busy bit 31 SET (NOT settled at 5); SIO's actual state NOT off" in out
              and "(ECPM) did NOT read off" in out,
              "pmgr_late's 0x7e: every timeout and pending bit decoded", out + r.stderr)

        # 0x7f with 0x6013's and 0x6016's bit 7 lost: neither pair is read,
        # since each went in one command with its marked byte.
        fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x10, 0x29), flags=0x90,
                           isrs=0x04, drained=0x15), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6013 = 0x29: bit 7 is clear, so this byte didn't survive" in out
              and "0x6016 = 0x15: bit 7 is clear, so this byte didn't survive" in out
              and "0x6012 = 0x10: sent in one command with 0x6013, which didn't survive the reset; not read" in out
              and "0x6015 = 0x04: sent in one command with 0x6016, which didn't survive the reset; not read" in out
              and "power-down done" not in out and "DCS's p-state at" not in out
              and "waits that timed out after" not in out and "soc clusters still busy" not in out,
              "pmgr_late's 0x7f with 0x6013 and 0x6016 lost: neither pair read", out + r.stderr)

        # Both pairs zeroed (the case the markers are for): no "every wait met",
        # no "none busy".
        fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0, 0), flags=0x90,
                           isrs=0, drained=0), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "every one of macOS's waits was met" not in out
              and "still busy at the end" not in out and out.count("didn't survive the reset") == 4,
              "pmgr_late's 0x7f with 0x6012-0x6016 zeroed: nothing read as a result", out + r.stderr)

        # 0x58 marked observe: CPU7 never reported; qto and the cores read off
        # from CPU0's command, PCPM1 not checked.
        fake(path, scratch(0x58, parked=0xff, left=0x10, down=0x7e, secs=121544, flags=0x90, isrs=0, drained=0),
             rtc_secs=121761)
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x58 (pmgr_late=1): 8: the last SPMI command" in out
              and "Nothing CPU7 sent after the commit landed" in out
              and "0x6006 = 0x10: macOS's waits that timed out after 10 ms: DISPLAY's power-down (58)\n" in out
              and "read off within 10 ms (0x6007 = 0x7e): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6\n" in out
              and "PCPM1 not checked (CPU7 kept up)" in out and "isn't in 0x6007 and" in out
              and "the SoC restarted about 31 s after the commit" in out,
              "pmgr_late's 0x58 marked observe: CPU7 never reported", out + r.stderr)

        # Every wait timed out: all eight names, in bit order.
        fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0xff, 0x80), flags=0x90,
                           isrs=0x04, drained=0x95), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        names = ["DCS's p-state after entry 49", "AFR's power state (51)", "ANE0's power-down (55)",
                 "AFR's power-down (57)", "DISPLAY's power-down (58)", "AVEMSR's power-down (60)",
                 "SIO_CPU's power state (61)", "a ClkGen mux (62-66) or PLL-off (69, 72)"]
        line = [l for l in out.splitlines() if l.startswith("0x6012 = 0xff")]
        pos = [line[0].find(n) for n in names] if line else []
        check(r.returncode == 0 and len(line) == 1 and all(p > 0 for p in pos) and pos == sorted(pos),
              "pmgr_late's qto 0xff: every wait named, in bit order", out + r.stderr)

        # Each 0x6013 bucket, for EACC and for PACC0.
        want = ["first read clear under 5 ms", "first read clear 5-20 ms", "first read clear 20-50 ms",
                "first read clear 50-100 ms", "first read clear 100-200 ms", "first read clear 200-500 ms",
                "first read clear 500 ms or more", "STILL PENDING at the end of CPU7's watch"]
        for i, w in enumerate(want):
            fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0, 0x80 | i << 3 | i),
                               flags=0x90, isrs=0x04, drained=0x95), rtc_secs=121761)
            r = run(path)
            out = r.stdout
            check(r.returncode == 0 and f"EACC's power-down {w}" in out and f"PACC0's {w}" in out
                  and "PACC0's was done just before the commit" in out,
                  f"pmgr_late's 0x6013 bucket {i}: {w}", out + r.stderr)

        # DCS's wait timed out, and the one after 50 settled: it was 49's.
        fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x01, 0x80), flags=0x90,
                           isrs=0x04, drained=0x95), rtc_secs=121761)
        r = run(path)
        check(r.returncode == 0 and "settled at 5" in r.stdout and "was the one after entry 49" in r.stdout,
              "pmgr_late's DCS timeout with 0x6016 settled: the wait after 49", r.stdout + r.stderr)

        # A stall in step 5 leaves 0x10, which says so.
        fake(path, scratch(0x10, parked=0xff))
        r = run(path)
        check(r.returncode == 0 and "a stall in any of them leaves 0x10" in r.stdout,
              "0x10: step 5's writes and waits came next under it", r.stdout + r.stderr)

        # alarm=1 (M1 run 4). 0x5f with every alarm command acked, armed at the
        # next boot and the compare value the commit's second + 3600.
        fake(path, scratch(0x5f, parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0, isrs=0, drained=0xbd),
             rtc_secs=121761, alarm=(0x41, 121544 + 3600, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x5f (pmgr_late=1): alarm=1: the commit x3 and the alarm's commands done" in out
              and "after 10 ms: DISPLAY's power-down (58); SIO_CPU's power state (61)\n" in out
              and "read off within 10 ms (0x6007 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "RTC second at just before the commit: 121544, 217 s before now" in out
              and "PCPM0 off, PCPM1 off" in out and "observe=1" not in out
              and "0x6016 = 0xbd: every alarm command was acked (no event was set)" in out and "AIC events" not in out
              and "the SoC restarted about 31 s after the commit" in out
              and "PMU alarm: 0x1401 = 0x41 (armed, fires in 3383 s), 0x140c = 125144, 0x14c0 event 0x00, 0x14c2 mask 0x00" in out
              and "0x140c is the commit's second + 3600, as the run wrote it" in out
              and "the armed alarm survived the reset\n" in out,
              "alarm=1's 0x5f, the expected case: every field decoded", out + r.stderr)

        # 0xbf: the event was set and echoed; it fired by the next boot.
        fake(path, scratch(0x5f, parked=0xfe, left=0x00, down=0xfe, secs=121544, flags=0xb0, drained=0xbf),
             rtc_secs=121544 + 4000, alarm=(0x03, 121544 + 3600, 1, 0))
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6016 = 0xbf: every alarm command was acked (the event was set, and echoed)" in out
              and "0x1401 = 0x03 (fired, latched)" in out and "survived the reset and has fired" in out
              and "0x14c0 event 0x01" in out,
              "alarm=1: the event echoed, and the alarm fired before the read", out + r.stderr)

        # The reset or the firmware cleared the enable: the compare value still
        # says the writes landed. And a wrong one says they didn't.
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x01, 121544 + 3600, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "0x1401 = 0x01 (not armed)" in r.stdout
              and "0x1401 reads 0x01 before the alarm's time, with 0x140c right and 0x1401's write acked: the reset or the boot firmware cleared the enable" in r.stdout
              and "survived" not in r.stdout,
              "alarm=1: 0x1401 back at 0x01 with 0x140c right: cleared by the reset or firmware", r.stdout + r.stderr)
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x01, 36502, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "0x140c is NOT the commit's second + 3600 (125144): the alarm wasn't tested" in r.stdout
              and "cleared the enable" not in r.stdout,
              "alarm=1: a stale 0x140c: not tested", r.stdout + r.stderr)

        # A 0x03 or 0x41 that isn't the run's: never "survived".
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0x87), rtc_secs=121761,
             alarm=(0x03, 36502, 1, 0))
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "survived" not in out and "0x1401 isn't shown to be this run's" in out
              and "0x140c is NOT the commit's second + 3600" in out,
              "alarm=1: a stale 0x03 the run never armed isn't read as surviving", out + r.stderr)
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x41, 200000, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "survived" not in r.stdout and "isn't shown to be this run's" in r.stdout,
              "alarm=1: another 0x41 (macOS's, after the run) isn't read as surviving", r.stdout + r.stderr)
        # A stale 0x03 with the right compare value, the helper stopped after 0x140c.
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0x9f), rtc_secs=121761,
             alarm=(0x03, 125144, 1, 0))
        r = run(path)
        check(r.returncode == 0 and "survived" not in r.stdout and "isn't shown to be this run's" in r.stdout,
              "alarm=1: 0x03 with 0x1401's write not acked isn't read as surviving", r.stdout + r.stderr)
        # The commit's second lost: only an acked 0x1401 says it's the run's.
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=0, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x41, 125144, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "the commit's second isn't known" in r.stdout
              and "the armed alarm survived the reset" in r.stdout,
              "alarm=1: commit second unknown, 0x1401 acked: survived", r.stdout + r.stderr)
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=0, flags=0xb0, drained=0x85), rtc_secs=121761,
             alarm=(0x03, 125144, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "can't be told" in r.stdout and "survived" not in r.stdout,
              "alarm=1: commit second unknown, 0x1401 not acked: can't be told", r.stdout + r.stderr)

        # 0x01 with 0x140c right says "cleared the enable" only if 0x1401's write was acked.
        for v, text in ((0x9d, "0x1401's write wasn't acked"), (0x00, "0x6016 recorded no result")):
            fake(path, scratch(0x5f if v else 0x59, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=v),
                 rtc_secs=121761, alarm=(0x01, 125144, 0, 0))
            r = run(path)
            check(r.returncode == 0 and text in r.stdout and "nothing shows the enable was ever set" in r.stdout
                  and "cleared the enable" not in r.stdout,
                  f"alarm=1: 0x6016 = 0x{v:02x} with 0x140c right: the enable isn't said to be cleared",
                  r.stdout + r.stderr)
        # Past the alarm's time, 0x01 may be a fired alarm.
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd),
             rtc_secs=125144 + 100, alarm=(0x01, 125144, 1, 0))
        r = run(path)
        check(r.returncode == 0 and "past the alarm's time" in r.stdout and "it may have fired" in r.stdout
              and "0x14c0 is 0x01" in r.stdout,
              "alarm=1: 0x01 past the alarm's time: it may have fired", r.stdout + r.stderr)

        # The block 0x6014 came in, lost: not read as facts.
        lost = bytearray(scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0, isrs=0, drained=0))
        lost[0x10:0x17] = bytes(7)
        fake(path, bytes(lost), rtc_secs=121761)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6014 = 0x00: bit 7 is clear, so the 7-byte command it went in" in out
              and "PCPM0" not in out and "interrupt pending at their stop" not in out and "AIC events" not in out
              and "the alarm's commands left no result" in out and "A repeat, not a negative" in out,
              "alarm=1: 0x6010-0x6016 zeroed: 0x6014 and 0x6015 aren't read", out + r.stderr)
        fake(path, bytes(lost[:4]) + bytes([0x58]) + bytes(lost[5:]), rtc_secs=121761)
        r = run(path)
        check(r.returncode == 0 and "bit 7 is clear" in r.stdout and "AIC events" not in r.stdout,
              "a 0x58 run with 0x6010-0x6016 zeroed: the AIC count isn't read either", r.stdout + r.stderr)

        # Any other 0x1401, raw.
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x81, 121544 + 3600, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "PMU alarm: 0x1401 = 0x81, 0x140c = 125144" in r.stdout and "survived" not in r.stdout
              and "cleared the enable" not in r.stdout,
              "alarm=1: an unknown 0x1401 is printed raw", r.stdout + r.stderr)

        # 0x59 with no result: the alarm's commands left nothing: a repeat.
        fake(path, scratch(0x59, parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0, drained=0), rtc_secs=121761,
             alarm=(0x01, 36502, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x59 (pmgr_late=1): 8 (alarm=1): the last SPMI command before the commit" in out
              and "0x6016 = 0x00: the alarm's commands left no result" in out and "AIC events" not in out
              and "A repeat, not a negative" in out
              and "the SoC restarted about 31 s after the commit" in out
              and "0x1401 = 0x01 (not armed)" in out,
              "alarm=1's 0x59 with 0x6016 = 0: no result, a repeat", out + r.stderr)

        # 0x59 with the alarm's commands all acked: the two commit writes after
        # them aren't recorded.
        fake(path, scratch(0x59, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x41, 125144, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "0x6016 = 0xbd: every alarm command was acked" in r.stdout
              and "nothing records the two commit writes after those commands" in r.stdout,
              "alarm=1's 0x59 with 0xbd: inconclusive about the last two commit writes", r.stdout + r.stderr)
        fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd), rtc_secs=121761,
             alarm=(0x41, 125144, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "nothing records the two commit writes" not in r.stdout,
              "alarm=1's 0x5f with 0xbd: no such caveat", r.stdout + r.stderr)

        # Partial results: each names the command it stopped at.
        for v, stop in ((0x80, "0x14c0's read"), (0x83, "0x14c0's echo"), (0x85, "0x140c"), (0x8d, "0x14c2 = 0"),
                        (0x9d, "0x1401 = 0x41")):
            fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=v), rtc_secs=121761)
            r = run(path)
            out = r.stdout
            check(r.returncode == 0 and f"0x6016 = 0x{v:02x}: the alarm's commands STOPPED at {stop} (" in out
                  and "so the alarm wasn't tested" in out and (("the event was set" in out) == bool(v & 2))
                  and (v not in (0x85, 0x8d, 0x9d) or ("no echo needed" in out and "0x14c0's echo" not in out)),
                  f"alarm=1's 0x6016 = 0x{v:02x}: stopped at {stop}", out + r.stderr)

        # A byte e1_hook doesn't write.
        for v in (0xff, 0x81, 0x93):
            fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=v), rtc_secs=121761)
            r = run(path)
            check(r.returncode == 0 and f"0x6016 = 0x{v:02x}: not a value e1_hook writes" in r.stdout,
                  f"alarm=1's 0x6016 = 0x{v:02x}: not one of e1_hook's", r.stdout + r.stderr)

        # A clean PMU, and runs without the alarm, still show the registers.
        fake(path, scratch(0), alarm=(0x01, 36502, 0, 0))
        r = run(path)
        check(r.returncode == 0 and "PMU alarm: 0x1401 = 0x01 (not armed), 0x140c = 36502" in r.stdout
              and "the alarm wasn't tested" not in r.stdout and "0x140c is" not in r.stdout,
              "a clean PMU: the alarm's registers, and nothing about a run", r.stdout + r.stderr)

        # --clear after a run that armed the alarm: 0x1401 back to 0x01, the
        # crumbs zeroed, the event, compare and mask left; the registers are
        # printed before anything is written.
        for c, what in ((0x41, "armed"), (0x03, "fired")):
            before = fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0xbd),
                          rtc_secs=121761, alarm=(c, 125144, 1, 0))
            r = run(path, "--clear")
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout
                  and r.stdout.index("PMU alarm:") < r.stdout.index("disarmed") < r.stdout.index("cleared:"),
                  f"--clear after a run that left the alarm {what}: 0x1401 to 0x01 and the crumbs zeroed, nothing else",
                  r.stdout + r.stderr)

        # The commit's second lost, 0x1401's write acked: still the run's alarm,
        # disarmed, and the crumbs zeroed.
        before = fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=0, flags=0xb0, drained=0xbd), rtc_secs=121761,
                      alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        want[0x1401] = 0x01
        check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout
              and "left alone" not in r.stdout,
              "--clear with the commit's second lost and 0x1401 acked: disarmed", r.stdout + r.stderr)

        # The second lost and nothing says 0x1401 was acked: nothing is written,
        # the breadcrumbs stay, exit 2.
        for res in (0x85, 0x00, 0xff, 0xa0):
            before = fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=0, flags=0xb0, drained=res), rtc_secs=121761,
                          alarm=(0x41, 125144, 0, 0))
            r = run(path, "--clear")
            after = open(path, "rb").read()
            check(r.returncode == 2 and after == before and "whose alarm this is can't be told" in r.stderr
                  and "cleared" not in r.stdout and "disarmed" not in r.stdout,
                  f"--clear, 0x6016 = 0x{res:02x}, the second lost, 0x1401 armed: nothing written, exit 2",
                  r.stdout + r.stderr)

        # 0x59 armed too (the reset came between the alarm and 0x5f, say).
        before = fake(path, scratch(0x59, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0), rtc_secs=121761,
                      alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        check(r.returncode == 0 and after[0x1401] == 0x01 and "disarmed" in r.stdout,
              "--clear at stage 0x59 with the alarm armed: disarmed", r.stdout + r.stderr)

        # Nothing armed: 0x1401 isn't written.
        before = fake(path, scratch(0x5f, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=0x85), rtc_secs=121761,
                      alarm=(0x01, 36502, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "disarmed" not in r.stdout,
              "--clear after an alarm run that didn't arm it: 0x1401 left alone", r.stdout + r.stderr)

        # After any other stage, an armed alarm is someone else's (macOS's wakes): left alone, and said so.
        before = fake(path, scratch(0x58, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=1), rtc_secs=121761,
                      alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "disarmed" not in r.stdout and after[0x1401] == 0x41
              and "PMU alarm: 0x1401 = 0x41" in r.stdout and "left alone" in r.stdout,
              "--clear after a run without the alarm: an armed 0x1401 is shown and left alone", r.stdout + r.stderr)

        # An alarm stage, but not the run's alarm (0x140c isn't the commit's second + 3600): left alone.
        for stg, res, c, cmp_ in ((0x5f, 0xbd, 0x41, 200000), (0x59, 0x00, 0x03, 36502), (0x5f, 0x85, 0x03, 36502)):
            before = fake(path, scratch(stg, parked=0xfe, down=0xfe, secs=121544, flags=0xb0, drained=res),
                          rtc_secs=121761, alarm=(c, cmp_, 1, 0))
            r = run(path, "--clear")
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            check(r.returncode == 0 and after == bytes(want) and after[0x1401] == c and "disarmed" not in r.stdout,
                  f"--clear at stage 0x{stg:02x} with 0x1401 = 0x{c:02x} and a compare value that isn't the run's: left alone",
                  r.stdout + r.stderr)

        # --clear after a pmgr_late run: the same 11 bytes, nothing else.
        before = fake(path, scratch(0x7f, parked=0xff, left=0x04, down=29, secs=121544, prog=(0x10, 0xa9),
                                    flags=0x90, isrs=0x24, drained=0x95))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "cleared" in r.stdout and "0x7f" in r.stdout,
              "--clear after a pmgr_late run: 0x6004-0x6007 and 0x6010-0x6016 only", r.stdout + r.stderr)

        # hold=1, the expected reset: every core held, took the release and read
        # off; 0x6005 the cores read off, 0x6012-0x6013 out and pending, the second from its low bytes.
        rec = dict(parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0, isrs=0, drained=0xbd, prog=[0xfe, 0x00])
        fake(path, scratch(0x5e, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x5e (pmgr_late=1): hold=1: everything recorded, CPU0 had nothing pending" in out
              and "read off after the release (0x6005 = 0xfe, up to 10 ms each): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "0x6006 = 0x50: macOS's waits that timed out after 10 ms: DISPLAY's power-down (58); SIO_CPU's power state (61)\n" in out
              and "wrote their stop bit and held (0x6007 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "RTC second just before the commit, from its low two bytes and the run's alarm: 121544, 217 s before now (121761)" in out
              and "PCPM0 off after the release, PCPM1 off after the release" in out
              and "0x6012 = 0xfe: the cores that took the release: CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "0x6013 = 0x00: of those, the ones with an interrupt pending just before their deep WFI: none\n" in out
              and "0x6015 = 0x00: cores with an interrupt pending at their stop request (hold=1): none\n" in out
              and "0x6016 = 0xbd: every alarm command was acked" in out
              and "the SoC restarted about" in out
              and "0x140c is the commit's second + 3600, as the run wrote it" in out
              and "parked (0x6005" not in out and "observe=1" not in out and "boot error" in out,
              "hold=1's 0x5e, the expected reset: every field decoded", out + r.stderr)

        # A core that held but wasn't read off, and one that took the release late.
        fake(path, scratch(0x5e, parked=0x7e, left=0x40, down=0xfe, secs=121544, flags=0x90, isrs=0x10, drained=0xbf,
                           prog=[0x7e, 0x40]),
             rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "read off after the release (0x6005 = 0x7e, up to 10 ms each): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6\n" in out
              and "SIO_CPU's power state (61)\n" in out and "DISPLAY" not in out.split("0x6006")[1].split("\n")[0]
              and "wrote their stop bit and held (0x6007 = 0xfe)" in out
              and "PCPM0 off after the release, PCPM1 NOT off after the release" in out
              and "the cores that took the release: CPU1,CPU2,CPU3,CPU4,CPU5,CPU6\n" in out
              and "deep WFI: CPU6\n" in out and "at their stop request (hold=1): CPU4\n" in out,
              "hold=1: CPU7 held and not read off, P1 not off", out + r.stderr)

        # 0x5d: CPU0 had something pending before its own WFI.
        fake(path, scratch(0x5d, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "0x5d (pmgr_late=1): hold=1: everything recorded, but CPU0 had an interrupt or SError pending" in r.stdout
              and "it likely never went down" in r.stdout
              and "0x6012 = 0xfe: the cores that took the release: CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in r.stdout
              and "0x6013 = 0x00: of those, the ones with an interrupt pending just before their deep WFI: none\n" in r.stdout,
              "hold=1's 0x5d: CPU0 had something pending", r.stdout + r.stderr)

        # 0x5c: the watch's stage: 0x6012-0x6014 aren't read as facts yet, whether
        # the 3-byte command landed or not; the second's low two bytes are still good.
        for kw, what in ((dict(flags=0x80, prog=None), "before the 3-byte command"),
                         (dict(flags=0xb0, prog=(0xfe, 0x00)), "with the 3-byte command landed")):
            fake(path, scratch(0x5c, parked=0xfe, left=0x50, down=0xfe, secs=121544, isrs=0, drained=0xbd, **kw),
                 rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
            r = run(path, cnt=run1)
            out = r.stdout
            check(r.returncode == 0 and "0x5c (pmgr_late=1): hold=1: the release went out and CPU0's watch ended" in out
                  and "read off after the release" in out and "being rewritten" in out
                  and "the cores that took the release" not in out and "deep WFI: " not in out
                  and "PCPM0 being rewritten: not decoded" in out
                  and "from its low two bytes and the run's alarm: 121544, 217 s before now (121761)" in out
                  and "0x6015 = 0x00: cores with an interrupt pending at their stop request (hold=1): none\n" in out,
                  f"hold=1's 0x5c {what}: 0x6012-0x6014 not read as facts, and the second's low two bytes good", out + r.stderr)

        # 0x5b: the commit x3 done, nothing after it; 0x6005 still the parked mask.
        fake(path, scratch(0x5b, parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0x80, isrs=0x04, drained=0xbd),
             rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x5b (pmgr_late=1): 9 (hold=1): the commit x3 and the alarm's commands done" in out
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "wrote their stop bit and held (0x6007 = 0xfe)" in out
              and "RTC second at just before the commit: 121544" in out
              and "PCPM0 not read yet (hold=1: nothing goes down before the commit)" in out
              and "at their stop request (hold=1): CPU2\n" in out
              and "the cores that took the release" not in out and "read off after the release" not in out,
              "hold=1's 0x5b: decoded as before the release", out + r.stderr)

        # 0x5a with the alarm acked: the same caveat as 0x59, naming 0x5b.
        fake(path, scratch(0x5a, parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0x80, isrs=0, drained=0xbd),
             rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "0x5a (pmgr_late=1): 8 (hold=1): the last SPMI command before the commit" in r.stdout
              and "stage 0x5a, so nothing records the two commit writes after those commands (0x5b comes after them)" in r.stdout,
              "hold=1's 0x5a with 0xbd: inconclusive, as 0x59's", r.stdout + r.stderr)

        # 0x5a, no alarm result; 0x39, held mask; 0x33, a core's stop request.
        fake(path, scratch(0x5a, parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0x80, isrs=0, drained=0),
             rtc_secs=121761, alarm=(0x01, 36502, 0, 0))
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "the alarm's commands left no result" in r.stdout and "0x140c is NOT" in r.stdout,
              "hold=1's 0x5a with no alarm result: no alarm tested", r.stdout + r.stderr)
        fake(path, scratch(0x39, parked=0xfe, left=0x50, down=0xfc), rtc_secs=121761)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "0x39 (pmgr_late=1): 7 (hold=1): every core's stop requested" in r.stdout
              and "wrote their stop bit and held (0x6007 = 0xfc): CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in r.stdout,
              "hold=1's 0x39: the cores that held", r.stdout + r.stderr)
        for i, c in enumerate((1, 2, 3, 5, 6, 4, 7)):
            fake(path, scratch(0x31 + i, parked=0xfe, left=0x50), rtc_secs=121761)
            r = run(path, cnt=run1)
            check(r.returncode == 0 and f"6 (hold=1): requesting CPU{c}'s stop, the order's {i + 1} of 7" in r.stdout,
                  f"hold=1's 0x{0x31 + i:02x}: CPU{c}'s stop request", r.stdout + r.stderr)

        # The low two bytes lost, at 0x5e: no made-up second, and --clear goes by 0x6016's acks, or refuses.
        lost = bytearray(scratch(0x5e, **rec))
        lost[0x10:0x12] = bytes(2)
        fake(path, bytes(lost), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6010-0x6011 read 0: the commit's second was lost" in out
              and "RTC second just before" not in out and "the SoC restarted about" not in out
              and "the commit's second isn't known, so 0x140c can't be checked" in out,
              "hold=1's 0x5e with the low two bytes lost: no made-up second, no reset time", out + r.stderr)
        for res, ok in ((0xbd, True), (0xbf, True), (0x85, False), (0x00, False)):
            lost = bytearray(scratch(0x5e, **dict(rec, drained=res)))
            lost[0x10:0x12] = bytes(2)
            before = fake(path, bytes(lost), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
            r = run(path, "--clear")
            after = open(path, "rb").read()
            if ok:
                want = bytearray(before)
                want[0x6004:0x6008] = bytes(4)
                want[0x6010:0x6017] = bytes(7)
                want[0x1401] = 0x01
                check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                      f"--clear at 0x5e, the low two bytes lost, 0x6016 = 0x{res:02x}: disarmed by the acks", r.stdout + r.stderr)
            else:
                check(r.returncode == 2 and after == before and "the commit's second was lost" in r.stderr,
                      f"--clear at 0x5e, the low two bytes lost, 0x6016 = 0x{res:02x}: refuses, nothing written", r.stdout + r.stderr)

        # 0x6012-0x6014 lost at 0x5e: their masks aren't facts, 0x6015's command survived.
        lost = bytearray(scratch(0x5e, **dict(rec, isrs=0x10)))
        lost[0x12:0x15] = bytes(3)
        for s in (0x5e, 0x5d):
            lost[4] = s
            fake(path, bytes(lost), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
            r = run(path, cnt=run1)
            out = r.stdout
            check(r.returncode == 0 and "bit 7 is clear, so the 3-byte command it went in (0x6012-0x6014) didn't survive" in out
                  and "0x6012 0x6013: went in one command with 0x6014, whose bit 7 is clear: not decoded" in out
                  and "the cores that took the release" not in out
                  and "at their stop request (hold=1): CPU4\n" in out,
                  f"hold=1's 0x{s:02x} with 0x6012-0x6014 lost: the masks aren't read, 0x6015 is", out + r.stderr)
        # The whole step-8 command lost too (0x6010-0x6016 zero): nothing read as a fact.
        lost = bytearray(scratch(0x5e, parked=0xfe, left=0x50, down=0xfe))
        lost[0x10:0x17] = bytes(7)
        fake(path, bytes(lost), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6015: step 8's command, whose second's low two bytes read 0: not decoded" in out
              and "the cores that took the release" not in out and "cores with an interrupt pending" not in out,
              "hold=1's 0x5e with 0x6010-0x6016 lost: nothing read as facts", out + r.stderr)

        # The low-two-bytes second across a 16-bit wrap, with and without the run's alarm.
        wrap = dict(parked=0xfe, left=0x50, down=0xfe, secs=0x2fff0, flags=0xb0, isrs=0, drained=0xbd, prog=(0xfe, 0x00))
        fake(path, scratch(0x5e, **wrap), rtc_secs=0x30005)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and f"from its low two bytes: {0x2fff0}, 21 s before now ({0x30005})" in r.stdout,
              "hold=1's second across a 16-bit wrap, no alarm: the 18 h form", r.stdout + r.stderr)
        before = fake(path, scratch(0x5e, **wrap), rtc_secs=0x30005, alarm=(0x41, 0x2fff0 + 3600, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        want[0x1401] = 0x01
        check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout
              and f"from its low two bytes and the run's alarm: {0x2fff0}" in r.stdout,
              "--clear across a 16-bit wrap: the run's alarm disarmed", r.stdout + r.stderr)

        # A read past 18 h: the run's own alarm still pins the second; a stale one isn't taken for it.
        fake(path, scratch(0x5e, **rec), rtc_secs=121544 + 72000, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and f"from its low two bytes and the run's alarm: 121544, {72000} s before now" in out
              and "0x140c is the commit's second + 3600, as the run wrote it" in out,
              "hold=1's 0x5e, read 20 h later: the run's alarm pins the second", out + r.stderr)
        before = fake(path, scratch(0x5e, **rec), rtc_secs=121544 + 72000, alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        check(r.returncode == 0 and after[0x1401] == 0x01 and "disarmed: 0x1401 = 0x01" in r.stdout,
              "--clear, read 20 h later: still the run's alarm, disarmed", r.stdout + r.stderr)

        # A stale compare value that fits the low two bytes (one 16-bit cycle earlier) isn't the run's:
        # 0x6016 must say 0x140c's write was acked. Without that, the recent-time second stands.
        stale = 121544 + 3600 - 65536
        fake(path, scratch(0x5e, **dict(rec, drained=0x85)), rtc_secs=121761, alarm=(0x01, stale, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "from its low two bytes: 121544, 217 s before now (121761)" in out
              and "and the run's alarm" not in out and "the SoC restarted about 31 s after the commit" in out
              and "0x140c is NOT the commit's second + 3600 (125144)" in out,
              "hold=1: a stale, colliding compare with 0x140c's write unacked: not taken for the run's", out + r.stderr)
        before = fake(path, scratch(0x5e, **dict(rec, drained=0x85)), rtc_secs=121761, alarm=(0x41, stale, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "disarmed" not in r.stdout and "left alone" in r.stdout,
              "--clear: a foreign alarm with a colliding compare, 0x140c unacked: left alone", r.stdout + r.stderr)
        # 0x140c acked, a later command not (0x8d): the compare is the run's, so it pins the second.
        fake(path, scratch(0x5e, **dict(rec, drained=0x8d)), rtc_secs=121544 + 72000, alarm=(0x01, 125144, 0, 0))
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "from its low two bytes and the run's alarm: 121544," in r.stdout,
              "hold=1: 0x140c acked and a later command not: the compare still pins the second", r.stdout + r.stderr)

        # The observer's short stages share the formula: low 0 means lost there too.
        fake(path, scratch(0x6f, parked=0xff, left=0x04, down=29, secs=0, prog=(0x12, 0x34), flags=0x90, isrs=0x33, drained=0x85),
             rtc_secs=121761)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "0x6010-0x6011 read 0: the commit's second was lost" in r.stdout
              and "from its low two bytes" not in r.stdout,
              "the observer's 0x6f with the low two bytes lost: no made-up second", r.stdout + r.stderr)

        # --clear after hold=1's stages: 0x5e's second comes from its low two bytes.
        for s, kw in ((0x5e, rec), (0x5d, rec), (0x5c, rec), (0x5c, dict(rec, prog=None, flags=0x80)),
                      (0x5b, dict(rec, prog=None, flags=0x80)), (0x5a, dict(rec, prog=None, flags=0x80))):
            before = fake(path, scratch(s, **kw), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
            r = run(path, "--clear")
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after hold=1's 0x{s:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)
        # An alarm that isn't the run's: left alone, even at 0x5e.
        before = fake(path, scratch(0x5e, **rec), rtc_secs=121761, alarm=(0x41, 200000, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "disarmed" not in r.stdout and "left alone" in r.stdout,
              "--clear after hold=1's 0x5e with another armed alarm: left alone, the crumbs zeroed", r.stdout + r.stderr)
        # Stage 0x39: no alarm yet; an armed one isn't the run's.
        before = fake(path, scratch(0x39, parked=0xfe, left=0x50, down=0xfe), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear")
        after = open(path, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "disarmed" not in r.stdout and "left alone" in r.stdout,
              "--clear after hold=1's 0x39: an armed alarm isn't the run's", r.stdout + r.stderr)

        # A clean run keeps the old stages' meaning: 0x38 and 0x58 still read as pmgr_late's.
        for st in (0x38, 0x58):
            fake(path, scratch(st, parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0), rtc_secs=121761)
            r = run(path, cnt=run1)
            check(r.returncode == 0 and f"0x{st:02x} (pmgr_late=1)" in r.stdout and "hold=1" not in r.stdout,
                  f"stage 0x{st:02x} stays pmgr_late's, not hold's", r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        run1 = "4800000000 24000000"
        # pmp_out=1 on hold=1, the expected reset. Bit 7 of the stage byte says 0x6015 is the PMP's byte.
        rec = dict(parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0, drained=0xbd, prog=[0xfe, 0x00])
        fake(path, scratch(0x5e | 0x80, isrs=0x8e, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0xde (pmp_out=1: bit 7, so the stage is 0x5e) (pmgr_late=1): hold=1: everything recorded" in out
              and "0x6015 = 0x8e: K4 ran: the idle pair returned 0, CPU_STATUS read non-zero by macOS's mask (low bits 10; the load log has the awake value), and RUN read 1 before its clear and reads 0 after the write: the PMP's CPU is stopped\n" in out
              and "cores with an interrupt pending" not in out
              and "read off after the release (0x6005 = 0xfe" in out
              and "0x6016 = 0xbd: every alarm command was acked" in out
              and "0x140c is the commit's second + 3600, as the run wrote it" in out,
              "pmp_out=1's 0xde, the expected reset: the PMP's byte, not the cores' interrupts", out + r.stderr)
        fake(path, scratch(0x5e | 0x80, isrs=0xce, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "the PMP's CPU is stopped; some core had an interrupt pending at its stop\n" in r.stdout,
              "pmp_out=1: bit 6 is the cores' interrupts, reduced to a bit", r.stdout + r.stderr)

        def pmp(byte, stage=0xde):
            fake(path, scratch(stage, isrs=byte, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
            r = run(path, cnt=run1)
            return r.returncode, r.stdout, r.stderr

        for byte, want, what in (
                (0x83, "CPU_STATUS read non-zero by macOS's mask (low bits 11; the load log has the awake value); RUN wasn't touched (pmp_stop=0)", "pmp_stop=0, idle seen"),
                (0x80, "CPU_STATUS's low two bits read 0 for the whole 10 ms", "the status never read idle: RUN not cleared"),
                (0x90, "the idle pair TIMED OUT (-ETIME): an ack never came within 1 s (so SetIOPPower may not have gone out)", "-ETIME: the PMP's state unknown"),
                (0xa0, "firmware had CRASHED by the idle pair (the pair may not have been sent)", "the firmware crashed"),
                (0xb0, "the idle pair FAILED with another error", "another error"),
                (0x89, "RUN was already 0 before the write, which nothing predicted; it reads 0 after", "RUN already 0 before the write"),
                (0x86, "RUN read 1 before its clear and 1 after the write: NOT stopped", "RUN stuck at 1 after the write")):
            rc, out, err = pmp(byte)
            check(rc == 0 and want in out and f"0x6015 = 0x{byte:02x}: K4 ran" in out, f"pmp_out=1's byte: {what}", out + err)
        rc, out, err = pmp(0x06)
        check(rc == 0 and "bit 7 is clear, so the PMP's byte didn't land or didn't survive the reset" in out and "K4 ran" not in out,
              "pmp_out=1's byte, bit 7 clear: not decoded", out + err)
        # Stage 0x5a tagged: the alarm's inconclusive case still reads, and the second stays whole.
        early = dict(parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0, drained=0xbd)
        fake(path, scratch(0xda, isrs=0x8e, **early), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, cnt=run1)
        out = r.stdout
        check(r.returncode == 0 and "0xda (pmp_out=1: bit 7, so the stage is 0x5a)" in out and "stage 0x5a, so nothing records the two commit writes" in out
              and "K4 ran" in out and "RTC second at just before the commit: 121544, 217 s before now" in out,
              "pmp_out=1's 0xda: decoded as hold's 0x5a, its second whole, plus the PMP's byte", out + r.stderr)
        for raw, want in ((0xdc, "0x6012-0x6014: being rewritten when the breadcrumbs stopped: not decoded"),
                          (0xdd, "CPU0 had an interrupt or SError pending just before its acks")):
            fake(path, scratch(raw, isrs=0x8e, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
            r = run(path, cnt=run1)
            check(r.returncode == 0 and want in r.stdout and "K4 ran" in r.stdout and "from its low two bytes and the run's alarm: 121544" in r.stdout,
                  f"pmp_out=1's 0x{raw:02x}: decoded as hold's, with the second from the alarm", r.stdout + r.stderr)
        # K4's own stage, and a stage 0x02 run: K4's kernel byte, or a stall.
        fake(path, scratch(0x83, isrs=0x8e, secs=4900), rtc_secs=5000)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "0x6004 = 0x83 (pmp_out=1: bit 7, so the stage is 0x3)" in r.stdout and "K4 (pmp_out=1): the PMP's idle pair" in r.stdout
              and "0x6015 = 0x8e: K4 ran" in r.stdout, "stage 0x83: K4 done, its byte decoded", r.stdout + r.stderr)
        fake(path, scratch(0x02, secs=4900), rtc_secs=5000)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "K4 didn't finish, CPU0 stalled or faulted in it" in r.stdout and "K4 ran" not in r.stdout,
              "stage 0x02 with no byte: says K4 may not have finished, and reads no PMP byte", r.stdout + r.stderr)
        fake(path, scratch(0x02, isrs=0x8e, secs=4900), rtc_secs=5000)
        r = run(path, cnt=run1)
        check(r.returncode == 0 and "0x6015 = 0x8e: K4 ran" in r.stdout and "K4 finished, its stage write didn't land" in r.stdout,
              "stage 0x02 with K4's byte: it finished, and only its stage write was lost", r.stdout + r.stderr)
        # Tagged stages decode like the plain ones through the park and the table.
        for raw, want in ((0x90, "4: all 7 other cores parked"), (0xb1, "requesting CPU1's stop"), (0xb9, "every core's stop requested"), (0xa8, "pmgr_late")):
            fake(path, scratch(raw, parked=0xfe, left=0x50, down=0xfe, secs=4900, flags=0xb0, isrs=0x8e), rtc_secs=5000)
            r = run(path, cnt=run1)
            check(r.returncode == 0 and want in r.stdout and "K4 ran" in r.stdout, f"stage 0x{raw:02x}: decoded with the tag off", r.stdout + r.stderr)
        # --clear with the tag and no way to attribute the alarm: refuses, and keeps everything.
        lost = dict(parked=0xfe, left=0x50, down=0xfe, secs=0, flags=0xb0, drained=0x85, prog=[0xfe, 0x00])
        orig = fake(path, scratch(0xde, isrs=0x8e, **lost), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear", cnt=run1)
        with open(path, "rb") as f:
            after = f.read()
        check(r.returncode == 2 and after == orig and "the commit's second was lost" in r.stderr,
              "--clear on a tagged stage with its second lost and 0x6016 unacked refuses and writes nothing", r.stdout + r.stderr)
        # --clear with the tag: the run's alarm is disarmed and the crumbs zeroed.
        orig = fake(path, scratch(0xde, isrs=0x8e, **rec), rtc_secs=121761, alarm=(0x41, 125144, 0, 0))
        r = run(path, "--clear", cnt=run1)
        with open(path, "rb") as f:
            after = f.read()
        want = bytearray(orig)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        want[0x1401] = 0x01
        check(r.returncode == 0 and after == bytes(want) and "disarmed" in r.stdout and "cleared" in r.stdout,
              "--clear after a pmp_out=1 run (tagged stage): disarms the alarm and zeroes the crumbs", r.stdout + r.stderr)

    # pmp_out=1, e1_hook.c's side of the tag and of 0x6015, which the bytes above only imitate.
    src = open(HOOK).read()
    k4 = src[src.index("static u8 m1_pmp_out(void)"):]
    k4 = k4[:k4.index("\n}\n")]
    check("static const u8 stage = 0x83;" in k4 and "m1_pmp_tag = BIT(7);" in k4 and "t[0] |= m1_pmp_tag;" in src
          and "res |= (apple_rtkit_is_crashed(m1_pmp_rtk) ? 2 : ret == -ETIME ? 1 : 3) << 4;" in k4
          and "res |= BIT(2);" in k4 and "res |= BIT(3);" in k4 and "res |= st & M1_ASC_IDLE;" in k4
          and "pmp_stop && (st & M1_ASC_IDLE)" in k4 and "ctrl & ~M1_ASC_CPU_RUN" in k4
          and k4.count("writel(") == 1 and k4.count("apple_rtkit_idle(") == 1
          and "b[5] = pmp_out ? pmpb | (isrs ? BIT(6) : 0) : isrs;" in src,
          "e1_hook.c's K4 writes the byte and the tag m1-crumbs reads, one RUN write, one idle")

    # m1-crumbs' tables against e1_hook.c's: each qto bit names the entries
    # its waits follow, and the buckets' edges are m1_bucket_ms[].
    g = runpy.run_path(TOOL, run_name="m1_crumbs")
    src = open(HOOK).read()
    block = src[src.index("} m1_waits[] = {"):]
    block = block[:block.index("};")]
    waits = [(int(i), int(bit)) for i, bit in re.findall(r"\{\s*(\d+),\s*M1_W_\w+,\s*BIT\((\d)\)", block)]
    by_bit = {}
    for idx, bit in waits:
        by_bit.setdefault(bit, []).append(idx)
    def entries(text):
        """The entry numbers a WAITS text names, ranges ("62-66") expanded."""
        return {n for a, b in re.findall(r"(\d+)(?:-(\d+))?", text) for n in range(int(a), int(b or a) + 1)}

    named = all(i in entries(g["WAITS"][bit]) for bit, ids in by_bit.items() for i in ids)
    ms = [int(x) for x in re.search(r"m1_bucket_ms\[\] = \{([^}]*)\}", src).group(1).split(",")]
    edges = ["under 5 ms"] + [f"{a}-{b} ms" for a, b in zip(ms, ms[1:])] + [f"{ms[-1]} ms or more"]
    nwaits = re.search(r"after (\d+) of them", g["LATE_TEXT"][0x28])
    check(len(waits) == 15 and sorted(by_bit) == list(range(8)) and len(g["WAITS"]) == 8 and named
          and ms[0] == 5 and len(g["BUCKETS"]) == len(ms) + 2
          and all(e in g["BUCKETS"][i] for i, e in enumerate(edges)) and "PENDING" in g["BUCKETS"][-1]
          and nwaits and int(nwaits.group(1)) == len(waits),
          "m1-crumbs' WAITS, BUCKETS and wait count match e1_hook.c's m1_waits[] and m1_bucket_ms[]",
          f"waits {waits}\nms {ms}\nWAITS {g['WAITS']}\nBUCKETS {g['BUCKETS']}")

    # The real counter, on this machine: older than the kernel's uptime.
    if os.uname().machine == "aarch64":
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nvmem")
            fake(path, scratch(0x50, parked=0xfe, left=0xfe, down=0xfe, secs=1000, flags=0xb0), rtc_secs=2000)
            env = dict(os.environ, M1_CRUMBS_NVMEM=path)
            env.pop("M1_CRUMBS_CNTVCT", None)
            up = float(open("/proc/uptime").read().split()[0])
            r = subprocess.run([sys.executable, "-I", TOOL], env=env, capture_output=True, text=True, timeout=30)
            age = [l for l in r.stdout.splitlines() if "its counter (CNTVCT) is" in l]
            ok = len(age) == 1 and float(age[0].split("its counter (CNTVCT) is ")[1].split(" s old")[0]) > up
            check(r.returncode == 0 and ok, "the real counter: read, and older than the kernel's uptime",
                  r.stdout + r.stderr)

    # m1-crumbs' alarm registers, stages and its 3600 s against e1_hook.c's.
    defs = {k: int(v, 0) for k, v in re.findall(r"#define (M1_PMU_ALARM\w*|M1_ALARM_\w+)\s+(0x[0-9a-f]+|\d+)", src)}
    check(defs.get("M1_PMU_ALARM_CTRL") == g["ALARM_CTRL"] and defs.get("M1_PMU_ALARM") == g["ALARM"]
          and defs.get("M1_PMU_ALARM_EVENT") == g["ALARM_EVENT"] and defs.get("M1_PMU_ALARM_MASK") == g["ALARM_MASK"]
          and defs.get("M1_ALARM_SECS") == g["ALARM_SECS"]
          and g["ALARM_STAGES"] == (0x59, 0x5f, 0x5a, 0x5b, 0x5c, 0x5d, 0x5e, 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x4e, 0x4f, 0x52, 0x4b, 0x3d, 0x1d, 0x15, 0x19, 0x46, 0x47, 0x56, 0x57, 0x51, 0x53, 0x54, 0x55, 0x3e, 0x3f, 0x1e, 0x1f, 0x16, 0x17, 0x1a, 0x1b)
          and "alarm ? 0x59 : 0x58" in src and "m1_stage(0x5f)" in src and "hold ? 0x5a : alarm ? 0x59 : 0x58" in src
          and "m1_stage(sio_stop ? 0x4f : 0x5b)" in src and "b[0] = 0x5c" in src and "? 0x5d : 0x5e" in src and "hold ? 0x39 :" in src
          and "hold ? 0x31 :" in src,
          "m1-crumbs' alarm registers, stages and seconds match e1_hook.c's", f"{defs}")

    # hold=1's stages mean one thing: none is another run's stage. (STAGES and
    # OBSERVE_STAGES share 0x10, 0x30 and 0x50 by design, so only HOLD is checked.)
    others = set(g["STAGES"]) | set(g["OBSERVE_STAGES"]) | set(g["LATE"]) | set(range(0x21, 0x28))
    check(not set(g["HOLD"]) & others, "hold=1's stages collide with no other run's",
          f"{sorted(set(g['HOLD']) & others)}")

    position_tests(g, src)
    cluster_ack_tests(g, src)
    mark_tests(g, src)
    gates_mark_tests(g, src)
    sio_down_tests(g, src)
    pcie_down_tests(g, src)
    a2_pre_tests(g, src)
    sio_steps_tests(g, src)
    with tempfile.TemporaryDirectory() as tmp:
        ppath = os.path.join(tmp, "nvmem")

        def pdec(stage, parked, left, down, at):
            # at: 0x6010, where the probe leaves the step it has reached (the low byte of a run's second).
            fake(ppath, scratch(stage, parked=parked, left=left, down=down, secs=at), rtc_secs=121761)
            r = run(ppath)
            return r.returncode, r.stdout, r.stderr

        probe_tests(g, src, pdec)
        look_tests(g, src, pdec)
        gate_tests(g, src, pdec)
        pre_tests(g, src)
        before = fake(ppath, scratch(0x0e, parked=0xf7, left=0xf7, down=0x8d, secs=21), rtc_secs=121761, alarm=(0x41, 999999, 0, 0))
        r = run(ppath, "--clear")
        after = open(ppath, "rb").read()
        want = bytearray(before)
        want[0x6004:0x6008] = bytes(4)
        want[0x6010:0x6017] = bytes(7)
        check(r.returncode == 0 and after == bytes(want) and "disarmed" not in r.stdout,
              "--clear after the probe's 0x0e: its bytes zeroed, and an alarm that isn't a run's left alone", r.stdout + r.stderr)
        # A clear whose write of either block fails leaves the stage, which is what the launchers go by (Pullfrog on #80: the stage was
        # zeroed first, and a gate test's reads of the lane could be left under it).
        rec = bytearray(scratch(0x0f, parked=0xfd, secs=0x28))
        rec[0x11:0x17] = bytes((0xff, 0x1f, 0x0f, 0x10, 0x0f, 0x10))
        for bad_off in (0x6010, 0x6004):
            before = fake(ppath, bytes(rec), rtc_secs=121761)
            r = subprocess.run([sys.executable, "-I", "-c", CLEAR_FAILS, hex(bad_off), TOOL], capture_output=True, text=True, timeout=30,
                               env=dict(os.environ, M1_CRUMBS_NVMEM=ppath, M1_CRUMBS_CNTVCT="2400000000 24000000"))
            after = open(ppath, "rb").read()
            check(r.returncode != 0 and "cleared" not in r.stdout and "injected" in r.stderr and after[0x6004] == 0x0f
                  and (after == before if bad_off == 0x6010 else after[0x6010:0x6017] == bytes(7) and after[0x6005] == 0xfd),
                  f"--clear with its write at {bad_off:#x} failing: the stage is still there, so nothing takes the PMU for clear",
                  r.stdout + r.stderr + after[0x6000:0x6020].hex(" "))

    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


ACK_PRELUDE = r"""
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((~0U << (l)) & (~0U >> (31 - (h))))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define __iomem
#define cpu_relax() do { } while (0)
static bool cluster_ack;
/* m1_wait_done()'s world: a word that reads a scripted sequence (its last value from then on), and a clock that ticks once a look. */
static void *post_map[128];
static u64 m1_tps = 1000, fake_now;
static const u32 *seq;
static unsigned int nseq, reads;
static u64 m1_now(void) { return fake_now++; }
static u32 readl(void *p) { unsigned int i = reads++; (void)p; return seq[i < nseq ? i : nseq - 1]; }
"""
ACK_MAIN = r"""
static const u32 s0[] = { 0 }, s1[] = { 0x20 }, s2[] = { 0x10, 0x10, 0 }, s3[] = { 0x10, 0x30, 0x20 }, s4[] = { 0x40 },
	s5[] = { 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x3b, 0x3c, 0x3d, 0x3e, 0x3f, 0x71 };
static const struct { const u32 *s; unsigned int n; } seqs[] = { { s0, 1 }, { s1, 1 }, { s2, 3 }, { s3, 3 }, { s4, 1 }, { s5, 16 } };

int main(void)
{
	static const u32 vals[] = { 0, 0x10, 0x20, 0x30, 0x0f, 0x2f, 0x1f, 0x3f, 1, 0x40, 0x100, 0x80000000, 0x40000000, 0x55, 0x57, 0x75, 0x77, 0xf0 };
	unsigned int a, i, k;

	for (a = 0; a < 2; a++) {
		cluster_ack = a;
		for (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++)
			printf("val %u %u %llx %u %llx %llx\n", a, i, (unsigned long long)e1_post_slpa[i].addr, e1_post_slpa[i].width,
			       (unsigned long long)e1_post_slpa[i].val, (unsigned long long)m1_post_val(i));
		for (k = 0; k < ARRAY_SIZE(m1_waits); k++)
			for (i = 0; i < ARRAY_SIZE(vals); i++)
				printf("met %u %u %u %u %llx %x %d\n", a, k, m1_waits[k].idx, m1_waits[k].kind,
				       (unsigned long long)m1_waits[k].addr, vals[i], m1_wait_met(&m1_waits[k], vals[i]));
		/* A wait that wasn't met, by its last read: what it leaves in qto and in 0x6014's byte. */
		for (k = 0; k < ARRAY_SIZE(m1_waits); k++)
			for (i = 0; i < ARRAY_SIZE(vals); i++) {
				u8 qto = 0, flags = 0;

				m1_wait_unmet(&m1_waits[k], vals[i], &qto, &flags);
				printf("unmet %u %u %u %x %x %x %x\n", a, m1_waits[k].idx, m1_waits[k].kind, vals[i], m1_waits[k].qto, qto, flags);
			}
		/* m1_wait_done() itself, on ANE0's cluster wait and on the first wait of all, DCS's. */
		for (k = 0; k < ARRAY_SIZE(m1_waits); k++) {
			if (k && m1_waits[k].idx != M1_ACK_ANE0)
				continue;
			for (i = 0; i < ARRAY_SIZE(seqs); i++) {
				u32 last = 0xdeadbeef;
				bool ret;

				seq = seqs[i].s;
				nseq = seqs[i].n;
				reads = 0;
				ret = m1_wait_done(&m1_waits[k], &last);
				printf("done %u %u %u %u %d %u %x\n", a, m1_waits[k].idx, m1_waits[k].kind, i, ret, reads, last);
			}
		}
	}
	return 0;
}
"""


def cluster_ack_tests(g, src):
    """cluster_ack=1, e1_hook.c's side: its table, its waits and the two functions that decide what step 5 writes and waits
    for, compiled as they stand; then the places that use them."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    defs = "".join(line + "\n" for line in src.splitlines() if re.match(r"#define (M1_ACK_\w+|M1_F_ACK\w*|M1_W_(CLEAR|SETTLED|PSTATE|CLUSTER))\s", line))
    code = (ACK_PRELUDE + cut("static const struct e1_mmio_write {", "\n};\n") + defs + cut("static const struct m1_wait {", "\n};\n")
            + cut("static bool m1_wait_met(", "\n}\n") + cut("static u64 m1_post_val(", "\n}\n")
            + cut("static bool m1_wait_done(", "\n}\n") + cut("static void m1_wait_unmet(", "\n}\n") + ACK_MAIN)
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "ack.c")
        with open(c, "w") as f:
            f.write(code)
        exe = os.path.join(tmp, "ack")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", exe, c], check=True)
        rows = [line.split() for line in subprocess.run([exe], capture_output=True, text=True, check=True).stdout.splitlines()]
    val = {(int(r[1]), int(r[2])): (int(r[3], 16), int(r[4]), int(r[5], 16), int(r[6], 16)) for r in rows if r[0] == "val"}
    n = len({i for _, i in val})
    plain = [(i, v) for (a, i), v in sorted(val.items()) if a == 0 and v[2] != v[3]]
    changed = {i: v for (a, i), v in val.items() if a == 1 and v[2] != v[3]}
    check(n == 74 and not plain, "without cluster_ack=1, m1_post_val() gives every one of the table's 74 values as it stands", f"{n} {plain}")
    # Addresses, widths and the table's own values, written out here: Corey's OK is for these three words and 0x20, and nothing else.
    check(changed == {55: (0x28e09800c, 4, 0, 0x20), 57: (0x28e098014, 4, 0, 0x20), 60: (0x28e098020, 4, 0, 0x20)},
          "with cluster_ack=1, exactly three values change: ANE0's, AFR's and AVEMSR's soc-cluster words, 4 bytes each, from the table's 0 to 0x20",
          str(sorted(changed.items())))
    others = sorted((i, hex(v[0]), hex(v[3])) for (a, i), v in val.items() if a == 1 and 0x28e098000 <= v[0] < 0x28e098100 and i not in changed)
    check(others == [(52, "0x28e098000", "0x0"), (53, "0x28e098004", "0x20"), (54, "0x28e098008", "0x20"), (58, "0x28e098018", "0x20")],
          "and the other four soc-cluster words go out as macOS has them: EACC 0, PACC0, PACC1 and DISPLAY 0x20", str(others))
    met = {}
    for r in rows:
        if r[0] == "met":
            met.setdefault((int(r[2]), int(r[3]), int(r[4]), int(r[5], 16)), {})[(int(r[1]), int(r[6], 16))] = int(r[7])
    kind = {k: int(v) for k, v in re.findall(r"#define M1_W_(CLEAR|SETTLED|PSTATE|CLUSTER)\s+(\d+)", src)}
    cluster = {(idx, addr): m for (_, idx, k, addr), m in met.items() if k == kind["CLUSTER"]}
    check(sorted(cluster) == [(55, 0x28e09800c), (57, 0x28e098014), (58, 0x28e098018), (60, 0x28e098020)]
          and all(m[(0, v)] == (not v & 0x10) and m[(1, v)] == (v == 0) for m in cluster.values() for _, v in m),
          "the four cluster waits (ANE0, AFR, DISPLAY, AVEMSR): bit 4 clear as macOS polls it, and with cluster_ack=1 the whole word 0",
          str({k: sorted(m.items()) for k, m in cluster.items()}))
    rest = [(k, m) for k, m in met.items() if k[2] != kind["CLUSTER"] and any(m[(0, v)] != m[(1, v)] for _, v in m)]
    check(len(met) == 15 and not rest, "the other eleven waits are the same with and without cluster_ack=1", str(rest[:2]))

    # A wait that wasn't met: its qto bit, but for a cluster wait with cluster_ack=1, which leaves it only if the flag stayed,
    # and 0x6014's bit 5 if anything else did. Read here from the layout, for every wait and value.
    unmet = [(int(r[1]), int(r[2]), int(r[3]), int(r[4], 16), int(r[5], 16), int(r[6], 16), int(r[7], 16)) for r in rows if r[0] == "unmet"]
    bad = [u for u in unmet
           if (u[5], u[6]) != (((u[4] if u[3] & 0x20 else 0), (0x20 if u[3] & ~0x20 else 0)) if u[0] and u[2] == kind["CLUSTER"] else (u[4], 0))]
    check(len(unmet) == 2 * 15 * 18 and not bad,
          "an unmet wait leaves its qto bit; with cluster_ack=1 a cluster wait leaves it only if bit 5 was set, and 0x6014's bit 5 if any other bit was",
          str(bad[:4]))
    # m1_wait_done(): met at the first read that meets it, 10 ms and no more (11 looks at this clock), and *last the deciding read.
    done = {(int(r[1]), int(r[3]), int(r[4])): (int(r[5]), int(r[6]), int(r[7], 16)) for r in rows if r[0] == "done"}
    C = kind["CLUSTER"]
    want = {}
    for a in (0, 1):
        want[(a, C, 0)] = (1, 1, 0)                                 # reads 0 at once
        want[(a, C, 1)] = (1, 1, 0x20) if not a else (0, 11, 0x20)  # the flag alone: met on bit 4, never as a whole word
        want[(a, C, 2)] = (1, 3, 0)                                 # busy twice, then 0
        want[(a, C, 3)] = (1, 3, 0x20) if not a else (0, 11, 0x20)  # busy, then the flag left
        want[(a, C, 4)] = (1, 1, 0x40) if not a else (0, 11, 0x40)  # another bit
        want[(a, C, 5)] = (0, 11, 0x3b)                             # busy for good: the eleventh look's value
    got = {key: v for key, v in done.items() if key[1] == C}
    other = sorted((key, v) for key, v in done.items() if key[1] != C)
    check(got == want and len(other) == 12 and all(other[i][1] == other[i + 6][1] for i in range(6)),
          "m1_wait_done(): a cluster word is met on bit 4, or with cluster_ack=1 only when it reads 0; it gives up after 10 ms either way, with the"
          " last value it read; another wait is the same with and without cluster_ack=1", f"{sorted(got.items())}\n{other}")

    # Step 5 writes through m1_post_val() and nothing else, and records the result where m1-crumbs reads it.
    step5 = cut("/* 5: the table's other entries", "\tdsb(sy);\n\tif (pmgr_late) {")
    check(step5.count("m1_post_val(i), post_map[i]);") == 2 and "w->val" not in step5 and len(re.findall(r"write\w*\(", step5)) == 2
          and "writeq_relaxed(m1_post_val(i), post_map[i]);" in step5 and "writel_relaxed(m1_post_val(i), post_map[i]);" in step5,
          "step 5's two writes, 8 and 4 bytes, both take m1_post_val()'s value, and it writes nothing else", step5)
    check("\t\t\tif (!m1_wait_done(&m1_waits[k], &v))\n\t\t\t\tm1_wait_unmet(&m1_waits[k], v, &qto, &flags);\n" in step5
          and step5.count("qto") == 1 and step5.count("flags") == 1,
          "step 5 hands every unmet wait, with its last read, to m1_wait_unmet(), and touches qto and the flags nowhere else", step5)
    # The three values go out in step 5 and nowhere else: M1_ACK_VAL is m1_post_val()'s alone, m1_post_val() is called by step 5, by the
    # load's count of changed values and by its log, and the load's own function writes nothing.
    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))  # no comments, no strings' text
    init = cut("static void __init m1_ack_init(void)", "\n}\n")
    check(len(re.findall(r"\bM1_ACK_VAL\b", code_only)) == 3 and "\t\treturn M1_ACK_VAL;\n" in cut("static u64 m1_post_val(", "\n}\n")
          and "static_assert(M1_ACK_VAL == M1_ACK_FLAG);" in src
          and len(re.findall(r"\bm1_post_val\(", code_only)) == 5 and init.count("m1_post_val(") == 1 and not re.search(r"write\w*\(", init),
          "0x20 is m1_post_val()'s alone, and nothing but step 5 writes what it returns: the load only counts and logs", init)
    # The mark is set before the table goes out and reaches 0x6014 with step 8's command; nothing else in the run sets the two bits.
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    sets = re.findall(r"flags \|= ([^;]+);", after)
    check(in_order_of(after, ["\tif (cluster_ack)\n\t\tflags |= M1_F_ACK;\n", "/* 5: the table's other entries", "/* 8 */", "b[4] = flags;",
                              "m1_crumb(0x6010, b, 7);", "/* 9 */"])
          and sets == ["BIT(3)", "BIT(6)", "BIT(6)", "M1_F_ACK", "BIT(4)", "BIT(5)", "(read_sysreg(isr_el1) >> 6) & 7"]
          and "if (!hold && cpu == 4 && m1_wait_off(M1_PS_PCPM0))\n\t\t\tflags |= BIT(4);" in after
          and "if (!hold && cpu == 7 && m1_wait_off(M1_PS_PCPM1))\n\t\t\tflags |= BIT(5);" in after
          and src.count("M1_F_ACK;") == 1 and len(re.findall(r"\bM1_F_ACK_REST\b", code_only)) == 2,
          "0x6014's bit 4 is set once, by cluster_ack=1, before the table, and goes out with step 8's command; in a hold=1 run nothing else there"
          " sets bit 4 or 5", str(sets))
    d = dict(re.findall(r"#define (M1_F_ACK\w*|M1_ACK_BUSY|M1_ACK_FLAG|M1_ACK_VAL)\s+(\S+)", src))
    bits = [int(b) for b in re.findall(r"\{\s*\d+,\s*M1_W_CLUSTER,\s*BIT\((\d)\),\s*M1_ACK_BUSY,", src)]
    check(d == {"M1_F_ACK": "BIT(4)", "M1_F_ACK_REST": "BIT(5)", "M1_ACK_BUSY": "BIT(4)", "M1_ACK_FLAG": "BIT(5)", "M1_ACK_VAL": "0x20U"}
          and g["F_ACK"] == 0x10 and g["F_ACK_REST"] == 0x20 and g["CLUSTER_WAITS"] == sum(1 << b for b in bits) == 0x3c and g["ACK_WROTE"] == 0x2c,
          "m1-crumbs' mark, its other bit and the cluster waits are e1_hook.c's", f"{d} {bits}")
    idx = {2: 55, 3: 57, 4: 58, 5: 60}
    check(sorted(g["ACK_WORDS"]) == [2, 3, 4, 5] and all(f"({i}" in g["ACK_WORDS"][b] for b, i in idx.items())
          and ["the table's own 0x20" in g["ACK_WORDS"][b] for b in idx] == [False, False, True, False]
          and {b for b in idx if g["ACK_WROTE"] >> b & 1} == {2, 3, 5},
          "m1-crumbs names each cluster word's entry, and which three cluster_ack=1 wrote and which the table already acks", str(g["ACK_WORDS"]))
    # The stages m1-crumbs reads the mark at, and the ones it says can't tell, are the run's: bit 7 from K4 on, 0x28 after the table,
    # 0x31-0x37 and 0x39 for hold=1's stop requests, 0x4e before the commit, wd2_bite=1's two after it; and fabric_up=1's and
    # gates_mark=1's in place of those three.
    check(g["ACK_EARLY"] == (0x28, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x39)
          and g["ACK_MARKED"] == (0x4e, 0x56, 0x57, 0x52, 0x51, 0x53, 0x4b, 0x54, 0x55, 0x3d, 0x3e, 0x3f, 0x1d, 0x1e, 0x1f, 0x15, 0x16, 0x17, 0x19, 0x1a, 0x1b)
          and "b[0] = 0x28;" in after and "m1_stage((hold ? 0x31 : pmgr_late ? 0x29 : 0x21) + i);" in after
          and "b[0] = hold ? 0x39 : pmgr_late ? 0x38 : 0x30;" in after and "b[0] = fabric_up ? M1_FAB_STAGE_PRE : sio_stop ? 0x4e : hold ? 0x5a : alarm ? 0x59 : 0x58;" in after
          and "else if (!recfg_bite && !wd2_bite)\n\t\t\tm1_stage(sio_stop ? 0x4f : 0x5b);" in after
          and "#define M1_WD2_STAGE\t\t0x57" in src and "#define M1_WD2_STAGE_PEND\t0x56" in src,
          "the stages m1-crumbs takes for a cluster_ack=1 run's are the ones e1_hook.c sends on that recipe")
    # The two bits are PCPM0's and PCPM1's wherever CPU0 watches the cores down: cluster_ack=1 only loads where it never does.
    release = cut("static void m1_hold_release(", "\n}\n")
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool cluster_ack;\nmodule_param(cluster_ack, bool, 0444);\n" in src and not re.search(r"\bcluster_ack\s*=[^=]", code_only)
          and "wd2_bite && (!ps_words || recfg_bite)" in src and "ps_words && !sio_stop" in src
          and "sio_stop && (!hold_nowatch || !disp_off || !one_commit || !smc_idle || position)" in src and "hold_nowatch && !hold" in src
          and release.index("if (hold_nowatch)\n\t\treturn;") < release.index("flags |= BIT(4);"),
          "cluster_ack is off unless asked for, read-only once loaded, and on wd2_bite=1's recipe, hold_nowatch=1 always, nothing else sets 0x6014's"
          " bits 4 and 5")
    check(in_order_of(params, ["\tif (wd2_bite && (!ps_words || recfg_bite)) {\n",
                               "\tif (cluster_ack && (!wd2_bite || !pmp_stop)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n\tif (cluster_ack) {\n",
                               "{ M1_ACK_ANE0, 0x28e09800cULL }, { M1_ACK_AFR, 0x28e098014ULL }, { M1_ACK_AVEMSR, 0x28e098020ULL },",
                               "\t\tfor (i = 0; i < ARRAY_SIZE(ack); i++) {\n",
                               "if (w->addr != ack[i].addr || w->width != 4 || w->val || e1_post_early(w) || e1_never_write(w->addr))\n\t\t\t\tgoto bad_ack;",
                               "\t\t\tfor (k = 0; k < ARRAY_SIZE(m1_waits); k++)\n\t\t\t\tif (m1_waits[k].idx == ack[i].idx && m1_waits[k].kind == M1_W_CLUSTER &&\n"
                               "\t\t\t\t    m1_waits[k].mask == M1_ACK_BUSY)\n\t\t\t\t\tbreak;\n\t\t\tif (k == ARRAY_SIZE(m1_waits))\n\t\t\t\tgoto bad_ack;\n",
                               "\t\tfor (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++)\n\t\t\tn += m1_post_val(i) != e1_post_slpa[i].val;\n"
                               "\t\tif (n != ARRAY_SIZE(ack))\n\t\t\tgoto bad_ack;\n\t}\n",
                               "\treturn 0;\n\nbad_wait:", "bad_ack:\n\tpr_err(", "\treturn -EINVAL;\n}"])
          and params.count("return 0;") == 1,
          "the load refuses cluster_ack=1 off run 15's recipe, unless the three entries are those words with the table's 0 and a cluster wait each, and"
          " unless exactly three values change: all of it before its one return 0", params)


MARK_HDR = r"""
#include <stdio.h>
#include "fabric_up.h"
#include "wd1_mark.h"
int main(void)
{
	static const u32 words[] = { 0x333, 0x111, 0x113, 0x311, 0x331, 0x133, 0x313, 0x131, 0x222, 0x112, 0, 0x80000333, 0x80000111, 0x80000113,
				     0x1333, 0x10111, 0x334, 0x33f, 0x11f, 0x211, 0x121 };
	static const u8 helds[] = { 0xff, 0xfe, 0x01, 0x00, 0x7f, 0xfd, 0x80, 0x03 };
	static const u32 ctls[] = { 0, 4, 1, 5, 0xffffffff, 0xfffffffb };
	static const u32 bites[] = { WD1_MARK_BITE, WD1_MARK_BITE - 1, WD1_MARK_BITE + 1, 0, 30U * WD1_HZ, WD1_MARK_COUNT };
	static const u32 counts[] = { 0, WD1_MARK_COUNT - 1, WD1_MARK_COUNT, WD1_MARK_COUNT + 1, WD1_MARK_COUNT + WD1_MARK_SLACK,
				      WD1_MARK_COUNT + WD1_MARK_SLACK + 1, WD1_MARK_BITE, 0xffffffff, 15U * WD1_HZ };
	unsigned int a, b, c, d, e;

	printf("consts %llx %x %x %x %x %x %x %x %llx %x %x %x %x %x %x %x %x\n", FAB_PS, FAB_ASKED, FAB_AWAKE, FAB_SUSPEND, FAB_WORD, M1_FAB_STAGE_PRE,
	       M1_FAB_STAGE, M1_FAB_STAGE_PEND, WD1_WDT, WD1_COUNT, WD1_BITE, WD1_CTRL, WD1_RESET_EN, WD1_HZ, WD1_MARK_BITE, WD1_MARK_COUNT,
	       WD1_MARK_SLACK);
	for (a = 0; a < sizeof(words) / sizeof(words[0]); a++)
		printf("word %x %x %d %u\n", words[a], m1_fab_up(words[a]), m1_fab_awake(words[a]), m1_fab_pre(words[a]));
	for (a = 0; a < sizeof(helds); a++)
		for (b = 0; b < sizeof(words) / sizeof(words[0]); b++)
			for (c = 0; c < 2; c++)
				for (d = 0; d < 2; d++)
					for (e = 0; e < 4; e++)
						printf("byte %x %x %u %u %u %x\n", helds[a], words[b], c, d, e,
						       m1_fab_byte(helds[a], words[b], c, d, (u8)e));
	for (a = 0; a < sizeof(ctls) / sizeof(ctls[0]); a++)
		for (b = 0; b < sizeof(bites) / sizeof(bites[0]); b++)
			for (c = 0; c < sizeof(counts) / sizeof(counts[0]); c++)
				printf("wd1 %x %x %x %x\n", ctls[a], bites[b], counts[c], m1_wd1_bits(ctls[a], bites[b], counts[c]));
	return 0;
}
"""
MARK_FN_PRELUDE = r"""
#include <stdio.h>
#include "fabric_up.h"
#include "wd1_mark.h"
typedef uint64_t u64;
#define __iomem
#define cpu_relax() do { } while (0)
#define dsb(x) do { } while (0)
/* A page of registers that reads a scripted sequence (its last value from then on) and logs every write. */
static unsigned char page[0x1000];
static u64 m1_tps = 1000, fake_now;
static const u32 *seq;
static unsigned int nseq, reads, nw;
static long rmin, rmax;
static struct { long off; u32 v; unsigned int reads; } wlog[16];
static u64 m1_now(void) { return fake_now++; }
static u32 readl(void *p)
{
	unsigned int i = reads++;
	long off = (unsigned char *)p - page;

	if (!i || off < rmin)
		rmin = off;
	if (!i || off > rmax)
		rmax = off;
	return seq[i < nseq ? i : nseq - 1];
}
static void writel(u32 v, void *p) { if (nw < 16) { wlog[nw].off = (unsigned char *)p - page; wlog[nw].v = v; wlog[nw].reads = reads; } nw++; }
/* m1_fab()'s world: the table's mappings, of which only DCS's first wait's is used. */
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((~0U << (l)) & (~0U >> (31 - (h))))
static void *post_map[128];
"""
MARK_FN_MAIN = r"""
static void show(const char *what, unsigned int n, int ret, u32 pre)
{
	unsigned int i;

	printf("%s %u ret %d pre %x reads %u at %lx-%lx writes %u:", what, n, ret, pre, reads, rmin, rmax, nw);
	for (i = 0; i < nw && i < 16; i++)
		printf(" %lx=%x@%u", wlog[i].off, wlog[i].v, wlog[i].reads);
	printf("\n");
}

int main(void)
{
	static const u32 f0[] = { 0x111, 0x333 }, f1[] = { 0x111, 0x113, 0x313, 0x333 }, f2[] = { 0x111, 0x113 }, f3[] = { 0x333, 0x333 },
		f4[] = { 0x80000111, 0x80000333 }, f5[] = { 0x222, 0x223, 0x333 }, w0[] = { 0 }, w1[] = { 4 }, w2[] = { 0xffffffff };
	static const struct { const u32 *s; unsigned int n; } fs[] = { { f0, 2 }, { f1, 4 }, { f2, 2 }, { f3, 2 }, { f4, 2 }, { f5, 3 } },
		ws[] = { { w0, 1 }, { w1, 1 }, { w2, 1 } };
	unsigned int i;

	for (i = 0; i < sizeof(fs) / sizeof(fs[0]); i++) {
		u32 pre = 0xdeadbeef;
		int ret;

		seq = fs[i].s; nseq = fs[i].n; reads = 0; nw = 0;
		ret = m1_fab_raise(page + 0x40, &pre);
		show("fab", i, ret, pre);
	}
	for (i = 0; i < sizeof(ws) / sizeof(ws[0]); i++) {
		int ret;

		seq = ws[i].s; nseq = ws[i].n; reads = 0; nw = 0;
		ret = m1_wd1_set(page);
		show("wd1", i, ret, 0);
	}
	/* m1_fab(): DCS's first wait is after entry 49, whose mapping is its word's, 0x800 above the fabric's in the same page. */
	post_map[m1_waits[0].idx] = page + 0x900;
	printf("fabptr %u %llx %llx %ld\n", m1_waits[0].idx, (unsigned long long)m1_waits[0].addr, (unsigned long long)M1_FABRIC_PS,
	       (long)((unsigned char *)m1_fab() - page));
	return 0;
}
"""


def mark_tests(g, src):
    """fabric_up=1 and wd1_mark=1, e1_hook.c's side: the two headers against an independent reading of them, the two functions
    that write, compiled as they stand against a page that logs every write, and the places that call them."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return [line.split() for line in subprocess.run([os.path.join(tmp, name)], capture_output=True, text=True, check=True).stdout.splitlines()]

        hdr = build("hdr", MARK_HDR)
        defs = "".join(line + "\n" for line in src.splitlines()
                       if re.match(r"#define (M1_ACK_\w+|M1_F_ACK\w*|M1_W_(CLEAR|SETTLED|PSTATE|CLUSTER)|M1_FABRIC_PS)\s", line))
        fn = build("fn", MARK_FN_PRELUDE + defs + cut("static const struct m1_wait {", "\n};\n") + cut("static void __iomem *m1_fab(void)", "\n}\n")
                   + cut("static bool m1_wd1_set(", "\n}\n") + cut("static bool m1_fab_raise(", "\n}\n") + MARK_FN_MAIN)

    # The headers: the word, the value and the stages; WD1's registers and the two values; all as PLAN.md has them.
    consts = [int(x, 16) for x in hdr[0][1:]]
    check(hdr[0][0] == "consts" and consts == [0x28e20c000, 0xf, 3, 1, 0x80000fff, 0x52, 0x53, 0x51, 0x2922b0000, 0x10, 0x14, 0x1c, 4, 24000000,
                                               0xd693a400, 0x55d4a800, 5 * 24000000]
          and consts[14] == 150 * 24000000 and consts[15] == 60 * 24000000,
          "fabric_up.h and wd1_mark.h: the fabric's word and 3; stages 0x52, 0x53, 0x51; WD1's count, bite and control; 150 s and 60 s", str(hdr[0]))
    check((g["FAB_PRE"], g["FAB_WD2"]) == (consts[5], (consts[7], consts[6])) and g["WD1_BITE_S"] * 24000000 == consts[14]
          and g["WD1_COUNT_S"] * 24000000 == consts[15] and g["WD2_BITE_S"] == 120 and g["WD1_STANDS"] == 0xc0,
          "m1-crumbs' stages for the run and its three times are the headers' (and wd2_bite.h's 120 s)")
    # The word's helpers: what's written, what counts as awake, and the class before the write.
    words = [(int(r[1], 16), int(r[2], 16), int(r[3]), int(r[4])) for r in hdr if r[0] == "word"]

    def klass(v):
        v12 = v & 0x80000fff
        return 3 if v12 == 0x333 else 1 if v12 == 0x111 else 2 if v & 0xf == 1 else 0

    bad = [w for w in words if w[1] != (w[0] & ~0xf | 3) or w[2] != ((w[0] & 0x80000fff) == 0x333) or w[3] != klass(w[0])]
    check(len(words) == 21 and not bad, "fabric_up.h: the write is the word with its low nibble := 3; awake is 0x333 with bit 31 clear; the class before"
          " the write is 0x333, 0x111, asked down to 1, or another", str(bad[:4]))
    bytes_ = [(int(r[1], 16), int(r[2], 16), int(r[3]), int(r[4]), int(r[5]), int(r[6], 16)) for r in hdr if r[0] == "byte"]
    bad = [b for b in bytes_ if b[5] != ((b[0] & 1) | (2 if b[0] & 0xfe != 0xfe else 0) | klass(b[1]) << 2 | b[2] << 4 | b[3] << 5 | b[4] << 6)]
    check(len(bytes_) == 8 * 21 * 2 * 2 * 4 and not bad,
          "fabric_up.h's byte: bit 0 the sio bit, bit 1 NOT all seven held, bits 3:2 the class, bit 4 settled, bit 5 still there, bits 7:6 WD1's two",
          str(bad[:4]))
    # Step 7's byte with CPU1 held always has bit 1 set, and a byte for a run in which all seven held never does.
    check(all(b[5] & 2 == 0 for b in bytes_ if b[0] & 0xfe == 0xfe) and all(b[5] & 2 for b in bytes_ if b[0] & 0xfe != 0xfe)
          and "b[3] = hold ? held : down;\n\tm1_crumb(0x6004, b, 4);" in src,
          "so the byte step 7 leaves at 0x6007 when CPU1 held can't be the run's byte for a run in which all seven held")
    wd1 = [(int(r[1], 16), int(r[2], 16), int(r[3], 16), int(r[4], 16)) for r in hdr if r[0] == "wd1"]
    bad = [w for w in wd1 if w[3] != ((1 if w[0] & 4 and w[1] == 0xd693a400 else 0) | (2 if 0x55d4a800 < w[2] <= 0x55d4a800 + 5 * 24000000 else 0))]
    check(len(wd1) == 6 * 6 * 9 and not bad and any(w[2] == 0x55d4a800 and w[0] == 4 and w[1] == 0xd693a400 and w[3] == 1 for w in wd1),
          "wd1_mark.h's two bits: armed with the 150 s bite; the count past 60 s by up to 5 s, and not at exactly the value written", str(bad[:4]))
    # FAB_WAS, in m1-crumbs, is indexed by that class.
    check(["0x111" in t for t in g["FAB_WAS"]] == [False, True, False, False] and ["0x333" in t and "neither" not in t for t in g["FAB_WAS"]] == [False, False, False, True]
          and "not there yet" in g["FAB_WAS"][2] and "neither 0x333 nor asked down to 1" in g["FAB_WAS"][0],
          "m1-crumbs names the fabric word's four classes in the header's order", str(g["FAB_WAS"]))

    # m1_fab_raise(): one write, to the word it read, of that word with its low nibble := 3; then up to 10 ms for 0x333.
    fab = {int(r[1]): r for r in fn if r[0] == "fab"}
    want = {0: "ret 1 pre 111 reads 2 at 40-40 writes 1: 40=113@1", 1: "ret 1 pre 111 reads 4 at 40-40 writes 1: 40=113@1",
            2: "ret 0 pre 111 reads 12 at 40-40 writes 1: 40=113@1", 3: "ret 1 pre 333 reads 2 at 40-40 writes 1: 40=333@1",
            4: "ret 0 pre 80000111 reads 12 at 40-40 writes 1: 40=80000113@1", 5: "ret 1 pre 222 reads 3 at 40-40 writes 1: 40=223@1"}
    got = {i: " ".join(r[2:]) for i, r in fab.items()}
    check(got == want, "m1_fab_raise(): reads the word, writes it back once with its low nibble := 3 and nothing else, polls that same word for 0x333"
          " with bit 31 clear, and gives up after 10 ms with what it last read", f"{got}")
    ptr = [r for r in fn if r[0] == "fabptr"]
    check(ptr == [["fabptr", "49", "28e20c800", "28e20c000", "256"]]
          and "\treturn post_map[m1_waits[0].idx] - (m1_waits[0].addr - M1_FABRIC_PS);\n" in cut("static void __iomem *m1_fab(void)", "\n}\n"),
          "m1_fab(): the fabric's word, 0x800 below DCS's in the mapping of the table's entry 49", str(ptr))
    # m1_wd1_set(): the bite, the count, then RESET_EN, in that order; the control cleared first only if it read armed.
    wd = {int(r[1]): " ".join(r[2:]) for r in fn if r[0] == "wd1"}
    check(wd == {0: "ret 0 pre 0 reads 1 at 1c-1c writes 3: 14=d693a400@1 10=55d4a800@1 1c=4@1",
                 1: "ret 1 pre 0 reads 1 at 1c-1c writes 4: 1c=0@1 14=d693a400@1 10=55d4a800@1 1c=4@1",
                 2: "ret 1 pre 0 reads 1 at 1c-1c writes 4: 1c=0@1 14=d693a400@1 10=55d4a800@1 1c=4@1"},
          "m1_wd1_set(): one read, of WD1's control; its bite := 150 s, its count := 60 s, then its control := 4, and nothing else; its control := 0"
          " first if it read armed", f"{wd}")

    # Where they're called: once each, by their own parameter, at their place in the replay; and nothing else writes those registers.
    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    check(in_order_of(after, ["\t/* 4 */\n\tm1_aic_mask_all();\n\tif (wd1_mark) {\n\t\tif (m1_wd1_set(m1_wdt))\n\t\t\tflags |= BIT(6);\n\t} else if (readl(m1_wdt + M1_WD1_CTRL)) {\n"
                              "\t\twritel(0, m1_wdt + M1_WD1_CTRL);\n\t\tflags |= BIT(6);\n\t}\n",
                              "/* 5: the table's other entries, the commit held back; with pmgr_late, macOS's waits among them */\n"
                              "\tif (fabric_up)\n\t\tfab_set = m1_fab_raise(m1_fab(), &fab_pre);\n\tfor (i = 0, k = 0; i < ARRAY_SIZE(e1_post_slpa) - 1; i++) {",
                              "\tif (ps_words)\n\t\tm1_pw_bits = m1_pw_write();\n", "\tif (fabric_up)\n\t\tfab_end = m1_fab_awake(readl(m1_fab()));\n",
                              "/* 6: release", "/* 8 */", "m1_crumb(0x6010, b, 7);\n",
                              "\tif (wd1_mark)\n\t\twd1 = m1_wd1_bits(readl(m1_wdt + WD1_CTRL), readl(m1_wdt + WD1_BITE), readl(m1_wdt + WD1_COUNT));\n",
                              "\t\tb[0] = fabric_up ? M1_FAB_STAGE_PRE : sio_stop ? 0x4e : hold ? 0x5a : alarm ? 0x59 : 0x58;\n",
                              "\t\tb[3] = fabric_up ? m1_fab_byte(held, fab_pre, fab_set, fab_end, wd1) : hold ? held : down;\n\t\tm1_crumb(0x6004, b, 4);\n",
                              "/* 9 */", "writel_relaxed(1, commit);"])
          and after.count("m1_wd1_set(") == 1 and after.count("m1_fab_raise(") == 1 and after.count("\t\tfab_set = ") == 1 and after.count("\t\tfab_end = ") == 1
          and after.count("\t\twd1 = ") == 1 and "u32 fab_pre = 0;\n\tbool fab_set = false, fab_end = false;\n" in after and "pmpb = 0, wd1 = 0;" in after,
          "the replay sets WD1 in step 4 in place of its stop, puts the fabric back before the table's first late entry, reads it again after the power"
          " words, and looks at WD1 last, after step 8's first command and before the stage that carries the byte", after[:3000])
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("  # an MMIO write, not a function whose name ends in "write"
    wd1_writes = re.findall(wr[:-2] + r"\([^;]*(?:WD1_\w+|M1_WD_(?:CUR|BITE|CTRL)\(1\)|M1_WD1_CTRL)[^;]*;", code_only)
    fab_writes = re.findall(wr[:-2] + r"\([^;]*(?:m1_fab\w*|FAB_\w+|M1_FABRIC_PS)[^;]*;", code_only)
    check(sorted(wd1_writes) == sorted(["writel(0, wdt + WD1_CTRL);", "writel(WD1_MARK_BITE, wdt + WD1_BITE);", "writel(WD1_MARK_COUNT, wdt + WD1_COUNT);",
                                         "writel(WD1_RESET_EN, wdt + WD1_CTRL);", "writel(0, m1_wdt + M1_WD1_CTRL);"])
          and fab_writes == ["writel(m1_fab_up(v), p);"] and len(re.findall(r"\bm1_wd1_set\(", code_only)) == 2
          and len(re.findall(r"\bm1_fab_raise\(", code_only)) == 2 and len(re.findall(r"\bWD1_MARK_(BITE|COUNT)\b", code_only)) == 2 + 2 + 5,
          "nothing but m1_wd1_set() and step 4's old stop writes WD1, and nothing but m1_fab_raise() writes the fabric's word", f"{wd1_writes} {fab_writes}")
    for name in ("m1_wd1_init", "m1_fab_init"):
        init = cut(f"static int __init {name}(void)", "\n}\n")
        check(not re.search(wr, init), f"{name}() only reads and logs", init)
    check("\tif (fabric_up) {\n\t\tint ret = m1_fab_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n"
          "\tif (wd1_mark) {\n\t\tint ret = m1_wd1_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n" in src,
          "the load returns what m1_fab_init() and m1_wd1_init() refuse with")
    # A tripwire, since a write through another name for the same register has no name to match: the module's MMIO write calls are
    # counted, in the whole file and in the replay's own function. A new one changes the count, and has to be accounted for here.
    after_code = re.sub(r"/\*.*?\*/", "", after, flags=re.S)
    # 48 since the probe (probe_tests() has its six: SIO_CPU's table word twice, SIO's RUN, ANS2 up and back, the PMP's RUN).
    # 53 since pcie_down=1 (pcie_down_tests() has its two: the three _SYS words' 0, and APCIE_ST's and APCIE_GP's).
    # 59 since sio_steps=1 (sio_steps_tests() has its six: GPIO's raise and its 0, the 43's 0, 0x28e03801c, the seven, the fourteen).
    check(len(re.findall(wr, code_only)) == 66 and len(re.findall(wr, after_code)) == 11,
          "e1_hook.c makes 66 MMIO write calls, 11 of them in m1_after_slpa() itself: none has been added without this count",
          f"{len(re.findall(wr, code_only))} {len(re.findall(wr, after_code))}")
    check("\tif (!m1_fab_awake(v)) {\n\t\tpr_err(" in cut("static int __init m1_fab_init(void)", "\n}\n")
          and "if (e1_never_write(FAB_PS)) {" in src and "m1_never(WD1_WDT + WD1_COUNT, 1) || m1_never(WD1_WDT + WD1_BITE, 1) || m1_never(WD1_WDT + WD1_CTRL, 1)" in src,
          "the load refuses fabric_up=1 unless the word reads 0x333 awake, and either parameter if its writes would touch the never-write list")
    # The parameters: off unless asked for, read-only once loaded, and only on run 16's recipe, wd1_mark=1 only on fabric_up=1's stages.
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool fabric_up;\nmodule_param(fabric_up, bool, 0444);\n" in src and "static bool wd1_mark;\nmodule_param(wd1_mark, bool, 0444);\n" in src
          and not re.search(r"\b(fabric_up|wd1_mark)\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (cluster_ack && (!wd2_bite || !pmp_stop)) {\n", "\tif (wd1_mark && !fabric_up) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n"
                                   "\tif (fabric_up && !cluster_ack) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n", "\treturn 0;\n\nbad_wait:"]),
          "fabric_up=1 loads only on cluster_ack=1's recipe and wd1_mark=1 only with fabric_up=1; both are off by default and read-only", params[-2500:])
    wout = cut("static void m1_wd2_out(void)", "\n}\n")
    check("\tif ((read_sysreg(isr_el1) >> 6) & 7)\n\t\tb[0] = fabric_up ? M1_FAB_STAGE_PEND : M1_WD2_STAGE_PEND;\n\telse\n\t\tb[0] = fabric_up ? M1_FAB_STAGE : M1_WD2_STAGE;\n" in wout
          and len(re.findall(r"\bM1_FAB_STAGE\w*\b", code_only)) == 3 + 3,
          "the run's three stages go out where 0x4e, 0x57 and 0x56 did, and nowhere else", wout)


GATES_HDR = r"""
#include <stdio.h>
#include "gates_mark.h"
int main(void)
{
	static const unsigned short ga[] = LOOK_GATE_A_OFFS, gb[] = LOOK_GATE_B_OFFS;
	/* Words with bit 30 set, and without it, the other bits varied. */
	static const u32 shut[] = { GATE_CLOSED, 0xffffffff, 0x40000001, 0xc0000000, 0x7fffffff };
	static const u32 open[] = { 0, 0xbfffffff, 0x3fffffff, 0x80000000, 0x00000001 };
	static const u32 lanes[] = { GATE_LANE_VAL, GATE_LANE_VAL ^ 1, GATE_LANE_VAL | (1U << 8), GATE_LANE_VAL | (1U << 30), 0x1f0000ff, 0, 0xffffffff,
				     0x0000030f, 0x1000030e };
	unsigned int m, j, l, i, s;
	u32 g[GATES_N];

	printf("consts %llx %x %x %x %llx %x %x %x %llx %x %x %x %x %x %x %x\n", LOOK_GATE_A, ga[0], ga[1], ga[2], LOOK_GATE_B, gb[0], gb[1],
	       gb[2], LOOK_SIO_PAGE, GATE_LANE, GATE_LANE_VAL, GATE_CLOSED, GATES_N, GATES_AT, GATES_LANE_MACOS, GATES_MARK);
	printf("stages %x %x %x\n", M1_GATES_STAGE_PRE, M1_GATES_STAGE, M1_GATES_STAGE_PEND);
	for (s = 0; s < 256; s++)
		printf("stage %x %x\n", s, gates_stage((u8)s));
	for (m = 0; m < 64; m++)
		for (j = 0; j < 5; j++)
			for (l = 0; l < sizeof(lanes) / sizeof(lanes[0]); l++) {
				for (i = 0; i < GATES_N; i++)
					g[i] = m >> i & 1 ? shut[(i + j) % 5] : open[(i + j) % 5];
				printf("byte %x %x %x %x %x %x %x %x\n", g[0], g[1], g[2], g[3], g[4], g[5], lanes[l], gates_byte(g, lanes[l]));
			}
	return 0;
}
"""
GATES_FN_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include "gates_mark.h"
typedef uint16_t u16;
typedef uint64_t u64;
#define __iomem
#define __init
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define PAGE_SIZE 0x4000UL
#define ENOMEM 12
#define ENODEV 19
/* Three pages that say which was read where, each read its own value; no writel() for the code to call. */
static unsigned char ga_page[PAGE_SIZE], gb_page[PAGE_SIZE], sio_page[PAGE_SIZE];
static void *m1_look_ga, *m1_look_gb, *m1_sio;
static unsigned int nread, map_fails;
static u32 readl(const void *p)
{
	const unsigned char *c = p;
	const unsigned char *const pg[3] = { ga_page, gb_page, sio_page };
	unsigned int k;

	for (k = 0; k < 3; k++)
		if (c >= pg[k] && c < pg[k] + PAGE_SIZE)
			break;
	printf("read %c %lx\n", k < 3 ? "ABS"[k] : '?', k < 3 ? (unsigned long)(c - pg[k]) : 0UL);
	return 0x1000 + nread++;
}
/* The n-th mapping fails (1-based); 0: none does. */
static void *ioremap_np(u64 pa, unsigned long size)
{
	static unsigned int n;

	printf("map %llx %lx\n", (unsigned long long)pa, size);
	if (++n == map_fails)
		return NULL;
	return pa == LOOK_GATE_A ? (void *)ga_page : pa == LOOK_GATE_B ? (void *)gb_page : NULL;
}
static void pr_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("err "); vprintf(fmt, ap); va_end(ap); }
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("info "); vprintf(fmt, ap); va_end(ap); }
"""
GATES_FN_MAIN = r"""
int main(int argc, char **argv)
{
	u32 g[GATES_N] = { 0 }, lane = 0;
	unsigned int i;
	int ret;

	(void)argc;
	if (argv[1][0] == 'r') {
		/* Step 8's reads, with the load's pointers in place. */
		m1_look_ga = ga_page;
		m1_look_gb = gb_page;
		m1_sio = sio_page;
		m1_gates_read(g, &lane);
		printf("got");
		for (i = 0; i < GATES_N; i++)
			printf(" %x", g[i]);
		printf(" %x\n", lane);
		return 0;
	}
	/* The load: "i N S", the N-th mapping failing (0: none), SIO's page mapped or not. */
	map_fails = (unsigned int)(argv[1][1] - '0');
	m1_sio = argv[1][2] == 's' ? (void *)sio_page : NULL;
	ret = m1_gates_init();
	printf("ret %d reads %u\n", ret, nread);
	return 0;
}
"""


def gates_mark_tests(g, src):
    """gates_mark=1: gates_mark.h against an independent reading of it; m1_gates_read() and m1_gates_init() compiled as they stand
    against pages that say what was read, with no writel() to call; the places in e1_hook.c that use them; and the byte, the
    run's stages and --clear through m1-crumbs."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return os.path.join(tmp, name)

        hdr = [line.split() for line in subprocess.run([build("hdr", GATES_HDR)], capture_output=True, text=True, check=True).stdout.splitlines()]
        fn = build("fn", GATES_FN_PRELUDE + cut("static void m1_gates_read(u32 *g, u32 *lane)", "\n}\n")
                   + cut("static int __init m1_gates_init(void)", "\n}\n") + GATES_FN_MAIN)

        def fnrun(arg):
            return subprocess.run([fn, arg], capture_output=True, text=True, check=True).stdout.splitlines()

        reads, loads = fnrun("r"), {a: fnrun(a) for a in ("i0s", "i1s", "i2s", "i0n")}

    # The header: look.h's six words in its order, gate_test.h's lane and polarity, the byte's two flags, the PMU byte, the stages.
    consts = [int(x, 16) for x in hdr[0][1:]]
    check(hdr[0][0] == "consts" and consts == [0x28e5a0000, 0x50, 0x64, 0x30c, 0x28e09c000, 0x310, 0x338, 0x34c, 0x28e580000, 0x160, 0x1000030f,
                                               1 << 30, 6, 0x6016, 0x40, 0x80]
          and hdr[1] == ["stages", "4b", "55", "54"],
          "gates_mark.h: the six words are 0x28e5a0050, 0x28e5a0064, 0x28e5a030c, 0x28e09c310, 0x28e09c338, 0x28e09c34c, in that order, then"
          " AFNC2_LW1 at 0x28e580160; bit 30 is closed; macOS's lane is 0x1000030f; the byte is 0x6016; stages 0x4b, 0x55, 0x54", str(hdr[:2]))
    words = [consts[0] + o for o in consts[1:4]] + [consts[4] + o for o in consts[5:8]]
    check(tuple(f"{w:#x}" for w in words) == g["GATE_WORDS"] and (g["GATES_LANE"], g["GATES_MARK"]) == (consts[14], consts[15])
          and (g["GATES_PRE"], g["GATES_WD2"]) == (0x4b, (0x54, 0x55)) and g["GATES_STAGES"] == (0x4b, 0x54, 0x55),
          "m1-crumbs names the six words in the header's order, and its byte's bits and the run's stages are the header's", str(g["GATE_WORDS"]))
    stages = {int(r[1], 16): int(r[2], 16) for r in hdr if r[0] == "stage"}
    check(len(stages) == 256 and all(v == {0x52: 0x4b, 0x53: 0x55, 0x51: 0x54}.get(s, s) for s, v in stages.items()),
          "gates_stage(): fabric_up=1's 0x52, 0x53 and 0x51 become 0x4b, 0x55 and 0x54, and every other stage stays as it is")
    rows = [[int(x, 16) for x in r[1:]] for r in hdr if r[0] == "byte"]
    bad = [r for r in rows if r[7] != (0x80 | sum(1 << i for i in range(6) if r[i] >> 30 & 1) | (0x40 if r[6] == 0x1000030f else 0))]
    check(len(rows) == 64 * 5 * 9 and not bad and {r[7] & 0x3f for r in rows} == set(range(64)),
          "gates_byte(): bits 0-5 bit 30 of the six words in order, and nothing else of them; bit 6 the lane at exactly 0x1000030f; bit 7 always",
          str(bad[:4]))

    # m1_gates_read(): seven reads, in the header's order, each into its own place; nothing written (there's no writel() to call).
    got = [r for r in reads if r.startswith("got")]
    check([r for r in reads if r.startswith("read")] == ["read A 50", "read A 64", "read A 30c", "read B 310", "read B 338", "read B 34c", "read S 160"]
          and got == ["got 1000 1001 1002 1003 1004 1005 1006"],
          "m1_gates_read(): 0x28e5a0050, 0x64, 0x30c, then 0x28e09c310, 0x338, 0x34c, then AFNC2_LW1, each into its own place, and nothing else",
          "\n".join(reads))
    # m1_gates_init(): both pages mapped non-posted, a page each; the load refused with nothing read if a mapping fails or SIO's page
    # isn't there; otherwise the seven words read once and logged.
    maps = ["map 28e5a0000 4000", "map 28e09c000 4000"]
    ok = loads["i0s"]
    check(ok[:2] == maps and ok[-1] == "ret 0 reads 7" and len([r for r in ok if r.startswith("read ")]) == 7
          and any(r.startswith("info m1: gates_mark: awake: gates 0x28e5a0000: +050 00001000 +064 00001001 +30c 00001002; 0x28e09c000: +310 00001003"
                               " +338 00001004 +34c 00001005; AFNC2_LW1 0x28e580160 00001006; byte 80.") for r in ok),
          "m1_gates_init(): the two gate pages mapped, the seven words read once and logged with their byte", "\n".join(ok))
    check(loads["i1s"][-1] == "ret -12 reads 0" and loads["i2s"][-1] == "ret -12 reads 0" and loads["i0n"][-1] == "ret -19 reads 0"
          and all(any(r.startswith("err m1: gates_mark:") for r in loads[a]) for a in ("i1s", "i2s", "i0n")),
          "m1_gates_init(): refuses, reading nothing, if either mapping fails or SIO's page isn't mapped", str(loads))
    init = cut("static int __init m1_gates_init(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("
    check(not re.search(wr, init) and "\tif (gates_mark) {\n\t\tint ret = m1_gates_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n" in src
          and in_order_of(src, ["\tif (observe || pmgr_late) {\n\t\tm1_soc = ioremap_np(M1_SOC_CLUSTERS, PAGE_SIZE);\n\t\tm1_sio = ioremap_np(",
                                "\tif (gates_mark) {\n\t\tint ret = m1_gates_init();"]),
          "the load runs m1_gates_init() after SIO's page is mapped and returns its refusal; it writes nothing")

    # Step 8: after stage 0x4b's command and the outcomes, the reads, dsb and isb, then the byte, all before step 9's mask and the commit.
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    check(in_order_of(after, ["\t\tb[0] = fabric_up ? M1_FAB_STAGE_PRE : sio_stop ? 0x4e : hold ? 0x5a : alarm ? 0x59 : 0x58;\n"
                              "\t\tif (gates_mark)\n\t\t\tb[0] = gates_stage(b[0]);\n\t\tif (sio_down)\n\t\t\tb[0] = siod_stage(b[0]);\n\t\tif (pcie_down)\n\t\t\tb[0] = pcd_stage(b[0]);\n\t\tif (sio_steps)\n\t\t\tb[0] = sst_stage(b[0]);\n\t\tif (a2_pre)\n\t\t\tb[0] = a2p_stage(b[0]);\n\t\tb[1] = parked",
                              "\t\tm1_crumb(0x6004, b, 4);\n", "\t\t\tm1_crumb(0x6012, b, 2);\n",
                              "\tif (gates_mark && !sio_down && !pcie_down && !sio_steps && !a2_pre) {\n\t\tu32 g[GATES_N], lane;\n\n\t\tm1_gates_read(g, &lane);\n\t\tdsb(sy);\n\t\tisb();\n"
                              "\t\tm1_gates = gates_byte(g, lane);\n\t\tm1_crumb(GATES_AT, &m1_gates, 1);\n\t}\n",
                              "\t/* recfg_bite=1", "/* 9 */", "\tlocal_daif_mask();", "writel_relaxed(1, commit);", "m1_alarm_arm(rtc);"])
          and after.count("m1_gates_read(") == 1 and len(re.findall(r"\bm1_gates_read\(", code_only)) == 4
          and re.findall(r"\bm1_gates\s*=[^=;]*;", code_only) == ["m1_gates = gates_byte(g, lane);", "m1_gates = m1_sio_down();", "m1_gates = m1_pcie_down();", "m1_gates = m1_sio_steps();", "m1_gates = m1_a2_pre();"] and len(re.findall(r"\bgates_stage\(", code_only)) == 2,
          "step 8 sends stage 0x4b in 0x52's place, then the outcomes, then reads the seven words and sends their byte, SError still unmasked,"
          " before the commit, and not with sio_down=1, pcie_down=1 or sio_steps=1, whose own bytes are the only other things that set it; the stage is mapped in two places", after[-4000:])
    alarm_fn = cut("static void m1_alarm_arm(const u8 *rtc)", "\n}\n")
    wout = cut("static void m1_wd2_out(void)", "\n}\n")
    check("done:\n\tm1_crumb(0x6016, gates_mark ? &m1_gates : &res, 1);\n}" in alarm_fn and alarm_fn.count("m1_crumb(") == 1
          and in_order_of(wout, ["\telse\n\t\tb[0] = fabric_up ? M1_FAB_STAGE : M1_WD2_STAGE;\n",
                                 "\tif (gates_mark)\n\t\tb[0] = gates_stage(b[0]);\n\tif (sio_down)\n\t\tb[0] = siod_stage(b[0]);\n\tif (pcie_down)\n\t\tb[0] = pcd_stage(b[0]);\n\tif (sio_steps)\n\t\tb[0] = sst_stage(b[0]);\n\tif (a2_pre)\n\t\tb[0] = a2p_stage(b[0]);\n\tb[1] = m1_wd2_sample("]),
          "after the commit: the alarm's one command to the PMU sends the gate byte in its result's place, and the last stage is the run's own",
          alarm_fn + wout)
    # No register written: no MMIO write names the gate pages, and the count of MMIO writes is mark_tests()' as it was.
    check(not re.search(r"write\w*\([^;]*m1_look_g[ab]", code_only) and not re.search(r"write\w*\([^;]*GATE_LANE\b[^;]*;", cut("static void m1_gates_read(", "\n}\n")),
          "nothing writes through the gate pages, and m1_gates_read() writes nothing")
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool gates_mark;\nmodule_param(gates_mark, bool, 0444);\n" in src and not re.search(r"\bgates_mark\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (gates_mark && (!wd1_mark || probe)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n", "\treturn 0;\n\nbad_wait:"])
          and 'gates_mark ? " gates_mark=1" : ""' in src,
          "gates_mark=1 loads only with wd1_mark=1 and never with probe=1; it's off by default, read-only, and named in the load line", params[-1500:])

    # m1-crumbs. The run's record at its last stage, all as meant (run 17's bytes, wd2_bite=1's sample, the marker standing), with the
    # gate byte at 0x6016.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        alarm = (0x41, 121544 + 3600, 0, 0)
        rec = dict(parked=0x7d, left=0, down=0xf5, secs=121544, prog=(0xff, 0xff), flags=0x90, isrs=0x8d)

        def dec(stage, gb, x=217, **kw):
            fake(path, scratch(stage, **dict(rec, drained=gb, **kw)), rtc_secs=121544 + x + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            return r.returncode, r.stdout, r.stderr

        WORD = "  {}: bit 30 {}\n"
        rc, out, err = dec(0xd5, 0xc6)
        check(rc == 0 and "stage 0x6004 = 0xd5 (pmp_out=1: bit 7, so the stage is 0x55) (pmgr_late=1): gates_mark=1: fabric_up=1's 0x53:" in out
              and ("0x6016 = 0xc6: gates_mark=1's byte, in place of the alarm's result: what CPU0 read in step 8, right before the commit and"
                   " before B2's own writes:\n") in out
              and WORD.format("0x28e5a0050", "clear, open (the gate B2 polls at op 140, after SIO's subtree)") in out
              and WORD.format("0x28e5a0064", "SET, closed") in out and WORD.format("0x28e5a030c", "SET, closed") in out
              and WORD.format("0x28e09c310", "clear, open") in out and WORD.format("0x28e09c338", "clear, open") in out
              and WORD.format("0x28e09c34c", "clear, open") in out
              and "  AFNC2_LW1 (0x28e580160), the fabric lane above SIO: read 0x1000030f, macOS's value\n" in out
              and "  so: 0x28e5a0050 read OPEN in the recipe's state too" in out and "nothing records the commit" not in out
              and "alarm command" not in out and "0x6016 = 0xc6: not a value" not in out
              and "0x6007 = 0xf5: fabric_up=1's byte, in place of the cores that held:\n" in out and "WD1 stood as the marker" in out
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out
              and "0x6014 bit 4 (cluster_ack=1)" in out and "0x6013 = 0xff: ps_words=1 wrote the 20 power words" in out
              and "0x6015 = 0x8d: K4 ran" in out
              and "RTC second just before the commit, from its low two bytes: 121544, 257 s before now (121801); right if this read comes within 18 h" in out
              and "the run's alarm" not in out and "0x140c is the commit's second + 3600, as the run wrote it" in out
              and "the armed alarm survived the reset" in out and "the SoC restarted about 217 s after the commit" in out,
              "gates_mark=1's 0xd5: the gate byte, read gate by gate, in the alarm's result's place; the rest read as --m1-mark's 0xd3", out + err)
        # The same bytes under --m1-mark's 0xd3 are its record as ever, and every line that isn't about the stage, 0x6016 or the alarm
        # reads the same at 0xd5.
        rc3, out3, err3 = dec(0xd3, 0xbd)
        rc5, out5, err5 = dec(0xd5, 0xbd)
        skip = ("0x6000:", "0x6010:", "stage 0x6004", "0x6016", "RTC second", "PMU alarm", "  0x140c", "  the armed alarm", "  0x28e", "  AFNC2", "  so: 0x28e")
        missing = [l for l in out3.splitlines() if not l.startswith(skip) and l not in out5.splitlines()]
        check(rc3 == 0 and rc5 == 0 and "0x6016 = 0xbd: every alarm command was acked" in out3 and "gates_mark=1" not in out3
              and "0x6016 = 0xbd: gates_mark=1's byte" in out5 and "every alarm command was acked" not in out5 and not missing,
              "0xbd at 0x6016: the alarm's result under 0xd3, the gate byte under 0xd5, and nothing else read differently", "\n".join(missing) + out3 + out5)
        rc, out, err = dec(0xd4, 0xff)
        check(rc == 0 and "so the stage is 0x54" in out and "gates_mark=1: fabric_up=1's 0x51:" in out and "pending afterwards" in out
              and "  so: 0x28e5a0050 read closed in the recipe's state: B2's poll of it at op 140 is cleared" in out
              and "did NOT read 0x1000030f" not in out and "  wd1_mark=1's marker: not read. CPU0 had an exception pending" in out,
              "gates_mark=1's 0xd4: the pending stage, its byte, and no reading of the time", out + err)
        # Stage 0x4b: the parked cores at 0x6005, the gate byte landed or not.
        rc, out, err = dec(0xcb, 0x81, x=90, parked=0xfe)
        check(rc == 0 and "so the stage is 0x4b" in out and "8 (gates_mark=1): fabric_up=1's 0x52, with the gate byte after it:" in out
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out and "wd2_bite=1, after" not in out
              and "0x6007 = 0xf5: fabric_up=1's byte" in out and "  so: 0x28e5a0050 read closed" in out
              and "  AFNC2_LW1 (0x28e580160), the fabric lane above SIO: did NOT read 0x1000030f, macOS's value (the load log has it awake)\n" in out
              and "  stage 0x4b, so nothing records the commit after this byte (0x54 or 0x55 comes after it): inconclusive about the rest of the entry\n" in out
              and "  wd1_mark=1's marker: not read. Stage 0x4b is the last before the commit" in out,
              "gates_mark=1's 0xcb with its byte: the stage before the commit, the parked cores, the byte, and no reading of the time", out + err)
        FAULT = ("0x6016 = 0x00: THE GATE BYTE DIDN'T LAND (bit 7 clear), though stage 0x4b and the steps' outcomes did: CPU0 was reading the six"
                 " gate words and the lane, or sending their byte, when the record stopped.")
        for x in (10, 90):
            rc, out, err = dec(0xcb, 0, x=x, parked=0xfe)
            check(rc == 0 and FAULT in out and "bit 30" not in out and f"the SoC restarted about {x} s after the commit" in out
                  and "about 10 s later" in out and "about 90 s after step 4" in out and "wd1_mark=1's marker: not read. Stage 0x4b" in out,
                  f"gates_mark=1's 0xcb with 0x6016 = 0, a restart at {x} s: said to be a read that faulted or hung, or the byte's command failing",
                  out + err)
        # Where the record doesn't say it's the gate byte, nothing is read from it: bit 7 clear after the run's later stage, the outcomes'
        # marker missing, pmp_out=1's bit missing.
        for stage, gb, kw, want in ((0xd5, 0x00, {}, "0x6016 = 0x00: bit 7 is clear, so the gate byte didn't survive the reset"),
                                    (0xd5, 0x7f, {}, "0x6016 = 0x7f: bit 7 is clear, so the gate byte didn't survive the reset"),
                                    (0xd4, 0x3f, {}, "0x6016 = 0x3f: bit 7 is clear"),
                                    (0xd5, 0xc6, dict(prog=(0xff, 0x00)), "0x6016 = 0xc6: not read. The gate byte goes out right after the steps' outcomes"),
                                    (0xcb, 0x00, dict(prog=(0xff, 0x00), parked=0xfe), "0x6016 = 0x00: not read. The gate byte goes out right after"),
                                    (0x55, 0xc6, {}, "0x6016 = 0xc6: not read. A gates_mark=1 run's stages always carry pmp_out=1's bit 7")):
            rc, out, err = dec(stage, gb, **kw)
            check(rc == 0 and want in out and "bit 30" not in out and "THE GATE BYTE" not in out and "alarm command" not in out,
                  f"stage 0x{stage:02x}, 0x6016 = 0x{gb:02x}{', no outcomes marker' if kw.get('prog') else ''}: the gate byte isn't read", out + err)
        # Every byte, at each of the run's stages, read independently of the decoder.
        wrong = []
        for s in (0x4b, 0x54, 0x55):
            for v in range(256):
                b = bytearray(0x20)
                b[0x13], b[0x16] = 0xff, v
                lines = g["gates_lines"](s, bytes(b), True)
                text = "\n".join(lines)
                if not v & 0x80:
                    ok = len(lines) == 1 and "bit 30" not in text and ("THE GATE BYTE DIDN'T LAND" in text) == (s == 0x4b)
                else:
                    ok = (len(lines) == 9 + (s == 0x4b) and "reset" not in text
                          and all((f"  {w}: bit 30 SET, closed" in lines[1 + i]) == bool(v >> i & 1)
                                  and (f"  {w}: bit 30 clear, open" in lines[1 + i]) == (not v >> i & 1) for i, w in enumerate(g["GATE_WORDS"]))
                          and ("read 0x1000030f, macOS's value" in lines[7] and "NOT" not in lines[7]) == bool(v & 0x40)
                          and lines[8].startswith("  so: ") and ("read closed" in lines[8]) == bool(v & 1) and ("OPEN" in lines[8]) == (not v & 1)
                          and ("a2_pre=1 is next" in lines[8]) == bool(v & 1) and ("op 140" in lines[8])
                          and ("nothing records the commit" in text) == (s == 0x4b))
                if not ok:
                    wrong.append((hex(s), hex(v), lines))
            b = bytearray(0x20)
            b[0x13], b[0x16] = 0xff, 0xc6
            if g["gates_lines"](s, bytes(b), False)[0].find("not read") < 0 or "bit 30" in "\n".join(g["gates_lines"](s, bytes(b), False)):
                wrong.append((hex(s), "no pmp_out=1 bit"))
        check(not wrong, "gates_mark=1: for every byte at each of its stages, each gate is called closed exactly when its bit is set, the lane macOS's"
              " exactly with bit 6, 0x28e5a0050's verdict follows bit 0, and nothing is read from a byte without bit 7", str(wrong[:2]))
        # The time against the marker: at each of the run's stages exactly as at fabric_up=1's, for every sample.
        mr = g["mark_reading"]
        wrong = [(hex(v), x) for v in range(256) for x in (60, 88, 90, 105, 120, 123, 135, 146, 150, 163, 300)
                 if mr(x, 0x4b, v) != mr(x, 0x52, v).replace("Stage 0x52", "Stage 0x4b") or mr(x, 0x54, v) != mr(x, 0x51, v)
                 or mr(x, 0x55, v) != mr(x, 0x53, v)]
        check(not wrong and "Stage 0x4b is the last before the commit" in mr(90, 0x4b, 0x7d),
              "the restart's time is read against the marker at 0x4b, 0x54 and 0x55 exactly as at 0x52, 0x51 and 0x53", str(wrong[:3]))
        for x, text in ((90, "about 90 s is what WD1 gives with its count never zeroed: B2 didn't reach its op 224"),
                        (120, "about 120 s is WD2: B2 zeroed WD1's count"), (150, "the entry pair FINISHED")):
            rc, out, err = dec(0xd5, 0xc6, x=x)
            check(rc == 0 and f"  wd1_mark=1's marker: {text}" in out or (x == 150 and "150 s is WD1's 150 s from a count that B2 zeroed" in out
                                                                            and text in out),
                  f"gates_mark=1's 0xd5, a restart at {x} s: read against the marker as --m1-mark's is", out + err)

        # --clear: the run's alarm disarmed by its compare value, as ever; with the second's low bytes lost, 0x6016 is never taken for the
        # alarm's result, so the alarm isn't disarmed and the crumbs stay (--m1-mark's 0xd3 with 0xbd there is disarmed, as before).
        for stage, kw in ((0xd5, {}), (0xd4, {}), (0xcb, dict(parked=0xfe))):
            before = fake(path, scratch(stage, **dict(rec, drained=0xc6, **kw)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt="4800000000 24000000")
            after_ = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after_ == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after gates_mark=1's 0x{stage:02x}: the run's alarm disarmed by its compare value, and the crumbs zeroed", r.stdout + r.stderr)
        lost = dict(rec, secs=0, drained=0xbd)
        before = fake(path, scratch(0xd5, **lost), rtc_secs=121761, alarm=alarm)
        r = run(path, "--clear", cnt="4800000000 24000000")
        check(r.returncode == 2 and open(path, "rb").read() == before and "disarmed" not in r.stdout
              and "after gates_mark=1's, sio_down=1's, pcie_down=1's, sio_steps=1's and a2_pre=1's stages it never does: it's that run's byte" in r.stderr,
              "--clear after 0xd5 with the second lost and 0xbd at 0x6016: not taken for an acked alarm, so nothing is written", r.stdout + r.stderr)
        before = fake(path, scratch(0xd3, **lost), rtc_secs=121761, alarm=alarm)
        r = run(path, "--clear", cnt="4800000000 24000000")
        check(r.returncode == 0 and "disarmed: 0x1401 = 0x01" in r.stdout and "gates_mark" not in r.stderr,
              "--clear after --m1-mark's 0xd3 with the second lost and 0xbd at 0x6016: the alarm's result, disarmed as before", r.stdout + r.stderr)


SIOD_HDR = r"""
#include <stdio.h>
#include <string.h>
#include "asc_probe.h"
#include "sio_down.h"
int main(void)
{
	static const u16 off[] = SIOD_OFFS, five[] = SIOD_AUTO_OFFS;
	static const unsigned char grp[] = SIOD_GROUPS;
	static const u32 words[] = { 0, 0x200, 0x100, 0x800, 0x1f000000, 0xfffff000, 0x1, 0xf, 0x10, 0x40, 0xf0, 0x0f000200, 0x0f00024f, 0x244,
				     0x10000000, 0x04000000, 0xe0fffb00 };
	unsigned int i, j, s, polls, closed, back, n;
	u32 now[SIOD_N], v;

	printf("consts %x %x %x %x %x %x %x %x %x\n", SIOD_N, SIOD_NOPOLL, SIOD_FIRST, SIOD_LAST, SIOD_BUSY, SIOD_GATE_64, GATE_SIO, GATES_AT,
	       ASC_PS_NOT_WRITTEN);
	printf("bits %x %x %x %x %x %x %x %x %x %u %u %x\n", SIOD_CLOSED, SIOD_AUTO_SHIFT, SIOD_AUTO_MASK, SIOD_BACK_STATE, SIOD_BACK_EN,
	       SIOD_BACK_GATE, SIOD_BACK_ALL, SIOD_AT, SIOD_AT_MASK, SIOD_AT_POLL, SIOD_AT_CLOSED, SIOD_MARK);
	printf("auto %d %d %d %d\n", SIOD_AUTO_EN_SET, SIOD_AUTO_OTHER, SIOD_AUTO_F, SIOD_AUTO_ZERO);
	printf("stages %x %x %x\n", M1_SIOD_STAGE_PRE, M1_SIOD_STAGE, M1_SIOD_STAGE_PEND);
	printf("offs");
	for (i = 0; i < sizeof(off) / sizeof(off[0]); i++)
		printf(" %x", off[i]);
	printf("\ngroups");
	for (i = 0; i < sizeof(grp); i++)
		printf(" %u", grp[i]);
	printf("\nfive");
	for (i = 0; i < sizeof(five) / sizeof(five[0]); i++)
		printf(" %x", five[i]);
	printf("\n");
	for (s = 0; s < 256; s++)
		printf("stage %x %x\n", s, siod_stage((u8)s));
	for (i = 0; i < 0x400; i += 4)
		printf("hasauto %x %d\n", i, siod_has_auto((u16)i));
	for (i = 0; i < sizeof(words) / sizeof(words[0]); i++) {
		printf("down %x %d\n", words[i], siod_down(words[i]));
		for (j = 0; j < sizeof(words) / sizeof(words[0]); j++)
			printf("back %x %x %x\n", words[i], words[j], siod_back(words[i], words[j]));
	}
	for (i = 0; i < 256; i++)
		printf("at %x %x\n", i, siod_byte_at(i));
	/* stdin: one case a line: polls, closed, back, then n pairs of a place and the word read there; the rest read 0. */
	while (scanf("%u %u %x %u", &polls, &closed, &back, &n) == 4) {
		memset(now, 0, sizeof(now));
		for (j = 0; j < n; j++) {
			if (scanf("%u %x", &i, &v) != 2 || i >= SIOD_N)
				return 1;
			now[i] = v;
		}
		printf("byte %x %x\n", siod_auto(off, now), siod_byte(off, now, polls, closed, (u8)back));
	}
	return 0;
}
"""
# m1_sio_down() and the three functions it calls, as they stand, against a model of SIO's page and the gate pages that logs
# every read, write and PMU byte. The scenario is argv[1], letters: c the gate 0x28e5a0050 closes once all 43 read off; l it
# closes 60 ms after that; w it reads closed from the start; b I2C4 keeps bit 11; s UART_P, found on, keeps its actual
# state; n SPI_P, found on, doesn't take its write-back; y SIO comes back without its auto-enable; a SIO takes its
# write-back but its actual state stays 0; g the gate, once closed, stays closed; f I2C0 keeps its bits
# 27:24 through the 0; u UART_N keeps bit 11 (B2 doesn't poll it); p SIO, SIO_CPU and SIO_SPMI0-2 read f in bits 27:24 after
# a 0, as they do after any switch-off on file; e SIO keeps its auto-enable through the 0; x SIO_CPU reads f in bits 27:24
# after its write-back, where it was found with 4, which the byte doesn't ask about.
SIOD_FN_PRELUDE = r"""
#include <stdio.h>
#include <string.h>
#include "asc_probe.h"
#include "sio_down.h"
typedef uint16_t u16;
typedef uint64_t u64;
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define static_assert(...) _Static_assert(__VA_ARGS__, "")
#define BIT(n) (1U << (n))
#define M1_PS_SIO_PAGE 0x28e580000ULL
#define dsb(x) do { } while (0)
#define isb() do { } while (0)
#define cpu_relax() do { } while (0)
static unsigned char ga_page[0x4000], gb_page[0x4000], sio_page[0x4000];
static void *m1_look_ga = ga_page, *m1_look_gb = gb_page, *m1_sio = sio_page;
static const char *sc;
static u64 clk, m1_tps = 24000000, shut_at;
static int latched;
static u32 word[0x400 / 4];
/* Every look at the clock is 1 ms on. */
static u64 m1_now(void) { return clk += m1_tps / 1000; }
static int has(char c) { return strchr(sc, c) != NULL; }
static int all_off(void)
{
	unsigned int o;

	for (o = SIOD_FIRST; o <= SIOD_LAST; o += 8)
		if (word[o / 4] & 0xff)
			return 0;
	return 1;
}
static u32 readl(const void *p)
{
	const unsigned char *c = p;
	unsigned long o;
	u32 v;

	if (c >= sio_page && c < sio_page + sizeof(sio_page)) {
		o = (unsigned long)(c - sio_page);
		v = o < 0x400 ? word[o / 4] : 0xdead0000;
		printf("r S %lx %x\n", o, v);
		return v;
	}
	if (c >= ga_page && c < ga_page + sizeof(ga_page)) {
		o = (unsigned long)(c - ga_page);
		v = 0x8004030f;
		if (o == 0x64 || o == 0x30c)
			v |= GATE_CLOSED;
		if (o == 0x50) {
			if (all_off() && !shut_at)
				shut_at = clk + (has('l') ? 60 * m1_tps / 1000 : 0);
			if (has('w') || ((has('c') || has('l')) && all_off() && clk >= shut_at) || (has('g') && latched))
				v |= GATE_CLOSED;
			latched = !!(v & GATE_CLOSED);
		}
		printf("r A %lx %x\n", o, v);
		return v;
	}
	o = (unsigned long)(c - gb_page);
	v = 0x8004031d | (o == 0x338 ? GATE_CLOSED : 0);
	printf("r B %lx %x\n", o, v);
	return v;
}
static void writel(u32 v, void *p)
{
	unsigned long o = (unsigned long)((unsigned char *)p - sio_page);
	u32 keep, was;

	printf("w S %lx %x\n", o, v);
	if (o >= 0x400)
		return;
	was = word[o / 4];
	if (has('n') && o == 0x1e8 && v)
		return;
	if (has('a') && o == 0x180 && v) {
		word[o / 4] = (v & 0x1f00000f) | (was & 0x300);
		return;
	}
	/* The settings as written, the actual state following the target, bit 11 clear, the two flags kept. */
	keep = was & 0x300;
	word[o / 4] = (v & 0x1f00000f) | ((v & 0xf) << 4) | keep;
	if (has('f') && o == 0x1a8 && !v)
		word[o / 4] |= was & 0x1f000000;
	if (has('p') && !(v & 0x10000000) && (o == 0x180 || o == 0x188 || o == 0x218 || o == 0x220 || o == 0x228))
		word[o / 4] |= 0x0f000000;
	if (has('e') && o == 0x180 && !v)
		word[o / 4] |= was & 0x10000000;
	if (has('x') && o == 0x188 && v)
		word[o / 4] |= 0x0f000000;
	if (has('y') && o == 0x180 && v)
		word[o / 4] &= ~0x10000000U;
	if (has('b') && o == 0x1c8 && !v)
		word[o / 4] |= 0x800;
	if (has('u') && o == 0x238 && !v)
		word[o / 4] |= 0x800;
	if (has('s') && o == 0x230 && !v)
		word[o / 4] |= was & 0xf0;
	shut_at = 0;
}
static void m1_crumb(unsigned int addr, const u8 *b, unsigned int n)
{
	printf("k %x %u %x\n", addr, n, b[0]);
}
"""
SIOD_FN_MAIN = r"""
int main(int argc, char **argv)
{
	unsigned int o;
	u8 b;

	(void)argc;
	sc = argv[1];
	/* As the recipe leaves them: SIO, SIO_CPU, SPI_P and UART_P on, five UARTs at macOS's 0x200, the rest off in two ways. */
	for (o = SIOD_FIRST; o <= SIOD_LAST; o += 8)
		word[o / 4] = o % 16 ? 0x0f000200 : 0x00000300;
	word[0x180 / 4] = 0x1f0000ff;
	word[0x188 / 4] = 0x040002ff;
	word[0x1e8 / 4] = 0x000000ff;
	word[0x230 / 4] = 0x000001ff;
	word[0x250 / 4] = 0x00000a00;
	/* p: the five read f in bits 27:24 whenever their auto-enable is clear, so SIO_CPU and SIO_SPMI1 are found that way too. */
	if (has('p')) {
		word[0x188 / 4] = 0x0f0002ff;
		word[0x220 / 4] = 0x0f000300;
	}
	b = m1_sio_down();
	printf("ret %x\n", b);
	for (o = SIOD_FIRST; o <= SIOD_LAST; o += 8)
		printf("end %x %x\n", o, word[o / 4]);
	return 0;
}
"""
SIOD_INIT_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include "asc_probe.h"
#include "sio_down.h"
typedef uint16_t u16;
typedef uint64_t u64;
#define __iomem
#define __init
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define BIT_ULL(n) (1ULL << (n))
#define GENMASK_ULL(h, l) (((~0ULL) << (l)) & (~0ULL >> (63 - (h))))
#define EINVAL 22
#define ENODEV 19
#define M1_PS_SIO_PAGE 0x28e580000ULL
static unsigned char page[0x4000];
static void *m1_sio, *m1_look_ga, *m1_look_gb;
static unsigned int nread;
static u64 never;
static u32 readl(const void *p) { printf("read %lx\n", (unsigned long)((const unsigned char *)p - page)); return 0x1000 + nread++; }
static int e1_never_write(u64 addr) { return addr == never; }
static void pr_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("err "); vprintf(fmt, ap); va_end(ap); }
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("info "); vprintf(fmt, ap); va_end(ap); }
"""
SIOD_INIT_MAIN = r"""
int main(int argc, char **argv)
{
	int ret;

	(void)argc;
	/* argv[1]: three letters, SIO's page and the two gate pages mapped (m) or not; argv[2]: an address on the never-write list. */
	m1_sio = argv[1][0] == 'm' ? page : NULL;
	m1_look_ga = argv[1][1] == 'm' ? page : NULL;
	m1_look_gb = argv[1][2] == 'm' ? page : NULL;
	sscanf(argv[2], "%llx", (unsigned long long *)&never);
	ret = m1_siod_init();
	printf("ret %d reads %u\n", ret, nread);
	return 0;
}
"""
# B2's order, written out a second time: SIO_CPU, I2C0-7, FPWM0-2, SIO_SPMI0-2, AES, SPI0-4, UART_N, UART0-4, UART6, UART7, MCA0-3,
# DPA0-4; SIO_ADMA, SPI_P, UART_P; AUDIO_P; SIO.
SIOD_ORDER = ([0x188] + list(range(0x1a8, 0x1e1, 8)) + [0x190, 0x198, 0x1a0] + [0x218, 0x220, 0x228] + [0x2d0] + list(range(0x1f0, 0x211, 8))
              + [0x238] + list(range(0x240, 0x271, 8)) + list(range(0x288, 0x2a1, 8)) + list(range(0x2a8, 0x2c9, 8)) + [0x280, 0x1e8, 0x230] + [0x278] + [0x180])


def sio_down_tests(g, src):
    """sio_down=1: sio_down.h against an independent reading of it; m1_sio_down() and what it calls compiled as they stand against
    a model of the registers that logs every access; m1_siod_init()'s refusals; the places in e1_hook.c that use them; and the
    byte, the run's stages and --clear through m1-crumbs."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    fns = (cut("static void m1_gates_read(u32 *g, u32 *lane)", "\n}\n") + cut("static bool m1_siod_poll(", "\n}\n")
           + cut("static u8 m1_siod_back(", "\n}\n") + cut("static u8 m1_sio_down(void)", "\n}\n"))
    init = cut("static int __init m1_siod_init(void)", "\n}\n")
    scen = ("", "c", "l", "w", "b", "s", "n", "u", "a", "cb", "cs", "cn", "cu", "cw", "ca", "cg", "f", "cf", "bsn", "cbsnu",
            "p", "cp", "e", "x", "cx", "pf", "pe", "cag", "y", "cy")
    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return os.path.join(tmp, name)

        def out(exe, *args):
            return subprocess.run([exe, *args], capture_output=True, text=True, check=True).stdout.splitlines()

        # siod_byte()'s cases: nothing set; each place alone with f in bits 27:24, with 4 there, with auto-enable, with a target,
        # with an actual state, with flags only; and 400 drawn at random, sparse, with every part of the byte varied.
        rnd = random.Random(20261111)
        cases = [(1, 0, 0, {}), (1, 1, 0x38, {}), (0, 1, 0xff, {}), (1, 0, 0xc7, {})]
        for i in range(43):
            cases += [(1, i & 1, 0x38, {i: w}) for w in (0x0f000000, 0x04000b00, 0x10000000, 0x1f000000, 0x1, 0x10, 0xb00, 0x0f000040)]
        five_at = [SIOD_ORDER.index(o) for o in (0x180, 0x188, 0x218, 0x220, 0x228)]
        cases += [(1, 0, 0, {i: 0x0f000200 for i in five_at}), (1, 1, 0x28, {i: 0x0f000200 for i in five_at[:2]}),
                  (0, 0, 0x38, {i: 0x0f000200 for i in five_at}), (1, 0, 0x38, {**{i: 0x0f000200 for i in five_at}, 7: 0x0f000000}),
                  (1, 0, 0x38, {**{i: 0x0f000200 for i in five_at}, five_at[2]: 0x0e000000}),
                  (1, 0, 0x38, {7: 0x0f000000, 30: 0x10000000}), (1, 0, 0x38, {30: 0x10000000, 7: 0x04000000}),
                  (1, 1, 0x38, {12: 0x20, 3: 0x2, 42: 0xff}), (0, 1, 0x38, {42: 0xff})]
        for _ in range(400):
            now = {rnd.randrange(43): rnd.choice((0, 0x200, 0x0f000000, 0x0f000200, 0x04000000, 0x10000000, 0x1f000000, 0x0f, 0x40, 0x800))
                   for _ in range(rnd.choice((0, 1, 1, 2, 3, 6)))}
            cases.append((rnd.randrange(4) > 0, rnd.randrange(2), rnd.randrange(256), now))
        feed = "".join(f"{int(p)} {c} {bk:x} {len(now)} " + " ".join(f"{i} {w:x}" for i, w in now.items()) + "\n" for p, c, bk, now in cases)
        hdr = [line.split() for line in subprocess.run([build("hdr", SIOD_HDR)], input=feed, capture_output=True, text=True, check=True).stdout.splitlines()]
        fn = build("fn", SIOD_FN_PRELUDE + fns + SIOD_FN_MAIN)
        runs = {a: out(fn, a) for a in scen}
        ini = build("ini", SIOD_INIT_PRELUDE + init + SIOD_INIT_MAIN)
        loads = {a: out(ini, *a) for a in (("mmm", "0"), ("nmm", "0"), ("mnm", "0"), ("mmn", "0"), ("mmm", "28e5802d0"), ("mmm", "28e580180"),
                                           ("mmm", "28e5802d8"))}
        # The list or the groups changed under the load's check: each has to be refused.
        bad_lists = {}
        for what, redef in (("a word twice", "#undef SIOD_OFFS\n#define SIOD_OFFS { " + ", ".join(hex(o) for o in SIOD_ORDER[:-1] + [0x188]) + " }\n"),
                            ("a word past the subtree", "#undef SIOD_OFFS\n#define SIOD_OFFS { " + ", ".join(hex(o) for o in SIOD_ORDER[:-1] + [0x2d8]) + " }\n"),
                            ("a word below the subtree", "#undef SIOD_OFFS\n#define SIOD_OFFS { " + ", ".join(hex(o) for o in SIOD_ORDER[:-1] + [0x160]) + " }\n"),
                            ("a word off its place", "#undef SIOD_OFFS\n#define SIOD_OFFS { " + ", ".join(hex(o) for o in SIOD_ORDER[:-1] + [0x184]) + " }\n"),
                            ("groups that don't add up", "#undef SIOD_GROUPS\n#define SIOD_GROUPS { 38, 3, 1 }\n")):
            bad_lists[what] = out(build("bad", SIOD_INIT_PRELUDE + redef + init + SIOD_INIT_MAIN), "mmm", "0")

    # The header.
    check(hdr[0] == ["consts", "2b", "238", "180", "2d0", "800", "64", "50", "6016", "b00"]
          and hdr[1] == ["bits", "1", "1", "6", "8", "10", "20", "38", "40", "3f", "62", "63", "80"] and hdr[2] == ["auto", "0", "1", "2", "3"]
          and hdr[3] == ["stages", "3d", "3f", "3e"]
          and [int(x, 16) for x in hdr[4][1:]] == SIOD_ORDER and hdr[5] == ["groups", "38", "3", "1", "1"]
          and hdr[6] == ["five", "180", "188", "218", "220", "228"]
          and sorted(SIOD_ORDER) == list(range(0x180, 0x2d1, 8)) and len(SIOD_ORDER) == 43,
          "sio_down.h: the 43 words from 0x180 to 0x2d0 in B2's order and four groups, UART_N the one not polled, bit 11, the gates at 0x64"
          " and 0x50, the byte at 0x6016 with its fields where the header's comment has them, the five words with an auto field, stages"
          " 0x3d, 0x3f, 0x3e", str(hdr[:7]))
    stages = {int(r[1], 16): int(r[2], 16) for r in hdr if r[0] == "stage"}
    check(len(stages) == 256 and all(v == {0x4b: 0x3d, 0x55: 0x3f, 0x54: 0x3e}.get(k, k) for k, v in stages.items())
          and (g["SIOD_PRE"], g["SIOD_WD2"], g["SIOD_STAGES"]) == (0x3d, (0x3e, 0x3f), (0x3d, 0x3e, 0x3f))
          and not set(g["SIOD_STAGES"]) & (set(g["GATES_STAGES"]) | {0x52, 0x51, 0x53, 0x4e, 0x4f, 0x56, 0x57}),
          "siod_stage(): gates_mark=1's 0x4b, 0x55 and 0x54 become 0x3d, 0x3f and 0x3e, every other stage stays, and m1-crumbs has the same three")
    FIVE = (0x180, 0x188, 0x218, 0x220, 0x228)
    hasauto = {int(r[1], 16): r[2] for r in hdr if r[0] == "hasauto"}
    downs = {int(r[1], 16): r[2] for r in hdr if r[0] == "down"}
    check(len(hasauto) == 256 and all((v == "1") == (k in FIVE) for k, v in hasauto.items())
          and all((v == "1") == (not k & 0xff) for k, v in downs.items()) and len(downs) == 17 and downs[0x0f000200] == "1" and downs[0x40] == "0",
          "siod_down(): a word is off in its power state when its target and its actual state read 0, whatever its auto fields and flags;"
          " siod_has_auto(): SIO, SIO_CPU and SIO_SPMI0-2 and no other offset", str(downs))
    backs = [[int(x, 16) for x in r[1:]] for r in hdr if r[0] == "back"]
    bad = [r for r in backs if r[2] != (0 if (r[0] ^ r[1]) & 0xff else 8) | (0 if (r[0] ^ r[1]) & 0x10000000 else 0x10)]
    check(len(backs) == 17 * 17 and not bad and {r[2] for r in backs} == {0, 8, 0x10, 0x18},
          "siod_back(): bit 3 if the target and the actual state read as found, bit 4 if the auto-enable does, each by itself;"
          " bits 27:24, bits 8, 9 and 11 and the rest don't count", str(bad[:4]))
    ats = {int(r[1], 16): int(r[2], 16) for r in hdr if r[0] == "at"}
    check(len(ats) == 256 and all(v == 0xc0 | (k & 0x3f) for k, v in ats.items()),
          "siod_byte_at(): bits 7 and 6, and the place in the low six")

    def want_auto(now):
        if any(w & 1 << 28 for w in now.values()):
            return 0
        fs = {SIOD_ORDER[i]: w >> 24 & 0xf for i, w in now.items() if w >> 24 & 0xf}
        return 3 if not fs else 2 if all(f == 0xf and o in FIVE for o, f in fs.items()) else 1

    def want_byte(polls, closed, back, now):
        up = sorted(i for i, w in now.items() if w & 0xff)
        if up:
            return 0xc0 | up[0]
        if not polls:
            return 0xc0 | 62
        return 0x80 | closed | want_auto(now) << 1 | (back & 0x38)

    rows = [[int(x, 16) for x in r[1:]] for r in hdr if r[0] == "byte"]
    bad = [(c, r) for c, r in zip(cases, rows) if r != [want_auto(c[3]), want_byte(*c)]]
    seen = {r[1] for r in rows}
    check(len(rows) == len(cases) and not bad and {r[0] for r in rows} == {0, 1, 2, 3} and {0xc0 | i for i in range(43)} | {0xfe} <= seen
          and len({b for b in seen if not b & 0x40}) >= 48,
          "siod_byte() and siod_auto(): a word whose target or actual state isn't 0 gives the first such word's place under bits 7 and 6; a"
          " poll unmet, 62; otherwise bit 0 the gate, bits 2:1 the auto fields (3 all 0; 2 auto-enable clear and f only in the five; 1 anything"
          " else in bits 27:24; 0 an auto-enable set, whatever else), bits 5:3 what came back, bit 6 clear, bit 7 set", str(bad[:3]))
    words = g["SIOD_WORDS"]
    names = (["SIO_CPU"] + [f"I2C{i}" for i in range(8)] + [f"FPWM{i}" for i in range(3)] + [f"SIO_SPMI{i}" for i in range(3)] + ["AES"]
             + [f"SPI{i}" for i in range(5)] + ["UART_N"] + [f"UART{i}" for i in (0, 1, 2, 3, 4, 6, 7)] + [f"MCA{i}" for i in range(4)]
             + [f"DPA{i}" for i in range(5)] + ["SIO_ADMA", "SPI_P", "UART_P", "AUDIO_P", "SIO"])
    check([o for _, o in words] == SIOD_ORDER and [n for n, _ in words] == names
          and tuple(g[k] for k in ("SIOD_CLOSED", "SIOD_AUTO_SHIFT", "SIOD_BACK_STATE", "SIOD_BACK_EN", "SIOD_BACK_GATE", "SIOD_AT", "SIOD_MARK",
                                   "SIOD_AT_POLL", "SIOD_AT_CLOSED", "SIOD_AUTO_EN_SET", "SIOD_AUTO_OTHER", "SIOD_AUTO_F", "SIOD_AUTO_ZERO"))
          == (1, 1, 8, 0x10, 0x20, 0x40, 0x80, 62, 63, 0, 1, 2, 3),
          "m1-crumbs has the byte's fields as the header has them, and the 43 words by name in B2's order")

    # m1_sio_down() in every scenario: the accesses, in order.
    found = {o: (0x0f000200 if o % 16 else 0x300) for o in SIOD_ORDER}
    found.update({0x180: 0x1f0000ff, 0x188: 0x040002ff, 0x1e8: 0xff, 0x230: 0x1ff, 0x250: 0xa00})
    notback = {"n": 0x1e8, "a": 0x180, "x": 0x188, "y": 0x180}
    polled = [o for o in SIOD_ORDER if o != 0x238]
    found0 = found
    for a in scen:
        found = {**found0, **({0x188: 0x0f0002ff, 0x220: 0x0f000300} if "p" in a else {})}
        ev = [l.split() for l in runs[a]]
        acc = [(e[0], e[1], int(e[2], 16), int(e[3], 16)) for e in ev if e[0] in "rw"]
        ws = [(i, e[2], e[3]) for i, e in enumerate(acc) if e[0] == "w"]
        ks = [(i, e) for i, e in enumerate(ev) if e[0] == "k"]
        ret = int([e for e in ev if e[0] == "ret"][0][1], 16)
        end = {int(e[1], 16): int(e[2], 16) for e in ev if e[0] == "end"}
        closes, late, was = "c" in a or "l" in a, "l" in a, "w" in a
        # The byte, worked out from the scenario alone: a word that stays up, by its place; else a poll unmet; else the gate, the
        # auto fields, and, in the second send, what came back.
        if "s" in a:
            want = want2 = 0xc0 | SIOD_ORDER.index(0x230)
        elif "b" in a:
            want = want2 = 0xc0 | 62
        else:
            want = 0x80 | (1 if closes else 0) | (0 if "e" in a else 1 if "f" in a else 2 if "p" in a else 3) << 1
            want2 = want | (0 if "a" in a or "n" in a else 8) | (0 if "y" in a else 0x10) | (0 if "g" in a else 0x20)
        # 1. As found, in B2's order, then the gate once, before any write.
        first = acc[:44]
        ok = [(e[1], e[2]) for e in first] == [("S", o) for o in SIOD_ORDER] + [("A", 0x50)] and all(e[0] == "r" for e in first)
        if was:
            # Closed before the first write: nothing more is read, nothing is written, and the byte says so, once.
            check(ok and len(acc) == 44 and not ws and len(ks) == 1 and ks[0][1][1:] == ["6016", "1", "ff"] and ret == 0xff
                  and ev[ks[0][0] + 1][0] == "ret" and end == found,
                  f"m1_sio_down(), scenario '{a}': 0x28e5a0050 closed before the first write: the 43 and the gate read, nothing written, 0xff"
                  " sent once and returned", " ".join(runs[a][44:48]))
            continue
        # 2. The switch-off: 43 writes of 0, B2's order; each group's writes together, then its polls, in order, UART_N's left out.
        offw = ws[:43]
        ok = ok and [(o, v) for _, o, v in offw] == [(o, 0) for o in SIOD_ORDER] and all(e[1] == "S" for e in acc if e[0] == "w")
        at, n = 44, 0
        for size in (38, 3, 1, 1):
            grp = SIOD_ORDER[n:n + size]
            ok = ok and [(e[0], e[2]) for e in acc[at:at + size]] == [("w", o) for o in grp]
            at += size
            for o in grp:
                if o == 0x238:
                    continue
                k = at
                while at < len(acc) and acc[at][:3] == ("r", "S", o):
                    at += 1
                # One read if the poll is met at once; about 100 of them, a millisecond apart, if it never is.
                stuck = "b" in a and o == 0x1c8
                ok = ok and (at - k == 1 if not stuck else 95 <= at - k <= 105)
            n += size
        # 3. The two gates polled, 0x64 first, then all 43 read again in the same order, then the six gate words and the lane.
        k = at
        while acc[at][:3] == ("r", "A", 0x64):
            at += 1
        ok = ok and at - k == 1
        k = at
        while acc[at][:3] == ("r", "A", 0x50):
            at += 1
        shut = closes and "s" not in a
        ok = ok and (at - k == 1 if was or (shut and not late) else 55 <= at - k <= 65 if shut and late else 95 <= at - k <= 105)
        ok = ok and [e[:3] for e in acc[at:at + 43]] == [("r", "S", o) for o in SIOD_ORDER]
        at += 43
        ok = ok and [e[:3] for e in acc[at:at + 7]] == [("r", "A", 0x50), ("r", "A", 0x64), ("r", "A", 0x30c), ("r", "B", 0x310), ("r", "B", 0x338),
                                                        ("r", "B", 0x34c), ("r", "S", 0x160)]
        at += 7
        # 4. The byte's first send, without "back", after those reads and before any write-back; 5. the write-back: all 43, in reverse,
        # each as found less bits 11, 9 and 8, whatever went before; 6. the byte again, last, and returned.
        backw = ws[43:]
        ok = ok and len(ws) == 86 and [(o, v) for _, o, v in backw] == [(o, found[o] & ~0xb00) for o in reversed(SIOD_ORDER)]
        # Each write-back is followed by reads of that word alone: one if it reads as found at once, about 10 ms of them if it never does.
        for n, (i, o, _) in enumerate(backw):
            nxt = backw[n + 1][0] if n + 1 < len(backw) else None
            reads = [e for e in acc[i + 1:nxt] if e[:3] == ("r", "S", o)]
            stuck = o in [notback[c] for c in a if c in notback]
            ok = ok and (8 <= len(reads) <= 13 if stuck else len(reads) == 1) and (nxt is None or len(reads) == nxt - i - 1)
        # ... and after the last of them, 0x28e5a0050 once more: one read if it's as before, 100 ms of them if it never is.
        tail = len(acc)
        while acc[tail - 1][:3] == ("r", "A", 0x50):
            tail -= 1
        ok = ok and backw[0][0] == at and all(e[0] == "r" and e[1] == "S" for e in acc[at:tail] if e[0] != "w") and backw[-1][0] < tail
        ok = ok and (95 <= len(acc) - tail <= 105 if "g" in a else len(acc) - tail == 1)
        ok = ok and len(ks) == 2 and all(e[1:3] == ["6016", "1"] for _, e in ks)
        b1, b2 = (int(e[3], 16) for _, e in ks)
        wline = [i for i, e in enumerate(ev) if e[0] == "w"]
        ok = ok and wline[42] < ks[0][0] < wline[43] and ks[1][0] > wline[85] and ev[ks[1][0] + 1][0] == "ret"
        ok = ok and b1 == want and b2 == want2 and ret == b2
        # And the words end as they were found (their two flags aside), but for the one that didn't take its write-back.
        ok = ok and all((end[o] ^ found[o]) & 0x1f0000ff == 0 for o in SIOD_ORDER if o not in [notback[c] for c in a if c in notback])
        check(ok, f"m1_sio_down(), scenario '{a}': the 43 as found and the gate; 43 writes of 0 in B2's order and groups, each polled for bit 11 but"
                  " UART_N; 0x28e5a0064 then 0x28e5a0050 polled for up to 100 ms; the 43 and the gates read; the byte; 43 write-backs in reverse, as"
                  f" found less bits 11, 9 and 8, each waited for; the byte again (0x{want:02x}, then 0x{want2:02x}); no other write",
              f"bytes {b1:#x} {b2:#x} ret {ret:#x}; writes {len(ws)}; " + " ".join(runs[a][:3]))
    # The byte, as returned, in the cases that matter.
    def byte(a):
        return int(runs[a][-44].split()[1], 16)
    want = {"": 0xbe, "c": 0xbf, "l": 0xbf, "w": 0xff, "cw": 0xff, "s": 0xe8, "cs": 0xe8, "b": 0xfe, "cb": 0xfe, "bsn": 0xe8, "u": 0xbe, "n": 0xb6, "cn": 0xb7, "y": 0xae, "cy": 0xaf,
            "a": 0xb6, "ca": 0xb7, "cg": 0x9f, "cag": 0x97, "f": 0xba, "cf": 0xbb, "p": 0xbc, "cp": 0xbd, "e": 0xb8, "x": 0xbe, "cx": 0xbf,
            "pf": 0xba, "pe": 0xb8}
    check(all(byte(a) == v for a, v in want.items()),
          "m1_sio_down(): 0xbe with the gate open and everything as meant, 0xbf closed (at once or 60 ms on); 0xbc and 0xbd if the five read f"
          " in bits 27:24 after the 0; 0xff closed before; 0xe8 for UART_P staying up, 0xfe for a poll never met; a word that doesn't come"
          " back in its target or actual state, one that comes back without its auto-enable, and a gate that stays closed after the"
          " write-back each clear their own bit, and bits 27:24 reading otherwise after the write-back clear none; bits 27:24 kept outside the five, or an auto-enable kept, lower the auto code; UART_N's bit 11 isn't asked about",
          " ".join(f"{a}={byte(a):#x}" for a in scen))

    # The source: what the function writes, and nothing else like it.
    body = cut("static u8 m1_sio_down(void)", "\n}\n")
    back = cut("static u8 m1_siod_back(", "\n}\n")
    poll = cut("static bool m1_siod_poll(", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("
    check(re.findall(wr + r"[^;]*;", body) == ["writel(0, m1_sio + off[i]);"] and re.findall(wr + r"[^;]*;", back) == ["writel(found & ~ASC_PS_NOT_WRITTEN, p);"]
          and not re.search(wr, poll) and not re.search(wr, init) and len(re.findall(r"\bm1_siod_back\(", code_only)) == 3
          and len(re.findall(r"\bm1_sio_down\(", code_only)) == 2 and "m1_look_gb" not in body.replace("m1_gates_read", ""),
          "m1_sio_down() writes 0 to the listed words in one place, the write-back is one function called from one place (and from sio_steps=1's), and the poll and"
          " the load write nothing")
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    check(in_order_of(after, ["\t\tm1_crumb(0x6004, b, 4);\n", "\t\t\tm1_crumb(0x6012, b, 2);\n", "\tif (gates_mark && !sio_down && !pcie_down && !sio_steps && !a2_pre) {\n",
                              "\tif (sio_down)\n\t\tm1_gates = m1_sio_down();\n", "\t/* recfg_bite=1", "/* 9 */", "\tlocal_daif_mask();",
                              "writel_relaxed(1, commit);", "m1_alarm_arm(rtc);"])
          and after.count("m1_sio_down(") == 1 and len(re.findall(r"\bsiod_stage\(", code_only)) == 2
          and "\tif (gates_mark)\n\t\tb[0] = gates_stage(b[0]);\n\tif (sio_down)\n\t\tb[0] = siod_stage(b[0]);\n\tif (pcie_down)\n\t\tb[0] = pcd_stage(b[0]);\n\tif (sio_steps)\n\t\tb[0] = sst_stage(b[0]);\n\tif (a2_pre)\n\t\tb[0] = a2p_stage(b[0]);\n\tb[1] = m1_wd2_sample(" in src,
          "step 8: after the stage and the outcomes, in place of gates_mark=1's reads and before step 9's mask and the commit; its byte is"
          " what the alarm's last command sends; the stage is mapped in two places", after[-4500:])
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool sio_down;\nmodule_param(sio_down, bool, 0444);\n" in src and not re.search(r"\bsio_down\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (sio_down && (!gates_mark || probe)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n", "\treturn 0;\n\nbad_wait:"])
          and 'sio_down ? " sio_down=1" : ""' in src
          and in_order_of(src, ["\tif (gates_mark) {\n\t\tint ret = m1_gates_init();", "\tif (sio_down) {\n\t\tint ret = m1_siod_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n"]),
          "sio_down=1 loads only with gates_mark=1 and never with probe=1; it's off by default, read-only, named in the load line, and the"
          " load runs m1_siod_init() after the gate pages are mapped and returns its refusal", params[-1500:])

    # m1_siod_init(): the 43 read once each and logged, in address order; refused, with nothing read, if a page isn't mapped, a word is
    # on the never-write list, or the list isn't the 43.
    ok = loads[("mmm", "0")]
    check(ok[-1] == "ret 0 reads 43" and [r for r in ok if r.startswith("read ")] == [f"read {o:x}" for o in range(0x180, 0x2d1, 8)]
          and len([r for r in ok if r.startswith("info m1: sio_down: awake: from 0x28e580")]) == 6 and not any(r.startswith("err") for r in ok)
          and ok[-2].endswith("0000102a 00000000 00000000 00000000 00000000 00000000"),
          "m1_siod_init(): the 43 words read once each, in address order, and logged", "\n".join(ok))
    check(all(loads[(m, "0")][-1] == "ret -19 reads 0" for m in ("nmm", "mnm", "mmn"))
          and all(loads[("mmm", n)][-1] == "ret -22 reads 0" for n in ("28e5802d0", "28e580180"))
          and loads[("mmm", "28e5802d8")][-1] == "ret 0 reads 43"
          and all(v[-1] == "ret -22 reads 0" and any(r.startswith("err m1: sio_down:") for r in v) for v in bad_lists.values()),
          "m1_siod_init(): refuses, reading nothing, if a page isn't mapped, one of the 43 is on the never-write list, a word is listed twice"
          " or lies outside the subtree or off its place, or the groups don't add up", str(loads) + str(bad_lists))

    # m1-crumbs. The run's record at its last stage, all as meant, with its byte at 0x6016.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        alarm = (0x41, 121544 + 3600, 0, 0)
        rec = dict(parked=0x7d, left=0, down=0xf5, secs=121544, prog=(0xff, 0xff), flags=0x90, isrs=0x8d)

        def dec(stage, gb, x=217, **kw):
            fake(path, scratch(stage, **dict(rec, drained=gb, **kw)), rtc_secs=121544 + x + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            return r.returncode, r.stdout, r.stderr

        rc, out_, err = dec(0xbf, 0xbe, x=90)
        check(rc == 0 and "stage 0x6004 = 0xbf (pmp_out=1: bit 7, so the stage is 0x3f) (pmgr_late=1): sio_down=1: gates_mark=1's 0x55:" in out_
              and "0x6016 = 0xbe: sio_down=1's byte, in place of the alarm's result:" in out_
              and "  the 43 words after the polls: all read target 0 and actual state 0, and B2's 42 polls for bit 11 clear were all met:" in out_
              and "  0x28e5a0050 before the first write: open, as in every run so far\n" in out_
              and "  the 43 words' auto fields after the 0: all 0 in all 43: the 0 reads back whole\n" in out_
              and "  0x28e5a0050 (B2's op 140), after up to 100 ms for 0x28e5a0064 and 100 ms more for it: bit 30 clear, OPEN\n" in out_
              and "  written back, target and actual state: all 43 read as found within 10 ms each\n" in out_
              and "  written back, auto-enable (bit 28): all 43 read as found then; bits 27:24 aren't compared\n" in out_
              and "  0x28e5a0050 after the write-back: open again within 100 ms\n" in out_ and "doesn't compare" not in out_
              and "  so: 0x28e5a0050 stayed OPEN with SIO's subtree off" in out_ and "not B2's at op 140" in out_
              and "nothing records the commit" not in out_ and "alarm command" not in out_ and "gates_mark=1's byte" not in out_
              and "0x6007 = 0xf5: fabric_up=1's byte, in place of the cores that held:\n" in out_ and "WD1 stood as the marker" in out_
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out_
              and "0x6013 = 0xff: ps_words=1 wrote the 20 power words" in out_
              and "  wd1_mark=1's marker: about 90 s is what WD1 gives with its count never zeroed: B2 didn't reach its op 224" in out_
              and "0x140c is the commit's second + 3600, as the run wrote it" in out_,
              "sio_down=1's 0xbf: its byte read line by line in the alarm's result's place; the rest read as --m1-gates' 0xd5", out_ + err)
        # The same record under --m1-gates' 0xd5 and under 0xbf differs only in the stage's and the byte's lines.
        rc5, out5, err5 = dec(0xd5, 0xbe)
        rcc, outc, errc = dec(0xbf, 0xbe)
        skip = ("0x6000:", "0x6010:", "stage 0x6004", "0x6016", "  0x28e", "  AFNC2", "  so: ", "  the 43", "  the other four", "  written back")
        missing = [l for l in out5.splitlines() if not l.startswith(skip) and l not in outc.splitlines()]
        check(rc5 == 0 and rcc == 0 and "gates_mark=1's byte" in out5 and "sio_down=1's byte" not in out5 and not missing,
              "0xbe at 0x6016: the gate byte under 0xd5, sio_down=1's under 0xbf, and nothing else read differently", "\n".join(missing))
        rc, out_, err = dec(0xbe, 0xbf)
        check(rc == 0 and "so the stage is 0x3e" in out_ and "sio_down=1: gates_mark=1's 0x54:" in out_ and "pending afterwards" in out_
              and "  so: 0x28e5a0050 CLOSED once SIO's subtree was off" in out_ and "a2_pre=1 is next" in out_
              and "  wd1_mark=1's marker: not read. CPU0 had an exception pending" in out_,
              "sio_down=1's 0xbe: the pending stage, its byte, and no reading of the time", out_ + err)
        rc, out_, err = dec(0xbf, 0xbc, x=90)
        check(rc == 0 and "bits 27:24 f in one or more of SIO, SIO_CPU and SIO_SPMI0-2, 0 everywhere else" in out_
              and "  so: 0x28e5a0050 stayed OPEN with SIO's subtree off" in out_ and "GPIO's power word first" in out_
              and "taken as how those words read with auto-enable clear" in out_ and "doesn't compare" not in out_,
              "sio_down=1's 0xbc at 0x6016: f in the five's bits 27:24 is said, and the gate's reading still counts, with that said", out_ + err)
        rc, out_, err = dec(0xbf, 0xc0 | 40, x=90)
        check(rc == 0 and "UART_P (0x28e580230), place 40 of 0 to 42 in B2's order, DIDN'T READ target 0 and actual state 0" in out_
              and "  so: SIO's subtree wasn't off, so the gate was bound to stay open and its reading isn't kept. What holds UART_P up" in out_
              and "  written back: NOT RECORDED in this case" in out_ and "the restart's time doesn't compare with --m1-gates'" in out_
              and "bit 30" not in out_,
              "sio_down=1's 0xe8: the word that stayed up by name and address, no reading of the gate, the write-back not recorded", out_ + err)
        rc, out_, err = dec(0xbd, 0x86, x=90, parked=0xfe)
        check(rc == 0 and "so the stage is 0x3d" in out_ and "8 (sio_down=1): gates_mark=1's 0x4b, with SIO's subtree switched off and put back" in out_
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out_
              and "  written back: NOTHING CONFIRMED" in out_ and "the restart's time doesn't compare with --m1-gates'" in out_
              and "  stage 0x3d, so nothing records the commit after this byte (0x3e or 0x3f comes after it)" in out_
              and "  wd1_mark=1's marker: not read. Stage 0x3d is the last before the commit" in out_,
              "sio_down=1's 0xbd with the byte's first send: the stage before the commit, the write-back not confirmed, no reading of the time",
              out_ + err)
        for x in (10, 90):
            rc, out_, err = dec(0xbd, 0, x=x, parked=0xfe)
            check(rc == 0 and "0x6016 = 0x00: SIO_DOWN'S BYTE DIDN'T LAND (bit 7 clear), though stage 0x3d and the steps' outcomes did" in out_
                  and "bit 30" not in out_ and "nothing shows them written back" in out_ and f"the SoC restarted about {x} s after the commit" in out_,
                  f"sio_down=1's 0xbd with 0x6016 = 0, a restart at {x} s: said to have stopped in the switch-off, with nothing shown written back",
                  out_ + err)
        for stage, gb, kw, want in ((0xbf, 0x00, {}, "0x6016 = 0x00: bit 7 is clear, so sio_down=1's byte didn't survive the reset"),
                                    (0xbe, 0x7f, {}, "0x6016 = 0x7f: bit 7 is clear"),
                                    (0xbf, 0xbe, dict(prog=(0xff, 0x00)), "0x6016 = 0xbe: not read. sio_down=1's byte goes out after the steps' outcomes"),
                                    (0x3f, 0xbe, {}, "0x6016 = 0xbe: not read. A sio_down=1 run's stages always carry pmp_out=1's bit 7")):
            rc, out_, err = dec(stage, gb, **kw)
            check(rc == 0 and want in out_ and "bit 30" not in out_ and "SIO_DOWN'S BYTE" not in out_ and "alarm command" not in out_,
                  f"stage 0x{stage:02x}, 0x6016 = 0x{gb:02x}{', no outcomes marker' if kw.get('prog') else ''}: sio_down=1's byte isn't read", out_ + err)
        # Every byte, at each of the run's stages, read independently of the decoder.
        wrong = []
        autos = ("AN AUTO-ENABLE (bit 28) STILL READ SET", "bits 27:24 NOT as expected", "bits 27:24 f in one or more of SIO, SIO_CPU and SIO_SPMI0-2",
                 "all 0 in all 43")
        for s in (0x3d, 0x3e, 0x3f):
            for v in range(256):
                b = bytearray(0x20)
                b[0x12], b[0x13], b[0x16] = 0xc0, 0xff, v
                lines = g["siod_lines"](s, bytes(b), True)
                text = "\n".join(lines)
                so = [l for l in lines if l.startswith("  so: ")]
                at = v & 0x3f
                if not v & 0x80:
                    ok = len(lines) == 1 and "bit 30" not in text and ("SIO_DOWN'S BYTE DIDN'T LAND" in text) == (s == 0x3d)
                elif v & 0x40 and 43 <= at <= 61:
                    ok = len(lines) == 1 and "Not read" in text and f"{at} isn't one sio_down=1 sends" in text
                elif v & 0x40:
                    name, off = g["SIOD_WORDS"][at] if at < 43 else (None, None)
                    ok = (len(so) == 1 and "bit 30" not in text and "OPEN" not in text and "a2_pre" not in text
                          and ("NOTHING WAS WRITTEN" in so[0] and "ALREADY CLOSED" in lines[1]) == (at == 63)
                          and ("B2's 42 polls for bit 11 clear: NOT all met" in text and "INCONCLUSIVE on the gate" in so[0]) == (at == 62)
                          and (at >= 43 or (f"{name} (0x28e580{off:03x}), place {at} of" in lines[1] and f"What holds {name} up" in so[0]
                                            and names.index(name) == at and SIOD_ORDER[at] == off))
                          and ("came later than its time stamp" in text) == (at != 63)
                          and ("written back: NOT RECORDED" in text and "doesn't compare with --m1-gates'" in text) == (at != 63)
                          and ("nothing records the commit" in text) == (s == 0x3d))
                else:
                    auto, verdict = v >> 1 & 3, v >> 1 & 3 >= 2
                    back = [l for l in lines if l.startswith(("  written back", "  0x28e5a0050 after the write-back"))]
                    ok = (len(so) == 1 and "SIO's subtree was off" in lines[1] and "open, as in every run so far" in lines[2]
                          and lines[3].startswith("  the 43 words' auto fields after the 0: ") and [a in lines[3] for a in autos].index(True) == auto
                          and sum(a in lines[3] for a in autos) == 1
                          and "0x28e5a0050 (B2's op 140)" in lines[4] and ("SET, closed" in lines[4]) == bool(v & 1) and ("OPEN" in lines[4]) == (not v & 1)
                          and (len(back) == 1 and "NOTHING CONFIRMED" in back[0] if not v & 0x38 else
                               len(back) == 3 and ("NOT" not in back[0]) == bool(v & 8) and ("NOT" not in back[1]) == bool(v & 0x10)
                               and ("NOT" not in back[2]) == bool(v & 0x20) and "target and actual state" in back[0] and "auto-enable (bit 28)" in back[1])
                          and ("INCONCLUSIVE on the gate" in so[0]) == (not verdict)
                          and ("CLOSED once" in so[0] and "a2_pre=1 is next" in so[0]) == (verdict and bool(v & 1))
                          and ("stayed OPEN" in so[0] and "GPIO's power word first" in so[0]) == (verdict and not v & 1)
                          and ("not B2's at op 140" in so[0]) == verdict
                          and ("taken as how those words read with auto-enable clear" in so[0]) == (auto == 2)
                          and ("came before" in so[0] or "comes before GPIO or a2_pre=1" in so[0]) == (not verdict)
                          and ("doesn't compare with --m1-gates'" in text) == (v & 0x38 != 0x38)
                          and "came later than its time stamp" not in text
                          and ("nothing records the commit" in text) == (s == 0x3d))
                if not ok:
                    wrong.append((hex(s), hex(v), lines))
            # SIO's firmware not shown stopped: no verdict from the gate, whatever the byte, if anything was written.
            for code in (0, 0x40, 0x80):
                for v in (0xbe, 0xbf, 0xbc, 0xb9, 0x86, 0xc5, 0xfe):
                    b = bytearray(0x20)
                    b[0x12], b[0x13], b[0x16] = code, 0xff, v
                    so = [l for l in g["siod_lines"](s, bytes(b), True) if l.startswith("  so: ")]
                    if (len(so) != 1 or "INCONCLUSIVE: SIO's firmware isn't shown stopped" not in so[0] or "a2_pre" in so[0] or "a lead" in so[0]
                            or "lead" in so[0] or "GPIO" in so[0]):
                        wrong.append((hex(s), hex(code), hex(v), so))
            b = bytearray(0x20)
            b[0x12], b[0x13], b[0x16] = 0xc0, 0xff, 0xbe
            if "not read" not in g["siod_lines"](s, bytes(b), False)[0] or len(g["siod_lines"](s, bytes(b), False)) != 1:
                wrong.append((hex(s), "no pmp_out=1 bit"))
        check(not wrong, "sio_down=1: for every byte at each of its stages: a place under bit 6 names its word, or the poll, or the gate closed"
              " before, and gives no reading of the gate; otherwise each line follows its field, the gate's reading counts only with the auto"
              " fields all 0 or f in the five, and only if SIO's firmware was stopped, the restart's time compares only with the words' power"
              " state, their auto-enable and the gate all back; and nothing is read from a byte without bit 7", str(wrong[:2]))
        # The first run's record, its bytes as filed in results/sio-down-20261111/crumbs.txt (written out here: mutants.py's copies
        # have no results/): its stages and its byte's layout were the first form's, and none of it is read by the second form's rules.
        first = bytes.fromhex("00300050bc7d00f5" + "00" * 8 + "e18effdf908e9600" + "00" * 8)
        filed = ["0x6000: 00 30 00 50 bc 7d 00 f5  00 00 00 00 00 00 00 00", "0x6010: e1 8e ff df 90 8e 96 00  00 00 00 00 00 00 00 00"]
        fake(path, first, rtc_secs=121544 + 130, alarm=alarm)
        r = run(path, cnt="960000000 24000000")
        check(r.returncode == 0 and filed == r.stdout.splitlines()[:2] and len(r.stdout.splitlines()) == 3
              and "stage 0x6004 = 0xbc: one of sio_down=1's stages in its first form" in r.stdout and "NOT READ" in r.stdout
              and all(w not in r.stdout for w in ("so: ", "GPIO", "a2_pre", "was off", "unknown", "sio_down=1's byte"))
              and all(len(g["decode"](bytes([0, 0x30, 0, 0x50, st]) + first[5:], bytes(6))) == 3 for st in (0x3a, 0x3b, 0x3c, 0xba, 0xbb, 0xbc))
              and not set(g["SIOD1_STAGES"]) & (set(g["STAGES"]) | set(g["ALARM_STAGES"]) | set(g["SIOD_STAGES"])),
              "the first sio_down=1 run's record (stage 0xbc, 0x6016 = 0x96): said to be the first form's and not read, at each of its"
              " three stages; they aren't stages of any run this version reads", r.stdout + r.stderr)
        mr = g["mark_reading"]
        wrong = [(hex(v), x) for v in range(256) for x in (60, 88, 90, 105, 120, 123, 135, 146, 150, 163, 300)
                 if mr(x, 0x3d, v) != mr(x, 0x4b, v).replace("Stage 0x4b", "Stage 0x3d") or mr(x, 0x3e, v) != mr(x, 0x54, v)
                 or mr(x, 0x3f, v) != mr(x, 0x55, v)]
        check(not wrong and "Stage 0x3d is the last before the commit" in mr(90, 0x3d, 0x7d),
              "the restart's time is read against the marker at 0x3d, 0x3e and 0x3f exactly as at 0x4b, 0x54 and 0x55", str(wrong[:3]))
        # --clear: the run's alarm disarmed by its compare value; with the second lost, 0x6016 is never taken for the alarm's result.
        for stage, kw in ((0xbf, {}), (0xbe, {}), (0xbd, dict(parked=0xfe))):
            before = fake(path, scratch(stage, **dict(rec, drained=0xbe, **kw)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt="4800000000 24000000")
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and open(path, "rb").read() == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after sio_down=1's 0x{stage:02x}: the run's alarm disarmed by its compare value, and the crumbs zeroed", r.stdout + r.stderr)
        before = fake(path, scratch(0xbf, **dict(rec, secs=0, drained=0xbd)), rtc_secs=121761, alarm=alarm)
        r = run(path, "--clear", cnt="4800000000 24000000")
        check(r.returncode == 2 and open(path, "rb").read() == before and "disarmed" not in r.stdout
              and "after gates_mark=1's, sio_down=1's, pcie_down=1's, sio_steps=1's and a2_pre=1's stages it never does: it's that run's byte" in r.stderr,
              "--clear after 0xbf with the second lost and 0xbd at 0x6016: not taken for an acked alarm, so nothing is written", r.stdout + r.stderr)


SST_HDR = r"""
#include <stdio.h>
#include "sio_steps.h"
int main(void)
{
	static const u8 a[] = SST_A_OFFS, b[] = SST_B_OFFS;
	static const u32 w[] = { 0, 0x200, 0x300, 0x800, 0xb00, 0x10, 0xf0, 1, 0xf, 0xff, 0x8000000f, 0x800000ff, 0x0f000200, 0x80101100, 0xc0101100,
				 0x80100100, 0xc0100100, 0x00101100, 0x80101101, 0x40000000, 0x80000000, 0x10000000 };
	unsigned int i, s, n;

	printf("consts %x %x %x %x %d %d %x %x %d %d %d", SST_GPIO, SST_GPIO_UP, SST_CLK_VAL, SST_CLK_PENDING, SST_A_N, SST_B_N, SST_PRE_VAL,
	       GATES_AT, (int)(sizeof(a) / sizeof(a[0])), (int)(sizeof(b) / sizeof(b[0])), SST_GROUPS);
	for (i = 0; i < sizeof(a) / sizeof(a[0]); i++)
		printf(" %x", a[i]);
	for (i = 0; i < sizeof(b) / sizeof(b[0]); i++)
		printf(" %x", b[i]);
	printf("\nbits %x %x %x %x %x %x %x %x %x %x %x %x %x\n", SST_G1, SST_G2, SST_SUB, SST_GPIO_OK, SST_PRE, SST_BACK, SST_FINAL, SST_MARK, SST_RES,
	       SST_GO_KEEP, SST_GO_AT_LO, SST_GO_AT_HI, SST_GO_CLOSED);
	printf("stages %x %x %x\ncb %x\n", M1_SST_STAGE_PRE, M1_SST_STAGE, M1_SST_STAGE_PEND, sst_closed_before());
	for (s = 0; s < 256; s++) {
		printf("st %x %x\nf %x %x %d\n", s, sst_stage((u8)s), s, sst_final((u8)s), sst_second((u8)s));
		for (n = 0; n < 8; n++)
			printf("g %x %x %x\n", s, n, sst_going((u8)s, n));
	}
	for (i = 0; i < sizeof(w) / sizeof(w[0]); i++)
		printf("w %x %d %d\n", w[i], sst_idle(w[i]), sst_clk_ok(w[i]));
	return 0;
}
"""
# m1_sio_steps(), the poll and the write-back it calls, as they stand, against a model of the six pages that logs every read, write
# and PMU byte. The scenario is argv[1], letters: c the gate reads closed before anything; g it closes once the subtree is off and
# GPIO has been up and is off again; h it closes once the subtree is off and all 22 read as B2 writes them; o once closed it stays
# closed; u GPIO's actual state never comes up; b GPIO keeps bit 11 after its 0; k I2C0 keeps bit 11 after its 0; n SPI_P's actual
# state stays f after its 0; Q 0x28e03801c doesn't take the write; q its bit 30, pending, stays set; W it clears after 30 ms; r
# 0x28e5b0024 doesn't take its 1; R 0x28e0a0014 doesn't; x SIO_CPU's actual state stays 0 after its write-back; z it comes back
# after 50 ms; e SIO's auto-enable doesn't come back; a SIO keeps its auto-enable after the 0; A I2C0, not one of the five, reads f
# in bits 27:24 after it; B GPIO's bit 11 clears 200 ms after its 0, past its poll; U GPIO's actual state stays f after its 0; D
# GPIO takes 30 ms to come up; G the gate closes 50 ms after what it follows is there; O it opens again 30 ms after that is gone.
# The five words that read f in bits 27:24 after any switch-off do so here after a 0.
SST_FN_PRELUDE = r"""
#include <stdio.h>
#include <string.h>
#include "asc_probe.h"
#include "sio_steps.h"
typedef uint64_t u64;
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define static_assert(...) _Static_assert(__VA_ARGS__, "")
#define BIT(n) (1U << (n))
#define dsb(x) do { } while (0)
#define isb() do { } while (0)
#define cpu_relax() do { } while (0)
static unsigned char pg[6][0x4000];
static void *m1_sio = pg[0], *m1_ps = pg[1], *m1_look_ga = pg[2], *m1_look_pc = pg[3], *m1_look_pa = pg[4], *m1_look_pb = pg[5];
static const char names[] = "SPACXY";
static const char *sc;
static u64 clk, m1_tps = 24000000, cpu_back_at, gpio_up_at, gpio_zero_at, clk_at, cond_at, reopen_at;
static int gpio_up, gpio_zero, gpio_cycled, latched, cpu_back, clk_written, was_closed;
static u32 w[6][0x400 / 4];
/* Every look at the clock is 1 ms on. */
static u64 m1_now(void) { return clk += m1_tps / 1000; }
static int has(char c) { return strchr(sc, c) != NULL; }
static int sub_off(void)
{
	unsigned int o;

	for (o = 0x180; o <= 0x2d0; o += 8) {
		if (w[0][o / 4] & 0xff)
			return 0;
	}
	return 1;
}
static int pre_done(void)
{
	unsigned int o;

	if ((w[3][0x1c / 4] & ~0x40000000U) != 0x80101100)
		return 0;
	for (o = 4; o <= 0x1c; o += 4) {
		if (w[4][o / 4] != 1)
			return 0;
	}
	for (o = 4; o <= 0x38; o += 4) {
		if (w[5][o / 4] != 1)
			return 0;
	}
	return 1;
}
static u32 readl(const void *p)
{
	unsigned int k = (unsigned int)(((const unsigned char *)p - pg[0]) / 0x4000);
	unsigned long o = (unsigned long)((const unsigned char *)p - pg[k]);
	u32 v = o < 0x400 ? w[k][o / 4] : 0xdead0000;

	if (k == 0 && o == 0x188 && has('z') && cpu_back && clk < cpu_back_at + 50 * m1_tps / 1000)
		v &= ~0xf0U;
	if (k == 1 && o == 0x120) {
		if (has('D') && gpio_up && !gpio_zero && clk < gpio_up_at + 30 * m1_tps / 1000)
			v &= ~0xf0U;
		if (has('B') && gpio_zero && clk < gpio_zero_at + 200 * m1_tps / 1000)
			v |= 0x800;
	}
	if (k == 2) {
		int cond = (has('g') && sub_off() && gpio_cycled && !(w[1][0x120 / 4] & 0xff)) || (has('h') && sub_off() && pre_done());
		int closed = cond;

		if (o != 0x50) {
			closed = o == 0x64;
		} else {
			if (has('G')) {
				if (!cond)
					cond_at = 0;
				else if (!cond_at)
					cond_at = clk;
				closed = cond && clk >= cond_at + 50 * m1_tps / 1000;
			}
			if (has('O') && !cond && was_closed) {
				if (!reopen_at)
					reopen_at = clk;
				closed = clk < reopen_at + 30 * m1_tps / 1000;
			}
			if (closed)
				was_closed = 1;
			if (closed && has('o'))
				latched = 1;
			closed = closed || latched || has('c');
		}
		v = 0x80040000 | (closed ? 0x40000000 : 0);
	}
	if (k == 3 && (has('q') || (has('W') && clk_written && clk < clk_at + 30 * m1_tps / 1000)))
		v |= 0x40000000;
	printf("r %c %lx %x\n", names[k], o, v);
	return v;
}
static void writel(u32 v, void *p)
{
	unsigned int k = (unsigned int)(((unsigned char *)p - pg[0]) / 0x4000);
	unsigned long o = (unsigned long)((unsigned char *)p - pg[k]);
	u32 was;

	printf("w %c %lx %x\n", names[k], o, v);
	if (o >= 0x400)
		return;
	was = w[k][o / 4];
	if (k == 0) {
		/* The settings as written, the actual state following the target, bit 11 clear, the two flags kept. */
		w[k][o / 4] = (v & 0x1f00000f) | ((v & 0xf) << 4) | (was & 0x300);
		if (!v && (o == 0x180 || o == 0x188 || o == 0x218 || o == 0x220 || o == 0x228))
			w[k][o / 4] |= 0x0f000000;
		if (has('a') && o == 0x180 && !v)
			w[k][o / 4] |= 0x10000000;
		if (has('A') && o == 0x1a8 && !v)
			w[k][o / 4] |= 0x0f000000;
		if (has('e') && o == 0x180 && v)
			w[k][o / 4] &= ~0x10000000U;
		if (has('k') && o == 0x1a8 && !v)
			w[k][o / 4] |= 0x800;
		if (has('n') && o == 0x1e8 && !v)
			w[k][o / 4] |= 0xf0;
		if (o == 0x188 && v) {
			cpu_back = 1;
			cpu_back_at = clk;
			if (has('x'))
				w[k][o / 4] &= ~0xf0U;
		}
	} else if (k == 1 && o == 0x120) {
		if (v & 0xf) {
			gpio_up = !has('u');
			gpio_up_at = clk;
			w[k][o / 4] = v | (gpio_up ? (v & 0xf) << 4 : 0);
		} else {
			if (gpio_up)
				gpio_cycled = 1;
			gpio_zero = 1;
			gpio_zero_at = clk;
			w[k][o / 4] = 0x200 | (has('b') ? 0x800 : 0) | (has('U') ? 0xf0 : 0);
		}
	} else if (k == 3) {
		clk_written = 1;
		clk_at = clk;
		if (!has('Q'))
			w[k][o / 4] = v;
	} else if ((k == 4 && !(has('R') && o == 0x14)) || (k == 5 && !(has('r') && o == 0x24))) {
		w[k][o / 4] = v;
	}
}
static void m1_crumb(unsigned int addr, const u8 *b, unsigned int n)
{
	printf("k %x %u %x\n", addr, n, b[0]);
}
"""
SST_FN_MAIN = r"""
int main(int argc, char **argv)
{
	static const u32 a[] = { 2, 2, 2, 2, 3, 2, 2 }, b[] = { 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 2, 2, 1, 1 };
	unsigned int o, i;
	u8 r;

	(void)argc;
	sc = argv[1];
	/* As the recipe leaves them: SIO, SIO_CPU, SPI_P and UART_P on, UART2 at Linux's 0x244, the rest off; GPIO off; the 22 as B1 leaves them. */
	for (o = 0x180; o <= 0x2d0; o += 8)
		w[0][o / 4] = 0x200;
	w[0][0x180 / 4] = 0x1f0000ff;
	w[0][0x188 / 4] = 0x040002ff;
	w[0][0x1e8 / 4] = 0x000000ff;
	w[0][0x230 / 4] = 0x000001ff;
	w[0][0x250 / 4] = 0x00000244;
	/* SIO_SPMI0-2 read f in bits 27:24 whenever their auto-enable is clear, which it always is. */
	w[0][0x218 / 4] = w[0][0x220 / 4] = w[0][0x228 / 4] = 0x0f000200;
	w[1][0x120 / 4] = 0x200;
	w[1][0x2d8 / 4] = 0x1f0f03ff;
	w[3][0x1c / 4] = 0x80100100;
	for (i = 0; i < 7; i++)
		w[4][1 + i] = a[i];
	for (i = 0; i < 14; i++)
		w[5][1 + i] = b[i];
	r = m1_sio_steps();
	printf("ret %x\n", r);
	for (o = 0x180; o <= 0x2d0; o += 8)
		printf("end S %x %x\n", o, w[0][o / 4]);
	printf("end P 120 %x\nend P 2d8 %x\nend C 1c %x\n", w[1][0x120 / 4], w[1][0x2d8 / 4], w[3][0x1c / 4]);
	for (i = 0; i < 7; i++)
		printf("end X %x %x\n", 4 + 4 * i, w[4][1 + i]);
	for (i = 0; i < 14; i++)
		printf("end Y %x %x\n", 4 + 4 * i, w[5][1 + i]);
	return 0;
}
"""
SST_INIT_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include "sio_steps.h"
typedef uint64_t u64;
#define __iomem
#define __init
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define EINVAL 22
#define ENODEV 19
#define PAGE_SIZE 0x4000
#define M1_PMGR_PS 0x28e080000ULL
static unsigned char pg[6][0x4000];
static const char names[] = "SPACXY";
static void *m1_sio, *m1_ps, *m1_look_ga, *m1_look_pc, *m1_look_pa, *m1_look_pb;
static unsigned int nread, nmap;
static u64 never;
static const char *maps;
static u32 readl(const void *p)
{
	unsigned int k = (unsigned int)(((const unsigned char *)p - pg[0]) / 0x4000);

	printf("read %c %lx\n", names[k], (unsigned long)((const unsigned char *)p - pg[k]));
	return 0x1000 + nread++;
}
static void *ioremap_np(u64 a, unsigned long n)
{
	unsigned int k = a == 0x28e038000ULL ? 3 : a == 0x28e0a0000ULL ? 4 : a == 0x28e5b0000ULL ? 5 : 0;

	printf("map %llx %lx\n", (unsigned long long)a, n);
	nmap++;
	return k && maps[k] == 'm' ? pg[k] : NULL;
}
static int e1_never_write(u64 addr) { return addr == never; }
static void pr_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("err "); vprintf(fmt, ap); va_end(ap); }
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("info "); vprintf(fmt, ap); va_end(ap); }
"""
SST_INIT_MAIN = r"""
int main(int argc, char **argv)
{
	int ret;

	(void)argc;
	/* argv[1]: six letters, SIO's page, the power page and the gate page mapped (m) or not, then the three pages of the 22: mapped
	 * already (p, as look=1 leaves them), to be mapped here (m), or failing to map (n); argv[2]: an address on the never-write list. */
	maps = argv[1];
	m1_sio = maps[0] == 'm' ? pg[0] : NULL;
	m1_ps = maps[1] == 'm' ? pg[1] : NULL;
	m1_look_ga = maps[2] == 'm' ? pg[2] : NULL;
	m1_look_pc = maps[3] == 'p' ? pg[3] : NULL;
	m1_look_pa = maps[4] == 'p' ? pg[4] : NULL;
	m1_look_pb = maps[5] == 'p' ? pg[5] : NULL;
	sscanf(argv[2], "%llx", (unsigned long long *)&never);
	ret = m1_sst_init();
	printf("ret %d reads %u maps %u\n", ret, nread, nmap);
	return 0;
}
"""
# B2's order for its ops 9 to 15 and 16 to 29, written out a second time.
SST_A_ORDER = [0x04, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c]
SST_B_ORDER = [0x04, 0x0c, 0x10, 0x14, 0x08, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38]


def sio_steps_tests(g, src):
    """sio_steps=1: sio_steps.h against an independent reading of it; m1_sio_steps() compiled as it stands against a model of the
    registers that logs every access; m1_sst_init()'s refusals; the places in e1_hook.c that use them; and the byte, the run's
    stages and --clear through m1-crumbs."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    def going(res, n):
        return 0x80 | (res & 0x0d) | (2 if n & 1 else 0) | (0x10 if n & 2 else 0)

    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    fns = cut("static bool m1_siod_poll(", "\n}\n") + cut("static u8 m1_siod_back(", "\n}\n") + cut("static u8 m1_sio_steps(void)", "\n}\n")
    init = cut("static int __init m1_sst_init(void)", "\n}\n")
    scen = ("", "g", "h", "c", "u", "b", "k", "n", "Q", "q", "r", "R", "x", "z", "go", "ho", "gu", "gn", "hr", "hq", "gb", "hx", "ub", "kn", "hQ", "cg",
            "a", "A", "B", "U", "e", "D", "W", "gG", "hG", "hW", "gO", "hO", "gD", "gU", "gB", "ga")
    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return os.path.join(tmp, name)

        def out(exe, *args):
            return subprocess.run([exe, *args], capture_output=True, text=True, check=True).stdout.splitlines()

        hdr = [l.split() for l in out(build("hdr", SST_HDR))]
        fn = build("fn", SST_FN_PRELUDE + fns + SST_FN_MAIN)
        runs = {a: out(fn, a) for a in scen}
        ini = build("ini", SST_INIT_PRELUDE + init + SST_INIT_MAIN)
        loads = {a: out(ini, *a) for a in (("mmmmmm", "0"), ("mmmppp", "0"), ("mmmpmp", "0"), ("nmmmmm", "0"), ("mnmmmm", "0"), ("mmnmmm", "0"),
                                           ("mmmnmm", "0"), ("mmmmnm", "0"), ("mmmmmn", "0"), ("mmmmmm", "28e080120"), ("mmmmmm", "28e03801c"),
                                           ("mmmmmm", "28e0a0010"), ("mmmmmm", "28e5b0038"), ("mmmmmm", "28e0802d8"))}
        bad_lists = {}
        for what, name, lst in (("the fourteen in address order, not B2's", "SST_B_OFFS", "0x04, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38"),
                                ("one of the fourteen past the last", "SST_B_OFFS", "0x04, 0x0c, 0x10, 0x14, 0x08, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x3c"),
                                ("the fourteen one short, which ends in offset 0", "SST_B_OFFS", "0x04, 0x0c, 0x10, 0x14, 0x08, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34"),
                                ("one of the seven at offset 0", "SST_A_OFFS", "0x00, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c"),
                                ("the seven one short", "SST_A_OFFS", "0x04, 0x08, 0x0c, 0x10, 0x14, 0x18"),
                                ("two of the seven swapped", "SST_A_OFFS", "0x04, 0x08, 0x0c, 0x14, 0x10, 0x18, 0x1c")):
            redef = f"#undef {name}\n#define {name} {{ {lst} }}\n"
            bad_lists[what] = out(build("bad" + str(len(bad_lists)), SST_INIT_PRELUDE + redef + init + SST_INIT_MAIN), "mmmmmm", "0")

    # The header against a second writing-out of it.
    st = {int(l[1], 16): int(l[2], 16) for l in hdr if l[0] == "st"}
    fin = {int(l[1], 16): (int(l[2], 16), int(l[3])) for l in hdr if l[0] == "f"}
    go = {(int(l[1], 16), int(l[2], 16)): int(l[3], 16) for l in hdr if l[0] == "g"}
    wd = {int(l[1], 16): (int(l[2]), int(l[3])) for l in hdr if l[0] == "w"}
    check(hdr[0] == ["consts", "120", "8000000f", "80101100", "40000000", "7", "14", "1", "6016", "7", "14", "3"] + [f"{o:x}" for o in SST_A_ORDER + SST_B_ORDER]
          and hdr[1] == ["bits", "1", "2", "4", "8", "10", "20", "40", "80", "3f", "d", "2", "10", "20"] and hdr[2] == ["stages", "15", "17", "16"]
          and hdr[3] == ["cb", "a0"],
          "sio_steps.h: GPIO's word at 0x120 and B2's 0x8000000f for it, 0x80101100 for 0x28e03801c with bit 30 aside, the seven and the"
          " fourteen in B2's order, each written 1, the byte at 0x6016 with its bits in both layouts, 0xa0 for a gate closed before, and"
          " stages 0x15, 0x17 and 0x16", str(hdr[:4]))
    check(all(st[s] == {0x4b: 0x15, 0x55: 0x17, 0x54: 0x16}.get(s, s) for s in range(256)),
          "sst_stage(): 0x15, 0x17 and 0x16 for gates_mark=1's 0x4b, 0x55 and 0x54, and every other stage as it is")
    check(len(fin) == 256 and len(go) == 2048 and all(fin[r] == (0xc0 | (r & 0x3f), int(r & 5 == 4)) for r in range(256))
          and all(go[(r, n)] == going(r, n) for r in range(256) for n in range(8))
          and not any(v & 0x60 for v in go.values()) and len({go[(r, n)] for r in (0, 1, 4, 5, 8, 9, 12, 13) for n in range(4)}) == 32,
          "sst_final() keeps its six bits and sets bits 6 and 7; sst_second() is true only for a subtree that went off with the gate open;"
          " sst_going() keeps the gate, the subtree and GPIO, puts the count of groups in bits 1 and 4, and never sets bit 5 or 6: 32"
          " bytes, each its own")
    check(all(wd[v][0] == int(not v & 0x8ff) for v in wd) and all(wd[v][1] == int(v == 0x80101100) for v in wd) and len(wd) == 22
          and wd[0x80101100] == (1, 1) and wd[0xc0101100] == (1, 0) and wd[0x80100100] == (1, 0) and wd[0x200] == (1, 0) and wd[0x800] == (0, 0) and wd[0xf0] == (0, 0),
          "sst_idle(): target 0, actual state 0 and bit 11 clear, the was-gated flags aside; sst_clk_ok(): B2's value with bit 30, pending, clear")
    check((g["SST_PRE"], g["SST_WD2"], g["SST_STAGES"]) == (0x15, (0x16, 0x17), (0x15, 0x16, 0x17))
          and tuple(g[n] for n in ("SST_G1", "SST_G2", "SST_SUB", "SST_GPIO_OK", "SST_PRE_OK", "SST_BACK", "SST_FINAL", "SST_MARK", "SST_GO_AT_LO",
                                   "SST_GO_AT_HI", "SST_GO_CLOSED")) == (1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80, 2, 0x10, 0x20)
          and not set(g["SST_STAGES"]) & (set(g["GATES_STAGES"]) | set(g["SIOD_STAGES"]) | set(g["SIOD1_STAGES"]) | set(g["PCD_STAGES"])
                                          | set(g["POSITION"]) | set(g["ACK_EARLY"]) | {0x0e, 0x0f}),
          "m1-crumbs has the byte's fields and the stages as the header has them, and the stages are no other run's")

    # m1_sio_steps() in every scenario: the accesses, in order.
    found = {o: 0x200 for o in SIOD_ORDER}
    found.update({0x180: 0x1f0000ff, 0x188: 0x040002ff, 0x1e8: 0xff, 0x230: 0x1ff, 0x250: 0x244, 0x218: 0x0f000200, 0x220: 0x0f000200, 0x228: 0x0f000200})
    rets = {}
    for a in scen:
        ev = [l.split() for l in runs[a]]
        acc = [(i, e[0], e[1], int(e[2], 16), int(e[3], 16)) for i, e in enumerate(ev) if e[0] in "rw"]
        ws = [(i, p, o, v) for i, t, p, o, v in acc if t == "w"]
        ks = [(i, int(e[1], 16), int(e[2]), int(e[3], 16)) for i, e in enumerate(ev) if e[0] == "k"]
        ret = rets[a] = int([e for e in ev if e[0] == "ret"][0][1], 16)
        end = {(e[1], int(e[2], 16)): int(e[3], 16) for e in ev if e[0] == "end"}
        first = [(t, p, o) for i, t, p, o, v in acc if not ws or i < ws[0][0]]
        if "c" in a:
            ok = (not ws and [k[1:] for k in ks] == [(0x6016, 1, 0xa0)] and ret == 0xa0 and first == [("r", "S", o) for o in SIOD_ORDER] + [("r", "A", 0x50)]
                  and all(end[("S", o)] == found[o] for o in SIOD_ORDER) and end[("P", 0x120)] == 0x200 and end[("C", 0x1c)] == 0x80100100)
            check(ok, f"m1_sio_steps(), scenario '{a}': the gate closed before the first write: the 43 and the gate read, nothing written, 0xa0 sent"
                  " once and returned", "\n".join(runs[a][-12:]))
            continue
        sub = not any(c in a for c in "knaA")
        g1 = "g" in a and not any(c in a for c in "nuU")
        res = (4 if sub else 0) | (0 if any(c in a for c in "ubBU") else 8) | (1 if g1 else 0)
        second = sub and not g1
        pre = second and not any(c in a for c in "QrRq")
        g2 = second and "h" in a and not any(c in a for c in "QrR")  # the model's gate doesn't ask for bit 30
        res2 = res | (0x10 if pre else 0) | (2 if g2 else 0)
        back = "x" not in a and "e" not in a and not ("o" in a and (g1 or g2))
        want_k = ([going(res, 0)] + ([going(res, n) for n in (1, 2, 3)] if second else []) + [0xc0 | res2, 0xc0 | res2 | (0x20 if back else 0)])
        want_w = ([("P", 0x120, 0x8000000f)] + [("S", o, 0) for o in SIOD_ORDER[:38]] + [("P", 0x120, 0)] + [("S", o, 0) for o in SIOD_ORDER[38:]]
                  + ([("C", 0x1c, 0x80101100)] + [("X", o, 1) for o in SST_A_ORDER] + [("Y", o, 1) for o in SST_B_ORDER] if second else [])
                  + [("S", o, found[o] & ~0xb00) for o in reversed(SIOD_ORDER)])
        wi = [w[0] for w in ws]
        n0 = 45 + (22 if second else 0)  # the writes before the write-back
        between = lambda lo, hi: [(t, p, o) for i, t, p, o, v in acc if lo < i < hi]
        ok = ([w[1:] for w in ws] == want_w and [k[1:] for k in ks] == [(0x6016, 1, b) for b in want_k] and ret == want_k[-1]
              and first == [("r", "S", o) for o in SIOD_ORDER] + [("r", "A", 0x50)]
              # GPIO's raise: only its own word is read before the first 0; its 0 follows the 38th with nothing between.
              and all(x == ("r", "P", 0x120) for x in between(wi[0], wi[1])) and len(between(wi[0], wi[1])) >= 1 and wi[39] == wi[38] + 1
              # No page is touched but at the words meant: GPIO's alone in the power page, the two gates read-only.
              and all(o == 0x120 for i, t, p, o, v in acc if p == "P") and all(t == "r" and o in (0x50, 0x64) for i, t, p, o, v in acc if p == "A")
              and all(o in SIOD_ORDER for i, t, p, o, v in acc if p == "S") and all(o == 0x1c for i, t, p, o, v in acc if p == "C")
              and all(o in SST_A_ORDER for i, t, p, o, v in acc if p == "X") and all(o in SST_B_ORDER for i, t, p, o, v in acc if p == "Y")
              # The bytes' places: the first after the last 0 and the gates' polls, one after each group, the last two around the write-back.
              and wi[44] < ks[0][0] < wi[45] and ks[-2][0] < wi[n0] and wi[-1] < ks[-1][0]
              and (not second or (wi[45] < ks[1][0] < wi[46] and wi[52] < ks[2][0] < wi[53] and wi[66] < ks[3][0] < ks[4][0] < wi[67]
                                  and [x for x in between(ks[3][0], ks[4][0]) if x[0] == "r"] and all(x == ("r", "A", 0x50) for x in between(ks[3][0], ks[4][0]))))
              and (second or not any(p in "CXY" for i, t, p, o, v in acc))
              # 0x28e5a0064 is polled before 0x28e5a0050, as in B2; GPIO's bit 11 after the 38's polls, where B2 has it.
              and [x for x in between(wi[44], ks[0][0]) if x[1] == "A"][0] == ("r", "A", 0x64)
              and (lambda seg: seg[0] == ("r", "S", SIOD_ORDER[0]) and [j for j, x in enumerate(seg) if x[1] == "P"]
                   and max(j for j, x in enumerate(seg) if x[1] == "S") < min(j for j, x in enumerate(seg) if x[1] == "P"))(between(wi[39], wi[40]))
              # UART_N is written and never polled, as in B2: read as found and after the polls, and not between.
              and len([1 for i, t, p, o, v in acc if p == "S" and o == 0x238 and t == "r" and i < ks[0][0]]) == 2
              and end[("P", 0x2d8)] == 0x1f0f03ff and not end[("P", 0x120)] & 0x8000000f
              and all(not (end[("S", o)] ^ found[o]) & 0x1f0000ff for o in SIOD_ORDER if not ("x" in a and o == 0x188) and not ("e" in a and o == 0x180) and not ("A" in a and o == 0x1a8))
              and (not pre or (end[("C", 0x1c)] == 0x80101100 and all(end[("X", o)] == 1 for o in SST_A_ORDER) and all(end[("Y", o)] == 1 for o in SST_B_ORDER)))
              and (second or (end[("C", 0x1c)] == 0x80100100 and [end[("X", o)] for o in SST_A_ORDER] == [2, 2, 2, 2, 3, 2, 2])))
        check(ok, f"m1_sio_steps(), scenario '{a}': the 43 and the gate read as found; GPIO raised, B2's 0 to the first 38, GPIO's 0 right after,"
              " the polls, the rest in B2's groups, the gates polled and the byte; the 22 in B2's order with a byte after each group only if the"
              " subtree went off and the gate stayed open; the byte's second layout; the 43 back in reverse as found and the byte again; no"
              " other word of any page touched, ps_pmp's untouched", "\n".join(runs[a][-40:]) + f"\nwant {[hex(b) for b in want_k]}")
    check(rets == dict(rets, **{"": 0xfc, "g": 0xed, "h": 0xfe, "c": 0xa0, "cg": 0xa0, "u": 0xf4, "b": 0xf4, "k": 0xe8, "n": 0xe8, "Q": 0xec, "q": 0xec,
                                "r": 0xec, "R": 0xec, "x": 0xdc, "z": 0xfc, "go": 0xcd, "ho": 0xde, "gu": 0xf4, "gn": 0xe8, "hr": 0xec, "hq": 0xee,
                                "gb": 0xe5, "hx": 0xde, "ub": 0xf4, "kn": 0xe8, "hQ": 0xec, "a": 0xe8, "A": 0xe8, "B": 0xf4, "U": 0xf4, "e": 0xdc,
                                "D": 0xfc, "W": 0xfc, "gG": 0xed, "hG": 0xfe, "hW": 0xfe, "gO": 0xed, "hO": 0xfe, "gD": 0xed, "gU": 0xf4, "gB": 0xe5,
                                "ga": 0xe9}),
          "m1_sio_steps(): 0xfc with everything made and the gate open throughout; 0xed if it closes with GPIO's cycle and 0xfe once the 22"
          " are in; a GPIO that doesn't come up or keeps bit 11, a word of the subtree that stays up or busy, a word of the 22 that doesn't"
          " take, a word that doesn't come back and a gate that stays closed each clear their own bit; a word back after 50 ms counts as"
          " back; 0x28e03801c counts only with bit 30 clear, within 100 ms; an auto-enable left, or f where no switch-off left it, isn't"
          " a subtree that's off; an auto-enable that doesn't come back isn't back; GPIO's two checks each count alone; a register that"
          " takes 30 or 50 ms is waited for", str({a: hex(v) for a, v in rets.items()}))

    # The source: what the function writes, and nothing else like it.
    body = cut("static u8 m1_sio_steps(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("
    check(re.findall(wr + r"[^;]*;", body) == ["writel(SST_GPIO_UP, m1_ps + SST_GPIO);", "writel(0, m1_sio + off[i]);", "writel(0, m1_ps + SST_GPIO);",
                                               "writel(SST_CLK_VAL, m1_look_pc + LOOK_PRE_CLK_OFF);", "writel(SST_PRE_VAL, m1_look_pa + pa[i]);",
                                               "writel(SST_PRE_VAL, m1_look_pb + pb[i]);"]
          and not re.search(wr, init) and len(re.findall(r"\bm1_sio_steps\(", code_only)) == 2 and "m1_look_gb" not in body
          and body.count("m1_siod_back(m1_sio + off[i], was[i], m1_tps / 10)") == 1 and "\tif (sst_second(res)) {\n" in body
          and all(len(re.findall(r"\b" + x + r"\b", re.sub(r"/\*.*?\*/", "", body, flags=re.S))) == n for x, n in (("m1_look_pc", 2), ("m1_look_pa", 2), ("m1_look_pb", 2))),
          "m1_sio_steps() writes in six places: GPIO's raise, the 43's 0, GPIO's 0, 0x28e03801c, the seven, the fourteen, the last three"
          " under sst_second(); the write-back is sio_down=1's function with 100 ms; the load writes nothing")
    hdr_src = open(os.path.join(os.path.dirname(HOOK), "sio_steps.h")).read()
    check(all(t in src for t in ("M1_PMGR_PS + SST_GPIO == 0x28e080120ULL", "SST_GPIO != M1_PS_PMP_RO", "SST_GPIO_UP == 0x8000000fU", "SST_CLK_VAL == 0x80101100U",
                                 "LOOK_PRE_CLK + LOOK_PRE_CLK_OFF == 0x28e03801cULL", "ARRAY_SIZE(((u8[])SST_A_OFFS)) == SST_A_N && SST_A_N == 7",
                                 "ARRAY_SIZE(((u8[])SST_B_OFFS)) == SST_B_N && SST_B_N == 14", "LOOK_PRE_A == 0x28e0a0000ULL && LOOK_PRE_B == 0x28e5b0000ULL"))
          and "\tstatic const u8 wa[SST_A_N] = { 0x04, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c };\n" in init
          and "\tstatic const u8 wb[SST_B_N] = { 0x04, 0x0c, 0x10, 0x14, 0x08, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38 };\n" in init
          and "#define SST_GPIO\t\t0x120\n" in hdr_src and "#define SST_GPIO_UP\t\t0x8000000fU\n" in hdr_src,
          "the 24 new addresses and the two values are pinned at build time, GPIO's offset against ps_pmp's, the lists' lengths too, and the"
          " load compares the two lists with a second writing-out of them")
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    check(in_order_of(after, ["\t\tm1_crumb(0x6004, b, 4);\n", "\t\t\tm1_crumb(0x6012, b, 2);\n", "\tif (gates_mark && !sio_down && !pcie_down && !sio_steps && !a2_pre) {\n",
                              "\tif (pcie_down)\n\t\tm1_gates = m1_pcie_down();\n", "\tif (sio_steps)\n\t\tm1_gates = m1_sio_steps();\n",
                              "\t/* recfg_bite=1", "/* 9 */", "\tlocal_daif_mask();", "writel_relaxed(1, commit);", "m1_alarm_arm(rtc);"])
          and after.count("m1_sio_steps(") == 1 and len(re.findall(r"\bsst_stage\(", code_only)) == 2,
          "step 8: after the stage and the outcomes, in place of gates_mark=1's reads and before step 9's mask and the commit; its byte is"
          " what the alarm's last command sends; the stage is mapped in two places", after[-4500:])
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool sio_steps;\nmodule_param(sio_steps, bool, 0444);\n" in src and not re.search(r"\bsio_steps\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (sio_steps && (!gates_mark || sio_down || pcie_down || probe)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n", "\treturn 0;\n\nbad_wait:"])
          and 'sio_steps ? " sio_steps=1" : ""' in src
          and in_order_of(src, ["\tif (gates_mark) {\n\t\tint ret = m1_gates_init();",
                                "\tif (sio_steps) {\n\t\tint ret = m1_siod_init();\n\n\t\tif (!ret)\n\t\t\tret = m1_sst_init();\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n"]),
          "sio_steps=1 loads only with gates_mark=1 and never with sio_down=1, pcie_down=1 or probe=1; it's off by default, read-only, named in"
          " the load line, and the load runs sio_down=1's check of the 43 and then m1_sst_init(), after the gate pages are mapped, and returns"
          " a refusal", params[-1500:])

    # m1_sst_init(): the pages of the 22 mapped unless they are already, the 24 words and the gate read once each and logged; refused,
    # with nothing read, if a page isn't there, an address is on the never-write list, or a list isn't B2's.
    ok = loads[("mmmmmm", "0")]
    reads = sorted(r for r in ok if r.startswith("read "))
    check(ok[-1] == "ret 0 reads 24 maps 3" and [r for r in ok if r.startswith("map ")] == ["map 28e038000 4000", "map 28e0a0000 4000", "map 28e5b0000 4000"]
          and reads == sorted(["read P 120", "read C 1c", "read A 50"] + [f"read X {o:x}" for o in SST_A_ORDER] + [f"read Y {o:x}" for o in SST_B_ORDER])
          and len([r for r in ok if r.startswith("info m1: sio_steps: awake: GPIO 0x28e080120 ")]) == 1 and not any(r.startswith("err") for r in ok)
          and loads[("mmmppp", "0")][-1] == "ret 0 reads 24 maps 0" and loads[("mmmpmp", "0")][-1] == "ret 0 reads 24 maps 1",
          "m1_sst_init(): the three pages mapped here unless look=1 mapped them, and GPIO's word, the 22 and the gate read once each and logged",
          "\n".join(ok))
    check(all(loads[(m, "0")][-1].startswith("ret -19 reads 0") for m in ("nmmmmm", "mnmmmm", "mmnmmm", "mmmnmm", "mmmmnm", "mmmmmn"))
          and all(loads[("mmmmmm", n)][-1] == "ret -22 reads 0 maps 0" for n in ("28e080120", "28e03801c", "28e0a0010", "28e5b0038"))
          and loads[("mmmmmm", "28e0802d8")][-1] == "ret 0 reads 24 maps 3"
          and all(v[-1] == "ret -22 reads 0 maps 0" and any(r.startswith("err m1: sio_steps:") for r in v) for v in bad_lists.values()),
          "m1_sst_init(): refuses, reading nothing, if a page isn't mapped or doesn't map, if GPIO's word, 0x28e03801c or one of the 21 is"
          " on the never-write list, or if a list is out of B2's order, has a word off its place, or is one short", str(loads) + str(bad_lists))

    # m1-crumbs. The run's record at its last stage, all as meant, with its byte at 0x6016.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        alarm = (0x41, 121544 + 3600, 0, 0)
        rec = dict(parked=0x7d, left=0, down=0xf5, secs=121544, prog=(0xff, 0xff), flags=0x90, isrs=0x8d)

        def dec(stage, gb, x=217, **kw):
            fake(path, scratch(stage, **dict(rec, drained=gb, **kw)), rtc_secs=121544 + x + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            return r.returncode, r.stdout, r.stderr

        rc, out_, err = dec(0x97, 0xfc, x=90)
        check(rc == 0 and "stage 0x6004 = 0x97 (pmp_out=1: bit 7, so the stage is 0x17) (pmgr_late=1): sio_steps=1: gates_mark=1's 0x55:" in out_
              and "0x6016 = 0xfc: sio_steps=1's byte, in place of the alarm's result:" in out_
              and "  the 43 words of SIO's subtree after the polls: all read target 0 and actual state 0, B2's 42 polls for bit 11 clear were all met, and the auto fields read as B2's 0 leaves them: the subtree was off\n" in out_
              and "its bit-11 poll after the 0 was met, and it read off and idle after the gates' polls: the cycle was made\n" in out_
              and "  0x28e5a0050 after that, with up to 100 ms for 0x28e5a0064 and 100 ms more for it: bit 30 clear, OPEN\n" in out_
              and "written after that: all read back as written\n" in out_ and "  0x28e5a0050 after them, with up to 100 ms more: bit 30 clear, OPEN\n" in out_
              and "  written back: all 43 read as found in target, actual state and auto-enable within 100 ms each, and 0x28e5a0050 read open again\n" in out_
              and "  so: 0x28e5a0050 stayed OPEN with SIO's subtree off, GPIO cycled as B2 cycles it and the 22 words as B2 writes them: neither is what the gate follows" in out_
              and "the restart's time is read as --m1-gates' is" in out_ and "nothing records the commit" not in out_
              and "alarm command" not in out_ and "gates_mark=1's byte" not in out_ and "sio_down=1's byte" not in out_ and "pcie_down=1's byte" not in out_
              and "0x6007 = 0xf5: fabric_up=1's byte, in place of the cores that held:\n" in out_ and "WD1 stood as the marker" in out_
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out_
              and "  wd1_mark=1's marker: about 90 s is what WD1 gives with its count never zeroed: B2 didn't reach its op 224" in out_
              and "0x140c is the commit's second + 3600, as the run wrote it" in out_,
              "sio_steps=1's 0x97: its byte read line by line in the alarm's result's place; the rest read as --m1-gates' 0xd5", out_ + err)
        rc5, out5, err5 = dec(0xd5, 0xfc)
        rcc, outc, errc = dec(0x97, 0xfc)
        skip = ("0x6000:", "0x6010:", "stage 0x6004", "0x6016", "  0x28e", "  AFNC2", "  so: ", "  the 43", "  GPIO", "  the 22", "  written back", "  the commit found",
                "  a poll that", "  the other four")
        missing = [l for l in out5.splitlines() if not l.startswith(skip) and l not in outc.splitlines()]
        check(rc5 == 0 and rcc == 0 and "gates_mark=1's byte" in out5 and "sio_steps=1's byte" not in out5 and not missing,
              "0xfc at 0x6016: the gate byte under 0xd5, sio_steps=1's under 0x97, and nothing else read differently", "\n".join(missing))
        for gb, want, what in ((0xed, "  so: 0x28e5a0050 CLOSED with GPIO's cycle made inside the switch-off, where it stayed open twice without it", "closed with GPIO's cycle"),
                               (0xfe, "  so: 0x28e5a0050 CLOSED ONCE THE 22 WORDS WERE WRITTEN, and not before: the gate follows them, or some of them", "closed once the 22 were in"),
                               (0xe5, "  so: 0x28e5a0050 CLOSED after the switch-off, where it stayed open twice (sio_down=1), but GPIO's cycle isn't shown made as meant", "closed without GPIO's cycle shown"),
                               (0xe8, "  so: INCONCLUSIVE on the gate: SIO's subtree wasn't off, or a poll B2 waits for wasn't met", "the subtree not off"),
                               (0xec, "  so: 0x28e5a0050 stayed OPEN, but the 22 didn't all read back as written", "open with the 22 not as written")):
            rc, out_, err = dec(0x97, gb, x=90)
            check(rc == 0 and want in out_ and out_.count("  so: ") == 1 + out5.count("  so: ") - 1 and "Not read" not in out_.split("0x6016 = ")[1].split("\n0x")[0],
                  f"sio_steps=1's 0x{gb:02x}: {what}, said once", out_ + err)
        for gb, want in ((0xec, "GPIO at B2's 0 and THE 22 NOT ALL AS B2 WRITES THEM"), (0xf4, "GPIO NOT SHOWN OFF AND IDLE and the 22 as B2 writes them")):
            rc, out_, err = dec(0x97, gb, x=90)
            check(rc == 0 and f"  the commit found SIO's subtree as the recipe leaves it, {want}: the restart's time doesn't compare with --m1-gates' cleanly\n" in out_
                  and "the restart's time is read as --m1-gates' is" not in out_,
                  f"sio_steps=1's 0x{gb:02x}: the 43 back doesn't make the commit's state B2's own: what bits 3 and 4 don't show isn't claimed (Pullfrog and"
                  " the adversarial review of #95)", out_ + err)
        rc, out_, err = dec(0x96, 0xdc)
        check(rc == 0 and "so the stage is 0x16" in out_ and "sio_steps=1: gates_mark=1's 0x54:" in out_ and "pending afterwards" in out_
              and "  written back: NOT CONFIRMED" in out_ and "the restart's time doesn't compare with --m1-gates'" in out_
              and "  wd1_mark=1's marker: not read. CPU0 had an exception pending" in out_,
              "sio_steps=1's 0x96: the pending stage, a write-back that isn't confirmed, and no reading of the time", out_ + err)
        rc, out_, err = dec(0x95, 0x8e, x=90, parked=0xfe)
        check(rc == 0 and "so the stage is 0x15" in out_ and "8 (sio_steps=1): gates_mark=1's 0x4b, with SIO's subtree switched off around GPIO's cycle" in out_
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out_
              and "  the 22 words: 1 of their three groups written and read back when the record stops; next was the seven words 0x28e0a0004 to 0x28e0a001c (B2's ops 9 to 15)\n" in out_
              and "  so: THE RUN STOPPED AT the seven words 0x28e0a0004 to 0x28e0a001c (B2's ops 9 to 15): in its writes, its read-back or the byte after it." in out_
              and "Nothing shows the 43 written back" in out_ and "  stage 0x15, so nothing records the commit after this byte" in out_
              and "A byte whose command failed stops the record, not the run: the pass, the write-back and the commit went on unrecorded" in out_
              and "So a restart at about 90 s fits both, and this record doesn't tell them apart" in out_ and "both end at WD1's marker" not in out_
              and "  wd1_mark=1's marker: not read. Stage 0x15 is the last before the commit" in out_,
              "sio_steps=1's 0x95 with the byte's first layout and one group in: the stage before the commit, the group it stopped at, no reading"
              " of the time", out_ + err)
        for x in (10, 90):
            rc, out_, err = dec(0x95, 0, x=x, parked=0xfe)
            check(rc == 0 and "0x6016 = 0x00: SIO_STEPS' BYTE DIDN'T LAND (bit 7 clear), though stage 0x15 and the steps' outcomes did" in out_
                  and "bit 30" not in out_ and "  so: " not in out_.split("0x6016 = ")[1].split("\n0x")[0]
                  and f"the SoC restarted about {x} s after the commit" in out_,
                  f"sio_steps=1's 0x95 with 0x6016 = 0, a restart at {x} s: said to have stopped in the switch-off or GPIO's cycle", out_ + err)
        for stage, gb, kw, want in ((0x97, 0x00, {}, "0x6016 = 0x00: bit 7 is clear, so sio_steps=1's byte didn't survive the reset"),
                                    (0x96, 0x7f, {}, "0x6016 = 0x7f: bit 7 is clear"),
                                    (0x97, 0xfc, dict(prog=(0xff, 0x00)), "0x6016 = 0xfc: not read. sio_steps=1's byte goes out after the steps' outcomes"),
                                    (0x17, 0xfc, {}, "0x6016 = 0xfc: not read. A sio_steps=1 run's stages always carry pmp_out=1's bit 7"),
                                    (0x97, 0x8c, {}, "0x6016 = 0x8c: bit 6 is clear, the byte's first layout, and this isn't one sio_steps=1 sends in it at stage 0x17"),
                                    (0x97, 0xc3, {}, "0x6016 = 0xc3: bit 6 is set, the byte's second layout, and this isn't one sio_steps=1 sends in it")):
            rc, out_, err = dec(stage, gb, **kw)
            check(rc == 0 and want in out_ and "bit 30" not in out_ and "SIO_STEPS' BYTE" not in out_ and "alarm command" not in out_
                  and "the 43 words of SIO's subtree after the polls" not in out_ and "so: 0x28e5a0050" not in out_,
                  f"stage 0x{stage:02x}, 0x6016 = 0x{gb:02x}{', no outcomes marker' if kw.get('prog') else ''}: sio_steps=1's byte isn't read", out_ + err)
        rc, out_, err = dec(0x97, 0xfc, prog=(0xff, 0x00))
        check(rc == 0 and "The steps' outcomes go out right after stage 0x15's command" in out_,
              "sio_steps=1 without the outcomes' marker: fabric_up=1's byte is said to follow stage 0x15's command, the run's own", out_ + err)
        # Every byte, at each of the run's stages, read independently of the decoder.
        wrong = []
        for s in (0x15, 0x16, 0x17):
            for v in range(256):
                for stop in (0xc0, 0x40):
                    b = bytearray(0x20)
                    b[0x12], b[0x13], b[0x16] = stop, 0xff, v
                    lines = g["sst_lines"](s, bytes(b), True)
                    text = "\n".join(lines)
                    so = [l for l in lines if l.startswith("  so: ")]
                    sub, g1, g2, gp, pr, bk = bool(v & 4), bool(v & 1), bool(v & 2), bool(v & 8), bool(v & 0x10), bool(v & 0x20)
                    second = sub and not g1
                    if not v & 0x80:
                        ok = len(lines) == 1 and not so and ("DIDN'T LAND" in text) == (s == 0x15) and ("Not read" in text) == (s != 0x15)
                    elif v == 0xa0:
                        ok = (len(so) == 1 and "NOTHING WAS WRITTEN" in so[0] and "ALREADY CLOSED" in text and len(lines) == (4 if s == 0x15 else 3)
                              and ("nothing records the commit" in text) == (s == 0x15) and "The commit found" not in text)
                    elif not v & 0x40:
                        n = (1 if v & 2 else 0) | (2 if v & 0x10 else 0)
                        if v & 0x20 or s != 0x15 or (n and not second):
                            ok = len(lines) == 1 and "Not read" in text and "first layout" in text
                        else:
                            ok = (len(so) == 1 and ("THE RECORD STOPS BEFORE THE BYTE'S SECOND LAYOUT" in so[0]) == (not second)
                                  and ("THE RUN STOPPED" in so[0]) == second and (f"the 22 words: {n} of their three groups" in text) == second
                                  and ("AT 0x28e03801c" in so[0]) == (second and n == 0) and ("AT the seven" in so[0]) == (second and n == 1)
                                  and ("AT the fourteen" in so[0]) == (second and n == 2) and ("IN THE GATE'S SECOND POLL" in so[0]) == (second and n == 3)
                                  and ("the subtree was off" in lines[1]) == sub and ("the cycle was made" in lines[2]) == gp
                                  and ("SET, closed" in lines[3]) == g1 and "Nothing shows the 43 written back" in so[0]
                                  and "nothing records the commit" in text and "the commit found" not in text and "CLOSED" not in so[0] and "OPEN" not in so[0])
                    elif (g1 and g2) or (not second and (g2 or pr)):
                        ok = len(lines) == 1 and "Not read" in text and "second layout" in text
                    else:
                        live = stop == 0xc0
                        ok = (len(so) == 1 and "sio_steps=1's byte" in lines[0]
                              and ("the subtree was off" in lines[1]) == sub and ("NOT all off" in lines[1]) == (not sub)
                              and ("the cycle was made" in lines[2]) == gp and ("THE CYCLE ISN'T SHOWN MADE" in lines[2]) == (not gp)
                              and ("SET, closed" in lines[3]) == g1 and ("clear, OPEN" in lines[3]) == (not g1)
                              and (": all read back as written" in lines[4]) == (second and pr) and ("NOT all read back as written" in lines[4]) == (second and not pr)
                              and ("the 22 words: not written" in lines[4]) == (not second)
                              and (not second or (("SET, closed" in lines[5]) == g2 and ("clear, OPEN" in lines[5]) == (not g2)))
                              and ("written back: all 43 read as found" in text) == bk and ("written back: NOT CONFIRMED" in text) == (not bk)
                              and ("SIO's firmware isn't shown stopped" in so[0]) == (not live)
                              and ("INCONCLUSIVE on the gate: SIO's subtree wasn't off" in so[0]) == (live and not sub)
                              and ("CLOSED with GPIO's cycle made inside the switch-off" in so[0]) == (live and sub and g1 and gp)
                              and ("but GPIO's cycle isn't shown made as meant" in so[0]) == (live and sub and g1 and not gp)
                              and ("CLOSED ONCE THE 22 WORDS WERE WRITTEN" in so[0]) == (live and second and g2)
                              and ("the gate follows them, or some of them" in so[0]) == (live and second and g2 and pr)
                              and ("that the cycle doesn't close it isn't shown" in so[0]) == (live and second and g2 and not gp)
                              and ("neither is what the gate follows" in so[0]) == (live and second and not g2 and gp and pr)
                              and ("stayed OPEN, but" in so[0]) == (live and second and not g2 and not (gp and pr))
                              and ("GPIO's cycle isn't shown made" in so[0] and "stayed OPEN, but" in so[0]) == (live and second and not g2 and not gp)
                              and ("the 22 didn't all read back as written" in so[0]) == (live and second and not g2 and not pr)
                              and ("a2_pre=1 is next" in so[0]) == (live and sub and ((g1 and gp) or g2))
                              and ("doesn't compare with --m1-gates'" in text) == (not bk or not gp or (second and not pr))
                              and ("the restart's time is read as --m1-gates' is" in text) == (bk and gp and (not second or pr))
                              and ("the commit found SIO's subtree as the recipe leaves it" in text) == bk
                              and ("and the 22 as B2 writes them" in text) == (bk and second and pr)
                              and ("THE 22 NOT ALL AS B2 WRITES THEM" in text) == (bk and second and not pr)
                              and ("GPIO NOT SHOWN OFF AND IDLE" in text) == (bk and not gp) and ("GPIO at B2's 0" in text) == (bk and gp)
                              and ("or it closes by itself later than the first poll waits" in so[0]) == (live and second and g2)
                              and ("nothing records the commit" in text) == (s == 0x15))
                    if not ok:
                        wrong.append((hex(s), hex(v), hex(stop)))
        check(not wrong, "every byte at sio_steps=1's three stages, with SIO's firmware stopped and not: each line follows its own bit, one verdict"
              " or none, the verdict the one its bits allow, and a byte no pass sends isn't read", str(wrong[:8]))
        for stage in (0x95, 0x96, 0x97):
            before = fake(path, scratch(stage, **dict(rec, drained=0xfc)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt="4800000000 24000000")
            after_ = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after_ == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after sio_steps=1's 0x{stage:02x}: the run's alarm disarmed by its compare value, and the crumbs zeroed", r.stdout + r.stderr)
        lost = dict(rec, secs=0, drained=0xfc)
        before = fake(path, scratch(0x97, **lost), rtc_secs=121761, alarm=alarm)
        r = run(path, "--clear", cnt="4800000000 24000000")
        check(r.returncode == 2 and open(path, "rb").read() == before and "disarmed" not in r.stdout
              and "after gates_mark=1's, sio_down=1's, pcie_down=1's, sio_steps=1's and a2_pre=1's stages it never does: it's that run's byte" in r.stderr,
              "--clear after 0x97 with the second lost and 0xfc at 0x6016: not taken for an acked alarm, so nothing is written", r.stdout + r.stderr)


PCD_HDR = r"""
#include <stdio.h>
#include "pcie_down.h"
int main(void)
{
	static const u16 off[] = PCD_OFFS;
	unsigned int i, p, s;
	u32 found, now[PCD_SYS_N];

	printf("consts %d %d %d %x %x %x %x", PCD_N, PCD_SYS_N, PCD_GP_SYS, PCD_BUSY, PCD_GATE, PCD_PS_STATE, GATES_AT);
	for (i = 0; i < sizeof(off) / sizeof(off[0]); i++)
		printf(" %x", off[i]);
	printf("\nbits %x %x %x %x %x %x %x %x %x\n", PCD_FOUND_UP, PCD_FOUND_BUSY, PCD_POLL, PCD_DOWN, PCD_ST_OK, PCD_PARENTS, PCD_CLOSED, PCD_MARK,
	       PCD_SECOND);
	printf("stages %x %x %x\n", M1_PCD_STAGE_PRE, M1_PCD_STAGE, M1_PCD_STAGE_PEND);
	for (s = 0; s < 256; s++)
		printf("st %x %x\n", s, pcd_stage((u8)s));
	while (scanf("%x %x %x %x %x %x", &found, &now[0], &now[1], &now[2], &p, &s) == 6)
		printf("b %x\n", pcd_byte(found, now, (u8)p, (u8)s));
	return 0;
}
"""
# m1_pcie_down() and the poll it calls, as they stand, against a model of the first power page and the second gate page that logs
# every read, write and PMU byte. The scenario is argv[1], letters: b APCIE_GP_SYS keeps bit 11 after its 0; l it keeps it for
# 30 ms; L for 130 ms, past its poll; u its actual state stays f; d its actual state takes 50 ms to drop; f it is found with bit 11 set; g it is found gated,
# as macOS has it; s APCIE_ST_SYS keeps bit 11; t APCIE_ST1_SYS's actual state stays up; p APCIE_GP's actual state stays f; c the
# gate 0x28e09c34c closes once APCIE_GP_SYS and APCIE_GP read off.
PCD_FN_PRELUDE = r"""
#include <stdio.h>
#include <string.h>
#include "pcie_down.h"
typedef uint64_t u64;
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define static_assert(...) _Static_assert(__VA_ARGS__, "")
#define BIT(n) (1U << (n))
#define dsb(x) do { } while (0)
#define isb() do { } while (0)
#define cpu_relax() do { } while (0)
static unsigned char ps_page[0x4000], gb_page[0x4000];
static void *m1_ps = ps_page, *m1_look_gb = gb_page;
static const char *sc;
static u64 clk, m1_tps = 24000000, gp_sys_at;
static int gp_sys_written;
static u32 word[0x400 / 4];
/* Every look at the clock is 1 ms on. */
static u64 m1_now(void) { return clk += m1_tps / 1000; }
static int has(char c) { return strchr(sc, c) != NULL; }
static u32 ps_read(unsigned long o)
{
	u32 v = word[o / 4];

	if (o == 0x320 && gp_sys_written) {
		if (has('l') && clk < gp_sys_at + 30 * m1_tps / 1000)
			v |= 0x800;
		if (has('L') && clk < gp_sys_at + 130 * m1_tps / 1000)
			v |= 0x800;
		if (has('d') && clk < gp_sys_at + 50 * m1_tps / 1000)
			v |= 0xf0;
	}
	return v;
}
static u32 readl(const void *p)
{
	const unsigned char *c = p;
	unsigned long o;
	u32 v;

	if (c >= ps_page && c < ps_page + sizeof(ps_page)) {
		o = (unsigned long)(c - ps_page);
		v = o < 0x400 ? ps_read(o) : 0xdead0000;
		printf("r P %lx %x\n", o, v);
		return v;
	}
	o = (unsigned long)(c - gb_page);
	v = 0x8004030e;
	if (o == 0x34c && has('c') && !(ps_read(0x320) & 0xff) && !(ps_read(0x150) & 0xff))
		v |= GATE_CLOSED;
	printf("r B %lx %x\n", o, v);
	return v;
}
static void writel(u32 v, void *p)
{
	unsigned long o = (unsigned long)((unsigned char *)p - ps_page);
	u32 was;

	printf("w P %lx %x\n", o, v);
	if (o >= 0x400)
		return;
	was = word[o / 4];
	/* The settings as written, the actual state following the target, bit 11 clear, the two flags kept. */
	word[o / 4] = (v & 0x1f00000f) | ((v & 0xf) << 4) | (was & 0x300);
	if (o == 0x320) {
		gp_sys_at = clk;
		gp_sys_written = 1;
		if (has('b'))
			word[o / 4] |= 0x800;
		if (has('u'))
			word[o / 4] |= 0xf0;
	}
	if (has('s') && o == 0x2e8)
		word[o / 4] |= 0x800;
	if (has('t') && o == 0x2f0)
		word[o / 4] |= 0x40;
	if (has('p') && o == 0x150)
		word[o / 4] |= 0xf0;
}
static void m1_crumb(unsigned int addr, const u8 *b, unsigned int n)
{
	printf("k %x %u %x\n", addr, n, b[0]);
}
"""
PCD_FN_MAIN = r"""
int main(int argc, char **argv)
{
	static const u16 off[] = PCD_OFFS;
	unsigned int i;
	u8 b;

	(void)argc;
	sc = argv[1];
	/* As Linux's suspend and ps_words=1 leave them: the two storage words and APCIE_ST off, APCIE_GP_SYS and APCIE_GP on. */
	word[0x2e8 / 4] = 0x0f000300;
	word[0x2f0 / 4] = 0x0f000300;
	word[0x320 / 4] = has('g') ? 0x1000030f : has('f') ? 0x1f0008ff : 0x1f0000ff;
	word[0x148 / 4] = 0x00000200;
	word[0x150 / 4] = 0x000000ff;
	word[0x2d8 / 4] = 0x1f0f03ff;
	b = m1_pcie_down();
	printf("ret %x\n", b);
	for (i = 0; i < ARRAY_SIZE(off); i++)
		printf("end %x %x\n", off[i], word[off[i] / 4]);
	printf("end 2d8 %x\n", word[0x2d8 / 4]);
	return 0;
}
"""
PCD_INIT_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include "pcie_down.h"
typedef uint64_t u64;
#define __iomem
#define __init
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define EINVAL 22
#define ENODEV 19
#define PAGE_SIZE 0x4000
#define M1_PMGR_PS 0x28e080000ULL
#define M1_PS_PMP_RO 0x2d8
static unsigned char page[0x4000];
static void *m1_ps, *m1_look_gb;
static unsigned int nread;
static u64 never;
static u32 readl(const void *p) { printf("read %lx\n", (unsigned long)((const unsigned char *)p - page)); return 0x1000 + nread++; }
static int e1_never_write(u64 addr) { return addr == never; }
static void pr_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("err "); vprintf(fmt, ap); va_end(ap); }
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("info "); vprintf(fmt, ap); va_end(ap); }
"""
PCD_INIT_MAIN = r"""
int main(int argc, char **argv)
{
	int ret;

	(void)argc;
	/* argv[1]: two letters, the power page and the gate page mapped (m) or not; argv[2]: an address on the never-write list. */
	m1_ps = argv[1][0] == 'm' ? page : NULL;
	m1_look_gb = argv[1][1] == 'm' ? page : NULL;
	sscanf(argv[2], "%llx", (unsigned long long *)&never);
	ret = m1_pcd_init();
	printf("ret %d reads %u\n", ret, nread);
	return 0;
}
"""
# B2's order, written out a second time: APCIE_ST_SYS, APCIE_ST1_SYS, APCIE_GP_SYS; APCIE_ST, APCIE_GP.
PCD_ORDER = [0x2e8, 0x2f0, 0x320, 0x148, 0x150]


def pcie_down_tests(g, src):
    """pcie_down=1: pcie_down.h against an independent reading of it; m1_pcie_down() compiled as it stands against a model of the
    registers that logs every access; m1_pcd_init()'s refusals; the places in e1_hook.c that use them; and the byte, the run's
    stages and --clear through m1-crumbs."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    def byte(found, now, polls, second):
        down = [not w & 0x8ff for w in now]
        return (0x80 | (1 if found & 0xf0 else 0) | (2 if found & 0x800 else 0) | (4 if polls & 4 else 0) | (8 if down[2] else 0)
                | (0x10 if polls & 3 == 3 and down[0] and down[1] else 0) | (second & 0x60))

    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    fns = cut("static bool m1_siod_poll(", "\n}\n") + cut("static u8 m1_pcie_down(void)", "\n}\n")
    init = cut("static int __init m1_pcd_init(void)", "\n}\n")
    scen = ("", "c", "b", "l", "u", "d", "f", "g", "s", "t", "p", "cb", "cl", "cu", "cd", "cf", "cg", "cs", "cp", "bu", "bsp", "gc", "fb", "lcd", "st", "L", "cL", "Lp")
    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return os.path.join(tmp, name)

        def out(exe, *args):
            return subprocess.run([exe, *args], capture_output=True, text=True, check=True).stdout.splitlines()

        rnd = random.Random(20261112)
        words = (0, 0x300, 0x0f000300, 0x800, 0xb00, 0x10, 0xf0, 0x1, 0xf, 0x1f0000ff, 0x1000030f, 0x1f0008ff, 0x10000000, 0x0f000000, 0xffffff00)
        cases = [(f, [a, b, c], p, s) for f in (0x1f0000ff, 0x1000030f, 0x1f0008ff, 0x800, 0) for a in (0, 0x10) for b in (0, 0x1) for c in (0, 0xf0, 0x800)
                 for p in range(8) for s in (0, 0x20, 0x40, 0x60, 0xff)]
        cases += [(rnd.choice(words), [rnd.choice(words) for _ in range(3)], rnd.randrange(256), rnd.randrange(256)) for _ in range(400)]
        feed = "".join(f"{f:x} {n[0]:x} {n[1]:x} {n[2]:x} {p:x} {s:x}\n" for f, n, p, s in cases)
        hdr = [l.split() for l in subprocess.run([build("hdr", PCD_HDR)], input=feed, capture_output=True, text=True, check=True).stdout.splitlines()]
        fn = build("fn", PCD_FN_PRELUDE + fns + PCD_FN_MAIN)
        runs = {a: out(fn, a) for a in scen}
        ini = build("ini", PCD_INIT_PRELUDE + init + PCD_INIT_MAIN)
        loads = {a: out(ini, *a) for a in (("mm", "0"), ("nm", "0"), ("mn", "0"), ("mm", "28e0802d8"), ("mm", "28e080320"), ("mm", "28e080148"),
                                           ("mm", "28e0802e8"))}
        bad_lists = {}
        for what, tail in (("ps_pmp's word", "0x148, 0x2d8"), ("a word off its place", "0x148, 0x154"), ("a word past the page", "0x148, 0x4150"),
                           ("another power word of the page", "0x148, 0x2e0"), ("the last two the other way round", "0x150, 0x148"),
                           ("a list one short, which ends in offset 0", "0x148")):
            redef = "#undef PCD_OFFS\n#define PCD_OFFS { 0x2e8, 0x2f0, 0x320, " + tail + " }\n"
            bad_lists[what] = out(build("bad" + str(len(bad_lists)), PCD_INIT_PRELUDE + redef + init + PCD_INIT_MAIN), "mm", "0")

    # The header against a second writing-out of it.
    consts, bits, stages = hdr[0], hdr[1], hdr[2]
    st = {int(l[1], 16): int(l[2], 16) for l in hdr if l[0] == "st"}
    got = [int(l[1], 16) for l in hdr if l[0] == "b"]
    check(consts == ["consts", "5", "3", "2", "800", "34c", "ff", "6016"] + [f"{o:x}" for o in PCD_ORDER]
          and bits == ["bits", "1", "2", "4", "8", "10", "20", "40", "80", "60"] and stages == ["stages", "1d", "1f", "1e"],
          "pcie_down.h: the five words in B2's order, the first three polled for bit 11, APCIE_GP_SYS the third, the gate at 0x34c, the"
          " byte at 0x6016 with its eight bits, and stages 0x1d, 0x1f and 0x1e", str(hdr[:3]))
    check(all(st[s] == {0x4b: 0x1d, 0x55: 0x1f, 0x54: 0x1e}.get(s, s) for s in range(256)),
          "pcd_stage(): 0x1d, 0x1f and 0x1e for gates_mark=1's 0x4b, 0x55 and 0x54, and every other stage as it is")
    wrong = [(c, hex(b)) for c, b in zip(cases, got) if b != byte(*c)]
    check(len(got) == len(cases) and not wrong,
          f"pcd_byte() in {len(cases)} cases: APCIE_GP_SYS as found (its actual state, its bit 11), its poll, its last read, the two storage"
          " words' polls and reads together, the second send's two bits and no other from its argument, and bit 7 always", str(wrong[:3]))
    check((g["PCD_PRE"], g["PCD_WD2"], g["PCD_STAGES"]) == (0x1d, (0x1e, 0x1f), (0x1d, 0x1e, 0x1f))
          and tuple(g[n] for n in ("PCD_FOUND_UP", "PCD_FOUND_BUSY", "PCD_POLL", "PCD_DOWN", "PCD_ST_OK", "PCD_PARENTS", "PCD_CLOSED", "PCD_MARK"))
          == (1, 2, 4, 8, 0x10, 0x20, 0x40, 0x80)
          and not set(g["PCD_STAGES"]) & (set(g["GATES_STAGES"]) | set(g["SIOD_STAGES"]) | set(g["SIOD1_STAGES"]) | set(g["POSITION"])
                                          | set(g["ACK_EARLY"]) | {0x0e, 0x0f}),
          "m1-crumbs has the byte's fields and the stages as the header has them, and the stages are no other run's")

    # m1_pcie_down() in every scenario: the accesses, in order.
    for a in scen:
        ev = [l.split() for l in runs[a]]
        acc = [(e[0], e[1], int(e[2], 16), int(e[3], 16)) for e in ev if e[0] in "rw"]
        ws = [(i, e[2], e[3]) for i, e in enumerate(acc) if e[0] == "w"]
        ks = [(i, int(e[1], 16), int(e[2]), int(e[3], 16)) for i, e in enumerate(ev) if e[0] == "k"]
        wi = [i for i, e in enumerate(ev) if e[0] == "w"]
        ret = int([e for e in ev if e[0] == "ret"][0][1], 16)
        end = {int(e[1], 16): int(e[2], 16) for e in ev if e[0] == "end"}
        found = 0x1000030f if "g" in a else 0x1f0008ff if "f" in a else 0x1f0000ff
        gp_down = "u" not in a
        polls = (0 if "s" in a else 1) | 2 | (0 if "b" in a or "L" in a else 4)
        now1 = [0x800 if "s" in a else 0, 0x40 if "t" in a else 0, (0x800 if "b" in a or "L" in a else 0) | (0xf0 if "u" in a or "d" in a else 0)]
        closed = "c" in a and gp_down and "p" not in a
        # L's bit 11 clears 130 ms after the 0: past its own poll, and before the last read only if the gate's poll ran its 100 ms.
        now2 = now1[:2] + [(0x800 if "b" in a or ("L" in a and closed) else 0) | (0 if gp_down else 0xf0)]
        second = (0 if "p" in a else 0x20) | (0x40 if closed else 0)
        want1, want2 = byte(found, now1, polls, 0), byte(found, now2, polls, second)
        gate = [e for e in acc if e[1] == "B"]
        first_reads = [e for e in acc[:acc.index(("w", "P", 0x2e8, 0))]]
        ok = (first_reads == [("r", "P", 0x320, found)]
              and [(o, v) for _, o, v in ws] == [(o, 0) for o in PCD_ORDER]
              and [(k[1], k[2], k[3]) for k in ks] == [(0x6016, 1, want1), (0x6016, 1, want2)] and ret == want2
              and wi[2] < ks[0][0] < wi[3] and wi[4] < ks[1][0]
              and all(e[2] in PCD_ORDER for e in acc if e[1] == "P") and all(e[2] == 0x34c and e[0] == "r" for e in gate)
              and 1 <= len(gate) <= 102 and (len(gate) < 60) == closed and bool(gate[-1][3] & 0x40000000) == closed
              and not any(e[3] & 0x40000000 for e in gate[:-1])
              and all(i > wi[4] for i, e in enumerate(ev) if e[0] == "r" and e[1] == "B")
              and len([e for e in acc if e[1] == "P" and e[0] == "r"]) <= 1 + 3 * 102 + 3 + 2 * 102 + 1
              and end[0x2d8] == 0x1f0f03ff and all(not end[o] & 0x1f00000f for o in PCD_ORDER))
        check(ok, f"m1_pcie_down(), scenario '{a}': APCIE_GP_SYS read as found; 0 to the three _SYS words in B2's order, each polled for bit 11,"
              " and the byte; 0 to APCIE_ST and APCIE_GP, the gate polled, the byte again with its last two bits; five writes, each a 0,"
              " nothing written back, ps_pmp's word untouched", "\n".join(runs[a][:12]) + f"\nwant {want1:x} {want2:x}")
    check(int(runs[""][-7].split()[1], 16) == 0xbd and int(runs["c"][-7].split()[1], 16) == 0xfd and int(runs["b"][-7].split()[1], 16) == 0xb1 and int(runs["bu"][-7].split()[1], 16) == 0xb1
          and int(runs["L"][-7].split()[1], 16) == 0xb9 and [l for l in runs["L"] if l.startswith("k ")][0].split()[3] == "91"
          and int(runs["g"][-7].split()[1], 16) == 0xbc and int(runs["f"][-7].split()[1], 16) == 0xbf and int(runs["u"][-7].split()[1], 16) == 0xb5
          and int(runs["d"][-7].split()[1], 16) == 0xbd and [l for l in runs["d"] if l.startswith("k ")][0].split()[3] == "95",
          "m1_pcie_down(): 0xbd if the 0 takes with the gate open and 0xfd closed; 0xb1 with bit 11 latched; 0xb9 if it clears after the poll and before the last read; 0xb5 with the word up alone;"
          " a word that drops late is up in the first send and off in the second", str({a: runs[a][-7] for a in ("", "c", "b", "bu", "L", "g", "f", "u", "d")}))

    # The source: what the function writes, and nothing else like it.
    body = cut("static u8 m1_pcie_down(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("
    check(re.findall(wr + r"[^;]*;", body) == ["writel(0, m1_ps + off[i]);", "writel(0, m1_ps + off[i]);"] and not re.search(wr, init)
          and len(re.findall(r"\bm1_pcie_down\(", code_only)) == 2 and not re.search(r"\bm1_sio\b", body) and "m1_look_ga" not in body
          and body.count("for (i = 0; i < PCD_SYS_N; i++)\n\t\twritel(0, m1_ps + off[i]);") == 1
          and body.count("for (i = PCD_SYS_N; i < PCD_N; i++)\n\t\twritel(0, m1_ps + off[i]);") == 1,
          "m1_pcie_down() writes 0 to the listed words in two places, the three and then the two, and nothing else; the load writes nothing")
    check(all(t in src for t in ("M1_PMGR_PS + PCD_ST_SYS == 0x28e0802e8ULL", "M1_PMGR_PS + PCD_ST1_SYS == 0x28e0802f0ULL",
                                 "M1_PMGR_PS + PCD_GP_SYS_OFF == 0x28e080320ULL", "M1_PMGR_PS + PCD_ST == 0x28e080148ULL",
                                 "M1_PMGR_PS + PCD_GP == 0x28e080150ULL", "PCD_GP_SYS_OFF != M1_PS_PMP_RO", "PCD_ST_SYS != M1_PS_PMP_RO",
                                 "PCD_ST1_SYS != M1_PS_PMP_RO", "PCD_ST != M1_PS_PMP_RO", "PCD_GP != M1_PS_PMP_RO",
                                 "LOOK_GATE_B + PCD_GATE == 0x28e09c34cULL"))
          and "#define PCD_OFFS\t\t{ PCD_ST_SYS, PCD_ST1_SYS, PCD_GP_SYS_OFF, PCD_ST, PCD_GP }\n" in open(os.path.join(os.path.dirname(HOOK), "pcie_down.h")).read(),
          "the five addresses and the gate's are pinned at build time, each word's offset against ps_pmp's, and the list is made of those five names")
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    check(in_order_of(after, ["\t\tm1_crumb(0x6004, b, 4);\n", "\t\t\tm1_crumb(0x6012, b, 2);\n", "\tif (gates_mark && !sio_down && !pcie_down && !sio_steps && !a2_pre) {\n",
                              "\tif (sio_down)\n\t\tm1_gates = m1_sio_down();\n", "\tif (pcie_down)\n\t\tm1_gates = m1_pcie_down();\n",
                              "\t/* recfg_bite=1", "/* 9 */", "\tlocal_daif_mask();", "writel_relaxed(1, commit);", "m1_alarm_arm(rtc);"])
          and after.count("m1_pcie_down(") == 1 and len(re.findall(r"\bpcd_stage\(", code_only)) == 2,
          "step 8: after the stage and the outcomes, in place of gates_mark=1's reads and before step 9's mask and the commit; its byte is"
          " what the alarm's last command sends; the stage is mapped in two places", after[-4500:])
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool pcie_down;\nmodule_param(pcie_down, bool, 0444);\n" in src and not re.search(r"\bpcie_down\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (pcie_down && (!gates_mark || sio_down || probe)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n", "\treturn 0;\n\nbad_wait:"])
          and 'pcie_down ? " pcie_down=1" : ""' in src
          and in_order_of(src, ["\tif (gates_mark) {\n\t\tint ret = m1_gates_init();", "\tif (pcie_down) {\n\t\tint ret = m1_pcd_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n"]),
          "pcie_down=1 loads only with gates_mark=1 and never with sio_down=1 or probe=1; it's off by default, read-only, named in the load"
          " line, and the load runs m1_pcd_init() after the gate pages are mapped and returns its refusal", params[-1500:])

    # m1_pcd_init(): the five and the gate read once each and logged; refused, with nothing read, if a page isn't mapped, a word is on
    # the never-write list, or the list has ps_pmp's word, one off its place or one past the page.
    ok = loads[("mm", "0")]
    check(ok[-1] == "ret 0 reads 6" and [r for r in ok if r.startswith("read ")] == [f"read {o:x}" for o in PCD_ORDER + [0x34c]]
          and len([r for r in ok if r.startswith("info m1: pcie_down: awake: APCIE_ST_SYS 00001000 APCIE_ST1_SYS 00001001 APCIE_GP_SYS 00001002"
                                                 " APCIE_ST 00001003 APCIE_GP 00001004; gate 0x28e09c34c 00001005")]) == 1
          and not any(r.startswith("err") for r in ok),
          "m1_pcd_init(): the five words and the gate read once each, in B2's order, and logged", "\n".join(ok))
    check(all(loads[(m, "0")][-1] == "ret -19 reads 0" for m in ("nm", "mn"))
          and all(loads[("mm", n)][-1] == "ret -22 reads 0" for n in ("28e080320", "28e080148", "28e0802e8"))
          and loads[("mm", "28e0802d8")][-1] == "ret 0 reads 6"
          and all(v[-1] == "ret -22 reads 0" and any(r.startswith("err m1: pcie_down:") for r in v) for v in bad_lists.values()),
          "m1_pcd_init(): refuses, reading nothing, if a page isn't mapped, one of the five is on the never-write list, or the list has"
          " ps_pmp's word, a word off its place, one past the page, another power word, two swapped, or is one short", str(loads) + str(bad_lists))
    check("static_assert(ARRAY_SIZE(((u16[])PCD_OFFS)) == PCD_N && PCD_N == 5" in src
          and "\tstatic const u16 want[PCD_N] = { 0x2e8, 0x2f0, 0x320, 0x148, 0x150 };\n" in init,
          "the list's length is pinned at build time, and the load compares it with the five offsets written out a second time")

    # m1-crumbs. The run's record at its last stage, all as meant, with its byte at 0x6016.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        alarm = (0x41, 121544 + 3600, 0, 0)
        rec = dict(parked=0x7d, left=0, down=0xf5, secs=121544, prog=(0xff, 0xff), flags=0x90, isrs=0x8d)

        def dec(stage, gb, x=217, **kw):
            fake(path, scratch(stage, **dict(rec, drained=gb, **kw)), rtc_secs=121544 + x + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            return r.returncode, r.stdout, r.stderr

        rc, out_, err = dec(0x9f, 0xbd, x=90)
        check(rc == 0 and "stage 0x6004 = 0x9f (pmp_out=1: bit 7, so the stage is 0x1f) (pmgr_late=1): pcie_down=1: gates_mark=1's 0x55:" in out_
              and "0x6016 = 0xbd: pcie_down=1's byte, in place of the alarm's result:" in out_
              and "  APCIE_GP_SYS (0x28e080320) as found, before any 0: actual state not 0: up, as both looks had it; bit 11 clear\n" in out_
              and "  APCIE_GP_SYS after its 0 (B2's op 143), polled as B2's op 146 polls it: bit 11 read clear within 100 ms\n" in out_
              and "  APCIE_GP_SYS at this send's last read: target 0, actual state 0, bit 11 clear: off and idle\n" in out_
              and "  APCIE_ST_SYS and APCIE_ST1_SYS (0x28e0802e8, 0x28e0802f0; B2's ops 141, 142, 144, 145): both polls met, both off and idle\n" in out_
              and "  APCIE_ST and APCIE_GP (0x28e080148, 0x28e080150; B2's ops 147, 148) within 100 ms of their 0: both read target 0 and actual state 0\n" in out_
              and "  0x28e09c34c (B2's op 156) within 100 ms after that: bit 30 clear, open" in out_
              and "  so: APCIE_GP_SYS went off cleanly with B2's own 0" in out_ and "nothing records the commit" not in out_
              and "alarm command" not in out_ and "gates_mark=1's byte" not in out_ and "sio_down=1's byte" not in out_
              and "0x6007 = 0xf5: fabric_up=1's byte, in place of the cores that held:\n" in out_ and "WD1 stood as the marker" in out_
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out_
              and "0x6013 = 0xff: ps_words=1 wrote the 20 power words" in out_
              and "  wd1_mark=1's marker: about 90 s is what WD1 gives with its count never zeroed: B2 didn't reach its op 224" in out_
              and "0x140c is the commit's second + 3600, as the run wrote it" in out_,
              "pcie_down=1's 0x9f: its byte read line by line in the alarm's result's place; the rest read as --m1-gates' 0xd5", out_ + err)
        rc5, out5, err5 = dec(0xd5, 0xbd)
        rcc, outc, errc = dec(0x9f, 0xbd)
        skip = ("0x6000:", "0x6010:", "stage 0x6004", "0x6016", "  0x28e", "  AFNC2", "  so: ", "  APCIE", "  the other four")
        missing = [l for l in out5.splitlines() if not l.startswith(skip) and l not in outc.splitlines()]
        check(rc5 == 0 and rcc == 0 and "gates_mark=1's byte" in out5 and "pcie_down=1's byte" not in out5 and not missing,
              "0xbd at 0x6016: the gate byte under 0xd5, pcie_down=1's under 0x9f, and nothing else read differently", "\n".join(missing))
        rc, out_, err = dec(0x9e, 0xfd)
        check(rc == 0 and "so the stage is 0x1e" in out_ and "pcie_down=1: gates_mark=1's 0x54:" in out_ and "pending afterwards" in out_
              and "bit 30 SET, closed" in out_ and "  wd1_mark=1's marker: not read. CPU0 had an exception pending" in out_,
              "pcie_down=1's 0x9e: the pending stage, its byte, and no reading of the time", out_ + err)
        rc, out_, err = dec(0x9f, 0xb1, x=90)
        check(rc == 0 and "bit 11 STAYED SET for 100 ms" in out_ and "last read: NOT OFF AND IDLE" in out_
              and "  so: A LEAD ON B2's OP 146: APCIE_GP_SYS DIDN'T READ OFF AND IDLE after the 0, with the PCIe ports live behind it, at the last read before the commit; its bit-11 poll wasn't met either." in out_
              and "It isn't proof that B2 waits there for good" in out_ and "If the restart comes at 90 s, the ports' stop before the 0" in out_
              and "about 120 s (WD2's bite) in place of 90 s (WD1's marker) means B2 got past its op 224" in out_ and "went off cleanly" not in out_,
              "pcie_down=1's 0xb1: bit 11 still set at the last read before the commit, said as a lead on op 146 and not as proof, with the"
              " restart's time still to be read and the ports' stop as the next build at 90 s", out_ + err)
        rc, out_, err = dec(0x9f, 0xb9, x=120)
        check(rc == 0 and "bit 11 STAYED SET for 100 ms" in out_ and "last read: target 0, actual state 0, bit 11 clear: off and idle" in out_
              and "  so: SLOW, NOT STUCK: bit 11 of APCIE_GP_SYS stayed set past the AP's 100 ms, and the word read off and idle at the last read before the commit." in out_
              and "A LEAD ON" not in out_ and "went off cleanly" not in out_ and "means B2 got past its op 224" in out_,
              "pcie_down=1's 0xb9: a poll unmet and the word off and idle before the commit, said as slow and not as stuck", out_ + err)
        rc, out_, err = dec(0x9d, 0x9d, x=90, parked=0xfe)
        check(rc == 0 and "so the stage is 0x1d" in out_ and "8 (pcie_down=1): gates_mark=1's 0x4b, with the PCIe power words switched off" in out_
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out_
              and "  APCIE_ST, APCIE_GP and the gate 0x28e09c34c: not in this byte." in out_
              and "  stage 0x1d, so nothing records the commit after this byte (0x1e or 0x1f comes after it)" in out_
              and "  wd1_mark=1's marker: not read. Stage 0x1d is the last before the commit" in out_,
              "pcie_down=1's 0x9d with the byte's first send: the stage before the commit, the last two bits not read, no reading of the time",
              out_ + err)
        for x in (10, 90):
            rc, out_, err = dec(0x9d, 0, x=x, parked=0xfe)
            check(rc == 0 and "0x6016 = 0x00: PCIE_DOWN'S BYTE DIDN'T LAND (bit 7 clear), though stage 0x1d and the steps' outcomes did" in out_
                  and "bit 30" not in out_ and "APCIE_GP_SYS (0x28e080320) as found" not in out_ and "so: APCIE" not in out_ and "so: THE 0" not in out_
                  and f"the SoC restarted about {x} s after the commit" in out_,
                  f"pcie_down=1's 0x9d with 0x6016 = 0, a restart at {x} s: said to have stopped in the switch-off", out_ + err)
        for stage, gb, kw, want in ((0x9f, 0x00, {}, "0x6016 = 0x00: bit 7 is clear, so pcie_down=1's byte didn't survive the reset"),
                                    (0x9e, 0x7f, {}, "0x6016 = 0x7f: bit 7 is clear"),
                                    (0x9f, 0xbd, dict(prog=(0xff, 0x00)), "0x6016 = 0xbd: not read. pcie_down=1's byte goes out after the steps' outcomes"),
                                    (0x1f, 0xbd, {}, "0x6016 = 0xbd: not read. A pcie_down=1 run's stages always carry pmp_out=1's bit 7")):
            rc, out_, err = dec(stage, gb, **kw)
            check(rc == 0 and want in out_ and "bit 30" not in out_ and "PCIE_DOWN'S BYTE" not in out_ and "alarm command" not in out_
                  and "APCIE_GP_SYS (0x28e080320) as found" not in out_ and "so: APCIE" not in out_ and "so: THE 0" not in out_,
                  f"stage 0x{stage:02x}, 0x6016 = 0x{gb:02x}{', no outcomes marker' if kw.get('prog') else ''}: pcie_down=1's byte isn't read", out_ + err)
        rc, out_, err = dec(0x9f, 0xbd, prog=(0xff, 0x00))
        check(rc == 0 and "The steps' outcomes go out right after stage 0x1d's command" in out_,
              "pcie_down=1 without the outcomes' marker: fabric_up=1's byte is said to follow stage 0x1d's command, the run's own", out_ + err)
        # Every byte, at each of the run's stages, read independently of the decoder.
        wrong = []
        for s in (0x1d, 0x1e, 0x1f):
            for v in range(256):
                b = bytearray(0x20)
                b[0x12], b[0x13], b[0x16] = 0xc0, 0xff, v
                lines = g["pcd_lines"](s, bytes(b), True)
                text = "\n".join(lines)
                so = [l for l in lines if l.startswith("  so: ")]
                if not v & 0x80:
                    ok = len(lines) == 1 and not so and ("DIDN'T LAND" in text) == (s == 0x1d) and ("Not read" in text) == (s != 0x1d)
                else:
                    first = s == 0x1d and not v & 0x60
                    ok = (len(so) == 1 and "pcie_down=1's byte" in lines[0]
                          and ("not 0: up" in lines[1]) == bool(v & 1) and ("ALREADY DOWN OR GATED" in lines[1]) == (not v & 1)
                          and ("BIT 11 ALREADY SET" in lines[1]) == bool(v & 2) and ("bit 11 clear" in lines[1]) == (not v & 2)
                          and ("read clear within 100 ms" in lines[2]) == bool(v & 4) and ("STAYED SET" in lines[2]) == (not v & 4)
                          and ("bit 11 clear: off and idle" in lines[3]) == bool(v & 8) and ("NOT OFF AND IDLE" in lines[3]) == (not v & 8)
                          and ("both polls met, both off and idle" in lines[4]) == bool(v & 0x10) and ("A POLL UNMET" in lines[4]) == (not v & 0x10)
                          and ("not in this byte" in text) == first
                          and (first or (("both read target 0 and actual state 0" in lines[5]) == bool(v & 0x20)
                                         and ("SET, closed" in lines[6]) == bool(v & 0x40) and ("clear, open" in lines[6]) == (not v & 0x40)))
                          and ("BEFORE ANYTHING HERE WROTE IT" in so[0]) == bool(v & 2)
                          and ("A LEAD ON B2's OP 146" in so[0] and "It isn't proof that B2 waits there for good" in so[0]) == (not v & 2 and not v & 8)
                          and ("with the PCIe ports live behind it" in so[0]) == (not v & 2 and not v & 8 and bool(v & 1))
                          and ("found down or gated already" in so[0]) == (not v & 2 and not v & 8 and not v & 1)
                          and ("its bit-11 poll wasn't met either" in so[0]) == (not v & 2 and not v & 8 and not v & 4)
                          and ("SLOW, NOT STUCK" in so[0]) == (v & 0xe == 0x8)
                          and ("INCONCLUSIVE: APCIE_GP_SYS was down or gated before the 0" in so[0]) == (v & 0xf == 0xc)
                          and ("one of the two storage words didn't" in so[0]) == (v & 0x1f == 0xd)
                          and ("went off cleanly with B2's own 0" in so[0]) == (v & 0x1f == 0x1d)
                          and ("B2 was bound" not in text and "says nothing new" not in text and "the commit found" not in text)
                          and ("about 120 s" in so[0] and "B2 got past" in so[0]) == (s == 0x1f and (bool(v & 2) or not v & 8 or not v & 4 or v & 0x1f == 0x1d))
                          and ("restart's time isn't read" in so[0]) == (s == 0x1e and (bool(v & 2) or not v & 8 or not v & 4 or v & 0x1f == 0x1d))
                          and ("at the last read before the commit" in so[0]) == (not v & 2 and (not v & 8 or not v & 4) and not first)
                          and ("at the first send's read" in so[0]) == (not v & 2 and (not v & 8 or not v & 4) and first)
                          and ("all five read off before the commit" in so[0]) == (v & 0x3f == 0x3d and not first)
                          and ("not all five" in so[0]) == (v & 0x3f == 0x1d and not first)
                          and ("nothing records the commit" in text) == (s == 0x1d)
                          and "SIO's firmware isn't shown stopped" not in text)
                if not ok:
                    wrong.append((hex(s), hex(v), lines))
            b = bytearray(0x20)
            b[0x12], b[0x13], b[0x16] = 0x40, 0xff, 0xbd
            if sum("SIO's firmware isn't shown stopped" in l for l in g["pcd_lines"](s, bytes(b), True)) != 1:
                wrong.append((hex(s), "SIO's firmware not stopped: not said"))
            b[0x12] = 0xc0
            if "not read" not in g["pcd_lines"](s, bytes(b), False)[0] or len(g["pcd_lines"](s, bytes(b), False)) != 1:
                wrong.append((hex(s), "no pmp_out=1 bit"))
        check(not wrong, "pcie_down=1: for every byte at each of its stages: each line follows its bit; one verdict, which is the latch if"
              " bit 11 was set as found, a lead and never proof if the word wasn't off and idle at the send's last read, slow if only the"
              " poll was unmet, inconclusive if the word was found down, and a clean switch-off only with none of those and the storage"
              " words off; it says all five read off only if the second send says so, speaks of the restart's time only at the clean"
              " last stage, and never of what the commit found; the first send's last"
              " two bits aren't read; nothing is read from a byte without bit 7", str(wrong[:2]))
        mr = g["mark_reading"]
        wrong = [(hex(v), x) for v in range(256) for x in (60, 88, 90, 105, 120, 123, 135, 146, 150, 163, 300)
                 if mr(x, 0x1d, v) != mr(x, 0x4b, v).replace("Stage 0x4b", "Stage 0x1d") or mr(x, 0x1e, v) != mr(x, 0x54, v)
                 or mr(x, 0x1f, v) != mr(x, 0x55, v)]
        check(not wrong and "Stage 0x1d is the last before the commit" in mr(90, 0x1d, 0x7d),
              "the restart's time is read against the marker at 0x1d, 0x1e and 0x1f exactly as at 0x4b, 0x54 and 0x55", str(wrong[:3]))
        # --clear: the run's alarm disarmed by its compare value; with the second lost, 0x6016 is never taken for the alarm's result.
        for stage, kw in ((0x9f, {}), (0x9e, {}), (0x9d, dict(parked=0xfe))):
            before = fake(path, scratch(stage, **dict(rec, drained=0xbd, **kw)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt="4800000000 24000000")
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and open(path, "rb").read() == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after pcie_down=1's 0x{stage:02x}: the run's alarm disarmed by its compare value, and the crumbs zeroed", r.stdout + r.stderr)
        before = fake(path, scratch(0x9f, **dict(rec, secs=0, drained=0xbd)), rtc_secs=121761, alarm=alarm)
        r = run(path, "--clear", cnt="4800000000 24000000")
        check(r.returncode == 2 and open(path, "rb").read() == before and "disarmed" not in r.stdout
              and "after gates_mark=1's, sio_down=1's, pcie_down=1's, sio_steps=1's and a2_pre=1's stages it never does: it's that run's byte" in r.stderr,
              "--clear after 0x9f with the second lost and 0xbd at 0x6016: not taken for an acked alarm, so nothing is written", r.stdout + r.stderr)


A2P_HDR = r"""
#include <stdio.h>
#include "a2_pre.h"
int main(void)
{
	static const u16 off[] = A2P_OFFS;
	static const u32 val[] = A2P_VALS, mask[] = A2P_MASKS, want[] = A2P_WANTS;
	unsigned int i, s, mux, made;
	u32 v[A2P_N];

	printf("consts %d %d %d %d %d %x %llx %d %x %x", A2P_N, (int)(sizeof(off) / sizeof(off[0])), (int)(sizeof(val) / sizeof(val[0])),
	       (int)(sizeof(mask) / sizeof(mask[0])), (int)(sizeof(want) / sizeof(want[0])), A2P_ASKED, (unsigned long long)A2P_MUX_PAGE,
	       A2P_MUX_N, A2P_MUX_VAL, GATES_AT);
	for (i = 0; i < A2P_N; i++)
		printf(" %x %x %x %x", off[i], val[i], mask[i], want[i]);
	printf("\nbits %x %x %x %x %x %x %x %x\n", A2P_MUX_BAD, A2P_TOOK(0), A2P_SETTLED(0), A2P_TOOK(1), A2P_SETTLED(1), A2P_TOOK(2),
	       A2P_SETTLED(2), A2P_MARK);
	printf("stages %x %x %x\n", M1_A2P_STAGE_PRE, M1_A2P_STAGE, M1_A2P_STAGE_PEND);
	for (s = 0; s < 256; s++)
		printf("st %x %x\n", s, a2p_stage((u8)s));
	while (scanf("%x %x %x %x %x", &mux, &v[0], &v[1], &v[2], &made) == 5)
		printf("b %x\n", a2p_byte(mux != 0, v, made));
	return 0;
}
"""
# m1_a2_pre(), m1_a2p_mux_ok() and the poll they call, as they stand, with e1_hook.c's own table of waits, against a model of the
# p-state page and the ClkGen muxes' page that logs every read, write and PMU byte. The scenario is argv[1], letters: x, y, z the
# write to DCS's word, the fabric's, the third is dropped; d, f, t that word settles 30 ms after its write; D, F, T it never
# settles (the value asked for reads back, the other copies stay, bit 31 set); r DCS's word goes back to what it was when the
# third word is written; B DCS's word reads the value in every field and keeps bit 31; m one mux reads unparked; p one reads
# with bit 30 set; i the third word reads 0x12 before anything, which is how A2 waits for it; K the PMU stops taking the byte at
# its third send, and the function goes on.
A2P_FN_PRELUDE = r"""
#include <stdio.h>
#include <string.h>
#include "a2_pre.h"
typedef uint64_t u64;
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define static_assert(...) _Static_assert(__VA_ARGS__, "")
#define BIT(n) (1U << (n))
#define GENMASK(h, l) (((~0U) << (l)) & (~0U >> (31 - (h))))
#define M1_ACK_BUSY BIT(4)
#define PAGE_MASK (~0x3fffULL)
#define M1_FABRIC_PS 0x28e20c000ULL
#define dsb(x) do { } while (0)
#define isb() do { } while (0)
#define cpu_relax() do { } while (0)
static unsigned char pst_page[0x4000], clk_page[0x4000];
static void *post_map[128];
static const char *sc;
static u64 clk, m1_tps = 24000000, at[3];
static u32 word[3] = { 0x333, 0x222, 0x555 }, was[3];	/* by offset / 0x400: the fabric's, the third, DCS's */
static int written[3];
static u64 m1_now(void) { return clk += m1_tps / 1000; }
static int has(char c) { return strchr(sc, c) != NULL; }
static void *m1_fab(void) { return pst_page; }
static u32 pst_read(unsigned int w)
{
	static const char slow[3] = { 'f', 't', 'd' }, never[3] = { 'F', 'T', 'D' };

	if (written[w] && (has(never[w]) || (has(slow[w]) && clk < at[w] + 30 * m1_tps / 1000)))
		return 0x80000000U | (was[w] & 0xff0) | (word[w] & 0xf);
	if (written[w] && w == 2 && has('B'))
		return 0x80000000U | word[w];
	return word[w];
}
static u32 readl(const void *p)
{
	const unsigned char *c = p;
	unsigned long o;
	u32 v;

	if (c >= pst_page && c < pst_page + sizeof(pst_page)) {
		o = (unsigned long)(c - pst_page);
		v = o % 0x400 || o > 0x800 ? 0xdead0000 : pst_read(o / 0x400);
		printf("r S %lx %x\n", o, v);
		return v;
	}
	o = (unsigned long)(c - clk_page);
	v = 0x80100000;
	if (o == 0x168 && has('m'))
		v = 0x85100000;
	if (o == 0x15c && has('p'))
		v = 0xc0100000;
	printf("r C %lx %x\n", o, v);
	return v;
}
static void writel(u32 v, void *p)
{
	static const char drop[3] = { 'y', 'z', 'x' };
	unsigned char *c = p;
	unsigned long o = (unsigned long)(c - pst_page);
	unsigned int w = o / 0x400;

	if (c < pst_page || c >= pst_page + sizeof(pst_page)) {
		printf("w C %lx %x\n", (unsigned long)(c - clk_page), v);
		return;
	}
	printf("w S %lx %x\n", o, v);
	if (o % 0x400 || o > 0x800)
		return;
	if (w == 1 && has('r'))
		word[2] = 0x555, written[2] = 0;
	if (has(drop[w]))
		return;
	was[w] = word[w];
	word[w] = (v & 0xf) | (v & 0xf) << 4 | (v & 0xf) << 8;
	written[w] = 1;
	at[w] = clk;
}
/* As e1_hook.c's: a command that fails stops every one after it, and the caller goes on. K: the third one fails. */
static void m1_crumb(unsigned int addr, const u8 *b, unsigned int n)
{
	static unsigned int sent;
	static int dead;

	if (dead || (has('K') && sent == 2)) {
		dead = 1;
		return;
	}
	sent++;
	printf("k %x %u %x\n", addr, n, b[0]);
}
"""
A2P_FN_MAIN = r"""
int main(int argc, char **argv)
{
	unsigned int k;
	u8 b;

	(void)argc;
	sc = argv[1];
	if (has('i'))
		word[1] = 0x12;
	for (k = 0; k < ARRAY_SIZE(m1_waits); k++)
		if ((m1_waits[k].addr & PAGE_MASK) == A2P_MUX_PAGE)
			post_map[m1_waits[k].idx] = clk_page + (m1_waits[k].addr & 0x3fff);
	b = m1_a2_pre();
	printf("ret %x\n", b);
	printf("end %x %x %x\n", word[2], word[0], word[1]);
	return 0;
}
"""
A2P_INIT_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include "a2_pre.h"
typedef uint64_t u64;
#define __iomem
#define __init
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define BIT(n) (1U << (n))
#define GENMASK(h, l) (((~0U) << (l)) & (~0U >> (31 - (h))))
#define M1_ACK_BUSY BIT(4)
#define PAGE_MASK (~0x3fffULL)
#define EINVAL 22
static unsigned char page[0x4000], rows[0x4000];
static unsigned int nread;
static u64 never;
static void *m1_fab(void) { return page; }
static u32 readl(const void *p)
{
	const unsigned char *c = p;

	if (c >= rows && c < rows + sizeof(rows))
		printf("read row %lu\n", (unsigned long)(c - rows));
	else
		printf("read %lx\n", (unsigned long)(c - page));
	return 0x1000 + nread++;
}
static int e1_never_write(u64 addr) { return addr == never; }
static void pr_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("err "); vprintf(fmt, ap); va_end(ap); }
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("info "); vprintf(fmt, ap); va_end(ap); }
"""
A2P_INIT_MAIN = r"""
int main(int argc, char **argv)
{
	unsigned int i;
	int ret;

	(void)argc;
	/* argv[1]: an address on the never-write list; argv[2]: a table row left unmapped, or 0. */
	sscanf(argv[1], "%llx", (unsigned long long *)&never);
	for (i = 0; i < ARRAY_SIZE(post_map); i++)
		post_map[i] = rows + i;
	sscanf(argv[2], "%u", &i);
	if (i)
		post_map[i] = NULL;
	ret = m1_a2p_init();
	printf("ret %d reads %u\n", ret, nread);
	return 0;
}
"""
# A2's order, written out a second time: DCS's word, the fabric's, the third; what goes to each, and what A2 then waits for.
A2P_ORDER = [(0x800, 2, 0x80000f00, 0x200), (0x000, 2, 0x80000f00, 0x200), (0x400, 0, 0x80000700, 0)]
# The five muxes in the table's order, as offsets in their page.
A2P_MUXES = [0x164, 0x16c, 0x168, 0x15c, 0x170]


def a2_pre_tests(g, src):
    """a2_pre=1: a2_pre.h against an independent reading of it; m1_a2_pre() compiled as it stands, with the module's own table of
    waits, against a model of the registers that logs every access; m1_a2p_init()'s refusals; the places in e1_hook.c that use
    them; and the byte, the run's stages and --clear through m1-crumbs."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    def byte(mux_ok, v, made):
        b = 0x80 | (0 if mux_ok else 1)
        for i, (_, val, mask, want) in enumerate(A2P_ORDER[:made]):
            b |= (2 << 2 * i if v[i] & 0xf == val else 0) | (4 << 2 * i if v[i] & mask == want else 0)
        return b

    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    waits = cut("#define M1_W_CLEAR\t\t0\n", "\n};\n")
    table = cut("static const struct e1_mmio_write {", "\n};\n")
    fns = cut("static bool m1_siod_poll(", "\n}\n") + cut("static bool m1_a2p_mux_ok(void)", "\n}\n") + cut("static u8 m1_a2_pre(void)", "\n}\n")
    init = cut("static int __init m1_a2p_init(void)", "\n}\n")
    scen = ("", "x", "y", "z", "xyz", "d", "f", "t", "D", "F", "T", "DFT", "dft", "r", "m", "p", "mp", "mx", "pD", "xF", "rT", "yz", "Dz", "mDFT", "rm", "B", "Bm", "i", "iz")
    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return os.path.join(tmp, name)

        def out(exe, *args):
            return subprocess.run([exe, *args], capture_output=True, text=True, check=True).stdout.splitlines()

        rnd = random.Random(20261113)
        words = (0, 2, 0x222, 0x202, 0x252, 0x552, 0x555, 0x333, 0x332, 0x80000222, 0x80000552, 0x200, 0x220, 0x100, 0x800, 0x700, 0x80000000, 0xf, 0x12, 0xfffff0ff)
        # For each word, one value for each pairing of its two bits: both, the value alone, the condition alone, neither.
        cases = [(m, [a, b, c], n) for m in (0, 1) for a in (0x222, 0x552, 0x200, 0x555) for b in (0x202, 0x80000222, 0x200, 0x333)
                 for c in (0, 0x220, 0x12, 0x222) for n in range(5)]
        cases += [(rnd.randrange(2), [rnd.choice(words) for _ in range(3)], rnd.randrange(6)) for _ in range(400)]
        feed = "".join(f"{m:x} {v[0]:x} {v[1]:x} {v[2]:x} {n:x}\n" for m, v, n in cases)
        hdr = [l.split() for l in subprocess.run([build("hdr", A2P_HDR)], input=feed, capture_output=True, text=True, check=True).stdout.splitlines()]
        fn = build("fn", A2P_FN_PRELUDE + waits + fns + A2P_FN_MAIN)
        runs = {a: out(fn, a) for a in scen}
        out_k = out(fn, "K")
        ini_src = A2P_INIT_PRELUDE + table + "static void *post_map[ARRAY_SIZE(e1_post_slpa)];\n" + waits
        ini = build("ini", ini_src + init + A2P_INIT_MAIN)
        loads = {a: out(ini, *a) for a in (("0", "0"), ("28e20c800", "0"), ("28e20c000", "0"), ("28e20c400", "0"), ("28e0802d8", "0"), ("28e038164", "0"),
                                           ("0", "62"), ("0", "66"), ("0", "49"))}
        bad_lists = {}
        for what, redef in (("the first two the other way round", "#undef A2P_OFFS\n#define A2P_OFFS { 0x000, 0x800, 0x400 }\n"),
                            ("a word off its place", "#undef A2P_OFFS\n#define A2P_OFFS { 0x800, 0x000, 0x404 }\n"),
                            ("a word past the page", "#undef A2P_OFFS\n#define A2P_OFFS { 0x800, 0x000, 0x4400 }\n"),
                            ("another value for the third", "#undef A2P_VALS\n#define A2P_VALS { 2, 2, 2 }\n"),
                            ("another value for DCS", "#undef A2P_VALS\n#define A2P_VALS { 3, 2, 0 }\n"),
                            ("a mux value the table doesn't write", "#undef A2P_MUX_VAL\n#define A2P_MUX_VAL 0x80100001U\n"),
                            ("six muxes", "#undef A2P_MUX_N\n#define A2P_MUX_N 6\n"),
                            ("the PLL-off word's page for the muxes'", "#undef A2P_MUX_PAGE\n#define A2P_MUX_PAGE 0x28e224000ULL\n")):
            bad_lists[what] = out(build("bad" + str(len(bad_lists)), ini_src + redef + init + A2P_INIT_MAIN), "0", "0")

    # The header against a second writing-out of it.
    consts, bits, stages = hdr[0], hdr[1], hdr[2]
    st = {int(l[1], 16): int(l[2], 16) for l in hdr if l[0] == "st"}
    got = [int(l[1], 16) for l in hdr if l[0] == "b"]
    check(consts == ["consts", "3", "3", "3", "3", "3", "f", "28e038000", "5", "80100000", "6016"] + [f"{x:x}" for o in A2P_ORDER for x in o]
          and bits == ["bits", "1", "2", "4", "8", "10", "20", "40", "80"] and stages == ["stages", "19", "1b", "1a"],
          "a2_pre.h: the three words in A2's order (DCS's, the fabric's, the third), A2's value for each and the wait's mask and value"
          " after it, five muxes at 0x80100000 in the page 0x28e038000, the byte at 0x6016 with its eight bits, and stages 0x19, 0x1b"
          " and 0x1a", str(hdr[:3]))
    check(all(st[s] == {0x4b: 0x19, 0x55: 0x1b, 0x54: 0x1a}.get(s, s) for s in range(256)),
          "a2p_stage(): 0x19, 0x1b and 0x1a for gates_mark=1's 0x4b, 0x55 and 0x54, and every other stage as it is")
    wrong = [(c, hex(b)) for c, b in zip(cases, got) if b != byte(*c)]
    check(len(got) == len(cases) and not wrong and len({byte(*c) for c in cases}) == 128,
          f"a2p_byte() in {len(cases)} cases: bit 0 set when the muxes aren't all as written, each move's two bits from its own word (the"
          " value asked for in bits 3:0, A2's condition), none for a move not made yet however its word reads, never more than three"
          " moves, and bit 7 always; every byte it can make is made", str(wrong[:3]))
    check(byte(True, [0x555, 0x333, 0x222], 3) == 0x80 == byte(True, [0x444, 0x111, 0x111], 3) == byte(True, [0x80000225, 0x333, 0x322], 3),
          "0x80 is the byte for the words as the recipe leaves them and for others that are neither as written nor as A2 waits for"
          " them: it doesn't say the words were unchanged")
    check((g["A2P_PRE"], g["A2P_WD2"], g["A2P_STAGES"]) == (0x19, (0x1a, 0x1b), (0x19, 0x1a, 0x1b)) and (g["A2P_MUX_BAD"], g["A2P_MARK"]) == (1, 0x80)
          and len(g["A2P_MOVES"]) == 3
          and not set(g["A2P_STAGES"]) & (set(g["GATES_STAGES"]) | set(g["SIOD_STAGES"]) | set(g["SIOD1_STAGES"]) | set(g["PCD_STAGES"])
                                          | set(g["SST_STAGES"]) | set(g["POSITION"]) | set(g["ACK_EARLY"]) | {0x0e, 0x0f}),
          "m1-crumbs has the byte's fields and the stages as the header has them, and the stages are no other run's")

    # m1_a2_pre() in every scenario: the accesses, in order.
    for a in scen:
        ev = [l.split() for l in runs[a]]
        acc = [(i, e[0], e[1], int(e[2], 16), int(e[3], 16)) for i, e in enumerate(ev) if e[0] in "rw"]
        ws = [(i, o, v) for i, k, pg, o, v in acc if k == "w"]
        ks = [(i, int(e[1], 16), int(e[2]), int(e[3], 16)) for i, e in enumerate(ev) if e[0] == "k"]
        ret = int([e for e in ev if e[0] == "ret"][0][1], 16)
        mux_ok = "m" not in a and "p" not in a
        took = ["x" not in a, "y" not in a, "z" not in a]
        # A third word that already reads as A2 waits for it does so with its write dropped too.
        settled = [took[0] and "D" not in a and "B" not in a, took[1] and "F" not in a, (took[2] and "T" not in a) or ("i" in a and "z" in a)]
        want = [0x80 | (0 if mux_ok else 1)]
        for n in (1, 2, 3):
            t, sd = took[:], settled[:]
            if "r" in a and n == 3:
                t[0] = sd[0] = False
            want.append(want[0] | sum((2 << 2 * i if t[i] else 0) | (4 << 2 * i if sd[i] else 0) for i in range(n)))
        mux_reads = [(pg, o) for i, k, pg, o, v in acc if i < ks[0][0]]
        s_reads = [[o for i, k, pg, o, v in acc if k == "r" and pg == "S" and lo < i < hi]
                   for lo, hi in ((ws[0][0], ks[1][0]), (ws[1][0], ks[2][0]), (ws[2][0], ks[3][0]))] if len(ws) == 3 and len(ks) == 4 else []
        ok = (len(ks) == 4 and [(k[1], k[2], k[3]) for k in ks] == [(0x6016, 1, w) for w in want] and ret == want[3]
              and [(o, v) for _, o, v in ws] == [(o, v) for o, v, _, _ in A2P_ORDER] and all(pg == "S" for i, k, pg, o, v in acc if k == "w")
              and mux_reads == [("C", o) for o in A2P_MUXES] and not any(pg == "C" for i, k, pg, o, v in acc if i > ks[0][0])
              and ks[0][0] < ws[0][0] < ks[1][0] < ws[1][0] < ks[2][0] < ws[2][0] < ks[3][0]
              and all(set(r) == {A2P_ORDER[n][0]} | {A2P_ORDER[j][0] for j in range(n)} if n else set(r) == {0x800} for n, r in enumerate(s_reads))
              and all(r[-(n + 1):] == [A2P_ORDER[j][0] for j in range(n + 1)] for n, r in enumerate(s_reads))
              and all(1 + n + 1 <= len(r) <= 102 + n + 1 for n, r in enumerate(s_reads))
              # A wait runs its 100 ms only for a word that doesn't come to read as A2 waits for it.
              and [len(r) > 60 for r in s_reads] == [not sd for sd in settled])
        check(ok, f"m1_a2_pre(), scenario '{a}': the five muxes read once each and the byte before any write; then 2 to DCS's word, 2 to"
              " the fabric's and 0 to the third, in A2's order, each polled for A2's condition for up to 100 ms and followed by a read of"
              " every word written so far and the byte; three writes, each a whole word, none to the muxes' page, nothing written back",
              "\n".join(runs[a][:14]) + f"\nwant {[hex(w) for w in want]}")
    last = {a: int([l for l in runs[a] if l.startswith("ret ")][0].split()[1], 16) for a in scen}
    lost = [l.split() for l in out_k]
    check([(e[1], e[2], e[3]) for e in lost if e[0] == "w"] == [("S", f"{o:x}", f"{v:x}") for o, v, _, _ in A2P_ORDER]
          and [e[3] for e in lost if e[0] == "k"] == ["80", "86"] and [e for e in lost if e[0] == "ret"] == [["ret", "fe"]]
          and out_k[-1] == "end 222 222 0",
          "m1_a2_pre() with the PMU refusing the byte from its third send on: the last byte recorded is DCS's, and all three moves are"
          " made all the same, so a byte that stops short doesn't show a move that stopped", "\n".join(out_k))
    check((last[""], last["xyz"], last["m"], last["D"], last["d"], last["r"], last["z"], last["DFT"], last["mDFT"], last["B"], last["i"], last["iz"]) == (0xfe, 0x80, 0xff, 0xfa, 0xfe, 0xf8, 0x9e, 0xaa, 0xab, 0xfa, 0xfe, 0xde)
          and runs["iz"][-1] == "end 222 222 12"
          and [l.split()[3] for l in runs["r"] if l.startswith("k ")] == ["80", "86", "9e", "f8"]
          and runs[""][-1] == "end 222 222 0" and runs["xyz"][-1] == "end 555 333 222",
          "m1_a2_pre(): 0xfe if all three take and settle, 0x80 if none takes, 0xff only with a mux not as written; a word that takes"
          " and doesn't settle has its first bit alone; one that settles late is settled; one put back after its send loses both bits"
          " in the last send; a third word that read as A2 waits for it before a write that was dropped has its second bit alone, so that"
          " bit by itself doesn't show a write that changed anything", str(last))

    # The source: what the function writes, and nothing else like it.
    body = cut("static u8 m1_a2_pre(void)", "\n}\n")
    mux_body = cut("static bool m1_a2p_mux_ok(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("
    check(re.findall(wr + r"[^;]*;", body) == ["writel(val[i], ps + off[i]);"] and not re.search(wr, init) and not re.search(wr, mux_body)
          and len(re.findall(r"\bm1_a2_pre\(", code_only)) == 2 and len(re.findall(r"\bm1_a2p_mux_ok\(", code_only)) == 2
          and "\t\twritel(val[i], ps + off[i]);\n\t\tdsb(sy);\n\t\tm1_siod_poll(ps + off[i], mask[i], want[i]);\n" in body
          and "void __iomem *ps = m1_fab();" in body and "static const u16 off[A2P_N] = A2P_OFFS;" in body
          and "static const u32 val[A2P_N] = A2P_VALS, mask[A2P_N] = A2P_MASKS, want[A2P_N] = A2P_WANTS;" in body
          and not re.search(r"\bm1_(ps|sio|look_\w+)\b", body),
          "m1_a2_pre() has one write, a2_pre.h's value to a2_pre.h's offset in the p-state page, and touches no power or gate page; the"
          " mux read and the load write nothing")
    check(all(t in src for t in ("FAB_PS + A2P_DCS == 0x28e20c800ULL", "FAB_PS + A2P_FAB == 0x28e20c000ULL", "FAB_PS + A2P_THIRD == 0x28e20c400ULL",
                                 "A2P_MUX_PAGE == (0x28e038164ULL & PAGE_MASK)", "A2P_MUX_VAL == 0x80100000U", "A2P_N == 3"))
          and "#define A2P_OFFS\t\t{ A2P_DCS, A2P_FAB, A2P_THIRD }\n" in open(os.path.join(os.path.dirname(HOOK), "a2_pre.h")).read()
          and "\tstatic const u16 want_off[A2P_N] = { 0x800, 0x000, 0x400 };\n\tstatic const u32 want_val[A2P_N] = { 2, 2, 0 };\n" in init,
          "the three addresses, the muxes' page and value and the lists' lengths are pinned at build time, and the load compares the"
          " offsets and values with a second writing-out of them")
    after = cut("static void __noreturn m1_after_slpa(void)", "\n}\n")
    check(in_order_of(after, ["\t\tm1_crumb(0x6004, b, 4);\n", "\t\t\tm1_crumb(0x6012, b, 2);\n", "\tif (gates_mark && !sio_down && !pcie_down && !sio_steps && !a2_pre) {\n",
                              "\tif (sio_steps)\n\t\tm1_gates = m1_sio_steps();\n", "\tif (a2_pre)\n\t\tm1_gates = m1_a2_pre();\n",
                              "\t/* recfg_bite=1", "/* 9 */", "\tlocal_daif_mask();", "writel_relaxed(1, commit);", "m1_alarm_arm(rtc);"])
          and after.count("m1_a2_pre(") == 1 and len(re.findall(r"\ba2p_stage\(", code_only)) == 2,
          "step 8: after the stage and the outcomes, in place of gates_mark=1's reads and before step 9's mask and the commit; its byte is"
          " what the alarm's last command sends; the stage is mapped in two places", after[-4500:])
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool a2_pre;\nmodule_param(a2_pre, bool, 0444);\n" in src and not re.search(r"\ba2_pre\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (a2_pre && (!gates_mark || sio_down || pcie_down || sio_steps || probe)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n",
                                   "\treturn 0;\n\nbad_wait:"])
          and 'a2_pre ? " a2_pre=1" : ""' in src
          and in_order_of(src, ["\tif (gates_mark) {\n\t\tint ret = m1_gates_init();", "\tif (a2_pre) {\n\t\tint ret = m1_a2p_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n"]),
          "a2_pre=1 loads only with gates_mark=1 and never with sio_down=1, pcie_down=1, sio_steps=1 or probe=1; it's off by default,"
          " read-only, named in the load line, and the load runs m1_a2p_init() after the gate pages are mapped and returns its refusal",
          params[-1500:])

    # m1_a2p_init(): the five muxes through their table rows and the three words read once each and logged; refused, with nothing
    # read, if a word is on the never-write list or the lists aren't the ones meant.
    ok = loads[("0", "0")]
    check(ok[-1] == "ret 0 reads 8" and [r for r in ok if r.startswith("read ")] == [f"read row {i}" for i in (62, 63, 64, 65, 66)] + ["read 800", "read 0", "read 400"]
          and len([r for r in ok if r.startswith("info m1: a2_pre: awake: DCS 0x28e20c800 00001005; fabric 0x28e20c000 00001006; 0x28e20c400 00001007;"
                                                 " the muxes, in the table's order: 00001000 00001001 00001002 00001003 00001004.")]) == 1
          and not any(r.startswith("err") for r in ok),
          "m1_a2p_init(): the five muxes read through the table's rows 62 to 66 and the three words, once each, and logged", "\n".join(ok))
    check(all(loads[(n, "0")][-1] == "ret -22 reads 0" for n in ("28e20c800", "28e20c000", "28e20c400"))
          and loads[("28e0802d8", "0")][-1] == "ret 0 reads 8" and loads[("28e038164", "0")][-1] == "ret 0 reads 8"
          and loads[("0", "62")][-1] == "ret -22 reads 0" and loads[("0", "66")][-1] == "ret -22 reads 0" and loads[("0", "49")][-1] == "ret 0 reads 8"
          and all(v[-1] == "ret -22 reads 0" and any(r.startswith("err m1: a2_pre:") for r in v) and not any(r.startswith("info") for r in v)
                  for v in bad_lists.values()),
          "m1_a2p_init(): refuses, reading nothing, if one of the three is on the never-write list, a mux row isn't mapped, the three aren't in A2's"
          " order at A2's offsets with A2's values, or the table's waits in the muxes' page aren't five rows that write the value"
          " meant; a mux's own address on that list doesn't stop it, since the muxes are only read", str(loads) + str(bad_lists))

    # m1-crumbs. The run's record at its last stage, all as meant, with its byte at 0x6016.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        alarm = (0x41, 121544 + 3600, 0, 0)
        rec = dict(parked=0x7d, left=0, down=0xf5, secs=121544, prog=(0xff, 0xff), flags=0x90, isrs=0x8d)

        def dec(stage, gb, x=217, **kw):
            fake(path, scratch(stage, **dict(rec, drained=gb, **kw)), rtc_secs=121544 + x + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            return r.returncode, r.stdout, r.stderr

        a2_so = ("so: ALL THREE", "so: A CANDIDATE", "so: NONE OF THE THREE", "DIDN'T TAKE", "DIDN'T READ BACK")
        rc, out_, err = dec(0x9b, 0xfe, x=90)
        check(rc == 0 and "stage 0x6004 = 0x9b (pmp_out=1: bit 7, so the stage is 0x1b) (pmgr_late=1): a2_pre=1: gates_mark=1's 0x55:" in out_
              and "0x6016 = 0xfe: a2_pre=1's byte, in place of the alarm's result:" in out_
              and "  the five ClkGen muxes the table parked (0x28e03815c to 0x28e038170), read after the table for the first time: all five read 0x80100000" in out_
              and "  DCS's p-state word (0x28e20c800) := 2: read asked for 2; read as A2 waits for it (bits 11:8 at 2 with bit 31 clear), at the last read before the commit\n" in out_
              and "  the fabric's (0x28e20c000) := 2: read asked for 2; read as A2 waits for it (bits 11:8 at 2 with bit 31 clear), at the last read before the commit\n" in out_
              and "  0x28e20c400 := 0: read asked for 0; read as A2 waits for it (bits 10:8 at 0 with bit 31 clear), at the last read before the commit"
                  " (what it read before the write isn't recorded in this run: 0x222 awake and after noirq)\n" in out_
              and "  (fabric_up=1's lines above are about step 5: this run then asks the fabric, and DCS, down to 2 before the commit)\n" in out_
              and "  so: ALL THREE MOVES TOOK AND SETTLED before the commit" in out_ and "nothing records the commit" not in out_
              and "alarm command" not in out_ and "gates_mark=1's byte" not in out_ and "pcie_down=1's byte" not in out_
              and "0x6007 = 0xf5: fabric_up=1's byte, in place of the cores that held:\n" in out_ and "WD1 stood as the marker" in out_
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out_
              and "0x6013 = 0xff: ps_words=1 wrote the 20 power words" in out_
              and "  wd1_mark=1's marker: about 90 s is what WD1 gives with its count never zeroed: B2 didn't reach its op 224" in out_
              and "0x140c is the commit's second + 3600, as the run wrote it" in out_,
              "a2_pre=1's 0x9b: its byte read line by line in the alarm's result's place; the rest read as --m1-gates' 0xd5", out_ + err)
        rc5, out5, err5 = dec(0xd5, 0xfe)
        rcc, outc, errc = dec(0x9b, 0xfe)
        skip = ("0x6000:", "0x6010:", "stage 0x6004", "0x6016", "  0x28e", "  AFNC2", "  so: ", "  DCS", "  the f")
        missing = [l for l in out5.splitlines() if not l.startswith(skip) and l not in outc.splitlines()]
        check(rc5 == 0 and rcc == 0 and "gates_mark=1's byte" in out5 and "a2_pre=1's byte" not in out5 and not missing,
              "0xfe at 0x6016: the gate byte under 0xd5, a2_pre=1's under 0x9b, and nothing else read differently", "\n".join(missing))
        rc, out_, err = dec(0x9a, 0xfa)
        check(rc == 0 and "so the stage is 0x1a" in out_ and "a2_pre=1: gates_mark=1's 0x54:" in out_ and "pending afterwards" in out_
              and "  so: A CANDIDATE: DCS's p-state word (0x28e20c800) := 2 TOOK AND DIDN'T SETTLE" in out_ and "It isn't proof that A2 waits there for ever" in out_
              and "restart's time isn't read" in out_ and "  wd1_mark=1's marker: not read. CPU0 had an exception pending" in out_,
              "a2_pre=1's 0x9a: the pending stage, a move that took and didn't settle said as a candidate and not as proof, and no reading"
              " of the time", out_ + err)
        rc, out_, err = dec(0x99, 0x86, x=90, parked=0xfe)
        check(rc == 0 and "so the stage is 0x19" in out_ and "8 (a2_pre=1): gates_mark=1's 0x4b, with A2's three moves made after it and left" in out_
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out_
              and "  the fabric's (0x28e20c000) := 2: neither of its bits is set, and none of a later move's" in out_
              and "  stage 0x19, so nothing records the commit after this byte (0x1a or 0x1b comes after it): the last move that shows is DCS's p-state word"
                  " (0x28e20c800) := 2, so CPU0 stopped in the move after it (the fabric's (0x28e20c000) := 2), or the send after that move failed and the"
                  " run went on unrecorded" in out_ and "this record alone doesn't show that it was one" in out_
              and not any(t in out_ for t in a2_so) and "  wd1_mark=1's marker: not read. Stage 0x19 is the last before the commit" in out_,
              "a2_pre=1's 0x99 with DCS's send: the stage before the commit, the move it stopped in named, no verdict and no reading of the"
              " time", out_ + err)
        for x in (10, 90):
            rc, out_, err = dec(0x99, 0, x=x, parked=0xfe)
            check(rc == 0 and "0x6016 = 0x00: A2_PRE'S BYTE DIDN'T LAND (bit 7 clear), though stage 0x19 and the steps' outcomes did" in out_
                  and "before any of the three writes" in out_ and "the five ClkGen muxes the table parked" not in out_ and not any(t in out_ for t in a2_so)
                  and f"the SoC restarted about {x} s after the commit" in out_,
                  f"a2_pre=1's 0x99 with 0x6016 = 0, a restart at {x} s: said to have stopped before any write", out_ + err)
        for stage, gb, kw, want in ((0x9b, 0x00, {}, "0x6016 = 0x00: bit 7 is clear, so a2_pre=1's byte didn't survive the reset"),
                                    (0x9a, 0x7f, {}, "0x6016 = 0x7f: bit 7 is clear"),
                                    (0x9b, 0xfe, dict(prog=(0xff, 0x00)), "0x6016 = 0xfe: not read. a2_pre=1's byte goes out after the steps' outcomes"),
                                    (0x1b, 0xfe, {}, "0x6016 = 0xfe: not read. An a2_pre=1 run's stages always carry pmp_out=1's bit 7")):
            rc, out_, err = dec(stage, gb, **kw)
            check(rc == 0 and want in out_ and "A2_PRE'S BYTE" not in out_ and "alarm command" not in out_
                  and "the five ClkGen muxes the table parked" not in out_ and not any(t in out_ for t in a2_so),
                  f"stage 0x{stage:02x}, 0x6016 = 0x{gb:02x}{', no outcomes marker' if kw.get('prog') else ''}: a2_pre=1's byte isn't read", out_ + err)
        rc, out_, err = dec(0x9b, 0xfe, prog=(0xff, 0x00))
        check(rc == 0 and "The steps' outcomes go out right after stage 0x19's command" in out_,
              "a2_pre=1 without the outcomes' marker: fabric_up=1's byte is said to follow stage 0x19's command, the run's own", out_ + err)
        # Every byte, at each of the run's stages, read independently of the decoder.
        wrong = []
        for s in (0x19, 0x1a, 0x1b):
            for v in range(256):
                b = bytearray(0x20)
                b[0x12], b[0x13], b[0x16] = 0xc0, 0xff, v
                lines = g["a2p_lines"](s, bytes(b), True)
                text = "\n".join(lines)
                so = [l for l in lines if l.startswith("  so: ")]
                took, settled = [bool(v & 2 << 2 * i) for i in range(3)], [bool(v & 4 << 2 * i) for i in range(3)]
                if not v & 0x80:
                    ok = len(lines) == 1 and not so and ("DIDN'T LAND" in text) == (s == 0x19) and ("Not read" in text) == (s != 0x19)
                else:
                    shown = [s != 0x19 or took[i] or settled[i] or any(took[i + 1:]) or any(settled[i + 1:]) for i in range(3)]
                    ok = ("a2_pre=1's byte" in lines[0] and ("NOT ALL FIVE READ 0x80100000" in lines[1]) == bool(v & 1)
                          and ("all five read 0x80100000" in lines[1]) == (not v & 1) and len(lines) >= 5
                          and all(("neither of its bits is set" in lines[2 + i]) == (not shown[i]) for i in range(3))
                          and all(not shown[i] or ((("read asked for" in lines[2 + i]) == took[i]) and (("DIDN'T READ ASKED FOR" in lines[2 + i]) == (not took[i]))
                                                   and (("read as A2 waits for it" in lines[2 + i]) == settled[i])
                                                   and (("NOT AS A2 WAITS FOR IT" in lines[2 + i]) == (not settled[i]))) for i in range(3))
                          and all((("at the last read before the commit" in lines[2 + i]) == (s != 0x19)) for i in range(3) if shown[i])
                          and ("nothing records the commit" in text) == (s == 0x19) and (not so) == (s == 0x19)
                          and "SIO's firmware isn't shown stopped" not in text)
                    if ok and s != 0x19:
                        n_cand = sum(took[i] and not settled[i] for i in range(3))
                        n_not = sum(not took[i] and not settled[i] for i in range(3))
                        n_back = sum(not took[i] and settled[i] for i in range(3))
                        every, none = all(took) and all(settled), not any(took) and not any(settled)
                        ok = (("ALL THREE MOVES TOOK AND SETTLED" in text) == every and ("NONE OF THE THREE READ AS WRITTEN OR AS A2 WAITS FOR IT" in text) == none
                              and len(so) == (1 if every or none else n_cand + n_not + n_back)
                              and (every or none or (sum("A CANDIDATE" in l for l in so) == n_cand and sum("DIDN'T TAKE" in l for l in so) == n_not
                                                     and sum("THE VALUE DIDN'T READ BACK" in l and "nor whether the write changed anything" in l for l in so) == n_back))
                              and "the write did something" not in text
                              and "found the words as the recipe leaves them" not in text and "--m1-gates' again" not in text
                              and all("It isn't proof that A2 waits there for ever" in l for l in so if "A CANDIDATE" in l)
                              and all("says nothing about A2's own" in l for l in so if "DIDN'T TAKE" in l or "NONE OF THE THREE" in l)
                              and ("less likely" in text) == every and (not every or "not excluded" in text))
                        ok = ok and (("about 120 s" in so[-1] and "is what B2 getting past its op 224 would give" in so[-1]
                                      and "as far as the marker and WD2's sample bear a reading out" in so[-1]) == (s == 0x1b)
                                     and "means B2" not in text
                                     and ("restart's time isn't read" in so[-1]) == (s == 0x1a)
                                     and not any("about 120 s" in l or "restart's time isn't read" in l for l in so[:-1]))
                    if ok and s == 0x19:
                        n = max([i + 1 for i in range(3) if took[i] or settled[i]], default=0)
                        last_line = lines[-1]
                        ok = (("one of three things this record can't tell apart" in last_line and "the send after it failed" in last_line) == (n == 0)
                              and ("or the send after that move failed and the run went on unrecorded" in last_line) == (n in (1, 2))
                              and ("or a later command failed" in last_line) == (n == 3)
                              and ("all three moves show in it" in last_line) == (n == 3)
                              and ("so CPU0 stopped in the move after it" in last_line) == (n in (1, 2))
                              and (n not in (1, 2) or (g["A2P_MOVES"][n - 1][0] in last_line and g["A2P_MOVES"][n][0] in last_line))
                              and "Inconclusive about the rest of the entry" in last_line)
                if not ok:
                    wrong.append((hex(s), hex(v), lines))
            b = bytearray(0x20)
            b[0x12], b[0x13], b[0x16] = 0x40, 0xff, 0xfe
            if sum("SIO's firmware isn't shown stopped" in l for l in g["a2p_lines"](s, bytes(b), True)) != 1:
                wrong.append((hex(s), "SIO's firmware not stopped: not said"))
            b[0x12] = 0xc0
            if "not read" not in g["a2p_lines"](s, bytes(b), False)[0] or len(g["a2p_lines"](s, bytes(b), False)) != 1:
                wrong.append((hex(s), "no pmp_out=1 bit"))
        check(not wrong, "a2_pre=1: for every byte at each of its stages: the muxes' line and each move's follow their bits; after the"
              " commit one verdict if all three took and settled or none did, and otherwise one line for each move that took without"
              " settling, a candidate and never proof, or didn't take, which says nothing of A2; the restart's time is spoken of once,"
              " at the last stages; before the commit no verdict, and the move the record stops in is named; nothing is read from a"
              " byte without bit 7", str(wrong[:2]))
        mr = g["mark_reading"]
        wrong = [(hex(v), x) for v in range(256) for x in (60, 88, 90, 105, 120, 123, 135, 146, 150, 163, 300)
                 if mr(x, 0x19, v) != mr(x, 0x4b, v).replace("Stage 0x4b", "Stage 0x19") or mr(x, 0x1a, v) != mr(x, 0x54, v)
                 or mr(x, 0x1b, v) != mr(x, 0x55, v)]
        check(not wrong and "Stage 0x19 is the last before the commit" in mr(90, 0x19, 0x7d),
              "the restart's time is read against the marker at 0x19, 0x1a and 0x1b exactly as at 0x4b, 0x54 and 0x55", str(wrong[:3]))
        # --clear: the run's alarm disarmed by its compare value; with the second lost, 0x6016 is never taken for the alarm's result.
        for stage, kw in ((0x9b, {}), (0x9a, {}), (0x99, dict(parked=0xfe))):
            before = fake(path, scratch(stage, **dict(rec, drained=0xbd, **kw)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt="4800000000 24000000")
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and open(path, "rb").read() == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after a2_pre=1's 0x{stage:02x}: the run's alarm disarmed by its compare value, and the crumbs zeroed", r.stdout + r.stderr)
        before = fake(path, scratch(0x9b, **dict(rec, secs=0, drained=0xbd)), rtc_secs=121761, alarm=alarm)
        r = run(path, "--clear", cnt="4800000000 24000000")
        check(r.returncode == 2 and open(path, "rb").read() == before and "disarmed" not in r.stdout
              and "after gates_mark=1's, sio_down=1's, pcie_down=1's, sio_steps=1's and a2_pre=1's stages it never does: it's that run's byte" in r.stderr,
              "--clear after 0x9b with the second lost and 0xbd at 0x6016: not taken for an acked alarm, so nothing is written", r.stdout + r.stderr)


PROBE_HDR = r"""
#include <stdio.h>
#include "asc_probe.h"
int main(void)
{
	static const u32 ps[] = { 0x1400024f, 0x0f0000ff, 0x1f0f03ff, 0x0f00030f, 0x000000f0, 0, 0x0f00020f, 0x000000e0, 0x00000f00, 0xffffff0f };
	unsigned int a, v;

	printf("consts %llx %llx %llx %x %x %x %x\n", ASC_SIO_MBOX, ASC_ANS_MBOX, ASC_PMP_MBOX, ASC_READY, ASC_ANS2_UP, ASC_PROBE_STAGE, ASC_PROBE_MARK);
	printf("more %d %x %x %x\n", ASC_TRIES, ASC_PS_NOT_WRITTEN, ASC_PS_STATE, ASC_PROBE_AT);
	printf("at %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n", ASC_AT_NONE, ASC_AT_PREPARE, ASC_AT_SIO_RUN, ASC_AT_ANS_PREP,
	       ASC_AT_PMP_RUN, ASC_AT_SIO_IDLE, ASC_AT_SIO_IDLED, ASC_AT_SIO_STOP, ASC_AT_SIO_STOPPED, ASC_AT_NOIRQ, ASC_AT_LATE, ASC_AT_ANS_UP,
	       ASC_AT_ANS_LATE, ASC_AT_ANS_BACK, ASC_AT_PMP_LATE, ASC_AT_PMP_IDLE, ASC_AT_PMP_IDLED, ASC_AT_PMP_STOP, ASC_AT_PMP_STOPPED,
	       ASC_AT_SIO_UP, ASC_AT_SIO_LATE, ASC_AT_DONE);
	for (a = 0; a < sizeof(ps) / sizeof(ps[0]); a++)
		printf("on %x %d\n", ps[a], asc_ps_on(ps[a]));
	for (v = 0; v < 128; v++)
		printf("sio %u %x\n", v, asc_sio_byte(v & 1, v >> 1 & 1, v >> 2 & 1, v >> 3 & 1, v >> 4 & 1, v >> 5 & 1, v >> 6 & 1));
	for (v = 0; v < 128; v++)
		printf("pmp %u %x\n", v, asc_pmp_byte(v & 1, v >> 1 & 1, v >> 2 & 1, v >> 3 & 1, v >> 4 & 1, v >> 5 & 1, v >> 6 & 1));
	for (v = 0; v < 128; v++)
		printf("ans %u %x\n", v, asc_ans_byte(v & 1, v >> 1 & 1, v >> 2 & 1, v >> 3 & 1, v >> 4 & 1, v >> 5 & 1, v >> 6 & 1));
	for (v = 0; v < 256; v++)
		printf("final %u %d %d %d %s %s %s\n", v, asc_sio_final(v), asc_pmp_final(v), asc_ans_final(v), asc_final_str(asc_sio_final(v)),
		       asc_final_str(asc_pmp_final(v)), asc_final_str(asc_ans_final(v)));
	printf("str %s|%s|%s\n", asc_final_str(-1), asc_final_str(0), asc_final_str(1));
	return 0;
}
"""

PROBE_RUN_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "asc_probe.h"
typedef uint16_t u16;
typedef uint64_t u64;
#define __iomem
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define cpu_relax() do { } while (0)
#define udelay(us) do { } while (0)
#define dsb(x) do { } while (0)
#define div64_u64(a, b) ((a) / (b))
#define ENODEV 19
#define ETIME 62
"""
PROBE_RUN_MODEL = r"""
/*
 * A model of the registers the probe touches. A power word takes a write's target, and its actual state, bits 7:4,
 * follows if the scenario lets that domain rise; its BUSY bit and two flags are cleared by a 1 written to them and
 * otherwise kept. A read of a coprocessor's word while its domain's actual state isn't f prints FAULT, a write to
 * ps_pmp NEVER, anything outside the model STRAY: none of the three may ever print. A write to the PMU's 0x6010,
 * the step about to be taken, prints as "at N".
 */
enum { R_SIO, R_SIO_CPU, R_UART_P, R_SPI_P, R_ANS2, R_PS_PMP, R_GATE44, R_GATE40, R_FAB, R_DCS, R_THIRD, R_SIO_CTRL, R_SIO_STAT,
       R_PMP_CTRL, R_PMP_STAT, R_SIO_MBOX, R_ANS_MBOX, R_PMP_MBOX, R_N };
static const char *const rname[R_N] = { "SIO", "SIO_CPU", "UART_P", "SPI_P", "ANS2", "ps_pmp", "gate44", "gate40", "fabric", "DCS", "third",
	"SIO_CTRL", "SIO_STAT", "PMP_CTRL", "PMP_STAT", "SIO_MBOX", "ANS_MBOX", "PMP_MBOX" };
static unsigned char sio_page[0x1000], ps_page[0x1000], pos_page[0x1000], fab_page[0x1000], sio_asc_page[0x1000], pmp_asc_page[0x1000],
	mbox_page[3][0x10];
static void *m1_sio = sio_page, *m1_ps = ps_page, *m1_pos = pos_page, *m1_sio_asc = sio_asc_page, *m1_pmp_asc = pmp_asc_page;
static void *m1_sio_mbox = mbox_page[0], *m1_ans_mbox = mbox_page[1], *m1_pmp_mbox = mbox_page[2];
static int sio_rtk_obj, pmp_rtk_obj;
static void *m1_sio_rtk = &sio_rtk_obj, *m1_pmp_rtk = &pmp_rtk_obj;
static struct { u32 val; } e1_post_slpa[M1_SIO_ENTRY + 1] = { [M1_SIO_ENTRY] = { 0x0400024f } };
static void *post_map[M1_SIO_ENTRY + 1] = { [M1_SIO_ENTRY] = sio_page + M1_PS_SIO_CPU };
static u64 m1_tps = 1000, fake_now;
static u32 reg[R_N];
static unsigned int mreads[3];
static int late;
static struct scenario {
	int sio_cpu_rises, sio_cpu_rises_late, ans2_rises, sio_running, sio_idle_ret, pmp_running, pmp_idle_ret, pmp_goes_idle, kwrite_ret;
	u32 ans2_late, ps_pmp, mbox[3], mbox_late[3];
	int ans2_stuck, pmp_on_at_idle, pmp_off_at_stop;
} sc;

static u64 m1_now(void) { return fake_now++; }
static void *m1_fab(void) { return fab_page; }
static void msleep(unsigned int ms) { (void)ms; }
static int reg_of(const void *p)
{
	static const struct { const unsigned char *at; int r; } map[] = {
		{ sio_page + M1_PS_SIO, R_SIO }, { sio_page + M1_PS_SIO_CPU, R_SIO_CPU }, { sio_page + M1_PS_UART_P, R_UART_P },
		{ sio_page + M1_PS_SPI_P, R_SPI_P }, { sio_page + M1_PS_ANS2, R_ANS2 }, { ps_page + M1_PS_PMP_RO, R_PS_PMP },
		{ pos_page + M1_POS_GATE44, R_GATE44 }, { pos_page + M1_POS_GATE40, R_GATE40 }, { fab_page, R_FAB }, { fab_page + 0x800, R_DCS },
		{ fab_page + 0x400, R_THIRD }, { sio_asc_page + M1_ASC_CPU_CONTROL, R_SIO_CTRL }, { sio_asc_page + M1_ASC_CPU_STATUS, R_SIO_STAT },
		{ pmp_asc_page + M1_ASC_CPU_CONTROL, R_PMP_CTRL }, { pmp_asc_page + M1_ASC_CPU_STATUS, R_PMP_STAT },
		{ mbox_page[0], R_SIO_MBOX }, { mbox_page[1], R_ANS_MBOX }, { mbox_page[2], R_PMP_MBOX },
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(map); i++) {
		if (map[i].at == (const unsigned char *)p)
			return map[i].r;
	}
	return -1;
}
static u32 readl(const void *p)
{
	static const int dom[3] = { R_SIO_CPU, R_ANS2, R_PS_PMP };
	int r = reg_of(p);

	if (r < 0) {
		printf("STRAY read\n");
		return 0xdead0000;
	}
	if (r >= R_SIO_MBOX) {
		if (!asc_ps_on(reg[dom[r - R_SIO_MBOX]]))
			printf("FAULT %s read with %s %08x\n", rname[r], rname[dom[r - R_SIO_MBOX]], reg[dom[r - R_SIO_MBOX]]);
		mreads[r - R_SIO_MBOX]++;
	}
	return reg[r];
}
static void writel(u32 v, void *p)
{
	int r = reg_of(p), rises = 1;

	if (r < 0) {
		printf("STRAY write %08x\n", v);
		return;
	}
	printf("w %s %08x\n", rname[r], v);
	if (r == R_SIO_CPU)
		rises = late ? sc.sio_cpu_rises_late : sc.sio_cpu_rises;
	if (r == R_ANS2)
		rises = sc.ans2_rises;
	if (r == R_SIO || r == R_SIO_CPU || r == R_UART_P || r == R_SPI_P || r == R_ANS2) {
		int up = (v & 0xf) == 0xf && rises;

		/* ans2_stuck: once up, ANS2 doesn't come down again. */
		if (r == R_ANS2 && sc.ans2_stuck && asc_ps_on(reg[r]))
			up = 1;
		reg[r] = (reg[r] & ASC_PS_NOT_WRITTEN & ~v) | (v & ~(ASC_PS_NOT_WRITTEN | 0xf0U)) | (up ? 0xf0 : 0);
	} else if (r == R_SIO_CTRL || r == R_PMP_CTRL) {
		reg[r] = v;
		/* pmp_off_at_stop: ps_pmp stops reading on once the PMP's RUN is cleared. */
		if (r == R_PMP_CTRL && sc.pmp_off_at_stop)
			reg[R_PS_PMP] = 0x00000300;
	} else if (r == R_PS_PMP) {
		printf("NEVER ps_pmp written\n");
	} else {
		printf("STRAY write to %s\n", rname[r]);
	}
}
#define writel_relaxed writel
static bool apple_rtkit_is_running(void *rtk) { return rtk == m1_sio_rtk ? sc.sio_running : sc.pmp_running; }
static int apple_rtkit_idle(void *rtk)
{
	int sio = rtk == m1_sio_rtk, ret = sio ? sc.sio_idle_ret : sc.pmp_idle_ret;

	printf("idle %s %d\n", sio ? "SIO" : "PMP", ret);
	if (!ret && (sio || sc.pmp_goes_idle))
		reg[sio ? R_SIO_STAT : R_PMP_STAT] |= 1;
	/* pmp_on_at_idle: ps_pmp reads on only from the PMP's idle pair on. */
	if (!sio && sc.pmp_on_at_idle)
		reg[R_PS_PMP] = 0x0f0002ff;
	return ret;
}
static int m1_kwrite(unsigned int addr, const u8 *b, unsigned int n)
{
	unsigned int i;

	/* The module's own gate on what may be written to the PMU, as m1_kwrite() applies it. */
	if (!m1_pmu_ok(true, addr, b, n))
		printf("STRAY write to the PMU's %x, which m1_pmu_ok() refuses\n", addr);
	if (addr == ASC_PROBE_AT && n == 1) {
		printf("at %u\n", b[0]);
		return sc.kwrite_ret;
	}
	printf("kwrite %x", addr);
	for (i = 0; i < n; i++)
		printf(" %02x", b[i]);
	printf(" ret %d\n", sc.kwrite_ret);
	return sc.kwrite_ret;
}
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("log "); vprintf(fmt, ap); va_end(ap); }
"""
PROBE_RUN_MAIN = r"""
static void run(unsigned int n, const struct scenario *s)
{
	sc = *s;
	late = 0;
	fake_now = 0;
	memset(reg, 0, sizeof(reg));
	memset(&m1_pr, 0, sizeof(m1_pr));
	memset(mreads, 0, sizeof(mreads));
	/* As m1_probe_init() leaves them when ANS's word read set at load. */
	m1_pr.ans_load = m1_pr.ans_load_read = true;
	/* Awake, after the drivers' late suspend: SIO's subtree on, SIO_CPU gating itself (actual 4), the NVMe's domain still on. */
	reg[R_SIO] = 0x0f0000ff;
	reg[R_UART_P] = reg[R_SPI_P] = 0xff;
	reg[R_SIO_CPU] = 0x1400024f;
	reg[R_ANS2] = 0x0f0000ff;
	reg[R_PS_PMP] = sc.ps_pmp;
	reg[R_SIO_CTRL] = reg[R_PMP_CTRL] = M1_ASC_CPU_RUN;
	reg[R_SIO_MBOX] = sc.mbox[0];
	reg[R_ANS_MBOX] = sc.mbox[1];
	reg[R_PMP_MBOX] = sc.mbox[2];
	printf("case %u\n", n);
	m1_probe_prepare();
	printf("mreads %u %u %u\n", mreads[0], mreads[1], mreads[2]);
	/* The noirq pass: genpd cuts SIO's subtree, and ANS2 unless the scenario keeps it. */
	late = 1;
	reg[R_SIO] = 0x300;
	reg[R_UART_P] = reg[R_SPI_P] = 0x200;
	reg[R_SIO_CPU] = 0x04000200;
	reg[R_ANS2] = sc.ans2_late;
	reg[R_SIO_MBOX] = sc.mbox_late[0];
	reg[R_ANS_MBOX] = sc.mbox_late[1];
	reg[R_PMP_MBOX] = sc.mbox_late[2];
	printf("noirq\n");
	m1_probe_late();
	printf("mreads %u %u %u\n", mreads[0], mreads[1], mreads[2]);
	printf("end ANS2 %08x SIO_CTRL %08x PMP_CTRL %08x\n", reg[R_ANS2], reg[R_SIO_CTRL], reg[R_PMP_CTRL]);
}

int main(void)
{
	const struct scenario good = { 1, 1, 1, 1, 0, 1, 0, 1, 0, 0x00000300, 0x0f0002ff, { 2, 2, 2 }, { 2, 2, 2 }, 0, 0, 0 };
	struct scenario s[16];
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(s); i++)
		s[i] = good;
	s[1].ans2_rises = 0;			/* the raise of ANS2 never reaches actual f */
	s[2].ans2_late = 0x0f0000ff;		/* Linux left ANS2 on */
	s[3].pmp_idle_ret = -ETIME;		/* the PMP's idle pair fails */
	s[4].pmp_goes_idle = 0;			/* it returns 0, but the PMP never reads idle */
	s[5].sio_idle_ret = -ETIME;		/* SIO's idle pair fails */
	s[6].sio_cpu_rises_late = 0;		/* SIO_CPU doesn't come up after noirq */
	s[7].ps_pmp = 0x00000300;		/* ps_pmp doesn't read on */
	s[8].kwrite_ret = -5;			/* the PMU doesn't take the bytes */
	s[9].mbox_late[0] = s[9].mbox_late[1] = s[9].mbox_late[2] = 0x80000000;	/* bit 1 clear on all three after noirq */
	s[10].sio_cpu_rises = 0;		/* SIO_CPU doesn't come up at prepare */
	s[11].sio_running = s[11].pmp_running = 0;	/* neither RTKit says it's running */
	s[12].ans2_late = 0x04000b00;		/* ANS2 found with BUSY and both flags set, and a PS_AUTO of its own */
	s[13].ans2_stuck = 1;			/* ANS2 doesn't come down again after the write-back */
	s[14].ps_pmp = 0x00000300;		/* ps_pmp reads on only from the PMP's idle pair on */
	s[14].pmp_on_at_idle = 1;
	s[15].pmp_off_at_stop = 1;		/* ps_pmp stops reading on when the PMP's RUN is cleared */
	for (i = 0; i < ARRAY_SIZE(s); i++)
		run(i, &s[i]);
	return 0;
}
"""
PROBE_FN_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include "asc_probe.h"
typedef uint64_t u64;
#define __iomem
#define cpu_relax() do { } while (0)
#define udelay(us) do { } while (0)
#define div64_u64(a, b) ((a) / (b))
/* A page whose reads follow a script (its last value from then on); the clock ticks once a look, 1000 to the second unless a case says otherwise. */
static unsigned char page[0x100];
static u64 m1_tps = 1000, fake_now;
static const u32 *seq;
static unsigned int nseq, reads, said;
static u64 m1_now(void) { return fake_now++; }
static u32 readl(void *p) { unsigned int i = reads++; (void)p; return seq[i < nseq ? i : nseq - 1]; }
static void pr_info(const char *fmt, ...) { va_list ap; said++; va_start(ap, fmt); printf("log "); vprintf(fmt, ap); va_end(ap); }
"""
PROBE_FN_MAIN = r"""
int main(void)
{
	static const u32 r0[] = { 2 }, r1[] = { 0, 0, 0x80000002 }, r2[] = { 0x80000000 }, r3[] = { 0xfffffffd }, r4[] = { 0x00000003 };
	/* The last three: no poll; a clock too slow for the 100 ms to end the poll, so B2's count does; and no poll with bit 1 set. */
	static const struct { const u32 *s; unsigned int n; u32 ps; bool poll; u64 tps; } c[] = {
		{ r0, 1, 0x040002ff, true, 1000 }, { r1, 3, 0x0f0000ff, true, 1000 }, { r2, 1, 0x1f0f03ff, true, 1000 },
		{ r3, 1, 0x000000f0, true, 1000 }, { r4, 1, 0x000000f0, true, 1000 }, { r0, 1, 0x1400024f, true, 1000 }, { r0, 1, 0, true, 1000 },
		{ r0, 1, 0x000000e0, true, 1000 }, { r2, 1, 0x1f0f03ff, false, 1000 }, { r2, 1, 0x1f0f03ff, true, 10000000 },
		{ r0, 1, 0x040002ff, false, 1000 },
	};
	unsigned int i;

	for (i = 0; i < sizeof(c) / sizeof(c[0]); i++) {
		int ret;

		seq = c[i].s; nseq = c[i].n; reads = 0; said = 0; m1_tps = c[i].tps;
		ret = m1_probe_word("WHO", "WHEN", page + 0x10, c[i].ps, c[i].poll);
		printf("word %u ret %d reads %u said %u\n", i, ret, reads, said);
	}
	return 0;
}
"""


def probe_tests(g, src, dec):
    """probe=1 (e1-hook/probe): asc_probe.h against an independent reading of it; the function that reads a word, compiled as it
    stands; the guards that keep a load with probe=1 from ever replaying; the probe's own writes; and its bytes through m1-crumbs.
    dec(stage, parked, left, down) runs m1-crumbs on a record."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return subprocess.run([os.path.join(tmp, name)], capture_output=True, text=True, check=True).stdout.splitlines()

        hdr = [line.split() for line in build("phdr", PROBE_HDR)]
        fn = build("pfn", PROBE_FN_PRELUDE + cut("static bool m1_probe_word(", "\n}\n") + PROBE_FN_MAIN)
        defs = "".join(line + "\n" for line in src.splitlines()
                       if re.match(r"#define (M1_PS_SIO|M1_PS_SIO_CPU|M1_PS_UART_P|M1_PS_SPI_P|M1_PS_ANS2|M1_PS_PMP_RO|M1_POS_GATE44|M1_POS_GATE40|"
                                   r"M1_ASC_CPU_CONTROL|M1_ASC_CPU_STATUS|M1_ASC_CPU_RUN|M1_ASC_IDLE|M1_SIO_ENTRY|M1_PMU_ALARM\w*|M1_PMU_RTC|"
                                   r"M1_ALARM_ON)\s", line))
        ran = build("prun", PROBE_RUN_PRELUDE + defs + "static bool alarm = true;\n" + cut("static bool m1_pmu_ok(bool write,", "\n}\n")
                    + PROBE_RUN_MODEL + cut("static struct {\n\tbool sio_run,", "} m1_pr;\n")
                    + cut("static bool m1_probe_word(", "\n}\n") + cut("static void m1_probe_at(u8 step)", "\n}\n")
                    + cut("static bool m1_sio_raise(void)", "\n}\n") + cut("static void m1_probe_prepare(void)\n{", "\n}\n") + cut("static void m1_probe_late(void)", "\n}\n") + PROBE_RUN_MAIN)

    consts = [int(x, 16) for x in hdr[0][1:]]
    check(consts == [0x39bc08000, 0x38f408000, 0x28ec08000, 2, 0x0f00030f, 0x0e, 0x80] and g["PROBE_STAGE"] == 0x0e and g["PROBE_MARK"] == 0x80,
          "asc_probe.h: the three words B2 polls, bit 1, B2's value for ANS2, stage 0x0e; m1-crumbs has the same stage and mark", str(hdr[0]))
    used = set(g["STAGES"]) | set(g["OBSERVE_STAGES"]) | set(g["LATE"]) | set(g["HOLD"]) | set(g["HOLD_TEXT"]) | set(g["LATE_TEXT"]) | set(range(0x21, 0x28)) \
        | set(g["POSITION"]) | set(g["DISP_STAGES"]) | set(g["SIO_STOP_STAGES"]) | set(g["ALARM_STAGES"]) | set(g["OBSERVED"]) | set(g["FAB_STAGES"]) | {0x70, 0x71, 0x72, 0x7e, 0x7f}
    check(0x0e not in used and 0x8e not in used and not re.search(r"m1_stage\(0x0e\)|= 0x0e;", src),
          "the probe's stage is no run's, with or without pmp_out=1's bit", str(sorted(hex(x) for x in used)))
    on = {int(r[1], 16): int(r[2]) for r in hdr if r[0] == "on"}
    check(len(on) == 10 and all(v == ((k >> 4 & 0xf) == 0xf) for k, v in on.items()), "asc_probe.h: a domain is on when its power word's bits 7:4 read f", str(on))
    bad = [r for r in hdr if r[0] in ("sio", "pmp") and int(r[2], 16) != (0x80 | int(r[1]))] + \
          [r for r in hdr if r[0] == "ans" and int(r[2], 16) != (0x80 | int(r[1]))]
    check(len(hdr) == 3 + 10 + 128 + 128 + 128 + 256 + 1 and not bad,
          "asc_probe.h's three bytes: bit 7 the mark, and each argument its own bit, in the order the header's comment gives", str(bad[:4]))
    check(hdr[1] == ["more", "255", "b00", "ff", "6010"] and hdr[2] == ["at"] + [str(i) for i in range(22)]
          and len(g["PROBE_AT"]) == 22 and g["PROBE_DONE"] == 21 and (0x6010, 7) in g["CRUMBS"],
          "asc_probe.h: B2's 255 tries; BUSY and the two flags left out of a write-back; the step goes to 0x6010, which --clear zeroes; 22"
          " steps, numbered as m1-crumbs has them", str(hdr[1:3]))
    # What a byte says of the read in the state B2 finds: the header's three functions, m1-crumbs' one, and a reading of the comment.
    word = {-1: "NOT READ", 0: "clear", 1: "SET"}
    num = {None: -1, False: 0, True: 1}
    finals = {int(r[1]): ([int(x) for x in r[2:5]], " ".join(r[5:])) for r in hdr if r[0] == "final"}
    mine = {v: [(-1 if not v & 0x80 or not v & 0x40 else v >> 3 & 1), (-1 if not v & 0x80 or not v & 0x40 or not v & 0x20 else v >> 3 & 1),
                (-1 if not v & 0x80 or not v & 0x0a else v >> 2 & 1)] for v in range(256)}
    bad = [v for v in range(256) if finals[v][0] != mine[v] or finals[v][1] != " ".join(word[x] for x in mine[v])
           or [num[g["probe_final"](k, v)] for k in ("SIO", "PMP", "ANS")] != mine[v]]
    check(len(finals) == 256 and not bad and hdr[-1] == ["str", "NOT", "READ|clear|SET"],
          "the read in the state B2 finds, for every byte: not read unless the byte landed and says that read was made (for the PMP, with RUN"
          " cleared too); asc_probe.h's three functions and m1-crumbs' agree", str([(hex(v), finals[v], mine[v]) for v in bad[:3]]))

    # m1_probe_prepare() and m1_probe_late() as they stand, against a model of the registers: twelve scenarios.
    cases, cur = [], None
    for line in ran:
        if line.startswith("case "):
            cur = {"prep": [], "late": [], "at": "prep"}
            cases.append(cur)
        elif line == "noirq":
            cur["at"] = "late"
        else:
            cur[cur["at"]].append(line)
    stray = [line for line in ran if line.split()[0] in ("FAULT", "STRAY", "NEVER")]
    check(len(cases) == 16 and not stray,
          "in none of sixteen scenarios does the probe read a coprocessor's word while its domain's actual state isn't f, write ps_pmp, touch"
          " a register outside its own, or write a PMU byte the module's own gate refuses", "\n".join(stray[:6]))

    def ops(c, at, marks=True):
        return [line for line in c[at] if line.split()[0] in (("w", "idle", "kwrite", "at") if marks else ("w", "idle", "kwrite"))]

    def marks(c, at):
        return [int(line.split()[1]) for line in c[at] if line.startswith("at ")]

    def summary(c):
        s = [line for line in c["late"] if line.startswith("log probe: SUMMARY: ")]
        return s[0][len("log probe: SUMMARY: "):] if len(s) == 1 else ""

    def kbytes(c):
        k = [line.split() for line in c["late"] if line.startswith("kwrite 6005 ")]
        return [int(x, 16) for x in k[0][2:5]] if len(k) == 1 else None

    good_prep = ["kwrite 6004 0e ret 0", "at 1", "w SIO_CPU 0400024f", "at 2", "at 3", "at 4", "at 5", "idle SIO 0", "at 6", "at 7",
                 "w SIO_CTRL 00000000", "at 8", "at 9"]
    good_late = ["at 10", "at 11", "w ANS2 0f00030f", "at 12", "at 13", "w ANS2 00000000", "at 14", "at 15", "idle PMP 0", "at 16", "at 17",
                 "w PMP_CTRL 00000000", "at 18", "at 19", "w SIO 0f00000f", "w UART_P 0000000f", "w SPI_P 0000000f", "w SIO_CPU 0400024f", "at 20",
                 "at 21", "kwrite 6005 ff ff fd ret 0"]
    c = cases[0]
    check(ops(c, "prep") == good_prep and ops(c, "late") == good_late
          and summary(c) == "bit 1 in the state B2 finds: SIO SET, ANS SET, PMP SET; bytes ff ff fd, written to the PMU: 0"
          and c["late"][-1] == "end ANS2 00000000 SIO_CTRL 00000000 PMP_CTRL 00000000" and c["prep"][-1] == "mreads 3 1 1"
          and c["late"][-2] == "mreads 4 2 4",
          "the probe as it's meant to go: the stage, then SIO_CPU fully on and SIO stopped at prepare; after noirq ANS2 up and back as found,"
          " the PMP stopped, SIO raised; every word read at each step, each step marked before it's taken; the bytes last",
          "\n".join(c["prep"] + c["late"]))
    # The mark before each thing the probe does to the hardware is that thing's, in every scenario: a Mac that goes down there says so.
    before = {"idle SIO": 5, "w SIO_CTRL": 7, "w ANS2 0f00030f": 11, "idle PMP": 15, "w PMP_CTRL": 17, "w SIO ": 19, "w UART_P": 19, "w SPI_P": 19}
    bad = []
    for i, c in enumerate(cases):
        for at in ("prep", "late"):
            last = None
            for line in ops(c, at):
                if line.startswith("at "):
                    last = int(line.split()[1])
                elif not line.startswith("kwrite"):
                    want_at = next((v for k, v in before.items() if line.startswith(k)), None)
                    if want_at is None:
                        want_at = (1 if at == "prep" else 19) if line.startswith("w SIO_CPU") else 13 if line.startswith("w ANS2 ") else -1
                    if last != want_at:
                        bad.append((i, at, line, last))
    check(not bad and all(marks(c, "prep")[:1] == [1] and marks(c, "prep")[-1:] == [9] and marks(c, "late")[:1] == [10]
                          and marks(c, "late")[-1:] == [21] and marks(c, "prep") + marks(c, "late") == sorted(marks(c, "prep") + marks(c, "late"))
                          for c in cases)
          and marks(cases[5], "prep") == [1, 2, 3, 4, 5, 6, 9] and marks(cases[2], "late") == [10, 12, 14, 15, 16, 17, 18, 19, 20, 21]
          and marks(cases[3], "late") == [10, 11, 12, 13, 14, 15, 16, 19, 20, 21] and ops(c, "prep")[0] == "kwrite 6004 0e ret 0",
          "every write and idle pair comes right after its own step's mark, the marks only rise, and a step that's skipped isn't marked",
          str(bad[:4]))
    # ANS2 in every scenario: written twice, B2's value and then the value found less BUSY and the two flags, or not at all if it read
    # on; and it ends with the target and actual state it was found with, unless the model won't let it down.
    found = {i: 0x0f0000ff if i == 2 else 0x04000b00 if i == 12 else 0x00000300 for i in range(len(cases))}
    bad = [i for i, c in enumerate(cases)
           if [line for line in ops(c, "late", False) if line.startswith("w ANS2 ")] != ([] if i == 2 else ["w ANS2 0f00030f", f"w ANS2 {found[i] & ~0xb00:08x}"])
           or (i != 13 and (int(c["late"][-1].split()[2], 16) ^ found[i]) & 0x0f0000ff)
           or any(line.startswith("w ANS2 ") for line in c["prep"])]
    check(not bad and cases[13]["late"][-1].startswith("end ANS2 000000f0 ")
          and any("ANS2 found 00000300, written back 00000000: it reads 000000f0, its target and actual state NOT as found within 10 ms" in line
                  for line in cases[13]["late"])
          and any("ANS2 found 04000b00, written back 04000000: it reads 04000800, its target and actual state as found" in line for line in cases[12]["late"]),
          "in every scenario ANS2 is raised and written back as found, less the bits the kernel never writes back, or, where it read on, never"
          " written; and a write-back that doesn't take is said", str(bad))
    want = {1: ("SIO SET, ANS NOT READ, PMP SET", [0xff, 0xff, 0xf1]), 2: ("SIO SET, ANS SET, PMP SET", [0xff, 0xff, 0xf7]),
            3: ("SIO SET, ANS SET, PMP NOT READ", [0xff, 0xc7, 0xfd]), 4: ("SIO SET, ANS SET, PMP NOT READ", [0xff, 0xd7, 0xfd]),
            5: ("SIO SET, ANS SET, PMP SET", [0xcb, 0xff, 0xfd]), 6: ("SIO NOT READ, ANS SET, PMP SET", [0xb7, 0xff, 0xfd]),
            7: ("SIO SET, ANS SET, PMP NOT READ", [0xff, 0xb0, 0xfd]), 8: ("SIO SET, ANS SET, PMP SET", [0xff, 0xff, 0xfd]),
            9: ("SIO clear, ANS clear, PMP clear", [0xf7, 0xf1, 0xf9]), 10: ("SIO SET, ANS SET, PMP SET", [0xf8, 0xff, 0xfd]),
            11: ("SIO SET, ANS SET, PMP NOT READ", [0xcb, 0xc7, 0xfd]), 12: ("SIO SET, ANS SET, PMP SET", [0xff, 0xff, 0xfd]),
            13: ("SIO SET, ANS SET, PMP SET", [0xff, 0xff, 0xbd]), 14: ("SIO SET, ANS SET, PMP NOT READ", [0xff, 0xbc, 0xfd]),
            15: ("SIO SET, ANS SET, PMP NOT READ", [0xff, 0xb7, 0xfd])}
    bad = [(i, summary(cases[i]), kbytes(cases[i])) for i, (s, b) in want.items()
           if kbytes(cases[i]) != b or not summary(cases[i]).startswith("bit 1 in the state B2 finds: " + s + "; bytes " + " ".join(f"{x:02x}" for x in b))]
    check(not bad, "the SUMMARY line and the bytes in the fifteen scenarios where something goes another way: a read that wasn't made says NOT"
          " READ, never clear (Pullfrog on #76), and the PMP's last read counts only if ps_pmp read on for every read of its word", str(bad[:3]))
    bad = [i for i, c in enumerate(cases) if kbytes(c) is None
           or summary(c).split(";")[0] != "bit 1 in the state B2 finds: " + ", ".join(
               f"{k} {word[num[g['probe_final'](k, kbytes(c)[j])]]}" for k, j in (("SIO", 0), ("ANS", 2), ("PMP", 1)))]
    check(not bad, "in every scenario the SUMMARY line says of the bytes what m1-crumbs would", str(bad))
    # The steps that depend on the one before: RUN is cleared only after an idle pair that worked (for the PMP, and read idle), and the
    # stage goes out only if the bytes did.
    check(ops(cases[5], "prep", False) == ["kwrite 6004 0e ret 0", "w SIO_CPU 0400024f", "idle SIO -62"]
          and ops(cases[11], "prep", False) == ["kwrite 6004 0e ret 0", "w SIO_CPU 0400024f"]
          and not any("PMP_CTRL" in line for i in (3, 4, 11) for line in ops(cases[i], "late"))
          and "idle PMP -62" in ops(cases[3], "late") and not any(line.startswith("idle") for line in ops(cases[11], "late"))
          and ops(cases[8], "late")[-1] == "kwrite 6005 ff ff fd ret -5" and summary(cases[8]).endswith("written to the PMU: -5")
          and all(c["late"][-1].endswith("SIO_CTRL 00000010 PMP_CTRL 00000000") for c in (cases[5],))
          and cases[3]["late"][-1].endswith("PMP_CTRL 00000010") and cases[4]["late"][-1].endswith("PMP_CTRL 00000010"),
          "SIO's RUN is cleared only after an idle pair that returned 0, the PMP's only if it then read idle; neither idle pair is sent to an RTKit"
          " that isn't running; bytes the PMU didn't take are said", "\n".join(ops(cases[5], "prep") + ops(cases[3], "late")))
    # What isn't read: nothing of SIO's at prepare if SIO_CPU didn't come up, nothing of it after noirq if it didn't then; nothing of the
    # PMP's if ps_pmp doesn't read on; ANS's after noirq only once ANS2 reads on.
    check(cases[10]["prep"][-1] == "mreads 0 1 1" and cases[6]["late"][-2].split()[1] == cases[6]["prep"][-1].split()[1]
          and cases[7]["late"][-2].split()[3] == "0" and cases[1]["late"][-2].split()[2] == cases[1]["prep"][-1].split()[2] == "1"
          and cases[9]["late"][-2] == "mreads 103 101 301",
          "a word is left unread wherever its domain doesn't read on, and read for 100 ms and no longer where bit 1 stays clear",
          " | ".join(cases[i]["prep"][-1] + " / " + cases[i]["late"][-2] for i in (1, 6, 7, 9, 10)))

    # m1_probe_word(): a word is read only if its domain reads on; then until bit 1 shows or 100 ms have gone; one line of log either way.
    word = [line for line in fn if line.startswith("word ")]
    # Case 5 is SIO_CPU as it reads awake, 0x1400024f: target f but actual 4, its clock gating itself. Not read.
    check(word == ["word 0 ret 1 reads 1 said 1", "word 1 ret 1 reads 3 said 1", "word 2 ret 0 reads 100 said 1", "word 3 ret 0 reads 100 said 1",
                   "word 4 ret 1 reads 1 said 1", "word 5 ret 0 reads 0 said 1", "word 6 ret 0 reads 0 said 1", "word 7 ret 0 reads 0 said 1",
                   "word 8 ret 0 reads 1 said 1", "word 9 ret 0 reads 255 said 1", "word 10 ret 1 reads 1 said 1"],
          "m1_probe_word(): no read of a domain whose actual state isn't f; bit 1 set ends it; bit 1 clear is read for 100 ms and no longer, and"
          " never more than B2's 255 times; without poll, once; one log line each", "\n".join(fn))
    logs = [line for line in fn if line.startswith("log ")]
    check("log probe: WHO, WHEN: power word 0f0000ff; ASC+0x8000 read 00000000 at first and 80000002 after 3 reads in " in logs[1] and logs[1].endswith("bit 1 SET")
          and logs[2].endswith("bit 1 clear") and "its power word reads 1400024f, actual not f, so its word isn't read" in logs[5],
          "m1_probe_word() logs the power word, the first and the last value read in full, and what bit 1 was", "\n".join(logs))

    # With probe=1 nothing replays: e1_replay() returns before it marks itself run, prepare takes neither of the replay's steps,
    # and prepare_late fails the suspend whatever else happens.
    replay = cut("static bool e1_replay(void)", "\n}\n")
    prep = cut("static int e1_prepare(void)", "\n}\n")
    late = cut("static int e1_prepare_late(void)", "\n}\n")
    check(in_order_of(replay, ["\tif (probe) {\n\t\tpr_info(", "\t\treturn false;\n\t}\n\treplay_done = 1;\n", "device_trylock(dev)", "apple_smc_write_u32("])
          and replay.index("if (probe)") < replay.index("replay_done = 1;") < replay.index("m1_pmu_start()"),
          "e1_replay() returns at once with probe=1, before it marks the replay run and before anything is written", replay[:900])
    check(in_order_of(prep, ["\tif (probe) {\n\t\tif (n == 1 && !look)\n\t\t\tm1_probe_prepare();\n\t\treturn 0;\n\t}\n", "m1_dcp_shutdown();", "m1_sio_fw_stop();"]),
          "prepare with probe=1 takes the probe's steps at the first suspend and never the replay's (the DCPs' shutdown, SIO's stop)", prep)
    check(in_order_of(late, ["\tif (probe) {\n\t\tbool same = n == 1 && READ_ONCE(prepare_calls) == 1;\n\n", "\t\tif (look && n == 1) {\n\t\t\tm1_look(\"after noirq\", 0);\n",
                             "\t\t} else if (!look && same) {\n\t\t\tm1_probe_late();\n\t\t}\n\t\tpr_info(",
                             "\t\treturn -EIO;\n\t}\n\tif (!(replay & 8)) {", "e1_replay()"])
          and late.count("m1_probe_late()") == 1,
          "prepare_late with probe=1 runs the probe's second half only in the suspend whose prepare ran the first, and fails every suspend with -EIO, "
          "before any way to the replay", late)
    trace = cut("static void e1_suspend_resume(void *data", "\n}\n")
    check("\tif (n > 1 && !probe && !READ_ONCE(replay_done)) {\n\t\tWRITE_ONCE(replay_done, 2);" in trace and trace.count("WRITE_ONCE(replay_done, 2)") == 1,
          "a second suspend with probe=1 calls nothing off: replay_done stays 0, which is what e1-hook/probe shows", trace)
    pfn = src[src.index("static bool m1_probe_word(const char *who"):src.index("/* look=1: the clock generators' blocks and the two pages")]
    check("pr_emerg(" not in pfn and pfn.count("pr_info(") == 11 and "pr_emerg(" not in late[late.index("if (probe)"):late.index("if (!(replay & 8))")],
          "the probe's lines are pr_info: journald doesn't broadcast them over the screen the launcher prints them on", "")
    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    check("static bool probe;\nmodule_param(probe, bool, 0444);\n" in src and not re.search(r"\bprobe\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (probe && (!wd1_mark || !pmp_stop)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n", "\treturn 0;\n\nbad_wait:"])
          and "\tif (probe) {\n\t\tint ret = m1_probe_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n" in src,
          "probe is off unless asked for, read-only once loaded, loads only on the latest recipe, and the load returns its refusal")

    # What the probe writes: RUN on SIO and on the PMP, the recipe's own; ANS2 up, B2's value, and back as found; SIO_CPU's table word.
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\([^;]*;"
    pprep = cut("static void m1_probe_prepare(void)\n{", "\n}\n")
    plate = cut("static void m1_probe_late(void)", "\n}\n")
    check(re.findall(wr, pprep) == ["writel_relaxed(e1_post_slpa[M1_SIO_ENTRY].val, post_map[M1_SIO_ENTRY]);",
                                    "writel(ctrl & ~M1_ASC_CPU_RUN, m1_sio_asc + M1_ASC_CPU_CONTROL);"]
          and re.findall(wr, plate) == ["writel(ASC_ANS2_UP, m1_sio + M1_PS_ANS2);", "writel(ps & ~ASC_PS_NOT_WRITTEN, m1_sio + M1_PS_ANS2);",
                                        "writel(ctrl & ~M1_ASC_CPU_RUN, m1_pmp_asc + M1_ASC_CPU_CONTROL);",
                                        "writel_relaxed(e1_post_slpa[M1_SIO_ENTRY].val, post_map[M1_SIO_ENTRY]);"]
          and not re.findall(wr, cut("static bool m1_probe_word(", "\n}\n")) and not re.findall(wr, cut("static int __init m1_probe_init(void)", "\n}\n"))
          and not re.findall(wr, cut("static void m1_probe_at(u8 step)", "\n}\n"))
          and plate.count("m1_sio_raise()") == 1 and "\traised = m1_sio_raise();\n\tpr_info(" in plate
          and (pprep + plate).count("m1_kwrite(") == 2 and "\tm1_kwrite(0x6004, &stage, 1);\n\tm1_probe_at(ASC_AT_PREPARE);\n" in pprep
          and "\tret = m1_kwrite(0x6005, b, 3);\n" in plate and "apple_smc_write" not in pprep + plate
          and "m1_ans_mbox, up, true);\n\tm1_pr.ans_back = true;\n\tif (off) {" in plate
          and "\tif (!ret && !(st & M1_ASC_IDLE))\n\t\tst = readl(m1_pmp_asc + M1_ASC_CPU_STATUS);\n\tpr_info(\"probe: PMP: its idle pair" in plate
          and "E1_S2R_COMMIT" not in pprep + plate and "m1_after_slpa" not in pprep + plate and "post_map[ARRAY_SIZE" not in pprep + plate,
          "the probe's MMIO writes are six: SIO_CPU's table word at prepare and after noirq, SIO's RUN, ANS2 up and back, the PMP's RUN; no key, no commit",
          "\n".join(re.findall(wr, pprep) + re.findall(wr, plate)))
    pinit = cut("static int __init m1_probe_init(void)", "\n}\n")
    check("#define M1_PS_ANS2\t\t0x3b8" in src and "#define M1_PS_PMP_RO\t\t0x2d8" in src
          and not re.search(r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\([^;]*M1_PS_PMP_RO", code_only)
          and "static_assert(M1_PS_SIO_PAGE + M1_PS_ANS2 == 0x28e5803b8ULL && M1_PMGR_PS + M1_PS_PMP_RO == 0x28e0802d8ULL);\n" in src
          and "static_assert(M1_SIO_ASC + 0x8000 == ASC_SIO_MBOX && M1_PMP_ASC + 0x8000 == ASC_PMP_MBOX && ASC_READY == BIT(1));\n" in src
          and in_order_of(pinit, ["\tif (w->addr != M1_PS_SIO_PAGE + M1_PS_SIO_CPU || w->width != 4 || (w->val & 0xf) != 0xf) {\n\t\tpr_err(",
                                  "\t\treturn -EINVAL;\n\t}\n\tif (m1_never(M1_PS_SIO_PAGE + M1_PS_ANS2, 1)) {\n\t\tpr_err(", "\t\treturn -EPERM;\n\t}\n",
                                  "\tm1_sio_mbox = ioremap_np(ASC_SIO_MBOX, PAGE_SIZE);\n\tm1_ans_mbox = ioremap_np(ASC_ANS_MBOX, PAGE_SIZE);\n"
                                  "\tm1_pmp_mbox = ioremap_np(ASC_PMP_MBOX, PAGE_SIZE);\n\tif (!m1_sio_mbox || !m1_ans_mbox || !m1_pmp_mbox) {\n",
                                  "\t\treturn -ENOMEM;\n\t}\n", "m1_probe_word("])
          and pinit.count("m1_probe_word(") == 3 and pinit.count(", false);") == 3 and ", true)" not in pinit
          and "\tps = readl(m1_sio + M1_PS_ANS2);\n\tm1_pr.ans_load_read = asc_ps_on(ps);\n\tm1_pr.ans_load = m1_probe_word(\"ANS\", " in pinit
          and "m1_ans_mbox, ps, false);" in pinit and "const struct e1_mmio_write *w = &e1_post_slpa[M1_SIO_ENTRY];" in pinit,
          "the load: ANS2's and ps_pmp's addresses and the three mailbox pages pinned; refused unless the table's entry 61 raises SIO_CPU and ANS2 is"
          " off the never-write list; each mapping its own coprocessor's; one read each with no poll, ANS's kept for its byte", pinit)

    # The bytes through m1-crumbs: stage 0x71 is the probe's, not a run's.
    rc, out, err = dec(0x0e, 0xff, 0xff, 0xf7, 21)
    check(rc == 0 and "stage 0x6004 = 0x0e: probe=1 (e1-hook/probe), not a run: nothing replayed and nothing was committed." in out
          and "0x6010 = 0x15: every step taken: the three bytes written\n" in out
          and "  awake, at the module's load, its firmware running: SET\n" in out
          and "  at prepare, its firmware shut down by the NVMe driver's suspend, ANS2 still on: SET\n" in out and "did NOT read as found" not in out
          and "0x6005 = 0xff: SIO, bit 1 of its ASC + 0x8000 (0x39bc08000), which B2's op 51 waits for:\n" in out
          and "  after noirq, its parents and SIO_CPU raised again, the state B2's op 51 finds: SET\n" in out
          and "  after RUN was cleared, the state B2's op 159 finds: SET\n" in out and "  after noirq, ANS2 still on, the state B2's op 151 finds: SET\n" in out
          and ("  so: bit 1 read SET for all three in the state the recipe leaves: that makes B2's three coprocessor waits less likely to be where the pair"
               " stops, and doesn't exclude them (B2 polls later, after its own raises)\n") in out
          and "parked (0x6005" not in out and "the SoC restarted" not in out and "PMU alarm" not in out,
          "the probe's bytes, all set: decoded as the probe's, with no run's lines", out + err)
    # 0x71 is the observer's stage under pmgr_late, as it was: the probe took another number for that reason.
    rc, out, err = dec(0x71, 0xff, 0x04, 0x1e, 0)
    check(rc == 0 and "probe=1" not in out, "stage 0x71 is still the observer's", out + err)
    rc, out, err = dec(0x0e, 0xf7, 0xf7, 0x8d, 21)
    check(rc == 0 and "the state B2's op 51 finds: clear\n" in out and "the state B2's op 159 finds: clear\n" in out
          and "ANS2 off, raised as B2's op 149 raises it, the state B2's op 151 finds: SET\n" in out
          and "  awake, at the module's load, its firmware running: not read\n" in out
          and "  ANS2's target and actual state did NOT read as found within 10 ms of being written back\n" in out
          and "  so: in the state the recipe leaves, bit 1 read CLEAR for SIO, PMP, for 100 ms. That's a lead for where B2 stops, not its location:" in out,
          "the probe's bytes with SIO's and the PMP's last reads clear: both named, as a lead; ANS's load read not made and its write-back not"
          " taken: both said", out + err)
    # A Mac that went down inside the probe: the stage and a step, and no bytes. And one whose bytes' write alone was lost.
    rc, out, err = dec(0x0e, 0, 0, 0, 12)
    check(rc == 0 and "0x6010 = 0x0c: THE PROBE BEGAN AND DIDN'T FINISH. The last step it marked: ANS's word read. The Mac went down in that step, or"
          " after it and before the next was marked" in out and out.count("byte didn't land") == 3 and "nothing decided for any of them" in out
          and "SET" not in out.split("0x6010 = ")[1],
          "stage 0x0e with a step and no bytes: the probe began and didn't finish, the step is named, and nothing is read from the bytes", out + err)
    rc, out, err = dec(0x0e, 0, 0, 0, 21)
    check(rc == 0 and "0x6010 = 0x15: every step was taken, and the write of the three bytes didn't land, or not all of it\n" in out
          and "DIDN'T FINISH" not in out, "stage 0x0e with the last step and no bytes: every step taken, the bytes lost", out + err)
    rc, out, err = dec(0x0e, 0, 0, 0, 0)
    check(rc == 0 and "0x6010 = 0x00: THE PROBE BEGAN AND DIDN'T FINISH. The last step it marked: nothing marked." in out,
          "stage 0x0e with no step: said", out + err)
    check(g["probe_at_line"](22, False).startswith("0x6010 = 0x16: THE PROBE BEGAN AND DIDN'T FINISH. The last step it marked: not a step's number.")
          and g["probe_at_line"](9, True) == "0x6010 = 0x09: prepare done: the kernel's noirq pass: NOT the last step's number, though the bytes landed",
          "a step number past the last, and bytes landed under a step short of it: both said")
    wrong = []
    for sio in range(256):
        for pmp in (0x00, 0x7f, 0x80, 0x88, 0xa8, 0xe8, 0xe0, 0xc8, 0xff, 0xf7):
            for ans in (0x00, 0x80, 0x82, 0x86, 0x88, 0x8c, 0x84, 0x8f):
                lines = g["probe_lines"](sio, pmp, ans, 21)
                so = [line for line in lines if line.startswith("  so: ")]
                # Read here from asc_probe.h's comment: the last read counts if the domain was on for it (and, for the PMP, RUN was cleared).
                f = {"SIO": (bool(sio & 8) if sio & 0x40 else None) if sio & 0x80 else None,
                     "PMP": (bool(pmp & 8) if pmp & 0x40 and pmp & 0x20 else None) if pmp & 0x80 else None,
                     "ANS": (bool(ans & 4) if ans & 0x0a else None) if ans & 0x80 else None}
                clear = [k for k in ("SIO", "PMP", "ANS") if f[k] is False]
                unread = [k for k in ("SIO", "ANS", "PMP") if f[k] is None]
                ok = (len(so) == 1 and ("bit 1 read CLEAR for" in so[0]) == bool(clear)
                      and all((k in so[0].split("for 100 ms")[0]) == (k in clear) for k in f if clear)
                      and ("bit 1 read SET for all three" in so[0]) == (not clear and not unread)
                      and ("nothing decided" in so[0]) == (not clear and bool(unread))
                      and ("didn't land" in "".join(lines)) == (not (sio & pmp & ans & 0x80)))
                if not ok:
                    wrong.append((hex(sio), hex(pmp), hex(ans), so))
    check(not wrong, "the probe's verdict, for every SIO byte against a spread of the other two: clear is named only for a last read that was made and"
          " read clear, and all three set only when all three were read", str(wrong[:2]))


LOOK_HDR = r"""
#include <stdint.h>
#include <stdio.h>
typedef uint16_t u16;
#include "look.h"
#define N(a) (sizeof(a) / sizeof((a)[0]))
int main(void)
{
	static const u16 aon[] = LOOK_AON_OFFS, aonw[] = LOOK_AON_W_OFFS, ps[] = LOOK_PS_OFFS, ga[] = LOOK_GATE_A_OFFS, gb[] = LOOK_GATE_B_OFFS;
	unsigned int i;

	printf("consts %x %x %llx %llx %x %x %x %llx %llx %llx %llx %x %d %x\n", LOOK_STAGE, LOOK_AT, LOOK_AON_PAGE, LOOK_SIO_PAGE, LOOK_SIO_FIRST,
	       LOOK_SIO_LAST, LOOK_SIO_ANS2, LOOK_PS_PAGE, LOOK_GATE_A, LOOK_GATE_B, LOOK_CLKGEN, LOOK_CLKGEN_STEP, LOOK_CLKGEN_N, LOOK_LOAD);
	printf("at %d %d %d %d %d %d %d %d %d %d %d\n", LOOK_AT_NONE, LOOK_AT_PSTATE, LOOK_AT_AON, LOOK_AT_POWER, LOOK_AT_GATES, LOOK_AT_CLKGEN, LOOK_AT_AON_W,
	       LOOK_AT_PRE_CLK, LOOK_AT_PRE_A, LOOK_AT_PRE_B, LOOK_AT_DONE);
	printf("pre %llx %x %llx %llx %x %x %x\n", LOOK_PRE_CLK, LOOK_PRE_CLK_OFF, LOOK_PRE_A, LOOK_PRE_B, LOOK_PRE_FIRST, LOOK_PRE_A_LAST, LOOK_PRE_B_LAST);
	printf("aon");
	for (i = 0; i < N(aon); i++)
		printf(" %x", aon[i]);
	for (i = 0; i < N(aonw); i++)
		printf(" %x", aonw[i]);
	printf("\nps");
	for (i = 0; i < N(ps); i++)
		printf(" %x", ps[i]);
	printf("\nga");
	for (i = 0; i < N(ga); i++)
		printf(" %x", ga[i]);
	printf("\ngb");
	for (i = 0; i < N(gb); i++)
		printf(" %x", gb[i]);
	printf("\n");
	return 0;
}
"""
LOOK_RUN_PRELUDE = r"""
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#include "look.h"
#include "gate_test.h"
#include "pre_test.h"
#define __iomem
#define __init
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define scnprintf snprintf
#define PAGE_SIZE 0x4000UL
#define EPERM 1
#define ENOMEM 12
#define ENODEV 19
"""
LOOK_RUN_MODEL = r"""
/*
 * The pages m1_look() reads, each a quarter of its real size, so that a read far out of place is a STRAY. A read
 * prints its physical address and returns a value made from it. There is no writel() here at all: a register
 * write in m1_look() doesn't compile. PMU writes go through the module's own gate.
 */
static unsigned char fab_page[0x1000], pos_page[0x1000], sio_page[0x1000], ps_page[0x1000], ga_page[0x1000], gb_page[0x1000],
	pc_page[0x1000], pa_page[0x1000], pb_page[0x1000], clk_pages[LOOK_CLKGEN_N * LOOK_CLKGEN_STEP];
/* All as a load starts with them: nothing mapped. main() sets what m1_init()'s earlier steps would have. */
static void *m1_pos, *m1_sio, *m1_ps, *m1_look_ga, *m1_look_gb, *m1_look_clk, *m1_look_pc, *m1_look_pa, *m1_look_pb;
static void *post_map[1];
static const struct { unsigned int idx; } m1_waits[1] = { { 0 } };
static void *m1_fab(void) { return post_map[m1_waits[0].idx]; }
static unsigned int maps, map_fail;
/* gate=1's check at load: each address it asks about is printed, and the one the scenario names is on the list. */
static bool gate, pre_test;
static u64 never_at;
static bool m1_never(u64 pa, unsigned int n)
{
	printf("never %llx %u\n", (unsigned long long)pa, n);
	return pa == never_at;
}
/* ioremap_np(): the page of each range the look may map, or NULL for the map_fail-th call. */
static void *ioremap_np(unsigned long long phys, unsigned long size)
{
	printf("map %llx %lx\n", phys, size);
	if (++maps == map_fail)
		return NULL;
	if (phys == LOOK_CLKGEN)
		return clk_pages;
	if (phys == LOOK_GATE_A)
		return ga_page;
	if (phys == LOOK_GATE_B)
		return gb_page;
	if (phys == 0x28e038000ULL)
		return pc_page;
	if (phys == 0x28e0a0000ULL)
		return pa_page;
	if (phys == 0x28e5b0000ULL)
		return pb_page;
	if (phys == LOOK_AON_PAGE)
		return pos_page;
	printf("STRAY map\n");
	return NULL;
}
static u32 readl(const void *p)
{
	static const struct { const unsigned char *base; unsigned long size; unsigned long long phys; } pg[] = {
		{ fab_page, sizeof(fab_page), 0x28e20c000ULL }, { pos_page, sizeof(pos_page), LOOK_AON_PAGE },
		{ sio_page, sizeof(sio_page), LOOK_SIO_PAGE }, { ps_page, sizeof(ps_page), LOOK_PS_PAGE }, { ga_page, sizeof(ga_page), LOOK_GATE_A },
		{ gb_page, sizeof(gb_page), LOOK_GATE_B }, { clk_pages, sizeof(clk_pages), LOOK_CLKGEN },
		{ pc_page, sizeof(pc_page), 0x28e038000ULL }, { pa_page, sizeof(pa_page), 0x28e0a0000ULL }, { pb_page, sizeof(pb_page), 0x28e5b0000ULL },
	};
	const unsigned char *q = p;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(pg); i++) {
		if (q >= pg[i].base && q < pg[i].base + pg[i].size) {
			unsigned long long a = pg[i].phys + (unsigned long)(q - pg[i].base);

			printf("r %llx\n", a);
			return (u32)((a ^ (a >> 7)) * 0x9e3779b1ULL);
		}
	}
	printf("STRAY read\n");
	return 0;
}
static int m1_kwrite(unsigned int addr, const u8 *b, unsigned int n)
{
	unsigned int i;

	if (!m1_pmu_ok(true, addr, b, n))
		printf("STRAY write to the PMU's %x, which m1_pmu_ok() refuses\n", addr);
	printf("k %x", addr);
	for (i = 0; i < n; i++)
		printf(" %02x", b[i]);
	printf("\n");
	return 0;
}
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("log "); vprintf(fmt, ap); va_end(ap); }
static void pr_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("err "); vprintf(fmt, ap); va_end(ap); }
"""
LOOK_RUN_MAIN = r"""
/* One load: the pointers as m1_init()'s earlier steps leave them in this scenario, then m1_look_init() as it stands. */
static void load(unsigned int n, bool pos, bool sio, bool fab, unsigned int fail)
{
	m1_pos = pos ? pos_page : NULL;
	m1_sio = sio ? sio_page : NULL;
	m1_ps = ps_page;
	post_map[0] = fab ? fab_page : NULL;
	m1_look_clk = m1_look_ga = m1_look_gb = m1_look_pc = m1_look_pa = m1_look_pb = NULL;
	maps = 0;
	map_fail = fail;
	printf("load %u\n", n);
	printf("ret %d\n", m1_look_init());
}

int main(void)
{
	load(0, false, true, true, 0);	/* the engine's page not mapped yet: the look maps it itself */
	load(1, true, true, true, 0);	/* mapped already, as m1_wd2_init() leaves it with the launcher's parameters */
	load(2, false, true, true, 2);	/* the second mapping fails */
	load(3, false, true, true, 7);	/* the engine's page doesn't map */
	load(4, true, false, true, 0);	/* SIO's page isn't there */
	load(5, true, true, false, 0);	/* the p-state page isn't there */
	load(6, true, true, true, 5);	/* the first of the two pages never read before doesn't map */
	gate = true;
	load(7, true, true, true, 0);	/* gate=1: its six words asked about, none on the list */
	never_at = 0x28e580268ULL;
	load(8, true, true, true, 0);	/* a UART's word on the list */
	never_at = 0x28e580160ULL;
	load(9, true, true, true, 0);	/* the lane's */
	never_at = 0;
	gate = false;
	printf("plain\n");
	m1_look("WHEN", 0);
	return 0;
}
"""


def look_tests(g, src, dec):
    """probe=1 look=1 (e1-hook/probe --look): look.h against an independent list of what's to be read; m1_look() as it stands
    against pages that say what was read, with no way to write a register; the places that call it; and its stage through
    m1-crumbs. dec(stage, parked, left, down, at) runs m1-crumbs on a record."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return subprocess.run([os.path.join(tmp, name)], capture_output=True, text=True, check=True).stdout.splitlines()

        hdr = [line.split() for line in build("lhdr", LOOK_HDR)]
        defs = "".join(line + "\n" for line in src.splitlines() if re.match(r"#define (M1_PMU_ALARM\w*|M1_PMU_RTC|M1_ALARM_ON)\s", line))
        defs_pos = [line for line in src.splitlines() if re.match(r"#define M1_POS_PAGE\s", line)]
        ran = build("lrun", LOOK_RUN_PRELUDE + defs + defs_pos[0] + "\nstatic bool alarm = true;\n" + cut("static bool m1_pmu_ok(bool write,", "\n}\n")
                    + LOOK_RUN_MODEL + cut("#define LOOK_PRE_ROW", "\n") + cut("static void m1_look_at(u8 group)", "\n}\n") + cut("static void m1_look(const char *when, u8 pass)", "\n}\n")
                    + cut("static void m1_pre_beside(const char *when, bool gates)", "\n}\n") + cut("static int __init m1_look_init(void)", "\n}\n") + LOOK_RUN_MAIN)

    # What's to be read, written out here from PLAN.md's list and not from the header: the three p-state words; six words of the
    # engine's page that its sequences read, and, last of all, the two they only write; SIO's page from SIO's own power word to
    # AES's, 8 apart, and ANS2's; seven in the first page; six gate words; fifteen clock generators' first two words. And, after all of
    # those, the 22 words B2 writes before its first handshake (its ops 8 to 29): one, then seven and fourteen that are 4 apart.
    pstate = [0x28e20c000, 0x28e20c400, 0x28e20c800]
    aon = [0x2922c0000 + o for o in (0x038, 0x03c, 0x040, 0x044, 0x10c, 0x110)]
    aonw = [0x2922c0008, 0x2922c0024]
    sio = [0x28e580000 + o for o in range(0x180, 0x2d8, 8)]
    ps = [0x28e080000 + o for o in (0x108, 0x110, 0x120, 0x2d8, 0x2e8, 0x2f0, 0x320)]
    gates = [0x28e5a0050, 0x28e5a0064, 0x28e5a030c, 0x28e09c310, 0x28e09c338, 0x28e09c34c]
    clk = [0x28e000000 + 0x2000 * i + o for i in range(15) for o in (0, 4)]
    pre_c = [0x28e03801c]
    pre_a = [0x28e0a0004, 0x28e0a0008, 0x28e0a000c, 0x28e0a0010, 0x28e0a0014, 0x28e0a0018, 0x28e0a001c]
    pre_b = [0x28e5b0004, 0x28e5b0008, 0x28e5b000c, 0x28e5b0010, 0x28e5b0014, 0x28e5b0018, 0x28e5b001c, 0x28e5b0020, 0x28e5b0024, 0x28e5b0028,
             0x28e5b002c, 0x28e5b0030, 0x28e5b0034, 0x28e5b0038]
    check(hdr[0] == ["consts", "f", "6010", "2922c0000", "28e580000", "180", "2d0", "3b8", "28e080000", "28e5a0000", "28e09c000", "28e000000", "2000", "15", "10"]
          and hdr[1] == ["at"] + [str(i) for i in (0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 7)] and hdr[2] == ["pre", "28e038000", "1c", "28e0a0000", "28e5b0000", "4", "1c", "38"]
          and [int(x, 16) for x in hdr[3][1:]] == [a - 0x2922c0000 for a in aon + aonw]
          and [int(x, 16) for x in hdr[4][1:]] == [a - 0x28e080000 for a in ps]
          and [int(x, 16) for x in hdr[5][1:] + hdr[6][1:]] == [a & 0xfff for a in gates] and len(sio) == 43
          and len(pre_c + pre_a + pre_b) == 22 and len(set(pre_c + pre_a + pre_b)) == 22
          and g["LOOK_STAGE"] == 0x0f and len(g["LOOK_AT"]) == 11 and g["LOOK_DONE"] == 7 and g["LOOK_LOAD"] == 0x10,
          "look.h: stage 0x0f, the group at 0x6010 with 0x10 for the load's look, eleven marks as m1-crumbs has them (7 still the last"
          " one, the new pages' 8 to 10), and the pages and offsets PLAN.md lists", str(hdr))
    used = set(g["STAGES"]) | set(g["OBSERVE_STAGES"]) | set(g["LATE"]) | set(g["HOLD"]) | set(g["HOLD_TEXT"]) | set(g["LATE_TEXT"]) | set(range(0x21, 0x28)) \
        | set(g["POSITION"]) | set(g["DISP_STAGES"]) | set(g["SIO_STOP_STAGES"]) | set(g["ALARM_STAGES"]) | set(g["OBSERVED"]) | set(g["FAB_STAGES"]) \
        | {0x70, 0x71, 0x72, 0x7e, 0x7f, g["PROBE_STAGE"]}
    check(0x0f not in used and 0x8f not in used and not re.search(r"m1_stage\(0x0f\)|= 0x0f;", src),
          "the look's stage is no run's and not the probe's", str(sorted(hex(x) for x in used)))

    # m1_look() as it stands: what it reads, in which order, under which mark, and what it logs.
    def fake(a):
        return ((a ^ (a >> 7)) * 0x9e3779b1) & 0xffffffff

    def reads(addrs):
        return [f"r {a:x}" for a in addrs]

    def looked(p):
        """One look's reads and marks, p being the pass's bit in each mark."""
        return (["k 6004 0f", f"k 6010 {p | 1:02x}"] + reads(pstate) + [f"k 6010 {p | 2:02x}"] + reads(aon) + [f"k 6010 {p | 3:02x}"] + reads(sio)
                + reads([0x28e5803b8]) + reads(ps) + [f"k 6010 {p | 4:02x}"] + reads(gates) + [f"k 6010 {p | 5:02x}"] + reads(clk)
                + [f"k 6010 {p | 6:02x}"] + reads(aonw) + [f"k 6010 {p | 8:02x}"] + reads(pre_c) + [f"k 6010 {p | 9:02x}"] + reads(pre_a)
                + [f"k 6010 {p | 10:02x}"] + reads(pre_b) + [f"k 6010 {p | 7:02x}"])

    want = looked(0)
    # The loads first (m1_look_init() from the pointers a load can find), then one look by itself.
    loads, cur = [], None
    for line in ran[:ran.index("plain")]:
        if line.startswith("load "):
            cur = []
            loads.append(cur)
        else:
            cur.append(line)
    stray = [line for line in ran if line.startswith("STRAY")]

    def ev(lines):
        return [line for line in lines if line.split()[0] in ("r", "k")]

    maps3 = ["map 28e000000 1e000", "map 28e5a0000 4000", "map 28e09c000 4000", "map 28e038000 4000", "map 28e0a0000 4000", "map 28e5b0000 4000"]
    whole = looked(0x10) + ["k 6010 00", "k 6004 00"]
    check(len(loads) == 10 and not stray and not any(line.startswith("never ") for i in range(7) for line in loads[i])
          and maps3[3:] == [f"map {a & ~0x3fff:x} 4000" for a in (pre_c[0], pre_a[0], pre_b[0])]
          and [line for line in loads[0] if line.startswith("map ")] == maps3 + ["map 2922c0000 4000"] and ev(loads[0]) == whole and loads[0][-1] == "ret 0"
          and [line for line in loads[1] if line.startswith("map ")] == maps3 and ev(loads[1]) == whole and loads[1][-1] == "ret 0"
          and sum(line.startswith("log look: awake, at load: ") for line in loads[0]) == 35,
          "the load's look: the engine's page is mapped by the look itself if nothing has mapped it (Pullfrog on #78: the first build read"
          " through it unmapped), every read goes through a mapped page, its marks say it's the load's, and the mark and the stage are"
          " zeroed after it",
          "\n".join(stray[:4] + loads[0][:8] + loads[1][-3:]))
    check(all(loads[i][-1] == want_ret and not ev(loads[i]) and sum(line.startswith("err ") for line in loads[i]) == 1
              for i, want_ret in ((2, "ret -12"), (3, "ret -12"), (4, "ret -19"), (5, "ret -19"), (6, "ret -12")))
          and [line for line in loads[3] if line.startswith("map ")] == maps3 + ["map 2922c0000 4000"]
          and [line for line in loads[6] if line.startswith("map ")] == maps3,
          "a load where a mapping fails (the second, one of the new pages', the engine's page's), or where SIO's page or the p-state page"
          " isn't there: refused, with nothing read and nothing marked",
          "\n".join(" | ".join(loads[i][-3:]) for i in (2, 3, 4, 5, 6)))
    # gate=1 at load: each of its six words is asked about before anything is read or marked, and one on the list refuses the load.
    asked = [f"never {a:x} 1" for a in (0x28e580250, 0x28e580258, 0x28e580260, 0x28e580268, 0x28e580270, 0x28e580160)]
    check([line for line in loads[7] if line.startswith("never ")] == asked and ev(loads[7]) == whole and loads[7][-1] == "ret 0"
          and loads[7].index(asked[-1]) < loads[7].index("k 6004 0f")
          and [line for line in loads[8] if line.startswith("never ")] == asked[:4] and [line for line in loads[9] if line.startswith("never ")] == asked
          and all(loads[i][-1] == "ret -1" and not ev(loads[i]) and sum(line.startswith("err ") for line in loads[i]) == 1 for i in (8, 9)),
          "gate=1 at load: its five UART words and the lane's are checked against the never-write list before the load's look, and the"
          " load is refused, with nothing read or marked, if one of them is on it", "\n".join(loads[7][:10] + loads[8][-3:] + loads[9][-3:]))
    ran = ran[ran.index("plain") + 1:]
    events = [line for line in ran if line.split()[0] in ("r", "k")]
    check(events == want and not stray and len(events) == 11 + 3 + 6 + 43 + 1 + 7 + 6 + 30 + 2 + 22
          and events[-29:-24] == ["k 6010 06", "r 2922c0008", "r 2922c0024", "k 6010 08", "r 28e03801c"]
          and events[-24:-15] == ["k 6010 09"] + reads(pre_a) + ["k 6010 0a"] and events[-15:] == reads(pre_b) + ["k 6010 07"],
          "m1_look() reads the 120 words of PLAN.md's list and no other, each group after its own mark, the stage first; after the 96 come"
          " the two words the engine only writes, and last of all the 22 B2 writes first, a mark to a page; it can't write a register (the"
          " test has no writel() to give it), and its PMU writes pass the module's own gate",
          "\n".join([f"{a}   |   {b}" for a, b in zip(events, want) if a != b][:8] + stray[:4] + [f"{len(events)} events, {len(want)} wanted"]))
    v = {a: f"{fake(a):08x}" for a in pstate + aon + aonw + sio + ps + gates + clk + [0x28e5803b8] + pre_c + pre_a + pre_b}
    logs = ["log look: WHEN: p-states 0x28e20c000: fabric " + v[pstate[0]] + " +400 " + v[pstate[1]] + " DCS " + v[pstate[2]],
            "log look: WHEN: AON 0x2922c0000: " + " ".join(f"+{a & 0xfff:03x} {v[a]}" for a in aon)]
    logs += [f"log look: WHEN: power 0x{row:x}, 8 apart:" + "".join(" " + v[a] for a in sio if row <= a < row + 0x20) for row in range(0x28e580180, 0x28e5802d8, 0x20)]
    logs += ["log look: WHEN: power 0x28e5803b8 (ANS2) " + v[0x28e5803b8] + "; 0x28e080000: " + " ".join(f"+{a & 0xfff:03x} {v[a]}" for a in ps),
             "log look: WHEN: gates 0x28e5a0000: " + " ".join(f"+{a & 0xfff:03x} {v[a]}" for a in gates[:3]) + "; 0x28e09c000: "
             + " ".join(f"+{a & 0xfff:03x} {v[a]}" for a in gates[3:])]
    logs += [f"log look: WHEN: clkgen 0x{a:x}: +0 {v[a]} +4 {v[a + 4]}" for a in clk[::2]]
    logs += ["log look: WHEN: AON 0x2922c0000, written by the engine and never read by it: +008 " + v[aonw[0]] + " +024 " + v[aonw[1]],
             "log look: WHEN: pre 0x28e03801c (B2's op 8 writes 80101100): " + v[pre_c[0]],
             "log look: WHEN: pre 0x28e0a0004 to 0x28e0a001c, 4 apart (B2's ops 9 to 15 write 1):" + "".join(" " + v[a] for a in pre_a),
             "log look: WHEN: pre 0x28e5b0004 to 0x28e5b0038, 4 apart (B2's ops 16 to 29 write 1):" + "".join(" " + v[a] for a in pre_b),
             "log look: WHEN: done"]
    got = [line for line in ran if line.startswith("log ")]
    check(got == logs and len(logs) == 2 + 11 + 2 + 15 + 1 + 3 + 1,
          "m1_look() logs every word it read, in full, under the address it read it from: 35 lines",
          "\n".join([f"{a}\n   wanted   {b}" for a, b in zip(got, logs) if a != b][:4] + [f"{len(got)} lines, {len(logs)} wanted"]))

    # Where it's called from, and that it changes nothing else: prepare takes none of the probe's steps, prepare_late looks instead of
    # probing, the load looks awake and then puts the breadcrumbs back as it found them.
    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    prep = cut("static int e1_prepare(void)", "\n}\n")
    late = cut("static int e1_prepare_late(void)", "\n}\n")
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    linit = cut("static int __init m1_look_init(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\("
    check("static bool look;\nmodule_param(look, bool, 0444);\n" in src and not re.search(r"\blook\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (look && !probe) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n\tif (probe && (!wd1_mark || !pmp_stop)) {"])
          and "\tif (probe) {\n\t\tif (n == 1 && !look)\n\t\t\tm1_probe_prepare();\n\t\treturn 0;\n\t}\n" in prep
          and "\t\tif (look && n == 1) {\n\t\t\tm1_look(\"after noirq\", 0);\n" in late and "\t\t} else if (!look && same) {\n\t\t\tm1_probe_late();\n\t\t}\n\t\tpr_info(" in late
          and src.count("m1_look(\"") == 2 and src.count("m1_probe_late();") == 1 and src.count("m1_probe_prepare();") == 1,
          "look is off unless asked for, read-only once loaded, and only with probe=1; with it prepare takes none of the probe's steps and"
          " prepare_late looks where the probe would have run; the replay's guards are probe=1's, untouched", prep + late[-900:])
    minit = cut("static int __init m1_init(void)", "\n}\n")
    check(in_order_of(linit, ["\tm1_look_clk = ioremap_np(LOOK_CLKGEN, LOOK_CLKGEN_N * LOOK_CLKGEN_STEP);\n\tm1_look_ga = ioremap_np(LOOK_GATE_A, PAGE_SIZE);\n"
                              "\tm1_look_gb = ioremap_np(LOOK_GATE_B, PAGE_SIZE);\n\tm1_look_pc = ioremap_np(LOOK_PRE_CLK, PAGE_SIZE);\n"
                              "\tm1_look_pa = ioremap_np(LOOK_PRE_A, PAGE_SIZE);\n\tm1_look_pb = ioremap_np(LOOK_PRE_B, PAGE_SIZE);\n"
                              "\tif (!m1_pos)\n\t\tm1_pos = ioremap_np(M1_POS_PAGE, PAGE_SIZE);\n"
                              "\tif (!m1_look_clk || !m1_look_ga || !m1_look_gb || !m1_look_pc || !m1_look_pa || !m1_look_pb || !m1_pos) {\n", "\t\treturn -ENOMEM;\n\t}\n"
                              "\tif (!m1_sio || !m1_ps || !post_map[m1_waits[0].idx]) {\n", "\t\treturn -ENODEV;\n\t}\n",
                              "\tm1_look(\"awake, at load\", LOOK_LOAD);\n", "\tm1_look_at(LOOK_AT_NONE);\n\tm1_kwrite(0x6004, &zero, 1);\n\treturn 0;\n"])
          and not re.search(wr, linit) and not re.search(wr, cut("static void m1_look(const char *when, u8 pass)", "\n}\n"))
          and not re.search(wr, cut("static void m1_look_at(u8 group)", "\n}\n"))
          # Last in m1_init(), after every step that maps a page it reads through; the table's mappings are made before m1_init() runs.
          and minit.endswith("\tif (look) {\n\t\tint ret = m1_look_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n\treturn 0;\n}\n")
          and minit.count("m1_look_init()") == 1 and src.count("m1_look_init()") == 1
          and all(minit.index(x) < minit.index("m1_look_init()") for x in ("m1_ps = ioremap_np(M1_PMGR_PS", "m1_sio = ioremap_np(M1_PS_SIO_PAGE",
                                                                             "m1_wd2_init()", "m1_probe_init()", "m1_fab_init()"))
          and src.index("post_map[i] = p + (a & ~PAGE_MASK);") < src.index("ret = m1_init();")
          and "static_assert(LOOK_AON_PAGE == M1_POS_PAGE && LOOK_SIO_PAGE == M1_PS_SIO_PAGE && LOOK_PS_PAGE == M1_PMGR_PS);\n" in src
          and "\tif (m1_look_clk)\n\t\tiounmap(m1_look_clk);\n\tif (m1_look_ga)\n\t\tiounmap(m1_look_ga);\n\tif (m1_look_gb)\n\t\tiounmap(m1_look_gb);\n"
              "\tif (m1_look_pc)\n\t\tiounmap(m1_look_pc);\n\tif (m1_look_pa)\n\t\tiounmap(m1_look_pa);\n\tif (m1_look_pb)\n\t\tiounmap(m1_look_pb);\n" in src
          and all(len(re.findall(r"\b" + x + r"\b", code_only)) == n for x, n in (("m1_look_pc", 13), ("m1_look_pa", 15), ("m1_look_pb", 15))),
          "the load maps the look's six ranges, each at its own address, looks once awake, and zeroes its mark and its stage again; no"
          " function of the look's writes a register; its pages are the recipe's where the recipe has them; all six are unmapped, and the"
          " three new pages' pointers are used nowhere else but in sio_steps=1's step and pre_test=1's test", linit)

    # Its stage through m1-crumbs.
    rc, out, err = dec(0x0f, 0, 0, 0, 7)
    check(rc == 0 and "stage 0x6004 = 0x0f: probe=1 look=1 (e1-hook/probe --look), not a run: nothing replayed, nothing was committed, nothing was"
          " stopped and no register was written. What it read is in the kernel log it saved, if the Mac was up to save it\n" in out
          and "0x6010 = 0x07: every group read: the look after noirq read all of them. What became of the suspend after it, this record doesn't say\n" in out
          and "DIDN'T FINISH" not in out and "the SoC restarted" not in out and "PMU alarm" not in out and "0x6005 = " not in out,
          "the look's stage with its last mark: a finished look after noirq, nothing claimed about the suspend, no run's lines and no probe's;"
          " the first look's record (results/look-20261110/crumbs.txt: stage 0x0f, 0x6010 = 0x07, written by the build that read 98 words)"
          " reads as finished still (Pullfrog on #88)",
          out + err)
    rc, out, err = dec(0x0f, 0, 0, 0, 4)
    check(rc == 0 and "0x6010 = 0x04: THE LOOK BEGAN AND DIDN'T FINISH: the look after noirq. The last group it marked: six gate-status words." in out,
          "the look's stage with an earlier mark: which look, and the group it stopped in", out + err)
    rc, out, err = dec(0x0f, 0, 0, 0, 0x16)
    check(rc == 0 and ("0x6010 = 0x16: THE LOOK BEGAN AND DIDN'T FINISH: the look at the module's load. The last group it marked: two words of the"
                       " engine's page that its sequences only write (+0x008, +0x024).") in out,
          "a mark with the load's bit: the look at the load, and its group", out + err)
    check([g["look_lines"](n)[0].split(": ", 1)[1].split(". The last group it marked: ")[1].split(".")[0] for n in (8, 9, 10)]
          == ["0x28e03801c, which B2's op 8 writes", "0x28e0a0004 to 0x28e0a001c, which B2's ops 9 to 15 write: a page not read before this look",
              "0x28e5b0004 to 0x28e5b0038, which B2's ops 16 to 29 write: a page not read before this look"]
          and all("THE LOOK BEGAN AND DIDN'T FINISH: the look after noirq" in g["look_lines"](n)[0] for n in (8, 9, 10))
          and "THE LOOK BEGAN AND DIDN'T FINISH: the look at the module's load. The last group it marked: 0x28e0a0004" in g["look_lines"](0x19)[0]
          and "THE LOOK BEGAN AND DIDN'T FINISH: the look at the module's load. The last group it marked: 0x28e5b0004" in g["look_lines"](0x1a)[0],
          "a look that stopped in one of the three new groups: not finished, which look, and the page it was reading")
    check(g["look_lines"](11)[0].startswith("0x6010 = 0x0b: THE LOOK BEGAN AND DIDN'T FINISH: the look after noirq. The last group it marked: not a group's number.")
          and g["look_lines"](0)[0].startswith("0x6010 = 0x00: THE LOOK BEGAN AND DIDN'T FINISH: the look after noirq. The last group it marked: nothing marked.")
          and g["look_lines"](0x17)[0].startswith("0x6010 = 0x17: every group read, by the look at the module's load, whose marks weren't zeroed after it")
          and "The look after noirq didn't begin" in g["look_lines"](0x17)[0],
          "a mark past the last group, no mark at all, and the load's last mark left standing: each said for what it is")


GATE_HDR = r"""
#include <stdio.h>
#include "gate_test.h"
int main(void)
{
	static const unsigned short uart[GATE_UART_N] = GATE_UARTS, kid[GATE_KID_N] = GATE_KIDS;
	static const u32 w[] = { 0x1000030f, 0x1f0000ff, 0x1f0003ff, 0x100000ff, 0x1000000f, 0x0000030f, 0x1400024f, 0x00000244, 0x00000200, 0x00000044, 0,
				 0x1f000bff, 0x0000080f };
	unsigned int i, j;
	u8 p[2];

	printf("consts %x %x %x %x %x %x %x %x %d", GATE_LANE, GATE_LANE_VAL, GATE_UART_VAL, GATE_PS_SET, GATE_PS_ACTUAL, GATE_SIO, GATE_CLOSED, GATE_PASS,
	       GATE_UART_N);
	for (i = 0; i < GATE_UART_N; i++)
		printf(" %x", uart[i]);
	printf("\nkids");
	for (i = 0; i < GATE_KID_N; i++)
		printf(" %x", kid[i]);
	printf("\nat %d %d %d %d %d %d %d %d %d %d\n", GATE_AT_NONE, GATE_AT_READ, GATE_AT_UARTS, GATE_AT_WAIT1, GATE_AT_LANE, GATE_AT_WAIT2,
	       GATE_AT_LANE_BACK, GATE_AT_UARTS_BACK, GATE_AT_DONE, GATE_AT_NOT_MADE);
	printf("more %x %x\n", GATE_NO_SNAP, GATE_MARK);
	for (i = 0; i < sizeof(w) / sizeof(w[0]); i++) {
		gate_pack(w[i], p);
		printf("one %x %d %d %d %02x %02x\n", w[i], gate_wanted(w[i]), gate_actual(w[i], 0), gate_actual(w[i], 0xf0), p[0], p[1]);
		for (j = 0; j < sizeof(w) / sizeof(w[0]); j++)
			printf("cmp %x %x %d %d\n", w[i], w[j], gate_took(w[i], w[j]), gate_back(w[i], w[j]));
	}
	for (i = 0; i < 128; i++)
		printf("byte %u %x\n", i, gate_byte(i & 1, i >> 1 & 1, i >> 2 & 1, i >> 3 & 1, i >> 4 & 1, i >> 5 & 1, i >> 6 & 1));
	return 0;
}
"""
GATE_RUN_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "asc_probe.h"
#include "look.h"
#include "gate_test.h"
typedef uint16_t u16;
typedef uint64_t u64;
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define cpu_relax() do { } while (0)
#define dsb(x) do { } while (0)
#define div64_u64(a, b) ((a) / (b))
"""
GATE_RUN_MODEL = r"""
/*
 * A model of what the gate test touches: the five UARTs' power words, the lane's, and SIO's and the lane's other
 * children's in SIO's page, and the six gate words. A power word takes a write's target, and its actual state
 * follows unless the scenario says the word is stuck; the lane takes a write's auto fields only if the scenario lets
 * it, and gates itself (actual 0) under UARTs that are all off, with a floor of 0 and auto on, or at any floor, as
 * the scenario has it. Bit 30 of 0x28e5a0050 follows the scenario's rule. A write to anything but the six, or a
 * read of anything else, prints STRAY. The clock ticks a millisecond a look at it.
 */
static unsigned char sio_page[0x1000], ga_page[0x1000], gb_page[0x1000];
static void *m1_sio = sio_page, *m1_look_ga = ga_page, *m1_look_gb = gb_page;
static const u16 uart_off[GATE_UART_N] = GATE_UARTS, kid_off[GATE_KID_N] = GATE_KIDS;
static u32 uart[GATE_UART_N], lane;
static unsigned int nread[GATE_UART_N + 1];
static u64 m1_tps = 1000, fake_now, off_tick;
static struct scenario {
	int rule;	/* the gate closes: 0 when every UART is off; 1 when the lane has gated itself too; 2 never; 3 late ticks after the UARTs went off */
	int lane_takes, lane_gates, lane_gates_at_f, uart_stuck, lane_back_fails, uart_back_fails;
	u32 uart_found[GATE_UART_N], lane_found, sio_found;
	unsigned int late;
	int lane_stays_gated;	/* once gated, the lane's actual state stays 0, whatever is written back */
	int stale;		/* an earlier test's byte and its reads of the lane are still in the PMU */
	int kfail;		/* not acked, and nothing written: 1 the zeroing of 0x6011-0x6016, 2 the command that carries the lane's reads */
} sc;
static int restoring;
static u8 pmu[0x20];	/* 0x6000-0x601f */

static u64 m1_now(void) { return fake_now++; }
static int uarts_off(void)
{
	unsigned int i;

	for (i = 0; i < GATE_UART_N; i++) {
		if (uart[i] & 0xf0)
			return 0;
	}
	return 1;
}
static void settle(void)
{
	int floor0 = (lane >> 28 & 1) && !(lane >> 24 & 0xf);

	if (sc.lane_stays_gated && restoring)
		return;
	lane = (lane & ~0xf0U) | (uarts_off() && ((floor0 && sc.lane_gates) || sc.lane_gates_at_f) ? 0 : (lane & 0xf) << 4);
}
static int which(const void *p)
{
	const unsigned char *q = p;
	unsigned int i;

	for (i = 0; i < GATE_UART_N; i++) {
		if (q == sio_page + uart_off[i])
			return (int)i;
	}
	return q == sio_page + GATE_LANE ? GATE_UART_N : -1;
}
static u32 readl(const void *p)
{
	static const u16 ga[] = LOOK_GATE_A_OFFS, gb[] = LOOK_GATE_B_OFFS;
	static const u32 gav[] = { 0x8004030f, 0xc0040302, 0xc0040308 }, gbv[] = { 0x8004031d, 0xc004030f, 0x8004030e };
	const unsigned char *q = p;
	int r = which(p);
	unsigned int i;

	if (r >= 0)
		nread[r]++;
	if (r >= 0 && r < GATE_UART_N)
		return uart[r];
	if (r == GATE_UART_N)
		return lane;
	for (i = 0; i < GATE_KID_N; i++) {
		if (q == sio_page + kid_off[i])
			return i ? 0x00000300 : sc.sio_found;
	}
	if (q == ga_page + GATE_SIO) {
		int closed = sc.rule == 0 ? uarts_off() : sc.rule == 1 ? uarts_off() && !(lane & 0xf0) :
			     sc.rule == 3 ? uarts_off() && fake_now - off_tick >= sc.late : 0;

		return gav[0] | (closed ? GATE_CLOSED : 0);
	}
	for (i = 0; i < 3; i++) {
		if (q == ga_page + ga[i])
			return gav[i];
		if (q == gb_page + gb[i])
			return gbv[i];
	}
	printf("STRAY read\n");
	return 0;
}
static void writel(u32 v, void *p)
{
	int r = which(p), were_off = uarts_off();

	if (r < 0) {
		printf("STRAY write %08x\n", v);
		return;
	}
	printf("w %llx %08x\n", 0x28e580000ULL + (unsigned long long)((unsigned char *)p - sio_page), v);
	if (r < GATE_UART_N) {
		if (restoring ? !sc.uart_back_fails : !(sc.uart_stuck && r == 2))
			uart[r] = (v & ~(ASC_PS_NOT_WRITTEN | 0xf0U)) | (v & 0xf) << 4;
	} else if (restoring ? !sc.lane_back_fails : sc.lane_takes) {
		lane = (lane & 0xf0) | (v & ~(ASC_PS_NOT_WRITTEN | 0xf0U));
	} else {
		/* The target lands, the auto fields don't. */
		lane = (lane & ~0xfU) | (v & 0xf);
	}
	if (!were_off && uarts_off())
		off_tick = fake_now;
	settle();
}
static int m1_kwrite(unsigned int addr, const u8 *b, unsigned int n)
{
	unsigned int i;

	int fail = (addr == 0x6011 && (sc.kfail & 1)) || (addr == LOOK_AT && n > 1 && (sc.kfail & 2));

	if (!m1_pmu_ok(true, addr, b, n) || addr < 0x6000 || addr + n > 0x6020) {
		printf("STRAY write to the PMU's %x, which m1_pmu_ok() refuses\n", addr);
		return -1;
	}
	/* The test puts things back from its LANE_BACK mark on, whether or not that mark's write is acked. */
	if (addr == LOOK_AT)
		restoring = (b[0] & 0xf) >= GATE_AT_LANE_BACK && (b[0] & 0xf) <= GATE_AT_DONE;
	if (addr == LOOK_AT && n == 1) {
		printf("at %02x\n", b[0]);
	} else {
		printf("k %x", addr);
		for (i = 0; i < n; i++)
			printf(" %02x", b[i]);
		printf("%s\n", fail ? " FAILED" : "");
	}
	if (fail)
		return -5;
	memcpy(pmu + (addr - 0x6000), b, n);
	return 0;
}
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("log "); vprintf(fmt, ap); va_end(ap); }
"""
GATE_RUN_MAIN = r"""
static void run(unsigned int n, const struct scenario *s)
{
	unsigned int i;

	sc = *s;
	fake_now = 0;
	off_tick = 0;
	restoring = 0;
	for (i = 0; i < GATE_UART_N; i++)
		uart[i] = sc.uart_found[i];
	lane = sc.lane_found;
	memset(nread, 0, sizeof(nread));
	memset(pmu, 0, sizeof(pmu));
	pmu[4] = LOOK_STAGE;
	if (sc.stale) {
		pmu[5] = 0xfd;
		memcpy(pmu + 0x11, "\xee\x1e\x0e\x10\x0e\x10", 6);
	}
	printf("case %u\n", n);
	m1_gate_test();
	printf("reads %u %u %u %u %u %u\n", nread[0], nread[1], nread[2], nread[3], nread[4], nread[5]);
	printf("pmu %02x %02x %02x %02x %02x %02x %02x %02x\n", pmu[5], pmu[0x10], pmu[0x11], pmu[0x12], pmu[0x13], pmu[0x14], pmu[0x15], pmu[0x16]);
	printf("end %08x %08x %08x %08x %08x %08x\n", uart[0], uart[1], uart[2], uart[3], uart[4], lane);
}

int main(void)
{
	const struct scenario base = { 0, 1, 1, 0, 0, 0, 0, { 0x244, 0x244, 0x244, 0x244, 0x244 }, 0x1f0000ff, 0x0f000200, 0, 0, 0, 0 };
	struct scenario s[20];
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(s); i++)
		s[i] = base;
	s[1].rule = 1;				/* the gate needs the lane gated as well */
	s[2].rule = 1; s[2].lane_takes = 0;	/* and the lane doesn't take macOS's value */
	s[3].rule = 2;				/* the gate never closes; the lane takes the value and gates */
	s[4].rule = 2; s[4].lane_gates = 0;	/* never closes, and the lane takes the value without gating */
	s[5].uart_stuck = 1;			/* one UART doesn't go off */
	s[6].lane_back_fails = 1;		/* the lane doesn't take its value back */
	s[7].uart_back_fails = 1;		/* the UARTs don't take theirs back */
	for (i = 0; i < GATE_UART_N; i++)	/* UARTs off already; the lane with its two flags set */
		s[8].uart_found[i] = 0x00000200;
	s[8].lane_found = 0x1f0003ff;
	s[9].sio_found = 0x1f0000ff;		/* SIO reads on after noirq: not the state the test is for */
	s[10].uart_found[3] = 0x000000ff;	/* something has a UART on */
	s[11].rule = 1; s[11].lane_gates_at_f = 1;	/* the lane gates at Linux's own floor once the UARTs are off */
	s[12].uart_found[1] = 0x00000200; s[12].uart_found[2] = 0x00000044; s[12].uart_found[4] = 0x00000200;	/* each UART found its own way */
	s[13].rule = 3; s[13].late = LATE;	/* the gate closes late, by itself: after the first poll, before the lane's write */
	s[14].lane_stays_gated = 1;		/* the lane takes its settings back, and its actual state stays 0 */
	/* Pullfrog on #80: an earlier test's bytes still in the PMU. */
	s[15].sio_found = 0x1f0000ff; s[15].stale = 1;			/* not made: they're zeroed */
	s[16].sio_found = 0x1f0000ff; s[16].stale = 1; s[16].kfail = 1;	/* not made, and the zeroing isn't acked: they stay */
	s[17].stale = 1; s[17].kfail = 3;	/* made; neither the zeroing nor the write of this test's reads is acked */
	s[18].kfail = 2;			/* made; the write of its reads isn't acked */
	s[19].stale = 1;			/* made: zeroed, then this test's */
	for (i = 0; i < ARRAY_SIZE(s); i++)
		run(i, &s[i]);
	return 0;
}
"""


def gate_tests(g, src, dec):
    """probe=1 look=1 gate=1 (e1-hook/probe --gate): gate_test.h against an independent reading of it; m1_gate_test() and its three
    helpers as they stand against a model of the power words and the gate; the places that call it; and its record through
    m1-crumbs. dec(stage, parked, left, down, at) runs m1-crumbs on a record."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return subprocess.run([os.path.join(tmp, name)], capture_output=True, text=True, check=True).stdout.splitlines()

        hdr = [line.split() for line in build("ghdr", GATE_HDR)]
        defs = "".join(line + "\n" for line in src.splitlines() if re.match(r"#define (M1_PMU_ALARM\w*|M1_PMU_RTC|M1_ALARM_ON)\s", line))
        # LATE: the model's gate closes this many ticks after the UARTs went off: past the first poll's last read, and before the
        # read that comes right before the lane's write.
        ran = build("grun", GATE_RUN_PRELUDE + "#define LATE 103\n" + defs + "static bool alarm = true;\n" + cut("static bool m1_pmu_ok(bool write,", "\n}\n")
                    + GATE_RUN_MODEL + cut("static void m1_look_at(u8 group)", "\n}\n") + cut("static void m1_gate_log(const char *when)", "\n}\n")
                    + cut("static bool m1_gate_wait(const char *when)", "\n}\n") + cut("static u32 m1_gate_write(void __iomem *p,", "\n}\n")
                    + cut("static void m1_gate_test(void)", "\n}\n") + GATE_RUN_MAIN)

    # The header: the lane and its value, the five UARTs and theirs, the words only read, the gate word and its bit.
    uarts = [0x250, 0x258, 0x260, 0x268, 0x270]
    check(hdr[0] == ["consts", "160", "1000030f", "200", "1f00000f", "f0", "50", "40000000", "20", "5"] + [f"{u:x}" for u in uarts]
          and hdr[1] == ["kids", "180", "378", "380", "388", "3a8"]
          and hdr[2] == ["at"] + [str(i) for i in range(10)] and g["GATE_PASS"] == 0x20 and len(g["GATE_AT"]) == 10 and g["GATE_DONE"] == 8
          and g["GATE_LANE_BACK"] == 6 and g["GATE_NOT_MADE"] == 9 and hdr[3] == ["more", "40", "80"] and g["GATE_NO_SNAP"] == 0x40
          and g["GATE_MARK"] == 0x80 and g["GATE_UNMADE"] == 0xe0 and ["byte", "96", "e0"] in hdr,
          "gate_test.h: AFNC2_LW1 and macOS's value for it, UART2, 3, 4, 6, 7 and 0x200, SIO's word and the lane's four other children to"
          " read, the gate word 0x28e5a0050 and bit 30; ten marks with 0x20 set, 0x40 for reads that weren't written, the byte of a"
          " test that wasn't made, as m1-crumbs has them", str(hdr[:4]))
    pw = [(0x28e580160, 0x1000030f)] + [(0x28e580000 + u, 0x200) for u in uarts]
    table = cut("} m1_pw[] = {", "\n};\n")
    check(all(f"{{ 0x{addr:x}ULL, 0x{val:08x}, " in table for addr, val in pw),
          "the gate test's six words and values are ps_words=1's own for them: nothing is written that the runs don't write")
    cmp_bad = [r for r in hdr if r[0] == "cmp"
               and (int(r[3]) != (not (int(r[1], 16) ^ int(r[2], 16)) & 0x1f00000f) or int(r[4]) != (not (int(r[1], 16) ^ int(r[2], 16)) & 0x1f0000ff))]
    byte_bad = [r for r in hdr if r[0] == "byte" and int(r[2], 16) != (0x80 | int(r[1]))]

    def packed(v):
        return [v & 0xff, (v >> 24 & 0x1f) | (0x20 if v & 0x100 else 0) | (0x40 if v & 0x200 else 0) | (0x80 if v & 0x800 else 0)]

    one_bad = [r for r in hdr if r[0] == "one" and (int(r[2]) != (int(r[1], 16) & 0xf == 0xf) or int(r[3]) != (not int(r[1], 16) & 0xf0)
                                                    or int(r[4]) != (int(r[1], 16) & 0xf0 == 0xf0) or [int(r[5], 16), int(r[6], 16)] != packed(int(r[1], 16)))]
    check(sum(r[0] == "cmp" for r in hdr) == 169 and not cmp_bad and sum(r[0] == "byte" for r in hdr) == 128 and not byte_bad
          and sum(r[0] == "one" for r in hdr) == 13 and not one_bad,
          "gate_test.h: a word took a value when its auto-enable, auto floor and target read as the value's, and is back as found when its"
          " actual state does too; wanted is a target of f; the two packed bytes keep target, actual, the auto fields and three flags;"
          " the byte has bit 7 and one bit an argument, in order", str(cmp_bad[:3] + byte_bad[:3] + one_bad[:3]))

    # m1_gate_test() as it stands, twenty scenarios.
    cases, cur = [], None
    for line in ran:
        if line.startswith("case "):
            cur = []
            cases.append(cur)
        else:
            cur.append(line)
    stray = [line for line in ran if line.startswith("STRAY")]
    found_u = {i: [0x244] * 5 for i in range(20)}
    found_u[8] = [0x200] * 5
    found_u[12] = [0x244, 0x200, 0x44, 0x244, 0x200]
    found_l = {i: 0x1f0003ff if i == 8 else 0x1f0000ff for i in range(20)}
    # What 0x6011-0x6016 gets: the lane packed three times. Before its write, after it, after the second poll.
    packs = {0: "ff 1f 0f 10 0f 10", 1: "ff 1f 0f 10 0f 10", 2: "ff 1f ff 1f ff 1f", 3: "ff 1f 0f 10 0f 10", 4: "ff 1f ff 10 ff 10", 5: "ff 1f ff 10 ff 10",
             6: "ff 1f 0f 10 0f 10", 7: "ff 1f 0f 10 0f 10", 8: "ff 7f 0f 10 0f 10", 11: "0f 1f 0f 10 0f 10", 12: "ff 1f 0f 10 0f 10", 13: "ff 1f 0f 10 0f 10",
             14: "ff 1f 0f 10 0f 10", 17: "ff 1f 0f 10 0f 10", 18: "ff 1f 0f 10 0f 10", 19: "ff 1f 0f 10 0f 10"}
    # Case 5: one UART stays on, so the model's lane, which gates itself only under a subtree that's all off, doesn't.
    want = {0: 0xff, 1: 0xfd, 2: 0xe1, 3: 0xf5, 4: 0xe5, 5: 0xe4, 6: 0xdf, 7: 0xbf, 8: 0xff, 9: 0xe0, 10: 0xe0, 11: 0xff, 12: 0xff, 13: 0xff, 14: 0xdf, 15: 0xe0, 16: 0xe0, 17: 0xff, 18: 0xff,
            19: 0xff}
    notmade, kfail = (9, 10, 15, 16), {16: 1, 17: 3, 18: 2}

    def ev(c, kinds=("w", "at", "k")):
        return [line for line in c if line.split()[0] in kinds]

    def want_ev(i):
        kf = kfail.get(i, 0)
        start = ["at 21", "k 6005 00", "k 6011 00 00 00 00 00 00" + (" FAILED" if kf & 1 else "")]
        if i in notmade:
            return start + ["at 29", f"k 6005 {want[i]:02x}"]
        # The lane's reads go out with step 6's mark, in one command; if that isn't acked the mark goes alone, with 0x40, as the later ones do.
        ns = 0x40 if kf & 2 else 0
        return (start + ["at 22"] + [f"w 28e580{u:03x} 00000200" for u in uarts] + ["at 23", "at 24", "w 28e580160 1000030f", "at 25",
                "k 6010 26 " + packs[i] + (" FAILED" if kf & 2 else "")] + (["at 66"] if kf & 2 else [])
                + [f"w 28e580160 {found_l[i] & ~0xb00:08x}", f"at {0x27 | ns:02x}"]
                + [f"w 28e580{u:03x} {f & ~0xb00:08x}" for u, f in zip(uarts, found_u[i])] + [f"at {0x28 | ns:02x}", f"k 6005 {want[i]:02x}"])

    bad = [(i, [f"{a} | {b}" for a, b in zip(ev(c), want_ev(i)) if a != b] or [len(ev(c)), len(want_ev(i))]) for i, c in enumerate(cases) if ev(c) != want_ev(i)]
    check(len(cases) == 20 and not stray and not bad,
          "m1_gate_test() in twenty scenarios: twelve writes and no other, each word once out and once back, to the five UARTs and the"
          " lane alone, ps_words=1's values out and each word's own found value back less the bits the kernel leaves out; each step after"
          " its mark; 0x6005 and 0x6011-0x6016 zeroed first, the lane's three reads to the PMU in one command with step 6's mark, before"
          " anything is written back, the byte last; whatever the gate or the lane did in between, and whether or not the PMU acked,"
          " everything is written back; and where SIO reads on or a UART is in use, nothing is written at all, under a mark of its own",
          "\n".join(stray[:4] + [str(b) for b in bad[:3]]))
    # What the PMU holds after each, through the decoder: the lane's reads are this test's by their mark alone (Pullfrog on #80).
    pmus = [[int(x, 16) for x in line.split()[1:]] for c in cases for line in c if line.startswith("pmu ")]
    old, new = [0xee, 0x1e, 0x0e, 0x10, 0x0e, 0x10], [0xff, 0x1f, 0x0f, 0x10, 0x0f, 0x10]

    def dl(i):
        return "\n".join(g["gate_lines"](pmus[i][1], pmus[i][0], bytes(pmus[i][2:])))

    reads3 = "read three times before anything was written back"
    check(len(pmus) == 20 and pmus[0] == [0xff, 0x28] + new and pmus[15] == [0xe0, 0x29] + [0] * 6 and pmus[16] == [0xe0, 0x29] + old
          and pmus[17] == [0xff, 0x68] + old and pmus[18] == [0xff, 0x68] + [0] * 6 and pmus[19] == [0xff, 0x28] + new
          and all(reads3 in dl(i) and "after the write of macOS's 0x1000030f: target f, actual state 0, auto-enable on, auto floor 0" in dl(i)
                  and "NOT this test's" not in dl(i) for i in (0, 19))
          and all(reads3 not in dl(i) and "right before its write" not in dl(i) and "after the write of macOS" not in dl(i) for i in (15, 16, 17, 18))
          and "0x6011-0x6016 read 0: the lane's three reads weren't written (the test wasn't made)" in dl(15)
          and "0x6011-0x6016 hold ee 1e 0e 10 0e 10: NOT this test's reads of the lane (the test wasn't made): left from before" in dl(16)
          and "0x6011-0x6016 hold ee 1e 0e 10 0e 10: NOT this test's reads of the lane (the mark says the write that carries them wasn't acked)" in dl(17)
          and "0x6011-0x6016 read 0: the lane's three reads weren't written (the mark says the write that carries them wasn't acked)" in dl(18)
          and all("the byte a test that wasn't made leaves" in dl(i) and "nothing is decided about the gate" in dl(i) for i in (15, 16))
          and all("0x6010 = 0x68: the gate test, after both looks: every step taken: the byte written" in dl(i)
                  and "closes once SIO's subtree is all off" in dl(i) for i in (17, 18))
          and any("zeroed first: 0 -5" in line for line in cases[16]) and any("zeroed first: 0 0" in line for line in cases[15])
          and any("to the PMU's 0x6010-0x6016 in one command: -5" in line for line in cases[17])
          and any("to the PMU's 0x6010-0x6016 in one command: 0" in line for line in cases[19]),
          "an earlier test's bytes at 0x6011-0x6016 are never read as this test's reads of the lane: zeroed first; a test that isn't made"
          " leaves a mark that says so; a made test's reads count only under the mark written with them, or a later one that doesn't say"
          " their write failed; and the byte's verdict stands either way", "\n".join(str(p) for p in pmus[15:]) + "\n" + dl(16) + "\n" + dl(17))
    # The model keeps a word's target, actual state and auto fields, not its two sticky flags: "as found" is by those, as gate_back() has it.
    ends = [[int(x, 16) & 0x1f0000ff for x in c[-1].split()[1:]] for c in cases]
    asfound = {i: [f & 0x1f0000ff for f in found_u[i]] + [0x1f0000ff] for i in range(20)}
    asfound[10] = [0x44, 0x44, 0x44, 0xff, 0x44, 0x1f0000ff]
    check(all(ends[i] == asfound[i] for i in range(20) if i not in (6, 7, 14)) and ends[6] == [0x44] * 5 + [0x100000ff] and ends[7] == [0] * 5 + [0x1f0000ff]
          and ends[14] == [0x44] * 5 + [0x1f00000f],
          "after the test the six words read as found, in every scenario where the hardware takes the write-back; where it doesn't, they"
          " don't, and the byte says so", "\n".join(c[-1] for c in cases))

    def summary(c):
        s = [line for line in c if line.startswith("log look: gate: SUMMARY: ")]
        return s[0][len("log look: gate: SUMMARY: "):] if len(s) == 1 else ""

    b30 = "bit 30 of 0x28e5a0050 before AFNC2_LW1's write: "
    texts = {0: "SIO's subtree all off; " + b30 + "SET; AFNC2_LW1 took macOS's value, and bit 30 after that: SET; put back: AFNC2_LW1 as found, the UARTs as found",
             1: "SIO's subtree all off; " + b30 + "clear; AFNC2_LW1 took macOS's value, and bit 30 after that: SET; put back: AFNC2_LW1 as found, the UARTs as found",
             2: "SIO's subtree all off; " + b30 + "clear; AFNC2_LW1 did NOT take macOS's value, and bit 30 after that: clear;",
             3: "SIO's subtree all off; " + b30 + "clear; AFNC2_LW1 took macOS's value, and bit 30 after that: clear;",
             # A UART that stayed on: the line the launcher shows as the result says the test decided nothing (Pullfrog on #80).
             5: "INCONCLUSIVE: the five UARTs did NOT all go off, so SIO's subtree wasn't all off; " + b30 + "clear;",
             6: "put back: AFNC2_LW1 NOT as found, the UARTs as found", 7: "put back: AFNC2_LW1 as found, the UARTs NOT all as found",
             9: "NOT MADE: after noirq SIO reads on, or a UART has a target of f", 10: "NOT MADE: after noirq SIO reads on, or a UART has a target of f",
             # The lane had gated at Linux's own floor, and comes back with the UARTs: neither is put down wrongly (the review of #80).
             11: "SIO's subtree all off; " + b30 + "SET; AFNC2_LW1 took macOS's value, and bit 30 after that: SET; put back: AFNC2_LW1 as found, the UARTs as found",
             13: "SIO's subtree all off; " + b30 + "SET;",
             # Its settings back and its actual state not: that isn't as found.
             14: "put back: AFNC2_LW1 NOT as found, the UARTs as found"}
    bad = [(i, summary(cases[i])) for i, t in texts.items() if t not in summary(cases[i])]
    check(not bad and all(summary(c).endswith(f"byte {want[i]:02x}, written to the PMU: 0") for i, c in enumerate(cases))
          and ["INCONCLUSIVE" in summary(c) for c in cases] == [i == 5 for i in range(20)]
          and ["NOT MADE" in summary(c) for c in cases] == [i in notmade for i in range(20)]
          and any("AFNC2_LW1 reads 1f00000f with everything written back: NOT as found within 10 ms" in line for line in cases[14])
          and any("AFNC2_LW1 found 1f0000ff, := 1000030f: it read 1f0000ff at once and reads 1f0000ff: auto-enable, auto floor and target NOT as written"
                  in line for line in cases[2])
          and any("right before AFNC2_LW1's write: it reads 1f0000ff, its actual state not 0; 0x28e5a0050 reads 8004030f: bit 30 clear" in line for line in cases[1])
          and any("right before AFNC2_LW1's write: it reads 1f00000f, its actual state 0 already; 0x28e5a0050 reads c004030f: bit 30 SET" in line for line in cases[11])
          and any("right before AFNC2_LW1's write: it reads 1f0000ff, its actual state not 0; 0x28e5a0050 reads c004030f: bit 30 SET" in line for line in cases[13])
          and any("AFNC2_LW1 reads 1000000f then: its actual state 0" in line for line in cases[1])
          and any("AFNC2_LW1 reads 100000ff then: its actual state not 0" in line for line in cases[4])
          and any("AFNC2_LW1 reads 1f0000ff with everything written back: as found" in line for line in cases[11])
          and any("below it: SIO 0f000200 DISPDFR_FE 00000300 DISPDFR_BE 00000300 MIPI_DSI 00000300 ISP_SYS 00000300" in line for line in cases[0])
          and sum(line.startswith("log look: gate: ") and ": gates 0x28e5a0000: +050 " in line for line in cases[0]) == 4,
          "the SUMMARY line and the lines before it say what the gate and the lane did in each scenario: the lane and the gate read right"
          " before the lane's write, so a lane gated at Linux's floor or a gate that closed late isn't put down to the write; the lane"
          " judged with everything back; the six gate words logged four times", str(bad[:2]) + "\n" + "\n".join(cases[11][:20]))
    # A write that's met at once is read once; one that isn't, for 10 ms and no longer.
    nreads = [[int(x) for x in line.split()[1:]] for c in cases for line in c if line.startswith("reads ")]
    check(len(nreads) == 20 and nreads[0] == [3, 3, 3, 3, 3, 6] and nreads[12] == [3, 3, 3, 3, 3, 6] and nreads[9] == [1, 1, 1, 1, 1, 1]
          and nreads[2][5] == 15 and nreads[5][2] == 12
          and nreads[7][:5] == [12] * 5 and max(max(r) for r in nreads) <= 25,
          "each UART is read once as found and once after each write of it that's met at once; the lane besides before its write, after"
          " the second poll and at the end; a write that isn't met is read for 10 ms and no longer; a test that isn't made reads each"
          " once", str(nreads))
    polls = [[line for line in c if "0x28e5a0050 reads" in line and "right before" not in line] for c in cases]
    # The model's clock ticks once a look at it, a millisecond a tick: a poll that's met at its first read says 1 ms, one that isn't 101.
    check(all(len(p) == (0 if i in notmade else 2) for i, p in enumerate(polls)) and "after 1 ms: bit 30 SET" in polls[0][0]
          and "after 101 ms: bit 30 clear" in polls[1][0] and "after 1 ms: bit 30 SET" in polls[1][1] and "after 101 ms: bit 30 clear" in polls[3][1]
          and "after 101 ms: bit 30 clear" in polls[13][0],
          "the gate is polled twice, each for 100 ms and no longer, and a set bit ends the poll at once", str(polls[:2] + polls[13:]))

    # Where it's called from, and what it may write through.
    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    late = cut("static int e1_prepare_late(void)", "\n}\n")
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    gt = cut("static void m1_gate_test(void)", "\n}\n")
    linit = cut("static int __init m1_look_init(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\([^;]*;"
    check("static bool gate;\nmodule_param(gate, bool, 0444);\n" in src and not re.search(r"\bgate\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (look && !probe) {\n", "\tif (gate && !look) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n\tif (probe && (!wd1_mark || !pmp_stop)) {"])
          and "\t\tif (look && n == 1) {\n\t\t\tm1_look(\"after noirq\", 0);\n\t\t\tif (gate)\n\t\t\t\tm1_gate_test();\n\t\t\tif (pre_test)\n\t\t\t\tm1_pre_test();\n\t\t} else if (!look && same) {\n\t\t\tm1_probe_late();\n\t\t}\n" in late
          and src.count("m1_gate_test();") == 1,
          "gate is off unless asked for, read-only once loaded, and only with look=1; its test runs once, right after the look that follows"
          " noirq, in the suspend probe=1 then fails", late[-1200:])
    check(not re.findall(wr, gt) and not re.findall(wr, cut("static void m1_gate_log(const char *when)", "\n}\n"))
          and not re.findall(wr, cut("static bool m1_gate_wait(const char *when)", "\n}\n"))
          and re.findall(wr, cut("static u32 m1_gate_write(void __iomem *p,", "\n}\n")) == ["writel(val, p);"]
          and gt.count("m1_gate_write(") == 4 and all(f"m1_gate_write(m1_sio + {t}," in gt for t in ("uart[i]", "GATE_LANE"))
          and gt.count("m1_gate_write(m1_sio + ") == 4 and "M1_PS_PMP_RO" not in gt and "m1_ps" not in gt and gt.count("m1_kwrite(0x6005, &res, 1)") == 2
          and "static_assert(LOOK_SIO_PAGE + GATE_LANE == 0x28e580160ULL && LOOK_GATE_A + GATE_SIO == 0x28e5a0050ULL && GATE_PASS > LOOK_LOAD);\n" in src
          and in_order_of(linit, ["\tif (gate) {\n", "\t\t\tif (m1_never(LOOK_SIO_PAGE + uart[i], 1))\n\t\t\t\tbreak;\n",
                                  "\t\tif (i < GATE_UART_N || m1_never(LOOK_SIO_PAGE + GATE_LANE, 1)) {\n", "\t\t\treturn -EPERM;\n", "\tm1_look(\"awake, at load\", LOOK_LOAD);"]),
          "the gate test writes registers through one function and one writel(), four times, each to SIO's page at a UART's offset or the"
          " lane's; its addresses are pinned, and the load refuses if one of the six is on the never-write list", gt[:600])

    # Its record through m1-crumbs.
    def gl(at, v, pack=(0xff, 0x1f, 0x0f, 0x10, 0x0f, 0x10)):
        return g["gate_lines"](at, v, bytes(pack))

    rc, out, err = dec(0x0f, 0xfd, 0, 0, 0x28)
    check(rc == 0 and "the looks wrote no register; gate=1's test, after them, changes six power words and writes them back (--gate): how far it got is below" in out
          and "0x6010 = 0x28: the gate test, after both looks: every step taken: the byte written\n" in out
          and "0x6011-0x6016 read 0 though the mark says their write was acked: nothing is read from them" in out
          and "  bit 30 of 0x28e5a0050, the gate B2 polls after SIO's subtree, before the lane's write: clear, through 100 ms and at the read right before the write\n" in out
          and "  AFNC2_LW1, written macOS's 0x1000030f: took it within 10 ms; its actual state after the second poll: 0\n" in out
          and "  bit 30 of 0x28e5a0050 after that: SET\n" in out and "  read as found with everything written back: AFNC2_LW1 yes; the UARTs yes\n" in out
          and "Closed after it: one before and one after can't tell that from a gate that closes late by itself" in out and "DIDN'T FINISH" not in out,
          "the gate test's record, the gate reading set only after the lane took macOS's value: decoded as closed after it, not because of"
          " it", out + err)
    lines = gl(0x28, 0xfd)
    check(lines[1] == "0x6011-0x6016: AFNC2_LW1 (0x28e580160), read three times before anything was written back:"
          and lines[2] == "  right before its write, SIO's subtree off: target f, actual state f, auto-enable on, auto floor f"
          and lines[3] == "  after the write of macOS's 0x1000030f: target f, actual state 0, auto-enable on, auto floor 0"
          and g["gate_word"](bytes((0x04, 0xe4))) == "target 4, actual state 0, auto-enable off, auto floor 4, was power-gated, was clock-gated, BUSY"
          and g["gate_word"](bytes((0xf4, 0x25))) == "target 4, actual state f, auto-enable off, auto floor 5, was power-gated"
          and g["gate_word"](bytes((0x00, 0x50))) == "target 0, actual state 0, auto-enable on, auto floor 0, was clock-gated"
          and g["gate_word"](bytes((0x00, 0x80))) == "target 0, actual state 0, auto-enable off, auto floor 0, BUSY",
          "the lane's three packed reads: target, actual state, auto-enable, auto floor and the three flags, each read back out", "\n".join(lines[:5]))
    # The record as the PMU would hold it, through decode(): the three reads come from 0x6011-0x6016 and no other byte.
    rec = bytearray(0x20)
    rec[4], rec[5], rec[0x10], rec[0x17] = 0x0f, 0xfd, 0x28, 0x99
    rec[0x11:0x17] = bytes((0xff, 0x1f, 0x0f, 0x10, 0x04, 0x44))
    whole = g["decode"](bytes(rec), bytes(6))
    check("  right before its write, SIO's subtree off: target f, actual state f, auto-enable on, auto floor f" in whole
          and "  after the write of macOS's 0x1000030f: target f, actual state 0, auto-enable on, auto floor 0" in whole
          and "  after the second poll of the gate: target 4, actual state 0, auto-enable off, auto floor 4, was clock-gated" in whole
          and "0x6005 = 0xfd: the gate test's byte:" in whole,
          "a gate test's whole record through decode(): the byte from 0x6005, the lane's three reads from 0x6011-0x6016, each in its"
          " own line", "\n".join(whole))
    # The last mark lost and the byte there: the test reached its end, and it isn't called unfinished (Pullfrog on #80).
    lost = gl(0x27, 0xfd)
    check("DIDN'T FINISH" not in lost[0] and "its byte landed" in lost[0] and "a later mark didn't land" in lost[0]
          and "may not have been written back" not in lost[0] and any("Closed after it" in line for line in lost)
          and "DIDN'T FINISH" in gl(0x27, 0x7d)[0] and "DIDN'T FINISH" in gl(0x27, 0)[0],
          "an earlier step's mark with the test's byte landed: read as a lost later mark, with the byte's verdict; without the byte, as a"
          " test that didn't finish", "\n".join(lost))
    rc, out, err = dec(0x0f, 0, 0, 0, 0x25)
    check(rc == 0 and ("0x6010 = 0x25: THE GATE TEST BEGAN AND DIDN'T FINISH, after both looks. The last step it marked: the gate polled again."
                       in out) and "a word it had changed may not have been written back" in out and "the test's byte didn't land (bit 7 clear), or the test stopped before it" in out,
          "the gate test stopped between its writes and its write-back: the step, and that a restart has undone it", out + err)
    verdicts = {0xff: "closes once SIO's subtree is all off, before the lane was given macOS's value", 0xe1: "the lane did NOT take macOS's value",
                0xf5: "something else is behind it, though the lane's actual state read 0",
                0xe5: "something else is behind it, and the lane's actual state didn't read 0", 0xe4: "SIO's subtree wasn't all off",
                0xe0: "SIO's subtree wasn't all off", 0xe9: "set at the second poll though the lane did NOT take macOS's value",
                0xfd: "set within 100 ms after the lane took macOS's value, with the lane's actual state then 0",
                0xed: "set within 100 ms after the lane took macOS's value though the lane's actual state wasn't 0 then"}
    bad = [hex(v) for v, t in verdicts.items() if t not in "\n".join(gl(0x28, v))]
    wrong = [hex(v) for v in range(128, 256) if sum(line.startswith("  so: ") for line in gl(0x28, v)) != 1]
    check(not bad and not wrong and "nothing is read" in gl(0x28, 0x7f)[-1] and len([line for line in gl(0x28, 0x7f) if line.startswith("  so: ")]) == 0
          and gl(0x2a, 0)[0].startswith("0x6010 = 0x2a: THE GATE TEST BEGAN AND DIDN'T FINISH, after both looks. The last step it marked: not a step's number."),
          "the gate test's verdict for each ending the model produced and two more, one verdict for every byte that landed, and none from"
          " one that didn't", str(bad) + str(wrong[:4]))
    # The mark decides whose the six bytes are. With the same bytes there each time: read under step 6's mark and the two after it;
    # not before it, not under a mark that says their write failed, not after a test that wasn't made.
    theirs = {at: reads3 in "\n".join(gl(at, 0xff)) for at in (0x21, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x66, 0x67, 0x68)}
    made0 = gl(0x29, 0xe0)
    check(theirs == {0x21: False, 0x25: False, 0x26: True, 0x27: True, 0x28: True, 0x29: False, 0x2a: False, 0x66: False, 0x67: False, 0x68: False}
          and "NOT this test's reads of the lane (the last mark that landed is from before their write)" in "\n".join(gl(0x25, 0))
          and "DIDN'T FINISH" in gl(0x26, 0)[0] and reads3 in gl(0x26, 0)[1]
          and gl(0x68, 0xff)[0] == "0x6010 = 0x68: the gate test, after both looks: every step taken: the byte written"
          and made0[0] == "0x6010 = 0x29: the gate test, after both looks: the test wasn't made: after noirq SIO read on, or a UART had a"
                          " target of f, and nothing was written"
          and made0[-2] == "0x6005 = 0xe0: the byte a test that wasn't made leaves" and made0[-1].startswith("  so: nothing was switched off")
          and len([line for line in made0 if line.startswith("  so: ")]) == 1
          and "NOT the 0xe0 a test that wasn't made leaves" in gl(0x29, 0xfd)[-2] and "closes once" not in "\n".join(gl(0x29, 0xff)),
          "whose the six bytes at 0x6011-0x6016 are is read from the mark alone; a test that wasn't made is said to be one, and gets no"
          " verdict from the byte", str(theirs) + "\n" + "\n".join(made0))
    # Each bit of the byte by itself, in the line that's its own: none read from its neighbour's place.
    own = {0: ("the five UARTs: all read actual 0 within 10 ms of being written 0x200", "the five UARTs: did NOT all read actual 0"),
           1: ("before the lane's write: SET", "before the lane's write: clear"), 2: ("0x1000030f: took it", "0x1000030f: did NOT take it"),
           3: ("bit 30 of 0x28e5a0050 after that: SET", "bit 30 of 0x28e5a0050 after that: clear"),
           4: ("after the second poll: 0", "after the second poll: not 0"), 5: ("AFNC2_LW1 yes", "AFNC2_LW1 NOT within 10 ms"),
           6: ("the UARTs yes", "the UARTs NOT all within 10 ms")}
    bad = [bit for bit, (yes, no) in own.items()
           if yes not in "\n".join(gl(0x28, 0x80 | 1 << bit)) or any(o[0] in "\n".join(gl(0x28, 0x80 | 1 << bit)) for b2, o in own.items() if b2 != bit)
           or no not in "\n".join(gl(0x28, 0xff & ~(1 << bit))) or any(o[1] in "\n".join(gl(0x28, 0xff & ~(1 << bit))) for b2, o in own.items() if b2 != bit)]
    check(not bad, "each of the byte's seven bits is read in its own line and no other", str(bad))


PRE_HDR = r"""
#include <stdio.h>
#include "pre_test.h"
int main(void)
{
	static const u32 v[] = { 0x80101100, 0xc0101100, 0x80100100, 0xc0100100, 1, 2, 0, 3, 0x80000001, 0x101, 0x40000001, 0x80101101 };
	u32 was[PRT_N], first[PRT_N], later[PRT_N], back[PRT_N];
	unsigned int i, j, k;
	u8 p[PRT_PACK_N];

	printf("consts %d %d %d %d %x %x %x %x %x %x %d\n", PRT_N, PRT_SEL_N, PRT_A_FIRST, PRT_B_FIRST, PRT_SEL_MORE, PRT_PASS, PRT_NO_SNAP, PRT_MARK,
	       PRT_CLK_BACK, PRT_MORE, PRT_PACK_N);
	printf("at %d %d %d %d %d %d %d %d %d %d %d\n", PRT_AT_NONE, PRT_AT_ZERO, PRT_AT_READ, PRT_AT_CLK, PRT_AT_A, PRT_AT_B, PRT_AT_WAIT, PRT_AT_BACK,
	       PRT_AT_BACK_A, PRT_AT_BACK_B, PRT_AT_DONE);
	printf("codes %d %d %d %d\n", PRT_OTHER, PRT_WRITTEN, PRT_FOUND, PRT_WRITTEN_MORE);
	for (i = 0; i < PRT_N; i++)
		printf("word %u %llx %x %x %x\n", i, (unsigned long long)prt_addr(i), prt_val(i), prt_back_val(i, 0xc0100102), prt_back_val(i, 2));
	for (i = 0; i < 2; i++) {
		for (j = 0; j < sizeof(v) / sizeof(v[0]); j++) {
			for (k = 0; k < sizeof(v) / sizeof(v[0]); k++)
				printf("code %u %x %x %u\n", i * 5, v[j], v[k], prt_code(i * 5, v[j], v[k]));
		}
	}
	/* The pack and the byte, a word at a time: every word as written and as found, then one word at a time something else. */
	for (k = 0; k < 4 * PRT_N + 4; k++) {
		for (i = 0; i < PRT_N; i++) {
			was[i] = i ? 2 : 0x80100100;
			first[i] = later[i] = prt_val(i);
			back[i] = was[i];
		}
		if (k < PRT_N)
			later[k] = was[k];
		else if (k < 2 * PRT_N)
			first[k - PRT_N] = 0x77;
		else if (k < 3 * PRT_N)
			later[k - 2 * PRT_N] = prt_val(k - 2 * PRT_N) | 0x40000000;
		else if (k < 4 * PRT_N)
			back[k - 3 * PRT_N] = 0x55;
		else if (k == 4 * PRT_N + 1)
			back[3] = back[19] = 1;
		else if (k == 4 * PRT_N + 2)
			was[0] = 0xc0100100, back[0] = 0x80100100;
		else if (k == 4 * PRT_N + 3)
			was[0] = back[0] = 0xc0100100;
		prt_pack(was, first, later, k == 2 * PRT_N ? 0xc0101100 : later[0], p);
		printf("pack %u %02x %02x %02x %02x %02x %02x byte %02x\n", k, p[0], p[1], p[2], p[3], p[4], p[5], prt_byte(was, back));
	}
	return 0;
}
"""
PRE_RUN_PRELUDE = r"""
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "pre_test.h"
#define __iomem
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define cpu_relax() do { } while (0)
#define dsb(x) do { } while (0)
#define div64_u64(a, b) ((a) / (b))
#define scnprintf snprintf
"""
PRE_RUN_MODEL = r"""
/*
 * A model of what the 22 words' test touches: the 22, the + 0 word of the last two pages, and the six gate words.
 * A word takes a write unless the scenario says otherwise; 0x28e03801c may read with bit 30 set for a while after a
 * write, or for good; one select may keep what it had, one may read 1 with bit 31 set, one may show a write late. A
 * write to anything but the 22, or a read of anything else, prints STRAY. The clock ticks a millisecond a look at it.
 */
static unsigned char pc_page[0x1000], pa_page[0x1000], pb_page[0x1000], ga_page[0x1000], gb_page[0x1000];
static void *m1_look_pc = pc_page, *m1_look_pa = pa_page, *m1_look_pb = pb_page, *m1_look_ga = ga_page, *m1_look_gb = gb_page;
static u32 reg[22], held[22];
static u64 wrote_at[22], m1_tps = 1000, fake_now;
static int written[22];
static struct scenario {
	int clk;		/* 0x28e03801c: 0 takes a write at once; 1 reads with bit 30 set for `late` ticks after one; 2 for good; 3 doesn't take B2's value */
	unsigned int late;
	int stuck, more, slow;	/* a select's number, 1 to 21, or 0: it keeps what it had; reads 1 with bit 31 set; shows a write `late` ticks on */
	int back_clk, back1, back2;	/* on the way back: 0x28e03801c doesn't take its value; these two selects don't */
	int kfail;		/* not acked, and nothing written: 1 the zeroing of 0x6011-0x6016, 2 the command that carries the reads, 4 the first write's mark, 8 the zeroing of 0x6005 */
	int stale;		/* an earlier test's bytes are still in the PMU */
	int reverts;		/* 0x28e03801c reads as found again once a select is written 1 */
	u32 found0;		/* 0x28e03801c as found, if not 0x80100100 */
} sc;
static int restoring;
static u8 pmu[0x20];	/* 0x6000-0x601f */

static u64 m1_now(void) { return fake_now++; }
static int which(const void *p)
{
	const unsigned char *q = p;
	int i;

	if (q == pc_page + 0x1c)
		return 0;
	for (i = 1; i <= 7; i++) {
		if (q == pa_page + 4 * i)
			return i;
	}
	for (i = 1; i <= 14; i++) {
		if (q == pb_page + 4 * i)
			return 7 + i;
	}
	return -1;
}
static unsigned long long phys(int r)
{
	return !r ? 0x28e03801cULL : r <= 7 ? 0x28e0a0000ULL + 4 * r : 0x28e5b0000ULL + 4 * (r - 7);
}
static u32 readl(const void *p)
{
	static const u16 ga[] = { 0x050, 0x064, 0x30c }, gb[] = { 0x310, 0x338, 0x34c };
	static const u32 gav[] = { 0x8004030f, 0xc0040302, 0xc0040308 }, gbv[] = { 0x8004031d, 0xc004030f, 0x8004030e };
	const unsigned char *q = p;
	int r = which(p);
	unsigned int i;

	if (!r)
		return reg[0] | (written[0] && (sc.clk == 2 || (sc.clk == 1 && fake_now - wrote_at[0] < sc.late)) ? 0x40000000U : 0);
	if (r > 0 && r == sc.slow && written[r] && fake_now - wrote_at[r] < sc.late)
		return held[r];
	if (r > 0)
		return reg[r] | (r == sc.more && reg[r] == 1 ? 0x80000000U : 0);
	if (q == pa_page)
		return 0x02000000;
	if (q == pb_page)
		return 0x02000001;
	for (i = 0; i < 3; i++) {
		if (q == ga_page + ga[i])
			return gav[i];
		if (q == gb_page + gb[i])
			return gbv[i];
	}
	printf("STRAY read\n");
	return 0;
}
static void writel(u32 v, void *p)
{
	int r = which(p);

	if (r < 0) {
		printf("STRAY write %08x\n", v);
		return;
	}
	printf("w %llx %08x\n", phys(r), v);
	if (!r ? (restoring ? sc.back_clk : sc.clk == 3) : restoring ? r == sc.back1 || r == sc.back2 : r == sc.stuck)
		return;
	held[r] = reg[r];
	reg[r] = v;
	wrote_at[r] = fake_now;
	written[r] = 1;
	if (r && !restoring && sc.reverts)
		reg[0] = sc.found0 ? sc.found0 : 0x80100100;
}
static int m1_kwrite(unsigned int addr, const u8 *b, unsigned int n)
{
	int fail = (addr == 0x6011 && (sc.kfail & 1)) || (addr == LOOK_AT && n > 1 && (sc.kfail & 2)) ||
		   (addr == LOOK_AT && n == 1 && b[0] == 0x83 && (sc.kfail & 4)) || (addr == 0x6005 && !b[0] && (sc.kfail & 8));
	unsigned int i;

	if (!m1_pmu_ok(true, addr, b, n) || addr < 0x6000 || addr + n > 0x6020) {
		printf("STRAY write to the PMU's %x, which m1_pmu_ok() refuses\n", addr);
		return -1;
	}
	/* The test puts things back from its BACK mark on, whether or not that mark's write is acked. */
	if (addr == LOOK_AT)
		restoring = (b[0] & 0xf) >= 7;
	if (addr == LOOK_AT && n == 1) {
		printf("at %02x%s\n", b[0], fail ? " FAILED" : "");
	} else {
		printf("k %x", addr);
		for (i = 0; i < n; i++)
			printf(" %02x", b[i]);
		printf("%s\n", fail ? " FAILED" : "");
	}
	if (fail)
		return -5;
	memcpy(pmu + (addr - 0x6000), b, n);
	return 0;
}
static void pr_info(const char *fmt, ...) { va_list ap; va_start(ap, fmt); printf("log "); vprintf(fmt, ap); va_end(ap); }
"""
PRE_RUN_MAIN = r"""
static void run(unsigned int n, const struct scenario *s)
{
	static const u32 found[22] = { 0x80100100, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 2, 2, 1, 1 };
	unsigned int i;

	sc = *s;
	fake_now = 0;
	restoring = 0;
	memcpy(reg, found, sizeof(reg));
	if (sc.found0)
		reg[0] = sc.found0;
	memset(written, 0, sizeof(written));
	memset(pmu, 0, sizeof(pmu));
	pmu[4] = LOOK_STAGE;
	if (sc.stale) {
		pmu[5] = 0xfd;
		memcpy(pmu + 0x11, "\xee\x1e\x0e\x10\x0e\x10", 6);
	}
	printf("case %u\n", n);
	m1_pre_test();
	printf("pmu %02x %02x %02x %02x %02x %02x %02x %02x\n", pmu[5], pmu[0x10], pmu[0x11], pmu[0x12], pmu[0x13], pmu[0x14], pmu[0x15], pmu[0x16]);
	printf("end");
	for (i = 0; i < 22; i++)
		printf(" %08x", reg[i]);
	printf("\n");
}

int main(void)
{
	struct scenario s[21];
	unsigned int i;

	memset(s, 0, sizeof(s));
	s[1].clk = 1; s[1].late = 30;		/* bit 30 clears within the poll */
	s[2].clk = 1; s[2].late = 150;		/* after the poll, before the read 100 ms on */
	s[3].clk = 2;				/* never */
	s[4].clk = 3;				/* 0x28e03801c doesn't take B2's value */
	s[5].stuck = 9;				/* 0x28e5b0008 keeps its 2 */
	s[6].more = 3;				/* 0x28e0a000c reads 0x80000001 */
	s[7].slow = 12; s[7].late = 50;		/* 0x28e5b0014 shows its write late */
	s[8].back_clk = 1;			/* 0x28e03801c doesn't go back */
	s[9].back1 = 5;				/* 0x28e0a0014 doesn't */
	s[10].back1 = 5; s[10].back2 = 19;	/* nor does 0x28e5b0030 */
	s[11].kfail = 1; s[11].stale = 1;	/* the zeroing isn't acked; this test's reads overwrite the old ones all the same */
	s[12].kfail = 2;			/* the write of its reads isn't acked */
	s[13].kfail = 3; s[13].stale = 1;	/* neither is: the old bytes stay, and the marks say so */
	s[14].stale = 1;			/* zeroed, then this test's */
	s[15].found0 = 0xc0100100;		/* found with bit 30 set: written back without it */
	s[16].stuck = 16;			/* 0x28e5b0024, found 0, keeps it */
	s[17].stuck = 20;			/* 0x28e5b0034, found 1: B2's value already */
	s[18].kfail = 4;			/* the PMU doesn't take the first write's mark: nothing is written */
	s[19].reverts = 1;			/* 0x28e03801c takes its value, and reads as found again once the 21 are written */
	s[20].kfail = 8; s[20].stale = 1;	/* an old byte at 0x6005 that can't be zeroed: no mark, nothing written (Pullfrog on #97) */
	for (i = 0; i < ARRAY_SIZE(s); i++)
		run(i, &s[i]);
	return 0;
}
"""
PRE_LOAD_MAIN = r"""
int main(void)
{
	pre_test = true;
	load(0, true, true, true, 0);	/* pre_test=1: its 22 words asked about, none on the list; the two + 0 words read last, under their mark */
	never_at = 0x28e03801cULL;
	load(1, true, true, true, 0);	/* the first on the list */
	never_at = 0x28e5b0038ULL;
	load(2, true, true, true, 0);	/* the last */
	never_at = 0;
	pre_test = false;
	load(3, true, true, true, 0);	/* without pre_test=1: none asked about, no read of a + 0 word */
	return 0;
}
"""


def pre_tests(g, src):
    """probe=1 look=1 pre_test=1 (e1-hook/probe --pre): pre_test.h against a second writing-out of it; m1_pre_test() and its
    helpers as they stand against a model of the 22 words; the load; the places that call it; and its record through m1-crumbs."""
    def cut(start, end):
        at = src.index(start)
        return src[at:src.index(end, at) + len(end)]

    with tempfile.TemporaryDirectory() as tmp:
        def build(name, code):
            c = os.path.join(tmp, name + ".c")
            with open(c, "w") as f:
                f.write(code)
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", os.path.join(tmp, name), c], check=True)
            return subprocess.run([os.path.join(tmp, name)], capture_output=True, text=True, check=True).stdout.splitlines()

        hdr = [line.split() for line in build("phdr", PRE_HDR)]
        defs = "".join(line + "\n" for line in src.splitlines() if re.match(r"#define (M1_PMU_ALARM\w*|M1_PMU_RTC|M1_ALARM_ON)\s", line))
        pieces = (cut("static void m1_look_at(u8 group)", "\n}\n") + cut("static void __iomem *m1_pre_word(unsigned int i)", "\n}\n")
                  + cut("static void m1_pre_log(const char *when, const u32 *v)", "\n}\n") + cut("static void m1_pre_beside(const char *when, bool gates)", "\n}\n"))
        ran = build("prun", PRE_RUN_PRELUDE + defs + "static bool alarm = true;\n" + cut("static bool m1_pmu_ok(bool write,", "\n}\n")
                    + PRE_RUN_MODEL + pieces + cut("static void m1_pre_test(void)", "\n}\n") + PRE_RUN_MAIN)
        defs_pos = [line for line in src.splitlines() if re.match(r"#define M1_POS_PAGE\s", line)]
        loads = build("pload", LOOK_RUN_PRELUDE + defs + defs_pos[0] + "\nstatic bool alarm = true;\n" + cut("static bool m1_pmu_ok(bool write,", "\n}\n")
                      + LOOK_RUN_MODEL + cut("#define LOOK_PRE_ROW", "\n") + cut("static void m1_look_at(u8 group)", "\n}\n")
                      + cut("static void m1_look(const char *when, u8 pass)", "\n}\n") + cut("static void m1_pre_beside(const char *when, bool gates)", "\n}\n")
                      + cut("static int __init m1_look_init(void)", "\n}\n") + LOOK_RUN_MAIN.split("int main(void)")[0] + PRE_LOAD_MAIN)

    # The header. The 22 by address, B2's values, what goes back, the marks, the codes, the six bytes and the byte.
    words = [0x28e03801c] + [0x28e0a0004 + 4 * i for i in range(7)] + [0x28e5b0004 + 4 * i for i in range(14)]
    B2 = [0x80101100] + [1] * 21

    def code(i, v, found):
        return (1 if v == B2[i] else 2 if v == found else 3 if (v & ~0x40000000 == B2[i] if not i else v & 0xff == 1) else 0)

    def pack(was, first, later, poll):
        bits = sum(code(i, later[i], was[i]) << 2 * (i - 1) for i in range(1, 22)) | code(0, poll, was[0]) << 42 | code(0, later[0], was[0]) << 44
        bits |= (all(first[i] == later[i] for i in range(1, 22)) << 46) | (bool(first[0] & 0x40000000) << 47)
        return list(bits.to_bytes(6, "little"))

    def byte(was, back):
        bad = [i for i in range(1, 22) if back[i] != was[i]]
        return 0x80 | (0x40 if back[0] == was[0] & ~0x40000000 else 0) | (0x20 if len(bad) > 1 else 0) | (bad[0] if bad else 0)

    check(hdr[0] == ["consts", "22", "21", "1", "8", "ffffff00", "80", "40", "80", "40", "20", "6"] and hdr[1] == ["at"] + [str(i) for i in range(11)]
          and hdr[2] == ["codes", "0", "1", "2", "3"]
          and [r for r in hdr if r[0] == "word"] == [["word", str(i), f"{words[i]:x}", f"{B2[i]:x}", "c0100102" if i else "80100102", "2"] for i in range(22)]
          and g["PRT_WORDS"] == tuple(words) and g["PRT_PASS"] == 0x80 and g["PRT_NO_SNAP"] == 0x40 and len(g["PRT_AT"]) == 11
          and (g["PRT_BACK"], g["PRT_DONE"]) == (7, 10) and (g["PRT_MARK"], g["PRT_CLK_BACK"], g["PRT_MORE"]) == (0x80, 0x40, 0x20),
          "pre_test.h: the 22 by address (0x28e03801c, seven from 0x28e0a0004, fourteen from 0x28e5b0004), B2's values for them, only"
          " 0x28e03801c written back less its bit 30; eleven marks with 0x80 set, 0x40 for reads that weren't written; as m1-crumbs has"
          " them", str(hdr[:3]))
    codes = [r for r in hdr if r[0] == "code"]
    check(len(codes) == 288 and all(int(r[4]) == code(int(r[1]), int(r[2], 16), int(r[3], 16)) for r in codes)
          and ["code", "0", "c0101100", "80100100", "3"] in codes and ["code", "5", "80000001", "2", "3"] in codes and ["code", "5", "1", "1", "1"] in codes
          and ["code", "5", "2", "2", "2"] in codes and ["code", "0", "c0100100", "80100100", "0"] in codes and ["code", "5", "40000001", "2", "0"] not in codes,
          "pre_test.h: a read is as written, as found, as written with more set (bit 30 for 0x28e03801c, above the low byte for the 21),"
          " or none of these, in that order", str(codes[:5]))
    packs, bad = [r for r in hdr if r[0] == "pack"], []
    for r in packs:
        k = int(r[1])
        was, first, later, back = [0x80100100] + [2] * 21, list(B2), list(B2), [0x80100100] + [2] * 21
        if k < 22:
            later[k] = was[k]
        elif k < 44:
            first[k - 22] = 0x77
        elif k < 66:
            later[k - 44] = B2[k - 44] | 0x40000000
        elif k < 88:
            back[k - 66] = 0x55
        elif k == 89:
            back[3] = back[19] = 1
        elif k == 90:
            was[0], back[0] = 0xc0100100, 0x80100100
        elif k == 91:
            was[0] = back[0] = 0xc0100100
        want = [f"{x:02x}" for x in pack(was, first, later, 0xc0101100 if k == 44 else later[0])] + ["byte", f"{byte(was, back):02x}"]
        if r[2:] != want:
            bad.append((r, want))
    check(len(packs) == 92 and not bad and packs[88][2:] == ["55", "55", "55", "55", "55", "55", "byte", "c0"] and packs[89][9] == "e3"
          and packs[90][9] == "c0" and packs[91][9] == "80" and packs[66][9] == "80" and packs[67][9] == "c1" and packs[87][9] == "d5"
          and len({tuple(r[2:8]) for r in packs[:66]}) == 46,
          "pre_test.h: the six bytes hold each select's code in its own two bits, 0x28e03801c's at the poll's end and 100 ms on, whether"
          " the 21 read the same at once as later, and bit 30 right after the write; the byte names the first select that isn't back,"
          " says if there are more, and judges 0x28e03801c against its found value less bit 30", str(bad[:2]))

    # m1_pre_test() as it stands, twenty-one scenarios.
    cases, cur = [], None
    for line in ran:
        if line.startswith("case "):
            cur = []
            cases.append(cur)
        else:
            cur.append(line)
    F = [0x80100100, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 2, 2, 1, 1]
    a_order = [0x04, 0x08, 0x0c, 0x10, 0x14, 0x18, 0x1c]
    b_order = [0x04, 0x0c, 0x10, 0x14, 0x08, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38]
    sc = [dict() for _ in range(21)]
    sc[1], sc[2], sc[3], sc[4] = dict(clk=1, late=30), dict(clk=1, late=150), dict(clk=2), dict(clk=3)
    sc[5], sc[6], sc[7] = dict(stuck=9), dict(more=3), dict(slow=12)
    sc[8], sc[9], sc[10] = dict(back_clk=1), dict(back=[5]), dict(back=[5, 19])
    sc[11], sc[12], sc[13], sc[14] = dict(kfail=1, stale=1), dict(kfail=2), dict(kfail=3, stale=1), dict(stale=1)
    sc[15], sc[16], sc[17] = dict(found0=0xc0100100), dict(stuck=16), dict(stuck=20)
    sc[18], sc[19], sc[20] = dict(kfail=4), dict(reverts=1), dict(kfail=8, stale=1)

    def row(c, when):
        """The 22 words of the log line that starts with when."""
        line = [x for x in c if x.startswith("log look: pre: " + when + ": 0x28e03801c ")]
        m = re.fullmatch(r"log look: pre: .*: 0x28e03801c ([0-9a-f]{8}); from 0x28e0a0004:((?: [0-9a-f]{8}){7}); from 0x28e5b0004:((?: [0-9a-f]{8}){14})", line[0])
        return [int(x, 16) for x in [m.group(1)] + m.group(2).split() + m.group(3).split()]

    bad = []
    for n, (c, s) in enumerate(zip(cases, sc)):
        found = [s.get("found0", F[0])] + F[1:]
        clk = s.get("clk", 0)
        first = [{0: 0x80101100, 1: 0xc0101100, 2: 0xc0101100, 3: found[0]}[clk]] + [1] * 21
        later = [{0: 0x80101100, 1: 0x80101100, 2: 0xc0101100, 3: found[0]}[clk]] + [1] * 21
        poll = 0xc0101100 if clk == 2 or (clk == 1 and s["late"] > 100) else later[0]
        if s.get("reverts"):
            later[0] = found[0]
        if "stuck" in s:
            first[s["stuck"]] = later[s["stuck"]] = found[s["stuck"]]
        if "more" in s:
            first[s["more"]] = later[s["more"]] = 0x80000001
        if "slow" in s:
            first[s["slow"]] = found[s["slow"]]
        back = [found[0] & ~0x40000000] + found[1:]
        if clk == 2:
            back[0] |= 0x40000000
        if clk == 3:
            back[0] = found[0]
        if s.get("back_clk"):
            back[0] = 0x80101100
        for i in s.get("back", []):
            back[i] = 1
        kfail, stale = s.get("kfail", 0), s.get("stale", 0)
        snap = 0x40 if kfail & 2 else 0
        pk, by = pack(found, first, later, poll), byte(found, back)
        ev = [x for x in c if x[:2] in ("w ", "at", "k ")]
        if kfail & 8:
            # 0x6005 couldn't be zeroed: no mark of the test's, no register write, the old bytes as they were.
            if ([x for x in c if x[:2] in ("w ", "at", "k ")] != ["k 6005 00 FAILED"] or "pmu fd 00 ee 1e 0e 10 0e 10" not in c
                    or c[-1] != "end " + " ".join(f"{x:08x}" for x in found) or any("STRAY" in x or x.startswith("log look: pre: as found") for x in c)
                    or not [x for x in c if x.startswith("log look: pre: SUMMARY: NOT MADE: the PMU's 0x6005 couldn't be zeroed (-5)")]):
                bad.append((n, c))
            continue
        want = (["k 6005 00", "at 81", "k 6011 00 00 00 00 00 00" + (" FAILED" if kfail & 1 else ""), "at 82", "at 83", "w 28e03801c 80101100", "at 84"]
                + [f"w {0x28e0a0000 + o:x} 00000001" for o in a_order] + ["at 85"] + [f"w {0x28e5b0000 + o:x} 00000001" for o in b_order] + ["at 86"]
                + ["k 6010 87 " + " ".join(f"{x:02x}" for x in pk) + (" FAILED" if snap else "")] + (["at c7"] if snap else [])
                + [f"w 28e03801c {found[0] & ~0x40000000:08x}", f"at {0x88 | snap:02x}"] + [f"w {words[i]:x} {found[i]:08x}" for i in range(1, 8)]
                + [f"at {0x89 | snap:02x}"] + [f"w {words[i]:x} {found[i]:08x}" for i in range(8, 22)] + [f"at {0x8a | snap:02x}", f"k 6005 {by:02x}"])
        if kfail & 4:
            # The PMU didn't take the first write's mark: no register write, no later mark, and the SUMMARY says so.
            if ([x for x in c if x[:2] in ("w ", "at", "k ")] != want[:4] + ["at 83 FAILED"] or row(c, "as found") != found
                    or "pmu 00 82 00 00 00 00 00 00" not in c or c[-1] != "end " + " ".join(f"{x:08x}" for x in found) or any("STRAY" in x for x in c)
                    or not [x for x in c if x.startswith("log look: pre: SUMMARY: NOT MADE: the PMU didn't take the mark of the first write (-5)")]):
                bad.append((n, c))
            continue
        old = [0xee, 0x1e, 0x0e, 0x10, 0x0e, 0x10]
        left = (old if stale and kfail == 3 else [0] * 6) if snap else pk
        pmu = "pmu " + " ".join(f"{x:02x}" for x in [by, 0x8a | snap] + left)
        got = (row(c, "as found"), row(c, r"right after the writes of 1 \(0x28e03801c: right after its own\)".replace("\\", "")), row(c, "100 ms after the writes"),
               row(c, "with all 22 written back"))
        if (ev != want or got != (found, first, later, back) or pmu not in c or c[-1] != "end " + " ".join(f"{x:08x}" for x in [back[0] & ~0x40000000] + back[1:])
                or any("STRAY" in x for x in c)):
            bad.append((n, [x for x in zip(ev, want) if x[0] != x[1]][:3], pmu, c[-2:]))
    check(len(cases) == 21 and not bad,
          "m1_pre_test() in twenty-one scenarios: 44 writes and no other, or none at all if the PMU doesn't take the zeroing of 0x6005 (and then no mark either) or the first write's mark, B2's values to the 22 in B2's order, then each as found (0x28e03801c"
          " first and less its bit 30, the 21 by address), every mark before its step, the six bytes in one command with the next step's"
          " mark before anything is written back, the log's four rows of 22 as the model has them, and the byte last", str(bad[:2]))

    def logs(n, what):
        return [x for x in cases[n] if x.startswith("log look: pre: " + what)]

    summ = [logs(n, "SUMMARY")[0] for n in range(21)]
    check(all("0x28e03801c took B2's value, bit 30 clear within 100 ms; 21 of the 21 read 1, as B2 writes them, 100 ms after their writes (2 were found at 1, and show nothing);" in summ[n] and summ[n].endswith("put back: 0x28e03801c as found, the 21 as found; byte c0, written to the PMU: 0")
              for n in (0, 1, 11, 12, 13, 14, 15))
          and "0x28e03801c read as B2 writes it only after its 100 ms, by the read 100 ms after the 21 were written (later, or once they were in); 21 of the 21" in summ[2]
          and "0x28e03801c did NOT hold B2's value; 21 of the 21" in summ[19] and "byte c0" in summ[19]
          and "did NOT finish taking B2's value: bit 30, pending, still SET; 21 of the 21" in summ[3] and "put back: 0x28e03801c NOT as found, the 21 as found; byte 80" in summ[3]
          and "0x28e03801c did NOT hold B2's value; 21 of the 21" in summ[4] and "byte c0" in summ[4]
          and all("; 20 of the 21 read 1" in summ[n] for n in (5, 6, 16)) and "; 21 of the 21 read 1" in summ[7] and "; 21 of the 21 read 1" in summ[17]
          and "put back: 0x28e03801c NOT as found, the 21 as found; byte 80" in summ[8]
          and "put back: 0x28e03801c as found, the 21 NOT all as found; byte c5" in summ[9] and "the 21 NOT all as found; byte e5" in summ[10]
          and "it read c0101100 at once and reads 80101100 after 30 ms: as written, bit 30 clear" in logs(1, "0x28e03801c found")[0]
          and "reads c0101100 after 101 ms: NOT as written: bit 30, pending, still SET" in logs(2, "0x28e03801c found")[0]
          and "reads 80100100 after 101 ms: NOT as written\n" in logs(4, "0x28e03801c found")[0] + "\n"
          and all(logs(n, "as found: 0x28e0a0000 02000000, 0x28e5b0000 02000001") and len(logs(n, "as found: gates 0x28e5a0000: +050 8004030f +064 c0040302")) == 1
                  and len([x for x in cases[n] if "0x28e0a0000 02000000, 0x28e5b0000 02000001" in x]) == 4
                  and len([x for x in cases[n] if ": gates 0x28e5a0000: +050 8004030f" in x]) == 3 for n in range(20) if n != 18) and not logs(20, "as found"),
          "m1_pre_test()'s log: the SUMMARY says what 0x28e03801c did, how many of the 21 read 1, and what came back; the poll of bit 30"
          " ends when the word reads as written and waits 100 ms and no longer (101 here: the model's clock ticks at each look); the two"
          " + 0 words are read four times, once before anything else, and the six gates three times", summ[2])

    # The load.
    lcases, cur = [], None
    for line in loads:
        if line.startswith("load "):
            cur = []
            lcases.append(cur)
        else:
            cur.append(line)
    nev = [[x for x in c if x.startswith("never ")] for c in lcases]
    check(len(lcases) == 4 and nev[0] == [f"never {w:x} 1" for w in words] and lcases[0][-1] == "ret 0"
          and nev[1] == nev[0][:1] and lcases[1][-1] == "ret -1" and nev[2] == nev[0] and lcases[2][-1] == "ret -1" and nev[3] == [] and lcases[3][-1] == "ret 0"
          and not any(x.startswith(("r ", "k ")) for c in lcases[1:3] for x in c),
          "pre_test=1 at load: each of the 22 asked of the never-write list before anything is read, and a load with one on it refused;"
          " without pre_test=1 none is asked about", str(nev[1:3]) + str(lcases[1][-3:]))
    tail0 = [x for x in lcases[0] if x.startswith(("k ", "r ", "log look: pre:"))][-7:]
    check(tail0[0] == "k 6010 17" and tail0[1:4] == ["k 6010 91", "r 28e0a0000", "r 28e5b0000"]
          and tail0[4].startswith("log look: pre: awake, at load: 0x28e0a0000 ") and tail0[5:] == ["k 6010 00", "k 6004 00"]
          and not any(x in ("r 28e0a0000", "r 28e5b0000", "k 6010 91") or x.startswith("log look: pre:") for x in lcases[3]),
          "pre_test=1 at load: after the look awake, the two + 0 words are read under a mark of their own (0x91), which is zeroed after;"
          " a load without it reads neither", str(tail0))

    # Where it's called from, and what it may write through.
    code_only = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', re.sub(r"/\*.*?\*/", "", src, flags=re.S))
    late = cut("static int e1_prepare_late(void)", "\n}\n")
    params = cut("static int __init e1_check_params(void)", "\n}\n")
    pt = cut("static void m1_pre_test(void)", "\n}\n")
    linit = cut("static int __init m1_look_init(void)", "\n}\n")
    wr = r"(?<![A-Za-z0-9_])write[bwlq](?:_relaxed)?\([^;]*;"
    check("static bool pre_test;\nmodule_param(pre_test, bool, 0444);\n" in src and not re.search(r"\bpre_test\s*=[^=]", code_only)
          and in_order_of(params, ["\tif (gate && !look) {\n", "\tif (pre_test && (!look || gate)) {\n\t\tpr_err(", "\t\treturn -EINVAL;\n\t}\n\tif (probe && (!wd1_mark || !pmp_stop)) {"])
          and "\t\tif (look && n == 1) {\n\t\t\tm1_look(\"after noirq\", 0);\n\t\t\tif (gate)\n\t\t\t\tm1_gate_test();\n\t\t\tif (pre_test)\n\t\t\t\tm1_pre_test();\n\t\t} else if (!look && same) {\n" in late
          and src.count("m1_pre_test();") == 1,
          "pre_test is off unless asked for, read-only once loaded, only with look=1 and never with gate=1; its test runs once, right after"
          " the look that follows noirq, in the suspend probe=1 then fails", late[-1200:])
    check(re.findall(wr, pt) == ["writel(SST_CLK_VAL, m1_pre_word(0));", "writel(SST_PRE_VAL, m1_look_pa + pa[i]);", "writel(SST_PRE_VAL, m1_look_pb + pb[i]);",
                                 "writel(prt_back_val(0, was[0]), m1_pre_word(0));", "writel(prt_back_val(i, was[i]), m1_pre_word(i));",
                                 "writel(prt_back_val(i, was[i]), m1_pre_word(i));"]
          and not re.findall(wr, cut("static void m1_pre_log(const char *when, const u32 *v)", "\n}\n"))
          and not re.findall(wr, cut("static void m1_pre_beside(const char *when, bool gates)", "\n}\n"))
          and "m1_ps" not in pt and "m1_sio" not in pt and "M1_PS_PMP_RO" not in pt and pt.count("m1_kwrite(0x6005, &res, 1)") == 1
          and "static const u8 pa[SST_A_N] = SST_A_OFFS, pb[SST_B_N] = SST_B_OFFS;" in pt
          and "static_assert(PRT_N == 1 + SST_A_N + SST_B_N && PRT_B_FIRST == PRT_A_FIRST + SST_A_N && PRT_SEL_N == PRT_N - 1 && PRT_SEL_N <= 0x1f);\n" in src
          and "static_assert(LOOK_PRE_FIRST + 4 * (SST_A_N - 1) == LOOK_PRE_A_LAST && LOOK_PRE_FIRST + 4 * (SST_B_N - 1) == LOOK_PRE_B_LAST);\n" in src
          and in_order_of(linit, ["\tif (pre_test) {\n", "\t\t\tif (m1_never(prt_addr(i), 1))\n\t\t\t\tbreak;\n", "\t\tif (i < PRT_N) {\n", "\t\t\treturn -EPERM;\n",
                                  "\tm1_look(\"awake, at load\", LOOK_LOAD);", "\tif (pre_test) {\n\t\tm1_look_at(PRT_PASS | LOOK_LOAD | PRT_AT_ZERO);\n\t\tm1_pre_beside(\"awake, at load\", false);\n\t}\n",
                                  "\tm1_look_at(LOOK_AT_NONE);"]),
          "the 22 words' test has six writel()s, each to one of the 22 through look=1's three mappings, with sio_steps.h's lists for B2's"
          " order; it touches no power page; the load refuses if one of the 22 is on the never-write list", pt[:600])

    # Its record through m1-crumbs.
    def pl(n):
        b = [int(x, 16) for x in [x for x in cases[n] if x.startswith("pmu ")][0].split()[1:]]
        return "\n".join(g["prt_lines"](b[1], b[0], bytes(b[2:])))

    out = [pl(n) for n in range(20)]
    check("0x6010 = 0x8a: the 22 words' test, after both looks: every step taken" in out[0] and "at the read right after the write; at the end of up to 100 ms it read as written, bit 30 clear; 100 ms after the last write it read as written, bit 30 clear" in out[0]
          and "bit 30 clear at the read" in out[0] and "bit 30 SET at the read" in out[1]
          and "21 read as written\n" in out[0] and "each of the 21 read the same" in out[0] and "so: ALL 22 READ AS B2 WRITES THEM in Linux's state after noirq, with every core up, at once and 100 ms on (0x28e5b0034 and 0x28e5b0038 read 1 before any write in both looks, and show nothing: 19 of the 21 are shown). In the sio_steps=1 run it was then the recipe's state" in out[0]
          and "0x6005 = 0xc0: with everything written back: 0x28e03801c read as found; all 21 read as found within 100 ms" in out[0]
          and "so: ALL 22 READ" in out[1]
          and "so: all 22 read as B2 writes them 100 ms after the last write, but NOT AT FIRST: 0x28e03801c didn't read as written, bit 30 clear, by the end of its 100 ms, which ended before the 21 were written (it came later, or once they were in: this can't tell). sio_steps=1 read the 21 at once" in out[2]
          and "recipe's state is blamed" in out[2] and "ALL 22 READ" not in out[2]
          and "it read as written but with bit 30, pending, still SET; 100 ms after the last write it read as written, bit 30 clear" in out[2]
          and "so: the 21 read 1; 0x28e03801c has B2's value but its bit 30, pending, was still set 100 ms after the last write" in out[3]
          and "0x6005 = 0x80: with everything written back: 0x28e03801c did NOT read as found; all 21" in out[3]
          and "it read as found: the write didn't take; 100 ms after" in out[4] and "so: the 21 read 1; 0x28e03801c did NOT hold B2's value 100 ms after the last write: the 21 alone" in out[4]
          and "so: the 21 read 1; 0x28e03801c did NOT hold B2's value 100 ms after the last write, though it read as written at the end of its own 100 ms: it changed once the 21 were written: the 21 alone" in out[19]
          and "20 read as written\n" in out[5] and "1 read as found: the write didn't take: 0x28e5b0008\n" in out[5] and "so: NOT ALL OF THE 21 READ 1 after the AP's write, with the cores up: the words named above. Why, this doesn't say" in out[5]
          and "1 read as written with more bits set: 0x28e0a000c\n" in out[6] and "so: NOT ALL OF THE 21" in out[6]
          and "NOT every one of the 21 read the same right after" in out[7] and "ALL 22 READ" not in out[7]
          and "but NOT AT FIRST: a select read otherwise right after its group's writes. sio_steps=1 read the 21 at once" in out[7]
          and "1 read as found: the write didn't take: 0x28e5b0024\n" in out[16] and "so: NOT ALL OF THE 21" in out[16] and "so: ALL 22 READ" in out[17]
          and "0x6010 = 0x82: THE 22 WORDS' TEST BEGAN AND DIDN'T FINISH" in out[18] and "so:" not in out[18],
          "m1-crumbs reads the six bytes: what 0x28e03801c read and whether bit 30 cleared, which of the 21 read 1 and what the others read,"
          " and what that says", out[5])
    check("0x6005 = 0x80: with everything written back: 0x28e03801c did NOT read as found; all 21 read as found" in out[8]
          and "0x6005 = 0xc5: with everything written back: 0x28e03801c read as found; 0x28e0a0014 did NOT read as found within 100 ms\n" in out[9] + "\n"
          and "0x6005 = 0xe5: with everything written back: 0x28e03801c read as found; 0x28e0a0014 did NOT read as found within 100 ms, and it isn't the only one" in out[10]
          and "so: ALL 22 READ" in out[11] and "so: ALL 22 READ" in out[14]
          and "0x6010 = 0xca: the 22 words' test, after both looks: every step taken" in out[12]
          and "0x6011-0x6016 read 0: the test's reads weren't written (the mark says the write that carries them wasn't acked)" in out[12] and "so:" not in out[12]
          and "0x6011-0x6016 hold ee 1e 0e 10 0e 10: NOT this test's reads (the mark says the write that carries them wasn't acked): nothing is read from them" in out[13]
          and "so:" not in out[13] and "0x6005 = 0xc0" in out[13],
          "m1-crumbs reads the byte: what came back, and the first select that didn't; and reads nothing from six bytes whose command"
          " wasn't acked", out[13])
    pr = g["prt_lines"]
    full = bytes(pack(F, list(B2), list(B2), 0x80101100))
    mid = ["\n".join(pr(0x80 | n, 0, full)) for n in range(1, 10)]
    check(all("THE 22 WORDS' TEST BEGAN AND DIDN'T FINISH" in m and g["PRT_AT"][n + 1] in m for n, m in enumerate(mid))
          and all(("a word it had changed may not have been written back" in m) == (3 <= n + 1 <= 9) for n, m in enumerate(mid))
          and all("NOT this test's reads (the last mark that landed is from before their write)" in m and "so:" not in m for m in mid[:6])
          and all("so: ALL 22 READ" in m for m in mid[6:]) and all("0x6005 = 0x00: the test's byte didn't land" in m for m in mid)
          and "its byte landed, and that is written after the last step" in "\n".join(pr(0x86, 0xc0, bytes(6)))
          and "0x6011-0x6016 read 0: the test's reads weren't written (the last mark that landed is from before their write)" in "\n".join(pr(0x83, 0, bytes(6)))
          and "read 0 though the mark says their write was acked" in "\n".join(pr(0x8a, 0xc0, bytes(6)))
          and "NOT this test's reads (the mark isn't a step's)" in "\n".join(pr(0x8d, 0, full))
          and "the byte's low bits aren't a word's number" in "\n".join(pr(0x8a, 0xd6, full))
          and "at the module's load: the + 0 words of the two pages" in "\n".join(pr(0x91, 0, bytes(6))) and len(pr(0x91, 0, bytes(6))) == 1,
          "m1-crumbs on a test that stopped: the step it marked last; the six bytes are read only under a mark from the write-back on;"
          " a byte with an earlier mark means a lost mark; the load's mark is told apart", mid[2])
    def verdict(poll, late, same=True, pend=False):
        bits = int.from_bytes(full, "little") & ~(0x3f << 42) | poll << 42 | late << 44 | same << 46 | pend << 47
        return [x for x in pr(0x8a, 0xc0, bits.to_bytes(6, "little")) if x.startswith("  so:")]

    v = {(p_, l_): verdict(p_, l_) for p_ in range(4) for l_ in range(4)}
    check(all(len(x) == 1 for x in v.values()) and "ALL 22 READ" in v[1, 1][0]
          and all("NOT AT FIRST: 0x28e03801c didn't read as written" in v[p_, 1][0] and "ALL 22" not in v[p_, 1][0] for p_ in (0, 2, 3))
          and all("has B2's value but its bit 30, pending, was still set 100 ms after the last write" in v[p_, 3][0] for p_ in range(4))
          and all("did NOT hold B2's value 100 ms after the last write" in v[p_, l_][0] for p_ in range(4) for l_ in (0, 2))
          and all("though it read as written at the end of its own 100 ms" in v[1, l_][0]
                  and "and read as written with bit 30 set at the end of its own 100 ms" in v[3, l_][0]
                  and "100 ms after the last write: the 21 alone" in v[0, l_][0] and "100 ms after the last write: the 21 alone" in v[2, l_][0] for l_ in (0, 2))
          and "NOT AT FIRST: a select read otherwise right after its group's writes. sio" in verdict(1, 1, same=False)[0]
          and "by the end of its 100 ms, which ended before the 21 were written (it came later, or once they were in: this can't tell); a select read otherwise" in verdict(3, 1, same=False)[0]
          and "ALL 22 READ" in verdict(1, 1, pend=True)[0],
          "m1-crumbs' verdict on 0x28e03801c for each pair of its two codes: the recipe's state is named only if every word read as"
          " written at once and bit 30 cleared within the poll, which is what sio_steps=1 judged; a word that went back isn't said to"
          " hold B2's value", str(v[3, 2]))
    b = bytearray(32)
    b[4], b[5], b[0x10] = 0x0f, 0xc0, 0x8a
    b[0x11:0x17] = full
    dec = "\n".join(g["decode"](bytes(b), bytes(6)))
    b[0x10] = 0x28
    decg = "\n".join(g["decode"](bytes(b), bytes(6)))
    check("pre_test=1's test, after them, writes the 22 words B2 writes first and writes them back (--pre)" in dec and "so: ALL 22 READ" in dec
          and "the gate test" not in dec and "the gate test" in decg and "22 words" not in decg,
          "m1-crumbs tells the 22 words' test from the gate test and the plain look by the mark's bit 7", dec)


def in_order_of(text, parts):
    at = 0
    for part in parts:
        i = text.find(part, at)
        if i < 0:
            return False
        at = i + 1
    return True


def position_tests(g, src):
    """position=1: the encoder itself, compiled from m1_position.h, against an independent reading of the layout; then
    its bytes through the decoder; then e1_hook.c's side."""
    with tempfile.TemporaryDirectory() as tmp:
        harness = os.path.join(tmp, "harness.c")
        with open(harness, "w") as f:
            f.write(HARNESS)
        exe = os.path.join(tmp, "harness")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", exe, harness],
                       check=True)

        def enc(rows):
            """rows: (phase, hs_a, state, sio, ctrl, count, gate44, gate40, elapsed, tps); returns [(a, b)]."""
            text = "".join(" ".join(f"{v:x}" for v in row) + "\n" for row in rows)
            r = subprocess.run([exe], input=text, capture_output=True, text=True, timeout=60)
            return [tuple(int(x, 16) for x in line.split()) for line in r.stdout.splitlines()]

        def sio_class(v):
            if v == 0xffffffff:
                return 0
            if v & 0xf == 0:
                return 1 if v & 0x800 else 2
            if (v >> 24) & 0xf != 0xf:
                return 3
            return 5 if (v >> 4) & 0xf == 0xf else 4

        # Every combination of the fields the class looks at, with the other bits varied.
        words = [0xffffffff]
        for top, bit11, actual, target in ((t, b, a, g2) for t in range(16) for b in (0, 1) for a in range(16) for g2 in range(16)):
            words.append(top << 24 | bit11 << 11 | actual << 4 | target)
            words.append(top << 24 | 0xf00000 | bit11 << 11 | 0x600 | actual << 4 | target)
        got = enc([(0, 0, 0, w, 0, 0, 0, 0, 0, 24000000) for w in words])
        bad = [hex(w) for w, (_, b) in zip(words, got) if b & 7 != sio_class(w)]
        check(len(got) == len(words) and not bad, f"SIO_CPU's class over {len(words)} words: as the layout says, none overlapping", str(bad[:8]))

        def row(phase=0, hs=0, state=0, sio=0, ctrl=0, count=0, g44=0, g40=0, el=0, tps=24000000):
            return (phase, hs, state, sio, ctrl, count, g44, g40, el, tps)

        a = enc([row(phase=p) for p in range(16)])
        check([x[0] & 0xf for x in a] == list(range(16)), "byte A: bits 3:0 are PHASE's low four bits")
        a = enc([row(phase=0x7f4), row(hs=0x80000000), row(hs=0x01000000), row(hs=0x81000000), row(hs=0x7effffff)])
        check([x[0] & 0x3f for x in a] == [4, 0x10, 0x20, 0x30, 0], "byte A: bit 4 is HS_A bit 31, bit 5 bit 24, nothing else of HS_A", str(a))
        a = enc([row(state=v) for v in (0xa03, 1, 0xa02, 0, 2, 0xa01, 0x1a03, 0xffffffff)])
        check([x[0] >> 6 for x in a] == [0, 1, 2, 3, 3, 3, 3, 3], "byte A: the state word's classes", str(a))
        b = enc([row(ctrl=4), row(ctrl=3), row(ctrl=0xfffffffb), row(ctrl=0xffffffff)])
        check([bool(x[1] & 8) for x in b] == [True, False, False, True], "byte B: bit 3 is RESET_EN (bit 2) alone", str(b))
        b = enc([row(g44=4), row(g44=8), row(g44=0xfffffff3), row(g40=2), row(g40=0xfffffffd)])
        check([x[1] & 0xe0 for x in b] == [0x20, 0x40, 0, 0x80, 0], "byte B: bits 5-7 are the gate words' bits", str(b))
        tps = 24000000
        q = tps // 4
        b = enc([row(count=1000000, el=1000000), row(count=1000000 + q, el=1000000), row(count=1000000 + q + 1, el=1000000),
                 row(count=q + 5000, el=5000), row(count=q + 5001, el=5000), row(count=0, el=q), row(count=0, el=q + 1),
                 row(count=720000000, el=25 * tps), row(count=2000, el=25 * tps)])
        check([bool(x[1] & 0x10) for x in b] == [True, True, False, True, False, True, False, False, False],
              "byte B: bit 4 says WD2's count is within 0.25 s of the time since the first commit, on either side", str(b))

        # The bytes, through the decoder, every stage.
        path = os.path.join(tmp, "nvmem")
        cnt = "4800000000 24000000"
        alarm = (0x41, 125144, 0, 0)
        (s1a, s1b), (s2a, s2b) = enc([
            row(phase=4, hs=0x81010000, state=0xa03, sio=0x0400024f, ctrl=4, count=1 * tps, g44=4, g40=0, el=1 * tps),
            row(phase=5, hs=0x80010000, state=0xa03, sio=0x0f00024f, ctrl=4, count=25 * tps, g44=8, g40=2, el=25 * tps)])
        rec = dict(parked=0xfe, left=0x50, down=0xfe, secs=121544, flags=0xb0, isrs=0, drained=0xbd)
        both = dict(rec, down=s2a, isrs=s2b, prog=(s1a, s1b))

        def decoded(stage, kw, **more):
            fake(path, scratch(stage, **kw), rtc_secs=121761, alarm=alarm, **more)
            r = run(path, cnt=cnt)
            return r.returncode, r.stdout, r.stderr

        LONE = "sample 1 alone, about 1 s after the commit: only stages 0x44 and 0x45 say whether a read failed"
        rc, out, err = decoded(0x45, both)
        check(rc == 0 and "0x6004 = 0x45 (pmgr_late=1): position=1: the samples recorded, CPU0 had nothing pending" in out
              and "read off after the release (0x6005 = 0xfe, up to 10 ms each): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "0x6005 bit 0: SIO_CPU's bits 27:24 did not read f at entry 61's wait, the reference for its class\n" in out
              and "macOS's waits that timed out after 10 ms: DISPLAY's power-down (58); SIO_CPU's power state (61)\n" in out
              and f"position sample 1 (0x6012 = 0x{s1a:02x}, 0x6013 = 0x{s1b:02x}):\n" in out
              and "  PHASE 0x4 (the entry pair's first (A2)); HS_A bit 31 up, bit 24 up; state word 0xa03 (a sequence's start)\n" in out
              and "  SIO_CPU: another target, bits 27:24 not f; WD2 RESET_EN set; WD2's count within 0.25 s of CPU0's time since the first commit write\n" in out
              and "  gate words: 0x2922c0044 bit 2 set, bit 3 clear; 0x2922c0040 bit 1 clear\n" in out
              and f"position sample 2 (0x6007 = 0x{s2a:02x}, 0x6015 = 0x{s2b:02x}):\n" in out
              and "  PHASE 0x5 (the entry pair's second (A2)); HS_A bit 31 up, bit 24 clear; state word 0xa03 (a sequence's start)\n" in out
              and "  SIO_CPU: another target, bits 27:24 f, actual not f; WD2 RESET_EN set; WD2's count within 0.25 s" in out
              and "  gate words: 0x2922c0044 bit 2 clear, bit 3 set; 0x2922c0040 bit 1 set\n" in out
              and "from its low two bytes and the run's alarm: 121544, 217 s before now (121761)" in out
              and "PCPM0 off after the release, PCPM1 off after the release" in out
              and "0x6016 = 0xbd: every alarm command was acked" in out
              and "0x140c is the commit's second + 3600, as the run wrote it" in out
              and "the SoC restarted about" in out and "wrote their stop bit" not in out
              and "cores with an interrupt pending" not in out and "the cores that took the release" not in out
              and "observe=1" not in out and "PLAN.md has the reading guide" in out and LONE not in out,
              "position=1's 0x45: both samples decoded from the module's own bytes", out + err)
        rc, out, err = decoded(0x44, both)
        check(rc == 0 and "0x44 (pmgr_late=1): position=1: the samples recorded, but CPU0 had an interrupt or SError pending" in out
              and "position sample 2" in out and LONE not in out, "position=1's 0x44: the same, with something pending", out + err)

        # Each sample's own lines, exact, with every field differing between the two and the other branch of each taken.
        (t1a, t1b), (t2a, t2b) = enc([
            row(phase=3, hs=0x01000000, state=0xa02, sio=0x00000800, ctrl=4, count=0, g44=8, g40=0, el=1 * tps),
            row(phase=0xe, hs=0x80000000, state=1, sio=0x0f0000ff, ctrl=0, count=25 * tps, g44=4, g40=2, el=25 * tps)])
        rc, out, err = decoded(0x45, dict(rec, down=t2a, isrs=t2b, prog=(t1a, t1b)))
        want1 = (f"position sample 1 (0x6012 = 0x{t1a:02x}, 0x6013 = 0x{t1b:02x}):\n"
                 "  PHASE 0x3 (pair 1's last); HS_A bit 31 down, bit 24 up; state word 0xa02 (A1's close)\n"
                 "  SIO_CPU: target 0, bit 11 set (B2 at op 91, inferred); WD2 RESET_EN set; WD2's count NOT within 0.25 s of CPU0's time since the first commit write\n"
                 "  gate words: 0x2922c0044 bit 2 clear, bit 3 set; 0x2922c0040 bit 1 clear\n")
        want2 = (f"position sample 2 (0x6007 = 0x{t2a:02x}, 0x6015 = 0x{t2b:02x}):\n"
                 "  PHASE 0xe (not a phase the sequences read so far write); HS_A bit 31 up, bit 24 clear; state word 1 (a sequence's end)\n"
                 "  SIO_CPU: another target, bits 27:24 f, actual f; WD2 RESET_EN clear; WD2's count within 0.25 s of CPU0's time since the first commit write\n"
                 "  gate words: 0x2922c0044 bit 2 set, bit 3 clear; 0x2922c0040 bit 1 set\n")
        check(rc == 0 and want1 in out and want2 in out, "each sample's lines say that sample's bits, in both branches of each", out + err)
        rc, out, err = decoded(0x45, dict(both, parked=0xff))
        check(rc == 0 and "0x6005 bit 0: SIO_CPU's bits 27:24 read f at entry 61's wait, the reference for its class\n" in out
              and "up to 10 ms each): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out and "observe=1" not in out,
              "position=1: 0x6005 bit 0 is SIO_CPU's reference, not observe's mark", out + err)
        rc, out, err = decoded(0x43, both)
        check(rc == 0 and "position sample 1" in out and "position sample 2" not in out
              and "0x43 (pmgr_late=1): position=1: about 25 s after the third commit, CPU0 began sample 2's reads" in out
              and "held and step 8's byte, or sample 2's, the stage after them didn't follow: not decoded" in out
              and "wrote their stop bit" not in out and "cores with an interrupt pending" not in out and LONE in out,
              "position=1's 0x43: sample 2's bytes not read as facts, and sample 1 is flagged as alone", out + err)
        rc, out, err = decoded(0x42, dict(rec, prog=(s1a, s1b)))
        check(rc == 0 and "position sample 1" in out and "position sample 2" not in out
              and "0x42 (pmgr_late=1): position=1: sample 1's bytes landed" in out
              and "wrote their stop bit and held (0x6007 = 0xfe)" in out
              and "0x6015 = 0x00: cores with an interrupt pending at their stop request (hold=1): none\n" in out and LONE in out,
              "position=1's 0x42: sample 1 decoded and flagged as alone, 0x6007 and 0x6015 still hold's", out + err)
        rc, out, err = decoded(0x41, dict(rec, prog=(s1a, s1b)))
        check(rc == 0 and "position sample" not in out and "step 8's second's upper bytes or sample 1's" in out
              and "0x41 (pmgr_late=1): position=1: about 1 s after the third commit, CPU0 began sample 1's reads" in out
              and "not decoded" in out and "wrote their stop bit and held (0x6007 = 0xfe)" in out and LONE not in out,
              "position=1's 0x41: sample 1's bytes not read as facts", out + err)
        rc, out, err = decoded(0x40, rec)
        check(rc == 0 and "0x40 (pmgr_late=1): position=1: the release went out and CPU0's watch ended" in out
              and "no sample recorded yet" in out and "position sample" not in out
              and "wrote their stop bit and held (0x6007 = 0xfe)" in out
              and "read off after the release" in out
              and "from its low two bytes and the run's alarm: 121544" in out,
              "position=1's 0x40: the watch's bytes, no sample", out + err)
        fake(path, scratch(0x45, **dict(both, flags=0x30)), rtc_secs=121761, alarm=alarm)
        r = run(path, cnt=cnt)
        check(r.returncode == 0 and "0x6014 = 0x30: bit 7 is clear, so its own one-byte command didn't survive the reset" in r.stdout,
              "position=1: 0x6014 without bit 7 isn't read", r.stdout + r.stderr)

        # --clear must disarm the run's alarm at every position stage, and zero the crumbs.
        for stage in (0x40, 0x41, 0x42, 0x43, 0x44, 0x45):
            before = fake(path, scratch(stage, **both), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after position=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)
        before = fake(path, scratch(0x45, **both), rtc_secs=121761, alarm=(0x41, 200000, 0, 0))
        r = run(path, "--clear", cnt=cnt)
        after = open(path, "rb").read()
        check(r.returncode == 0 and after[0x1401] == 0x41 and "left alone" in r.stdout and "disarmed" not in r.stdout,
              "--clear after position=1 with another alarm armed: left alone", r.stdout + r.stderr)

    # The decoder's names for each class, written here from the plan's grouping and not copied from its tables.
    lines = g["sample_lines"]
    for cls, text in enumerate(("0xa03 (a sequence's start)", "1 (a sequence's end)", "0xa02 (A1's close)", "some other value")):
        got = lines(1, 0x6012, cls << 6, 0x6013, 0)[1]
        check(got.endswith("state word " + text), f"the state word's class {cls} is named {text}", got)
    for cls, text in enumerate(("all ones: a failed read", "target 0, bit 11 set (", "target 0, bit 11 clear (", "another target, bits 27:24 not f;",
                                "another target, bits 27:24 f, actual not f;", "another target, bits 27:24 f, actual f;")):
        got = lines(1, 0x6012, 0, 0x6013, cls)[2]
        check(got.startswith("  SIO_CPU: " + text), f"SIO_CPU's class {cls} is named {text}", got)
    groups = {"the entry pair": (4, 5, 7), "pair 3": (6, 9), "pair 0": (0,), "pair 1": (2, 3, 8, 0xa, 0xb, 0xc, 0xd), "not a phase": (1, 0xe, 0xf)}
    for text, phases in groups.items():
        for phase in phases:
            got = lines(1, 0x6012, phase, 0x6013, 0)[1]
            check(f"PHASE {phase:#x} ({text}" in got, f"PHASE {phase:#x} is named for {text}", got)

    # sio_on=1's run: position=1 with pmp_out=1, so the stages have bit 7, one sample, and 0x6007's bit 0 is the raises.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nvmem")
        cnt = "4800000000 24000000"
        alarm = (0x41, 125144, 0, 0)
        run7 = dict(parked=0xfe, left=0x50, down=0xff, secs=121544, flags=0xb0, isrs=0x8d, drained=0xbd, prog=(0x14, 0xdc))
        ONE = "one sample by design (pmp_out=1 keeps 0x6015), about 1 s after the commit: it can't tell a stop from slow progress"
        ALONE = "sample 1 alone, about 1 s after the commit"
        HELD = "wrote their stop bit and held (0x6007 = 0xff): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n"
        RAISED = "0x6007 bit 0 (sio_on=1): ps_sio, UART_P and SPI_P all read actual f within 10 ms\n"
        NOT_RAISED = "0x6007 bit 0 (sio_on=1): ps_sio, UART_P and SPI_P did NOT all read actual f within 10 ms\n"

        def dec(stage, kw):
            fake(path, scratch(stage, **kw), rtc_secs=121761, alarm=alarm)
            r = run(path, cnt=cnt)
            return r.returncode, r.stdout, r.stderr

        rc, out, err = dec(0xc5, run7)
        check(rc == 0 and "stage 0x6004 = 0xc5 (pmp_out=1: bit 7, so the stage is 0x45) (pmgr_late=1): position=1: the samples recorded, CPU0 had nothing pending" in out
              and "position sample 1 (0x6012 = 0x14, 0x6013 = 0xdc):\n" in out and "position sample 2" not in out
              and ONE + "\n" in out and ALONE not in out
              and HELD in out and RAISED in out
              and "0x6015 = 0x8d: K4 ran" in out and "cores with an interrupt pending" not in out
              and "0x6016 = 0xbd: every alarm command was acked" in out
              and "from its low two bytes and the run's alarm: 121544" in out,
              "sio_on=1's 0xc5: one sample, the raises read on, K4's byte, the alarm's", out + err)
        rc, out, err = dec(0xc5, dict(run7, down=0xfe))
        check(rc == 0 and NOT_RAISED in out and RAISED not in out and "CPU0" not in out.split("held (0x6007")[1].split("\n")[0],
              "sio_on=1's 0xc5 with 0x6007's bit 0 clear: the raises did not all read on", out + err)
        rc, out, err = dec(0xc4, run7)
        check(rc == 0 and "so the stage is 0x44" in out and "CPU0 had an interrupt or SError pending" in out and "position sample 2" not in out and ONE + "\n" in out,
              "sio_on=1's 0xc4: the same, with something pending", out + err)
        rc, out, err = dec(0xc2, run7)
        check(rc == 0 and "so the stage is 0x42" in out and "there's no sample 2" in out and "position sample 1" in out and "position sample 2" not in out
              and ONE + ", and only stages 0x44 and 0x45 say whether a read failed\n" in out and ALONE not in out and HELD in out and RAISED in out,
              "sio_on=1's 0xc2: sample 1 landed, but no last check", out + err)
        rc, out, err = dec(0xc1, run7)
        check(rc == 0 and "so the stage is 0x41" in out and "position sample" not in out and "not decoded" in out and RAISED in out,
              "sio_on=1's 0xc1: its bytes not read as a sample", out + err)
        rc, out, err = dec(0xc0, run7)
        check(rc == 0 and "so the stage is 0x40" in out and "no sample recorded yet" in out and HELD in out and RAISED in out,
              "sio_on=1's 0xc0: the watch's bytes, no sample", out + err)
        at_8 = dict(run7, prog=None)  # 0xda comes before the release: 0x6012-0x6013 are still the RTC second's
        rc, out, err = dec(0xda, at_8)
        check(rc == 0 and "so the stage is 0x5a" in out and RAISED in out and HELD in out, "sio_on=1's 0xda: the raises seen before the commit", out + err)
        rc, out, err = dec(0xda, dict(at_8, down=0xfe))
        check(rc == 0 and "sio_on=1" not in out, "a plain pmp_out=1 run's 0xda says nothing of the raises", out + err)
        for stage in (0xc0, 0xc1, 0xc2, 0xc4, 0xc5, 0xda):
            before = fake(path, scratch(stage, **(at_8 if stage == 0xda else run7)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after sio_on=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)

        # disp_off=1's run: the same with bit 3 set too, and sample 1's byte B is K3b's.
        DISP = "  display (K3b): internal DCP: stopped; external DCP: stopped; the domains of the DCPs that stopped: all read off\n"
        run8 = dict(run7, prog=(0x14, 0xdf))
        rc, out, err = dec(0xcd, run8)
        check(rc == 0 and "stage 0x6004 = 0xcd (pmp_out=1: bit 7; disp_off=1: bit 3, so the stage is 0x45) (pmgr_late=1): position=1: the samples recorded, CPU0 had nothing pending" in out
              and "position sample 1 (0x6012 = 0x14, 0x6013 = 0xdf):\n" in out and DISP in out
              and "  gate words: 0x2922c0044 bit 2 clear, bit 3 set; 0x2922c0040 bit 1 set\n" in out
              and "SIO_CPU:" not in out and "position sample 2" not in out and RAISED in out and "0x6015 = 0x8d: K4 ran" in out,
              "disp_off=1's 0xcd: K3b's byte, the gate's bits, no SIO class", out + err)
        rc, out, err = dec(0xcd, dict(run8, prog=(0x14, 0x06)))
        check(rc == 0 and "  display (K3b): internal DCP: shut down, but RUN didn't read 1 then 0; external DCP: its RTKit shutdown FAILED another way; the domains of the DCPs that stopped: NOT all read off, or none stopped\n" in out
              and "  gate words: 0x2922c0044 bit 2 clear, bit 3 clear; 0x2922c0040 bit 1 clear\n" in out,
              "disp_off=1: each DCP's outcome decoded on its own", out + err)
        rc, out, err = dec(0xcd, dict(run8, prog=(0x14, 0xd3)))
        check(rc == 0 and "  display (K3b): internal DCP: stopped; external DCP: its RTKit shutdown TIMED OUT; the domains of the DCPs that stopped: all read off\n" in out,
              "disp_off=1: a timeout on one DCP, and the other's domains off", out + err)
        rc, out, err = dec(0xcd, dict(run8, parked=0xff))
        check(rc == 0 and "0x6005 bit 0 (smc_idle=1): the SMC acked the AP idle notice within 200 ms\n" in out and "SIO_CPU's bits 27:24" not in out
              and "up to 10 ms each): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out,
              "disp_off=1: 0x6005 bit 0 is the SMC's ack, not CPU0 and not SIO_CPU's reference", out + err)
        rc, out, err = dec(0xcd, run8)
        check(rc == 0 and "0x6005 bit 0 (smc_idle=1): the SMC did NOT ack an AP idle notice, or this run sent none\n" in out,
              "disp_off=1: 0x6005 bit 0 clear: no ack, or none sent", out + err)
        fake(path, scratch(0x85, down=0, isrs=0, parked=0, left=0, secs=0, flags=0, drained=0), rtc_secs=121761, alarm=alarm)
        r = run(path, cnt=cnt)
        check(r.returncode == 0 and "K5 (smc_idle=1): the AP idle notice to the SMC is over" in r.stdout, "stage 0x85: K5 finished, nothing later", r.stdout + r.stderr)
        rc, out, err = dec(0xcc, run8)
        check(rc == 0 and "disp_off=1: bit 3, so the stage is 0x44" in out and "CPU0 had an interrupt or SError pending" in out and DISP in out,
              "disp_off=1's 0xcc: the same, with something pending", out + err)
        rc, out, err = dec(0xca, run8)
        check(rc == 0 and "so the stage is 0x42" in out and DISP in out and ", and only stages 0x44 and 0x45 say whether a read failed" in out,
              "disp_off=1's 0xca: sample 1 landed, no last check", out + err)
        rc, out, err = dec(0xc9, run8)
        check(rc == 0 and "so the stage is 0x41" in out and "position sample" not in out and "not decoded" in out,
              "disp_off=1's 0xc9: its bytes not read as a sample", out + err)
        rc, out, err = dec(0xc8, run8)
        check(rc == 0 and "so the stage is 0x40" in out and "no sample recorded yet" in out and RAISED in out,
              "disp_off=1's 0xc8: the watch's bytes, no sample", out + err)
        fake(path, scratch(0x84, down=0, isrs=0, parked=0, left=0, secs=0, flags=0, drained=0), rtc_secs=121761, alarm=alarm)
        r = run(path, cnt=cnt)
        check(r.returncode == 0 and "K3b (disp_off=1): both DCPs and their domains were handled" in r.stdout and "so the stage is 0x4" in r.stdout,
              "stage 0x84: K3b finished, nothing later", r.stdout + r.stderr)
        fake(path, scratch(0x02, down=0, isrs=0, parked=0, left=0, secs=0, flags=0, drained=0), rtc_secs=121761, alarm=alarm)
        r = run(path, cnt=cnt)
        check(r.returncode == 0 and "in a disp_off=1 run, K3b comes first: a stall or fault in it leaves this stage too" in r.stdout,
              "stage 0x02 says a stall in K3b leaves it, too", r.stdout + r.stderr)
        for stage in (0xc8, 0xc9, 0xca, 0xcc, 0xcd):
            before = fake(path, scratch(stage, **run8), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after disp_off=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)

        # sio_stop=1's run: 0x6012 has the K steps' outcomes, 0x6013 their marker, no sample, and stages 0x4e and 0x4f.
        # 0x6014 = 0x80, as runs 11, 12 and 15 left it (the hold_nowatch=1 records there are): such a run never sets PCPM0's and
        # PCPM1's bits, which are cluster_ack=1's there.
        run10 = dict(run7, prog=(0xff, 0xa5), flags=0x80)
        LINE = "  internal DCP: stopped; external DCP: stopped; the domains of the DCPs that stopped: all read off\n"
        rc, out, err = dec(0xcf, run10)
        check(rc == 0 and "stage 0x6004 = 0xcf (pmp_out=1: bit 7, so the stage is 0x4f) (pmgr_late=1): sio_stop=1: the commit and the alarm are done" in out
              and "0x6012 = 0xff, the steps before the commit:\n" in out and LINE in out
              and "  SMC's AP idle notice: acked within 200 ms; SIO's firmware: stopped\n" in out
              and "position sample" not in out and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out and "read off after the release" not in out
              and "PCPM0 not read (hold_nowatch=1: CPU0 doesn't watch them), PCPM1 not read" in out and HELD in out and RAISED in out
              and "0x6015 = 0x8d: K4 ran" in out and "from its low two bytes and the run's alarm: 121544" in out,
              "sio_stop=1's 0xcf: the steps' outcomes, no sample", out + err)
        rc, out, err = dec(0xcf, dict(run10, prog=(0x4a, 0xa5)))
        check(rc == 0 and "  internal DCP: shut down, but RUN didn't read 1 then 0; external DCP: shut down, but RUN didn't read 1 then 0; the domains of the DCPs that stopped: NOT all read off, or none stopped\n" in out
              and "  SMC's AP idle notice: NOT acked; SIO's firmware: its RTKit idle pair FAILED another way\n" in out,
              "sio_stop=1: each outcome decoded on its own", out + err)
        rc, out, err = dec(0xcf, dict(run10, prog=(0x20, 0xa5)))
        check(rc == 0 and "  SMC's AP idle notice: acked within 200 ms; SIO's firmware: its RTKit idle pair TIMED OUT\n" in out,
              "sio_stop=1: SIO's timeout", out + err)
        rc, out, err = dec(0xcf, dict(run10, prog=(0, 0xa5)))
        check(rc == 0 and "0x6012 = 0x00, the steps before the commit:\n" in out and out.count("TIMED OUT") == 3 and "SMC's AP idle notice: NOT acked" in out
              and "didn't land" not in out and "from its low two bytes and the run's alarm: 121544" in out,
              "sio_stop=1: a recorded all-zero outcome is still an outcome", out + err)
        for stage, prog in ((0xcf, (0, 0)), (0xce, (0x01, 0x00)), (0xcf, (0xff, 0xa4)), (0xcf, (0xff, 0xbf)), (0xcf, (0xff, 0x80))):
            rc, out, err = dec(stage, dict(run10, prog=prog))
            check(rc == 0 and f"0x6012 0x6013 = {prog[0]:02x} {prog[1]:02x}: the steps' outcomes didn't land (0x6013 is neither their marker, 0xa5, nor ps_words=1's, 0xc0 and up):" in out
                  and "TIMED OUT" not in out and "NOT acked" not in out and "the steps before the commit:" not in out
                  and "from its low two bytes and the run's alarm: 121544" in out,
                  f"sio_stop=1's 0x{stage:02x}, no marker at 0x6013 ({prog[0]:02x} {prog[1]:02x}): no outcomes reported as facts", out + err)
        # ps_words=1: 0x6013 is 0xc0 | one bit per group, and the steps' outcomes are as they were.
        WLINE = "0x6013 = 0x{:02x}: ps_words=1 wrote the 20 power words, after the table:\n"
        rc, out, err = dec(0xcf, dict(run10, prog=(0xff, 0xff)))
        check(rc == 0 and WLINE.format(0xff) in out and LINE in out and "0x6012 = 0xff, the steps before the commit:\n" in out and "NOT all" not in out
              and all(f"  {g}: read actual 0 within 10 ms each\n" in out for g in ("the five UARTs", "MSG", "ATC1-3_USB_AON", "ATC0-3_PCIE", "ATC1-3_COMMON"))
              and "  PCIE0_REF, APCIE_GP_SYS, AFNC1_LW2, AFNC2_LW1: target and auto fields read back as written\n" in out
              and "from its low two bytes and the run's alarm: 121544" in out,
              "ps_words=1, every group as asked", out + err)
        rc, out, err = dec(0xcf, dict(run10, prog=(0xff, 0xc0)))
        check(rc == 0 and WLINE.format(0xc0) in out and out.count("NOT all read actual 0") == 5 and "NOT all read back as written" in out and "didn't land" not in out,
              "ps_words=1, no group as asked: an outcome, not a lost record", out + err)
        for bit, what in ((0, "the five UARTs: read"), (1, "MSG: read"), (2, "ATC1-3_USB_AON: read"), (3, "ATC0-3_PCIE: read"), (4, "ATC1-3_COMMON: read")):
            rc, out, err = dec(0xcf, dict(run10, prog=(0xff, 0xc0 | 1 << bit)))
            check(rc == 0 and f"  {what} actual 0 within 10 ms each\n" in out and out.count("NOT all read actual 0") == 4 and "NOT all read back as written" in out,
                  f"ps_words=1: bit {bit} is {what.split(':')[0]}'s alone", out + err)
        rc, out, err = dec(0xcf, dict(run10, prog=(0xff, 0xe0)))
        check(rc == 0 and "fields read back as written\n" in out and out.count("NOT all read actual 0") == 5, "ps_words=1: bit 5 is the fabric words'", out + err)
        rc, out, err = dec(0xcf, run10)
        check(rc == 0 and "ps_words=1" not in out, "sio_stop=1 alone says nothing of the power words", out + err)
        rc, out, err = dec(0xb9, dict(run10, prog=None))
        check(rc == 0 and "RTC second at the replay's start (K1): 121544," in out and "the steps before the commit:" not in out and "didn't land" not in out,
              "the stage before sio_stop=1's 0x4e: 0x6012 is still the second's, read whole", out + err)
        rc, out, err = dec(0xce, run10)
        check(rc == 0 and "so the stage is 0x4e" in out and "8 (sio_stop=1)" in out and "0x6012 = 0xff" in out
              and "(0x4f, or recfg_bite=1's 0x46 or 0x47, or wd2_bite=1's 0x56 or 0x57, comes after them)" in out,
              "sio_stop=1's 0xce: before the commit", out + err)
        for stage in (0xce, 0xcf):
            before = fake(path, scratch(stage, **run10), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after sio_stop=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)

        # recfg_bite=1's run: stage 0x47 (0x46 with something pending) in place of 0x4f, with the sample at 0x6005, where parked was.
        NEW, OLD, AWAKE = 1, 2, 3

        def smp(cls, phase, armed=True, sram=True):
            return cls | phase << 2 | (0x40 if armed else 0) | (0x80 if sram else 0)

        SHOWN = "  so: the engine took WD2's bite from the changed word: the SRAM takes a write and the engine runs it. Feasibility shown, whenever the reset came"
        REJECT = ("  so: A2, before its last phase, armed WD2 with the old bite though the word read the new one: the engine didn't take the bite from this word."
                  " (A copy elsewhere isn't excluded: only the first 64 KiB of the SRAM has been read)\n")
        rc, out, err = dec(0xc7, dict(run10, parked=smp(NEW, 4), prog=(0xff, 0xff)))
        check(rc == 0 and "stage 0x6004 = 0xc7 (pmp_out=1: bit 7, so the stage is 0x47) (pmgr_late=1): recfg_bite=1: the commit, the alarm and the release are done, and CPU0 read WD2 and PHASE (0x6005) with nothing pending afterwards" in out
              and "0x6005 = 0xd1: recfg_bite=1, after the commit, the alarm and the release" in out
              and "  WD2's bite: the new value, 45 s; WD2's control showed RESET_EN\n" in out
              and "  PHASE 0x4 (the entry pair's first (A2)), read after the bite; A2's bite word in the SRAM, read in step 8 just before the commit: the new value\n" in out
              and SHOWN + "\n" in out and "parked (0x6005" not in out and "observe=1" not in out and "read off after the release" not in out
              and LINE in out and WLINE.format(0xff) in out and HELD in out and RAISED in out and "0x6015 = 0x8d: K4 ran" in out
              and "PCPM0 not read (hold_nowatch=1: CPU0 doesn't watch them)" in out
              and "from its low two bytes and the run's alarm: 121544" in out and "the SoC restarted about" in out,
              "recfg_bite=1's 0xc7: the sample at 0x6005, the steps' outcomes, no parked line", out + err)
        rc, out, err = dec(0xc6, dict(run10, parked=smp(NEW, 4)))
        check(rc == 0 and "so the stage is 0x46" in out and "but had an SError, IRQ or FIQ pending afterwards: that would end its deep WFI" in out
              and SHOWN + "\n" in out and "parked (0x6005" not in out and "from its low two bytes and the run's alarm: 121544" in out,
              "recfg_bite=1's 0xc6: the same sample, and CPU0 had something pending after its reads", out + err)
        NEVER = (". But RESET_EN didn't show within 0.3 s: CPU0 looked inside A2's opening, or the word after the bite was disturbed."
                 " Then WD2 never bites, and a Mac that stayed dark is that, not a completed entry\n")
        for phase in (0, 4, 5):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(NEW, phase, armed=False)))
            check(rc == 0 and "WD2's control did NOT show RESET_EN within 0.3 s\n" in out and SHOWN + NEVER in out and "may be a completed entry" not in out,
                  f"recfg_bite=1: the new bite without RESET_EN, PHASE {phase}: feasibility shown, and a dark ending wouldn't be a completed entry", out + err)
        # A2's own close clears RESET_EN after its last phase: past PHASE 4 and 5, no RESET_EN is how a finished A2 looks.
        for phase in (1, 2, 3, 6, 7, 8, 9, 0xa, 0xb, 0xc, 0xd, 0xe, 0xf):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(NEW, phase, armed=False)))
            check(rc == 0 and SHOWN + f". RESET_EN didn't show within 0.3 s, with PHASE {phase:#x}, which isn't one A2 shows before its last (0, 4, 5):"
                  " every A sequence clears RESET_EN at its end, so A2 may have run to its end before CPU0 looked."
                  " A Mac that then stayed dark may be a completed entry\n" in out and "not a completed entry" not in out,
                  f"recfg_bite=1: the new bite without RESET_EN, PHASE {phase:#x}: A2 may have finished, and a dark ending may be a completed entry", out + err)
        for phase in range(16):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(NEW, phase)))
            check(rc == 0 and SHOWN + "\n" in out, f"recfg_bite=1: the new bite with RESET_EN, PHASE {phase:#x}: feasibility shown, nothing added", out + err)
        UNSEEN = ("  so: nothing decided: WD2 holds the old bite with A2 before its last phase, though the word read the new one, which points at the engine"
                  " not taking the bite from this word. But RESET_EN didn't show within 0.3 s, and A2's opening sets it right after the bite:"
                  " this isn't the state that opening leaves, so the record alone doesn't say\n")
        for phase in (4, 5):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(OLD, phase)))
            check(rc == 0 and "  WD2's bite: the old value, 30 s; " in out and REJECT in out and "Feasibility shown" not in out,
                  f"recfg_bite=1: the old bite with PHASE {phase} says the engine didn't take the bite from this word", out + err)
            # The one reading that rejects the approach needs the whole of what A2's opening leaves: RESET_EN too.
            rc, out, err = dec(0xc7, dict(run10, parked=smp(OLD, phase, armed=False)))
            check(rc == 0 and UNSEEN in out and REJECT not in out and "Feasibility shown" not in out,
                  f"recfg_bite=1: the old bite with PHASE {phase} and no RESET_EN decides nothing", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=smp(AWAKE, 4)))
        check(rc == 0 and "observe=1" not in out and "its awake value" in out, "recfg_bite=1: bit 0 of the sample isn't observe=1's mark", out + err)
        for phase in (0, 2, 3, 6, 7, 8, 9, 0xa, 0xb, 0xc, 0xd, 1, 0xe, 0xf):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(OLD, phase)))
            check(rc == 0 and f"  so: nothing decided about the edit: with PHASE {phase:#x}, a later sequence's opening may have re-armed WD2 with its own 30 s before CPU0 looked" in out
                  and REJECT not in out and "Feasibility shown" not in out,
                  f"recfg_bite=1: the old bite with PHASE {phase:#x} decides nothing (7 is A2's last: the next opening re-arms WD2 before its own code)", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=smp(OLD, 4, sram=False)))
        check(rc == 0 and "read in step 8 just before the commit: NOT the new value\n" in out and REJECT not in out
              and "  so: the edit wasn't in place at the commit (the word didn't read the new value in step 8), so it wasn't tested. Not a negative\n" in out,
              "recfg_bite=1: the old bite with the SRAM word not new before the commit: not tested, not a negative", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=smp(AWAKE, 0, armed=False)))
        check(rc == 0 and "  WD2's bite: its awake value; WD2's control did NOT show RESET_EN within 0.3 s\n" in out
              and "  PHASE 0x0 (its awake value, or pair 0's), read after the bite; " in out
              and "  so: nothing decided: nothing had armed WD2 within 0.3 s of the release. If the Mac then stayed dark, WD2 was never armed: not a completed entry, and the edit is the first suspect\n" in out,
              "recfg_bite=1: the awake bite decides nothing, and says what a dark ending would then be", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=smp(AWAKE, 4)))
        check(rc == 0 and "  so: nothing decided: WD2 showed RESET_EN with its awake bite, which no sequence read so far would leave\n" in out,
              "recfg_bite=1: RESET_EN with the awake bite decides nothing", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=smp(0, 4)))
        check(rc == 0 and "  WD2's bite: another value; " in out and "  so: nothing decided: not a bite any sequence read so far writes. Unreadable, not a negative\n" in out,
              "recfg_bite=1: another bite decides nothing", out + err)
        for phase in range(16):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(NEW, phase, sram=False)))
            check(rc == 0 and SHOWN + ". (Step 8's read of the word didn't show the new value, which doesn't fit a new bite in WD2: that read isn't to be trusted then)\n" in out
                  and f"  PHASE {phase:#x} (" in out and "NOT the new value" in out,
                  f"recfg_bite=1: the new bite shows feasibility whatever else reads (PHASE {phase:#x}), and says step 8's read doesn't fit", out + err)
        ABSENT = "  so: nothing decided, and the edit wasn't in place at the commit (the word didn't read the new value in step 8): the reset time says nothing about it either"
        for cls, armed in ((AWAKE, False), (AWAKE, True), (0, True)):
            rc, out, err = dec(0xc7, dict(run10, parked=smp(cls, 4, armed=armed, sram=False)))
            check(rc == 0 and ABSENT + "\n" in out and "Feasibility shown" not in out and REJECT not in out,
                  f"recfg_bite=1: bite class {cls} with the SRAM word not new before the commit: the edit wasn't there, whatever else reads", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=0x00))
        check(rc == 0 and ABSENT + ". And 0x00 is also what a byte that didn't land, or didn't survive the reset, reads: not a reading to lean on\n" in out,
              "recfg_bite=1: 0x00 at 0x6005 is called out as a byte that may not have landed", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=smp(NEW, 4), down=0xfe))
        check(rc == 0 and NOT_RAISED in out and RAISED not in out, "recfg_bite=1's 0xc7 with 0x6007's bit 0 clear: the raises did not all read on", out + err)
        # 0xfe, the parked cores, is what stage 0x4e leaves at 0x6005: it must never read as the new bite.
        rc, out, err = dec(0xc7, dict(run10, parked=0xfe))
        check(rc == 0 and "Feasibility shown" not in out and "WD2's bite: the old value, 30 s" in out and "nothing decided about the edit: with PHASE 0xf" in out
              and "And 0xfe is what stage 0x4e leaves in this byte (the parked cores): if the stage's command landed without its second byte, this isn't a sample at all\n" in out,
              "recfg_bite=1: 0xfe at 0x6005, the byte stage 0x4e leaves there, is never feasibility, and is called out", out + err)
        rc, out, err = dec(0xcf, dict(run10, parked=0xd1))
        check(rc == 0 and "recfg_bite=1" not in out and "parked (0x6005 = 0xd1)" in out, "stage 0x4f's 0x6005 is still parked, never a sample", out + err)
        for stage in (0xc6, 0xc7):
            before = fake(path, scratch(stage, **dict(run10, parked=smp(NEW, 4))), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after recfg_bite=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)

        # wd2_bite=1's run: stage 0x57 (0x56 with something pending) in place of 0x4f, with its own sample at 0x6005.
        A2, W_NEW, W_AWAKE = 1, 2, 3

        def wsmp(pre=A2, seen=True, post=True, still=True, counting=True, phase=1):
            return pre | (4 if seen else 0) | (8 if post else 0) | (0x10 if still else 0) | (0x20 if counting else 0) | phase << 6

        def took(who="A2", dark="no reset is a candidate for a completed entry"):
            return ("  so: the write took, and WD2 was armed and counting with a 120 s bite. The reset's time is the result now: about 120 s after the commit"
                    f" fits {who} arming last and never closing; {dark}; about 30 s, or another time T, fits a later"
                    " sequence arming WD2 again, T - 30 s after the commit")

        TOOK = took()
        SEQ = "the sequence that armed WD2"
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(), prog=(0xff, 0xff)))
        check(rc == 0 and wsmp() == 0x7d
              and "stage 0x6004 = 0xd7 (pmp_out=1: bit 7, so the stage is 0x57) (pmgr_late=1): wd2_bite=1: the commit, the alarm and the release are done, and CPU0 wrote WD2's bite, if WD2 was armed, and read WD2 and PHASE (0x6005) with nothing pending afterwards" in out
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out
              and "  WD2's bite before CPU0's write: 30 s, an A sequence's opening's; WD2's control showed RESET_EN, so CPU0 wrote the bite, 120 s\n" in out
              and "  read after that: the bite 120 s; RESET_EN set; the count changing\n" in out
              and "  PHASE, read last: 0x4 (the entry pair's first (A2))\n" in out and TOOK + "\n" in out
              and "parked (0x6005" not in out and "observe=1" not in out and "read off after the release" not in out and "recfg_bite=1, after" not in out
              and LINE in out and WLINE.format(0xff) in out and HELD in out and RAISED in out and "0x6015 = 0x8d: K4 ran" in out
              and "PCPM0 not read (hold_nowatch=1: CPU0 doesn't watch them)" in out
              and "from its low two bytes and the run's alarm: 121544" in out and "the SoC restarted about" in out,
              "wd2_bite=1's 0xd7: the sample at 0x6005, the steps' outcomes, no parked line, and bit 0 of the sample isn't observe=1's mark", out + err)
        rc, out, err = dec(0xd6, dict(run10, parked=wsmp()))
        # The pending stage: CPU0 likely stayed up, so a dark ending isn't a candidate for anything, and the sentence says so itself.
        check(rc == 0 and "so the stage is 0x56" in out and "but had an SError, IRQ or FIQ pending afterwards: that would end its deep WFI" in out
              and took(dark="no reset isn't a candidate for a completed entry here, since CPU0 likely never went down (the stage)") + "\n" in out
              and "no reset is a candidate" not in out and "parked (0x6005" not in out and "from its low two bytes and the run's alarm: 121544" in out,
              "wd2_bite=1's 0xd6: the same sample, CPU0 had something pending after its reads, and no reset is then no candidate for a completed entry", out + err)
        rc, out, err = dec(0xd6, dict(run10, parked=wsmp(post=False)))
        check(rc == 0 and "The bite wasn't moved, and the reset's time says nothing about a moved bite. And CPU0 had an exception pending after its reads (the stage): it likely never went down\n" in out,
              "wd2_bite=1's 0xd6 with a write that didn't read back: the pending exception is said there too", out + err)
        # A2 is named only while PHASE reads 4 or 5: every A sequence opens with the same 30 s.
        for code, what, more in (
                (0, "0x0 (its awake value, or pair 0's)", took(SEQ) + ". PHASE still read 0 after 10 ms, where A2 writes 4 a few ops after its opening: which sequence armed WD2 isn't shown by this record\n"),
                (1, "0x4 (the entry pair's first (A2))", TOOK + "\n"), (2, "0x5 (the entry pair's second (A2))", TOOK + "\n"),
                (3, "neither 0, 4 nor 5: A2's last, or a later sequence's",
                 took(SEQ) + ". PHASE read neither 4 nor 5, A2's before its last: A2 may have run to its end already and a later A sequence armed WD2, each opening with the same 30 s."
                 " Then the entry had finished before CPU0's write, and the bite that was moved is that sequence's\n")):
            rc, out, err = dec(0xd7, dict(run10, parked=wsmp(phase=code)))
            check(rc == 0 and f"  PHASE, read last: {what}\n" in out and more in out and ("fits A2 arming last" in out) == (code in (1, 2)),
                  f"wd2_bite=1: PHASE's code {code} reads as {what.split(' (')[0]}, and A2 is named only for 4 and 5", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(seen=False, post=False, still=False)))
        check(rc == 0 and "WD2's control did NOT show RESET_EN within 0.3 s, so CPU0 wrote nothing\n" in out and "the write took" not in out
              and ("  so: CPU0 made no write: nothing had armed WD2 within 0.3 s of the release, so the bite wasn't moved and the reset's time says nothing about a moved bite."
                   " A Mac that then stayed dark isn't a completed entry by this record: WD2 was never seen armed\n") in out,
              "wd2_bite=1: no RESET_EN within 0.3 s: no write was made, the reset's time says nothing about a moved bite, and a dark ending isn't a completed entry", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(pre=W_NEW, seen=False, still=False)))
        check(rc == 0 and "the write took" not in out and "The bite read 120 s before and after: wd2-bite's store from user space, still there" in out,
              "wd2_bite=1: no write, and a bite of 120 s before and after: that's the store from user space, not CPU0's", out + err)
        for pre in (0, A2, W_AWAKE):
            rc, out, err = dec(0xd7, dict(run10, parked=wsmp(pre=pre, seen=False, still=False)))
            check(rc == 0 and "store from user space" not in out and "the write took" not in out
                  and "  so: an inconsistent record: CPU0 made no write, and the bite read 120 s after that though it didn't just before." in out,
                  f"wd2_bite=1: no write, with a bite of 120 s after and class {pre} before: an inconsistent record, said as that", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(post=False)))
        check(rc == 0 and "the write took" not in out and "read after that: the bite NOT 120 s; " in out
              and "  so: CPU0 MADE ITS WRITE AND THE BITE DIDN'T READ 120 s AFTER IT: the register didn't keep the kernel's store" in out,
              "wd2_bite=1: a write whose read-back isn't 120 s is said in capitals, and is never a moved bite", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(still=False)))
        check(rc == 0 and "the write took" not in out and "RESET_EN NOT set" in out
              and "An unarmed WD2 doesn't bite, so a Mac that then stayed dark is that as readily as a completed entry\n" in out,
              "wd2_bite=1: RESET_EN gone after the write: a dark ending wouldn't be a completed entry by this record", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(counting=False)))
        check(rc == 0 and "the write took" not in out and "the count NOT changing within 1 ms" in out
              and "WD2 may not be counting any more. A Mac that then stayed dark may be that, and isn't a completed entry by this record\n" in out,
              "wd2_bite=1: a count that stopped after the write: a dark ending wouldn't be a completed entry by this record", out + err)
        for pre, what in ((0, "another value"), (W_AWAKE, "the value it read awake at load")):
            rc, out, err = dec(0xd7, dict(run10, parked=wsmp(pre=pre)))
            check(rc == 0 and TOOK + f". But the bite read {what} before the write, not the 30 s an A sequence's opening leaves, though RESET_EN showed:"
                  " what armed WD2 isn't clear from this record\n" in out,
                  f"wd2_bite=1: RESET_EN with a bite of {what} before the write: said, since an opening leaves 30 s", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=wsmp(pre=W_NEW)))
        check(rc == 0 and "the write took" not in out
              and "  so: the bite read 120 s before CPU0's write as after it, so this record doesn't show that write took; WD2 was armed and counting with a 120 s bite." in out
              and "But the bite read the new value, 120 s before the write, not the 30 s an A sequence's opening leaves" in out,
              "wd2_bite=1: a bite that read 120 s before the write too doesn't show the write took", out + err)
        rc, out, err = dec(0xd7, dict(run10, parked=0x00))
        check(rc == 0 and "the write took" not in out and "And 0x00 is also what a byte that didn't land, or didn't survive the reset, reads: not a reading to lean on\n" in out,
              "wd2_bite=1: 0x00 at 0x6005 is called out as a byte that may not have landed", out + err)
        # 0xfe, the parked cores, is what stage 0x4e leaves at 0x6005: it must never read as the expected sample.
        rc, out, err = dec(0xd7, dict(run10, parked=0xfe))
        check(rc == 0 and "the write took" not in out and "WD2 was armed and counting" not in out and "completed entry" not in out
              and ("  so: 0xfe is what stage 0x4e leaves in this byte (the parked cores): the stage's command most likely landed without its second byte,"
                   " and then this isn't a sample at all. None of the three lines above is to be leaned on\n") in out,
              "wd2_bite=1: 0xfe at 0x6005, the byte stage 0x4e leaves there, is called that before anything is read out of it", out + err)
        # Every byte, with and without the pending stage: what the sentence claims against the bits, read independently.
        wrong = []
        for v in range(256):
            for pend in (False, True):
                so = [line for line in g["wd2_lines"](v, pend) if line.startswith("  so: ")]
                pre, seen, post, still, counting, code = v & 3, v & 4, v & 8, v & 0x10, v & 0x20, v >> 6
                moved = bool(seen and post and still and counting and v != 0xfe)
                ok = (len(so) == 1
                      and ("the write took" in so[0]) == (moved and pre != 2)
                      and ("The reset's time is the result now" in so[0]) == moved
                      and ("no reset is a candidate for a completed entry" in so[0]) == (moved and not pend)
                      and ("fits A2 arming last" in so[0]) == (moved and code in (1, 2))
                      and ("isn't a sample at all" in so[0]) == (v == 0xfe)
                      and ("CPU0 made no write" in so[0]) == (not seen)
                      and ("likely never went down" in so[0]) == (pend and v != 0xfe)
                      and ("not the 30 s an A sequence's opening leaves" in so[0]) == bool(seen and pre != 1 and v != 0xfe)
                      and ("an inconsistent record" in so[0]) == bool(not seen and post and pre != 2)
                      and ("store from user space, still there" in so[0]) == bool(not seen and post and pre == 2)
                      and ("not a reading to lean on" in so[0]) == (v == 0))
                if not ok:
                    wrong.append((hex(v), pend, so))
        check(not wrong, "wd2_bite=1: for all 256 bytes, with either stage, the sentence says the write took, names A2, or calls no reset a candidate for a"
              " completed entry only where the bits and the stage allow it", str(wrong[:3]))
        rc, out, err = dec(0xcf, dict(run10, parked=0x7d))
        check(rc == 0 and "wd2_bite=1" not in out and "parked (0x6005 = 0x7d)" in out, "stage 0x4f's 0x6005 is still parked, never wd2_bite=1's sample", out + err)
        rc, out, err = dec(0xc7, dict(run10, parked=0x7d))
        check(rc == 0 and "wd2_bite=1, after" not in out and "0x6005 = 0x7d: recfg_bite=1, after" in out, "recfg_bite=1's stage still reads its own sample there", out + err)
        for stage in (0xd6, 0xd7):
            before = fake(path, scratch(stage, **dict(run10, parked=wsmp())), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after wd2_bite=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)
        # A reset two minutes after the commit is timed like any other.
        fake(path, scratch(0xd7, **dict(run10, parked=wsmp())), rtc_secs=121544 + 120 + 40, alarm=alarm)
        r = run(path, cnt="960000000 24000000")
        check(r.returncode == 0 and "the SoC restarted about 120 s after the commit (119.0 to 121.0 s:" in r.stdout,
              "wd2_bite=1: a restart 120 s after the commit is timed as that", r.stdout + r.stderr)

        # cluster_ack=1's run: wd2_bite=1's, with 0x6014's bit 4 its mark and bit 5 "something other than the flag was left in a
        # word", and qto's bits 2-5 the cluster words whose flag stayed.
        ACK = "0x6014 bit 4 (cluster_ack=1): step 5 wrote ANE0's, AFR's and AVEMSR's soc-cluster words (0x28e09800c, 0x28e098014, 0x28e098020) as 0x20,"
        ALL0 = "  so: each of the four words read 0 within 10 ms of its write, before the commit: target 0, not busy, and the flag clear\n"
        ENDING = "  (how the run ended is the stage's and wd2_bite=1's sample's to say; PLAN.md has how an ending reads with this)\n"
        NONE = "0x6006 = 0x{:02x}: {}; bits 2-5 (cluster_ack=1), the soc-cluster words whose up-done flag was still set at their wait's last read: {}\n"
        MET = "macOS's other waits were all met within 10 ms"
        ack15 = dict(run10, parked=wsmp(), prog=(0xff, 0xff), left=0)
        rc, out, err = dec(0xd7, dict(ack15, flags=0x90))
        check(rc == 0 and NONE.format(0, MET, "none") in out and ACK in out and ALL0 + ENDING in out
              and "STAYED" not in out and "every one of macOS's waits" not in out and "nothing here says whether" not in out
              and "0x6014 = 0x90: CPU0 had nothing pending before the commit; PCPM0 not read (hold_nowatch=1: CPU0 doesn't watch them)" in out
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out and TOOK + "\n" in out,
              "cluster_ack=1's 0xd7 with every wait met: the three words went out as 0x20 and all four read 0, with wd2_bite=1's sample as ever", out + err)
        # What the cluster words read says nothing about the ending by itself: no sentence here credits a reset to the flags.
        for stage in (0xce, 0xd6, 0xd7):
            rc, out, err = dec(stage, dict(ack15, flags=0x90, parked=0xfe if stage == 0xce else wsmp()))
            lines = [line for line in out.splitlines() if "cluster_ack" in line or line.startswith("  so: each of the four")]
            check(rc == 0 and ALL0 in out and not any("reset" in line or "stalls" in line or "completed entry" in line for line in lines),
                  f"cluster_ack=1's 0x{stage:02x}: the words read 0, and the cluster lines leave the ending to the stage and the sample", out + err)
        rc, out, err = dec(0xd7, dict(ack15, flags=0x80))
        check(rc == 0 and "cluster_ack" not in out and "0x6006 = 0x00: every one of macOS's waits was met within 10 ms\n" in out,
              "the same record without the mark is wd2_bite=1's run, and nothing is said of the cluster words", out + err)
        rc, out, err = dec(0xd7, dict(ack15, flags=0x90, left=0x04))
        check(rc == 0 and NONE.format(4, MET, "ANE0's (55)") in out and ACK in out and ALL0 not in out
              and ("  so: the up-done flag STAYED SET, 10 ms after the write, in ANE0's (55): the 0x20 didn't clear it there."
                   " With a flag left in a word this run wrote, the ending says nothing about cleared flags\n") in out
              and "something else left" not in out and "ANE0's power-down" not in out,
              "cluster_ack=1: ANE0's flag stayed, and nothing else was left anywhere: said as that, and not as a wait on bit 4", out + err)
        rc, out, err = dec(0xd7, dict(ack15, flags=0xb0, left=0x28))
        check(rc == 0 and NONE.format(0x28, MET, "AFR's (57), AVEMSR's (60)") in out and ALL0 not in out
              and ("  so: the up-done flag STAYED SET, 10 ms after the write, in AFR's (57), AVEMSR's (60): the 0x20 didn't clear it there;"
                   " and at least one of the four words had something else left too (0x6014 bit 5: bit 4, busy, the target or another bit),"
                   " which word and which bit isn't recorded. With a flag left in a word this run wrote, the ending says nothing about cleared flags\n") in out,
              "cluster_ack=1: two flags stayed and something else was left in a word: both said, and that the record doesn't say where", out + err)
        # Only the control: DISPLAY's flag, after the table's own 0x20. The three words the run changed took their ack.
        rc, out, err = dec(0xd7, dict(ack15, flags=0x90, left=0x10))
        check(rc == 0 and NONE.format(0x10, MET, "DISPLAY's (58: the table's own 0x20, the control)") in out and ALL0 not in out
              and ("  so: ANE0's, AFR's and AVEMSR's flags read clear after their 0x20, and the flag STAYED SET in DISPLAY's, 10 ms after the table's own 0x20,"
                   " which the earlier runs wrote there too. The three words this run changed took the ack; DISPLAY's flag is new: no earlier run looked at it\n") in out
              and "says nothing about cleared flags" not in out,
              "cluster_ack=1: DISPLAY's flag alone stayed: the three words the run wrote aren't blamed for it", out + err)
        # No flag stayed, but a word wasn't 0: another bit (bit 6, say), busy or the target. The acks took.
        rc, out, err = dec(0xd7, dict(ack15, flags=0xb0, left=0x41))
        check(rc == 0 and ACK in out and ALL0 not in out and "STAYED" not in out
              and NONE.format(0x41, "macOS's waits that timed out after 10 ms: DCS's p-state after entry 49 (to 7), after 50 (to 5), or both (its busy bit 31 clear,"
                              " macOS's poll, and actual = target, e1_hook's own); SIO_CPU's power state (61)", "none") in out
              and ("  so: no flag stayed: bit 5 read clear in all four words. But at least one of them didn't read 0 within 10 ms of its write (0x6014 bit 5):"
                   " bit 4, busy, the target or another bit was left, and which word and which bit isn't recorded. The acks took; what was left is new\n") in out,
              "cluster_ack=1: no flag stayed but a word didn't read 0: the acks took, and what was left isn't claimed to be known; the other waits read as ever", out + err)
        # 0x6006 = 0 with 0x6007 = 0 too: the command's last bytes may not have landed, so "no flag stayed" isn't claimed.
        for flags, rest in ((0x90, "nothing other than a flag was left in any of the four words"), (0xb0, "something other than a flag was left in at least one of the four words")):
            rc, out, err = dec(0xd7, dict(ack15, flags=flags, down=0))
            check(rc == 0 and ALL0 not in out and "no flag stayed" not in out
                  and ("  so: 0x6006 and 0x6007 both read 0. That's also what bytes that didn't land read, and 0x6007 would otherwise say no core held:"
                       f" whose flag stayed isn't known from this record. 0x6014, another command's, says {rest}\n") in out,
                  f"cluster_ack=1 with 0x6006 and 0x6007 both 0 (0x6014 = 0x{flags:02x}): no word is said to have read 0", out + err)
        rc, out, err = dec(0xd7, dict(ack15, flags=0xa0))
        UNREAD = ("  0x6014's bits 4 and 5 are cluster_ack=1's alone in a sio_stop=1 run: bit 5 only with bit 4, and only at that run's stages (0x4e, 0x56, 0x57,"
                  " or fabric_up=1's 0x52, 0x51, 0x53, or gates_mark=1's 0x4b, 0x54, 0x55, or sio_down=1's 0x3d, 0x3e, 0x3f, or pcie_down=1's 0x1d, 0x1e, 0x1f, or sio_steps=1's 0x15, 0x16, 0x17, or a2_pre=1's 0x19, 0x1a, 0x1b, with pmp_out=1's bit 7). Here they aren't that: don't read them\n")
        check(rc == 0 and "0x6014 bit 4 (cluster_ack=1)" not in out and UNREAD in out,
              "bit 5 without the mark in a sio_stop=1 record: called out, and not read as cluster_ack=1's", out + err)
        # Stages no cluster_ack=1 run sends (0x4f, and recfg_bite=1's), and a stage without pmp_out=1's bit: the mark isn't read there.
        for stage in (0xcf, 0xc6, 0xc7, 0x57, 0x4e):
            rc, out, err = dec(stage, dict(ack15, flags=0x90, left=0x04))
            check(rc == 0 and "0x6014 bit 4 (cluster_ack=1)" not in out and UNREAD in out and "nothing here says whether" not in out
                  and ("ANE0's power-down (55)" in out or stage in (0x57, 0x4e)),
                  f"stage 0x{stage:02x} with 0x6014 = 0x90: not a cluster_ack=1 run's stage, so the mark isn't read and the bits are called unreadable", out + err)
        # The mark's byte lost (bit 7 clear), or a stage before it was sent: 0x6006's cluster bits can't be read either way, and it says so,
        # whether or not one is set.
        NOMARK = ("  (nothing here says whether this was a cluster_ack=1 run: its mark is 0x6014's bit 4, sent just before stage 0x4e (0x52 with fabric_up=1, 0x4b with gates_mark=1, 0x3d with sio_down=1, 0x1d with pcie_down=1, 0x15 with sio_steps=1, 0x19 with a2_pre=1){}."
                  " In one, bits 2-5 are the cluster words whose flag stayed, not the waits on bit 4 that timed out)\n")
        for stage in (0xce, 0xd6, 0xd7):
            for left in (0x04, 0x00):
                rc, out, err = dec(stage, dict(ack15, flags=0x10, left=left, parked=0xfe if stage == 0xce else wsmp()))
                check(rc == 0 and NOMARK.format(", and that byte didn't survive") in out and "0x6014 bit 4 (cluster_ack=1)" not in out
                      and ("macOS's waits that timed out after 10 ms: ANE0's power-down (55)" in out) == bool(left) and "0x6014 = 0x10: bit 7 is clear" in out,
                      f"stage 0x{stage:02x} with a 0x6014 that didn't land and 0x6006 = 0x{left:02x}: its bit 4 isn't read as the mark, and 0x6006's cluster bits"
                      " are said to be unreadable as either run's", out + err)
        for stage in (0xa8, 0xb1, 0xb7, 0xb9):
            for left in (0x08, 0x00):
                rc, out, err = dec(stage, dict(ack15, parked=0xfe, flags=0x90, left=left))
                check(rc == 0 and NOMARK.format("") in out and "0x6014 bit 4 (cluster_ack=1)" not in out and ("AFR's power-down (57)" in out) == bool(left),
                      f"stage 0x{stage:02x}, before the mark's byte is sent, 0x6006 = 0x{left:02x}: the note, and no mark is read", out + err)
        # Without pmp_out=1's bit, or at a stage only other runs send (0x38, 0x5c), a record can't be cluster_ack=1's: no note.
        for stage in (0x28, 0x31, 0x39, 0xb8, 0xdc):
            rc, out, err = dec(stage, dict(ack15, parked=0xfe, flags=0x90, left=0x08))
            check(rc == 0 and "nothing here says whether" not in out and "cluster_ack" not in out and "AFR's power-down (57)" in out,
                  f"stage 0x{stage:02x} can't be a cluster_ack=1 run's: its 0x6006 reads as macOS's waits, with no note", out + err)
        # Outside sio_stop=1's stages the two bits are PCPM0's and PCPM1's, as ever: position=1's 0xc5 with both set.
        rc, out, err = dec(0xc5, dict(run7, left=0x04))
        check(rc == 0 and "cluster_ack" not in out and "PCPM0 off after the release, PCPM1 off after the release" in out and "ANE0's power-down (55)" in out,
              "position=1's 0xc5 with 0x6014 = 0xb0: PCPM0's and PCPM1's bits, never cluster_ack=1's", out + err)
        # Every flags byte with the mark, every 0x6006, 0x6007 zero or not: what the sentence claims, read independently.
        wrong = []
        for f in range(0x80, 0x100):
            if not f & 0x10:
                continue
            for q in range(256):
                for held in (0, 0xff):
                    lines = g["ack_lines"](f, q, held)
                    so = [line for line in lines if line.startswith("  so: ")]
                    stuck, wrote, rest, lost = bool(q & 0x3c), bool(q & 0x2c), bool(f & 0x20), not held and not q
                    ok = (len(so) == 1 and len(lines) == 3 and "reset" not in "".join(lines) and "stalls" not in "".join(lines)
                          and ("each of the four words read 0" in so[0]) == (not stuck and not rest and not lost)
                          and ("STAYED SET" in so[0]) == stuck
                          and ("the 0x20 didn't clear it there" in so[0]) == wrote
                          and ("The three words this run changed took the ack" in so[0]) == (stuck and not wrote)
                          and ("no flag stayed" in so[0]) == (not stuck and rest and not lost)
                          and ("isn't known from this record" in so[0]) == lost
                          and ("had something else left too" in so[0] or "didn't read 0 within 10 ms" in so[0]
                               or "something other than a flag was left in at least one" in so[0]) == rest
                          and all((n in so[0]) == bool(q >> i & 1) for i, n in ((2, "ANE0's (55)"), (3, "AFR's (57)"), (5, "AVEMSR's (60)"))))
                    if not ok:
                        wrong.append((hex(f), hex(q), held, so))
        check(not wrong, "cluster_ack=1: for every flags byte, every 0x6006 and 0x6007 zero or not, the words are said to have read 0 only when no flag bit is"
              " set, bit 5 is clear and the bytes landed; a flag is said to have stayed only in the words 0x6006 names; and nothing is said of the ending",
              str(wrong[:3]))
        for stage in (0xd6, 0xd7):
            before = fake(path, scratch(stage, **dict(ack15, flags=0x90)), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after cluster_ack=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)

        # fabric_up=1's run, and wd1_mark=1's on it: stages of their own from step 8's command on, and 0x6007 their byte. 0xf5 is
        # the byte when all goes as meant: the sio bit, bit 1 (NOT all held) clear, 0x111 before the write, 0x333 after it and
        # at step 5's end, and the marker standing. 0x35 is that without a marker.
        FAB = "0x6007 = 0x{:02x}: fabric_up=1's byte, in place of the cores that held:\n"
        BACK = ("  so: the fabric was at 3 when the table went out and still there when step 5 ended, where macOS has it at its commit:"
                " the kernel had lowered it, and the write brought it back\n")
        STOOD = ("  bits 7:6 (wd1_mark=1): WD1 stood as the marker just before the commit: armed, its bite 150 s, its count past 60 s and running."
                 " The restart's time is read against it below, as far as the stage and wd2_bite=1's sample bear a reading out\n")
        NOMARKER = ("  bits 7:6 (wd1_mark=1): clear: no marker in WD1 stands in this record. In a run without wd1_mark=1 (the reading's label and the"
                    " load log say which this was) the restart's time is read against WD2 alone. In one with it, the marker read back wrong in both"
                    " respects, WD1 may still be armed some other way, and a time that isn't WD2's may be WD1's\n")
        BIT1 = ("  bit 1 is set: NOT all seven other cores wrote their stop bit and held; or this is still step 7's byte, the cores that held, where"
                " this bit is CPU1's, and stage 0x52's command didn't put the run's byte in its place. Either way nothing more is read out of it: no"
                " fabric outcome, and no marker in WD1 is taken to stand\n")
        fab17 = dict(ack15, flags=0x90)
        rc, out, err = dec(0xd3, dict(fab17, down=0xf5))
        check(rc == 0 and "stage 0x6004 = 0xd3 (pmp_out=1: bit 7, so the stage is 0x53) (pmgr_late=1): fabric_up=1: wd2_bite=1's 0x57:" in out
              and FAB.format(0xf5) in out and "  the other cores: all seven wrote their stop bit and held\n" in out
              and "  bit 0 (sio_on=1): ps_sio, UART_P and SPI_P all read actual f within 10 ms\n" in out
              and "  the fabric's p-state word (0x28e20c000) before CPU0's write: 0x111: asked down to 1 by the kernel's suspend, and there\n" in out
              and ("  after the write of 3 to its low nibble: it read 0x333, not busy, within 10 ms; at the end of step 5, after the ClkGen park and"
                   " PLL-off: 0x333\n") in out
              and BACK in out and STOOD in out and BIT1 not in out and "wrote their stop bit and held (0x6007" not in out
              and "0x6007 bit 0 (sio_on=1)" not in out
              and "0x6005 = 0x7d: wd2_bite=1, after the commit, the alarm and the release:\n" in out and TOOK + "\n" in out
              and ACK in out and ALL0 in out and NONE.format(0, MET, "none") in out and LINE in out and WLINE.format(0xff) in out
              and "0x6015 = 0x8d: K4 ran" in out and "PCPM0 not read (hold_nowatch=1: CPU0 doesn't watch them)" in out
              and "from its low two bytes and the run's alarm: 121544" in out and "parked (0x6005" not in out,
              "fabric_up=1 and wd1_mark=1's 0xd3: 0x6007 is the run's byte, never the cores that held; wd2_bite=1's sample, cluster_ack=1's"
              " words and the steps' outcomes read as in run 16", out + err)
        rc, out, err = dec(0xd1, dict(fab17, down=0xf5))
        check(rc == 0 and "so the stage is 0x51" in out and "fabric_up=1: wd2_bite=1's 0x56:" in out
              and "but had an SError, IRQ or FIQ pending afterwards: that would end its deep WFI" in out
              and took(dark="no reset isn't a candidate for a completed entry here, since CPU0 likely never went down (the stage)") + "\n" in out
              and "no reset is a candidate" not in out and FAB.format(0xf5) in out and BACK in out and STOOD in out and ACK in out,
              "fabric_up=1's 0xd1: wd2_bite=1's pending stage, with the run's byte", out + err)
        rc, out, err = dec(0xd2, dict(fab17, parked=0xfe, down=0xf5))
        check(rc == 0 and "so the stage is 0x52" in out and "8 (fabric_up=1): sio_stop=1's 0x4e, with fabric_up=1's byte at 0x6007:" in out
              and "parked (0x6005 = 0xfe): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out and FAB.format(0xf5) in out and BACK in out and STOOD in out
              and "wd2_bite=1, after" not in out and "0x6012 = 0xff, the steps before the commit:\n" in out and ACK in out and ALL0 in out
              and "  stage 0x52, so nothing records the commit after those commands (0x51 or 0x53 comes after them): inconclusive about the rest of the entry\n" in out,
              "fabric_up=1's 0xd2: the last stage before the commit, with the parked cores at 0x6005, the run's byte at 0x6007 and no sample", out + err)
        # The same byte under wd2_bite=1's own stage is the cores that held, as ever: the stage is what says which.
        rc, out, err = dec(0xd7, dict(fab17, down=0xf5))
        check(rc == 0 and "fabric_up=1's byte, in place of" not in out and "wd1_mark=1" not in out
              and "wrote their stop bit and held (0x6007 = 0xf5): CPU2,CPU4,CPU5,CPU6,CPU7\n" in out,
              "0xf5 at 0x6007 under stage 0xd7 is the cores that held, not fabric_up=1's byte", out + err)
        # Without pmp_out=1's bit the stage can't be this run's: neither the byte nor the cluster mark is read, nor the time.
        fake(path, scratch(0x53, **dict(fab17, down=0xf5)), rtc_secs=121544 + 120 + 40, alarm=alarm)
        r = run(path, cnt="960000000 24000000")
        check(r.returncode == 0 and "0x6014 bit 4 (cluster_ack=1)" not in r.stdout and UNREAD in r.stdout
              and "0x6007 = 0xf5: not read. A fabric_up=1 run's stages always carry pmp_out=1's bit 7, and this one doesn't: not a record that run leaves\n" in r.stdout
              and "fabric_up=1's byte, in place of" not in r.stdout and "WD1 stood" not in r.stdout and "wd1_mark=1's marker" not in r.stdout,
              "stage 0x53 without bit 7: not this run's record, so neither its byte, nor a marker, nor the cluster mark is read", r.stdout + r.stderr)
        for byte, text in ((0x35, NOMARKER),
                           (0x75, "  bits 7:6 (wd1_mark=1): the marker did NOT stand just before the commit: WD1 was armed with its 150 s bite, but its count"
                                  " didn't read past 60 s, by up to 5 s: the count write didn't take, or the count isn't running. The restart's time says"
                                  " nothing about B2, and a time that isn't WD2's may be WD1's\n"),
                           (0xb5, "  bits 7:6 (wd1_mark=1): the marker did NOT stand just before the commit: WD1's count read past 60 s, by up to 5 s, but"
                                  " it wasn't armed with the 150 s bite. The restart's time says nothing about B2, and a time that isn't WD2's may be"
                                  " WD1's\n")):
            rc, out, err = dec(0xd3, dict(fab17, down=byte))
            check(rc == 0 and text in out and STOOD not in out and "wd1_mark=1's marker:" not in out and BACK in out,
                  f"fabric_up=1's byte 0x{byte:02x}: no standing marker, so the restart's time isn't read against WD1", out + err)
        # Bit 1 set: not all held, or step 7's byte never replaced. Nothing more is read, whatever the other bits and the time.
        for byte in (0xf7, 0xff, 0xfe, 0x37, 0x02):
            fake(path, scratch(0xd3, **dict(fab17, down=byte)), rtc_secs=121544 + 120 + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            check(r.returncode == 0 and FAB.format(byte) + BIT1 in r.stdout and "the fabric's p-state word" not in r.stdout and "WD1 stood" not in r.stdout
                  and "bits 7:6 (wd1_mark=1)" not in r.stdout and "wd1_mark=1's marker" not in r.stdout,
                  f"fabric_up=1's byte 0x{byte:02x}, bit 1 set, with the outcomes' marker behind it: nothing more is read out of it, and no marker stands",
                  r.stdout + r.stderr)
        # The restart's time against a marker that stood: 90, 120, 150 s and later, and a time that's none of them.
        for x, text in ((90, "  wd1_mark=1's marker: about 90 s is what WD1 gives with its count never zeroed: B2 didn't reach its op 224"),
                        (88, "  wd1_mark=1's marker: about 90 s is what WD1 gives with its count never zeroed"),
                        (120, "  wd1_mark=1's marker: about 120 s is WD2: B2 zeroed WD1's count, so B2's first poll passed"),
                        (150, "  wd1_mark=1's marker: 150 s is WD1's 150 s from a count that B2 zeroed, with WD2, which the sample above saw armed and"
                              " counting, stopped before it could fire: the entry pair FINISHED"),
                        (163, "  wd1_mark=1's marker: 163 s is WD1's 150 s from a count that B2 zeroed, which then came about 13 s after the commit, with"
                              " WD2, which the sample above saw armed and counting, stopped before it could fire: the entry pair FINISHED"),
                        (30, "  wd1_mark=1's marker: 30 s is none of its times (about 90 s, about 120 s, 150 s or more): not read."),
                        (135, "  wd1_mark=1's marker: 135 s is none of its times (about 90 s, about 120 s, 150 s or more): not read."),
                        (105, "  wd1_mark=1's marker: 105 s is none of its times (about 90 s, about 120 s, 150 s or more): not read.")):
            for byte in (0xf5, 0x35):
                fake(path, scratch(0xd3, **dict(fab17, down=byte)), rtc_secs=121544 + x + 40, alarm=alarm)
                r = run(path, cnt="960000000 24000000")
                check(r.returncode == 0 and f"the SoC restarted about {x} s after the commit" in r.stdout
                      and (text in r.stdout) == (byte == 0xf5) and ("wd1_mark=1's marker:" in r.stdout) == (byte == 0xf5),
                      f"fabric_up=1's 0xd3, a restart {x} s after the commit, 0x6007 = 0x{byte:02x}: read against the marker only if it stood",
                      r.stdout + r.stderr)
        # The 90 s and 120 s readings each carry the alternative that the time alone can't exclude.
        mr = g["mark_reading"]
        check("a pair that finished, with a later A sequence starting about 60 s after the commit and arming WD2 with its own 30 s, gives the same time"
              in mr(90, 0x53, 0x7d) and "a WD1 that can't reset the SoC in this state" in mr(120, 0x53, 0x7d),
              "the marker's 90 s and 120 s readings name what else gives that time")
        # The time is read only as far as the stage and wd2_bite=1's sample bear it out (Pullfrog on #74): the records it gave, through
        # the decoder, and then every sample at every stage.
        for stage, sample, x, want, never in (
                (0xd3, 0xfd, 120, "about 120 s is WD2's moved bite, but PHASE didn't read 4 or 5, A2's before its last, in the sample above: a later A sequence"
                 " may have armed WD2, after a pair that finished. Not read as B2 stopping in its last ops", "B2 zeroed WD1's count, so"),
                (0xd2, 0xfe, 150, "not read. Stage 0x52 is the last before the commit: nothing here records the commit, the release or WD2 after it, so"
                 " 150 s can't be read as B2's progress. WD1 has been armed since step 4, so a run that stopped anywhere from there on restarts by"
                 " itself about 90 s later, committed or not", "FINISHED"),
                (0xd2, 0xfe, 90, "not read. Stage 0x52 is the last before the commit", "B2 didn't reach"),
                (0xd3, 0x02, 150, "150 s is WD1's 150 s from a count that B2 zeroed, but wd2_bite=1's sample, above, doesn't show WD2 armed and counting:"
                 " a WD2 that wouldn't have fired anyway says nothing by staying silent. Not read as a pair that finished", "FINISHED"),
                (0xd3, 0x5d, 150, "doesn't show WD2 armed and counting", "FINISHED"),
                (0xd3, wsmp(still=False), 150, "doesn't show WD2 armed and counting", "FINISHED"),
                (0xd3, 0xfe, 163, "doesn't show WD2 armed and counting", "FINISHED"),
                (0xd3, 0xfe, 120, "doesn't show WD2 armed and counting with CPU0's 120 s bite", "B2 zeroed WD1's count, so"),
                (0xd1, 0x75, 120, "not read. CPU0 had an exception pending after its reads (the stage) and likely never went down, and a core left up holds"
                 " the engine by itself: 120 s says nothing about a pair with every core down", "B2 zeroed WD1's count, so"),
                (0xd3, wsmp(post=False), 120, "about 120 s, but wd2_bite=1's sample, above, doesn't show WD2 armed and counting with CPU0's 120 s bite, so this"
                 " record doesn't make the time WD2's. Not read as B2's progress", "B2 zeroed WD1's count, so"),
                (0xd3, wsmp(post=False), 150, "stopped before it could fire: the entry pair FINISHED", "Not read")):
            fake(path, scratch(stage, **dict(fab17, parked=sample, down=0xf5)), rtc_secs=121544 + x + 40, alarm=alarm)
            r = run(path, cnt="960000000 24000000")
            line = [l for l in r.stdout.splitlines() if l.startswith("  wd1_mark=1's marker: ")]
            check(r.returncode == 0 and len(line) == 1 and want in line[0] and never not in line[0],
                  f"stage 0x{stage:02x}, sample 0x{sample:02x}, a restart at {x} s: the marker's reading goes no further than the stage and the sample do",
                  r.stdout + r.stderr)
        wrong = []
        for stage in (0x52, 0x51, 0x53):
            for v in range(256):
                for x in (60, 88, 90, 105, 120, 123, 135, 146, 150, 163, 300):
                    t = mr(x, stage, v)
                    # Read here from wd2_bite.h's layout: seen armed (bit 2), the bite 120 s after the write (3), still armed (4), counting (5).
                    live = bool(v & 4 and v & 0x10 and v & 0x20) and v != 0xfe
                    moved, code = live and bool(v & 8), v >> 6
                    last, at120, late = stage == 0x53, x in (120, 123), x >= 146
                    ok = (t.startswith("  wd1_mark=1's marker: ")
                          and ("B2 didn't reach its op 224" in t) == (last and x in (88, 90))
                          and ("B2 zeroed WD1's count, so B2's first poll passed" in t) == (last and at120 and moved and code in (1, 2))
                          and ("the entry pair FINISHED" in t) == (last and late and live)
                          and ("which then came about" in t) == (last and x in (163, 300))
                          and ("ot read" in t) == (not last or x in (60, 105, 135) or (at120 and not (moved and code in (1, 2))) or (late and not live)))
                    if not ok:
                        wrong.append((hex(stage), hex(v), x, t))
        check(not wrong, "for every sample at each of the run's stages: B2's progress is read from 90 s only at the last stage, from 120 s only with WD2's"
              " bite moved and PHASE at A2's 4 or 5, a finished pair from 150 s or later only with WD2 armed and counting in a real sample, and nothing at"
              " the other two stages",
              str(wrong[:3]))
        # Step 7 leaves the cores that held at 0x6007 (0xff as a rule). If stage 0x52's command lands without its last byte, that 0xff must not
        # read as the run's byte, all outcomes good and a marker standing: the steps' outcomes, sent only after a command that didn't fail,
        # are what says it landed whole (Pullfrog on #74).
        STALE = ("0x6007 = 0xff: not read. The steps' outcomes go out right after stage 0x52's command, and only if it didn't fail, and their marker"
                 " isn't at 0x6013: that command isn't shown to have landed whole, and this byte, its last, may still be the one step 7 leaves here, the"
                 " cores that held (0xff as a rule). It isn't read as fabric_up=1's byte, and no marker in WD1 is taken to stand\n")
        step7 = dict(parked=0xfe, left=0, down=0xff, secs=121544, flags=0x90, isrs=0x8d, drained=0)
        rc, out, err = dec(0xb9, step7)
        check(rc == 0 and "wrote their stop bit and held (0x6007 = 0xff): CPU1,CPU2,CPU3,CPU4,CPU5,CPU6,CPU7\n" in out
              and "fabric_up=1's byte, in place of the cores that held" not in out,
              "step 7's record, stage 0xb9: 0x6007 is the cores that held", out + err)
        for stage in (0xd2, 0xd3, 0xd1):
            for x in (90, 120, 150):
                fake(path, scratch(stage, **dict(step7, parked=0xfe if stage == 0xd2 else wsmp())), rtc_secs=121544 + x + 40, alarm=alarm)
                r = run(path, cnt="960000000 24000000")
                check(r.returncode == 0 and STALE in r.stdout and "fabric_up=1's byte, in place of the cores that held" not in r.stdout
                      and "the fabric's p-state word" not in r.stdout and "WD1 stood" not in r.stdout
                      and "wd1_mark=1's marker" not in r.stdout and "the steps' outcomes didn't land" in r.stdout
                      and f"the SoC restarted about {x} s after the commit" in r.stdout,
                      f"stage 0x{stage:02x} over step 7's 0xff with no outcomes after it, a restart at {x} s: the byte isn't read, and no marker stands",
                      r.stdout + r.stderr)
        # Without the outcomes' marker even a byte that looks like the run's isn't taken for it, and the time isn't read against it.
        fake(path, scratch(0xd3, **dict(step7, parked=wsmp(), down=0xf5)), rtc_secs=121544 + 120 + 40, alarm=alarm)
        r = run(path, cnt="960000000 24000000")
        check(r.returncode == 0 and STALE.replace("0x6007 = 0xff", "0x6007 = 0xf5") in r.stdout and "WD1 stood" not in r.stdout
              and "wd1_mark=1's marker" not in r.stdout and "the SoC restarted about 120 s after the commit" in r.stdout,
              "0xf5 at 0x6007 with no outcomes after its command: not read as the run's byte, and the restart's time isn't read against a marker",
              r.stdout + r.stderr)
        check("\tif (m1_raw && m1_spmi_raw(true, addr, (u8 *)b, n))\n\t\tm1_raw = false;\n" in src
              and g["fab_landed"](bytes(0x13) + b"\xdf") and g["fab_landed"](bytes(0x13) + b"\xa5") and g["fab_landed"](bytes(0x13) + b"\xc0")
              and not any(g["fab_landed"](bytes(0x13) + bytes([m])) for m in (0x00, 0x01, 0x80, 0xa4, 0xbf)),
              "a breadcrumb command that fails stops every one after it, so the outcomes' marker at 0x6013 says stage 0x52's command didn't fail")
        # Every byte: what the fabric's sentence and the marker's line claim, against the bits, read independently.
        wrong = []
        for v in range(256):
            lines = g["fab_lines"](v)
            so = [line for line in lines if line.startswith("  so: ")]
            wd = [line for line in lines if line.startswith("  bits 7:6 (wd1_mark=1): ")]
            was, took_, kept, w = v >> 2 & 3, bool(v & 0x10), bool(v & 0x20), v & 0xc0
            if v & 2:
                ok = len(lines) == 2 and lines[1] + "\n" == BIT1 and not so and not wd
            else:
                ok = (len(so) == 1 and len(wd) == 1 and len(lines) == 8 and "reset" not in "".join(lines[:6])
                      and lines[1] == "  the other cores: all seven wrote their stop bit and held"
                      and ("was at 3 when the table went out and still there when step 5 ended" in so[0]) == (took_ and kept)
                      and ("the kernel had lowered it, and the write brought it back" in so[0]) == (took_ and kept and was in (1, 2))
                      and ("the fabric wasn't a difference from macOS in this run" in so[0]) == (took_ and kept and was == 3)
                      and ("which nothing predicted" in so[0]) == (took_ and kept and was == 0)
                      and ("came back to 3 after the write and was NOT there when step 5 ended" in so[0]) == (took_ and not kept)
                      and ("settled late" in so[0]) == (not took_ and kept)
                      and ("did NOT come back to 3" in so[0]) == (not took_ and not kept and v != 0)
                      and ("not a reading to lean on" in so[0]) == (v == 0)
                      and ("all read actual f" in lines[2] and "NOT" not in lines[2]) == bool(v & 1)
                      and ("WD1 stood as the marker" in wd[0]) == (w == 0xc0) and ("did NOT stand" in wd[0]) == (w in (0x40, 0x80))
                      and ("no marker in WD1 stands" in wd[0]) == (w == 0)
                      and ("its count didn't read past 60 s" in wd[0]) == (w == 0x40) and ("it wasn't armed with the 150 s bite" in wd[0]) == (w == 0x80))
            if not ok:
                wrong.append((hex(v), so, wd))
        check(not wrong, "fabric_up=1: for all 256 bytes, nothing is read past a set bit 1; the fabric is said to be at 3 only when it read 0x333 after"
              " the write and at step 5's end, and the marker to have stood only when both its bits are set", str(wrong[:2]))
        for stage in (0xd1, 0xd2, 0xd3):
            before = fake(path, scratch(stage, **dict(fab17, down=0xf5, parked=0xfe if stage == 0xd2 else wsmp())), rtc_secs=121761, alarm=alarm)
            r = run(path, "--clear", cnt=cnt)
            after = open(path, "rb").read()
            want = bytearray(before)
            want[0x6004:0x6008] = bytes(4)
            want[0x6010:0x6017] = bytes(7)
            want[0x1401] = 0x01
            check(r.returncode == 0 and after == bytes(want) and "disarmed: 0x1401 = 0x01" in r.stdout,
                  f"--clear after fabric_up=1's 0x{stage:02x}: the run's alarm disarmed and the crumbs zeroed", r.stdout + r.stderr)

        # wd2_bite.h itself: the register and its values, and the sample's byte against an independent reading of its layout.
        w_c = os.path.join(tmp, "w.c")
        with open(w_c, "w") as f:
            f.write('#include <stdio.h>\n#include "wd2_bite.h"\nint main(void)\n{\n'
                    'static const u32 bites[] = { WD2_BITE_A2, WD2_BITE_NEW, WD2_BITE_AWAKE, 0x12345678, 0 };\n'
                    'static const u32 awakes[] = { WD2_BITE_AWAKE, WD2_BITE_NEW };\n'
                    'printf("consts %llx %x %x %x %x %x %x %x\\n", WD2_WDT + WD2_BITE, WD2_COUNT, WD2_BITE, WD2_CTRL, WD2_RESET_EN, WD2_BITE_A2, WD2_BITE_AWAKE, WD2_BITE_NEW);\n'
                    'for (unsigned a = 0; a < 2; a++) for (unsigned b = 0; b < 5; b++) for (unsigned c = 0; c < 8; c++) for (unsigned q = 0; q < 5; q++)\n'
                    ' for (unsigned f = 0; f < 8; f++) for (unsigned k = 0; k < 3; k++) for (unsigned p = 0; p < 32; p++)\n'
                    '  printf("%x %x %x %x %x %x %x %02x\\n", awakes[a], bites[b], c, bites[q], f, k, p,\n'
                    '         m1_wd2_sample(bites[b], awakes[a], c, bites[q], f, 1000, k == 2 ? 3 : 1000 + k, p));\n'
                    'return 0;\n}\n')
        wexe = os.path.join(tmp, "w")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", wexe, w_c], check=True)
        rows = [line.split() for line in subprocess.run([wexe], capture_output=True, text=True, check=True).stdout.splitlines()]
        check(rows[0] == ["consts", "2922b0024", "20", "24", "2c", "4", "2aea5400", "80befc00", "aba95000"]
              and 0x2aea5400 == 30 * 24000000 and 0x80befc00 == 90 * 24000000 and 0xaba95000 == 120 * 24000000 and 0xaba95000 < 1 << 32,
              "wd2_bite.h: the register is 0x2922b0024, WD2's count, bite and control at 0x20, 0x24, 0x2c; 30 s, 90 s and 120 s at 24 MHz",
              str(rows[0]))

        def want_wd2(awake, pre, ctl, post, after_, moved, phase):
            cls = 1 if pre == 0x2aea5400 else 2 if pre == 0xaba95000 else 3 if pre == awake else 0
            return (cls | (4 if ctl & 4 else 0) | (8 if post == 0xaba95000 else 0) | (0x10 if after_ & 4 else 0) | (0x20 if moved else 0)
                    | {0: 0, 4: 1, 5: 2}.get(phase & 0xf, 3) << 6)

        bad = [r for r in rows[1:] if int(r[7], 16) != want_wd2(*(int(x, 16) for x in r[:7]))]
        check(len(rows) == 1 + 2 * 5 * 8 * 5 * 8 * 3 * 32 and not bad,
              "m1_wd2_sample: the bite before the write, RESET_EN before and after, the bite and the count after (any change, a count that fell too),"
              " PHASE's low four bits as a code: nothing else", str(bad[:4]))
        check(want_wd2(0x80befc00, 0x2aea5400, 4, 0xaba95000, 4, 1, 4) == 0x7d and 0xfe & 3 != 1
              and all(want_wd2(0x80befc00, 0x2aea5400, 4, 0xaba95000, 4, 1, ph) != 0xfe for ph in range(16)),
              "the parked byte, 0xfe, isn't the sample that's expected, whatever PHASE reads: its class bits don't say A2's 30 s")
        check(g["WD2_PRE"][1].startswith("30 s, ") and g["WD2_PRE"][2].startswith("the new value, 120 s") and g["WD2_PHASES"] == (0, 4, 5, None),
              "m1-crumbs' names for the sample's classes and PHASE codes are the header's order")

        # recfg_bite.h itself: the sample's byte against an independent reading of its layout, the two values, and the verdict.
        r_c = os.path.join(tmp, "r.c")
        with open(r_c, "w") as f:
            f.write('#include <stdio.h>\n#include "recfg_bite.h"\nint main(void)\n{\n'
                    'static const u32 bites[] = { RECFG_BITE_OLD, RECFG_BITE_NEW, 0x80befc00, 0x12345678, 0 };\n'
                    'printf("consts %llx %x %x %x %x %x %llx %llx %llx %x %x %x\\n", RECFG_SRAM + RECFG_BITE, RECFG_BITE_OLD, RECFG_BITE_NEW, RECFG_TABLE, RECFG_A2_ENTRY, RECFG_A2,\n'
                    '       RECFG_CFG_REG, RECFG_CFG_LOCK, RECFG_WDT, RECFG_WD2_COUNT, RECFG_WD2_BITE, RECFG_WD2_CTRL);\n'
                    'for (unsigned b = 0; b < 5; b++) for (unsigned p = 0; p < 32; p++) for (unsigned c = 0; c < 8; c++) for (unsigned s = 0; s < 5; s++)\n'
                    '  printf("%x %x %x %x %02x\\n", bites[b], p, c, bites[s], m1_recfg_sample(bites[b], 0x80befc00, p, c, bites[s]));\n'
                    'return 0;\n}\n')
        exe = os.path.join(tmp, "r")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", exe, r_c], check=True)
        rows = [line.split() for line in subprocess.run([exe], capture_output=True, text=True, check=True).stdout.splitlines()]
        check(rows[0] == ["consts", "292f02010", "2aea5400", "405f7e00", "1000", "1010", "2000", "2925c0200", "2925c0214", "2922b0000", "20", "24", "2c"]
              and 0x2aea5400 == 30 * 24000000 and 0x405f7e00 == 45 * 24000000,
              "recfg_bite.h: the word is 0x292f02010, 30 s and 45 s at 24 MHz, the table at 0x1000 and A2 at 0x2000; WD2's count, bite and control at 0x20, 0x24, 0x2c",
              str(rows[0]))

        def want_sample(bite, phase, ctrl, sram):
            cls = 1 if bite == 0x405f7e00 else 2 if bite == 0x2aea5400 else 3 if bite == 0x80befc00 else 0
            return cls | (phase & 0xf) << 2 | (0x40 if ctrl & 4 else 0) | (0x80 if sram == 0x405f7e00 else 0)

        bad = [r for r in rows[1:] if int(r[4], 16) != want_sample(*(int(x, 16) for x in r[:4]))]
        check(len(rows) == 1 + 5 * 32 * 8 * 5 and not bad, "m1_recfg_sample: the bite's class, PHASE's low four bits, RESET_EN, the SRAM word: nothing else",
              str(bad[:4]))
        check(want_sample(0x405f7e00, 0xf, 4, 0x405f7e00) != 0xfe and 0xfe & 3 != 1,
              "the parked byte, 0xfe, isn't a sample with the new bite: its class bits say old")
        # The awake class is only for a bite that's neither value: an awake bite equal to one of them must not hide it.
        c_c = os.path.join(tmp, "c.c")
        with open(c_c, "w") as f:
            f.write('#include <stdio.h>\n#include "recfg_bite.h"\nint main(void)\n{\n'
                    'printf("%d %d %d %d %d %d\\n", m1_bite_class(RECFG_BITE_OLD, RECFG_BITE_OLD), m1_bite_class(RECFG_BITE_NEW, RECFG_BITE_NEW),\n'
                    '       m1_bite_class(RECFG_BITE_NEW, RECFG_BITE_OLD), m1_bite_class(RECFG_BITE_OLD, RECFG_BITE_NEW), m1_bite_class(7, 7), m1_bite_class(7, 8));\n'
                    'return 0;\n}\n')
        cexe = os.path.join(tmp, "c")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", cexe, c_c], check=True)
        got = subprocess.run([cexe], capture_output=True, text=True, check=True).stdout.split()
        check(got == ["2", "1", "1", "2", "3", "0"], "m1_bite_class: new is 1 and old is 2 whatever the awake bite was; awake is 3 only for a bite that's neither", str(got))

        v_c = os.path.join(tmp, "v.c")
        with open(v_c, "w") as f:
            f.write('#include <stdio.h>\n#include <stdlib.h>\n#include "recfg_bite.h"\nint main(int argc, char **argv)\n{\n'
                    'u32 w[10];\nif (argc != 11) return 2;\nfor (int i = 0; i < 10; i++) w[i] = (u32)strtoul(argv[i + 1], NULL, 16);\n'
                    'printf("%d\\n", recfg_bite_verdict(w[0], w[1], w + 2, w + 4));\nreturn 0;\n}\n')
        vexe = os.path.join(tmp, "v")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", vexe, v_c], check=True)

        def verdict(words):
            return int(subprocess.run([vexe] + [f"{w:x}" for w in words], capture_output=True, text=True, check=True).stdout)

        # The ten words as the format and WD2's register map give them: nothing here is copied from the dump.
        head = ((0x2922b0000 >> 10) << 6) | (4 - 1) << 2 | 1
        offs = int.from_bytes(bytes(reg >> 2 for reg in (0x2c, 0x20, 0x24, 0x2c)), "little")
        good = [0x1000, 0, (0x292f00000 + 0x2000) >> 4, 0, head, offs, 0, 0, 0x2aea5400, 4]
        OLDV, NEWV, BAD_BITE, BAD_CFG, LOCKED, BAD_TABLE, BAD_SEQ = range(7)
        check(verdict(good) == OLDV and verdict(good[:8] + [0x405f7e00, 4]) == NEWV, "the verdict: the ten words as expected, with the old bite and with the new")
        check(verdict(good[:8] + [0x405f7e01, 4]) == BAD_BITE and verdict(good[:8] + [0, 4]) == BAD_BITE and verdict(good[:8] + [0x80befc00, 4]) == BAD_BITE,
              "the verdict: any other bite, in the op as expected, is its own answer")
        for i, (name, want_v) in enumerate((("the offset register", BAD_CFG), ("the lock", LOCKED), ("the table entry", BAD_TABLE), ("its second word", BAD_TABLE),
                                           ("the op's head", BAD_SEQ), ("the offsets", BAD_SEQ), ("control's first value", BAD_SEQ), ("count's value", BAD_SEQ),
                                           (None, None), ("control's last value", BAD_SEQ))):
            if name is None:
                continue
            for flip in (1, 0x80000000, 0x100):
                words = list(good)
                words[i] ^= flip
                check(verdict(words) == want_v, f"the verdict: {name} off by {flip:#x} is refused as that")
                words[8] = 0x405f7e00
                check(verdict(words) == want_v, f"the verdict: the same with the new bite there")
        check(verdict([0x1004, 1, 0, 0, 0, 0, 0, 0, 0x2aea5400, 4]) == BAD_CFG and verdict([0x1000, 1, 0, 0, 0, 0, 0, 0, 0x2aea5400, 4]) == LOCKED
              and verdict([0x1000, 0, 1, 0, 0, 0, 0, 0, 0x2aea5400, 4]) == BAD_TABLE,
              "the verdict names the first thing that isn't as expected")
        dump = os.path.expanduser("~/.local/share/sleep-drain/recfg-decode-20261108/run5/sram-00000.bin")
        if os.path.exists(dump):
            with open(dump, "rb") as f:
                raw = f.read()
            word = lambda off: int.from_bytes(raw[off:off + 4], "little")
            real = [0x1000, 0, word(0x1010), word(0x1014)] + [word(0x2000 + 4 * i) for i in range(6)]
            check(verdict(real) == OLDV and real == good,
                  "this Mac's own dump of 2026-11-08: its ten words are the ones built here, and the verdict is the old bite")
            check([o for o in range(0, len(raw) - 3, 4) if word(o) == 0x2aea5400] == [0x1a10, 0x1cf0, 0x2010, 0x21b0],
                  "and the old bite is in that dump four times, A2's at 0x2010")
        else:
            print("skip the dump's own words: no local dump here")

        # m1_pos_byte_d, the module's own code.
        d_h = os.path.join(tmp, "d.c")
        with open(d_h, "w") as f:
            f.write('#include <stdio.h>\n#include "m1_position.h"\nint main(void)\n{\n'
                    'for (unsigned k = 0; k < 256; k++) for (unsigned g = 0; g < 16; g++) printf("%x %x %02x\\n", k, g,'
                    ' m1_pos_byte_d((u8)k, g << 2, (g >> 2) << 1));\nreturn 0;\n}\n')
        exe = os.path.join(tmp, "d")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.dirname(HOOK), "-o", exe, d_h], check=True)
        rows = [line.split() for line in subprocess.run([exe], capture_output=True, text=True, check=True).stdout.splitlines()]
        bad = [r for r in rows if int(r[2], 16) != (int(r[0], 16) & 0x1f) | (int(r[1], 16) & 1) << 5 | (int(r[1], 16) >> 1 & 1) << 6 | (int(r[1], 16) >> 2 & 1) << 7]
        check(len(rows) == 4096 and not bad, "m1_pos_byte_d: K3b's five bits and the gate's three, nothing else", str(bad[:4]))

    # e1_hook.c's side.
    def in_order(text, pieces):
        at = 0
        for piece in pieces:
            i = text.find(piece, at)
            if i < 0:
                return False
            at = i + 1
        return True

    defs = {k: int(v, 0) for k, v in re.findall(r"#define (M1_POS_\w+|M1_WDX_STATE|M1_WDX)\s+(0x[0-9a-fA-F]+|\d+)", src)}
    check(defs.get("M1_POS_PAGE") == 0x2922c0000 and defs.get("M1_POS_PHASE") == 0x110 and defs.get("M1_POS_GATE44") == 0x44
          and defs.get("M1_POS_GATE40") == 0x40 and defs.get("M1_POS_HS_A") == 4 and defs.get("M1_WDX_STATE") == 0x1c
          and defs.get("M1_WDX") == 0x2922b8000 and "#define E1_S2R_COMMIT\t\t0x2925c4000ULL" in src,
          "position=1's addresses are the plan's", str(defs))
    pos = src[src.index("static void m1_position(u8 off"):]
    pos = pos[:pos.index("\n}\n")]
    sample = src[src.index("static void m1_pos_sample(u8 *s)"):]
    sample = sample[:sample.index("\n}\n")]
    order = ["m1_crumb(0x6014, &flags, 1);", "b[0] = M1_POS_ST(0x40);", "b[1] = off | ((disp_off ? m1_smc_acked : m1_sio_ref) ? BIT(0) : 0);", "b[2] = qto;", "b[3] = held;",
             "m1_crumb(0x6004, b, 4);", "if (m1_raw) {\n\t\tm1_pos_wait(m1_t_third + m1_tps);", "m1_stage(M1_POS_ST(0x41));", "m1_pos_sample(s);", "m1_crumb(0x6012, s, 2);",
             "m1_stage(M1_POS_ST(0x42));", "if (m1_raw && !pmp_out) {\n\t\tm1_pos_wait(m1_t_third + 25 * m1_tps);", "m1_stage(0x43);", "m1_pos_sample(s);", "m1_crumb(0x6007, s, 1);",
             "m1_crumb(0x6015, s + 1, 1);", "? 0x44 : 0x45));", "writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));"]
    check(in_order(pos, order), "m1_position() sends the stages and samples in the order m1-crumbs reads them, each wait only with raw SPMI up", pos)
    release = src[src.index("static void m1_hold_release("):]
    release = release[:release.index("\n}\n")]
    check(in_order(release, ["m1_wait_off(M1_PS_PCPM0)", "m1_wait_off(M1_PS_PCPM1)", "if (position) {\n\t\tm1_position(off, qto, held, flags);\n\t\treturn;\n\t}", "b[0] = 0x5c;"]),
          "m1_hold_release() branches to m1_position() after both PCPM waits and doesn't go on to hold=1's stages", release)
    check("while (m1_now() < at)\n\t\tcpu_relax();" in src, "m1_pos_wait() spins until the counter reaches the deadline")
    check(in_order(sample, ["post_map[ARRAY_SIZE(e1_post_slpa) - 1] + M1_POS_HS_A;", "phase = readl(m1_pos + M1_POS_PHASE);", "hs = readl(hs_a);",
                            "state = readl(m1_wdx + M1_WDX_STATE);", "sio = readl(m1_sio + M1_PS_SIO_CPU);", "ctrl = readl(m1_wdt + M1_WD_CTRL(2));",
                            "count = readl(m1_wdt + M1_WD_CUR(2));", "now = m1_now();", "gate44 = readl(m1_pos + M1_POS_GATE44);",
                            "gate40 = readl(m1_pos + M1_POS_GATE40);", "s[0] = m1_pos_byte_a(phase, hs, state);",
                            "s[1] = disp_off ? m1_pos_byte_d(m1_disp_bits, gate44, gate40) :",
                            "m1_pos_byte_b(sio, ctrl, m1_pos_count_near(count, now - m1_t_first, m1_tps), gate44, gate40);"]),
          "m1_pos_sample() reads each word into its own name, and byte A goes to s[0], byte B to s[1]", sample)
    check(pos.count("writel(") == 1 and "writel_relaxed" not in pos and "write" not in re.sub(r"writel\(U32_MAX, m1_spmi|write_sysreg_s", "", pos)
          and "write" not in sample,
          "position=1 writes nothing but breadcrumbs and nub-spmi0's acks; a sample only reads", pos + sample)
    for word in ("M1_POS_PHASE", "hs_a", "M1_WDX_STATE", "M1_PS_SIO_CPU", "M1_WD_CTRL(2)", "M1_WD_CUR(2)", "M1_POS_GATE44", "M1_POS_GATE40"):
        check(f"readl(" in sample and word in sample, f"a sample reads {word}", sample)
    settled = re.findall(r"\{\s*(\d+),\s*M1_W_SETTLED,[^}]*\}", src)
    check(settled == ["61"] and "0x28e580188ULL" in re.search(r"\{\s*61,\s*M1_W_SETTLED[^\n]*", src).group(0)
          and "if (m1_waits[k].kind == M1_W_SETTLED)" in src and "m1_sio_ref = ((v >> 24) & 0xf) == 0xf;" in src,
          "SIO_CPU's reference comes from entry 61's wait, the only M1_W_SETTLED", str(settled))
    commit = src[src.index("m1_t_first = m1_now();"):]
    commit = commit[:commit.index("m1_t_third = m1_now();")]
    check(commit.count("writel_relaxed(1, commit);") == 3 and commit.index("writel_relaxed(1, commit);") > 0,
          "the time since the first commit starts before its first write, the 25 s after its third", commit)
    check('position && (!hold || hold_nowatch || (pmp_out && !sio_on))' in src and 'sio_on && (!pmp_out || (!position && !sio_stop))' in src
          and 'sio_stop && (!hold_nowatch || !disp_off || !one_commit || !smc_idle || position)' in src,
          "e1_check_params refuses position=1 off hold=1, with hold_nowatch, or with pmp_out and no sio_on; sio_on=1 without pmp_out=1 and position=1 or sio_stop=1; sio_stop=1 off its recipe")

    # sio_on=1's own code.
    raises = src[src.index("static bool m1_sio_raise(void)"):]
    raises = raises[:raises.index("\n}\n")]
    check(in_order(raises, ["{ M1_PS_SIO, 0x0f00000f }, { M1_PS_UART_P, 0xf }, { M1_PS_SPI_P, 0xf },", "writel(on[i].val, m1_sio + on[i].off);", "dsb(sy);",
                            "((readl(m1_sio + on[i].off) >> 4) & 0xf) != 0xf", "m1_now() - t0 > m1_tps / 100"]) and raises.count("writel(") == 1,
          "m1_sio_raise() writes the three OK'd words, in genpd's order, and only those, polling each up to 10 ms for actual f", raises)
    d2 = {k: int(v, 0) for k, v in re.findall(r"#define (M1_PS_\w+|M1_SIO_ENTRY)\s+(0x[0-9a-fA-F]+|\d+)", src)}
    check(d2.get("M1_PS_SIO") == 0x180 and d2.get("M1_PS_UART_P") == 0x230 and d2.get("M1_PS_SPI_P") == 0x1e8 and d2.get("M1_SIO_ENTRY") == 61
          and "#define M1_PS_SIO_PAGE\t\t0x28e580000ULL" in src, "the raises are at 0x28e580180, 0x28e580230 and 0x28e5801e8, before entry 61", str(d2))
    step5 = src[src.index("/* 5: the table's other entries"):]
    step5 = step5[:step5.index("/* 6: release")]
    check(in_order(step5, ["if (sio_on && i == M1_SIO_ENTRY && m1_sio_raise())", "held |= BIT(0);", "writel_relaxed(m1_post_val(i), post_map[i]);"]),
          "the raises come just before entry 61's own write, and 0x6007's bit 0 records them", step5)
    table = re.findall(r"\{ (0x[0-9a-f]+)ULL, \d, 0x[0-9a-f]+ULL \},", src[src.index("e1_post_slpa[] = {"):src.index("e1_post_slpa[] = {") + 12000])
    check(len(table) > 61 and int(table[61], 16) == 0x28e580188, "e1_post_slpa[61] is SIO_CPU's word", str(table[59:63]))
    check("e1_post_slpa[M1_SIO_ENTRY].addr != M1_PS_SIO_PAGE + M1_PS_SIO_CPU" in src
          and all(f"e1_never_write(M1_PS_SIO_PAGE + M1_PS_{n})" in src for n in ("SIO", "UART_P", "SPI_P")),
          "the load refuses if entry 61 isn't SIO_CPU's word, or a raise would touch the never-write list")
    # disp_off=1's own code.
    shut = src[src.index("static void m1_dcp_shutdown(void)\n{"):]
    shut = shut[:shut.index("\n}\n")]
    check(in_order(shut, ["apple_rtkit_shutdown(m1_dcp_rtk[i])", "readl(m1_dcp_asc[i] + M1_ASC_CPU_CONTROL)",
                          "writel(ctrl & ~M1_ASC_CPU_RUN, m1_dcp_asc[i] + M1_ASC_CPU_CONTROL);", "M1_DCP_STOPPED : M1_DCP_NOT_STOPPED;"])
          and shut.count("apple_rtkit_shutdown(") == 1 and shut.count("writel(") == 1 and "apple_rtkit_idle" not in shut,
          "m1_dcp_shutdown(): one RTKit shutdown each, RUN cleared after it", shut)
    check("if (disp_off && n == 1 && !READ_ONCE(replay_done))\n\t\tm1_dcp_shutdown();" in src[src.index("static int e1_prepare(void)"):src.index("static int e1_prepare(void)") + 900],
          "the DCPs are shut down at the first prepare of the suspend that will replay, before noirq and slpa")
    stop = src[src.index("static u8 m1_disp_stop(void)"):]
    stop = stop[:stop.index("\n}\n")]
    check(in_order(stop, ["if (m1_dcp_code[i] == M1_DCP_STOPPED) {", "turned = true;",
                          "m1_ps_off(m1_dcp_ps[i][0] + (d->ps[0] & ~PAGE_MASK))", "m1_ps_off(m1_dcp_ps[i][1] + (d->ps[1] & ~PAGE_MASK))", "res |= m1_dcp_code[i] << (2 * i);",
                          "if (turned && all_off)", "res |= BIT(4);", "m1_kwrite(0x6004, &stage, 1);"]) and "rtkit" not in stop.lower() and stop.count("writel(") == 0,
          "m1_disp_stop(): the domains off only for a DCP whose RUN read 1 then 0, and no message to any DCP", stop)
    check("static const u8 stage = 0x84;" in stop, "K3b's stage is 0x84", stop)
    check("m1_dcp_code[i] = ret == -ETIME || ret == -ETIMEDOUT ? M1_DCP_TIMEOUT : M1_DCP_FAILED;\n\t\t\tcontinue;\n\t\t}" in shut,
          "a DCP whose RTKit shutdown failed is left alone: RUN is only cleared after it returned 0 (what Corey OK'd)", shut)
    check("m1_dcp_code[i] = (ctrl & M1_ASC_CPU_RUN) && !(readl(m1_dcp_asc[i] + M1_ASC_CPU_CONTROL) & M1_ASC_CPU_RUN) ?" in shut,
          "a DCP is only STOPPED, and its domains only go off, if RUN read 1 then 0", shut)
    check(all(f"#define M1_DCP_{n}" in src for n in ("TIMEOUT\t\t0", "FAILED\t\t1", "NOT_STOPPED\t2", "STOPPED\t\t3")),
          "K3b's outcome codes are the ones the decoder and the layout comment give")
    psoff = src[src.index("static bool m1_ps_off(void __iomem *p)"):]
    psoff = psoff[:psoff.index("\n}\n")]
    check("writel(v & ~(BIT(28) | BIT(12) | BIT(11) | BIT(10) | BIT(9) | BIT(8) | GENMASK(3, 0)), p);" in psoff and psoff.count("writel(") == 1
          and "(readl(p) >> 4) & 0xf" in psoff and "m1_tps / 100" in psoff,
          "m1_ps_off(): target 0 as the power-state driver asks for it, polled up to 10 ms for actual 0", psoff)
    check('{ "internal", "/soc/mbox@38bc08000", 0x38bc00000ULL, { 0x28e580350ULL, 0x28e580328ULL } },' in src
          and '{ "external", "/soc/mbox@289c08000", 0x289c00000ULL, { 0x28e080280ULL, 0x28e080258ULL } },' in src,
          "the DCPs, their ASCs and their domains are the ones Corey OK'd, child first")
    after = src[src.index("static void __noreturn m1_after_slpa(void)"):]
    check(in_order(after, ["if (m1_kwrite(0x6004, &two, 1))", "if (disp_off)\n\t\tm1_disp_bits = m1_disp_stop();", "if (pmp_out)\n\t\tpmpb = m1_pmp_out();", "preempt_disable();"]),
          "K3b comes after K3, before K4, with interrupts still on", after[:1400])
    check("#define M1_POS_ST(s)\t((s) | (disp_off ? 8 : 0))" in src
          and all(f"M1_POS_ST({x})" in pos for x in ("0x40", "0x41", "0x42")) and "M1_POS_ST(((read_sysreg(isr_el1) >> 6) & 7) ? 0x44 : 0x45)" in pos,
          "disp_off=1's position stages are bit 3 up", pos)
    check("s[1] = disp_off ? m1_pos_byte_d(m1_disp_bits, gate44, gate40) :" in sample, "disp_off=1 samples K3b's byte in place of byte B", sample)
    check('disp_off && !sio_on' in src, "e1_check_params refuses disp_off=1 without sio_on=1")
    check(all(f"m1_never(d->{w}" in src for w in ("asc + M1_ASC_CPU_CONTROL", "ps[0]", "ps[1]")),
          "the load refuses if a DCP's word would touch the never-write list")
    # sio_stop=1.
    fw = src[src.index("static void m1_sio_fw_stop(void)\n{"):]
    fw = fw[:fw.index("\n}\n")]
    check(in_order(fw, ["apple_rtkit_idle(m1_sio_rtk)", "M1_DCP_TIMEOUT : M1_DCP_FAILED", "return;", "readl(m1_sio_asc + M1_ASC_CPU_CONTROL)",
                        "writel(ctrl & ~M1_ASC_CPU_RUN, m1_sio_asc + M1_ASC_CPU_CONTROL);", "M1_DCP_STOPPED : M1_DCP_NOT_STOPPED;", "msleep(2);"])
          and fw.count("apple_rtkit_idle(") == 1 and fw.count("writel(") == 1 and "shutdown" not in fw,
          "m1_sio_fw_stop(): the idle pair once, RUN cleared only after it returned 0", fw)
    check("if (ret) {\n\t\tm1_sio_code = ret == -ETIME || ret == -ETIMEDOUT ? M1_DCP_TIMEOUT : M1_DCP_FAILED;\n\t\treturn;\n\t}" in fw,
          "SIO's RUN is left alone if its idle pair failed (what Corey OK'd)", fw)
    check("if (sio_stop && n == 1 && !READ_ONCE(replay_done))\n\t\tm1_sio_fw_stop();" in src[src.index("static int e1_prepare(void)"):src.index("static int e1_prepare(void)") + 1100],
          "SIO's firmware is stopped at the first prepare of the suspend that will replay, after the DCPs", "")
    check(in_order(src, ["b[0] = fabric_up ? M1_FAB_STAGE_PRE : sio_stop ? 0x4e : hold ? 0x5a : alarm ? 0x59 : 0x58;",
                         "m1_crumb(0x6004, b, 4);\n\t\tif (sio_stop) {",
                         "b[0] = m1_disp_bits | (m1_smc_acked ? BIT(5) : 0) | m1_sio_code << 6;\n\t\t\tb[1] = ps_words ? M1_W_MARK | m1_pw_bits : M1_K_MARK;\n\t\t\tm1_crumb(0x6012, b, 2);"])
          and "m1_stage(sio_stop ? 0x4f : 0x5b);" in src and "#define M1_K_MARK\t\t0xa5" in src and "K_MARK = 0xa5" in open(TOOL).read(),
          "sio_stop=1 records the steps' outcomes after stage 0x4e, in one command with their marker, and the decoder's marker is the module's")
    # ps_words=1: exactly what Corey OK'd on 2026-11-09, in this order.
    pw = re.findall(r"\{ 0x([0-9a-f]+)ULL, 0x([0-9a-f]{8}), (M1_PW_\w+) \},", src[src.index("} m1_pw[] = {"):src.index("static void __iomem *m1_pw_map")])
    want = [(0x28e580250, 0x200, "UART"), (0x28e580258, 0x200, "UART"), (0x28e580260, 0x200, "UART"), (0x28e580268, 0x200, "UART"), (0x28e580270, 0x200, "UART"),
            (0x292280078, 0x200, "MSG"),
            (0x292280090, 0x300, "USB_AON"), (0x292280098, 0x300, "USB_AON"), (0x2922800a0, 0x300, "USB_AON"),
            (0x28e080330, 0x300, "PCIE"), (0x28e080340, 0x300, "PCIE"), (0x28e080350, 0x300, "PCIE"), (0x28e080360, 0x300, "PCIE"),
            (0x28e080240, 0x300, "COMMON"), (0x28e0802f8, 0x300, "COMMON"), (0x28e080300, 0x300, "COMMON"),
            (0x28e080138, 0x1400024f, "FABRIC"), (0x28e080320, 0x1000030f, "FABRIC"), (0x28e080220, 0x1000030f, "FABRIC"), (0x28e580160, 0x1000030f, "FABRIC")]
    got = [(int(a, 16), int(v, 16), g[len("M1_PW_"):]) for a, v, g in pw]
    check(got == want, "m1_pw[] is the 20 words Corey OK'd, with their values, children first and the fabric last", repr(got))
    check(not {a & ~7 for a, _, _ in got} & {0x28e0802d8, 0x28e700000} and 0x28e0802e0 not in {a for a, _, _ in got} and len({a for a, _, _ in got}) == 20,
          "m1_pw[] has neither the PMP's word, nor 0x28e700000, nor PMS_SRAM, and no word twice", repr(got))
    check("if (e1_never_write(m1_pw[i].addr)) {" in src, "the never-write check covers m1_pw[] at load", "")
    check(in_order(src, ["b[0] = 0x28;", "m1_crumb(0x6004, b, 3);", "if (ps_words)\n\t\tm1_pw_bits = m1_pw_write();", "/* 6: release (hold=1"]),
          "the power words go out after the table's last write and stage 0x28, before the stop bits", "")
    check("if (ps_words && !sio_stop) {" in src, "ps_words=1 refuses to load without sio_stop=1", "")
    check("ok = !((readl(p) ^ m1_pw[i].val) & (GENMASK(28, 24) | GENMASK(3, 0)));" in src and "while (!(ok = !((readl(p) >> 4) & 0xf)) && m1_now() - t0 <= m1_tps / 100)" in src
          and "writel(m1_pw[i].val, p);\n\t\tdsb(sy);" in src and "u8 res = GENMASK(M1_PW_FABRIC, 0);" in src,
          "each word: the value as listed, then its check, 10 ms at most", "")
    check("#define M1_W_MARK\t\t0xc0" in src and "W_MARK = 0xc0" in open(TOOL).read(), "the decoder's power-word mark is the module's", "")
    check('#define M1_SIO_ASC\t\t0x39bc00000ULL' in src and '#define M1_SIO_MBOX_NODE\t"/soc/mbox@39bc08000"' in src
          and "m1_never(M1_SIO_ASC + M1_ASC_CPU_CONTROL, 1)" in src, "SIO's ASC and mailbox are this Mac's, and the load checks the never-write list")
    check(0x39bc00044 not in (0x28e0802d8, 0x28e700000), "SIO's RUN word isn't on the never-write list")

    # recfg_bite=1: the module reads, and never writes, the SRAM; the sample goes out once, after the release, in one command with its stage.
    tool = open(TOOL).read()
    check("#define M1_RECFG_STAGE\t\t0x47" in src and "#define M1_RECFG_STAGE_PEND\t0x46" in src and "RECFG_STAGES = (0x46, 0x47)\n" in tool
          and g["SIO_STOP_STAGES"] == (0x4e, 0x4f, 0x52, 0x4b, 0x3d, 0x1d, 0x15, 0x19, 0x46, 0x47, 0x56, 0x57, 0x51, 0x53, 0x54, 0x55, 0x3e, 0x3f, 0x1e, 0x1f, 0x16, 0x17, 0x1a, 0x1b),
          "recfg_bite=1's stages are 0x46 and 0x47 in the module and in m1-crumbs, with sio_stop=1's stages")
    uses = [l for l in src.splitlines() if re.search(r"\bm1_recfg\b", l)]
    literals = [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", src)]
    check(len(uses) == 9 and not [l for l in uses if "write" in l] and src.count("readl(m1_recfg + ") == 4
          and src.count("RECFG_SRAM") == 1 and "m1_recfg = ioremap_np(RECFG_SRAM, M1_RECFG_MAP);" in src
          and not [x for x in literals if 0x292f00000 <= x < 0x292f70000],
          "the module maps the SRAM's start, reads it at load and in step 8, unmaps it, and holds no other way to the SRAM",
          "\n".join(uses))
    init = src[src.index("static int __init m1_recfg_init(void)"):]
    init = init[:init.index("\n}\n")]
    check(in_order(init, ["m1_recfg = ioremap_np(RECFG_SRAM, M1_RECFG_MAP);", "entry[0] = readl(m1_recfg + RECFG_A2_ENTRY);", "entry[1] = readl(m1_recfg + RECFG_A2_ENTRY + 4);",
                          "seq[i] = readl(m1_recfg + RECFG_A2 + 4 * i);", "verdict = recfg_bite_verdict(RECFG_TABLE, 0, entry, seq);",
                          "ctl = readl(m1_wdt + M1_WD_CTRL(2));", "m1_wd2_bite_awake = readl(m1_wdt + M1_WD_BITE(2));",
                          "if (verdict != RECFG_NEW) {", "return -EINVAL;",
                          "if ((ctl & M1_WD_RESET_EN) || m1_wd2_bite_awake == RECFG_BITE_OLD || m1_wd2_bite_awake == RECFG_BITE_NEW) {", "return -EBUSY;",
                          "return 0;"])
          and "write" not in init,
          "the load refuses unless table A's entry 2 and A2's opening read as expected with the new bite, and unless WD2 is in its awake state; it writes nothing",
          init)
    after = src[src.index("static void __noreturn m1_after_slpa(void)"):]
    after = after[:after.index("\n}\n")]
    check(in_order(after, ["b[0] = fabric_up ? M1_FAB_STAGE_PRE : sio_stop ? 0x4e : hold ? 0x5a : alarm ? 0x59 : 0x58;", "m1_crumb(0x6012, b, 2);",
                           "if (recfg_bite) {\n\t\tsram = readl(m1_recfg + RECFG_BITE);\n\t\tdsb(sy);\n\t\tisb();\n\t}",
                           "/* 9 */", "local_daif_mask();", "if (position)\n\t\tm1_t_first = m1_now();", "writel_relaxed(1, commit);",
                           "if (alarm) {\n\t\tdsb(sy);\n\t\tm1_alarm_arm(rtc);\n\t}",
                           "if (!hold)\n\t\t\tm1_stage(0x5f);\n\t\telse if (!recfg_bite && !wd2_bite)\n\t\t\tm1_stage(sio_stop ? 0x4f : 0x5b);",
                           "writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));",
                           "if (hold)\n\t\tm1_hold_release(held, qto, flags);\n\tif (recfg_bite)\n\t\tm1_recfg_out(sram);", "m1_arm_deep_sleep();"]),
          "step 8 reads the SRAM word after the 0x4e crumbs and before SError is masked, with dsb and isb; a recfg_bite=1 run sends no stage 0x4f,"
          " and its sample goes out after the release and before CPU0's deep WFI", after)
    rout = src[src.index("static void m1_recfg_out(u32 sram)"):]
    rout = rout[:rout.index("\n}\n")]
    check(in_order(rout, ["while (!((ctl = readl(m1_wdt + M1_WD_CTRL(2))) & M1_WD_RESET_EN) && m1_now() - t0 < 3 * m1_tps / 10)",
                          "bite = readl(m1_wdt + M1_WD_BITE(2));", "while (!(phase = readl(m1_pos + M1_POS_PHASE)) && m1_now() - t0 < m1_tps / 100)",
                          "dsb(sy);\n\tisb();\n\tb[0] = ((read_sysreg(isr_el1) >> 6) & 7) ? M1_RECFG_STAGE_PEND : M1_RECFG_STAGE;",
                          "b[1] = m1_recfg_sample(bite, m1_wd2_bite_awake, phase, ctl, sram);", "m1_crumb(0x6004, b, 2);",
                          "writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));"])
          and rout.count("t0 = m1_now();") == 2 and rout.count("writel(") == 1 and "writel_relaxed" not in rout and rout.count("m1_crumb(") == 1
          and "m1_stage(" not in rout,
          "m1_recfg_out(): RESET_EN waited for, up to 0.3 s, then the bite, then PHASE for up to 10 ms while it reads 0; one command with"
          " the stage and the byte; nothing written but that breadcrumb and nub-spmi0's acks", rout)
    check("if (recfg_bite && !ps_words) {" in src
          and ("static_assert(RECFG_WDT == M1_WDT && RECFG_WD2_COUNT == M1_WD_CUR(2) && RECFG_WD2_BITE == M1_WD_BITE(2) &&\n"
               "\t      RECFG_WD2_CTRL == M1_WD_CTRL(2) && RECFG_WD_RESET_EN == M1_WD_RESET_EN);") in src
          and 'recfg_bite ? " recfg_bite=1" : ""' in src and "module_param(recfg_bite, bool, 0444);" in src,
          "recfg_bite=1 refuses to load without ps_words=1; the header's WD2 registers and RESET_EN are the module's; the load line names it;"
          " the parameter can't be set after the load")
    # "The new bite shows the engine ran the edit" rests on the module never writing WD2 itself, and on the load's refusals being used.
    # And, since wd2_bite=1, on its one write to WD2 being the bite, in m1_wd2_out() alone, which a recfg_bite=1 run never calls.
    check(sorted(re.findall(r"write\w*\([^;]*m1_wdt[^;]*;", src)) == ["writel(0, m1_wdt + M1_WD1_CTRL);", "writel(WD2_BITE_NEW, m1_wdt + M1_WD_BITE(2));"]
          and {x for x in literals if 0x2922b0020 <= x <= 0x2922b002c} == {0x2922b0024},
          "the module's two writes through the watchdog mapping are WD1's control and wd2_bite=1's one, WD2's bite; the only literal address"
          " among WD2's registers is the bite's")
    check(("\tif (recfg_bite) {\n\t\tint ret = m1_recfg_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n"
           "\tif (wd2_bite) {\n\t\tint ret = m1_wd2_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n\t/* Last: it reads through pages") in src,
          "m1_init() runs m1_recfg_init() for recfg_bite=1 alone and then m1_wd2_init(), for wd2_bite=1 alone, with only the look's start-up"
          " after it, and returns their refusals")

    # wd2_bite=1: one write to WD2, its bite, by CPU0 after the release, and only if WD2 showed RESET_EN; the sample in one command with its stage.
    check("#define M1_WD2_STAGE\t\t0x57" in src and "#define M1_WD2_STAGE_PEND\t0x56" in src and "WD2_STAGES = (0x56, 0x57) + FAB_WD2 + GATES_WD2 + SIOD_WD2 + PCD_WD2 + SST_WD2 + A2P_WD2\n" in tool
          and g["SAMPLE_STAGES"] == (0x46, 0x47, 0x56, 0x57, 0x51, 0x53, 0x54, 0x55, 0x3e, 0x3f, 0x1e, 0x1f, 0x16, 0x17, 0x1a, 0x1b) and g["WD2_PEND"] == (0x56, 0x51, 0x54, 0x3e, 0x1e, 0x16, 0x1a),
          "wd2_bite=1's stages are 0x56 and 0x57 in the module and in m1-crumbs, with recfg_bite=1's as the stages whose 0x6005 is a sample")
    wout = src[src.index("static void m1_wd2_out(void)"):]
    wout = wout[:wout.index("\n}\n")]
    check(in_order(wout, ["while (!((ctl = readl(m1_wdt + M1_WD_CTRL(2))) & M1_WD_RESET_EN) && m1_now() - t0 < 3 * m1_tps / 10)",
                          "pre = readl(m1_wdt + M1_WD_BITE(2));",
                          "if (ctl & M1_WD_RESET_EN) {\n\t\twritel(WD2_BITE_NEW, m1_wdt + M1_WD_BITE(2));\n\t\tdsb(sy);\n\t}",
                          "post = readl(m1_wdt + M1_WD_BITE(2));", "after = readl(m1_wdt + M1_WD_CTRL(2));", "c0 = readl(m1_wdt + M1_WD_CUR(2));",
                          "while ((c1 = readl(m1_wdt + M1_WD_CUR(2))) == c0 && m1_now() - t0 < m1_tps / 1000)",
                          "while (!(phase = readl(m1_pos + M1_POS_PHASE)) && m1_now() - t0 < m1_tps / 100)",
                          "dsb(sy);\n\tisb();\n\tif ((read_sysreg(isr_el1) >> 6) & 7)\n\t\tb[0] = fabric_up ? M1_FAB_STAGE_PEND : M1_WD2_STAGE_PEND;\n"
                          "\telse\n\t\tb[0] = fabric_up ? M1_FAB_STAGE : M1_WD2_STAGE;",
                          "b[1] = m1_wd2_sample(pre, m1_wd2_bite_awake, ctl, post, after, c0, c1, phase);", "m1_crumb(0x6004, b, 2);",
                          "writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));"])
          and wout.count("t0 = m1_now();") == 3 and wout.count("writel(") == 2 and "writel_relaxed" not in wout and wout.count("m1_crumb(") == 1
          and "m1_stage(" not in wout and wout.count("readl(") == 7 and wout.count("WD2_BITE_NEW") == 1
          and not re.search(r"write\w*\([^;]*M1_WD_(CUR|CTRL)\(2\)", src),
          "m1_wd2_out(): RESET_EN waited for, up to 0.3 s; the bite read; the bite written once, only if RESET_EN showed; the bite, the control and"
          " the count (twice, up to 1 ms) read; PHASE for up to 10 ms while it reads 0; one command with the stage and the byte; nothing else"
          " written but nub-spmi0's acks, and WD2's count and control never", wout)
    winit = src[src.index("static int __init m1_wd2_init(void)"):]
    winit = winit[:winit.index("\n}\n")]
    check(in_order(winit, ["if (!m1_pos)\n\t\tm1_pos = ioremap_np(M1_POS_PAGE, PAGE_SIZE);", "return -ENOMEM;", "ctl = readl(m1_wdt + M1_WD_CTRL(2));",
                           "m1_wd2_bite_awake = readl(m1_wdt + M1_WD_BITE(2));",
                           "if ((ctl & M1_WD_RESET_EN) || m1_wd2_bite_awake == WD2_BITE_A2) {", "return -EBUSY;", "return 0;"])
          and not re.search(r"\bwrite[bwlq]?(_relaxed)?\(", winit),
          "m1_wd2_init(): WD2 and PHASE read awake, the load refused if WD2 reads armed or its bite A2's 30 s; it writes nothing", winit)
    check(in_order(after, ["if (recfg_bite)\n\t\tm1_recfg_out(sram);\n\tif (wd2_bite)\n\t\tm1_wd2_out();", "m1_arm_deep_sleep();"])
          and src.count("m1_wd2_out();") == 1 and src.count("static void m1_wd2_out(void)") == 1,
          "a wd2_bite=1 run sends no stage 0x4f, and its write and sample come after the release and before CPU0's deep WFI, once", after[-1500:])
    check("if (wd2_bite && (!ps_words || recfg_bite)) {" in src and 'wd2_bite ? " wd2_bite=1" : ""' in src and "module_param(wd2_bite, bool, 0444);" in src
          and ("static_assert(WD2_WDT == M1_WDT && WD2_COUNT == M1_WD_CUR(2) && WD2_BITE == M1_WD_BITE(2) && WD2_CTRL == M1_WD_CTRL(2) &&\n"
               "\t      WD2_RESET_EN == M1_WD_RESET_EN && WD2_BITE_A2 == RECFG_BITE_OLD);") in src
          and "static_assert(M1_WDT + M1_WD_BITE(2) == 0x2922b0024ULL && WD2_BITE_NEW == 0xaba95000U);" in src,
          "wd2_bite=1 refuses to load without ps_words=1 or with recfg_bite=1; the header's WD2 registers are the module's, and the register and"
          " value are the ones Corey OK'd; the load line names it; the parameter can't be set after the load")
    check(0x2922b0024 not in (0x28e0802d8, 0x28e700000), "WD2's bite isn't on the never-write list")
    check("\tif (!m1_pos)\n\t\tm1_pos = ioremap_np(M1_POS_PAGE, PAGE_SIZE);\n\tif (!m1_recfg || !m1_pos) {" in init and init.count("return -ENOMEM;") == 1
          and ("seq[0], seq[1], seq[2], seq[3], seq[4], seq[5], ctl, readl(m1_wdt + M1_WD_CUR(2)),\n"
               "\t\tm1_wd2_bite_awake, readl(m1_pos + M1_POS_PHASE));") in init
          and init.count("ioremap") == 2,
          "m1_recfg_init(): PHASE's page mapped non-posted too, a failed mapping refused, and the load line shows WD2's own awake words")
    check(rout.count("readl(") == 3 and rout.count("M1_WD_BITE(2)") == 1 and rout.count("M1_POS_PHASE") == 1 and rout.count("M1_WD_CTRL(2)") == 1,
          "m1_recfg_out() reads three registers, each in one place: WD2's control, WD2's bite, PHASE")

    # smc_idle=1 and one_commit=1.
    smc = src[src.index("static bool m1_smc_idle(void)"):]
    smc = smc[:smc.index("\n}\n")]
    check(in_order(smc, ["msleep(6);", "apple_rtkit_is_running(m1_smc_rtk)", "apple_rtkit_send_message(m1_smc_rtk, M1_RTKIT_EP_MGMT, M1_RTKIT_SET_AP_PWR | M1_RTKIT_PWR_IDLE, NULL, false)",
                         "!apple_rtkit_is_running(m1_smc_rtk)", "m1_tps / 5", "msleep(30);", "m1_kwrite(0x6004, &stage, 1);"]) and smc.count("apple_rtkit_send_message(") == 1,
          "K5: one SetAPPower 0x201 to the SMC, between the gaps, the ack read as RTKit no longer running", smc)
    check("#define M1_RTKIT_EP_MGMT\t0" in src and "#define M1_RTKIT_SET_AP_PWR\t(0xbULL << 52)" in src and "#define M1_RTKIT_PWR_IDLE\t0x201ULL" in src
          and '#define M1_SMC_MBOX_NODE\t"/soc/mbox@290408000"' in src, "the notice's encoding and the SMC's mailbox are RTKit's and this Mac's")
    check(in_order(after, ["pmpb = m1_pmp_out();", "if (smc_idle)\n\t\tm1_smc_acked = m1_smc_idle();", "preempt_disable();"]), "K5 comes after K4, with interrupts still on", after[:2000])
    com = src[src.index("m1_t_first = m1_now();"):]
    com = com[:com.index("m1_t_third = m1_now();")]
    check("if (!one_commit) {\n\t\twritel_relaxed(1, commit);\n\t\twritel_relaxed(1, commit);\n\t}" in com
          and in_order(com, ["writel_relaxed(1, commit);", "m1_alarm_arm(rtc);", "if (!one_commit) {"]),
          "one_commit=1 skips the second and third commit writes; the alarm stays after the first", com)
    check("(one_commit || smc_idle) && !disp_off" in src, "e1_check_params refuses one_commit=1 or smc_idle=1 without disp_off=1")
    never = (0x28e0802d8, 0x28e700000)
    check(not {0x38bc00044, 0x289c00044, 0x28e580350, 0x28e580328, 0x28e080280, 0x28e080258} & set(never), "none of the display writes is on the never-write list")
    check(not {0x28e580180, 0x28e580230, 0x28e5801e8} & set(never), "none of the three raises is on the never-write list")
    check("0x6004 && end <= 0x6008" in src and "0x6010 && end <= 0x6017" in src, "the breadcrumb range is unchanged")
    others = set(g["STAGES"]) | set(g["OBSERVE_STAGES"]) | set(g["LATE"]) | set(range(0x21, 0x28)) | set(range(0x31, 0x3a)) | {0x5a, 0x5b, 0x5c, 0x5d, 0x5e}
    check(not set(g["POSITION"]) & others and not set(g["SIO_STOP_STAGES"]) & (others | set(g["POSITION"]) | set(g["DISP_STAGES"])), "position=1's and sio_stop=1's stages collide with no other run's")


HARNESS = """#include <inttypes.h>
#include <stdio.h>
#include "m1_position.h"
int main(void)
{
	uint32_t p, h, st, sio, ctrl, count, g44, g40;
	uint64_t el, tps;

	while (scanf("%" SCNx32 " %" SCNx32 " %" SCNx32 " %" SCNx32 " %" SCNx32 " %" SCNx32 " %" SCNx32 " %" SCNx32 " %" SCNx64 " %" SCNx64,
		     &p, &h, &st, &sio, &ctrl, &count, &g44, &g40, &el, &tps) == 10)
		printf("%02x %02x\\n", m1_pos_byte_a(p, h, st), m1_pos_byte_b(sio, ctrl, m1_pos_count_near(count, el, tps), g44, g40));
	return 0;
}
"""


if __name__ == "__main__":
    sys.exit(main())
