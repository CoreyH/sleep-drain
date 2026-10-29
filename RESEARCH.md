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
| 2026-10-22 | 7.2.10, AOP unbound | on | 17.6 | 99% → 48% | **1825 mW** (no change) |
| 2026-10-25 | 7.2.10 | **off** | 17.0 | 100% → 52% | **1797 mW** (−41 mW vs 1838) |
| 2026-10-26 | 7.2.10, USB/PCIe wake off | off | 24.2 | 36% → (charged) → 44% | **no result** |
| 2026-10-27 | **7.2.11**, USB/PCIe wake off | off | 17.7 | 100% → 50% | **1759 mW** (−38 mW vs 1797) |
| 2026-10-28 | 7.2.11 **aurora9**, USB/PCIe wake **on** | off | 19.8 | 88% → 28% | **1844 mW** (+85 mW vs 1759) |

The 2026-10-26 run doesn't count. The laptop went to sleep at 10:31 at 36%, was
charged while asleep, unplugged while asleep, and stayed asleep until 10:45 the
next day. With no reading at the unplug, there's no way to split charging from
drain. The snapshot did show:
- 54 `macsmc-ac`/`macsmc-battery` wakeup events that never woke the system;
- the AOP mailbox at 3.62/s again (315,770 IRQs in 87,229 s);
- `/dev/kmsg buffer overrun`: 313,418 kernel messages lost. `pm_debug_messages`
  logs a "Timekeeping suspended" line on every s2idle wakeup, so overnight it
  pushed the PMGR suspend-entry lines out of the ring buffer.

The hook no longer turns on `pm_debug_messages`. For a valid night, the laptop
must be awake on battery right before the sleep: if it went to sleep plugged in,
open the lid after unplugging and close it again.

The 2026-10-27 night is the valid re-run. The kernel log confirms that
atc0–3_usb switched off for the sleep. As in the 68 s test, the Wi-Fi PCIe
link (apcie_gp, pcie0_ref) and the DCP domains stayed on. Two things changed
against 2026-10-25, though: USB/PCIe wake off, and the kernel (7.2.10 →
7.2.11, installed that morning). So −38 mW is an upper bound for what USB-C
wake costs. Either way it is small, like the radios (−41 mW). Together, radios
off and wake off take 1838 → 1759 mW (−79 mW, −4%). The AOP mailbox still fired
3.6 times a second (230,527 in 63,685 s).

The 2026-10-28 night (USB-C wake back on, radios off, kernel aurora9) drew
1844 mW, 85 mW more than 2026-10-27. As expected, atc0–2_usb stayed on all
night; atc3, with no controller bound, switched off. The AOP rate was
unchanged (3.62/s), and nothing else looked different. Three nights with radios
off now bracket the question: 1797 (7.2.10, wake on), 1759 (aurora8, wake
off), 1844 (aurora9, wake on). There is no clean measure of night-to-night
noise yet. The closest pair, 2026-10-21 and -22 (1838 and 1825 mW), is not a
repeat: the AOP was unbound on the second night. Their 13 mW gap is only an
observed spread. The gauge may also be noisier than that: aurora #274 found
`energy_now` jumping ±55 mAh after a rest. Taking the differences at face
value, two readings fit:
- USB-C wakeup costs about 85 mW, and 7.2.10 → aurora8 added about 47 mW;
- or it costs about 38 mW, and aurora9 added about 47 mW.
Re-running the 2026-10-27 flags (radios off + wake off) on aurora9 would tell
them apart, if gauge noise doesn't swamp the difference. Nights logged with
the coulomb counter (`avg_mw_cc`) won't depend on the gauge.

`sleep-drain-diff` read that night wrong at first, listing atc*_usb as never
switched off. "PM: resume from suspend-to-idle" is a `pm_pr_dbg` line, so it
stopped being logged once the hook dropped `pm_debug_messages`. The diff then
took each domain's last state from the whole log, which is the state after
resume. Without that marker it now takes the deepest state each domain reached
before "suspend exit".

## Kernel context: what aurora already does for j314s (2026-10-28)

Two omacom/linux-aurora PRs merged on 2026-10-15 and are in every 7.2 kernel
tested here:

- **#91 (pmgr-misc, PMP for j314s)** turns on PMP, the power-management
  coprocessor, for this board in the device tree. The boot log says so:
  "apple-pmgr-misc …: PMP manages fabric and DCS p-states". The PR measured
  s2idle at 2.72 → 2.25 W on 5 V input, which most likely explains the −340 mW
  from 11.16 to 7.2.10 (2026-10-21). The same PR adds an idle fabric governor
  (`apple_pmgr_misc.idle_fabric_pstate`, 0 here). It refuses to run while PMP
  owns the fabric, and it only acts while awake, so it is no knob for sleep.
  Open item from the PR: the fabric save and restore around suspend is a host
  workaround; a proper firmware sleep handshake needs reverse engineering.
- **#92 (thunderbolt, opt-in s2idle port teardown)**:
  `thunderbolt_apple.s2idle_teardown` (N here) powers off connected
  Thunderbolt/USB4 ports that carry no PCIe tunnel for the sleep. It measured
  4.03 → 2.71 W with an M4 Mac attached. It does nothing with no cable plugged
  in, which is how every night here is measured. Turn it on if the laptop sleeps
  attached to a Thunderbolt peer or a non-charging dock.

Kernel 7.2.11.aurora9 (installed 2026-10-28, built that morning) is mostly
M3-family (T603x, G15 GPU) and display work. Nothing in it touches the t6000
sleep path, so the 2026-10-28 night counts aurora8 → aurora9 as no change. If
its result looks off, re-run the 10-27 flags on aurora9 to check.

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

### Unbind test (2026-10-22, awake, 20 s per phase)

| phase | IRQ 40 | Total System Power |
|---|---|---|
| apple_aop bound | 120 | 16.0 W |
| unbound | **0** | 14.4 W |
| bind attempted (failed) | 0 | 14.5 W |

Unbinding `293c00000.aop` stops the mailbox interrupts completely. The awake
drop of about 1.5 W is suggestive but unproven: it's a short sample, and the unbind also
removed the AOP audio card (`aop_audio`, "J314 HPAI", the built-in mics), which
may have been streaming. Rebinding fails on t6000: every endpoint (0x20–0x2b)
times out its shutdown acknowledgment, then "Failed to stop AOP ETIMEDOUT",
then on bind "RTKit: endpoint discovery failed: -62". That matches
`quiesce_on_unbind: false` for T6000 in aop.rs. So the AOP stays gone until a
reboot, and unbind/bind around each suspend isn't a usable workaround. The lid
switch comes from the SMC (macsmc-input), so suspend and wake are unaffected.

Night experiment: 2026-10-22, AOP left unbound (no reboot), radios on, 7.2.10.

## Finding 2: the drain is static, not wakeups (2026-10-23)

With the AOP unbound, the 2026-10-22 night drew 1825 mW, the same as 1838 mW
with it bound. Yet the CPUs were almost perfectly quiet: s2idle stretches
averaged 228 s, the longest was 3600 s, and the night's totals were 3,344
arch_timer ticks, 7,242 function-call IPIs and 1,304 NVMe interrupts. So the
3.6/s AOP wakeups cost little. ALS stopping at suspend would still be a nice
upstream cleanup, but it's not the fix.

The 1.8 W is a floor: something stays powered while everything sleeps. cpuidle
offers `CPU PD` per core ("CPU/cluster powered down") and nothing for the rest of
the SoC (fabric, memory controller, coprocessors, PCIe). The per-state
`s2idle/time` counters are useless for this: they only cover a few seconds over
two nights, because timekeeping is suspended during the deep part of s2idle.

Candidates for the floor, roughly from cheapest test to most effort:
- The PCIe devices stay runtime-active (both root ports, 01:00.0 brcmfmac
  Wi-Fi, 01:00.1 bcm4377 BT, 02:00.0, likely the SD reader), so the links and
  controller stay up. Test: radios off, then remove the PCIe devices before sleep.
- NVMe (ANS coprocessor) power state during s2idle.
- The display pipeline (DCP) with the lid closed.
- DRAM and the fabric not entering the SoC sleep state macOS uses. That's the
  hard one, and it needs the m1n1 hypervisor trace.

Method from here: budget device power awake with the SMC `Total System Power`
sensor (display off, idle, toggle one device at a time), then confirm the
biggest delta with one night.

## Finding 3: which power domains stay on during s2idle (2026-10-26)

The hook now turns on dynamic debug for `pmgr-pwrstate.c` around the sleep, so
the kernel log has a `PS <name>: pwrstate = 0x0|0xf` line for every PMGR
domain switch. `sleep-drain-diff` lists what switched off between "suspend
entry" and "resume from suspend-to-idle". A 68 s suspend was enough
(`20261026T091412`).

- 108 domains are on before suspend. **73 are `apple,always-on` in the device
  tree**: genpd never powers them off. They are the DRAM controllers (dcs_00–15,
  amcc0–3), the fabric (afnc*, afi, afc), the nub/aon/SPMI blocks, sep, pmp,
  pms, soc_dpe, aic, dwi, the four ATC ports' common/pcie/usb_aon blocks, and
  debug/debug_usb/debug_auth. This is the part s2idle can't reach.
- 24 switched off for the sleep, including gfx, ans2 (NVMe), audio, i2c/spi/uart,
  sio, one ATC USB block, dispext1 and the SD reader's PCIe link.
- On before, not always-on, and **never switched off**: `atc0_usb atc1_usb
  atc2_usb` (USB-C), `apcie_gp pcie0_ref` (the Wi-Fi/BT PCIe link), `disp0_cpu0
  disp0_fe pmp-disp0` (internal DCP), `dispext0_cpu0 dispext0_fe pmp-dispext0
  pmp-dispext1` (an external DCP with nothing attached).

Why the USB-C blocks stay on: the three bound `dwc3-apple` controllers are
wakeup-enabled, and pmgr-pwrstate.c gives domains that are on at boot
`GENPD_FLAG_DEFER_OFF | GENPD_FLAG_ACTIVE_WAKEUP`, so a wakeup-enabled device
keeps its domain on through suspend. With `/etc/sleep-drain/wake-off`
(`sleep-drain-wake off`: the USB controllers and Wi-Fi stop being wakeup
sources for the sleep, while lid and power button still wake), a 65 s suspend
(`20261026T091720`) switched **atc0–3_usb all off**. The Wi-Fi PCIe link
(apcie_gp, pcie0_ref) stayed on even with brcmfmac's wakeup off; something else
holds it (the driver keeping the chip in D0?). After resume the wakeup settings
were restored and Wi-Fi reconnected.

Night experiment: radios off + wake off. Result 2026-10-27: 1759 mW vs 1797 mW
(radios off alone), on a newer kernel; see Results.

### The Wi-Fi link's domains are held by the PCIe controller (2026-10-28)

`pcie-off` (`sleep-drain-pcie off`) removes the Wi-Fi/Bluetooth chip's PCI
functions for the sleep and rescans after. The awake pre-screen
(`wifi-link-budget`, results/wifi-link-20261028T100742.txt) and a 70 s suspend
(`20261028T102904`) both brought the chip back cleanly on a plain rescan
(`pcie_back=restored`), with Wi-Fi reconnected and Bluetooth powered. But
**apcie_gp and pcie0_ref stayed on** in both, and the genpd tree shows why:

    pcie0_ref <- apcie_gp <- apcie_gp_sys   apple,always-on; holds 590000000.pcie and two IOMMUs
    pcie1_ref <- apcie_st <- apcie_st_sys   not always-on; the SD reader's chain does switch off

A parent domain can't power off while a child is on. The PCIe controller's own
domain is marked always-on in the device tree, so the Wi-Fi port's refclock and
gp domain are on for as long as the controller is, whatever happens to the chip.
pcie-apple has no system suspend/resume (no saving and restoring of controller
state, no retraining links on resume), which is presumably why its domain is
always-on. Powering the controller down in s2idle is kernel work in pcie-apple,
not something a hook can do. No night was spent on pcie-off.

### Upstream angle for the USB-C finding (omacom/linux-aurora)

`drivers/usb/dwc3/dwc3-apple.c` calls `devm_device_init_wakeup(dev)` at probe
("Like a PCI xHCI controller, allow USB devices to wake the system by
default"). But the DWC3 core and its xHCI only come up when a cable connects
(and stay up across sleep as a syscore device, which keeps the domain on
anyway). With nothing attached nothing can signal a wakeup, yet the
wakeup-enabled glue device keeps its `atcN_usb` domain on through s2idle via
`GENPD_FLAG_ACTIVE_WAKEUP`.

Proposed fix, correct for every user: be wakeup-*capable* only while the core
is up (`device_set_wakeup_capable()` on core start/stop), or otherwise keep an
idle port out of the wakeup path. A docked, lid-closed setup with a USB keyboard
still wakes the Mac; an empty port powers down. Tonight's number decides how
much it's worth, and it goes in the patch or issue.

Local policy on corey-mbp14-omarchy (Corey doesn't wake from USB at all):
disable wakeup on the dwc3-apple controllers permanently with a udev rule in
dotfiles `system/`, instead of the experiment flag, once the result is in.
Left to explain after that: the DCP domains (internal and dispext0 stay
powered; dispext1, never bound, does switch off) and the 73 always-on domains.

### Awake power budget, first run (2026-10-23): inconclusive

`results/budget-20261023T103432.txt`. The baselines drifted steadily down
(13.05 → 12.68 → 12.74 → 12.52 → 12.35 → 12.13 W over 12 minutes, charger
plugged in at about 49%), and "display on" read lower than "display off" (12.05
vs 12.13 W). So either `hyprctl dispatch dpms off` doesn't power the panel down
on DCP, or the drift swamps everything. Only Wi-Fi off looks like a saving
(−0.2 to −0.4 W against the neighbouring baselines). Bluetooth off, SD runtime PM
(the SD reader stayed `active` anyway), SD removal and SD L1.1/L1.2 showed
nothing beyond the drift. Next time: on battery, after the system has settled,
with a longer baseline, and with the screen off by a method that verifiably
saves power.

Side effects of that run: `rfkill unblock wlan` didn't bring the network back
(NetworkManager's radio stays off; `nmcli radio wifi on` is needed, which is now
done in both scripts). And awake idle at 12–13 W is very high for an M1 Pro,
which deserves its own look (yesterday's log had `PS gfx: Failed to reach power state`).

Build note: the headers package has `rust/` without .rmeta, and the kernel was
built with rustc 1.85.0, so an out-of-tree Rust module build isn't possible.
It needs a full omacom/linux-aurora build with a pinned 1.85 toolchain.

## Lead: the display processors' PMP requests stay up through sleep (2026-10-29)

omacom/linux-aurora #274 (M3 Pro, J514S) took s2idle from about 2.6 W to
1.35 W. Most of that came from patch 6, which withdraws the external display
processors' PMP report requests in `suspend_noirq` and asserts them again in
`resume_noirq`, for entries marked `apple,release-for-sleep`. Its author also
found `energy_now` useless for short runs (the gauge's estimate jumps ±55 mAh
after a rest), and measured 300 s sleeps with `charge_counter` instead.

T600x has the same mechanism: `pmp_report@28e3c0000`
(`apple,t6000-pmp-v2-report`), whose entries bind the same
`apple,t6000-pmp-v2-report-entry` driver patch 6 extends. In
`t600x-die0.dtsi`, `pmp-disp0` (report@c) and `pmp-dispext0` (report@d) are
`apple,always-on`. `pmp-dispext1` (report@e) is not, and its PMGR parent
(dispext1_cpu0) switches off in s2idle, so genpd already drops that one. Read
awake with `pmp-request` on 2026-10-29, nothing attached: status 0x1,
target = actual = 0x7000, so requests 0xc, 0xd and 0xe are asserted and
acknowledged. pmp-dispext0's request therefore stays asserted all night.

Experiment: `sleep-drain-pmp release 0xd` loads `pmp_sleep_release`, an
out-of-tree module that does what patch 6 does. In each sleep's noirq phase,
after every device's suspend callback has run, it withdraws request 0xd if the
PMP has acknowledged it. It asserts it again in resume_noirq, before any
device resumes. Both writes run under the pmp-report driver's own lock: the
driver is built in, so the module reaches it through the report device's
drvdata, with the struct layout checked against the kernel's BTF and verified
again at load. An earlier draft did the write from userspace in the sleep hook.
Review (#3) pointed out that a userspace read-modify-write of the shared mask
can silently overwrite a concurrent driver update, and nothing in userspace can
prevent that. `pmp-request` is now read-only. Measure with short sleeps (lid
closed at least 10 min, on battery), A/B with the module loaded and unloaded on
the same kernel and flags, by `avg_mw_cc`. If it saves real power, the upstream
fix is marking report@d (and maybe report@c) `apple,release-for-sleep` in
`t600x-die0.dtsi`, on top of #274.

`charge_counter` counts mA·s on this machine, not µAh: over 61 s awake it gave
12.3 W against `energy_now`'s 12.1 W. The hook now logs it (`cc=`, with
`uv=` for the voltage) and reports `avg_mw_cc`.

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
