#!/usr/bin/env python3
"""safe-boot/sleepdrain-safe.hook, in the initramfs's own shell, without booting.

Runs the hook's run_hook and run_latehook in mkinitcpio's busybox ash, inside a
bubblewrap sandbox with a fake /sysroot (its fstab), /proc/cmdline and
/proc/mounts. busybox's own cat, grep and sed stand in as in the initramfs;
mount, blockdev, modprobe, reboot and resolve_device are stubs that record
their calls. Each case checks the calls, the rewritten fstab, and that a
failure restarts instead of booting on.

  python3 -I tests/safe_boot_hook_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "safe-boot", "sleepdrain-safe.hook")
BUSYBOX = "/usr/lib/initcpio/busybox"
UUID = "725346d2-f127-47bc-b464-9dd46155e8d6"

FSTAB = f"""# <file system> <dir> <type> <options> <dump> <pass>
UUID={UUID} / btrfs rw,relatime,x-systemd.growfs,compress=zstd:1,subvol=@ 0 0
UUID={UUID} /home btrfs rw,relatime,x-systemd.growfs,compress=zstd:1,subvol=@home 0 0
UUID=553C-45DC /boot vfat rw,relatime,fmask=0022,dmask=0022 0 2
UUID={UUID} /var/log btrfs rw,noatime,compress=zstd:1,subvol=@log 0 0
tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0
"""

# The hook's helpers come from init_functions in the initramfs; these stand in.
DRIVER = r"""
log() { echo "$*" >> /out/calls; }
msg() { echo "msg: $*"; }
err() { echo "err: $*"; }
resolve_device() { echo /dev/mapper/root; }
blockdev() { log "blockdev $*"; [ "$1" = --getro ] && cat /fake/getro; return 0; }
modprobe() { log "modprobe $*"; }
mount() { log "mount $*"; ! grep -qxF -- "$*" /fake/mount-fails 2>/dev/null; }
reboot() { log "reboot $*"; echo REBOOT; exit 3; }
sleep() { :; }
root=UUID=725346d2-f127-47bc-b464-9dd46155e8d6
rwopt=rw
. /hook
run_hook
echo "rwopt=$rwopt"
run_latehook
echo FINISHED
"""


def run(tmp, cmdline, getro="1", root_mount="ro", fstab=FSTAB, mount_fails=None):
    os.makedirs(os.path.join(tmp, "sysroot/etc"))
    os.makedirs(os.path.join(tmp, "out"))
    os.makedirs(os.path.join(tmp, "fake"))
    os.makedirs(os.path.join(tmp, "bb"))
    for a in ("ash", "sh", "cat", "grep", "sed", "mkdir", "echo", "true"):
        os.symlink(BUSYBOX, os.path.join(tmp, "bb", a))
    with open(os.path.join(tmp, "sysroot/etc/fstab"), "w") as f:
        f.write(fstab)
    with open(os.path.join(tmp, "fake/cmdline"), "w") as f:
        f.write(cmdline + "\n")
    with open(os.path.join(tmp, "fake/getro"), "w") as f:
        f.write(getro + "\n")
    with open(os.path.join(tmp, "fake/mounts"), "w") as f:
        f.write(f"/dev/mapper/root /sysroot btrfs {root_mount},relatime,subvol=/@ 0 0\n")
    if mount_fails:
        with open(os.path.join(tmp, "fake/mount-fails"), "w") as f:
            f.write(mount_fails + "\n")
    with open(os.path.join(tmp, "driver"), "w") as f:
        f.write(DRIVER)
    cmd = ["bwrap", "--unshare-user", "--uid", "0",
           "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/bin", "/bin",
           "--ro-bind", os.path.join(tmp, "bb"), "/bb",
           "--bind", os.path.join(tmp, "sysroot"), "/sysroot",
           "--bind", os.path.join(tmp, "out"), "/out",
           "--ro-bind", os.path.join(tmp, "fake"), "/fake",
           "--ro-bind", HOOK, "/hook", "--ro-bind", os.path.join(tmp, "driver"), "/driver",
           "--tmpfs", "/run", "--tmpfs", "/tmp", "--dev", "/dev", "--tmpfs", "/proc",
           "--ro-bind", os.path.join(tmp, "fake/cmdline"), "/proc/cmdline",
           "--ro-bind", os.path.join(tmp, "fake/mounts"), "/proc/mounts",
           "--clearenv", "--setenv", "PATH", "/bb",
           "/bb/ash", "/driver"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    calls = open(os.path.join(tmp, "out/calls")).read().splitlines() \
        if os.path.exists(os.path.join(tmp, "out/calls")) else []
    fstab_after = open(os.path.join(tmp, "sysroot/etc/fstab")).read()
    return p.returncode, p.stdout, calls, fstab_after


failed = 0


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("".join("     " + l for l in detail.splitlines(True)))


def case(name, **kw):
    tmp = tempfile.mkdtemp()
    try:
        rc, out, calls, fstab = run(tmp, **kw)
    finally:
        shutil.rmtree(tmp)
    detail = f"exit {rc}\n{out}\ncalls:\n" + "\n".join(calls) + "\nfstab:\n" + fstab
    return rc, out, calls, fstab, detail


def main():
    if not os.path.exists(BUSYBOX) or not shutil.which("bwrap"):
        print("skip: needs mkinitcpio's busybox and bwrap")
        return 0

    print("== without sleepdrain.safe")
    rc, out, calls, fstab, d = case("off", cmdline="root=UUID=x rw quiet")
    check(rc == 0 and "FINISHED" in out and calls == [] and fstab == FSTAB and "rwopt=rw" in out,
          "does nothing: no calls, rw untouched, fstab as it was", d)

    print("== a safe boot")
    rc, out, calls, fstab, d = case("on", cmdline="root=UUID=x rw quiet sleepdrain.safe")
    check(rc == 0 and "FINISHED" in out, "finishes", d)
    check(calls[:2] == ["blockdev --setro /dev/mapper/root", "blockdev --getro /dev/mapper/root"],
          "the root device is made read-only, then checked", d)
    check("rwopt=ro" in out, "root is mounted read-only (rwopt=ro)", d)
    want = [
        "modprobe overlay",
        "mount -t tmpfs -o mode=0755 sleep-drain-safe /run/sleep-drain-safe/rw",
        "mount --move /sysroot /run/sleep-drain-safe/lower/root",
        "mount -t overlay sds-root -o lowerdir=/run/sleep-drain-safe/lower/root,"
        "upperdir=/run/sleep-drain-safe/rw/root/upper,workdir=/run/sleep-drain-safe/rw/root/work /sysroot",
        "mount -t btrfs -o ro,subvol=@home /dev/mapper/root /run/sleep-drain-safe/lower/_home",
        "mount -t overlay sds-_home -o lowerdir=/run/sleep-drain-safe/lower/_home,"
        "upperdir=/run/sleep-drain-safe/rw/_home/upper,workdir=/run/sleep-drain-safe/rw/_home/work /sysroot/home",
        "mount -t btrfs -o ro,subvol=@log /dev/mapper/root /run/sleep-drain-safe/lower/_var_log",
        "mount -t overlay sds-_var_log -o lowerdir=/run/sleep-drain-safe/lower/_var_log,"
        "upperdir=/run/sleep-drain-safe/rw/_var_log/upper,workdir=/run/sleep-drain-safe/rw/_var_log/work "
        "/sysroot/var/log",
    ]
    check(calls[2:] == want, "the overlays, in order: /, /home, /var/log; /boot not mounted", d)
    lines = fstab.splitlines()
    commented = [l for l in lines if l.startswith("# sleep-drain-safe: ")]
    check([l.split()[3] for l in commented] == ["/", "/home", "/boot", "/var/log"],
          "the fstab's /, /home, /boot and /var/log lines are commented out", d)
    check("tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0" in lines and lines[0].startswith("# <file"),
          "the tmpfs line and the comments stay", d)
    check(len(lines) == len(FSTAB.splitlines()), "no line lost or added", d)

    print("== failures restart, never boot on")
    for name, kw, needle in [
        ("the device stays writable", dict(getro="0"), "is still writable"),
        ("root mounted read-write", dict(root_mount="rw"), "root isn't mounted read-only"),
        ("a subvolume without subvol=", dict(fstab=FSTAB.replace(",subvol=@log", "")), "no subvol= for /var/log"),
        ("the overlay on / fails",
         dict(mount_fails="-t overlay sds-root -o lowerdir=/run/sleep-drain-safe/lower/root,"
                          "upperdir=/run/sleep-drain-safe/rw/root/upper,workdir=/run/sleep-drain-safe/rw/root/work /sysroot"),
         "the overlay on /"),
        ("mounting @home fails",
         dict(mount_fails="-t btrfs -o ro,subvol=@home /dev/mapper/root /run/sleep-drain-safe/lower/_home"),
         "mounting @home read-only"),
    ]:
        rc, out, calls, fstab, d = case(name, cmdline="sleepdrain.safe", **kw)
        check(rc == 3 and "REBOOT" in out and "FINISHED" not in out and needle in out
              and calls[-1] == "reboot -f",
              f"{name}: restarts ({needle})", d)

    print("== an fstab without a final newline")
    rc, out, calls, fstab, d = case("nonl", cmdline="sleepdrain.safe", fstab=FSTAB.rstrip("\n"))
    check(rc == 0 and fstab.splitlines()[-1] == "tmpfs /tmp tmpfs defaults,noatime,mode=1777 0 0"
          and any("subvol=@log" in c for c in calls), "the last line is still read", d)

    print("all passed" if not failed else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
