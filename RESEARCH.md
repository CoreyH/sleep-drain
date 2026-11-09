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
safe boot, on battery at 100%. maralcbr's one-way replay at its floor
setting, their bits 0-3 only: the 31 watchdog and error-handler writes, the
keys and PMP words, the other 43 post-`slpa` writes ending with 1 to
0x2925c4000, their 500 ms, then every CPU powered down. None of their other
bits ran: no AOP notices, no power-domain writes, no SEP message, no
internal-PCIe-port teardown, no wake masks.

- The 10-minute reading ended at 13:44:57 and was saved; the Mac then
  suspended and went dark. **About 30 s later it reset itself** and booted
  the regular system. This boot's `BACC` puts the power-on at 13:45:32,
  and Corey saw "maybe 30 seconds". The keyboard never lit, so run-floor's
  watch never ran: the suspend didn't come back, and the CPUs went down.
- Not our panic restart: `kernel.panic=10` plus a restart (3.5 s here)
  would have powered the Mac on about 15 s after the replay, not 30 s.
- macsmc-reboot's probe logged "PMU logged 1 boot error(s) and 0 panic(s)".
  The PMU's boot-error stage (nvmem 0x6003) reads 0x30, the stage
  macsmc-reboot sets at probe, so the reset came while Linux's stage was
  still set. Here the line follows only unclean ends: it's also in the
  boots after the power-key reset on 11-06 (22:01) and the restart at
  09:51 today. Clean boots log nothing. The PMU's `fault-shadow` read the
  same after this reset and after a clean power-off, so it doesn't record
  this.
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
WD2 isn't the only thing: stopping it 100 ms after the write (their W1)
moved the reset to about 116 s instead of removing it. Their R11-R17 had every core in its final deep sleep, the clusters and
device domains off, the SMC's AP-sleep messages (boot stage 0x50), WD1
off and nothing touched after the commit, and the entry still never
completed. Their open candidates include the SEP's SLEEP message.

So T6000 very likely stops where T6020 does: at the commit, with an entry
that doesn't complete, reset after about 25-30 s. That the commit arms the
reset is DeepIdle's T6020 finding, not yet shown here: the floor without
its last write would show it (E1a, without the table, stayed dark 46
minutes with no reset). A floor figure needs the entry to complete; until
then the Mac can't stay asleep long enough to measure.

Each attempt now has a fast signature: a reset about 30 s in means "not
yet". A Mac that stays dark is only a candidate, though. A hang before the
commit looks the same (E1a), and so would a hang in the commit write
itself. A breadcrumb written just before the commit, read on the next
boot, rules out the first but not the second. What shows a completed entry
is a low `BQCC` reading: a hang draws s2idle's ~2 W (E1a, 162 mA). A high
reading leaves it open, since on T8103 completed entries drew about 2.3 W
until the PMP saw the clusters idle (Naeem's try39, malik-na/m1-power-lab
74833809d621).

### The team: Naeem's Linux S2R entry completes on T8103; DeepIdle's R20 hangs (2026-11-07)

**Naeem Malik (M1 Air, T8103): the first S2R entry from Linux.** His run 2
(malik-na/m1-power-lab 84a905788261,
`docs/evidence/2026-11-07-linux-s2r/README.md`; driver malik-na/linux-aurora
`m1power/s2r-mem` at aca68b693bd2, read with Corey's OK):

- His finisher ran through its third commit: PMU scratch 0x9f0a reads `g`.
  It also set boot_stage 0x50 and programmed the RTC alarm 60 s after the
  commit, and the alarm fired.
- The next boot came 181 s after the commit: his WD1 dead-man (170 s) plus
  the boot. WD2's reset would have come 5 s after the commit, so the entry
  completed.
- There's no resume yet: the wake lands in m1n1, which can't hand back to
  Linux. The power wasn't measured.
- Run 1 hung before the finisher. His stopped secondaries had armed
  ACC_OVRD's deep-sleep bits, which act per cluster, under cores that were
  still running. The fix: only the last core of a cluster that isn't CPU0's
  arms them, and CPU0 arms its own last.

His order (`apple-s2r.c`), as `platform_suspend_ops` for "mem":

1. The PM core takes the secondaries offline into an S2R deep stop. Each
   cleans its L1, writes its bit in the PMGR CPU-start block's STOP
   register, arms ACC_OVRD under the cluster rule, sets CYC_OVRD to WFI
   mode 3 with no WFI return, and waits in WFI.
2. Then CPU0, in `cpu_suspend()`'s finisher with interrupts off:
   - the AON wake mask, so that only the PMU can wake the AP;
   - the AON watchdogs off (WD1 kept as the dead-man);
   - PMU boot_stage 0x50;
   - the clkgen muxes parked;
   - the PMGR counter blocks, CPM gating, cluster PS and SIO AUTO_ENABLE;
   - nub-spmi's interrupts: only the PMU alarm enabled, all acked;
   - the commit;
   - AIC masked;
   - the RTC alarm, over polled SPMI;
   - the commit twice more;
   - SPMI acked, and both clusters reported idle to the PMP;
   - CPU0's own deep WFI.

`apple-s2r.c` itself writes no SMC keys.

**What our floor does differently.** Its table already writes at the PMGR
offsets of his counter blocks (pmgr + 0x48000-0x48600 and + 0x78000-0x7d000
here) and cluster PS words (pmgr + 0x18000-0x18020), so very likely their
T6000 forms. It also turns WD0 off, and does more besides (the APSC
disables, the PLL-off sequence). But:

- the other CPUs stay up through the commit, and power down by IPI 500 ms
  after it, without a stop bit;
- interrupts stay on;
- there's no wake mask, no SPMI acks and no AIC mask;
- the commit is written once;
- there's no 0x50, no alarm and no report to the PMP.

PLAN.md's E2 now has these as the M1 recipe.

**DeepIdle (M2 Pro, T6020), R20** (JJRPF/Aurora-DeepIdle 75d4729189). They
wrote the cluster APSC disable, the same writes as entries 44-49 of
maralcbr's table here, before their release IPIs:

- most of their parked secondaries never woke to sleep, and PCPM0 stayed on;
- for the first time the timer didn't reset the SoC, and the Mac hung for
  28 minutes until the owner held power.

Our floor wrote the same disables and still reset about 30 s in. Our other
CPUs were idle in Linux, not parked.

We replied on their issue #9 (JJRPF/Aurora-DeepIdle#9, comment 6709046397).
The reply gives the ADT windows for their two pre-`slpw` words (which macOS
driver writes them is still unknown) and the floor's result.

### M1 run 1: the other seven cores down, both P complexes off, and still the reset at 30 s (2026-11-07)

`e1-hook/run-floor --m1` (`replay=15 oneway=1 quiet=1 m1=1`, #34 at
94f04b3; flag `radios-off`), in a safe boot, on battery at 79%. This was
the M1 recipe: Naeem's T8103 entry mapped onto T6000, on top of the quiet
floor. e1_hook.c's m1 block gives the full order. Raw data:
`results/m1-run1-20261107/`.

- The 10-minute reading ended at 19:57:07 and was saved. The Mac then
  suspended and went dark, and reset itself into the regular boot. Corey
  didn't see the keyboard light, so run-floor's watch didn't run.
- **The breadcrumbs survived the reset** (the first time on T6000), and
  `e1-hook/m1-crumbs` read them first thing in the next boot:
  - Stage 0x50 was reached: the last SPMI command before nub-spmi0's acks,
    the commit and CPU0's deep WFI.
  - All 7 other cores parked, left their spin when released, and read off
    in their PMGR power states within 10 ms. **PCPM0 and PCPM1, both P
    complexes, read off.**
  - CPU0 had nothing pending (`ISR_EL1` 0) before the commit, no core had
    an interrupt pending at its stop, and the cores took no AIC events.
  - The kernel's SPMI path still answered after `slpa`, and raw SPMI worked
    from then on. The boot-error stage (0x6003) reads 0x50: the reset found
    the replay's boot stage.
  - WD1 was off by step 4. Its control word read 0x4 at the module's load,
    before apple_wdt's suspend stopped it.
- **The reset came 30.6 s after the commit** (29.6-31.6 s: the PMU's RTC
  counts whole seconds). The commit's PMU second was in 0x6010. The arch counter, which restarts with the SoC, was compared
  with the PMU's RTC in the next boot. This is the floor's signature: the
  S2R watchdog (WD2) firing on an entry that didn't complete.
- After the reset, the AP wake masks read Linux's values again (0, 0x40, 0,
  ...): the run's masks don't outlive the reset. CPU-start + 0x4 read 0x880
  as before.

So with the seven other cores down, both P complexes off and nothing
pending, the entry still doesn't complete. The one CPU this doesn't cover
is CPU0. Its last breadcrumb comes before nub-spmi0's acks, the commit,
and its own ACC_OVRD, CYC_OVRD and WFI, and nothing records whether CPU0,
or the E complex with it, went down after that. So a CPU-side cause isn't
ruled out for CPU0 and its cluster; it is ruled out for the rest. That's
about where DeepIdle stands on T6020 (their R11-R17). Naeem's T8103 entry
completes, so what his has and ours lacks is the next lead. One thing to
start with: his kernel sends no SMC sleep keys, and ours sends maralcbr's
four.

### After M1 run 1: no cause in the PMU, Naeem's entry completes either way, and run 2 watches CPU0 (2026-11-07)

**The PMU doesn't say what caused the reset.** In the first boot after run
1's reset, the PMU's fault log (0x1000, 10 bytes) read all zeros. Its SoCD
area (0x8b00, 0x400 bytes) held an "INIT" header and noise, not trace
records.
- The fault shadow (0x867b) has the bits for por, rst, wdog/reset_in1,
  finger_reset, force_off and por_rtc set. But it read the same after the
  floor's reset as after a clean power-off ("The floor"), so it keeps
  history, not this reset.
- The only trace of the reset in the PMU stays the boot-error stage, 0x6003
  = 0x50. It keeps the stage of the last unclean end: since run 1 that has
  been 0x50 at every boot (runs 2-4's load logs read it 0x50 before those
  runs), so it can't tell one reset from another. The per-boot record is
  macsmc-reboot's "PMU logged N boot error(s)" line in that boot's journal
  (macsmc-reboot.c L189-198 at 4fdd0fc5; run 4's reset logged 1, and the safe
  boot before it logged none).
- All of these are read-only reads through the PMU's nvmem, at the offsets
  macOS's device tree gives. `results/m1-run1-20261107/pmu-after-reset.txt`
  has the details. A control read at a later clean boot will compare the
  hashes.

**Naeem's newer runs** (malik-na/m1-power-lab a2103f221e62, its
`docs/evidence/2026-11-07-linux-s2r/README.md` at 5d77d5e45980): his Linux
entry on T8103 completed in every run from 3 to 12. Each reached his last
marker with no WD2 reset, and his WD1 dead-man restarted the Mac about
181 s after the commit. The exception is test 7, with the dead-man off,
which stayed down until a long press: nothing else reset a completed entry.
- It completed both with macOS's SMC handshake (test 9: slpw, WKTP=2, slpa
  and SetAPPower 0x201, all acked) and without it (run 2).
- It completed with pms_sram on (test 10) and with it off.
- What fails there now is the wake: the boot CPU never runs its first
  instruction at the RVBAR (test 8).
- His bisection of macOS: blocking the AOP's message, or the whole SMC
  handshake, still slept and woke. Blocking the PMP's 0x201 pair made macOS
  panic.
- His remaining candidate, the PMP, is about the wake, not the entry.
- He also decoded the AON reconfig engine's wake sequences from its SRAM.
  The engine has progress registers at 0x23d2b8028 and 0x23d2b8040, and a
  WD1 reset clears them, so they can't be read after a failed wake. They sit
  in the AON watchdog block's 0x23d2b8000 page (his apple-s2r.c). Two things
  here are our inference: that they also track the entry, and that T6000's
  are at 0x2922b8028 and 0x2922b8040.

For our entry, the SMC keys we send probably aren't what blocks it, since
T8103 completes either way. Our run still keeps slpa, because T6000's PMU
declares its standby as an SMC write of MBSE (`function-external_standby`,
`info-has_slpsmc`).

**From his macOS traces** (mac27-hv README, same repo):
- The AON engine loads WD2 itself at the commit. Its count read 0.10 s at
  the last core's snapshot.
- A WD2 reset means the engine's completion condition went unmet with every
  AP step done (try41).
- In try41, a run with the CPU side complete still reset; try42, with fuller
  tracing, slept. Naeem puts it down to entry timing.

**Two loose ends in our own run:**
- apple_wdt's default timeout is 30 s (aurora 4fdd0fc5 `apple_wdt.c`, L54),
  about our reset time. WD1 was off by step 4 (run 1's 0x6014). Whether
  anything turns it back on after the commit isn't known.
- T6020's reset comes about 60 s after the commit, and T8103's about 5 s.

**So M1 run 2 is the observer** (`run-floor --m1-observe`, e1_hook
`observe=1`). It is run 1 with one change: CPU7, P1's last core, isn't
released before the commit.
- CPU0 writes CPU7's stop bit before the commit, while CPU7 still spins.
  That was the replay's reading of macOS's order: on T8103 each secondary
  writes its own stop request about 40 ms before the commit (Naeem's reading
  of the T8103 traces, malik-na/linux-aurora bd01a988d309's message), and only then
  arms ACC_OVRD and takes its deep WFI after the commit; CPU0 doesn't write
  them. T6000's macOS order isn't traced publicly.
- After the commit, CPU0 hands over and goes into its deep WFI. CPU7 then
  polls CPU0's and the E complex's PMGR power states for up to 500 ms each.
- CPU7 reads the three watchdogs, the seven soc clusters' busy bits,
  SIO_CPU, the AP wake status, and the two words at T8103's
  progress-register offsets (0x2922b8028 and 0x2922b8040, inferred).
- It records all of it in the OK'd breadcrumb bytes. Each stage byte says
  which bytes hold what, whichever SPMI command lands last (Pullfrog's
  catch on #37). Last, CPU7 records whether it had anything pending itself,
  then stops.
- e1_hook.c's m1 block has the byte layout. `m1-crumbs` decodes it and now
  prints the reset time itself, from the PMU's RTC and the SoC's counter,
  to within a second.

What the outcomes would mean:
- **CPU0 doesn't read off.** That's the first concrete difference from a
  completed T8103 entry, and the boot cluster is the next target.
- **CPU0 and the E complex read off, and the reset still comes at ~30 s.**
  The CPU side is very likely closed. Run 1 had the other seven cores down
  before the commit, and run 2 would show CPU0 and ECPM go down after it.
  Still, no run sees all eight down together. CPU1-6 go down before the
  commit, not after it as in macOS, and CPU7's own power-down after its
  second of watching is unobserved; only its last pending check is recorded.
  The observer's other bytes point at the first candidates elsewhere: a soc
  cluster still busy, SIO_CPU on, or a new wake-status bit. The two
  inferred progress words may help too, if they turn out to be the
  engine's.
- **The watchdog bytes.** They say whether the 30 s is WD2's, loaded at the
  commit, or WD1's, armed again (step 4 stopped it).
- **Stage 0x50 with the observe mark.** Nothing CPU7 sent after the commit
  landed: raw SPMI after the commit failed, or the hand-over never
  happened.

### M1 run 2, the observer: CPU0 and the E complex go down too, and WD2 still resets at ~30 s (2026-11-07)

`e1-hook/run-floor --m1-observe` (`replay=15 oneway=1 quiet=1 m1=1
observe=1`, #37 at b3f664e; flag `radios-off`), in a safe boot, on battery
at 84%. Raw data: `results/m1-run2-20261107/`. Its load log
(`e1_hook-load.txt`) is the awake baseline for everything CPU7 read.

- The reading ended at 23:14:17 and was saved. The Mac then suspended, went
  dark and restarted by itself into the regular boot. The power key wasn't
  needed.
- `m1-crumbs`, about 4 minutes into the next boot, found **stage 0x6f**:
  every breadcrumb landed, CPU7's included.
- **Every core went down.**
  - CPU1-6 read off at their release, before the commit, as in run 1.
  - After the commit, CPU0's PMGR power state read off within 500 ms of
    the hand-over, and the E complex's (ECPM) within 500 ms more. CPU0's
    deep WFI hadn't returned by CPU7's read; with its power state off, it
    very likely never did.
  - CPU7 had nothing pending just before its own deep WFI. Its own
    power-down isn't observed: nothing was left awake to see it.
- **The reset is WD2's.**
  - At CPU7's read right after its two polls, WD2's control was 4 (RESET_EN),
    with about 30 s to go. WD1 and WD0 were off. That read came at most about
    a second after the commit, since each 500 ms poll ended as soon as CPU0 or
    ECPM read off; likely a few ms (inferred: in run 3, CPU1-6 and PCPM0 read
    off within their 10 ms polls).
  - Awake at load, WD2's control was 0, with a 90 s bite and its count at
    71.8 s. WD1 had apple_wdt's 30 s, and apple_wdt's suspend had stopped
    it by step 4.
  - So the commit arms WD2 with about 30 s.
  - The SoC restarted about 30 s after the commit (28.6-30.6 s), as in run
    1.
- **What differed from awake, at CPU7's reads after its polls (at most about
  a second after the commit):**
  - Bit 4 was set in the soc-cluster words of EACC, PACC0, PACC1 and
    DISPLAY, and clear in ANE0's, AFR's and AVEMSR's. Awake, no soc
    cluster had bit 4 set (EACC 0x0f, the others 0x2f).
  - The table had written EACC, ANE0, AFR and AVEMSR to 0, and PACC0, PACC1
    and DISPLAY to 0x20. It writes them in K2, before `slpw`, with every
    CPU up, because e1_hook's error-handler address range (maralcbr's
    `lab_post_is_eh`) catches them; macOS writes them after `slpa`.
  - SIO_CPU's actual state wasn't off. The table's step-5 write,
    0x400024f, sets its target to f, after genpd had switched it off
    (next section).
- **What didn't differ:**
  - No AP wake-status bit was new. Those words are inferred at
    0x292004024, and all read 0 awake.
  - The two words at T8103's progress-register offsets (0x2922b8028 and
    0x2922b8040) read 0 awake, and their low bytes read 0 after the commit.
    So they say nothing about the entry. That they would track it was our
    inference ("After M1 run 1"): Naeem ties them to the wake.
- **Two awake reads settle older questions:**
  - wdt reg[3] (0x2922b8008) reads 0, so macOS's watchdog disable has
    nothing to clear here.
  - The CPM-gating candidates read 0x80000000 at 0x28e09c030 and +0x80,
    and 0 at +0x34, +0x38 and +0x88. Their role is inferred.

So with every core down, the E complex and P complex 0 off, nothing
pending and no new wake-status bit, the engine still doesn't complete its
entry, and WD2, armed at the commit, resets the SoC about 30 s later. CPU7
saw two things differ from awake: the four soc clusters' bit 4, and SIO_CPU's
actual state not off (run 3: its actual state wasn't f either, 10 ms after
the write, and CPU7 read it not 0 after the commit; no byte records its value).

### After M1 run 2: DISPLAY can't finish powering down, and the replay runs macOS's PMGR quiesce early and without its waits (2026-11-08)

Desk work on run 2, no hardware. Four research tracks (the soc clusters,
CPM gating, the engine and WD2, the order against macOS's), then a synthesis
and a check of every claim the next rung rests on. Sources, still current on
2026-11-07 at 23:05 UTC: JJRPF/Aurora-DeepIdle 75d472918995 (docs and
worklog), malik-na/m1-power-lab f4d61b647072 (READMEs), malik-na/linux-aurora
`m1power/s2r-mem` 148c72964eaa, maralcbr/linux 37be2668, xnu-12377.121.6 and
macOS's ADT (`/boot/macos-session/iodevicetree.txt`). No m1n1 code, scripts
or traces.

**What the soc-cluster word is** (0x28e098000 + 4n; maralcbr's trace calls
it `error-handler[2]+0x18000`):
- macOS's `ApplePMGR::enableCluster` writes `(v & ~0xf) | (on ? 0xf : 0)`
  and, when told to wait, polls bit 4 until it clears (DeepIdle's decode of
  macOS 13.5 on T6020, worklog L2185-2189).
- `AppleT6020PMGR::quiesceHW` calls it for logical clusters 1 to N and waits
  only above 3 (L2975-2979). A cluster's logical number is byte 3 of its ADT
  `soc-clusters` entry. T6000's (iodevicetree.txt L4072): EACC 1, PACC0 2,
  PACC1 3, ANE0 6, AFR 7, DISPLAY 8, AVEMSR 9.
- On T6020, with only the targets written, idle ANE0 and AVEMSR went from
  0x2f to 0x00. PACC0 and PACC1 (cores parked) and DISPLAY (DCP running)
  stopped at 0x10, and DISPLAY's 10 ms poll timed out (L2214-2226). With the
  cores down, a P cluster still had bit 4 set about 6 ms after the cores'
  release, as its complex read off, and not 500 ms after (R13 and R12,
  L2915-2917 and L2898).
- So bits 3:0 are the target and bits 5:4 the cluster's state: 2 up, 1
  changing, 0 down or never cycled (EACC reads 0x0f awake, here and on
  T6020). Bit 4 set means a power change was asked for and hasn't finished:
  shown on T6020, inferred here.

**Where macOS writes them, and where the replay does:**
- maralcbr's table is in macOS 15.6.1's trace order. Its soc-cluster words
  (entries 52-60) come after `slpa`, after the counter blocks, the clusters'
  DVFS and APSC writes, DCS (7, then 5) and AFR's power state, and before
  SIO_CPU, the ClkGen park, PLL-off and the commit. The clusters go in
  ascending logical order.
- On T6020, quiesceHW waits on DCS, AFR's power state, each cluster above 3,
  SIO and SIO_CPU, the ClkGen muxes and PLL-off (worklog L1866-1876, L2682).
- macOS's secondaries are still up then: XNU's H13 code (T6000's family)
  stalls them in WFE until the boot CPU lets them go (xnu-12377.121.6 cpu.c
  L1074-1077 and L1127-1141; H13.h L67).
- e1_hook sends 31 of the 74 entries in K2, before `slpw`, with Linux still
  running on every CPU: every address `e1_post_is_eh()` (e1_hook.c L435)
  catches. That is maralcbr's `lab_post_is_eh` range under their
  `lab_eh_early` default (macsmc-reboot.c L329-339): a lab choice, not
  macOS's order.
  - Only five of the 31 are the watchdog and error-handler words (entries
    2-6).
  - The other 26 are PMGR words: the counter blocks (7-14, 39-42 and 59),
    AFR's power state (51), the seven soc-cluster targets (52-55, 57, 58 and
    60) and the ClkGen park (62-66).
  - The ADT's error-handler node maps whole PMGR windows (0x28e000000 and
    0x28e080000, 0x80000 each), which is why the trace labels them
    error-handler writes.
- The replay makes none of macOS's waits.

**What Linux has already done by `prepare_late`:**
- genpd has switched SIO_CPU and its parent SIO off, and AFR's power state
  too. The hook-point check's boot logged it, with genpd's debug lines on
  (`safe-journal-20261106T201952.txt` L5650, L5722 and L5738, before
  `op=prepare_late` at L5776).
  - So the table's AFR write (entry 51, 0) changes nothing.
  - Its SIO_CPU write (0x400024f: target f, AUTO_ENABLE off) asks SIO_CPU
    back on under an off parent (run 3: its actual state wasn't f 10 ms later). macOS has both on, and T6020's quiesceHW
    waits on both; Linux has both off. The replay leaves a state that is
    neither. That write is run 2's "SIO_CPU not off".
  - PLAN.md's prepared answer to "SIO_CPU not off" was macOS's
    read-modify-write. Here that would leave SIO_CPU off, which isn't
    macOS's state either, so it's dropped.
- disp0_cpu0 and disp0_fe stay on through s2idle, held by their always-on
  child pmp-disp0 (Finding 3;
  `/var/log/sleep-drain/20261106T223107/pre/genpd`). macOS has every display
  domain off at sleep entry (maralcbr L567-600).
- The PMP runs, and owns the fabric and DCS p-states (`apple-pmgr-misc ...
  PMP manages fabric and DCS p-states`, every boot). aurora's driver leaves
  DCS alone in s2idle while the PMP runs, since lowering it "leaves s2idle
  unable to resume" (apple-pmgr-misc.c at 4fdd0fc5, L392-396). The table
  lowers DCS to 7, then 5, with the PMP running. macOS idles the PMP and
  stops its CPU (sleep action 610) before quiesceHW (90000) on T6020
  (DeepIdle's deep-idle-research.md L952-954 and L966-982), and on T6000 has
  the PMP's power domain off at sleep entry (maralcbr L567-600: 0x0f0f0300).

**Run 2's bytes, read with that** (inferred):
- **ANE0, AFR and AVEMSR: not pending.** They are T6000's runtime-gated soc
  clusters: `soc-cluster-power-gating` is 0x2e0, and only their entries carry
  gating bytes (L4072, L4125). Linux never gates them awake (0x2f), and macOS
  writes them 0, not 0x20, so it already had them down. Under the replay
  they most likely powered down at its write; bit 5 wasn't recorded, so that
  isn't shown.
- **DISPLAY: pending** from its write, before `slpw`, to CPU7's read after its
  polls (at most about a second after the commit, likely a few ms, inferred). With disp0's domains on, its power-down can't
  finish, as on T6020 with the DCP running. macOS waits for it (logical 8),
  so at macOS's commit DISPLAY's power-down has finished. That is the
  clearest difference from macOS's state at the commit that run 2 shows.
  That the engine needs it isn't shown: T6020 had every display domain off
  from its run R6 on and still reset, though whether its DISPLAY cluster
  settled wasn't recorded (worklog L2534-2536, L2991).
- **EACC and PACC0: not shown stuck.** CPU7 read EACC about 20-50 us after its
  ECPM poll ended (inferred from the code: 13 MMIO reads and one SPMI
  command), and PACC0 at least about 0.15 ms after PCPM0 read off in step 6
  and at most about a second later (the polls' bounds; run 2's code, b3f664e).
  T6020's window, still pending at 6 ms and clear by 500 ms of the release,
  overlaps that range; it doesn't cover its top. Run 3 (below) shows PACC0
  pending just before the commit and first read clear under 5 ms after the
  hand-over, so run 2's read was likely before it cleared (inferred); run 2
  and run 3 can't be told apart from "the moved writes changed it".
  PACC1 holds CPU7, so its bit 4 says nothing.
- **The early batch isn't shown to matter.** macOS also writes the cluster
  targets with every core up, and T8103's entry completes with them written
  before or after its cores go down (Naeem). But the batch also parks the
  SoC's clocks (ClkGen) before the SMC handshake, where macOS parks them
  last, just before PLL-off.
- **WD2: the commit loads it.** CPU7 read about 30 s left of a 90 s bite, so
  a count near 60 s; at load the count read 71.8 s. Had the count run on
  from there, it would have been about 139 s at the commit (about 604 s
  later, with a 32-bit counter's 179 s wrap at 24 MHz), past the bite, and
  the block resets once the count passes the bite with RESET_EN set. Had it
  stood still, it would still be above 60 s. Either way the commit rewrote
  the count, the bite or both, not only RESET_EN. Run 2's bytes can't tell
  which. T8103's engine loads exactly 5 s (Naeem, mac27-hv README L332) and
  T6020's about 60 s.
- **CPM gating doesn't explain run 2's pattern.** The page at 0x28e09c000 is
  most likely T6000's PMGR power-gate page: it sits 0x4000 past the
  soc-cluster words, as T8103's and T6020's do, and every ADT
  `pwrgate-regs` offset lands in it, counted from 0x28e000000 (L4077). Which
  word gates which complex isn't known. But the P complexes power off with
  their candidate words at 0, and EACC stayed pending with its candidate set.
- **The CPU side.** Across runs 1 and 2, every core and all three complexes
  have read off around the commit, though not all in one run. What's left
  is on the SoC side.

**So:** run 2 shows one clear difference from macOS's PMGR state at the
commit: DISPLAY's unfinished power-down, which Linux's display chain blocks.
It also shows that the replay sends 26 of macOS's PMGR quiesce writes before
`slpw`, parks the clocks before the SMC handshake, makes none of macOS's
waits, leaves SIO half on, and lowers DCS under a running PMP. None of these
is shown to block the entry. T6020 ran quiesceHW's steps in macOS's order
with 10 ms waits, with every display domain off, the PMP idled and macOS's
SMC messages sent, and still reset (worklog L2991). The next rung, M1 run 3,
puts the quiesce in macOS's place, makes its waits, and records which ones
fail (PLAN.md).

### M1 run 3: the PMGR quiesce in macOS's place: only DISPLAY's and SIO_CPU's waits time out, DCS settles, and the SoC still resets at about 30 s (2026-11-08)

`e1-hook/run-floor --m1-late` (`replay=15 oneway=1 quiet=1 m1=1 observe=1
pmgr_late=1`, #39 at 2ccc7c6; flag `radios-off`), in a safe boot, on battery
at 87%. It is run 2's recipe with one change: the 26 PMGR quiesce writes that
maralcbr's range sent before `slpw` go out after `slpa`, at their places in
the table, with macOS's waits. The observer stayed up. Raw data:
`results/m1-run3-20261108/`; its load log is the awake baseline.

- The reading ended at 10:15:55 and was saved. The Mac suspended, went dark
  and restarted by itself 30.0-31.6 s after the commit (two `m1-crumbs`
  reads, `boots.txt`), the ending of runs 1 and 2 (29.6-31.6 and 28.6-30.6
  s). If the delay is the same each time, the three windows meet at
  30.0-30.6 s (inferred). That it is WD2's reset is a strong inference
  (control 4, 30 s left, the timing); the PMU doesn't record the cause.

**The facts (each byte re-derived from e1_hook.c at 2ccc7c6, not from the
decoder):**
- **Stage 0x7f.** Every breadcrumb of CPU7's landed, and it had nothing
  pending just before its acks and its own WFI. Its own power-down is
  unobserved, as in run 2.
- **The cores.** CPU1-6 read off at their release, PCPM0 before the commit,
  CPU0 within 500 ms of the hand-over and ECPM within 500 ms more (0x6005 =
  0xff, 0x6014 = 0x90). CPU0's last WFI hadn't returned (0x6006 bit 7). When
  CPU0 and ECPM read off isn't recorded; PCPM1 wasn't checked, and no run has
  seen all eight cores down at once.
- **The watchdogs.** At CPU7's read WD2's control was 4 (RESET_EN) with 30 s
  to its bite (0x6007, rounded), WD1 off, WD0 not armed (0x6006 = 0x04).
  Awake at load WD2 read control 0, bite 90.0 s, count 127.7 s: past its
  bite, with no reset while RESET_EN is off.
- **macOS's 15 waits** (qto = 0x50; up to 10 ms each, on CPU0 in step 5).
  - Two timed out: DISPLAY's power-down (entry 58, bit 4 of 0x28e098018) and
    SIO_CPU (entry 61, 0x28e580188, actual != target).
  - The other 13 read met within 10 ms: DCS after 49 and 50 (bit 31 clear and
    actual == target), AFR's power state (51), ANE0's, AFR's and AVEMSR's
    power-downs (55, 57, 60), the five ClkGen-SOC muxes (62-66, bit 30
    clear) and PLL-off after 69 and 72 (bit 31 clear).
  - Only DCS's two show a state change: all but AFR's power state and SIO_CPU
    already read met awake at load (the load log), and AFR's power state was
    already off by genpd. A met wait may have been met at its first read,
    and bit 5 isn't recorded.
  - That these 15 are macOS's T6000 waits is T6020's decode mapped by what
    each write does (inferred, e1_hook.c L881-884).
- **DCS** read actual 5, target 5, bit 31 clear at CPU0's last wait before
  the commit (0x6016 = 0x95), against 0x666 awake (load log), with the PMP
  running. Only that end value is recorded: not the intermediate 7, and
  nothing after entry 50. The ClkGen park, PLL-off and the commit followed
  with no hang: the risk PLAN.md named did not come true this time.
- **EACC and PACC0.** CPU0 read PACC0's bit 4 still set just before the
  commit, about 0.05-0.15 ms after PCPM0 read off (inferred from the SPMI
  commands between). CPU7 first read both bits clear under 5 ms after the
  hand-over (0x6013 = 0xc0). At the end of its watch bit 4 was clear on
  EACC, PACC0, ANE0, AFR and AVEMSR, and set on PACC1 (CPU7's own cluster:
  expected) and DISPLAY (0x6015 = 0xa4).
- **SIO.** CPU7 read SIO_CPU's actual state not off and SIO's actual state
  off (0x6015 bit 7, 0x6016 bit 6).
- **Awake baselines worth keeping** (load log): fabric-ps 0x28e20c000 =
  0x333; ClkGen 0x28e038164/16c/168/170 = 0x85100000 and 0x28e03815c =
  0x87100000, against the table's 0x80100000; PLL-off 0x239; AFR's power
  state 0x1f0003ff; SIO 0x1f0000ff; SIO_CPU 0x1400024f.

**What they show:**
1. Moving the quiesce to macOS's place, with macOS's waits, doesn't complete
   the entry with the observer up: the same reset at about 30 s.
2. The two waits that fail are the two PLAN.md named beforehand. Nothing else
   macOS waits for fails here (with the mapping caveat above). Neither is
   shown to block the entry.
3. The DRAM p-state settles at 5 under a running PMP, so the trigger for
   moving the PMP rung up (DCS not settling) did not fire. Not shown: DCS at the commit (nothing
   reads it after entry 50, on purpose), or that DCS matters to the entry.
4. EACC and PACC0 are not stuck, so the trigger for writing the CPU-cluster
   targets last did not fire. For
   PACC0, the transition straddles the commit: pending just before it, clear
   within 5 ms of the hand-over. A natural millisecond-scale power-down is
   enough to explain that (T6020: 6 ms still pending, clear by 500 ms,
   worklog L2915-2917, L2898), but the 5 ms bucket can't place it either
   side of the commit (inferred).
5. CPU7 read 30 s left in both runs although WD2's count at load differed
   (71.8 s in run 2, 127.7 s here): that supports "the commit loads WD2"
   (inferred; the free-running arithmetic assumes a 32-bit counter at 24 MHz).

**Inferred causes:**
- DISPLAY can't finish because disp0's domains stay on through s2idle
  (Finding 3), as on T6020 with the DCP running (worklog L2214-2225).
- SIO_CPU: entry 61 is macOS's read-back of an on domain with AUTO_ENABLE
  cleared. Under genpd's off SIO it asks for target f, and SIO_CPU's actual
  state wasn't f 10 ms later and wasn't 0 at CPU7's read. That limbo is the
  replay's own artefact. Its actual value is most likely 4 (actual only ever
  reads 0, 4 or f in this Mac's dumps), but no byte records it.

**What the data can't tell apart:**
- Whether DISPLAY's unfinished power-down, SIO_CPU's limbo, or something else
  holds the engine: both were unfinished, and no byte reads the engine.
  T6020 was left with neither as far as its records go (display domains off,
  SIO on), but it recorded neither DISPLAY's bit nor SIO_CPU's state.
- Whether `pmgr_late` matters without the observer: run 4 has `pmgr_late`
  and no observer, but with the alarm in the same run; `pmgr_late`, no
  observer and no alarm is still unrun. Run 1 had no observer and no
  `pmgr_late`.
- EACC: CPU7's first read is at the hand-over itself (e1_hook.c L1530-1533)
  and bucket 0 is any read under 5 ms. EACC has no earlier read, so "never
  pending in this run" (its awake state is bit 4 clear) can't be told from
  "pending, then clear". T6020's R17 and run 2 favour the second (inferred).
  Bit 5 wasn't recorded, so "bit 4 clear" means "not changing", not "down".
- Run 2 against run 3 for EACC and PACC0: "read too soon" and "behaves
  differently because the writes moved" both fit; too-soon is only the
  simpler story. Run 2's "about a second after the commit" was the two
  polls' upper bound; it was likely a few ms (inferred).
- DCS after entry 50, the fabric's p-state at the commit (Linux's
  suspend_noirq sets its target to 1, "set 0 ps to pstate 1" in the Nov 6
  boot; the table never writes it; T6020's macOS ends it at 3, worklog L2682), and anything
  behind the parked clocks: not read.
- Whether CPU7 staying up matters: it was up at most about 1.5 s after the
  commit, likely milliseconds; nothing here says, either way. Run 1 (CPU7
  down, no alarm, no `pmgr_late`) had already reset; run 4 took CPU7 down
  before the commit with `pmgr_late` and reset the same way, so CPU7 staying
  up is not the sole blocker, if the alarm's commands are harmless (inferred).

**So:** with the quiesce in macOS's place and every wait but DISPLAY's and
SIO_CPU's reading met within 10 ms, the entry still ends in the reset. The
clearest differences left at the commit are DISPLAY's unfinished power-down
and SIO_CPU's half-on state, both facts of this replay on T6000, and neither
is shown to matter. What's common to T6000 and T6020 and untested is listed
in the next section and in PLAN.md's rungs.

### After M1 run 3: what's new, and what's left to try (2026-11-08)

Desk work, no hardware: five research tracks (run 3's bytes re-derived from
the code, new sources, the two unmet waits, the alarm rung and the observer,
what still differs at the commit), a synthesis, and an adversarial check of
every claim the next rung rests on. Sources, heads rechecked 2026-11-08 at
about 10:50 UTC: JJRPF/Aurora-DeepIdle cc38b1c535cf (docs and worklog),
malik-na/m1-power-lab a2d5e147f0dc (READMEs, unchanged since f4d61b647072),
malik-na/linux-aurora `m1power/s2r-mem` 148c72964eaa and `m1power/s2r-pmp`
b6b2ea1d19e0 (his Linux driver and deep stop, GPL-2.0-only), maralcbr/linux
37be2668, aurora-silicon/aura PRs #112 and #117 (the PR text only),
xnu-12377.121.6 and macOS's ADT. No m1n1 code, scripts or traces, and no Aura
code. One track also fetched DeepIdle's `data/psmap.txt`; nothing here rests
on it.

**New since run 2:**
- No public source shows an S2R entry completed from Linux on T600x. T6020
  still ends in its 86 s reset (DeepIdle's worklog at cc38b1c535cf).
- **DeepIdle's R18 (T6020):** the SEP answered SLEEP (reply tag 0x11, data
  0) and the run still reset at about 86 s, its PMU scratch byte for byte
  R17's. Their conclusion: "the SEP SLEEP message alone was not what the
  entry waited for" (L3175). R18B (the SEP's PMGR hand-off) and R21 (three
  fabric lanes' PS_AUTO floor to 0, macOS's value at sleep entry) are built
  and not run. Against R21's table this Mac has the same gap on AFNC1_LW2,
  AFNC2_LW1 and APCIE_GP_SYS, but its SEP word (0x28e580c00) already reads
  macOS's 0x1000030f (`results/sleep-peek-20261104T120619/`), and AFNC2_LW1
  did gain its was-gated bits across an s2idle at PS_AUTO f ("PMGR power
  states on this Mac", above), which cuts against "never gated".
- **Naeem's global sleep (T8103, no result published yet):**
  `m1power/s2r-pmp` bd01a988d309 and b6b2ea1d19e0 (2026-11-08), default on.
  Each secondary requests its stop, holds in WFE through the commit, and
  only after CPU0's release arms ACC_OVRD's deep sleep and takes its deep
  WFI, "as XNU does on H13": XNU stalls H13's secondaries until the boot CPU
  clears `arm64_stall_sleep` (xnu-12377.121.6 cpu.c L1074-1077, L1127-1141),
  and his macOS traces have every core's deep WFI after the commits
  (mac27-hv README L87-91). So "cores down before the commit" is his Linux
  entries' shape, not macOS's. His earlier T8103 Linux entries complete
  without global sleep; T6020's R11-R15 released their secondaries after the
  commit and R16-R17 before it, and all reset.
- **DJ's T8140 (MacBook Neo, J700), the PR text only:** "hundreds of sleep
  and wake cycles, including an 8/8 soak" on Linux since 2026-11-06, with no
  power figure; the author's claim, read as prose. The driver it names
  (`drivers/soc/apple/t8140-sleep.c`) wasn't found publicly and has no OK of
  Corey's to read. (2026-11-08: aurora-silicon/linux PR #1208 carries it,
  unread; "After M1 run 4" has what its text reports.)

**What the three SoCs still say (inferred):** T8103's Linux entry completes
with almost nothing: no SMC handshake in one run, no PMP firmware or an
idled PMP, no AOP, GPU and ATC domains on; it always has the alarm after the
first commit and three commits. T6020 resets despite macOS's order and
waits, every driver-held domain off, the PMP idled, SMC, AOP, SIO and SEP
messages and macOS's wake mask. T6000 and T6020 fail at the same point with
very different device state, so a common cause is favoured over a
T6000-only anomaly, and the common untested set is short: the alarm after
the commit (tested since on T6000 alone, run 4: not sufficient with
nub-spmi0's enables all 0; its line enabled untested), the PMP's CPU stopped
or absent, the fabric lanes' PS_AUTO, the PCIe ports, the SMC deadline word,
the MCC quiesce. Nothing here shows what the AON engine's completion
condition is on T8103, T6020 or T6000; on T8140, DJ's PR text names two
CPU-side conditions ("After M1 run 4").

**The ladder from here** is in PLAN.md (the rungs after M1 run 4).

### M1 run 4: the PMGR quiesce and the RTC alarm, no observer: every core but CPU0 down before the commit, the alarm written between the commits, and the SoC still resets at about 30 s (2026-11-08)

`e1-hook/run-floor --m1-alarm` (`replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1
alarm=1`, #41 at 3336cf1; flag `radios-off`), in a safe boot, on battery at
99% (`reading-summary.txt`). Against run 3 it has two changes, Corey's pick of
a cumulative shape: the PMU's RTC alarm, written between the commit's first
write and the other two, and no observer (CPU7 released before the commit, as
in run 1). Raw data: `results/m1-run4-20261108/`; its load log is the awake
baseline.

The reading ended at 13:33:00 and was saved. The Mac suspended, went dark and
restarted by itself into the regular boot (`boots.txt`: `uptime -s` 13:33:57).
That boot's journal has macsmc-reboot's "PMU logged 1 boot error(s) and 0
panic(s)"; the safe boot's journal doesn't. macsmc-reboot.c L189-198 (at
4fdd0fc5) only logs and zeroes the PMU's counts; that the count marks an
unclean end is inferred, from the cell's name and the pattern here.

**The facts (each byte re-derived from e1_hook.c at 3336cf1, not from the
decoder):**
- **Stage 0x5f** (L1937): the last SPMI command after all three commit writes
  and a dsb (L1927-1934). CPU0 issued the third commit write and raw SPMI still
  worked after it. Nothing later is recorded: the acks, ACC_OVRD, CYC_OVRD, the
  WFI.
- **0x6016 = 0xbd** (`m1_alarm_arm()`, L1738-1775): 0x14c0 read 0 (no event,
  nothing to echo), then 0x140c, 0x14c2 = 0 and 0x1401 = 0x41, each acked
  (the reply's ack bit, L1334), all between the commit's first write and the
  other two (L1927-1933). Acked isn't a read-back: the next boot's 0x140c and
  0x1401 confirm those two; 0x14c2 read 0 at load already, so its value shows
  nothing.
- **The commit's second:** 0x6010-0x6013 = 184897, step 8's RTC second, about
  0.1 ms (two SPMI commands, inferred) before the first commit write
  (L1893-1899).
- **The cores:** parked 0xfe (all seven). 0x6007 = 0xfe: CPU1-7, CPU7 included,
  read off within 10 ms of their release, in the order 1, 2, 3, 5, 6, 4, 7.
  0x6014 = 0xb0: PCPM0 and PCPM1 read off (PCPM1 and CPU7 for the first time
  with `pmgr_late`: run 3 kept CPU7 up, run 1 had no `pmgr_late`), CPU0 had
  nothing pending at step 8, WD1 was off at step 4 (apple_wdt's suspend had
  stopped it: it read 0x4 at load), K3's write worked. 0x6015 = 0: no core had
  an interrupt pending at its stop.
- **qto (0x6006) = 0x50:** of macOS's 15 waits, only DISPLAY's (entry 58) and
  SIO_CPU's (61) timed out, as in run 3; the other 13 read met within 10 ms,
  both DCS waits among them (no byte in this run records DCS's value).
- **The reset.** The SoC restarted by itself 29.6-31.3 s after step 8's
  second (two `m1-crumbs` reads, 29.6-31.6 and 29.3-31.3, `boots.txt`), the
  ending of runs 1-3 (29.6-31.6, 28.6-30.6, 30.0-31.6). The four windows still
  meet at 30.0-30.6 s, as run 3's three did (inferred, if the delay is the same
  each time; the counter's restart may lag the reset by an amount common to all
  runs). That it's WD2's reset is a strong inference (runs 2 and 3 read its
  control 4 with 30 s to its bite; the timing; WD1 off at step 4); no observer
  read WD2 in this run, and the PMU doesn't record the cause.
- **The alarm survived the reset.** At the next boot 0x1401 read 0x41 (armed),
  0x140c 188497 (the commit's second 184897 + 3600), 0x14c0 0, 0x14c2 0, about
  8 minutes after the reset and again about 40 s later at `m1-crumbs --clear`,
  which then set 0x1401 back to 0x01 and zeroed the breadcrumbs (`crumbs.txt`,
  `boots.txt`). Disarmed at about +9 minutes, it never fired.
- **The load log** against run 3's: the alarm line is new (0x1401 = 01, 0x140c =
  96 8e 00 00 = 36502, 0x14c0 and 0x14c2 = 0); the observer's lines are gone with
  the observer; which idle P cores read power state 0x100 differs, as it does
  between any two boots; no wait word changed. 36502 is `e1-hook/check`'s 60 s
  alarm of 2026-11-06 19:19:48 UTC (`e1-hook-20261106T201837/log.txt`; inferred
  from the PMU RTC's epoch): the compare value stayed through 41 hours and the
  floor runs' and runs 1-3's resets (inferred, nothing else wrote it).
- 0x6003 = 0x50 says nothing about this run: it keeps the stage of the last
  unclean end, and since run 1 that has been 0x50 at every boot (run 4's load
  log already reads it, as runs 2's and 3's do), so it can't tell one reset from
  another.

**What they show:**
1. The run counts by PLAN.md's rule (0x5f with 0x6016 = 0xbd): the alarm's
   commands were acked and all three commit writes were sent. The entry still
   doesn't complete. The alarm written between the commits, with nub-spmi0's
   enables all 0 (step 7, L1886-1887), doesn't complete the entry on T6000
   (for this configuration; as macOS's try41 already showed on T8103 with the
   alarm present).
2. CPU7 and PCPM1 down before the commit with macOS's PMGR quiesce in macOS's
   place: shown for the first time, with the same two waits timing out as in
   run 3. That match says nothing about CPU7: `qto` is measured in step 5,
   before step 6 releases any core (L1823-1851), so in runs 3 and 4 all seven
   cores were parked and spinning at every wait. With no observer and CPU7 down
   before the commit, the reset is unchanged, so CPU7's staying up (runs 2
   and 3) is not the sole blocker, if the alarm's commands are harmless
   (inferred). Run 1 (CPU7 down, no alarm, no `pmgr_late`) had already reset;
   run 4 extends that to `pmgr_late`.
3. `pmgr_late`'s result repeats without the observer, but with the alarm in
   the same run: the cell `pmgr_late`, no observer, no alarm is still unrun.
4. The reset's timing doesn't depend on which core goes down last: CPU0 alone
   after the commit in runs 1 and 4, CPU7 in runs 2 and 3 (inferred, if the
   delay is constant; the windows allow it).
5. New: on T6000 the armed alarm survives the reset and the boot firmware, as
   the PMU's scratch bytes do. Every alarm run must be followed by `m1-crumbs
   --clear`, or the alarm stays armed until +3600 s; what a fired alarm does
   after a reset is unknown.

**What the data can't tell apart:**
- Alarm not needed, needed but not sufficient, or the alarm's commands
  harmful: a reset looks the same. The commands aren't only the alarm's three
  writes: there's a read and four SPMI writes between the commit's first write
  and the other two (0x14c0, 0x140c, 0x14c2, 0x1401, the 0x6016 crumb), stage
  0x5f after the third, and a second round of nine acks, none of which runs 1-3
  had.
- Which open blocker holds the engine, or several: DISPLAY's unfinished
  power-down and SIO_CPU's half-on state (`qto` 0x50 again), the PMP, or
  something no byte reads. No byte reads the engine.
- That this reset is WD2's: strongly inferred, not read in this run.
- CPU0's and the E complex's state after the commit: nothing here reads them
  (runs 2 and 3 read them off, with CPU7 up). Whether CPU0 reached its deep WFI
  is not recorded either: 0x5f comes before the acks and the WFI.
- Which commit write anchors WD2's count: the writes are 0.2-0.4 ms apart at
  most (inferred) against a 1 s timer.

**So:** with every core but CPU0 down before the commit, both P complexes off,
nothing pending, macOS's PMGR quiesce in its place and the alarm armed between
the commits, the entry still ends in the reset at about 30 s. The alarm doesn't
complete the entry here, in this configuration, and stays in the recipe as a
move toward macOS. The clearest unmet differences at the commit remain
DISPLAY's unfinished power-down, SIO_CPU's half-on state and the PMP running
un-idled (none is shown to matter).

### After M1 run 4: what's new, and what's left to try (2026-11-08)

Desk work, no hardware: five research tracks (run 4's bytes re-derived from the
code, new sources, every core held through the commit, the PMP out, what else
is left), a synthesis, and an adversarial check of every claim the next rung
rests on. Sources, heads rechecked 2026-11-08 at 13:53-14:04 UTC:
JJRPF/Aurora-DeepIdle 03cb9f059cc9, malik-na/m1-power-lab a2d5e147f0dc,
malik-na/linux-aurora `m1power/s2r-pmp` b6b2ea1d19e0 and `m1power/s2r-mem`
148c72964eaa (Naeem's Linux driver and deep stop, GPL-2.0-only), maralcbr/linux
(unchanged), omacom/linux-aurora 4fdd0fc5, xnu-12377.121.6, checkra1n/PongoOS
(`recfg_soc.c` and `recfg.h`: its base table and sequence format only),
aurora-silicon/linux PRs #1208, #1217 and #1225 and aurora-silicon/aura #112
and #117 (the PR text only; #119's title only) and macOS's ADT. No m1n1 code,
scripts or traces, and no Aura code.

**New since run 3:**
- No public source shows an S2R entry completed from Linux on T600x.
- **DJ's T8140 series** (aurora-silicon/linux#1208, head cd27afb6ccd0; the
  author's prose, and the driver wasn't opened: it has no OK of Corey's to
  read) reports completed, returning entries from Linux on a J700: 30 of 30
  over two boots, and 16 of 16 on battery with nothing plugged in, with a
  0.049 W average over a 300 s sleep (entry and wake included), against 0.109 W
  measured for macOS under aura on that machine. Its text names two CPU-side
  conditions: a core that misses its stop holds the sleep engine until the
  watchdog resets the SoC, and a cluster power-gate enable, set at boot,
  without which "the sleep engine never started" (a 5 s reset there). It says
  nothing of a device-side condition, though its driver refuses a sleep unless
  both DCPs, the PMP and SIO have stopped; its text says the PMP's "idle and
  stop" are the sleep's last step. Its text starts T8140's order with WD2, which
  T6000's table never writes; the code, read since (below), puts the PMU's boot
  stage and the power-word match first and WD2 after the stop bits. Either way
  the order doesn't transfer.
- **Naeem's global sleep:** no result published. b6b2ea1d19e0's message says
  "Found by review of bd01a988d before the hardware run", and bd01a988d309's
  names this repo's T6000 port as the lead, so that support isn't independent
  of this repo. Naeem's newest reading of the T8103 traces corrects this notebook:
  each secondary writes its own stop bit about 40 ms before the commit
  (corrected in the "M1 run 2" section above).
- **DeepIdle:** R18B was armed and stopped before suspend (the SEP driver
  failed to bind, a loader race, fixed); no result yet, and R21 is built and
  not armed (03cb9f059cc9).
- **The PMU's alarm on a J316s:** RyanTheTide's receipts on aurora-silicon/linux
  #1225 (2026-11-08) report, on their test kernel, that the alarm's comparator
  matches (the control's ARM bit clears itself) but the alarm never reaches the
  AP's SPMI interrupt, and the event register "keeps the same value". This Mac's
  own result is the opposite: the alarm wakes s2idle through the SPMI interrupt
  ("The alarm wakes J314s from s2idle"). Their driver acks 0x14c0 before arming,
  so a stale 0x14c0 alone doesn't explain "keeps the same value". It does keep
  bit 1 of 0x1401 across its read-modify-write on Maverick, and this J314s found
  that bit latched from a wake macOS had scheduled ("Awake test"), with the
  firing here setting both. So a bit 1 left set could be what they see, if the
  event latches only on that bit's 0-to-1 edge, which nobody has tested. Posted
  as a hypothesis on #1225, with the check that tells it apart (Corey's OK,
  2026-11-08); their board or setup could still differ.

**Where the candidates stand** (the order is in PLAN.md, and only there):
- **The PMP out** (idle pair, then its CPU stopped). Every completed T8103 Linux
  entry had no PMP firmware or an idled PMP (Naeem's linux-s2r README L108-120);
  T8140's text stops the PMP in the sleep's last step; T6020 idled the PMP and
  reset, and no R-run stopped its CPU; macOS on T6000 has the PMP and PMS_SRAM
  domains off at sleep entry ("PMGR power states on this Mac", above), which no
  run reaches, since ps_pmp is never-write. On this Mac the PMP has run un-idled through all four
  runs. So it's the largest difference at the commit that T8103 can't speak
  against and T6020 only half tried (inferred). Against holding the cores first:
  T8103's Linux entries complete without it, T6020's R11-R15 went down after the
  commit and reset, and on this Mac CPU1-6 read off at their release in all four
  runs (CPU7 before the commit in runs 1 and 4; in runs 2 and 3 it stayed up and
  its power-down is unobserved). A moderate prior, perhaps 1 in 5 (judgment).
  It's a new RTKit message and an ASC write: Corey's OK first.
- **Every core held through the commit** (XNU's H13 global sleep, no new
  address, no new PMU byte): the same shape as T8140's held cores, but T8103's
  Linux entries complete without it and CPU7 held through the commit in runs 2
  and 3 changed nothing. A low prior, about 1 in 15 (judgment); it would read
  all seven cores going down after the commit for the first time on T6000.
- **The engine's reconfig SRAM, read-only.** There is now a basis for T6000's
  address (inferred): PongoOS's table has T8012 and T8015 at AON + 0xf00000 and
  Naeem's T8103 SRAM is AON + 0xf00000 (0x292f00000 here, by the ADT's AON
  base), but T6000's AON isn't a same-offset copy (the commit is +0x5c4000 here,
  +0x2c4000 on T8103). PongoOS reads the table's offset from a config register
  at AON + 0x2c0200 on those SoCs, and the lock at +0x2c0214; by the commit's
  shift those would be 0x2925c0200 and 0x2925c0214, inside the ADT-declared
  window 0x2925c0000 + 0x14000, which makes them the safer first read
  (inferred). macOS's `carveout-memory-map` lists 0x292f00000 too, twice
  (region-id-29 and region-id-36, 0x70000 bytes), which no node's `reg` covers:
  something real sits at that address, though the ADT doesn't say what (their
  names are empty), and being listed doesn't make it safe to read: the map
  lists MMIO too (region-id-32 is 0x28e700000, on the never-write list).
  PLAN.md has the steps (Corey's OK of 2026-11-08; `e1-hook/recfg-peek`).
- **DJ's T8140 driver:** read, with Corey's OK of 2026-11-08 ("DJ's T8140
  driver, read", below). Not a rung.

### DJ's T8140 driver, read (2026-11-08)

Desk work, no hardware. Corey's OK of 2026-11-08 to read aurora-silicon/linux#1208
(head cd27afb6ccd0; DJ, DjDeveloperr; GPL Linux code), crediting them:
`drivers/soc/apple/t8140-sleep.c` and its CPU, PMP, RTKit, SPMI and MCC
pieces, read by three readers, then a synthesis and two adversarial checks of
every claim. No m1n1 and no Aura. The SIO, DCP, GPU, ANS, SMC and SEP drivers
weren't in the download. No T8140 code reads the engine, so what it says the
engine waits for is the author's statement or measurement, not something
this code shows.

What it adds, for T6000:
- **Coprocessors stopped before the commit.** The driver refuses a sleep
  unless DCP, DCPEXT, the PMP and SIO have stopped or are ready to
  (t8140-sleep.c:1239-1246, :1317-1325). Its comments and commit messages say
  a commit with the PMP and SIO still running "reset the SoC 5.5 s later"
  (:1229, d43118bcb473) and that a commit with the PMP running reset the SoC
  (pmp-export.c:153-155, 4f7e92c1fbe1): this Mac's symptom. Two passages pair
  them and two name the PMP alone; none describes a test with only one
  running. On this Mac the PMP has run un-idled through all four runs.
- **How it stops the PMP**, before its whole last mile (t8140-sleep.c:2575):
  report requests withdrawn first (pmp-report.c:456-481), SetAPPower 0x201 then
  SetIOPPower 0x201, once each, acks awaited (rtkit.c:2291-2295), then a poll
  of up to 5 ms for ASC status bits 1 and 5 whose result it ignores, the
  mailbox's receive off, and RUN cleared twice from fresh reads
  (t8140-sleep.c:761-772). PLAN.md's rung 3 has what this repo builds and what
  would need a new OK.
- **The display.** "The engine waits for DISP_CPU with no timeout"
  (t8140-sleep.c:1423, :1466; comments), and of AVE its comment says that turning
  it off at the commit would put "that sequence, with its untimed wait, right
  before the CPU complexes'" (:2614-2618; extending that to other complexes,
  such as this Mac's DISPLAY, is this notebook's inference). T8140 stops the
  display firmware and powers its CPU domain off
  first. DISPLAY's entry 58 is one of the two waits this Mac never meets (qto
  0x50). T6020's display-off runs still reset, so not sufficient alone; rung
  6 is the way to test it here.
- **The cores' shape:** awake through the commit, stop bits written first in
  the last mile, deep WFI after the one commit write (:2399-2400, :2469-2476),
  as hold=1 is, except that hold=1 writes them after the PMGR quiesce, not first,
  and spins where T8140 waits in WFE. A core that misses its stop holds the
  engine until WD2 resets the SoC (932de6dc2b1f, a measurement with a test
  switch not in the series). That doesn't explain runs 1 and 4, where CPU1-7
  read off before the commit.
- **The cluster power-gate word** (bit 31 set at boot, 3bd4bd1bf835: without it
  the boot CPU only clock-gates and the engine "never starts", a 5 s reset). Not
  this Mac's problem: CPU0's power word reads `was_pwrgated` at load (0x100 in
  run 2, 0x1f0 in run 3) and CPU0 read off after the commit in runs 2 and 3. 
- **Ranked low or not carrying over** (judgment): one commit write here against
  T6000's three; no SPMI near the commit, the alarm armed before the suspend;
  the AIC masked after the commit; WD2 armed by the driver with control 1
  (T8140 has an ADT property for it that T6000's lacks); the MCC step-down,
  the voters and CLLT, which aren't in T6000's table, whose coverage can't be
  checked; a list of 10 domains raised and 49 lowered to macOS's measured state.
  The SLC writeback (mcc.c:243-257) is for the return path, which a completed
  entry would need later.

Corey's next run stays the hold rung, as it was (its one-variable argument and
the fact that it was built and reviewed are unchanged). The analysis's judgment
for what's blocking T6000: the PMP running at the commit about 1 in 3 as at
least one blocker (raised from 1 in 5 by the author's statements, not by anything
the code shows), about 1 in 8 that stopping it alone completes the entry; the
display chain about 1 in 5; every core held about 1 in 12. Something no list
names, about 1 in 4, which is what the reconfig SRAM read is for. PLAN.md has
the order.

### M1 run 5: every other core held through the commit: all seven took the release and read off after it, and the SoC still resets at about 30 s (2026-11-08)

`e1-hook/run-floor --m1-hold` (`replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1
alarm=1 hold=1`, #43; the module built at 68a1e28; flag `radios-off`), in a safe
boot, on battery at 66% (`reading-summary.txt`). Against run 4 it has one change,
PLAN.md's rung 2: each other core writes its own stop bit and holds, spinning,
through the commit; CPU0 lets go after the third commit write; each core arms
ACC_OVRD's deep sleep (all seven, where run 4 armed only CPU4 and CPU7: e1_hook.c
L2124) and takes its deep WFI; and CPU0 reads them down and goes last. Raw data:
`results/m1-run5-20261108/`. `e1-hook/recfg-peek` read the engine's SRAM in the
same boot, less than a minute before the load ("The engine's reconfig SRAM",
below): a confound to state, though the load log's waits are unchanged from
run 4's.

The reading ended at 20:07:06 and was saved at 20:07:07. The Mac suspended and
restarted by itself into the regular boot (`boots.txt`: `uptime -s` 20:08:04),
whose journal has macsmc-reboot's "PMU logged 1 boot error(s) and 0 panic(s)",
as after run 4.

**The facts (each byte re-derived from e1_hook.c at 68a1e28):**
- **Stage 0x5e** (L1979): the run's last breadcrumb by design. CPU0 had read the
  others down, recorded everything and had nothing pending just before its acks
  and its own deep WFI. Nothing later is recorded.
- **The cores:** 0x6007 = 0xfe, CPU1-7 wrote their stop bit and held through the
  commit; 0x6012 = 0xfe, all seven took the release; 0x6005 = 0xfe, all seven
  read off after it, up to 10 ms each (L1947-1958); 0x6013 = 0, none had an
  interrupt pending just before its deep WFI; 0x6015 = 0, none at its stop
  request.
- **0x6014 = 0xb0:** PCPM0 and PCPM1 read off after the release (L1959-1962),
  CPU0 had nothing pending at step 8, WD1 was off at step 4, K3's write worked.
- **qto (0x6006) = 0x50:** the same two of macOS's 15 waits timed out as in runs
  3 and 4: DISPLAY's (entry 58) and SIO_CPU's (61).
- **0x6016 = 0xbd:** every alarm command acked, no event set, as in run 4.
- **The commit's second:** 208543, from 0x6010-0x6011 = 9f 2e (its low two bytes,
  all that stays from 0x5c on) and the alarm's compare value at the next boot.
- **The reset.** The SoC restarted by itself 30.0-31.3 s after step 8's second
  (three `m1-crumbs` reads of the same reset: 30.0-32.0, 29.6-31.6, 29.3-31.3;
  `boots.txt`, `crumbs.txt`). The five runs' windows still meet at 30.0-30.6 s
  (inferred, as for run 4: if the delay is the same each time).
- **The alarm survived the reset again:** at the next boot 0x1401 read 0x41 and
  0x140c 212143 (208543 + 3600). `m1-crumbs --clear` disarmed it and zeroed the
  breadcrumbs about 4 minutes after the commit, unfired.
- **The load log** against run 4's: `hold=1` is new; the waits, WD1's control
  and the nub-spmi0 words read the same; which idle cores read power state
  0x100 differs, as between any two boots. 0x140c read 188497 at load, run 4's
  compare value (disarming doesn't rewrite it).

**What they show:**
1. PLAN.md's expected case, byte for byte: every other core held through the
   commit with its stop bit written, took the release and read off after it,
   both P complexes went off, nothing was pending, and the SoC still reset at
   about 30 s. (That the entry itself didn't complete is inferred: see the next
   list.)
2. The first time on T6000 that all seven other cores are seen going down after
   the commit. That CPU1-7 and the P complexes went down says the hold didn't
   break their power-down.
3. With the stop bits written after the PMGR quiesce (step 5), as built. T8103's
   macOS writes them before it; that variant is unrun (no new register or byte).

**What the data can't tell apart:**
- CPU0's own last step and the E complex's power-down: 0x5e comes before CPU0's
  acks and WFI, and ECPM isn't read in this run (runs 2 and 3 read CPU0 and ECPM
  off, with CPU7 up).
- Whether CPU0's reading the others down matters (`--m1-hold-nowatch` is unrun).
- Which open condition holds the engine: no byte read the engine in this run.
- Whether the entry pair finished at all: a completed entry followed by a wake
  that stalls in a later pair would reset at the same 30 s ("The engine's reconfig
  SRAM", below).

**So:** with every other core held through the commit, the run still ends in the
reset at about 30 s. If the pair the decode below names is the entry, the decode
gives no reason the hold would have changed that: neither sequence touches an
address known to be a core's. But their first poll is an unidentified AON status
word (0x2922c0044, never read), and whether the engine needs the cores down below
those sequences is still open.

### The engine's reconfig SRAM, read and decoded (2026-11-08)

Read-only, with Corey's OK of 2026-11-08: `e1-hook/recfg-peek` stages 1-4, in run
5's safe boot at 19:56. Decoded at the desk: three independent walkers of
PongoOS's format (`recfg.h` and `recfg.c`, MIT, the format only), whose listings
of the four table-A sequences agree byte for byte, and two more walks of the
entry pair; a layout and an address map; a reading of the sequences as programs;
and two adversarial checks of every claim, whose corrections are folded in here.
No m1n1 code, scripts or traces, and no Aura. The dump and the op-by-op listings
stay out of this public repository (`results/m1-run5-20261108/recfg-peek.txt` has
the stage logs).

**The read worked.** 0x2925c0200, the analogue of PongoOS's table-offset register
by the commit's shift, reads 0x00001000; its lock's analogue 0x2925c0214 reads 0.
0x292f00000 (AON + 0xf00000) reads 0x10001000. At SRAM + 0x1000 are four words of
the form the format expects (each an address >> 4, inside the SRAM), and the first
64 KiB read back with no fault, in 16 KiB pieces. So the address inferred from
PongoOS's table, Naeem's T8103 SRAM and the ADT's carve-outs is right.

**What's in the 64 KiB** (the bytes):
- 0x0-0xfff: a small firmware image (a vector table, an `MCE FW` version string,
  a `fwsg` segment table; Cortex-M by the vector table's shape and its VTOR and
  NVIC writes, inferred). Reading its code, nothing in it addresses the tables or
  the sequences: it isn't what walks them, as far as the image shows.
- 0x1000 and 0x1100: two tables of four sequences each, called A0-A3 and B0-B3
  here. Each entry is an (address >> 4, 0) pair.
- 0x1020-0x19ff: parameter words and more pointer blocks. With the tables there
  are 45 distinct targets, 37 of them past the 64 KiB read.
- From 0x1a00: the sequences, in PongoOS's format, walking cleanly to their End
  words, apart from a word the checker rejects at the start of B0 and B1 (0x74;
  taking it as a prefix, the rest walks: inferred), a word at 0xa714 inside B1 that
  a strict walk rejects (the walk resumes if the 8 words after it are its payload:
  inferred), and B3, cut off by the read's end at 0xfff0.
- Table A's four sequences touch 35 addresses, with 57 reads, all of six words:
  the handshake word below (38), the watchdog page's 0x2922b801c (8), DCS (5),
  fabric (3), 0x28e20c400 (2) and one EACC word (1).

**Each table-A sequence has a table-B partner, in lockstep (the pairing is the
bytes, the lockstep inferred).** A_i writes phase codes to 0x2922c0110 (`PHASE`)
and handshakes on 0x2925c4004 (`HS_A`, the commit word + 4); B_i reads those codes
in the same order (B2 checks only 5 and 7, never 4; B0 checks 0 twice) and
handshakes on 0x2922c010c (`HS_B`). HS_A's bit 31 goes up and the sequence waits
for its bit 24 to rise, then lowers bit 31 and waits for bit 24 to fall. HS_B's
bit 1 goes up and the sequence waits for its bit 0 to fall, then lowers bit 1 and
waits for bit 0 to rise (that HS_A's bit 24 follows HS_B's bit 1 and HS_B's bit 0
follows HS_A's bit 31 is inferred). The sequences write HS_A's bit 31 and HS_B's
bit 1 and never read them, so whether they read back as written isn't shown. The
raises match where the dump shows them (6/6, 7/7, 3/3). What executes table B
isn't shown; the firmware at SRAM 0 isn't that, by its image.

| Pair | A | B | Phases | Contents (the bytes; "restores devices", "parks clocks" and "PMU command" are readings of them, inferred) | Its place (inferred) |
|---|---|---|---|---|---|
| 2 | 0x2000 | 0xf100 | 4 5 7 | A2 sets DCS and fabric to p-state 2 and 0x28e20c400 to 0, each waited for. B2 powers down the SIO subtree, PCIe, ANS and the PMP, parks clocks, sends one PMU command. | The entry |
| 3 | 0x21a0 | 0xfb40 | 6 9 | B3 checks all 16 DCS_nn words read off and the MCC words, then powers the fabric on. | First of the wake |
| 0 | 0x1a00 | 0x2300 | 0 | B0 resets SIO_CPU and the PMP (0x8f000244 then 0x80000000) and writes about 1,800 registers: PMGR's, the MCC's and other blocks (that some are PLLs or clock tunables is inferred). | Second of the wake |
| 1 | 0x1ce0 | 0x5920 | 2 8 c d a b 3 | B1 restores devices. A1 ends by bringing EACC up and writing 1 to 0x28e0d4008. | Last of the wake: that write would start the boot CPU, unlike Naeem's statement for T8103 (linux-s2r README L100-102) |

So awake → pair 2 → S2R → pair 3 → pair 0 → pair 1 → awake (inferred, from the
contents and from the state words B1, B2, B3 and B0 hand each other). Nothing read
so far shows what the commit's write selects: all four A sequences handshake on the
commit's own page, so that doesn't pick one. A completed entry followed by a wake
that stalls in a later pair (3, 0 or 1) would give the same 30 s, since each A
sequence re-arms WD2; that isn't ruled out either.

**The 30 s is the engine's own timeout.** Every table-A sequence opens by writing
WD2: control 0, count 0, bite 0x2aea5400 (720,000,000 ticks, 30.000 s at 24 MHz),
control 4 (RESET_EN), and closes with WD2's control := 0, the only stop (A2's is
at 0x2188). So the reset of runs 1-5 means some A sequence armed it and none
reached its close within 30 s (inferred: no byte has read the engine). It's what
the observers of runs 2 and 3 saw: control 4 with about 30 s to the bite, where
awake it reads control 0 and a 90 s bite. The notebook's "the commit loads WD2" is
that preamble (inferred).

**What the entry pair waits for, in order** (the order across A2 and B2, and that
A2 0x2058 waits on B2's raise, are the inferred lockstep; the op numbers are a
per-register count through B2; the offsets are exact):

| Where | What it waits for | On this Mac |
|---|---|---|
| B2 0xf100, 0xf114 | AON status word 0x2922c0044: bit 2 set, a delay, bit 3 clear | Never read. Everything past A2's first handshake waits behind this |
| B2 0xf134 | 0x2922c0040 bit 1 clear, after writing it 0 | Never read |
| A2 0x2058 | B2's first raise; then, A2's own: DCS := 2, fabric := 2, 0x28e20c400 := 0, each waited for (0x2074, 0x2090, 0x20ac) | DCS settled at target 5 with the PMP running (run 3). Fabric's value at the commit isn't read (Linux's noirq sets its target to 1). 0x28e20c400 never read |
| B2 op 50, 0xf2bc | SIO_CPU 0x28e580188, actual == f, after B2 writes 0x0f00024f | The leading candidate, below |
| B2 op 51, 0xf2cc | SIO's ASC word 0x39bc08000, bit 1 | Unknown |
| B2 ops 91-164 | bit 11 == 0 on SIO_CPU, the SIO subtree (SIO itself last), GPIO (op 128), PCIe, ANS, PMP and DWI, and bit 30 on the gate words, after the writes of 0 (B2 raises GPIO first, op 47 := 0x8000000f, with SIO, one of its ADT parents, off here; UART_N, APCIE_ST and APCIE_GP are written 0 with no poll) | Unknown. Bit 11 reads 0 on every power word in this Mac's three PMGR dumps (`results/sleep-peek-20261104T120619/`), but it was set on a raise that hadn't finished (gfx, 0xf000b4f, `safe-journal-20261106T201952.txt` L3810) |
| B2 ops 150-151 | ANS2 raised, actual f; ANS's ASC word 0x38f408000, bit 1 | Unknown |
| B2 ops 158-159 | ps_pmp actual f; the PMP's ASC word 0x28ec08000, bit 1 | ps_pmp reads target f, firmware running, unlike macOS at this point |
| B2 ops 177-241 | clock blocks' busy bits, AON words, the AIC domain, the PMU's SPMI status and ack | No known reason to fail |

B2 raises SIO_CPU with no write to SIO: its one write of SIO is op 137, := 0, and
SIO's only raise in what's been read is B1's, before SIO_CPU's. So the pair expects
SIO on. On this Mac genpd has SIO off by the replay
(`safe-journal-20261106T201952.txt` L5650, L5738; run 3 read SIO's actual off after
the commit, and SIO_CPU's actual hadn't reached f 10 ms after entry 61 in runs 3, 4
and 5). That makes op 50 the leading candidate for where the engine stops, about
4 in 10 (judgment: the pair is the entry about 85%, B2 gets that far about 75%,
unmet with SIO off about 90%, a miss blocks rather than falls through about 75%).
It isn't established: nothing has placed the engine there, and two earlier stops
are open (B2's gate about 1 in 7, A2's p-state step about 1 in 12, also judgment).
Cautions:
- What a failed compare does isn't in the format: the handshakes (and A2's three
  settle waits) have a zero count field and, for the handshakes, must block for the
  protocol to work (inferred). Op 50's count field is 0xff. Across all 619 reads of
  the eight sequences that field is one of 0, 5, 0xf, 0xff or 0xfff; PongoOS's
  `recnt` and "retry" are its low nine bits, with the three bits above "retry"
  (PongoOS's reserved bits 17-19) always equal to it (the bytes). Whether a non-zero
  count expires isn't shown.
- SIO on under macOS at this point is inferred from B2's structure and T6020's
  decode. The T6000 table can't show it: that trace ran under m1n1's hypervisor,
  which keeps SIO on ("What the trace has to answer", above).
- T8103 is the outside check: Naeem's completing entries only clear AUTO_ENABLE on
  SIO, SIO_BUSIF and SIO_CPU (apple-s2r.c L408-413), with no raise. Whether T8103's
  SIO was off at those commits isn't recorded.
- SIO alone may not be enough: Linux's device tree gives ps_sio_cpu four parents
  (ps_sio, ps_uart_p, ps_spi_p, ps_audio_p), genpd has the last three off too, and B1
  raises UART_P and SPI_P with SIO before SIO_CPU, leaving AUDIO_P at target 0.

B2 itself writes ps_pmp (0xf77c and 0xf7a8: 0x0f00024f, then 0), and B0 and B1 write
it on the way up. The engine drives that domain; the never-write rule stays for
the hook.

**What the entry pair doesn't touch.** A2 and B2 access no address known to be a
CPU-start word, a soc-cluster word, a core or complex power word, or a DCP register.
So, if this pair is the entry, no poll in these two sequences is known to depend on
the cores' order or on DISPLAY's pending bit (`qto` bit 4). Their first poll is an
unidentified AON status word (0x2922c0044), and whether it reflects the cores is
unknown. T8140's author says a core left awake at the commit keeps the engine
waiting until WD2 resets the SoC (t8140-sleep.c:807-809), without saying where that
wait sits, so this doesn't exclude a wait beneath these sequences.

**What it changes** (judgment; the order is in PLAN.md, and only there):
- The hold run changed no condition these sequences are known to poll (whether
  0x2922c0044 reflects the cores is unknown), and the same reset followed.
- `--m1-pmp` isn't the next run: unless the engine gets past op 50, or stops in A2's
  p-state step, which the PMP owns on this Mac, a reset from it says almost nothing
  about the PMP (ops 157-164, 107 ops after op 50).
- DJ's T8140 statement that "the engine waits for DISP_CPU with no timeout" has no
  counterpart in T6000's entry pair.
- A read of `PHASE`, `HS_A` and the state word after the commit, from a core still
  up, would say which sequence and round the engine is in while that core is up,
  which may not be where it stops with every core down, at no new write. PLAN.md
  has it.

### M1 run 6: the position read: the commit starts A2, which waits at its first handshake with B2 behind its first gate, and the SoC still resets at about 30 s (2026-11-09)

`e1-hook/run-floor --m1-position` (`replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1
alarm=1 hold=1 position=1`, #48; the module built at 00442f6; flag `radios-off`), in
a safe boot, on battery at 99% (`reading-summary.txt`). Against run 5 it has one
change: after reading the others down, CPU0 stays up, every exception masked, for
about 25 s, and samples where the engine is about 1 s and about 25 s after the third
commit. No new write and no new PMU byte. Raw data: `results/m1-run6-20261109/`.

`recfg-peek --position` read every word awake at 10:55:28, in the same safe boot, and
the module loaded at 11:17:31. The reading ended at 11:27:31 and was saved at 11:27:32.
The Mac went dark and restarted by itself into the regular boot (Corey's report;
`uptime -s` 11:28:28; "PMU logged 1 boot error(s) and 0 panic(s)", as after runs 4 and
5). `--position-after` read the words again at 11:33:50, after `m1-crumbs` and
`sleep-drain-safe collect`.

**The facts:**
- **Stage 0x45**, the run's last by design: both samples landed, and CPU0 had nothing
  pending just before its acks and its own deep WFI.
- **The cores, as in run 5:** CPU1-7 read off after the release, PCPM0 and PCPM1 off.
  `qto` 0x50: DISPLAY's and SIO_CPU's waits timed out, as in runs 3-5.
- **The restart:** 30.0-31.9 s after the commit (29.9-31.9 from `m1-crumbs`' first
  read, 30.0-32.0 from `--clear`'s). The armed alarm survived it and `--clear` disarmed it.
- **The two samples are byte for byte the same** (0x14 0xdc), and every word read as
  below. `HS_B` and 0x28e20c400 don't fit in the bytes.

| Word | Awake | About 1 s and about 25 s after the commit | Next boot |
|---|---|---|---|
| `PHASE` 0x2922c0110 | 0 | 4 | 0 |
| `HS_A` 0x2925c4004 | 0x00010000 | bit 31 up, bit 24 clear | 0x00010000 |
| State word 0x2922b801c | 0 | 0xa03 | 0 |
| WD2's control | 0 | RESET_EN set | 0 |
| WD2's count | running | within 0.25 s of CPU0's time since the first commit write | running |
| SIO_CPU 0x28e580188 | 0x1400024f | bits 27:24 f, actual not f | 0x1400024f |
| 0x2922c0044 | 0x8 | bit 2 clear, bit 3 set | 0x8 |
| 0x2922c0040 | 0x3 | bit 1 set | 0x3 |
| `HS_B` 0x2922c010c | 0x80000000 | not read | 0x80000000 |
| 0x28e20c400 | 0x222 | not read | 0x222 |

**What that shows:**
- **The commit starts A2, at once.** `PHASE` 4 is written only by A2, 0xa03 is a
  table-A sequence's opening, and WD2's count matches the time since the first commit
  write in both samples. "What the commit's write selects", open in the SRAM section,
  is answered for the A side: the entry pair's. The notebook's "the commit loads WD2"
  is now measured, as A2's preamble.
- **A2 reached its first handshake and stayed there,** bit 31 raised and waiting for
  bit 24, for at least the 24 s between the samples.
- **B2 didn't get past its opening segment.** 0x2922c0040 bit 1 still reads 1, and B2
  clears it at ops 4-6. 0x2922c0044 bit 2 is clear, which is B2's first poll. So B2 is
  at op 1 or hasn't started; these bytes can't tell which.
- **SIO_CPU's class places nothing.** 0x6005 bit 0 says bits 27:24 already read f at
  entry 61's wait (PLAN.md expected 4), and B2 is nowhere near op 49 anyway.
- **These words don't survive WD2's reset.** `PHASE` was 4 and the state word 0xa03
  about 5 s before the restart, and the next boot reads both back at their awake values.
  So a next-boot pass can't give a position, after this run or a plain `--m1-hold`.
- **The reset is still 30 s after the commit,** not 30 s after CPU0's deep WFI (which
  would be about 55 s). The sequences started at the commit, whatever CPU0 was doing.

**What it doesn't settle: whether CPU0 being up is what holds the gate.** A reset near
30 s can't say, since A2 arms WD2 before its first handshake. T8140's author says a core
left awake holds the engine until WD2 resets the SoC, and T8103's entries completed
only once every core reached its deep WFI. 0x2922c0044 bit 2 reads clear awake with
every core running, so the awake pass doesn't exclude a rollup of the cores, and the
same pattern (bit 3 set, bit 2 clear, then the reverse) is what a two-state rollup
would show. If the gate is the cores', run 6 stopped there because of the read itself,
and every earlier run got past it and stopped somewhere no read has reached: with
CPU0 up, or after the reset, nothing here can see that. If it isn't, it has been closed
in every run, and something else (DISPLAY, the PMP, or a state no list names) keeps it
closed. T8140's "the engine waits for DISP_CPU with no timeout" fits a first poll with
no timeout, but says nothing about this word.

**Checked at the desk, no hardware:**
- CPU0 writes no stop bit on purpose: macOS never stops the boot CPU through the PMGR
  (e1_hook.c L795-796), and T8103's deep stop writes each core's own bit.
- None of the replay's table writes page 0x2922c0000, so nothing the replay does
  addresses that gate directly.
- The sequences use that page as a bank of control words, most written and then polled
  on a status bit (B2's 0x40; 0x2c-0x3c, 0x48 and 0x4c in the others). 0x44 is only ever
  read: bits 2 and 3 by B2, bit 1 by B1 at 0x7b1c. What drives it isn't shown.

**What it changes** (judgment; the order is in PLAN.md, and only there): the position
read has reached the limit of what a core still up can see, and the next-boot pass adds
nothing. It confirmed the pairing and put a floor under the engine's progress, but it
can't choose a rung by itself.
