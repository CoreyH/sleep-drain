# Plan

Last updated 2026-11-05. RESEARCH.md is the lab notebook: what we found. This
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
- Every change goes branch → PR → Pullfrog → merge commit. Corey's standing
  OK (2026-11-06): a PR can be merged once the review bots have finished on
  its head commit and nothing they raised is left open. Anything posted
  outside this repo (issues, comments, upstream PRs) still needs Corey's
  explicit OK each time.
- JJRPF/Aurora-DeepIdle: its patches are GPL-2.0 and its DTS changes
  `GPL-2.0+ OR MIT`, per their SPDX lines (JJRPF on DeepIdle#9,
  2026-11-05). Its code may be used under those terms, credited with a
  credit line or a `Co-developed-by:`/`Signed-off-by:` pair.
- Never boot a saved device tree on this Mac (RESEARCH.md, "Testing the
  driver without a new device tree").
- Root goes through pkexec, which needs a healthy Omarchy shell.

State that lives on the MacBook, not in git:
- linux-aurora 7.2.11.aurora14, on Omarchy 4.5.0 (updated 2026-11-05; the
  kernel didn't change). m1n1-aurora and uboot-asahi are held back in
  pacman.conf (IgnorePkg). GRUB isn't, but the installed 2:2.16-3 is newer than
  the repo's 2:2.16-1, so pacman leaves it alone.
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
  - Before it, two m1n1 tracers need fixing, and Claude won't do that
    (CLAUDE.md: m1n1 is off limits). What's wrong, found 2026-11-05:
    `trace_spmi` doesn't import on any Mac since m1n1 5d6df45b renamed
    `SPMIRegs`, and it looks up `/arm-io/nub-spmi-a0` first. This Mac has
    seven `aapl,spmi` gen-1 controllers: nub-spmi0 and 1, clvr-spmi0-2 and
    aop-spmi0 and 1. `trace_pmgr` has T8103's addresses, including eight
    hooked lanes at 0x200200000, and traces only reg[0] and reg[1] in full.
    T6000 needs reg[2] (power states), reg[7] (wake masks), reg[36] (S2R
    timer) and reg[46]. If A4 happens, Corey fixes them by hand.
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

  The throwaway root works (2026-11-05): `sleep-drain-safe` and
  `safe-boot/` (CLAUDE.md has how it works; `tests/safe_boot_hook_test.py`
  and `tests/safe_boot_save_test.py` pass).
  - **Device-tested** in two safe boots. The first (12:06) had no Wi-Fi:
    Asahi's late hook mounts the vendor firmware tmpfs at
    `/sysroot/usr/lib/firmware/vendor` before ours, and it moved under the
    overlay with root (fixed in #19). The second (12:30), recorded in
    `/var/log/sleep-drain/safe-saved-20261105T123137/`: dm-0 read-only
    (`ro` 1, `blockdev --getro` 1); /, /home and /var/log overlays on
    read-only btrfs mounts; /boot not mounted; the firmware back on top and
    Wi-Fi connected. In both, a file written to /home was gone after the
    restart, and `save`, then `collect`, brought it and the journal back.
  - A Claude session in a safe boot loses its own transcript at the
    restart, so anything it finds goes into a file it `save`s.
  - Still open: the recovery setup (someone at the power key, or
    macvdmtool).

  **The experiment ladder** (drafted 2026-11-05; E0 done). The route is
  maralcbr's published replay, not a trace of our own (Corey's call,
  2026-11-05; m1n1 code is off limits to Claude). Each rung runs in a safe
  boot, with Corey at the power key, and only with his go-ahead for that rung.

  What DeepIdle has already settled on T6020 (DeepIdle#9 reply and
  `docs/status.md`, 2026-11-05), so the ladder skips it:
  - `WKTP` and `slpw` did no harm. `slpa` alone doesn't power the SoC down,
    but the SMC takes the storage away: every NVMe read and write fails,
    `waka` doesn't bring it back, and only a full power-off does (their run
    of 2026-11-04, root read-only after btrfs errors). So `slpa` goes last,
    and a run that sends it ends in a hard reset.
  - Since then (their worklog, 2026-11-05): a second `slpa` run never
    resumed, and the Mac cold-booted about 100 s after entry. Either the SoC
    really went down, or the SMC resets the Mac when the CPUs don't go down
    after `slpa`. In the second case, E1, which keeps the CPUs up, would end
    the same way.
  - Parking every CPU in deep WFI (suspend through their own firmware's
    PSCI) draws 1.365 W against 1.39-1.41 W for s2idle. The gap to macOS
    (0.24 W there) is the SoC staying up, not the cores.
  - Their next step is what macOS does between `slpa` and the power-down.
    For J314s, maralcbr's tables have it: 74 writes after `slpa`, ending
    with the S2R timer (RESEARCH.md, "maralcbr's J314s trace").

  The rungs:
  - [x] **E0, no risk: the safe boot as a baseline.** Done 2026-11-05
    (RESEARCH.md, "E0"): 1978 mW by `avg_mw_cc` in a safe boot with nothing
    attached, against aurora14's 1974 mW; the alarm, the hook, `save` and
    `collect` all worked. A first run with a USB-C dock attached drew
    3233 mW: unplug everything before a run.
  - **E1: the SoC side of macOS's sequence, without the CPUs.** maralcbr's
    keys and PMP request words (their bits 0 and 2), then the 74 post-`slpa`
    writes (bit 3), with the CPUs left running: no CPU power-down. This is
    what's never been measured. Does the SoC power down, and what does it
    draw? It runs where maralcbr's did, at s2idle's `prepare_late`. With no
    resume, the drain comes from the gauge across the reset. Two harmless
    checks come first, each in a safe boot (RESEARCH.md, "Before E1"):
    - [x] **The hook point.** Done 2026-11-06 (RESEARCH.md, "E1's checks and
      E1a"): both runs passed. `prepare_late` comes after the whole noirq
      pass: every device's callbacks, and genpd switching domains off. A
      device callback can't stand in for it. A module's own device runs
      first in each pass, so from its `suspend_noirq`, `slpa` and the PMGR
      writes would come before the other devices' noirq callbacks. Nothing
      here registers s2idle ops, but `s2idle_set_ops` isn't exported, so the
      module finds it by address through a kprobe (lab only, never for
      upstream). Check it with the module built without the replay: at
      `prepare_late` it only logs and reads one SMC key. Run that first with
      `pm_test=platform`, which stops right after `prepare_late` and resumes
      without sleeping, then in an alarm sleep. ftrace's `suspend_resume`
      and `device_pm_callback_end` events show whether it ran after the last
      noirq callback.
    - [x] **The measurement across the reset.** Done 2026-11-06 with V0 and
      V2 (RESEARCH.md, "E1's checks and E1a"): `BQCC` is a charge counter in
      mAh that spans resets, so it replaces `charge_now`; V1 isn't needed.
      The plan as it stood: `charge_counter` restarts at
      every boot, so it can't span the reset. In the next boot it measures
      that boot's own drain, which gets subtracted. `charge_now`, the gauge's
      estimate, is what spans the reset. It re-estimates after one (±25 mAh,
      maralcbr), and here it reads 50-108 mAh off when one end is taken under
      load. So both ends are read the same way, at idle. Validate the method
      in safe boots, with Corey at the power key:
      - V0: three power-key resets and three reboots, from awake. This
        measures the reset's own step. `pkexec sleep-drain-reading v0`
        after each boot runs it, alternating regular and safe boots.
      - V1: two s2idle sleeps of 4 h, each ended by the alarm, then saved
        and reset at once. That boot's `charge_counter` gives the truth.
      - V2: two s2idle sleeps of 4 h, cut by the power key. This is E1's
        exact procedure, and nights work for it. V2 is also E1's control.

      Each run should land within 0.2 W of its truth (for V2, s2idle's
      157 mA, about 2 W). If they do, E1's coarse answer holds even at 2 h: a floor
      near 0.2 W, or s2idle's 2 W. A finer floor needs 8 h runs. If they
      don't, an input-side USB-C meter is the fallback, good for A/B only.

    E1 itself: right before the sleep, `save` the idle reading to the boot
    partition. After `slpa` the NVMe is gone, and the ESP is on it too.
    Then let it run for hours. If the Mac resets on its own about 100 s in,
    as in DeepIdle's second `slpa` run, that's the result; the next boot's
    power-on time shows it.
    - [x] **E1a: the keys and PMP words only** (bits 0 and 2, no post-`slpa`
      writes). Ran 2026-11-06, 46 min: about 162 mA, the same as plain
      s2idle, and no self-reset. Its log was lost with the reset, so whether
      the SMC accepted the keys isn't known yet.
    - [x] **E1a under `pm_test=platform`** (`e1-hook/e1a-platform`): done
      2026-11-07 (RESEARCH.md, "E1's checks and E1a"). The SMC accepted all
      four keys; it still answers after `slpa`; the NVMe is lost until a
      restart; no reset, no freeze. The alarm sleep couldn't run: its
      programs are on the lost disk.
    - [ ] **The floor: the whole one-way replay** (`pkexec
      e1-hook/run-floor`: `replay=15 oneway=1`, maralcbr's bits 0-3 in their
      default order: the 31 watchdog and error-handler writes, the keys and
      PMP words, the other 43 post-`slpa` writes, their 500 ms, then every
      CPU's power-down), in a safe boot, measured with `BQCC` across the
      power-key reset. Does Linux get near macOS's 0.1 W? If it does, the
      sequence is right and the wake path is what's left (the NVMe first).
      If it stays near 1.4-2 W, something before `slpa` is missing. E1b (the
      74 writes with the CPUs up) is skipped: E1a shows nothing powers down
      while they run. Reviewed 2026-11-07: the table is maralcbr's L237-310
      byte for byte, and nothing in it or the PMP words is on the
      never-write list (the module checks again at load). How to read the
      night:
      - Dark until the power key: the replay reached the CPUs' power-down.
        `BQCC` gives the floor. Nothing else can make the Mac sleep with
        `e1_hook` loaded: only the first suspend after its load can replay,
        and any suspend that doesn't replay fails.
      - Awake, with run-floor's "NO FLOOR" block on the screen: no floor.
        The suspend failed, the replay stopped short (it then fails its
        suspend, `-EIO`), or no suspend began within 120 s. The block says
        which, from `suspend_stats` and `e1_hook`'s parameters and log;
        photograph it, then restart.
      - Back in the regular boot instead of dark: a fault, restarted by
        run-floor's `kernel.panic=10`. `BACC` dates it.
  - **E2 onwards, from the floor's result.** If it stays high, one at a time
    on top of it: the domains macOS has off (bits 8-10), the SEP sleep
    message (bit 11), the AOP's notices (bit 6), the internal PCIe ports
    (bit 13).
  - **The code.** An out-of-tree module: our own, written from these
    findings and maralcbr's tables (maralcbr/linux is GPL-2.0, as part of
    the kernel). maralcbr ran the replay from macsmc-reboot at s2idle's
    `prepare_late`, a call that only links with the driver built into the
    kernel. The module registers its own s2idle ops for the same point (E1's
    first check). It sends the keys with
    the ordinary SMC write. By the code that still works there: both mailbox
    interrupts are `IRQF_NO_SUSPEND`, and neither the SMC nor its mailbox
    has a power domain. The SMC's atomic mode is one-way, so it's out. The
    alternative to the kprobe lookup is a kernel that exports
    `s2idle_set_ops`, but the safe boot only boots the regular kernel today.
  - **What this can't answer: waking.** A floor near 0.1 W would show that
    the sequence is right. The fix itself also needs the CPUs restarted and
    the devices resumed after the SoC loses power. DeepIdle does that through
    its own firmware (PSCI in m1n1), which is off limits to Claude here.
- [ ] **A6. Talk to the others before duplicating work.** Every post needs
  Corey's OK.
  - **maralcbr first:** asked on 2026-11-04
    ([#281](https://github.com/maralcbr/omarchy-mx-mac/issues/281#issuecomment-6630474430)).
    The request is for the 15.6.1 trace and the replay's results. It offers
    the PMU alarm fix (SPMI 192 and the event clears), and shares this Mac's
    reads. Waiting for a reply (none as of 2026-11-05); check it before
    starting A3 or A4.
  - DeepIdle: JJRPF answered #9 on 2026-11-05: the license (above), a go
    for upstreaming the alarm, and their `slpa` and CPU-park results (A5's
    ladder). A reply pointing them at maralcbr's post-`slpa` table waits for
    Corey's OK.
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

- [x] **B1.** Why an awake alarm raised its IRQ 1 to 7 times while a sleep
  raised it once (#15). The cause is an RTC-core re-arm loop: `CLKM` lags the
  PMU's counter by about 6 ms, and the loop shows when the tick offset's
  fraction is under that. RESEARCH.md has the details. The fix in
  `rtc-alarm-test/rtc-macsmc-alarm.c` (arm a second later below 1/32 s of
  fraction) is verified on device: one IRQ per alarm. It goes into B2's
  rtc-macsmc patch.
- [ ] **B2.** Turn `rtc-alarm-test/` into patches against linux-aurora.
  - **Drafted (2026-11-04):** `patches/pmu-alarm-t600x/`, five patches on
    `integration/aurora` 396f2d63. Its README lists them and what was checked:
    applies, builds, DTBs, dt-schema, checkpatch. The working tree is
    `~/src/linux-aurora`, branch `pmu-alarm-t600x` (a shallow, sparse clone).
  - **Open:** boot a kernel built from the series. The kernel comes from
    #351's CI once a maintainer adds `ci:build` (Corey isn't on
    linux-aurora's vouch list). `test-kernel` installs it, and the test
    needs one reboot.
  - **Patch 5's tree can't boot with it yet.** `test-kernel` leaves out the
    package's DTBs, since m1n1's boot.bin takes the pacman-owned
    linux-aurora's trees for every boot. Putting patch 5's tree into boot.bin
    would also boot the regular aurora14 with it, and aurora14's SPMI
    controller, which lacks patch 1, then loses the PMU (RESEARCH.md, "First
    swap"). GRUB's `devicetree` can't stand in either: it swaps m1n1's tree,
    with the addresses m1n1 writes each boot, for a static one (the same
    problem as a saved tree).
  - So the boot test runs the CI kernel's own drivers (patches 1, 3 and 4),
    with patch 5 applied live by `pmu-alarm-dt.ko` alone, built for that
    kernel (CI keeps every `linux-aurora-*` package, so its headers should be
    there). `rtc-alarm-test/load` needs a mode for this: apply the tree, then
    reload the kernel's own `spmi_apple_controller`, `macsmc_reboot` and
    `rtc_macsmc`. Patch 5 stays checked by comparison: the J314s DTB matches
    the changeset property for property.
  - A boot with patch 5 in boot.bin and no test module waits for a
    linux-aurora release that carries patch 1.
- [x] **B3.** Posted with Corey's OK and his `Signed-off-by`:
  [omacom/linux-aurora#351](https://github.com/omacom/linux-aurora/pull/351)
  (2026-11-04). The branch is `pmu-alarm-t600x` on CoreyH/aurora-linux, the
  same fork network. It asks for `ci:build`, credits DeepIdle, and says the
  boot test will follow. Next: answer reviews, and do B2's boot test when the
  package exists.

## Track C: keyboard wake (once A is under way)

The internal keyboard can't wake the Mac on either kernel. spi-hid-apple's
wake code is compiled out (`IRQ_WAKE_SUPPORT` is never defined), and the
apple-gpio irqchip has no `irq_set_wake`. The results are on
omacom/linux-aurora#274 (issuecomment-6520008651).

## Watch

- The Touch ID/power key reported presses that woke the Mac with nobody
  touching it (two on 2026-11-02).

## Loose ends (Corey's call)

- The Omarchy 4.5.0 update left `/etc/mkinitcpio.conf.d/omarchy_hooks.conf.pacnew`:
  the local `omarchy_hooks.conf` was changed, so pacman didn't replace it.
  The pacnew has no HOOKS line, and the new `00-omarchy-hooks.conf` leaves
  this Mac's hooks alone (they have `asahi` and `encrypt`). So taking the
  pacnew as it is would fall back to `/etc/mkinitcpio.conf`'s HOOKS, which
  lack `plymouth`. The initramfs builds and boots as it is; leave it until
  someone checks what Omarchy intends.

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
