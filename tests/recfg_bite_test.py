#!/usr/bin/env python3
"""e1-hook/recfg_poke, the one write to the reconfig SRAM, and e1-hook/recfg-bite's
order, without hardware.

The writer is compiled with its memory device pointed at a sparse file whose
offsets are physical addresses, its safe-boot flag and kernel command line at
files here, and with -DRECFG_TRACE, so that every access is logged: each case
checks what it read, that it stored at most to the one address, and the file's
whole content after. More builds drop or change its stores, as an SRAM that's
locked or takes another value would, or disturb the next word. The launcher
runs as uid 0 in a bubblewrap user namespace with stub tools and a stub
sleep-drain-safe that record what they're asked and what the log directory held
at each save, and then with the real reader and writer on the fake device.

  python3 -I tests/recfg_bite_test.py
"""
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))

CFG, LOCK, SRAM, SRAM_LEN = 0x2925c0200, 0x2925c0214, 0x292f00000, 0x70000
BITE = SRAM + 0x2010
OLD, NEW = 0x2aea5400, 0x405f7e00
# The ten words, built from the format and WD2's register map as recfg_bite.h builds them: nothing is copied from the dump.
HEAD = ((0x2922b0000 >> 10) << 6) | (4 - 1) << 2 | 1
OFFS = int.from_bytes(bytes(reg >> 2 for reg in (0x2c, 0x20, 0x24, 0x2c)), "little")
TEN = [CFG, LOCK, SRAM + 0x1010, SRAM + 0x1014] + [SRAM + 0x2000 + 4 * i for i in range(6)]
GOOD = [0x1000, 0, (SRAM + 0x2000) >> 4, 0, HEAD, OFFS, 0, 0, OLD, 4]

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("     " + str(detail))


def content(path):
    """Every non-zero word of a sparse file, by offset: its whole content, cheaply."""
    res = {}
    with open(path, "rb") as f:
        fd = f.fileno()
        pos, end = 0, os.fstat(fd).st_size
        while pos < end:
            try:
                start = os.lseek(fd, pos, os.SEEK_DATA)
            except OSError:
                break
            stop = os.lseek(fd, start, os.SEEK_HOLE)
            raw = os.pread(fd, stop - start, start)
            for i in range(0, len(raw) - 3, 4):
                w = struct.unpack_from("<I", raw, i)[0]
                if w:
                    res[start + i] = w
            pos = stop
    return res


with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
    fake = os.path.join(tmp, "mem")
    flag = os.path.join(tmp, "active")
    cmdline = os.path.join(tmp, "cmdline")
    trace = os.path.join(tmp, "trace")
    src_path = os.path.join(E1_HOOK, "recfg_poke.c")
    SAFE_CMDLINE = "root=/dev/mapper/root rw quiet sleepdrain.safe\n"

    def build(name, *extra, trace_to=trace):
        exe = os.path.join(tmp, name)
        # _FORTIFY_SOURCE as some distributions' compilers set it by default: glibc's warn_unused_result is then an error here too.
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=3", '-DRECFG_MEM="%s"' % fake,
                        '-DRECFG_SAFE_FLAG="%s"' % flag, '-DRECFG_CMDLINE="%s"' % cmdline, '-DRECFG_TRACE="%s"' % trace_to, *extra, "-o", exe, src_path],
                       check=True)
        return exe

    poke = build("recfg_poke")
    dropped = build("recfg_poke_drop", "-DRECFG_POKE_DROP(n)=1")
    garbled_once = build("recfg_poke_once", "-DRECFG_POKE_STORE(v,n)=((n)==0?(v)^0xffffU:(v))")
    garbled = build("recfg_poke_garble", "-DRECFG_POKE_STORE(v,n)=((v)^0xffffU)")
    # A store that takes, and clears the next word with it: the value A2 gives WD2's control.
    neighbour = build("recfg_poke_neighbour", "-DRECFG_POKE_STORE(v,n)=((n)==0?(*(volatile uint32_t*)((volatile char*)p+4)=0,(v)):(v))")

    def mem(values, extra=()):
        """A fresh device: the ten words, and other words that must not change."""
        with open(fake, "wb") as f:
            f.truncate(SRAM + SRAM_LEN)
        with open(fake, "r+b") as f:
            for pa, v in list(zip(TEN, values)) + list(extra):
                f.seek(pa)
                f.write(struct.pack("<I", v))
        return content(fake)

    def run(exe, *args):
        """Runs it; returns (result, its accesses in order as (op, pa, value))."""
        if os.path.exists(trace):
            os.unlink(trace)
        r = subprocess.run([exe, *args], capture_output=True, text=True)
        acc = []
        if os.path.exists(trace):
            with open(trace) as f:
                acc = [(op, int(pa, 16), int(v, 16)) for op, pa, v in (line.split() for line in f.read().splitlines())]
        return r, acc

    # Around the word: the other three A sequences' bites and A2's neighbours must be left alone.
    NEIGHBOURS = [(SRAM + 0x1a10, OLD), (SRAM + 0x1cf0, OLD), (SRAM + 0x21b0, OLD), (SRAM + 0x2018, 0x11111111), (SRAM + 0x201c, 0x22222222),
                  (SRAM + 0x1ffc, 0x33333333), (SRAM, 0x44444444), (SRAM + SRAM_LEN - 4, 0x55555555)]

    def shape(acc):
        """The accesses as a string: T for a pass over the ten words, in order; w(value) for a store to the bite word; ? for anything else."""
        out, i = [], 0
        while i < len(acc):
            if [(op, pa) for op, pa, _ in acc[i:i + 10]] == [("r", pa) for pa in TEN]:
                out.append("T")
                i += 10
            elif acc[i][0] == "w" and acc[i][1] == BITE:
                out.append("w(%s)" % {NEW: "new", OLD: "old"}.get(acc[i][2], hex(acc[i][2])))
                i += 1
            else:
                out.append("?")
                i += 1
        return " ".join(out)

    open(flag, "w").close()
    with open(cmdline, "w") as f:
        f.write(SAFE_CMDLINE)
    before = mem(GOOD, NEIGHBOURS)
    r, acc = run(poke, "patch")
    after = content(fake)
    check(r.returncode == 0 and "the word took the write: it reads the new value, 45 s, and the other nine read as before" in r.stdout,
          "patch: exit 0, and it says the word took the write", r.stdout + r.stderr)
    check(shape(acc) == "T w(new) T", "patch reads the ten words in order, stores once, to 0x292f02010, and reads all ten again", acc)
    check(after == {**before, BITE: NEW} and before[BITE] == OLD, "patch changes that one word in the whole device, and nothing else",
          {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)})
    out = r.stdout
    check(all(out.index("reading 0x%x\n" % pa) < out.index("0x%x = 0x%08x" % (pa, v)) for pa, v in zip(TEN, GOOD))
          and out.index("A2's WD2 bite word, 0x292f02010: the old value, 30 s") < out.index("writing 0x405f7e00 to 0x292f02010\n")
          < out.index("the ten words again, after the write:\n") < out.index("0x292f02010 = 0x405f7e00")
          < out.index("A2's WD2 bite word, 0x292f02010: the new value, 45 s"),
          "patch says each read before it, the verdict, then the write before it, then the ten again and their verdict", out)

    r, acc = run(poke, "patch")
    check(r.returncode == 0 and "already the new value: nothing written" in r.stdout and shape(acc) == "T" and content(fake) == after,
          "patch on a word that's already new: exit 0, no store", r.stdout + str(acc))
    # Each line is out before its access is made: with the trace on stdout too, the order shows.
    inline = build("recfg_poke_inline", trace_to="/dev/stdout")
    mem(GOOD, NEIGHBOURS)
    seen = subprocess.run([inline, "patch"], capture_output=True, text=True).stdout
    check(all(seen.index("reading 0x%x\n" % pa) < seen.index("r 0x%x " % pa) for pa in TEN)
          and seen.index("writing 0x405f7e00 to 0x292f02010\n") < seen.index("w 0x292f02010 0x405f7e00\n") < seen.rindex("reading 0x292f02010\n")
          < seen.rindex("r 0x292f02010 0x405f7e00\n"),
          "patch prints each \"reading\" line before that read and the \"writing\" line before the store", seen)
    mem(GOOD[:8] + [NEW, 4], NEIGHBOURS)
    r, acc = run(poke, "restore")
    check(r.returncode == 0 and "the word reads the old value, 30 s, again" in r.stdout and shape(acc) == "T w(old) T" and content(fake) == before,
          "restore: one store of the old value to the same word, the ten read again, and the device is as it began", r.stdout + str(acc))
    r, acc = run(poke, "restore")
    check(r.returncode == 0 and "already the old value: nothing written" in r.stdout and shape(acc) == "T" and content(fake) == before,
          "restore on a word that's already old: exit 0, no store", r.stdout + str(acc))

    # Anything not as expected: nothing is written, by either. restore writes only over patch's own value.
    for i in range(10):
        for flip in (0x10, 0x80000000):
            values = list(GOOD)
            values[i] ^= flip
            start = mem(values, NEIGHBOURS)
            for mode in ("patch", "restore"):
                r, acc = run(poke, mode)
                check(r.returncode == 3 and "nothing written" in r.stdout and shape(acc) == "T" and content(fake) == start,
                      f"{mode} refuses (exit 3) with word {i} off by {flip:#x}: ten reads, no store", r.stdout + str(acc))
            if i != 8:
                values[8] = NEW
                start = mem(values, NEIGHBOURS)
                for mode in ("patch", "restore"):
                    r, acc = run(poke, mode)
                    check(r.returncode == 3 and shape(acc) == "T" and content(fake) == start,
                          f"{mode} refuses the same with the new bite there", r.stdout + str(acc))
    start = mem([0xffffffff] * 10)
    for mode in ("patch", "restore"):
        r, acc = run(poke, mode)
        check(r.returncode == 3 and shape(acc) == "T" and content(fake) == start, f"{mode} on all ones: no store", r.stdout + str(acc))

    # An SRAM that drops the store (locked), takes another value, or changes a neighbour with it.
    start = mem(GOOD, NEIGHBOURS)
    r, acc = run(dropped, "patch")
    check(r.returncode == 5 and "THE WRITE DIDN'T TAKE" in r.stdout and shape(acc) == "T w(new) T" and content(fake) == start,
          "a store that's dropped: exit 5, said so, one try, nothing changed", r.stdout + str(acc))
    start = mem(GOOD[:8] + [NEW, 4], NEIGHBOURS)
    r, acc = run(dropped, "restore")
    check(r.returncode == 5 and "THE OLD VALUE DIDN'T GO BACK" in r.stdout and shape(acc) == "T w(old) T" and content(fake) == start,
          "a restore that's dropped: exit 5, said so", r.stdout + str(acc))
    start = mem(GOOD, NEIGHBOURS)
    r, acc = run(garbled_once, "patch")
    check(r.returncode == 5 and "THE TEN WORDS READ NEITHER AS BEFORE NOR AS INTENDED" in r.stdout and "the ten words read as they did before the write" in r.stdout
          and shape(acc) == "T w(new) T w(old) T" and content(fake) == start,
          "a store that lands as another value: exit 5, and the old value is put back, once", r.stdout + str(acc))
    r, acc = run(garbled, "patch")
    check(r.returncode == 5 and "THE TEN WORDS STILL DON'T READ AS THEY DID BEFORE THE WRITE" in r.stdout
          and shape(acc) == "T w(new) T w(old) T" and content(fake) == {**start, BITE: OLD ^ 0xffff},
          "a store that always lands as another value: exit 5, two stores and no more, only that word touched", r.stdout + str(acc))
    start = mem(GOOD, NEIGHBOURS)
    r, acc = run(neighbour, "patch")
    left = dict(start)
    del left[SRAM + 0x2014]
    check(r.returncode == 5 and "the word took the write" not in r.stdout and "THE TEN WORDS READ NEITHER AS BEFORE NOR AS INTENDED" in r.stdout
          and "THE TEN WORDS STILL DON'T READ AS THEY DID BEFORE THE WRITE" in r.stdout and shape(acc) == "T w(new) T w(old) T" and content(fake) == left,
          "a store that takes but changes the next word: caught by the ten read again (exit 5), the bite put back, and no store to the neighbour",
          r.stdout + str(acc))

    # The same for each of the four words outside A2: all ten decide after the write, not A2's six alone.
    for i, name in ((0, "the offset register"), (1, "its lock"), (2, "table A's entry 2"), (3, "the entry's second word")):
        # The injected write's result is checked: glibc marks pwrite warn_unused_result under _FORTIFY_SOURCE, an error with -Werror.
        exe = build("recfg_poke_w%d" % i, '-DRECFG_POKE_STORE(v,n)=((n)==0&&pwrite(mem_fd,"\\x01\\x00\\x00\\x10",4,%dL)!=4?(exit(9),(v)):(v))' % TEN[i])
        start = mem(GOOD, NEIGHBOURS)
        r, acc = run(exe, "patch")
        check(r.returncode == 5 and "the word took the write" not in r.stdout and "THE TEN WORDS READ NEITHER AS BEFORE NOR AS INTENDED" in r.stdout
              and shape(acc) == "T w(new) T w(old) T" and content(fake) == {**start, TEN[i]: 0x10000001},
              f"a store that takes and changes {name}: caught by the ten read again (exit 5), the bite put back", r.stdout + str(acc))
    # restore's own read-back: the ten must read as before patch, not merely the bite no longer new.
    for exe, what, left in ((garbled, "lands as another value", {BITE: OLD ^ 0xffff}), (neighbour, "changes the next word", {SRAM + 0x2014: None})):
        start = mem(GOOD[:8] + [NEW, 4], NEIGHBOURS)
        r, acc = run(exe, "restore")
        want = {k: v for k, v in {**start, BITE: OLD, **left}.items() if v is not None}
        check(r.returncode == 5 and "THE OLD VALUE DIDN'T GO BACK" in r.stdout and "the word reads the old value, 30 s, again" not in r.stdout
              and shape(acc) == "T w(old) T" and content(fake) == want,
              f"a restore whose store {what}: exit 5, said so, one store", r.stdout + str(acc))

    # Outside a safe boot, and its arguments: nothing is even read.
    start = mem(GOOD, NEIGHBOURS)
    os.unlink(flag)
    for mode in ("patch", "restore"):
        r, acc = run(poke, mode)
        check(r.returncode == 2 and "safe boot only" in r.stderr and acc == [] and content(fake) == start, f"{mode} refuses without the safe boot's flag, reading nothing", r.stderr)
    open(flag, "w").close()
    for text in ("root=/dev/mapper/root rw quiet\n", "root=/dev/mapper/root nosleepdrain.safe rw\n", "sleepdrain.safer\n", "sleepdrain.safe=0\n", ""):
        with open(cmdline, "w") as f:
            f.write(text)
        r, acc = run(poke, "patch")
        check(r.returncode == 2 and "safe boot only" in r.stderr and acc == [] and content(fake) == start,
              f"patch refuses with the flag but a command line of {text!r}, reading nothing", r.stderr)
    os.unlink(cmdline)
    r, acc = run(poke, "patch")
    check(r.returncode == 2 and acc == [] and content(fake) == start, "patch refuses when the command line can't be read", r.stderr)
    for text in ("sleepdrain.safe\n", "sleepdrain.safe", "a  sleepdrain.safe  b\n"):
        with open(cmdline, "w") as f:
            f.write(text)
        mem(GOOD, NEIGHBOURS)
        r, acc = run(poke, "patch")
        check(r.returncode == 0 and shape(acc) == "T w(new) T", f"patch goes with the flag and a command line of {text!r}", r.stdout + r.stderr)
    # A command line as long as this Mac's safe boot's, and as long as the kernel allows (COMMAND_LINE_SIZE is 2048), the word last.
    for n in (238, 2047):
        with open(cmdline, "w") as f:
            f.write("x" * (n - 16) + " sleepdrain.safe\n")
        mem(GOOD, NEIGHBOURS)
        r, acc = run(poke, "patch")
        check(r.returncode == 0 and shape(acc) == "T w(new) T", f"patch goes with a {n}-byte command line whose last word is sleepdrain.safe", r.stdout + r.stderr)
    with open(cmdline, "w") as f:
        f.write(SAFE_CMDLINE)
    start = mem(GOOD, NEIGHBOURS)
    # Started with a standard descriptor closed, the device would take it: with stdout or stderr, every line printed would be a write to it.
    for fd in (0, 1, 2):
        if os.path.exists(trace):
            os.unlink(trace)
        r = subprocess.run([poke, "patch"], stdout=subprocess.PIPE if fd == 2 else None, stderr=subprocess.PIPE if fd == 1 else None,
                           preexec_fn=lambda fd=fd: os.close(fd))
        check(r.returncode == 2 and not os.path.exists(trace) and content(fake) == start,
              f"with descriptor {fd} closed, the device isn't opened in its place: exit 2, nothing read or written")
    for args in ([], ["patch", "restore"], ["write"], ["patch", "0x292f02014"], ["0x292f02010"], ["patch", "0x405f7e00"], ["--patch"], ["restore", ""]):
        r, acc = run(poke, *args)
        check(r.returncode == 2 and acc == [] and content(fake) == start, "refuses arguments " + repr(args) + ", reading nothing", r.stdout + r.stderr)

    # The source: one store, one address, two values, no number taken from the command line or the environment.
    src = open(src_path).read()
    hdr = open(os.path.join(E1_HOOK, "recfg_bite.h")).read()
    check(src.count("wr32(") == 2 and "wr32(BITE_PA, value);" in src and src.count("str %w0") == 1 and src.count("*(volatile uint32_t *)p = v;") == 1
          and sorted(re.findall(r"\bstore\((\w+)\);", src)) == ["RECFG_BITE_NEW", "RECFG_BITE_OLD", "RECFG_BITE_OLD"]
          and "#define BITE_PA ((uint64_t)(RECFG_SRAM + RECFG_BITE))" in src
          and len(re.findall(r"(?<![a-z_])open\(", src)) == 1 and "open(RECFG_MEM, O_RDWR | O_SYNC)" in src
          and not any(w in src for w in ("strtoul", "strtol", "atoi", "sscanf", "argv[2]", "getenv", "environ")),
          "recfg_poke.c has one store, to BITE_PA, of one of the two values, one open of the device, and takes no number from its arguments or the environment")
    check('#define RECFG_MEM "/dev/mem"\n' in src and '#define RECFG_SAFE_FLAG "/run/sleep-drain-safe/active"\n' in src
          and '#define RECFG_CMDLINE "/proc/cmdline"\n' in src and "#define MAP_UNIT UINT64_C(0x4000)\n" in src and "sysconf" not in src,
          "recfg_poke.c's own memory device, safe-boot flag and command line, which every build here overrides, are the real ones; it maps by 16 KiB")
    check("#define RECFG_BITE_OLD\t\t0x2aea5400U" in hdr and "#define RECFG_BITE_NEW\t\t0x405f7e00U" in hdr and "#define RECFG_SRAM\t\t0x292f00000ULL" in hdr
          and "#define RECFG_A2\t\t0x2000U" in hdr and "#define RECFG_BITE\t\t(RECFG_A2 + 4 * 4)" in hdr,
          "recfg_bite.h: the word and the two values are the ones Corey OK'd on 2026-11-09")
    check(BITE & ~7 not in (0x28e0802d8, 0x28e700000), "the word isn't on the never-write list")

    # The launcher, as uid 0 in a user namespace.
    if shutil.which("bwrap") is None:
        print("FAIL bwrap is missing")
        failed += 1
    else:
        launcher = os.path.join(E1_HOOK, "recfg-bite")
        VERDICT = "A2's WD2 bite word, 0x292f02010: the old value, 30 s"
        TOOK = "the word took the write: it reads the new value, 45 s, and the other nine read as before"
        ALREADY = "already the new value: nothing written"
        MAY = "A2'S BITE WORD MAY READ 45 s NOW"

        def manifest(root):
            res = {}
            for d, _, fs in os.walk(root):
                for f in fs:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        res[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
            return res

        def sandbox(*, args=(), safe=True, uid=0, peek_rc=0, patch_rc=0, restore_rc=0, save_fail_at=None, tee_fails=False, sync_fails=False,
                    old=None, records=(), term_at=None, real=None, sysctl=True, date=None, sync_logs=False, waiting=False):
            """Runs the launcher with stubs; returns (rc, output, calls, the log root, manifests at each save).
            old: which built file is older than a source ("peek", "poke", "peek-header", "poke-header").
            records: directories on record from a safe boot, as (time, step, the log's text).
            tee_fails: True, or "write" for the write's log alone. term_at: a save's number: SIGTERM to the launcher there.
            real: (reader, writer) built for the fake device, in place of the stubs. sysctl: False for no kernel.panic to write.
            date: the time the launcher's date gives, for a run in the same second as one on record.
            sync_logs: each sync is listed in calls. waiting: a save is still on the boot partition, not collected.
            A stub that "dies of a signal" exits 139, which is all its caller can see of one, and leaves no core dump."""
            root = tempfile.mkdtemp(dir=tmp)
            calls = os.path.join(root, "calls")
            log_root = os.path.join(root, "log")
            snaps = os.path.join(root, "snaps")
            sysdir = os.path.join(root, "sysctl")
            os.mkdir(log_root)
            os.mkdir(snaps)
            if sysctl:
                os.mkdir(sysdir)
                with open(os.path.join(sysdir, "panic"), "w") as f:
                    f.write("0\n")
            peek_s, poke_s, save = (os.path.join(root, n) for n in ("recfg_peek", "recfg_poke", "safe"))
            with open(peek_s, "w") as f:
                f.write(f"""#!/bin/bash
echo "peek $1" >> {calls}
[[ -d $2 ]] || {{ echo 'no dir' >&2; exit 9; }}
echo "the reader's stage $1"
[[ {peek_rc} == 0 || {peek_rc} == 3 ]] && echo "{VERDICT}"
exit {peek_rc}
""")
            with open(poke_s, "w") as f:
                f.write(f"""#!/bin/bash
echo "poke $* panic=$(cat {sysdir}/panic 2>/dev/null)" >> {calls}
echo "the writer's $1"
case $1 in
patch) [[ {patch_rc} != 0 ]] || echo "{TOOK}"
  exit {patch_rc} ;;
restore) exit {restore_rc} ;;
esac
exit 9
""")
            with open(save, "w") as f:
                f.write(f"""#!/bin/bash
echo "save $*" >> {calls}
n=$(ls {snaps} | wc -l)
cp -a {log_root} {snaps}/$n
{f'[[ $n != {term_at - 1} ]] || {{ kill -TERM $PPID; sleep 0.3; }}' if term_at else ''}
{f'[[ $n != {save_fail_at - 1} ]] || exit 1' if save_fail_at else ''}
exit 0
""")
            srcs = {}
            for n in ("recfg_peek.c", "recfg_poke.c", "recfg_bite.h"):
                srcs[n] = os.path.join(root, n)
                open(srcs[n], "w").close()
                os.utime(srcs[n], (1000, 1000))
            for p in (peek_s, poke_s, save):
                os.chmod(p, 0o755)
            os.utime(peek_s, (2000, 2000))
            os.utime(poke_s, (2000, 2000))
            if real:
                peek_s, poke_s = real
            if old == "peek":
                os.utime(srcs["recfg_peek.c"], (3000, 3000))
            elif old == "poke":
                os.utime(srcs["recfg_poke.c"], (3000, 3000))
            elif old in ("peek-header", "poke-header"):
                os.utime(srcs["recfg_bite.h"], (3000, 3000))
                # Only the named tool is older than the header; the other is rebuilt.
                os.utime(poke_s if old == "peek-header" else peek_s, (4000, 4000))
            for when, step, text in records:
                d = os.path.join(log_root, f"recfg-bite-{when}-{step}")
                os.mkdir(d)
                with open(os.path.join(d, "log.txt"), "w") as f:
                    f.write(text)
            flag_s = os.path.join(root, "active")
            if safe:
                open(flag_s, "w").close()
            saved = os.path.join(root, "saved")
            if waiting:
                os.mkdir(saved)
                open(os.path.join(saved, "20261109T210500.tar"), "w").close()
            env = ["env", "BITE_PEEK=" + peek_s, "BITE_PEEK_SRC=" + srcs["recfg_peek.c"], "BITE_POKE=" + poke_s, "BITE_POKE_SRC=" + srcs["recfg_poke.c"],
                   "BITE_HEADER=" + srcs["recfg_bite.h"], "BITE_SAFE_FLAG=" + flag_s, "BITE_SAVE=" + save, "BITE_LOG_ROOT=" + log_root,
                   "BITE_SYSCTL=" + sysdir, "BITE_SAVED=" + saved]
            if tee_fails or sync_fails or date or sync_logs:
                bindir = os.path.join(root, "bin")
                os.mkdir(bindir)
                if date:
                    with open(os.path.join(bindir, "date"), "w") as f:
                        f.write(f"#!/bin/bash\necho {date}\n")
                    os.chmod(os.path.join(bindir, "date"), 0o755)
                if sync_logs:
                    with open(os.path.join(bindir, "sync"), "w") as f:
                        f.write(f'#!/bin/bash\necho "sync" >> {calls}\nexec /usr/bin/sync "$@"\n')
                    os.chmod(os.path.join(bindir, "sync"), 0o755)
                for name, on in (("tee", tee_fails), ("sync", sync_fails)):
                    if on:
                        p = os.path.join(bindir, name)
                        with open(p, "w") as f:
                            # "write": only the write's log can't be written, as when the overlay fills between the two steps.
                            only = 'case "$*" in *-write/log.txt) ;; *) exec /usr/bin/tee "$@" ;; esac\n' if on == "write" else ""
                            f.write("#!/bin/bash\n" + only + ("cat > /dev/null\n" if name == "tee" else "") + "exit 1\n")
                        os.chmod(p, 0o755)
                env.append("PATH=" + bindir + ":" + os.environ["PATH"])
            r = subprocess.run(["bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user", "--uid", str(uid), "--gid", "0", "--"]
                               + env + [launcher] + list(args), capture_output=True, text=True)
            lines = open(calls).read().split("\n")[:-1] if os.path.exists(calls) else []
            manifests = [manifest(os.path.join(snaps, n)) for n in sorted(os.listdir(snaps), key=int)]
            sandbox.panic = open(os.path.join(sysdir, "panic")).read().strip() if sysctl else None
            return r.returncode, r.stdout + r.stderr, lines, log_root, manifests

        def steps(logs):
            return sorted(d.rsplit("-", 1)[1] for d in os.listdir(logs))

        FULL = ["peek 6", "save save", "poke patch panic=10", "save save"]
        rc, out, calls, logs, snaps = sandbox()
        check(rc == 0 and calls == FULL and "done: A2's WD2 bite word reads 45 s" in out and MAY not in out and sandbox.panic == "10",
              "recfg-bite: the check, saved, then kernel.panic and the write, saved; exit 0", (rc, calls, out))
        check(steps(logs) == ["check", "write"], "each step has a directory of its own", os.listdir(logs))
        check(len(snaps) == 2 and all("-write/" not in f for f in snaps[0]) and any("-write/log.txt" in f for f in snaps[1])
              and snaps[0].items() <= snaps[1].items(),
              "the first save holds the check alone, and nothing in it changes after", [sorted(s) for s in snaps])
        cdir = [d for d in os.listdir(logs) if d.endswith("-check")][0]
        text = open(os.path.join(logs, cdir, "log.txt")).read()
        check("the reader's stage 6" in text and VERDICT in text and text.rstrip().endswith("if the check above still holds")
              and "next: recfg_poke patch, one write, 0x292f02010 := 0x405f7e00 (45 s)" in text,
              "the check's saved log ends with the line that says the write comes next", text)
        wdir = [d for d in os.listdir(logs) if d.endswith("-write")][0]
        check("the writer's patch" in open(os.path.join(logs, wdir, "log.txt")).read(), "the write's log holds the writer's output")

        # Stops before the writer runs: nothing written, exit 3 (the SRAM isn't as expected) or 4 (anything else).
        rc, out, calls, logs, snaps = sandbox(peek_rc=3)
        check(rc == 3 and calls == FULL[:2] and "nothing written" in out and steps(logs) == ["check"] and MAY not in out,
              "a check that isn't as expected: exit 3, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(peek_rc=1)
        check(rc == 4 and calls == FULL[:2] and "the check failed (exit 1); nothing written" in out and MAY not in out, "a check that fails: exit 4, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(peek_rc=139)
        check(rc == 4 and calls == FULL[:2] and "the check failed (exit 139); nothing written" in out, "a reader killed by a signal (139): exit 4, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=1)
        check(rc == 4 and calls == FULL[:2] and "couldn't save the check; nothing written" in out, "a check that can't be saved: exit 4, no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(tee_fails=True)
        check(rc == 4 and not [c for c in calls if c.startswith("poke")] and "couldn't write" in out, "a check whose log can't be written: exit 4, no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(sysctl=False)
        check(rc == 4 and calls == FULL[:2] and "couldn't set kernel.panic" in out, "kernel.panic can't be set: exit 4, no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(term_at=1)
        check(rc == 143 and calls == FULL[:2] and MAY not in out, "SIGTERM during the check's save: no write, and nothing said about the word", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(date="20261109T230000", records=(("20261109T230000", "check", "another run's\n"),))
        check(rc == 4 and calls == [] and "couldn't make" in out and open(os.path.join(logs, "recfg-bite-20261109T230000-check", "log.txt")).read() == "another run's\n",
              "a directory that's there already (another run, the same second) isn't written into: exit 4, nothing read", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(date="20261109T230000", records=(("20261109T230000", "write", "another run's\n"),))
        check(rc == 4 and calls == FULL[:2] and "couldn't make" in out and MAY not in out
              and open(os.path.join(logs, "recfg-bite-20261109T230000-write", "log.txt")).read() == "another run's\n",
              "a write directory that's there already: exit 4 after the check, the writer never runs, and nothing is said about the word", (rc, calls, out))
        NOT_SAVED = "NOT SAVED: the lines above are the only record"
        rc, out, calls, logs, snaps = sandbox(peek_rc=3, save_fail_at=1)
        check(rc == 3 and NOT_SAVED in out, "a check that isn't as expected and can't be saved says its record isn't saved", (rc, out))
        rc, out, calls, logs, snaps = sandbox(peek_rc=3)
        check(rc == 3 and NOT_SAVED not in out, "and doesn't say that when it was saved", (rc, out))
        rc, out, calls, logs, snaps = sandbox(safe=False)
        check(rc == 4 and "safe boot only" in out and calls == [], "refuses outside a safe boot (exit 4)", out)
        rc, out, calls, logs, snaps = sandbox(uid=1000)
        check(rc == 4 and "as root" in out and calls == [], "refuses to run as non-root (exit 4)", out)
        for old in ("peek", "poke", "peek-header", "poke-header"):
            rc, out, calls, logs, snaps = sandbox(old=old)
            check(rc == 4 and "older than its source" in out and calls == [], f"refuses a tool older than its source or the header ({old})", out)
        for args in (("--more",), ("--restore", "--after"), ("patch",)):
            rc, out, calls, logs, snaps = sandbox(args=args)
            check(rc == 1 and "usage" in out and calls == [], "refuses arguments " + repr(args), out)

        # From the writer's start on. Nothing here stores again: a way out that isn't a finished, saved write says the word may read 45 s.
        rc, out, calls, logs, snaps = sandbox(patch_rc=5)
        check(rc == 5 and calls == FULL and "THE WRITE DIDN'T READ BACK AS MEANT" in out and MAY not in out and NOT_SAVED not in out,
              "a write that doesn't read back: exit 5, saved, the writer's own account stands, and no second store", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(patch_rc=5, save_fail_at=2)
        check(rc == 5 and calls == FULL and NOT_SAVED in out, "the same when its record can't be saved: still exit 5, and it says the record isn't saved", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(patch_rc=3)
        check(rc == 3 and calls == FULL and "changed between the check and the write" in out and MAY not in out,
              "a writer that refuses: exit 3, saved; nothing was written", (rc, calls, out))
        for bad in (2, 9, 139):
            rc, out, calls, logs, snaps = sandbox(patch_rc=bad)
            check(rc == 1 and calls == FULL and "recfg_poke failed" in out and MAY in out,
                  f"a writer that fails ({bad}): exit 1, saved, and it says the word may read 45 s", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=2)
        check(rc == 1 and calls == FULL and "couldn't save the write's record" in out and MAY in out,
              "a write whose record can't be saved: exit 1, no store to undo it, and it says the word may read 45 s", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(tee_fails="write")
        check(rc == 1 and calls == FULL and "couldn't write" in out and MAY in out,
              "a write whose log can't be written: exit 1, and it says the word may read 45 s", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(term_at=2)
        check(rc == 143 and calls == FULL and MAY in out and "done: A2's WD2 bite word reads 45 s" not in out,
              "SIGTERM during the write's save: exit 143, and it says the word may read 45 s", (rc, calls, out))
        ltext = open(launcher).read()
        check(sorted(re.findall(r'run "\$POKE" (\w+)', ltext)) == ["patch", "restore"] and not re.search(r'^\s*"\$POKE"', ltext, re.M)
              and ltext.count('"$POKE"') == 3,
              "the launcher runs the writer in two places only: patch in the default mode, restore in --restore")

        # --restore, by hand.
        rc, out, calls, logs, snaps = sandbox(args=("--restore",))
        check(rc == 0 and calls == ["poke restore panic=10", "save save"] and steps(logs) == ["restore"] and "the old value is there" in out,
              "--restore: kernel.panic, the writer's restore, saved; exit 0", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), restore_rc=5)
        check(rc == 5 and calls == ["poke restore panic=10", "save save"] and "the old value is there" not in out, "--restore passes on the writer's exit status", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), save_fail_at=1)
        check(rc == 0 and calls == ["poke restore panic=10", "save save"] and "THE RESTORE'S RECORD WASN'T SAVED" in out,
              "--restore goes by the writer, and says so when its record wasn't saved", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), safe=False)
        check(rc == 1 and "safe boot only" in out and calls == [], "--restore refuses outside a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), old="peek")
        check(rc == 0 and calls == ["poke restore panic=10", "save save"], "--restore doesn't need the reader", (rc, calls, out))
        for old in ("poke", "poke-header"):
            rc, out, calls, logs, snaps = sandbox(args=("--restore",), old=old)
            check(rc == 1 and "older than its source" in out and calls == [], f"--restore refuses a writer older than its source or the header ({old})", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), sysctl=False)
        check(rc == 1 and calls == [] and "couldn't set kernel.panic" in out, "--restore without kernel.panic: no store", (rc, calls, out))

        # --after, the one read in a regular boot: only when the newest write on record left the word at 45 s, and nothing since.
        W = ("20261109T210000", "write", "the writer's patch\n" + TOOK + "\n")
        C = ("20261109T210000", "check", VERDICT + "\n")
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W))
        check(rc == 0 and calls == ["peek 7"] and snaps == [] and "nothing was written" in out and sandbox.panic == "0",
              "--after runs stage 7, the one word, once, with no save, no writer and no sysctl", (rc, calls, out))
        adir = [d for d in os.listdir(logs) if d.endswith("-after")]
        check(len(adir) == 1 and VERDICT in open(os.path.join(logs, adir[0], "log.txt")).read(), "and its log holds the verdict", adir)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, ("20261109T210000", "write", ALREADY + "\n")))
        check(rc == 0 and calls == ["peek 7"], "--after goes after a write that found the word at 45 s already", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), peek_rc=3)
        check(rc == 3 and calls == ["peek 7"], "--after passes on a word that's neither value (exit 3)", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), sync_logs=True)
        check(rc == 0 and calls == ["sync", "peek 7", "sync"],
              "--after's record is on the disk before the read (a read that hangs the Mac is still the one read), and synced again after it", (rc, calls, out))
        # The reader exits 1 or 2 only before its read: that record mustn't count as the read.
        for bad in (1, 2):
            rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), peek_rc=bad)
            check(rc == 1 and calls == ["peek 7"] and f"the reader stopped before its read (exit {bad}): nothing read, and this can be run again" in out
                  and steps(logs) == ["check", "unread", "write"],
                  f"--after: a reader that stops before its read (exit {bad}) leaves a record named -unread, not -after", (rc, calls, out, os.listdir(logs)))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W, ("20261109T211500", "unread", "")))
        check(rc == 0 and calls == ["peek 7"], "--after goes after a try that read nothing", (rc, calls, out))
        for bad in (9, 139):
            rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), peek_rc=bad)
            check(rc == 1 and calls == ["peek 7"] and f"the read failed (exit {bad})" in out and steps(logs) == ["after", "check", "write"],
                  f"--after: a reader that fails any other way (exit {bad}) may have read: an error, and its record stays", (rc, calls, out))
        for what, records, msg in (
                ("nothing on record", (), "no write is on record"),
                ("a check alone (the SRAM wasn't as expected, or the write faulted)", (C,), "no write is on record"),
                ("a write that didn't take", (C, ("20261109T210000", "write", "THE WRITE DIDN'T TAKE: the word still reads the old value\n")), "doesn't say the word read 45 s"),
                ("a write with an empty log", (C, ("20261109T210000", "write", "")), "doesn't say the word read 45 s"),
                ("an older write that took and a newer one that didn't", (W, ("20261109T220000", "write", "not as expected: nothing written\n")), "recfg-bite-20261109T220000-write"),
                ("a restore after the write", (C, W, ("20261109T210500", "restore", "the word reads the old value, 30 s, again\n")), "recfg-bite-20261109T210500-restore"),
                ("a restore in the same second as the write", (C, W, ("20261109T210000", "restore", "x\n")), "recfg-bite-20261109T210000-restore"),
                ("an --after already on record", (C, W, ("20261109T211500", "after", VERDICT + "\n")), "recfg-bite-20261109T211500-after"),
                ("an --after on record with nothing in it (the Mac went down at the read)", (C, W, ("20261109T211500", "after", "")), "recfg-bite-20261109T211500-after"),
                ("a write whose line is there, but not at a line's start", (C, ("20261109T210000", "write", "x " + TOOK + "\nx " + ALREADY + "\n")), "doesn't say the word read 45 s"),
                # Two later records, one on each side of the newest write: each one counts, whatever its place in the list.
                ("an --after before the write and a restore after it", (("20261109T200000", "after", "x\n"), W, ("20261109T213000", "restore", "x\n")), "recfg-bite-20261109T213000-restore"),
                ("a restore before the write and an --after after it", (("20261109T200000", "restore", "x\n"), W, ("20261109T213000", "after", "x\n")), "recfg-bite-20261109T213000-after"),
                ("two restores, one each side", (("20261109T200000", "restore", "x\n"), W, ("20261109T213000", "restore", "x\n")), "recfg-bite-20261109T213000-restore"),
                ("two --afters, one each side", (("20261109T200000", "after", "x\n"), W, ("20261109T213000", "after", "x\n")), "recfg-bite-20261109T213000-after")):
            rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=records)
            check(rc == 1 and msg in out and "Nothing read" in out and calls == [], f"--after refuses with {what}, reading nothing", (rc, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False,
                                              records=(("20261109T200000", "restore", "x\n"), ("20261109T200500", "after", "x\n"), C, W))
        check(rc == 0 and calls == ["peek 7"], "--after goes when the restore and the --after on record are older than the newest write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=True, records=(C, W))
        check(rc == 1 and "regular boot" in out and calls == [], "--after refuses in a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "--after refuses to run as non-root", out)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), sync_fails=True)
        check(rc == 1 and calls == [] and "couldn't sync" in out and "nothing read" in out and steps(logs) == ["check", "unread", "write"],
              "--after: a record that can't be put on the disk first means no read, and doesn't count as one", (rc, calls, out, os.listdir(logs)))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), old="poke")
        check(rc == 0 and calls == ["peek 7"], "--after doesn't need the writer", (rc, calls, out))
        for old in ("peek", "peek-header"):
            rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), old=old)
            check(rc == 1 and "older than its source" in out and calls == [] and steps(logs) == ["check", "write"],
                  f"--after refuses a reader older than its source or the header ({old}), reading nothing and leaving no record", (rc, calls, out, os.listdir(logs)))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, records=(C, W), waiting=True)
        check(rc == 1 and "sleep-drain-safe collect first. Nothing read" in out and calls == [] and steps(logs) == ["check", "write"],
              "--after refuses while a safe boot's save is still waiting to be collected", (rc, calls, out))
        check('SAFE_FLAG=${BITE_SAFE_FLAG:-/run/sleep-drain-safe/active}\n' in ltext and 'SAVE=${BITE_SAVE:-/usr/local/bin/sleep-drain-safe}\n' in ltext
              and 'LOG_ROOT=${BITE_LOG_ROOT:-/var/log/sleep-drain}\n' in ltext and 'SYSCTL=${BITE_SYSCTL:-/proc/sys/kernel}\n' in ltext
              and 'SAVED=${BITE_SAVED:-/boot/sleep-drain-safe/saved}\n' in ltext and 'PEEK=${BITE_PEEK:-$HERE/recfg_peek}\n' in ltext
              and 'POKE=${BITE_POKE:-$HERE/recfg_poke}\n' in ltext and 'HEADER=${BITE_HEADER:-$HERE/recfg_bite.h}\n' in ltext,
              "the launcher's own paths, which every case here overrides, are the real ones")

        # The real reader and writer under the launcher, on the fake device: the pieces fit each other.
        peek_real = os.path.join(tmp, "recfg_peek_real")
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=3", '-DRECFG_MEM="%s"' % fake, "-o", peek_real,
                        os.path.join(E1_HOOK, "recfg_peek.c")], check=True)
        before = mem(GOOD, NEIGHBOURS)
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke))
        check(rc == 0 and calls == ["save save", "save save"] and content(fake) == {**before, BITE: NEW} and "done: A2's WD2 bite word reads 45 s" in out,
              "the launcher with the real tools: the one word changed on the device, two saves, exit 0", (rc, calls, out))
        cdir, wdir = (os.path.join(logs, [d for d in os.listdir(logs) if d.endswith(e)][0], "log.txt") for e in ("-check", "-write"))
        ctext, wtext = open(cdir).read(), open(wdir).read()
        check(ctext.count("reading 0x") == 10 and "A2's WD2 bite word, 0x292f02010: the old value, 30 s\n" in ctext and "writing" not in ctext
              and "writing 0x405f7e00 to 0x292f02010\n" in wtext and TOOK + "\n" in wtext,
              "its two logs: the check's ten reads and verdict, then the writer's own lines, with the line --after looks for", ctext + wtext)
        real_w = ("20261109T210000", "write", wtext)
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke))
        wdir2 = os.path.join(logs, [d for d in os.listdir(logs) if d.endswith("-write")][0], "log.txt")
        check(rc == 0 and content(fake) == {**before, BITE: NEW} and ALREADY in out and ALREADY + "\n" in open(wdir2).read(),
              "run again: the check finds the new value, and nothing is written", out)
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke), safe=False, args=("--after",), records=(real_w,))
        check(rc == 0 and calls == [] and content(fake) == {**before, BITE: NEW} and "0x292f02010 = 0x405f7e00  A2's WD2 bite word: the new value, 45 s" in out
              and out.count("reading 0x") == 1,
              "--after with the real reader and the real write's record: one read, a word that kept the new value, and nothing written", (rc, out))
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke), args=("--restore",))
        check(rc == 0 and content(fake) == before and "the old value is there" in out, "--restore with the real writer: the device is as it began", (rc, out))
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke), safe=False, args=("--after",), records=(real_w,))
        check(rc == 0 and "A2's WD2 bite word: the old value, 30 s" in out, "--after then reports the old value", (rc, out))
        values = list(GOOD)
        values[5] ^= 0x100
        start = mem(values, NEIGHBOURS)
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke))
        check(rc == 3 and content(fake) == start and calls == ["save save"] and not [d for d in os.listdir(logs) if d.endswith("-write")],
              "the real tools on an SRAM that isn't as expected: exit 3 at the check, saved, and the writer never runs", (rc, calls, out))
        start = mem(GOOD, NEIGHBOURS)
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, dropped))
        check(rc == 5 and content(fake) == start and "THE WRITE DIDN'T TAKE" in out and "THE WRITE DIDN'T READ BACK AS MEANT" in out and MAY not in out,
              "the real tools on an SRAM that drops the store: exit 5, said by the writer and the launcher, and the device unchanged", (rc, out))
        wdir3 = os.path.join(logs, [d for d in os.listdir(logs) if d.endswith("-write")][0], "log.txt")
        rc, out, calls, logs, snaps = sandbox(real=(peek_real, poke), safe=False, args=("--after",), records=(("20261109T210000", "write", open(wdir3).read()),))
        check(rc == 1 and "doesn't say the word read 45 s" in out, "and --after won't read after that write's record", (rc, out))

print("\n%d failures" % failed)
sys.exit(1 if failed else 0)
