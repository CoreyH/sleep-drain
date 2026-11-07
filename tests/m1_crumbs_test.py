#!/usr/bin/env python3
"""e1-hook/m1-crumbs against a fake PMU nvmem file (M1_CRUMBS_NVMEM).

Each case writes a 64 KiB file shaped like the PMU's nvmem, with chosen bytes
at 0x6000-0x601f and the RTC at 0x1406, runs the tool with a fake SoC counter
(M1_CRUMBS_CNTVCT), and checks what it prints: run 1's stages and the
observer's (observe=1), and the reset time after a commit. --clear must zero
0x6004-0x6007 and 0x6010-0x6016 and nothing else; an unknown argument changes
nothing.

  python3 -I tests/m1_crumbs_test.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.realpath(os.path.join(HERE, "..", "e1-hook", "m1-crumbs"))

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

        # Run 1's reset, 30.6 s after the commit: the counter is 199.4 s old
        # at RTC second 5200, and the commit was at 4970.
        fake(path, scratch(0x50, parked=0xfe, left=0xfe, down=0xfe, secs=4970, flags=0xb0), rtc_secs=5200)
        r = run(path, cnt=f"{int(199.4 * 24000000)} 24000000")
        check(r.returncode == 0 and "the SoC restarted 30.6 s after the commit" in r.stdout
              and "199.4 s old" in r.stdout,
              "after a commit: the reset time from the counter and the RTC", r.stdout + r.stderr)
        r = run(path, cnt="none")
        check(r.returncode == 0 and "restarted" not in r.stdout, "no counter: no reset time", r.stdout + r.stderr)
        fake(path, scratch(0x30, parked=0xfe, left=0xfe, down=0xfe, secs=4970), rtc_secs=5200)
        r = run(path)
        check(r.returncode == 0 and "restarted" not in r.stdout,
              "a run that didn't reach the commit: no reset time", r.stdout + r.stderr)

        # The observer, all of it landed: CPU0 and ECPM off, WD2 armed with 29 s
        # left, the commit's second in two bytes, PACC1 busy, nothing new in the
        # wake status.
        fake(path, scratch(0x6f, parked=0xff, left=0x04, down=29, secs=4970, prog=(0x12, 0x34), flags=0x90,
                           isrs=0x04, drained=0x80), rtc_secs=5200)
        r = run(path, cnt=f"{int(199.4 * 24000000)} 24000000")
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x6f: observe: CPU7 recorded everything" in out
              and "CPU0 read off within 500 ms" in out and "(ECPM) read off within 500 ms" in out
              and "read off at their release: CPU1,CPU2,CPU3,CPU4,CPU5,CPU6" in out
              and "WD2 control 4 (RESET_EN); WD1 control 0 (off); WD0 not armed" in out
              and "CPU0's last WFI had not returned" in out
              and "0x6007 = 29: 29 s until the first armed watchdog's reset" in out
              and "RTC second just before the commit (from its low two bytes): 4970, 230 s before now" in out
              and "0x6012 0x6013 = 12 34" in out and "PCPM1 not checked (CPU7 up)" in out
              and "soc clusters still busy: PACC1" in out and "SIO_CPU's actual state off" in out
              and "commit: none" in out and "the SoC restarted 30.6 s after the commit" in out
              and "parked (" not in out,
              "the observer's 0x6f: every field decoded", out + r.stderr)

        # Only 0x61 landed: CPU0 and ECPM not off, CPU0's WFI had returned;
        # 0x6010-0x6016 are still CPU0's.
        fake(path, scratch(0x61, parked=0x7e, left=0x84, down=30, secs=4970, flags=0x90, isrs=0, drained=2),
             rtc_secs=5200)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "CPU0 did NOT read off" in out and "(ECPM) did NOT read off" in out
              and "HAD RETURNED" in out and "0x6007 = 30" in out
              and "RTC second at just before the commit: 4970" in out
              and "0x6016 = 2: AIC events" in out and "soc clusters" not in out,
              "the observer's 0x61: CPU7's three bytes, CPU0's others", out + r.stderr)

        # 0x60 only: 0x6005-0x6007 still say what 0x30 wrote.
        fake(path, scratch(0x60, parked=0xfe, left=0x7e, down=0x7e, secs=4970, flags=0x90), rtc_secs=5200)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "0x6004 = 0x60: observe: CPU7 began" in out
              and "parked (0x6005 = 0xfe)" in out and "read off within 10 ms (0x6007 = 0x7e)" in out
              and "PCPM1 not checked" in out,
              "the observer's 0x60: run 1's meanings for 0x6005-0x6007", out + r.stderr)

        # 0x6f with 0x6016's bit 7 lost; and the commit's second across a
        # 16-bit wrap, a watchdog none, a cluster and SIO_CPU still on.
        fake(path, scratch(0x6f, parked=0x81, left=0x40, down=255, secs=0xfff0, prog=(0, 0), flags=0x80,
                           isrs=0xa0, drained=0x00), rtc_secs=0x10005)
        r = run(path)
        out = r.stdout
        check(r.returncode == 0 and "didn't survive the reset" in out and "AP wake-status" not in out
              and f"from its low two bytes): {0xfff0}, 21 s before now" in out
              and "no watchdog was armed" in out and "WD1 control 4 (RESET_EN)" in out
              and "soc clusters still busy: DISPLAY" in out and "SIO_CPU's actual state NOT off" in out,
              "the observer's 0x6f: a lost 0x6016, a 16-bit wrap, WD1 armed, DISPLAY and SIO_CPU on",
              out + r.stderr)

    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
