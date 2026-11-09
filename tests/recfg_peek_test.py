#!/usr/bin/env python3
"""e1-hook/recfg_peek's stages and e1-hook/recfg-peek's order, without hardware.

The reader is compiled with its memory device pointed at a sparse file whose
offsets are physical addresses, so each case writes the words it wants at the
real addresses, and with -DRECFG_TRACE, so that every read's physical address
is logged and checked against what that stage may touch. The launcher runs as
uid 0 in a bubblewrap user namespace, with a stub reader and a stub
sleep-drain-safe that record what they're asked and what the log directory
held at each save: sleep-drain-safe collect keeps the first copy of a directory
and stops on one that differs, so a stage's directory must not change after
its save. It also runs the position read's stages (a-h, one word each but a), the
launcher's --position (saved after each, then done.txt and the mark run-floor
needs) and --position-after (a regular boot, only with a collected done.txt),
stage 6, the ten words that say where A2's WD2 bite is (recfg_bite.h), and
stage 7, that word alone.

  python3 -I tests/recfg_peek_test.py
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))

CFG, LOCK, SRAM, SRAM_LEN = 0x2925c0200, 0x2925c0214, 0x292f00000, 0x70000
FIRST = 0x10000
PAGE = 0x4000

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


with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
    binary = os.path.join(tmp, "recfg_peek")
    fake = os.path.join(tmp, "mem")
    trace = os.path.join(tmp, "trace")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror",
                    '-DRECFG_MEM="%s"' % fake, '-DRECFG_TRACE="%s"' % trace, "-o", binary,
                    os.path.join(E1_HOOK, "recfg_peek.c")], check=True)

    def run(stage, outdir):
        """Runs one stage; returns (result, the addresses it read, in order)."""
        if os.path.exists(trace):
            os.unlink(trace)
        r = subprocess.run([binary, str(stage), outdir], capture_output=True, text=True)
        addrs = []
        if os.path.exists(trace):
            with open(trace) as f:
                addrs = [int(line, 16) for line in f.read().split()]
        return r, addrs

    def sram_only(addrs):
        return all(SRAM <= a <= SRAM + SRAM_LEN - 4 and a % 4 == 0 for a in addrs)

    def fresh(name):
        d = os.path.join(tmp, name)
        os.mkdir(d)
        return d

    # A table at the SRAM's start, as T8103's is: 12 sequences, the last at
    # 0x1c000, and 4 entries left empty.
    mem = Mem(fake)
    mem.fill(SRAM, 0x1c000 + 0x10000, 0x5a)
    targets = [SRAM + 0x40 + 0x2000 * i for i in range(11)] + [SRAM + 0x1c000]
    table(mem, SRAM, targets)
    for i in range(12, 16):
        mem.word(SRAM + 4 * i, 0)
    mem.word(CFG, 0)
    mem.word(LOCK, 0x12345678)
    out = fresh("o1")

    r, a = run(1, out)
    check(r.returncode == 0, "stage 1 goes on")
    check("0x2925c0200 = 0x00000000" in r.stdout, "stage 1 prints the offset register's word", r.stdout)
    check("0x2925c0214 = 0x12345678" in r.stdout, "stage 1 prints the lock's word", r.stdout)
    check(a == [CFG, LOCK], "stage 1 reads exactly the offset register and its lock", a)
    check(r.stdout.index("reading 0x2925c0200") < r.stdout.index("= 0x00000000"), "stage 1 says what it's reading before each read", r.stdout)
    r, a = run(2, out)
    expected = "0x292f00000 = 0x%08x" % (targets[0] >> 4)
    check(r.returncode == 0 and expected in r.stdout, "stage 2 prints the SRAM's actual first word", r.stdout)
    check(a == [SRAM], "stage 2 reads exactly the SRAM's first word", a)
    check("reading 0x292f00000" in r.stdout, "stage 2 says what it's reading before the read", r.stdout)
    r, a = run(3, out)
    check(r.returncode == 0, "stage 3 goes on with a table", r.stdout)
    check("SRAM + the offset register" in r.stdout, "stage 3 says where it looked", r.stdout)
    check("12 of 16 entries" in r.stdout, "stage 3 counts the entries that point into the SRAM", r.stdout)
    check("in the SRAM" in r.stdout and "table[ 0]" in r.stdout, "stage 3 prints each entry", r.stdout)
    check(not os.listdir(out), "stage 3 writes no dump", os.listdir(out))
    check(r.stdout.index("reading 16 words") < r.stdout.index("table[ 0]"), "stage 3 announces the table read first", r.stdout)
    check(a == [CFG] + [SRAM + 4 * i for i in range(16)], "stage 3 reads the offset register and the 16 words, nothing else", a)
    r, a = run(4, out)
    first = os.path.join(out, "sram-00000.bin")
    check(r.returncode == 0 and os.path.getsize(first) == FIRST, "stage 4 dumps 0x10000 bytes", r.stdout + r.stderr)
    with open(first, "rb") as f:
        got = f.read()
    check(got[0:4] == struct.pack("<I", targets[0] >> 4), "stage 4's dump starts at the SRAM's first word")
    check(a == [SRAM + i for i in range(0, FIRST, 4)], "stage 4 reads the first 0x10000 bytes, word by word, in order", (len(a), a[:3], a[-3:]))
    check([l for l in r.stdout.splitlines() if l.startswith("reading")] ==
          ["reading 0x%x" % (SRAM + i) for i in range(0, FIRST, PAGE)],
          "stage 4 says each 16 KiB page before reading it", r.stdout)
    mem.word(SRAM + FIRST, 0xdeadbeef)
    mem.word(SRAM + 0x2c000 - 4, 0xfeedface)
    r, a = run(5, out)
    second = os.path.join(out, "sram-10000.bin")
    # 0x1c000 + 0x10000 = 0x2c000, already a 16 KiB boundary.
    check(r.returncode == 0 and os.path.getsize(second) == 0x2c000 - FIRST,
          "stage 5 reads to the last sequence + 0x10000", r.stdout + r.stderr)
    with open(second, "rb") as f:
        got = f.read()
    check(got[:4] == struct.pack("<I", 0xdeadbeef) and got[-4:] == struct.pack("<I", 0xfeedface),
          "stage 5's dump is the part after the first dump", got[:4])
    dumped = a[17:]
    check(a[:17] == [CFG] + [SRAM + 4 * i for i in range(16)] and dumped == [SRAM + i for i in range(FIRST, 0x2c000, 4)],
          "stage 5 reads the table and then only the part it dumps", (a[:18], a[-2:]))
    r, a = run(4, out)
    check(r.returncode == 1, "a dump refuses to overwrite an earlier one", r.stdout + r.stderr)

    # The highest entry counts, in any order.
    mem = Mem(fake)
    table(mem, SRAM, [SRAM + 0x1c000] + [SRAM + 0x100 * (i + 1) for i in range(5)])
    out2 = fresh("o1a")
    r, a = run(5, out2)
    check(r.returncode == 0 and os.path.getsize(os.path.join(out2, "sram-10000.bin")) == 0x2c000 - FIRST,
          "stage 5 goes by the highest entry, not the last", r.stdout + r.stderr)

    # Rounding up, and the cap at the SRAM's end.
    mem = Mem(fake)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(5)] + [SRAM + 0x1c010])
    out2 = fresh("o2")
    r, a = run(5, out2)
    check(r.returncode == 0 and os.path.getsize(os.path.join(out2, "sram-10000.bin")) == 0x30000 - FIRST,
          "stage 5 rounds up to 16 KiB", r.stdout + r.stderr)
    mem = Mem(fake)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(5)] + [SRAM + SRAM_LEN - 0x10])
    out3 = fresh("o3")
    r, a = run(5, out3)
    check(r.returncode == 0 and os.path.getsize(os.path.join(out3, "sram-10000.bin")) == SRAM_LEN - FIRST,
          "stage 5 stops at the end of the SRAM", r.stdout + r.stderr)
    check(sram_only(a[1:]) and max(a) == SRAM + SRAM_LEN - 4, "and its last read is the SRAM's last word", hex(max(a)))

    # The degenerate table, with every entry at the SRAM's start: no second dump.
    mem = Mem(fake)
    table(mem, SRAM, [SRAM] * 6)
    out4 = fresh("o4")
    r, a = run(5, out4)
    check(r.returncode == 0 and "hold it" in r.stdout and not os.listdir(out4),
          "stage 5 has nothing further to read when every entry is at the SRAM's start", r.stdout)

    # An offset register inside the SRAM moves the table.
    mem = Mem(fake)
    mem.word(CFG, 0x1000)
    table(mem, SRAM + 0x1000, [SRAM + 0x2000 + 0x100 * i for i in range(8)])
    r, a = run(3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "0x292f01000" in r.stdout and "8 of 16" in r.stdout,
          "stage 3 honours an offset that fits", r.stdout)
    check(a == [CFG] + [SRAM + 0x1000 + 4 * i for i in range(16)], "and reads the table there", a[:4])

    # The offset register's boundary: 16 words must fit in the SRAM.
    mem = Mem(fake)
    mem.word(CFG, SRAM_LEN - 64)
    table(mem, SRAM + SRAM_LEN - 64, [SRAM + 0x2000 + 0x100 * i for i in range(8)])
    r, a = run(3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "SRAM + the offset register" in r.stdout and "8 of 16" in r.stdout,
          "an offset that just fits (0x6ffc0) is used", r.stdout)
    check(sram_only(a[1:]) and a[-1] == SRAM + SRAM_LEN - 4, "and the table's last word is the SRAM's last", hex(a[-1]))
    mem.word(CFG, SRAM_LEN - 60)
    r, a = run(3, os.path.join(tmp, "o4"))
    check("doesn't fit" in r.stdout and sram_only(a[1:]), "an offset 4 bytes further (0x6ffc4) falls back to SRAM + 0", r.stdout)

    # An offset register that doesn't fit: SRAM + 0, said so.
    mem = Mem(fake)
    mem.word(CFG, 0x80000)
    table(mem, SRAM, [SRAM + 0x2000 + 0x100 * i for i in range(8)])
    r, a = run(3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "doesn't fit" in r.stdout and "8 of 16" in r.stdout,
          "stage 3 falls back to SRAM + 0 when the offset doesn't fit", r.stdout)
    check(a == [CFG] + [SRAM + 4 * i for i in range(16)], "and reads only the SRAM's start", a[:3])
    mem.word(CFG, 3)
    r, a = run(3, os.path.join(tmp, "o4"))
    check(r.returncode == 0 and "doesn't fit" in r.stdout, "an unaligned offset falls back too", r.stdout)
    mem.word(CFG, 0xffffffff)
    r, a = run(3, os.path.join(tmp, "o4"))
    check("doesn't fit" in r.stdout and sram_only(a[1:]), "all ones in the offset register falls back too", r.stdout)

    # Stops, and no dump, when there's no table.
    mem = Mem(fake)
    out5 = fresh("o5")
    r, a = run(3, out5)
    check(r.returncode == 3 and "no table" in r.stdout, "all zeros aren't a table", r.stdout)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(3)])
    r, a = run(3, out5)
    check(r.returncode == 3 and "3 of 16" in r.stdout, "three entries aren't enough for a table", r.stdout)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(4)])
    r, a = run(3, out5)
    check(r.returncode == 0 and "4 of 16" in r.stdout, "four entries are", r.stdout)
    mem = Mem(fake)
    table(mem, SRAM, [SRAM + 0x100 * (i + 1) for i in range(3)])
    r, a = run(5, out5)
    check(r.returncode == 3 and not os.listdir(out5), "stage 5 refuses without a table", r.stdout)
    check(a == [CFG] + [SRAM + 4 * i for i in range(16)], "and reads only the table", a[:3])
    table(mem, SRAM, [SRAM + SRAM_LEN + 0x100 * i for i in range(12)])
    r, a = run(3, out5)
    check(r.returncode == 3 and "0 of 16" in r.stdout, "entries past the SRAM's end don't count", r.stdout)
    table(mem, SRAM, [SRAM - 0x10 - 0x100 * i for i in range(12)])
    r, a = run(3, out5)
    check(r.returncode == 3 and "0 of 16" in r.stdout, "entries before the SRAM's start don't count", r.stdout)
    mem = Mem(fake)
    mem.word(SRAM, 0xffffffff)
    r, a = run(2, out5)
    check(r.returncode == 3 and "all ones" in r.stdout, "stage 2 stops at all ones", r.stdout)

    # Table entries that point anywhere are never followed.
    for i, hostile in enumerate(([0xffffffff] * 16, [0x292f7000 + j for j in range(16)],
                                 [(SRAM + SRAM_LEN) >> 4] * 16, [0x10000000 + j * 0x100 for j in range(16)])):
        mem = Mem(fake)
        for j, w in enumerate(hostile):
            mem.word(SRAM + 4 * j, w)
        d = fresh("h%d" % i)
        run(3, d)
        r, a = run(4, d)
        check(sram_only(a), "hostile table %d: stage 4's reads stay in the SRAM" % i, a[:3])
        r, a = run(5, d)
        check(sram_only(a[1:]) and a[0] == CFG, "hostile table %d: stage 5's stay in the SRAM" % i, a[:3])

    # The position read's words: each stage reads exactly its own, in order.
    WORDS = {"a": [0x2922b002c, 0x2922b0020, 0x28e580188], "b": [0x2922b801c], "c": [0x2925c4004], "d": [0x28e20c400],
             "e": [0x2922c0040], "f": [0x2922c0044], "g": [0x2922c010c], "h": [0x2922c0110]}
    mem = Mem(fake)
    for i, pa in enumerate(sum(WORDS.values(), [])):
        mem.word(pa, 0xc0de0000 + i)
    out6 = fresh("o6")
    seen = 0
    for letter, pas in WORDS.items():
        r, a = run(letter, out6)
        check(r.returncode == 0 and a == pas, f"stage {letter} reads exactly its words", str([hex(x) for x in a]))
        for pa in pas:
            line = "0x%x = 0x%08x" % (pa, 0xc0de0000 + seen)
            seen += 1
            check(line in r.stdout and r.stdout.index("reading 0x%x" % pa) < r.stdout.index(line),
                  f"stage {letter} says what it's reading before each read, and prints 0x{pa:x}'s word", r.stdout)
    check(not os.listdir(out6), "the position stages write no dump", os.listdir(out6))
    # None of them is the never-write list's, nor any byte of it.
    check(not [pa for pa in sum(WORDS.values(), []) if 0x28e0802d8 <= pa < 0x28e0802e0 or 0x28e700000 <= pa < 0x28e700008],
          "no position word is on the never-write list")

    # Stage 6: the ten words that say where A2's WD2 bite is, read in order and never followed, and its verdict.
    # Built here from the format and WD2's register map, as recfg_bite.h builds them: nothing is copied from the dump.
    HEAD = ((0x2922b0000 >> 10) << 6) | (4 - 1) << 2 | 1
    OFFS = int.from_bytes(bytes(reg >> 2 for reg in (0x2c, 0x20, 0x24, 0x2c)), "little")
    TEN = [CFG, LOCK, SRAM + 0x1010, SRAM + 0x1014] + [SRAM + 0x2000 + 4 * i for i in range(6)]
    GOOD = [0x1000, 0, (SRAM + 0x2000) >> 4, 0, HEAD, OFFS, 0, 0, 0x2aea5400, 4]

    def ten(values):
        m = Mem(fake)
        for pa, v in zip(TEN, values):
            m.word(pa, v)

    out7 = fresh("o7")
    ten(GOOD)
    r, a = run(6, out7)
    check(r.returncode == 0 and a == TEN, "stage 6 reads exactly the ten words, in order", str([hex(x) for x in a]))
    check(r.stdout.rstrip().endswith("A2's WD2 bite word, 0x292f02010: the old value, 30 s"), "stage 6's last line is the verdict: the old bite", r.stdout)
    check(all("0x%x = 0x%08x" % (pa, v) in r.stdout and r.stdout.index("reading 0x%x\n" % pa) < r.stdout.index("0x%x = 0x%08x" % (pa, v))
              for pa, v in zip(TEN, GOOD)), "stage 6 says what it's reading before each read, and prints each word", r.stdout)
    ten(GOOD[:8] + [0x405f7e00, 4])
    r, a = run(6, out7)
    check(r.returncode == 0 and a == TEN and r.stdout.rstrip().endswith("0x292f02010: the new value, 45 s"), "stage 6 says when the bite is the new one", r.stdout)
    for i, text in ((0, "the table offset register's analogue doesn't read 0x1000"), (1, "the lock's analogue reads non-zero"),
                    (2, "table A's entry 2 doesn't point at the SRAM + 0x2000"), (3, "table A's entry 2 doesn't point"),
                    (4, "A2 doesn't open with the op that arms WD2"), (5, "A2 doesn't open"), (6, "A2 doesn't open"), (7, "A2 doesn't open"),
                    (8, "neither the old value nor the new one"), (9, "A2 doesn't open")):
        values = list(GOOD)
        values[i] ^= 0x10
        ten(values)
        r, a = run(6, out7)
        check(r.returncode == 3 and a == TEN and text in r.stdout.splitlines()[-1],
              f"stage 6 stops (exit 3) when word {i} isn't as expected, having read the same ten and no more", r.stdout)
    ten([0xffffffff] * 10)
    r, a = run(6, out7)
    check(r.returncode == 3 and a == TEN, "stage 6 on all ones: the same ten reads, and it stops", r.stdout)
    check(not os.listdir(out7), "stage 6 writes no dump", os.listdir(out7))
    # Each "reading" line is out before its read is made: with the trace on stdout too, the order shows.
    inline = os.path.join(tmp, "recfg_peek_inline")
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", '-DRECFG_MEM="%s"' % fake, '-DRECFG_TRACE="/dev/stdout"', "-o", inline,
                    os.path.join(E1_HOOK, "recfg_peek.c")], check=True)
    ten(GOOD)
    seen = subprocess.run([inline, "6", out7], capture_output=True, text=True).stdout
    check(all(seen.index("reading 0x%x\n" % pa) < seen.index("\n0x%x\n" % pa) < seen.index("0x%x = 0x%08x" % (pa, v)) for pa, v in zip(TEN, GOOD)),
          "stage 6 prints each \"reading\" line before it makes that read, and the word after", seen)
    check(not [pa for pa in TEN if 0x28e0802d8 <= pa < 0x28e0802e0 or 0x28e700000 <= pa < 0x28e700008],
          "none of stage 6's words is on the never-write list")

    # Stage 7: the bite word alone, for the regular boot after the run: one read, and which value.
    BITE = SRAM + 0x2010
    for value, rc_, text in ((0x2aea5400, 0, "the old value, 30 s"), (0x405f7e00, 0, "the new value, 45 s"), (0x405f7e01, 3, "neither the old value nor the new one"),
                             (0, 3, "neither"), (0xffffffff, 3, "neither")):
        ten(GOOD[:8] + [value, 4])
        r, a = run(7, out7)
        check(r.returncode == rc_ and a == [BITE] and r.stdout.rstrip().endswith("0x292f02010 = 0x%08x  A2's WD2 bite word: %s" % (value, text)
              if text != "neither" else "neither the old value nor the new one"),
              f"stage 7 reads the one word and nothing else, and says {value:#x} is {text} (exit {rc_})", r.stdout + str([hex(x) for x in a]))
    ten([0xffffffff] * 8 + [0x2aea5400, 0xffffffff])
    r, a = run(7, out7)
    check(r.returncode == 0 and a == [BITE], "stage 7 goes by that word whatever the other nine read: it never reads them", r.stdout)
    seen = subprocess.run([inline, "7", out7], capture_output=True, text=True).stdout
    check(seen.index("reading 0x292f02010\n") < seen.index("\n0x292f02010\n") < seen.index("0x292f02010 = 0x"), "stage 7 prints its \"reading\" line before the read", seen)
    check(not os.listdir(out7), "stage 7 writes no dump", os.listdir(out7))

    # Started with stdout closed, the device would become this program's stdout: it stops before any read.
    ten(GOOD)
    if os.path.exists(trace):
        os.unlink(trace)
    r = subprocess.run([binary, "6", out7], stderr=subprocess.PIPE, preexec_fn=lambda: os.close(1))
    check(r.returncode == 1 and not os.path.exists(trace), "with stdout closed, the device isn't used as stdout: exit 1 before any read", r.stderr)

    # Its arguments: no address can be asked for.
    for args in (["0", tmp], ["8", tmp], ["12", tmp], ["x", tmp], ["i", tmp], ["A", tmp], ["ab", tmp], ["1"],
                 ["0x2925c0200", tmp], []):
        r = subprocess.run([binary] + args, capture_output=True, text=True)
        check(r.returncode == 2, "refuses arguments " + repr(args), r.stdout + r.stderr)

    # The launcher, as uid 0 in a user namespace.
    if shutil.which("bwrap") is None:
        print("FAIL bwrap is missing")
        failed += 1
    else:
        launcher = os.path.join(E1_HOOK, "recfg-peek")

        def manifest(root):
            """Every file under root, with its hash."""
            res = {}
            for d, _, fs in os.walk(root):
                for f in fs:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        res[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
            return res

        def sandbox(*, safe=True, rc_by_stage=None, save_fails=False, uid=0, args=(), old_binary=False,
                    tee_fails=False, pdone=False, sync_fails=False, save_fail_at=None, pa=()):
            """Runs the launcher with stubs; returns (rc, output, calls, the log root, manifests at each save)."""
            rc_by_stage = rc_by_stage or {}
            root = tempfile.mkdtemp(dir=tmp)
            calls = os.path.join(root, "calls")
            stub = os.path.join(root, "recfg_peek")
            run_dir = os.path.join(root, "run")
            os.mkdir(run_dir)
            rcs = " ".join(f"[{k}]={v}" for k, v in rc_by_stage.items())
            with open(stub, "w") as f:
                f.write(f"""#!/bin/bash
declare -A rc=({rcs})
echo "stage $1" >> {calls}
echo "the reader's stage $1"
[[ -d $2 ]] || {{ echo 'no dir' >&2; exit 9; }}
[[ $1 == 4 ]] && echo dump > "$2/sram-00000.bin"
if [[ ${{rc[$1]:-0}} == segv ]]; then kill -SEGV $$; fi
exit ${{rc[$1]:-0}}
""")
            os.chmod(stub, 0o755)
            src = os.path.join(root, "recfg_peek.c")
            open(src, "w").close()
            if old_binary:
                os.utime(stub, (1, 1))
            else:
                os.utime(src, (1, 1))
            log_root = os.path.join(root, "log")
            os.mkdir(log_root)
            if pdone:
                os.mkdir(os.path.join(log_root, "recfg-peek-20261109T090000-pa"))
                os.mkdir(os.path.join(log_root, "recfg-peek-20261109T090000-pdone"))
                with open(os.path.join(log_root, "recfg-peek-20261109T090000-pdone", "done.txt"), "w") as f:
                    f.write("every word\n")
            for when in pa:
                # Another pass's first stage, with no pdone: a pass that stopped partway.
                os.mkdir(os.path.join(log_root, f"recfg-peek-{when}-pa"))
            snaps = os.path.join(root, "snaps")
            os.mkdir(snaps)
            save = os.path.join(root, "safe")
            with open(save, "w") as f:
                f.write(f"""#!/bin/bash
echo "save $*" >> {calls}
n=$(ls {snaps} | wc -l)
cp -a {log_root} {snaps}/$n
{'exit 1' if save_fails else f'[[ $n != {save_fail_at - 1} ]] || exit 1' if save_fail_at else 'exit 0'}
""")
            os.chmod(save, 0o755)
            flag = os.path.join(root, "active")
            if safe:
                open(flag, "w").close()
            env = ["env", "RECFG_BIN=" + stub, "RECFG_SRC=" + src, "RECFG_SAFE_FLAG=" + flag,
                   "RECFG_SAVE=" + save, "RECFG_LOG_ROOT=" + log_root, "RECFG_RUNDIR=" + run_dir]
            if tee_fails or sync_fails:
                bindir = os.path.join(root, "bin")
                os.mkdir(bindir)
                if tee_fails:
                    # A tee that can't write, as on a full overlay.
                    teep = os.path.join(bindir, "tee")
                    with open(teep, "w") as f:
                        f.write("#!/bin/bash\ncat > /dev/null\nexit 1\n")
                    os.chmod(teep, 0o755)
                if sync_fails:
                    # A syncfs that fails, as on a disk error.
                    syncp = os.path.join(bindir, "sync")
                    with open(syncp, "w") as f:
                        f.write("#!/bin/bash\nexit 1\n")
                    os.chmod(syncp, 0o755)
                env.append("PATH=" + bindir + ":" + os.environ["PATH"])
            r = subprocess.run(
                ["bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                 "--unshare-user", "--uid", str(uid), "--gid", "0", "--"] + env + [launcher] + list(args),
                capture_output=True, text=True)
            lines = open(calls).read().split("\n")[:-1] if os.path.exists(calls) else []
            manifests = [manifest(os.path.join(snaps, n)) for n in sorted(os.listdir(snaps), key=int)]
            sandbox.mark = os.path.exists(os.path.join(run_dir, "sleep-drain-position-awake"))
            return r.returncode, r.stdout + r.stderr, lines, log_root, manifests

        DEFAULT = ["stage 1", "save save", "stage 2", "save save", "stage 3", "save save", "stage 4", "save save"]

        rc, out, calls, logs, snaps = sandbox()
        check(rc == 0 and calls == DEFAULT, "the launcher runs stages 1-4 in order, saving after each", calls)
        check("done" in out, "and says when it's done", out)
        dirs = sorted(os.listdir(logs))
        check([d.rsplit("-", 1)[1] for d in dirs] == ["s1", "s2", "s3", "s4"], "each stage has its own directory", dirs)
        text = open(os.path.join(logs, dirs[2], "log.txt")).read()
        check("the reader's stage 3" in text and "== stage 3" in text, "it logs each stage in its own directory", text)
        check(os.path.exists(os.path.join(logs, dirs[3], "sram-00000.bin")), "stage 4's dump lands in stage 4's directory")
        check(all(snaps[i].items() <= snaps[i + 1].items() for i in range(len(snaps) - 1)),
              "no file changes after the save that first saw it (what collect needs)",
              [sorted(s) for s in snaps])

        rc, out, calls, logs, snaps = sandbox(args=("--rest",))
        check(rc == 0 and calls == ["stage 5", "save save"], "--rest runs stage 5 alone, saved", calls)
        check([d.rsplit("-", 1)[1] for d in os.listdir(logs)] == ["s5"], "--rest has its own directory", os.listdir(logs))
        rc, out, calls, logs, snaps = sandbox(args=("--more",))
        check(rc == 1 and "usage" in out and calls == [], "refuses an unknown argument", out)

        # --position, in a safe boot: each stage saved before the next, then the record and the mark.
        LETTERS = "abcdefgh"
        POS = [x for c in LETTERS for x in (f"stage {c}", "save save")] + ["save save"]
        rc, out, calls, logs, snaps = sandbox(args=("--position",))
        dirs = sorted(os.listdir(logs))
        check(rc == 0 and calls == POS and sandbox.mark, "--position runs stages a-h, saving after each, then the record, then the mark", (calls, out))
        check(sorted(d.rsplit("-", 1)[1] for d in dirs) == sorted([f"p{c}" for c in LETTERS] + ["pdone"]), "each word has its own directory, and there's a pdone", dirs)
        check(all(snaps[i].items() <= snaps[i + 1].items() for i in range(len(snaps) - 1)),
              "--position: no file changes after the save that first saw it", [sorted(s) for s in snaps])
        check(not any("pdone" in f for s in snaps[:-1] for f in s) and any("done.txt" in f for f in snaps[-1]),
              "done.txt appears only in the last save", [sorted(s) for s in snaps])
        rc, out, calls, logs, snaps = sandbox(args=("--position",), rc_by_stage={"e": 1})
        check(rc == 1 and not sandbox.mark and calls == POS[:8] + ["stage e", "save save"][:1] + ["save save"]
              and "stage e failed" in out and not [d for d in os.listdir(logs) if d.endswith("pdone")],
              "a failed word stops it, with no record and no mark", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position",), rc_by_stage={"h": "segv"})
        check(rc == 1 and not sandbox.mark and not [d for d in os.listdir(logs) if d.endswith("pdone")],
              "a reader killed on the last word: no record and no mark", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position",), save_fails=True)
        check(rc == 1 and not sandbox.mark and calls == ["stage a", "save save"], "a failed save stops --position, with no mark", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position",), save_fail_at=9)
        check(rc == 1 and not sandbox.mark and calls == POS and "run-floor --m1-position will refuse" in out,
              "a failed save of the pass's record leaves no mark", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position",), sync_fails=True)
        check(rc == 0 and calls == POS and sandbox.mark, "--position goes by its saves, not its syncs", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position",), safe=False)
        check(rc == 1 and "safe boot only" in out and calls == [] and not sandbox.mark, "--position refuses outside a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(args=("--position",), uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "--position refuses to run as non-root", out)

        # --position-after, the one read in a regular boot: only with a collected done.txt.
        AFTER = [f"stage {c}" for c in LETTERS]
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True)
        check(rc == 0 and calls == AFTER and not sandbox.mark and snaps == [], "--position-after reads a-h, with no save and no mark", (calls, out))
        dirs = sorted(d for d in os.listdir(logs) if not d.endswith(("pdone", "-pa")))
        check(len(dirs) == 1 and dirs[0].endswith("-after"), "--position-after writes one directory", dirs)
        text = open(os.path.join(logs, dirs[0], "log.txt")).read()
        check(all(f"the reader's stage {c}" in text for c in LETTERS), "and its log holds every stage", text)
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False)
        check(rc == 1 and "no safe boot's --position is on record" in out and calls == [], "--position-after refuses without a collected done.txt", out)
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=True, pdone=True)
        check(rc == 1 and "regular boot" in out and calls == [], "--position-after refuses in a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True, uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "--position-after refuses to run as non-root", out)
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True, rc_by_stage={"f": 1})
        check(rc == 1 and calls == AFTER[:6] and "stage f failed" in out, "--position-after stops at a failed word", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True, tee_fails=True)
        check(rc == 1 and calls == ["stage a"] and "couldn't write" in out, "--position-after stops if its log can't be written", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True, sync_fails=True)
        check(rc == 1 and calls == ["stage a"] and "not reading further" in out, "--position-after stops at a failed sync, before the next read", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True, pa=("20261109T100000",))
        check(rc == 1 and "never finished" in out and "recfg-peek-20261109T100000-pa" in out and calls == [],
              "--position-after refuses when the newest pass never finished, whatever an older one's done.txt says", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pdone=True, pa=("20261109T080000",))
        check(rc == 0 and calls == AFTER, "--position-after goes when the newest pass finished, whatever an older one did", (calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--position-after",), safe=False, pa=("20261109T080000",))
        check(rc == 1 and "never finished" in out and calls == [], "--position-after refuses a pass with no done.txt", (calls, out))

        rc, out, calls, logs, snaps = sandbox(safe=False)
        check(rc == 1 and "safe boot only" in out and calls == [], "refuses outside a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "refuses to run as non-root", out)
        rc, out, calls, logs, snaps = sandbox(old_binary=True)
        check(rc == 1 and "older than" in out and calls == [], "refuses a binary older than the source", out)
        rc, out, calls, logs, snaps = sandbox(rc_by_stage={3: 3})
        check(rc == 0 and calls == DEFAULT[:6] and "stopped at stage 3" in out, "a stage that says stop ends it, saved", calls)
        rc, out, calls, logs, snaps = sandbox(rc_by_stage={2: 3})
        check(calls == DEFAULT[:4], "stops at stage 2's all ones, saved", calls)
        rc, out, calls, logs, snaps = sandbox(rc_by_stage={2: 1})
        check(rc == 1 and calls == DEFAULT[:3] + ["save save"] and "stage 2 failed" in out,
              "a stage's error stops it, after one more save", calls)
        faillog = open(os.path.join(logs, sorted(os.listdir(logs))[1], "log.txt")).read()
        check("failed (exit 1)" in faillog, "and the log says so", faillog)
        rc, out, calls, logs, snaps = sandbox(rc_by_stage={2: "segv"})
        check(rc == 1 and calls == DEFAULT[:3] + ["save save"] and "exit 139" in out,
              "a reader killed by a signal counts as an error, saved", (calls, out))
        rc, out, calls, logs, snaps = sandbox(tee_fails=True)
        check(rc == 1 and calls == ["stage 1", "save save"] and "couldn't write" in out,
              "a log that can't be written stops it, after one more save", (calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fails=True)
        check(rc == 1 and calls == ["stage 1", "save save"] and "not reading further" in out,
              "a failed save stops it before the next read", calls)

print("\n%d failures" % failed)
sys.exit(1 if failed else 0)
