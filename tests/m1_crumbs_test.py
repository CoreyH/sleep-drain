#!/usr/bin/env python3
"""e1-hook/m1-crumbs against a fake PMU nvmem file (M1_CRUMBS_NVMEM).

Each case writes a 64 KiB file shaped like the PMU's nvmem, with chosen bytes
at 0x6000-0x601f and the RTC at 0x1406, runs the tool, and checks what it
prints. --clear must zero 0x6004-0x6007 and 0x6010-0x6016 and nothing else;
an unknown argument changes nothing.

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


def run(path, *args):
    env = dict(os.environ, M1_CRUMBS_NVMEM=path)
    return subprocess.run([sys.executable, "-I", TOOL, *args], env=env, capture_output=True, text=True, timeout=30)


def scratch(stage, parked=0, left=0, down=0, secs=0, flags=0, isrs=0, drained=0, boot=0x30, err=0x50):
    s = bytearray(0x20)
    s[1], s[3] = boot, err
    s[4:8] = bytes((stage, parked, left, down))
    s[0x10:0x14] = secs.to_bytes(4, "little")
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

    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
