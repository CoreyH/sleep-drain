#!/usr/bin/env python3
"""e1-hook/wd2_poke, the one write to WD2's bite from user space, and e1-hook/wd2-bite's
order, without hardware.

The writer is compiled with its memory device pointed at a sparse file whose
offsets are physical addresses, its safe-boot flag and kernel command line at
files here, and with -DWD2_TRACE, so that every access is logged: each case
checks what it read, that it stored at most once, to the one address, and the
file's whole content after. More builds drop or change its store, as a register
that ignores it or takes another value would, or arm WD2 with it. The launcher
runs as uid 0 in a bubblewrap user namespace with a stub writer and a stub
sleep-drain-safe that record what they're asked and what the log directory held
at each save, and then with the real writer on the fake device.

  python3 -I tests/wd2_bite_test.py
"""
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))

WDT = 0x2922b0000
CTRL, COUNT, BITE = WDT + 0x2c, WDT + 0x20, WDT + 0x24
THREE = [CTRL, COUNT, BITE]  # the order the tool reads them in
HZ = 24000000
A2, AWAKE, NEW = 30 * HZ, 90 * HZ, 120 * HZ
SIZE = WDT + 0x8000

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
    src_path = os.path.join(E1_HOOK, "wd2_poke.c")
    SAFE_CMDLINE = "root=/dev/mapper/root rw quiet sleepdrain.safe\n"

    def build(name, *extra, trace_to=trace):
        exe = os.path.join(tmp, name)
        # _FORTIFY_SOURCE as some distributions' compilers set it by default.
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror", "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=3", '-DWD2_MEM="%s"' % fake,
                        '-DWD2_SAFE_FLAG="%s"' % flag, '-DWD2_CMDLINE="%s"' % cmdline, '-DWD2_TRACE="%s"' % trace_to, *extra, "-o", exe, src_path],
                       check=True)
        return exe

    poke = build("wd2_poke")
    dropped = build("wd2_poke_drop", "-DWD2_POKE_DROP=1")
    garbled = build("wd2_poke_garble", "-DWD2_POKE_STORE(v)=((v)^0xffffU)")
    # A store that takes, and sets RESET_EN in WD2's control with it (8 bytes on from the bite).
    arming = build("wd2_poke_arming", "-DWD2_POKE_STORE(v)=(*(volatile uint32_t*)((volatile char*)p+8)=4,(v))")

    # Around the register: WD0's and WD1's words, the word between WD2's bite and control, and the page's ends must be left alone.
    NEIGHBOURS = [(WDT + 0x00, 0x1111), (WDT + 0x04, 0x2222), (WDT + 0x0c, 0x3333), (WDT + 0x10, 0x4444), (WDT + 0x14, 30 * HZ), (WDT + 0x1c, 4),
                  (WDT + 0x28, 0x5555), (WDT + 0x30, 0x6666), (WDT - 4, 0x7777), (WDT + 0x3ffc, 0x8888), (WDT + 0x4000, 0x9999)]

    def mem(ctrl=0, count=0x1234, bite=AWAKE, extra=NEIGHBOURS):
        """A fresh device: WD2's three words, and other words that must not change."""
        with open(fake, "wb") as f:
            f.truncate(SIZE)
        with open(fake, "r+b") as f:
            for pa, v in [(CTRL, ctrl), (COUNT, count), (BITE, bite)] + list(extra):
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

    def shape(acc):
        """The accesses as a string: T for a pass over the three words, in order; w for the store of 120 s to the bite
        (w(0x...) for another value there); ? for anything else."""
        out, i = [], 0
        while i < len(acc):
            if [(op, pa) for op, pa, _ in acc[i:i + 3]] == [("r", pa) for pa in THREE]:
                out.append("T")
                i += 3
            elif acc[i][0] == "w" and acc[i][1] == BITE:
                out.append("w" if acc[i][2] == NEW else "w(%#x)" % acc[i][2])
                i += 1
            else:
                out.append("?")
                i += 1
        return " ".join(out)

    WRITING = "writing 0xaba95000 to 0x2922b0024, one 32-bit store\n"
    TOOK = "the register took the write: the bite reads the new value, 120 s, and WD2 still isn't armed"
    AS_AWAKE = "WD2: as it is awake: not armed, its bite 90 s"

    open(flag, "w").close()
    with open(cmdline, "w") as f:
        f.write(SAFE_CMDLINE)

    # check: three reads, a verdict, no store, whatever is there.
    before = mem()
    r, acc = run(poke, "check")
    check(r.returncode == 0 and shape(acc) == "T" and content(fake) == before and AS_AWAKE + "\n" in r.stdout and "writing" not in r.stdout,
          "check on WD2 as it is awake: exit 0, the three words read in order (control, count, bite), nothing stored", r.stdout + str(acc))
    out = r.stdout
    check(all(out.index("reading 0x%x\n" % pa) < out.index("0x%x = 0x%08x" % (pa, v)) for pa, v in zip(THREE, (0, 0x1234, AWAKE))),
          "check says each read before it is made, then what it read", out)
    start = mem(bite=NEW)
    r, acc = run(poke, "check")
    check(r.returncode == 7 and shape(acc) == "T" and content(fake) == start and "WD2: not armed, its bite the new value, 120 s\n" in r.stdout,
          "check with 120 s there already: exit 7, its own status", r.stdout)
    for kw, what in ((dict(ctrl=4), "ARMED"), (dict(ctrl=5), "ARMED"), (dict(ctrl=7, bite=A2), "ARMED"), (dict(ctrl=1), "not as expected"), (dict(ctrl=2, bite=NEW), "not as expected"),
                     (dict(ctrl=8), "not as expected"), (dict(ctrl=0x100), "not as expected"), (dict(ctrl=0x80000000), "not as expected"), (dict(ctrl=0xfffffffb), "not as expected"),
                     (dict(bite=A2), "not as expected"), (dict(bite=0), "not as expected"), (dict(bite=AWAKE + 1), "not as expected"), (dict(bite=0xffffffff, ctrl=0xffffffff), "ARMED")):
        start = mem(**kw)
        for mode in ("check", "patch"):
            r, acc = run(poke, mode)
            check(r.returncode == 3 and shape(acc) == "T" and content(fake) == start and f"WD2: {what}" in r.stdout and "writing" not in r.stdout
                  and (mode == "check" or "not as it is awake: nothing written\n" in r.stdout),
                  f"{mode} with {kw}: exit 3, said ({what}), three reads and no store", r.stdout + str(acc))

    # patch: one store, of the one value, to the one address, only over WD2 as it is awake.
    before = mem()
    r, acc = run(poke, "patch")
    after = content(fake)
    check(r.returncode == 0 and TOOK + "\n" in r.stdout and shape(acc) == "T w T",
          "patch: exit 0, the three words read, one store, the three read again, and it says the register took the write", r.stdout + str(acc))
    check([(pa, v) for op, pa, v in acc if op == "w"] == [(0x2922b0024, 0xaba95000)], "its store is 0xaba95000 to 0x2922b0024", acc)
    check(after == {**before, BITE: NEW} and before[BITE] == AWAKE,
          "patch changes that one word in the whole device, and nothing else",
          {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)})
    out = r.stdout
    check(out.index(AS_AWAKE) < out.index(WRITING) < out.index("the three words again, after the write:\n") < out.index("0x2922b0024 = 0xaba95000")
          < out.index("WD2: not armed, its bite the new value, 120 s") < out.index(TOOK),
          "patch says the verdict, then the write before it, then the three again and their verdict", out)
    r, acc = run(poke, "patch")
    check(r.returncode == 7 and "already the new value: nothing written" in r.stdout and "took the write" not in r.stdout and shape(acc) == "T" and content(fake) == after,
          "patch on a bite that's already 120 s: no store, and exit 7, not the exit of a store that took", r.stdout + str(acc))
    # Each line is out before its access is made: with the trace on stdout too, the order shows.
    inline = build("wd2_poke_inline", trace_to="/dev/stdout")
    mem()
    seen = subprocess.run([inline, "patch"], capture_output=True, text=True).stdout
    check(all(seen.index("reading 0x%x\n" % pa) < seen.index("r 0x%x " % pa) for pa in THREE)
          and seen.index(WRITING) < seen.index("w 0x2922b0024 0xaba95000\n") < seen.rindex("reading 0x2922b0024\n") < seen.rindex("r 0x2922b0024 0xaba95000\n"),
          "patch prints each \"reading\" line before that read and the \"writing\" line before the store", seen)
    # The count runs between the two passes on the Mac: it's read, and it decides nothing.
    for count in (0, 1, 0xffffffff):
        start = mem(count=count)
        r, acc = run(poke, "patch")
        check(r.returncode == 0 and shape(acc) == "T w T" and content(fake) == {k: v for k, v in {**start, BITE: NEW}.items()},
              f"patch with a count of {count:#x}: the same", r.stdout)

    # A register that drops the store, takes another value, or is armed by it: said, and nothing more is written.
    start = mem()
    r, acc = run(dropped, "patch")
    check(r.returncode == 5 and "THE WRITE DIDN'T TAKE: the bite still reads 90 s. Nothing is changed\n" in r.stdout and shape(acc) == "T w T" and content(fake) == start,
          "a store that's dropped: exit 5, said so, one try, nothing changed", r.stdout + str(acc))
    r, acc = run(garbled, "patch")
    check(r.returncode == 6 and "WD2 READS NEITHER AS BEFORE NOR AS INTENDED after the write" in r.stdout and "Nothing more is written" in r.stdout
          and "took the write" not in r.stdout and shape(acc) == "T w T" and content(fake) == {**start, BITE: NEW ^ 0xffff},
          "a store that lands as another value: exit 6, said so, and no second store (the OK is for one value)", r.stdout + str(acc))
    start = mem()
    r, acc = run(arming, "patch")
    check(r.returncode == 6 and "WD2: ARMED" in r.stdout and "(control 0x00000000 before, 0x00000004 now)" in r.stdout and "took the write" not in r.stdout
          and shape(acc) == "T w T" and content(fake) == {**start, BITE: NEW, CTRL: 4},
          "a store that takes and leaves WD2 armed: exit 6, with both controls, and no second store", r.stdout + str(acc))

    # Outside a safe boot, and its arguments: nothing is even read.
    start = mem()
    os.unlink(flag)
    for mode in ("check", "patch"):
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
    for text in ("sleepdrain.safe\n", "a  sleepdrain.safe  b\n", "x" * 2031 + " sleepdrain.safe\n"):
        with open(cmdline, "w") as f:
            f.write(text)
        mem()
        r, acc = run(poke, "patch")
        check(r.returncode == 0 and shape(acc) == "T w T", f"patch goes with the flag and a {len(text)}-byte command line that has the word", r.stdout + r.stderr)
    with open(cmdline, "w") as f:
        f.write(SAFE_CMDLINE)
    start = mem()
    # Started with a standard descriptor closed, the device would take it: with stdout or stderr, every line printed would be a write to it.
    for fd in (0, 1, 2):
        if os.path.exists(trace):
            os.unlink(trace)
        r = subprocess.run([poke, "patch"], stdout=subprocess.PIPE if fd == 2 else None, stderr=subprocess.PIPE if fd == 1 else None,
                           preexec_fn=lambda fd=fd: os.close(fd))
        check(r.returncode == 2 and not os.path.exists(trace) and content(fake) == start,
              f"with descriptor {fd} closed, the device isn't opened in its place: exit 2, nothing read or written")
    for args in ([], ["patch", "check"], ["write"], ["restore"], ["patch", "0x2922b0024"], ["0x2922b0024"], ["patch", "0xaba95000"], ["--patch"], ["check", ""]):
        r, acc = run(poke, *args)
        check(r.returncode == 2 and acc == [] and content(fake) == start, "refuses arguments " + repr(args) + ", reading nothing", r.stdout + r.stderr)
    # check opens the device read-only: on a device that can't be opened for writing it still reads, and patch doesn't start.
    os.chmod(fake, 0o444)
    if os.access(fake, os.W_OK):
        print("note the read-only device isn't checked here: this user can write it all the same")
    else:
        r, acc = run(poke, "check")
        r2, acc2 = run(poke, "patch")
        check(r.returncode == 0 and shape(acc) == "T" and r2.returncode == 2 and acc2 == [] and content(fake) == start,
              "check opens the device read-only; patch, which can't open it for writing, reads nothing", r.stdout + r2.stderr)
    os.chmod(fake, 0o644)

    # The build that runs on the Mac has no trace and none of the test macros: the same change there, by the device's content
    # and, on an arm64 host, by its instructions.
    plain = os.path.join(tmp, "wd2_poke_plain")
    subprocess.run(["cc", "-O2", "-Wall", '-DWD2_MEM="%s"' % fake, '-DWD2_SAFE_FLAG="%s"' % flag, '-DWD2_CMDLINE="%s"' % cmdline, "-o", plain, src_path], check=True)
    start = mem()
    r, acc = run(plain, "patch")
    check(r.returncode == 0 and acc == [] and content(fake) == {**start, BITE: NEW} and TOOK in r.stdout,
          "built as for the Mac (cc -O2 -Wall, no trace): patch changes the one word", r.stdout)
    if platform.machine() == "aarch64" and shutil.which("objdump"):
        dis = subprocess.run(["objdump", "-d", "--no-show-raw-insn", plain], capture_output=True, text=True, check=True).stdout
        ins = [re.sub(r"\s+", " ", m.group(1).split("//")[0]).strip() for m in (re.match(r"\s*[0-9a-f]+:\s+(\S.*)", line) for line in dis.splitlines()) if m]
        at = [i for i, x in enumerate(ins) if x.split()[0] == "dsb"]
        check(len(at) == 1 and ins[at[0]] == "dsb sy" and re.fullmatch(r"str w\d+, \[x\d+(, #\d+)?\]", ins[at[0] - 1]) is not None,
              "that build's one dsb comes right after a 32-bit str: one store instruction, the width the kernel writes this register with",
              [ins[i - 1:i + 1] for i in at])
    else:
        print("note the store's instructions aren't checked here: not an arm64 host, or no objdump")

    # The tests' hooks can't be built into a tool that opens the real device.
    for hook in ("-DWD2_POKE_DROP=1", "-DWD2_POKE_STORE(v)=(v)", '-DWD2_TRACE="/tmp/x"'):
        r = subprocess.run(["cc", "-O2", "-Wall", hook, "-o", os.path.join(tmp, "never"), src_path], capture_output=True, text=True)
        check(r.returncode != 0 and "are for test builds, which set WD2_MEM too" in r.stderr and not os.path.exists(os.path.join(tmp, "never")),
              f"a build with {hook.split('=')[0]} and the real memory device doesn't compile", r.stderr[:300])

    # The source: one store, one address, one value, no number taken from the command line or the environment.
    src = open(src_path).read()
    hdr = open(os.path.join(E1_HOOK, "wd2_bite.h")).read()
    WR32 = '''static void wr32(uint64_t pa, uint32_t v)
{
	volatile void *p = map_word(pa, PROT_READ | PROT_WRITE);

	trace('w', pa, v);
	if (WD2_POKE_DROP)
		return;
	v = WD2_POKE_STORE(v);
#ifdef __aarch64__
	asm volatile("str %w0, [%1]\\n\\tdsb sy" : : "r"(v), "r"(p) : "memory");
#else
	*(volatile uint32_t *)p = v;
#endif
}
'''
    check(WR32 in src and src.count("wr32(") == 2 and src.count("wr32(BITE_PA, WD2_BITE_NEW);") == 1 and src.count("str %") == 1
          and src.count("PROT_WRITE") == 1 and src.count("map_word(") == 3 and src.count("mmap(") == 1 and src.count("asm volatile") == 2
          and "#define BITE_PA ((uint64_t)(WD2_WDT + WD2_BITE))" in src and "WD2_BITE_AWAKE);" not in src and "WD2_BITE_A2" not in src
          and not any(w in src for w in ("pwrite", "memcpy", "memset", "mprotect", "MAP_PRIVATE", "strtoul", "strtol", "atoi", "sscanf", "argv[2]", "getenv", "environ"))
          and len(re.findall(r"(?<![a-z_])write\(", src)) == 0 and len(re.findall(r"(?<![a-z_])open\(", src)) == 1
          and "open(WD2_MEM, patch ? O_RDWR | O_SYNC : O_RDONLY | O_SYNC)" in src,
          "wd2_poke.c has one store, in wr32, the reviewed function line for line, of 120 s to BITE_PA and nothing else (no 90 s, no 30 s); one open of"
          " the device, read-only for check; and no number from its arguments or the environment")
    check('#define WD2_MEM "/dev/mem"\n' in src and '#define WD2_SAFE_FLAG "/run/sleep-drain-safe/active"\n' in src
          and '#define WD2_CMDLINE "/proc/cmdline"\n' in src and "#define MAP_UNIT UINT64_C(0x4000)\n" in src and "sysconf" not in src
          and '_Static_assert(BITE_PA == UINT64_C(0x2922b0024) && WD2_BITE_NEW == UINT32_C(0xaba95000),' in src
          and "_Static_assert((BITE_PA & 3) == 0," in src,
          "wd2_poke.c's own memory device, safe-boot flag and command line, which every build here overrides, are the real ones; it maps by 16 KiB;"
          " the register and the value are asserted as the ones Corey OK'd")
    check("#define WD2_WDT\t\t\t0x2922b0000ULL" in hdr and "#define WD2_BITE\t\t0x24" in hdr and "#define WD2_BITE_NEW\t\t(120U * WD2_HZ)" in hdr
          and "#define WD2_BITE_AWAKE\t\t(90U * WD2_HZ)" in hdr and "#define WD2_BITE_A2\t\t(30U * WD2_HZ)" in hdr and "#define WD2_HZ\t\t\t24000000U" in hdr
          and BITE == 0x2922b0024 and NEW == 0xaba95000 and AWAKE == 0x80befc00 and A2 == 0x2aea5400,
          "wd2_bite.h: the register and its three values are the ones in PLAN.md")
    check(BITE % 4 == 0 and not {BITE & ~7, BITE} & {0x28e0802d8, 0x28e700000}, "the register is aligned, and isn't on the never-write list")

    # The launcher, as uid 0 in a user namespace.
    if shutil.which("bwrap") is None:
        print("FAIL bwrap is missing")
        failed += 1
    else:
        launcher = os.path.join(E1_HOOK, "wd2-bite")
        MAY = "WD2'S BITE MAY READ 120 s NOW"
        NOT_SAVED = "NOT SAVED: the lines above are the only record"
        NEXT = "next: wd2_poke patch, one 32-bit store from user space, 0x2922b0024 := 0xaba95000 (120 s), if the check above still holds"

        def manifest(root):
            res = {}
            for d, _, fs in os.walk(root):
                for f in fs:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        res[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
            return res

        def sandbox(*, args=(), safe=True, uid=0, check_rc=0, patch_rc=0, save_fail_at=None, tee_fails=False, old=None, records=(), term_at=None,
                    real=None, sysctl=True, date=None, mark=None, mark_path=None):
            """Runs the launcher with stubs; returns (rc, output, calls, the log root, manifests at each save).
            old: "poke" or "header", the file that's newer than the built tool. records: directories already there, as (time, step, text).
            tee_fails: True, or "write" for the write's log alone. term_at: a save's number: SIGTERM to the launcher there.
            real: the writer built for the fake device, in place of the stub. sysctl: False for no kernel.panic to write.
            date: the time the launcher's date gives. A stub that "dies of a signal" exits 139, and leaves no core dump.
            mark: what this boot's mark says already, of an earlier run. mark_path: the mark's file, to share one "boot" between two runs
            (False: a path that can't be written). sandbox.mark is what it says after."""
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
            poke_s, save = (os.path.join(root, n) for n in ("wd2_poke", "safe"))
            with open(poke_s, "w") as f:
                f.write(f"""#!/bin/bash
echo "poke $* panic=$(cat {sysdir}/panic 2>/dev/null)" >> {calls}
echo "the writer's $1"
case $1 in
check) [[ {check_rc} != 0 ]] || echo "{AS_AWAKE}"
  exit {check_rc} ;;
patch) [[ {patch_rc} != 0 ]] || echo "{TOOK}"
  exit {patch_rc} ;;
esac
exit 9
""")
            with open(save, "w") as f:
                f.write(f"""#!/bin/bash
echo "save $*" >> {calls}
n=$(ls {snaps} | wc -l)
cp -a {log_root} {snaps}/$n
{f'[[ $n != {term_at - 1} ]] || {{ kill -TERM $PPID; sleep 0.3; }}' if term_at else ''}
{f'[[ $n != {save_fail_at - 1} ]] || {{ echo "the save\'s own word: restart now" >&2; exit 1; }}' if save_fail_at else ''}
exit 0
""")
            srcs = {}
            for n in ("wd2_poke.c", "wd2_bite.h"):
                srcs[n] = os.path.join(root, n)
                open(srcs[n], "w").close()
                os.utime(srcs[n], (1000, 1000))
            for p in (poke_s, save):
                os.chmod(p, 0o755)
            os.utime(poke_s, (2000, 2000))
            if real:
                poke_s = real
            if old == "poke":
                os.utime(srcs["wd2_poke.c"], (3000, 3000))
            elif old == "header":
                os.utime(srcs["wd2_bite.h"], (3000, 3000))
            for when, step, text in records:
                d = os.path.join(log_root, f"wd2-bite-{when}-{step}")
                os.mkdir(d)
                with open(os.path.join(d, "log.txt"), "w") as f:
                    f.write(text)
            flag_s = os.path.join(root, "active")
            if safe:
                open(flag_s, "w").close()
            mark_s = os.path.join(root, "no-such-dir", "mark") if mark_path is False else mark_path or os.path.join(root, "mark")
            if mark is not None:
                with open(mark_s, "w") as f:
                    # "": a mark with nothing in it, as one whose write failed would be.
                    f.write(mark + "\n" if mark else "")
            env = ["env", "WD2_MARK=" + mark_s, "WD2_POKE=" + poke_s, "WD2_POKE_SRC=" + srcs["wd2_poke.c"], "WD2_HEADER=" + srcs["wd2_bite.h"], "WD2_SAFE_FLAG=" + flag_s,
                   "WD2_SAVE=" + save, "WD2_LOG_ROOT=" + log_root, "WD2_SYSCTL=" + sysdir]
            if real:
                # The real writer is newer than the real sources it was just built from.
                env[3:5] = ["WD2_POKE_SRC=" + src_path, "WD2_HEADER=" + os.path.join(E1_HOOK, "wd2_bite.h")]
            if tee_fails or date:
                bindir = os.path.join(root, "bin")
                os.mkdir(bindir)
                if date:
                    with open(os.path.join(bindir, "date"), "w") as f:
                        f.write(f"#!/bin/bash\necho {date}\n")
                    os.chmod(os.path.join(bindir, "date"), 0o755)
                if tee_fails:
                    p = os.path.join(bindir, "tee")
                    with open(p, "w") as f:
                        # "write": only the write's log can't be written, as when the overlay fills between the two steps.
                        only = 'case "$*" in *-write/log.txt) ;; *) exec /usr/bin/tee "$@" ;; esac\n' if tee_fails == "write" else ""
                        f.write("#!/bin/bash\n" + only + "cat > /dev/null\nexit 1\n")
                    os.chmod(p, 0o755)
                env.append("PATH=" + bindir + ":" + os.environ["PATH"])
            r = subprocess.run(["bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-user", "--uid", str(uid), "--gid", "0", "--"]
                               + env + [launcher] + list(args), capture_output=True, text=True)
            lines = open(calls).read().split("\n")[:-1] if os.path.exists(calls) else []
            manifests = [manifest(os.path.join(snaps, n)) for n in sorted(os.listdir(snaps), key=int)]
            sandbox.panic = open(os.path.join(sysdir, "panic")).read().strip() if sysctl else None
            sandbox.mark = open(mark_s).read().strip() if os.path.exists(mark_s) else None
            return r.returncode, r.stdout + r.stderr, lines, log_root, manifests

        def steps(logs):
            return sorted(d.rsplit("-", 1)[1] for d in os.listdir(logs))

        FULL = ["poke check panic=0", "save save", "poke patch panic=10", "save save"]
        rc, out, calls, logs, snaps = sandbox()
        check(rc == 0 and calls == FULL and "done: WD2's bite reads 120 s after this store, so a store from user space changes a register here" in out and MAY not in out
              and sandbox.panic == "10" and sandbox.mark == "took",
              "wd2-bite: the check, saved, then kernel.panic and the write, saved; exit 0, and this boot's mark says the store took", (rc, calls, out, sandbox.mark))
        check(steps(logs) == ["check", "write"], "each step has a directory of its own", os.listdir(logs))
        check(len(snaps) == 2 and all("-write/" not in f for f in snaps[0]) and any("-write/log.txt" in f for f in snaps[1])
              and snaps[0].items() <= snaps[1].items(),
              "the first save holds the check alone, and nothing in it changes after", [sorted(s) for s in snaps])
        cdir = [d for d in os.listdir(logs) if d.endswith("-check")][0]
        text = open(os.path.join(logs, cdir, "log.txt")).read()
        check("the writer's check" in text and AS_AWAKE in text and text.rstrip().endswith(NEXT),
              "the check's saved log ends with the line that says the write comes next, with its register and value", text)
        wdir = [d for d in os.listdir(logs) if d.endswith("-write")][0]
        check("the writer's patch" in open(os.path.join(logs, wdir, "log.txt")).read(), "the write's log holds the writer's output")

        # Stops before the writer runs: nothing written. Exit 3, WD2 isn't as it is awake; 4, before anything is read, for what can be put right
        # in this boot; 1 for the rest, which run-floor answers with "restart the Mac".
        rc, out, calls, logs, snaps = sandbox(check_rc=3)
        check(rc == 3 and calls == FULL[:2] and "WD2 isn't as it is awake (above): nothing written. That's a result." in out and steps(logs) == ["check"]
              and MAY not in out and NOT_SAVED not in out,
              "a check that finds WD2 not as it is awake: exit 3, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(check_rc=3, save_fail_at=1)
        check(rc == 3 and NOT_SAVED in out and "the save's own word: restart now" in out, "and it says so, with the save's own message, when that record couldn't be saved", (rc, out))
        for bad in (1, 2, 135, 139):
            rc, out, calls, logs, snaps = sandbox(check_rc=bad)
            check(rc == 1 and calls == FULL[:2] and f"the check failed (exit {bad}); nothing written" in out and MAY not in out,
                  f"a check that fails ({bad}): exit 1, saved, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=1)
        check(rc == 1 and calls == FULL[:2] and "couldn't save the check (sleep-drain-safe's own message is above); nothing written. Restart the Mac" in out
              and "the save's own word: restart now" in out and MAY not in out,
              "a check that can't be saved: exit 1, not the exit that lets the run be made again, the save's own message shown, and no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(tee_fails=True)
        check(rc == 1 and not [c for c in calls if c.startswith("poke patch")] and "couldn't write" in out and MAY not in out,
              "a check whose log can't be written: exit 1, no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(sysctl=False)
        check(rc == 1 and [c.split(" panic=")[0] for c in calls] == ["poke check", "save save"] and "couldn't set kernel.panic; nothing written" in out,
              "kernel.panic can't be set: exit 1, no write", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(term_at=1)
        check(rc == 143 and calls == FULL[:2] and MAY not in out, "SIGTERM during the check's save: no write, and nothing said about the register", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(date="20261110T230000", records=(("20261110T230000", "check", "another run's\n"),))
        check(rc == 4 and calls == [] and "couldn't make" in out and open(os.path.join(logs, "wd2-bite-20261110T230000-check", "log.txt")).read() == "another run's\n",
              "a directory that's there already (another run, the same second) isn't written into: exit 4, nothing read", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(date="20261110T230000", records=(("20261110T230000", "write", "another run's\n"),))
        check(rc == 4 and calls == FULL[:2] and "couldn't make" in out and MAY not in out
              and open(os.path.join(logs, "wd2-bite-20261110T230000-write", "log.txt")).read() == "another run's\n",
              "a write directory that's there already: exit 4 after the check, the writer never runs, and nothing is said about the register", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(safe=False)
        check(rc == 4 and "safe boot only" in out and calls == [], "refuses outside a safe boot (exit 4)", out)
        rc, out, calls, logs, snaps = sandbox(uid=1000)
        check(rc == 4 and "as root" in out and calls == [], "refuses to run as non-root (exit 4)", out)
        for old in ("poke", "header"):
            rc, out, calls, logs, snaps = sandbox(old=old)
            check(rc == 4 and "older than its source" in out and calls == [], f"refuses a tool older than its source or the header ({old})", out)
        for args in (("--more",), ("--restore",), ("--after",), ("patch",), ("check", "patch")):
            rc, out, calls, logs, snaps = sandbox(args=args)
            check(rc == 1 and "usage" in out and calls == [], "refuses arguments " + repr(args), out)

        # From the writer's start on. Nothing here stores again.
        rc, out, calls, logs, snaps = sandbox(patch_rc=5)
        check(rc == 5 and calls == FULL and "THE WRITE DIDN'T TAKE: a store from user space didn't change this register. That's a result" in out
              and MAY not in out and NOT_SAVED not in out and "done:" not in out and sandbox.mark == "dropped",
              "a write that didn't take: exit 5, saved, said as a result, no second store, and this boot's mark says it was dropped", (rc, calls, out, sandbox.mark))
        # run-floor goes on after exit 5, so a result that couldn't be saved must not be one: the save may have left the boot partition mounted.
        rc, out, calls, logs, snaps = sandbox(patch_rc=5, save_fail_at=2)
        check(rc == 1 and calls == FULL and "THE WRITE DIDN'T TAKE: a store from user space didn't change this register" in out
              and "that result's record couldn't be saved (sleep-drain-safe's own message is above): photograph these lines, and restart the Mac" in out
              and "the save's own word: restart now" in out and MAY not in out,
              "a write that didn't take and whose record can't be saved: exit 1, never 5, with the save's own message shown", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=2)
        check(rc == 1 and "the save's own word: restart now" in out, "a save that fails after a write that took shows its own message too", (rc, out))
        rc, out, calls, logs, snaps = sandbox(patch_rc=3)
        check(rc == 3 and calls == FULL and "WD2 changed between the check and the write: nothing written." in out and MAY not in out,
              "a writer that refuses: exit 3, saved; nothing was written", (rc, calls, out))
        for bad in (6, 2, 9, 139):
            rc, out, calls, logs, snaps = sandbox(patch_rc=bad)
            check(rc == 1 and calls == FULL and f"wd2_poke failed (exit {bad})" in out and MAY in out and "done:" not in out and sandbox.mark == "started",
                  f"a writer that fails, or finds WD2 neither way after its write ({bad}): exit 1, saved, it says the bite may read 120 s, and the mark stays"
                  " at started", (rc, calls, out, sandbox.mark))
        rc, out, calls, logs, snaps = sandbox(patch_rc=7)
        check(rc == 1 and calls == FULL and "WD2 changed between the check and the write: nothing written" in out and "done:" not in out and MAY not in out,
              "a writer that finds 120 s there itself: nothing written, exit 1, never the exit of a store that took", (rc, calls, out))

        # One store from user space in a boot: a second run checks, saves, and gives the first one's result, without storing.
        ONE = FULL[:2]
        rc, out, calls, logs, snaps = sandbox(mark="took", check_rc=7)
        check(rc == 0 and calls == ONE and steps(logs) == ["check"] and "this boot's one store was made earlier and took: the bite reads 120 s. Nothing is written now" in out
              and "next: wd2_poke patch" not in out and sandbox.mark == "took" and sandbox.panic == "0",
              "run again after a store that took: the check, saved, no store, exit 0", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(mark="dropped", check_rc=0)
        check(rc == 5 and calls == ONE and steps(logs) == ["check"] and "this boot's one store was made earlier and didn't take: the bite reads 90 s. It isn't made again (the OK is for one)" in out
              and "next: wd2_poke patch" not in out and sandbox.mark == "dropped",
              "run again after a store that was dropped: the check, saved, exit 5, and no second store", (rc, calls, out))
        text = open(os.path.join(logs, os.listdir(logs)[0], "log.txt")).read()
        check("It isn't made again (the OK is for one)" in text, "and the saved check says so too", text)
        rc, out, calls, logs, snaps = sandbox(check_rc=7)
        check(rc == 7 and calls == ONE and "WD2's bite reads 120 s, and no store was made in this boot" in out and "the control isn't made" in out
              and "took" not in out and sandbox.mark is None,
              "a bite of 120 s with no store made in this boot: exit 7, saved, nothing written, and never called a store that took", (rc, calls, out))
        for mark, check_rc in (("took", 0), ("dropped", 7), ("started", 0), ("started", 7), ("", 0), ("something else", 7)):
            rc, out, calls, logs, snaps = sandbox(mark=mark, check_rc=check_rc)
            check(rc == 1 and calls == ONE and "WD2 doesn't read as that store left it. Nothing is written now. Restart the Mac" in out and "next: wd2_poke patch" not in out,
                  f"a mark of {mark!r} with a check exit of {check_rc}: not what that store left: exit 1, no store", (rc, calls, out))
        for mark, check_rc in (("took", 7), ("dropped", 0), (None, 7)):
            rc, out, calls, logs, snaps = sandbox(mark=mark, check_rc=check_rc, save_fail_at=1)
            check(rc == 1 and calls == ONE and "couldn't save that (sleep-drain-safe's own message is above); nothing written. Restart the Mac" in out
                  and "the save's own word: restart now" in out,
                  f"the same three ways out (mark {mark!r}) with a save that fails: exit 1, never the exits run-floor goes on after", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(mark_path=False)
        check(rc == 1 and calls == FULL[:2] and "couldn't write" in out and "nothing written" in out and MAY not in out,
              "a mark that can't be left: exit 1 before the store, which isn't made", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(save_fail_at=2)
        check(rc == 1 and calls == FULL and "couldn't save the write's record" in out and MAY in out,
              "a write whose record can't be saved: exit 1, and it says the bite may read 120 s", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(tee_fails="write")
        check(rc == 1 and calls == FULL and "couldn't write" in out and MAY in out,
              "a write whose log can't be written: exit 1, and it says the bite may read 120 s", (rc, calls, out))
        rc, out, calls, logs, snaps = sandbox(term_at=2)
        check(rc == 143 and calls == FULL and MAY in out and "done:" not in out,
              "SIGTERM during the write's save: exit 143, and it says the bite may read 120 s", (rc, calls, out))
        ltext = open(launcher).read()
        check(sorted(re.findall(r'run "\$POKE" (\w+)', ltext)) == ["check", "patch"] and not re.search(r'^\s*"\$POKE"', ltext, re.M) and ltext.count('"$POKE"') == 2
              and ltext.index('run "$POKE" check') < ltext.index('echo 10 > "$SYSCTL/panic"') < ltext.index('run "$POKE" patch'),
              "the launcher runs the writer in two places only, check and then patch, with kernel.panic set between them")
        check('SAFE_FLAG=${WD2_SAFE_FLAG:-/run/sleep-drain-safe/active}\n' in ltext and 'SAVE=${WD2_SAVE:-/usr/local/bin/sleep-drain-safe}\n' in ltext
              and 'LOG_ROOT=${WD2_LOG_ROOT:-/var/log/sleep-drain}\n' in ltext and 'SYSCTL=${WD2_SYSCTL:-/proc/sys/kernel}\n' in ltext
              and 'POKE=${WD2_POKE:-$HERE/wd2_poke}\n' in ltext and 'HEADER=${WD2_HEADER:-$HERE/wd2_bite.h}\n' in ltext,
              "the launcher's own paths, which every case here overrides, are the real ones")

        # The real writer under the launcher, on the fake device: the pieces fit each other.
        before = mem()
        rc, out, calls, logs, snaps = sandbox(real=poke)
        check(rc == 0 and calls == ["save save", "save save"] and content(fake) == {**before, BITE: NEW} and "done: WD2's bite reads 120 s" in out,
              "the launcher with the real writer: the one word changed on the device, two saves, exit 0", (rc, calls, out))
        cdir, wdir = (os.path.join(logs, [d for d in os.listdir(logs) if d.endswith(e)][0], "log.txt") for e in ("-check", "-write"))
        ctext, wtext = open(cdir).read(), open(wdir).read()
        check(ctext.count("reading 0x") == 3 and AS_AWAKE + "\n" in ctext and "writing" not in ctext and NEXT in ctext
              and WRITING in wtext and TOOK + "\n" in wtext and wtext.count("reading 0x") == 6,
              "its two logs: the check's three reads and verdict, then the writer's own lines", ctext + wtext)
        boot = os.path.join(tmp, "boot-mark")
        before = mem()
        rc, out, calls, logs, snaps = sandbox(real=poke, mark_path=boot)
        if os.path.exists(trace):
            os.unlink(trace)
        rc2, out2, calls2, logs2, snaps2 = sandbox(real=poke, mark_path=boot)
        t = open(trace).read() if os.path.exists(trace) else ""
        check(rc == 0 and rc2 == 0 and content(fake) == {**before, BITE: NEW} and "this boot's one store was made earlier and took" in out2
              and calls2 == ["save save"] and "w 0x" not in t and t.count("r 0x") == 3,
              "the real writer, run again in the same boot after a store that took: three reads, saved, exit 0, and no store", (rc, rc2, out2, t))
        rc, out, calls, logs, snaps = sandbox(real=poke)
        check(rc == 7 and content(fake) == {**before, BITE: NEW} and "no store was made in this boot" in out and "done:" not in out,
              "the real writer in another boot that finds 120 s there: exit 7, nothing written, and not called a store that took", (rc, out))
        os.unlink(boot)
        start = mem()
        rc, out, calls, logs, snaps = sandbox(real=dropped, mark_path=boot)
        check(rc == 5 and content(fake) == start and "THE WRITE DIDN'T TAKE: the bite still reads 90 s" in out
              and "THE WRITE DIDN'T TAKE: a store from user space didn't change this register" in out and MAY not in out,
              "the real writer on a register that drops the store: exit 5, said by the writer and the launcher, and the device unchanged", (rc, out))
        os.unlink(trace)
        rc, out, calls, logs, snaps = sandbox(real=dropped, mark_path=boot)
        t = open(trace).read() if os.path.exists(trace) else ""
        check(rc == 5 and content(fake) == start and "It isn't made again (the OK is for one)" in out and "w 0x" not in t and t.count("r 0x") == 3,
              "run again in the same boot after a store that was dropped: three reads, no second store, exit 5 again", (rc, out, t))
        start = mem(ctrl=4, bite=A2)
        rc, out, calls, logs, snaps = sandbox(real=poke)
        check(rc == 3 and content(fake) == start and calls == ["save save"] and not [d for d in os.listdir(logs) if d.endswith("-write")],
              "the real writer on a WD2 that's armed: exit 3 at the check, saved, and the writer's patch never runs", (rc, calls, out))

print("\n%d failures" % failed)
sys.exit(1 if failed else 0)
