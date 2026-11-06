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
- `tests/`: hardware-free checks. `python3 -I tests/pmu_alarm_test.py` runs
  `pmu-alarm`'s real test against a fake PMU; `tests/rtc_alarm_load_test.py`
  runs `rtc-alarm-test/load` as root in a bubblewrap sandbox against a fake
  /sys, with one failure injected per case; `tests/rtc_alarm_check_test.py`
  runs `rtc-alarm-test/check` the same way against a stub rtcwake;
  `tests/sleep_drain_valid_test.py` runs `sleep-drain-log`'s resume summary
  (drain and validity) on recorded snapshots; `tests/peek_decoders_test.py`
  checks `pmgr-map` and `smc-keys` on synthetic input, and `pmgr-map` on this
  Mac's ADT dump when it's there; `tests/safe_boot_hook_test.py` runs the
  safe boot's initramfs hook in mkinitcpio's busybox ash, in a bubblewrap
  sandbox with stub mount and blockdev, one failure per case;
  `tests/safe_boot_save_test.py` runs `sleep-drain-safe save` in a fake safe
  boot and `collect` in a fake next boot.
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
  policy names that case.

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
