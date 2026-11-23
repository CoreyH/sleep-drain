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
    stub(b, "mkinitcpio", 'while (($#)); do [[ $1 == -g ]] && { echo image > "$2"; }; shift; done\n')
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
              "a regular line whose last module_blacklist already names apple_sep: both entries are that line, and the message says the driver is out anyway",
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
        check(p.returncode == 0 and entry_line(cfg_of(t), NOSEP) == same and "already keeps apple_sep out" in p.stdout,
              "the hyphen spelling with apple_sep in its list is seen as keeping the driver out", p.stdout + p.stderr + cfg_of(t))
    # A backslash in the regular line must compare as it is.
    with tempfile.TemporaryDirectory() as t:
        reg = r"root=UUID=aaaa rw systemd.mask=var-lib-sleep\x2ddrain.mount quiet"
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {reg} {NO_SEP} sleepdrain.safe",
              "a regular line with a backslash escape in it: the entries are written and found as written", p.stdout + p.stderr + cfg_of(t))
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
