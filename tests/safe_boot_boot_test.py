#!/usr/bin/env python3
"""sleep-drain-safe boot [--no-sep | --sep-ro] and sepro-cmdline, without root, GRUB or a boot partition.

The script is copied with its fixed paths pointed into a scratch directory (the SEP driver's directory in sysfs among them, a
directory the test fills), need_root and regular_release replaced, and
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
SEP_RO = "apple_sep.xart_writes=0 apple_sep.provision_keybag=0"
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


def setup(t, regular="root=UUID=aaaa rw quiet splash", keep_need_root=False):
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
            ("SEP_DRIVER=/sys/bus/platform/drivers/apple_sep", f"SEP_DRIVER={t}/sepdrv"),
            ("[[ -f /etc/initcpio/hooks/sleepdrain-safe && -f /etc/initcpio/install/sleepdrain-safe ]]",
             f"[[ -f {t}/etc/initcpio/hooks/sleepdrain-safe && -f {t}/etc/initcpio/install/sleepdrain-safe ]]"),
            ('need_root() { [[ $EUID -eq 0 ]] || die "run as root (pkexec)"; }', "need_root() { :; }")]
    for old, new in subs:
        assert src.count(old) == 1, old
        if not (keep_need_root and old.startswith("need_root()")):
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


PLAIN, NOSEP, SEPRO = "sleep-drain-safe", "sleep-drain-safe-nosep", "sleep-drain-safe-sepro"
MODES = (["boot"], ["boot", "--no-sep"], ["boot", "--sep-ro"])
REG = "root=UUID=aaaa rw quiet splash"


def tree(t):
    """Every file under the scratch boot partition and grub.d with its contents, and what is selected: what
    sepro-cmdline must leave exactly as it was."""
    out = {"next": nxt(t)}
    for sub in ("boot", "etc"):
        for d, _, files in os.walk(os.path.join(t, sub)):
            for f in files:
                with open(os.path.join(d, f), newline="") as fh:
                    out[os.path.join(d, f)] = fh.read()
    return out


def sepro_cmdline_tests():
    """sepro-cmdline: the line boot --sep-ro gives its entry, printed after that command's checks, with nothing written
    and no root. Held against the third entry boot writes, over the cases above that have a sep-ro line, and refused
    with boot --sep-ro's own messages over the ones it refuses."""
    MSG_CHARS, MSG_SEP, MSG_OUT = "GRUB may read another way", "has a -- in it", "the driver wouldn't load in a --sep-ro boot"
    MSG_VAR, MSG_LONG = "has a GRUB variable in it", "lose its end, where the read-only flag is"
    # need_root as it stands: this runs without root, so a need_root call would end it.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, keep_need_root=True)
        before = tree(t)
        p = run(t, tool, "sepro-cmdline")
        check(p.returncode == 0 and p.stdout == f"{REG} {SEP_RO} sleepdrain.safe\n" and p.stderr == "" and tree(t) == before and image(t) == (None, 0),
              "sepro-cmdline, without root: the regular line, the driver's two words and the flag, on one line; no script, no image, no grub.cfg change, nothing selected",
              p.stdout + p.stderr)
        q = run(t, tool, "boot", "--sep-ro")
        check(q.returncode != 0 and "run as root" in q.stderr, "(boot --sep-ro in that copy does ask for root)", q.stdout + q.stderr)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "sepro-cmdline")
        q = run(t, tool, "boot", "--sep-ro")
        cfg = cfg_of(t)
        r = run(t, tool, "sepro-cmdline")
        check(p.returncode == 0 and q.returncode == 0 and entry_line(cfg, SEPRO) == "linux /vmlinuz-linux-aurora " + p.stdout.rstrip("\n")
              and r.stdout == p.stdout and r.returncode == 0 and cfg_of(t) == cfg and nxt(t) == SEPRO,
              "sepro-cmdline before and after boot --sep-ro: the same line both times, the one the third entry has; with the safe entries in grub.cfg it still copies the regular one, and what is queued stays",
              p.stdout + r.stdout + r.stderr + cfg)
        for args in (["sepro-cmdline", "/a", "/b"], ["sepro-cmdline", "/vmlinuz-linux-aurora", "x"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and "usage: sleep-drain-safe sepro-cmdline [KERNEL]" in p.stderr and p.stdout == "", f"{' '.join(args)}: the usage, nothing printed", p.stdout + p.stderr)
        for k in ("vmlinuz", "/aurora test/vmlinuz", '/aurora-test/"x"/vmlinuz', "/aurora-test/$x/vmlinuz", "/aurora-test/x;y"):
            p = run(t, tool, "sepro-cmdline", k)
            check(p.returncode != 0 and "a kernel's path starts with /" in p.stderr and p.stdout == "",
                  f"sepro-cmdline with the kernel path {k!r}: refused, nothing printed", p.stdout + p.stderr)
        p = run(t, tool)
        check(p.returncode == 2 and "| sepro-cmdline [KERNEL] |" in p.stderr, "no command: the usage names sepro-cmdline", p.stderr)
    # The lines the third entry is made from differently: a regular line that sets either parameter already.
    words = ("apple_sep.xart_writes=1", "apple-sep.xart-writes=0", "apple_sep.provision-keybag=1", "apple_sep.xart_writes", "apple_sep.xart_writes=",
             "apple_sep.xart_writes=1 apple-sep.xart-writes=0 apple_sep.provision_keybag=1 x=1 apple_sep.xart_writes=1",
             "apple_sep.xart_guard=1 xapple_sep.xart_writes=1 apple_sep.xart_writes_x=1 foo=apple_sep.xart_writes=1 apple_sepxart_writes=1",
             "module_blacklist=apple_sep module_blacklist=foo", "foo=--bar --baz x=a--b a_b=c.d,e:f/g@h%i+j")
    for word in words:
        for tail in (" quiet", ""):
            with tempfile.TemporaryDirectory() as t:
                reg = f"root=UUID=aaaa rw {word}{tail}"
                tool = setup(t, regular=reg)
                before = tree(t)
                p = run(t, tool, "sepro-cmdline")
                untouched = tree(t) == before
                q = run(t, tool, "boot", "--sep-ro")
                third = entry_line(cfg_of(t), SEPRO) or ""
                out = p.stdout.rstrip("\n")
                check(p.returncode == 0 and q.returncode == 0 and untouched and third == "linux /vmlinuz-linux-aurora " + out and out.endswith(f" {SEP_RO} sleepdrain.safe")
                      and sum(w.split("=")[0].replace("-", "_") == "apple_sep.xart_writes" for w in out.split()) == 1
                      and sum(w.split("=")[0].replace("-", "_") == "apple_sep.provision_keybag" for w in out.split()) == 1,
                      f"sepro-cmdline with the regular line {reg[18:]!r}: the third entry's line as boot --sep-ro then writes it, one setting of each parameter, the script's",
                      p.stdout + p.stderr + third)
    # What boot --sep-ro refuses, refused with the same words, and nothing printed.
    refused = [(r, MSG_OUT) for r in ("root=UUID=aaaa rw module_blacklist=apple_sep quiet", "root=UUID=aaaa rw module_blacklist=foo module-blacklist=bar,apple_sep quiet")]
    refused += [(r, MSG_CHARS) for r in ('root=UUID=aaaa rw module-blacklist=apple_sep "module_blacklist=apple_pmp" quiet', r"root=UUID=aaaa rw systemd.mask=var-lib-sleep\x2ddrain.mount quiet",
                                         'root=UUID=aaaa rw "foo=a b" quiet', "root=UUID=aaaa rw 'foo=a b' quiet", "root=UUID=aaaa rw foo=a\\ b quiet",
                                         'root=UUID=aaaa quiet "--" single', "root=UUID=aaaa quiet \\-\\- single", "root=UUID=aaaa quiet #", "root=UUID=aaaa quiet; true",
                                         "root=UUID=aaaa quiet | x", "root=UUID=aaaa quiet {", "root=UUID=aaaa quiet\x01", "root=UUID=aaaa quiet\x7f", "root=UUID=aaaa quiet café",
                                         "root=UUID=aaaa quiet a*b", "root=UUID=aaaa quiet ~", "root=UUID=aaaa quiet\r", 'root=UUID=aaaa quiet "--"\r',
                                         "root=UUID=aaaa rw \\\n    quiet splash", "root=UUID=aaaa rw \\", 'root=UUID=aaaa rw "quiet\n    splash"', "root=UUID=aaaa rw 'quiet")]
    refused += [(r, MSG_VAR) for r in ("root=UUID=aaaa rw $extra_args quiet", "root=UUID=aaaa rw ${more} quiet")]
    refused += [(r, MSG_SEP) for r in ("root=UUID=aaaa rw quiet -- single", "root=UUID=aaaa rw quiet --", "-- root=UUID=aaaa", "root=UUID=aaaa quiet\t--\tsingle")]
    for reg, why in refused:
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t)
            # Written raw, as the cases above write theirs: a carriage return or a second line stays what it is.
            for f in ("regular", "boot/grub/grub.cfg"):
                with open(os.path.join(t, f), newline="") as fh:
                    body = fh.read()
                with open(os.path.join(t, f), "w", newline="") as fh:
                    fh.write(body.replace(f"linux /vmlinuz-linux-aurora {REG}\n", f"linux /vmlinuz-linux-aurora {reg}\n"))
            before = tree(t)
            p = run(t, tool, "sepro-cmdline")
            q = run(t, tool, "boot", "--sep-ro")
            check(p.returncode != 0 and p.stdout == "" and why in p.stderr and tree(t) == before and q.returncode != 0 and why in q.stderr
                  and p.stderr.strip() == q.stderr.strip(),
                  f"sepro-cmdline with the regular line {reg!r}: refused in boot --sep-ro's own words, nothing printed, nothing written", p.stdout + p.stderr + q.stderr)

    # The length, with the kernel's own path counted: GRUB puts BOOT_IMAGE= and that path first.
    def near(n, kernel="/vmlinuz-linux-aurora"):
        """A regular line that makes the sep-ro entry's kernel line exactly n bytes long for that kernel path."""
        base = "root=UUID=aaaa rw pad="
        fixed = len(f"BOOT_IMAGE={kernel} ") + len(base) + len(f" {SEP_RO} sleepdrain.safe")
        return base + "x" * (n - fixed)

    for kernel in (None, "/aurora-test/s2r/vmlinuz", "/aurora-test/a-name-as-long-as-a-name-gets/vmlinuz"):
        for n, ok in ((2047, True), (2048, False)):
            with tempfile.TemporaryDirectory() as t:
                reg = near(n, kernel or "/vmlinuz-linux-aurora")
                tool = setup(t, regular=reg)
                before = tree(t)
                p = run(t, tool, "sepro-cmdline", *([kernel] if kernel else []))
                if ok:
                    check(p.returncode == 0 and p.stdout == f"{reg} {SEP_RO} sleepdrain.safe\n" and tree(t) == before,
                          f"sepro-cmdline {kernel or ''}: a line that lands on the last byte that fits with that kernel's path ({n}): printed", p.stdout[-200:] + p.stderr)
                else:
                    check(p.returncode != 0 and p.stdout == "" and MSG_LONG in p.stderr and tree(t) == before,
                          f"sepro-cmdline {kernel or ''}: one byte over with that kernel's path: refused, nothing printed", p.stdout[-200:] + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        # A line that fits the regular kernel's entry and not a longer path's.
        tool = setup(t, regular=near(2047))
        p, q = run(t, tool, "sepro-cmdline"), run(t, tool, "sepro-cmdline", "/aurora-test/s2r/vmlinuz")
        check(p.returncode == 0 and q.returncode != 0 and MSG_LONG in q.stderr and q.stdout == "",
              "a line that just fits the regular kernel's entry: printed for it, refused for a test kernel's longer path", p.stderr + q.stderr)
    # No regular entry to copy; no grub.cfg (a safe boot doesn't mount /boot).
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        run(t, tool, "boot", "--sep-ro")
        with open(os.path.join(t, "boot/grub/grub.cfg")) as f:
            cfg = f.read()
        with open(os.path.join(t, "boot/grub/grub.cfg"), "w") as f:
            f.write(cfg.replace(f"    linux /vmlinuz-linux-aurora {REG}\n", ""))
        p = run(t, tool, "sepro-cmdline")
        check(p.returncode != 0 and "no /vmlinuz-linux-aurora entry" in p.stderr and p.stdout == "",
              "sepro-cmdline with only the safe entries' lines in grub.cfg: refused; a safe entry isn't the line to copy", p.stdout + p.stderr)
        os.remove(os.path.join(t, "boot/grub/grub.cfg"))
        p = run(t, tool, "sepro-cmdline")
        check(p.returncode != 0 and "can't read" in p.stderr and "grub.cfg" in p.stderr and p.stdout == "", "sepro-cmdline with no grub.cfg to read: refused, nothing printed", p.stdout + p.stderr)


def main():
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot")
        cfg = cfg_of(t)
        check(p.returncode == 0 and entry_line(cfg, PLAIN) == f"linux /vmlinuz-linux-aurora {REG} sleepdrain.safe"
              and entry_line(cfg, NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe" and cfg.count(NO_SEP) == 1
              and entry_line(cfg, SEPRO) == f"linux /vmlinuz-linux-aurora {REG} {SEP_RO} sleepdrain.safe" and cfg.count("apple_sep.") == 2
              and nxt(t) == PLAIN and "without the Secure Enclave" not in p.stdout and "read-only (apple_sep" not in p.stdout
              and "the next restart is a safe boot" in p.stdout
              and f"linux /vmlinuz-linux-aurora {REG}\n" in cfg,
              "boot: the three entries are written, each with its own id, the blacklist in the second alone and the driver's two parameters, both with the module's prefix, in the third alone; the plain one is selected; the regular entry is as it was",
              p.stdout + p.stderr + cfg)
        p = run(t, tool, "boot", "--sep-ro")
        check(p.returncode == 0 and cfg_of(t) == cfg and nxt(t) == SEPRO
              and f"with the Secure Enclave's driver asked to open its store read-only ({SEP_RO})" in p.stdout
              and "sleep-drain-safe status" in p.stdout and "without the Secure Enclave" not in p.stdout,
              "boot --sep-ro: grub.cfg isn't rewritten, the third entry is selected, and the message says what was asked of the driver and where to check it",
              p.stdout + p.stderr)
        p = run(t, tool, "status")
        check("the next restart is a safe boot with the Secure Enclave's driver asked to open its store read-only" in p.stdout,
              "status after it: the next restart is the third entry's", p.stdout + p.stderr)
        # That selection must outlast a later command that fails, and one whose grub-reboot writes nothing.
        stub(os.path.join(t, "bin"), "grub-reboot", "exit 1\n")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and "grub-reboot failed; nothing new selected" in p.stderr and nxt(t) == SEPRO
              and entry_line(cfg_of(t), SEPRO) == f"linux /vmlinuz-linux-aurora {REG} {SEP_RO} sleepdrain.safe",
              "boot --no-sep whose grub-reboot fails after boot --sep-ro: the queued entry still has its two parameters and is still the one selected",
              p.stdout + p.stderr)
        os.remove(os.path.join(t, "next"))
        stub(os.path.join(t, "bin"), "grub-reboot", "exit 0\n")
        p = run(t, tool, "boot", "--sep-ro")
        check(p.returncode != 0 and f"the next entry isn't {SEPRO}: none is set" in p.stderr and "the next restart is" not in p.stdout,
              "boot --sep-ro with a grub-reboot that returns 0 without writing the selection: refused, no boot announced", p.stdout + p.stderr)
        stub(os.path.join(t, "bin"), "grub-reboot", f'echo "$1" > {t}/next\n')
        run(t, tool, "boot")
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
        for args in (["boot", "--nosep"], ["boot", "--no-sep", "extra"], ["boot", "extra"], ["boot", "--sepro"], ["boot", "--sep-ro", "extra"],
                     ["boot", "--no-sep", "--sep-ro"], ["boot", "--sep-ro", "--no-sep"], ["boot", "--sep-ro", "--sep-ro"]):
            p = run(t, tool, *args)
            check(p.returncode != 0 and "usage: sleep-drain-safe boot [--no-sep | --sep-ro]" in p.stderr and cfg_of(t) == cfg and nxt(t) is None,
                  f"{' '.join(args)}: refused with the usage, grub.cfg untouched, nothing selected", p.stdout + p.stderr)
        p = run(t, tool)
        check(p.returncode == 2 and "boot [--no-sep | --sep-ro] | status" in p.stderr, "no command: the usage names boot [--no-sep | --sep-ro]", p.stderr)
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
    # A two-entry script and grub.cfg, as the version before --sep-ro wrote them: the third entry isn't there, so boot --sep-ro
    # regenerates, and a regeneration that fails leaves everything and selects nothing.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        p = run(t, tool, "boot", "--no-sep")
        sp = os.path.join(t, "etc/grub.d/44_sleep_drain_safe")
        full = open(sp).read()
        at = full.index("menuentry 'Safe boot for sleep experiments (disk read-only, Secure Enclave driver asked")
        two = full[:at] + "MENU\n"
        assert full[at:].count("menuentry") == 1 and full[at:].rstrip().endswith("MENU")
        with open(sp, "w") as f:
            f.write(two)
        subprocess.run([os.path.join(t, "bin/grub-mkconfig"), "-o", os.path.join(t, "boot/grub/grub.cfg")], check=True)
        before = cfg_of(t)
        mk = os.path.join(t, "bin", "grub-mkconfig")
        good = open(mk).read()
        stub(os.path.join(t, "bin"), "grub-mkconfig", "exit 1\n")
        p = run(t, tool, "boot", "--sep-ro")
        check(p.returncode != 0 and "nothing selected" in p.stderr and cfg_of(t) == before and nxt(t) == NOSEP and open(sp).read() == two
              and entry_line(before, SEPRO) is None,
              "boot --sep-ro over a two-entry grub.cfg with grub-mkconfig failing: refused; grub.cfg and the script as they were, the queued no-SEP selection stays",
              p.stdout + p.stderr)
        with open(mk, "w") as f:
            f.write(good)
        p = run(t, tool, "boot", "--sep-ro")
        check(p.returncode == 0 and nxt(t) == SEPRO and entry_line(cfg_of(t), SEPRO) == f"linux /vmlinuz-linux-aurora {REG} {SEP_RO} sleepdrain.safe"
              and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {REG} {NO_SEP} sleepdrain.safe",
              "the same command once grub-mkconfig works: it regenerates with all three entries and selects the third", p.stdout + p.stderr + cfg_of(t))
    # A grub-mkconfig that drops one of the driver's two parameters: the candidate is refused before it replaces grub.cfg.
    for drop in ("apple_sep.xart_writes=0", "apple_sep.provision_keybag=0"):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t)
            p = run(t, tool, "boot", "--sep-ro")
            cfg = cfg_of(t)
            ok0 = p.returncode == 0 and nxt(t) == SEPRO
            with open(os.path.join(t, "regular")) as f:
                reg = f.read()
            with open(os.path.join(t, "regular"), "w") as f:
                f.write(reg.replace("quiet splash", "quiet splash loglevel=3"))
            with open(os.path.join(t, "boot/grub/grub.cfg"), "w") as f:
                f.write(cfg.replace(f"{REG}\n", f"{REG} loglevel=3\n"))
            cfg = cfg_of(t)
            mk = os.path.join(t, "bin", "grub-mkconfig")
            body = open(mk).read().replace('    "$s"\n', f'    "$s" | sed "s/ {drop}//"\n')
            assert body != open(mk).read()
            with open(mk, "w") as f:
                f.write(body)
            for args in MODES:
                p = run(t, tool, *args)
                check(ok0 and p.returncode != 0 and "doesn't have the safe entries as written" in p.stderr and cfg_of(t) == cfg and nxt(t) == SEPRO
                      and entry_line(cfg_of(t), SEPRO) == f"linux /vmlinuz-linux-aurora {REG} {SEP_RO} sleepdrain.safe",
                      f"{' '.join(args)} with a regeneration whose third entry would lack {drop}: refused before it replaces grub.cfg; the queued entry keeps both parameters",
                      p.stdout + p.stderr + cfg_of(t))
    # A regular line that sets either of the driver's two parameters already, in any spelling modprobe and the kernel take for
    # the module's name and the parameter's, with a value or bare: it stops no mode. The plain entry and the no-SEP entry
    # inherit the line as it is, as before; the third entry's line has the word left out and this script's two put on, so
    # that it has exactly one setting of each parameter, and neither an earlier nor a later word can be the one the driver takes.
    def settings(line, name):
        return sum(w.split("=")[0].replace("-", "_") == "apple_sep." + name for w in line.split())

    words = ("apple_sep.xart_writes=1", "apple_sep.xart_writes=0", "apple-sep.xart_writes=1", "apple_sep.xart-writes=1", "apple-sep.xart-writes=0",
             "apple_sep.provision_keybag=1", "apple-sep.provision_keybag=0", "apple_sep.provision-keybag=1", "apple_sep.xart_writes",
             "apple_sep.xart_writes=", "apple_sep.xart_writes=1 apple-sep.xart-writes=0 apple_sep.provision_keybag=1 x=1 apple_sep.xart_writes=1")
    for word in words:
        for tail in (" quiet", ""):
            with tempfile.TemporaryDirectory() as t:
                reg = f"root=UUID=aaaa rw {word}{tail}"
                rest = " ".join(w for w in reg.split() if w.split("=")[0].replace("-", "_") not in ("apple_sep.xart_writes", "apple_sep.provision_keybag"))
                tool = setup(t, regular=reg)
                for args, want in ((["boot"], PLAIN), (["boot", "--no-sep"], NOSEP), (["boot", "--sep-ro"], SEPRO)):
                    p = run(t, tool, *args)
                    cfg = cfg_of(t)
                    third = entry_line(cfg, SEPRO) or ""
                    check(p.returncode == 0 and nxt(t) == want and entry_line(cfg, PLAIN) == f"linux /vmlinuz-linux-aurora {reg} sleepdrain.safe"
                          and entry_line(cfg, NOSEP) == f"linux /vmlinuz-linux-aurora {reg} {NO_SEP} sleepdrain.safe"
                          and third == f"linux /vmlinuz-linux-aurora {rest} {SEP_RO} sleepdrain.safe"
                          and settings(third, "xart_writes") == 1 and settings(third, "provision_keybag") == 1
                          and ("left out of this entry" in p.stdout) == (want == SEPRO),
                          f"{' '.join(args)} with the regular line {reg[18:]!r}: taken; the plain and the no-SEP entry inherit the line, and the third entry"
                          " has one setting of each of the two parameters, this script's" + (", and the command says the line's own was left out" if want == SEPRO else ""),
                          p.stdout + p.stderr + cfg)
    with tempfile.TemporaryDirectory() as t:
        reg = "root=UUID=aaaa rw apple_sep.xart_guard=1 xapple_sep.xart_writes=1 apple_sep.xart_writes_x=1 foo=apple_sep.xart_writes=1 apple_sepxart_writes=1 quiet"
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--sep-ro")
        check(p.returncode == 0 and nxt(t) == SEPRO and entry_line(cfg_of(t), SEPRO) == f"linux /vmlinuz-linux-aurora {reg} {SEP_RO} sleepdrain.safe",
              "a regular line with other parameters of the driver, other modules' and words that only contain the two names: taken, the third entry is that line and the two words",
              p.stdout + p.stderr + cfg_of(t))
    # --sep-ro alone on a regular line whose own blacklist keeps apple_sep out: the driver wouldn't load, so that boot isn't the
    # one asked for. The other two modes go on as before, and write the third entry with the others.
    for reg in ("root=UUID=aaaa rw module_blacklist=apple_sep quiet", "root=UUID=aaaa rw module_blacklist=foo module-blacklist=bar,apple_sep quiet"):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t, regular=reg)
            before = cfg_of(t)
            p = run(t, tool, "boot", "--sep-ro")
            check(p.returncode != 0 and "the driver wouldn't load in a --sep-ro boot" in p.stderr and "nothing written, nothing selected" in p.stderr
                  and nxt(t) is None and cfg_of(t) == before and image(t) == (None, 0),
                  f"boot --sep-ro with the regular line {reg[18:]!r}: refused, nothing written, no image built", p.stdout + p.stderr)
            for args, want in ((["boot"], PLAIN), (["boot", "--no-sep"], NOSEP)):
                p = run(t, tool, *args)
                check(p.returncode == 0 and nxt(t) == want and entry_line(cfg_of(t), SEPRO) == f"linux /vmlinuz-linux-aurora {reg} {SEP_RO} sleepdrain.safe",
                      f"{' '.join(args)} with that line: taken as before, the third entry written with the others", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = "root=UUID=aaaa rw module_blacklist=apple_sep module_blacklist=foo quiet"
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--sep-ro")
        check(p.returncode == 0 and nxt(t) == SEPRO, "boot --sep-ro with apple_sep only in an earlier blacklist, which the kernel drops: taken", p.stdout + p.stderr)
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
    # Quoting and backslashes: GRUB takes them off, so a later quoted blacklist would win unseen. A GRUB variable: only known
    # at boot. Refused in both modes, with or without a blacklist, and before the image is built: nothing at all is written.
    for reg in ('root=UUID=aaaa rw module-blacklist=apple_sep "module_blacklist=apple_pmp" quiet',
                r"root=UUID=aaaa rw systemd.mask=var-lib-sleep\x2ddrain.mount quiet",
                'root=UUID=aaaa rw "foo=a b" quiet',
                'root=UUID=aaaa rw "foo=a b" pad=' + "x" * 1100,
                "root=UUID=aaaa rw module_blacklist=apple_sep 'foo=a b' quiet",
                'root=UUID=aaaa rw module_blacklist=apple_sep foo=a\\ b quiet',
                'root=UUID=aaaa rw module_blacklist=apple_sep $extra_args quiet',
                'root=UUID=aaaa rw ${more} quiet'):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t, regular=reg)
            before = cfg_of(t)
            for args in MODES:
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
        for args in MODES:
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
        for args in MODES:
            p = run(t, tool, *args)
            check(p.returncode != 0 and "lose its end, where the read-only flag is" in p.stderr and nxt(t) is None and cfg_of(t) == before
                  and image(t) == (None, 0),
                  f"{' '.join(args)} with a regular line whose plain entry just fits and whose other two wouldn't: refused, nothing written, no image built", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular=near(2048))
        p = run(t, tool, "boot")
        check(p.returncode != 0 and "lose its end" in p.stderr and nxt(t) is None, "a plain entry one byte over: refused", p.stdout + p.stderr)
    # The third entry is the longest of the three (its two words are 26 bytes more than the blacklist), and all three are
    # written every time: so it is the one that has to fit, in every mode.
    with tempfile.TemporaryDirectory() as t:
        reg = near(2047 - len(f" {SEP_RO}"), tail="module_blacklist=apple_sep ")
        tool = setup(t, regular=reg)
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode == 0 and nxt(t) == NOSEP and entry_line(cfg_of(t), NOSEP) == f"linux /vmlinuz-linux-aurora {reg} sleepdrain.safe",
              "a line that already keeps apple_sep out, whose third entry lands on the last byte that fits: the no-SEP entry is that line, not a longer one, and is selected", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = near(2047 - len(f" {SEP_RO}"))
        tool = setup(t, regular=reg)
        for args, want in ((["boot", "--sep-ro"], SEPRO), (["boot", "--no-sep"], NOSEP), (["boot"], PLAIN)):
            p = run(t, tool, *args)
            check(p.returncode == 0 and nxt(t) == want and entry_line(cfg_of(t), SEPRO).endswith(f" {SEP_RO} sleepdrain.safe")
                  and len("BOOT_IMAGE=/vmlinuz-linux-aurora " + entry_line(cfg_of(t), SEPRO)[len("linux /vmlinuz-linux-aurora "):]) == 2047,
                  f"{' '.join(args)} with a line whose third entry lands exactly on the last byte that fits (2047 with the kernel's own prefix): taken", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t, regular=near(2048 - len(f" {SEP_RO}")))
        before = cfg_of(t)
        for args in MODES:
            p = run(t, tool, *args)
            check(p.returncode != 0 and "lose its end, where the read-only flag is" in p.stderr and nxt(t) is None and cfg_of(t) == before
                  and image(t) == (None, 0),
                  f"{' '.join(args)} with a line whose third entry is one byte over, though the other two fit: refused, nothing written, no image built", p.stdout + p.stderr)
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
        check(p.returncode != 0 and "GRUB may read another way" in p.stderr and nxt(t) is None and image(t) == (None, 0),
              "a regular line that fits counted in characters and not in bytes: refused (for its characters outside ASCII, before its length is asked)",
              p.stdout + p.stderr)
    # The read-back itself failing: the selection is unknown, and the message says that, not "not selected".
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        stub(os.path.join(t, "bin"), "grub-editenv", "exit 1\n")
        p = run(t, tool, "boot", "--no-sep")
        check(p.returncode != 0 and "couldn't be read back" in p.stderr and "isn't known" in p.stderr and "Not selected" not in p.stderr
              and "the next restart is" not in p.stdout,
              "a grub-editenv that can't read the block after grub-reboot: the command fails saying the selection isn't known", p.stdout + p.stderr)
    # What follows "--" goes to init: a blacklist added at the end would set nothing in the kernel. Refused, in both modes.
    # And what GRUB would make a bare -- of, with the quotes and backslashes it takes off: refused for those characters.
    for reg in ("root=UUID=aaaa rw quiet -- single", "root=UUID=aaaa rw quiet --", "-- root=UUID=aaaa", "root=UUID=aaaa quiet\t--\tsingle",
                'root=UUID=aaaa quiet "--" single', "root=UUID=aaaa quiet '--' single", 'root=UUID=aaaa quiet -"-" single',
                "root=UUID=aaaa quiet \\-\\- single", 'root=UUID=aaaa "foo=a -- b" quiet'):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t, regular=reg)
            before = cfg_of(t)
            for args in MODES:
                p = run(t, tool, *args)
                why = "GRUB may read another way" if any(c in reg for c in "\"'\\") else "has a -- in it"
                check(p.returncode != 0 and why in p.stderr and "nothing selected" in p.stderr and nxt(t) is None
                      and cfg_of(t) == before and image(t) == (None, 0),
                      f"{' '.join(args)} with the regular line {reg!r}: refused, nothing written, no image built", p.stdout + p.stderr)
    # What GRUB reads another way than this script: it drops a carriage return (so a quoted -- before one is a bare -- to
    # the kernel, and not a word here), starts a comment at #, ends the command at ;. Refused, in both modes, whatever else
    # the line has, with a selection queued earlier and its image left as they were.
    for reg in ('root=UUID=aaaa quiet "--"\r', "root=UUID=aaaa quiet --\r", "root=UUID=aaaa quiet\r", "root=UUID=aaaa quiet #", "root=UUID=aaaa quiet # --",
                "root=UUID=aaaa quiet; true", "root=UUID=aaaa quiet | x", "root=UUID=aaaa quiet {", "root=UUID=aaaa quiet\x01", "root=UUID=aaaa quiet\x7f",
                "root=UUID=aaaa quiet caf\u00e9", "root=UUID=aaaa quiet a*b", "root=UUID=aaaa quiet ~",
                # A command GRUB carries on to the next line, of which only the first is copied: a backslash at the end, a quote left open.
                "root=UUID=aaaa rw \\\n    quiet splash", "root=UUID=aaaa rw \\", 'root=UUID=aaaa rw "quiet\n    splash"', "root=UUID=aaaa rw 'quiet"):
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t)
            p = run(t, tool, "boot", "--no-sep")
            cfg, img = cfg_of(t), image(t)
            ok0 = p.returncode == 0 and nxt(t) == NOSEP and img == ("image\n", 1)
            for f in ("regular", "boot/grub/grub.cfg"):
                with open(os.path.join(t, f), newline="") as fh:
                    body = fh.read()
                assert body.count(f"linux /vmlinuz-linux-aurora {REG}\n") == 1
                with open(os.path.join(t, f), "w", newline="") as fh:
                    fh.write(body.replace(f"linux /vmlinuz-linux-aurora {REG}\n", f"linux /vmlinuz-linux-aurora {reg}\n"))
            cfg = cfg_of(t)
            for args in MODES:
                p = run(t, tool, *args)
                check(ok0 and p.returncode != 0 and "GRUB may read another way" in p.stderr and "nothing selected" in p.stderr and nxt(t) == NOSEP
                      and cfg_of(t) == cfg and image(t) == img,
                      f"{' '.join(args)} with the regular line {reg!r}: refused, the queued selection, grub.cfg and the image as they were", p.stdout + p.stderr)
    with tempfile.TemporaryDirectory() as t:
        reg = "root=UUID=aaaa rw quiet foo=--bar --baz x=a--b a_b=c.d,e:f/g@h%i+j"
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
            sep = [l for l in p.stdout.splitlines() if "Secure Enclave's driver" in l]
            unv = want.startswith(UNV)
            check(len(sep) == 1 and sep[0].startswith(want + (" apple_sep " if unv else ": apple_sep ")) and (not unv or "is kept out of this boot (" not in sep[0]),
                  f"status in a safe boot, {what}: {'not verified, never kept out' if unv else 'may load' if want == MAY else 'kept out'}", p.stdout + p.stderr)
    # status in a safe boot, the enclave's store: "read-only" only on the bound driver's own word, diag/xart of every device
    # bound to apple_sep, at least one, each exactly "disabled" and a newline; "enabled" said as that; anything else not
    # verified. What the command line asks for is said beside it and proves nothing: both parameters 0 as last given,
    # nothing after a "--" counted.
    RO = "the Secure Enclave's store (xART) is read-only in this boot: the bound driver's diag/xart reads \"disabled\"."
    RO2 = "the Secure Enclave's store (xART) is read-only in this boot: the bound driver's diag/xart reads \"disabled\", for each of its 2 devices."
    OPEN = "the Secure Enclave's store (xART) is OPEN TO WRITES in this boot: the bound driver's diag/xart reads \"enabled\"."
    UNV = "NOT VERIFIED whether the Secure Enclave's store (xART) is read-only in this boot: "
    NODEV = UNV + "no device is bound to apple_sep, so nothing says how the store is open."
    NOREAD = UNV + "396400000.sep's diag/xart isn't there or can't be read."
    ASKS, NOASK = f" The command line asks for a read-only store ({SEP_RO})", " The command line doesn't ask for a read-only store"
    WITH = f"root=x rw {SEP_RO} sleepdrain.safe"
    DEV, DEV2 = "396400000.sep", "396500000.sep"

    def odd(v):
        return UNV + f"396400000.sep's diag/xart reads {v}, which is neither \"enabled\" nor \"disabled\" and a newline."

    # devices: None for no driver directory at all, or {name: the attribute's bytes, None for no attribute, a tuple for a directory in its place}
    store = (("one bound device says disabled, and the command line has the two words", {DEV: b"disabled\n"}, WITH, RO + ASKS),
             ("the same in a boot whose command line doesn't ask", {DEV: b"disabled\n"}, "root=x rw sleepdrain.safe", RO + NOASK),
             ("the device says enabled though the command line has the two words", {DEV: b"enabled\n"}, WITH, OPEN + ASKS),
             ("the driver isn't there at all", None, WITH, NODEV + ASKS),
             ("the driver is there with no device bound", {}, WITH, NODEV + ASKS),
             ("the only thing with the attribute is the driver's link to its module", {"module": b"disabled\n"}, WITH, NODEV + ASKS),
             ("the bound device has no such attribute", {DEV: None}, WITH, NOREAD + ASKS),
             ("the attribute can't be read", {DEV: ()}, WITH, NOREAD + ASKS),
             ("the attribute can't be read and the command line doesn't ask", {DEV: ()}, "root=x rw sleepdrain.safe", NOREAD + NOASK),
             ("the attribute is empty", {DEV: b""}, WITH, odd("''") + ASKS),
             ("the attribute says disabled with no newline", {DEV: b"disabled"}, WITH, odd("disabled") + ASKS),
             ("the attribute says disabled and something after it", {DEV: b"disabled\nenabled\n"}, WITH, odd("$'disabled\\nenabled\\n'") + ASKS),
             ("the attribute says disabled and a second newline", {DEV: b"disabled\n\n"}, WITH, odd("$'disabled\\n\\n'") + ASKS),
             ("the attribute says disabled with a space", {DEV: b"disabled \n"}, WITH, odd("$'disabled \\n'") + ASKS),
             ("the attribute says DISABLED", {DEV: b"DISABLED\n"}, WITH, odd("$'DISABLED\\n'") + ASKS),
             ("the attribute says 0", {DEV: b"0\n"}, WITH, odd("$'0\\n'") + ASKS),
             ("two bound devices both say disabled", {DEV: b"disabled\n", DEV2: b"disabled\n"}, WITH, RO2 + ASKS),
             ("one of two says enabled", {DEV: b"disabled\n", DEV2: b"enabled\n"}, WITH, OPEN + ASKS),
             ("the first of two says enabled", {DEV: b"enabled\n", DEV2: b"disabled\n"}, WITH, OPEN + ASKS),
             ("one of two has no attribute", {DEV: None, DEV2: b"disabled\n"}, WITH, NOREAD + ASKS),
             ("the command line has the two words only after --", {DEV: b"disabled\n"}, f"root=x rw -- {SEP_RO}", RO + NOASK),
             ("the command line has xart_writes=0 alone", {DEV: b"disabled\n"}, "root=x rw apple_sep.xart_writes=0", RO + NOASK),
             ("the command line has a later apple_sep.xart_writes=1", {DEV: b"disabled\n"}, f"root=x {SEP_RO} apple_sep.xart_writes=1", RO + NOASK),
             ("the command line has a later apple-sep.xart-writes=1, the other spelling", {DEV: b"disabled\n"}, f"root=x {SEP_RO} apple-sep.xart-writes=1", RO + NOASK),
             ("the command line has the two words in the hyphen spellings", {DEV: b"disabled\n"}, "root=x apple-sep.xart-writes=0 apple_sep.provision-keybag=0", RO + ASKS),
             ("the command line has the two words without the module's prefix", {DEV: b"disabled\n"}, "root=x xart_writes=0 provision_keybag=0", RO + NOASK),
             ("the command line asks and the device says enabled all the same", {DEV: b"enabled\n"}, f"root=x {SEP_RO}", OPEN + ASKS))

    def fill(t, devices):
        if devices is None:
            return
        drv = os.path.join(t, "sepdrv")
        os.makedirs(drv)
        for f in ("bind", "unbind", "uevent"):
            open(os.path.join(drv, f), "w").close()
        if "module" not in devices:
            os.makedirs(os.path.join(drv, "module", "parameters"))
        for name, val in devices.items():
            os.makedirs(os.path.join(drv, name, "diag"))
            if isinstance(val, tuple):
                os.makedirs(os.path.join(drv, name, "diag", "xart"))
            elif val is not None:
                with open(os.path.join(drv, name, "diag", "xart"), "wb") as f:
                    f.write(val)

    for what, devices, cmdline, want in store:
        with tempfile.TemporaryDirectory() as t:
            tool = setup(t)
            os.makedirs(os.path.join(t, "run/sleep-drain-safe/rw"))
            with open(os.path.join(t, "run/sleep-drain-safe/active"), "w") as f:
                f.write("/dev/null\n")
            with open(os.path.join(t, "proc-cmdline"), "w") as f:
                f.write(cmdline + "\n")
            fill(t, devices)
            p = run(t, tool, "status")
            got = [l for l in p.stdout.splitlines() if "Secure Enclave's store" in l]
            said_ro = any(l.startswith("the Secure Enclave's store (xART) is read-only") for l in p.stdout.splitlines())
            check(got == [want] and said_ro == want.startswith("the Secure Enclave's store (xART) is read-only"),
                  f"status in a safe boot, the store, {what}: {'read-only' if said_ro else 'OPEN TO WRITES' if want.startswith(OPEN) else 'not verified'},"
                  f" and the command line {'asks' if want.endswith(ASKS) else 'does not ask'}", p.stdout + p.stderr)
    # In a regular boot status says nothing of the store.
    with tempfile.TemporaryDirectory() as t:
        tool = setup(t)
        fill(t, {DEV: b"disabled\n"})
        p = run(t, tool, "status")
        check("this boot is a regular boot" in p.stdout and "Secure Enclave" not in p.stdout, "status in a regular boot: no line about the driver or the store", p.stdout + p.stderr)
    sepro_cmdline_tests()
    print("all ok" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
