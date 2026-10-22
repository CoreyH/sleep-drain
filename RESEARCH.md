# s2idle drain on the M1 Pro (j314s): research notes

## The problem

Asleep, the MacBook draws about 2.2 W (about 3.5 %/h). A night in the cabinet costs
about half the battery. macOS sleeps far deeper on this hardware (not measured here yet).
Linux on Apple Silicon only has s2idle (`/sys/power/mem_sleep` = `[s2idle]`):
the kernel freezes tasks, suspends devices and parks the CPUs in their deepest
idle state. Nothing tells the SoC to enter the system-level sleep that macOS uses.
Hibernate isn't an option: Omarchy's `omarchy-hibernation-setup` skips Apple
Silicon, and aurora-linux #99 was closed as unsupported.

## Baseline (kernel 11.16, Wi-Fi and Bluetooth on, nothing attached)

| night | hours | drain | avg |
|---|---|---|---|
| 2026-10-07 | 19.3 | 94% → 19% | 2198 mW |
| 2026-10-19 | 19.8 | 100% → 31% | 2172 mW |
| 2026-10-20 | 24.0 | 100% → 15% | 2175 mW |

The three nights are consistent, so a change of about 100 mW or more should show up in a single night.

## Results

| night | kernel | radios | hours | drain | avg |
|---|---|---|---|---|---|
| 2026-10-21 | 7.2.10 (omacom) | on | 22.0 | 97% → 32% | **1838 mW** (−340 mW, −15%) |

## Finding 1: the AOP wakes the SoC about 3.6 times a second all night (2026-10-22)

`sleep-drain-diff 20261021T141534`: over 79,040 s asleep, IRQ 40
(`293408000.mbox-recv`) fired 286,255 times (3.6/s), followed by about 278k
function-call IPIs and 184k arch_timer ticks. The kernel log shows s2idle
re-entering constantly: "Timekeeping suspended for" never more than 0.38 s
(avg 0.275 s; the log only kept 4,548 of these lines). Nothing else is significant: NVMe 285,
the SMC mailbox 417, Wi-Fi/BT single digits. The only wakeup source is macsmc-input (2).

That mailbox belongs to the **AOP** (Always-On Processor, `293c00000.aop`),
which serves `las` (lid angle), `als` (ambient light) and `audio` (the mics).

- Awake, the same IRQ runs at about 6/s with nobody reading the sensors
  (iio-sensor-proxy isn't running).
- `drivers/iio/common/aop_sensors/aop_als.rs` (omacom/linux-aurora): `enable_als()`
  arms the sensor at probe with `ALS_INTERVAL_US = 200000`, i.e. 5 reports/s.
  The driver has **no suspend/resume**, and `unbind` only unregisters the listener.
- `rmmod aop_als` doesn't change the rate (119 → 119 → 120 per 20 s), as the
  code predicts: the interval stays armed in the AOP.

Hypothesis: ALS reports (plus some LAS) keep waking the CPUs out of s2idle. Each
wake costs a full exit and re-entry on every core, which is a plausible share of the 1.8 W.

Next: patch `aop_als` with PM ops: set `ALS_PROP_INTERVAL` to 0 on suspend and
restore it on resume (or on runtime-PM when no reader is attached). Count IRQ 40
awake first; one night tells the drain. It's still unconfirmed which AOP
endpoint sends each message (aop.rs has no per-endpoint counter), so a debug
counter in `recv_rb` may be needed to attribute ALS vs LAS vs audio.

Build note: the headers package has `rust/` without .rmeta, and the kernel was
built with rustc 1.85.0, so an out-of-tree Rust module build isn't possible.
It needs a full omacom/linux-aurora build with a pinned 1.85 toolchain.

## What gets recorded now

On every suspend, `sleep-drain-log` writes `/var/log/sleep-drain/<id>/{pre,post}/`
(see the header of `sleep-drain-log`). `sleep-drain-report` shows one row per
night, and `sleep-drain-diff [id]` breaks down one suspend: which IRQs fired
while asleep, which wakeup sources fired, CPU idle residency, wakeup lines in the
kernel log, power domains, and the devices that were runtime-active.

First observation (2026-10-21, while awake): cpuidle has only `WFI` and `CPU PD`
per core. There's no cluster-level or SoC-level state.

## Experiments, one variable per night

1. Kernel: Omarchy linux-aurora 7.2.10 (from 2026-10-21), radios on. The new baseline.
2. `sleep-drain-radios off`: Wi-Fi and Bluetooth soft-blocked while asleep.
3. Later, from the diff data: unbind or power off whatever stays runtime-active or
   keeps firing IRQs (Touch ID/SEP, the Neural Engine, the GPU, the display
   pipeline, USB-C/ATC PHYs, the NVMe controller).

The SMC's `Total System Power` (`/sys/class/hwmon/*/power*_input`, label
`Total System Power`) lets you A/B test while awake in minutes. Use it to
pre-screen what a device costs before spending a night on it.

## Bigger tools, when we get serious

- **m1n1 hypervisor** (the Asahi reverse-engineering workflow). Boot macOS, or
  Linux, as a guest under m1n1 with a second machine attached over USB-C. Then
  trace every MMIO access and mailbox message while macOS goes to sleep: the PMGR
  power-state writes, the SMC keys, and what the AOP/RTKit coprocessors are told.
  That's how the sequence Linux is missing would be found. Needs: a second
  computer (Linux or macOS) and a USB-C cable, plus m1n1 from source.
  VMs don't help here, because power state is hardware.
- **SMC key dump** across macOS sleep and Linux s2idle, to compare what the SMC
  is asked to do.
- **Upstream**: report issues and patches to omacom/linux-aurora (where aurora
  development moved on 2026-10-18), with these numbers. The Asahi Linux team
  owns the general s2idle story.
