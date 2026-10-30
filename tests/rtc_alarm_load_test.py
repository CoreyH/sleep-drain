#!/usr/bin/python3 -I
"""Hardware-free checks for rtc-alarm-test/load: the real script, as root in a
bubblewrap sandbox, against a fake /sys and device tree.

  python3 -I tests/rtc_alarm_load_test.py      (from the repo root)

Stub rmmod/insmod/modprobe model the drivers: a module can load with its probe
failed, two modules with the same driver name can't both be loaded, and taking
the SPMI controller away while a holder of the PMU's nvmem cells is still bound
is recorded as a hazard. Each case injects one failure and checks that the swap
stops there, goes back to the stock drivers, and exits non-zero.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOAD = os.path.join(ROOT, "rtc-alarm-test", "load")

# Platform device each module binds, and modules that can't be loaded together
DEVICE = {
    "spmi_apple_controller": "2920a1300.spmi", "spmi_apple_wake": "2920a1300.spmi",
    "macsmc_reboot": "macsmc-reboot",
    "rtc_macsmc": "macsmc-rtc", "rtc_macsmc_alarm": "macsmc-rtc",
}
CONTROLLERS = ("spmi_apple_controller", "spmi_apple_wake")

STUB = r'''#!/bin/bash
# Fake module tool: $0 is rmmod, insmod or modprobe. FAIL picks one failure:
# rmmod:<module>, probe:<module> (loads, doesn't bind) or nvmem.
S=/sys; P=$S/bus/platform/devices; NV=$S/bus/nvmem/devices/spmi_nvmem0
DT=$S/firmware/devicetree/base/soc/spmi@2920a1300
log() { echo "$*" >> "$LOG"; }
dev_of() { case $1 in
  spmi_apple_controller|spmi_apple_wake) echo 2920a1300.spmi ;;
  macsmc_reboot) echo macsmc-reboot ;;
  rtc_macsmc|rtc_macsmc_alarm) echo macsmc-rtc ;;
esac; }
rival_of() { case $1 in
  spmi_apple_controller) echo spmi_apple_wake ;; spmi_apple_wake) echo spmi_apple_controller ;;
  rtc_macsmc) echo rtc_macsmc_alarm ;; rtc_macsmc_alarm) echo rtc_macsmc ;;
esac; }
bound_to() { [[ $(readlink -f "$P/$1/driver/module" 2>/dev/null) == "$S/module/$2" ]]; }
bind() {  # bind <module>
  local m=$1 d; d=$(dev_of "$m")
  [[ -n $d ]] || return 0
  [[ $FAIL == "probe:$m" ]] && { log "probe-failed $m"; return 0; }
  case $m in
    rtc_macsmc*|macsmc_reboot) [[ -e $NV ]] || { log "probe-deferred $m"; return 0; } ;;
  esac
  mkdir -p "$S/bus/platform/drivers/$m"
  ln -sfn "$S/module/$m" "$S/bus/platform/drivers/$m/module"
  ln -sfn "$S/bus/platform/drivers/$m" "$P/$d/driver"
  case $m in
    spmi_apple_*) if [[ $m == spmi_apple_controller || $FAIL != nvmem ]]; then
        mkdir -p "$NV"; ln -sfn "$DT/pmic@f" "$NV/of_node"; fi ;;
    rtc_macsmc*) mkdir -p "$S/class/rtc/rtc0"; ln -sfn "$P/macsmc-rtc" "$S/class/rtc/rtc0/device"
      [[ $m == rtc_macsmc_alarm ]] && touch "$S/class/rtc/rtc0/wakealarm" ;;
  esac
}
load() {  # load <module>
  local m=$1 r; r=$(rival_of "$m")
  [[ -d $S/module/$m ]] && return 0
  if [[ -n $r && -d $S/module/$r ]]; then log "conflict $m"; return 1; fi
  mkdir -p "$S/module/$m"; log "load $m"
  [[ $m == pmu_alarm_dt ]] && touch "$DT/interrupt-controller"
  bind "$m"
}
case ${0##*/} in
  rmmod)
    m=$1; [[ $FAIL == "rmmod:$m" ]] && { log "rmmod-failed $m"; exit 1; }
    [[ -d $S/module/$m ]] || exit 1
    case $m in spmi_apple_*)
      if bound_to macsmc-reboot macsmc_reboot || bound_to macsmc-rtc rtc_macsmc ||
         bound_to macsmc-rtc rtc_macsmc_alarm; then log "HAZARD $m removed under nvmem holders"; fi
      rm -rf "$NV" ;;
    esac
    d=$(dev_of "$m"); [[ -n $d ]] && bound_to "$d" "$m" && rm -f "$P/$d/driver"
    [[ $m == rtc_macsmc* ]] && rm -rf "$S/class/rtc/rtc0"
    rm -rf "$S/module/$m"; log "rmmod $m" ;;
  insmod) m=${1##*/}; m=${m%.ko}; load "${m//-/_}" ;;
  modprobe) load "$1" ;;
  modinfo) echo "$(uname -r) SMP preempt mod_unload aarch64" ;;
esac
'''


def make_world(tmp):
    """The fake /sys (with the device tree), the stubs, and a copy of load."""
    s = os.path.join(tmp, "sys")
    base = os.path.join(s, "firmware/devicetree/base")
    os.makedirs(os.path.join(base, "soc/spmi@2920a1300/pmic@f"))
    with open(os.path.join(base, "compatible"), "wb") as f:
        f.write(b"apple,j314s\0apple,t6000\0apple,arm-platform\0")
    for m in ("rtc_macsmc", "macsmc_reboot", "spmi_apple_controller", "apple_nvmem_spmi"):
        os.makedirs(os.path.join(s, "module", m))
    for m in ("spmi_apple_controller", "macsmc_reboot", "rtc_macsmc"):
        drv = os.path.join(s, "bus/platform/drivers", m)
        dev = os.path.join(s, "bus/platform/devices", DEVICE[m])
        os.makedirs(drv)
        os.makedirs(dev)
        os.symlink("/sys/module/" + m, os.path.join(drv, "module"))
        os.symlink("/sys/bus/platform/drivers/" + m, os.path.join(dev, "driver"))
    nv = os.path.join(s, "bus/nvmem/devices/spmi_nvmem0")
    os.makedirs(nv)
    os.symlink("/sys/firmware/devicetree/base/soc/spmi@2920a1300/pmic@f", os.path.join(nv, "of_node"))
    rtc = os.path.join(s, "class/rtc/rtc0")
    os.makedirs(rtc)
    os.symlink("/sys/bus/platform/devices/macsmc-rtc", os.path.join(rtc, "device"))

    stubs = os.path.join(tmp, "stubs")
    os.makedirs(stubs)
    with open(os.path.join(stubs, "rmmod"), "w") as f:
        f.write(STUB)
    os.chmod(os.path.join(stubs, "rmmod"), 0o755)
    for name in ("insmod", "modprobe", "modinfo"):
        os.symlink("rmmod", os.path.join(stubs, name))

    test = os.path.join(tmp, "rtc-alarm-test")
    os.makedirs(test)
    shutil.copy(LOAD, test)
    for ko in ("pmu-alarm-dt", "spmi-apple-wake", "rtc-macsmc-alarm"):
        open(os.path.join(test, ko + ".ko"), "w").close()
    return test


def run(fail):
    tmp = tempfile.mkdtemp(prefix="load-test-")
    try:
        test = make_world(tmp)
        log = os.path.join(tmp, "log")
        open(log, "w").close()
        cmd = ["bwrap", "--unshare-user", "--uid", "0", "--ro-bind", "/", "/",
               "--bind", os.path.join(tmp, "sys"), "/sys", "--dev", "/dev",
               # A /proc of its own: the host's has no device-tree link on x86
               "--tmpfs", "/proc", "--symlink", "/sys/firmware/devicetree/base", "/proc/device-tree",
               "--bind", tmp, tmp, "--setenv", "PATH", os.path.join(tmp, "stubs") + ":/usr/bin",
               "--setenv", "FAIL", fail, "--setenv", "LOG", log,
               "/bin/bash", os.path.join(test, "load")]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        actions = open(log).read().split("\n")
        # The links are absolute, into the sandbox's /sys; the fake drivers'
        # directories are named after their modules.
        bound = {}
        for dev in ("2920a1300.spmi", "macsmc-reboot", "macsmc-rtc"):
            link = os.path.join(tmp, "sys/bus/platform/devices", dev, "driver")
            bound[dev] = os.path.basename(os.readlink(link)) if os.path.islink(link) else "unbound"
        return p.returncode, p.stdout, [a for a in actions if a], bound
    finally:
        shutil.rmtree(tmp)


STOCK = {"2920a1300.spmi": "spmi_apple_controller", "macsmc-reboot": "macsmc_reboot",
         "macsmc-rtc": "rtc_macsmc"}
TEST = {"2920a1300.spmi": "spmi_apple_wake", "macsmc-reboot": "macsmc_reboot",
        "macsmc-rtc": "rtc_macsmc_alarm"}

failed = 0


def check(name, ok, rc, out, actions, bound):
    global failed
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failed += 1
        print(f"     exit {rc}, bound {bound}\n     actions: {actions}")
        print("".join("     " + l for l in out.splitlines(True)))


if __name__ == "__main__":
    probe = subprocess.run(["bwrap", "--unshare-user", "--uid", "0", "--ro-bind", "/", "/", "true"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) \
        if shutil.which("bwrap") else None
    if not probe or probe.returncode != 0:
        print("skip: needs bwrap with unprivileged user namespaces")
        sys.exit(0)

    rc, out, actions, bound = run("")
    check("no failure: the test drivers bound, nothing removed under the nvmem holders",
          rc == 0 and bound == TEST and "done: rtc0, wakealarm present" in out
          and not any(a.startswith("HAZARD") for a in actions), rc, out, actions, bound)

    cases = [
        ("rmmod:rtc_macsmc", "rtc_macsmc won't unload: stops before anything else",
         lambda a: a == ["rmmod-failed rtc_macsmc"]),
        ("rmmod:spmi_apple_controller", "controller won't unload: stops, holders reloaded",
         lambda a: not any(x.startswith("load pmu_alarm_dt") for x in a)),
        ("probe:spmi_apple_wake", "spmi-apple-wake loads but doesn't bind: removed, stock back",
         lambda a: "rmmod spmi_apple_wake" in a and "load spmi_apple_controller" in a),
        ("nvmem", "the PMU's nvmem never comes back: stock controller back",
         lambda a: "rmmod spmi_apple_wake" in a and "load spmi_apple_controller" in a),
        ("probe:rtc_macsmc_alarm", "rtc-macsmc-alarm doesn't bind: holders out before the controller",
         lambda a: a.index("rmmod macsmc_reboot", a.index("load rtc_macsmc_alarm"))
         < a.index("rmmod spmi_apple_wake")),
    ]
    for fail, name, seq_ok in cases:
        rc, out, actions, bound = run(fail)
        check(f"{fail}: {name}",
              rc != 0 and bound == STOCK and seq_ok(actions)
              and not any(a.startswith(("HAZARD", "conflict")) for a in actions)
              and "done:" not in out, rc, out, actions, bound)

    sys.exit(1 if failed else 0)
