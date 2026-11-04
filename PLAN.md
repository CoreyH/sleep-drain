# Plan

Last updated 2026-11-04. RESEARCH.md is the lab notebook: what we found. This
file is what we do next, in order. Update it at the end of every session, in
the PR with that session's work.

## Where things stand

- **The drain is not fixed.** s2idle draws about 1.97 W (the aurora14
  baseline, 2026-11-02), and macOS sleeps at about 0.1 W on this machine.
  Device-level changes (radios, USB and PCIe wake, the Wi-Fi link, the display
  PMP requests, the AOP) moved it by little or nothing. The gap is the SoC
  never entering the sleep state macOS uses.
- **Done: timed wakes.** The PMU's RTC alarm wakes J314s from s2idle
  (`rtc-alarm-test/`), so a drain test takes 15 minutes instead of a night.
- **Done: A2.** RESEARCH.md's "Deep sleep: T6000's keys and registers" has the
  details. In short:
  - maralcbr has already traced macOS 15.6.1 going to sleep on a J314s, and
    published the sequence as replay tables in maralcbr/linux. The trace
    itself and the replay's results aren't public.
  - On this Mac, T6000's SMC has the sleep keys (`MBSE`, `MBSW`, `WKTP`,
    `pmVC`). Linux's s2idle moves none of the three it can read; `pmVC`,
    the key macOS writes, wasn't read. `CLSP` reads 0 and `CLWK` doesn't
    move, so the SMC apparently never registers a sleep (inferred).
  - The likely S2R timer is 0x2925c4000, which already reads 1 under Linux.

## Picking this up

- Start a fresh session for each step below, and tell it: "read PLAN.md and
  do the next open step". On the other computer, clone CoreyH/sleep-drain:
  CLAUDE.md, this file and RESEARCH.md hold everything. Claude's own notes
  on the MacBook don't travel.
- Every change goes branch → PR → Pullfrog → merge commit. Merging, and
  anything posted outside this repo (issues, comments, upstream PRs), needs
  Corey's explicit OK each time.
- JJRPF/Aurora-DeepIdle: use its findings, not its code. It has no LICENSE
  file (the README says GPL-2.0); DeepIdle#9 asks.
- Never boot a saved device tree on this Mac (RESEARCH.md, "Testing the
  driver without a new device tree").
- Root goes through pkexec, which needs a healthy Omarchy shell.

State that lives on the MacBook, not in git:
- linux-aurora 7.2.11.aurora14 (Omarchy 4.5.0rc3). m1n1-aurora, uboot-asahi
  and GRUB are held back in pacman.conf (IgnorePkg).
- `/etc/sleep-drain/radios-off` is set. `install.sh` last ran at 3b79df2.
- The alarm needs the test drivers after every boot:
  `make -C rtc-alarm-test && pkexec rtc-alarm-test/load`.
- The RTC was set to the real time on 2026-11-02. It had counted from 1970
  since the battery ran flat.
- `/boot/macos-session/`: macOS 26.6.2's IODeviceTree, `pmset` log and
  battery readings from 2026-10-30.

## Track A: deep sleep (the main track)

What's known is in RESEARCH.md, "Deep sleep: what's known". In short: macOS
powers the CPU cores off and restarts them at a locked reset vector. On the
M2 Pro, its way into sleep is the SMC key `MBSE` = `slpw`, the display
domains off through the PMP, `MBSE` = `slpa`, then AP power 0x201 to the SMC
and the PMP. Nobody has a SoC sleep working under Linux on any Apple Silicon
yet. The blockers are powering cores off and restarting them, the last PMGR
steps, and restarting the DCP and the PMP afterwards.

- [x] **A1. Desk research** into RESEARCH.md (this PR).
- [x] **A2. Read-only groundwork on this Mac** (#14).
  - `smc-peek`, `pmgr-peek`/`pmgr-map` and `sleep-peek` walked the SMC's
    2052 keys and read all 206 PMGR power states, awake and around an alarm
    sleep. That's 1634 key values, plus `MBSE`, `MBSW` and `WKTP`.
  - #281, DeepIdle, Asahi/m1n1 and the Linux/XNU sources were read and
    fact-checked: their sleep-related parts in full, but not DeepIdle's
    awake-idle series or the PSCI RFC's list thread.
  - RESEARCH.md has the keys, the registers and the questions for the
    trace.
- [ ] **A3. The second computer and a test volume.** Corey does this, with
  the session. See "The second computer" below.
- [ ] **A4. Trace macOS going to sleep under the m1n1 hypervisor.** maralcbr
  has traced this already (15.6.1, J314s), so ask for that trace first
  (A6). Then trace only what's missing: RESEARCH.md's nine questions, with
  `trace_smc`, `trace_pmp`, `trace_pmgr` and `trace_spmi`. Expect the Mac to
  hang once the cores power off, as on the M2 Pro. The trace covers the way
  into sleep, not the wake.
  - Before it, two tracers need fixing: `trace_spmi` stops on T6000 (it
    looks up `/arm-io/nub-spmi-a0` first), and `trace_pmgr` has T8103's
    addresses. Point `trace_pmgr` at 0x2925c0000, 0x28e224000 and
    0x292004000 (the AP wake masks) too.
  - The guest: maralcbr ran 15.6.1 on M1, which Asahi's guide doesn't list.
    If that holds, it saves hunting for a 14.8.x installer.
  - Exit: T6000's sequence, with the answers to RESEARCH.md's questions,
    compared with maralcbr's tables and DeepIdle's T6020 trace.
- [ ] **A5. Plan the first Linux-side experiments** from A2 and A4, such as
  the SMC handshake from s2idle, and CPU power-down and restart. These can
  hang or crash the Mac, and on the M2 Pro they once corrupted a filesystem.
  Before any of them:
  - a recovery setup: someone at the power key, or a remote hard reset;
  - a read-only or throwaway root;
  - Corey's go-ahead for each experiment.
- [ ] **A6. Talk to the others before duplicating work.** Every post needs
  Corey's OK.
  - **maralcbr first** (#281, or maralcbr/linux): ask for the 15.6.1 trace
    logs and what the replay did (it stopped on 2026-10-14 with no result
    posted). Offer the PMU alarm wake on J314s (`rtc-alarm-test`, with the
    SPMI 192 fix), which #281 parked.
  - DeepIdle: #9 has no reply yet.
  - Asahi: Sven Peter's PSCI-via-EFI RFC covers T8103 only.

## The second computer

Decided on 2026-11-02: **Corey's Linux desktop** is the host for everything
up to the trace (A3 and A4). An Apple Silicon Mac is available, and comes in
only when a job needs one: the remote recovery for A5.

**On the Linux desktop, for the hypervisor trace (A4):**
- Python 3.9 or newer, with `pyserial` and `construct`.
- m1n1 built from source, main from 2026-10-16 or later. The host's
  proxyclient and the m1n1 binary must come from the same commit: the proxy
  protocol isn't stable. On an x86-64 desktop, build the binary either on the
  MacBook (native aarch64; clang/lld plus Rust's
  `aarch64-unknown-none-softfloat` target), or cross-compile it on the
  desktop.
- m1n1's udev rule (USB ID 1209:316d gives `/dev/m1n1` and `/dev/m1n1-sec`),
  with the user in the serial device's owning group (e.g. `uucp` or
  `dialout`): the rule only adds the links, no group.
- A USB-C data cable from the desktop to the MacBook's **DFU port**: the
  rear-left port, next to MagSafe. A-to-C works too. Connect directly, not
  through a hub. Charge the MacBook through MagSafe while tracing.
- Claude Code on the desktop, with this repo cloned.

**On the Mac, for remote recovery in A5:** macvdmtool, which only runs on an
Apple Silicon Mac. It gives a hard reset and a serial console without anyone
at the power key. It needs a USB-C cable to the same DFU port; the serial
console needs a SuperSpeed C-to-C cable.

**On the MacBook (A3)**, step by step with the session:
1. A second APFS volume with macOS 14.8.3 or 14.8.9. macOS 26.6.2 can't be
   the guest: it uses SPTM, and m1n1's hypervisor doesn't support that on M1.
   Check the free space in macOS first.
2. In that volume's recovery: `bputil -nkcas`, `csrutil disable`, and
   `kmutil configure-boot` with a bare m1n1, which always starts the proxy.

Boot policy is per volume, so Linux and the main macOS stay as they are. The
risk is picking the wrong volume in those commands. Apple's catalog no longer
lists 14.8.3, and an older installer may refuse to run on macOS 26; the Asahi
hypervisor guide links an archived installer.

## Track B: upstream the RTC alarm (in parallel, small)

- [ ] **B1.** Find out why an awake alarm raises its IRQ 1 to 7 times while a
  sleep raises it once. This needs awake alarms only.
- [ ] **B2.** Turn `rtc-alarm-test/` into patches against linux-aurora, in
  this order:
  1. the SPMI T6000 notify fix (192);
  2. the rtc-macsmc `rtc_alarm_event` cell;
  3. the `apple,smc-rtc` binding;
  4. the t600x DT: nub_spmi0's interrupt and interrupt-controller, the nvmem
     cells, and the rtc node.
- [ ] **B3.** Draft the PR for Corey, and post it only with Corey's OK. Credit
  JJRPF's Maverick map.

## Track C: keyboard wake (once A is under way)

The internal keyboard can't wake the Mac on either kernel. spi-hid-apple's
wake code is compiled out (`IRQ_WAKE_SUPPORT` is never defined), and the
apple-gpio irqchip has no `irq_set_wake`. The results are on
omacom/linux-aurora#274 (issuecomment-6520008651).

## Watch

- The Touch ID/power key reported presses that woke the Mac with nobody
  touching it (two on 2026-11-02).

## Loose ends (Corey's call)

- `/etc/systemd/system/omarchy-wifi-resume-fix.service` fails on every
  resume, because its script is gone. Remove it?
- dotfiles:
  - `master` is 2 commits behind;
  - `chromium-flags.conf` and `shell.json` have uncommitted changes;
  - PRs #83 and #84 are open.

## Session hygiene

- One step per session. End it by updating this file's checkboxes and
  "Where things stand".
- Hand web research and long reads to subagents. Keep their conclusions, with
  links, in RESEARCH.md.
