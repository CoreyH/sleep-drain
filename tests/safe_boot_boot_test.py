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


def safe_line(t):
    cfg = open(os.path.join(t, "boot/grub/grub.cfg")).read()
    lines = [l.strip() for l in cfg.splitlines() if l.strip().startswith("linux ") and l.strip().endswith(" sleepdrain.safe")]
    return cfg, lines


def main():
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot")
        cfg, lines = safe_line(t)
        check(p.returncode == 0 and lines == ["linux /vmlinuz-linux-aurora root=UUID=aaaa rw quiet splash sleepdrain.safe"]
              and NO_SEP not in cfg and "without the Secure Enclave" not in p.stdout and "the next restart is a safe boot" in p.stdout
              and open(os.path.join(t, "next")).read().strip() == "sleep-drain-safe",
              "boot: the safe entry is the regular command line and the hook's flag, nothing about apple_sep, and it is selected", p.stdout + p.stderr + cfg)
        os.remove(os.path.join(t, "next"))
        p = run(t, tool, "boot", "--no-sep")
        cfg, lines = safe_line(t)
        check(p.returncode == 0 and lines == [f"linux /vmlinuz-linux-aurora root=UUID=aaaa rw quiet splash {NO_SEP} sleepdrain.safe"]
              and cfg.count(NO_SEP) == 1 and f"without the Secure Enclave's driver ({NO_SEP})" in p.stdout
              and open(os.path.join(t, "next")).read().strip() == "sleep-drain-safe"
              and "linux /vmlinuz-linux-aurora root=UUID=aaaa rw quiet splash\n" in cfg,
              "boot --no-sep: the safe entry alone gains module_blacklist=apple_sep, before the hook's flag; the regular entry is as it was; the message says so",
              p.stdout + p.stderr + cfg)
        os.remove(os.path.join(t, "next"))
        p = run(t, tool, "boot")
        cfg, lines = safe_line(t)
        check(p.returncode == 0 and lines == ["linux /vmlinuz-linux-aurora root=UUID=aaaa rw quiet splash sleepdrain.safe"] and NO_SEP not in cfg
              and "without the Secure Enclave" not in p.stdout,
              "a plain boot after it: the entry is written without the blacklist again", p.stdout + p.stderr + cfg)
        os.remove(os.path.join(t, "next"))
        before = open(os.path.join(t, "boot/grub/grub.cfg")).read()
        for args in (["boot", "--nosep"], ["boot", "--no-sep", "extra"], ["boot", "extra"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and "usage: sleep-drain-safe boot [--no-sep]" in p.stderr
                  and open(os.path.join(t, "boot/grub/grub.cfg")).read() == before and not os.path.exists(os.path.join(t, "next")),
                  f"{' '.join(args)}: refused with the usage, grub.cfg untouched, nothing selected", p.stdout + p.stderr)
        p = run(t, tool)
        check(p.returncode == 2 and "boot [--no-sep] | status" in p.stderr, "no command: the usage names boot [--no-sep]", p.stderr)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular=f"root=UUID=aaaa rw {NO_SEP} quiet")
        before = open(os.path.join(t, "boot/grub/grub.cfg")).read()
        for args in (["boot"], ["boot", "--no-sep"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and "already has module_blacklist=apple_sep" in p.stderr
                  and open(os.path.join(t, "boot/grub/grub.cfg")).read() == before and not os.path.exists(os.path.join(t, "next")),
                  f"{' '.join(args)} with the blacklist already on the regular entry's line: refused, nothing written or selected", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        # A grub-mkconfig that drops the blacklist from the entry: the script must not claim a boot without the driver.
        mk = os.path.join(t, "bin", "grub-mkconfig")
        body = open(mk).read().replace('    "$s"\n', f'    "$s" | sed "s/ {NO_SEP}//"\n')
        with open(mk, "w") as f:
            f.write(body)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and "doesn't have module_blacklist=apple_sep; nothing selected" in p.stderr and not os.path.exists(os.path.join(t, "next")),
              "boot --no-sep with a grub.cfg whose safe entry lacks the blacklist: refused, nothing selected", p.stdout + p.stderr)
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
