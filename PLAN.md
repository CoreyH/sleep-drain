# Plan

Last updated 2026-11-08. RESEARCH.md is the lab notebook: what we found. This
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
- **In progress: E2, making the S2R entry complete** (A5). maralcbr's
  replay with Naeem's M1 recipe takes every core down and commits the entry,
  but the entry never completes: the engine's own watchdog, WD2, resets the
  SoC about 30 s after the commit (RESEARCH.md, "M1 run 2", "M1 run 3" and
  "M1 run 4"). In run 3, with macOS's PMGR quiesce in macOS's place, only
  DISPLAY's and SIO_CPU's waits timed out. Run 4 (2026-11-08: that quiesce,
  the PMU's RTC alarm between the commit's first write and the other two, no
  observer) ended the same way, the reset 29.6-31.3 s after the commit, with
  CPU1-7 and both P complexes off before it; the armed alarm survived the
  reset. Next is Corey's pick from the rungs below (their order is at the
  top of that list).

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
    - [x] **The floor: maralcbr's one-way replay, bits 0-3** (ran
      2026-11-07: the Mac reset itself about 30 s in, very likely the S2R
      entry's watchdog; RESEARCH.md, "The floor"). (`pkexec
      e1-hook/run-floor`: `replay=15 oneway=1`, in maralcbr's
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
      - The keyboard lit, and run-floor's "NO FLOOR" block on the screen:
        no floor.
        The suspend failed, the replay stopped short (it then fails its
        suspend, `-EIO`), or no suspend began within 120 s. The block says
        which, from `suspend_stats` and `e1_hook`'s parameters and log;
        photograph it, then restart.
      - Back in the regular boot instead of dark: a fault, restarted by
        run-floor's `kernel.panic=10`. `BACC` dates it.
  - **E2 onwards: make the S2R entry complete.** The floor's reset about
    30 s in is very likely the entry watchdog's (DeepIdle: the 0x2925c4000
    write is the commit and arms it). Each attempt is quick: a short
    safe-boot run with no reading needed, since a reset about 30 s in means
    "not yet". A Mac that stays dark is a candidate, not a result
    (RESEARCH.md, "The floor"). A breadcrumb just before the commit (a PMU
    scratch write, so with Corey's OK) rules out a hang before it; only a
    low BQCC reading shows the entry completed. In order:
    - [ ] **The quiet commit** (`pkexec e1-hook/run-floor --quiet`,
      `quiet=1`): the floor without maralcbr's 500 ms and its two AFNC
      reads, so the commit is the replay's last MMIO access and every CPU
      powers down right after it (an interrupt, such as a coprocessor's
      message, can still reach any CPU before it masks them). On T8103
      macOS adds only the RTC alarm and the same
      write twice more before its cores go down, and Naeem's tries 26-31
      reset because a last core touched a device after the commit.
    - [x] **The M1 recipe** (run 1, 2026-11-07; RESEARCH.md, "M1 run 1"):
      CPU1-7 parked, released and read off, both P complexes off, nothing
      pending, and still the reset about 31 s after the commit. CPU0 and the E
      complex aren't covered. It was `pkexec e1-hook/run-floor --m1`,
      e1_hook `m1=1`: Naeem's T8103 entry, the
      first to complete from Linux (RESEARCH.md, "The team"), mapped to
      T6000 by function on top of maralcbr's keys and table and the quiet
      commit. e1_hook.c says what each step does and why; in short:
      - After `slpa`, CPU0 masks its interrupts and AIC2's, and parks the
        other CPUs, awake, each with its own interrupt sources off. They
        stay up through the table's 42 writes, as XNU's H13 code (T6000's
        family) keeps them, since 15 of those writes go to the P clusters'
        own blocks.
      - Then each in turn writes its own stop bit in the PMGR's CPU-start
        block and goes down; only a P cluster's last core arms its deep
        sleep (Naeem's fix for a hang, malik-na/linux-aurora 3bbb16ebac56),
        and CPU0 arms its own last. CPU0 checks each core's PMGR power state.
      - Then, as on T8103: the AP wake mask (only the PMU), nub-spmi0's
        interrupts off and acked, the commit three times.
      - Boot stage 0x50 and breadcrumbs in spare PMU scratch bytes (Corey's
        OK, 2026-11-07); `e1-hook/m1-crumbs` reads them at the next boot,
        and `--clear` zeroes them.
      - Left out: the RTC alarm (a PMU write not OK'd yet), WD1's dead-man,
        and three of Naeem's steps with no known T6000 form: the report of
        idle clusters to the PMP, the CPM power gating at pmgr + 0x1c080,
        and the watchdog block's + 0x8008/+ 0x800c.
      - Its risk: DeepIdle's R20 on an M2 Pro wrote the same cluster APSC
        disable as the table, with its other cores parked in WFI; 7 of the 9
        never went down and the Mac hung dark for 28 minutes, with no
        watchdog reset. Here the cores spin through those writes, but a
        run may still hang instead of resetting: Corey at the power key.
      - If it fails, the breadcrumbs show how far it got, at least. Next
        would be the APSC writes moved after the cores' stop (macOS's T6020
        order).
    - [x] **M1 run 2, the observer** (ran 2026-11-07; RESEARCH.md, "M1 run
      2" and "After M1 run 2"): stage 0x6f, every breadcrumb in. CPU0 and
      the E complex went down after the commit too, and WD2, armed by the
      commit with about 30 s, reset the SoC. At CPU7's read after its
      polls (at most about a second after the commit), four soc clusters
      had a power-down pending (EACC, PACC0, PACC1, DISPLAY) and SIO_CPU's
      actual state wasn't off; no wake-status bit was new. It
      was `pkexec e1-hook/run-floor --m1-observe`, e1_hook `observe=1`: run
      1, but CPU7 stays up through the commit, with its stop bit written
      before it (macOS's order), and records what it sees in the OK'd
      breadcrumbs.
      - Its reading: DISPLAY can't finish powering down with Linux's
        display chain on, and macOS waits for it. EACC and PACC0 may only
        have been read too soon. The replay sends 26 of macOS's PMGR
        quiesce writes before `slpw` (maralcbr's lab order, not macOS's),
        makes none of its waits, asks SIO_CPU back on under an off SIO,
        and lowers DCS with the PMP running.
      - Dropped with it: the wdt reg[3] rung (it reads 0, so there's
        nothing to clear), and the prepared answer to "SIO_CPU not off"
        (macOS's read-modify-write would leave it off, which isn't macOS's
        state either).
    - [x] **M1 run 3: the PMGR quiesce in macOS's place** (ran 2026-11-08,
      RESEARCH.md "M1 run 3": stage 0x7f, the reset 30.0-31.6 s after the
      commit, WD2's by every sign; only DISPLAY's and SIO_CPU's waits timed
      out, DCS settled at 5, no hang; EACC and PACC0 first read clear under 5
      ms of the hand-over. e1_hook `pmgr_late=1`,
      `pkexec e1-hook/run-floor --m1-late`; e1_hook.c's
      m1 comment has its breadcrumbs, and `m1-crumbs` decodes them). Run 2's
      recipe, the observer included, with one change: where the table's
      PMGR quiesce goes, and macOS's waits after it. No new write and no
      new PMU byte. Its value is mostly the readout: T6020 ran the same
      steps in macOS's order with waits and still reset.
      - K2 keeps only the five watchdog and error-handler words (entries
        2-6). That is still maralcbr's order, not macOS's; a later rung can
        move them. The other 26 go out in step 5 at their places in the
        table, after `slpa`, with the other cores parked awake.
      - CPU0 then waits as macOS does, up to 10 ms each, and records which
        waits time out: DCS (entries 49-50: its busy bit clear, macOS's
        poll, and, stricter, actual = target), AFR's power state (51),
        ANE0, AFR, DISPLAY and AVEMSR (55, 57, 58, 60), SIO_CPU (61), the
        ClkGen muxes (62-66) and PLL-off (69 and 72: entries 67-69 are one
        setPLLOffMode, and 70-72 look like a second). It also reads PACC0's
        bit 4 just before the commit.
      - CPU7 times EACC's and PACC0's power-downs from the hand-over,
        stopping about 500 ms after ECPM reads off. It records DCS's
        p-state as CPU0 last read it, at the end of its wait after entry 50
        (before the ClkGen park, PLL-off and the commit); nothing reads DCS
        after the commit. It gives up run 2's wdt-page and wake-status
        bytes, which read nothing new.
      - New reads, all of registers macOS polls, or for the load log's
        baseline: the ClkGen muxes, PLL-off, DCS, the fabric's p-state and
        ps_sio.
      - One new risk: DCS now changes before the ClkGen park, in macOS's
        order (runs 1-2 parked the clocks first), so the DRAM p-state may
        really change under the running PMP. A hang there is dark at stage
        0x10, with no watchdog armed: the power key ends it.
      - How to read it:
        - Result (2026-11-08): the expected case, except that SIO_CPU's
          wait timed out too (a case pre-registered below). DCS settled, so
          the trigger for moving the PMP rung up didn't fire, and EACC and
          PACC0 weren't stuck, so the trigger for writing the CPU-cluster
          targets last (since dropped) didn't either.
        - The expected case: a reset at ~30 s with only DISPLAY's wait
          timed out (AFR's power state is already off, so its wait passes),
          and EACC and PACC0 settled. DISPLAY is then the only one of
          macOS's PMGR waits that fails. Next are the alarm, then DISPLAY
          (among the fix rungs below).
        - EACC or PACC0 still pending ~500 ms after their complexes read
          off: a CPU cluster doesn't finish even in macOS's place. Writing
          the CPU-cluster targets last comes up (since dropped: this didn't
          happen); first rule out CPU7's own PACC1.
        - DCS's wait times out, or DCS isn't settled at 5 at the end of its
          wait after entry 50: the DRAM p-state doesn't settle with the PMP
          running, and the PMP rung moves up. There is no reading
          after the commit, on purpose: the ClkGen park and PLL-off come
          first, and on T8103 Naeem found most of the registers where Linux
          and macOS differed at the commit rewritten right after it.
        - SIO_CPU's wait times out: most likely its off parent. ClkGen's or
          PLL-off's: a new lead.
        - Dark past 2 minutes: a candidate. Leave it 30+ minutes, then the
          power key, `m1-crumbs` and a BQCC reading.
        - Dark at stage 0x10: one of step 5's writes or waits stalled, and
          no breadcrumb says which; or raw SPMI failed after 0x10 and the
          entry went on, which the BQCC reading tells apart. Dark at
          0x28-0x58: the stop came in the step that stage names (6-9).
      - A reset with the observer up doesn't rule a change out: CPU7 stays
        up to about 1.5 s after the commit. Any rung whose readout comes
        back clean gets a run without it.
    - The fix rungs after run 3, ranked (RESEARCH.md, "After M1 run 3" and
      "After M1 run 4"): one per run, and each one that moves the replay
      toward macOS's sequence stays in the runs after it, unless Corey picks
      a cumulative rung.
      **The order after run 4 (judgment; this is the only place it's kept):**
      the PMP out (rung 3) first if Corey OKs its new RTKit message and ASC
      write, else every core held (rung 2), which needs only Corey's go; then,
      beside the ladder, the reconfig SRAM question and DJ's T8140 driver
      (both Corey's OK); then rungs 4-7 as numbered. Corey picks.
      1. **M1 run 4: the PMU's RTC alarm** (ran 2026-11-08, `e1_hook alarm=1`,
         `pkexec e1-hook/run-floor --m1-alarm`: stage 0x5f, 0x6016 = 0xbd,
         the reset at about 30 s; the armed alarm survived it), written between the first
         commit and the other two. macOS does it on T8103 (mac27-hv README
         L88-91);
         DeepIdle reports the alarm after the commit from DJ's T8140 trace
         (worklog L2585, L2610; it doesn't say how many commit writes there
         are, or whether any follow the alarm), and its decode of macOS 13.5
         has T6020 re-program the alarm
         after quiesceHW (a static decode, not a trace). Every completed
         T8103 Linux entry has it. Only this Mac's run 4 has written it after
         the commit, between the commit's first write and the other two (run 4,
         with nub-spmi0's enables all 0, and it still took the reset); T6020
         armed it before the suspend (L2741), and runs 1-3 never armed it.
         Against it: macOS's try41 had the alarm and still took WD2's reset
         (README L370-373), so it isn't enough by itself (run 4: nor on T6000
         with its line off), and whether it's needed is untested on every
         SoC (run 4 can't say: an entry that doesn't complete looks the same
         either way). A cheap test of a low-prior cause.
         - Corey OK'd its PMU writes on 2026-11-07: 0x14c0 (the event,
           written back if set), 0x140c (now + 3600 s), 0x14c2 (0), 0x1401
           (0x41), and 0x1401 back to 0x01 at the next boot. No new MMIO, no
           SEP message, no never-write address.
         - **Its shape, Corey's pick of 2026-11-08:** `pmgr_late` + the
           alarm with no observer, CPU7 released before the commit as in run
           1. That is the shape of every completed
           T8103 *Linux* entry (cores down before the commit, CPU0 alone doing
           commit, alarm, commit twice, WFI): a lab choice, not a move toward
           macOS, whose own entries keep every core up to the commit (rung
           2). Two changes against run 3, so it's cumulative; on a reset it
           clears the alarm and CPU7's staying up as sole blockers in one
           run (run 4, a reset: both, on the assumption that the alarm's
           commands and the post-commit acks are harmless. Run 1, CPU7 down
           with no alarm and no `pmgr_late`, had already reset; run 4 extends
           that to `pmgr_late`; run 3 without the observer and without the
           alarm is still unrun). (The strict way, one change at a time, was
           run 3 without the observer first, then the alarm.)
         - The build: `alarm=1` (with `m1=1 pmgr_late=1`, no `observe`). In
           step 9: commit once, dsb; then over raw polled SPMI, only if step
           8's RTC read worked: read 0x14c0 and, if it read non-zero, write
           that byte back; write 0x140c (4 bytes LE) = step 8's RTC second +
           3600 (the RTC's own seconds, no offset); 0x14c2 = 0; 0x1401 =
           0x41; then commit twice more, dsb, stage 0x5f, ack nub-spmi0's
           words, and step 10. The helper stops at the first failure, flushes
           the FIFO and still writes the other two commits, so a failed alarm
           leaves a `pmgr_late` run without the observer (not a repeat of
           run 3, whose CPU7 stayed up), with the failure recorded. Every SPMI
           command leaves a vector to ack before the deep WFI (Naeem's
           b6b2ea1d19e0), which is why 0x5f comes before the acks.
         - Breadcrumbs, in the OK'd bytes: stage 0x59 in place of step 8's
           0x58 (it announces that 0x6016 is the alarm's byte) and 0x5f after
           the third commit. 0x6016: bit 7 written, then 0x14c0 read (bit 0),
           its event was set (1), its echo acked or not needed (2), 0x140c
           (3), 0x14c2 (4), 0x1401 (5). 0xbd is all acked with no event, 0xbf
           all acked with one echoed.
         - Every `m1=1` load reads 0x1401 and refuses if any bit but 0 and
           1 is set (bit 6: armed). After an alarm run that means
           `m1-crumbs --clear`, which
           disarms it; one macOS left armed shows in `pmu-alarm status`, and
           clearing that isn't in the 11-07 OK. `m1_pmu_ok` gains exactly
           these accesses. `m1-crumbs` prints 0x1401, 0x140c, 0x14c0 and
           0x14c2 before it disarms (any value raw), says whether 0x140c is
           the commit's second + 3600, and disarms only an alarm that's the
           run's: stage 0x59 or 0x5f, with 0x140c the commit's second + 3600
           (or, if that second was lost, 0x6016 saying 0x1401's write was
           acked). An armed alarm it can't attribute stops `--clear` with the
           breadcrumbs kept.
         - How to read it:
           - Stage 0x5f with 0x6016 = 0xbd or 0xbf means the alarm's writes
             went through and all three commit writes were sent: the run
             counts. Stage 0x59 with those bytes is inconclusive: the alarm's
             commands were acked (0x140c = the commit's second + 3600
             corroborates it), but nothing records the two commit writes
             after them, and 0x5f comes after those. Most likely only that
             crumb was lost; repeat the run before reading a reset as a
             negative. A partial byte, stage 0x59 with 0x6016 = 0, or a wrong
             0x140c at the next boot: repeat it.
           - The next boot's 0x1401 is a separate fact, once 0x6016 says its
             write was acked. 0x41 (0x03 if it fired) means the armed alarm
             survived the reset. 0x01 with 0x140c right means the reset or
             the boot firmware cleared it (new; the run still counts), or,
             past the alarm's time, that it may have fired (T8103's fired
             alarm reads 0x01). `m1-crumbs` says which. (Run 4, 2026-11-08:
             0x41, 0x140c = 188497 = the commit's second + 3600: the armed
             alarm survived the reset and the boot; `--clear` set 0x1401 back
             to 0x01.)
           - A reset at about 30 s: the alarm written between the commits,
             with nub-spmi0's enables all 0, doesn't complete the entry,
             assuming its own commands are harmless. (Run 4: this case, with
             CPU7 down before the commit and no observer; its own commands
             include the SPMI commands after the first commit and the
             post-commit acks.) That doesn't test the
             alarm with its line enabled: every completed T8103 run had word
             6 = 1 or word 0 bit 2 set (apple-s2r.c L420-436). A later variant
             could set EN(0) = BIT(2) in step 7 (a new value in a register
             step 7 already writes, no PMU byte), and a fired alarm could
             then wake the SoC at +3600 s. The alarm stays in later runs: it
             moves toward macOS.
           - Dark past 2 minutes: a candidate; BQCC decides. Leave it at
             least 30 minutes, and end it before about 55: if the alarm wakes
             the SoC at +3600 s, the wake would land in m1n1 with WD1 off and
             no resume path, and the Mac would hang, at an unknown draw,
             until the power key.
             In the next boot: `m1-crumbs`, then `pkexec m1-crumbs --clear`
             (a PMU write can't change BQCC), then the reading. Then
             attribute it: run 3 without the observer (`--m1-late-noobs`, to
             build then; no module change).
           - A reset well away from 30 s: new; read everything else first.
      2. **Every core held through the commit** (XNU's global sleep on H13;
         RESEARCH.md, "After M1 run 3" and "After M1 run 4"). Each parked
         core writes its own stop bit, as in runs 1, 3 and 4, and holds,
         spinning, until CPU0 lets go after the third commit write (Naeem's
         reading of the T8103 traces has each secondary writing its own bit,
         about 40 ms before the commit, bd01a988d309; CPU0 writing CPU7's bit
         in `observe=1` was this replay's choice); then each arms ACC_OVRD and
         takes its deep WFI. Same addresses, no new PMU byte, toward macOS, so
         it needs only Corey's go. Naeem's newest commits do this on T8103,
         with no result yet. Against it: Naeem's earlier T8103 Linux entries
         complete without it, and T6020's R11-R15 released their secondaries
         after the commit, R16-R17 before it, and all reset. Low prior (about
         1 in 15, judgment). The readout: CPU0 reads the others down after
         the release and goes last, since CPU7 as the observer would stay up
         through the commit in a way the rung is about (and `alarm=1` refuses
         the observer); a run without that readout comes after a clean one.
         Built, to run (`e1_hook hold=1`, `pkexec e1-hook/run-floor --m1-hold`):
         the same recipe as run 4 (`m1=1 pmgr_late=1 alarm=1`, no observer) and
         this one change.
         - In step 6, CPU0 asks each core in turn (stage 0x31-0x37 by its place
           in the order) and waits up to 10 ms for it to say held: the core
           clears its fast IPI and AIC events, records ISR_EL1, writes its own
           stop bit, says held and spins, every exception masked, on a flag in
           memory. After the third commit write, stage 0x5b and the acks, CPU0
           sets the flag; each core clears its IPI and AIC events again,
           records ISR_EL1, says out, arms ACC_OVRD's deep sleep (every core,
           as XNU's `arm64_prepare_for_sleep()` does: the last-of-a-cluster
           rule was for cores going down under running siblings) and takes its
           deep WFI. CPU0 then reads each one's power state (up to 10 ms each,
           in the park's order) and PCPM0's and PCPM1's, records that, and
           takes its own deep WFI last. `--m1-hold-nowatch` skips the
           reading, the run to make once a watched one comes back clean. No
           new MMIO address and no new PMU byte.
         - Breadcrumbs, in the OK'd bytes: 0x6004 0x31-0x37 and 0x39, then
           0x5a (in place of 0x58) before the commit with the held mask in
           0x6007, 0x5b after the third commit, then 0x5c with 0x6005 = the
           cores read off after the release, 0x6012 = those that took the
           release, 0x6013 = those with an interrupt pending just before their
           deep WFI, 0x6014 as before with PCPM0's and PCPM1's bits read after
           the release, and 0x5e (0x5d: CPU0 had something pending just before
           its own WFI). The alarm's 0x6016 as in run 4. Only the commit
           second's low two bytes stay from 0x5c on, which is enough for the
           reset's time and `m1-crumbs --clear`.
         - How to read it: the expected case is a reset at about 30 s with
           stage 0x5e, off 0xfe, held 0xfe, 0x6012 0xfe, 0x6013 0, the PCPM
           bits set, `qto` 0x50, 0x6016 0xbd and the alarm armed at the next
           boot: every other core held through the commit, took the release
           and read off after it, and the entry still doesn't complete. CPU0's
           own last step and the E complex's power-down stay unobserved. Then
           `--m1-hold-nowatch`, to rule out CPU0's reads after the release.
           A core held but not read off, or not out: the hold broke its power-down
           (0x6013 says if it had something pending). Not held at 0x39 or
           0x5a: it never finished its stop request; the run still goes on to
           the reset. PCPM bits clear with every core off: a P complex didn't go
           down. A stop at 0x31-0x37, 0x39, 0x5a or 0x5b says where the breadcrumbs
           stopped (0x5a with 0x6016 = 0xbd is the same inconclusive case as
           0x59 was). Dark past 2 minutes: a candidate; at least 30 minutes,
           end before about 55 because of the armed alarm, `m1-crumbs`,
           `--clear`, then the BQCC reading: only a low BQCC shows a completed
           entry, and a stop at 0x31-0x5b isn't one. A reset well away from
           30 s: new; read everything else first.
         - Could ride along: entries 2-6 (WD0's control and the four +0xa00
           masks), which `pmgr_late` still sends before the keys in
           maralcbr's lab order. Toward macOS, no new write, low prior.
      3. **The PMP out of the entry.** Its idle pair (SetAPPower, then
         SetIOPPower 0x201, once each: a doubled SetAPPower crashed T6020's
         PMP), then its CPU stopped (the ASC's RUN bit, 0x28ec00044), as
         macOS does before quiesceHW. Or a boot where it never starts
         (`module_blacklist=apple_pmp`, after a pre-check boot: it costs
         the GPU and maybe the panel, and changes DCS handling). Every
         completed T8103 Linux entry ran with no PMP firmware or with it
         idled. Naeem's run 9 idled it between `slpw` and `slpa`
         (malik-na/linux-aurora `m1power/s2r-pmp`), while macOS's T6020 trace
         sends the pair right after `slpa`; interrupts are off from step 1,
         so here it goes before step 1 or over a polled path. On T6000 the
         PMP owns DCS, which the table lowers. The highest prior of the
         rungs that need a new OK (judgment), but T6020 idled the PMP and
         still reset, and no R-run stopped its CPU. A new PMP message and an
         ASC write: Corey's OK first. ps_pmp stays on the never-write list.
      4. **Two unfinished transitions removed** (a probe, not a fix): skip
         table entries 58 (DISPLAY) and 61 (SIO_CPU), and keep their wait
         loops running, so `qto` should read 0x00. No new write. These are
         the only two macOS waits run 3 measured as unmet, and both unfinished
         states are this replay's. It moves away from macOS's resulting
         state (DISPLAY down, SIO_CPU on), so it isn't kept: a dark Mac means
         a stuck transition holds the engine (bisect with single skips, then
         make the state finish the way macOS does); a reset says only that
         the pending bits as such aren't the sole blocker.
      5. **SIO to macOS's state** (Corey told first): `ps_sio` (0x28e580180)
         on with genpd's own power-on word (0x0f00000f) just before entry 61,
         polled to actual f, then entry 61 and its wait as the table has
         them. A new PMGR write outside macOS's T6000 table. T6020 had this
         state and still reset; expect no change. A hang here is dark at
         stage 0x10 (power key).
      6. **DISPLAY settled at the commit**, with Corey told first: on a test
         kernel, for one safe boot, the DCP firmware shut down and disp0's and
         dispext0's domains off at noirq (DeepIdle S6's design,
         `appledrm.test_sleep_off`, credit JJRPF; the always-on report@c and
         @d holds removed for that boot), e1_hook unchanged, so entry 58's
         wait reports, for the first time on any chip, whether DISPLAY's
         cluster settles with the chain off. T6020's display-off runs reset
         but never recorded that bit. Two of the words share ps_pmp's page;
         the never-write check stays. Not recommended: forcing the chain
         off from the hook. (DeepIdle's Y-run stalls were most likely
         unmapped pages, worklog L2614-2616, so that's untested; R6, with
         Linux turning the domains off itself, still reset.)
      7. **Fabric lanes to macOS's PS_AUTO 0**, after DeepIdle's R21 reports:
         0x1000030f to 0x28e080220 (AFNC1_LW2), 0x28e580160 (AFNC2_LW1) and
         0x28e080320 (APCIE_GP_SYS), after entry 61 and before 62. New PMGR
         writes: Corey's OK first. e1_never_write still refuses 0x28e0802d8,
         which shares a page with two of them.
      - A question for Corey, beside the ladder: the engine's reconfig SRAM,
        read-only, the one step that could read what it waits for rather
        than guess. Naeem dumped and decoded T8103's (0x23df00000, 128 KiB,
        12 sequences; linux-s2r README L99-104); on T6000 the address isn't
        known. A basis (inferred; RESEARCH.md, "After M1 run 4"): PongoOS's
        table has T8012's and T8015's at AON + 0xf00000, as T8103's is, which
        here would be 0x292f00000; but the AON block isn't a same-offset
        copy of T8103's (the commit is +0x2c4000 on T8103 and +0x5c4000 here),
        the ADT's AON windows don't reach that offset, and PongoOS's are
        0x10000 long and read the table's offset from a config register
        (T8103's is at SRAM + 0, Naeem's dump). So the first read would be that
        register's analogue by the commit's shift, 0x2925c0200 (and its lock
        at 0x2925c0214, as PongoOS's +0x2c0214), inside the ADT-declared
        window 0x2925c0000 + 0x14000; then one word of 0x292f00000; then a
        table check before any dump, 0x10000 first. An unmapped or unlisted
        AON address may SError or have read side effects, and a read isn't
        part of any run: Corey's OK first, in a safe boot of its own or with
        the confound stated.
      - Riding along when bytes allow, reads only: WD2's raw count and bite
        after the commit (what the commit loads), and the commit page. If an
        observer run comes back: first-read buckets in microseconds with a
        first-sample bit, SIO_CPU's actual nibble in place of DCS's, and
        the fabric's p-state before the ClkGen park. AON perf group 0's
        state counters (0x2922b4000: AWAKE, SLP_S2R, DEEP_WAIT...; T6020 read
        them after an enable, worklog L1698-1712) are the only engine-side
        reading planned, and retiring the observer gives them up, but
        enabling them is a new AON write (Corey's OK) and CPU7 can't see past
        its own stop, so they may only ever show AWAKE.
      - Later, each with Corey's OK: SetAPPower 0x201 to the SMC right after
        `slpa` (T8103 completes with and without it, and T6020 sent it and
        still reset); the SEP's SLEEP message (it touches state the SEP
        shares with macOS; DeepIdle's R18 shows the SLEEP message alone
        isn't enough on T6020, and this Mac's SEP word already reads macOS's
        value); WD2 stopped after the commit, which breaks the recipe's
        documented rule (`e1_never_write()` doesn't enforce WD2) and can
        leave the Mac dark; maralcbr's other bits (the domains macOS has
        off, 8-10; the AOP's notices, 6; the internal PCIe ports, 13); CPM
        gating (read the 0x28e09c000 page first); XNU's per-core debug
        power-down before the deep WFI; anything on nub-spmi1. Dropped: the
        CPU-cluster targets written last (run 3 found EACC and PACC0 not
        stuck, so its trigger didn't fire).
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
