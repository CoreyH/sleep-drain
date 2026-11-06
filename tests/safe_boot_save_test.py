#!/usr/bin/env python3
"""sleep-drain-safe save, then collect, without a safe boot.

First sandbox: a safe boot as the hook leaves it. /run/sleep-drain-safe has the
active marker and the lower (on-disk) /var/log; /var/log is the merged view,
which has two more log lines and one more snapshot than the lower one; and
/proc/mounts lists the overlays. mount and umount are stubs; the boot partition
is a directory bound where save mounts it. Second sandbox: the next regular
boot, with that partition at /boot and the disk's /var/log, for collect.

  python3 -I tests/safe_boot_save_test.py
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "sleep-drain-safe")
OLD_LOG = "2026-11-04T10:00:00+01:00 pre  a snap=1\n2026-11-04T18:00:00+01:00 post b snap=1\n"
NEW_LINES = "2026-11-05T12:00:00+01:00 pre  c safe=yes snap=2\n2026-11-05T12:15:00+01:00 post d snap=2\n"
FSTAB = ("UUID=725346d2 / btrfs rw,subvol=@ 0 0\n"
         "UUID=553C-45DC /boot vfat rw,relatime 0 2\n"
         "UUID=725346d2 /var/log btrfs rw,subvol=@log 0 0\n")
MOUNTS = ("sds-root / overlay rw,lowerdir=/run/sleep-drain-safe/lower/root 0 0\n"
          "sds-_var_log /var/log overlay rw,lowerdir=/run/sleep-drain-safe/lower/_var_log 0 0\n")

failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("".join("     " + l for l in detail.splitlines(True)))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def stubs(tmp):
    d = os.path.join(tmp, "stubs")
    for name, body in {
        "mount": 'echo "mount $*" >> /calls/log; [ ! -e /calls/fail-mount ]',
        "umount": 'echo "umount $*" >> /calls/log',
        "journalctl": 'echo "journal line"',
        "grep": 'if [ -e /calls/fail-grep ]; then exit 2; fi; exec /usr/bin/grep "$@"',
    }.items():
        write(os.path.join(d, name), "#!/bin/sh\n" + body + "\n")
        os.chmod(os.path.join(d, name), 0o755)
    return d


def sandbox(tmp, binds, args):
    cmd = ["bwrap", "--unshare-user", "--uid", "0",
           "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/bin", "/bin",
           "--ro-bind", "/etc", "/etc", "--dev", "/dev", "--tmpfs", "/tmp",
           "--ro-bind", stubs(tmp), "/stubs", "--bind", os.path.join(tmp, "calls"), "/calls",
           "--ro-bind", SCRIPT, "/sleep-drain-safe"]
    for src, dst in binds:
        cmd += ["--bind", src, dst]
    cmd += ["--setenv", "PATH", "/stubs:/usr/bin", "/bin/bash", "/sleep-drain-safe"] + args
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def run_save(tmp, args, fail_mount=False, setup=True):
    run = os.path.join(tmp, "run")
    merged = os.path.join(tmp, "varlog")
    binds = [(run, "/run"), (merged, "/var/log"), (os.path.join(tmp, "home"), "/home"),
             (os.path.join(tmp, "proc"), "/proc"),
             (os.path.join(tmp, "bootpart"), "/run/sleep-drain-safe/boot")]
    if not setup:
        # The same safe boot, later: whatever the caller changed since.
        return sandbox(tmp, binds, ["save"] + args)
    write(os.path.join(run, "sleep-drain-safe/active"), "/dev/mapper/root\n")
    write(os.path.join(run, "sleep-drain-safe/lower/root/etc/fstab"), FSTAB)
    lower = os.path.join(run, "sleep-drain-safe/lower/_var_log")
    write(os.path.join(lower, "sleep-drain.log"), OLD_LOG)
    write(os.path.join(lower, "sleep-drain/1/pre/x"), "old\n")
    os.makedirs(os.path.join(run, "sleep-drain-safe/rw"), exist_ok=True)
    write(os.path.join(merged, "sleep-drain.log"), OLD_LOG + NEW_LINES)
    write(os.path.join(merged, "sleep-drain/1/pre/x"), "old\n")
    write(os.path.join(merged, "sleep-drain/2/pre/x"), "new\n")
    write(os.path.join(tmp, "home/corey/results/r.txt"), "result\n")
    write(os.path.join(tmp, "proc/mounts"), MOUNTS)
    os.makedirs(os.path.join(tmp, "bootpart"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "calls"), exist_ok=True)
    if fail_mount:
        write(os.path.join(tmp, "calls/fail-mount"), "")
    return sandbox(tmp, binds, ["save"] + args)


def run_collect(tmp, disk_log, fail_grep=False, disk_files=None):
    if fail_grep:
        write(os.path.join(tmp, "calls/fail-grep"), "")
    disk = os.path.join(tmp, "disklog")
    write(os.path.join(disk, "sleep-drain.log"), disk_log)
    write(os.path.join(disk, "sleep-drain/1/pre/x"), "old\n")
    for rel, text in (disk_files or {}).items():
        write(os.path.join(disk, rel), text)
    os.makedirs(os.path.join(tmp, "run2"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "boot"), exist_ok=True)
    shutil.copytree(os.path.join(tmp, "bootpart/sleep-drain-safe"),
                    os.path.join(tmp, "boot/sleep-drain-safe"), dirs_exist_ok=True)
    binds = [(os.path.join(tmp, "run2"), "/run"), (disk, "/var/log"), (os.path.join(tmp, "boot"), "/boot")]
    return sandbox(tmp, binds, ["collect"])


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0

    print("== save in a safe boot")
    tmp = tempfile.mkdtemp()
    try:
        rc, out = run_save(tmp, ["/home/corey/results"])
        saved = os.path.join(tmp, "bootpart/sleep-drain-safe/saved")
        tars = sorted(os.listdir(saved)) if os.path.isdir(saved) else []
        calls = open(os.path.join(tmp, "calls/log")).read().splitlines()
        d = f"exit {rc}\n{out}\ncalls: {calls}\ntars: {tars}"
        check(rc == 0 and len(tars) == 1 and tars[0].endswith(".tar"), "one tar on the boot partition", d)
        check(calls == ["mount -t vfat -o rw,flush UUID=553C-45DC /run/sleep-drain-safe/boot",
                        "umount /run/sleep-drain-safe/boot"],
              "mounts only the boot partition (from the disk's fstab), and unmounts it", d)
        names = []
        if tars:
            with tarfile.open(os.path.join(saved, tars[0])) as t:
                names = sorted(n for n in t.getnames() if not t.getmember(n).isdir())
                new = t.extractfile("./log/sleep-drain.log.new").read().decode()
            check(new == NEW_LINES, "the log part is only the two new lines", new)
            check("./log/sleep-drain/2/pre/x" in names and not any("/1/" in n for n in names),
                  "the new snapshot only", "\n".join(names))
            check(any(n.startswith("./log/sleep-drain/safe-journal-") for n in names), "the boot's journal",
                  "\n".join(names))
            check("./paths/home/corey/results/r.txt" in names, "the given path", "\n".join(names))
        check(not [f for f in os.listdir(os.path.join(tmp, "run/sleep-drain-safe/rw")) if f.startswith("save-")],
              "no staging left in RAM", d)

        print("== collect in the next regular boot")
        # A sleep logged in the regular boot before collect, and a line the
        # disk has already: neither is duplicated.
        regular = "2026-11-05T13:00:00+01:00 pre  e safe=no snap=3\n"
        rc, out = run_collect(tmp, OLD_LOG + NEW_LINES.splitlines(True)[0] + regular)
        disk = os.path.join(tmp, "disklog")
        log = open(os.path.join(disk, "sleep-drain.log")).read()
        d = f"exit {rc}\n{out}\nlog:\n{log}"
        check(rc == 0 and log == OLD_LOG + NEW_LINES.splitlines(True)[0] + regular + NEW_LINES.splitlines(True)[1],
              "new lines appended once, none duplicated", d)
        check(open(os.path.join(disk, "sleep-drain/2/pre/x")).read() == "new\n", "the snapshot is in place", d)
        t = [f for f in os.listdir(os.path.join(disk, "sleep-drain")) if f.startswith("safe-saved-")]
        check(len(t) == 1 and os.path.exists(os.path.join(disk, "sleep-drain", t[0], "home/corey/results/r.txt")),
              "the given path is under safe-saved-<time>/, not over the original", d)
        check(os.listdir(os.path.join(tmp, "boot/sleep-drain-safe/saved")) == [], "the tar is deleted", d)
    finally:
        shutil.rmtree(tmp)

    print("== two saves in one safe boot, then collect")
    tmp = tempfile.mkdtemp()
    try:
        more = "2026-11-05T12:30:00+01:00 pre  f safe=yes snap=4\n2026-11-05T12:45:00+01:00 post g snap=4\n"
        rc1, out1 = run_save(tmp, [])
        # The safe boot goes on: one more sleep, then a second save.
        write(os.path.join(tmp, "varlog/sleep-drain.log"), OLD_LOG + NEW_LINES + more)
        write(os.path.join(tmp, "varlog/sleep-drain/4/pre/x"), "newer\n")
        rc2, out2 = run_save(tmp, [], setup=False)
        tars = sorted(os.listdir(os.path.join(tmp, "bootpart/sleep-drain-safe/saved")))
        check(rc1 == 0 and rc2 == 0 and len(tars) == 2,
              f"two tars, even within one second: {tars}", out1 + out2)
        named = [l.split("sleep-drain-safe/saved/")[1].split(")")[0]
                 for l in (out1 + out2).splitlines() if "sleep-drain-safe/saved/" in l]
        check(sorted(named) == tars, f"each save names its own tar: {named}", out1 + out2)
        rc, out = run_collect(tmp, OLD_LOG)
        disk = os.path.join(tmp, "disklog")
        log = open(os.path.join(disk, "sleep-drain.log")).read()
        left = os.listdir(os.path.join(tmp, "boot/sleep-drain-safe/saved"))
        d = f"exit {rc}\n{out}\nlog:\n{log}\nleft: {left}"
        check(rc == 0 and left == [] and "kept" not in out,
              "the second tar repeats the first, and both are collected and deleted", d)
        check(log == OLD_LOG + NEW_LINES + more, "every new line once, in order", d)
        check(open(os.path.join(disk, "sleep-drain/2/pre/x")).read() == "new\n"
              and open(os.path.join(disk, "sleep-drain/4/pre/x")).read() == "newer\n",
              "both sleeps' snapshots in place", d)
    finally:
        shutil.rmtree(tmp)

    print("== collect over a snapshot that differs")
    tmp = tempfile.mkdtemp()
    try:
        run_save(tmp, [])
        rc, out = run_collect(tmp, OLD_LOG, disk_files={"sleep-drain/2/pre/x": "something else\n"})
        left = os.listdir(os.path.join(tmp, "boot/sleep-drain-safe/saved"))
        d = f"exit {rc}\n{out}\nleft: {left}"
        check(rc == 0 and len(left) == 1 and f"/boot/sleep-drain-safe/saved/{left[0]} stays" in out
              and open(os.path.join(tmp, "disklog/sleep-drain/2/pre/x")).read() == "something else\n",
              "keeps the disk's copy and the tar, and names the tar", d)
    finally:
        shutil.rmtree(tmp)

    print("== collect when adding the log lines fails")
    tmp = tempfile.mkdtemp()
    try:
        run_save(tmp, [])
        rc, out = run_collect(tmp, OLD_LOG, fail_grep=True)
        left = os.listdir(os.path.join(tmp, "boot/sleep-drain-safe/saved"))
        check(rc != 0 and "kept" in out and len(left) == 1, "fails and keeps the tar", f"exit {rc}\n{out}")
    finally:
        shutil.rmtree(tmp)

    print("== the boot partition won't mount")
    tmp = tempfile.mkdtemp()
    try:
        rc, out = run_save(tmp, [], fail_mount=True)
        stage = [f for f in os.listdir(os.path.join(tmp, "run/sleep-drain-safe/rw")) if f.endswith(".tar")]
        check(rc != 0 and "couldn't mount" in out and len(stage) == 1,
              "fails, and says the tar is still in RAM", f"exit {rc}\n{out}")
    finally:
        shutil.rmtree(tmp)

    print("== save outside a safe boot")
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "calls"))
        os.makedirs(os.path.join(tmp, "run"))
        rc, out = sandbox(tmp, [(os.path.join(tmp, "run"), "/run")], ["save"])
        check(rc != 0 and "isn't a safe boot" in out, "refuses", out)
    finally:
        shutil.rmtree(tmp)

    print("all passed" if not failed else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
