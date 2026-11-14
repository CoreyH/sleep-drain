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
- `e1-hook/`: the one-way sleep-entry experiments (E1 and E2; PLAN.md, A5). `e1_hook.c` is an
  out-of-tree module (`make -C e1-hook`) that runs at the end of a suspend's noirq pass and, with
  `replay=15`, replays macOS's sleep entry for good: the Mac resets or stays dark, and never wakes.
  `run-floor` is its launcher, one option per rung, each rung adding to the one before
  (`--m1-acc-last` is the latest); `m1-crumbs` reads the record a run leaves in the PMU after the
  restart, and `--clear` zeroes it. `probe` is the one launcher that isn't one-way: it loads the module
  with `probe=1`, which reads three coprocessor words in the recipe's state and fails the suspend, so
  the Mac is meant to come back (it didn't: with two firmwares stopped the kernel doesn't resume);
  `probe --look` adds `look=1`, which stops nothing and writes no register, only reads; `probe --gate`
  adds one test to the look that changes six power words and writes them back (it didn't come back
  either: treat it as one-way; the PMU carries its result); `probe --pre` adds another, which writes the
  22 clock words the engine's entry writes first, records every read-back and writes them back (it
  came back, and none of the 22 took the write). The rest are one-purpose tools for single reads and
  writes (`recfg-peek`, `recfg-bite`, `wd2-bite`). **`e1-hook/README.md` says what every parameter, option
  and tool does: read it before working here.** What always holds: a one-way run loads only in a
  safe boot and with `oneway=1`; the module won't build or load if a write would touch the
  never-write list (0x28e0802d8, `ps_pmp`; 0x28e700000); the reconfig SRAM's dumps stay out of the
  repo; nothing here is installed by `install.sh`.
- `tests/`: hardware-free checks, one file per tool, each run as `python3 -I -B tests/<name>.py`.
  `tests/README.md` lists what each covers. The two that guard the one-way runs are
  `tests/m1_crumbs_test.py` (the decoder, and `e1_hook.c`'s own table, wait and write functions
  compiled in user space) and `tests/e1a_launcher_test.py` (every `run-floor` option, about 6 minutes);
  `tests/e1_hook_probe_test.py` guards `e1-hook/probe`.
  `tests/mutants.py` breaks that code one way at a time and says what the first of them missed.
- `patches/`: patch series for upstream, kept here for review before they're
  posted. `patches/pmu-alarm-t600x/` is the PMU RTC alarm for omacom/linux-aurora
  (B2); its README says what's in it and how it was checked.
- `results/`: raw output of the awake tests.

## How changes land

- **Who decides what.** Corey, 2026-11-10: "this entire project and codebase is
  yours now. how it is built is entirely you call now and there are no other
  developers or agents in the codebase so make it comfortable for you and you
  alone." So the code, the tests, the docs and these conventions are Claude's
  to shape, for its own use from one session to the next, without asking.
  And later that day, asked to approve a changed register write: "you can
  continue here without waiting for my approval to build new steps/phases. let
  me know when I need to run the safe boot side of things." So a new step of
  the experiments, its register writes included, is built, reviewed and merged
  without waiting, and RESEARCH.md and PLAN.md record what each write is and
  why. What stays Corey's (Claude's reading): they run every safe-boot run
  themselves, so the steps Claude hands over say in plain words what's new in
  that run; and anything posted outside this repo needs their OK each time.
  Claude still asks first before building anything that could outlast a
  restart or damage the Mac (persistent PMU or NVRAM state, the SEP, firmware).
- **Branch → PR → merge**, on CoreyH/sleep-drain. No direct pushes to `main`.
  Pullfrog reviews every PR. Merge with a merge commit, not a squash: commit
  SHAs are cited in RESEARCH.md and in the code. A build that writes to
  hardware also gets one adversarial review by a subagent before it merges;
  its fixes and plain docs changes don't get a second one.
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
  Aura stay off limits. On 2026-11-13 Corey OK'd reading his second driver too
  (`m1power/s2r-rs1`: `drivers/soc/apple/s2r_seq.rs`, `s2r_rs.rs`), credited.
  It follows the vendor's kernel "as read", not traces: leads to check on
  this Mac, never values for a patch that goes upstream. On 2026-11-12 Corey OK'd the same for the M4 sleep
  drivers in aurora-silicon/linux#1477 and #1664 (`drivers/soc/apple/t6040-sleep.c`
  by fbalazsy, `t6041-sleep.c` by AxxzyWasTaken; `GPL-2.0-only OR MIT`),
  credited; aura itself, which they run under, stays off limits.

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
