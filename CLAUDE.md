# sleep-drain: working conventions

Research and tooling for the s2idle battery drain on Corey's 14-inch M1 Pro
MacBook Pro (Omarchy on the omacom/linux-aurora kernel). RESEARCH.md is the
lab notebook: the problem, every night's result, and the findings so far.
PLAN.md is what's next, in order, and how to pick it up: start there.

## Layout

- `sleep-drain-log`: the systemd-sleep hook (installed to
  `/usr/lib/systemd/system-sleep/`). It logs one line per suspend and resume to
  `/var/log/sleep-drain.log`, writes kernel snapshots to
  `/var/log/sleep-drain/<id>/{pre,post}/`, and applies the experiment flags in
  `/etc/sleep-drain/`.
- `sleep-drain-report`: one row per night. `sleep-drain-diff [id]`: what
  happened during one suspend (interrupts, wakeups, power domains).
- `sleep-drain-radios`, `sleep-drain-wake`, `sleep-drain-pcie`, `sleep-drain-pmp`:
  toggle the experiment flags (radios-off, wake-off, pcie-off, pmp-release).
- `pmp-sleep-release/`: an out-of-tree module (C, built against the running
  kernel's headers by `install.sh`) that withdraws chosen PMP report requests in
  each sleep's noirq phase, under the pmp-report driver's lock, as aurora #274's
  patch 6 does. `sleep-drain-pmp` loads and unloads it; nothing loads it at boot.
- `pmp-request.c`: shows the PMP report masks on T600x through /dev/mem,
  read-only. `install.sh` builds it into /usr/local/bin.
- `power-budget`, `wifi-link-budget`: awake A/B tests on the SMC's Total System
  Power. They run as root and restore everything, including the network, before
  exiting.
- `sleep-guard`: powers off instead of suspending when the lid closes on
  battery below a threshold. `upower-critical.conf`, a UPower drop-in, does
  the same for UPower's critical-battery action at 2%, which by default
  suspends here.
- `test-kernel`: installs an aurora kernel package (such as an upstream PR's
  CI build) beside the regular linux-aurora, boots it once via grub-reboot,
  and removes it again, without touching anything pacman owns.
- `sleep-drain-safe` and `safe-boot/`: the safe boot for risky experiments
  (A5). `pkexec sleep-drain-safe boot` makes the next restart, once, the
  regular kernel with its own initramfs, whose `sleepdrain-safe` hook makes
  the opened LUKS device read-only and puts RAM overlays on /, /home and
  /var/log. Nothing the session writes reaches the disk, so a hang or hard
  reset can't damage the filesystem; `/boot` isn't mounted. The disk stays
  read-only all session: `pkexec sleep-drain-safe save [PATH...]` writes what
  the boot added (new log lines, new snapshots, the journal, PATHs) as one tar
  to the boot partition, mounted just for that, and `pkexec sleep-drain-safe
  collect` puts it in place after the next regular boot. The regular
  initramfs never gets the hook. The sleep log's `pre` line says `safe=yes`
  for a sleep in a safe boot.
- `sleep-drain-reading`: idle battery readings that can span a reset (E1's
  measurement check). `take` (root) leaves the Mac alone for 10 minutes,
  logging the battery every 10 s, and records the median of the last
  minute, plus smc-peek's raw `BAAC`, `BACC` and `BQCC`, in
  `/var/log/sleep-drain/reading-<time>/`. Then it stops, restarts (into a
  safe boot, if asked), saves and asks for a power-key reset, or suspends.
  `v0` runs V0, one step per boot. It refuses on the charger or with
  anything attached. `install.sh` builds smc-peek for it.
- `pmu-alarm`: reads the PMU's RTC wake alarm on J314s through its nvmem
  interface, and test-fires it awake (`test`, as root) to check the register map
  before the driver port.
- `rtc-alarm-test/`: the PMU RTC alarm driver test on J314s. Out-of-tree
  modules (the alarm `rtc-macsmc`, the integration SPMI controller, and an
  `of_changeset` that describes the alarm in the live device tree), swapped in
  by `pkexec rtc-alarm-test/load` after `make -C rtc-alarm-test`. Undone by a
  restart; not installed by `install.sh`. `pkexec rtc-alarm-test/check`
  then fires one alarm awake and wakes a few rtcwake sleeps with it, saying
  what ended each. Those sleeps skip the systemd-sleep hook; a timed drain
  test arms the alarm with `rtcwake -m no` and suspends through systemd.
  `rtc-alarm-test/spmi-peek.c` reads nub-spmi0's interrupt mask and ack words,
  read-only. `rtc-alarm-test/trace-irq` (root) traces awake alarms with
  kprobes on the SPMI controller and the alarm's handler. Its
  `--zero-fraction` option briefly clears the RTC offset's sub-second part and
  restores it. `rtc-alarm-test/rtc-phase` times the PMU's counter against
  rtc0 (read-only). The kernel's alarmtimer holds the alarm RTC's module once
  it loads, so only a restart swaps it.
- `smc-peek/`: an out-of-tree module (`make -C smc-peek`, against the running
  kernel's headers) that lists the SMC's keys through macsmc, read-only, in
  `/sys/kernel/debug/smc-peek/{keys,values}`. It never writes a key, and reads
  a function key only if `read_funcs` names it. `smc-keys` decodes and diffs
  its dumps.
- `pmgr-peek.c`: reads PMGR registers through /dev/mem, read-only, and refuses
  any address outside the PMGR windows of the live device tree (`--cpu-start`
  adds the CPU-start block's four words). `pmgr-map` builds its address list
  from macOS's device tree (`/boot/macos-session/iodevicetree.txt`) and
  decodes what it reads.
- `sleep-peek`: both of the above as root (`pkexec`), awake and around one
  timed s2idle sleep that goes through the hook; writes
  `results/sleep-peek-<time>/`. Not installed by `install.sh`.
- `e1-hook/`: E1's module (PLAN.md, A5). `e1_hook.c` is an out-of-tree
  module (`make -C e1-hook`, against the running kernel's headers) that
  registers its own s2idle ops, so that its `prepare_late` runs after the
  whole noirq pass. `s2idle_set_ops` isn't exported, so it finds it through
  a kprobe that's registered disabled and never armed: lab only, never for
  upstream. With `replay=0`, the default, it only logs each op and reads one
  SMC key, `B0AV`, at `prepare` and `prepare_late`; read-only parameters
  count the ops and keep the reads. `pkexec e1-hook/check`, in a safe boot
  only, runs that under `pm_test=platform` and in a 60 s alarm sleep, with
  ftrace's power events on the module's clock; `e1-hook/verdict` says
  whether `prepare_late` ran after the last noirq callback and whether the
  SMC answered there. It saves `/var/log/sleep-drain/e1-hook-<time>/` with
  `sleep-drain-safe save`. `replay` is maralcbr's one-way replay, ported
  from their `lab_s2r_run`, at the first `prepare_late`: `5` is E1a, their
  keys and PMP words (`e1-hook/run-e1a`); `15` is the floor, adding their 74
  post-`slpa` writes and every CPU powered down for good
  (`e1-hook/run-floor`: the Mac never wakes from it; its header says how to
  end it with the power key). `quiet=1` (`run-floor --quiet`, with `15`
  only) leaves out maralcbr's 500 ms and its two AFNC reads after the
  post-`slpa` writes, so the S2R-timer commit is the replay's last MMIO
  access before the CPUs power down. `m1=1` (`run-floor --m1`, with `15`
  and `quiet=1` only) is E2's M1 recipe, Naeem Malik's T8103 entry mapped
  onto T6000: the suspend pinned to CPU0, the other CPUs parked awake
  before the post-`slpa` writes and stopped one by one before the commit,
  interrupts masked, the AP wake mask, boot stage 0x50 and breadcrumbs in
  the PMU's spare scratch bytes; `e1-hook/m1-crumbs` reads and (`--clear`,
  root) zeroes them at the next boot, and says when the SoC restarted.
  `observe=1` (`run-floor --m1-observe`, with `m1=1` only) is its run 2:
  CPU7 stays up through the commit, watches CPU0 and the E complex power
  down, reads the watchdogs and other state, and records them in the same
  breadcrumbs. `pmgr_late=1` (`run-floor --m1-late`, which adds it to
  `--m1-observe`'s; with `m1=1` only) is its run 3: the table's PMGR
  quiesce goes out after `slpa`, in its place in the table, with macOS's
  waits, and the breadcrumbs say which waits timed out and when EACC and
  PACC0 finished powering down. `alarm=1` (`run-floor --m1-alarm`, with
  `m1=1 pmgr_late=1` and no `observe=1`) is its run 4: the PMU's RTC alarm,
  an hour after the commit, written between the commit's first write and the
  other two, with no observer; `m1-crumbs --clear` disarms it at the next
  boot. `13` is the floor with the CPUs left up. `13`
  and `15` load only with `oneway=1` and in a safe boot (`sleepdrain.safe`
  on the command line). Only the first suspend after their load can replay
  (a `suspend_resume` tracepoint probe counts suspends from `enter_state()`'s
  first line), and any suspend that doesn't replay to its end fails with
  `-EIO`, so with them loaded the Mac is in the floor or awake, never in a
  plain s2idle; writing 1 to `cancel` calls the replay off. run-floor waits
  for its suspend (`/sys/power/suspend_stats`) and, if it fails, comes back
  or never begins, calls the replay off, lights the keyboard and prints
  "NO FLOOR" with what happened. The module won't build or load if any of its writes would
  touch the never-write list (0x28e0802d8, `ps_pmp`; 0x28e700000). Both
  launchers run in a safe boot only, take the reading and suspend, and
  unload the module on any other way out before the suspend is asked for;
  after that, run-floor calls the replay off instead
  (`tests/e1a_launcher_test.py`).
  `e1-hook/e1a-platform` runs E1a's replay under `pm_test=platform` instead
  and prints what each key write returned, for a photo, since the NVMe is
  gone after `slpa`. Not installed by `install.sh`.
- `tests/`: hardware-free checks. `python3 -I tests/pmu_alarm_test.py` runs
  `pmu-alarm`'s real test against a fake PMU; `tests/rtc_alarm_load_test.py`
  runs `rtc-alarm-test/load` as root in a bubblewrap sandbox against a fake
  /sys, with one failure injected per case; `tests/rtc_alarm_check_test.py`
  runs `rtc-alarm-test/check` the same way against a stub rtcwake;
  `tests/e1_hook_check_test.py` runs `e1-hook/check` the same way against a
  fake kernel that plays both runs, and `tests/e1_hook_verdict_test.py` runs
  `e1-hook/verdict` on made-up captures, one fault per case;
  `tests/sleep_drain_valid_test.py` runs `sleep-drain-log`'s resume summary
  (drain and validity) on recorded snapshots; `tests/peek_decoders_test.py`
  checks `pmgr-map` and `smc-keys` on synthetic input, and `pmgr-map` on this
  Mac's ADT dump when it's there; `tests/safe_boot_hook_test.py` runs the
  safe boot's initramfs hook in mkinitcpio's busybox ash, in a bubblewrap
  sandbox with stub mount and blockdev, one failure per case;
  `tests/safe_boot_save_test.py` runs `sleep-drain-safe save` in a fake safe
  boot and `collect` in a fake next boot; `tests/m1_crumbs_test.py` runs
  `e1-hook/m1-crumbs` on a fake PMU nvmem file; `tests/sleep_drain_reading_test.py`
  runs `sleep-drain-reading` as uid 0 in a bubblewrap user namespace against
  a fake battery, with stub `sleep-drain-safe` and `systemctl`.
- `patches/`: patch series for upstream, kept here for review before they're
  posted. `patches/pmu-alarm-t600x/` is the PMU RTC alarm for omacom/linux-aurora
  (B2); its README says what's in it and how it was checked.
- `results/`: raw output of the awake tests.

## How changes land

- **Branch → PR → merge**, on CoreyH/sleep-drain. No direct pushes to `main`.
  Pullfrog reviews every PR. Merge with a merge commit, not a squash: commit
  SHAs are cited in RESEARCH.md and in the code.
- Merge without asking once the review bots (Pullfrog) have finished on the
  PR's head commit and nothing they raised is left open: Corey's standing OK
  since 2026-11-06. Pass the full head SHA to `--match-head-commit`. Anything
  posted outside this repo still needs Corey's OK each time.
- Nothing is live until it's installed: after merging, run
  `pkexec bash install.sh` (root only through pkexec; from an agent session,
  only while the Omarchy shell is healthy, or the polkit prompt never shows).
- Experiment flags are machine state, not code. Say in the PR or in
  RESEARCH.md which flags a night ran with.
- **m1n1 is off limits to Claude.** Asahi's m1n1 repo asks AI tools not to
  work on it (its AGENTS.md; asahilinux.org/llm-policy). Claude doesn't write,
  change or draft m1n1 code, not even for Corey to type in. Corey decided on
  2026-11-05 that using m1n1 as a tool is fine here, since nothing goes to
  Asahi. Whether Claude may interpret an m1n1 trace is still open: Asahi's
  policy names that case. Corey decided on 2026-11-07 that Claude may read
  Naeem Malik's Linux S2R driver (malik-na/linux-aurora `m1power/s2r-mem`:
  `drivers/soc/apple/apple-s2r.c` and its deep stop in `smp_apple.c`),
  GPL-2.0-only Linux code whose values come from m1n1 traces, crediting him.
  That isn't m1n1 code; m1n1 itself, its patches and scripts, and DeepIdle's
  Aura stay off limits.

## Experiments

- One variable per night. Compare against the last valid night with the same
  kernel; a kernel update counts as a variable.
- A night is valid only if the laptop was awake on battery right before the
  sleep (`valid=yes` in the log). A sleep that starts on the charger, or charges
  while asleep (by the coulomb counter, not the gauge's energy), doesn't count.
- Short timed sleeps: arm the alarm with `pkexec rtcwake -m no -s 900`, then
  `systemctl suspend`, so the hook runs. Needs `rtc-alarm-test/load` on this
  boot. Compare by `avg_mw_cc`.
- A one-minute lid close is enough to see which power domains switch off
  (`sleep-drain-diff`). Use it to decide whether a night is worth spending.
- The goal is an upstream fix (omacom/linux-aurora), framed as the right
  default for everyone, not only this laptop.
