#!/usr/bin/env python3
"""sleep-drain-safe boot [--no-sep], without root, GRUB or a boot partition.

The script is copied with its five fixed paths pointed into a scratch directory, need_root and regular_release replaced, and
mkinitcpio, lsinitcpio, grub-probe, grub-mkconfig, grub-reboot and sync replaced by stubs on PATH. grub-mkconfig's stub writes the
regular entry's section and then each executable script of the scratch grub.d between its BEGIN and END lines, which is the shape
the script's own regen_grub() checks. What this shows is the entry the script writes and what it says; no boot is made.

    python3 -I -B tests/safe_boot_boot_test.py
"""
import os
import re
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "sleep-drain-safe")
NO_SEP = "module_blacklist=apple_sep"
fails = 0


def check(ok, what, detail=""):
    global fails
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails += 1
        if detail:
            print("     " + detail.replace("\n", "\n     "))


def stub(d, name, body):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write("#!/bin/bash\n" + body)
    os.chmod(p, 0o755)


def setup(t, regular="root=UUID=aaaa rw quiet splash"):
    for sub in ("boot/grub", "etc/grub.d", "etc/initcpio/hooks", "etc/initcpio/install", "bin"):
        os.makedirs(os.path.join(t, sub))
    for f in ("etc/initcpio/hooks/sleepdrain-safe", "etc/initcpio/install/sleepdrain-safe"):
        open(os.path.join(t, f), "w").close()
    src = open(TOOL).read()
    subs = [("GRUB_CFG=/boot/grub/grub.cfg", f"GRUB_CFG={t}/boot/grub/grub.cfg"),
            ("SCRIPT=/etc/grub.d/44_sleep_drain_safe", f"SCRIPT={t}/etc/grub.d/44_sleep_drain_safe"),
            ("IMAGE_DIR=/boot/sleep-drain-safe", f"IMAGE_DIR={t}/boot/sleep-drain-safe"),
            ("RUN=/run/sleep-drain-safe", f"RUN={t}/run/sleep-drain-safe"),
            ("KPARAM=/sys/module/kernel/parameters/module_blacklist", f"KPARAM={t}/kparam"),
            ("PROC_CMDLINE=/proc/cmdline", f"PROC_CMDLINE={t}/proc-cmdline"),
            ("[[ -f /etc/initcpio/hooks/sleepdrain-safe && -f /etc/initcpio/install/sleepdrain-safe ]]",
             f"[[ -f {t}/etc/initcpio/hooks/sleepdrain-safe && -f {t}/etc/initcpio/install/sleepdrain-safe ]]"),
            ('need_root() { [[ $EUID -eq 0 ]] || die "run as root (pkexec)"; }', "need_root() { :; }")]
    for old, new in subs:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    src, n = re.subn(r"\nregular_release\(\) \{\n.*?\n\}\n", "\nregular_release() { echo 7.2.11-test; }\n", src, count=1, flags=re.S)
    assert n == 1
    tool = os.path.join(t, "sleep-drain-safe")
    with open(tool, "w") as f:
        f.write(src)
    os.chmod(tool, 0o755)
    b = os.path.join(t, "bin")
    stub(b, "mkinitcpio", f'echo called >> {t}/mkinitcpio.calls; while (($#)); do [[ $1 == -g ]] && {{ echo image > "$2"; }}; shift; done\n')
    stub(b, "lsinitcpio", "echo hooks/sleepdrain-safe\n")
    stub(b, "grub-probe", "echo 553C-45DC\n")
    stub(b, "grub-reboot", f'echo "$1" > {t}/next\n')
    stub(b, "grub-editenv", f'[[ -f {t}/next ]] && echo "next_entry=$(cat {t}/next)"; exit 0\n')
    stub(b, "sync", "exit 0\n")
    regular_section = ("### BEGIN /etc/grub.d/10_linux ###\nmenuentry 'Omarchy' --id regular {\n"
                       f"    linux /vmlinuz-linux-aurora {regular}\n    initrd /initramfs-linux-aurora.img\n}}\n### END /etc/grub.d/10_linux ###\n")
    with open(os.path.join(t, "regular"), "w") as f:
        f.write(regular_section)
    stub(b, "grub-mkconfig", f'''out=
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
    with open(os.path.join(t, "boot/grub/grub.cfg"), "w") as f:
        f.write(regular_section)
    return tool


def run(t, tool, *args):
    env = dict(os.environ, PATH=os.path.join(t, "bin") + ":" + os.environ["PATH"])
    return subprocess.run([tool, *args], capture_output=True, text=True, env=env)


def cfg_of(t):
    return open(os.path.join(t, "boot/grub/grub.cfg")).read()


def entry_line(cfg, entry):
    """The linux line of the menu entry with that id, or None."""
    inside = False
    for l in cfg.splitlines():
        if f"--id {entry} " in l:
            inside = True
        elif inside and l.strip().startswith("linux "):
            return l.strip()
        elif inside and l.startswith("}"):
            inside = False
    return None


def image(t):
    """The safe boot's image and the number of times mkinitcpio ran: what a refusal must leave alone."""
    p, c = os.path.join(t, "boot/sleep-drain-safe/initramfs.img"), os.path.join(t, "mkinitcpio.calls")
    return (open(p).read() if os.path.exists(p) else None, len(open(c).read().split()) if os.path.exists(c) else 0)


def nxt(t):
    p = os.path.join(t, "next")
    return open(p).read().strip() if os.path.exists(p) else None


PLAIN, NOSEP = "sleep-drain-safe", "sleep-drain-safe-nosep"
REG = "root=UUID=aaaa rw quiet splash"


def main():
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot")
        cfg = cfg_of(t)
        check(p.returncode == 0 and entry_line(cfg, PLAIN) == f"linux /vmlinuz-linux-aurora {REG} sleepdrain.safe"
              and entry_line(cfg, NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe" and cfg.count(NO_SEP) == 1
              and nxt(t) == PLAIN and "without the Secure Enclave" not in p.stdout and "the next restart is a safe boot" in p.stdout
              and f"linux /vmlinuz-linux-aurora {REG}\n" in cfg,
              "boot: both entries are written, each with its own id, the blacklist in the second alone; the plain one is selected; the regular entry is as it was",
              p.stdout + p.stderr + cfg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and cfg_of(t) == cfg and nxt(t) == NOSEP
              and "without the Secure Enclave's driver (apple_sep on the module blacklist)" in p.stdout,
              "boot --no-sep: grub.cfg isn't rewritten, the second entry is selected, the message says so", p.stdout + p.stderr)
        # The selection queued above must outlast a later command that fails.
        stub(os.path.join(t, "bin"), "grub-reboot", "exit 1\n")
        p = run(t, tool, "boot")
        check(p.returncode != 0 and "grub-reboot failed; nothing new selected" in p.stderr and nxt(t) == NOSEP
              and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe",
              "a plain boot whose grub-reboot fails after boot --no-sep: the queued entry still has the blacklist and is still the one selected",
              p.stdout + p.stderr)
        # A grub-reboot that returns 0 and writes nothing: the plain entry must not be announced, and the queued one stays.
        stub(os.path.join(t, "bin"), "grub-reboot", "exit 0\n")
        p = run(t, tool, "boot")
        check(p.returncode != 0 and f"the next entry isn't {PLAIN}: next_entry={NOSEP}" in p.stderr and "the next restart is" not in p.stdout
              and nxt(t) == NOSEP,
              "a grub-reboot that returns 0 without writing the selection: the command fails and says what is queued, where it would have announced a boot",
              p.stdout + p.stderr)
        stub(os.path.join(t, "bin"), "grub-reboot", f'echo "$1" > {t}/next\n')
        p = run(t, tool, "boot")
        check(p.returncode == 0 and nxt(t) == PLAIN and cfg_of(t) == cfg, "a plain boot after it: the first entry is selected, grub.cfg as it was", p.stdout + p.stderr)
        os.remove(os.path.join(t, "next"))
        stub(os.path.join(t, "bin"), "grub-reboot", "exit 0\n")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and f"the next entry isn't {NOSEP}: none is set" in p.stderr and "without the Secure Enclave" not in p.stdout,
              "the same with nothing queued and boot --no-sep: refused, no boot without the driver announced", p.stdout + p.stderr)
        stub(os.path.join(t, "bin"), "grub-reboot", f'echo "$1" > {t}/next\n')
        run(t, tool, "boot")
        os.remove(os.path.join(t, "next"))
        for args in (["boot", "--nosep"], ["boot", "--no-sep", "extra"], ["boot", "extra"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and "usage: sleep-drain-safe boot [--no-sep]" in p.stderr and cfg_of(t) == cfg and nxt(t) is None,
                  f"{' '.join(args)}: refused with the usage, grub.cfg untouched, nothing selected", p.stdout + p.stderr)
        p = run(t, tool)
        check(p.returncode == 2 and "boot [--no-sep] | status" in p.stderr, "no command: the usage names boot [--no-sep]", p.stderr)
    # An older, single-entry script and grub.cfg, and a grub-mkconfig that fails once: the retry must regenerate.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        old_script = ("#!/bin/sh\ncat <<'MENU'\nmenuentry 'Safe boot for sleep experiments (disk read-only)' --id sleep-drain-safe {\n"
                      f"    linux /vmlinuz-linux-aurora {REG} sleepdrain.safe\n    initrd /sleep-drain-safe/initramfs.img\n}}\nMENU\n")
        sp = os.path.join(t, "etc/grub.d/44_sleep_drain_safe")
        with open(sp, "w") as f:
            f.write(old_script)
        os.chmod(sp, 0o755)
        subprocess.run([os.path.join(t, "bin/grub-mkconfig"), "-o", os.path.join(t, "boot/grub/grub.cfg")], check=True)
        before = cfg_of(t)
        mk = os.path.join(t, "bin", "grub-mkconfig")
        good = open(mk).read()
        stub(os.path.join(t, "bin"), "grub-mkconfig", "exit 1\n")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and "nothing selected" in p.stderr and cfg_of(t) == before and nxt(t) is None and open(sp).read() == old_script,
              "boot --no-sep over an older single-entry grub.cfg with grub-mkconfig failing: refused, grub.cfg and the script as they were, nothing selected",
              p.stdout + p.stderr)
        with open(mk, "w") as f:
            f.write(good)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe",
              "the same command once grub-mkconfig works: it regenerates and selects the second entry", p.stdout + p.stderr + cfg_of(t))
    # A grub-mkconfig that drops the blacklist: the candidate is refused before it replaces grub.cfg, and a queued selection stays.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot", "--no-sep")
        cfg = cfg_of(t)
        check(p.returncode == 0 and nxt(t) == NOSEP, "(a selection queued for the next case)", p.stdout + p.stderr)
        # Force a regeneration: the regular command line changes.
        with open(os.path.join(t, "regular")) as f:
            reg = f.read()
        with open(os.path.join(t, "regular"), "w") as f:
            f.write(reg.replace("quiet splash", "quiet splash loglevel=3"))
        with open(os.path.join(t, "boot/grub/grub.cfg"), "w") as f:
            f.write(cfg.replace(f"{REG}\n", f"{REG} loglevel=3\n"))
        cfg = cfg_of(t)
        mk = os.path.join(t, "bin", "grub-mkconfig")
        body = open(mk).read().replace('    "$s"\n', f'    "$s" | sed "s/ {NO_SEP}//"\n')
        assert body != open(mk).read()
        with open(mk, "w") as f:
            f.write(body)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and "doesn't have the safe entries as written" in p.stderr and cfg_of(t) == cfg and nxt(t) == NOSEP
              and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe",
              "a regeneration whose second entry would lack the blacklist: refused before it replaces grub.cfg; the queued entry keeps its blacklist",
              p.stdout + p.stderr + cfg_of(t))
    # An inherited blacklist is kept: the kernel takes the last module_blacklist= only.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular="root=UUID=aaaa rw module_blacklist=apple_pmp,foo quiet")
        p = run(t, tool, "boot", "--no-sep")
        cfg = cfg_of(t)
        check(p.returncode == 0 and nxt(t) == NOSEP
              and entry_line(cfg, NOSEP) == "linux /vmlinuz-linux-aurora root=UUID=aaaa rw module_blacklist=apple_pmp,foo quiet module_blacklist=apple_pmp,foo,apple_sep sleepdrain.safe"
              and entry_line(cfg, PLAIN) == "linux /vmlinuz-linux-aurora root=UUID=aaaa rw module_blacklist=apple_pmp,foo quiet sleepdrain.safe",
              "a regular line with its own module_blacklist: the second entry's last module_blacklist repeats that list and adds apple_sep; the first entry is the regular line",
              p.stdout + p.stderr + cfg)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular="root=UUID=aaaa rw module_blacklist=foo module_blacklist=apple_sep,bar quiet")
        p = run(t, tool, "boot")
        cfg = cfg_of(t)
        same = "linux /vmlinuz-linux-aurora root=UUID=aaaa rw module_blacklist=foo module_blacklist=apple_sep,bar quiet sleepdrain.safe"
        check(p.returncode == 0 and entry_line(cfg, PLAIN) == same and entry_line(cfg, NOSEP) == same and "already keeps apple_sep out" in p.stdout,
              "a regular line whose last module_blacklist already names apple_sep: both entries are that line, no longer, and the message says the driver is out anyway",
              p.stdout + p.stderr + cfg)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular="root=UUID=aaaa rw module_blacklist=apple_sep module_blacklist=foo quiet")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and entry_line(cfg_of(t), NOSEP).endswith(" quiet module_blacklist=foo,apple_sep sleepdrain.safe"),
              "apple_sep named only in an earlier module_blacklist, which the kernel drops: added to the last one's list all the same",
              p.stdout + p.stderr + cfg_of(t))
    # Both spellings are one parameter to the kernel, and the last one given wins.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular="root=UUID=aaaa rw module_blacklist=apple_sep module-blacklist=apple_pmp quiet")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP).endswith(" quiet module_blacklist=apple_pmp,apple_sep sleepdrain.safe")
              and "already keeps apple_sep out" not in p.stdout,
              "module_blacklist=apple_sep followed by module-blacklist=apple_pmp: the kernel would block apple_pmp alone, so the second entry ends with both",
              p.stdout + p.stderr + cfg_of(t))
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular="root=UUID=aaaa rw module-blacklist=apple_sep,foo quiet")
        p = run(t, tool, "boot")
        same = "linux /vmlinuz-linux-aurora root=UUID=aaaa rw module-blacklist=apple_sep,foo quiet sleepdrain.safe"
        check(p.returncode == 0 and entry_line(cfg_of(t), PLAIN) == same and entry_line(cfg_of(t), NOSEP) == same
              and "already keeps apple_sep out" in p.stdout,
              "the hyphen spelling with apple_sep in its list is seen as keeping the driver out", p.stdout + p.stderr + cfg_of(t))
    # A backslash in the regular line must compare as it is.
    with tempfile.TemporaryDirectory() as t:
        reg = r"root=UUID=aaaa rw systemd.mask=var-lib-sleep\x2ddrain.mount quiet"
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {reg} {NO_SEP} sleepdrain.safe",
              "a regular line with a backslash escape in it: the entries are written and found as written", p.stdout + p.stderr + cfg_of(t))
    # Quoting: GRUB takes the quotes off, so a later quoted blacklist would win unseen. A GRUB variable: only known at boot.
    # Refused in both modes, and before the image is built: nothing at all is written.
    for reg in ('root=UUID=aaaa rw module-blacklist=apple_sep "module_blacklist=apple_pmp" quiet',
                "root=UUID=aaaa rw module_blacklist=apple_sep 'foo=a b' quiet",
                'root=UUID=aaaa rw module_blacklist=apple_sep foo=a\\ b quiet',
                'root=UUID=aaaa rw module_blacklist=apple_sep $extra_args quiet',
                'root=UUID=aaaa rw ${more} quiet'):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t, regular=reg)
            before = cfg_of(t)
            for args in (["boot", "--no-sep"], ["boot"]):
                p = run(t, tool, *args)
                check(p.returncode != 0 and "nothing written, nothing selected" in p.stderr and nxt(t) is None and cfg_of(t) == before
                      and not os.path.exists(os.path.join(t, "etc/grub.d/44_sleep_drain_safe")) and image(t) == (None, 0),
                      f"{' '.join(args)} with a regular line that can't be worked out here ({reg[18:]}): refused; no script, no grub.cfg change, no image built, nothing selected",
                      p.stdout + p.stderr)
    # A no-SEP boot is queued; then the regular line gains a blacklist and a quoted option. Whatever is asked next, the queued
    # entry must go on excluding apple_sep: nothing may be published under its id on a guess.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot", "--no-sep")
        cfg = cfg_of(t)
        with open(os.path.join(t, "regular")) as f:
            reg = f.read()
        changed = 'module_blacklist=apple_pmp "foo=a b" quiet splash'
        with open(os.path.join(t, "regular"), "w") as f:
            f.write(reg.replace("quiet splash", changed))
        with open(os.path.join(t, "boot/grub/grub.cfg"), "w") as f:
            f.write(cfg.replace(f"{REG}\n", f"{REG.replace('quiet splash', changed)}\n"))
        cfg = cfg_of(t)
        stub(os.path.join(t, "bin"), "grub-reboot", "exit 1\n")
        img = image(t)
        for args in (["boot"], ["boot", "--no-sep"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and nxt(t) == NOSEP and cfg_of(t) == cfg and image(t) == img and img == ("image\n", 1)
                  and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe",
                  f"{' '.join(args)} after a no-SEP boot was queued and the regular line became one that can't be parsed: refused, and the queued entry still excludes apple_sep",
                  p.stdout + p.stderr + cfg_of(t))
    # The kernel's command line is 2048 bytes; what doesn't fit is cut at the end, where the read-only flag is.
    def near(n, tail=""):
        """A regular line that makes the plain entry's kernel line exactly n characters long."""
        base = f"root=UUID=aaaa rw {tail}pad="
        fixed = len("BOOT_IMAGE=/vmlinuz-linux-aurora ") + len(base) + len(" sleepdrain.safe")
        return base + "x" * (n - fixed)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular=near(2047))
        before = cfg_of(t)
        for args in (["boot"], ["boot", "--no-sep"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and "lose its end, where the read-only flag is" in p.stderr and nxt(t) is None and cfg_of(t) == before
                  and image(t) == (None, 0),
                  f"{' '.join(args)} with a regular line whose plain entry just fits and whose no-SEP entry wouldn't: refused, nothing written, no image built", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular=near(2048))
        p = run(t, tool, "boot")
        check(p.returncode != 0 and "lose its end" in p.stderr and nxt(t) is None, "a plain entry one byte over: refused", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = near(2047, tail="module_blacklist=apple_sep ")
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {reg} sleepdrain.safe",
              "a line at the limit that already keeps apple_sep out: the no-SEP entry is that line, not a longer one, and is selected", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = near(2047 - len(f" {NO_SEP}"))
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP).endswith(f" {NO_SEP} sleepdrain.safe"),
              "a line whose no-SEP entry lands exactly on the last byte that fits: taken", p.stdout + p.stderr)
    # The regular line changes while the image is being built: the entries would be made from a line that is no longer the regular
    # one. Refused, with the image that was there, if any, left in place.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot", "--no-sep")
        cfg, img = cfg_of(t), image(t)
        check(p.returncode == 0 and img == ("image\n", 1), "(a first boot --no-sep, for the next case)", p.stdout + p.stderr)
        mk = os.path.join(t, "bin", "mkinitcpio")
        body = open(mk).read()
        with open(mk, "w") as f:
            f.write(body.replace("#!/bin/bash\n", f"#!/bin/bash\nsed -i 's/quiet splash$/quiet splash module_blacklist=apple_pmp/' {t}/boot/grub/grub.cfg {t}/regular\n", 1))
        with open(os.path.join(t, "boot/sleep-drain-safe/initramfs.img"), "w") as f:
            f.write("the image the queued entry boots\n")
        p = run(t, tool, "boot", "--no-sep")
        now = cfg_of(t)
        check(p.returncode != 0 and "changed while the image was built" in p.stderr and nxt(t) == NOSEP
              and image(t) == ("the image the queued entry boots\n", 2) and not os.path.exists(os.path.join(t, "boot/sleep-drain-safe/initramfs.img.new"))
              and entry_line(now, NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe" and "module_blacklist=apple_pmp\n" in now,
              "the regular line gaining a blacklist during the image build: refused; the old image, the entries and the queued selection stay; no staged image is left",
              p.stdout + p.stderr + now)
        with open(mk, "w") as f:
            f.write(body)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP).endswith(" module_blacklist=apple_pmp,apple_sep sleepdrain.safe"),
              "run again: the entries are made from the new line, with both modules excluded", p.stdout + p.stderr + cfg_of(t))
    # The regular entry goes away while the image is built and the safe entries stay: they are not the line to copy.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot", "--no-sep")
        cfg = cfg_of(t)
        mk = os.path.join(t, "bin", "mkinitcpio")
        body = open(mk).read()
        with open(mk, "w") as f:
            f.write(body.replace("#!/bin/bash\n", f"#!/bin/bash\nsed -i '/^    linux .vmlinuz-linux-aurora {REG}$/d' {t}/boot/grub/grub.cfg\n", 1))
        with open(os.path.join(t, "boot/sleep-drain-safe/initramfs.img"), "w") as f:
            f.write("the image the queued entry boots\n")
        p = run(t, tool, "boot")
        now = cfg_of(t)
        check(p.returncode != 0 and "changed while the image was built" in p.stderr and nxt(t) == NOSEP
              and image(t)[0] == "the image the queued entry boots\n" and not os.path.exists(os.path.join(t, "boot/sleep-drain-safe/initramfs.img.new"))
              and entry_line(now, NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe" and f"{REG}\n" not in now,
              "the regular entry removed during the image build, the safe entries left: refused; the old image and the queued selection stay",
              p.stdout + p.stderr + now)
        with open(mk, "w") as f:
            f.write(body)
        p = run(t, tool, "boot")
        check(p.returncode != 0 and "no /vmlinuz-linux-aurora entry" in p.stderr and nxt(t) == NOSEP and image(t)[0] == "the image the queued entry boots\n",
              "and with no regular entry to begin with, only this script's own: refused before the image is built; a safe entry isn't copied",
              p.stdout + p.stderr)
    # A long grub.cfg: a submenu of 300 kB after the regular entry, more than a pipe holds, with other kernels' lines in it.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        sub = "submenu 'Advanced options' {\n" + "".join(
            f"menuentry 'Omarchy, with Linux fallback {i}' {{\n    linux /vmlinuz-linux-aurora root=UUID=bbbb{i} ro single\n"
            f"    initrd /initramfs-fallback-{i}.img\n    # {'padding ' * 40}\n}}\n" for i in range(800)) + "}\n"
        assert len(sub) > 300000
        for f in ("regular", "boot/grub/grub.cfg"):
            with open(os.path.join(t, f)) as fh:
                reg = fh.read()
            with open(os.path.join(t, f), "w") as fh:
                fh.write(reg.replace("### END /etc/grub.d/10_linux ###\n", sub + "### END /etc/grub.d/10_linux ###\n"))
        for args, want in ((["boot"], PLAIN), (["boot", "--no-sep"], NOSEP)):
            p = run(t, tool, *args)
            check(p.returncode == 0 and nxt(t) == want and entry_line(cfg_of(t), PLAIN) == f"linux /vmlinuz-linux-aurora {REG} sleepdrain.safe"
                  and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe",
                  f"{' '.join(args)} with a 300 kB submenu after the regular entry: the first regular line is the one copied, and the command finishes",
                  f"rc {p.returncode}\n" + p.stdout + p.stderr[:600])
    # Bytes, not characters: a line of two-byte characters that fits by count and not by size.
    with tempfile.TemporaryDirectory() as t:
        base = "root=UUID=aaaa rw pad="
        fixed = len("BOOT_IMAGE=/vmlinuz-linux-aurora ") + len(base) + len(" sleepdrain.safe")
        reg = base + "\u00e9" * ((2047 - fixed) // 2 + 20)
        assert len(reg) + fixed - len(base) + len(base) < 2047 and len(reg.encode()) + fixed - len(base) > 2047
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot")
        check(p.returncode != 0 and "lose its end" in p.stderr and nxt(t) is None and image(t) == (None, 0),
              "a regular line that fits counted in characters and not in bytes: refused", p.stdout + p.stderr)
    # Quoting changes the line's length on its way to the kernel: taken only at under half the limit.
    with tempfile.TemporaryDirectory() as t:
        reg = 'root=UUID=aaaa rw "foo=a b" pad=' + "x" * 1100
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot")
        check(p.returncode != 0 and "lose its end" in p.stderr and nxt(t) is None,
              "a quoted line of 1.1 kB, which would fit as written: refused, since GRUB's own quoting could take it past the limit", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = 'root=UUID=aaaa rw "foo=a b" quiet'
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {reg} {NO_SEP} sleepdrain.safe",
              "a short quoted line with no blacklist of its own: taken, apple_sep added at the end", p.stdout + p.stderr)
    # The read-back itself failing: the selection is unknown, and the message says that, not "not selected".
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        stub(os.path.join(t, "bin"), "grub-editenv", "exit 1\n")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and "couldn't be read back" in p.stderr and "isn't known" in p.stderr and "Not selected" not in p.stderr
              and "the next restart is" not in p.stdout,
              "a grub-editenv that can't read the block after grub-reboot: the command fails saying the selection isn't known", p.stdout + p.stderr)
    # What follows "--" goes to init: a blacklist added at the end would set nothing in the kernel. Refused, in both modes.
    # And what GRUB would make a bare -- of: it takes quotes and backslashes off before the kernel sees the line.
    for reg in ("root=UUID=aaaa rw quiet -- single", "root=UUID=aaaa rw quiet --", "-- root=UUID=aaaa", 'root=UUID=aaaa quiet "--" single',
                "root=UUID=aaaa quiet '--' single", 'root=UUID=aaaa quiet -"-" single', "root=UUID=aaaa quiet \\-\\- single",
                "root=UUID=aaaa quiet\t--\tsingle", 'root=UUID=aaaa "foo=a -- b" quiet'):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t, regular=reg)
            before = cfg_of(t)
            for args in (["boot"], ["boot", "--no-sep"]):
                p = run(t, tool, *args)
                check(p.returncode != 0 and "has a -- in it, or what GRUB could make one of" in p.stderr and "nothing selected" in p.stderr and nxt(t) is None
                      and cfg_of(t) == before and image(t) == (None, 0),
                      f"{' '.join(args)} with the regular line {reg!r}: refused, nothing written, no image built", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = 'root=UUID=aaaa rw quiet foo=--bar --baz "x=a --b"'
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {reg} {NO_SEP} sleepdrain.safe",
              "a line with -- only inside arguments: taken", p.stdout + p.stderr)
    # The kernel drops a string parameter over 1024 bytes: a blacklist that long would keep nothing out. Only a line that
    # has apple_sep in its list already can get there: one that gets the list a second time is past the line's limit first.
    for n, fits_ in ((1024, True), (1025, False)):
        with tempfile.TemporaryDirectory() as t:
            lst = "m" * (n - len(",apple_sep"))
            tool = setup(t, regular=f"root=UUID=aaaa rw module_blacklist={lst},apple_sep")
            before = cfg_of(t)
            p = run(t, tool, "boot", "--no-sep")
            if fits_:
                check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP).endswith(f" module_blacklist={lst},apple_sep sleepdrain.safe"),
                      f"a blacklist of {n} bytes that has apple_sep already: taken", p.stdout + p.stderr)
            else:
                check(p.returncode != 0 and f"would be {n} bytes" in p.stderr and "nothing selected" in p.stderr and nxt(t) is None
                      and cfg_of(t) == before and image(t) == (None, 0),
                      f"a blacklist of {n} bytes that has apple_sep already: refused, nothing written, no image built", p.stdout + p.stderr)
    # status in a safe boot: "kept out" only by the kernel's own list; when that can't be read, not verified, with what the
    # command line asks for (nothing after a "--" counted) said beside it.
    KEPT, MAY = "the Secure Enclave's driver is kept out of this boot", "the Secure Enclave's driver may load in this boot"
    UNV = "NOT VERIFIED whether the Secure Enclave's driver is kept out of this boot: the kernel's own module blacklist reads only as root (pkexec sleep-drain-safe status). The command line "
    cases = (("the kernel's list has apple_sep", "apple_sep", "root=x rw sleepdrain.safe", KEPT + " (the kernel's module blacklist lists apple_sep)"),
             ("the kernel's list is unset though the command line names apple_sep after --", "(null)", "root=x rw -- module_blacklist=apple_sep sleepdrain.safe", MAY),
             ("the kernel's list has another module whose name has apple_sep in it", "apple_sep_x", "root=x module_blacklist=apple_sep", MAY),
             ("the kernel's list can't be read and the command line's last blacklist has apple_sep", None, "root=x module_blacklist=foo,apple_sep sleepdrain.safe",
              UNV + "asks for it;"),
             ("the kernel's list can't be read and the command line has a blacklist of 1,025 bytes with apple_sep, which the kernel drops", None,
              "root=x module_blacklist=" + "m" * 1015 + ",apple_sep sleepdrain.safe", UNV + "asks for it;"),
             ("the kernel's list can't be read and the command line has the blacklist only after --", None, "root=x rw -- module_blacklist=apple_sep", UNV + "doesn't ask for it;"),
             ("the kernel's list can't be read and the command line has no blacklist", None, "root=x rw sleepdrain.safe", UNV + "doesn't ask for it;"))
    for what, kparam, cmdline, want in cases:
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t)
            os.makedirs(os.path.join(t, "run/sleep-drain-safe/rw"))
            with open(os.path.join(t, "run/sleep-drain-safe/active"), "w") as f:
                f.write("/dev/null\n")
            with open(os.path.join(t, "proc-cmdline"), "w") as f:
                f.write(cmdline + "\n")
            if kparam is not None:
                with open(os.path.join(t, "kparam"), "w") as f:
                    f.write(kparam + "\n")
            p = run(t, tool, "status")
            sep = [l for l in p.stdout.splitlines() if "Secure Enclave" in l]
            unv = want.startswith(UNV)
            check(len(sep) == 1 and sep[0].startswith(want + (" apple_sep " if unv else ": apple_sep ")) and (not unv or "is kept out of this boot (" not in sep[0]),
                  f"status in a safe boot, {what}: {'not verified, never kept out' if unv else 'may load' if want == MAY else 'kept out'}", p.stdout + p.stderr)
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
