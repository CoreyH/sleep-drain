#!/usr/bin/env python3
"""e1-hook/run-e1a's and run-floor's arming and disarming, without root or a module.

Each case runs a copy of a launcher as uid 0 in a bubblewrap user namespace,
next to a fake e1_hook.ko, with its paths (E1A_* for run-e1a, FLOOR_* for
run-floor) pointed into a temp directory and stub insmod, rmmod, modinfo and
sleep-drain-reading that record their calls. insmod fills in the module's
parameters in the fake /sys/module. The module must stay loaded only after a
reading that saved and asked for the suspend; every other way out unloads it.
Both launchers get the same cases, but for the hand-off. run-floor's own: it
sets kernel.panic and panic_on_oops (in a fake /proc/sys) before the load,
refuses with pm_test set, an older build or an unknown argument, and unloads
a build that ignored its parameters; --quiet adds quiet=1 and its own label,
--m1 quiet=1 and m1=1 and its own, --m1-observe those and observe=1 and its own,
--m1-late those and pmgr_late=1 and its own, --m1-alarm quiet=1, m1=1, pmgr_late=1
and alarm=1 and its own, and --m1-hold those and hold=1 and its own (and
--m1-hold-nowatch with hold_nowatch=1), --m1-pmp --m1-hold's and pmp_out=1 and
its own (and --m1-pmp-idle with pmp_stop=0), and --m1-position --m1-hold's and
position=1 and its own, only after recfg-peek's awake pass left its mark in /run.
And after the hand-off it stays and watches a fake
/sys/power/suspend_stats: a floor leaves it waiting with the replay armed; a
suspend that fails (before any PM notifier, too) or comes back, no suspend at
all, or a signal, calls the replay off (cancel) without an unload, and
all but the signal light the keyboard.

  python3 -I tests/e1a_launcher_test.py
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
E1_HOOK = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))
RELEASE = os.uname().release

# Each launcher: its script, its environment's prefix, what it loads e1_hook
# with, its reading's label, and its own arguments.
LAUNCHERS = (
    ("run-e1a", "E1A", "replay=5", "e1a", ()),
    ("run-floor", "FLOOR", "replay=15 oneway=1", "floor", ()),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1", "floor-quiet", ("--quiet",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1", "floor-m1", ("--m1",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 observe=1", "floor-m1-observe", ("--m1-observe",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 observe=1 pmgr_late=1", "floor-m1-late", ("--m1-late",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1", "floor-m1-alarm", ("--m1-alarm",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1", "floor-m1-hold", ("--m1-hold",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1",
     "floor-m1-hold-nowatch", ("--m1-hold-nowatch",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 pmp_out=1", "floor-m1-pmp", ("--m1-pmp",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 pmp_out=1 pmp_stop=0",
     "floor-m1-pmp-idle", ("--m1-pmp-idle",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 position=1", "floor-m1-position",
     ("--m1-position",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 pmp_out=1 position=1 sio_on=1", "floor-m1-sio-pmp",
     ("--m1-sio-pmp",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 pmp_out=1 position=1 sio_on=1 disp_off=1", "floor-m1-disp-off",
     ("--m1-disp-off",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 pmp_out=1 position=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1",
     "floor-m1-macos-end", ("--m1-macos-end",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1",
     "floor-m1-sio-stop", ("--m1-sio-stop",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1",
     "floor-m1-power-words", ("--m1-power-words",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1 recfg_bite=1",
     "floor-m1-recfg-bite", ("--m1-recfg-bite",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1",
     "floor-m1-wd2-bite", ("--m1-wd2-bite",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1 cluster_ack=1",
     "floor-m1-cluster-ack", ("--m1-cluster-ack",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1 cluster_ack=1 fabric_up=1",
     "floor-m1-fabric-up", ("--m1-fabric-up",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1 cluster_ack=1 fabric_up=1 wd1_mark=1",
     "floor-m1-mark", ("--m1-mark",)),
    ("run-floor", "FLOOR", "replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1 hold_nowatch=1 pmp_out=1 sio_on=1 disp_off=1 one_commit=1 smc_idle=1 sio_stop=1 ps_words=1 wd2_bite=1 cluster_ack=1 fabric_up=1 wd1_mark=1 gates_mark=1",
     "floor-m1-gates", ("--m1-gates",)),
)

failed = 0


def name(launcher):
    return " ".join((launcher[0],) + launcher[4])


def is_m1(launcher):
    """--m1, --m1-observe, --m1-late, --m1-alarm, the two --m1-hold, the two --m1-pmp and --m1-position: all load m1=1."""
    return any(a in ("--m1", "--m1-observe", "--m1-late", "--m1-alarm", "--m1-hold", "--m1-hold-nowatch", "--m1-pmp",
                     "--m1-pmp-idle", "--m1-position", "--m1-sio-pmp", "--m1-disp-off", "--m1-macos-end", "--m1-sio-stop",
                     "--m1-power-words", "--m1-recfg-bite", "--m1-wd2-bite", "--m1-cluster-ack", "--m1-fabric-up", "--m1-mark", "--m1-gates") for a in launcher[4])


def is_gates(launcher):
    """--m1-gates: --m1-mark's run with gates_mark=1."""
    return "--m1-gates" in launcher[4]


def is_mark(launcher):
    """--m1-mark, and --m1-gates on top of it: --m1-fabric-up's run with wd1_mark=1."""
    return "--m1-mark" in launcher[4] or is_gates(launcher)


def is_fab(launcher):
    """--m1-fabric-up, and --m1-mark on top of it: --m1-cluster-ack's run with fabric_up=1."""
    return "--m1-fabric-up" in launcher[4] or is_mark(launcher)


def is_ack(launcher):
    """--m1-cluster-ack, and --m1-fabric-up on top of it: --m1-wd2-bite's run, wd2-bite's store included, with cluster_ack=1."""
    return "--m1-cluster-ack" in launcher[4] or is_fab(launcher)


def runs_wd2(launcher):
    """The launchers that run wd2-bite first and load wd2_bite=1."""
    return "--m1-wd2-bite" in launcher[4] or is_ack(launcher)


def check(cond, what, detail=""):
    global failed
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failed += 1
        if detail:
            print("".join("     " + l for l in detail.splitlines(True)))


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


PM_TEST = "[none] core processors platform devices freezer\n"
# modinfo -F parm, as for the floor's build; the floor's first build had no
# cancel, the next no quiet, the next no m1, the next no observe, the next no
# pmgr_late, the next no alarm, and E1a's no oneway either.
PARMS = {
    "floor": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
             "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
             "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
             "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
             "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
             "pmp_out:With alarm=1 (bool)\npmp_stop:With pmp_out=1 (bool)\nposition:With hold=1 (bool)\nsio_on:With position=1 (bool)\ndisp_off:With sio_on=1 (bool)\none_commit:With disp_off=1 (bool)\nsmc_idle:With disp_off=1 (bool)\nsio_stop:With disp_off=1 (bool)\nps_words:With sio_stop=1 (bool)\nrecfg_bite:With ps_words=1 (bool)\nwd2_bite:With ps_words=1 (bool)\ncluster_ack:With wd2_bite=1 (bool)\nfabric_up:With cluster_ack=1 (bool)\nwd1_mark:With fabric_up=1 (bool)\ngates_mark:With wd1_mark=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-nosiostop": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                       "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                       "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                       "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                       "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
                       "pmp_out:With alarm=1 (bool)\npmp_stop:With pmp_out=1 (bool)\nposition:With hold=1 (bool)\nsio_on:With position=1 (bool)\ndisp_off:With sio_on=1 (bool)\none_commit:With disp_off=1 (bool)\nsmc_idle:With disp_off=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-nomacend": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                      "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                      "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                      "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                      "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
                      "pmp_out:With alarm=1 (bool)\npmp_stop:With pmp_out=1 (bool)\nposition:With hold=1 (bool)\nsio_on:With position=1 (bool)\ndisp_off:With sio_on=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-nodisp": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                    "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                    "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                    "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                    "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
                    "pmp_out:With alarm=1 (bool)\npmp_stop:With pmp_out=1 (bool)\nposition:With hold=1 (bool)\nsio_on:With position=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-nosio": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                   "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                   "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                   "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                   "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
                   "pmp_out:With alarm=1 (bool)\npmp_stop:With pmp_out=1 (bool)\nposition:With hold=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-noposition": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                        "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                        "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                        "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                        "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
                        "pmp_out:With alarm=1 (bool)\npmp_stop:With pmp_out=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-nopmp": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                   "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                   "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                   "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                   "alarm:With m1=1 and pmgr_late=1 (bool)\nhold:With alarm=1 (bool)\nhold_nowatch:With hold=1 only (bool)\n"
                   "replay:LAB ONLY (uint)\n",
    "floor-nohold": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                    "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                    "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\nm1_undo:m1: 0x30 (int)\n"
                    "observe:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                    "alarm:With m1=1 and pmgr_late=1 (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-noalarm": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                     "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                     "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\n"
                     "m1_undo:m1: 0x30 (int)\nobserve:With m1=1 only (bool)\npmgr_late:With m1=1 only (bool)\n"
                     "replay:LAB ONLY (uint)\n",
    "floor-nolate": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                    "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                    "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\n"
                    "m1_undo:m1: 0x30 (int)\nobserve:With m1=1 only (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-noobserve": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                       "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\n"
                       "m1:With replay=15 oneway=1 quiet=1 only (bool)\nm1_ret:m1: the PMU (int)\n"
                       "m1_undo:m1: 0x30 (int)\nreplay:LAB ONLY (uint)\n",
    "floor-nom1": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                  "cancel:Write 1 to call the replay off (bool)\nquiet:replay=15 only (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-noquiet": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                     "cancel:Write 1 to call the replay off (bool)\nreplay:LAB ONLY (uint)\n",
    "floor-notifier": "replay_done:0 not yet (uint)\noneway:Must be 1 for replay=13 or 15 (bool)\n"
                      "replay:LAB ONLY (uint)\n",
    "e1a": "replay_done: (uint)\nreplay:LAB ONLY, safe boot only (uint)\n",
}
PARMS["floor-nopwords"] = PARMS["floor"].replace("ps_words:With sio_stop=1 (bool)\n", "")
PARMS["floor-norecfg"] = PARMS["floor"].replace("recfg_bite:With ps_words=1 (bool)\n", "")
PARMS["floor-nowd2"] = PARMS["floor"].replace("wd2_bite:With ps_words=1 (bool)\n", "")
PARMS["floor-noack"] = PARMS["floor"].replace("cluster_ack:With wd2_bite=1 (bool)\n", "")
PARMS["floor-nofab"] = PARMS["floor"].replace("fabric_up:With cluster_ack=1 (bool)\n", "")
PARMS["floor-nomark"] = PARMS["floor"].replace("wd1_mark:With fabric_up=1 (bool)\n", "")
PARMS["floor-nogates"] = PARMS["floor"].replace("gates_mark:With wd1_mark=1 (bool)\n", "")


def sysctl(tmp):
    return tuple(open(os.path.join(tmp, "sysctl", k)).read().strip() for k in ("panic", "panic_on_oops"))


def setup(tmp, launcher, reading="ok", safe=True, ac="0", loaded=False, pm_test=PM_TEST, build="floor",
          takes=True, takes_quiet=True, takes_m1=True, takes_observe=True, takes_late=True, takes_alarm=True, takes_hold=True,
          takes_nowatch=True, takes_pmp=True, takes_nostop=True, takes_position=True, awake_mark=True, takes_sio=True, pmp_rc=0, takes_disp=True, takes_macend=True, takes_siostop=True, takes_pwords=True, takes_recfg=True, bite_rc=0, takes_wd2=True, wd2_rc=0, takes_ack=True, takes_fab=True, takes_mark=True, takes_gates=True, insmod_rc=0, tools=True, pmp_stale=False, attached=None, save_rc=0, srcs=None, dmesg="line"):
    script = launcher[0]
    calls = os.path.join(tmp, "calls")
    moddir = os.path.join(tmp, "module")
    d = os.path.join(tmp, "e1-hook")
    os.makedirs(d)
    shutil.copy(os.path.join(E1_HOOK, script), os.path.join(d, script))
    write(os.path.join(d, "e1_hook.ko"), "not a module\n")
    # The module's sources beside it, as in the checkout: older than the build ("fresh"), or the named one newer.
    if srcs:
        os.utime(os.path.join(d, "e1_hook.ko"), (2000, 2000))
        for n in ("e1_hook.c", "recfg_bite.h", "m1_position.h", "wd2_bite.h", "fabric_up.h", "wd1_mark.h", "gates_mark.h", "asc_probe.h", "look.h", "gate_test.h"):
            write(os.path.join(d, n), "")
            os.utime(os.path.join(d, n), (3000, 3000) if srcs == n else (1000, 1000))
    if safe:
        write(os.path.join(tmp, "run", "active"), "")
    if awake_mark:
        write(os.path.join(tmp, "sleep-drain-position-awake"), "")
    write(os.path.join(tmp, "ac", "online"), ac + "\n")
    write(os.path.join(tmp, "pm_test"), pm_test)
    write(os.path.join(tmp, "sysctl", "panic"), "0\n")
    write(os.path.join(tmp, "sysctl", "panic_on_oops"), "0\n")
    for k, v in (("success", "0"), ("fail", "0"), ("last_failed_errno", "0"), ("last_failed_step", "")):
        write(os.path.join(tmp, "stats", k), v + "\n")
    os.makedirs(os.path.join(tmp, "log"))
    # What's attached, as sleep-drain-reading sees it: a root hub is always there and doesn't count.
    write(os.path.join(tmp, "usb", "usb1", "idVendor"), "1d6b\n")
    write(os.path.join(tmp, "usb", "usb1", "idProduct"), "0002\n")
    os.makedirs(os.path.join(tmp, "typec", "port0"))
    if attached == "usb":
        write(os.path.join(tmp, "usb", "1-1", "idVendor"), "05ac\n")
        write(os.path.join(tmp, "usb", "1-1", "idProduct"), "1234\n")
    elif attached == "typec":
        os.makedirs(os.path.join(tmp, "typec", "port0-partner"))
    write(os.path.join(tmp, "kbd", "max_brightness"), "255\n")
    write(os.path.join(tmp, "kbd", "brightness"), "0\n")
    if loaded:
        os.makedirs(moddir)
    stubs = os.path.join(tmp, "stubs")
    # The module's directory and its parameters, as the kernel shows them; a
    # build that doesn't know replay and oneway (or quiet, m1, observe,
    # pmgr_late or alarm) ignores them.
    quiet = f' quiet=1) echo Y > {moddir}/parameters/quiet ;;' if takes_quiet else ""
    quiet += f' m1=1) echo Y > {moddir}/parameters/m1 ;;' if takes_m1 else ""
    quiet += f' observe=1) echo Y > {moddir}/parameters/observe ;;' if takes_observe else ""
    quiet += f' pmgr_late=1) echo Y > {moddir}/parameters/pmgr_late ;;' if takes_late else ""
    quiet += f' alarm=1) echo Y > {moddir}/parameters/alarm ;;' if takes_alarm else ""
    quiet += f' hold=1) echo Y > {moddir}/parameters/hold ;;' if takes_hold else ""
    quiet += f' hold_nowatch=1) echo Y > {moddir}/parameters/hold_nowatch ;;' if takes_nowatch else ""
    quiet += f' pmp_out=1) echo Y > {moddir}/parameters/pmp_out ;;' if takes_pmp else ""
    quiet += f' pmp_stop=0) echo N > {moddir}/parameters/pmp_stop ;;' if takes_nostop else ""
    quiet += f' position=1) echo Y > {moddir}/parameters/position ;;' if takes_position else ""
    quiet += f' sio_on=1) echo Y > {moddir}/parameters/sio_on ;;' if takes_sio else ""
    quiet += f' disp_off=1) echo Y > {moddir}/parameters/disp_off ;;' if takes_disp else ""
    quiet += f' one_commit=1) echo Y > {moddir}/parameters/one_commit ;; smc_idle=1) echo Y > {moddir}/parameters/smc_idle ;;' if takes_macend else ""
    quiet += f' sio_stop=1) echo Y > {moddir}/parameters/sio_stop ;;' if takes_siostop else ""
    quiet += f' ps_words=1) echo Y > {moddir}/parameters/ps_words ;;' if takes_pwords else ""
    quiet += f' recfg_bite=1) echo Y > {moddir}/parameters/recfg_bite ;;' if takes_recfg else ""
    quiet += f' wd2_bite=1) echo Y > {moddir}/parameters/wd2_bite ;;' if takes_wd2 else ""
    quiet += f' cluster_ack=1) echo Y > {moddir}/parameters/cluster_ack ;;' if takes_ack else ""
    quiet += f' fabric_up=1) echo Y > {moddir}/parameters/fabric_up ;;' if takes_fab else ""
    quiet += f' wd1_mark=1) echo Y > {moddir}/parameters/wd1_mark ;;' if takes_mark else ""
    quiet += f' gates_mark=1) echo Y > {moddir}/parameters/gates_mark ;;' if takes_gates else ""
    params = (f'for a; do case $a in replay=*) echo "${{a#replay=}}" > {moddir}/parameters/replay ;;'
              f' oneway=1) echo Y > {moddir}/parameters/oneway ;;{quiet} esac; done\n') if takes else ""
    # The rest of the floor's parameters, as before any suspend; cancel is
    # write-only there, an empty file here.
    params += (f'cd {moddir}/parameters && for kv in attempts=0 prepare_late_calls=0 replay_done=0'
               f' replay_ret=1,1,1,1 eh_writes=0 post_writes=0 late_ret=1 m1_ret=1 m1_undo=1; do echo "${{kv#*=}}" > "${{kv%%=*}}";'
               f' done && : > cancel\n')
    write(os.path.join(stubs, "insmod"),
          f'#!/bin/sh\necho "insmod $*" >> {calls}\n[ {insmod_rc} = 0 ] || exit {insmod_rc}\nmkdir -p {moddir}/parameters\n{params}', 0o755)
    # --m1-recfg-bite's launcher, logged to a file of its own with what was true when it ran: its arguments, kernel.panic,
    # and whether e1_hook had been loaded by then. run-floor runs nothing else of the kind: no writer, no restore.
    if tools:
        count = f'count() {{ if [ -f {calls} ]; then grep -c "^$1" {calls}; else echo 0; fi; }}\n'
        state = f'panic=$(cat {tmp}/sysctl/panic) insmod=$(count insmod)'
        write(os.path.join(stubs, "recfg-bite"),
              f'#!/bin/sh\n{count}echo "bite [$*] {state}" >> {tmp}/recfg-calls\necho "the bite launcher ran"\nexit {bite_rc}\n', 0o755)
        # --m1-wd2-bite's, the same way.
        write(os.path.join(stubs, "wd2-bite"),
              f'#!/bin/sh\n{count}echo "wd2 [$*] {state}" >> {tmp}/wd2-calls\necho "the wd2 launcher ran"\nexit {wd2_rc}\n', 0o755)
    write(os.path.join(stubs, "rmmod"), f'#!/bin/sh\necho "rmmod $*" >> {calls}\nrm -rf {moddir}\n', 0o755)
    # The PMP's release: logged to a file of its own, and whether the reading had begun by then.
    write(os.path.join(stubs, "pmp"), f'#!/bin/sh\necho "$*" >> {tmp}/pmp-calls\ngrep -q "^reading" {calls} 2>/dev/null && echo LATE >> {tmp}/pmp-calls\nexit {pmp_rc}\n', 0o755)
    write(os.path.join(stubs, "save"), f'#!/bin/sh\necho "sleep-drain-safe $*" >> {calls}\nexit {save_rc}\n', 0o755)
    # The kernel log: a line of the module's; or none of its lines; or a dmesg that fails.
    write(os.path.join(stubs, "dmesg"), {"line": '#!/bin/sh\necho "[   12.345678] e1_hook: replay: a stub line"\n',
                                         "none": '#!/bin/sh\necho "[   12.345678] another driver: a line"\n',
                                         "fails": '#!/bin/sh\nexit 1\n'}[dmesg], 0o755)
    # pmp_sleep_release.ko is install.sh's, built for one kernel: pmp_stale is the one a kernel update leaves behind.
    stale = 'case "$3" in *pmp_sleep_release.ko) echo "0.0.0-0-OLD SMP preempt mod_unload aarch64"; exit 0 ;; esac\n' if pmp_stale else ""
    write(os.path.join(stubs, "modinfo"),
          f'#!/bin/sh\n{stale}case "$2" in\nvermagic) echo "{RELEASE} SMP preempt mod_unload aarch64" ;;\n'
          f'parm) printf "%s" "{PARMS[build]}" ;;\nesac\n', 0o755)
    mp = f"{moddir}/parameters"
    # What the kernel does with the suspend the reading asked for, a second
    # after it exits: in the background, so that the launcher is watching.
    later = {
        # It fails before PM_SUSPEND_PREPARE (a sync cut by a wakeup): only the
        # probe saw it begin, and only suspend_stats' fail moves.
        "abort": f"echo 1 > {mp}/attempts; echo -16 > {tmp}/stats/last_failed_errno; echo 1 > {tmp}/stats/fail",
        # The replay runs, stops short at MBSE=slpw, and fails the suspend.
        "eio": (f"echo 1 > {mp}/attempts; echo 1 > {mp}/prepare_late_calls; echo 1 > {mp}/replay_done;"
                f" echo -110,1,1,1 > {mp}/replay_ret; echo 31 > {mp}/eh_writes; echo -5 > {tmp}/stats/last_failed_errno;"
                f" echo 1 > {tmp}/stats/fail"),
    }
    body = {
        "ok": "exit 0",
        "fail": "exit 1",
        # Like the real reading: a long child, then the request for the suspend
        # as its last step. The child's output doesn't hold the test's pipes.
        "wait": f'echo "$PPID $$" > {tmp}/pids; sleep 30 > /dev/null 2>&1; echo "suspend" >> {calls}',
        # The suspend asked for: then the launcher watches. "handoff" leaves
        # suspend_stats alone (the test stops the launcher, or it times out).
        "handoff": f'echo "$PPID $$" > {tmp}/pids; exit 0',
        "abort": f'( sleep 1; {later["abort"]} ) > /dev/null 2>&1 & exit 0',
        "eio": f'( sleep 1; {later["eio"]} ) > /dev/null 2>&1 & exit 0',
    }[reading]
    write(os.path.join(stubs, "reading"), f'#!/bin/bash\necho "reading $*" >> {calls}\n{body}\n', 0o755)
    return d


def env(tmp, launcher, watch_s=3):
    prefix = launcher[1]
    e = {
        "PATH": os.path.join(tmp, "stubs") + ":/usr/bin:/bin",
        prefix + "_SAFE_FLAG": os.path.join(tmp, "run", "active"),
        prefix + "_AC": os.path.join(tmp, "ac"),
        prefix + "_MODDIR": os.path.join(tmp, "module"),
        prefix + "_READING": os.path.join(tmp, "stubs", "reading"),
        prefix + "_PM_TEST": os.path.join(tmp, "pm_test"),
        prefix + "_SYSCTL": os.path.join(tmp, "sysctl"),
    }
    if prefix == "FLOOR":
        e.update(FLOOR_SAVE=os.path.join(tmp, "stubs", "save"), FLOOR_STATS=os.path.join(tmp, "stats"),
                 FLOOR_LOGDIR=os.path.join(tmp, "log"), FLOOR_RUNDIR=tmp, FLOOR_WATCH_S=str(watch_s),
                 FLOOR_KBD=os.path.join(tmp, "kbd"), FLOOR_PMP=os.path.join(tmp, "stubs", "pmp"),
                 FLOOR_BITE=os.path.join(tmp, "stubs", "recfg-bite"), FLOOR_WD2_BITE=os.path.join(tmp, "stubs", "wd2-bite"), FLOOR_PMP_KO=os.path.join(tmp, "stubs", "pmp_sleep_release.ko"),
                 FLOOR_USB=os.path.join(tmp, "usb"), FLOOR_TYPEC=os.path.join(tmp, "typec"))
    return e


def cmd(d, launcher, argv=None):
    return (["bwrap", "--unshare-user", "--uid", "0", "--dev-bind", "/", "/", "bash", os.path.join(d, launcher[0])]
            + list(launcher[4] if argv is None else argv))


def calls(tmp):
    try:
        with open(os.path.join(tmp, "calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def run(tmp, d, launcher, watch_s=3, argv=None, **kw):
    return subprocess.run(cmd(d, launcher, argv), env=env(tmp, launcher, watch_s), capture_output=True, text=True,
                          timeout=60, **kw)


def cancelled(tmp):
    try:
        with open(os.path.join(tmp, "module", "parameters", "cancel")) as f:
            return f.read()
    except FileNotFoundError:
        return None


def kbd(tmp):
    return open(os.path.join(tmp, "kbd", "brightness")).read().strip()


def wait_for(path):
    for _ in range(200):
        if os.path.exists(path) and os.path.getsize(path):
            return True
        time.sleep(0.05)
    return False


def test_handoff(launcher):
    script, _, args, label, _ = launcher
    script = name(launcher)
    # run-floor makes a fault restart the Mac; run-e1a leaves both alone.
    panic = ("10", "1") if launcher[0] == "run-floor" else ("0", "0")
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="ok")
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 0 and c == ["insmod " + os.path.join(d, "e1_hook.ko") + " " + args,
                                          f"reading take --label {label} --then suspend"]
              and sysctl(tmp) == panic,
              f"{script}: a reading that hands off to the suspend leaves the replay armed ({args});"
              f" kernel.panic={panic[0]} panic_on_oops={panic[1]}",
              r.stdout + r.stderr + "\n".join(c) + f"\nsysctl {sysctl(tmp)}")


def test_floor_watch(launcher):
    script, _, args, label, _ = launcher
    script = name(launcher)

    def insmod_reading(d):
        return ["insmod " + os.path.join(d, "e1_hook.ko") + " " + args, f"reading take --label {label} --then suspend"]

    # The floor: the suspend began and nothing runs again. Killing the
    # launcher mid-watch stands in for that; the replay must still be armed.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        alive = p.poll() is None
        try:
            os.kill(launcher_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        c = calls(tmp)
        check(alive and c == insmod_reading(d) and cancelled(tmp) == "" and sysctl(tmp) == ("10", "1")
              and kbd(tmp) == "0",
              f"{script}: after the hand-off it waits for the suspend with the replay armed: no unload, no cancel,"
              " the keyboard dark; kernel.panic=10 panic_on_oops=1",
              f"alive {alive}\n" + out + err + "\n".join(c) + f"\ncancel {cancelled(tmp)!r} sysctl {sysctl(tmp)}")

    for reading, what, want in (
            ("abort", "a suspend that fails before any PM notifier (a sync cut by a wakeup)",
             ("the suspend failed or came back", "attempts=1", "errno -16")),
            ("eio", "a replay that stops short and fails its suspend",
             ("the suspend failed or came back", "replay_done=1", "replay_ret=-110,1,1,1", "eh_writes=31")),
            ("handoff", "no suspend within the watch",
             ("no suspend began within 3 s",))):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, reading=reading)
            t0 = time.monotonic()
            r = run(tmp, d, launcher)
            took = time.monotonic() - t0
            c = calls(tmp)
            logs = os.listdir(os.path.join(tmp, "log"))
            logged = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
            check(r.returncode == 2 and cancelled(tmp) == "1\n" and c[:2] == insmod_reading(d)
                  and not any(x.startswith("rmmod") for x in c)
                  and c[2:] == ["sleep-drain-safe save " + os.path.join(tmp, "log", logs[0])]
                  and "NO FLOOR" in r.stdout and all(w in r.stdout for w in want)
                  and (("m1_ret=1 m1_undo=1" in r.stdout) == is_m1(launcher))
                  and "e1_hook: replay: a stub line" in r.stdout and "NO FLOOR" in logged
                  and (reading != "handoff" or took >= 3) and kbd(tmp) == "255",
                  f"{script}: {what}: the replay is called off, not unloaded; the keyboard lit; NO FLOOR printed,"
                  " logged and saved; exit 2",
                  f"exit {r.returncode}, {took:.1f} s\n" + r.stdout + r.stderr + "\n".join(c)
                  + f"\ncancel {cancelled(tmp)!r}")

    # A signal while it watches: the replay called off, the module left loaded.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        try:
            os.kill(launcher_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        c = calls(tmp)
        check(p.returncode == 143 and cancelled(tmp) == "1\n" and c == insmod_reading(d),
              f"{script}: SIGTERM while it watches: the replay called off, no unload, exit 143",
              f"exit {p.returncode}\n" + out + err + "\n".join(c) + f"\ncancel {cancelled(tmp)!r}")


def test_failed_reading(launcher):
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="fail")
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 1 and c[-1:] == ["rmmod e1_hook"] and "unloaded" in r.stderr,
              f"{name(launcher)}: a failed reading or save unloads the module, exit 1", r.stdout + r.stderr + "\n".join(c))


def test_signals(launcher):
    for sig, code in ((signal.SIGHUP, 129), (signal.SIGTERM, 143), (signal.SIGINT, 130)):
        # A hangup or ^C reaches the whole job, reading and launcher; a kill
        # may reach the launcher only. Either way: no suspend, then the unload.
        for both in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                d = setup(tmp, launcher, reading="wait")
                p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
                pids = os.path.join(tmp, "pids")
                for _ in range(200):
                    if os.path.exists(pids) and os.path.getsize(pids):
                        break
                    time.sleep(0.05)
                launcher_pid, reading = (int(x) for x in open(pids).read().split())
                if both:
                    os.kill(reading, sig)
                try:
                    os.kill(launcher_pid, sig)
                except ProcessLookupError:
                    # The reading's end can take the launcher out first on a busy machine; the checks below still hold it to the same exit.
                    pass
                t0 = time.monotonic()
                out, err = p.communicate(timeout=30)
                c = calls(tmp)
                who = "the reading and the launcher" if both else "the launcher only"
                check(p.returncode == code and c[-1:] == ["rmmod e1_hook"] and "suspend" not in c
                      and time.monotonic() - t0 < 10,
                      f"{name(launcher)}: {signal.Signals(sig).name} to {who}: no suspend, the module unloaded, exit {code}",
                      f"exit {p.returncode}\n" + out + err + "\n".join(c))


def test_broken_stdout(launcher):
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="ok")
        rfd, wfd = os.pipe()
        os.close(rfd)
        try:
            r = subprocess.run(cmd(d, launcher), env=env(tmp, launcher), stdout=wfd, stderr=subprocess.PIPE,
                               text=True, timeout=60)
        finally:
            os.close(wfd)
        c = calls(tmp)
        if "--m1-recfg-bite" in launcher[4]:
            # Its first line of output comes before recfg-bite, so that nothing is written to the SRAM for a run that can't be seen.
            check(r.returncode != 0 and c == [] and recfg_calls(tmp) == [],
                  f"{name(launcher)}: a broken stdout stops it before recfg-bite and before any load",
                  f"exit {r.returncode}\n" + r.stderr + "\n".join(c + recfg_calls(tmp)))
            return
        if runs_wd2(launcher):
            # The same for wd2-bite: no store to WD2 for a run that can't be seen.
            check(r.returncode != 0 and c == [] and wd2_calls(tmp) == [],
                  f"{name(launcher)}: a broken stdout stops it before wd2-bite and before any load",
                  f"exit {r.returncode}\n" + r.stderr + "\n".join(c + wd2_calls(tmp)))
            return
        check(r.returncode != 0 and c[-1:] == ["rmmod e1_hook"] and not any(x.startswith("reading") for x in c),
              f"{name(launcher)}: a broken stdout after insmod unloads the module before any reading",
              f"exit {r.returncode}\n" + r.stderr + "\n".join(c))


def test_refusals(launcher):
    for what, kw, msg in (("outside a safe boot", dict(safe=False), "safe boot only"),
                          ("on the charger", dict(ac="1"), "charger"),
                          ("with e1_hook loaded already", dict(loaded=True), "loaded already")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and sysctl(tmp) == ("0", "0"),
                  f"{name(launcher)}: refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))


def test_floor_refusals(launcher):
    quiet = "--quiet" in launcher[4]
    m1 = is_m1(launcher)
    recfg = "--m1-recfg-bite" in launcher[4]
    wd2 = runs_wd2(launcher)
    ack = is_ack(launcher)
    fab = is_fab(launcher)
    mark = is_mark(launcher)
    gates = is_gates(launcher)
    pwords = "--m1-power-words" in launcher[4] or recfg or wd2
    siostop = "--m1-sio-stop" in launcher[4] or pwords
    macend = "--m1-macos-end" in launcher[4] or siostop
    disp = "--m1-disp-off" in launcher[4] or macend
    sio = "--m1-sio-pmp" in launcher[4] or disp
    pmp = "--m1-pmp" in launcher[4] or "--m1-pmp-idle" in launcher[4] or sio
    nostop = "--m1-pmp-idle" in launcher[4]
    position = ("--m1-position" in launcher[4] or sio) and not siostop
    hold = "--m1-hold" in launcher[4] or "--m1-hold-nowatch" in launcher[4] or pmp or position
    nowatch = "--m1-hold-nowatch" in launcher[4] or siostop
    alarm = "--m1-alarm" in launcher[4] or hold
    late = "--m1-late" in launcher[4] or alarm
    observe = "--m1-observe" in launcher[4] or "--m1-late" in launcher[4]
    plain = not quiet and not m1
    cases = [("with pm_test set", dict(pm_test="none core processors [platform] devices freezer\n"), None,
              "pm_test isn't none"),
             ("an older build, without oneway (E1a's)", dict(build="e1a"), None, "older build"),
             ("the floor's first build, without cancel", dict(build="floor-notifier"), None, "older build")]
    if quiet:
        cases.append(("a build without quiet", dict(build="floor-noquiet"), None, "without quiet"))
    if m1:
        cases += [("a build without quiet", dict(build="floor-noquiet"), None, "without quiet"),
                  ("a build without m1", dict(build="floor-nom1"), None, "without m1")]
    if observe:
        cases.append(("a build without observe", dict(build="floor-noobserve"), None, "without observe"))
    if late:
        cases.append(("a build without pmgr_late", dict(build="floor-nolate"), None, "without pmgr_late"))
    if alarm:
        cases.append(("a build without alarm", dict(build="floor-noalarm"), None, "without alarm"))
    if hold:
        cases.append(("a build without hold", dict(build="floor-nohold"), None, "without hold"))
    if pmp:
        cases.append(("a build without pmp_out", dict(build="floor-nopmp"), None, "without pmp_out"))
    if position:
        cases.append(("a build without position", dict(build="floor-noposition"), None, "without position"))
    if position and not sio:
        cases.append(("without the awake pass's mark", dict(awake_mark=False), None, "the awake pass hasn't run in this boot"))
    if sio:
        cases += [("a build without sio_on", dict(build="floor-nosio"), None, "without sio_on"),
                  ("with pmp_sleep_release built for another kernel", dict(pmp_stale=True), None, "isn't built for " + RELEASE)]
    if disp:
        cases.append(("a build without disp_off", dict(build="floor-nodisp"), None, "without disp_off"))
    if macend:
        cases.append(("a build without one_commit and smc_idle", dict(build="floor-nomacend"), None, "without one_commit and smc_idle"))
    if siostop:
        cases.append(("a build without sio_stop", dict(build="floor-nosiostop"), None, "without sio_stop"))
    if pwords:
        cases.append(("a build without ps_words", dict(build="floor-nopwords"), None, "without ps_words"))
    if recfg:
        cases += [("a build without recfg_bite", dict(build="floor-norecfg"), None, "without recfg_bite"),
                  ("without recfg-bite", dict(tools=False), None, "stubs/recfg-bite"),
                  ("with a USB device attached, as the reading would after the write", dict(attached="usb"), None, "unplug everything first (attached: 1-1=05ac:1234)"),
                  ("with a USB-C partner attached", dict(attached="typec"), None, "unplug everything first (attached: port0-partner)")]
    if wd2:
        cases += [("a build without wd2_bite", dict(build="floor-nowd2"), None, "without wd2_bite"),
                  ("without wd2-bite", dict(tools=False), None, "stubs/wd2-bite"),
                  ("with a USB device attached, as the reading would after the write", dict(attached="usb"), None, "unplug everything first (attached: 1-1=05ac:1234)"),
                  ("with a USB-C partner attached", dict(attached="typec"), None, "unplug everything first (attached: port0-partner)")]
    if ack:
        cases.append(("a build without cluster_ack", dict(build="floor-noack"), None, "without cluster_ack"))
    if fab:
        cases.append(("a build without fabric_up", dict(build="floor-nofab"), None, "without fabric_up"))
    if mark:
        cases.append(("a build without wd1_mark", dict(build="floor-nomark"), None, "without wd1_mark"))
    if gates:
        cases.append(("a build without gates_mark", dict(build="floor-nogates"), None, "without gates_mark"))
    if plain:
        # The floor's build before quiet still runs the floor itself.
        usage = "usage: run-floor [--quick] [--quiet | --m1 | --m1-observe | --m1-late | --m1-alarm | --m1-hold | --m1-hold-nowatch | --m1-pmp | --m1-pmp-idle | --m1-position | --m1-sio-pmp | --m1-disp-off | --m1-macos-end | --m1-sio-stop | --m1-power-words | --m1-recfg-bite | --m1-wd2-bite | --m1-cluster-ack | --m1-fabric-up | --m1-mark | --m1-gates]"
        cases += [("an unknown argument", {}, ["--loud"], usage),
                  ("a second argument", {}, ["--quiet", "--quiet"], usage),
                  ("--quiet and --m1 together", {}, ["--quiet", "--m1"], usage),
                  ("--m1 and --m1-observe together", {}, ["--m1", "--m1-observe"], usage),
                  ("--m1-observe and --m1-late together", {}, ["--m1-observe", "--m1-late"], usage),
                  ("--m1-late and --m1-alarm together", {}, ["--m1-late", "--m1-alarm"], usage),
                  ("--m1-alarm and --m1-hold together", {}, ["--m1-alarm", "--m1-hold"], usage),
                  ("--m1-hold and --m1-hold-nowatch together", {}, ["--m1-hold", "--m1-hold-nowatch"], usage),
                  ("--m1-hold and --m1-pmp together", {}, ["--m1-hold", "--m1-pmp"], usage),
                  ("--m1-pmp and --m1-pmp-idle together", {}, ["--m1-pmp", "--m1-pmp-idle"], usage),
                  ("--m1-hold and --m1-position together", {}, ["--m1-hold", "--m1-position"], usage),
                  ("--position alone", {}, ["--position"], usage),
                  ("--pmp alone", {}, ["--pmp"], usage),
                  ("--hold alone", {}, ["--hold"], usage),
                  ("--alarm alone", {}, ["--alarm"], usage),
                  ("--observe alone", {}, ["--observe"], usage),
                  ("--late alone", {}, ["--late"], usage),
                  ("--m1-wd2-bite and --m1-cluster-ack together", {}, ["--m1-wd2-bite", "--m1-cluster-ack"], usage),
                  ("--cluster-ack alone", {}, ["--cluster-ack"], usage),
                  ("--m1-cluster-ack and --m1-fabric-up together", {}, ["--m1-cluster-ack", "--m1-fabric-up"], usage),
                  ("--fabric-up alone", {}, ["--fabric-up"], usage),
                  ("--m1-fabric-up and --m1-mark together", {}, ["--m1-fabric-up", "--m1-mark"], usage),
                  ("--mark alone", {}, ["--mark"], usage),
                  ("--m1-mark and --m1-gates together", {}, ["--m1-mark", "--m1-gates"], usage),
                  ("--gates alone", {}, ["--gates"], usage)]
    # A build older than one of its sources: another version's, perhaps, whose breadcrumbs this version's m1-crumbs would misread.
    cases += [(f"a module older than {src}", dict(srcs=src), None, f"e1_hook.ko is older than {src}: make -C") for src in ("e1_hook.c", "recfg_bite.h", "m1_position.h", "wd2_bite.h", "fabric_up.h", "wd1_mark.h", "gates_mark.h", "asc_probe.h", "look.h", "gate_test.h")]
    for what, kw, argv, msg in cases:
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher, argv=argv)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and recfg_calls(tmp) == [] and wd2_calls(tmp) == [] and sysctl(tmp) == ("0", "0"),
                  f"{name(launcher)}: refuses {what}, and loads nothing", r.stdout + r.stderr + "\n".join(calls(tmp)))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, srcs="fresh", reading="fail")
        r = run(tmp, d, launcher)
        check(r.returncode == 1 and "is older than" not in r.stderr and [x.split()[0] for x in calls(tmp)] == ["insmod", "reading", "rmmod"],
              f"{name(launcher)}: a module newer than its four sources loads as ever", r.stdout + r.stderr + "\n".join(calls(tmp)))
    # A build that loads but doesn't know the parameters, which the kernel
    # only warns about: unloaded at once, before any reading.
    ignored = [("replay and oneway", dict(takes=False))]
    if quiet or m1:
        ignored.append(("quiet", dict(takes_quiet=False)))
    if m1:
        ignored.append(("m1", dict(takes_m1=False)))
    if observe:
        ignored.append(("observe", dict(takes_observe=False)))
    if late:
        ignored.append(("pmgr_late", dict(takes_late=False)))
    if alarm:
        ignored.append(("alarm", dict(takes_alarm=False)))
    if hold:
        ignored.append(("hold", dict(takes_hold=False)))
    if nowatch:
        ignored.append(("hold_nowatch", dict(takes_nowatch=False)))
    if pmp:
        ignored.append(("pmp_out", dict(takes_pmp=False)))
    if nostop:
        ignored.append(("pmp_stop", dict(takes_nostop=False)))
    if position:
        ignored.append(("position", dict(takes_position=False)))
    if sio:
        ignored.append(("sio_on", dict(takes_sio=False)))
    if disp:
        ignored.append(("disp_off", dict(takes_disp=False)))
    if macend:
        ignored.append(("one_commit and smc_idle", dict(takes_macend=False)))
    if siostop:
        ignored.append(("sio_stop", dict(takes_siostop=False)))
    if pwords:
        ignored.append(("ps_words", dict(takes_pwords=False)))
    if recfg:
        ignored.append(("recfg_bite", dict(takes_recfg=False)))
    if wd2:
        ignored.append(("wd2_bite", dict(takes_wd2=False)))
    if ack:
        ignored.append(("cluster_ack", dict(takes_ack=False)))
    if fab:
        ignored.append(("fabric_up", dict(takes_fab=False)))
    if mark:
        ignored.append(("wd1_mark", dict(takes_mark=False)))
    if gates:
        ignored.append(("gates_mark", dict(takes_gates=False)))
    for what, kw in ignored:
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, **kw)
            r = run(tmp, d, launcher)
            c = calls(tmp)
            check(r.returncode == 1 and "didn't take" in r.stderr and c[-1:] == ["rmmod e1_hook"]
                  and not any(x.startswith("reading") for x in c),
                  f"{name(launcher)}: a build that ignored {what} is unloaded before any reading",
                  r.stdout + r.stderr + "\n".join(c))
    if plain:
        # The floor itself loads the build from before quiet. No suspend
        # follows here, so it ends in the watch's NO FLOOR.
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, reading="ok", build="floor-noquiet")
            r = run(tmp, d, launcher)
            c = calls(tmp)
            check(r.returncode == 2 and c[:2] == ["insmod " + os.path.join(d, "e1_hook.ko") + " replay=15 oneway=1",
                                                  "reading take --label floor --then suspend"]
                  and "no suspend began" in r.stdout,
                  f"{name(launcher)}: without --quiet, the build from before quiet loads as before",
                  r.stdout + r.stderr + "\n".join(c))


def test_sio_release(launcher):
    """--m1-sio-pmp: no awake pass needed; the PMP's report requests withdrawn before the reading; a failure there stops it."""
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, awake_mark=False)
        r = run(tmp, d, launcher)
        c = calls(tmp)
        pc = open(os.path.join(tmp, "pmp-calls")).read() if os.path.exists(os.path.join(tmp, "pmp-calls")) else ""
        check(any(x.startswith("reading") for x in c) and pc == "release 0xc 0xd\n",
              "run-floor --m1-sio-pmp: needs no awake pass, and withdraws the PMP's requests 0xc and 0xd before the reading",
              r.stdout + r.stderr + "\n".join(c) + "\n" + pc)
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, pmp_rc=1)
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 1 and "couldn't load the PMP report-release module" in r.stderr and c[-1:] == ["rmmod e1_hook"]
              and not any(x.startswith("reading") for x in c),
              "run-floor --m1-sio-pmp: a failed release unloads e1_hook before any reading", r.stdout + r.stderr + "\n".join(c))


def recfg_calls(tmp):
    try:
        with open(os.path.join(tmp, "recfg-calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def wd2_calls(tmp):
    try:
        with open(os.path.join(tmp, "wd2-calls")) as f:
            return f.read().split("\n")[:-1]
    except FileNotFoundError:
        return []


def test_insmod_fails(launcher):
    """A module that refuses to load: the kernel's reason shown and saved, and nothing read."""
    recfg = "--m1-recfg-bite" in launcher[4]
    wd2 = runs_wd2(launcher)
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, insmod_rc=1)
        r = run(tmp, d, launcher)
        c = calls(tmp)
        rc_ = recfg_calls(tmp)
        logs = os.listdir(os.path.join(tmp, "log"))
        logged = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
        check(r.returncode == 1 and "e1_hook didn't load" in r.stderr and "e1_hook: replay: a stub line" in r.stderr
              and "didn't take" not in r.stderr and len(c) == 3 and c[0].startswith("insmod ")
              and c[1] == "sleep-drain-safe save " + os.path.join(tmp, "log", logs[0] if logs else "") and c[2] == "rmmod e1_hook"
              and "e1_hook didn't load" in logged and "e1_hook: replay: a stub line" in logged and "wasn't saved" not in r.stderr
              and rc_ == ([BITE] if recfg else []) and (STILL in r.stderr) == recfg
              and wd2_calls(tmp) == ([WD2] if wd2 else []) and (r.stderr.count(WD2_STILL) == 1) == wd2 and (WD2_STILL in r.stderr) == wd2,
              f"{name(launcher)}: a module that refuses to load: the kernel's lines shown and saved, no reading"
              + (", and it says the bite word still reads 45 s" if recfg else "") + (", and it says WD2's bite still reads 120 s" if wd2 else ""),
              r.stdout + r.stderr + "\n".join(c + rc_ + wd2_calls(tmp)))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, insmod_rc=1, save_rc=1)
        r = run(tmp, d, launcher)
        check(r.returncode == 1 and "e1_hook: replay: a stub line" in r.stderr and "(that wasn't saved: the lines above are the record)" in r.stderr,
              f"{name(launcher)}: and it says so when those lines couldn't be saved", r.stdout + r.stderr)
    for dmesg in ("none", "fails"):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, insmod_rc=1, dmesg=dmesg)
            r = run(tmp, d, launcher)
            c = calls(tmp)
            check(r.returncode == 1 and "e1_hook didn't load" in r.stderr and len(c) == 3 and c[1].startswith("sleep-drain-safe save ") and c[2] == "rmmod e1_hook",
                  f"{name(launcher)}: a module that refuses to load with a kernel log that {'has no line of its own' if dmesg == 'none' else 'can not be read'}: still said, and saved",
                  r.stdout + r.stderr + "\n".join(c))


# recfg-bite as run-floor must run it: no arguments, kernel.panic set, nothing loaded yet.
BITE = "bite [] panic=10 insmod=0"
STILL = "A2's bite word was changed and still reads 45 s"
AGAIN = "run-floor --m1-recfg-bite can simply be run again"
LOADED = "e1_hook is still loaded, so restart before another run"


def test_recfg(launcher):
    """--m1-recfg-bite: the SRAM word changed before anything is loaded, with kernel.panic set, by recfg-bite and nothing else;
    nothing loaded if it can't be changed; and no store to undo it on any way out, only the word that it still reads 45 s."""
    script = name(launcher)

    # No suspend comes: NO FLOOR, the replay called off, and the block says the word is still changed.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        r = run(tmp, d, launcher)
        rc_ = recfg_calls(tmp)
        logs = os.listdir(os.path.join(tmp, "log"))
        logged = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
        check(r.returncode == 2 and rc_ == [BITE] and "NO FLOOR" in r.stdout
              and STILL + ": nothing here puts it back" in r.stdout and STILL in logged and r.stderr.count(STILL) == 0
              and r.stdout.index("run-floor: A2's bite word first") < r.stdout.index("the bite launcher ran") < r.stdout.index("e1_hook loaded with"),
              f"{script}: recfg-bite runs once, first, with kernel.panic set and nothing loaded; NO FLOOR says, on the screen and in its saved log,"
              " that the word still reads 45 s, and nothing stores again", r.stdout + r.stderr + "\n".join(rc_))

    # The word can't be changed: nothing is loaded, and nothing claims it was changed.
    UNCHANGED = "A2's bite word wasn't changed"
    for bite_rc, msg in ((3, UNCHANGED + ": the SRAM isn't as expected (recfg-bite exit 3, above)"), (4, "recfg-bite stopped before its write (exit 4, above"),
                         (5, "the write was made and the ten words don't read as meant (recfg-bite exit 5:"), (1, "recfg-bite failed (exit 1)"),
                         (143, "recfg-bite failed (exit 143)")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, bite_rc=bite_rc)
            r = run(tmp, d, launcher)
            rc_ = recfg_calls(tmp)
            # Only exit 3 says the word wasn't changed: after 5 a store was made, and the writer's own lines say what's there.
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and rc_ == [BITE] and STILL not in r.stderr
                  and (UNCHANGED in r.stderr) == (bite_rc == 3),
                  f"{script}: recfg-bite exit {bite_rc}: nothing loaded, nothing read, and recfg-bite's own account of the word stands",
                  r.stdout + r.stderr + "\n".join(calls(tmp) + rc_))

    # A failed reading, a signal during it, a build that ignored the parameter: the unload as ever, and the word left as it is, said so.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="fail")
        r = run(tmp, d, launcher)
        rc_ = recfg_calls(tmp)
        check(r.returncode == 1 and rc_ == [BITE] and calls(tmp)[-1:] == ["rmmod e1_hook"] and r.stderr.count(STILL) == 1 and r.stderr.count(AGAIN) == 1
              and LOADED not in r.stderr,
              f"{script}: a failed reading: the module unloaded, and it says once that the word still reads 45 s and that the run can be made again",
              r.stdout + r.stderr + "\n".join(rc_))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="wait")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        os.kill(launcher_pid, signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        rc_ = recfg_calls(tmp)
        check(p.returncode == 143 and rc_ == [BITE] and calls(tmp)[-1:] == ["rmmod e1_hook"] and err.count(STILL) == 1 and err.count(AGAIN) == 1,
              f"{script}: SIGTERM during the reading: the module unloaded, and it says the word still reads 45 s", out + err + "\n".join(rc_))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, takes_recfg=False)
        r = run(tmp, d, launcher)
        rc_ = recfg_calls(tmp)
        check(r.returncode == 1 and "didn't take" in r.stderr and rc_ == [BITE] and r.stderr.count(STILL) == 1,
              f"{script}: a build that ignored recfg_bite: unloaded, and it says the word still reads 45 s", r.stdout + r.stderr + "\n".join(rc_))

    # The floor itself (the launcher killed while it waits), and a signal while it watches: recfg-bite ran once and nothing else did.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        try:
            os.kill(launcher_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        check(recfg_calls(tmp) == [BITE], f"{script}: while it waits for the floor, the word stays changed", out + err + "\n".join(recfg_calls(tmp)))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        try:
            os.kill(launcher_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        rc_ = recfg_calls(tmp)
        check(p.returncode == 143 and rc_ == [BITE] and cancelled(tmp) == "1\n" and err.count(STILL) == 1 and err.count(LOADED) == 1 and AGAIN not in err,
              f"{script}: SIGTERM while it watches: the replay called off, and it says the word still reads 45 s and, e1_hook staying loaded, to restart first",
              out + err + "\n".join(rc_))
    # The launcher itself: recfg-bite is the only program of the kind it runs, and it never asks for a restore.
    text = open(os.path.join(E1_HOOK, "run-floor")).read()
    check(text.count('"$BITE"') == 1 and '"$BITE" || rc=$?' in text and "POKE" not in text and "--restore" in text and '"$BITE" --' not in text,
          "run-floor runs recfg-bite once, with no arguments, and has no writer and no restore of its own")


# wd2-bite as run-floor must run it: no arguments, kernel.panic set, nothing loaded yet.
WD2 = "wd2 [] panic=10 insmod=0"
WD2_STILL = "WD2's bite still reads 120 s from wd2-bite's write"
WD2_AGAIN = "run-floor {} can simply be run again"  # with the option that ran it


def test_wd2(launcher):
    """--m1-wd2-bite: WD2's bite written from user space before anything is loaded, with kernel.panic set, by wd2-bite and nothing else; the
    run goes on whether that store took (exit 0) or not (exit 5), and stops on anything else; no store to undo it on any way out."""
    script = name(launcher)
    insmod = "insmod {} " + launcher[2]
    WD2_AGAIN = globals()["WD2_AGAIN"].format(launcher[4][0])
    other = "--m1-cluster-ack" if launcher[4][0] != "--m1-cluster-ack" else "--m1-wd2-bite"

    # The store took, and no suspend comes: NO FLOOR, the replay called off, and the block says the bite still reads 120 s.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        r = run(tmp, d, launcher)
        wc = wd2_calls(tmp)
        logs = os.listdir(os.path.join(tmp, "log"))
        logged = open(os.path.join(tmp, "log", logs[0])).read() if len(logs) == 1 else ""
        check(r.returncode == 2 and wc == [WD2] and recfg_calls(tmp) == [] and "NO FLOOR" in r.stdout
              and WD2_STILL + ": nothing here puts the 90 s back" in r.stdout and WD2_STILL in logged and r.stderr.count(WD2_STILL) == 0
              and r.stdout.index("run-floor: WD2's bite from user space first") < r.stdout.index("the wd2 launcher ran")
              < r.stdout.index("run-floor: wd2-bite's store took: WD2's bite reads 120 s. Going on to the run") < r.stdout.index("e1_hook loaded with")
              and calls(tmp)[0] == insmod.format(os.path.join(d, "e1_hook.ko")),
              f"{script}: wd2-bite runs once, first, with kernel.panic set and nothing loaded, and never recfg-bite; the load follows with wd2_bite=1;"
              " NO FLOOR says, on the screen and in its saved log, that the bite still reads 120 s", r.stdout + r.stderr + "\n".join(wc))

    # The store didn't take: a result, and the run goes on with the same load. Nothing then claims the bite reads 120 s.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff", wd2_rc=5)
        r = run(tmp, d, launcher)
        wc = wd2_calls(tmp)
        c = calls(tmp)
        check(r.returncode == 2 and wc == [WD2] and c[:2] == [insmod.format(os.path.join(d, "e1_hook.ko")), f"reading take --label {launcher[3]} --then suspend"]
              and "run-floor: wd2-bite's store didn't take (exit 5, above): that's a result, and it's saved. Going on to the run: e1_hook's write is the kernel's" in r.stdout
              and "store took" not in r.stdout and WD2_STILL not in r.stdout + r.stderr and "NO FLOOR" in r.stdout,
              f"{script}: wd2-bite exit 5, the store didn't take: said as a result, the module loads and the reading runs all the same, and nothing"
              " says the bite reads 120 s", r.stdout + r.stderr + "\n".join(c + wc))

    # No store, because the bite read 120 s before any in this boot: no control, said so, and the run goes on. Nothing says a store took.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff", wd2_rc=7)
        r = run(tmp, d, launcher)
        c = calls(tmp)
        check(r.returncode == 2 and wd2_calls(tmp) == [WD2] and c[:2] == [insmod.format(os.path.join(d, "e1_hook.ko")), f"reading take --label {launcher[3]} --then suspend"]
              and "run-floor: wd2-bite made no store: WD2's bite read 120 s already, with none made in this boot (exit 7, above). The control wasn't made" in r.stdout
              and "store took" not in r.stdout and "didn't take" not in r.stdout and WD2_STILL not in r.stdout + r.stderr,
              f"{script}: wd2-bite exit 7, no store made: said as that, never as a store that took, and the run goes on", r.stdout + r.stderr + "\n".join(c))

    # Anything else from wd2-bite: nothing is loaded.
    for wd2_rc, msg in ((3, "WD2 isn't as it is awake (wd2-bite exit 3, above), so nothing was written."), (4, "wd2-bite stopped before its write (exit 4, above"),
                        (1, "wd2-bite failed (exit 1)"), (2, "wd2-bite failed (exit 2)"), (6, "wd2-bite failed (exit 6)"), (8, "wd2-bite failed (exit 8)"),
                        (126, "wd2-bite failed (exit 126)"), (127, "wd2-bite failed (exit 127)"), (143, "wd2-bite failed (exit 143)")):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, wd2_rc=wd2_rc)
            r = run(tmp, d, launcher)
            wc = wd2_calls(tmp)
            check(r.returncode == 1 and msg in r.stderr and calls(tmp) == [] and wc == [WD2] and WD2_STILL not in r.stderr and "Going on" not in r.stdout,
                  f"{script}: wd2-bite exit {wd2_rc}: nothing loaded, nothing read, and wd2-bite's own account of the register stands",
                  r.stdout + r.stderr + "\n".join(calls(tmp) + wc))

    # A failed reading, a signal during it, a build that ignored the parameter: the unload as ever, and the bite left as it is, said so.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="fail")
        r = run(tmp, d, launcher)
        wc = wd2_calls(tmp)
        check(r.returncode == 1 and wc == [WD2] and calls(tmp)[-1:] == ["rmmod e1_hook"] and r.stderr.count(WD2_STILL) == 1 and r.stderr.count(WD2_AGAIN) == 1
              and LOADED not in r.stderr and f"run-floor {other} can" not in r.stderr,
              f"{script}: a failed reading: the module unloaded, and it says once that the bite still reads 120 s and that the run can be made again",
              r.stdout + r.stderr + "\n".join(wc))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="fail", wd2_rc=5)
        r = run(tmp, d, launcher)
        check(r.returncode == 1 and wd2_calls(tmp) == [WD2] and calls(tmp)[-1:] == ["rmmod e1_hook"] and WD2_STILL not in r.stderr,
              f"{script}: a failed reading after a store that didn't take: the module unloaded, and nothing says the bite reads 120 s", r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="wait")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        os.kill(launcher_pid, signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        wc = wd2_calls(tmp)
        check(p.returncode == 143 and wc == [WD2] and calls(tmp)[-1:] == ["rmmod e1_hook"] and err.count(WD2_STILL) == 1 and err.count(WD2_AGAIN) == 1,
              f"{script}: SIGTERM during the reading: the module unloaded, and it says the bite still reads 120 s", out + err + "\n".join(wc))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, takes_wd2=False)
        r = run(tmp, d, launcher)
        wc = wd2_calls(tmp)
        check(r.returncode == 1 and "didn't take" in r.stderr and wc == [WD2] and r.stderr.count(WD2_STILL) == 1,
              f"{script}: a build that ignored wd2_bite: unloaded, and it says the bite still reads 120 s", r.stdout + r.stderr + "\n".join(wc))

    # The floor itself (the launcher killed while it waits), and a signal while it watches: wd2-bite ran once and nothing else did.
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        try:
            os.kill(launcher_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        check(wd2_calls(tmp) == [WD2] and cancelled(tmp) == "", f"{script}: while it waits for the floor, wd2-bite has run once and the replay stays armed",
              out + err + "\n".join(wd2_calls(tmp)))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher, reading="handoff")
        p = subprocess.Popen(cmd(d, launcher), env=env(tmp, launcher, watch_s=30), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for(os.path.join(tmp, "pids"))
        launcher_pid = int(open(os.path.join(tmp, "pids")).read().split()[0])
        time.sleep(1.5)
        try:
            os.kill(launcher_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        out, err = p.communicate(timeout=30)
        wc = wd2_calls(tmp)
        check(p.returncode == 143 and wc == [WD2] and cancelled(tmp) == "1\n" and err.count(WD2_STILL) == 1 and err.count(LOADED) == 1 and WD2_AGAIN not in err,
              f"{script}: SIGTERM while it watches: the replay called off, and it says the bite still reads 120 s and, e1_hook staying loaded, to restart first",
              out + err + "\n".join(wc))
    # The real wd2-bite under run-floor, with a stub writer whose store doesn't take. Saved: the run goes on. With its second save failing
    # (as when sleep-drain-safe can't unmount the boot partition and says to restart): that isn't exit 5, and nothing is loaded.
    for save_fails in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            d = setup(tmp, launcher, reading="handoff")
            real = os.path.join(tmp, "real")
            rcalls = os.path.join(real, "calls")
            os.makedirs(os.path.join(real, "log"))
            for n in ("wd2_poke.c", "wd2_bite.h"):
                write(os.path.join(real, n), "")
                os.utime(os.path.join(real, n), (1000, 1000))
            write(os.path.join(real, "wd2_poke"),
                  f'#!/bin/sh\necho "poke $1" >> {rcalls}\ncase $1 in check) exit 0 ;; patch) echo "THE WRITE DID NOT TAKE (the stub writer)"; exit 5 ;; esac\nexit 9\n', 0o755)
            os.utime(os.path.join(real, "wd2_poke"), (2000, 2000))
            unmount = '[ "$(grep -c "^save" ' + rcalls + ')" != 2 ] || { echo "sleep-drain-safe: saved, but /boot is still mounted at /run/x: restart now" >&2; exit 1; }\n'
            write(os.path.join(real, "save"), f'#!/bin/sh\necho "save $*" >> {rcalls}\n' + (unmount if save_fails else "") + "exit 0\n", 0o755)
            e = env(tmp, launcher)
            e.update(FLOOR_WD2_BITE=os.path.join(E1_HOOK, "wd2-bite"), WD2_POKE=os.path.join(real, "wd2_poke"), WD2_POKE_SRC=os.path.join(real, "wd2_poke.c"),
                     WD2_HEADER=os.path.join(real, "wd2_bite.h"), WD2_SAFE_FLAG=os.path.join(tmp, "run", "active"), WD2_SAVE=os.path.join(real, "save"),
                     WD2_LOG_ROOT=os.path.join(real, "log"), WD2_SYSCTL=os.path.join(tmp, "sysctl"), WD2_MARK=os.path.join(real, "mark"))
            r = subprocess.run(cmd(d, launcher), env=e, capture_output=True, text=True, timeout=60)
            c = calls(tmp)
            rc_ = open(rcalls).read().split("\n")[:-1] if os.path.exists(rcalls) else []
            four = ["poke check", "save save", "poke patch", "save save"]
            if save_fails:
                check(r.returncode == 1 and rc_ == four and c == [] and "Going on" not in r.stdout and "it's saved" not in r.stdout
                      and "/boot is still mounted at /run/x: restart now" in r.stderr and "that result's record couldn't be saved" in r.stderr
                      and "wd2-bite failed (exit 1), above. Nothing is loaded and nothing replays: restart the Mac" in r.stderr,
                      f"{script}: the real wd2-bite, a store that didn't take and a save that failed: no load and no reading, and the save's own"
                      " word to restart is on the screen", r.stdout + r.stderr + "\n".join(c + rc_))
            else:
                check(r.returncode == 2 and rc_ == four and c[:2] == [insmod.format(os.path.join(d, "e1_hook.ko")), f"reading take --label {launcher[3]} --then suspend"]
                      and "wd2-bite's store didn't take (exit 5, above): that's a result, and it's saved. Going on to the run" in r.stdout,
                      f"{script}: the real wd2-bite, a store that didn't take, saved: the run goes on", r.stdout + r.stderr + "\n".join(c + rc_))

    # The launcher itself: wd2-bite is the only program of the kind it runs for this recipe, once and with no arguments.
    text = open(os.path.join(E1_HOOK, "run-floor")).read()
    check(text.count('"$WD2BITE"') == 1 and '"$WD2BITE" || rc=$?' in text and '"$WD2BITE" --' not in text and "wd2_poke" not in text,
          "run-floor runs wd2-bite once, with no arguments, and has no writer of its own")


def test_quick(launcher):
    """--quick ahead of the recipe: a 1-minute reading, and nothing else changes."""
    _, _, args, label, own = launcher
    argv = ["--quick"] + list(own)
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher)
        r = run(tmp, d, launcher, argv=argv)
        c = calls(tmp)
        check(c[:2] == ["insmod " + os.path.join(d, "e1_hook.ko") + " " + args, f"reading take --minutes 1 --label {label} --then suspend"]
              and "in about 1 minutes" in r.stdout,
              f"run-floor {' '.join(argv)}: a 1-minute reading, the same parameters", r.stdout + r.stderr + "\n".join(c))
    with tempfile.TemporaryDirectory() as tmp:
        d = setup(tmp, launcher)
        r = run(tmp, d, launcher, argv=["--quick", "--quick"] + list(own))
        check(r.returncode == 1 and "usage:" in r.stderr and calls(tmp) == [], f"run-floor --quick twice before {' '.join(own) or 'nothing'} is refused",
              r.stdout + r.stderr)


def main():
    if not shutil.which("bwrap"):
        print("skip: needs bwrap")
        return 0
    for launcher in LAUNCHERS:
        if launcher[0] == "run-floor":
            test_floor_watch(launcher)
        else:
            test_handoff(launcher)
        test_failed_reading(launcher)
        test_signals(launcher)
        test_broken_stdout(launcher)
        test_refusals(launcher)
        if launcher[0] == "run-floor":
            test_floor_refusals(launcher)
        if "--m1-sio-pmp" in launcher[4] or "--m1-disp-off" in launcher[4] or "--m1-macos-end" in launcher[4] or "--m1-sio-stop" in launcher[4] or "--m1-power-words" in launcher[4] or "--m1-recfg-bite" in launcher[4] or runs_wd2(launcher):
            test_sio_release(launcher)
        if "--m1-recfg-bite" in launcher[4]:
            test_recfg(launcher)
        if runs_wd2(launcher):
            test_wd2(launcher)
        if launcher[0] == "run-floor":
            test_insmod_fails(launcher)
        if launcher[0] == "run-floor":
            test_quick(launcher)
    print(f"{failed} failed" if failed else "all ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
