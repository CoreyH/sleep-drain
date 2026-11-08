#!/usr/bin/env python3
"""e1-hook/m1-crumbs against a fake PMU nvmem file (M1_CRUMBS_NVMEM).

Each case writes a 64 KiB file shaped like the PMU's nvmem, with chosen bytes
at 0x6000-0x601f and the RTC at 0x1406, runs the tool with a fake SoC counter
(M1_CRUMBS_CNTVCT), and checks what it prints: run 1's stages, the
observer's (observe=1) and run 3's (pmgr_late=1), and the reset time after a
commit. --clear must zero
0x6004-0x6007 and 0x6010-0x6016 and nothing else; an unknown argument changes
nothing.

  python3 -I tests/m1_crumbs_test.py
"""
import os
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


def fake(path, scratch, rtc_secs=5000):
    b = bytearray(0xffff)
    b[0x6000:0x6000 + len(scratch)] = scratch
    b[0x1406:0x140c] = bytes(2) + rtc_secs.to_bytes(4, "little")
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

    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
