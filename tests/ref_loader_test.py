#!/usr/bin/env python3
"""ref-loader, without root, a boot partition or a loader: the recipe, install, restore, status and recovery-notes.

The script runs as it stands, with its paths pointed into a scratch directory through its REFL_* variables: a
directory stands in for the boot partition, made-up files for m1n1.bin, U-Boot and the device trees, and a made-up
/etc/default/update-m1n1 names them. Commands that need root run as uid 0 in a bubblewrap user namespace. findmnt, df,
mount, umount, mountpoint, lsblk and findfs are stubs on PATH; cp, mv and sync are wrappers that log their call, run
the real one, and can be told to fail or to damage what they wrote, for the cases where a write doesn't read back.
dd is a wrapper too: the real one, unless told that the read from the device fails. gzip, sha256sum and the rest are
the real ones. The scratch directory has to take O_DIRECT reads, as the boot partition does: the tool confirms
nothing without one, so where it can't (the test says so first) nothing here could pass, and nothing is run.

The recipe is held against two things: the bundle's bytes worked out here piece by piece, and, where
/usr/bin/update-m1n1 is installed, that script itself run on the same made-up inputs in a sandbox.

    python3 -I -B tests/ref_loader_test.py
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.realpath(os.path.join(HERE, "..", "ref-loader"))
UPDATE_M1N1 = "/usr/bin/update-m1n1"
MARGIN = 16 * 1048576
fails = 0


def check(ok, what, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails += 1
        if detail:
            print("     " + str(detail).replace("\n", "\n     "))


def write(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    os.chmod(path, mode)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dtb(payload):
    """A file with a device tree blob's first two header words: the magic and the total size."""
    return struct.pack(">II", 0xd00dfeed, 8 + len(payload)) + payload


def stub(t, name, body):
    write(os.path.join(t, "bin", name), "#!/bin/bash\n" + body, 0o755)


# The stock inputs. One device tree of each set names this Mac, as the scratch /proc/device-tree/compatible has it. The device trees' names sort differently in the C locale (capitals first, - before _) than in a
# language's; the stock list has its own order, neither of the two.
STOCK_DTBS = (("t6000-j314s.dtb", b"apple,j314s\0apple,t6000\0" + b"stock tree one" * 50), ("B.dtb", b"stock tree two" * 31), ("a_b.dtb", b"three" * 7), ("a-b.dtb", b"four" * 90))
# The reference set's tree for this Mac is the first of the bundle, with 4 MB of other trees after it, in lines: more than a pipe holds, so a reader that stops at the first match breaks the writer.
REF_DTBS = (("b.dtb", b"ref tree b" * 40), ("B.dtb", b"apple,j314s\0apple,t6000\0" + b"ref tree B" * 12), ("a_b.dtb", b"ref a_b" * 9), ("a-b.dtb", b"ref a-b" * 3),
            ("Z.dtb", b"ref Z\n" * 700000))
CONFIG = ("# a comment\n\nchosen.bootargs=foo bar\ndisplay=wait\nunknown=1\nmitigations=off\n   chosen.padded=1   \n#chosen.off=1\nchosenx=1\n")
ACCEPTED = b"chosen.bootargs=foo bar\ndisplay=wait\nmitigations=off\nchosen.padded=1\n"


def setup(t, config=CONFIG, safe=False):
    for sub in ("boot/m1n1", "state", "bin", "calls", "run", "stock/dtbs", "ref/dtbs", "sys-block/fake0p4"):
        os.makedirs(os.path.join(t, sub))
    write(os.path.join(t, "stock/m1n1.bin"), b"STOCK M1N1 " + bytes(range(256)) * 20)
    write(os.path.join(t, "stock/u-boot-nodtb.bin"), b"U-BOOT " + bytes(range(255, -1, -1)) * 300)
    os.utime(os.path.join(t, "stock/u-boot-nodtb.bin"), (1757412000, 1757412000))
    for name, payload in STOCK_DTBS:
        write(os.path.join(t, "stock/dtbs", name), dtb(payload))
    write(os.path.join(t, "ref/m1n1.bin"), b"REFERENCE M1N1 " + bytes(range(256)) * 33)
    for name, payload in REF_DTBS:
        write(os.path.join(t, "ref/dtbs", name), dtb(payload))
    if config is not None:
        write(os.path.join(t, "m1n1.conf"), config)
    # As this Mac's /etc/default/update-m1n1 does it: DTBS from a command, one path a line.
    write(os.path.join(t, "stock.list"), "".join(f"{t}/stock/dtbs/{n}\n" for n, _ in STOCK_DTBS))
    write(os.path.join(t, "defaults"), f"SOURCE={t}/stock/\nCONFIG={t}/m1n1.conf\nDTBS=$(cat {t}/stock.list; true)\n")
    write(os.path.join(t, "proc-cmdline"), "root=UUID=aaaa rw quiet\n")
    write(os.path.join(t, "compatible"), b"apple,j314s\0apple,t6000\0apple,arm-platform\0")
    write(os.path.join(t, "sys-block/fake0p4/partition"), "4\n")
    write(os.path.join(t, "calls/fstype"), "vfat\n")
    write(os.path.join(t, "calls/source"), "/dev/fake0p4\n")
    write(os.path.join(t, "calls/avail"), str(200 * 1048576) + "\n")
    c = f"{t}/calls"
    stub(t, "findmnt", f'''case " $* " in
  *" FSTYPE "*) [[ -f {c}/fstype ]] && cat {c}/fstype ;;
  *" SOURCE "*) [[ -f {c}/source ]] && cat {c}/source ;;
  *) exit 1 ;;
esac
''')
    stub(t, "df", f"echo Avail; cat {c}/avail\n")
    # mount: the scratch boot partition appears at the mount point; umount takes it away again.
    stub(t, "mount", f'''echo "mount $*" >> {c}/log
[[ ! -e {c}/fail-mount ]] || exit 32
mnt=${{@: -1}}
rmdir "$mnt" && ln -s {t}/boot "$mnt"
''')
    stub(t, "umount", f'echo "umount $*" >> {c}/log\n[[ ! -e {c}/fail-umount ]] || exit 32\nrm "$1" && mkdir "$1"\n')
    stub(t, "mountpoint", f"[[ -e {c}/mounted ]]\n")
    stub(t, "findfs", f'echo "findfs $*" >> {c}/log; echo /dev/fake0p4\n')
    stub(t, "lsblk", f'''[[ ! -e {c}/fail-lsblk ]] || exit 1
case "$2:$3" in
  PARTUUID:/dev/fake0p4) echo 0f1e2d3c-aaaa-bbbb-cccc-0123456789ab ;;
  UUID:/dev/fake0p4) echo 553C-45DC ;;
  LABEL:/dev/fake0p4) echo "EFI - TEST" ;;
  SIZE:/dev/fake0p4) echo " 500M" ;;
  FSTYPE:/dev/fake0p4) echo vfat ;;
  PKNAME:/dev/fake0p4) echo fake0 ;;
  SIZE:/dev/fake0) echo "465.9G" ;;
  *) exit 1 ;;
esac
''')
    # The wrappers: the call's last argument, by its base name, is what a fault file names (a glob).
    hit = f'''dst=${{@: -1}}
hit() {{ [[ -f {c}/$1 ]] && [[ ${{dst##*/}} == $(cat {c}/$1) ]]; }}
echo "{{name}} ${{dst##*/}}" >> {c}/log
'''
    stub(t, "cp", hit.replace("{name}", "cp") + f'/usr/bin/cp "$@" || exit\nif hit bad-cp; then echo damage >> "$dst"; fi\n')
    stub(t, "mv", hit.replace("{name}", "mv") + f'''hit fail-mv && exit 1
/usr/bin/mv "$@" || exit
touch {c}/moved-${{dst##*/}}
if hit bad-mv; then echo damage >> "$dst"; fi
if hit bad-mv-once; then echo damage >> "$dst"; rm {c}/bad-mv-once; fi
exit 0
''')
    # sync: a fault file names one call by its last argument, as above; calls/fail-syncfs fails every sync of the
    # filesystem (sync -f), and calls/fail-syncfs-after-mv every one made once boot.bin has been renamed over.
    stub(t, "sync", hit.replace("{name}", 'sync$([[ $1 == -f ]] && echo " -f")') + f'''hit fail-sync && exit 1
if [[ $1 == -f ]]; then
  [[ ! -e {c}/fail-syncfs ]] || exit 1
  [[ ! -e {c}/fail-syncfs-after-mv || ! -e {c}/moved-boot.bin ]] || exit 1
fi
exec /usr/bin/sync "$@"
''')
    # dd, the read from the device. calls/no-direct: no direct read at all, as on a filesystem without O_DIRECT.
    # calls/direct-eio names a file (a glob, by its base name) whose read delivers every byte and then fails, as a
    # device error at the end would; calls/direct-eio-after-mv fails boot.bin's once it has been renamed over.
    stub(t, "dd", f'''src=
for a in "$@"; do [[ $a != if=* ]] || src=${{a#if=}}; done
eio() {{ echo "dd: error reading '$src': Input/output error" >&2; exit 1; }}
[[ ! -e {c}/no-direct ]] || {{ echo "dd: failed to open '$src': Invalid argument" >&2; exit 1; }}
if [[ -f {c}/direct-eio && ${{src##*/}} == $(cat {c}/direct-eio) ]]; then /usr/bin/dd "$@"; eio; fi
if [[ -e {c}/direct-eio-after-mv && -e {c}/moved-boot.bin && ${{src##*/}} == boot.bin ]]; then eio; fi
exec /usr/bin/dd "$@"
''')
    if safe:
        write(os.path.join(t, "run/active"), "/dev/mapper/root\n")
        write(os.path.join(t, "run/lower/root/etc/fstab"), "UUID=725346d2 / btrfs rw,subvol=@ 0 0\n# UUID=dead /boot vfat rw 0 2\nUUID=553C-45DC /boot vfat rw,relatime 0 2\n")
    write(os.path.join(t, "boot/m1n1/boot.bin"), stock_bundle(t))


def gz(path):
    """gzip -c of a file, as update-m1n1 calls it."""
    return subprocess.run(["gzip", "-c", path], check=True, capture_output=True).stdout


def stock_bundle(t, accepted=None):
    conf = os.path.join(t, "m1n1.conf")
    if accepted is None:
        accepted = ACCEPTED if os.path.exists(conf) and open(conf).read() == CONFIG else b""
    return (read(os.path.join(t, "stock/m1n1.bin")) + b"".join(read(os.path.join(t, "stock/dtbs", n)) for n, _ in STOCK_DTBS)
            + gz(os.path.join(t, "stock/u-boot-nodtb.bin")) + accepted)


def ref_bundle(t, order=None):
    """The reference bundle: the reference m1n1 and device trees (sorted as bytes: the C locale), the stock U-Boot and configuration."""
    names = order or sorted((n for n, _ in REF_DTBS), key=lambda n: n.encode())
    return (read(os.path.join(t, "ref/m1n1.bin")) + b"".join(read(os.path.join(t, "ref/dtbs", n)) for n in names)
            + gz(os.path.join(t, "stock/u-boot-nodtb.bin")) + ACCEPTED)


def run(t, *args, root=True, env=None):
    e = {"PATH": os.path.join(t, "bin") + ":/usr/bin", "REFL_BOOT": f"{t}/boot", "REFL_STATE": f"{t}/state", "REFL_DEFAULTS": f"{t}/defaults",
         "REFL_RUN": f"{t}/run", "REFL_PROC_CMDLINE": f"{t}/proc-cmdline", "REFL_SYS_BLOCK": f"{t}/sys-block", "REFL_COMPATIBLE": f"{t}/compatible", "LANG": "en_US.UTF-8"}
    e.update(env or {})
    cmd = [TOOL, *args]
    if root:
        cmd = ["bwrap", "--unshare-user", "--uid", "0", "--gid", "0", "--dev-bind", "/", "/"] + cmd
    return subprocess.run(cmd, capture_output=True, text=True, env=e)


CONFIRM = "sync -f m1n1"


def log(t, confirm=False):
    """The wrappers' and stubs' calls. The sync of the filesystem that precedes every reading of the live boot.bin (through
    the loader's directory, so it shows as CONFIRM) writes nothing, and is left out unless asked for."""
    p = os.path.join(t, "calls/log")
    return [l for l in (open(p).read().splitlines() if os.path.exists(p) else []) if confirm or l != CONFIRM]


def clear_log(t):
    p = os.path.join(t, "calls/log")
    if os.path.exists(p):
        os.remove(p)


def esp(t):
    """The boot partition's m1n1 directory: every name with a hash of its contents."""
    d = os.path.join(t, "boot/m1n1")
    return {n: sha(read(os.path.join(d, n))) for n in sorted(os.listdir(d))}


def records(t):
    d = os.path.join(t, "state")
    return {n: open(os.path.join(d, n)).read() for n in sorted(os.listdir(d))}


def flag(t, name, value=""):
    write(os.path.join(t, "calls", name), value)


def unflag(t, name):
    p = os.path.join(t, "calls", name)
    if os.path.exists(p):
        os.remove(p)


def make_ref(t):
    p = os.path.join(t, "ref.bundle")
    write(p, ref_bundle(t))
    return p


def recipe():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        out = os.path.join(t, "out.bin")
        p = run(t, "build", f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs", out, root=False)
        got = read(out) if os.path.exists(out) else b""
        m, u = read(f"{t}/ref/m1n1.bin"), read(f"{t}/stock/u-boot-nodtb.bin")
        names = ["B.dtb", "Z.dtb", "a-b.dtb", "a_b.dtb", "b.dtb"]
        trees = b"".join(read(f"{t}/ref/dtbs/{n}") for n in names)
        rest = got[len(m) + len(trees):]
        d = zlib.decompressobj(wbits=31)
        plain = d.decompress(rest) if rest else b""
        check(p.returncode == 0 and got.startswith(m) and got[len(m):len(m) + len(trees)] == trees,
              "build from a directory, without root, in a language's locale: the bundle starts with m1n1.bin, then every *.dtb in the C locale's order (B, Z, a-b, a_b, b)",
              p.stdout + p.stderr)
        check(plain == u and d.eof and d.unused_data == ACCEPTED and rest[:4] == b"\x1f\x8b\x08\x08"
              and struct.unpack("<I", rest[4:8])[0] == 1757412000 and rest[10:27] == b"u-boot-nodtb.bin\0",
              "    then one gzip member that unpacks to U-Boot, its header naming u-boot-nodtb.bin and carrying that file's time, as gzip -c of the installed"
              " file gives; then the configuration's accepted lines and nothing else (comments, empty and unknown lines left out, padding stripped)",
              repr(rest[:30]) + repr(d.unused_data))
        check(got == ref_bundle(t) and f"sha256 {sha(got)} ({len(got)} bytes)" in p.stdout and "5 device trees (B.dtb first, b.dtb last)" in p.stdout
              and not os.path.exists(out + ".ref-loader.new"),
              "    the whole file is those pieces and no more; build prints its sha256 and size; no temporary file is left", p.stdout)
        # A list file: its order, whatever it is; empty lines skipped; a last line without its newline.
        order = ["b.dtb", "B.dtb", "Z.dtb"]
        write(os.path.join(t, "ref.list"), f"{t}/ref/dtbs/b.dtb\n\n{t}/ref/dtbs/B.dtb\n{t}/ref/dtbs/Z.dtb")
        p = run(t, "build", f"{t}/ref/m1n1.bin", f"{t}/ref.list", out, root=False)
        check(p.returncode == 0 and read(out) == ref_bundle(t, order) and "3 device trees (b.dtb first, Z.dtb last)" in p.stdout,
              "build from a list file: the paths in the file's own order, three of the five", p.stdout + p.stderr)
        # The stock inputs through build give the stock bundle: the recipe's two uses are one.
        p = run(t, "build", f"{t}/stock/m1n1.bin", f"{t}/stock.list", out, root=False)
        check(p.returncode == 0 and read(out) == stock_bundle(t) == read(f"{t}/boot/m1n1/boot.bin"), "build from the stock m1n1.bin and the stock list: the stock bundle, byte for byte", p.stdout + p.stderr)
        os.remove(out)
        write(os.path.join(t, "empty.bin"), b"")
        write(os.path.join(t, "bad/magic.dtb"), b"\xd0\x0d\xfe\xee" + struct.pack(">I", 12) + b"abcd")
        write(os.path.join(t, "short/size.dtb"), dtb(b"abcdef")[:-1])
        write(os.path.join(t, "long/size.dtb"), dtb(b"abcdef") + b"x")
        write(os.path.join(t, "tiny/t.dtb"), b"\xd0\x0d")
        write(os.path.join(t, "missing.list"), f"{t}/ref/dtbs/b.dtb\n{t}/ref/dtbs/nothere.dtb\n")
        write(os.path.join(t, "blank.list"), "\n\n")
        write(os.path.join(t, "noboard.list"), "".join(f"{t}/ref/dtbs/{n}\n" for n, _ in REF_DTBS if n != "B.dtb"))
        os.makedirs(os.path.join(t, "nodtb"))
        os.makedirs(os.path.join(t, "adir"))
        before = esp(t)
        for what, args, why in (
                ("no m1n1.bin", [f"{t}/nothere.bin", f"{t}/ref/dtbs", out], "no m1n1.bin to read"),
                ("an empty m1n1.bin", [f"{t}/empty.bin", f"{t}/ref/dtbs", out], "no m1n1.bin to read"),
                ("a directory with no *.dtb", [f"{t}/ref/m1n1.bin", f"{t}/nodtb", out], "no *.dtb in"),
                ("neither a directory nor a file", [f"{t}/ref/m1n1.bin", f"{t}/nothere", out], "neither a directory of *.dtb nor a list"),
                ("a list naming a file that isn't there", [f"{t}/ref/m1n1.bin", f"{t}/missing.list", out], "not a readable file"),
                ("a list with no path", [f"{t}/ref/m1n1.bin", f"{t}/blank.list", out], "no path in the list"),
                ("a .dtb that doesn't start with the magic", [f"{t}/ref/m1n1.bin", f"{t}/bad", out], "doesn't start with d00dfeed"),
                ("a .dtb of two bytes", [f"{t}/ref/m1n1.bin", f"{t}/tiny", out], "doesn't start with d00dfeed"),
                ("a .dtb one byte shorter than its header says", [f"{t}/ref/m1n1.bin", f"{t}/short", out], "whose header says 14 bytes in a file of 13"),
                ("a .dtb one byte longer than its header says", [f"{t}/ref/m1n1.bin", f"{t}/long", out], "whose header says 14 bytes in a file of 15"),
                ("four good device trees none of which names this Mac", [f"{t}/ref/m1n1.bin", f"{t}/noboard.list", out], "none of the 4 device trees names this Mac (apple,j314s)"),
                ("an output that is a directory", [f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs", f"{t}/adir"], "is a directory"),
                ("an output in the boot partition's m1n1 directory", [f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs", f"{t}/boot/m1n1/boot.bin"], "not into"),
                ("an output beside the live file", [f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs", f"{t}/boot/m1n1/new.bin"], "not into"),
                ("two arguments", [f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs"], "usage: ref-loader build"),
                ("four arguments", [f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs", out, "x"], "usage: ref-loader build")):
            p = run(t, "build", *args, root=False)
            check(p.returncode != 0 and why in p.stderr and not os.path.exists(out) and esp(t) == before and "built" not in p.stdout,
                  f"build with {what}: refused, no bundle written", p.stdout + p.stderr)
        # Where this Mac's own name can't be read, that check isn't made, and build says so.
        p = run(t, "build", f"{t}/ref/m1n1.bin", f"{t}/noboard.list", out, root=False, env={"REFL_COMPATIBLE": f"{t}/nothere"})
        check(p.returncode == 0 and "was not checked" in p.stderr and os.path.exists(out) and "names this Mac" not in p.stdout,
              "build where the running device tree's compatible can't be read: built, with a note that the device trees weren't checked for this Mac", p.stdout + p.stderr)
        os.remove(out)
        p = run(t, "build", f"{t}/ref/m1n1.bin", f"{t}/ref/dtbs", out, root=False)
        check(p.returncode == 0 and "one of the device trees names this Mac (apple,j314s)" in p.stdout, "    and where it can: build says one of them names this Mac", p.stdout + p.stderr)
        os.remove(out)
        # verify-recipe
        p = run(t, "verify-recipe", root=False)
        s = sha(stock_bundle(t))
        check(p.returncode == 0 and "match: the live boot.bin is exactly what the recipe builds" in p.stdout and p.stdout.count(f"sha256 {s} ({len(stock_bundle(t))} bytes)") == 2
              and "4 device trees" in p.stdout and "4 configuration lines" in p.stdout and esp(t) == before and records(t) == {},
              "verify-recipe, without root: the stock bundle rebuilt from the defaults file's inputs is the live boot.bin; both sha256 printed; nothing written", p.stdout + p.stderr)
        live = os.path.join(t, "boot/m1n1/boot.bin")
        good = read(live)
        m_len = len(read(f"{t}/stock/m1n1.bin"))
        first = len(read(f"{t}/stock/dtbs/t6000-j314s.dtb"))
        trees_len = sum(len(read(f"{t}/stock/dtbs/{n}")) for n, _ in STOCK_DTBS)
        for what, at, piece in (("in m1n1.bin", 5, "/m1n1.bin\n"), ("in the second device tree", m_len + first + 3, "/stock/dtbs/B.dtb\n"),
                                ("in the gzip header's time", m_len + trees_len + 5, "the gzip'd U-Boot (byte 6 of it"),
                                ("in the configuration lines", len(good) - 2, "the configuration lines")):
            bad = bytearray(good)
            bad[at] ^= 0x01
            write(live, bytes(bad))
            p = run(t, "verify-recipe", root=False)
            check(p.returncode == 1 and f"MISMATCH: the first byte that differs is byte {at + 1}, in " in p.stdout and piece in p.stdout and "match:" not in p.stdout,
                  f"verify-recipe with a live boot.bin that differs by one bit {what}: MISMATCH, exit 1, and the piece is named", p.stdout + p.stderr)
        write(live, good[:-1])
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 1 and "MISMATCH" in p.stdout, "verify-recipe with a live boot.bin one byte short: MISMATCH", p.stdout + p.stderr)
        write(live, good)
        for what, do, undo, why in (("/boot not a mount point", lambda: unflag(t, "fstype"), lambda: flag(t, "fstype", "vfat\n"), "isn't a mount point"),
                                    ("/boot a btrfs", lambda: flag(t, "fstype", "btrfs\n"), lambda: flag(t, "fstype", "vfat\n"), "not the FAT boot partition"),
                                    ("no m1n1/boot.bin on it", lambda: os.rename(live, live + ".away"), lambda: os.rename(live + ".away", live), "no m1n1/boot.bin on"),
                                    ("a safe boot's marker", lambda: write(f"{t}/run/active", ""), lambda: os.remove(f"{t}/run/active"), "this is a safe boot"),
                                    ("sleepdrain.safe on the kernel's command line", lambda: write(f"{t}/proc-cmdline", "root=x rw sleepdrain.safe\n"),
                                     lambda: write(f"{t}/proc-cmdline", "root=x rw\n"), "this is a safe boot")):
            do()
            p = run(t, "verify-recipe", root=False)
            check(p.returncode != 0 and why in p.stderr and "match" not in p.stdout, f"verify-recipe with {what}: refused, no match said", p.stdout + p.stderr)
            undo()
        write(f"{t}/proc-cmdline", "root=x rw -- sleepdrain.safe\n")
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 0, "    (sleepdrain.safe only after a --, which isn't the kernel's: not a safe boot)", p.stdout + p.stderr)
        # An input that isn't there.
        os.rename(f"{t}/stock/dtbs/B.dtb", f"{t}/stock/dtbs/B.away")
        p = run(t, "verify-recipe", root=False)
        check(p.returncode != 0 and "an input of the stock bundle isn't a readable file" in p.stderr and "match" not in p.stdout,
              "verify-recipe with a device tree of the stock list missing: refused", p.stdout + p.stderr)
        os.rename(f"{t}/stock/dtbs/B.away", f"{t}/stock/dtbs/B.dtb")
    # No configuration file, as on this Mac: no lines.
    with tempfile.TemporaryDirectory() as t:
        setup(t, config=None)
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 0 and "0 configuration lines" in p.stdout and read(f"{t}/boot/m1n1/boot.bin").endswith(gz(f"{t}/stock/u-boot-nodtb.bin")),
              "verify-recipe with no m1n1.conf: the bundle ends with the gzip'd U-Boot, and matches", p.stdout + p.stderr)
    # update-m1n1 itself on the same inputs, in a sandbox where all it can write is the scratch directory and a /run of its own.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        if not (os.path.exists(UPDATE_M1N1) and os.path.exists("/etc/default/update-m1n1") and shutil.which("bwrap")):
            print(f"skip {UPDATE_M1N1} isn't installed here: the recipe is not held against the script itself in this run")
            return
        target = os.path.join(t, "by-update-m1n1.bin")
        p = subprocess.run(["bwrap", "--unshare-user", "--uid", "0", "--gid", "0", "--ro-bind", "/", "/", "--dev", "/dev", "--tmpfs", "/run", "--bind", t, t,
                            "--ro-bind", os.path.join(t, "defaults"), "/etc/default/update-m1n1", "--setenv", "PATH", "/usr/bin", UPDATE_M1N1, target],
                           capture_output=True, text=True)
        made = read(target) if os.path.exists(target) else b""
        check(p.returncode == 0 and made == stock_bundle(t), f"{UPDATE_M1N1} itself, run on the made-up inputs: its bundle is the one worked out here, byte for byte",
              p.stdout + p.stderr)
        write(os.path.join(t, "boot/m1n1/boot.bin"), made)
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 0 and "match:" in p.stdout and len(made) > 0, "    and verify-recipe says ref-loader's rebuild matches the file that script wrote", p.stdout + p.stderr)


FLAG = "--recovery-checked"


def install_and_restore():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        ref = make_ref(t)
        stock, bundle = stock_bundle(t), ref_bundle(t)
        s_sha, r_sha = sha(stock), sha(bundle)
        before = esp(t)
        # Without the flag: refused before anything, root or not, and the refusal says what the flag stands for.
        for root in (False, True):
            p = run(t, "install", ref, root=root)
            check(p.returncode != 0 and "install is only made with --recovery-checked; nothing written" in p.stderr
                  and "made a scratch file in that same m1n1/ directory and removed it again, never touching boot.bin" in p.stderr
                  and "a read-only mount shows it too" in p.stderr and "the restore steps (ref-loader recovery-notes) somewhere outside this Mac" in p.stderr
                  and "the owner's word, not a proof" in p.stderr and "ref-loader recovery-notes" in p.stderr and esp(t) == before and records(t) == {} and log(t) == [],
                  f"install without --recovery-checked ({'root' if root else 'not root'}): refused, nothing written; it names the write check in m1n1/ from macOS, and says the flag is the owner's word, not a proof",
                  p.stdout + p.stderr)
        p = run(t, "install", FLAG, ref, root=False)
        check(p.returncode != 0 and "run as root" in p.stderr and esp(t) == before, "install without root: refused", p.stdout + p.stderr)
        for args in ([FLAG], [FLAG, ref, "extra"], [FLAG, FLAG, ref], ["--force", ref], []):
            p = run(t, "install", *args)
            check(p.returncode == 2 and "usage: ref-loader" in p.stderr and esp(t) == before, f"install {' '.join(a.replace(t, '') for a in args)}: the usage, nothing written", p.stdout + p.stderr)
        live = os.path.join(t, "boot/m1n1/boot.bin")
        write(os.path.join(t, "empty.bundle"), b"")
        write(os.path.join(t, "stock.copy"), stock)
        write(os.path.join(t, "cut.bundle"), bundle[:-1])
        write(os.path.join(t, "other-uboot.bundle"), bundle[:-len(ACCEPTED) - 20] + b"x" + bundle[-len(ACCEPTED) - 19:])
        write(os.path.join(t, "noconf.bundle"), bundle[:-len(ACCEPTED)])
        write(os.path.join(t, "noboard.bundle"), ref_bundle(t, [n for n, _ in REF_DTBS if n != "B.dtb"]))
        cases = (("a safe boot's marker", lambda: write(f"{t}/run/active", ""), lambda: os.remove(f"{t}/run/active"), ref, "this is a safe boot"),
                 ("sleepdrain.safe on the kernel's command line", lambda: write(f"{t}/proc-cmdline", "root=x sleepdrain.safe\n"), lambda: write(f"{t}/proc-cmdline", "root=x\n"), ref, "this is a safe boot"),
                 ("/boot not mounted", lambda: unflag(t, "fstype"), lambda: flag(t, "fstype", "vfat\n"), ref, "isn't a mount point"),
                 ("/boot not FAT", lambda: flag(t, "fstype", "ext4\n"), lambda: flag(t, "fstype", "vfat\n"), ref, "not the FAT boot partition"),
                 ("no bundle at that path", None, None, f"{t}/nothere.bundle", "no bundle to read"),
                 ("an empty bundle", None, None, f"{t}/empty.bundle", "no bundle to read"),
                 ("a live boot.bin the recipe doesn't rebuild", lambda: write(live, stock + b"x"), lambda: write(live, stock), ref, "the stock recipe doesn't rebuild the live boot.bin"),
                 ("the stock bundle itself as the bundle", None, None, f"{t}/stock.copy", "is the stock bundle itself"),
                 ("a bare m1n1.bin as the bundle", None, None, f"{t}/ref/m1n1.bin", "doesn't end with the gzip'd U-Boot and the configuration lines"),
                 ("a bundle cut one byte short", None, None, f"{t}/cut.bundle", "doesn't end with the gzip'd U-Boot"),
                 ("a bundle with one other byte in its U-Boot", None, None, f"{t}/other-uboot.bundle", "doesn't end with the gzip'd U-Boot"),
                 ("a bundle without the configuration lines", None, None, f"{t}/noconf.bundle", "doesn't end with the gzip'd U-Boot"),
                 ("a well-made bundle with no device tree that names this Mac", None, None, f"{t}/noboard.bundle", "has no device tree that names this Mac (apple,j314s)"),
                 ("one byte too little room for both files and the margin", lambda: flag(t, "avail", str(len(bundle) + len(stock) + MARGIN - 1)),
                  lambda: flag(t, "avail", str(200 * 1048576)), ref, "the boot partition has"))
        for what, do, undo, b, why in cases:
            if do:
                do()
            now = esp(t)
            p = run(t, "install", FLAG, b)
            check(p.returncode != 0 and why in p.stderr and ("othing written" in p.stderr or "boot partition" in p.stderr) and esp(t) == now and records(t) == {}
                  and "installed:" not in p.stdout and not any(l.startswith(("cp ", "mv ")) for l in log(t)),
                  f"install with {what}: refused, nothing written to the boot partition or the records", p.stdout + p.stderr + str(log(t)))
            if undo:
                undo()
        check(esp(t) == before, "    (the boot partition is as it was after all of those)")
        # The install, with exactly the room it needs.
        flag(t, "avail", str(len(bundle) + len(stock) + MARGIN))
        clear_log(t)
        p = run(t, "install", FLAG, ref)
        check(p.returncode == 0 and read(live) == bundle and read(live + ".stock") == stock and sorted(os.listdir(f"{t}/boot/m1n1")) == ["boot.bin", "boot.bin.stock"]
              and records(t) == {"ref.sha256": r_sha + "\n", "stock.sha256": s_sha + "\n"},
              "install: boot.bin is the bundle, boot.bin.stock the stock file, each byte for byte; both sha256 recorded; no temporary file left", p.stdout + p.stderr + str(esp(t)))
        check("pkexec ref-loader restore" in p.stdout and "copy\n    m1n1/boot.bin.stock over m1n1/boot.bin" in p.stdout and "from macOS" in p.stdout
              and f"is now the bundle (sha256 {r_sha}" in p.stdout and f"kept the stock loader as {live}.stock (sha256 {s_sha}" in p.stdout
              and "the regular kernel too" in p.stdout and "match: the live boot.bin is exactly what the recipe builds" in p.stdout,
              "    it shows the recipe's match, says what was written, and prints how to go back: pkexec ref-loader restore, and the copy from macOS", p.stdout)
        want = [CONFIRM, "sync stock.sha256.new", "mv stock.sha256", "cp boot.bin.stock.ref-loader.new", "sync boot.bin.stock.ref-loader.new", "mv boot.bin.stock", "sync -f boot.bin.stock",
                "sync ref.sha256.new", "mv ref.sha256", "cp boot.bin.ref-loader.new", "sync boot.bin.ref-loader.new", "mv boot.bin", "sync -f boot.bin"]
        check(log(t, confirm=True) == want, "    the steps in order: the filesystem synced before the live file is read for the recipe; then the stock's record, the stock file (written beside, fsync'd, renamed, the filesystem synced), the bundle's record,"
              " the bundle the same way; boot.bin is touched last, by one rename", "\n".join(log(t, confirm=True)))
        p = run(t, "status", root=False)
        check(p.returncode == 0 and f"m1n1/boot.bin: sha256 {r_sha} ({len(bundle)} bytes): the bundle ref-loader installed" in p.stdout
              and f"m1n1/boot.bin.stock: present and intact (the recorded sha256 {s_sha}, read from the device)" in p.stdout and "builds another bundle now" in p.stdout
              and "the recorded stock" in p.stdout and "  that reading: confirmed on the partition (synced, then read from the device)" in p.stdout,
              "status after it, without root: boot.bin is the installed bundle, boot.bin.stock present and intact", p.stdout + p.stderr)
        # A second install over the bundle: the recipe no longer rebuilds the live file.
        clear_log(t)
        now = esp(t)
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "the stock recipe doesn't rebuild the live boot.bin" in p.stderr and "restore first" in p.stderr and esp(t) == now and log(t) == [],
              "install again over the installed bundle: refused (restore first), nothing written", p.stdout + p.stderr)
        # restore, twice.
        p = run(t, "restore", root=False)
        check(p.returncode != 0 and "run as root" in p.stderr and esp(t) == now, "restore without root: refused", p.stdout + p.stderr)
        flag(t, "avail", str(len(stock) + MARGIN - 1))
        p = run(t, "restore")
        check(p.returncode != 0 and "the boot partition has" in p.stderr and "Nothing written" in p.stderr and esp(t) == now, "restore with one byte too little room: refused, nothing written", p.stdout + p.stderr)
        flag(t, "avail", str(len(stock) + MARGIN))
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode == 0 and read(live) == stock and read(live + ".stock") == stock and sorted(os.listdir(f"{t}/boot/m1n1")) == ["boot.bin", "boot.bin.stock"]
              and f"restored: boot.bin is the stock loader again (sha256 {s_sha}" in p.stdout
              and log(t, confirm=True) == [CONFIRM, "cp boot.bin.ref-loader.new", "sync boot.bin.ref-loader.new", "mv boot.bin", "sync -f boot.bin"] and "note:" not in p.stdout,
              "restore: the filesystem synced and the live file read first; then boot.bin is the stock file again, byte for byte, by the same steps; boot.bin.stock stays",
              p.stdout + p.stderr + str(log(t, confirm=True)))
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode == 0 and "boot.bin already is the stock loader" in p.stdout and "nothing written" in p.stdout and log(t, confirm=True) == [CONFIRM] and read(live) == stock
              and "confirmed on the partition (synced, then read from the device)" in p.stdout,
              "restore a second time: after a sync of the filesystem and a read from the device it says boot.bin already is the stock, and writes nothing", p.stdout + p.stderr)
        p = run(t, "status", root=False)
        check(f"m1n1/boot.bin: sha256 {s_sha} ({len(stock)} bytes): the stock loader" in p.stdout and "present and intact" in p.stdout
              and "builds exactly the live boot.bin now" in p.stdout, "status after it: boot.bin is the stock loader", p.stdout + p.stderr)
        # Install again: the stock is kept already, so only the bundle needs room, and boot.bin.stock isn't written.
        flag(t, "avail", str(len(bundle) + MARGIN))
        clear_log(t)
        p = run(t, "install", FLAG, ref)
        check(p.returncode == 0 and read(live) == bundle and read(live + ".stock") == stock and "the stock loader is kept already" in p.stdout
              and not any("boot.bin.stock" in l or "stock.sha256" in l for l in log(t)),
              "install again after the restore, with room for the bundle alone: taken; boot.bin.stock and its record are not written a second time", p.stdout + p.stderr + str(log(t)))
        run(t, "restore")

        # What the kept stock can be found as, each refused with boot.bin (the stock) and everything else left alone.
        def refused(what, why, restore_too=True):
            now_e, now_r = esp(t), records(t)
            clear_log(t)
            p = run(t, "install", FLAG, ref)
            check(p.returncode != 0 and why in p.stderr and "nothing written" in p.stderr.lower() and esp(t) == now_e and records(t) == now_r and log(t) == []
                  and "installed:" not in p.stdout,
                  f"install with {what}: refused, nothing written", p.stdout + p.stderr)
            if restore_too:
                p = run(t, "restore")
                check(p.returncode != 0 and "Nothing written" in p.stderr and esp(t) == now_e and log(t) == [] and "restored" not in p.stdout,
                      f"restore with {what}: refused, nothing written", p.stdout + p.stderr)

        flag(t, "avail", str(200 * 1048576))
        write(live, bundle)     # so that a restore would have something to do
        os.remove(f"{t}/state/stock.sha256")
        p = run(t, "restore")
        check(p.returncode != 0 and "no sha256 of the stock loader is recorded" in p.stderr and read(live) == bundle, "restore with boot.bin.stock there and no record: refused, boot.bin untouched", p.stdout + p.stderr)
        write(live, stock)
        refused("boot.bin.stock there and no record of its sha256", "no sha256 is recorded for it", restore_too=False)
        write(f"{t}/state/stock.sha256", "not a hash\n")
        refused("a record that isn't a sha256", "isn't a sha256", restore_too=False)
        write(f"{t}/state/stock.sha256", s_sha + "\n")
        write(live + ".stock", stock[:-1] + b"X")
        refused("a boot.bin.stock whose sha256 isn't the recorded one", "isn't the recorded stock")
        write(live, bundle)
        p = run(t, "restore")
        check(p.returncode != 0 and "isn't the recorded stock" in p.stderr and read(live) == bundle and log(t) == [],
              "    restore with that boot.bin.stock while boot.bin is the bundle: refused; the damaged file is never written over boot.bin", p.stdout + p.stderr)
        p = run(t, "status", root=False)
        check("m1n1/boot.bin.stock: present and NOT the recorded stock" in p.stdout, "    status says boot.bin.stock is not the recorded stock", p.stdout + p.stderr)
        write(live, stock)
        os.remove(live + ".stock")
        write(live, bundle)
        p = run(t, "restore")
        check(p.returncode != 0 and "there is no m1n1/boot.bin.stock" in p.stderr and read(live) == bundle, "restore with no boot.bin.stock: refused, boot.bin untouched", p.stdout + p.stderr)
        write(live, stock)
        # An older stock: the packages changed since it was kept, and the live file is the new stock.
        old = b"an older stock bundle"
        write(live + ".stock", old)
        write(f"{t}/state/stock.sha256", sha(old) + "\n")
        refused("a boot.bin.stock that is an older stock than the recipe builds now, intact by its record", "is an older stock bundle than the one update-m1n1 builds now", restore_too=False)
        os.remove(live + ".stock")
        refused("a record of another stock and no boot.bin.stock", "records another stock bundle", restore_too=False)
        # A record of the live stock and no file: the file may be written, since it is the recorded stock.
        write(f"{t}/state/stock.sha256", s_sha + "\n")
        p = run(t, "install", FLAG, ref)
        check(p.returncode == 0 and read(live) == bundle and read(live + ".stock") == stock, "install with a record of the live stock and no boot.bin.stock: the stock file is written, then the bundle", p.stdout + p.stderr)
        run(t, "restore")


def write_faults():
    def fresh(t, installed=False):
        setup(t)
        ref = make_ref(t)
        if installed:
            p = run(t, "install", FLAG, ref)
            assert p.returncode == 0, p.stderr
            clear_log(t)
        return ref, stock_bundle(t), ref_bundle(t), os.path.join(t, "boot/m1n1/boot.bin")

    # install: the stock's copy.
    for what, name, pat in (("the stock's copy doesn't read back as written", "bad-cp", "boot.bin.stock.ref-loader.new"),
                            ("the stock's copy can't be fsync'd", "fail-sync", "boot.bin.stock.ref-loader.new"),
                            ("the stock's copy can't be renamed into place", "fail-mv", "boot.bin.stock"),
                            ("boot.bin.stock doesn't read back after its rename", "bad-mv", "boot.bin.stock")):
        with tempfile.TemporaryDirectory() as t:
            ref, stock, bundle, live = fresh(t)
            flag(t, name, pat)
            p = run(t, "install", FLAG, ref)
            check(p.returncode != 0 and "couldn't keep the stock file" in p.stderr and "boot.bin is untouched, the bundle not installed" in p.stderr and read(live) == stock
                  and os.listdir(f"{t}/boot/m1n1") == ["boot.bin"] and "installed:" not in p.stdout and "ref.sha256" not in records(t),
                  f"install where {what}: it stops there; boot.bin is the stock file, untouched; no boot.bin.stock and no temporary file is left", p.stdout + p.stderr + str(os.listdir(f"{t}/boot/m1n1")))
    # install: the bundle's write, before the rename.
    for what, name, pat in (("the bundle's copy doesn't read back as written", "bad-cp", "boot.bin.ref-loader.new"),
                            ("the bundle's copy can't be fsync'd", "fail-sync", "boot.bin.ref-loader.new"),
                            ("the bundle's copy can't be renamed over boot.bin", "fail-mv", "boot.bin")):
        with tempfile.TemporaryDirectory() as t:
            ref, stock, bundle, live = fresh(t)
            flag(t, name, pat)
            p = run(t, "install", FLAG, ref)
            check(p.returncode != 0 and "the bundle is not installed: boot.bin is as it was, the stock one" in p.stderr and read(live) == stock and read(live + ".stock") == stock
                  and sorted(os.listdir(f"{t}/boot/m1n1")) == ["boot.bin", "boot.bin.stock"] and "installed:" not in p.stdout and "ref.sha256" not in records(t),
                  f"install where {what}: boot.bin is as it was, the stock file; nothing half-written is left; no bundle is recorded", p.stdout + p.stderr)
            p = run(t, "status", root=False)
            check("the stock loader" in p.stdout and "present and intact" in p.stdout, "    status: boot.bin is the stock loader", p.stdout + p.stderr)
    # install: boot.bin reads back wrong after the rename. The stock goes back.
    with tempfile.TemporaryDirectory() as t:
        ref, stock, bundle, live = fresh(t)
        flag(t, "bad-mv-once", "boot.bin")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "doesn't read back as written after the rename" in p.stderr and "putting the stock back" in p.stderr
              and "The bundle is not installed. boot.bin is the stock one again".lower() in p.stderr.lower() and read(live) == stock and read(live + ".stock") == stock
              and "installed:" not in p.stdout and "ref.sha256" not in records(t) and sorted(os.listdir(f"{t}/boot/m1n1")) == ["boot.bin", "boot.bin.stock"],
              "install where boot.bin doesn't read back as the bundle after the rename: the stock is put back by the same steps and read back; the command fails and says boot.bin is the stock again",
              p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        ref, stock, bundle, live = fresh(t)
        flag(t, "bad-mv", "boot.bin")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "BOOT.BIN IS NOT VERIFIED and the stock could not be put back" in p.stderr and "DO NOT RESTART" in p.stderr
              and "pkexec ref-loader restore" in p.stderr and read(live + ".stock") == stock and "installed:" not in p.stdout,
              "install where boot.bin never reads back right, not as the bundle and not as the stock put back: it says BOOT.BIN IS NOT VERIFIED, DO NOT RESTART, and how to try again; boot.bin.stock is intact",
              p.stdout + p.stderr)
        unflag(t, "bad-mv")
        p = run(t, "restore")
        check(p.returncode == 0 and read(live) == stock and "restored" in p.stdout, "    restore after it, once the fault is gone: boot.bin is the stock again", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        ref, stock, bundle, live = fresh(t)
        flag(t, "fail-sync", "boot.bin")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "couldn't sync the filesystem after renaming" in p.stderr and "installed:" not in p.stdout and read(live + ".stock") == stock,
              "install where the filesystem can't be synced after the rename: not called installed", p.stdout + p.stderr)
    # restore: the same faults.
    for what, name, pat in (("the copy doesn't read back as written", "bad-cp", "boot.bin.ref-loader.new"), ("the copy can't be fsync'd", "fail-sync", "boot.bin.ref-loader.new"),
                            ("the copy can't be renamed over boot.bin", "fail-mv", "boot.bin")):
        with tempfile.TemporaryDirectory() as t:
            ref, stock, bundle, live = fresh(t, installed=True)
            flag(t, name, pat)
            p = run(t, "restore")
            check(p.returncode != 0 and "the stock is NOT restored: boot.bin is as it was, not the stock" in p.stderr and read(live) == bundle and read(live + ".stock") == stock
                  and sorted(os.listdir(f"{t}/boot/m1n1")) == ["boot.bin", "boot.bin.stock"] and "restored:" not in p.stdout,
                  f"restore where {what}: it says the stock is NOT restored; boot.bin is the bundle still, whole; nothing half-written is left", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        ref, stock, bundle, live = fresh(t, installed=True)
        flag(t, "bad-mv", "boot.bin")
        p = run(t, "restore")
        check(p.returncode != 0 and "BOOT.BIN IS NOT VERIFIED after the write" in p.stderr and "DO NOT RESTART" in p.stderr and "restored:" not in p.stdout
              and read(live + ".stock") == stock,
              "restore where boot.bin doesn't read back as the stock after the rename: BOOT.BIN IS NOT VERIFIED, DO NOT RESTART; not called restored; boot.bin.stock intact", p.stdout + p.stderr)


def sync_gate():
    """Pullfrog on #136: verify-recipe is the gate for "the stock loader is live, a restart is allowed", so it has to
    mean the partition, not the page cache: a filesystem that can't be synced fails it, whatever reads back."""
    SAYS = "the boot partition's writeback isn't confirmed"
    DEVICE = "confirmed on the partition (synced, then read from the device)"
    # The reviewer's fault: both syncs of the filesystem after a rename over boot.bin fail, the bundle's and the rollback's.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        ref = make_ref(t)
        stock, live = stock_bundle(t), os.path.join(t, "boot/m1n1/boot.bin")
        flag(t, "fail-syncfs-after-mv")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "BOOT.BIN IS NOT VERIFIED and the stock could not be put back" in p.stderr and "DO NOT RESTART" in p.stderr
              and p.stderr.count("couldn't sync the filesystem after renaming") == 2 and read(live) == stock and "installed:" not in p.stdout,
              "install where every sync of the filesystem fails once boot.bin has been renamed over (the bundle's, then the rollback's): DO NOT RESTART;"
              " the file reads as the stock through the cache all the same", p.stdout + p.stderr)
        for cmd, root in ((["verify-recipe"], False), (["status"], False), (["restore"], True), (["install", FLAG, ref], True)):
            clear_log(t)
            before = esp(t)
            p = run(t, *cmd, root=root)
            check(p.returncode != 0 and SAYS in p.stderr and "DO NOT RESTART" in p.stderr and "match" not in p.stdout and "already is the stock" not in p.stdout
                  and "the stock loader" not in p.stdout and "restored" not in p.stdout and esp(t) == before and log(t) == [],
                  f"{cmd[0]} after it, while the sync still fails: it fails saying the partition's writeback isn't confirmed; no match, no \"the stock\", nothing written,"
                  " though the bytes read back are the stock's", p.stdout + p.stderr)
        unflag(t, "fail-syncfs-after-mv")
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 0 and "match: the live boot.bin is exactly what the recipe builds, " + DEVICE in p.stdout,
              "verify-recipe once the filesystem syncs: a fresh match, read from the device, exit 0", p.stdout + p.stderr)
        p = run(t, "restore")
        check(p.returncode == 0 and "boot.bin already is the stock loader" in p.stdout, "    and restore then says boot.bin already is the stock", p.stdout + p.stderr)
    # The plain cases: a filesystem that doesn't sync, before anything was written.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        ref = make_ref(t)
        flag(t, "fail-syncfs")
        before = esp(t)
        for cmd, root in ((["verify-recipe"], False), (["status"], False), (["install", FLAG, ref], True)):
            p = run(t, *cmd, root=root)
            check(p.returncode != 0 and SAYS in p.stderr and "match" not in p.stdout and esp(t) == before and records(t) == {} and log(t) == [] and "installed:" not in p.stdout,
                  f"{cmd[0]} on a boot partition whose filesystem can't be synced: it fails saying so, with no match said and nothing written", p.stdout + p.stderr)
        unflag(t, "fail-syncfs")
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 0 and p.stdout.count(DEVICE) == 2, "verify-recipe: the live file's line and the match's line both say it was synced and read from the device", p.stdout + p.stderr)
        # restore's "already the stock" needs the sync too.
        assert run(t, "install", FLAG, ref).returncode == 0 and run(t, "restore").returncode == 0
        flag(t, "fail-syncfs")
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode != 0 and SAYS in p.stderr and "Nothing written" in p.stderr and "already is the stock" not in p.stdout and log(t) == [],
              "restore with boot.bin reading as the stock and a filesystem that can't be synced: it doesn't say \"already the stock\"; it fails, nothing written", p.stdout + p.stderr)


def device_read():
    """Pullfrog on #136, the last point: a match that only the page cache gives confirms nothing. Wherever the tool would say
    what boot.bin is, or that a write is verified, a read from the device that failed makes it say NOT CONFIRMED and fail,
    with the cache's hash and the error printed."""
    DEVICE = "confirmed on the partition (synced, then read from the device)"
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        ref = make_ref(t)
        stock, bundle, live = stock_bundle(t), ref_bundle(t), os.path.join(t, "boot/m1n1/boot.bin")
        s_sha, r_sha = sha(stock), sha(bundle)
        before = esp(t)
        for what, name, value, err in (("no direct read is possible at all", "no-direct", "", "Invalid argument"),
                                       ("the read from the device delivers every byte and then fails with an I/O error", "direct-eio", "boot.bin", "Input/output error")):
            flag(t, name, value)
            p = run(t, "verify-recipe", root=False)
            check(p.returncode != 0 and "NOT CONFIRMED: the bytes are what the recipe builds as read through the page cache, but the read from the device failed" in p.stdout
                  and err in p.stdout and "DO NOT RESTART on this" in p.stdout and p.stdout.count(f"sha256 {s_sha} ({len(stock)} bytes)") == 2
                  and "read through the page cache only" in p.stdout and "match:" not in p.stdout and "confirmed on the partition" not in p.stdout,
                  f"verify-recipe when {what}: it fails; the cache's hash is printed, and that the bytes match there, with dd's own error; NOT CONFIRMED, DO NOT RESTART; no match said",
                  p.stdout + p.stderr)
            p = run(t, "status", root=False)
            check(p.returncode != 0 and f"m1n1/boot.bin: sha256 {s_sha} ({len(stock)} bytes): by the page cache's bytes only, " in p.stdout and err in p.stdout
                  and "NOT CONFIRMED" in p.stderr and "DO NOT RESTART" in p.stderr and DEVICE not in p.stdout,
                  f"status when {what}: the cache's hash for information, and it fails with NOT CONFIRMED", p.stdout + p.stderr)
            clear_log(t)
            p = run(t, "install", FLAG, ref)
            check(p.returncode != 0 and "couldn't be read from the device" in p.stderr and "Nothing written" in p.stderr and esp(t) == before and records(t) == {} and log(t) == []
                  and "installed:" not in p.stdout and "match:" not in p.stdout,
                  f"install when {what}: its own check isn't confirmed, so it refuses; nothing written", p.stdout + p.stderr)
            unflag(t, name)
        # A live file that differs, read through the cache only: a mismatch, and said to be the cache's.
        write(live, stock + b"x")
        flag(t, "no-direct")
        p = run(t, "verify-recipe", root=False)
        check(p.returncode != 0 and "MISMATCH" in p.stdout and "that reading is the page cache's only" in p.stdout and "match:" not in p.stdout,
              "verify-recipe with a live file that differs and no direct read: MISMATCH, and it says the reading is the cache's", p.stdout + p.stderr)
        unflag(t, "no-direct")
        write(live, stock)
        # The read-backs that decide "verified" in install: the stock's copy and the bundle's, before their renames.
        flag(t, "direct-eio", "boot.bin.stock.ref-loader.new")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "reads sha256 " + s_sha + " through the page cache, but the read from the device failed" in p.stderr and "Not renamed into place" in p.stderr
              and "couldn't keep the stock file" in p.stderr and read(live) == stock and os.listdir(f"{t}/boot/m1n1") == ["boot.bin"] and "installed:" not in p.stdout,
              "install where the stock's copy reads back right only through the cache: not verified, not renamed into place; boot.bin untouched, no boot.bin.stock", p.stdout + p.stderr)
        flag(t, "direct-eio", "boot.bin.ref-loader.new")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "reads sha256 " + r_sha + " through the page cache, but the read from the device failed" in p.stderr and "Not renamed into place" in p.stderr
              and "the bundle is not installed: boot.bin is as it was" in p.stderr and read(live) == stock and read(live + ".stock") == stock and "installed:" not in p.stdout
              and "ref.sha256" not in records(t),
              "install where the bundle's copy reads back right only through the cache: not verified, not renamed over boot.bin, which is as it was", p.stdout + p.stderr)
        unflag(t, "direct-eio")
        # After the rename: boot.bin can't be read from the device any more. Neither the bundle nor the stock put back is verified.
        flag(t, "direct-eio-after-mv")
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "after the rename, " in p.stderr and "through the page cache, but the read from the device failed (dd: error reading" in p.stderr
              and "BOOT.BIN IS NOT VERIFIED and the stock could not be put back" in p.stderr and "DO NOT RESTART" in p.stderr and "installed:" not in p.stdout
              and read(live) == stock and "stock one again" not in p.stderr,
              "install where boot.bin can't be read from the device once it has been renamed over: the bundle isn't verified, the stock put back isn't either, though the"
              " cache reads it as the stock: BOOT.BIN IS NOT VERIFIED, DO NOT RESTART", p.stdout + p.stderr)
        # The recovery paths, while the device read still fails: none of them lifts the prohibition on the cache's word.
        for cmd, root, said in ((["verify-recipe"], False, "NOT CONFIRMED: the bytes are what the recipe builds as read through the page cache"),
                                (["status"], False, "by the page cache's bytes only, the stock loader"),
                                (["restore"], True, "boot.bin's bytes are the stock loader's as read through the page cache"),
                                (["install", FLAG, ref], True, "couldn't be read from the device")):
            clear_log(t)
            now = esp(t)
            p = run(t, *cmd, root=root)
            check(p.returncode != 0 and said in p.stdout + p.stderr and "Input/output error" in p.stdout + p.stderr and "DO NOT RESTART" in p.stdout + p.stderr
                  and "match:" not in p.stdout and "already is the stock" not in p.stdout and "restored" not in p.stdout and DEVICE not in p.stdout
                  and esp(t) == now and log(t) == [],
                  f"{cmd[0]} after it, while the device read still fails: non-zero, the cache's reading said for what it is, with the error; nothing confirmed, nothing written",
                  p.stdout + p.stderr)
        unflag(t, "direct-eio-after-mv")
        p = run(t, "verify-recipe", root=False)
        check(p.returncode == 0 and p.stdout.count(DEVICE) == 2, "verify-recipe once the device reads again: the match, confirmed on the partition, exit 0", p.stdout + p.stderr)
        p = run(t, "restore")
        check(p.returncode == 0 and "boot.bin already is the stock loader" in p.stdout and DEVICE in p.stdout, "    and restore then says boot.bin already is the stock, read from the device", p.stdout + p.stderr)
        # restore's own read-backs.
        os.remove(f"{t}/calls/moved-boot.bin")
        assert run(t, "install", FLAG, ref).returncode == 0
        flag(t, "direct-eio", "boot.bin.stock")
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode != 0 and "m1n1/boot.bin.stock reads sha256 " + s_sha + " through the page cache, but the read from the device failed" in p.stderr
              and "Nothing written" in p.stderr and read(live) == bundle and log(t) == [],
              "restore where boot.bin.stock can be read through the cache only: that it is the recorded stock isn't confirmed; nothing written", p.stdout + p.stderr)
        p = run(t, "status", root=False)
        check(p.returncode != 0 and "m1n1/boot.bin.stock: present; by the page cache's bytes only, sha256 " + s_sha + ", the recorded one. NOT CONFIRMED" in p.stdout
              and "the bundle ref-loader installed" in p.stdout and "  that reading: " + DEVICE in p.stdout and "present and intact" not in p.stdout,
              "status there: boot.bin confirmed from the device, boot.bin.stock said by the cache's bytes only and not called intact; it fails", p.stdout + p.stderr)
        flag(t, "direct-eio", "boot.bin.ref-loader.new")
        p = run(t, "restore")
        check(p.returncode != 0 and "Not renamed into place" in p.stderr and "the stock is NOT restored: boot.bin is as it was" in p.stderr and read(live) == bundle and "restored:" not in p.stdout,
              "restore where its copy reads back right only through the cache: not renamed over boot.bin; NOT restored", p.stdout + p.stderr)
        unflag(t, "direct-eio")
        os.remove(f"{t}/calls/moved-boot.bin")
        flag(t, "direct-eio-after-mv")
        p = run(t, "restore")
        check(p.returncode != 0 and "after the rename, " in p.stderr and "BOOT.BIN IS NOT VERIFIED after the write" in p.stderr and "DO NOT RESTART" in p.stderr
              and "restored:" not in p.stdout and read(live) == stock,
              "restore where boot.bin can't be read from the device after its rename: BOOT.BIN IS NOT VERIFIED, DO NOT RESTART, though the cache reads the stock; not called restored",
              p.stdout + p.stderr)
        unflag(t, "direct-eio-after-mv")
        p = run(t, "restore")
        check(p.returncode == 0 and "boot.bin already is the stock loader" in p.stdout and DEVICE in p.stdout, "    and once the device reads again restore says it already is the stock", p.stdout + p.stderr)
        # install over a kept stock that only the cache can read: that it is the recorded stock isn't confirmed.
        flag(t, "direct-eio", "boot.bin.stock")
        clear_log(t)
        now = esp(t)
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "boot.bin.stock reads sha256 " + s_sha + " through the page cache, but the read from the device failed" in p.stderr
              and "isn't confirmed. Nothing written" in p.stderr and esp(t) == now and log(t) == [] and read(live) == stock and "installed:" not in p.stdout,
              "install where the kept boot.bin.stock can be read through the cache only: that it is the recorded stock isn't confirmed; refused, nothing written", p.stdout + p.stderr)
        unflag(t, "direct-eio")


def direct_reads_work():
    """Whether the scratch directory takes O_DIRECT reads, as ref-loader's read-backs need."""
    with tempfile.TemporaryDirectory() as t:
        write(os.path.join(t, "f"), b"x" * 5000)
        return subprocess.run(["dd", f"if={t}/f", "iflag=direct", "bs=1M", "status=none"], capture_output=True).returncode == 0


def safe_boot():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        ref = make_ref(t)
        stock, bundle = stock_bundle(t), ref_bundle(t)
        live = os.path.join(t, "boot/m1n1/boot.bin")
        assert run(t, "install", FLAG, ref).returncode == 0
        # The same disk in a safe boot: the marker, the lower fstab, and /boot not mounted.
        write(f"{t}/run/active", "/dev/mapper/root\n")
        write(f"{t}/run/lower/root/etc/fstab", "UUID=725346d2 / btrfs rw,subvol=@ 0 0\n# UUID=dead /boot vfat rw 0 2\nUUID=553C-45DC /boot vfat rw,relatime 0 2\n")
        unflag(t, "fstype")
        mnt = f"{t}/run/ref-loader-boot"
        clear_log(t)
        p = run(t, "install", FLAG, ref)
        check(p.returncode != 0 and "this is a safe boot" in p.stderr and "Nothing written" in p.stderr and log(t) == [] and read(live) == bundle,
              "install in a safe boot: refused, the boot partition not even mounted", p.stdout + p.stderr)
        p = run(t, "status", root=False)
        check(p.returncode != 0 and "only root can mount it to look" in p.stderr and log(t) == [], "status in a safe boot without root: it says why it can't look; nothing mounted", p.stdout + p.stderr)
        p = run(t, "status")
        check(p.returncode == 0 and "the bundle ref-loader installed" in p.stdout and log(t, confirm=True) == [f"mount -t vfat -o ro,flush UUID=553C-45DC {mnt}", CONFIRM, f"umount {mnt}"],
              "status in a safe boot as root: the boot partition of the disk's own fstab mounted read-only, looked at, and unmounted", p.stdout + p.stderr + str(log(t)))
        clear_log(t)
        p = run(t, "restore")
        calls = log(t, confirm=True)
        check(p.returncode == 0 and read(live) == stock and "restored: boot.bin is the stock loader again" in p.stdout
              and calls[0] == f"mount -t vfat -o rw,flush UUID=553C-45DC {mnt}" and calls[-1] == f"umount {mnt}"
              and calls[1:-1] == [CONFIRM, "cp boot.bin.ref-loader.new", "sync boot.bin.ref-loader.new", "mv boot.bin", "sync -f boot.bin"] and not os.path.lexists(mnt),
              "restore in a safe boot: it mounts the boot partition itself, read-write, as sleep-drain-safe save does, puts the stock back by the same steps, and unmounts",
              p.stdout + p.stderr + str(calls))
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode == 0 and "already is the stock loader" in p.stdout and log(t) == [f"mount -t vfat -o rw,flush UUID=553C-45DC {mnt}", f"umount {mnt}"],
              "restore there a second time: says boot.bin already is the stock; mounted and unmounted, nothing written", p.stdout + p.stderr + str(log(t)))
        # The ways it can't get there, and the ways out after the mount: always unmounted.
        write(live, bundle)
        clear_log(t)
        flag(t, "fail-mount")
        p = run(t, "restore")
        check(p.returncode != 0 and "couldn't mount UUID=553C-45DC" in p.stderr and read(live) == bundle and not any(l.startswith("umount") for l in log(t)),
              "restore in a safe boot when the mount fails: refused, nothing written, nothing to unmount", p.stdout + p.stderr)
        unflag(t, "fail-mount")
        flag(t, "mounted")
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode != 0 and "something is mounted at" in p.stderr and log(t) == [] and read(live) == bundle, "restore when something is mounted at its mount point already: refused", p.stdout + p.stderr)
        unflag(t, "mounted")
        write(live + ".stock", stock + b"x")
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode != 0 and "isn't the recorded stock" in p.stderr and read(live) == bundle and log(t)[-1] == f"umount {mnt}" and len(log(t)) == 2,
              "restore in a safe boot with a boot.bin.stock that isn't the recorded one: refused after the mount, nothing written, and unmounted all the same", p.stdout + p.stderr + str(log(t)))
        write(live + ".stock", stock)
        flag(t, "bad-cp", "boot.bin.ref-loader.new")
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode != 0 and "NOT restored" in p.stderr and read(live) == bundle and log(t)[-1] == f"umount {mnt}",
              "restore in a safe boot where the copy doesn't read back: not restored, boot.bin as it was, unmounted all the same", p.stdout + p.stderr)
        unflag(t, "bad-cp")
        flag(t, "fail-umount")
        p = run(t, "restore")
        check(p.returncode != 0 and "restored: boot.bin is the stock loader again" in p.stdout and "still mounted at" in p.stderr and "restart now" in p.stderr,
              "restore in a safe boot when the unmount fails: the stock is back, and the command fails saying the partition is still mounted", p.stdout + p.stderr)
        unflag(t, "fail-umount")
        os.remove(mnt) if os.path.islink(mnt) else None
        write(live, bundle)
        write(f"{t}/run/lower/root/etc/fstab", "UUID=725346d2 / btrfs rw,subvol=@ 0 0\n")
        clear_log(t)
        p = run(t, "restore")
        check(p.returncode != 0 and "no /boot in the disk's fstab" in p.stderr and log(t) == [] and read(live) == bundle, "restore in a safe boot whose fstab has no /boot: refused", p.stdout + p.stderr)
        os.remove(f"{t}/run/lower/root/etc/fstab")
        p = run(t, "restore")
        check(p.returncode != 0 and "the disk's fstab isn't at" in p.stderr and log(t) == [], "restore in a safe boot with no lower fstab: refused", p.stdout + p.stderr)


def notes():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        before = esp(t)
        p = run(t, "recovery-notes", root=False)
        o = p.stdout
        s_sha = sha(stock_bundle(t))
        check(p.returncode == 0 and "/dev/fake0p4: partition number 4 of the disk fake0 (465.9G)" in o and "GPT partition UUID   0f1e2d3c-aaaa-bbbb-cccc-0123456789ab" in o
              and 'label "EFI - TEST", volume id 553C-45DC' in o and "size                 500M" in o and "m1n1/boot.bin " in o and "m1n1/boot.bin.stock " in o,
              "recovery-notes, without root: the boot partition's device, number, disk, size, label, volume id and GPT partition UUID, and the two file names", o + p.stderr)
        check("create a scratch file under a new name and remove it again. Do not touch boot.bin" in o and "a read-only mount shows it too" in o
              and "Copy m1n1/boot.bin.stock over m1n1/boot.bin" in o and "Eject" in o and "THE macOS STEPS WERE NOT TRIED FROM HERE" in o
              and "OUTSIDE this Mac" in o and "pkexec ref-loader restore" in o and "the owner's word, not a proof" in o
              and f"sha256 {s_sha}" in o and "no stock loader is recorded yet" in o and esp(t) == before and records(t) == {},
              "    the write check from macOS (a scratch file made and removed in m1n1/, boot.bin not touched), the copy back, that the macOS steps were not tried from here,"
              " to keep the notes outside the Mac, and what --recovery-checked stands for; nothing written", o)
        write(f"{t}/state/stock.sha256", "ab" * 32 + "\n")
        p = run(t, "recovery-notes", root=False)
        check("the stock loader's recorded sha256: " + "ab" * 32 in p.stdout, "    with a stock recorded: its sha256 is in the notes", p.stdout)
        for what, do, why in (("lsblk failing", lambda: flag(t, "fail-lsblk"), "couldn't read /dev/fake0p4's GPT partition UUID"),
                              ("/boot not mounted", lambda: unflag(t, "source"), "isn't a mount point")):
            do()
            p = run(t, "recovery-notes", root=False)
            check(p.returncode != 0 and why in p.stderr and "recovery notes" not in p.stdout, f"recovery-notes with {what}: refused, no notes that name no partition", p.stdout + p.stderr)
        unflag(t, "fail-lsblk")
        p = run(t, "recovery-notes", "x", root=False)
        check(p.returncode == 2, "recovery-notes with an argument: the usage", p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t, safe=True)
        unflag(t, "source")
        p = run(t, "recovery-notes", root=False)
        check(p.returncode == 0 and "/dev/fake0p4: partition number 4" in p.stdout and log(t) == ["findfs UUID=553C-45DC"] and "is now" not in p.stdout,
              "recovery-notes in a safe boot: the partition is the one the disk's own fstab names for /boot; nothing mounted", p.stdout + p.stderr + str(log(t)))
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        for args in ([], ["bogus"], ["verify-recipe", "x"], ["restore", "x"], ["status", "x"]):
            p = run(t, *args)
            check(p.returncode == 2 and "usage: ref-loader build" in p.stderr and "install --recovery-checked <bundle>" in p.stderr, f"ref-loader {' '.join(args)}: the usage", p.stderr)


def installer():
    root = os.path.dirname(TOOL)
    inst, un = open(os.path.join(root, "install.sh")).read(), open(os.path.join(root, "uninstall.sh")).read()
    check("install -m 755 ref-loader /usr/local/bin/ref-loader" in [l.strip() for l in inst.splitlines()], "install.sh installs ref-loader to /usr/local/bin")
    keep = un.find("if [[ -e /boot/m1n1/boot.bin.stock || -e /var/lib/sleep-drain/ref-loader/stock.sha256 ]]; then")
    gone = un.find("rm -f /usr/local/bin/ref-loader")
    check(0 <= keep < gone and un.count("/usr/local/bin/ref-loader") == 2 and un[keep:gone].count("else") == 1,
          "uninstall.sh removes ref-loader only when no stock loader is kept or recorded: while one is, the tool is the way back")
    src = open(TOOL).read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    check(not any(w in code for w in ("insmod", "modprobe", "rmmod", "grub-")) and "/usr/bin/update-m1n1" not in code,
          "ref-loader's code runs no module tool and nothing of GRUB's, and doesn't call update-m1n1")


def main():
    if not direct_reads_work():
        print(f"skip the scratch directory ({tempfile.gettempdir()}) takes no O_DIRECT read, and ref-loader confirms nothing without one: nothing run here."
              " Set TMPDIR to a filesystem that does")
        return 0
    installer()
    recipe()
    install_and_restore()
    write_faults()
    sync_gate()
    device_read()
    safe_boot()
    notes()
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
