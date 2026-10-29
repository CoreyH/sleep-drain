# sleep-drain: working conventions

Research and tooling for the s2idle battery drain on Corey's 14-inch M1 Pro
MacBook Pro (Omarchy on the omacom/linux-aurora kernel). RESEARCH.md is the
lab notebook: the problem, every night's result, and the findings so far.

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
- `sleep-guard`: powers off instead of sleeping when the battery is too low.
- `results/`: raw output of the awake tests.

## How changes land

- **Branch → PR → merge**, on CoreyH/sleep-drain. No direct pushes to `main`.
  Pullfrog reviews every PR. Merge with a merge commit, not a squash: commit
  SHAs are cited in RESEARCH.md and in the code.
- Nothing is live until it's installed: after merging, run
  `pkexec bash install.sh` (root only through pkexec; from an agent session,
  only while the Omarchy shell is healthy, or the polkit prompt never shows).
- Experiment flags are machine state, not code. Say in the PR or in
  RESEARCH.md which flags a night ran with.

## Experiments

- One variable per night. Compare against the last valid night with the same
  kernel; a kernel update counts as a variable.
- A night is valid only if the laptop was awake on battery right before the
  sleep (`valid=yes` in the log). A sleep that starts on the charger, or charges
  while asleep, doesn't count.
- A one-minute lid close is enough to see which power domains switch off
  (`sleep-drain-diff`). Use it to decide whether a night is worth spending.
- The goal is an upstream fix (omacom/linux-aurora), framed as the right
  default for everyone, not only this laptop.
