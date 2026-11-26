#!/usr/bin/env python3
"""test-kernel, without root, GRUB, a boot partition or a kernel: install (with the safe sep-ro entry, without it, and
--safe-only), boot (plain and --safe-sepro), list and remove.

The script runs as it stands, as uid 0 in a bubblewrap user namespace, with its paths pointed into a scratch directory
through its TK_* variables and tar's, mkinitcpio's, GRUB's, df's, pacman's and uname's places taken by stubs on PATH
(tar is the real one: the package is a real .tar.zst made here). sleep-drain-safe is the real script too, a copy with
its two GRUB paths pointed at the same scratch directory, so the safe entry's command line is what its sepro-cmdline
says; a stub stands in for it where it must fail or say something else. grub-mkconfig's stub is
tests/safe_boot_boot_test.py's: the regular section, then every executable script of the scratch grub.d between its
BEGIN and END lines. What this shows is what the script writes, selects and removes; no boot is made.

    python3 -I -B tests/test_kernel_test.py
"""
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.realpath(os.path.join(HERE, "..", "test-kernel"))
SAFE_TOOL = os.path.realpath(os.path.join(HERE, "..", "sleep-drain-safe"))
SEP_RO = "apple_sep.xart_writes=0 apple_sep.provision_keybag=0"
REG = "root=UUID=aaaa rw quiet splash"
REL = "9.9.9-test"
K = "/aurora-test/s2r/vmlinuz"
PLAIN, SAFE = "aurora-test-s2r", "aurora-test-s2r-safe"
MIB = 1048576
fails = 0


def check(ok, what, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails += 1
        if detail:
            print("     " + detail.replace("\n", "\n     "))


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def stub(t, name, body):
    write(os.path.join(t, "bin", name), "#!/bin/bash\n" + body, 0o755)


def regular_section(regular):
    return ("### BEGIN /etc/grub.d/10_linux ###\nmenuentry 'Omarchy' --id regular {\n"
            f"    linux /vmlinuz-linux-aurora {regular}\n    initrd /initramfs-linux-aurora.img\n}}\n### END /etc/grub.d/10_linux ###\n")


def setup(t, regular=REG, hook=True, avail=200, initramfs_mib=40):
    for sub in ("boot/grub", "etc/grub.d", "etc/initcpio/hooks", "etc/initcpio/install", "modules", "state", "bin", "pkg", "calls"):
        os.makedirs(os.path.join(t, sub))
    if hook:
        for f in ("etc/initcpio/hooks/sleepdrain-safe", "etc/initcpio/install/sleepdrain-safe"):
            write(os.path.join(t, f), "")
    with open(os.path.join(t, "boot/initramfs-linux-aurora.img"), "wb") as f:
        f.truncate(initramfs_mib * MIB)
    # The package: a kernel of 2 MiB, a module, and the two things install leaves out.
    tree = os.path.join(t, "pkg/usr/lib/modules", REL)
    write(os.path.join(tree, "kernel/foo.ko"), "a module\n")
    write(os.path.join(tree, "pkgbase"), "linux-aurora\n")
    write(os.path.join(tree, "dtbs/x.dtb"), "a device tree\n")
    with open(os.path.join(tree, "vmlinuz"), "wb") as f:
        f.write(b"the kernel\n")
        f.truncate(2 * MIB)
    # Owned by root, as a package's files are: the uid the sandbox has.
    subprocess.run(["tar", "--zstd", "--owner=0", "--group=0", "-cf", os.path.join(t, "k.pkg.tar.zst"), "-C", os.path.join(t, "pkg"), "usr"], check=True)
    # The real sleep-drain-safe, reading this scratch grub.cfg.
    src = open(SAFE_TOOL).read()
    for old, new in (("GRUB_CFG=/boot/grub/grub.cfg", f"GRUB_CFG={t}/boot/grub/grub.cfg"),
                     ("SCRIPT=/etc/grub.d/44_sleep_drain_safe", f"SCRIPT={t}/etc/grub.d/44_sleep_drain_safe")):
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    write(os.path.join(t, "sleep-drain-safe"), src, 0o755)
    stub(t, "depmod", f'echo "depmod $*" >> {t}/calls/log\n')
    # mkinitcpio: the image says what it was built with; calls/mk-fail-safe fails the build that has -A, and
    # calls/mk-edit is a command run during that build (the regular line changing under it).
    stub(t, "mkinitcpio", f'''echo "mkinitcpio $*" >> {t}/calls/log
out= add=
while (($#)); do
  case $1 in -g) out=$2; shift ;; -A) add=$2; shift ;; esac
  shift
done
if [[ -n $add ]]; then
  [[ ! -e {t}/calls/mk-fail-safe ]] || {{ echo "==> ERROR: module not found: overlay"; exit 1; }}
  [[ ! -e {t}/calls/mk-edit ]] || bash {t}/calls/mk-edit
fi
echo "image hooks: base {'$'}{{add:-}}" > "$out"
''')
    stub(t, "lsinitcpio", f'[[ -e {t}/calls/no-hook ]] && exit 0; grep -o "sleepdrain-safe" "$2" | sed "s|^|hooks/|"; exit 0\n')
    # grub-probe runs after install's first checks and before it creates anything: calls/probe-hook, if there, is run
    # then, for the cases where something appears between the check and the creation.
    stub(t, "grub-probe", f"[[ ! -e {t}/calls/probe-hook ]] || bash {t}/calls/probe-hook\necho 553C-45DC\n")
    stub(t, "grub-reboot", f'echo "$1" > {t}/next\n')
    stub(t, "grub-editenv", f'''if [[ $2 == unset ]]; then
  [[ ! -e {t}/calls/unset-fails ]] || exit 1
  [[ -e {t}/calls/unset-noop ]] || rm -f {t}/next
else
  [[ ! -e {t}/calls/list-fails ]] || exit 1
  [[ ! -e {t}/calls/saved ]] || echo "saved_entry=$(cat {t}/calls/saved)"
  [[ -f {t}/next ]] && echo "next_entry=$(cat {t}/next)"
fi
exit 0
''')
    stub(t, "sync", "exit 0\n")
    stub(t, "df", f'echo Avail; echo "$(cat {t}/calls/avail)M"\n')
    stub(t, "pacman", "exit 1\n")
    stub(t, "uname", "echo 7.2.11-test\n")
    write(os.path.join(t, "calls/avail"), f"{avail}\n")
    write(os.path.join(t, "regular"), regular_section(regular))
    stub(t, "grub-mkconfig", f'''out=
while (($#)); do [[ $1 == -o ]] && out=$2; shift; done
{{
  cat {t}/regular
  for s in {t}/etc/grub.d/*; do
    [[ -x $s ]] || continue
    echo
    echo "### BEGIN $s ###"
    "$s"
    echo "### END $s ###"
  done
}} > "$out"
''')
    write(os.path.join(t, "boot/grub/grub.cfg"), regular_section(regular))


def run(t, *args, root=True, safe=None):
    env = {"PATH": os.path.join(t, "bin") + ":/usr/bin", "TK_BOOT": f"{t}/boot", "TK_STATE": f"{t}/state", "TK_GRUB_D": f"{t}/etc/grub.d",
           "TK_MODULES": f"{t}/modules", "TK_INITCPIO": f"{t}/etc/initcpio", "TK_SAFE": safe or f"{t}/sleep-drain-safe"}
    cmd = [TOOL, *args]
    if root:
        cmd = ["bwrap", "--unshare-user", "--uid", "0", "--gid", "0", "--dev-bind", "/", "/"] + cmd
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def install(t, *flags, **kw):
    return run(t, "install", *flags, os.path.join(t, "k.pkg.tar.zst"), "s2r", **kw)


def cfg_of(t):
    return open(os.path.join(t, "boot/grub/grub.cfg")).read()


def script_of(t):
    p = os.path.join(t, "etc/grub.d/43_aurora_test_s2r")
    return open(p).read() if os.path.exists(p) else None


def entry(cfg, eid, word="linux"):
    """The line starting with word in the menu entry with that id, or None."""
    inside = False
    for l in cfg.splitlines():
        if f"--id {eid} " in l:
            inside = True
        elif inside and l.strip().startswith(word + " "):
            return l.strip()
        elif inside and l.startswith("}"):
            inside = False
    return None


def nxt(t):
    p = os.path.join(t, "next")
    return open(p).read().strip() if os.path.exists(p) else None


def calls(t, word):
    p = os.path.join(t, "calls/log")
    return [l for l in open(p).read().splitlines() if l.startswith(word + " ")] if os.path.exists(p) else []


def written(t):
    """Everything the tool could have written: every directory, file and link under the boot partition, grub.d, the
    modules and the state directory, a file with a hash of its contents. A refused install must leave this as it was."""
    out = {}
    for sub in ("boot", "etc", "modules", "state"):
        for d, dirs, files in os.walk(os.path.join(t, sub)):
            out[os.path.relpath(d, t) + "/"] = "a directory"
            for f in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
                p = os.path.join(d, f)
                out[os.path.relpath(p, t)] = "-> " + os.readlink(p) if os.path.islink(p) else hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def state(t):
    p = os.path.join(t, "state/s2r")
    return open(p).read() if os.path.exists(p) else None


def images(t):
    d = os.path.join(t, "boot/aurora-test/s2r")
    return sorted(os.listdir(d)) if os.path.isdir(d) else None


SAFE_LINE = f"linux {K} {REG} {SEP_RO} sleepdrain.safe"
PLAIN_LINE = f"linux {K} {REG}"


def both_entries():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        p = install(t)
        cfg, scr = cfg_of(t), script_of(t) or ""
        mk = calls(t, "mkinitcpio")
        check(p.returncode == 0 and entry(cfg, PLAIN) == PLAIN_LINE and entry(cfg, PLAIN, "initrd") == "initrd /aurora-test/s2r/initramfs.img"
              and entry(cfg, SAFE) == SAFE_LINE and entry(cfg, SAFE, "initrd") == "initrd /aurora-test/s2r/initramfs-safe.img"
              and scr.count("menuentry ") == 2 and cfg.count("menuentry ") == 3 and cfg.count("apple_sep.") == 2 and cfg.count("sleepdrain.safe") == 1
              and regular_section(REG) in cfg and "with a safe entry" in p.stdout and "test-kernel boot s2r --safe-sepro" in p.stdout,
              "install with the hook there: two entries in the one script, the plain one with the regular line and the safe one with what"
              " sleep-drain-safe sepro-cmdline says for this kernel's path, each with its own initramfs; the regular entry as it was",
              p.stdout + p.stderr + cfg)
        check(mk == [f"mkinitcpio -k {REL} -g {t}/boot/aurora-test/s2r/initramfs.img",
                     f"mkinitcpio -k {REL} -A sleepdrain-safe -g {t}/boot/aurora-test/s2r/initramfs-safe.img.new"]
              and images(t) == ["initramfs-safe.img", "initramfs.img", "mkinitcpio-safe.log", "vmlinuz"]
              and "sleepdrain-safe" in open(os.path.join(t, "boot/aurora-test/s2r/initramfs-safe.img")).read()
              and "sleepdrain-safe" not in open(os.path.join(t, "boot/aurora-test/s2r/initramfs.img")).read(),
              "the two images: the plain one as before, the safe one built for the same release with -A sleepdrain-safe, staged and renamed", "\n".join(mk) + str(images(t)))
        tree = os.path.join(t, "modules", REL)
        check(os.path.exists(os.path.join(tree, "kernel/foo.ko")) and not os.path.exists(os.path.join(tree, "pkgbase")) and not os.path.exists(os.path.join(tree, "dtbs"))
              and state(t) == f"{REL}\nentries=plain,safe\n" and nxt(t) is None,
              "the modules without pkgbase and dtbs, as before; the state file has the release and entries=plain,safe; nothing selected", str(state(t)))
        p = run(t, "list")
        check(p.returncode == 0 and p.stdout == f"s2r\t{REL}\tin grub.cfg; safe entry: in grub.cfg\n", "list: both entries in grub.cfg", p.stdout + p.stderr)
        p = run(t, "boot", "s2r")
        check(p.returncode == 0 and nxt(t) == PLAIN and "the next restart boots s2r once" in p.stdout and "safe boot" not in p.stdout,
              "boot s2r: the plain entry is selected and read back", p.stdout + p.stderr)
        for args in (["boot", "s2r", "--safe-sepro"], ["boot", "--safe-sepro", "s2r"]):
            os.remove(os.path.join(t, "next"))
            p = run(t, *args)
            check(p.returncode == 0 and nxt(t) == SAFE and f"the next restart boots s2r ({REL}) once, as a safe boot" in p.stdout
                  and f"asked to open its store read-only ({SEP_RO})" in p.stdout and "whether it did is the driver's to say, in that boot: sleep-drain-safe status" in p.stdout,
                  f"{' '.join(args)}: the safe entry is selected and read back; the message says the store being read-only is the driver's to say",
                  p.stdout + p.stderr)
        p = run(t, "list")
        check(p.stdout == f"s2r\t{REL}\tin grub.cfg; safe entry: in grub.cfg, boots next (the safe entry)\n", "list after it: the safe entry boots next", p.stdout + p.stderr)
        # The selection's read-back, as sleep-drain-safe boot makes it.
        for body, why, left in (("exit 1\n", "grub-reboot failed; nothing new selected", SAFE), ("exit 0\n", f"the next entry isn't {PLAIN}: next_entry={SAFE}", SAFE)):
            stub(t, "grub-reboot", body)
            p = run(t, "boot", "s2r")
            check(p.returncode != 0 and why in p.stderr and nxt(t) == left and "the next restart" not in p.stdout,
                  f"boot s2r with a grub-reboot that {'fails' if 'exit 1' in body else 'returns 0 and writes nothing'}: refused, no boot announced, the queued entry stays", p.stdout + p.stderr)
        os.remove(os.path.join(t, "next"))
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode != 0 and f"the next entry isn't {SAFE}: none is set" in p.stderr and "the next restart" not in p.stdout,
              "boot s2r --safe-sepro with a grub-reboot that returns 0 and writes nothing: refused, no safe boot announced", p.stdout + p.stderr)
        stub(t, "grub-reboot", f'echo "$1" > {t}/next\n')
        good = open(os.path.join(t, "bin/grub-editenv")).read()
        stub(t, "grub-editenv", "exit 1\n")
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode != 0 and "couldn't be read back" in p.stderr and "isn't known" in p.stderr and "the next restart" not in p.stdout,
              "boot s2r --safe-sepro with a GRUB environment that can't be read back: fails saying the selection isn't known", p.stdout + p.stderr)
        write(os.path.join(t, "bin/grub-editenv"), good, 0o755)
        os.remove(os.path.join(t, "next"))
        for args in (["boot", "s2r", "--safe"], ["boot", "s2r", "--safe-sepro", "--safe-sepro"], ["boot", "s2r", "other"], ["install", "--safe-only", "x"],
                     ["install", "a", "b", "c"], ["install", "--bogus", "a", "b"], []):
            p = run(t, *args)
            check(p.returncode == 2 and "usage: test-kernel install [--safe-only] <pkg.tar.zst> <name> | boot <name> [--safe-sepro]" in p.stderr and nxt(t) is None,
                  f"{' '.join(args) or '(no command)'}: the usage, nothing selected", p.stdout + p.stderr)
        # What --safe-sepro refuses: an entry that isn't a safe sep-ro one any more, by the script's own reading of grub.cfg.
        good_cfg = cfg_of(t)
        for what, old, new, why in (
                ("without apple_sep.xart_writes=0", " apple_sep.xart_writes=0", "", "doesn't have apple_sep.xart_writes=0 as its one setting"),
                ("with apple_sep.xart_writes=1", "apple_sep.xart_writes=0", "apple_sep.xart_writes=1", "doesn't have apple_sep.xart_writes=0 as its one setting"),
                ("with a later apple-sep.xart-writes=1 as well", " sleepdrain.safe", " apple-sep.xart-writes=1 sleepdrain.safe", "doesn't have apple_sep.xart_writes=0 as its one setting"),
                ("without apple_sep.provision_keybag=0", " apple_sep.provision_keybag=0", "", "doesn't have apple_sep.provision_keybag=0 as its one setting"),
                ("with a bare apple_sep.provision_keybag before it", " apple_sep.provision_keybag=0", " apple_sep.provision_keybag apple_sep.provision_keybag=0",
                 "doesn't have apple_sep.provision_keybag=0 as its one setting"),
                ("without sleepdrain.safe", " sleepdrain.safe", "", "its last word isn't sleepdrain.safe"),
                ("with a word after sleepdrain.safe", " sleepdrain.safe", " sleepdrain.safe single", "its last word isn't sleepdrain.safe"),
                ("with a -- before the two words", f" {SEP_RO}", f" -- {SEP_RO}", "it has a -- in it"),
                ("with a quote in it", " quiet", ' "quiet"', "a character GRUB may read another way"),
                ("that boots another kernel", f"linux {K} {REG} {SEP_RO}", f"linux /vmlinuz-linux-aurora {REG} {SEP_RO}", "doesn't boot /aurora-test/s2r/vmlinuz"),
                ("that loads the plain initramfs", "initrd /aurora-test/s2r/initramfs-safe.img", "initrd /aurora-test/s2r/initramfs.img", "doesn't load /aurora-test/s2r/initramfs-safe.img"),
                ("that isn't there", f"--id {SAFE} ", "--id aurora-test-other ", "has no safe entry")):
            bad = good_cfg.replace(old, new) if old in SAFE_LINE or "initrd" in old or "--id" in old else None
            if bad is None or bad == good_cfg:
                # The replacement is of the safe entry's line alone.
                bad = good_cfg.replace(SAFE_LINE, SAFE_LINE.replace(old, new))
            assert bad != good_cfg, what
            write(os.path.join(t, "boot/grub/grub.cfg"), bad)
            p = run(t, "boot", "s2r", "--safe-sepro")
            check(p.returncode != 0 and why in p.stderr and "nothing selected" in p.stderr.lower() and nxt(t) is None and "the next restart" not in p.stdout,
                  f"boot s2r --safe-sepro with a safe entry {what}: refused, nothing selected", p.stdout + p.stderr)
        write(os.path.join(t, "boot/grub/grub.cfg"), good_cfg)
        img = os.path.join(t, "boot/aurora-test/s2r/initramfs-safe.img")
        os.rename(img, img + ".away")
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode != 0 and "its safe image isn't in" in p.stderr and nxt(t) is None, "boot s2r --safe-sepro with the safe image gone: refused", p.stdout + p.stderr)
        os.rename(img + ".away", img)
        write(os.path.join(t, "calls/no-hook"), "")
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode != 0 and "safe image has no sleepdrain-safe hook" in p.stderr and nxt(t) is None,
              "boot s2r --safe-sepro with a safe image that doesn't show the hook: refused", p.stdout + p.stderr)
        os.remove(os.path.join(t, "calls/no-hook"))
        # The regular line changes after the install: the safe entry is of the older line, and isn't selected; the plain one still is.
        changed = good_cfg.replace(f"linux /vmlinuz-linux-aurora {REG}\n", f"linux /vmlinuz-linux-aurora {REG} loglevel=3\n")
        assert changed != good_cfg
        write(os.path.join(t, "boot/grub/grub.cfg"), changed)
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode != 0 and "made from another command line than the regular entry has now" in p.stderr and nxt(t) is None,
              "boot s2r --safe-sepro after the regular entry's line changed: refused, remove and install again", p.stdout + p.stderr)
        write(os.path.join(t, "boot/grub/grub.cfg"), good_cfg.replace(f"linux /vmlinuz-linux-aurora {REG}\n", f'linux /vmlinuz-linux-aurora {REG} "x"\n'))
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode != 0 and "sepro-cmdline fails now" in p.stderr and "GRUB may read another way" in p.stderr and nxt(t) is None,
              "boot s2r --safe-sepro with a regular line sleep-drain-safe can't follow: refused, with its reason", p.stdout + p.stderr)
        write(os.path.join(t, "boot/grub/grub.cfg"), good_cfg)
        # regen_grub's rule with two entries in the section: a regeneration that would change anything outside it is not taken.
        write(os.path.join(t, "regular"), regular_section(REG + " loglevel=3"))
        run(t, "boot", "s2r", "--safe-sepro")
        p = run(t, "remove", "s2r")
        check(p.returncode != 0 and "would change more than the test entry" in p.stderr and "grub.cfg not regenerated" in p.stderr and cfg_of(t) == good_cfg
              and nxt(t) == SAFE and images(t) is not None and state(t) is not None,
              "remove with a grub-mkconfig that would change the regular entry too: grub.cfg left as it is, both entries still in it, the files and the selection in place",
              p.stdout + p.stderr)
        write(os.path.join(t, "regular"), regular_section(REG))
        # remove deleted the script before it regenerated, as it always has: put it back for the removal that works.
        write(os.path.join(t, "etc/grub.d/43_aurora_test_s2r"), scr, 0o755)
        p = run(t, "remove", "s2r")
        check(p.returncode == 0 and cfg_of(t) == regular_section(REG) and script_of(t) is None and images(t) is None and state(t) is None
              and not os.path.exists(os.path.join(t, "modules", REL)) and nxt(t) is None and f"removed s2r ({REL})" in p.stdout,
              "remove: both entries out of grub.cfg, the script, both images, the modules and the state gone, and the queued safe entry unselected", p.stdout + p.stderr + cfg_of(t))
    # A regeneration that changes the regular section at install: the two-entry section isn't taken either.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        write(os.path.join(t, "regular"), regular_section(REG + " loglevel=3"))
        before = cfg_of(t)
        p = install(t)
        check(p.returncode != 0 and "would change more than the test entry" in p.stderr and cfg_of(t) == before and "not in grub.cfg yet" in p.stderr,
              "install with a grub-mkconfig that would change the regular entry too: grub.cfg left as it is, and the install says it isn't in it yet", p.stdout + p.stderr)
    # A regeneration that would publish the safe entry without one of its words: refused before it replaces grub.cfg.
    for drop in ("apple_sep.xart_writes=0", "apple_sep.provision_keybag=0", "sleepdrain.safe"):
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            mk = os.path.join(t, "bin/grub-mkconfig")
            body = open(mk).read()
            write(mk, body.replace('    "$s"\n', f'    "$s" | sed "s/ {drop}//"\n'), 0o755)
            before = cfg_of(t)
            p = install(t)
            check(p.returncode != 0 and "doesn't have the test kernel's entries as written" in p.stderr and cfg_of(t) == before,
                  f"install with a regeneration whose safe entry would lack {drop}: the candidate is refused, grub.cfg as it was", p.stdout + p.stderr)


def plain_only():
    WRONG = (("no sleepdrain.safe", f"{REG} {SEP_RO}", "its last word isn't sleepdrain.safe"),
             ("apple_sep.xart_writes=1", f"{REG} apple_sep.xart_writes=1 apple_sep.provision_keybag=0 sleepdrain.safe", "doesn't have apple_sep.xart_writes=0"),
             ("neither word", f"{REG} sleepdrain.safe", "doesn't have apple_sep.xart_writes=0"),
             ("two lines", f"{REG} {SEP_RO} sleepdrain.safe\\nsecond", "a character GRUB may read another way"),
             ("nothing", "", "it is empty"))
    cases = [("the sleepdrain-safe hook isn't installed", dict(hook=False), None, None, "the sleepdrain-safe mkinitcpio hook isn't installed"),
             ("a regular line sleep-drain-safe refuses (a quote)", dict(regular='root=UUID=aaaa rw "quiet"'), None, None, "sepro-cmdline failed"),
             ("a regular line that keeps apple_sep out", dict(regular="root=UUID=aaaa rw module_blacklist=apple_sep"), None, None, "the driver wouldn't load in a --sep-ro boot"),
             ("a sleep-drain-safe without sepro-cmdline", {}, "echo 'usage: sleep-drain-safe boot | status' >&2; exit 2\n", None, "sepro-cmdline failed: usage: sleep-drain-safe boot"),
             ("no sleep-drain-safe at all", {}, None, None, "sepro-cmdline failed"),
             ("mkinitcpio failing for the safe image", {}, None, "mk-fail-safe", "mkinitcpio failed for the safe image"),
             ("a safe image that doesn't show the hook", {}, None, "no-hook", "the safe image as built has no sleepdrain-safe hook"),
             ("the regular line changing while the images are built", {}, None, "mk-edit", "changed while the images were built")]
    cases += [(f"a sleep-drain-safe whose line has {what}", {}, f"printf '{line}\\n'\n", None, why) for what, line, why in WRONG]
    for what, kw, safe_stub, flag, why in cases:
        with tempfile.TemporaryDirectory() as t:
            setup(t, **kw)
            regular = kw.get("regular", REG)
            safe = None
            if safe_stub:
                stub(t, "fake-safe", safe_stub)
                safe = os.path.join(t, "bin/fake-safe")
            elif what == "no sleep-drain-safe at all":
                safe = os.path.join(t, "nowhere/sleep-drain-safe")
            if flag == "mk-edit":
                # The plain entry keeps the line read before the build, as it always has; only the safe one is held to the line as it is after.
                write(os.path.join(t, "calls/mk-edit"), f"sed -i 's/quiet splash$/quiet splash loglevel=3/' {t}/boot/grub/grub.cfg {t}/regular\n")
            elif flag:
                write(os.path.join(t, "calls", flag), "")
            for mode in ((), ("--safe-only",)):
                before = written(t)
                p = install(t, *mode, safe=safe)
                cfg = cfg_of(t)
                if mode:
                    # mk-edit changed the regular line itself; everything of the kernel's is gone all the same.
                    now = written(t)
                    same = now == before if flag != "mk-edit" else set(now) == set(before)
                    check(p.returncode != 0 and why in p.stderr and "--safe-only" in p.stderr and same and nxt(t) is None and "installed" not in p.stdout
                          and (flag != "mk-fail-safe" or "==> ERROR: module not found: overlay" in p.stderr),
                          f"install --safe-only with {what}: it fails, and nothing is installed or left behind (no script, no entry, no image, no modules, no state)",
                          p.stdout + p.stderr + str(sorted(set(now) ^ set(before))))
                    if flag == "mk-edit":
                        write(os.path.join(t, "boot/grub/grub.cfg"), regular_section(REG))
                        write(os.path.join(t, "regular"), regular_section(REG))
                        os.remove(os.path.join(t, "calls/mk-edit"))
                else:
                    check(p.returncode == 0 and "no safe entry for s2r, the plain entry only" in p.stderr and why in p.stderr
                          and entry(cfg, PLAIN) == f"linux {K} {regular}" and entry(cfg, SAFE) is None and "-safe" not in cfg and "sleepdrain.safe" not in cfg
                          and (script_of(t) or "").count("menuentry ") == 1 and state(t) == f"{REL}\nentries=plain\n"
                          and images(t) in (["initramfs.img", "vmlinuz"], ["initramfs.img", "mkinitcpio-safe.log", "vmlinuz"]),
                          f"install with {what}: the plain entry only, as before, and it says why there is no safe one",
                          p.stdout + p.stderr + cfg + str(images(t)))
                    p = run(t, "list")
                    check(p.stdout == f"s2r\t{REL}\tin grub.cfg; no safe entry\n", "    list: no safe entry", p.stdout + p.stderr)
                    p = run(t, "boot", "s2r", "--safe-sepro", safe=safe)
                    check(p.returncode != 0 and "s2r has no safe entry" in p.stderr and nxt(t) is None, "    boot s2r --safe-sepro: refused, nothing selected", p.stdout + p.stderr)
                    p = run(t, "boot", "s2r")
                    check(p.returncode == 0 and nxt(t) == PLAIN, "    boot s2r: selected as before", p.stdout + p.stderr)
                    if flag == "mk-edit":
                        os.remove(os.path.join(t, "calls/mk-edit"))
                    p = run(t, "remove", "s2r")
                    check(p.returncode == 0 and script_of(t) is None and images(t) is None and state(t) is None and nxt(t) is None, "    remove: gone", p.stdout + p.stderr)
                    if flag == "mk-edit":
                        write(os.path.join(t, "calls/mk-edit"), f"sed -i 's/quiet splash$/quiet splash loglevel=3/' {t}/boot/grub/grub.cfg {t}/regular\n")
                        write(os.path.join(t, "boot/grub/grub.cfg"), regular_section(REG))
                        write(os.path.join(t, "regular"), regular_section(REG))
    # A state file from before the safe entry: the release alone.
    with tempfile.TemporaryDirectory() as t:
        setup(t, hook=False)
        install(t)
        write(os.path.join(t, "state/s2r"), REL + "\n")
        p, q, r = run(t, "list"), run(t, "boot", "s2r", "--safe-sepro"), run(t, "boot", "s2r")
        check(p.stdout == f"s2r\t{REL}\tin grub.cfg; no safe entry\n" and q.returncode != 0 and "has no safe entry" in q.stderr and r.returncode == 0 and nxt(t) == PLAIN,
              "a state file from before the safe entry (the release alone): a plain kernel to list and boot, with no safe entry", p.stdout + q.stderr + r.stderr)
        p = run(t, "remove", "s2r")
        check(p.returncode == 0 and state(t) is None and not os.path.exists(os.path.join(t, "modules", REL)), "    and remove takes it away by that release", p.stdout + p.stderr)


def safe_only():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        p = install(t, "--safe-only")
        cfg, scr = cfg_of(t), script_of(t) or ""
        check(p.returncode == 0 and entry(cfg, SAFE) == SAFE_LINE and entry(cfg, SAFE, "initrd") == "initrd /aurora-test/s2r/initramfs-safe.img"
              and scr.count("menuentry ") == 1 and cfg.count("menuentry ") == 2 and f"--id {PLAIN} " not in scr and f"--id {PLAIN} " not in cfg
              and "initramfs.img" not in scr.replace("initramfs-safe.img", "") and f"linux {K} {REG}\n" not in cfg and f"linux {K} {REG}\n" not in scr
              and all(l.endswith(" sleepdrain.safe") for l in (scr + cfg).splitlines() if l.strip().startswith(f"linux {K} "))
              and "with the safe entry alone: no plain entry and no plain initramfs" in p.stdout,
              "install --safe-only: one entry, the safe one; no plain entry and no line for this kernel without the two words and the flag, in the script or in grub.cfg",
              p.stdout + p.stderr + cfg)
        check(calls(t, "mkinitcpio") == [f"mkinitcpio -k {REL} -A sleepdrain-safe -g {t}/boot/aurora-test/s2r/initramfs-safe.img.new"]
              and images(t) == ["initramfs-safe.img", "mkinitcpio-safe.log", "vmlinuz"] and state(t) == f"{REL}\nentries=safe\n",
              "    one image built, the safe one; no plain initramfs on the boot partition; the state file says entries=safe", str(calls(t, "mkinitcpio")) + str(images(t)))
        p = run(t, "list")
        check(p.stdout == f"s2r\t{REL}\tsafe entry only (--safe-only): in grub.cfg\n", "    list says so", p.stdout + p.stderr)
        p = run(t, "boot", "s2r")
        check(p.returncode != 0 and "s2r has only the safe entry (installed with --safe-only): test-kernel boot s2r --safe-sepro" in p.stderr and nxt(t) is None
              and "the next restart" not in p.stdout,
              "    boot s2r without --safe-sepro: refused, saying this kernel has only the safe entry; nothing selected", p.stdout + p.stderr)
        p = run(t, "boot", "s2r", "--safe-sepro")
        check(p.returncode == 0 and nxt(t) == SAFE and "as a safe boot" in p.stdout, "    boot s2r --safe-sepro: selected", p.stdout + p.stderr)
        p = run(t, "install", os.path.join(t, "k.pkg.tar.zst"), "s2r")
        check(p.returncode != 0 and "s2r is installed already" in p.stderr, "    a second install under the name: refused", p.stdout + p.stderr)
        p = run(t, "remove", "s2r")
        check(p.returncode == 0 and cfg_of(t) == regular_section(REG) and script_of(t) is None and images(t) is None and state(t) is None
              and not os.path.exists(os.path.join(t, "modules", REL)) and nxt(t) is None,
              "    remove: the entry, the script, the image, the modules and the state gone, the selection unset", p.stdout + p.stderr)
    # Failures after the first write, which the cases in plain_only() don't reach: GRUB's side.
    for what, mk_body, why in (("grub-mkconfig failing", "exit 1\n", "grub-mkconfig failed"),
                               ("a grub-mkconfig that would change the regular entry too", None, "would change more than the test entry"),
                               ("a regeneration whose entry would lack apple_sep.xart_writes=0", "sed", "doesn't have the test kernel's entries as written")):
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            mk = os.path.join(t, "bin/grub-mkconfig")
            if mk_body == "sed":
                write(mk, open(mk).read().replace('    "$s"\n', '    "$s" | sed "s/ apple_sep.xart_writes=0//"\n'), 0o755)
            elif mk_body:
                stub(t, "grub-mkconfig", mk_body)
            before = written(t)
            if mk_body is None:
                write(os.path.join(t, "regular"), regular_section(REG + " loglevel=3"))
                before = written(t)
            p = install(t, "--safe-only")
            check(p.returncode != 0 and why in p.stderr and "grub.cfg wasn't taken" in p.stderr and "nothing of s2r is left behind" in p.stderr and written(t) == before,
                  f"install --safe-only with {what}: it fails, grub.cfg as it was, and nothing is left behind", p.stdout + p.stderr + str(sorted(set(written(t)) ^ set(before))))


def space_and_names():
    # The kernel is 2 MiB and the regular initramfs 40: one image needs 74 MiB with the margin, two need 114.
    for avail, mode, hook, ok in ((113, (), True, False), (114, (), True, True), (74, (), False, True), (73, (), False, False),
                                  (74, ("--safe-only",), True, True), (73, ("--safe-only",), True, False)):
        with tempfile.TemporaryDirectory() as t:
            setup(t, hook=hook, avail=avail)
            before = written(t)
            p = install(t, *mode)
            n = 114 if hook and not mode else 74
            if ok:
                check(p.returncode == 0, f"install {' '.join(mode)}{'' if hook else '(no hook)'} with {avail} MiB free on /boot, the {n} it needs: taken", p.stdout + p.stderr)
            else:
                check(p.returncode != 0 and f"/boot has {avail} MiB free; s2r needs about {n} MiB" in p.stderr and written(t) == before,
                      f"install {' '.join(mode)}{'' if hook else '(no hook)'} with {avail} MiB free on /boot, one short of the {n} it needs"
                      f" ({'both images counted' if n == 114 else 'the one image'}): refused, nothing written", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        before = written(t)
        for name in ("s2r-safe", "safe-safe"):
            p = run(t, "install", os.path.join(t, "k.pkg.tar.zst"), name)
            check(p.returncode != 0 and "a name can't end in -safe" in p.stderr and written(t) == before, f"install under the name {name}: refused, since that is the form of a safe entry's id", p.stdout + p.stderr)
        write(os.path.join(t, "state/s2r-safe"), REL + "x\n")
        before = written(t)
        p = install(t)
        check(p.returncode != 0 and "s2r-safe is installed" in p.stderr and written(t) == before, "install s2r with an older kernel named s2r-safe there: refused, the ids would collide", p.stdout + p.stderr)
        os.remove(os.path.join(t, "state/s2r-safe"))
        os.makedirs(os.path.join(t, "modules", REL))
        before = written(t)
        for mode in ((), ("--safe-only",)):
            p = install(t, *mode)
            check(p.returncode != 0 and "refusing to touch it" in p.stderr and written(t) == before and os.path.isdir(os.path.join(t, "modules", REL)),
                  f"install {' '.join(mode)} of a release whose module tree is there already: refused, and the tree is still there", p.stdout + p.stderr)
        for args in (["list"], ["install", os.path.join(t, "k.pkg.tar.zst"), "s2r"], ["boot", "s2r", "--safe-sepro"], ["remove", "s2r"]):
            p = run(t, *args, root=False)
            check(p.returncode != 0 and "run as root (pkexec)" in p.stderr and written(t) == before, f"{args[0]} without root: refused", p.stdout + p.stderr)


def not_ours():
    """What an install may write over or take back is only what that run created (Pullfrog on #136)."""
    D, S = "boot/aurora-test/s2r", "etc/grub.d/43_aurora_test_s2r"
    # The reviewer's reproduction: a file in the image directory of that name, no record, and a grub-mkconfig that fails.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        write(os.path.join(t, D, "keep"), "somebody's file\n")
        stub(t, "grub-mkconfig", "exit 1\n")
        before = written(t)
        for mode in (("--safe-only",), ()):
            p = install(t, *mode)
            check(p.returncode != 0 and written(t) == before and os.path.isfile(os.path.join(t, D, "keep"))
                  and "there is no record of a kernel named s2r, and yet this is there" in p.stderr and f"{t}/{D} (a directory holding: keep )" in p.stderr
                  and "Nothing installed" in p.stderr and calls(t, "mkinitcpio") == [] and "taken back" not in p.stderr,
                  f"install {' '.join(mode)} with a file in the image directory of that name, no record, and grub-mkconfig failing (the reviewer's case):"
                  " refused before anything is written; the file is still there", p.stdout + p.stderr)
    # One per thing found beforehand, in both modes, with a GRUB that works: refused up front, what was found is said, nothing touched.
    def there_dir(t): write(os.path.join(t, D, "vmlinuz"), "an older kernel\n")
    def there_empty_dir(t): os.makedirs(os.path.join(t, D))
    def there_file(t): write(os.path.join(t, D), "a file where the directory would be\n")
    def there_link(t): os.makedirs(os.path.join(t, "boot/aurora-test")); os.symlink("/nonexistent", os.path.join(t, D))
    def there_script(t): write(os.path.join(t, S), "#!/bin/sh\necho somebody's entry\n", 0o755)
    def there_script_link(t): os.symlink("/nonexistent", os.path.join(t, S))
    def there_both(t): there_dir(t); there_script(t)
    for what, do, names in (("an image directory of that name", there_dir, [D + " (a directory holding: vmlinuz )"]),
                            ("an empty image directory of that name", there_empty_dir, [D + " (a directory holding: )"]),
                            ("a file where the image directory would be", there_file, [D + " (not a directory)"]),
                            ("a dangling link where the image directory would be", there_link, [D + " (not a directory)"]),
                            ("a GRUB script of that name", there_script, [S]),
                            ("a dangling link where the GRUB script would be", there_script_link, [S]),
                            ("both", there_both, [D, S])):
        with tempfile.TemporaryDirectory() as t:
            setup(t)
            do(t)
            before = written(t)
            for mode in ((), ("--safe-only",)):
                p = install(t, *mode)
                check(p.returncode != 0 and "there is no record of a kernel named s2r, and yet this is there" in p.stderr and all(f"{t}/{n}" in p.stderr for n in names)
                      and "Nothing installed" in p.stderr and written(t) == before and os.path.lexists(os.path.join(t, D if D in names[0] else S))
                      and state(t) is None and not os.path.exists(os.path.join(t, "modules", REL)) and calls(t, "mkinitcpio") == [] and cfg_of(t) == regular_section(REG),
                      f"install {' '.join(mode)} with {what} and no record: refused before anything is written, saying what is there; it is left as it was", p.stdout + p.stderr)
    # Something that appears between the first checks and its creation (here: while grub-probe runs). The creation
    # fails; what this run had created by then is taken back, and the thing that appeared is not.
    made = {"the module tree": (f"modules/{REL}/theirs", f"mkdir -p {{t}}/modules/{REL} && echo theirs > {{t}}/modules/{REL}/theirs", "couldn't create", []),
            "the record": ("state/s2r", "echo theirs > {t}/state/s2r", "couldn't create the record", [f"modules/{REL}"]),
            "the image directory": (D + "/theirs", "mkdir -p {t}/" + D + " && echo theirs > {t}/" + D + "/theirs", "couldn't create", [f"modules/{REL}", "state/s2r"]),
            "the GRUB script": (S, "echo theirs > {t}/" + S, "couldn't create", [f"modules/{REL}", "state/s2r", D])}
    for what, (path, hook, why, ours) in made.items():
        for mode in ((), ("--safe-only",)):
            with tempfile.TemporaryDirectory() as t:
                setup(t)
                before = written(t)
                write(os.path.join(t, "calls/probe-hook"), hook.format(t=t) + "\n")
                p = install(t, *mode)
                now = written(t)
                # What is there now and wasn't before: the thing that appeared (and a directory it was made in), nothing else.
                top = path.split("/theirs")[0]
                only_theirs = (all(now.get(k) == v for k, v in before.items())
                               and all(k.rstrip("/") == top or k.startswith(top + "/") or top.startswith(k) for k in now if k not in before))
                check(p.returncode != 0 and why in p.stderr and only_theirs and os.path.isfile(os.path.join(t, path)) and open(os.path.join(t, path)).read() == "theirs\n"
                      and all(not os.path.lexists(os.path.join(t, o)) for o in ours if o != os.path.dirname(path) and o != path)
                      and all((f"{t}/{o}" in p.stderr) for o in ours) and f"{t}/{top}" not in (p.stderr.split("taken back", 1) + [""])[1] and "installed" not in p.stdout
                      and (("taken back" in p.stderr) == bool(ours) or bool(mode)),
                      f"install {' '.join(mode)} when {what} appears after the first checks: its creation fails; "
                      + (f"the {len(ours)} thing{'s' if len(ours) > 1 else ''} this run had created {'are' if len(ours) > 1 else 'is'} taken back, and named" if ours else "nothing had been created yet")
                      + "; what appeared is left where it is, untouched", p.stdout + p.stderr + str(sorted(set(now.items()) ^ set(before.items()))))
    # The two directories those live in, made by a first install that fails: gone again.
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        os.rmdir(os.path.join(t, "state"))
        stub(t, "grub-mkconfig", "exit 1\n")
        p = install(t, "--safe-only")
        check(p.returncode != 0 and not os.path.exists(os.path.join(t, "boot/aurora-test")) and not os.path.exists(os.path.join(t, "state")),
              "install --safe-only failing where neither /boot/aurora-test nor the state directory was there before: both are gone again", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        write(os.path.join(t, "boot/aurora-test/other/vmlinuz"), "another kernel\n")
        stub(t, "grub-mkconfig", "exit 1\n")
        before = written(t)
        p = install(t, "--safe-only")
        check(p.returncode != 0 and written(t) == before and "nothing of s2r is left behind" in p.stderr,
              "install --safe-only failing beside another kernel's image directory: that one is untouched", p.stdout + p.stderr)
    # A plain install that fails after its four things exist is what it was: left for remove.
    with tempfile.TemporaryDirectory() as t:
        setup(t, hook=False)
        stub(t, "grub-mkconfig", "exit 1\n")
        p = install(t)
        check(p.returncode != 0 and "not in grub.cfg yet" in p.stderr and state(t) == f"{REL}\nentries=plain\n" and images(t) == ["initramfs.img", "vmlinuz"] and "taken back" not in p.stderr,
              "a plain install whose grub-mkconfig fails: as before, installed and not in grub.cfg yet, nothing taken back", p.stdout + p.stderr)
        stub(t, "grub-mkconfig", "out=\nwhile (($#)); do [[ $1 == -o ]] && out=$2; shift; done\ncat " + t + "/regular > \"$out\"\n")
        p = run(t, "remove", "s2r")
        check(p.returncode == 0 and state(t) is None and images(t) is None and script_of(t) is None, "    and remove takes it away", p.stdout + p.stderr)


def unselect():
    with tempfile.TemporaryDirectory() as t:
        setup(t)
        assert install(t).returncode == 0
        for how, eid in ((["boot", "s2r"], PLAIN), (["boot", "s2r", "--safe-sepro"], SAFE)):
            assert run(t, *how).returncode == 0 and nxt(t) == eid
            p = run(t, "unselect")
            check(p.returncode == 0 and nxt(t) is None and f"cleared the selection of {eid}: the next restart is GRUB's default entry, the regular kernel" in p.stdout,
                  f"unselect after {' '.join(how)}: the selection of {eid} is cleared and read back, and it says the next restart is the default entry", p.stdout + p.stderr)
        p = run(t, "unselect")
        check(p.returncode == 0 and nxt(t) is None and "nothing is selected for the next restart" in p.stdout and "Nothing changed" in p.stdout,
              "unselect with nothing selected: says so, changes nothing", p.stdout + p.stderr)
        write(os.path.join(t, "calls/saved"), "regular\n")
        for other in ("sleep-drain-safe-sepro", "sleep-drain-safe", "aurora-testing", "xaurora-test-s2r", "0"):
            write(os.path.join(t, "next"), other + "\n")
            p = run(t, "unselect")
            check(p.returncode == 0 and nxt(t) == other and f"the next restart is the entry {other}, once: not one of test-kernel's" in p.stdout and "left as it is" in p.stdout,
                  f"unselect with another tool's entry selected ({other}): left alone, and said", p.stdout + p.stderr)
        write(os.path.join(t, "next"), "aurora-test-gone\n")
        p = run(t, "unselect")
        check(p.returncode == 0 and nxt(t) is None and "cleared the selection of aurora-test-gone" in p.stdout and "GRUB's default entry (regular)" in p.stdout,
              "unselect with an aurora-test entry of a kernel that isn't installed any more: cleared all the same; the saved default is named", p.stdout + p.stderr)
        write(os.path.join(t, "next"), SAFE + "\n")
        for flag, why in (("unset-fails", f"couldn't clear the selection: the next restart STILL boots {SAFE}"), ("unset-noop", f"the next entry still reads {SAFE}: NOT cleared"),
                          ("list-fails", "GRUB's environment block couldn't be read")):
            write(os.path.join(t, "calls", flag), "")
            p = run(t, "unselect")
            check(p.returncode != 0 and why in p.stderr and nxt(t) == SAFE and "cleared the selection" not in p.stdout,
                  f"unselect when {flag.replace('-', ' ')}: it fails and says the entry is still selected or not known; not called cleared", p.stdout + p.stderr)
            os.remove(os.path.join(t, "calls", flag))
        p = run(t, "unselect", "s2r")
        check(p.returncode == 2 and nxt(t) == SAFE, "unselect with an argument: the usage, nothing cleared", p.stderr)
        p = run(t, "unselect", root=False)
        check(p.returncode != 0 and "run as root" in p.stderr and nxt(t) == SAFE, "unselect without root: refused", p.stderr)
        p = run(t)
        check("| unselect |" in p.stderr, "the usage names unselect", p.stderr)


def main():
    not_ours()
    unselect()
    both_entries()
    plain_only()
    safe_only()
    space_and_names()
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
