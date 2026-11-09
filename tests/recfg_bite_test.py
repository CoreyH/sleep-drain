#!/usr/bin/env python3
"""e1-hook/recfg_poke, the one write to the reconfig SRAM, and e1-hook/recfg-bite's
order, without hardware.

The writer is compiled with its memory device pointed at a sparse file whose
offsets are physical addresses, its safe-boot flag at a file here, and with
-DRECFG_TRACE, so that every access is logged: each case checks what it read,
that it stored at most to the one address, and the file's whole content after.
Two more builds drop or change its stores, as an SRAM that's locked or takes
another value would. The launcher runs as uid 0 in a bubblewrap user namespace
with stub tools and a stub sleep-drain-safe that record what they're asked and
what the log directory held at each save.

  python3 -I tests/recfg_bite_test.py
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
    trace = os.path.join(tmp, "trace")
    src_path = os.path.join(E1_HOOK, "recfg_poke.c")

    def build(name, *extra):
        exe = os.path.join(tmp, name)
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", '-DRECFG_MEM="%s"' % fake, '-DRECFG_SAFE_FLAG="%s"' % flag,
                        '-DRECFG_TRACE="%s"' % trace, *extra, "-o", exe, src_path], check=True)
        return exe

    poke = build("recfg_poke")
    dropped = build("recfg_poke_drop", "-DRECFG_POKE_DROP(n)=1")
    garbled_once = build("recfg_poke_once", "-DRECFG_POKE_STORE(v,n)=((n)==0?(v)^0xffffU:(v))")
    garbled = build("recfg_poke_garble", "-DRECFG_POKE_STORE(v,n)=((v)^0xffffU)")

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
    NEIGHBOURS = [(SRAM + 0x1a10, OLD), (SRAM + 0x1cf0, OLD), (SRAM + 0x21b0, OLD), (SRAM + 0x2018, 0x11111111), (SRAM + 0x200c + 0x10, 0x22222222),
                  (SRAM + 0x1ffc, 0x33333333), (SRAM, 0x44444444), (SRAM + SRAM_LEN - 4, 0x55555555)]
    READS = [("r", pa, None) for pa in TEN]

    def reads_then(acc, rest):
        return [(op, pa) for op, pa, _ in acc[:10]] == [(op, pa) for op, pa, _ in READS] and acc[10:] == rest

    open(flag, "w").close()
    before = mem(GOOD, NEIGHBOURS)
    r, acc = run(poke, "patch")
    after = content(fake)
    check(r.returncode == 0 and "the word took the write: it reads the new value, 45 s" in r.stdout, "patch: exit 0, and it says the word took the write", r.stdout + r.stderr)
    check(reads_then(acc, [("w", BITE, NEW), ("r", BITE, NEW)]), "patch reads the ten words in order, stores once, to 0x292f02010, and reads it back", acc)
    check(after == {**before, BITE: NEW} and before[BITE] == OLD, "patch changes that one word in the whole device, and nothing else",
          {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)})
    out = r.stdout
    check(all(out.index("reading 0x%x\n" % pa) < out.index("0x%x = 0x%08x" % (pa, v)) for pa, v in zip(TEN, GOOD))
          and out.index("A2's WD2 bite word, 0x292f02010: the old value, 30 s") < out.index("writing 0x405f7e00 to 0x292f02010\n")
          < out.rindex("reading 0x292f02010\n") < out.index("0x292f02010 = 0x405f7e00\n"),
          "patch says each read before it, the verdict, then the write before it, then the read-back", out)

    r, acc = run(poke, "patch")
    check(r.returncode == 0 and "already the new value: nothing written" in r.stdout and reads_then(acc, []) and content(fake) == after,
          "patch on a word that's already new: exit 0, no store", r.stdout + str(acc))
    r, acc = run(poke, "restore")
    check(r.returncode == 0 and "the word reads the old value, 30 s, again" in r.stdout and reads_then(acc, [("w", BITE, OLD), ("r", BITE, OLD)])
          and content(fake) == before,
          "restore: one store of the old value to the same word, read back, and the device is as it began", r.stdout + str(acc))
    r, acc = run(poke, "restore")
    check(r.returncode == 0 and "already the old value: nothing written" in r.stdout and reads_then(acc, []) and content(fake) == before,
          "restore on a word that's already old: exit 0, no store", r.stdout + str(acc))

    # Anything not as expected: nothing is written, by either.
    for i in range(10):
        for flip in (0x10, 0x80000000):
            values = list(GOOD)
            values[i] ^= flip
            start = mem(values, NEIGHBOURS)
            r, acc = run(poke, "patch")
            check(r.returncode == 3 and "nothing written" in r.stdout and reads_then(acc, []) and content(fake) == start,
                  f"patch refuses (exit 3) with word {i} off by {flip:#x}: ten reads, no store", r.stdout + str(acc))
            r, acc = run(poke, "restore")
            if i == 8:
                # The op is as expected and only the bite is strange: the old value goes back.
                check(r.returncode == 0 and reads_then(acc, [("w", BITE, OLD), ("r", BITE, OLD)]) and content(fake) == {**start, BITE: OLD},
                      f"restore puts the old value over a bite that's neither ({values[8]:#x}), the op around it being as expected", r.stdout + str(acc))
            else:
                check(r.returncode == 3 and "nothing written" in r.stdout and reads_then(acc, []) and content(fake) == start,
                      f"restore refuses (exit 3) with word {i} off by {flip:#x}: no store", r.stdout + str(acc))
            if i != 8:
                values[8] = NEW
                start = mem(values, NEIGHBOURS)
                for mode in ("patch", "restore"):
                    r, acc = run(poke, mode)
                    check(r.returncode == 3 and reads_then(acc, []) and content(fake) == start,
                          f"{mode} refuses the same with the new bite there", r.stdout + str(acc))
    start = mem([0xffffffff] * 10)
    for mode in ("patch", "restore"):
        r, acc = run(poke, mode)
        check(r.returncode == 3 and reads_then(acc, []) and content(fake) == start, f"{mode} on all ones: no store", r.stdout + str(acc))

    # An SRAM that drops the store (locked), or takes another value.
    start = mem(GOOD, NEIGHBOURS)
    r, acc = run(dropped, "patch")
    check(r.returncode == 5 and "THE WRITE DIDN'T TAKE" in r.stdout and reads_then(acc, [("w", BITE, NEW), ("r", BITE, OLD)]) and content(fake) == start,
          "a store that's dropped: exit 5, said so, one try, nothing changed", r.stdout + str(acc))
    start = mem(GOOD[:8] + [NEW, 4], NEIGHBOURS)
    r, acc = run(dropped, "restore")
    check(r.returncode == 5 and "THE OLD VALUE DIDN'T GO BACK" in r.stdout and reads_then(acc, [("w", BITE, OLD), ("r", BITE, NEW)]) and content(fake) == start,
          "a restore that's dropped: exit 5, said so", r.stdout + str(acc))
    start = mem(GOOD, NEIGHBOURS)
    r, acc = run(garbled_once, "patch")
    check(r.returncode == 5 and "THE WORD READS NEITHER VALUE" in r.stdout and "the word reads the old value, 30 s, again" in r.stdout
          and reads_then(acc, [("w", BITE, NEW), ("r", BITE, NEW ^ 0xffff), ("w", BITE, OLD), ("r", BITE, OLD)]) and content(fake) == start,
          "a store that lands as another value: exit 5, and the old value is put back", r.stdout + str(acc))
    r, acc = run(garbled, "patch")
    check(r.returncode == 5 and "THE OLD VALUE DIDN'T GO BACK EITHER" in r.stdout
          and reads_then(acc, [("w", BITE, NEW), ("r", BITE, NEW ^ 0xffff), ("w", BITE, OLD), ("r", BITE, OLD ^ 0xffff)])
          and content(fake) == {**start, BITE: OLD ^ 0xffff},
          "a store that always lands as another value: exit 5, two tries and no more, only that word touched", r.stdout + str(acc))

    # Outside a safe boot, and its arguments: nothing is even read.
    start = mem(GOOD, NEIGHBOURS)
    os.unlink(flag)
    for mode in ("patch", "restore"):
        r, acc = run(poke, mode)
        check(r.returncode == 2 and "safe boot only" in r.stderr and acc == [] and content(fake) == start, f"{mode} refuses outside a safe boot, reading nothing", r.stderr)
    open(flag, "w").close()
    for args in ([], ["patch", "restore"], ["write"], ["patch", "0x292f02014"], ["0x292f02010"], ["patch", "0x405f7e00"], ["--patch"], ["restore", ""]):
        r, acc = run(poke, *args)
        check(r.returncode == 2 and acc == [] and content(fake) == start, "refuses arguments " + repr(args) + ", reading nothing", r.stdout + r.stderr)

    # The source: one store, one address, two values, no number taken from the command line.
    src = open(src_path).read()
    hdr = open(os.path.join(E1_HOOK, "recfg_bite.h")).read()
    check(src.count("wr32(") == 2 and "wr32(BITE_PA, value);" in src and src.count("str %w0") == 1 and src.count("*(volatile uint32_t *)p = v;") == 1
          and sorted(a for a in __import__("re").findall(r"put\((\w+)\)", src)) == ["RECFG_BITE_NEW", "RECFG_BITE_OLD", "RECFG_BITE_OLD"]
          and "#define BITE_PA ((uint64_t)(RECFG_SRAM + RECFG_BITE))" in src
          and not any(w in src for w in ("strtoul", "strtol", "atoi", "sscanf", "argv[2]")),
          "recfg_poke.c has one store, to BITE_PA, of one of the two values, and takes no number from its arguments")
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

        def manifest(root):
            res = {}
            for d, _, fs in os.walk(root):
                for f in fs:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        res[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
            return res

        def sandbox(*, args=(), safe=True, uid=0, peek_rc=0, patch_rc=0, restore_rc=0, save_fail_at=None, tee_fails=False, sync_fails=False,
                    old=None, checks=()):
            """Runs the launcher with stubs; returns (rc, output, calls, the log root, manifests at each save).
            old: which built file is older than a source ("peek", "poke", "peek-header", "poke-header").
            checks: earlier check directories on record, as (time, has a verdict line)."""
            root = tempfile.mkdtemp(dir=tmp)
            calls = os.path.join(root, "calls")
            log_root = os.path.join(root, "log")
            snaps = os.path.join(root, "snaps")
            os.mkdir(log_root)
            os.mkdir(snaps)
            peek_s, poke_s, save = (os.path.join(root, n) for n in ("recfg_peek", "recfg_poke", "safe"))
            with open(peek_s, "w") as f:
                f.write(f"""#!/bin/bash
echo "peek $1" >> {calls}
[[ -d $2 ]] || {{ echo 'no dir' >&2; exit 9; }}
echo "the reader's stage $1"
{'kill -SEGV $$' if peek_rc == 'segv' else f'''[[ {peek_rc} == 0 || {peek_rc} == 3 ]] && echo "{VERDICT}"
exit {peek_rc}'''}
""")
            with open(poke_s, "w") as f:
                f.write(f"""#!/bin/bash
echo "poke $*" >> {calls}
echo "the writer's $1"
case $1 in
patch) {'kill -SEGV $$' if patch_rc == 'segv' else f'exit {patch_rc}'} ;;
restore) exit {restore_rc} ;;
esac
exit 9
""")
            with open(save, "w") as f:
                f.write(f"""#!/bin/bash
echo "save $*" >> {calls}
n=$(ls {snaps} | wc -l)
cp -a {log_root} {snaps}/$n
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
            if old == "peek":
                os.utime(srcs["recfg_peek.c"], (3000, 3000))
            elif old == "poke":
                os.utime(srcs["recfg_poke.c"], (3000, 3000))
            elif old in ("peek-header", "poke-header"):
                os.utime(srcs["recfg_bite.h"], (3000, 3000))
                # Only the named tool is older than the header; the other is rebuilt.
                os.utime(poke_s if old == "peek-header" else peek_s, (4000, 4000))
            for when, has_verdict in checks:
                d = os.path.join(log_root, f"recfg-bite-{when}-check")
                os.mkdir(d)
                with open(os.path.join(d, "log.txt"), "w") as f:
                    f.write("reading 0x2925c0200\n" + (VERDICT + "\n" if has_verdict else ""))
            flag_s = os.path.join(root, "active")
            if safe:
                open(flag_s, "w").close()
            env = ["env", "BITE_PEEK=" + peek_s, "BITE_PEEK_SRC=" + srcs["recfg_peek.c"], "BITE_POKE=" + poke_s, "BITE_POKE_SRC=" + srcs["recfg_poke.c"],
                   "BITE_HEADER=" + srcs["recfg_bite.h"], "BITE_SAFE_FLAG=" + flag_s, "BITE_SAVE=" + save, "BITE_LOG_ROOT=" + log_root]
            if tee_fails or sync_fails:
                bindir = os.path.join(root, "bin")
                os.mkdir(bindir)
                for name, on in (("tee", tee_fails), ("sync", sync_fails)):
                    if on:
                        p = os.path.join(bindir, name)
                        with open(p, "w") as f:
                            f.write("#!/bin/bash\n" + ("cat > /dev/null\n" if name == "tee" else "") + "exit 1\n")
                        os.chmod(p, 0o755)
                env.append("PATH=" + bindir + ":" + os.environ["PATH"])
            r = subprocess.run(["bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user", "--uid", str(uid), "--gid", "0", "--"]
                               + env + [launcher] + list(args), capture_output=True, text=True)
            lines = open(calls).read().split("\n")[:-1] if os.path.exists(calls) else []
            manifests = [manifest(os.path.join(snaps, n)) for n in sorted(os.listdir(snaps), key=int)]
            return r.returncode, r.stdout + r.stderr, lines, log_root, manifests

        def steps(logs):
            return sorted(d.rsplit("-", 1)[1] for d in os.listdir(logs))

        FULL = ["peek 6", "save save", "poke patch", "save save"]
        rc, out, calls, logs, snaps = sandbox()
        check(rc == 0 and calls == FULL and "done: A2's WD2 bite word reads 45 s" in out, "recfg-bite: the check, saved, then the write, saved; exit 0", (rc, calls, out))
        check(steps(logs) == ["check", "write"], "each step has a directory of its own", os.listdir(logs))
        check(len(snaps) == 2 and all("-write/" not in f for f in snaps[0]) and any("-write/log.txt" in f for f in snaps[1])
              and all(snaps[0].items() <= snaps[1].items() for _ in (0,)),
              "the first save holds the check alone, and nothing in it changes after", [sorted(s) for s in snaps])
        cdir = [d for d in os.listdir(logs) if d.endswith("-check")][0]
        text = open(os.path.join(logs, cdir, "log.txt")).read()
        check("the reader's stage 6" in text and VERDICT in text and text.rstrip().endswith("if the check above still holds")
              and "next: recfg_poke patch, one write, 0x292f02010 := 0x405f7e00 (45 s)" in text,
              "the check's saved log ends with the line that says the write comes next", text)
        wdir = [d for d in os.listdir(logs) if d.endswith("-write")][0]
        check("the writer's patch" in open(os.path.join(logs, wdir, "log.txt")).read(), "the write's log holds the writer's output")

        rc, out, calls, logs, snaps = sandbox(peek_rc=3)
        check(rc == 3 and calls == FULL[:2] and "nothing written" in out and steps(logs) == ["check"], "a check that isn't as expected: exit 3, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(peek_rc=1)
        check(rc == 1 and calls == FULL[:2] and "the check failed (exit 1); nothing written" in out, "a check that fails: exit 1, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(peek_rc="segv")
        check(rc == 1 and calls == FULL[:2] and "nothing written" in out, "a reader killed by a signal: exit 1, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=1)
        check(rc == 1 and calls == FULL[:2] and "couldn't save the check; nothing written" in out, "a check that can't be saved: no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(tee_fails=True)
        check(rc == 1 and "poke patch" not in calls and "couldn't write" in out, "a check whose log can't be written: no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(patch_rc=5)
        check(rc == 5 and calls == FULL and "THE WRITE DIDN'T READ BACK" in out, "a write that doesn't read back: exit 5, saved", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(patch_rc=3)
        check(rc == 3 and calls == FULL and "changed between the check and the write" in out, "a writer that refuses: exit 3, saved", (rc, calls, out))
        for bad in (2, 9, "segv"):
            rc, out, calls, logs, snaps = sandbox(patch_rc=bad)
            check(rc == 1 and calls == FULL and "recfg_poke failed" in out, f"a writer that fails ({bad}): exit 1, saved", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=2)
        check(rc == 1 and calls == FULL + ["poke restore"] and "the old value was put back (recfg_poke restore exit 0)" in out,
              "a write whose record can't be saved: the old value goes back, exit 1", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=2, restore_rc=5)
        check(rc == 1 and calls == FULL + ["poke restore"] and "(recfg_poke restore exit 5)" in out, "and it says so if that restore fails", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(safe=False)
        check(rc == 1 and "safe boot only" in out and calls == [], "refuses outside a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "refuses to run as non-root", out)
        for old in ("peek", "poke", "peek-header", "poke-header"):
            rc, out, calls, logs, snaps = sandbox(old=old)
            check(rc == 1 and "older than its source" in out and calls == [], f"refuses a tool older than its source or the header ({old})", out)
        for args in (("--more",), ("--restore", "--after"), ("patch",)):
            rc, out, calls, logs, snaps = sandbox(args=args)
            check(rc == 1 and "usage" in out and calls == [], "refuses arguments " + repr(args), out)

        # --restore.
        rc, out, calls, logs, snaps = sandbox(args=("--restore",))
        check(rc == 0 and calls == ["poke restore", "save save"] and steps(logs) == ["restore"] and "the old value is there" in out,
              "--restore: the writer's restore, saved; exit 0", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), restore_rc=5)
        check(rc == 5 and calls == ["poke restore", "save save"] and "the old value is there" not in out, "--restore passes on the writer's exit status", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), save_fail_at=1)
        check(rc == 0 and calls == ["poke restore", "save save"], "--restore goes by the writer, not by its save", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), safe=False)
        check(rc == 1 and "safe boot only" in out and calls == [], "--restore refuses outside a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(args=("--restore",), old="peek")
        check(rc == 0 and calls == ["poke restore", "save save"], "--restore doesn't need the reader", (rc, calls, out))

        # --after, the one read in a regular boot: only with a check on record that reached its verdict.
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True),))
        check(rc == 0 and calls == ["peek 6"] and snaps == [] and "nothing was written" in out, "--after reads stage 6 once, with no save and no writer", (rc, calls, out))
        adir = [d for d in os.listdir(logs) if d.endswith("-after")]
        check(len(adir) == 1 and VERDICT in open(os.path.join(logs, adir[0], "log.txt")).read(), "and its log holds the verdict", adir)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True),), peek_rc=3)
        check(rc == 3 and calls == ["peek 6"], "--after passes on a verdict that isn't as expected (exit 3)", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True),), peek_rc=1)
        check(rc == 1 and calls == ["peek 6"] and "the read failed" in out, "--after: a failed read is an error", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False)
        check(rc == 1 and "no safe boot's check is on record" in out and calls == [], "--after refuses with no check on record", out)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True), ("20261109T220000", False)))
        check(rc == 1 and "never reached its verdict" in out and "recfg-bite-20261109T220000-check" in out and calls == [],
              "--after refuses when the newest check never reached its verdict, whatever an older one did", out)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T200000", False), ("20261109T210000", True)))
        check(rc == 0 and calls == ["peek 6"], "--after goes when the newest check reached its verdict", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=True, checks=(("20261109T210000", True),))
        check(rc == 1 and "regular boot" in out and calls == [], "--after refuses in a safe boot", out)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True),), uid=1000)
        check(rc == 1 and "as root" in out and calls == [], "--after refuses to run as non-root", out)
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True),), sync_fails=True)
        check(rc == 1 and calls == ["peek 6"] and "couldn't sync" in out, "--after: a record that can't be synced is an error", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(args=("--after",), safe=False, checks=(("20261109T210000", True),), old="poke")
        check(rc == 0 and calls == ["peek 6"], "--after doesn't need the writer", (rc, calls, out))

print("\n%d failures" % failed)
sys.exit(1 if failed else 0)
