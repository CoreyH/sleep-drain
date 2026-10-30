#!/usr/bin/python3 -I
"""Hardware-free checks for pmu-alarm: its real test() against a fake PMU.

  python3 -I tests/pmu_alarm_test.py      (from the repo root)

The fake models both acknowledgments seen so far: Maverick (M2 Pro, per
JJRPF/Aurora-DeepIdle: control bit 1 clears only when written as 0; 0x14c0 is
write-1-to-clear) and T6030 (aurora #274: bit 1 is write-1-to-clear). Its
seconds run at 4x, so a 5 s alarm fires in about a second.
"""
import importlib.machinery
import importlib.util
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "pmu-alarm")


def load():
    loader = importlib.machinery.SourceFileLoader("pmu_alarm", TOOL)
    spec = importlib.util.spec_from_loader("pmu_alarm", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakePmu:
    def __init__(self, m, style, stale):
        self.m, self.style = m, style
        self.t0, self.base = time.monotonic(), 1_000_000
        self.ctrl = 0x03 if stale else 0x01
        self.event = 1 if stale else 0
        self.mask, self.alarm = 0, self.base - 100

    def secs(self):
        return self.base + int((time.monotonic() - self.t0) * 4)

    def _tick(self):
        if self.ctrl & self.m.CTRL_EN and self.secs() >= self.alarm:
            self.ctrl = (self.ctrl & ~self.m.CTRL_EN) | self.m.CTRL_FIRED
            self.event |= 1

    def u8(self, off):
        self._tick()
        return {self.m.CTRL: self.ctrl, self.m.EVENT: self.event, self.m.IRQ_MASK: self.mask}[off]

    def u32(self, off):
        assert off == self.m.ALARM
        return self.alarm

    def write(self, off, data):
        m = self.m
        if off == m.CTRL:
            v, was = data[0], self.ctrl & m.CTRL_FIRED
            if self.style == "maverick":
                fired = was if v & m.CTRL_FIRED else 0
            else:
                fired = 0 if v & m.CTRL_FIRED else was
            self.ctrl = (v & ~m.CTRL_FIRED) | fired
        elif off == m.EVENT:
            if data[0] & 1:
                self.event &= ~1
        elif off == m.ALARM:
            self.alarm = int.from_bytes(data, "little")
        else:
            raise AssertionError(f"unexpected write at {off:#x}")

    def state(self):
        self._tick()
        return {"secs": self.secs(), "alarm": self.alarm, "ctrl": self.ctrl,
                "event": self.event, "mask": self.mask}


def child(style, stale, seconds):
    """Run test() in this process against a fake PMU (used by the parent)."""
    m = load()
    fake = FakePmu(m, style, stale)
    m.Pmu = lambda write: fake
    m.os.geteuid = lambda: 0
    sys.stdout.reconfigure(line_buffering=True)  # as main() does
    sys.exit(m.test(seconds))


def run(style, stale, seconds=5, send=None):
    cmd = [sys.executable, "-I", __file__, "--child", style, str(int(stale)), str(seconds)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = []
    for line in p.stdout:
        out.append(line)
        if send and line.startswith("armed:"):
            p.send_signal(send)
    return p.wait(), "".join(out)


failed = 0


def check(name, ok, out):
    global failed
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failed += 1
        print("".join("     " + l for l in out.splitlines(True)))


def final_disarmed(out):
    finals = [l for l in out.splitlines() if l.startswith("final:")]
    return bool(finals) and "en=0" in finals[-1]


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3] == "1", int(sys.argv[4]))

    rc, out = run("maverick", stale=True)
    check("maverick: stale latch cleared by writing bit 1 as 0, alarm fires, cleaned up",
          rc == 0 and "bit 1 as 0" in out and "result: FIRED" in out and final_disarmed(out), out)

    rc, out = run("t6030", stale=True)
    check("t6030: stale latch cleared by write-back, alarm fires, cleaned up",
          rc == 0 and "back as read" in out and "result: FIRED" in out and final_disarmed(out), out)

    rc, out = run("maverick", stale=False)
    check("maverick: nothing latched at start, alarm fires, cleaned up",
          rc == 0 and "before:" in out and "result: FIRED" in out and final_disarmed(out), out)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        rc, out = run("maverick", stale=False, seconds=60, send=sig)
        check(f"{sig.name} after arming: exits {128 + sig}, alarm disarmed",
              rc == 128 + sig and final_disarmed(out) and "result:" not in out, out)

    rc, out = run("maverick", stale=False, seconds=60, send=signal.SIGINT)
    check("SIGINT after arming: alarm disarmed", rc != 0 and final_disarmed(out), out)

    sys.exit(1 if failed else 0)
