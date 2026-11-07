# s2idle drain on the M1 Pro (j314s): research notes

## The problem

Asleep, the MacBook draws about 2.2 W (about 3.5 %/h). A night in the cabinet costs
about half the battery. macOS sleeps about twenty times deeper on this hardware: 7-10 mA,
about 0.1 W, in a 185-minute clamshell sleep on battery under macOS 26.6.2 on 2026-10-30
(measured on this Mac and posted to [omacom/linux-aurora#290](https://github.com/omacom/linux-aurora/issues/290)).
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

**Result (2026-10-29): no.** Three lid-closed sleeps on battery, kernel
aurora9, radios off, measured by `avg_mw_cc`:

| run | withdrawn in sleep | slept | avg (cc) | avg current |
|---|---|---|---|---|
| 1 | 0xd (pmp-dispext0) | 824 s | 2012 mW | 162 mA |
| 2 | nothing (control) | 682 s | 1987 mW | 163.0 mA |
| 3 | 0xc + 0xd (pmp-disp0 too) | 672 s | 1945 mW | 161.6 mA |

The module withdrew the requests every time (`pmp_released` 0x2000, then
0x3000), the PMP acknowledged them, both were asserted again with no
failures, and the screen came back normally. Withdrawing pmp-dispext0 saved
nothing. Withdrawing both display requests came out 42 mW below the control,
but the current barely moved (163.0 → 161.6 mA): most of that difference is
the battery voltage dropping between runs (12.19 → 12.04 V), and the rest is
inside the run-to-run spread. On T600x the display processors' PMP requests
don't hold s2idle power up, unlike T6030, where withdrawing them saved about
1.25 W. Marking report@c or report@d `apple,release-for-sleep` isn't worth
proposing for power.

Two method notes from these runs:
- `energy_now` went *up* by 0.66–0.85 Wh in every one of these sleeps, with
  no charger attached: the gauge re-estimating after a rest, as #274 warned.
  The energy-based average read −3.5 to −4.5 W. Short runs need the coulomb
  counter.
- A 12-minute run includes a few seconds awake at each end (the hook reads its
  pre snapshot before capturing and suspending, and its post snapshot after
  restoring), roughly +100 mW at this length. That cancels in an A/B of
  similar lengths, but these numbers sit above the overnight averages for that
  reason. (Corrected 2026-11-05: the journals show only about 1.5-2.5 s
  outside s2idle per sleep, worth 10-40 mW at these lengths, so that isn't
  most of the gap. For E0b, the gap is a full battery's higher voltage; see
  "Before E1" below.)

`charge_counter` counts mA·s on this machine, not µAh: over 61 s awake it gave
12.3 W against `energy_now`'s 12.1 W. The hook now logs it (`cc=`, with
`uv=` for the voltage) and reports `avg_mw_cc`.

## The PMU's RTC wake alarm (2026-10-30)

A timed wake is the piece unattended sleep tests need, and one real
suspend-to-RAM needs. JJRPF/Aurora-DeepIdle got it working on the M2 Pro.
omacom/linux-aurora #274 merged an alarm for the M3's PMU into `rtc-macsmc`.

On this J314s, macOS's IODeviceTree (macOS 26.6.2, `/boot/macos-session/`)
describes the PMU as `spmi-mpmu@F`, `"pmu,spmi","pmu,maverick"`, the same
family as the M2 Pro, with the same map:

| ADT property | offset |
|---|---|
| `info-rtc` | 0x1406 (6 bytes) |
| `info-rtc_alarm_offset` | 0x140c (4 bytes) |
| `info-rtc_alarm_ctrl` / `_en_mask` | 0x1401 / 0x40 |
| `info-rtc_alarm_monitor_mask` | 0x01 |
| `info-rtc_alarm_event` | 0x14c0 |
| `info-rtc_irq_mask_offset` | 0x14c2 |
| `info-clock_offset` | 0x1411 (6 bytes) |

Its interrupt is peripheral 2 of `nub-spmi0`, and `nub-spmi0`'s own line is
AIC 636 (the M3 Pro uses 485, the M2 Pro 724). macOS used it for 13 dark wakes
during a 3-hour sleep. The Linux DT has none of this yet: `nub_spmi0` has no
interrupts, and the rtc node only has `rtc_offset`.

Read through `spmi_nvmem0` (the pmic@f node, world-readable):
- The seconds sit in the upper four bytes of the 6-byte RTC. They **only latch
  when a read starts at 0x1406**; reading 0x1408 alone returns the last latched
  value. The low two bytes read `0a 00` and don't change.
- The alarm compares against those seconds. One was found latched: macOS's last
  scheduled wake (dasd, `wakeAt` 15:03:16), which fired under Linux and decodes
  to 15:03:17. After firing, the control byte read `0x03` (enable cleared, bit 1
  latched) **and** 0x14c0 bit 0 was set, so the event shows in both places, the
  control byte like T6030's.

`pmu-alarm test` arms it awake to see which writes clear the event, before the
driver port: `rtc-macsmc`'s alarm (from #274), plus clearing 0x14c0, plus the
t600x DT cells and interrupts.

### Awake test: the alarm fires, and how its event clears (2026-10-30)

`pkexec pmu-alarm test 20` (`results/pmu-alarm-20261030.txt`):
- Armed 20 s ahead (control 0x41), it **fired on the target second**: control
  0x03 (enable cleared, bit 1 set) and 0x14c0 bit 0 set.
- Writing 1 to 0x14c0 clears 0x14c0. Writing the control byte back as read
  (bit 1 as 1, T6030's acknowledgment in #274) **leaves bit 1 set**. Writing
  it with bit 1 as 0 clears it. Both registers needed their own write, before
  arming and after firing, as on the M2 Pro (Aurora-DeepIdle).
- It also cleared macOS's stale dasd alarm, latched since 15:03.
- The timebase fits the merged driver: CLKM >> 15 (972075.6) matched the PMU's
  seconds (972076). J314s keeps a tick offset (`rtc_offset` at 0x1411, 6
  bytes), so the driver's tick-offset path applies, not `offset_in_seconds`.

So the driver port needs one change: on a PMU with an `rtc_alarm_event` cell,
clear the event by writing 1 there instead of writing the control byte back.
The control byte's bit 1 then clears where the driver writes the control byte
with it as 0. The SPMI interrupt is an edge, so a 0x14c0 left set would block
every later alarm.

### Testing the driver without a new device tree

A device tree saved from one boot can't be booted again. iBoot places the
SEP's firmware and the NVMe controller's (`flash@`) memory anew every boot
(the `OF: reserved mem:` lines differ between consecutive boots), and m1n1
writes the addresses into the tree. A stale tree would hand that memory to
Linux. m1n1's boot.bin carries the tree for every boot, so it isn't a one-off
test either.

`rtc-alarm-test/` makes the change in the running kernel instead
(`pkexec rtc-alarm-test/load`, undone by any restart):
- `pmu-alarm-dt.ko` adds the t600x DT change to the live tree with an
  `of_changeset`: nub-spmi0 as an interrupt controller on AIC 636, the alarm's
  four nvmem cells, and the rtc node's cells, interrupt and `wakeup-source`;
- `spmi-apple-wake.ko` is the integration branch's SPMI controller at
  2037f3505288, which sets `IRQCHIP_SKIP_SET_WAKE`. aurora9 predates it, so its
  controller can raise the alarm's interrupt but `enable_irq_wake()` fails;
- `rtc-macsmc-alarm.ko` is the merged `rtc-macsmc` (integration 0d8a55c70a26)
  plus the `rtc_alarm_event` cell.

The load script unloads `rtc_macsmc` and `macsmc_reboot` (the holders of the
PMU's nvmem cells) and `spmi_apple_controller`, applies the tree change, then
loads the test drivers. Both drivers read the new properties only at probe.

### First swap: T6000's SPMI "reply ready" interrupt is 192, not 256 (2026-10-30)

The swap went through and every driver bound, but every SPMI read then timed
out (`failed to wait for RX FIFO not empty`; `results/spmi-irq-20261030.txt`).
Once the controller has an interrupt, it waits for its "notify" event instead
of polling, and the event never came. The RTC registered without its alarm
(`no alarm: -110`). (AIC 636's interrupt count stayed 0, but that shows
nothing: the controller is a chained handler, and those never update the
per-CPU counts. It stayed 0 once the alarm worked, too.)

The line is right: macOS's nub-spmi0 has one AIC interrupt, 636. Its own
events are numbered from 0xc0, though, not 0x100. With a read's reply left in
the FIFO, `spmi-peek` (read-only, /dev/mem) shows the pending bit at **192**
(ack word 6, bit 0). The driver had unmasked 256 (mask word 8), T6030's
position. So T6000's controller has 192 peripheral interrupts, and its events
follow them. `spmi-apple-wake` now takes the count per SoC: 192 for
`apple,t6000-spmi`, 256 for the rest. Upstream needs this before any t600x DT
gives nub-spmi0 its interrupt; with the stock driver the PMU becomes
unreachable.

### The alarm wakes J314s from s2idle (2026-11-02)

With #9's controller, `rtc-alarm-test/load` came up clean: SPMI reads work
with no timeouts, and `rtc0` has `wakealarm` with wakeup enabled
(`results/rtc-alarm-20261102.txt`; aurora9, on the charger).
- Awake, three times: each alarm fired on its second, and afterwards the PMU
  read control 0x01 with 0x14c0 clear, disarmed and cleared by the driver's
  handler. That's the event-cell change working.
- **Every sleep that nothing else woke ended on the alarm:** 6 of 6
  `rtcwake -m freeze` sleeps of 20-30 s, back to back, with `pm_wakeup_irq`
  the RTC's IRQ. 8 suspends, 0 failures.
- Two other sleeps ended early (8.4 s, 16.2 s) on the Touch ID/power key. The
  SMC reported `BTN_TOUCHID` pressed and released, and `macsmc-input`'s
  wakeup source counted the events; it wakes the system without an IRQ.
  Nobody meant to press it, so this may be a spurious press report. Worth
  watching: it would cut short timed sleep tests and nights alike.
- An awake alarm raised the IRQ 5-6 times, a sleep's only once. Possibly the
  PMU repeats the message until 0x14c0 is cleared; the extra ones find the
  event already clear. Harmless, but look into it before upstream. (Answered
  by B1, below: an RTC-core re-arm loop, not repeated messages.)

This was the missing piece for short timed sleeps: on battery, a sleep ended
by the alarm can stand in for a whole night when comparing one variable.
`rtc-alarm-test/check` reruns these checks.

A drain test can't sleep through `rtcwake -m freeze`, though. rtcwake writes
`/sys/power/state` itself, so the sleep skips the systemd-sleep hook: no
`sleep-drain-log` line, no snapshots, and none of the experiment flags
(radios-off, wake-off, pcie-off) applied. That's fine for `check`, which tests
the driver. For drain, arm the alarm on its own and suspend through systemd:

    pkexec rtcwake -m no -s 900 && systemctl suspend

`mem_sleep` offers only `[s2idle]` here, so `systemctl suspend` is the same
freeze. The hook runs before the sleep starts and counts a sleep under 600 s
as `valid=no-short`, so arm with some margin over 600. wake-off leaves the RTC
a wakeup source: it only disables USB and PCI wakeups.

### aurora14, and the RTC at the real time (2026-11-02)

After the Omarchy 4.5.0rc3 update (linux-aurora 7.2.11.aurora14, built
2026-11-01), the same modules, rebuilt against aurora14's headers, behave the
same (`results/rtc-alarm-aurora14-20261102.txt`, on the charger): the load
came up clean, the awake alarm fired on time, and 3 of 3 sleeps ended on the
alarm. aurora14's stock SPMI controller still has no per-SoC notify position,
so the 192 fix is still needed upstream.

Every run until then had the RTC at 1970: the PMU lost power over the
weekend, which zeroed the offset. It also meant every boot set the system
clock to 1970 when the RTC probed (`hctosys`), until timesyncd corrected it.
This kernel has `RTC_HCTOSYS` but not `RTC_SYSTOHC`, so nothing writes the
right time back. One `hwclock --systohc` fixed it, and gave the alarm its
first test with a real offset (about 1.79e9 s): `wakealarm` 1793627079 became
PMU alarm 8879, in the counter's seconds. It fired awake, and a 30 s sleep
ended on it.

It happened again on 2026-11-05. The laptop stayed awake on battery until 2%,
when UPower's critical action suspended it (22:41, 103 mAh left; the stock
`CriticalPowerAction=Auto` asked logind for a suspend). It never resumed, and
by morning the PMU had lost power: the next boot set the clock to 1970 again
(`hwclock --systohc` fixes it, as before). `install.sh` now makes UPower's
critical action a power-off (`upower-critical.conf`). The kernel has a guard
of its own: macsmc-power answers the SMC's critical-battery event
(0x71020000) with a shutdown once the SMC flags the battery empty (`BCF0`) or
its voltage drops below the design minimum (`BITV`). Whether that event came
during the sleep is unknown: nothing after the suspend was logged.

The awake IRQ count isn't steady: 5-6 per alarm on aurora9, 7 on aurora14's
first, 1 on the next (with the real offset). Sleeps always raised 1. B1, below,
explains it: the offset's fraction is what matters.

`pmu-alarm` now finds the PMU's nvmem device by its node. The number isn't
fixed: each bind of the SPMI controller registers a new one (`spmi_nvmem2`
after this load), and the hardcoded `spmi_nvmem0` made `check`'s status lines
refuse.

### Why an awake alarm raised its IRQ several times (B1, 2026-11-04)

The extra interrupts were a re-arm loop between the PMU and the RTC core, not
the PMU repeating its message.
- **The handler is fast.** `rtc-alarm-test/trace-irq` traces awake alarms with
  kprobes on the SPMI controller and the alarm's handler. One SPMI
  transaction takes 40-60 µs, and the handler has cleared the event within
  0.4 ms of the interrupt. (The "about 20 ms" noted above was a whole
  `pmu-alarm status` run from userspace, not one read.) With this boot's RTC
  offset, three alarms raised one IRQ each
  (`results/trace-irq-20261104T132635.txt`).
- **Two clocks.** The alarm compares against the PMU's counter seconds, while
  rtc0's time is the SMC's `CLKM` plus the tick offset at 0x1411.
  `rtc-alarm-test/rtc-phase` times both: `CLKM` lags the PMU's counter by
  5.5-5.8 ms.
- **The loop.** When the offset's fraction (its sub-second part) is under that
  lag, the alarm fires while rtc0 still reads the second before. The RTC core
  finds its timer not yet expired and sets the same second again. The PMU,
  already there, fires again at once, and so on until rtc0 catches up. In the
  trace, each IRQ is followed by the core's kworker writing the alarm value
  (0x140c) again, about 1.2 ms a round.
- **It's what happened from 2026-10-30 to 11-02.** The PMU had lost power and
  the offset was 0, so its fraction was 0. Since `hwclock --systohc` set it
  (fraction 0.458 s), an awake alarm raises one IRQ.
- **Reproduced.** `trace-irq --zero-fraction` clears the fraction for the run
  and restores it afterwards. Its three alarms raised 6, 5 and 5 IRQs, against
  1, 1 and 1 with the offset as it is
  (`results/trace-irq-20261104T133147.txt`).
- **A sleep always raised one** because its IRQ is handled only at resume,
  long after rtc0 has caught up.

The fix, in `rtc-alarm-test/rtc-macsmc-alarm.c`, for B2's patch: when the tick
offset's fraction is under 1/32 s (31 ms), the alarm is armed one counter
second later. It then fires within the set second, and
`read_alarm` still reports the time that was set. The cost: with a fraction
between the lag and 1/32 s, about 3% of offsets, the alarm comes up to a
second late, which the driver already allows ("the set second or up to one
second after it"). The seconds-offset path (T6030) is unchanged. If T6030's
`CLKM` lags its PMU too, it would loop on every awake alarm (untested).

Trying the fix took a restart. The kernel's alarmtimer holds a reference on
the first wake-capable RTC's module (`alarmtimer.3.auto` is a child of rtc0),
so `rtc_macsmc_alarm` can't be unloaded. That's also why `rtc-alarm-test` is
undone only by a restart.

**Verified after the restart.** With the fraction cleared, the three alarms
raised one IRQ each, where the old driver raised 6, 5 and 5
(`results/trace-irq-20261104T134316.txt`). With the offset as it is, also
one each (`results/trace-irq-20261104T134347.txt`). In both, the alarm value
is written once, when it's set; after the IRQ the RTC core disarms instead of
re-arming. With the fraction cleared, a "+4 s" alarm fired 5 s later: the
extra counter second, still within the set second by rtc0. The margin should
stay well above `CLKM`'s lag. Five of six readings put it at 5.5-6.3 ms, but
the first, right after boot, read 14-24 ms. If B2 sees more than that, raise
the margin to 1/16 s.

### The first timed drain sleep: the aurora14 baseline (2026-11-02)

`pkexec rtcwake -m no -s 900 && systemctl suspend` on battery at 98%, with
`rtc-alarm-test` loaded and the `radios-off` flag (snapshot 20261102T150637).
The sleep ran from 15:06:38 to 15:21:37, ending on the second the alarm was
set for: `pm_wakeup_irq` 144, no macsmc-input events.

**1974 mW by the coulomb counter** (157 mA at 12.6 V). That's inside the
1945-2012 mW spread of the 2026-10-29 short sleeps on aurora9 with radios off
(control 1987 mW, 163 mA at 12.1 V). It's also close to the 1945 mW of the
2026-10-30 sleep on aurora10 with #274. So neither aurora14 nor the newly live
alarm and SPMI interrupt cost anything visible. This is the baseline for timed
A/B sleeps on aurora14.

The gauge's energy rose 0.78 Wh again, so the hook flagged the sleep
`no-charged`. The hook now judges charging by the coulomb counter whenever
both snapshots have one, and falls back to energy otherwise. Timed sleeps on
battery now come out `valid=yes`; lines already in the log keep their old
flag. `tests/sleep_drain_valid_test.py` runs the hook's summary on this
sleep's own snapshots.

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
  VMs don't help here, because power state is hardware. It only sees the way
  into sleep, and here it needs a macOS 14.8.x guest (see the next section).
- **SMC key dump** across macOS sleep and Linux s2idle, to compare what the SMC
  is asked to do.
- **Upstream**: report issues and patches to omacom/linux-aurora (where aurora
  development moved on 2026-10-18), with these numbers. The Asahi Linux team
  owns the general s2idle story.

## Deep sleep: what's known (desk research, 2026-11-02)

This section was collected from public sources, and none of it has been
checked on this Mac yet. Some of DeepIdle's material is AI-assisted reverse
engineering that was later partly corrected (DeepIdle#5). "Inferred" marks
our own reading.

**What macOS does.**
- XNU's deep-sleep path powers the cores off, with WFI retention disabled
  ([machine_routines_asm.s](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/machine_routines_asm.s)).
- On wake, a PMGR "reconfig engine" script restarts the CPU at the kernel's
  reset vector, without iBoot
  ([cpu.c](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/cpu.c)).
  iBoot locks the boot CPU's RVBAR
  ([Asahi](https://github.com/AsahiLinux/docs/blob/main/docs/fw/macho-boot-protocol.md)).
- On the M2 Pro, a hypervisor trace showed this way into sleep
  ([DeepIdle](https://github.com/JJRPF/Aurora-DeepIdle/blob/main/docs/hv-boot-trace-reference.md)):
  1. the SMC key `MBSE` set to `slpw`;
  2. a PMP handshake that turns the display domains off;
  3. `MBSE` set to `slpa`;
  4. `SetAPPower 0x201` sent to the SMC and the PMP;
  5. CPU-start register writes, then every core in deep WFI.

  The wake wasn't visible. `MBSW` reportedly holds the wake reason, and the
  AOP and SEP stay powered (DeepIdle#5, from the ADT's `aot-power`). On
  T6000, macOS does tell both before sleeping: the AOP gets SetAPPower 0x201
  before `slpa`, and the SEP gets a sleep message. At sleep entry the SEP's
  power state is auto-gated, with actual 0 (maralcbr's trace; see A2 below).
- Upstream's macsmc-reboot names the `MBSE` values `slpw` and `susp` in a
  comment, but sends only `rest` or `offw`, then `off1` or `phra`; `slpa`
  isn't named
  ([macsmc-reboot.c L73-168](https://github.com/omacom/linux-aurora/blob/396f2d63edcfd3d1939e7cab857dffbc388043a9/drivers/power/reset/macsmc-reboot.c#L73-L168)).
  On an M4 Max, `susp` powered the machine off, and the "wake" was a cold
  boot ([aurora-silicon#791](https://github.com/aurora-silicon/linux/discussions/791)).
- macOS sleeps at 0.24 W on the M2 Pro (DeepIdle#3), against about 0.1 W on
  this J314s (measured here; #290).

**Where Linux is.** Nobody has powered the SoC down yet.
- Sven Peter's PSCI-via-EFI RFC
  ([LWN](https://lwn.net/Articles/1081810/)) puts a firmware call interface
  in m1n1. It has no CPU_OFF and no SYSTEM_SUSPEND, covers T8103 only, and
  the name reportedly drew objections on the list. A2 couldn't confirm that
  last part: the list archive refused the fetch.
- DeepIdle ported it to their kernel and U-Boot. A SYSTEM_SUSPEND stub there
  ran 20 of 20 cycles, but with the SoC still on and one CPU running.
- **Powering a core off and restarting it at RVBAR fails** on the M2 Pro
  ([worklog](https://github.com/JJRPF/Aurora-DeepIdle/blob/main/docs/worklog.md)).
  An earlier J314s attempt
  ([linux-aurora#170](https://github.com/omacom/linux-aurora/pull/170)) never
  got as far: CPU1 was never seen to reach the stopped state (corrected in
  A2 below).
  On the M2 Pro:
  - the PMGR start strobes never restart a stopped core;
  - a core in deep WFI without retention resumes after the WFI, not at RVBAR;
  - the cluster-wide override killed the SoC;
  - hotplug tests corrupted a filesystem.
- **Other DeepIdle results:**
  - `slpa` sent from s2idle lost the NVMe until a full power-off;
  - the PMP acknowledges IDLE, but setting RUN never restarts it;
  - resetting `ps_pmp` crashed the SoC.
- The DCP driver doesn't support a full shutdown yet.
- There is no code anywhere for the last PMGR steps (clock and PLL
  quiesce, and an "S2R timer").

**T6000 against T6020.**
- XNU gives H13 and H14 the same sleep flags, so the CPU side likely carries
  over (inferred).
- The CPU-start block is at PMGR + 0x54000 on T6000 and + 0x28000 on T6020
  ([smp.md](https://github.com/AsahiLinux/docs/blob/main/docs/hw/cpu/smp.md)).
- T6000's S2R timer address and its `MBSE` values were unknown. A2 below has
  candidates for both, from a J314s trace.
- The PMU is the same Maverick part (above).

**The hypervisor only sees the way in.**
- m1n1 drops the guest's CYC_OVRD write that turns off WFI return: it logs
  `Skip: msr` and, with bit 0 set, takes that CPU out of the guest
  ([hv_exc.c L353-367](https://github.com/AsahiLinux/m1n1/blob/70757dc74c2dac485188acaa2fa7e73be5b108ee/src/hv/hv_exc.c#L353-L367),
  [hv/__init__.py](https://github.com/AsahiLinux/m1n1/blob/70757dc74c2dac485188acaa2fa7e73be5b108ee/proxyclient/m1n1/hv/__init__.py#L700-L780)).
  It panics (`HV: Failed to rendezvous, missing CPUs`) once a core it
  thinks is in the guest really powers off.
- On the M2 Pro, every sleep under it ended the run. Runs 8, 9 and 11 ended
  in that panic, and the Mac rebooted by itself within 40-96 s. Run 5 ended
  in a silent hang that needed the power key
  ([worklog L967-983](https://github.com/JJRPF/Aurora-DeepIdle/blob/24dd39e77887466bd51b9e903268087db476f6cc/docs/worklog.md#L967-L983)).
- This Mac's macOS 26.6.2 uses SPTM and TXM (`sptm` and `trxm` boot objects
  in `/boot/macos-session/iodevicetree.txt`). m1n1 runs SPTM guests only on
  M2 and later, so the guest here has to be macOS 13.5 or 14.8.x, on its own
  volume
  ([hypervisor guide](https://asahilinux.org/docs/sw/m1n1-hypervisor/)).

**Others on this.**
- maralcbr/omarchy-mx-mac#281 is an M1 Pro sleep effort. It recovers hung
  sleeps by hard-resetting through the DFU port with macvdmtool, from a
  second Apple Silicon Mac. It traced macOS going to sleep on a J314s; see
  A2 below.
- PLAN.md has what we do with all this.

## Deep sleep: T6000's keys and registers (A2, 2026-11-04)

The read-only groundwork for the trace (PLAN.md, A2). It draws on three
sources:
- the reads below, taken on this Mac with `sleep-peek`
  (`results/sleep-peek-20261104T120619/`);
- macOS's device tree, saved on this Mac;
- the two outside efforts, read in full and fact-checked against their
  sources: maralcbr/omarchy-mx-mac#281 with its kernel fork, and DeepIdle.

The biggest find is that maralcbr has already traced macOS going to sleep on
a J314s.

### maralcbr's J314s trace (#281)

[#281](https://github.com/maralcbr/omarchy-mx-mac/issues/281) is an issue,
opened 2026-10-09, with no update after 2026-10-10. The work went on in the
lab branches of maralcbr/linux, a fork of omacom/linux-aurora, until
2026-10-14, then in maralcbr's draft linux-aurora#170 (a CPU1 power-down
diagnostic) on 2026-10-21. maralcbr traced macOS 15.6.1 going to sleep on a J314s under the
m1n1 hypervisor, then replayed the sequence from s2idle, one way, to measure
the floor
([macsmc-reboot.c L208](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L208)).
The trace itself isn't published, and neither are the replay's results. What
survives is the replay's tables, which is most of what A4 was meant to find:
- **SMC keys, in order:** `MBSE`=`slpw`, `pmVC`=0x100, `WKTP`=2, then
  `MBSE`=`slpa`
  ([L844-876](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L844-L876)).
  This Mac's ADT ties `pmVC` to pmgr's `toggle_vdd_cio` function, an SMC
  write (the CIO rail is the USB4/Thunderbolt one, inferred).
- **The PMP region:** 0x28e3d8328 (pmgr reg[41] + 0x18328) gets 4 before
  `slpw`. After `WKTP`, 0x28e3d0860 gets 1 << 61, then 0x28e3d8328 gets 1
  ([L231-232](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L231-L232),
  order at
  [L839-854](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L839-L854)).
- **Between `slpw` and `slpa`, the internal PCIe ports go down:** PERST, then
  refclk and appclk off
  ([4ede5eba](https://github.com/maralcbr/linux/commit/4ede5ebaa8296934587945dfbc9f74343881ac0e)).
- **The coprocessors:**
  - before `slpa`, the AOP gets 0x00a4000000000000 on endpoints 0x2b, 0x29,
    0x2a and 0x23, then SetAPPower 0x201
    ([rtkit.c L1173-1203](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/soc/apple/rtkit.c#L1173-L1203));
  - the SEP gets a sleep message on control endpoint 0: type 0x0c, param 2.
    That much was traced. The replay sends it after `slpw`, but the sources
    don't say where macOS sends it
    ([fb148251](https://github.com/maralcbr/linux/commit/fb148251cfa156b62c54e44f929ba35623996b02));
  - the replay also shuts the ANS (NVMe) down before `slpw`, but nothing
    says the trace put it there (likely maralcbr's choice, inferred).
- **After `slpa`, 74 MMIO writes in a fixed order**
  ([L235-311](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L235-L311)):
  - the PCIe root complex;
  - watchdog WD0's control register, to 0;
  - error-handler blocks;
  - each CPU cluster's DVFS (cpufreq) and CPM blocks;
  - three words in pmgr reg[2] (+ 0x58000, + 0x60000, + 0x64000), to 0;
  - the bridge at pmgr reg[56]: 0x58c008014 = 1, then 0x58c0082bc = 1, 2,
    4;
  - pmgr-misc's dcs-ps (0x557, then 0x775);
  - AFR's power state, to 0, and SIO_CPU's (0x28e580188), to 0x400024f;
  - 0x28e224000 (pmgr reg[46]): 0x219, 0x21b, 0x8000021a, then 0x21a
    three times;
  - last of all, **0x2925c4000 (pmgr reg[36] + 0x4000) = 1**. T6020's S2R
    timer is MPMGR_AON + 0x4000, started with a 1 (DeepIdle's static
    analysis). T6000's reg[36] matches that block's size (0x14000) and
    place: two indices lower, just before the same 0x28e210000 block. So
    reg[36] is probably T6000's MPMGR_AON, and this write T6000's S2R timer
    (both inferred).
- **The AP wake masks:** 0x292004008-0x292004020, seven words in pmgr reg[7],
  where a set bit masks the source. macOS's values are 0xf3ffafff 0xffa1f6ff
  0xfbdfffff 0x3fffffff 0xffffffc0 0xffffffff 0x00001fff
  ([L699-702](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L699-L702)).
  Below source 0xcd, the bits they leave clear are exactly the 21
  `ap-wake-sources` of this Mac's ADT (bit = source id). Word 6 also leaves
  0xcd-0xdf clear, ids the ADT names no source for.
- **The power states macOS has off at sleep entry:** 31 of them that Linux
  leaves on, with macOS's values
  ([L567-600](https://github.com/maralcbr/linux/blob/37be2668e36f5e95bfb7132231b7700e3c0fa568/drivers/power/reset/macsmc-reboot.c#L567-L600)).
  They are compared with this Mac's below.
- **The last CPU** writes CYC_OVRD = 0x3000001: WFI mode 3, retention off.

More from maralcbr's sources:
- **Power was measured at the USB-C input.** A second Mac (an M4 Pro)
  powered the J314s over the DFU-port cable and read what it delivered,
  because the gauge is useless across a hard reset. With that cable's
  Thunderbolt link torn down, s2idle drew about 2.7 W at 5 V. The live link
  cost another 1.35 W.
- **Recovery:**
  - `macvdmtool reboot` from the second Mac hard-resets a suspended J314s
    (#281 gives no timing). maclab times the same reset at 36-37 s, but on
    an M1 MacBook Air driven from an M3 Max
    ([maclab](https://github.com/chriskearney/maclab));
  - in one CPU power-down trial (#170, 2026-10-21) the Mac stayed
    unreachable after one bounded cabled reset, and physical recovery was
    requested
    ([linux-aurora#170](https://github.com/omacom/linux-aurora/pull/170)).
- **#281's own PMU alarm** fired but never reached Linux in s2idle, and was
  parked. `rtc-alarm-test` here has that working, which is worth offering in
  A6.
- **The guest was macOS 15.6.1.** Asahi's hypervisor guide lists only 13.5
  and 14.8.3 for M1. If 15.6.1 works on T6000, A3 doesn't depend on finding
  a 14.8.x installer.

### What macOS's device tree says

`pmgr-map adt` decodes the pmgr node of the IODeviceTree saved from macOS
26.6.2 (`/boot/macos-session/iodevicetree.txt`), with the struct layouts of
m1n1's `proxyclient/m1n1/adt.py`, and checks it against the live device tree.
The rest of this subsection was read directly from the same file: pmgr's
`events` and `pwrgate-regs`, the CPU, PMU and SMC nodes, and the sleep
flags.

**Power states.** The pmgr node lists 355 devices, and 187 have a
power-state register. Linux's device tree describes 193 power states, and
174 of them sit at the same address under the same name, which shows the
decode is right.
- **13 are only in the ADT:** the cores (ECPU0_0-1, PCPU0_0-3, PCPU1_0-3)
  and the three cluster power managers (ECPM, PCPM0, PCPM1), at PMGR + 0x0 to
  0x70. This 8-core part has PCPU0_3 and PCPU1_3 fused off. `cpus` lists 2
  E-cores and 6 P-cores out of `max_cpus` 10, yet the fused-off cores'
  registers are still listed.
- **19 are only in Linux's tree:**
  - the ANE and ISP sub-domains, which their own ADT nodes describe instead;
  - afnc3_ioa/ls/lw0 and gp_usb_aon. This ADT marks those `no_ps` and gives
    them no register (their slots are clear in the ps-regs masks), so
    macOS's PMGR driver doesn't manage them. The registers exist anyway:
    Linux binds all four, the afnc3 ones read 0x1000030f (target f, auto-PM,
    actual 0), and gp_usb_aon reads 0x300 (off).
- **The PMGR's sleep events:** among pmgr's 188 `events` are SLP_DDR,
  AWAKE, SLP_S2R, DEEP_WAIT, SYS_AWAKE, SYS_S2R_AOP and SYS_SLP_DDR, plus two
  wake times, DDR_WK_TIME and S2R_WK_TIME, the same nine as T6020's.
  DeepIdle reads them as the reconfig engine's system states.
- **Other pmgr properties:**
  - `soc-clusters`: EACC, PACC0, PACC1, ANE0, AFR, AVEMSR, DISPLAY;
  - `soc-cluster-power-gating` 0x2e0, `cpu-power-gate-latency-us` 50000,
    `wake-time-events` 1;
  - `pwrgate-regs`: five entries whose offsets lie outside their register
    windows. Their meaning is unknown and they weren't read.

**The CPUs.** Each core's node has `function-enable_core`: pmgr's `Core`
function with the core's bit (cpu0 0x1 to cpu8 0x100; 0x20 and 0x200, the
fused-off cores, are absent). It also has `function-cpu_idle`, pmgr's
`cpuI`. Each core's `cpu-impl-reg` (0x210050000, 0x210150000, 0x211050000,
and so on) holds RVBAR at + 0x0. Each core's node also carries its cluster's
`cpm-impl-reg` (0x210e40000, 0x211e40000, 0x212e40000) and `acc-impl-reg`
(0x210f00000, 0x211f00000, 0x212f00000).

**What can wake the AP.** pmgr's `ap-wake-sources` lists 21:
- the AOP: AOP.GPIO.IRQ4 (0x0c), AOP.GPIO.IRQ6 (0x0e), AOP.OutboxNotEmpty
  (0x1a), AOP.CPUWakeupAP (0x1b);
- the NUB: NUB.WakeupAP (0x28), NUB.GPIO.IRQ2 (0x2b), NUB.APWatchdog (0x31),
  NUB.APWakeupTime (0x32), NUB.SPMISw0IRQ (0x33), NUB.SPMISw1IRQ (0x34),
  NUB.SPMISw3IRQ (0x36);
- the SMC: SMC.CPUWakeupAP (0x55), SMC.OutboxNotEmpty (0x5a);
- the four Type-C PHY blocks: ATC0-3.CIOWakeup (0x7e-0x81),
  ATC0-3.USBWakeup (0x82-0x85). ATC0-2 are this Mac's three USB-C ports.
  ATC3 has a PHY but no USB controller here (it carries the HDMI port's
  DisplayPort, inferred).

None is named for the keyboard or the trackpad, so in a real sleep a key
press would presumably reach the AP through the SMC's trackpadkeyboard event
(bit 12, below) and SMC.OutboxNotEmpty (inferred). That matters for Track C
once the SoC really sleeps. macOS's own wake reasons on this Mac use these
names (pmset log, 2026-10-30):
- RTC wakes come in as `NUB.SPMISw3IRQ nub-spmi0.0x02 rtc`;
- lid and Wi-Fi wakes come in as `smc.sysState.Wake(0x70070000) lid` (or
  `wifibt`) `SMC.OutboxNotEmpty`.

macOS calls the state Deep Idle ("DarkWake from Deep Idle [CDNP]").

**The SMC and the PMU.** The PMU's node (`spmi-mpmu@F`) has
`function-external_standby` = smc-pmu's `keyW` on `MBSE`: the PMU's external
standby is a write of the SMC key `MBSE`. It also has `info-has_slpsmc` 1 and
`function-rtc_read_helper` = `keyR` on `CLKM`. Unlike T6020's, it has no
`function-suspend_helper` (PMGR `S2RT`, the S2R timer start). The SMC's
`smc-pmu` node names its event bits: pwrbtn 0, lid 8, charger 9, codec 10,
wifibt 11, trackpadkeyboard 12, sdconn 14, acattach 40, acdetach 41,
USB-C_plug 42, USB2_plug 43, USB2_wake 44, aupo 45, rtc 48.

**What keeps state through sleep.**
- `retention`: dart-aop, dart-sep, dart-dcp, dart-dcpext0 and dart-dcpext1.
- `losesPowerInSleep`: clvr-spmi0-2.
- `shutdown-sleep`: the NVMe (iop-ans-nub) and GPU (iop-gfx-nub0)
  coprocessors.
- `no-hibernate-sleep`: the SMC's and the NVMe's nubs.
- `reconfig-wdog-support` on the watchdog, and `reconfig-firmware` on the
  three ACIO coprocessors (the reconfig engine, inferred).
- `manual-enable-s2r`: both internal PCIe bridges, as on T6020.
- `PowerOffAtSleep`: the audio codec output.

### The SMC on this Mac

`smc-peek` walked the SMC's 2052 keys three times: awake, again 60 s later,
and right after a 90 s s2idle sleep on the alarm. Each walk read 1634 values
with no errors. The whole run was on the charger, charging at 98% (the hook
logged `ac=1`, `valid=no-short`), so what follows is the SMC on the charger;
on battery it hasn't been checked. 389 keys are function-flagged (0x10).
Those weren't read, except the three named:

| key | type, size | flags | value, all three times |
|---|---|---|---|
| `MBSE` | hex_, 4 | 0xd4 | `30000000` (read with `read_funcs`) |
| `MBSW` | hex_, 8 | 0xd4 | 0 (read with `read_funcs`) |
| `WKTP` | ui8, 1 | 0xd0 | 0 (read with `read_funcs`) |
| `MBSe`, `MBSw` | hex_, 8 | 0xd4 | never read: `MBSe` is the power button's read-to-clear flag ([smc.md](https://github.com/AsahiLinux/docs/blob/main/docs/hw/soc/smc.md)) |
| `pmVC` | ui32, 4 | 0xf0 | not read (the key macOS writes 0x100 to) |
| `PMVC` | flt, 4 | 0x85 | a power reading (3.5-4.0) |
| `CLSP` | ui64, 8 | 0x84 | 0 |
| `CLWK` | ui64, 8 | 0x84 | 8892425 |
| `NTAP` | flag, 1 | 0xc0 | 1 |

- **The keys and their flags match T6020's.** `MBSE`, `MBSW`, `MBSe`,
  `MBSw`, `WKTP` and `pmVC` have the same types, sizes and flags as in
  DeepIdle's T6020 key list. `pmVC` (ui32, 0xf0) and `PMVC` (flt, 0x85) are
  different keys, both in that list, and the trace's key is `pmVC`. Awake,
  macOS on T6020 read `MBSE` as ff000000; under Linux here it reads
  `30000000`.
- **Across s2idle the SMC's sleep keys don't move.** `MBSE`, `MBSW` and
  `WKTP` stayed put, and `MBSW` holds no wake reason after the alarm wake.
  `CLSP` (possibly "the time the SMC last went to sleep") is 0, and `CLWK`
  ("last woke up") didn't move either. So by these two keys, whose meanings
  are only guessed, the SMC didn't sleep during s2idle (inferred). That fits
  Linux: nothing sends the SMC anything on the way into s2idle. Among macsmc
  and its sub-drivers, only macsmc-input has PM callbacks (`.prepare` and
  `.complete`), and they only turn its lid and power-button events into
  wakeups.
- **What did change across the sleep** (beyond the keys that also change
  between two awake reads): temperatures, currents, voltages and rail
  powers, and four keys in two of the SMC's four power-port records (`D1`
  to `D4`: the three USB-C ports and MagSafe, inferred). On port 3, the
  charger's (`D3DE` reads "pd charger"), `D3LI` went from 92 to 96 and a
  counter in `D3IQ` from 100 to 102. On port 4, `D4LI` went from 0 to 2 and
  `D4LG` got its first entry.
  They look like event logs (inferred); whether s2idle or the charger caused
  them is unknown.
- **The SMC keeps sampling power through s2idle.** `zSPa`, the power the
  adapter supplies accumulated once per sample in mW, and `zSPc`, its sample
  count, exist here too. Between the awake reads `zSPa` averaged 16.5 W, near
  `PDTR` and well above `PSTR`. `zSPc` went up 90 between the walks before
  and after the sleep, about 97 s apart with 90 s of it asleep. That's the
  awake rate (57 in about 61 s), so it kept counting through s2idle, as
  DeepIdle found on T6020. With the battery held, they would give
  s2idle's power at the charger.

### PMGR power states on this Mac

`pmgr-peek` read all 206 power-state registers awake twice and after the
sleep (`pmgr-map show` decodes them).
- **The cores' power states have target 0.** The P-cores' actual state
  flips between on (f) and off (0) from one read to the next (5 of the 6
  were seen off); the two E-cores read on all three times. PCPM1, the second
  P-cluster's manager, read off once. So under Linux too, the PMGR gates idle
  cores and whole clusters (the `CPU PD` idle state).
- **After the sleep**, the sticky `was_clkgated` and `was_pwrgated` bits are
  newly set on the domains genpd switched off for it. The domains that
  switched off match Finding 3. AFNC2_LW1, which is always-on, has both
  bits newly set too (0x1f0000ff, then 0x1f0003ff): hardware auto-PM gated
  it at some point in the sleep, without genpd.
- **macOS at sleep entry against Linux.** Of the 31 states macOS has off at
  sleep entry (maralcbr's table), 19 are `apple,always-on` in Linux's tree:
  - 16 of those sit at target f and are on: the ATC ports' `*_common`,
    `*_pcie` and `*_usb_aon` blocks, PMP, PMS_SRAM, MSG, `apcie_gp_sys`,
    AFNC1_LW2 and AFNC2_LW1 (on in every read, but auto-gated during the
    sleep);
  - the other three already match macOS's state, through hardware auto-PM:
    SEP (auto, actual 0), NUB_SPMI1 (clock-gated) and AFNC3_LW0.

  Of the other 12:
  - dispext1's FE and CPU go off in s2idle (Finding 3);
  - disp0, dispext0 and `pcie0_ref` are held on by always-on child domains
    in Linux's tree: pmp-disp0 and pmp-dispext0 (under the DCPs' cpu0
    domains), and `apcie_gp_sys` (through `apcie_gp`). The DCP driver's
    suspend also keeps the two DCPs' domains on;
  - UART2, 3, 4, 6 and 7 (there is no UART5) sit clock-gated (target 4),
    where macOS has them off.

  ATC0's common and usb_aon blocks aren't in macOS's list (its pcie block
  is). That's probably because m1n1's hypervisor forces on the USB port it
  talks over: ATCn_USB, ATCn_USB_AON and all their parents (inferred).
  Neither are the DRAM controllers (dcs_*, amcc*).
- **PMP and PMS_SRAM.** macOS has PMP (0x0f0f0300) and PMS_SRAM (0x0f000300)
  at target 0, actual 0, with auto-PM off. PMP's PS_MIN is f in macOS's
  value too, so PS_MIN isn't what keeps it on. Linux keeps PMP at target f
  with auto-PM on (0x1f0f03ff, the value DeepIdle read on T6020). DeepIdle
  crashed a T6020 by resetting `ps_pmp`, so never touch 0x28e0802d8.

### The CPU-start block and macOS's sleep registers, read under Linux

All of these were read last, after everything else was saved, and none
faulted.
- **CPU-start + 0x4 reads 0x880**, and + 0x8, + 0xc and + 0x10 read 0.
  Bits 7 and 11 are 4·cluster+core for core 3 of each P-cluster: the two
  fused-off cores. m1n1 only ever writes this block, and we found no
  published read of it on T6000. On an M4 Max, +0x0's stop bit reads back
  latched after a core powers off
  ([aura#33](https://github.com/aurora-silicon/aura/pull/33#issuecomment-6558169697)).
  Here, +0x4 looks like a per-core flag that is set for missing cores
  (inferred).
- **The AP wake masks** read 0 in every word except word 1, 0x40 (source
  38). So nearly every wake source is unmasked under Linux, where macOS
  masks everything but its 21 named sources and the unnamed ids
  0xcd-0xdf. It doesn't matter in s2idle, where the AIC wakes the cores and
  the SoC never sleeps (inferred).
- **0x28e224000 reads 0x239.** macOS steps it through 0x219, 0x21b,
  0x8000021a and 0x21a just before its last write.
- **0x2925c4000 already reads 1**, the value macOS writes there last.
  DeepIdle says the same of T6020's S2R-timer word under Linux (unsourced).
  So the 1 that macOS writes is probably a start or a re-arm, not a state
  change visible on read (inferred).

### Corrections

- **linux-aurora#170 (J314s) never saw CPU1 stop.** This file said #170
  failed as the M2 Pro attempts did. One run aborted with -EBUSY, one reset
  after the stop write, and the other two hit the ~50 ms OFF timeout. In
  the last of them, the core's first WFI returned while an interrupt was
  pending. No run reached a restart, so whether a PMGR-stopped core can be
  restarted on T6000 is untested.
- **`MBSE` and `MBSe` are different keys.** `MBSE` is the sleep-state key.
  `MBSe` is the power button's read-to-clear flag: never read it. That's
  for anyone surveying "MBSE and its neighbours", as A2 set out to.
- **The cpuidle states do have s2idle hooks.** Both of `cpuidle-apple`'s
  states set `.enter_s2idle`. CPU PD's `s2idle/usage` reads 542-544 per CPU
  after this boot's alarm sleep. WFI's always reads 0, because s2idle uses
  only the deepest state.

### What the trace has to answer

A4's job changes: maralcbr's trace exists, so first ask for it (A6), then
fill the gaps on 14.8.x. In order:
1. **Order on T6000 (trace_smc and trace_pmp).**
   - Does maralcbr's order (`slpw`, `pmVC`, `WKTP`, `slpa`) hold on 14.8.x?
     On T6020, 15.7.1 writes `WKTP` first.
   - Where do SetAPPower 0x201 to the SMC and the PMP, and PMP SetIOPPower,
     fall relative to `slpa`? DeepIdle's three T6020 sources disagree on
     this.
   - Record the raw bytes of each `MBSE` write, which settles the byte
     order. Is any other `MBSE` value written (`hiba`, `susp`, `waka`)? Is
     `MBSE` read back after `slpa`?
   - Does the SMC send notifications during sleep entry?
2. **The PMP (trace_pmp, run UNBUF).**
   - What is the TGT_WR sequence at 0x28e3d07c0, decoded with T6000's bits
     (ECPM 0x40, PCPM0 0x80, PCPM1 0x100, GFX 0x200, DISP0_FE 0x1000,
     DISPEXT0_FE 0x2000, DISPEXT1_FE 0x4000)? Linux on J314s requests only
     0x7000. On T6020, macOS keeps PCPM0, PCPM1 and GFX requested while
     awake (DeepIdle's trace).
   - What do 0x28e3d8328 (4, then 1) and 0x28e3d0860 (1 << 61) hold before
     these writes, and are they polled after them?
3. **The power states (trace_pmgr).**
   - Which states does macOS turn off, in what order, and through which
     register? For the always-on ones in particular: the ATC blocks, PMP,
     the fabric and `apcie_gp_sys`.
   - The power states the hypervisor protects (UART0, UART_P and SIO, plus
     the proxy port's ATC blocks and their parents) need decoding. It logs
     "Dangerous write" on every guest write to them, keeps them on, and shows
     the guest its own target as the actual state
     ([map_essential](https://github.com/AsahiLinux/m1n1/blob/70757dc74c2dac485188acaa2fa7e73be5b108ee/proxyclient/m1n1/hv/__init__.py#L1532-L1544)).
     Only a write whose target nibble is 0 (`data & 0xf`) is a power-off the
     hypervisor blocked; the rest are ordinary updates.
   - trace_pmgr reads the power-state banks from the ADT, but it hardcodes
     T8103 addresses (its ignored ranges and eight hooked lanes at
     0x200200000) and traces only reg[0] and reg[1] in full. Replace those
     addresses, and add reg[7], reg[36] and reg[46].
4. **The wake setup (trace_pmgr).** When are the AP wake masks written: at
   boot or at sleep entry? Is anything armed on NUB.APWakeupTime, or written
   to `S2R_WK_TIME` or `DDR_WK_TIME`?
5. **The S2R timer and the last writes (trace_pmgr).**
   - Is 0x2925c4000 read or polled before macOS writes 1?
   - What do 0x28e224000's values (0x219 / 0x21b / 0x8000021a / 0x21a)
     mean, and is there a poll between them? maralcbr's replay has none.
     Linux leaves it at 0x239, with bit 5 set where every macOS value has
     it clear.
   - Does macOS clear watchdogs WD1 and WD2 as well as WD0?
   - How long is it from `slpw` to `slpa`, and from `slpa` to the last
     write?
6. **The PMU (trace_spmi).**
   - What does macOS send the PMU over nub-spmi0 on the way in:
     `pm_setting` (0x1405), the alarm, any SPMI sleep command? Is there SPMI
     traffic after `slpa`?
   - The cluster PMUs (clvr-spmi0-2, which "lose power in sleep") are worth
     tracing too.
   - trace_spmi as written fails on T6000 (it looks up
     `/arm-io/nub-spmi-a0` first) and needs a fix.
7. **The coprocessors (trace_aop, trace_nvme and trace_dcp, beyond A4's four
   tracers).** Where do the NVMe shutdown, the DCPs' power-off and the AOP
   and SEP messages fall relative to `slpw` and `slpa`? On T6020, macOS
   15.7.1 shuts the ANS down before `slpa`, and `slpa` sent from s2idle lost
   the NVMe.
8. **The CPUs (hypervisor log).**
   - What do CYC_OVRD and ACC_OVRD get on each core ("Skip: msr" lines)?
     Which core goes last?
   - What does XNU write to the CPU-start block (+0x0 stop, +0x4), and with
     which bit numbering?
   - Can the hypervisor keep the boot CPU alive past SetAPPower, long enough
     to see PMGR's quiesce and the S2R-timer write in order? Does XNU print
     "Starting S2R timer"?
   - How does the run end: a rendezvous panic that reboots by itself, or a
     hang?
9. **The wake (not traceable under the hypervisor).** After a macOS sleep,
   what do `MBSE`, `MBSW`, `CLSP` and `CLWK` read? These are boot-time reads
   in the guest's own trace.

Still unknown after A2, and not answerable by this trace:
- **The wake itself.** Nobody has seen an S2R wake under Linux on any Apple
  Silicon. Nobody knows whether iBoot sets up the reconfig engine's wake
  script, or the boot CPU's RVBAR, for a raw m1n1 boot object. This Mac's
  RVBARs (cpu-impl-reg + 0x0) haven't been read; that's one more read-only
  check.
- **Restarting the DCPs and the PMP after their domains go off.** Linux has
  no working path for either (`dcp_sleep` is unused; on T6020 the PMP never
  answered after IDLE).
- **Restarting a PMGR-stopped core on T6000.** It's untested here, and T6000
  lacks XNU's Rhodes cluster workaround, so T6020's failure may not carry
  over.

## DeepIdle's T6020 results, to 2026-11-05

JJRPF answered DeepIdle#9 on 2026-11-05
([comment](https://github.com/JJRPF/Aurora-DeepIdle/issues/9)), and the
repo's [`docs/status.md`](https://github.com/JJRPF/Aurora-DeepIdle/blob/main/docs/status.md)
was updated the same day. All of this is on T6020 (M2 Pro, J414s), not
checked here.
- **License.** The patches are GPL-2.0 and the DTS changes `GPL-2.0+ OR MIT`,
  per their SPDX lines; the README's License section says so.
- **`slpa` doesn't power the SoC down.** In their first `slpa` inside a real
  suspend (2026-11-04, all CPUs parked), the kernel ran on, and every NVMe
  read and write failed (btrfs `-5`, root read-only). `waka` doesn't bring
  the storage back (`IOP power acknowledgment failed: -62`, `ANS did not
  boot`); only a full power-off does. No watchdog fired, and the owner
  reset by hand after about 15 minutes. That run was a suspend to RAM through
  their firmware's PSCI, not s2idle. `WKTP` and `slpw` did no harm over three
  s2idle cycles each (power not measured).
- **The closest precedent for E1's keys** is their s2idle run of 2026-10-22:
  `WKTP`, `slpw` and `slpa` from the macsmc-reboot device's own PM callbacks,
  `slpa` in `suspend_late`. The Mac resumed, then NVMe I/O failed and btrfs
  went read-only. `pmVC`, which maralcbr also writes, is in none of their
  runs.
- **Parked CPUs save almost nothing.** Suspend with every CPU in deep WFI,
  through PSCI SYSTEM_SUSPEND in their own firmware: 1.365 W over 30
  minutes, against 1.390 and 1.413 W for s2idle in the same boot. macOS
  sleeps the same machine at 0.24 W. So the gap is the SoC staying up.
- **Their state.** SYSTEM_SUSPEND works, 20 of 20 cycles with all 10 CPUs
  (CPU_OFF into a retention park, CPU_ON back). Their next step is what
  macOS does between `slpa` and the power-down. For J314s, maralcbr's
  post-`slpa` table above has that. Still open for them: PMGR's last steps,
  the boot CPU's wake hand-back, and DCP resume.
- **Since then, in their worklog**
  ([`docs/worklog.md`](https://github.com/JJRPF/Aurora-DeepIdle/blob/797c0a36544a745276017e2d530a8308c3050993/docs/worklog.md)
  at 797c0a3, 2026-11-05):
  - They now blame the dead ANS on its SART. By their static reading of
    macOS 13.5, macOS writes the SART back every time it starts the ANS, and
    Linux didn't. Their test kernel now saves and restores it.
  - A second `slpa` run with that kernel (07:01; suspend to RAM, `slpw` and
    `slpa`, 60 s alarm) never resumed. The Mac cold-booted about 100 s after
    entry, before any of their 120 s watchdogs, and the PMU logged a boot
    error. Either the SoC really went down and the alarm brought a cold boot
    (their H1), or the SMC resets the Mac about 100 s after `slpa` when the
    AP doesn't go down (H2). A run with a 30 s alarm is next.
  - Also from macOS 13.5, statically: macOS sets the PMU's boot stage to
    0x50 for sleep, and at quiesce arms a 10 s deadline that Apple's names
    say the SMC enforces. Linux leaves the boot stage at 0x30.
  - The `(8)` after their `MBSW` read is the 8 bytes `apple_smc_read()`
    returned, not an SMC error.

What this changes here: A5's ladder skips `slpa` alone, and its first real
rung is `slpa` with maralcbr's post-`slpa` writes and the CPUs left running
(PLAN.md, A5).

## A5's experiments

### E0: a timed sleep in the safe boot (2026-11-05)

The ladder's baseline rung (PLAN.md, A5): the 2026-11-02 aurora14 timed sleep,
repeated in a safe boot (`sleep-drain-safe`, up since 12:54). Same kernel
(7.2.11-14-1-ARCH), the `radios-off` flag, `rtc-alarm-test` loaded (alarm
RTC rtc0), `rtcwake -m no -s 900`, then `systemctl suspend`, on battery at
100% (the baseline was at 98%). Since the baseline, Omarchy went from
4.5.0rc3 to 4.5.0, the kernel unchanged.

| Run | Snapshot | Attached | Slept | `avg_mw_cc` | Valid |
|---|---|---|---|---|---|
| E0 | 20261105T125757 | a USB-C dock (`typec=port3:not_ufp`; USB 05e3:0751 hub, 0bda:8153 Realtek Ethernet, 2109:8888 VIA billboard) | 903 s | 3233 mW | yes |
| E0b | 20261105T131638 | nothing (`typec=-`, `usb=-`) | 900 s | **1978 mW** | yes |

- **E0b matches the baseline:** 1978 mW against 1974 mW (20261102T150637),
  inside the 1945-2012 mW spread of the earlier short sleeps. So neither the
  safe boot (RAM overlays, read-only disk) nor Omarchy 4.5.0's userspace
  costs anything visible. E0b is the baseline for the ladder's later rungs.
- **Both woke on the alarm:** `pm_wakeup_irq` 145, macsmc-rtc's count 0 to
  1, then 1 to 2.
- **The dock costs about 1.25 W asleep** (3233 against 1978 mW, consecutive
  sleeps in the same boot). The Mac was the source on port 3
  (`not_ufp`), so it powered the dock all through the sleep. The first run
  isn't a baseline for that reason; the session that ran it found the dock
  in the hook's `pre` line and reran with nothing attached.
- **The gauge's energy rose** again at 100% (`avg_mw` -682 and -2599 mW), as
  on 2026-11-02, so only the coulomb counter means anything here.
- **The run itself** was one `pkexec` command in the safe boot: load the
  alarm drivers, arm it, suspend, wait for the hook's `post` line, then
  `sleep-drain-safe save`. That session's notes and both journals were
  collected after the restart (`/var/log/sleep-drain/safe-saved-20261105T1313*/`
  and `safe-journal-20261105T13*.txt`); its own transcript was lost with the
  boot, as expected.

### Before E1: the hook point and the measurement (desk research, 2026-11-05)

Pullfrog's review of #21 found two problems in E1's plan: a device callback
runs the replay at a different point than maralcbr did, and the gauge's
reading after a hard reset isn't trustworthy as it stood. A research pass
answered both from the sources, each claim checked by two skeptics. Nothing
here has run on the Mac yet.

**Where the replay runs.** In
[`suspend_enter()`](https://github.com/omacom/linux-aurora/blob/4fdd0fc5c931ef9f14474942b37c3d451e97fc04/kernel/power/suspend.c#L419-L498)
at omacom/linux-aurora 4fdd0fc5, the commit aurora14's package builds,
s2idle goes:
1. `dpm_suspend_late`: every device's `suspend_late`;
2. `s2idle_ops->prepare`;
3. `dpm_suspend_noirq`: `suspend_device_irqs()`, then every device's
   `suspend_noirq`, async ones included, with genpd switching each domain off
   once its last device has suspended;
4. `s2idle_ops->prepare_late`;
5. `s2idle_loop()`.

maralcbr's replay runs at step 4 (`lab_s2r_prepare_late`), or at step 2 with
their floor's bit 4.
- **A device callback can't stand in for step 4.** Each phase walks the
  device list from its tail, and a device a module registers is added at the
  tail. So a module's `suspend_noirq` is the first synchronous callback of
  step 3, before the other devices' noirq callbacks and genpd's power-offs;
  async devices (PCI, USB, i2c, HID) may run before it or alongside it.
  `suspend_late` is earlier still. From there, `slpa` and the PMGR writes
  would come before the rest of the noirq pass. The same goes for
  `pmp-sleep-release`'s device: its noirq callback runs first in the pass,
  not last.
- **`s2idle_set_ops()` isn't exported**: it's missing from the running
  kernel's `Module.symvers`. Nothing in this kernel registers s2idle ops: the
  only callers are ACPI's, and `CONFIG_ACPI` is off. maralcbr's call links
  only with macsmc-reboot built into the kernel; here it's a module.
- **A module can still reach step 4** by looking up `s2idle_set_ops`'s
  address with `register_kprobe()` and calling it. KASLR, module signing,
  lockdown and CFI are all off here. It replaces nothing. It takes
  `system_transition_mutex`, which a suspend holds throughout, so it can't
  race one. On unload, the module sets the ops back to NULL. This sidesteps
  the export rules, so it's for this lab only. The other way is a kernel that
  exports the function (`test-kernel`), but the safe boot only boots the
  regular kernel today.
- **Ruled out:**
  - syscore ops: never called on the s2idle path;
  - `device_pm_move_to_tail`: not exported;
  - a kprobe on the s2idle path: those functions are inlined, and a kprobe
    handler can't sleep, while an SMC write does;
  - the `suspend_resume` tracepoint at the end of `dpm_suspend_noirq`: the
    right moment, but a probe shouldn't sleep;
  - CPU PM notifiers: atomic, and called on every idle entry;
  - PM notifiers: they run before tasks freeze;
  - `suspend_set_ops()`: that's deep sleep. It would make `mem` mean deep,
    and by the source, taking the CPUs offline fails here.

  `device_move()` under an early device, or device links, can push a
  callback toward the end of step 3, but not past the async devices, and
  links reorder other devices too.
- **The SMC at step 4.** The replay sends its keys with the ordinary
  `apple_smc_write()`, which takes a mutex and waits up to 500 ms for the
  reply. Nothing in step 3 should stop it:
  - both SMC mailbox interrupts are requested `IRQF_NO_SUSPEND`, so
    `suspend_device_irqs()` leaves them on;
  - the reply completes from the hard interrupt;
  - neither the SMC nor its mailbox has PM callbacks or a power domain.

  So by the code, SMC writes work at step 4, and in noirq too. DeepIdle kept
  `slpa` out of noirq because they expected the mailbox interrupt to be off
  there; with aurora's mailbox driver it isn't. The SMC's atomic mode is no
  way around this anyway: once it's entered (`NTAP`=0), every ordinary SMC
  command fails until a reboot.
- **A harmless check.** `/sys/power/pm_test` offers `platform`, which runs
  steps 1-4, waits, and resumes without sleeping. Build the module without
  the replay, so that at step 4 it only logs and reads one SMC key. ftrace's
  `power:suspend_resume` and `power:device_pm_callback_end` events then show
  whether it ran after the last noirq callback. Then repeat it in a real
  alarm sleep.

**Measuring a sleep that ends in a hard reset.**
- **`charge_counter` can't span the reset.** It's the SMC's `BAAC`, the
  battery current summed in mA·s, and it restarts at every boot. It starts
  near zero in every boot logged here: ordinary reboots and cold boots; a
  power-key reset isn't tested. In the next boot, then, it's that boot's own
  drain since power-on, if the boot is on battery: −280856 mA·s (78 mAh) at
  395 s of uptime in one boot today.
- **`charge_now` spans it.** It's `B0RM`, the battery gauge's estimate of
  the remaining charge. (`energy_now` is the same times 11.4 V, not a
  measured energy.) Whether that estimate comes through a reset intact isn't
  known. maralcbr saw it stale for about 25 s after one, then re-estimated by
  ±25 mAh
  ([#281](https://github.com/maralcbr/omarchy-mx-mac/issues/281#issuecomment-6093626202)).
- **A bigger error here.** Nine battery sleeps of 8.5-18 minutes had their
  pre reading taken under awake load. In those, `charge_now` showed 50-108
  mAh less drain than `charge_counter` (mean 87, sd 21). The one sleep whose
  pre reading came about a second after unplugging agreed within 1.4 mAh. So
  both ends have to be read the same way, at idle.
- **What it can resolve.** If matched idle readings remove the offset's
  mean, and the boot's drain is subtracted, the ±25 mAh step and the
  offset's spread leave about 33-46 mAh (1σ). That's ±0.1-0.15 W over 4 h
  and ±0.05-0.07 W over 8 h. A floor near 0.2 W and s2idle's 2.0 W are then
  more than 6σ apart at 2 h. Telling 0.1 W from 0.3 W takes about 8 h, about
  3σ in one run.
- **The method.**
  - Unplug at least 30 minutes before, and attach nothing.
  - A logger records `charge_now`, `voltage_now` and `charge_counter` every
    10 s, plus `BACC` and `BQCC` through smc-peek.
  - The reading is the median of the last minute of 10 idle minutes. Take it
    right before the suspend, and save it to the boot partition. Take it
    again after 10 idle minutes in the next boot, with the Mac powered on
    straight after the reset.
  - The average, in mW, is [(Q_pre − Q_post) − Q_boot − Q_tail] × mean V /
    (t_end − t_suspend), with every Q in mAh, V in volts and t in hours:
    - Q_pre and Q_post are the two readings of `charge_now`;
    - Q_boot is the next boot's drain up to the post reading,
      −`charge_counter`/3600;
    - Q_tail is the charge used between the saved reading and the suspend:
      the `charge_counter` change over that gap, /3600. A reset loses it,
      so V2 and E1 take it from V1, whose hook lines survive. A few seconds
      awake is about 1-2 mAh, but a minute at 800 mA is 13 mAh;
    - t_end is the next boot's power-on. `BACC` counts seconds since then.
- **The controls** (PLAN.md, E1):
  - V0 measures the reset's step from awake, and whether `BAAC`, `BACC` and
    `BQCC` restart;
  - V1 checks the method against the same boot's `charge_counter` after an
    alarm-ended sleep;
  - V2 is E1's own procedure on plain s2idle.
- **Compare by current as well.**

  | Run | Current | Power | Voltage |
  |---|---|---|---|
  | E0b | 156.8 mA | 1978 mW | 12.62 V |
  | aurora14's baseline | 156.6 mA | 1974 mW | 12.60 V |
  | the 16.4 h night | 157.0 mA | 1909 mW | 12.16 V |

  The night's last reading was on the charger. The 65-70 mW by which the
  short runs sit above the night is a full battery's voltage, not the awake
  seconds. Each window has only about 1.5-2.5 s outside s2idle, worth
  10-30 mW at 900 s.
- **An input-side meter is the fallback** (maralcbr's method; untested here).
  `charge_behaviour` offers `inhibit-charge` on this Mac. With the battery
  idling, an inline logging USB-C meter between a plain PD adapter and a
  USB-C-to-MagSafe cable could supply the Mac. A reset can't spoil its trace.
  But it measures input power, losses included, so it suits A/B
  comparisons, not the battery-side floor.

### E1's checks and E1a (2026-11-06)

All of these ran on battery with nothing attached, from `sleep-drain-reading`'s
idle readings (10 minutes alone, the median of the last minute) in
`/var/log/sleep-drain/reading-20261106T*`.

**V0, the reset's own step.** Three reboots (regular → safe boot) and three
power-key resets (safe → regular), alternating, 12:27-14:08.
- `charge_now` across each, after the next boot's own drain
  (−`charge_counter`/3600) and the tail before the reset: reboots −6.4,
  +3.4, +0.8 mAh; power-key resets +0.7, −4.3, −1.7 mAh. Read at idle at
  both ends, the gauge's estimate spans a reset to about ±6 mAh, far better
  than the ±25 mAh the plan allowed, and the two kinds of reset don't differ.
- **`BQCC` is a charge counter in mAh that doesn't restart.** Over each
  10-minute window it rose 67-79 while `BAAC` counted 67.9-78.6 mAh, within
  1 mAh every time; it rises on discharge (si16, so it wraps after 65.5 Ah).
  Across each restart it rose by the charge used in between (the next
  boot's own `BAAC` since power-on, plus the tail before the reset):
  leftovers +0.4-0.6 mAh for reboots and +1.4-2.9 mAh for resets, where the
  tail is estimated from the idle current. It's probably the battery gauge's
  own counter (the SMC restarts with every boot; `BQCC` doesn't).
- `BAAC` and `BACC` restart at every boot, reboot or reset. `BACC` dates
  the power-on: the kernel starts 21-23 s after it, every time.

**V2, E1's procedure on plain s2idle.** A safe-boot sleep from 15:14, lid
closed in a bag, cut by the power key at about 19:55 (4.69 h). `BQCC` rose
779 mAh; less the next boot's 25 mAh and a 2 mAh tail, the sleep used 752 mAh:
**160 mA, about 1.94 W**, against the 157 mA of the earlier sleeps (+2%).
`charge_now` gives 776 mAh, 165 mA (+5%). So a sleep that ends in a hard
reset is measured to a few percent with nothing but the Mac, and `BQCC` is
the measure from now on. V1 isn't needed.

**The hook point** (`e1-hook/check`, safe boot, 20:18): both runs passed.
Under `pm_test=platform` and in a 60 s alarm sleep, the module's
`prepare_late` ran 133-135 µs after the last of the 115 noirq device
callbacks (`simple-pm-bus soc`) and 1 µs after `dpm_suspend_noirq` ended,
right before "suspend debug: Waiting" and "PM: suspend-to-idle"
respectively. Its `B0AV` read there took 241-262 µs (11.8 V), the same as at
`prepare`. The SMC answers at step 4, as the code reading said.

**E1a: maralcbr's keys and PMP words, with the CPUs up** (`e1-hook/run-e1a`,
`replay=5`, PR #27; safe boot, lid open). At the first `prepare_late`: 4 to
0x28e3d8328, `MBSE`=`slpw`, `pmVC`=0x100, `WKTP`=2, 1 << 61 to 0x28e3d0860, 1
to 0x28e3d8328, `MBSE`=`slpa` (the pages mapped non-posted, as maralcbr's
are; the replay stops at the first failed key write). Asleep from about
21:14:30; Corey held the power key at 22:00, with the Mac dark, and the next
power-on was 22:00:48 by `BACC`.
- No self-reset in 46 minutes, unlike DeepIdle's second `slpa` run.
- `BQCC` rose 149 mAh; less the next boot's 22 mAh and a 2 mAh tail, the
  sleep used 125 mAh in 0.772 h: **about 162 mA**, the same as plain s2idle.
  `charge_now` agrees (about 170 mA).
- Whether the SMC accepted the keys wasn't known that night: the replay's
  log was in the safe boot's RAM, which the reset cleared. The next morning
  answered it.

**E1a under `pm_test=platform`** (`e1-hook/e1a-platform`, 2026-11-07 10:46,
safe boot). The same replay at `prepare_late`, but `suspend_test()` then
resumes after 5 s instead of sleeping, so the results can be read. From a
photo of the screen (the save to the boot partition failed, see below):
- **The SMC accepted every key:** `replay_ret=0,0,0,0`, with "MBSE=slpw 0",
  "pmVC=0x100 0", "WKTP=2 0", "MBSE=slpa 0" in the log. The whole sequence
  took 1.6 ms (135.159666-135.161280 s), right after `prepare_late`'s `B0AV`
  read (238 µs). So the night's E1a sent all of it too.
- **The SMC still answers after `slpa`:** the battery's `voltage_now`, read
  through it after the resume, came back (12.356 V).
- **The NVMe was lost:** `nvme0` went from `live` before to `resetting`
  after, and every read from the disk failed with "Input/output error" from
  then on: `sleep-drain-safe save` and `rtc-alarm-test/load` couldn't even
  start. A restart (through the launcher; its programs were in memory)
  brought it back. An earlier attempt that morning ended in the same
  "Input/output error", unsaved. DeepIdle lost the NVMe the same way on
  T6020; here it happens without a real sleep. macOS, and maralcbr's
  replay, shut the ANS down before `slpw`.
- Everything else resumed: the display, the terminal, the SMC. The Mac sat
  normally for 5 minutes afterwards, with no reset and no freeze.
- (An accidental run of `run-e1a` that morning, 09:51, put the Mac to sleep
  with the same replay for about 3 minutes; it ended in a restart with no
  reading after it, so it has no drain figure.)

So the keys work, and the SMC acts on them (the NVMe goes), but with the
CPUs running the rest of the SoC doesn't sleep and nothing is saved. The
saving has to come from the steps after `slpa`: maralcbr's 74 post-`slpa`
writes and the CPU power-down. That's the next run, the floor: the whole
one-way replay, measured with `BQCC` across the power-key reset, against
macOS's 0.1 W.

### The floor: the S2R entry starts, and a watchdog resets it (2026-11-07)

`e1-hook/run-floor` (`replay=15 oneway=1`, #29; flag `radios-off`), in a
safe boot, on battery at 100%. maralcbr's whole one-way replay: the 31
watchdog and error-handler writes, the keys and PMP words, the other 43
post-`slpa` writes ending with 1 to 0x2925c4000, their 500 ms, then every
CPU powered down.

- The 10-minute reading ended at 13:44:57 and was saved; the Mac then
  suspended and went dark. **About 30 s later it reset itself** and booted
  the regular system. This boot's `BACC` puts the power-on at 13:45:32,
  and Corey saw "maybe 30 seconds". The keyboard never lit, so run-floor's
  watch never ran: the suspend didn't come back, and the CPUs went down.
- Not our panic restart: `kernel.panic=10` plus a restart (3.5 s here)
  would have powered the Mac on about 15 s after the replay, not 30 s.
- macsmc-reboot's probe logged "PMU logged 1 boot error(s) and 0 panic(s)"
  (an unclean end with the PMU's boot stage at 0x30, as for a power-key
  reset; clean boots log nothing). The PMU's `fault-shadow` read the same
  after this reset and after a clean power-off, so it doesn't record this.
- **The SD card reader's link didn't come up** in the boot after the reset
  ("pci@1,0 link didn't come up"), the first time in 13 boots. It was
  back after a full power-off. Its port is PCIe, which the replay only
  touches after `slpa` (apcie writes at the table's start), so the replay
  very likely ran past `slpa`.
- The `BQCC` across the 35 s is about 2 mAh: too short to give a floor.

DeepIdle's worklog (JJRPF/Aurora-DeepIdle, 2026-11-05 to 11-07, runs D to
R19) explains the reset. On T6020, `AppleT6020PMGR::startS2RTimer` is one
write, 1 to MPMGR_AON + 0x4000: the same word as 0x2925c4000 here, the
replay's last write. It is the **commit** of the PMGR's S2R entry, and it
loads a watchdog (WD2, the AON "reconfig watchdog") that only a completed
entry stops. When the entry doesn't complete, it resets the SoC: about
60 s after the write on T6020 (85-86 s after `echo mem`), every run.
Their R11-R17 had every core in its final deep sleep, the clusters and
device domains off, the SMC's AP-sleep messages (boot stage 0x50), WD1
off and nothing touched after the commit, and the entry still never
completed. Their open candidates include the SEP's SLEEP message.

So on T6000 the replay reaches the same point: the commit, then an entry
that doesn't complete, reset by its watchdog after about 25-30 s. A floor
figure needs the entry to complete; until then the Mac can't stay asleep
long enough to measure. On the plus side, each attempt now has a fast
signature: a reset about 30 s in means "not yet", and no reset means the
entry completed.
