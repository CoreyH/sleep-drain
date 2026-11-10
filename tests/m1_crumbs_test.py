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
          and g["ALARM_STAGES"] == (0x59, 0x5f, 0x5a, 0x5b, 0x5c, 0x5d, 0x5e, 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x4e, 0x4f, 0x52, 0x46, 0x47, 0x56, 0x57,
                                    0x51, 0x53)
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
    # 0x31-0x37 and 0x39 for hold=1's stop requests, 0x4e before the commit, wd2_bite=1's two after it.
    check(g["ACK_EARLY"] == (0x28, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x39) and g["ACK_MARKED"] == (0x4e, 0x56, 0x57, 0x52, 0x51, 0x53)
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
    check(len(re.findall(wr, code_only)) == 42 and len(re.findall(wr, after_code)) == 11,
          "e1_hook.c makes 42 MMIO write calls, 11 of them in m1_after_slpa() itself: none has been added without this count",
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
                  " or fabric_up=1's 0x52, 0x51, 0x53, with pmp_out=1's bit 7). Here they aren't that: don't read them\n")
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
        NOMARK = ("  (nothing here says whether this was a cluster_ack=1 run: its mark is 0x6014's bit 4, sent just before stage 0x4e (0x52 with fabric_up=1){}."
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
    check("if (disp_off && n == 1 && !READ_ONCE(replay_done))\n\t\tm1_dcp_shutdown();" in src[src.index("static int e1_prepare(void)"):src.index("static int e1_prepare(void)") + 500],
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
    check("if (sio_stop && n == 1 && !READ_ONCE(replay_done))\n\t\tm1_sio_fw_stop();" in src[src.index("static int e1_prepare(void)"):src.index("static int e1_prepare(void)") + 700],
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
          and g["SIO_STOP_STAGES"] == (0x4e, 0x4f, 0x52, 0x46, 0x47, 0x56, 0x57, 0x51, 0x53),
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
           "\tif (wd2_bite) {\n\t\tint ret = m1_wd2_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n\treturn 0;\n}") in src,
          "m1_init() runs m1_recfg_init() for recfg_bite=1 alone and m1_wd2_init() last, for wd2_bite=1 alone, and returns their refusals")

    # wd2_bite=1: one write to WD2, its bite, by CPU0 after the release, and only if WD2 showed RESET_EN; the sample in one command with its stage.
    check("#define M1_WD2_STAGE\t\t0x57" in src and "#define M1_WD2_STAGE_PEND\t0x56" in src and "WD2_STAGES = (0x56, 0x57) + FAB_WD2\n" in tool
          and g["SAMPLE_STAGES"] == (0x46, 0x47, 0x56, 0x57, 0x51, 0x53) and g["WD2_PEND"] == (0x56, 0x51),
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
