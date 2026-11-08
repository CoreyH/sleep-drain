#!/usr/bin/env python3
"""e1-hook/recfg_peek's stages and e1-hook/recfg-peek's order, without hardware.

The reader is compiled with its memory device pointed at a sparse file whose
offsets are physical addresses, so each case writes the words it wants at the
real addresses. The launcher runs as uid 0 in a bubblewrap user namespace, with
a stub reader and a stub sleep-drain-safe that record what they're asked.

  python3 -I tests/recfg_peek_test.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))

CFG, LOCK, SRAM, SRAM_LEN = 0x2925c0200, 0x2925c0214, 0x292f00000, 0x70000

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("     " + str(detail))


class Mem:
    def __init__(self, path):
        self.path = path
        with open(path, "wb") as f:
            f.truncate(SRAM + SRAM_LEN)

    def word(self, addr, value):
        with open(self.path, "r+b") as f:
            f.seek(addr)
            f.write(struct.pack("<I", value))

    def fill(self, addr, length, byte=0xaa):
        with open(self.path, "r+b") as f:
            f.seek(addr)
            f.write(bytes([byte]) * length)


def table(mem, base, targets):
    """Writes table words at base: each target's physical address >> 4."""
    for i, t in enumerate(targets):
        mem.word(base + 4 * i, t >> 4)


def run(binary, stage, outdir):
    return subprocess.run([binary, str(stage), outdir], capture_output=True, text=True)


with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
    binary = os.path.join(tmp, "recfg_peek")
    fake = os.path.join(tmp, "mem")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror",
                    '-DRECFG_MEM="%s"' % fake, "-o", binary,
                    os.path.join(E1_HOOK, "recfg_peek.c")], check=True)

    # A table at the SRAM's start, as T8103's is: 12 sequences, the last at
    # 0x1c000, and 4 entries left empty.
    mem = Mem(fake)
    mem.fill(SRAM, 0x1c000 + 0x10000, 0x5a)
    targets = [SRAM + 0x40 + 0x2000 * i for i in range(11)] + [SRAM + 0x1c000]
    table(mem, SRAM, targets)
    for i in range(12, 16):
        mem.word(SRAM + 4 * i, 0)
    mem.word(CFG, 0)
    mem.word(LOCK, 1)
    out = os.path.join(tmp, "o1")
    os.mkdir(out)

    r = run(binary, 1, out)
    check(r.returncode == 0, "stage 1 goes on")
    check("0x2925c0200 = 0x00000000" in r.stdout, "stage 1 prints the offset register's word", r.stdout)
    check("0x2925c0214 = 0x00000001" in r.stdout, "stage 1 prints the lock's word", r.stdout)
    r = run(binary, 2, out)
    check(r.returncode == 0 and "0x292f00000 = " in r.stdout, "stage 2 prints the SRAM's first word", r.stdout)
    r = run(binary, 3, out)
    check(r.returncode == 0, "stage 3 goes on with a table", r.stdout)
    check("SRAM + the offset register" in r.stdout, "stage 3 says where it looked", r.stdout)
    check("12 of 16 entries" in r.stdout, "stage 3 counts the entries that point into the SRAM", r.stdout)
    check("in the SRAM" in r.stdout and "table[ 0]" in r.stdout, "stage 3 prints each entry", r.stdout)
    check(not os.listdir(out), "stage 3 writes no dump", os.listdir(out))
    r = run(binary, 4, out)
    first = os.path.join(out, "sram-00000.bin")
    check(r.returncode == 0 and os.path.getsize(first) == 0x10000, "stage 4 dumps 0x10000 bytes", r.stdout + r.stderr)
    with open(first, "rb") as f:
        got = f.read()
    check(got[0:4] == struct.pack("<I", targets[0] >> 4), "stage 4's dump starts at the SRAM's first word")
    r = run(binary, 5, out)
    second = os.path.join(out, "sram-10000.bin")
    # 0x1c000 + 0x10000 = 0x2c000, already a 16 KiB boundary.
    check(r.returncode == 0 and os.path.getsize(second) == 0x2c000 - 0x10000,
          "stage 5 reads to the last sequence + 0x10000", r.stdout + r.stderr)
    r = run(binary, 4, out)
    check(r.returncode == 1, "a dump refuses to overwrite an earlier one", r.stdout + r.stderr)

    # Rounding up, and the cap at the SRAM's end.
    mem = Mem(fake)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(5)] + [SRAM + 0x1c010])
    out2 = os.path.join(tmp, "o2")
    os.mkdir(out2)
    r = run(binary, 5, out2)
    check(r.returncode == 0 and os.path.getsize(os.path.join(out2, "sram-10000.bin")) == 0x30000 - 0x10000,
          "stage 5 rounds up to 16 KiB", r.stdout + r.stderr)
    mem = Mem(fake)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(5)] + [SRAM + SRAM_LEN - 0x10])
    out3 = os.path.join(tmp, "o3")
    os.mkdir(out3)
    r = run(binary, 5, out3)
    check(r.returncode == 0 and os.path.getsize(os.path.join(out3, "sram-10000.bin")) == SRAM_LEN - 0x10000,
          "stage 5 stops at the end of the SRAM", r.stdout + r.stderr)

    # A table whose sequences all fit in the first dump: nothing more to read.
    mem = Mem(fake)
    table(mem, SRAM, [SRAM] * 6)
    out4 = os.path.join(tmp, "o4")
    os.mkdir(out4)
    r = run(binary, 5, out4)
    check(r.returncode == 0 and "hold it" in r.stdout and not os.listdir(out4),
          "stage 5 reads nothing when the first dump holds it all", r.stdout + os.path.basename(out4))

    # An offset register inside the SRAM moves the table.
    mem = Mem(fake)
    mem.word(CFG, 0x1000)
    table(mem, SRAM + 0x1000, [SRAM + 0x2000 + 0x100 * i for i in range(8)])
    r = run(binary, 3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "0x292f01000" in r.stdout and "8 of 16" in r.stdout,
          "stage 3 honours an offset that fits", r.stdout)

    # An offset register that doesn't fit: SRAM + 0, said so.
    mem = Mem(fake)
    mem.word(CFG, 0x80000)
    table(mem, SRAM, [SRAM + 0x2000 + 0x100 * i for i in range(8)])
    r = run(binary, 3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "doesn't fit" in r.stdout and "8 of 16" in r.stdout,
          "stage 3 falls back to SRAM + 0 when the offset doesn't fit", r.stdout)
    mem.word(CFG, 3)
    r = run(binary, 3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "doesn't fit" in r.stdout, "an unaligned offset falls back too", r.stdout)

    # Stops, and no dump, when there's no table.
    mem = Mem(fake)
    out5 = os.path.join(tmp, "o5")
    os.mkdir(out5)
    r = run(binary, 3, out5)
    check(r.returncode == 3 and "no table" in r.stdout, "all zeros aren't a table", r.stdout)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(3)])
    r = run(binary, 3, out5)
    check(r.returncode == 3 and "3 of 16" in r.stdout, "three entries aren't enough for a table", r.stdout)
    r = run(binary, 5, out5)
    check(r.returncode == 3 and not os.listdir(out5), "stage 5 refuses without a table", r.stdout)
    table(mem, SRAM, [SRAM + SRAM_LEN + 0x100 * i for i in range(12)])
    r = run(binary, 3, out5)
    check(r.returncode == 3 and "0 of 16" in r.stdout, "entries past the SRAM's end don't count", r.stdout)
    mem = Mem(fake)
    mem.word(SRAM, 0xffffffff)
    r = run(binary, 2, out5)
    check(r.returncode == 3 and "all ones" in r.stdout, "stage 2 stops at all ones", r.stdout)

    # Its arguments: no address can be asked for.
    for args in (["0", tmp], ["6", tmp], ["12", tmp], ["x", tmp], ["1"], ["0x2925c0200", tmp], []):
        r = subprocess.run([binary] + args, capture_output=True, text=True)
        check(r.returncode == 2, "refuses arguments " + repr(args), r.stdout + r.stderr)

    # The launcher, as uid 0 in a user namespace.
    if shutil.which("bwrap") is None:
        print("FAIL bwrap is missing")
        failed += 1
    else:
        launcher = os.path.join(E1_HOOK, "recfg-peek")

        def sandbox(*, safe=True, rc_by_stage=None, save_fails=False, uid=0):
            """Runs the launcher with stubs; returns (rc, stdout+stderr, saves, stages run)."""
            rc_by_stage = rc_by_stage or {}
            root = tempfile.mkdtemp(dir=tmp)
            calls = os.path.join(root, "calls")
            stub = os.path.join(root, "recfg_peek")
            rcs = " ".join(f"[{k}]={v}" for k, v in rc_by_stage.items())
            with open(stub, "w") as f:
                f.write(f"""#!/bin/bash
declare -A rc=({rcs})
echo "stage $1" >> {calls}
echo "the reader's stage $1"
[[ -d $2 ]] || {{ echo 'no dir' >&2; exit 9; }}
exit ${{rc[$1]:-0}}
""")
            os.chmod(stub, 0o755)
            save = os.path.join(root, "safe")
            with open(save, "w") as f:
                f.write(f"""#!/bin/bash
echo "save $*" >> {calls}
{'exit 1' if save_fails else 'exit 0'}
""")
            os.chmod(save, 0o755)
            flag = os.path.join(root, "active")
            if safe:
                open(flag, "w").close()
            log_root = os.path.join(root, "log")
            os.mkdir(log_root)
            r = subprocess.run(
                ["bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                 "--unshare-user", "--uid", str(uid), "--gid", "0", "--",
                 "env", "RECFG_BIN=" + stub, "RECFG_SAFE_FLAG=" + flag,
                 "RECFG_SAVE=" + save, "RECFG_LOG_ROOT=" + log_root, launcher],
                capture_output=True, text=True)
            lines = open(calls).read().split("\n")[:-1] if os.path.exists(calls) else []
            return r.returncode, r.stdout + r.stderr, lines, log_root

        rc, out, calls, logs = sandbox()
        check(rc == 0 and calls == ["stage 1", "save save", "stage 2", "save save", "stage 3", "save save",
                                    "stage 4", "save save", "stage 5", "save save"],
              "the launcher runs the five stages in order, saving after each", calls)
        check("done" in out, "and says when it's done", out)
        logdir = os.path.join(logs, os.listdir(logs)[0])
        text = open(os.path.join(logdir, "log.txt")).read()
        check("the reader's stage 3" in text and "== stage 3" in text, "it logs each stage", text)

        rc, out, calls, logs = sandbox(safe=False)
        check(rc == 1 and "safe boot only" in out and calls == [], "refuses outside a safe boot", out)
        rc, out, calls, logs = sandbox(uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "refuses to run as non-root", out)
        rc, out, calls, logs = sandbox(rc_by_stage={3: 3})
        check(rc == 0 and calls == ["stage 1", "save save", "stage 2", "save save", "stage 3", "save save"] and "stopped at stage 3" in out,
              "a stage that says stop ends it, saved", calls)
        rc, out, calls, logs = sandbox(rc_by_stage={2: 3})
        check(calls == ["stage 1", "save save", "stage 2", "save save"], "stops at stage 2's all ones, saved", calls)
        rc, out, calls, logs = sandbox(rc_by_stage={2: 1})
        check(rc == 1 and calls == ["stage 1", "save save", "stage 2"] and "stage 2 failed" in out,
              "a stage's error stops it", calls)
        rc, out, calls, logs = sandbox(save_fails=True)
        check(rc == 1 and calls == ["stage 1", "save save"] and "not reading further" in out,
              "a failed save stops it before the next read", calls)

print("\n%d failures" % failed)
sys.exit(1 if failed else 0)
