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

### M1 run 7: the combined rung: SIO's parents up and the PMP stopped, and the SoC still resets at about 30 s (2026-11-09)

`e1-hook/run-floor --m1-sio-pmp` (`replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1
hold=1 pmp_out=1 position=1 sio_on=1`, #50; the module built at ff3f208; flag
`radios-off`; the launcher also loaded `pmp_sleep_release` with 0xc and 0xd, "withdrawing
0x3000 for sleep" in the journal), in a safe boot, on battery at 75%
(`reading-summary.txt`). Against run 6 it has several changes at once, by Corey's pick:
SIO, UART_P and SPI_P raised just before entry 61; the PMP's report requests withdrawn at
noirq; K4, the PMP's idle pair and its CPU stopped; one position sample at about 1 s, and
CPU0 then down. Raw data: `results/m1-run7-20261109/`.

The module loaded at 14:18:27. The reading ended at 14:28:27 and was saved at 14:28:28. The
Mac went dark and restarted by itself into the regular boot (Corey's report; `uptime -s`
14:29:25; "PMU logged 1 boot error(s) and 0 panic(s)", as after runs 4-6).

**The facts:**
- **Stage 0xc5** (0x45 with K4's bit 7): the sample landed and CPU0 had nothing pending just
  before its acks and its own deep WFI.
- **SIO's raises:** 0x6007 = 0xff: all seven cores held, and bit 0 says ps_sio, UART_P and SPI_P
  all read actual f within 10 ms. **`qto` 0x10: SIO_CPU's wait at entry 61 was met for the
  first time on this Mac,** and only DISPLAY's wait timed out.
- **The PMP:** 0x6015 = 0x8e: the idle pair returned 0, CPU_STATUS's low bits read 10 (idle by
  macOS's mask; awake, over 19,299 reads, the low bits' AND was 0 and OR 1, so bit 1 is new), and RUN
  read 1 before the write and 0 after it. The first time the PMP's CPU has been stopped at the
  commit on this Mac.
- **The sample, about 1 s after the commit** (0x14 0xdd): `PHASE` 4, `HS_A` bit 31 up, bit 24
  clear, state word 0xa03, WD2 armed with its count within 0.25 s of the time since the first
  commit write, 0x2922c0044 bit 2 clear and bit 3 set, 0x2922c0040 bit 1 set. The same as run 6's
  except SIO_CPU's class, now bits 27:24 f with actual f.
- **The restart:** 30.0-31.5 s after the commit (30.0-32.0 from `m1-crumbs`' first read, 29.5-31.5
  from `--clear`'s). The armed alarm survived it, and `--clear` disarmed it.

**What that shows:**
- SIO_CPU's failed wait in runs 3-6 was its parents being off: with SIO, UART_P and SPI_P on, it
  settles, so B2's op 50 would now be met if B2 gets there.
- With SIO's subtree up and the PMP stopped, the engine at about 1 s with CPU0 up is where it was in
  run 6: A2 at its first handshake, B2's gate words at their awake values. Neither change opens the
  gate while a core is up.
- Neither change, nor both, ends the reset. The record stops at stage 0xc5, before CPU0's acks and its
  deep WFI, and the engine was sampled only at about 1 s. If CPU0's deep WFI took (no byte records it),
  the engine had about 29 s with every core down. A2's own WD2 is the likely source of the reset, but the
  30.0-31.5 s window doesn't exclude A2 closing after the sample and a later sequence re-arming WD2 within
  about the next half second, as in runs 1-5 (the SRAM section).
- DISPLAY is now the one macOS wait left unmet (`qto` bit 4).

**What it doesn't settle.** Where the engine stops with every core down: run 6's limit stands, and
this run adds no sensor past B2's gate. A stop past B2's gate, op 51 (SIO's ASC word) or later, would
look the same from here.

**What it changes** (judgment; the order is in PLAN.md, and only there): the two known mismatches
that could be fixed from the hook are gone, and the display chain is the rung left. T6020 had every
display domain off from its R6 on and still reset, so don't expect it alone to end the reset.

### M1 run 8: the display rung's first try: the DCPs didn't stop, so the display wasn't tested (2026-11-09)

`e1-hook/run-floor --quick --m1-disp-off` (`replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1
hold=1 pmp_out=1 position=1 sio_on=1 disp_off=1`, #53; the module built at 3b36234; flag
`radios-off`; the launcher also loaded `pmp_sleep_release` with 0xc and 0xd), in a safe boot, on
battery at 44%, with a 1-minute reading. Against run 7 it adds step K3b: both DCPs given the PMP's
idle pair, and, if they read idle, RUN cleared, then their domains off. Raw data:
`results/m1-run8-20261109/`.

**The facts:**
- **Stage 0xcd** (0x45 with K4's bit 7 and K3b's bit 3): the sample landed and CPU0 had nothing
  pending just before its acks and its own deep WFI.
- **K3b failed.** Sample 1's byte B, 0xd0: bit 4 set, an idle pair failed on at least one DCP;
  bits 0-3 clear, neither DCP stopped, so neither pair of domains was touched. The byte can't
  say which DCP refused, or how (a timeout, a mismatched ack, RTKit not running at that point); the
  load log, about a minute earlier, has both running. The PMP's idle pair, the same call on the same
  path, worked moments later (0x6015 = 0x8e), so the path is fine.
- **SIO and the PMP as in run 7:** 0x6007 = 0xff, the raises read on; `qto` 0x10, only
  DISPLAY's wait timed out, as K3b didn't take the display down.
- **The sample, about 1 s after the commit** (0x14): `PHASE` 4, `HS_A` bit 31 up, bit 24 clear, state
  word 0xa03; 0x2922c0044 bit 2 clear, bit 3 set; 0x2922c0040 bit 1 set. As runs 6 and 7.
- **The restart:** 28.9-30.7 s after the commit (28.7-30.7 and 28.9-30.9 from the two reads).
  The armed alarm survived it, and `--clear` disarmed it. Corey's report: the usual restart.

**What it shows.** Nothing about the display: DISPLAY's domains stayed on and its wait failed as in
every earlier run. It shows the idle pair isn't a way to stop these DCPs at that point, at least
one of them. JJRPF's DeepIdle `test_sleep_off` (GPL-2.0) stopped all three of T6020's DCPs with
`apple_rtkit_shutdown()` and CPU_CONTROL := 0, at `dcp_platform_suspend`, with the domains then
off under genpd (`data/s2r-domains/2026-11-06-2119-s6-dcp-off-held.txt`), and Linux's NVMe suspend
does the same. The next try uses that, at the same point in the suspend, before noirq and `slpa`.

### Related work, swept 2026-11-09

A read-only sweep of the related projects after run 8 (no m1n1 and no Aura; conclusions reported
from hypervisor traces are marked). What's new or was missing here:

- **T8132 (M4 Air, J713) completes S2R from Linux**, a second chip through the engine: niteria
  (Bartosz Nitka), aurora-silicon/linux#1314, a draft stacked on DJ's #1208 and #1059. 4 of 4 sleeps
  on one boot, a 6 h alarm sleep on time, about 0.09 W over 9.08 h (Wi-Fi and Bluetooth off). Commit
  c058e736840e lists what it took beyond DJ's port: the unbound external displays lowered too, the
  SoC perf state and DCS brought down before the memory controller stops, what the wake reads cleaned
  to the point of persistence, and a stub that restarts WD2 for 60 s on entry. It boots through
  Aura, which we can't use.
- **DJ's DCP stop is readable now** (aurora-silicon/linux c922a676a197): set_power_state(0), a wait
  for the display DART's streams, A500 with the firmware's callbacks answered, RTKit idle with a
  retry, CPU status polled for IDLE and STOPPED, the mailbox off, RUN cleared twice ("the run bit is
  never cleared under running firmware"). niteria's J713 stop (#1059, eb39666ebf6c) is the same shape,
  204 power cycles in one boot. Two stated reasons a firmware refuses idle: its secure RTBuddy port
  (f7edd90a82d1; this Mac's ADT has none) and OSLOG counters left unechoed (9bacbcadf516; T8140 only
  so far). Hector Martin's 2022 AsahiLinux/linux dcp/sleep stops a DCP with setDCPPower(0), then
  RTKit idle, then RUN off (5aadf1fa9115); aurora still has the path, `dcp_sleep()`, unused.
- **DJ on the engine's first step**, T8140 (#1208): "The engine's first step polls for DCS at this
  state and has no way to get it there": macOS has every voter at 0 by then, and the driver zeroes the
  PMC voters and steps DCS itself. It doesn't identify our 0x2922c0044.
- **Naeem Malik's run 11** (malik-na/m1-power-lab 3b942a82a9d8, 2026-11-08): global sleep on T8103,
  the secondaries holding in WFE through the commit, the same no-wake: "the core order is not what
  keeps the boot CPU from restarting". His macOS 27 notes (conclusions from his traces) have
  DCP/DCPEXT told the 0x201 pair before slpa. His driver doesn't touch the DCPs.
- **DeepIdle is paused on a filesystem error**: its status says "Work paused due to FS error", and a
  btrfs superblock rescue kit went up on 2026-11-08. The cause isn't stated; a forced reset is an
  inference. It's the reason this repo's runs stay in a safe boot.
- **A risk to reading the display rung** (DeepIdle's worklog L2596-L2598, from DJ's T8140 work): with no
  platform error handler, an enabled and pending memory-controller error at the commit wakes the SoC
  within 20 ms, and display power-off raises DSID_AGE_OUT. T6020's R8 cleared the enables and still
  reset. An early wake or restart after the display goes off could be this, not engine progress.
- **RJ Skerry-Ryan's apple-s2r on T6021** (rryan/linux 7b89ae23dfc6, October), not in these notes:
  a full Linux enter order, with DISPLAY handled as a SoC cluster and AFR skipped while the display
  system domain is up. No completed entry is stated. Dylan Ironside's T8132 port is active.
- **The t600x alarm is merged upstream of aurora** (aurora-silicon/linux#1312), with Maverick's fix for
  the hypothesis from #1225 here: control bit 1 latched stale, so arming as 0x43 latched no new edge.
- Watch aurora#401, which would keep a third DCP powered through sleep if it reaches this kernel.

### M1 run 9: the display off: every macOS wait met for the first time, and the SoC still resets at about 30 s (2026-11-09)

`e1-hook/run-floor --quick --m1-disp-off` (`replay=15 oneway=1 quiet=1 m1=1 pmgr_late=1 alarm=1 hold=1
pmp_out=1 position=1 sio_on=1 disp_off=1`, #54; the module built at 3674f02; flag `radios-off`; the
launcher also loaded `pmp_sleep_release` with 0xc and 0xd), in a safe boot, on battery at 33%, with a
1-minute reading. Against run 8 it has one change: the DCPs shut down through RTKit at the first
suspend's `prepare`, before noirq and `slpa`, not given the idle pair after `slpa`. Raw data:
`results/m1-run9-20261109/`.

**The facts:**
- **Stage 0xcd:** the sample landed and CPU0 had nothing pending just before its acks and deep WFI.
- **The display:** byte B 0xdf: both DCPs' RTKit shutdowns returned 0, RUN read 1 then 0 on both,
  and all four domains read off.
- **`qto` 0x00: every one of macOS's waits was met within 10 ms, for the first time,** DISPLAY's
  (entry 58) among them. T6020's display-off runs never recorded that bit, so as far as these notes
  go it's the first time on any chip.
- **SIO and the PMP as in run 7:** the raises read on, SIO_CPU settled, the PMP's CPU stopped
  (0x6015 = 0x8e).
- **The sample, about 1 s after the commit** (0x14): `PHASE` 4, `HS_A` bit 31 up, bit 24 clear,
  state word 0xa03; 0x2922c0044 bit 2 clear, bit 3 set; 0x2922c0040 bit 1 set. The same as runs 6-8.
- **The restart:** 29.7-31.4 s after the commit (29.4-31.4 and 29.7-31.7 from the two reads). Corey's
  report: about 30 s of black, then the restart. The armed alarm survived it, and `--clear` disarmed it.

**What that shows:**
- DISPLAY's power-down settles once both DCPs are stopped and their domains off, so the display
  chain was what held that wait.
- With the PMP stopped, SIO's subtree up, the display off and every macOS PMGR wait met, B2's gate
  words at about 1 s, with CPU0 up, are where they were in run 6. None of these opens it while a core is up.
- No early wake, so the memory-controller error "Related work" warned of didn't show here.
- The ending doesn't change. As in run 7, the record stops before CPU0's acks and deep WFI, so
  about 29 s with every core down is a reading, and a close of A2 after the sample and a re-arm
  within about the next 1.4 s isn't excluded. It matches T6020, which had every display domain off
  from R6 on and still reset.

**What it changes** (judgment; the order is in PLAN.md, and only there): this is the first run with
every known PMGR-level difference from macOS gone. What's left is what the working chips do that this
recipe doesn't, and they can now be read: the desk comparison comes before more rungs.

### M1 run 10: the commit written once and the SMC told: the same reset (2026-11-09)

`e1-hook/run-floor --quick --m1-macos-end` (run 9's parameters and `one_commit=1 smc_idle=1`, #56; the
module built at b157386; flag `radios-off`; the launcher also loaded `pmp_sleep_release` with 0xc
and 0xd), in a safe boot, on battery at 72%, with a 1-minute reading. Against run 9 it has two changes:
the commit written once, as macOS's T6000 table has it, and SetAPPower 0x201 alone to the SMC after K4.
Raw data: `results/m1-run10-20261109/`.

**The facts:** stage 0xcd. 0x6005 bit 0: the SMC acked the AP idle notice within 200 ms. `qto` 0x00, every
macOS wait met, as in run 9; both DCPs stopped and their domains off (byte B 0xdf); SIO's raises read on;
the PMP's CPU stopped (0x6015 = 0x8e). The sample at about 1 s (0x14) is as in runs 6-9: `PHASE` 4, `HS_A`
bit 31 up, bit 24 clear, state word 0xa03; 0x2922c0044 bit 2 clear, bit 3 set; 0x2922c0040 bit 1 set.
The restart: 29.8-31.7 s after the commit (29.8-31.8 and 29.7-31.7 from the two reads). Corey timed
it at about 30 s of black. The armed alarm survived it, and `--clear` disarmed it.

**What that shows:** a single commit write is enough to start the sequence: A2 was at its first
handshake with one write as with three. The SMC takes the notice and answers it here. Neither moved the
gate words at about 1 s with CPU0 up, or the reset.

### The working drivers against this recipe (2026-11-09)

A desk comparison of run 9's recipe with the two Linux entries that complete, DJ's (Diljit Singh)
T8140 driver (aurora-silicon/linux#1208, `t8140-sleep.c` at 5b30b8ddbad6) and niteria's (Bartosz Nitka)
T8132 (#1314, at f745c6707845), with Naeem Malik's T8103 driver and maralcbr's J314s trace for this
Mac. A subagent's reading: the two facts Corey's next choice turns on were checked against this repo
(no code here touches SIO's ASC or RTKit; `sio_on` needs `position`), the rest wasn't. T8140 and T8132
facts may not carry over. The odds are judgment, each as "is a blocker", not "fixes it alone".

- **SIO's firmware stopped, about 1 in 4.** Both drivers refuse a sleep unless the DCPs, the PMP and SIO
  have stopped, and a T8140 comment says a commit with the PMP and SIO running "reset the SoC 5.5 s later",
  this Mac's symptom with T8140's shorter WD2. DJ's ae8686a0bcf4 sends the idle pair, turns the mailbox's
  receive off and clears RUN twice, about 64 ms before the commit by macOS's timing on T8140. Here
  `apple-sio` runs its firmware (run 9's journal) and aurora's `sio_suspend` is a stub ("TODO: SIO coproc
  sleep state"), so genpd cuts its power at noirq with the firmware running, and `sio_on` only brings the
  domains back. B2's ops 50-51 are where it would bite, past the gate, so the 1 s sample wouldn't show
  it. Against: no T8140 passage tests SIO alone, and T6020 sent SIO messages and still reset.
- **CPU0 straight to its deep WFI with this recipe, about 1 in 6.** `sio_on` needs `position`, so
  runs 7-10 all kept CPU0 up about 1 s after the commit. DJ's comment gives "under 33 ms from the complex
  words to the WFI (the engine's bound for the cores to go idle)", T8140 and unmeasured there. No new
  address, but it loses the sample.
- **The internal PCIe ports down, about 1 in 6.** DJ's `t8140_apcie_port_stop`; macOS does it on this Mac
  between `slpw` and `slpa` (maralcbr's `lab_pcie_ports_down`). New register writes, about 13 per port.
- **Every power word at macOS's state, about 1 in 6.** Both drivers bring the domains to a measured macOS
  state. This Mac's list is in RESEARCH's power-state notes (`lab_ps_off`): AFNC1_LW2, AFNC2_LW1, APCIE_GP_SYS,
  PCIE0_REF, ATC COMMON, PCIE and USB_AON, five UARTs, MSG. So "the known differences at the PMGR level
  are used up" was true of macOS's waits, not its power words.
- **Lower:** the stop bits first (about 1 in 10), the SEP told (1 in 10; it already reads macOS's state here),
  an MCC step-down (1 in 10, no T6000 source), the AOP told (1 in 12), the PMP's third request 0xe (1 in 20).
- **Not real or not carrying over:** DJ's display-off preamble (run 9's shutdown met DISPLAY's wait without
  it), the PMC voters and DCS step (the PMP manages those here and A2 writes them), CPM gates, the AMCC enables
  (no early wake here), T8132's WD2 resume stub (wake side).

### M1 run 11: SIO's firmware stopped and CPU0 straight down: the same reset (2026-11-09)

`e1-hook/run-floor --quick --m1-sio-stop` (run 10's parameters, with `hold_nowatch=1` in place of
`position=1`, and `sio_stop=1`, #58; the module built at 85c695b; flag `radios-off`; the launcher also
loaded `pmp_sleep_release` with 0xc and 0xd), in a safe boot, on battery at 54%, with a 1-minute reading.
Against run 10 it has two changes, as Corey chose: SIO's RTKit idle pair and a RUN clear at the first
`prepare`, and CPU0 sent to its deep WFI as soon as it has let the others go after the commit, with no sample.
Raw data: `results/m1-run11-20261109/`.

**The facts:** stage 0xcf, the run's last breadcrumb by design. 0x6012 = 0xff with its marker at 0x6013:
both DCPs stopped and their domains off, the SMC acked its notice, and SIO's firmware stopped (the idle
pair returned 0, and RUN read 1 before the clear and 0 after it). `qto` 0x00, every macOS wait met; all
seven other cores wrote their stop bit and held; SIO's raises read on; the PMP's CPU stopped
(0x6015 = 0x8e). The restart: 29.8-31.3 s after the commit (29.8-31.8 and 29.3-31.3 from the two reads).
Corey counted about 30 s of black. The armed alarm survived it, and `--clear` disarmed it.

**Not recorded:** with `hold_nowatch=1` nothing is written after stage 0x4f, so neither the deep WFIs nor the
engine's position were observed in this run.

**What that shows:** the two leads rated highest in the comparison above didn't move the reset, in the form
tested. SIO's stop here came at the first `prepare`, well before the commit, and without the mailbox step
and second RUN clear of DJ's T8140 driver, so "SIO doesn't matter" goes further than this run does. Sending
CPU0 straight down ends the same way as keeping it up about 1 s (runs 6-10), but with no sample this run
can't say whether the engine got further.

**Next, Corey's OK of 2026-11-09 (built as `--m1-power-words`, not yet run):** the remaining power words at macOS's state, in one run. From
maralcbr's `lab_ps_off` (`drivers/power/reset/macsmc-reboot.c` at 37be2668, GPL-2.0, macOS's values on a
J314s), leaving out what's handled or already matches (the four display words `disp_off` turns off,
DISPEXT1's two, SEP, NUB_SPMI1, AFNC3_LW0), the PMP's word, which is never written, and PMS_SRAM.
20 words:

| Value | Words |
|---|---|
| 0x1000030f | AFNC1_LW2 0x28e080220, APCIE_GP_SYS 0x28e080320, AFNC2_LW1 0x28e580160 |
| 0x1400024f | PCIE0_REF 0x28e080138 |
| 0x00000300 | ATC1-3_COMMON 0x28e080240, 0x28e0802f8, 0x28e080300; ATC0-3_PCIE 0x28e080330, 0x340, 0x350, 0x360; ATC1-3_USB_AON 0x292280090, 0x98, 0xa0 |
| 0x00000200 | UART2, 3, 4, 6, 7 0x28e580250, 0x258, 0x260, 0x268, 0x270; MSG 0x292280078 |

macOS has the internal PCIe ports down before it gets here, and this recipe doesn't, so the PCIe and fabric
words are the ones most likely to behave differently here. maralcbr's own table has these words as macOS's
state at sleep entry; whether their runs wrote this exact set isn't established here.

### M1 run 12: macOS's remaining power words: the same reset (2026-11-09)

`e1-hook/run-floor --quick --m1-power-words` (run 11's parameters and `ps_words=1`, #60; the module built at
0c28390; kernel 7.2.11-14; flag `radios-off`; `pmp_sleep_release` with 0xc and 0xd), in a safe boot, on
battery at about 40%. Raw data: `results/m1-run12-20261109/`.

**The facts:** stage 0xcf. 0x6012 = 0xff as in run 11 (both DCPs, the SMC's ack, SIO's firmware stopped), `qto`
0x00. 0x6013 = 0xdf: the sixteen target-0 words (UARTs, MSG, ATC USB_AON, PCIE and COMMON) all read actual 0
within 10 ms; of the four fabric and PCIe words, at least one didn't read back its target and auto fields as
written (one bit for the group, so which isn't known). The restart: about 30 s by Corey's count. `m1-crumbs`
couldn't time it: a system update restarted the Mac once more before the read (`boots.txt`; the journal puts
it at roughly 28-34 s). The alarm was disarmed by `--clear`.

**The kernel changed after this run:** linux-aurora 7.2.11.aurora14 to aurora15 (2026-11-09 20:17). The module
needs a rebuild, and later runs compare against this one across a kernel update.

**What that shows:** sixteen more domains at macOS's state didn't move the reset. Twelve runs have now ended
the same way, the last two with nothing able to see past the commit.

### A different approach: instrument the engine instead of guessing (2026-11-09; step 1 has Corey's OK of that day, the rest is proposed)

Every rung since run 6 has been a guess at what the engine waits for, at about 1 in 6 each, and the only
observer, a core left up, may itself hold the gate. The one thing that survives a run is the time of the
reset, and the engine's own sequences set it: each A sequence writes WD2's bite (0x2aea5400, 30 s) from the
reconfig SRAM. PongoOS's `recfg.c` exists to modify these sequences, and the table's lock analogue reads 0
here. So, if the SRAM is writable and the engine runs it live:

1. **Feasibility, one run:** change A2's bite word alone to 45 s. Right after the commit, before its deep
   WFI, CPU0 reads WD2's bite and `PHASE` once and records them (with `HS_A`'s two bits, and whether the
   SRAM word still read the new value just before the commit). No reset time enters the test:
   - **the new bite in WD2** says the SRAM is writable and that the engine ran the changed word. That's
     the feasibility result, whenever the reset then comes;
   - **the old bite while `PHASE` reads 4 or 5**, A2 before its last phase, says A2 itself armed WD2 from
     something other than the word as it reads: the edit isn't what the engine runs. Only this rejects
     the approach;
   - **the old bite with any other `PHASE`** decides nothing about the edit: a later A sequence may have
     re-armed WD2 before CPU0 looked. That includes 7, A2's last code, which stays until the next
     sequence writes its own, and that sequence's opening re-arms WD2 first (Pullfrog's point). A later
     sequence that soon would be news of its own: the entry finished within milliseconds;
   - **the awake bite** says A2 hadn't armed WD2 yet, and decides nothing.

   The reset time is a second fact, read with those (Pullfrog's two points on #61). Every A sequence
   zeroes WD2's count and arms it with its own bite, so the reset comes one bite after the last sequence
   to start: at 45 s if that's A2, at t + 30 s if a later A sequence started t after the commit. So:
   - an unchanged reset near 30 s is inconclusive by itself. With the new bite read it fits a later
     sequence arming right after the commit (the entry completing and a wake stalling, the case the SRAM
     section leaves open) or a reset from something else. It's no evidence against the edit;
   - a reset at 45 s fits A2 as the last to arm, the entry never finishing with every core down, and
     equally a later sequence that started 15 s in. A second run with another bite tells them apart (the
     reset follows A2's bite or it doesn't), with no other word changed;
   - any other time T fits a later sequence that started at T - 30 s.
2. **Then markers:** a write of a different bite placed at chosen points (in place of an unpolled write,
   or in a relocated copy of a sequence with the table repointed). The reset time then says how far the
   engine got with every core down: the first measurement past the gate. The same caution holds: a later
   A sequence's preamble overwrites a marker's bite and restarts the count, so a marker's reset time alone
   doesn't name the op the engine stopped at. It's read against step 1's result and against the same
   marker at another bite, and the other A sequences' bites can be given values of their own to say which
   armed last (more SRAM words, each needing the OK). Whether a marker can go in B2 depends on what executes table
   B, which isn't known; one in A2 right after its first handshake needs no such knowledge, and says
   whether B2's first gate ever opens.

New writes to the engine's SRAM: Corey's OK first, word by word. Undone by a restart if iBoot rewrites the
SRAM at boot, which a read of the word in the boot after step 1 confirms (inferred until then).

**Step 1 as built** (`run-floor --m1-recfg-bite`, not yet run). A2's bite is A2's fifth word, SRAM +
0x2010 (0x292f02010): like every A sequence, A2 opens with one Write32 op of four writes on the watchdog
page, WD2's control, count, bite and control, and the old bite is in the first 64 KiB four times, once
per A sequence. The tools know the place by ten words, built from PongoOS's format and WD2's register
map and checked against the dump here, not copied from it: the table offset register's analogue (0x1000)
and its lock (0), table A's entry 2 (SRAM + 0x2000, as an address >> 4), and that op's six words. After
the write all ten are read again, so a neighbour that changed with the bite is caught at the desk and not
as a Mac that stays dark. Nothing writes the old value back by itself: the OK is one write, and
`recfg-bite --restore` is there for a person to run.

The sample changed in the building, for two reasons the design's critics gave. CPU0 now takes it after
it has let the other cores go, as in every sample of runs 6-10, and waits for WD2's RESET_EN first, up
to 0.3 s: run 6 only bounds A2's arming to within 0.25 s of the commit, and a look a fraction of a
millisecond after it could have come before A2's opening, or inside it. And the classes are numbered so
that the byte stage 0x4e leaves in the sample's place can't read as the new bite. The byte has no room for
`HS_A`'s two bits, which the proposal above named: it holds the bite's class, `PHASE`'s low four bits,
whether RESET_EN showed, and whether the SRAM word still read the new value in step 8.

Two readings were tightened by the build's reviews. The old bite with `PHASE` at 4 or 5 is the rejection
only if RESET_EN showed too, the state A2's opening leaves; without it the record decides nothing. And
the new bite without RESET_EN means a WD2 that never bites only while `PHASE` reads 0, 4 or 5: past
those, no RESET_EN is how a finished A2 looks, since every A sequence clears it at its end, and a Mac
that then stayed dark could be a completed entry.

### M1 run 13: the feasibility run: the SRAM didn't take the write (2026-11-09)

(Run 14, below, repeated this with one 64-bit store, and that was dropped too.)

`e1-hook/run-floor --quick --m1-recfg-bite` (#62, built at 7dc57fd; kernel 7.2.11-15; flag `radios-off`), in a
safe boot, on battery at about 95%. Raw data: `results/m1-run13-20261109/`.

**The facts:** `recfg-bite`'s check read the ten words as on 2026-11-08 (the offset register's analogue
0x1000, its lock 0, table A's entry 2 pointing at A2, A2's opening with the bite at 0x2aea5400) and was
saved. `recfg_poke` then made its one store, 0x405f7e00 to 0x292f02010 (an aligned 32-bit `str` through
`/dev/mem`, then `dsb sy`), and read all ten again: each read as before, the bite still 0x2aea5400. It said
"THE WRITE DIDN'T TAKE" and exited 5, with no put-back (none was called for: nothing had changed). run-floor
stopped there: e1_hook wasn't loaded, and there was no reading, no suspend and no reset. The safe boot's
saved journal has no kernel message about the access: the store was taken without a fault and changed
nothing. `recfg-bite --after` wasn't run: the write record says the word never read 45 s, so its gate
refuses, and there's nothing for it to measure.

**What that shows:** this SRAM doesn't take a store from the AP under Linux, at least not a 32-bit one at
this address. A silent drop fits a lock set before Linux runs, as iBoot sets PongoOS's range lock on
A9-A11 (inferred: PongoOS stops before writing a locked SRAM, so what a store then does isn't in its
code). The store's width is a weaker suspect: PongoOS edits a Write32 value in place with a 32-bit
volatile store (`datap[i] = data`), which works there when the SRAM is unlocked. A write filter in front of
the SRAM for the AP isn't excluded either. Judgment: a lock, about 70%; the width, about 15%; something
else, about 15%.

So step 1 of "A different approach" failed in the way PLAN.md's reading guide gave first ("the SRAM is
locked against a write from here. Not a run"), and the markers of step 2 need a writable SRAM. Twelve
resets are still the only data past the commit.

**The width gets one test (Corey's OK of 2026-11-10; #64; not run yet):** `recfg_poke` now makes the same
change as one aligned 64-bit `str` at 0x292f02010: 0x405f7e00 in its low half and, in its high half, the 4
that 0x292f02014 reads (the value A2 gives WD2's control), so the bite is still the only word meant to
change. The reads stay 32 bits, as they were when they worked. If that store is dropped too, the width is
out and a lock is near certain.

### M1 run 14: the write as one 64-bit store: dropped too (2026-11-10)

`e1-hook/run-floor --quick --m1-recfg-bite` (#64: `recfg_poke` built at 4b733d2, the module still 7dc57fd's;
kernel 7.2.11-15; flag `radios-off`), in a safe boot, on battery at 98% or more. Raw data:
`results/m1-run14-20261110/`.

**The facts:** `recfg-bite`'s check read the ten words as in run 13 and on 2026-11-08, and was saved.
`recfg_poke` then made its one store, 0x00000004405f7e00 at 0x292f02010 (an aligned 64-bit `str` through
`/dev/mem`, then `dsb sy`: 0x405f7e00 for the bite and 0x00000004 for the word after it), and read all ten
again: each read as before, the bite still 0x2aea5400 and the next word still 4. It said "THE WRITE DIDN'T
TAKE" and exited 5, with no put-back. run-floor stopped there: e1_hook wasn't loaded, and there was no
reading, no suspend and no reset. The safe boot's saved journal has no kernel message about the access.
`recfg-bite --after` wasn't run: its gate refuses, as after run 13.

**What that shows:** the width isn't why. A store to this SRAM from user space under Linux is dropped
without a fault at 32 bits and at 64, so the sequences can't be edited the way these two runs tried, and
the markers of "A different approach" are off. The likeliest reason by far is write protection set before
Linux runs, a lock like PongoOS's range lock or a filter in front of the SRAM (which of the two can't be
told from here, and nothing read so far shows a way to undo either from Linux).

**What the two runs lack: a control.** Nothing in this repository has ever shown that a store made the way
`recfg_poke` makes it, from user space through a `/dev/mem` mapping, changes any register on this Mac:
every other tool here that maps `/dev/mem` only reads, and every write that has been seen to land (the
PMGR words, the stop bits) was the module's, from the kernel. A bus that drops a store from EL0 and takes
it from the kernel would look exactly like these two runs. That's unlikely (judgment), but it's cheap to
rule out, with one store of the same kind to a register that's known to take the kernel's writes on the
same block. Judgment: the SRAM is write-protected, about 9 in 10; the store's path, about 1 in 15;
something else, the rest.

**What's left of the plan without an SRAM write.** The reset's time is still the only thing that
survives a run with every core down, and it can be moved without touching the sequences: WD2's bite is a
register (0x2922b0024; upstream's `apple_wdt.c` names it, `APPLE_WDT_WD2_BITE_TIME`, and uses only WD1),
and A2 writes it once, in its opening. A core that changes it after that opening changes when A2's WD2
resets the SoC, and nothing else: every later A sequence writes its own 30 s and zeroes the count, and
every A sequence clears RESET_EN at its close. So with the bite moved to 120 s by CPU0 right after the
commit:
- a reset about 120 s after the commit fits A2 arming last and never closing, an entry that's stuck and
  not slow. It doesn't prove it: A2 closing and a later A sequence starting at about 90 s would reset
  at the same time, the collision the 45 s proposal had at 15 s. Nothing gives a reason for a sequence
  to start just then, so it's the far less likely reading (judgment), and a second run with another bite
  settles it: the reset follows the bite, or it doesn't;
- no reset is a candidate for A2 closing, an entry that completed, which no run has shown. It's no more
  than a candidate, because the reset that's missing is the one this write changes: the write may itself
  have kept WD2 from biting, and a read-back says the register took the value, not that it still counts
  or bites. What would stand beside it: CPU0 reading, after its write, that RESET_EN is still set and
  the count still moves; the battery's drain over the dark stretch; and a run that does reset at its
  moved bite, which shows a moved WD2 still bites;
- a reset at about 30 s says a later A sequence re-armed WD2 within a second or two of the commit, the
  completed entry and stalled wake the SRAM section left open, or that WD2's bite isn't compared live.
  WD1's is: `apple_wdt` sets its timeout by writing the bite with the watchdog running, and restarts the
  machine by writing a bite of 0 with RESET_EN set. That WD2, the same block's third watchdog, behaves the
  same is inferred;
- a reset at another time T says a later sequence started T - 30 s after the commit.

That's step 1's reading with the engine's own program left alone, with step 1's limits: one run's reset
time narrows the cases and a second bite value decides between them. It's one new register write. WD2
has been on e1_hook's never-written list so far, so it needs Corey's OK. PLAN.md has the order.

Corey's OK came on 2026-11-10, for that register and value at those two moments, and it's built (#66,
`run-floor --m1-wd2-bite`). It ran the same day: M1 run 15, next.

### M1 run 15: WD2's bite moved to 120 s, and the reset follows it (2026-11-10)

`e1-hook/run-floor --quick --m1-wd2-bite` (run 12's parameters and `wd2_bite=1`, #66; the module and
`wd2_poke` built at bbc8156; kernel 7.2.11-15; flag `radios-off`; `pmp_sleep_release` with 0xc and 0xd), in a
safe boot, on battery at 76%, with a 1-minute reading. Against run 12 it has two changes, both Corey's OK of
2026-11-10: one store to WD2's bite from user space before anything is loaded, and one by CPU0 after the
commit. Raw data: `results/m1-run15-20261110/`.

**The control, awake:** `wd2-bite`'s check read WD2's control 0, its count running and its bite 0x80befc00
(90 s), and was saved. `wd2_poke` stored 0xaba95000 to 0x2922b0024 through `/dev/mem` (one 32-bit `str`,
then `dsb sy`) and read the three words again: the bite 0xaba95000, the control still 0. So a store made
from user space the way `recfg_poke` makes its own does change a register here. Runs 13 and 14 stand as
read: the SRAM's refusal is the SRAM's.

**The facts:**
- **The load:** WD2 read control 0 and bite 0xaba95000 awake, the control's value, and `PHASE` 0.
- **Stage 0xd7** (0x57 with K4's bit 7), the run's last by design: the sample landed, and CPU0 had nothing
  pending after its reads, just before its acks and its deep WFI.
- **The sample, 0x6005 = 0x7d:** WD2's control showed RESET_EN within 0.3 s; the bite then read 30 s, an A
  sequence's own; CPU0 wrote 120 s; the bite read 120 s after, RESET_EN was still set, and the count
  was changing; `PHASE` read 4, A2's first.
- **The rest as in run 12:** `qto` 0x00, every macOS wait met; both DCPs stopped and their domains off, the
  SMC's ack, SIO's firmware stopped (0x6012 = 0xff); the sixteen target-0 power words read actual 0 and
  the fabric and PCIe group didn't all read back as written (0x6013 = 0xdf); the PMP's CPU stopped
  (0x6015 = 0x8e); all seven other cores wrote their stop bit and held.
- **The restart: 120.0-121.3 s after the commit** (119.3-121.3, 119.8-121.8 and 120.0-122.0 from three
  reads in the first boot). Corey: dark for two minutes, then the restart. The armed alarm survived it,
  and `--clear` disarmed it.

**What that shows:**
- **The reset moved, for the first time, and it went where the bite was put.** The twelve runs that
  reached the commit before this one all reset at about 30 s (runs 13 and 14 never suspended). WD2's bite
  is writable from the AP, by the kernel after the commit and from user space awake, and it's compared
  live: the count ran on from A2's arming and the SoC reset when it reached the new bite. So it was WD2
  that ended this run, as it was taken to end the twelve earlier suspend runs.
- **A2 armed WD2 at the commit, and no A sequence started after it, unless one did 90.0-91.3 s in.**
  `PHASE` read 4 after CPU0's write, so none had by then. Every A sequence opens by writing its own
  30 s and zeroing the count, so one that started t after the commit would have reset the SoC at
  t + 30 s: only a start in that 1.3 s window fits the restart that was measured.
- **A2 didn't close in 120 s.** Its close clears RESET_EN, and then there would have been no reset at
  all, unless a later sequence armed WD2 again.
- **So the entry is stuck, not slow, and it isn't a completed entry with a wake that stalls.** The one
  other reading these facts allow is A2 closing and a later A sequence starting about 90 s after the
  commit, which nothing known gives a reason for (judgment: a few in a hundred). A run with another
  bite would rule it out: the reset follows the bite, or it doesn't.
- **The battery says nothing firm about the dark stretch** (`boots.txt`): the gauge fell 87 mAh in the
  300 s from the reading's end to 152 s into the next boot. The dark stretch is 120 s of that, and the
  rest is a boot and a session that draw more than the reading's 0.54 A, by an amount nothing recorded.
  No figure for the dark stretch comes out of it.

**What it doesn't show: where the engine stops.** With CPU0 up, runs 6-10 placed A2 at its first
handshake and B2 at or before its first poll. With every core down, nothing has seen B2 at all: run 15
says only that the pair never finished.

**What it changes** (judgment; the order is in PLAN.md, and only there):
- WD2 is no longer a fixed 30 s. A run can be given up to about 179 s, and the reset's time now carries
  information the AP chose: with A2's WD2 moved out of the way, a reset at T says a later sequence
  started at T - 30 s, to the second.
- Two of B2's own late steps can be read without the SRAM (the sequences, from the dump; the listing
  stays off the repository). Past its power-downs, B2 zeroes WD0's and WD1's counts, and its
  second-to-last step writes 1 to the PMU's register 0x0201 over SPMI (B0 writes 1 there too, and B1,
  the last of the wake, writes 0). WD1 is Linux's own watchdog, and the PMU keeps its registers through
  a reset, at least the scratch bytes and the alarm's. So a WD1 armed by CPU0 before it goes down would
  reset the SoC at a time that says whether B2 got that far, and a read of 0x0201 after a run might say
  whether B2 reached its end (if a restart doesn't clear it, which isn't known).
- What holds B2's first poll is still the question. The best lead that needs no write is the part of
  the SRAM that hasn't been read: the eight other descriptor blocks' sequences, the soc clusters' by
  the look of them, where a complex's untimed wait would be ("Before the feasibility run").

**The PMU's register 0x0201, read once (read-only, Corey's OK of 2026-11-10; `pmu-0201.txt`):** 0x02,
11.5 minutes into the first boot after the reset. That's neither the 1 that B2 and B0 write there nor
the 0 that B1 writes. So in a boot where no engine sequence has finished, the register holds a value of
its own, and what the sequences write is either a command whose value doesn't read back or a state that
a boot sets again. Either way this read can't say whether B2 reached that step in run 15: a 1 would have
said it did, and a 2 fits a B2 that never got there as well as a boot that put the 2 back. It's worth
one more read at a known point, in the next safe boot before any run, to see whether 2 is what every
boot shows.

### The rest of the SRAM, read: the clusters' own sequences, and DCS's (2026-11-10)

Read-only, with Corey's OK of 2026-11-10: `e1-hook/recfg-peek --to-end` (#68, `recfg_peek` built at
ee7b1c0), in a safe boot at 10:54. All 24 pieces, 0x10000-0x6ffff, read without a fault or a stop, in six
saves; `results/recfg-peek-to-end-20261110/` has each piece's log lines. The dump and the listings stay off
this repository, with the first 64 KiB's.

**What's there (the bytes):** the SRAM's content ends in the ninth piece, where the last sequence the
pointer blocks name does (0x33160 and its length). From 0x34000 to the end no two words of a piece are
alike: memory nothing has written.

**The decode so far** (the two walkers of 2026-11-08 and a third written today, which agree wherever the
PongoOS format reaches; not finished):
- All 37 targets past the first 64 KiB open with words that format doesn't have: metas 13, 7, 5 and 15,
  where it has only End and Delay. B0 and B1 open with the same meta 13, the word a strict walk rejected
  at their start. Taken as one-word markers, metas 13, 7 and 5 leave 24 of the 37 walking cleanly to an
  End that fits the next sequence's start.
- Thirteen don't walk yet. Nine open with meta 15 and use another form of op: each is shared by the four
  blocks at 0x1200-0x1500 and writes the 0x2004..., 0x2008... pages. Four small ones use meta 2 with a
  payload, as the word inside B1 does.

**What the 24 are** (by the pages they write and the cluster words they poll; the naming is inferred):
- **The CPU complexes' sequences**, a pair each, up and down: the E complex (its own block at
  0x210e00000, soc-cluster word 0x28e098000), and the two P complexes (0x211e00000 and 0x212e00000, words
  + 4 and + 8).
- **Four more clusters' pairs:** ANE0's (cluster word index 3), one at index 5 that writes 0x264...,
  DISPLAY's (it polls the four display domains `disp_off=1` turns off), and one at index 8 that writes
  0x40d... (AVEMSR by its index).
- **DCS's:** two sequences for each of four instances (0x200..., 0x201..., 0x202..., 0x203...), beside the
  nine shared ones.
- **Two that are an End word and nothing else,** the third and fourth slots of ANE0's block.
That's 6, 8, 8 and 2.
So the eight descriptor blocks aren't a soc cluster each, as "Before the feasibility run" guessed: the
blocks are the tables of two kinds of sequencer, the clusters' and DCS's.

**The untimed waits** (a read with no retry and a count of 0, the form the handshakes have):
- Every cluster's down sequence opens by raising 0x2922800b0, and then waits, with no timeout. A CPU
  complex's waits for bit 6 of its soc-cluster word, writes 0x51 to its word at 0x28e09a000 + 4n, and
  waits for bit 5 of that to clear. ANE0's, the index-5 cluster's and the index-8 cluster's have only the
  second wait. DISPLAY's has none in what's decoded.
- That's where T8140's "a complex has a sequence with its untimed wait" is on this chip.

**What it shows, and doesn't:**
- Nothing here places the stall. For the four clusters that aren't CPU complexes (ANE0, AFR, DISPLAY,
  AVEMSR), macOS's table waits on the busy bit, and since run 9 each of those waits is met within 10 ms
  (`qto` 0). That isn't the same test as the sequences' own waits, and nothing has read 0x28e09a000 + 4n.
- **The CPU complexes' sequences are untested where it matters.** `qto` doesn't cover them, and it's
  taken before the cores are let go. What has been seen: with CPU7 up, CPU0's and the E complex's power
  read off in runs 2 and 3, and EACC's and PACC0's busy bits read clear in run 3 alone (in run 2, with
  the PMGR quiesce early and unwaited, both still read busy at the end of CPU7's watch); with CPU0 up,
  both P complexes' power read off (runs 5 and 6). Nothing has seen the E complex's sequence finish in
  a run where CPU0 goes down last, which is every run since.
  Its two untimed waits are the newest candidates for what's ahead of B2's first poll.
- A2's three settle waits (DCS, fabric and 0x28e20c400, each with no timeout) run sequences that are now
  on file. Whether DCS reaches p-state 2 from the state this recipe leaves is something the AP can try
  before the commit, where a breadcrumb can say how it went.

**The PMU's register 0x0201, once more** (`pmu-0201.txt`): 0x02 again, in a boot with no run before it.
So 2 is what a boot shows, and the read after run 15 said nothing about B2. Only a 1 would ever be news.

### Related work, swept 2026-11-10, and what it says about the cluster words

A read-only sweep by a subagent, at Corey's suggestion (no m1n1 and no Aura; a conclusion its author says
comes from a hypervisor trace is marked). Nothing was posted anywhere. Checked here: the three passages
of DJ's driver quoted below, and this SRAM's own ops.

**No one has published a completed S2R entry on an M1 Pro or Max, or an M2 Pro or Max,** and nothing new
names what B2's first poll waits for. By project:
- **Naeem Malik (T8103):** no new run. His entry completes and nothing wakes it (WD1 resets at about
  181 s); the work is paused and moves to macOS on another machine (malik-na/m1-power-lab fa6d307394bb).
- **Diljit Singh (DJ, T8140), aurora-silicon/linux:** no T600x port. The driver is becoming
  `drivers/soc/apple/sleep.c` with per-SoC match data (#1334), whose first step for a new chip is to
  measure its sleep under Aura. A consolidated draft is #1445. A sleep-DDR vote in the wake block decides
  how deep T8140 sleeps, and some states of the always-on coprocessor stall its wake (#1217, #1358, #1377;
  from traces in part).
- **Bartosz Nitka (niteria, T8132):** 16 of 16 sleeps on one boot; the system cache is written back
  before the commit (715a0229e1b8, from a trace); still through Aura.
- **Dylan Ironside (T8132, J604), new:** IronsideXXVI/omarchy-j604. No completed entry. An early sleep
  request to the SEP turns a suspend that doesn't finish into a reset 25-45 s later, with the same "PMU
  logged 1 boot error(s)" line: a second source of such a reset, to expect if a SEP rung is ever built.
  It isn't ours: run 15's reset followed WD2's bite.
- **RJ Skerry-Ryan (T6021):** nothing new since October. In his tree, and not in these notes before:
  fabric and DCS stepped down in lock step before the commit (fabric 5, DCS 7, fabric 3, DCS 5;
  686d1baf9ab7), and a driver for the PMGR's sleep-state counters, which he logs at the next boot
  because the block is always-on and "may tell how far an earlier boot got" (353718fc6237).
- **DeepIdle (T6020):** active again on a fresh install, with no new S2R run. **maralcbr:** nothing new.
- **Kernel changes on their way to this Mac** (aurora-silicon, merged 2026-11-08 to -10): afnc3's and
  `gp_usb_aon`'s power nodes disabled on T6000 (#1294), PMP votes on J314s (#1290), EDAC on T6000's
  error handlers (#1304), keyboard and trackpad wakes (#1347). Each would be a variable.

**Bit 5 of a soc-cluster word is a flag, not a state: "up done", cleared by writing 1.**
- DJ's driver says so for T8140 (`sleep.c` at ae8031d65734, L105-106: "Set when a complex's up sequence
  ends, and cleared by writing 1"). Before the commit it turns the AVE complex off by writing 0x20,
  "Target 0, and the leftover done flag from iBoot's up, as XNU's first write does", and polls for the
  word to read 0. Its reason (L2838-2842): macOS has that complex off already, "so at the commit its
  word is already 0 and the engine has no sequence to run for it. Turning it off at the commit instead
  puts that sequence, with its untimed wait, right before the CPU complexes'". It also acknowledges a
  leftover done flag in a DCS word, "as the DCS word has none on macOS".
- **This SRAM agrees, for T6000:** A1, the last sequence of the wake, raises EACC by writing 0xf to
  0x28e098000, waits with no timeout for bit 5, and then writes 0x2f. That's a done flag being waited
  for and written back as 1.
- **So the replay's literal zeros aren't macOS's state here.** Awake under Linux, ANE0's, AFR's and
  AVEMSR's words read 0x2f: on, with the flag iBoot's up left, which nothing has acknowledged. The table
  writes them 0, macOS's value, traced where those complexes were off already. PACC0's, PACC1's and
  DISPLAY's get 0x20, the value that also acknowledges the flag. If the flag stays until it's written,
  three words are at 0x20 at the commit where macOS has 0 (inferred: nothing has read them there; the
  runs' `qto` looks at bit 4 alone).
- **What that could do** (inferred): the engine has those complexes' down sequences to run, or to wait
  on, at the commit, each with its untimed wait. That would sit ahead of B2's first poll, where every
  sample has found the engine.

**Other things the sweep found, for the list:** 0x2922800b0, which every cluster's down sequence raises
first, is `ps_nub_fabric` in Linux's device tree. 0x28e20c400, the third word A2 settles, is likely a
third perf-state word (its awake 0x222 has the shape of fabric's 0x333; T8140 brings a SoC perf state
down before DCS). The fabric is at p-state 1 at the commit here, deeper than RJ's sequence for T6021
leaves it, and A2 then asks for 2.

Corey's OK came on 2026-11-10, for 0x20 in place of the table's 0 in those three words, and it's built
(`run-floor --m1-cluster-ack`: run 15's recipe with `cluster_ack=1`; not run yet). PLAN.md has what the
build does and how its endings read.

### M1 run 16: the three cluster words acked, all four read 0, and the reset still comes at 120 s (2026-11-10)

`e1-hook/run-floor --quick --m1-cluster-ack` (run 15's parameters and `cluster_ack=1`, #71; the module
built at 7ad9be9; kernel 7.2.11-15; flag `radios-off`; `pmp_sleep_release` with 0xc and 0xd), in a safe
boot, on battery at 57%, with a 1-minute reading. Against run 15 it has one change, Corey's OK of
2026-11-10: step 5 writes ANE0's, AFR's and AVEMSR's soc-cluster words (0x28e09800c, 0x28e098014,
0x28e098020) as 0x20, not the table's 0, and the four cluster waits, DISPLAY's too, ask for the whole
word to read 0. Raw data: `results/m1-run16-20261110/`.

**The facts:**
- **The load:** the nine soc-cluster words read 0x0f, 0x2f, 0x2f, 0x2f, 0, 0x2f, 0x2f, 0x0f, 0x2f awake:
  ANE0's, AFR's, DISPLAY's and AVEMSR's all 0x2f, up with the flag set. `wd2-bite`'s store took (90 s to
  120 s), as in run 15.
- **The cluster words: all four read 0 within 10 ms of their writes.** 0x6014 = 0x90: the run's mark, and
  nothing but a flag left in any word; 0x6006 = 0x00: no flag left either. 0x6007 = 0xff shows that
  command's last bytes landed. So 0x20 written to a word that reads 0x2f takes the cluster to target 0
  and clears the flag, here as on T8140, and DISPLAY's, after the table's own 0x20, reads 0 as well.
- **Everything else as in run 15, byte for byte** but for the second and that mark: stage 0xd7, the
  sample 0x7d (WD2 armed with 30 s, CPU0's 120 s read back, the count running, `PHASE` 4), 0x6012 =
  0xff, 0x6013 = 0xdf, 0x6015 = 0x8e, 0x6016 = 0xbd.
- **The restart: 119.1-120.5 s after the commit** (119.1-121.1, 118.5-120.5 and 119.0-121.0 from three
  reads in the first boot). Corey: two minutes of black screen, then the restart. The armed alarm survived
  it, and `--clear` disarmed it.

**What that shows:**
- **The leftover "up done" flags aren't what stalls the entry, or not alone.** At the commit the four
  words now read what macOS has there, and the ending is run 15's: the same reset, at WD2's moved bite.
  It reads as it did there: the pair never finished, but for the one other reading that run left open
  (A2 closing and a later A sequence starting about 90 s in), which a second run at the same bite
  can't rule out.
- **What it can't say: whether the flags were ever left.** No run has read those words after the
  table's plain 0, so the premise (that they read 0x20 in runs 3 to 15) is still inferred. Either way the
  words are at macOS's value now, and `cluster_ack=1` stays in the recipe for that.

**What it changes** (judgment; the order is in PLAN.md, and only there): one more difference from macOS
is gone and the ending didn't move. Sixteen runs in, a guess at the next difference is the weaker half
of a run. The stronger half is an instrument that reads where the pair stops with every core down, which
the next section proposes, with the one difference that sits at A2's first step of its own.

### After run 16: the fabric is one step below what the engine asks for, and WD1 can say how far B2 gets (2026-11-10)

Desk work, from the sequences on file, this kernel's source and its log. Nothing here is from m1n1 or
Aura. The sequences' listing stays off the repository.

**The fabric's p-state at the commit is 1 under Linux, and A2's first step of its own asks for 2.**
- A2, once B2 has answered its first handshake, writes 2 to DCS's p-state word and waits, writes 2 to
  the fabric's (0x28e20c000) and waits for bits 11:8 to read 2 with bit 31 clear, and writes 0 to
  0x28e20c400 and waits. None of the three waits has a timeout.
- This kernel lowers the fabric at every suspend. `apple-pmgr-misc`'s `suspend_noirq` writes the suspend
  p-state, 1, to the fabric word's low nibble, and with the PMP described it leaves DCS alone
  (omacom/linux-aurora, `drivers/soc/apple/apple-pmgr-misc.c`; "set 0 ps to pstate 1" in this morning's
  s2idle sleep on 7.2.11-15, and no line for DCS). The word reads 0x333 awake (every load log). E1's
  module runs after the whole noirq pass, so every run so far has reached the commit with the fabric
  asked down to 1. Nothing has read the word there.
- macOS doesn't have it there. T6020's macOS ends the fabric at 3 before its commit (DeepIdle's
  worklog), RJ's T6021 sequence steps it 5, then 3, and maralcbr's J314s table never writes it after
  `slpa`. That T6000's macOS has it at 3 at the commit is inferred from those and from 0x333 awake.
- So the engine's own sequence, written for a fabric coming down from 3 to 2, here has to bring it up
  from 1 to 2, after the table has parked the ClkGen muxes and switched the PLL off. If that step can't
  settle, A2 waits there for good, WD2 running. That is a stall right behind B2's first handshake, with
  a cause that's Linux's and not macOS's (judgment: about 1 in 4 that it's what stops the pair; it needs
  the gate to open first, which only a run with every core down can show).
- The way to test it: put the fabric back at 3 before the table's DCS entries, while the clocks still
  run, as Linux's own resume does, wait for it to settle, and read the word before and after. One PMGR
  write (0x28e20c000, the low nibble to 3), which needs Corey's OK.

**WD1 as a three-way marker, with nothing left up to watch.**
- B2 zeroes WD0's and WD1's counts at its ops 223 and 224 of 246, after its whole power-down, and writes
  neither watchdog's control. A2 stops WD2 only at its very end.
- **WD1 has an owner while the Mac is awake, so the marker has to be set inside the replay.** The
  kernel's `apple_wdt` keeps WD1 armed with a 30 s bite and pings it (control 4 at every load, run 16's
  too), and its suspend callback stops it before E1's module runs: by step 4 WD1 has read off in every
  run ("M1 run 2"). A WD1 armed at the module's load would be pinged through the reading and then
  stopped by that callback, and a reset at 120 s would then read, wrongly, as B2 having zeroed it.
  (Pullfrog on #72. Corey's OK of 2026-11-10 was for arming it at the load, about 70 s ahead: that
  can't work, and what follows needs a fresh OK.)
- So set it in step 4, after the kernel has let go of it and a moment before the commit, with the lead
  written into the count: WD1's bite := 150 s, its count := 60 s, then RESET_EN (three writes; the count
  one is new in kind, since the kernel and the engine only ever write 0 there). Just before the commit
  CPU0 reads WD1's control, bite and count back and records whether the marker stands: armed, the bite
  150 s, the count at 60 s and running. A marker that doesn't stand makes the run's time unreadable for
  B2's progress, and it's to be read that way, not as a result. With WD2 at 120 s as in runs 15 and 16,
  the reset's time then reads the pair's progress:
  - **about 90 s after the commit:** WD1, its count never zeroed, is what fits. B2 then didn't reach
    op 224, where WD1's count is zeroed: the stall is at the gate, in the first handshake (A2's three steps) or in B2's power-down.
    It supports that and doesn't prove it: the same time comes out if B2 zeroed WD1's count, the pair
    finished, and a later A sequence started about 60 s after the commit and armed WD2 with its own
    30 s, the delayed re-arm that run 15 left open (Pullfrog on #72). Runs 15 and 16 saw no sequence
    start then, but they aren't this run. A second run with another count would separate the two: WD1's
    time moves with the count, and a later sequence's doesn't.
  - **about 120 s:** WD2. B2 zeroed WD1's count, so the gate opened, A2's three steps settled and B2's
    power-down ran, and the pair then stopped in its last twenty-odd ops: the second handshake, the
    PMU command, the third handshake.
  - **about 150 s, or dark:** the entry completed (A2 stopped WD2), and WD1, zeroed at about the
    commit, ended it as a dead-man, as on T8103; dark if WD1 doesn't count in that state.
- The three times are 30 s apart and the restart is timed to about a second, so one run reads it. It
  needs no core left up, which is what has kept every sample so far at the gate.
- It also ends a completed entry by itself: no power key, and no wait in the dark. A floor reading
  would be a later run, with WD1 left out.
- Nothing is armed while the Mac is awake: step 4 is past `slpa`, where the replay has no way back.
- What the restart's time can't say by itself is which watchdog fired. Each reading above is the one
  that fits with nothing else assumed; the 90 s one has the alternative given there, and the 120 s one
  has its own: a WD1 that can't reset the SoC in that state would leave WD2's 120 s whatever B2 did,
  which a run with WD1's bite below WD2's would show.

Both are built (`run-floor --m1-mark`: `fabric_up=1` and `wd1_mark=1` on run 16's recipe; not run yet).
The fabric's write is the one Corey OK'd on 2026-11-10; the marker's three are the ones shown to Corey
that day after Pullfrog's catch, built on their standing go-ahead for new steps. PLAN.md has what the
build does and how its endings read.

**Why both in one run** (judgment): the marker reads the same whether the fabric is the cause or not.
With the fabric at 3, 90 s says the early stall isn't the fabric's, 120 s says the pair got past it to
its last ops, and 150 s or dark is the entry. What the pairing gives up is knowing whether the fabric
write was needed for a run that gets further, which a later run without it would settle.

### M1 run 17: the fabric back at 3 and WD1 as a marker: the restart comes at 90 s, so B2 doesn't get through its power-down (2026-11-10)

`e1-hook/run-floor --quick --m1-mark` (run 16's parameters, `fabric_up=1` and `wd1_mark=1`, #74; the module
built at 4386efb; kernel 7.2.11-15; flag `radios-off`; `pmp_sleep_release` with 0xc and 0xd), in a safe boot,
on battery at about 17%, with a 1-minute reading. Against run 16 it has two changes: one write that puts the
fabric's p-state back to 3 before the table (Corey's OK of 2026-11-10), and three that set WD1 as a marker in
step 4 (its bite 150 s, its count 60 s, RESET_EN; shown to Corey that day, built on their standing go-ahead).
Raw data: `results/m1-run17-20261110/`.

**The facts:**
- **The load:** the fabric's word read 0x333 awake. WD1 read control 4, a count of 7.9 s and a bite of 30 s:
  the kernel's, as expected. `wd2-bite`'s store took, as in runs 15 and 16.
- **Stage 0xd3**, the run's last by design, with the steps' outcomes behind stage 0xd2's command (0x6012 =
  0xff, 0x6013 = 0xdf), so the run's byte landed whole.
- **The fabric, 0x6007 = 0xf5:** the word read 0x111 at the start of step 5, asked down to 1 by the kernel's
  suspend and there. After CPU0's write it read 0x333 within 10 ms, and it still read 0x333 at the end of
  step 5, after the ClkGen park and PLL-off. That read, the first of that page after the clocks are parked,
  returned.
- **The marker stood:** just before the commit WD1 read armed, its bite 150 s, its count past 60 s and
  running. So the count takes a non-zero write.
- **WD2's sample, 0x7d,** as in runs 15 and 16: armed with A2's 30 s, CPU0's 120 s read back, counting,
  `PHASE` 4.
- **The rest as in run 16:** all seven other cores held; `qto` 0x00 and 0x6014 = 0x90, so the four cluster
  words read 0 again; the PMP's CPU stopped (0x6015 = 0x8e); every alarm command acked.
- **The restart: 90.0-91.5 s after the commit** (89.5-91.5, 90.0-92.0 and 89.7-91.7 from three reads in the
  first boot). Corey: "rebooted at just about 90 seconds". The armed alarm survived it, and `--clear`
  disarmed it.

**What that shows:**
- **The restart moved again, to where WD1's unzeroed count puts it.** 150 s less 60 s, to the second. WD2,
  armed and counting at 120 s, was 30 s from firing. So WD1 does reset the SoC in that state, and B2 hadn't
  zeroed its count 90 s after the commit: B2 doesn't reach its op 224. The stall is in B2's first 223 ops
  or in what they wait on: its first poll (the gate), the first handshake with A2's three steps, or its
  power-down of SIO, PCIe, ANS, the PMP and the clocks.
- **The one other reading** is a pair that finished and a later A sequence starting about 60 s after the
  commit. Runs 15 and 16, the same recipe but for the fabric and the marker, saw no sequence start then
  (their restarts came at 120 s). It isn't excluded, and nothing supports it.
- **The fabric premise was right, and it isn't the stall, or not alone.** Every run before this one reached
  the commit with the fabric at 1, where macOS has 3 (inferred). This one had it at 3, where it stayed, and
  B2 still didn't get through. `fabric_up=1` stays in the recipe, as `cluster_ack=1` does: both make the
  state at the commit macOS's.
- **A treatment can now show progress short of a finished entry.** Before the marker every failure looked
  the same. From here a change that gets B2 past its power-down moves the restart from 90 s to 120 s or
  later, whether or not the pair then finishes.

### After run 17: the three coprocessor words B2 polls, and a probe that isn't one-way (2026-11-10)

Desk work, from the sequences on file. The listing stays off the repository.

**What B2 waits on before op 224, in order** (the table in "The engine's reconfig SRAM"): the gate
(0x2922c0044: bit 2 set, then bit 3 clear); the handshake, inside which A2 settles DCS, the fabric and
0x28e20c400; then its power-down, whose waits are of three kinds. Power words it has just written, polled
for bit 11 clear. Gate-status words, polled for bit 30. And, three times, a coprocessor's word:
- op 49-51: SIO_CPU := 0x0f00024f, wait for actual f, then **wait for bit 1 of 0x39bc08000**, SIO's ASC
  + 0x8000, the first word of its mailbox block;
- op 149-151: ANS2 := 0x0f00030f, wait for actual f, then **bit 1 of 0x38f408000**, ANS's;
- op 157-159: ps_pmp := 0x0f00024f, wait for actual f, then **bit 1 of 0x28ec08000**, the PMP's.
Each is tried 255 times and then, as far as the encoding shows, not passed. So for each of the three the
engine powers the coprocessor's domain up and expects a status bit that nothing here has ever read: nothing
documents offset 0 of that block, and the three words were left out of every read so far ("awake, one per
boot, once something needs them", PLAN.md). The local decode has had them as candidates since 2026-11-08.

**Why they're the next thing to look at** (judgment):
- They're where Linux's state most plainly isn't macOS's. Under macOS the three firmwares are asleep by
  the commit. Here SIO's is idled and its CPU's RUN cleared at prepare, then genpd cuts SIO's power at
  noirq and step 5 raises it again: the engine finds an ASC that has been through a power cycle with its
  CPU never started. The PMP's is idled and stopped after `slpa`. ANS is whatever Linux's NVMe suspend
  leaves. If bit 1 says "this processor is in its sleep state", a CPU held in reset may never show it.
- They can be read without a one-way run. A suspend that E1's `prepare_late` fails runs the noirq pass
  and then comes back. The steps the recipe takes on SIO and the PMP before the commit can be taken
  there, with the three words read before and after each, in full, into the kernel log, which is then
  saved. No commit, no reset, and a result in one short safe boot.
- What a run can't see, this can: whether the bit is set while the firmware runs, after its idle pair,
  after RUN is cleared, and after SIO's power cycle.

**What it can and can't say** (Pullfrog on #75). The probe samples the state the recipe's preparation
leaves, before any commit. B2 reads each word later, after its own write to the domain's power word and
its wait for actual f, and after everything A2 and B2 do before that; and what bit 1 means, and whether it
holds across those steps, isn't known. So a sample is a lead, not the location of the stall:
- A word that never shows bit 1 in the recipe's state is the best lead there is for where B2 stops. The
  next run then leaves that coprocessor differently (idled and not stopped, or not power-cycled) and
  reads the marker: 120 s or later is what confirms it.
- All three showing it makes the three polls less likely to be the stall, and doesn't exclude them.
Either way everything else B2 waits on before op 224 stays a candidate: the gate, A2's three steps, the
waits for actual f after B2's own raises (SIO_CPU, ANS2, ps_pmp), the bit-11 polls and the bit-30
gate-status polls.

**The probe as built** (`e1_hook probe=1`, launched by `pkexec e1-hook/probe` in a safe boot;
`e1-hook/asc_probe.h` has the ops, the steps and the bytes). Run once: "The probe's run", below. Pullfrog and one adversarial
review went over it on #76; what they changed is marked.
- It loads with `--m1-mark`'s parameters, so that everything the recipe maps and checks at load is there,
  and `probe=1`, with which the replay can't run: `e1_replay()` returns at once, `prepare` takes none of
  the replay's steps, and `prepare_late` returns -EIO whatever else happens, so every suspend fails and
  the Mac comes back awake. No key is written to the SMC (`B0AV` is read, as with any load) and nothing
  is committed. Failing the suspend from the module, and not `pm_test=platform` as first written here,
  needs nothing set outside the module, and a forgotten setting can't leave the Mac asleep. The launcher
  goes on only if the loaded module reads `probe=Y`; one that doesn't has its replay called off and is
  unloaded before any suspend is asked for.
- At load it reads ANS's and the PMP's words once each, awake. For ANS that is the only read with its
  firmware running, and it goes into ANS's byte: by `prepare` the NVMe driver's suspend has shut ANS's
  firmware down and cleared its CPU's RUN (`apple_nvme_suspend()`; the review), with ANS2 still on until
  noirq. SIO's isn't read at load: SIO_CPU gates its own clock awake and reads actual 4.
- At `prepare` (after the drivers' late suspend, before noirq) it puts SIO_CPU fully on with the table's
  own word, then takes `sio_stop=1`'s steps on SIO: the idle pair, then RUN cleared. SIO's word is read
  before, between and after, ANS's and the PMP's once each. **No run writes SIO_CPU's word there,** before
  noirq and with SIO's firmware running: the runs write it in step 5.
- At `prepare_late` (after noirq) it reads ANS's word, takes `pmp_out=1`'s steps on the PMP with its word
  read before, between and after, and raises SIO's parents and SIO_CPU as step 5 does and reads SIO's
  word in the state B2's op 51 finds.
- A word is read only while its domain's power word reads actual f, and again while bit 1 reads clear:
  B2's 255 tries, 400 µs apart, about 100 ms. Every word is logged in full.
- **What it writes that no run has: ANS2 (0x28e5803b8), twice.** Linux's NVMe suspend leaves ANS2 off, and
  B2's op 149 raises it before its poll, so the probe writes B2's own value, 0x0f00030f, waits up to 10 ms
  for actual f, reads ANS's word, and writes ANS2 back as it found it, less BUSY (bit 11) and the two
  write-1-to-clear flags (bits 9 and 8), which the kernel's own driver never writes back either, and
  waits up to 10 ms for its target and actual state to read as found (the review). ANS's CPU isn't
  started, and the driver brings the NVMe up again at resume as after any suspend. Whether ANS2 reaches
  actual f there isn't known: its parent in the device tree may be off by then. If it doesn't, ANS's
  word isn't read, and the record says NOT READ, not clear (Pullfrog). `ps_pmp` is read, never written.
- **New as reads:** the three mailbox words, and ANS2's and `ps_pmp`'s power words; 0x28e20c400 is read
  once more, for the log.
- **What it leaves out of the recipe's state:** the DCPs' shutdown (the screen has to come back); the
  SMC's keys and its idle notice; the commit; the cores' power-down. So SIO's and the PMP's words are
  read in the recipe's state for those two coprocessors, not in the whole of it.
- **The PMP's display report requests are withdrawn at noirq, as in every run since run 7** (the review
  found the first build left them asserted): the launcher has `pmp_sleep_release` do it
  (`sleep-drain-pmp release 0xc 0xd`), as `run-floor` does and as T8140's driver does before it idles
  the PMP. The PMP has never been idled on this Mac with them asserted, so a probe without that could
  have failed at the idle pair and read nothing in the state B2 finds. What's new with it: the module
  asserts the two requests again at resume, and here the PMP is stopped by then. From its source: it
  writes each request's bit to the report block's target word, waits up to 50 ms for the PMP to take
  it, twice, and logs an error; nothing else depends on the answer. Whether a display processor minds
  resuming with its request asserted and not acknowledged isn't known; with the PMP stopped it gets no
  service from it either way.
- Afterwards SIO's firmware and the PMP's stay stopped for the rest of the boot. Neither kernel driver
  does anything at resume (`apple-sio`'s resume is empty, `apple_pmp` has no PM ops: linux-aurora's
  sources, checked again in the review), so the Mac should come back with its screen and keyboard, and
  without sound. "Should": Linux has never run on after the PMP's idle pair and RUN cleared. The
  launcher prints the module's lines for a photo, saves them, and asks for a restart.
- **The record in the PMU, if the screen doesn't come back.** Stage 0x0e, which no run uses, goes out at
  the start of `prepare`, and 0x6010 then has the step about to be taken, 21 of them, so that a Mac that
  goes down inside the probe, where the console is suspended and no watchdog runs, says where (the
  review). Three bytes at 0x6005 to 0x6007 at the end say what bit 1 read at each step. `m1-crumbs`
  decodes all of it after the restart, and the module's SUMMARY line is read from the same bytes.
- The probe runs once, in the first suspend to reach the module's `prepare`, and its second half only in
  that same suspend. A suspend that ends before `prepare` (a key still going up) has stopped nothing, and
  the launcher asks again, three times at most, and never once `prepare` has run, in its own suspend or
  in one something else asked for. It writes `/sys/power/state` itself, so systemd's sleep hook doesn't
  run and the radios stay as they are: hands off the keyboard and trackpad until its photo block.

### The probe's run: bit 1 reads set for all three, the PMP's only once it's idled; the Mac didn't come back (2026-11-10)

`pkexec e1-hook/probe` in a safe boot, PR #76's build (7027cfc), about 18:30. The screen went black and
stayed black; after 2 to 3 minutes Corey held the power key. Nothing was saved (the boot partition's
`saved` was empty), so the kernel log with the words in full is lost. The PMU's record is whole: stage
0x0e, step 0x15, the last of the 21, and all three bytes landed (`results/asc-probe-20261110/`).

**What the bytes say** (ff fc fd):

| | SIO, 0x39bc08000 | ANS, 0x38f408000 | PMP, 0x28ec08000 |
|---|---|---|---|
| awake, at the load, firmware running | not read (SIO_CPU gates itself) | set | not in the bytes |
| at prepare | set, firmware running | set, firmware shut down, ANS2 on | clear or not read, firmware running |
| after its idle pair | set | | set (after noirq) |
| after RUN was cleared | set | | set |
| after noirq, before the idle pair | | | **clear**, through the whole poll |
| **the state B2 finds** | **set** (parents and SIO_CPU raised again, actual f) | **set** (ANS2 raised, actual f within 10 ms) | **set** |

Every step went as meant: both idle pairs returned 0, both RUN bits read 1 and then 0, SIO_CPU and
`ps_pmp` read actual f, ANS2 was off after noirq, reached actual f when raised as B2's op 149 raises it,
and read its found target and actual state again within 10 ms of the write-back.

**What it shows.**
- **In the state the recipe leaves, all three of B2's coprocessor waits would pass.** That makes ops 51,
  151 and 159 less likely to be where the pair stops. It doesn't exclude them: B2 polls later, after its
  own raises and everything before them.
- **Bit 1 follows the PMP's idle pair.** With the PMP's firmware running it read clear to the end of a
  poll (up to 255 reads, 400 µs apart, about 100 ms at most; the count was in the log that's lost, and
  the byte keeps only the outcome); after the idle pair it read set, and stayed set with RUN cleared.
  The one coprocessor here whose firmware was seen both ways changed with it, so the bit looks like "this
  coprocessor's CPU is idle", and B2's three waits like "idle before its power goes" (inferred). SIO's and
  ANS's read set while their firmware was running, which fits firmware that idles between requests and
  doesn't prove it.
- **The PMP's idle pair matters to the engine.** Had the PMP been left running, as in runs 1 to 6, B2's
  op 159 would most likely have stopped there for its 255 tries. Runs 7 to 17 idle it first, which makes
  that wait less likely to be what stops them and doesn't exclude it: the probe sampled before any
  commit, without the SMC's and the DCPs' steps, and B2 reads the word later, after its own writes
  (Pullfrog on #77).
- **ANS2 comes up by itself after noirq.** Raised with B2's value under whatever Linux's suspend leaves
  above it, it reached actual f. B2's wait at op 150 would pass.

**The Mac didn't come back, and that isn't explained.** The probe finished (step 0x15 and the bytes are
its last acts before it fails the suspend), so the hang was in the kernel's resume or after it, with
SIO's firmware and the PMP's stopped and `pmp_sleep_release` asserting two report requests to the
stopped PMP. Which of those it was, nothing here says: the screen stayed dark and the save never ran.
What it costs: this probe's words in full, and the line it logged after noirq with the gate words, the
fabric's, DCS's and 0x28e20c400. What it means for the next one: **a probe that stops a coprocessor's
firmware doesn't come back on this kernel.** One that only reads would be an ordinary failed suspend.

**Where that leaves the stall** (run 17: B2 doesn't reach op 224 with every core down). In B2's order,
what's left that can wait for ever, by the form of its read: the gate (0x2922c0044 bit 2 set, then bit 3
clear); 0x2922c0040 bit 1 after B2's own write; the first handshake, inside which A2 settles DCS, the
fabric and 0x28e20c400 (0x222 awake, to 0); eleven clock-generator status words at 0x28e000000 to
0x28e01c000, each polled for bit 31 clear after B2 writes the word beside it; and two more AON words,
0x2922c003c bit 8 and 0x2922c0038 bit 1, after B2's writes. Beside those, the polls with 255 tries whose
end isn't known: bit 11 clear on some fifty power words after B2's writes of 0, and bit 30 set on six
gate-status words (0x28e5a0064, 0x28e5a0050, 0x28e5a030c, 0x28e09c338, 0x28e09c34c, 0x28e09c310). None
of the clock-generator words, the six gate words, or the AON words other than 0x2922c0040 and
0x2922c0044 has ever been read on this Mac.

### The look as built: 98 words read, nothing stopped, nothing written (2026-11-10)

`pkexec e1-hook/probe --look` in a safe boot loads `e1_hook` with the probe's parameters and `look=1`
(`e1-hook/look.h` has what it reads). Run once: "The look's run", below. Pullfrog and one adversarial
review went over it on #78; what they changed is marked.
- **What it reads,** awake at the module's load and again after the noirq pass of the suspend it fails:
  the fabric's p-state word, 0x28e20c400 and DCS's; six words of the engine's page at 0x2922c0000 that
  its sequences read (+ 0x38, + 0x3c, + 0x40, + 0x44, HS_B and PHASE); SIO's page of power words from
  SIO's own to AES's, 43 of them, and ANS2's; AIC's, DWI's, GPIO's, `ps_pmp`'s and the three PCIe words
  in the first page; the six gate-status words; the first two words of fifteen clock generators,
  0x28e000000 to 0x28e01c000; and last, in a group of their own, the two words of the engine's page that
  its sequences write and never read, + 8 and + 0x24 (the review: nothing shows a read of those is
  harmless, so everything else is logged first). 98 words, each logged in full under its address.
- **What it doesn't do.** No replay, and every suspend failed, by the probe's own guards, which it
  shares. None of the probe's steps: no firmware idled or stopped, ANS2 not raised, SIO_CPU not written.
  No register written at all; the test compiles `m1_look()` with no `writel()` to give it. The launcher
  doesn't load `pmp_sleep_release` for it, and refuses if it's loaded.
- **Its only writes** are to the PMU's scratch, by the path every run uses: stage 0x0f and, at 0x6010,
  the group it's about to read (six groups), with 0x10 set by the load's look, so that a read that faults
  says which group and which of the two looks. The load's look zeroes both again when it's through, and
  the suspend's leaves the last mark.
- **The first build would have stopped at insmod** (Pullfrog and the review, each by reading): the load's
  look read the engine's page before `m1_wd2_init()` had mapped it, a NULL read in the kernel. The look's
  start-up is now the last of the load's steps, maps that page itself if nothing has, and refuses unless
  every page it reads through is there; the test runs it as it stands from the pointers a load can find.
- **Why it should come back when the probe didn't.** The probe stopped two coprocessors' firmware and the
  kernel didn't resume; why isn't explained. This stops nothing, so the suspend it fails should be like
  any other that a driver refuses. That hasn't been tried. So the launcher prints and saves the load's
  look before it asks for the suspend (the review), and whatever happens then, that look is on file. The
  two saves are of two files, each written whole and never changed after: `collect` keeps the first
  copy of a file it has, so one file saved at two lengths would have come back at its first (Pullfrog).
- **What can go wrong.** A read that faults: the clock generators' and gate words have never been read
  here from Linux, though the engine's own sequences read them, and the two words read last aren't read
  by anything known. A fault at the load would freeze the screen there; the mark in the PMU names the
  group.
- **What it gives.** What Linux's own suspend leaves in each word after noirq. The recipe goes on from
  there (the DCPs shut down, two firmwares stopped, the SMC idled, the PMP's requests withdrawn, the cores
  parked, macOS's table), and a word may follow any of that, so this is the value the engine finds only
  for a word none of those changes (the review). Read against what the wake sequence B1 leaves, a word
  that differs is a lead (for the gate words, bit 31 clear would mean a gate B2 waits on is never
  enabled). It says nothing about what the engine's own writes then do.

### The look's run: it came back, and the engine's own words are as its wake sequence leaves them (2026-11-10)

`pkexec e1-hook/probe --look` in a safe boot, PR #78's build (24f4b0c), 19:51. The screen went black for a
second or two and came back. Both looks read all 98 words, both were saved and collected
(`results/look-20261110/`), and the PMU's mark says the same (stage 0x0f, 0x6010 = 7). The suspend took
1.1 s from entry to exit and returned an error to the launcher, as meant.

**It came back.** A suspend that `prepare_late` fails resumes like any other here. So what kept the probe
from coming back was what it did (two firmwares stopped, or the report requests asserted to a stopped
PMP), not how it ended.

**What was read** (awake at the load; after noirq where it differs):

| | awake | after noirq |
|---|---|---|
| fabric p-state, 0x28e20c000 | 0x333 | 0x111 |
| 0x28e20c400 | 0x222 | the same |
| DCS p-state, 0x28e20c800 | 0x666 | 0x555 |
| engine's page: + 0x38, + 0x3c, + 0x40, + 0x44 | 0, 0x100, 3, 8 | the same |
| HS_B, PHASE | 0x80000000, 0 | the same |
| engine's page: + 8, + 0x24 (only ever written by it) | 1, 0 | the same |
| clock generators, + 0 (15) | all 1: bit 31 clear | the same |
| clock generators, + 4 | 4; 0x28e002004 2, 0x28e016004 5, 0x28e018004 1 | the same |
| gates 0x28e5a0050, 0x28e09c310, 0x28e09c34c | bit 31 set, bit 30 clear | the same |
| gates 0x28e5a0064, 0x28e09c338 | bit 31 set, bit 30 set | the same |
| gate 0x28e5a030c | bit 31 set, bit 30 clear | bit 30 **set** |
| SIO's page, 43 power words | a mix | all off but UART2, 3, 4, 6, 7 at 0x244 |
| ANS2 | on | off |
| AIC, DWI, `ps_pmp`, APCIE_GP_SYS | on | on |
| GPIO; APCIE_ST_SYS, ST1_SYS | on | off |

**Read against the engine's wake sequence, B1** (the local decode; `~/.local/share/sleep-drain/look/compare.py`):
- **The engine's own page is as B1 leaves it.** + 8 reads 1 and + 0x24 reads 0, B1's last writes; + 0x38
  reads 0; + 0x3c reads 0x100, the bit B1 waits for after writing 1; + 0x40 reads 3 and + 0x44 reads 8, as
  before. The two words nothing had read, and that the engine never reads, read without trouble.
- **The clock generators are as B1 leaves them:** none busy, every + 4 word B1's own value. Nothing asks
  them to change across Linux's suspend.
- **All six gates are enabled** (bit 31), as B1 writes them. Had one read disabled, B2's poll of it could
  never have been met; none does.
- So for these words, "Linux never ran the engine's wake, so the engine isn't in the state its entry
  starts from" has nothing in its favour.

**What bit 30 of a gate word is.** 0x28e5a030c read clear awake and set after noirq, with ANS2 gone off in
between: bit 30 follows what's behind the gate, and reads set when it's all off (inferred from that one
change and B2's polls). After Linux's own suspend three of B2's six read set already. The other three are
each behind power words B2 itself switches off just before it polls: 0x28e5a0050 behind SIO's subtree,
where five UARTs are still at 4; 0x28e09c34c, polled after PCIe, with APCIE_GP_SYS still on; 0x28e09c310,
polled after the PMP and DWI, both on. Nothing here says one of them can't close; nothing shows it does,
since what else sits behind each isn't known.

**DCS is at 6 awake and 5 after noirq**, with the PMP running and owning it, and the fabric at 3 and 1.
A2 lowers DCS to 2, the fabric to 2 and 0x28e20c400 from 0x222 to 0, and waits for each with no timeout.
None of those three moves has been seen on this Mac.

**ANS's and the PMP's words in full,** from the module's load, which the probe's run had lost
(`load-mailbox-words.txt`): ANS 0x2e, the PMP 0x2d, each with its firmware running. They differ in the
low two bits, 10 and 01. With the probe's run (the PMP's bit 1 clear running, set idled) that fits bits
1:0 as a state, 01 running and 10 idle, which is also what macOS's idle test on an ASC's CPU status looks
at (inferred).

**Where that leaves the stall.** Run 17: B2 doesn't reach op 224. The probe: its coprocessor waits would
pass. The look: its page, its clock generators and its gates' enables are in order. Left, in B2's order:
the gate, 0x2922c0044 bit 2, which has only ever been read with a core up; A2's three moves inside the
first handshake; and B2's polls after its own writes (bit 11, the gates' bit 30, the clock generators'
bit 31). A2's three can be tried from a running core without changing what the engine then finds: if the
AP makes them first, A2 finds them made (what a move that doesn't settle for the AP would mean for A2 is
another matter: A2 makes them later, with every core down).

**One of B2's gate polls has something on file against it.** Since run 12, `ps_words=1` writes macOS's
values to twenty power words, and every run has recorded that the last four, the ones given macOS's auto
floor, didn't all read back as written (0x6013 = 0xdf; "M1 run 12"). One bit for the group: which word,
and what it read, isn't known, and it wasn't followed up. One of the four is AFNC2_LW1, 0x28e580160, the
fabric lane the device tree gives as SIO's parent. macOS has it at 0x1000030f at sleep entry, on with
auto power-gating and a floor of 0; Linux has 0x1f0000ff, a floor of f
(`results/sleep-peek-20261104T120619/`). And 0x28e5a0050, the gate B2 polls after it has switched SIO's
subtree off, read open in this look with the subtree off but five UARTs. If that gate closes only when
the lane above SIO gates itself, a lane that never takes macOS's floor would leave B2's poll unmet, before
op 224. That's a chain of two inferences, each of which one read can test: PLAN.md, "The order after the
look".

### The gate test as built: six power words out and back, and the gate B2 waits on watched (2026-11-10)

`pkexec e1-hook/probe --gate` in a safe boot loads `e1_hook` with the look's parameters and `gate=1`
(`e1-hook/gate_test.h` has the steps and the record). Not run yet.
- **It's the look, and one test after it.** Both looks run as before and write no register. Then, in the
  same failed suspend, after noirq:
  0. Everything read as found: the five UARTs, AFNC2_LW1, SIO's own word and the lane's other children
     (DISPDFR_FE and BE, MIPI_DSI, ISP_SYS). **The test isn't made, and nothing is written, if SIO reads
     on or a UART has a target of f:** that isn't the state it's for.
  1. UART2, 3, 4, 6 and 7 := 0x200, `ps_words=1`'s value for them, up to 10 ms each for actual 0. Linux's
     suspend leaves those five at 4, the only words of SIO's subtree not off; with them off the subtree is
     as B2 has it after its op 138.
  2. Up to 100 ms for bit 30 of 0x28e5a0050, the gate word B2 polls there.
  3. AFNC2_LW1 and the gate word read once more; then AFNC2_LW1 := 0x1000030f, macOS's value and
     `ps_words=1`'s, read back at once and for up to 10 ms.
  4. The gate again, up to 100 ms, and AFNC2_LW1 once more, to see whether it gated itself.
  5. AFNC2_LW1 written back as found, then the five UARTs, each less the bits the kernel's driver never
     writes back, up to 10 ms each to read as found; the lane read last, with everything back, since its
     actual state follows what's below it.
- **What it writes:** those six words, each once out and once back, with values the runs already write
  and then the values found. Nothing else: no firmware is touched, and `ps_pmp` is neither read nor
  written by it. The load refuses if one of the six is on the never-write list.
- **What it risks, and this is by design.** If the gate does close, SIO's island loses power with SIO's
  firmware running. Linux's own suspend hasn't been seen to do that here (the gate read open after it).
  The island also holds SPI3 (the keyboard and trackpad), the I2C buses and SIO's DART, and the kernel has
  no way to bring SIO's firmware back; the one resume tried with a firmware gone, the probe's, didn't come
  back. So the result the test is after may be the one it doesn't resume from. In a safe boot that costs
  the kernel log and nothing else: the disk is read-only, and a restart puts every power word back.
- **The record, made to stand without the kernel log:** the look's stage 0x0f; the step about to be taken
  at 0x6010, with 0x20 set; 0x6005 and 0x6011-0x6016 zeroed when the test starts, and the result byte at
  0x6005 at the end; and at 0x6011-0x6016, AFNC2_LW1 three times in two bytes each (target, actual state,
  auto-enable, auto floor, its three flags): right before its write, after it, and after the second poll.
  Those six bytes go out before anything is put back, in one command with the mark of the step that
  follows (6), so the mark is what says they're this test's: `m1-crumbs` reads them only under that mark
  or a later one, and a later mark carries 0x40 if the command wasn't acked. A test that isn't made
  leaves a mark of its own (9). The kernel log has every word in full, with the six gate words at four
  points.
- **What it can show, in Linux's suspended state:** the gate closes with the subtree off (then this poll
  isn't the stall there, and the lane's floor isn't needed for it); it reads closed only after the lane
  has macOS's value; the lane doesn't take the value, with what it reads instead, which would be the "not
  read back as written" of runs 12 to 17 found; or the gate stays open either way.
- **What it can't:** "after" isn't "because of". There is one poll before the lane's write and one after,
  and a gate that closes late by itself would look the same; the read right before the write narrows
  that and doesn't end it. And the recipe's state is further on (the PMP and SIO's firmware stopped, the
  cores parked, macOS's table out), and B2 polls after its own writes. A result here is a lead for the
  next one-way run.
- **Checked:** the test compiles the function as it stands against a model in twenty scenarios (a gate
  that closes at step 2, only at step 4, late by itself, or never; a lane that doesn't take the value, or
  had gated at Linux's own floor; SIO on, or a UART in use; words that don't go off, or don't come back, or
  come back in their settings and not their actual state; each UART found its own way; an earlier test's
  bytes still in the PMU, with and without the PMU acking this test's writes): twelve writes and no other
  in each test that's made, none in one that isn't, and always the write-back.
- **What the adversarial review of #80 changed** (one pass by a subagent, set on breaking it; it found no
  way for the test to write a wrong word or to skip a write-back): the risk above, said plainly where the
  first build called the test safe to come back from; the precondition of step 0; 0x6005 zeroed first, so
  that a byte left there isn't read as this test's; the reads of step 3 before the write, and the "after,
  not because" in every verdict; the lane's three reads in the PMU; the lane judged back last; the
  never-write check at load; and the launcher printing the look's thirty lines above the photo's block
  where they don't push the result off the screen. Pullfrog's three: a UART that didn't go off makes the
  SUMMARY say INCONCLUSIVE first; a result byte that landed means the test reached its end even if its
  last mark didn't; and bytes left at 0x6011-0x6016 by an earlier test can't be read as this one's reads
  of the lane. For the last, besides the mark rule above: `m1-crumbs --clear` now zeroes the stage's
  block last, so a clear that stops halfway leaves the stage and no launcher takes the PMU for clear, and
  `e1-hook/probe` refuses if 0x6010-0x6016 aren't zero under a zero stage.

### The gate test's run: the gate stayed open, the lane already read as macOS has it, and the Mac didn't come back (2026-11-10)

Run once, about 22:02, in a safe boot, on the charger, by Corey: `pkexec e1-hook/probe --gate` at a7d9772.
The screen went black for about 30 s and the Mac restarted by itself. The test's kernel log is lost with
that; the look at the load was saved before the suspend. On file in `results/gate-20261110/`: the PMU's
record as `m1-crumbs` read it (`crumbs.txt`: stage 0x0f, mark 0x28, byte 0xb5, the lane's reads `0f 70`
three times), the load's look (`gate-load.txt`), and the clear and collect.

- **The test was made and ran to its end.** Every step's mark and the byte landed, so all twelve writes
  were issued, the write-backs among them.
- **The five UARTs went off,** each reading actual 0 within 10 ms.
- **The gate stayed open.** Bit 30 of 0x28e5a0050 read clear through 100 ms with all of SIO's subtree
  off, at the read right before the lane's write, and through 100 ms after it.
- **The lane read as macOS has it before the test wrote it.** Right before its write AFNC2_LW1 read
  target f, actual 0, auto-enable on, auto floor 0, both was-gated flags set: 0x1000030f. The same after
  the write and after the second poll. So with nothing below it on, the lane had gated itself, and its
  word then reads as macOS's does at sleep entry. What it read as found, with the UARTs still at 4, was
  in the lost log; its last read matched that, whichever it was.
- **Put back:** the lane read as found; the UARTs didn't all read as found within 10 ms. Which, and what
  it read, was in the log.
- **Then no resume,** and not the ending the build warned of: the gate never read closed, so by bit 30
  SIO's island kept its power. Why the resume failed isn't known. A restart by itself at about 30 s fits
  a watchdog that a resume re-armed and nothing then fed (inferred; nothing on file says which).

What it settles, all of it in Linux's suspended state:
- **The lane lead is closed.** Linux's lane and macOS's read alike once everything below is off: there
  is no floor for the recipe to fix, and the gate doesn't follow the lane.
- **"Didn't all read back as written"** (0x6013 = 0xdf since run 12) **stays unexplained.** That check
  compares auto-enable, the auto floor and the target only (`m1_pw_write()`, 0x1f00000f), so a lane
  that is up where macOS's is gated passes it: at least one of the four words differed in those fields,
  and this run doesn't say which or how (Pullfrog on #81). What the run adds: the lane's floor read 0
  here before anything of ours wrote it, where Linux has f awake, so that field isn't simply what was
  last written. When it changed was in the lost log.
- **Something else holds 0x28e5a0050 open,** and it isn't on SIO's page: the look's run read every word
  there off after noirq but these five UARTs (SIO and SIO_CPU 0x0f000200). What it is isn't known.
- **So B2's poll of that gate (op 140) stands higher as the stall than before.** Unless something in
  the recipe's state closes it (SIO's and the PMP's firmware stopped, the cores parked, macOS's table),
  B2 would wait there for good, before op 224, which is where run 17 put the stall. Read in the
  recipe's state it never has been.

What was wrong in the build: "if the gate stays open it should come back as the look did". It doesn't. A
`--gate` run is to be treated as one-way.

### The gates read as built: six gate words and the lane, read right before the commit (2026-11-10)

`e1_hook gates_mark=1`, `pkexec e1-hook/run-floor --quick --m1-gates`: run 17's recipe (`--m1-mark`) with
one addition that writes no register. Built, not run. `e1-hook/gates_mark.h` has the words, the byte and
the stages.

- **What it reads.** In step 8, after the stage and the steps' outcomes have gone out and right before
  the commit, CPU0 reads 0x28e5a0050, 0x28e5a0064, 0x28e5a030c, 0x28e09c310, 0x28e09c338 and
  0x28e09c34c, then AFNC2_LW1 (0x28e580160). The load reads the same seven awake and logs them.
- **What it leaves.** One byte at 0x6016: bits 0-5 the six words' bit 30 (set is closed), bit 6 the
  lane at exactly 0x1000030f, bit 7 always. Every scratch byte was taken, so it has the alarm's
  result's place: the alarm's last command sends the gate byte again where its result went, and the
  alarm's own registers at the next boot still say whether it was armed. The run's last stages are its
  own for that reason: 0xcb, 0xd5 and 0xd4 for `--m1-mark`'s 0xd2, 0xd3 and 0xd1.
- **What changes about the run.** One more PMU command before the commit, and seven reads of PMGR
  words in a state they've never been read in (the clocks parked). A read that faults or hangs leaves
  stage 0xcb with 0x6016 still 0, and the Mac restarts by the panic or by WD1's marker; a byte whose
  own command failed leaves the same record. Everything else, the restart's time against the marker
  included, reads as run 17's.
- **Reading it.** 0x28e5a0050 open: B2's op 140 is the stall's likeliest place, and what holds that
  gate is the question. Closed: that poll is cleared, as far as a read before B2's own writes can clear
  it, and `a2_pre=1` is next. The other five are B2's later polls: one that reads open behind a power
  word the recipe has already switched off is a lead of the same kind.

Checked: Pullfrog, the module's build, `tests/m1_crumbs_test.py`, `tests/e1a_launcher_test.py` and the
new code's own mutants (41, all caught); no adversarial pass, since nothing new is written. Built by a
subagent (Opus) and reviewed here.

### The gates read's run: the six gates read as after Linux's own suspend, and the restart came at 90 s again (2026-11-11)

`pkexec e1-hook/run-floor --quick --m1-gates`, in a safe boot, on battery (e3a444d; aurora15;
`results/gates-20261111/`). Dark for about 90 s by Corey's watch, then a restart.

- **The record is whole.** Stage 0xd5, every step's outcome as in run 17, the marker stood, WD2 armed
  and counting with the 120 s bite, `PHASE` 4, and the restart 90.0-92.0 s after the commit. Run 17's
  ending, a second time: B2 doesn't reach its op 224.
- **The reads work there.** Seven PMGR words read with the clocks parked, no fault.
- **The byte: 0xd6.** Awake at the load it was 0x92 (0x28e5a0050 8004030f, 0x28e5a0064 c0040302,
  0x28e5a030c 80040308, 0x28e09c310 8004031d, 0x28e09c338 c004030f, 0x28e09c34c 8004030e; the lane
  0x1f0000ff). Right before the commit:

  | word | awake | before the commit |
  |---|---|---|
  | 0x28e5a0050 (SIO's subtree; op 140) | open | open |
  | 0x28e5a0064 | closed | closed |
  | 0x28e5a030c (ANS2) | open | closed |
  | 0x28e09c310 (the PMP, DWI) | open | open |
  | 0x28e09c338 | closed | closed |
  | 0x28e09c34c (PCIe) | open | open |
  | AFNC2_LW1 | 0x1f0000ff | 0x1000030f, macOS's |

  That is what the look read after Linux's own suspend. Nothing the recipe adds (the stopped
  firmware, the parked cores, macOS's table, the power words) closes a gate or opens one.
- **What it doesn't say, and the plan said it would.** PLAN.md had "open there too" as pointing at
  op 140. It doesn't: the recipe leaves ps_sio, UART_P, SPI_P and SIO_CPU on at the commit, as macOS
  has them (`sio_on=1`, the table's entry 61), and B2 switches them off itself before it polls. So
  0x28e5a0050 was bound to read open here, and likewise 0x28e09c310 with the PMP's power word never
  written. That was knowable before the run. The one reading with the subtree all off is still the
  gate test's, in Linux's suspended state: open.
- **The lane has macOS's whole word at the commit,** so the fabric group's 0xdf is one of the other
  three words (PCIE0_REF, APCIE_GP_SYS, AFNC1_LW2), or its check's timing.

`m1-crumbs`' line for an open 0x28e5a0050 said "the stall's likeliest place"; it now says what the
read can't show. The question it leaves is the one that matters: does that gate close once SIO's
subtree goes off in the recipe's state? The AP making B2's own switch-off there, and polling, is the
nearest read there is, and it's the AP's state before the commit, not B2's at op 140 (PLAN.md).

### `sio_down=1` as built: B2's switch-off of SIO's subtree, made by the AP and put back (2026-11-11)

`e1_hook sio_down=1`, `pkexec e1-hook/run-floor --quick --m1-sio-down`: `--m1-gates`' recipe with one
thing in place of its reads, and this one writes. Run once since (the next section). `e1-hook/sio_down.h` has the words,
their order, the byte and the stages.

- **What it does.** In step 8, after the stage and the steps' outcomes and before the commit, CPU0
  reads the 43 power words of SIO's subtree (0x28e580180 to 0x28e5802d0) as found; writes each 0, in
  B2's order and groups (SIO_CPU and 37 leaves; SIO_ADMA, SPI_P and UART_P; AUDIO_P; SIO), polling each
  group for bit 11 clear after its writes as B2 does, UART_N left out as B2 leaves it out; polls
  0x28e5a0064 and then 0x28e5a0050 for bit 30, up to 100 ms each (B2's ops 139 and 140); reads the 43
  and the six gate words; sends one byte; writes all 43 back in reverse order, each as found less bits
  11, 9 and 8, with up to 10 ms each to read as found; polls 0x28e5a0050 once more, up to 100 ms, to
  read as it did before the first write; and sends the byte again.
- **Why B2's own words, when the gate test had the subtree off already.** There Linux's suspend had
  switched it off, and its words read 0x0f000200 (SIO, SIO_CPU) or carried 0x200; B2 writes 0, which
  also clears the auto floor and bit 28. And that was Linux's suspended state, not the recipe's.
- **Settled while building:**
  - **The words go back before the commit.** Left off, B2's own raise of SIO_CPU (its op 49) would
    wait under an off parent, as run 3's did, and the restart's time would stop comparing with
    `--m1-gates`'. Put back, the commit finds what that run's found, and the byte says whether all 43
    read as found.
  - **Not made:** B2's cycle of GPIO's power word around this (its ops 47 and 90). GPIO is on the
    other power page, whose gates are in 0x28e09c000, so it isn't expected behind 0x28e5a0050; if the
    gate stays open, it's the first thing a later run can add. And B2's raise of SIO_CPU to
    0x0f00024f with its poll of SIO's ASC (ops 49-51): entry 61 has SIO_CPU on already, and the probe
    read that word.
- **The byte, at 0x6016,** under stages of its own (0xba, then 0xbc or 0xbb): bit 0, 0x28e5a0050
  closed after its poll; 1, 0x28e5a0064 closed; 2, all 42 polls met; 3, all 43 read target, actual
  state and auto settings 0; 4, the other four gates as `--m1-gates` found them; 5, all 43 read back
  as found and the gate as before; 6,
  0x28e5a0050 closed before the first write; 7, always.
- **What's new about the run.** 86 writes to 43 power words, 0 and then the value found. SIO's
  firmware has been stopped since before noirq (`sio_stop=1`) if that step went as meant, as it did
  in the last run; the switch-off doesn't check, and `m1-crumbs` gives no verdict unless 0x6012 says it
  did. Between the switch-off and the write-back CPU0 touches only PMGR words and nub-spmi0. If the gate does close, SIO's island loses
  power and comes back with the write-back: the commit then finds a SIO that was power-cycled, which
  no run has had. B2 polls SIO's ASC for bit 1 after its own raise (op 51); ANS's read set after
  such a raise in the probe, SIO's hasn't been read that way. Normally the step adds a few ms before
  the commit, 100 ms while the gate stays open, about 5 s if every poll and wait went unmet.
- **Reading it.** Every poll met, all 43 off, the gate open before:
  - **closed:** the gate can close in the recipe's state, so op 140 is less likely the stall. If the
    restart still comes at 90 s, `a2_pre=1` is next. If it comes later, the power-cycle of SIO's
    island did something, and that's the lead.
  - **open:** a lead on op 140, stronger than the gate test's: B2's words and values, in the recipe's
    state. What else is behind that gate is the question, GPIO first.
  - Either is the AP's state before the commit, not B2's at op 140: the cores are up, A2's DCS and
    fabric moves haven't been made, nor B2's own raises (Pullfrog on #83).
  - A poll unmet or a word not off: inconclusive, and which word isn't recorded. Bit 5 clear: the
    ending doesn't compare with `--m1-gates`'. Stage 0xba with no byte: it stopped in the switch-off.

Checked: the module's build; `tests/m1_crumbs_test.py`, which runs `m1_sio_down()` as it stands
against a model of the registers in twenty scenarios (43 writes of 0 in B2's order, 43 write-backs
in reverse, no other write, in every one); `tests/e1a_launcher_test.py`; the new code's own mutants.
An Opus subagent was to build it and stopped before writing anything (it reported a safety check
cutting off one of its responses), so it was built in the session.

What the reviews of #84 changed. The adversarial pass (one subagent, set on breaking it) found no
wrong write, no loop without a bound, and nothing past the load's check, and three gaps in what the
record claimed:
- the gate wasn't read again after the write-back, so "back" could be said with 0x28e5a0050 still
  closed at the commit: it's now polled once more, and bit 5 needs it to read as before;
- "as found" overstated: the two was-gated flags (bits 8 and 9) are neither written back nor
  compared, so a word that gated in between keeps them, SIO_CPU's bit 9 among them, which entry 61
  had cleared as macOS does. Said now, in `sio_down.h` and in `m1-crumbs`' line; B2 writes that bit
  itself at its op 49;
- the switch-off runs whatever `sio_stop=1`'s outcome was: `m1-crumbs` now gives no verdict unless
  0x6012 says SIO's firmware was stopped.
Pullfrog's one: "all off" compared the target and the actual state only, so a word that kept its
auto-enable or auto floor through the 0, the fields that set B2's 0 apart from Linux's own
switch-off, still counted as off. Bit 3 now needs those to read 0 too.

### The `sio_down=1` run: every bit-11 poll met, the gate open, and a record that can't say the subtree was all off (2026-11-11)

`pkexec e1-hook/run-floor --quick --m1-sio-down`, in a safe boot, on battery (b453922; aurora15;
`results/sio-down-20261111/`: the PMU's record, the clear and collect, the load's lines). Dark for
about 90 s by Corey's watch, then a restart.

- **The record is whole.** Stage 0xbc, every step's outcome as in the two runs before, SIO's firmware
  stopped, the marker stood, WD2 armed and counting with the 120 s bite, `PHASE` 4, and the restart
  89.3 to 91.3 s after the time stamp: run 17's time a third time. Whether it's run 17's ending isn't
  shown, with the write-back unconfirmed (below).
- **The byte: 0x96.**

  | bit | | read |
  |---|---|---|
  | 2 | B2's 42 polls for bit 11 clear | all met |
  | 3 | all 43 words 0 in target, actual state and auto settings | **no** |
  | 6 | 0x28e5a0050 before the first write | open |
  | 1 | 0x28e5a0064 after its poll | closed |
  | 0 | 0x28e5a0050 after 100 ms more | **open** |
  | 4 | the other four gates | as in `--m1-gates`' run |
  | 5 | all 43 back as found, and the gate as before | **not confirmed** |

- **So it's inconclusive, by the rule set before the run.** The gate stayed open, but at least one
  word didn't read all 0, and the one bit doesn't say which word or which field. If a word's actual
  state stayed up, the gate was bound to stay open. If only an auto field read non-zero, the subtree
  was off in its power state, but not shown to be as B2's 0 leaves it, which is the difference from
  Linux's own switch-off this run was built to make: the gate's reading would then be of a state
  between the two (Pullfrog on #85). The check was target and actual state alone until Pullfrog's
  finding on #84 added the auto fields to it; with one bit for both, the run can't tell the two apart.
  That was the record's fault, not the review's: the two belonged in two bits.
- **What it does show:**
  - **B2's bit-11 polls after its SIO writes pass**, all 42, with a core up: its ops 91 to 138 are less
    likely the stall.
  - **The run got through the switch-off and the write-back** with no fault recorded before the
    commit, reached its last breadcrumb after it, and restarted at about the same time as the runs
    before. That isn't the same ending shown: with the write-back unconfirmed, the commit may have
    found SIO's subtree differently, and an SIO parent that wasn't back would stall B2 earlier, at
    its own raise of SIO_CPU, with WD1 still restarting the Mac at 90 s (Pullfrog on #85).
  - 0x28e5a0064 and the four other gates don't move when SIO's subtree is written 0.
- **One explanation would cover three things on file** (judgment, not shown): a power word's auto
  fields don't always read back as written.
  - Since run 12 `ps_words=1`'s last group, four words given macOS's auto settings, "didn't all read
    back as written" in target and auto fields (0x6013 = 0xdf, here too).
  - In the gate test the lane's auto floor read 0 before anything wrote it, where Linux has f awake:
    "that field isn't simply what was last written".
  - Here both bit 3 and bit 5 compare auto fields: bit 3 asks them to read 0 after the 0, bit 5 to
    read as found after the write-back. A word whose auto fields read by their own rule fails both,
    with its target and actual state exactly as meant.
  The load's lines have the 43 awake: SIO 0x1f0000ff, SIO_CPU 0x1400024f, SIO_SPMI0-2 0x0f000200
  with no Linux driver, the rest 0xff, 0x200, 0x244, 0x2ff or 0x300.
- **Next: the same run with a record that separates them** (PLAN.md): target and actual state apart
  from the auto fields, the words' write-back apart from the gate's last read, and, if a word did
  stay up, which.

### The record laid out again, and what's on file about bits 27:24 (2026-11-11)

No run. The first `sio_down=1` run's byte couldn't say whether a word stayed up or only an auto field
read non-zero. Before laying the byte out again, the cheap check PLAN.md asked for: is any of the 43
known not to take a 0 in its auto fields? What's on file says five of them won't read 0 there, and
that it means nothing.

- **Only five of the 43 have the field.** SIO, SIO_CPU and SIO_SPMI0-2 are the only ones whose bits
  28:24 read anything but 0, in the look's two passes and in two loads' awake lines
  (`results/look-20261110/`, `results/sio-down-20261111/load.txt`).
- **With auto-enable set, bits 27:24 read what the actual state reads,** in every read on file: SIO
  0x1f0000ff, SIO_CPU 0x1400024f, ANS2 0x1f0003ff, 0x28e0802e8 and 0x28e0802f0 0x1000030f, awake;
  and the lane above SIO, 0x1f0000ff awake and 0x1000030f after Linux's suspend, with nothing writing
  it in between (the gate test).
- **With auto-enable clear, they read f:** SIO and SIO_CPU after Linux's own switch-off (0x0f000200),
  ANS2 and the two at 0x28e0802e8 (0x0f000300), SIO_SPMI0-2 in every read, with no driver. Linux's
  driver never sets the field (mainline's `apple_pmgr_ps_set()`; aurora's copy not checked): it reads
  the word, clears auto-enable, the flags and the target, and writes the rest back as read. So a word
  that read 4 or 0 there before its switch-off (SIO_CPU; 0x28e0802e8, if it was idle then as it was
  at the look's load) was written with that and reads f afterwards.
- Apple's own writes don't treat it as a setting either: macOS's table writes SIO_CPU as 0x0400024f
  (entry 61: auto-enable cleared, the rest as read), and B2 raises it with 0x0f00024f.
- **So (judgment): bits 27:24 aren't a floor that holds what was written.** Under auto-enable they
  follow the state; without it they read f. The name "auto floor", used here since run 12, was a
  guess. This is the one explanation the last section asked for: `ps_words=1`'s last group "not
  reading back as written" since run 12, and both unconfirmed bits of the first `sio_down=1` run.
  After a literal 0 those five were bound to read 0x0f000000 with their flags, so "all 43 read 0 in
  every field" couldn't be met whatever the subtree did, and "back as found" compared the same field.
  The 0x96 is what a subtree that went off and came back would have left. It doesn't show that it did.

**The byte's second form** (`e1-hook/sio_down.h`; `run-floor --m1-sio-down` as before; the same 86
writes, or none):

- **Bit 6 clear, the subtree went off** (all 43 read target 0 and actual state 0, every poll met, the
  gate open before): bit 0, 0x28e5a0050 closed after its poll; bits 2:1, the auto fields after the 0
  (3 all 0; 2 auto-enable clear in all 43 and bits 27:24 f in some of those five, 0 elsewhere; 1 bits
  27:24 anything else; 0 an auto-enable still set); bit 3, all 43 back in target and actual state;
  bit 4, back in auto-enable, which is a setting (bits 27:24 aren't compared); bit 5, the gate open
  again after the write-back; bit 7 always.
- **Bit 6 set, it didn't:** bits 5:0 are the place in B2's order (0 to 42) of the first word whose
  target or actual state didn't read 0; 62 for a poll unmet with all 43 off; 63 for the gate closed
  before the first write, in which case nothing is written now. What came back isn't recorded in this
  layout.
- 0x28e5a0064's bit and "the other four gates" are dropped: they read the same in both runs so far.
  The six gate words are still read where they were, so the run's accesses up to the byte are the
  first run's.
- **Stages of its own:** 0xbd before the commit, then 0xbf, or 0xbe with an exception pending (0x3d,
  0x3f, 0x3e under K4's bit), where the first form had 0xba, 0xbc, 0xbb. `m1-crumbs` doesn't read a
  record at the first form's stages.
- **Expected, if the desk reading holds and the subtree goes off and comes back:** stage 0xbf, and
  0xbc at 0x6016 with the gate open, 0xbd closed.
- **Code 2 is read as "as B2's 0 leaves them", and `m1-crumbs` says so beside the verdict.** The run
  writes the literal B2 writes to the words B2 writes, so a field that reads f after it reads f
  after B2's too, in this state; the check is there to catch a 0 that didn't take in the power state
  or left an auto-enable on. Pullfrog's point on #85, a state between Linux's switch-off and B2's,
  still holds for codes 0 and 1, which give no verdict on the gate. The restart's time compares with
  `--m1-gates`' only if bits 3, 4 and 5 are all set.

Checked: the module builds; `tests/m1_crumbs_test.py` (the byte against a second writing-out of both
layouts, `m1_sio_down()` as it stands against the register model in 28 scenarios, every byte value
through the decoder at each stage); `tests/e1a_launcher_test.py`; the 99 `siod` mutants, all caught.
No adversarial pass: no new write, and the write path changed only by the early return and by what
the write-back function reports.

Pullfrog's two on #86, both taken. Bit 4 first covered auto-enable and bits 27:24 together, and the
decoder excused it with the argument about bits 27:24: a word that came back without its
auto-enable would have passed as comparable. It's auto-enable alone now, and the time compares only
with all three bits. And the second form first kept the first form's stages, so the filed 0x96 read
as "all off, all 0, GPIO next" under the new decoder: hence the stages of its own.

### The second `sio_down=1` run: the subtree off, the gate still open, and 22 words B2 writes first that nothing here has touched (2026-11-11)

`pkexec e1-hook/run-floor --quick --m1-sio-down`, in a safe boot, on battery (5fb0f10; aurora15;
`results/sio-down-2-20261111/`: the PMU's record, the clear and collect, the load's lines). Dark for
about 90 s by Corey's watch, then a restart.

- **The record is whole.** Stage 0xbf, every step's outcome as in the three runs before, SIO's
  firmware stopped, the marker stood, WD2 armed and counting with the 120 s bite, `PHASE` 4, the
  restart 89.0 to 91.0 s after the time stamp.
- **The byte: 0xb4**, the first layout (the subtree went off):

  | bits | | read |
  |---|---|---|
  | 6 | all 43 target 0 and actual state 0, all 42 polls met, the gate open before | yes |
  | 2:1 | the auto fields after the 0 | code 2: auto-enable clear in all 43, bits 27:24 f only in the five |
  | 0 | 0x28e5a0050, 100 ms after 0x28e5a0064's poll | **open** |
  | 3 | all 43 back in target and actual state within 10 ms each | **no** |
  | 4 | all 43 back in auto-enable | yes |
  | 5 | the gate open again after the write-back | yes |

- **What it shows:**
  - **B2's 0 switches SIO's subtree off from the AP**, every word, in the recipe's state, and the
    desk reading of bits 27:24 held: f in the five after a literal 0, no auto-enable left. The first
    run's "not all 0" was very likely that readback (that run's own words can't be read again).
  - **0x28e5a0050 stays open with all of it off**, B2's words and values. That's the lead on B2's
    op 140 the first run couldn't give. As before it's the AP's state before the commit, not B2's at
    op 140.
  - **A word doesn't read as found in target or actual state 10 ms after its write-back.** Which
    isn't recorded. The plain candidates (judgment): a domain that takes longer than 10 ms to come
    up from fully off, or a word found mid-change. So the restart's time doesn't compare with
    `--m1-gates`' by the rule: 90 s a fourth time, but a parent of SIO_CPU that wasn't back at the
    commit would stall B2 earlier and end the same way.
- **What else B2 does before op 140, from the local listing** (no run; the listing stays out of
  the repo). Between its gate and the first handshake, before any power word, B2 writes:
  - op 8: 0x28e03801c := 0x80101100 (the wake sequence B1 writes 0x80100100 there);
  - ops 9 to 15: 1 to the seven words 0x28e0a0004 to 0x28e0a001c;
  - ops 16 to 29: 1 to the fourteen words 0x28e5b0004 to 0x28e5b0038.

  The two pages are the ones right after the two gate pages (0x28e09c000, 0x28e5a0000). B0, a wake
  sequence, writes the same 21 ones. No run and no look has read or written any of the 22: the
  look's "gates' enables" were bit 31 of the six gate words themselves. Linux's device tree doesn't
  cover those pages, so `pmgr-peek` refuses them. If they are what lets a gate close (inferred from
  where they are and when B2 writes them, nothing more), the AP's switch-off was never going to
  close 0x28e5a0050 without them. And GPIO, the planned next thing, already reads off after
  Linux's own suspend (the look): B2's part there is a cycle, 0x8000000f and then 0, not a
  switch-off the recipe lacks.
- **Next: read the 22, awake and after noirq** (PLAN.md): the look again with them added, read-only.

### The look with the 22 words, as built (2026-11-11)

Built, not run. `probe --look` now reads 120 words: the 98 as before, in the same order, and then
the 22 above, last of all (`e1-hook/look.h`). Nothing is written to any register, as before.

- **Three new marks at 0x6010, one to a page:** 8 for 0x28e03801c, 9 for 0x28e0a0004 to 0x28e0a001c,
  10 for 0x28e5b0004 to 0x28e5b0038. "Every group read" stays 7, the last mark, so the first look's
  record (`results/look-20261110/`) still decodes as finished (Pullfrog on #88: numbered in reading
  order, 7 would have become "stopped at 0x28e03801c"). 0x28e03801c is in a page the recipe's waits
  already read (the ClkGen muxes at 0x28e038164 and on), so only marks 9 and 10 guard pages nothing
  here has read.
- **What a fault would look like.** The first read of each new page is at the module's load, awake,
  before the launcher has saved anything. If it faults the Mac goes down there, and the PMU's
  0x6010 reads 0x19 or 0x1a after the restart (`m1-crumbs`). That B2 writes these words says they
  exist, not that the AP may read them (judgment: likely, since the gate pages beside them read).
- **The log** has three more lines for each look, each naming what B2 writes there:
  `pre 0x28e03801c (B2's op 8 writes 80101100)`, `pre 0x28e0a0004 to 0x28e0a001c, 4 apart` and
  `pre 0x28e5b0004 to 0x28e5b0038, 4 apart`.
- **Checked:** `tests/m1_crumbs_test.py` runs `m1_look()` and `m1_look_init()` as they stand: the
  120 words and no other, each page after its mark, a failed mapping of a new page refused; thirteen
  new mutants (`tests/mutants.py look`). The module builds against aurora15. Pullfrog only, as for
  any step that only reads.

### The second look's run: the 22 words are as the wake sequence leaves them (2026-11-11)

`pkexec e1-hook/probe --look` in a safe boot, 15:12, the build of #88; `results/look-2-20261111/`.
It came back as the first look did: the suspend failed as meant, the log was saved and collected,
the PMU read stage 0x0f with 0x6010 = 0x07 (cleared since). Neither new page faulted.

- **The 22, the same awake at the load and after noirq:**
  - 0x28e03801c = 0x80100100. B2's op 8 writes 0x80101100.
  - 0x28e0a0004 to 0x28e0a001c = 2 2 2 2 3 2 2. B2's ops 9 to 15 write 1 to each.
  - 0x28e5b0004 to 0x28e5b0038 = 2 2 2 2 2 2 2 2 0 0 2 2 1 1. B2's ops 16 to 29 write 1 to each.
- **Read against the local listing:** these are, word for word, the last values the wake sequence
  B1 writes there (its continuation writes the 21 in two commands near its end; B1 itself writes
  0x80100100). Linux's suspend moves none of them. So they are in the state B2 is written to
  start from, as the first look found for the engine's page, the clock generators and the gates.
- **What they look like** (judgment, from the listing alone): selects with a sleep setting, 1,
  which B0 and B2 give all 21, and a running setting which B1 puts back (2 for most). Two of them
  (0x28e5b0034, 0x28e5b0038) already read 1. 0x28e03801c is a clock-control word of the kind the
  table's ClkGen entries write: B1 polls its bit 30 after writing it. B2 polls nothing after ops 8
  to 29; its first handshake comes next.
- **The other 98 words** read exactly as in the first look, at the load and after noirq.
- **What it says about the stall:** nothing is out of place before B2 starts, again. What it
  leaves open is the question the read was for: the AP's switch-off of SIO's subtree
  (`sio_down=1`) left 0x28e5a0050 open with these 22 at their running values, and B2 makes the
  switch-off after moving them. Whether the gate closes once they're moved isn't known.
- **Next** (PLAN.md): the `sio_down=1` rung with B2's ops 8 to 29 made by the AP first. New
  writes, so it gets an adversarial pass.

### What the other projects have since 2026-11-10 (sweep of 2026-11-12)

Read on GitHub at Corey's request: PR descriptions, DeepIdle's `docs/` (status, worklog, its
question for Naeem), commit headlines. No m1n1 or Aura code, no driver source. Nothing posted.

- **omacom/linux-aurora#351 (our alarm series): a third PMU works.** p4ulcristian tested its
  rtc-macsmc unchanged on a J413 (M2 Air, Stowe PMU, aurora16) with a T8112 description, which is
  their draft #454 on top of #351: 3 of 3 awake alarms, 19 of 19 timed s2idle wakes. On Stowe the
  control byte's event bit clears with the event-byte write, not by writing it as 0; the driver's
  sequence covers both. T8112's controller has 256 peripheral interrupts, so patch 1 isn't involved.
- **A Pro and a Max now sleep, on M4.** aurora-silicon/linux#1664 (AxxzyWasTaken, draft,
  2026-11-12): suspend to RAM on a J616c (M4 Max, T6041) at EL2 under aura, entry, wake and full
  resume, 3 and 7 sleeps in a row; a 5 minute sleep cost about 1 mAh. #1477 (fbalazsy, open):
  the same for J616s (M4 Pro, T6040), its controller following "the order of macOS's PMGR quiesce
  and commit (CPU complexes, power gates, axi2af bridges, APCIe, DCS, the MasterMini S2R timer)".
  #1664 notes a reset 5 s after the commit when the parked cores hadn't been stopped. These are
  the first published completed entries on Pro or Max chips; both go through aura at EL2, a path
  this Mac doesn't have. Their sources weren't read.
- **DeepIdle (M2 Pro, T6020) is at the wall we're at,** runs R25 to R38B (2026-11-11). With every
  core asleep and nothing touched after the commit, WD2 (which the commit reloads to 60 s) resets
  the Mac 61 s later. What they saw with an observer core:
  - in the PMGR's state counters (a performance-counter block of their own, 0x29e2c8000 there):
    the system state moves from `SYS_AWAKE` to `SYS_SLP_DDR`; the SoC-level state stays `AWAKE`;
  - in `MPMGR_AON` (0x29e5c0000, the block whose + 0x4000 is the timer; T6000's is 0x2925c0000):
    five words change within 4 ms of the commit and then nothing moves: + 0x14 3 → 1, + 0x100
    8 → 0xa, + 0x4000 1 → 0, + 0x4008 0x00010000 → 1, + 0x400c 0 → 1; none changes without it;
  - pages 8 to 10 of that block (+ 0x8000 and up) hang whoever reads them, from Linux too;
  - no change from Naeem's global-sleep order (cores arm deep sleep only after the commit, R38A)
    or from the SPMI interrupt word as macOS leaves it (R38B).

  Not compared here: T6000's 0x2925c0014 was already on the list of reads worth making
  ("Before the feasibility run"), and nothing on this Mac has read it or + 0x100, + 0x4008, + 0x400c.
- **Naeem Malik (T8103):** nothing public since 2026-11-09 (linux-aurora 11-08, the lab's hand-over
  note 11-09). DeepIdle's worklog says he answered their question with "working on the driver".
  The 0.05 W over 3 hours is his post of 2026-11-07. A newer post wasn't found by search.
- **maralcbr/linux:** no push since 2026-11-05.
- **Diljit Singh (T8140):** the sleep core (#1445) is merged in aurora-silicon; #1658 (merged) makes
  the Neo's wake 0.21 s faster.

### The M4 Pro's and M4 Max's sleep drivers, read (2026-11-12)

Corey's OK of 2026-11-12. Read: `drivers/soc/apple/t6040-sleep.c` in aurora-silicon/linux#1477
(fbalazsy, at 7aefde062d64) and `t6041-sleep.c` in #1664 (AxxzyWasTaken, at 84f27ca4d316), both
`GPL-2.0-only OR MIT`, both on Diljit Singh's T8140 work (#1208, #1445). Their comments and
constants, not every function. Their values come from macOS traced under aura, which wasn't read.
Nothing was posted. What follows is their authors' findings on M4, with what on T6000 looks like
the same thing (the mapping is judgment unless a run of ours is cited).

- **It's the same engine, laid out the same way.** They call the entry program A2D (iBoot's
  reconfig sequence 24) and the next one D2S. A2D there waits on the same words as B2 here: an
  engine page whose + 0x40 and + 0x44 (bits 2 and 3) are its gates and whose + 0x114 is a
  progress code (4, 0x10, 5, 7); it powers GPIO on first, polls SIO's coprocessor word for bit 1,
  writes SIO_CPU := 0x80000000, switches groups of power words off and polls each for bit 11.
  That is B2's opening, op for op, with T6000's page at 0x2922c0000 (PHASE at + 0x110).
- **Bit 11 of a power word is a latch, not only "busy"** (their PARENT_OFF or PARENT_MISSING). A
  block raised under an off parent, lowered before its children, or lowered with its axi2af
  bridge still armed keeps bit 11, and the engine, which polls it clear after each of its own
  writes, then waits for good. On the M4 Max six words latched that way from the driver's own
  teardown, and on a second sleep of a boot two more did; on the M4 Pro the engine sat on the
  storage PCIe PHY's word (APCIE_PHY_SW) until macOS's PCIe quiesce was done first.
  - Here: none of the 51 words the look reads has bit 11 set after Linux's own noirq (checked
    today in `results/look-2-20261111/look.txt`), and the AP's switch-off of SIO's 43 met every
    poll. Not checked: the words after the recipe's own writes, `ps_words=1`'s above all (its
    last group "didn't all read back as written" in every run since 12).
- **The first gate, engine page + 0x40, is "the AP partition that does not power down".** What
  the M4 Pro's driver records as holding it: cores that hadn't reached their last WFI (the
  complexes' words never dropped bit 4). It also says a pending wake source that the commit's
  masks let through would hold it, and reads for one in its dry run; neither driver records that
  as a stall that happened (Pullfrog on #91), so here it is a question, not a finding.
- **The engine also raises domains and waits for them, with no timeout.** On the M4 Pro it
  stalled raising ATC0_COMMON: its parent fabric read 0x1000030f (auto, level 0) and gates itself
  when nothing below it is on, which was the case after Linux's suspend; macOS has the children
  on at the commit. The fix was to raise the fabric and the four commons by hand right before
  the commit. Here AFNC2_LW1 reads the same 0x1000030f, and B2 raises GPIO and SIO_CPU itself.
- **Cluster words:** the two extra complexes' words (+ 0xc, + 0x10; here the display's) must read 0
  at the commit, flag included, or the engine has a sequence to wait for. Run 16 did this here.
- **After A2D, the memory side.** D2S polls the DCS power gates, which close only in dynamic
  power-gate mode (+ 0x20 bits 8 and 12 on each DCS block), which macOS sets at boot and after
  each wake and Linux never does; the M4 Max also needed the AMCC error enables off and macOS's
  cache flush. With the mode on, the M4 Pro's AOP still held a DRAM request.
- **Cores:** "one left awake keeps the engine waiting until the watchdog resets the SoC", as
  T8140's author wrote and as every run here assumes.

**What it changes here** (judgment). The AP-side tests of op 140 (the gate test, both
`sio_down=1` runs) may be testing the wrong thing: on M4 each stall was a word the engine waited
on because of what the kernel had left around it (an off parent, a latched bit 11, a core),
found by reading the engine's program against the state at the commit. The same reading
of B2 hasn't been done here with these classes in hand, nor with the wake-source question. It
costs no run.

### B2 read against the M4 classes: the order holds, the raises are covered, and the PCIe ports are live at the commit (2026-11-12)

No run. PLAN.md's desk step: B2's ops up to 224 from the local listing (which stays out of the
repo), against the four classes of the section above, with the two looks, the runs' records, the
live device tree's parent links and `e1_hook.c`. Aurora's `drivers/pci/controller/pcie-apple.c` was
read for one fact, and the M4 Pro's driver again for its PCIe and power-gate comments. Nothing
was posted.

**1. Bit 11 as a latch.** B2 writes 0 to 52 power words and polls 49 of them for bit 11.
- **B2's order is sound against the device tree:** no word goes off before a child of its own, and
  no child the tree knows of is left on behind one (GPIO, a child of SIO and PMS, goes off before
  SIO; the three PCIe `_SYS` words before APCIE_ST and APCIE_GP, and before ANS2).
- **SIO's 43 are covered:** the AP's own 0 to each, in the recipe's state, met all 42 of their
  polls (the second `sio_down=1` run).
- **Of the other seven** (GPIO, the three PCIe words, ANS2, `ps_pmp`, DWI), none has bit 11 set
  after Linux's noirq, and the recipe writes one of them afterwards: APCIE_GP_SYS, which
  `ps_words=1` gives macOS's 0x1000030f. No run has read it since.
- **The PCIe ports are live at the commit, and macOS's aren't** (the first three are on file, the
  last is judgment):
  - macOS has APCIE_GP_SYS at 0x1000030f at sleep entry: auto-enable set, actual state 0, so idle
    and gated by itself (bits 27:24 follow the actual state, "The record laid out again").
  - Linux has it at 0x1f0000ff awake and after noirq (both looks): the same auto-enable, actual
    state f. The lane above SIO, in the same mode, did gate itself once SIO's subtree was off (the
    gate test). So this block isn't idle.
  - Aurora's PCIe suspend returns at once for a host that isn't tunneled
    (`apple_pcie_suspend_noirq()`): the internal host's ports (Wi-Fi and Bluetooth, the card
    reader) keep their link and their clocks through the suspend.
  - Since run 12 `ps_words=1`'s last group "didn't all read back as written", and the gates read
    narrowed that to PCIE0_REF, APCIE_GP_SYS or AFNC1_LW2: this block's own chain. A word that
    can't gate reads f in bits 27:24 where 0 was written.
  - Both working drivers stop these ports themselves before the commit (T8140's "radio port
    stop", kept by the M4 Pro's: the link into L2, PERST#, the PHY's clocks and the port's clock
    off, then `apcie_sys_gp` lowered with its bridge bits cleared), and the M4 Pro's engine sat on
    a PCIe word until macOS's quiesce was done first. Here B2 writes the 0 itself (op 143), polls
    bit 11 (op 146) and then the gate 0x28e09c34c (op 156), which read open at the commit. A 0
    that meets a live port is the latch class, at a word nothing here has tested.

**2. What B2 raises, and under what.**

| raise | parents in the device tree | at the commit |
|---|---|---|
| GPIO (op 47, not waited for) | PMS, SIO | PMS always on; SIO raised by `sio_on=1` |
| SIO_CPU (op 49, actual f) | SIO, UART_P, SPI_P, AUDIO_P | the first three raised by `sio_on=1`; AUDIO_P at target 0, as B1 leaves it; SIO_CPU reads on |
| ANS2 (op 149, actual f) | AFNC2_LW0 | 0x1f0000ff, never written here; ANS2 came up within 10 ms in the probe |
| `ps_pmp` (op 157, actual f) | none | on, never written |

  Nothing B2 raises sits under a parent that is off or has gated itself. The M4 Pro's stall (a
  domain raised under a lane at 0x1000030f with nothing on below) has no counterpart in B2's own
  raises. The nearest thing on file is ours: the second `sio_down=1` run's write-back raised SIO
  again under AFNC2_LW1 at 0x1000030f with all of it off, and at least one word wasn't back in
  10 ms.

**3. The first gate.** Nothing new can be read. With CPU0 up it holds (run 6), which is what the
M4 Pro's driver says a core does; with CPU0 down nothing can see it, and nothing B2 writes before
op 224 outlasts the reset. For a pending wake source, what's on file: CPU0 had no exception
pending before its last WFI in both `sio_down=1` runs (their stages); and the seven words
inferred to be the AP's wake status (0x292004024, right after the masks, as on T8103 and T6020)
read 0 awake and showed no new bit after the commit in run 2, the one run that sampled them
there, with CPU7 up. That address is inferred, not confirmed, and whether those words are what
the first gate follows isn't known (the M4's are in its AOP's block). So nothing on file points
at a wake source, and nothing rules one out with every core down.

**4. After B2.** The next sequence waits, with no timeout, for the 16 DCS power words to read
actual state 0. Linux already has all 16 in auto mode (0x1f0000ff or 0x1f0002ff awake,
`results/sleep-peek-20261104T120619/`), where the M4's Linux had them fixed on, and that sequence
itself writes a word with bits 8 and 12 set to each DCS block before it waits. So the M4's
missing power-gate mode has no counterpart to add here, as far as a listing shows. It is past
op 224 in any case.

**The six gates, with the M4 Pro's driver beside them.** Each gate word is the first of a
five-word entry in a table B1 writes whole (42 entries from 0x28e5a0000, 55 from 0x28e09c0e0,
0x14 apart). The M4 Pro's driver has the same kind of block ("its pwrgate block reports bit 30"
once every domain behind it is at 0), which is the reading of bit 30 here. The 21 words B0 and
B2 set to 1 are in the pages right after the two tables. Two of them read 1 awake already and
ANS2's gate does close under Linux, while SIO's doesn't with its word at 2: that fits the 21
being the tables' mode, sleep's being 1 (inferred, and which word covers which gate isn't
known). If so the AP's test of op 140 was bound to read open, and B2 passes it. `pre=1` is still
the test of that.

**Where B2 can stall before op 224, in its order** (judgment):

| ops | wait | now |
|---|---|---|
| 1 to 6 | the first gate | can't be read with every core down |
| 31 to 34 | the handshake, behind A2's three p-state moves | untried (`a2_pre=1`) |
| 50, 51 | SIO_CPU up, SIO's word | passed for the AP (the probe) |
| 91 to 138 | bit 11 of SIO's 43, GPIO's | passed for the AP but GPIO's (second `sio_down=1` run) |
| 139, 140 | two gates | 0x28e5a0050 open for the AP, with its mode words unmoved |
| 144 to 146 | bit 11 of the three PCIe words | **untried, with live ports behind APCIE_GP_SYS** |
| 150 to 156 | ANS2 up and off, three gates | ANS2 passed (the probe); 0x28e09c34c is PCIe's |
| 158 to 164 | `ps_pmp` up and off, DWI, a gate | the raise passed; the rest can't be made by the AP (`ps_pmp` is never written) |
| 177 to 192 | eleven clock generators, two words of the engine's page | untried |

**What it picks** (PLAN.md has the order). The PCIe step is the one place where the state at the
commit is known to differ from macOS's in a way two working drivers had to fix, and one small run
tests it from the AP with a reading that doesn't depend on a gate: B2's own five PCIe writes made
before the commit, bit 11 and the actual state recorded, and the words left off so that B2 finds
them done. A restart at 120 s instead of 90 s would say B2 got through. `pre=1` comes after it.

### `pcie_down=1` as built: B2's PCIe switch-off, made by the AP and left (2026-11-12)

`pkexec e1-hook/run-floor --quick --m1-pcie-down`, `--m1-gates`' recipe with one thing in place of its
reads (`e1-hook/pcie_down.h` has the words, the byte and the stages). Built on Corey's standing go-ahead
of 2026-11-10; nothing in it outlasts the restart.

- **What CPU0 does in step 8, right before the commit:** reads APCIE_GP_SYS as found; writes a
  literal 0 to APCIE_ST_SYS, APCIE_ST1_SYS and APCIE_GP_SYS (0x28e0802e8, 0x28e0802f0, 0x28e080320:
  B2's ops 141 to 143); polls each for bit 11 clear, up to 100 ms, as B2's ops 144 to 146 do; sends
  the byte; writes 0 to APCIE_ST and APCIE_GP (0x28e080148, 0x28e080150: ops 147, 148), with up to
  100 ms each to read off; polls the gate 0x28e09c34c (op 156) for bit 30, up to 100 ms; and sends
  the byte again.
- **The new writes: those five, each a 0, each one B2's own, made a little early.** Nothing is
  written back, so B2 finds the five off and its own 0 changes nothing. No PCIe register is touched,
  and nothing after it in the run touches one.
- **B2's plain 0, not the M4 Pro driver's by-hand version** (auto-enable cleared first, then the
  bridge's bits): what the run asks is what B2's own write meets.
- **The byte, at 0x6016:** bit 0 APCIE_GP_SYS found up; bit 1 found with bit 11 already set (the
  recipe's own `ps_words=1` write would be why); bit 2 its poll met; bit 3 it read off and idle
  (target 0, actual state 0, bit 11 clear) at the send's last read; bit 4 the two storage words'
  polls met and both off and idle; bit 5 APCIE_ST and APCIE_GP off;
  bit 6 the gate closed; bit 7 always. 0xbd is a clean switch-off with the gate open.
- **Stages of its own:** 0x1d before the commit, then 0x1f, or 0x1e with an exception pending
  (0x9d, 0x9f, 0x9e under K4's bit).
- **What it can say:**
  - Bit 3 clear in the second send: the word wasn't off and idle at the last read before the
    commit, with the ports live. That is a lead on B2's op 146, the M4's latch by the look of it,
    and not proof that B2 waits there for good: the AP's 100 ms isn't the engine's wait, and the
    restart's time still says how far B2 got (Pullfrog on #93). At 90 s, the ports' stop is the
    build. Bit 2 clear with bit 3 set: bit 11 was slow, and had cleared by the commit.
  - 0xbd or 0xfd and a restart at about 120 s: B2 got past its op 224 for the first time.
  - 0xbd or 0xfd and 90 s: B2's PCIe step passes as the AP makes it; the stall is elsewhere.
- **Guards:** the five addresses are pinned at build time, each offset against `ps_pmp`'s (the
  never-write word is in the same page) and the list's length too; the load compares the list with
  the five offsets written out a second time, refuses if one is on the never-write list or a page
  isn't mapped, and logs the five and the gate awake.
- **The adversarial pass** (a subagent, on f842e85) found no path to a write outside the five. Its
  three findings, all taken: the decoder's clean verdict ignored a word found already down, parents
  that stayed up and the stage; the load's check would have let a short or wrong list through
  (a list one short ends in offset 0, a core's own word); and two comments said all five were read
  as found where only APCIE_GP_SYS is.

Checked: the module builds; `tests/m1_crumbs_test.py` (the header against a second writing-out in
1,600 cases; `m1_pcie_down()` as it stands against a register model in 28 scenarios; the load's
refusals; every byte value through the decoder at each stage; `--clear`); `tests/e1a_launcher_test.py`;
the 221 `pcd`, `gates` and `siod` mutants, all caught; and one adversarial pass, as for every build
that writes.

Pullfrog's two on #93, both taken. The decoder read an unmet poll or a word still up as proof
that B2 stops at op 146 and told the reader to set the restart's time aside: the AP's 100 ms isn't
the engine's wait, so it's a lead, and the time is still read. With that, bit 3 now asks for bit
11 clear as well, so the last read before the commit says whether a slow bit 11 had cleared. And
the clean verdict claimed all five off without the second send's bit: the adversarial pass had
found the same.

### The `pcie_down=1` run: the PCIe words go off cleanly, their gate closes, and the restart still comes at 90 s (2026-11-12)

`pkexec e1-hook/run-floor --quick --m1-pcie-down`, in a safe boot, on battery (764f78f; aurora15;
`results/pcie-down-20261112/`: the PMU's record, the clear and collect, the load's lines). Dark for
90 s by Corey's watch, then a restart.

- **The record is whole.** Stage 0x9f, every step's outcome as in the runs before, the marker
  stood, WD2 armed and counting with the 120 s bite, `PHASE` 4, nothing pending on CPU0, and the
  restart 88.9 to 90.9 s after the time stamp.
- **The byte: 0xfd,** every bit as meant and one more:

  | bit | | read |
  |---|---|---|
  | 0, 1 | APCIE_GP_SYS as found | up, bit 11 clear |
  | 2 | its bit-11 poll after the 0 | met |
  | 3 | at the last read before the commit | off and idle |
  | 4 | the two storage words | polls met, off and idle |
  | 5 | APCIE_ST and APCIE_GP | off within 100 ms |
  | 6 | the gate 0x28e09c34c | **closed** within 100 ms |

- **Awake at the load:** APCIE_ST_SYS and APCIE_ST1_SYS 0x1000030f (gated by themselves),
  APCIE_GP_SYS 0x1f0000ff, APCIE_ST and APCIE_GP 0xff, the gate 0x8004030e.
- **What it shows:**
  - **B2's PCIe step isn't the stall.** Its own 0 takes on APCIE_GP_SYS with the ports live: no
    latch, bit 11 clear, off within the poll. The M4's class doesn't show on this chip at this
    word. And with all five left off, so that B2 had nothing to do there, the restart came at
    90 s all the same: B2 still doesn't reach its op 224. The time compares with `--m1-gates`'
    (nothing here was written back).
  - **A gate closes for the AP, in the recipe's state, with the 21 words unmoved.** 0x28e09c34c
    read open right before the commit in `--m1-gates`' run and closed here within 100 ms of
    what's behind it going off. So B2's poll at op 156 would be met too, and the desk reading's
    guess that the 21 words are what lets a gate close is wrong for this one at least (the seven
    of its page read 2 2 2 2 3 2 2 throughout).
  - **So 0x28e5a0050 staying open is a lead again.** It stayed open with all 43 words of SIO's
    subtree off, B2's values, in the same state (the second `sio_down=1` run). "It was bound to
    read open without the 21 words" no longer excuses that: either something else behind that
    gate is still on, or its own page's words differ from this page's. That is B2's op 140, before
    the PCIe step in its order, and the first poll on file that the AP's own switch-off doesn't
    meet.
- **Left of B2's power-down that the AP hasn't made:** its cycle of GPIO (0x8000000f, then 0:
  GPIO's parents are PMS and SIO, and B2 makes the cycle inside its switch-off of SIO's subtree),
  the 22 words, the PMP's and DWI's switch-off with their gate (the PMP's word is never written
  here), and the clock generators.

### `sio_steps=1` as built: the switch-off with GPIO's cycle inside it, then the 22 words, the gate after each (2026-11-12)

Built, not run. `pkexec e1-hook/run-floor --quick --m1-sio-steps`, on `--m1-gates`' recipe
(`e1-hook/sio_steps.h` has the words, the order, the byte and the stages). It is the build PLAN.md
called `pre=1` in stages: one run that asks which of the two things B2 does before its op 140, and
the AP hasn't, lets 0x28e5a0050 close.

- **What CPU0 does in step 8**, where `--m1-gates` reads the gates, before the commit:
  1. reads the 43 words of SIO's subtree as found, and the gate once (closed already: nothing is
     written, byte 0xa0);
  2. GPIO's power word 0x28e080120 := 0x8000000f (B2's op 47: on, with bit 31, the reset), with SIO
     still on as in B2, and up to 100 ms for its actual state to read f;
  3. `sio_down=1`'s switch-off, B2's order and groups, with GPIO := 0 right after the first 38
     writes and before their polls (B2's op 90), and GPIO's bit 11 polled after them;
  4. 0x28e5a0064 and 0x28e5a0050 polled for bit 30, up to 100 ms each, and a byte to 0x6016;
  5. only if all 43 went off (their auto fields as B2's 0 leaves them) and the gate is still open: B2's ops 8 to 29, a byte after each group
     (0x28e03801c := 0x80101100; 1 to the seven; 1 to the fourteen, in B2's order), each read back,
     and the gate polled again;
  6. the byte in its second layout; the 43 written back in reverse, as found, with up to 100 ms
     each (10 ms wasn't enough for one word in the second `sio_down=1` run); the gate polled to
     read open; the byte again.
- **Settled in the build:**
  - *GPIO's cycle goes inside the switch-off, where B2 has it,* and the switch-off alone isn't
    made first: GPIO's parents are PMS and SIO, and a raise under an off SIO is the M4's
    raised-under-an-off-parent class, which would muddy both readings. The switch-off alone has
    been read twice already (open), so the first reading here is "with GPIO's cycle" against those.
  - *The 22 go in after the switch-off, not before it as in B2,* so that the gate can be read
    between the two. If the gate only follows them when they're in place first, this run reads open
    and can't tell: a second pass in B2's own order is the follow-up then.
  - *What goes back:* the 43, as before (B2's own raise of SIO_CPU would otherwise wait under an
    off parent). They go back with the 21 at 1, which the plan worried about; it is the wake
    sequence's own order (B1 raises its power words first and puts the 21 back near its end), and
    bit 5 says whether they came. GPIO stays at B2's 0 (Linux's suspend has it off, 0x200, already) and the 22 stay
    as B2 writes them: B2 writes the same values to the same words first thing, so the commit finds
    nothing there that B2 wouldn't have put there.
- **New writes: 24, to 23 words.** Two to GPIO's word, one to 0x28e03801c, 21 ones. Built on Corey's standing
  go-ahead of 2026-11-10; none outlasts the restart. GPIO's word is in `ps_pmp`'s page: its
  address is pinned against `ps_pmp`'s at build time, and the load refuses unless each of the 23
  is the word named (a second writing-out of both lists), none is on the never-write list, and
  every page is mapped. What the reset does to the pins behind GPIO is what B2 does to them at
  every macOS sleep; nothing here uses them afterwards (the record goes through nub-spmi0).
- **The risk the plan named:** some of the 21 may select something a running core needs, and the
  engine moves them with every core down. A byte after each group names the one a stop falls in;
  WD1's marker restarts the Mac at 90 s.
- **The byte** (0x6016, stages 0x95, then 0x97 or 0x96). Second layout (bit 6 set): bit 0 the gate
  closed after the switch-off with GPIO's cycle; bit 1 closed after the 22; bit 2 the subtree off,
  every poll met; bit 3 GPIO's cycle made; bit 4 the 22 read back as written; bit 5 the 43 back
  and the gate open again. 0xfc: all made, the gate open throughout. 0xed: closed with GPIO's
  cycle. 0xfe: closed once the 22 were in. First layout (bit 6 clear): where a run that stopped
  had got to.
- **Reading it** (`m1-crumbs` says the same):
  - *0xed:* the gate follows GPIO's pass through on-with-reset. B2 makes it itself, so op 140 is
    cleared as far as the AP can clear it; `a2_pre=1` is next.
  - *0xfe:* the gate follows the 22, or some of them, or it closes by itself later than the
    first poll waits (the second comes some hundreds of ms after it, and no run has watched the
    gate that long with the subtree off). Either way B2's own poll would be met: the same
    conclusion for op 140.
  - *0xfc:* neither, as the AP can make them. The lead on op 140 stands: something else behind
    that gate is still on, or it only closes in B2's own state.
  - Each is the AP's state before the commit, not B2's at op 140 (Pullfrog on #83).
- **Checked:** `tests/m1_crumbs_test.py` compiles the header against a second writing-out, and
  runs `m1_sio_steps()` as it stands against a model of its six pages in 26 scenarios (every write
  in order and no other, `ps_pmp`'s word untouched), the load's refusals, and the decoder for
  every byte at each stage; `tests/e1a_launcher_test.py` has the option; 91 new mutants
  (`tests/mutants.py sst:`), and 28 older ones repaired. One adversarial pass, as for any build
  that writes.
- **Pullfrog on #95**, three points, all taken: 0x28e03801c counts as written only once it reads
  B2's value with bit 30, pending, clear (B1 polls that bit; the first build masked it out); the
  subtree counts as off only with its auto fields as B2's 0 leaves them, `sio_down=1`'s codes 2
  and 3 (a leftover auto-enable gave a clean verdict before); and the decoder's baseline claim,
  below.
- **The adversarial review of #95** found no path that writes anything but the words meant, and
  these, all taken: the decoder said the commit found GPIO at 0 and the 22 as B2 writes them on
  bit 5 alone, whatever bits 3 and 4 said; the 0xfe verdict didn't allow for a gate that closes
  late by itself; a stop in the 22 and a failed command read as if the restart's time told them
  apart (it only tells a panic from the rest; and after a failed command the run goes on
  unrecorded, so that case isn't bound to end at 90 s: Pullfrog's second pass); and the model had no slow register, so none of
  the new waits, GPIO's two checks apart, the 0x28e5a0064 poll, or the auto-enable's part in
  "back" was pinned by a test. Ten scenarios and twelve mutants more.

### The `sio_steps=1` run: the gate stays open with GPIO's cycle made; the 22 don't all read back; the 43 aren't shown back; 90 s again (2026-11-12)

`pkexec e1-hook/run-floor --quick --m1-sio-steps`, in a safe boot, on battery, 15:00 (a086f30;
aurora15; `results/sio-steps-20261112/`: the PMU's record, the clear and collect, the load's
lines). Dark for about 90 s by Corey's reckoning (they missed the start), then a restart.

- **The record is whole.** Stage 0x97, every step's outcome as in the runs before, SIO's firmware
  stopped, the marker stood, WD2 armed and counting with the 120 s bite, `PHASE` 4, the restart
  89 to 91.5 s after the time stamp (two reads).
- **The byte: 0xcc**, the second layout (the pass went through):

  | bit | | read |
  |---|---|---|
  | 2 | all 43 off, every poll met, auto fields as B2's 0 leaves them | yes |
  | 3 | GPIO's cycle made: up within 100 ms of 0x8000000f, then off and idle | yes |
  | 0 | 0x28e5a0050 after that | **open** |
  | 4 | the 22 written and all read back as written | **no** |
  | 1 | 0x28e5a0050 after them | **open** |
  | 5 | all 43 back as found within 100 ms each, and the gate open again | **no** |

- **Awake at the load:** GPIO 0xff; 0x28e03801c 0x80100100; the seven 2 2 2 2 3 2 2; the
  fourteen 2 2 2 2 2 2 2 2 0 0 2 2 1 1; the gate 0x8004030f. As both looks had them.
- **What it shows:**
  - **GPIO's cycle isn't what the gate follows.** With SIO's subtree off as B2 switches it off
    and GPIO taken through on-with-reset and off, where B2 does it, 0x28e5a0050 stayed open: the
    third reading of that gate with the subtree off, and the first with the cycle. This half of
    the run is clean.
  - **The 22 are written, and not all of them read back as B2 writes them.** Which isn't
    recorded: the byte has one bit for all three groups. The candidates (judgment): 0x28e03801c's
    bit 30, pending, not clearing within 100 ms with the cores up (B1 polls it after its own
    write, 255 tries); or a word of the 21 that doesn't take a 1 in this state. The local listing
    says the 21 are plain settings as far as the engine goes: B0, B1 and B2 write them 0, 1 and 2
    and never read one, and what both looks read is, word for word, what B1's continuation
    writes. The same page has a word at + 0 that B0 and B1 write (0x2000000) and nothing here
    has read. So the gate staying open after them says nothing yet about the 22.
  - **The 43 aren't shown back, with 100 ms each.** The second `sio_down=1` run had the same
    with 10 ms (target or actual state), so the wait wasn't the reason. Either a word can't
    read as found again (SIO_CPU is the candidate: awake it reads target f with actual state 4,
    under its firmware, which is stopped by step 8), or, new here, the subtree doesn't come up
    with the 21 at 1. The byte doesn't say which, nor whether it was the words or the gate.
  - **90 s again**, which by the rule doesn't compare with `--m1-gates'`: the commit isn't shown
    to have found SIO's subtree as the recipe leaves it, and the 22 not as B2 writes them.
- **What the record lacked.** One bit for "the 22 read back" and one for "back" were too few: the
  run's two open questions are both "which word". A run's stages leave 0x6016 alone; the probe's
  stage has seven bytes free (0x6005, 0x6011 to 0x6016, as the gate test used them).
- **Next** (PLAN.md), to be built, nothing to run yet: the 22 written and read back by
  themselves, in the look's state, with the whole read-back in the log and in the PMU: which of
  them take B2's values with the cores up, and whether bit 30 clears.

### `pre_test=1` as built: the 22 words by themselves, after the look, every read-back recorded (2026-11-13)

Built on Corey's standing go-ahead of 2026-11-10 and "take the next step" of 2026-11-13; not run.
`pkexec e1-hook/probe --pre` loads the look's parameters and `pre_test=1` (`e1-hook/pre_test.h`).

- **What it asks.** The `sio_steps=1` run's bit 4 said the 22 words B2 writes first didn't all read
  back as written, in the recipe's state. Which, and what they read, with nothing of the recipe
  done: after the look that follows noirq, cores up, no firmware stopped.
- **The writes: 44, to the 22 words and no other; none outlasts the restart.** B2's own values
  (0x28e03801c := 0x80101100; 1 to 0x28e0a0004-1c and 0x28e5b0004-38, B2's order), which
  `sio_steps=1` wrote once already; and, new, each written back as found (0x28e03801c first, less
  bit 30, which is a status bit; then the 21 by address: B1's order, b1.lst and b1b.lst). New too
  is the state: the kernel is running, and is meant to resume. These are clock words B2 writes
  with every core down, so the run is handed over as one-way.
- **The reads beside them.** The six gate words three times. And the + 0 word of the two select
  pages, 0x28e0a0000 and 0x28e5b0000, which B0 and B1 write 0x2000000 and nothing here had read:
  first awake at the load, under a mark of their own (0x91), then four times in the test. Only
  read.
- **The record.** The log has every word at every step. The PMU, for a run that doesn't come back:
  the step at 0x6010 (0x80 set); six bytes at 0x6011-0x6016, sent in one command with the
  write-back's first mark, before anything is written back: two bits for each of the 21 (as
  written, as found, as written with more bits set, something else) from the read 100 ms after the
  writes, the same for 0x28e03801c at the end of its 100 ms poll and again 100 ms on, whether the
  21 read the same at once as later, and whether bit 30 was seen set right after the write; and a
  byte at 0x6005 after the write-back: 0x28e03801c back, and the first select that isn't.
- **What the reviews changed** (the adversarial pass and Pullfrog on #97; neither found a write
  to anything but the 22, or a word not written back). The decoder named the recipe's state
  whenever all 22 read as written 100 ms on; `sio_steps=1` judged the 21 at once and 0x28e03801c
  within 100 ms, before the 21, so now only a record that passes those same reads does, and one
  that passes only later says that run's "no" may be this alone. A clock word that went back
  isn't said to hold B2's value. 0x28e5b0034 and 0x28e5b0038 are found at 1 and show nothing:
  19 of the 21 are shown. The test isn't made, and nothing is written, if the PMU doesn't take
  the zeroing of 0x6005 (an old byte there would read as this test's end) or the first write's
  mark (a test that didn't come back would leave a plain look's record).
- **Checks.** `tests/m1_crumbs_test.py`: the header against a second writing-out; `m1_pre_test()`
  as it stands against a model of the 22 in 21 scenarios (bit 30 clearing at once, late, later
  than the poll, never; the word not taking; a select stuck, slow, or reading with more bits; words
  not coming back; PMU writes not acked; old bytes left; a word that goes back); the load; the decoder. 78 mutants, all
  caught. `tests/e1_hook_probe_test.py` for `--pre`.

### The `pre_test=1` run: none of the 22 takes the AP's write; the Mac comes back (2026-11-13)

`pkexec e1-hook/probe --pre`, run once by Corey in a safe boot, module of c4e923e
(results/pre-test-20261113/: the saved log, the load's look, the PMU record).

- **It came back**: the screen went black for a second or two, the suspend failed as meant, the
  log was saved. PMU record 0x8a at 0x6010, `aa aa aa aa 6a 69` at 0x6011-0x6016, 0xc0 at 0x6005.
- **Not one of the 22 changed.** 0x28e03801c, written 0x80101100, read 0x80100100 at once, through
  its 100 ms and 100 ms after: no bit 30, no change. The 21 read 2 2 2 2 3 2 2 and
  2 2 2 2 2 2 2 2 0 0 2 2 1 1 before the writes of 1, right after them, and 100 ms on: the same
  row three times. The two that count as "as written" are the two found at 1. Written back as
  found, all 22 read as found.
- **Beside them:** the six gate words didn't move (0x28e5a0050 open, 0x8004030f, as in every look).
  0x28e0a0000 and 0x28e5b0000, read for the first time, both read 0x02000000, the value B0 and B1
  write there, awake and after noirq.
- **What it says.** In Linux's state after noirq, with the cores up, the AP's writes to these 22
  words are dropped: the words are read-only to it there, or locked. So `sio_steps=1`'s "the 22
  didn't all read back" was most likely this, all 22 of them, and not the recipe's state; and
  that run's gate reading after "the 22" was a reading without them. It also means that run did
  not leave the 22 as B2 writes them (its bit 4 said as much).
- **What it doesn't say:** why. One thing the desk shows (b2.lst, local): right before its op 8,
  B2 writes 0 to two words of the engine's own page, 0x2922c0008 and 0x2922c0040, and waits for
  bit 1 of the second to clear; B1's last write puts 0x2922c0008 back to 1. Both looks today read
  0x2922c0008 = 1 and 0x2922c0040 = 3. So those two may be what opens the 22 to a write. That's a
  reading of the order, not something a run has shown.
- **Next** (PLAN.md): either that question, on this launcher (the two engine-page words written
  as B2 writes them, then the 22, everything put back), or `a2_pre=1`. Not built.

### What the other projects have since 2026-11-12 (sweep of 2026-11-13)

Read on GitHub at Corey's request, before the next build: PR and issue threads, commit headlines and
messages, DeepIdle's `docs/` (worklog and status: their findings, as in the last sweep), and the three
sleep drivers already read with Corey's OK, at their heads of today and only by search for the clock and
engine steps: `sleep.c` on aurora-silicon's `integration/aurora` (the T8140 driver, renamed),
`sleep-t6040.c` in #1477 at 04027a015ce2, `t6041-sleep.c` in #1664 at f3fcc17b7c64. No m1n1 or Aura
code. Of Naeem Malik's new branch, the commit messages only. Nothing posted.

- **Our own items: nothing waits on us.** omacom/linux-aurora#351 is open and mergeable, with no
  review and no `ci:build` yet; #454 (the M2 alarm on top of it) was answered on 2026-11-12. #290
  (the drain issue) has no comment since 2026-10-30. Near our work: #456 (an opt-in s2idle wake on
  charger plug and unplug), and #334 with #441 (a GPU vote to the PMP on t600x, on by default on
  T6001), which change `pmp-report`, the driver `pmp-sleep-release` takes its lock from: look at
  them before the next PMP night. Upstream of aurora, aurora-silicon/linux#1687 (merged) reads
  T6000's SPMI monitor counters and takes the monitor out of the primary PMIC node's interrupt
  parent.
- **Nobody has an M1 Pro or M2 Pro entry that completes, and DeepIdle has tried to replay everything
  it traced macOS's AP writing.** T6020, runs R39A to R47A (2026-11-12), every one "no change": the commit is
  taken, the engine parks, WD2 resets. On the way they traced macOS and found: its AP writes the
  S2R timer twice and touches nothing else in the always-on block that holds it (T6000's
  0x2925c0000; not the engine's page at 0x2922c0000, which that trace didn't cover), never the five
  words that step at the commit; it sends stop requests to the secondaries while they're awake, switches
  both PCPMs off through the PMP, then the timer, then each core writes its own two override
  registers; it halts the GPU's, ANS's, SIO's and the PMP's firmware before the commit; 59 power
  words were in another state than macOS's (SIO_CPU, SIO_SPMI, FPWM0 to 2, I2C5 to 7, SPI, UART,
  audio, ANS2 and PCIe among them; the memory controllers matched). They then tried each in turn:
  the stop requests with a late release, two timer writes, the halts, 50 power words forced to
  macOS's, 417 memory-controller writes (R46A), 484 device writes in all (R47A). The last two
  left no marker that the replay ran, so they're attempts with the same ending, not confirmed
  replays. Their close: every write the hypervisor sees between `slpw` and the sleep is
  reproduced and the engine still parks; "what
  remains is invisible to the HV: the cores' real deep WFI (the HV masks it), anything on the
  cores/PCPM side that macOS does inside the guest kernel's own idle path, and unreadable
  blocks." They've armed nothing further and wait for Naeem's result.
- **Naeem Malik (T8103) has a second driver and is asking the engine's waits, as we are.** Branch
  `m1power/s2r-rs1` (2026-11-12 and 13, head ed5e15e8de): the entry again in Rust, beside
  `apple-s2r.c`, runs 16 to 22. From the commit messages:
  - `gate_record` reads, just before the release, the 49 words "sequence 0 waits on (44 device
    power-state words, bit 11 must be 0; five power-manager words, bit 30 must be 1; addresses and
    masks from the sleep engine's program as read live on this M1)", and records how many aren't
    as needed and which come first. That's our looks' method: the engine's polls, asked from the AP.
  - Run 18: `ps_fpwm1`, the sequence's second wait, "reads busy (bit 11) before sequence 0 starts,
    never while awake". Run 19 waits up to 50 ms for it; run 20 keeps `ps_fpwm1` on through the
    sleep. Run 21 clears bit 42 of both clusters' perf-state words; run 22 puts the last steps in
    another order (perf requests, throttlers, four words each polled, APSC last).
  - No message says an entry completed on this driver; the 0.05 W result was the older one's.
  - **Its source wasn't opened.** Its first commit calls it "the vendor OS's last stage as read",
    and later ones cite "the vendor kernel's last stage": that isn't the trace-derived driver
    Corey's OK of 2026-11-07 names. Whether to read it is Corey's call, and for a fix that goes
    upstream it's a different question than values from traces.
- **M4.** aurora-silicon/linux#1769 (merged today) gives `sleep.c` per-SoC data and operations, for
  SoCs that differ by "clock parks that wait for a done bit, a cache flush and DRAM access off
  ..., CYC_OVRD for the cores' deep WFI, and an AIC2 instead of an AIC3". AIC2 and CYC_OVRD are
  the M1 and M2 generations'; no such SoC is matched yet and no PR for one was found. #1477
  (M4 Pro): 25 of 25 on a second J616s, about 0.34 W over a 603 s sleep; and a warning: on that
  SMC `MBSE = slpw` can't be taken back, and a suspend called off after it leaves both fans at
  0 rpm until a completed sleep or a restart. (Nothing of `probe`'s writes to the SMC here;
  `e1_hook.c` says so at the probe's branch.) #1664 (M4 Max): the full part reset after a clean
  commit record where the binned one wakes; today's finding is eight memory channels the binned
  part lacks, never flushed, their power words on, holding a fabric domain on. They found
  it with a bitmap of every power word that reads on at the commit, kept in the PMU and compared
  with the machine that wakes. There's no waking T6000 to compare ours with.
- **In the drivers, two things for T6000.**
  - The clock park is the recipe's. T8140 writes 0x80100000 to three mux words ("The SoC refuses
    the first, on macOS too"); the M4 Pro gives four ClkGen words at + 0x38050 and on [27:24] := 0
    with bit 31, each polled until bit 30 clears. T6000's table writes 0x80100000 to the muxes at
    0x28e038164 and on, and wait 62 polls their bit 30. Those sit in 0x28e03801c's page.
  - The M4 Pro has "the hardware S2R-lo entry counter: bits 31:3 of MiniPMGR + 0x70970", which
    "tells 'the SoC entered S2R' from 'the engine never finished'". Whether T6000 has one, and
    where, isn't known; nothing here has looked.

**What this changes for the next step** (PLAN.md, the 22 after B2's two writes; judgment):

1. **Whether the clock word's page takes the AP's writes isn't known.** The recipe parks five
   muxes in 0x28e03801c's page, but its waits there only poll bit 30 clear, which those words
   already read awake: a write that was dropped passes them too, and no record has a mux's value
   after its write (Pullfrog on #99). So the clock word's refusal on `probe --pre` may be the
   word's, the value's or the page's. A read of the five after the table would say, and costs a
   one-way run nothing: the next run carries it.
2. **DeepIdle's "nothing but the timer" is about another block.** Their trace covers the
   always-on block with the timer, T6000's 0x2925c0000, not the engine's page that 0x2922c0008
   and 0x2922c0040 are in (Pullfrog on #99). Whether macOS's AP writes those two isn't known
   from anything read here.
3. **Toward the cores, as a judgment and not a deduction.** DeepIdle's replays of the AP's writes
   changed nothing as far as they're confirmed, and the M4 drivers say the engine waits for every
   other core in its last WFI and for a held CPU to stop. `a2_pre=1` asks three of A2's waits in
   one run; the two engine words would take two runs to ask one of B2's polls. So `a2_pre=1`
   goes first, and the two engine words stay on the list.

### `a2_pre=1` as built: A2's three moves made by the AP, and the table's muxes read (2026-11-13)

Built, not run. `pkexec e1-hook/run-floor --quick --m1-a2-pre`, on `--m1-gates`' recipe
(`e1-hook/a2_pre.h` has the words, the values, the waits, the byte and the stages). It goes before
the test of the engine's two words for the reasons in the sweep above: one run for three of A2's
waits.

- **What A2 does there** (the local listing, a2.ops; it stays off the repository). After B2's first
  handshake: op 11 writes 2 to DCS's p-state word 0x28e20c800, the whole word, and op 12 waits with
  no timeout for bits 11:8 to read 2 with bit 31 clear; ops 13 and 14 the same for the fabric's word
  0x28e20c000; op 15 writes 0 to 0x28e20c400 and op 16 waits for bits 10:8 to read 0 with bit 31
  clear. None of the three moves has been seen on this Mac: the recipe leaves DCS asked down to 5,
  `fabric_up=1` leaves the fabric at 3, and the third word reads 0x222 awake and after noirq.
- **What CPU0 does in step 8**, where `--m1-gates` reads the gates, before the commit:
  1. reads the five ClkGen muxes the table parked in step 5 (0x28e038164, 16c, 168, 15c, 170, written
     0x80100000) and sends a byte to 0x6016 with one bit for them. The rider from the sweep: the
     recipe's waits there only poll bit 30 clear, so nothing shows the parks taking;
  2. writes 2 to DCS's word, waits up to 100 ms for A2's condition, reads the word, sends the byte;
  3. the same for the fabric's word, with DCS's read again for the byte;
  4. writes 0 to the third word, the same, all three read again: the last read before the commit.
  Nothing is written back: A2 writes the same values to the same words.
- **New writes: three**, each with a value no run has written (the fabric's 2 where `fabric_up=1`
  writes 3). Built on Corey's standing go-ahead of 2026-11-10; none outlasts the restart. What isn't
  known is whether CPU0 runs on with DCS at 2: if it doesn't, WD1's marker restarts the Mac about
  90 s after step 4 and the byte stops short of that move.
- **The byte**, under the run's own stages (0x99 before the commit, then 0x9b, or 0x9a with
  something pending): bit 0 NOT all five muxes read 0x80100000 (set is the bad case, so that a
  clean byte isn't 0xff); bits 1 and 2 DCS's word read asked for 2, and read as A2 waits for it;
  bits 3 and 4 the fabric's; bits 5 and 6 the third word's; bit 7 always. A move's bits are 0
  before it's made and come from a new read in every later send. 0xfe: all three took and settled,
  muxes parked. 0x80 or 0x81 after the commit: none took.
- **What it can say.** All three taken and settled and the restart still at 90 s: A2's own waits
  are less likely to be where the pair stops, since the engine finds them made (not excluded: A2
  makes them later, with every core down). A move that takes and doesn't settle in the AP's
  100 ms: a candidate, not proof (Pullfrog on #79). A move that doesn't read back: the AP's write
  was refused or changed back (the PMP owns DCS under Linux), which says nothing about A2. The mux
  bit says by itself whether the AP's writes take in the clock word's page in the recipe's state,
  which `probe --pre` left open for 0x28e03801c.
- **At the load** `m1_a2p_init()` refuses unless the three words and values are the ones named
  (written out a second time), none is on the never-write list, and the table's waits in the
  muxes' page are five rows that write 0x80100000; it logs the eight words awake. Reads only.
- **Also fixed:** `run-floor`'s check for a module older than its sources didn't list
  `pre_test.h`, and `probe`'s didn't list `pcie_down.h`.

**What the reviews changed** (#100: one adversarial pass by a subagent, and Pullfrog). Neither found
a path that writes anything but the three words, another value or another order.
- **A byte that stops short isn't a move that stopped** (both). A PMU command that fails stops
  every one after it and the run goes on, so stage 0x99 with DCS's bits alone is also what a
  failed send before a clean run leaves. `m1-crumbs`, the header and PLAN.md now say "the last
  thing recorded", with the three things it can be; a test makes the PMU refuse the third send
  and sees all three writes made.
- **0x80 doesn't say the words were unchanged** (both). It says none read as written or as A2
  waits for it; a word taken as another value gives the same byte. The verdict no longer says the
  commit found the recipe's state. A word that reads as A2 waits for it without its value reading
  back gets its own line, which says only that: whether the write changed anything isn't in the
  byte, since nothing reads the word just before it (Pullfrog's second pass).
- **The restart's time** is no longer read in this byte's verdict as "B2 got past its op 224":
  that reading is `mark_reading()`'s, with its conditions (the adversarial pass).
- **Said in the verdicts now:** A2 makes its moves between its own writes to PHASE and
  0x2925c4004, not only with every core down; this run leaves the fabric asked down to 2 at the
  commit, where `fabric_up=1`'s lines speak of 3; the mux bit doesn't say which mux or what it
  read; what the third word read before its write isn't recorded.
- **At the load:** the three words are read in separate statements (Pullfrog: the order of
  arguments' evaluation isn't the compiler's to keep, and the test failed under GCC 15), nothing
  is read before every check has passed, and two checks `e1_check_params()` already makes are gone.
- **Test holes closed** (the adversarial pass, each shown by a change the tests let through): a
  wait that ignores bit 31 (the model now has a word that reads its value with bit 31 set); the
  barrier between a write and its wait. And one of mine, found by the mutants: a misplaced
  condition made the test of the verdicts pass whatever they said; fixed, it then caught a slip
  in the reworded decoder.

### Naeem Malik's second driver, read: a working M1 Pro driver is cited, and a time limit after the cluster words (2026-11-13)

Corey's OK of 2026-11-13. Read: `drivers/soc/apple/s2r_seq.rs` in malik-na/linux-aurora,
`m1power/s2r-rs1` at ed5e15e8dee4 (Naeem Malik, `GPL-2.0-only OR MIT`), its constants, options and
last stage, not every function; and the branch's commit messages. T8103, M1. Its order is, in its
own words, "the last stage of the vendor OS as read from its kernel", with two private files behind
its tables; that isn't trace-derived, so for anything that goes upstream it's a source of leads to
check on this Mac, not of values. Nothing was posted. What follows is his, with what on T6000 looks
like the same thing (the mapping is judgment).

- **He cites a working deep sleep on T6000.** Commit c7737e76ec: "A working deep sleep on t6000 (a
  reference driver for the M1 Pro, read on 2026-11-12) writes the PMGR's S2R timer as its last
  register write and lets the parked cores go down only after it." DeepIdle's worklog quotes the
  same line and adds that this repository isn't that reference. What his commits take from it:
  - the timer is the last register write of the entry, and the other cores go down only after it;
  - the display: the firmware is put to sleep through its own protocol in device suspend, RTKit
    idle at `suspend_late`, woken at `resume_early`;
  - the boot core's timers are stopped before its final wait;
  - the wake mask opens every named wake source and masks every source without a name;
  - the boot core's last routine is assembly, and if the wait falls through it turns the data
    cache on again and returns.
  **Which driver that is wasn't found:** not in aurora-silicon/linux's PR titles, branch names or
  code, not in a GitHub search of commits and code. Naeem would know; asking him is Corey's call.
- **On T8103 the chip starts its own block at the first cluster power word, and the cores have to
  be in their wait soon after.** His last stage: the watchdog step, the SEP's domain off, five
  clock words parked (bits 27:24 := 0 with bit 31, bit 30 polled clear: our muxes), the base
  class's steps, the gating words; then "No way back from here, and nothing slow: the chip starts
  its own block at the first cluster word and every core has to be in its wait soon after." The
  cluster words' low nibbles go to 0 (PMGR + 0x18000 and + 0x18004), three SIO words lose bit 28,
  the timer is written, the interrupt controller is masked, the PMU's alarm is set, the timer
  twice more, release, WFI. Every wait after the cluster words is capped, "and the caps together
  stay far below the 52 ms the chip was shown to allow".
  **Here:** T6000's cluster words are 0x28e098000, + 4 and + 8, and macOS's table writes them
  (0, 0x20, 0x20) in step 5, entries 52 to 54 of 74. In our replay the table runs fast, but then
  come steps 6 to 8 (the other cores' stop requests and holds, the PMU's breadcrumbs, and in
  `--m1-sio-steps` up to 9.5 s of step 8) before the commit, the alarm's commands and the cores'
  WFI. If T6000 has a limit like T8103's, every run so far has broken it. This fits what's on
  file: a CPU complex's down sequence in the SRAM opens with a wait and is "untested where it
  matters" ("The untimed waits", above). It isn't shown: the 52 ms is his T8103 measurement,
  where from isn't in what was read, and how long our steps 6 to 8 take isn't recorded.
- **The wake-status words latch at the first timer write:** "what reads set after it is what stood
  pending and unmasked at that moment." He reads the five right after it and keeps the count and
  the first four in the PMU. With his recorded mask five unnamed sources stood there; his newer
  masks close every unnamed one. Nothing here has read T6000's at the commit.
- **What the engine's first sequence waits on, on T8103:** 44 power words for bit 11 clear, then
  five PMGR words for bit 30 set: B2's two kinds of poll here. His run 18 found `ps_fpwm1`
  reading busy before the sequence starts and never awake; run 20 keeps it on through the sleep.
  FPWM0 to 2 are in SIO's subtree here, and `--m1-sio-steps` saw all 43 of it off with every
  poll met.
- **Smaller things:** his watchdog step also zeroes the word at the watchdog block's + 0x8008
  and clears the low byte of the one at + 0x800c, keeping its upper bits; our table for T6000 has
  neither; the engine's progress is the top byte
  of + 0x8028 there (ours: `PHASE`, and 0x2922b801c); the engine arms WD2 "when one of its blocks
  starts"; the vendor's last stage clears bit 42 of both clusters' perf-state words (run 21).
- **No commit says an entry completed on this driver** as of run 22.

**What this changes** (judgment):

1. **The time between the cluster words and the cores' WFI is the first thing to test after the
   `--m1-a2-pre` run.** That run stands as built: what it reads (the muxes, the three moves) is
   read before the commit and doesn't depend on this. But a restart at 90 s after it would no
   longer weigh much against A2's waits, since the same limit may end every run.
2. **The build after it:** the three CPU cluster words taken out of the table's place and written
   last, with only what T8103's last stage has after them (the timer, the alarm's commands capped,
   the release, WFI), the time from the first of them to the release kept in the PMU. To settle
   at the desk first: how long steps 6 to 8 take now, what the table's entries after 54 need
   (the SIO_CPU write, the muxes, PLL-off: macOS makes them after the cluster words), and whether
   the cores' holds can be taken before the cluster words.
3. **Asking Naeem which T6000 driver he read is worth more than either run** if it's public or
   he'll share it. A draft is Corey's to ask for. (Asked on 2026-11-14, with Corey's OK:
   malik-na/linux-aurora#77.)

### The `a2_pre=1` run: all three moves took and settled, the restart still at 91 s, and the muxes didn't all read parked (2026-11-14)

Corey's run, in a safe boot on battery: `pkexec e1-hook/run-floor --quick --m1-a2-pre`, module
built from main at 0b5cebb. Dark, then a restart "just about 90 seconds" later. The record, the
load log and the clear and collect are in `results/a2-pre-20261114/`.

- **Stage 0x9b**, the run's last by design; every earlier byte as in the `pcie_down=1` run (0x6012
  0xff, 0x6013 0xdf, 0x6014 0x90, 0x6015 0x8e, 0x6007 0xf5, 0x6005 0x7d with PHASE at 0x4).
- **0x6016 = 0xff.** DCS's word read asked for 2 and as A2 waits for it; so did the fabric's; the
  third word read asked for 0 and as A2 waits for it; all at the last read before the commit. No
  move stopped CPU0. Whether each settled within its own 100 ms isn't in the record: the last
  byte is from reads made after the third move's wait, and the earlier sends are overwritten
  (Pullfrog on #103).
- **Bit 0 is set: the five ClkGen muxes didn't all read 0x80100000 after the table.** The first
  read of them after their write in any run. The bit doesn't say which, how many or what they
  read. Awake they read 85100000 85100000 85100000 87100000 85100000 (the load log, in the
  table's order), so "read as found" is one of the cases. `probe --pre` had 0x28e03801c, in the
  same page, read as found after the AP's write.
- **The restart: 91 s after the commit** (89.6 to 91.6 s), WD1's marker again.

What it says:

1. **A2's three moves can be made by the AP with a core up, and the engine finding them made
   doesn't change the ending.** By the outcomes written down before the run, A2's own waits are
   less likely to be where the pair stops. Not excluded, and worth less than it was when the run
   was built: if T6000 has T8103's time limit after the cluster words (the section above), every
   run ends at 90 s whatever the engine finds.
2. **The recipe's clock park hasn't been shown to take in any run, and here it's shown not to
   have, in whole or in part.** The table's waits on those rows pass on a write that was dropped.
   macOS makes these writes from the AP, so either something in macOS's state lets them land that
   ours lacks, or they don't all land there either (T8140's driver says its SoC refuses one of
   three, on macOS too). Which muxes, and what they read, is the next read-out; nothing is
   concluded about the recipe from one bit.

Next (PLAN.md): the cluster words written last, with the time from them to the release recorded,
and the muxes read out one by one as that run's rider. Desk first, as listed in the section above.

### `acc_last=1` as built: the CPU complexes' cluster words written last, and the time after them kept (2026-11-14)

The build PLAN.md had next. `run-floor --m1-acc-last` is `--m1-gates`' recipe with `acc_last=1`
(`e1-hook/acc_last.h`).

- **The desk checks first.** The table's entries 52 to 54 are the soc-cluster words of EACC, PACC0
  and PACC1 (0x28e098000 := 0, + 4 := 0x20, + 8 := 0x20): T6000's counterpart of the two words Naeem
  Malik's T8103 driver writes last (PMGR + 0x18000 and + 0x18004 there too). No wait of macOS's is on
  them. After them the table has the other clusters' words (ANE0, AFR, DISPLAY, AVEMSR, each with a
  wait), SIO_CPU, the five mux parks and PLL-off, then the commit. How long the replay takes from
  entry 52 to CPU0's WFI was never recorded; what is known is its bounds: up to 10 ms a wait, the 20
  power words (four of which don't read back, so likely their whole 10 ms each), seven stop requests,
  about a dozen PMU commands, step 8's reads or moves, the commit, six more PMU commands, and up to
  300 ms for the engine to arm WD2. So tens of ms at the least, and it can't be said to be under
  T8103's 52 ms in any run so far. The holds can come before the cluster words: nothing in step 6
  reads them.
- **What the run does.** Step 5 leaves the three entries out. Steps 6 to 8 run as before. In step 9,
  under the mask, CPU0 reads the clock, writes the three (the table's values, in the table's order)
  and then the commit, with nothing between. After the alarm's commands, the release and
  `wd2_bite=1`'s stage it reads the clock again and sends one more byte with the span as a code.
  **No address or value is new.** What is new is the order: the three now come after the table's
  entries 55 to 72 where macOS writes them before. That is T8103's order as Naeem has it (the parks,
  then the cluster words, then the timer); whether T6000 minds isn't known, and a run that ends
  differently in step 5 (a wait unmet at 0x6006) would say so.
- **The rider.** In step 8, in place of `gates_mark=1`'s reads, the five ClkGen muxes are read, one
  bit each: which read 0x80100000. The `a2_pre=1` run had one bit for all five.
- **The byte, 0x6016:** bits 4:0 the muxes in the table's order; bits 7:5 the time's code (0 never;
  1 to 5 under 5, 10, 20, 50, 100 ms; 6 that or more; 7 "not yet", the send before the cluster
  words). Stages 0x91, then 0x93 or 0x92.
- **What the span covers:** from before the first cluster word to after the last stage went out, so
  the commit, the alarm, the release of the other cores and CPU0's wait for WD2. It bounds the other
  cores' way to their WFI from above. It leaves out the byte's own command, the acks and CPU0's WFI.
- **What it can say.** A restart at about 120 s, at 150 s or more, or none: B2 got past its op 224,
  and the place of these writes is what the runs lacked (place, not yet time: they now also follow
  the power words and the stop requests). A restart at 90 s with a code of 3 or less: a limit of
  T8103's size is unlikely to be what ends the runs (a shorter one isn't excluded). A code of 4 is
  too close to 52 ms to call, since the tail isn't timed; with 5 or 6, or at the pending stage
  0x92, the limit is untested, and the wait for WD2 is cut next.
- **What the reviews changed** (the adversarial pass found the write path sound): the verdict is
  given for codes 1 to 3 only and never at the pending stage; the decoder's opening line says what
  the run was to do, not what it did; stage 0x12's text names the three writes, which are now made
  under the mask, so that a fault there shows as that stage and not as a panic; a barrier after
  the three writes; and the tests now have the row's width, the barriers and the stage's gating.
- **Checked:** `tests/m1_crumbs_test.py` (1,077 checks; the three functions compiled as they stand
  against a model that logs every access), `tests/e1a_launcher_test.py` (2,042),
  `tests/e1_hook_probe_test.py` (79), `tests/mutants.py` (928 mutants, 89 of them this rung's).

### The `acc_last=1` run: the cluster words last, under 5 ms to the last stage, still 90 s; none of the five muxes parked (2026-11-14)

Corey's run, in a safe boot on battery: `pkexec e1-hook/run-floor --quick --m1-acc-last`, module
built from main at 5dfe5de. The record, the load log and the clear and collect are in
`results/acc-last-20261114/`.

- **Stage 0x93**, the clean last stage; every earlier byte as in the `a2_pre=1` run (0x6012 0xff,
  0x6013 0xdf, 0x6014 0x90, 0x6015 0x8e, 0x6007 0xf5, 0x6005 0x7d with PHASE at 0x4). **0x6006 =
  0:** every wait of step 5 was met with the three cluster words not yet written, so the table's
  later entries don't mind the new order.
- **0x6016 = 0x20.** The time's code is 1: under 5 ms from before the first cluster word to after
  the last stage went out. That span holds the commit, the alarm's commands, the release, WD2's
  arming (so the engine armed WD2 within those 5 ms) and the stage's command. Not in it: the
  byte's own command (answered, since the byte landed), the acks and CPU0's WFI; and nothing
  records that the other cores left their hold.
- **Bits 4:0 are 0: none of the five muxes read 0x80100000 after the table.** Awake they read
  85100000 85100000 85100000 87100000 85100000 (the load log). What they read in step 8 isn't in
  the byte.
- **The restart: 90 s after the commit** (88.7 to 90.7 s), WD1's marker again.

What it says:

1. **A limit of T8103's size between the cluster words and the cores' wait is unlikely to be what
   ends the runs.** The three were written last, the run was at its last stage within 5 ms, and
   the pair stopped where it always has. Unlikely, not shown: the tail isn't timed, a shorter
   limit on T6000 isn't excluded, and the other cores' leaving isn't recorded.
2. **None of the five muxes read the table's value in step 8.** That is all the bits say: not
   whether the writes were refused, or took and were changed afterwards (PLL-off and the power
   words come between the park and the read), or took and read back otherwise than as written
   (bit 31 may not read back as set after a park). What the muxes did read isn't recorded.
   A hypothesis, not a finding: the ClkGen page is closed to the AP's writes in the state the
   replay reaches, as `probe --pre` found for 0x28e03801c in the same page (another word, in
   another state: awake after noirq, nothing stopped). If so, something in macOS's state opens
   those words that ours lacks, and B2 clearing two words of the engine's page right before its
   22 writes is a lead on what; or the park is refused under macOS too and its polls pass as ours
   do (T8140's driver says so of one of its three).

Next (PLAN.md): the muxes' values read out, before and after the park, which tells those cases
apart; then, if they were refused, what opens them and the 22 to a write (Pullfrog on #105).

### The strategy review: what isn't known lies after CPU0's last record, and almost everything built looks before it (2026-11-14 to -15)

Corey, 2026-11-14, after the `acc_last=1` run: "can we pause for a moment and assess our strategy. are
there other tools we can use to try and crack this case? any way to speed up the cycles? do we *need*
safe mode?", and later "I could buy one of those 'cloud kvm' devices too". Done as two read-only
passes: 18 fact-finders over this notebook, PLAN.md, the local SRAM decode, the code, the logs and
outside sources; then four independent strategists, five fact checks and four adversarial reviews of
a draft. The 32 reports are on this MacBook, out of the repo: `~/.local/share/sleep-drain/strategy-review-20261114/`.
No run, no write, nothing posted. What follows is what was measured or read, then the judgment.

**Measured, about the instrument**
- Two read-only probes that returned gave about 436 full words; six one-way runs gave about 12
  payload bytes. Every new bit of the last four one-way runs was read by CPU0 before the commit, or
  (`acc_last=1`'s span) right after it. Three things are to be kept apart: the commit; CPU0's last
  record, made after the commit and the release (WD2's sample with PHASE, and that span); and CPU0's
  own last WFI. From after that last record no run has anything but the restart's time, so no
  record shows that the last core went down at all.
- B2 is 246 ops: 152 writes, 89 polls, 4 delays, End. Its first 222 ops write nothing that outlasts
  a reset; ops 223-224 (WD0's and WD1's counts) are the time channel's one boundary. 22 polls have
  the unbounded form: ops 1, 3, 6 (the engine's page), 31, 33, 231, 233, 244 (HS_B), 177-187 (bit 31
  of eleven clock blocks), 189, 192, and 241 (the SPMI ack). 58 have the retry bit and 6 don't, all
  with a count field of 255; read as one 12-bit field, as this Mac's bytes suggest, the 58 are
  4095. No public source says what the engine does when a counted poll runs out (PongoOS only parses
  the format; XNU names the engine three times). Estimate: it stops there, 65%; falls through, 25%;
  something else, 10%. The op-140 gate lead rests on the first.
- A run takes Corey a median 10.8 minutes (machine floor 232 s, of which the 60 s idle reading and
  the 90 s marker are chosen; the safe boot about 1.5 minutes). Same-day runs are a median 140
  minutes apart: the run is about 8% of the loop, the build and its review the rest. 19 rungs, mean
  1,144 lines each (tests 44%, mutants 8%, the mechanism 45-110 lines). No review found a wrong
  register write in any build; findings were decoder claims, load checks and test holes.
- The safe boot: at the end of noirq the filesystems are synced, user space frozen and the NVMe
  controller shut down, so a run that never returns is a power cut on an idle drive. btrfs logged
  nothing in 89 boots; /boot's vfat was found "not properly unmounted" on 11-12 and 11-14. What the
  safe boot guards is a kernel that keeps running, damaged, with a writable disk: a probe that
  returns, or a replay that stops short after `slpa`. That path can be closed in the module: once
  anything has been changed, restart, don't fail the suspend.

**Corrections to these notes**
- "aurora-linux #99" (the hibernation line at the top) is iconidentify/aurora-linux#99, moved to
  omacom/linux-aurora#50, which is open and blocked.
- aura boots this board: Aurora Silicon lists J314S as qualified (aurora-silicon/linux discussion
  95). This Mac already runs at EL2, under m1n1-aurora 1.6.1.aurora3, with spin-table CPUs and no
  `aurora,s2r-mailbox` in /chosen. The sleep core (`drivers/soc/apple/sleep.c`) is in
  aurora-silicon/linux (7.3) only; omacom/linux-aurora has none. #1477 was closed on 11-14 and split
  into #1903, #1904 and #1909 to #1916. #1769 doesn't say the M1 generation "differs in kind".
- UART0's power word (0x28e580240) reads 0x200 awake: the port is runtime-suspended, not switched
  off by the suspend. It isn't among the UARTs `ps_words=1` writes; B2 switches it off itself.
- Run 2's observer did see CPU0 and its complex off. What nothing has seen is the last core and its
  complex going down, and anything after that.

**The way back.** XNU: "The reconfig engine script brings the CPU out of reset at the kernel's reset
vector"; iBoot locks CPU0's RVBAR to the boot object's entry page. The contract in aurora-silicon's
series (#1208): the loader sends a reset secondary back through its spin-table release address and
the boot core to a resume mailbox, published as `/chosen/aurora,s2r-mailbox`; without it their
driver doesn't bind. On T8103 the boot CPU has never run its first instruction at the RVBAR after a
completed entry, and a core restart there failed on T6020. PSCI through EFI is T8103 only, has no
system suspend, and has had no new version since the PSCI maintainer's objection in September. So a
completed entry here gives a drain figure, not a sleep: the return needs a loader that publishes the
mailbox (not Claude's to write), the sleep core in the kernel this Mac runs, a core that does
execute at the RVBAR, and device restore. CPU0's RVBAR and its lock bit have never been read here.

**Found, not on file before**
- Witnesses that need no CPU. The keyboard backlight is a hardware PWM (pwm@39b030000, domain
  fpwm0, power word 0x28e580190), the tenth of the 43 words B2 writes in its SIO chain: lit before
  the commit and filmed, it says whether B2 gets that far. The Mac's input power through an inline
  logging meter (KM003C: 1 kHz, open Linux tools; `charge_behaviour` offers inhibit-charge here):
  the plateau of the dark stretch is resolvable (about 80%), B2's single steps probably not (20%).
- CPU0's last WFI: a return is counted only with `observe=1`, so since run 11 a WFI that returns
  has been lost silently. A pending source holding a core up is the stall the M4 drivers found.
- cpuidle's deepest state ("CPU PD") is what every core is asked into during s2idle, so "every
  core down" might also be had from Linux's own idle: the commit alone at the end of noirq, the
  alarm at 15 s, and a look on the way back. A hypothesis, twice over: cpuidle counts requests, not
  cores and complexes found off together, and that path isn't the recipe's (no stop bits, no
  `ACC_OVRD` arming, a WFI that returns). So a look that finds nothing changed neither places nor
  excludes the recipe's stall; only an engine that moves would say something. Without `slpa` the
  SMC doesn't take the NVMe, but B2 lowers the storage's PCIe words and cycles ANS2 itself, so a
  commit that gets far enough can still lose the disk and the log: words in full if the kernel and
  the NVMe survive, the PMU's bytes otherwise.
- An SMC key dump has never been diffed across a one-way run.
- Stores that may outlast a reset: `pram` (1.5 MB of DRAM at 0x107e6cd0000 by macOS's tree, right
  where the live tree's dcp_data ends and outside /memory; XNU carries panic logs in it across a
  reset; survival through this boot chain untested); PMU bytes 0x6008-0x600e and 0x6017-0x601f read
  0 and have no Linux cell, but lie in macOS's legacy scratchpad; the PMU's 1 KiB `socd` area.
- macOS's side without m1n1: `ioreg` and `log show` for the platform sleep and quiesce actions
  (free); a kernel extension registered as a quiesce action could snapshot PMGR words at macOS's
  last step before a real sleep and on wake (Permissive Security on its own volume; unverified on
  26.6.2). Its snapshot precedes the cores' last WFI.
- A serial console on UART0 through macvdmtool or tuxvdmtool from the second Mac: about 75% that
  text can be printed up to the last core's stop (the unknown is the port's mux through the
  suspend); blind after it. A network KVM can't reset the Mac, sees nothing once it is dark, has a
  dead keyboard at the passphrase prompt as the initramfs is built, and adds two attachments.
- Policies, quoted: Asahi's, "A contributor using an LLM to interpret a trace made using the m1n1
  hypervisor may be issued with a first and final warning"; m1n1's AGENTS.md forbids "any usage of
  AI or LLM tools whatsoever"; both aurora kernels take AI-assisted patches with an `Assisted-by`
  tag and no AI sign-off.

**The judgment** (four strategists and four reviewers, who agreed on the main lines)
- At least 85% of what isn't known lies after CPU0's last record: whether the last core and its
  complex go down, and what the engine does then. If B2 never passes its first
  gate, none of the AP-made rungs on its later ops (runs 7 to 12 and 16, the gates read, both
  `sio_down` runs, `pcie_down`, `sio_steps`, `a2_pre`, `acc_last`) could have moved the ending. A
  wider record of what CPU0 reads before the commit doesn't change that.
- Where the entry stops, by the four strategists: B2 never passes op 1, 40 to 50%; in ops 2-34, 12
  to 20%; in the power-downs, 27 to 37%; the pair finishes and something else resets, 5 to 8%. That
  the entry can be completed on this Mac from Linux: about 50%. A whole sleep-and-wake cycle here
  within three months: 4 to 10%.
- So: witnesses for the dark stretch first (PLAN.md has the order); AP-made rungs on B2's ops stop
  until a witness shows B2 past op 1; the scripted write engine, the chained RAM-only boot, serial
  code, a `pram` store and the kernel extension wait for a witness to name a place; the one-way
  runs get a budget (ten runs or three weeks, and no run without two outcomes each worth 20%).
- For the nights themselves: suspend, then power off after some minutes, which needs only the alarm
  (omacom/linux-aurora#351). On 2026-11-14 the Mac went to sleep at 15:57 with 50% (inside
  `sleep-guard`'s day hours, where its threshold is 15%), ran flat overnight, and the PMU's clock
  reads 1970 again.

### After the review: Corey's answers, a meter already in the house, the first free checks, and the others' weekend (2026-11-16)

**Corey's answers** to the review's five questions: "I completely forgot, I have a power-z model c240! its
right here. and yes on everything else." So: a lid close on battery may mean sleep, then power off after
20 to 30 minutes; PMU bytes 0x6008-0x600e may carry the run record; runs on the charger with a meter in
line are fine; questions to upstream may be drafted (each post still needs an OK on its text); and the
one-step-a-session rule of 2026-11-10 is lifted.

**The meter is a POWER-Z C240, not a KM003C.** ChargerLAB's own pages (read by a subagent, 2026-11-16): it
has "no HID port on the side, which means you cannot connect it to PC. So it's basically a lite version of
the KM003C"; the screen shows volts, amps, watts, "D+, D-, CC, charging timer, and accumulated energy input
(Wh)"; 0-50 V, 0-6 A, PD 3.1 to 140 W; accuracy "relatively lower" than the KM003C's, no figure; refresh
rate, resolution and its own draw aren't stated. So it can't log. It can be filmed: a sustained lower
level in the dark stretch (the review's "plateau") shows on the watts line and, summed, on the Wh counter;
single steps of milliseconds don't. The KM003C isn't bought. It's worth its price only if the C240 shows a
level worth resolving. Unknown until tried: what its last digit is at 1 to 10 W, and how fast the screen
follows.

**`inhibit-charge`, checked awake on the charger** (`results/inhibit-charge-20261116/check.txt`; one root
script, which first set the PMU's clock: it read 1970-01-01 again, and reads the right time now). Battery
at 97%, charging at 1.26 A. Two to four seconds after `inhibit-charge` was written to
`macsmc-battery/charge_behaviour` the status read "Not charging", and from six seconds on the battery's
current read 0, for the 34 s it was left so; the SMC's total system power stayed at 9.7 to 10.5 W. Two to
four seconds after `auto` it was charging again. So with the charge inhibited the meter's reading is the
Mac's draw and nothing else, as far as the battery's own gauge can say (its 0 is the gauge's, to its
resolution; a trickle under that isn't excluded). Not checked: whether the setting holds through a suspend
or a one-way run's commit, which is where it's needed. The SMC keeps it, by the look of it; a run will say.

**The PMU's register 0x0201, a third read:** 0x02 (regular boot, 2026-11-16, after `acc_last=1`'s run and
the flat battery). Still what every boot shows.

**What the others did, 2026-11-13 to -16** (a read of public issue threads, PR lists and commit messages;
no code read today, nothing posted):
- **malik-na/linux-aurora#77** (our question): no reply.
- **Naeem Malik's second driver went on, on the M1 (T8103) under system firmware 13.5**: branch
  `m1power/s2r-rs1-135`, nine commits past `m1power/s2r-rs1`, runs 26 to 38, dated 2026-11-15 and -16. From
  his commit messages (his words in quotes; his values are his chip's, and leads here, not facts about
  T6000):
  - His engine gets through its gates. "The decoded sleep program writes the power chip's 0x0201 = 01 in
    sequence 1, after sequence 0's island gate polls, and runs 27b and 32 read 04 -> 01: the engine gets
    past gates #45 and #47." That is the same PMU register B2 writes 1 to here (op 237-238), used as a
    witness that outlasts the restart. On his PMU it reads 04 before and 01 after; here a boot shows 02,
    which is why the reads of 2026-11-10 said nothing. If a restart keeps the engine's 1 here as it does
    there, 0x02 after every run agrees with WD1's marker: B2 has never reached its PMU command.
  - And his M1 restarts all the same, about 5 s after the release, and not by the watchdog: "Run 36 moved
    the engine's watchdog 2 to 100 s (read back) and the M1 still restarted ~5.5 s after the release, with
    0x0201 04 -> 01 and the power chip's restart counter unchanged: not watchdog 2. Under the earlier
    system firmware the same sequence slept until the alarm, so the restart is new with 13.5 and its
    coprocessor firmware." Run 38 masks every wake source to tell a wake from a reset by firmware. This
    Mac's system firmware is 13.5 too. (Not so: its OS firmware set is 13.5, its system firmware 26.6.2;
    the entry of 2026-11-18 below.)
  - What his sequence 0 waits on is readable from the AP there: "44 device power-state words, bit 11 must
    be 0; five power-manager words, bit 30 must be 1", read "just before the release". Run 18 found one
    busy (`ps_fpwm1`, "never while awake"). Here B2's first poll is one AON word (0x2922c0044 bit 2), and
    its bit-11 polls come later (ops 35-229).
  - `sio_on`: macOS turns SIO's parents on "about 1 ms before its commit", with the auto flag clear, "so the
    island's gate (#45) can read ready when the chip's sleep program powers the island down itself". Here
    the recipe leaves SIO's parents and SIO_CPU on too ("The gates read's run").
  - `pmp_idle`: "macOS idles the PMP (AP 0x201, IOP 0x201) between MBSE slpw and slpa and will not complete
    a sleep without it (try61)". This recipe's `pmp_out=1` idles the PMP after `slpa` (step K4), not
    before it. A difference in order, not known to matter.
- **omacom/linux-aurora#351** (the alarm, ours): open, no maintainer's review, no `ci:build`. #454
  (p4ulcristian's T8112 alarm on top of it) is open too.
- **aurora-silicon/linux:** #1477's nine parts (#1904, #1909-#1916) are open with no comments; #1664 (M4
  Max) wakes on its author's J616c and still resets on joske's J614c after a week of byte-for-byte
  records, the open difference being one fabric word (`fab3_soc`); a row of T6050 deep-sleep follow-ups
  merged on 2026-11-16 (#2046, #2053, #2057, #2066): their deep sleep is at the stage of docks and
  displays on that chip. Nothing for T600x.
- **Nothing new** on omacom/linux-aurora#290 (our drain issue) or JJRPF/Aurora-DeepIdle#9 since 2026-11-07.

**What it changes** (judgment):
- An entry whose engine gets past its gates can still end in a restart seconds after the release, with
  nothing dark to see. Whether his entry finished before that restart isn't shown: 0x0201 witnesses the
  gates, not the pair's last command, and run 38's result (a wake, or a reset by firmware) isn't out. So "the
  Mac restarted" has three readings a run has to keep apart: WD1's marker at its set time (B2 short of
  op 224), a restart well before it (something else ended it: his case), and dark past it (B2 past
  op 224). The last look's marker time and the PMU's 0x0201 separate them; `m1-crumbs` should print
  0x0201 with every record from now on (a read through the same nvmem file, nothing new).
- The estimate that the entry can be completed on this Mac (about 50%) had no term for the system
  firmware. His finding puts one in, direction down, size unknown: by his account the same sequence slept
  until the alarm before 13.5, and this Mac never has.
- His gates are power words the AP can read; that the same holds for whatever stands behind
  0x2922c0044's bit 2 here is not known.

### `last_look=1` as built: CPU0's last WFI recorded if it returns, the marker at 70 s, and a second form with a wake and a blinking keyboard (2026-11-16)

The first rung of the order after the review (PLAN.md), built as `e1_hook last_look=1` and `ll_wake=1`,
`run-floor --m1-last-look` and `--m1-last-look-wake`; `e1-hook/last_look.h` has the bytes. Both sit on
`--m1-mark`'s recipe, run 17's, without any of the AP-made steps on B2's ops that came after it.

**What changes in run A (`last_look=1`).**
- WD1's count is written as 80 s where `wd1_mark=1` writes 60 s: the same register, another value. A marker
  that stands restarts the SoC about 70 s after step 4. A restart that moves from 90 s to 70 s is WD1's; one
  that stays at 90 s never was (the second reading "The strategy review" left open).
- CPU0's final loop now does something when its WFI returns. Since run 11 it only looped. The first return
  sends seven bytes to the PMU's 0x6008-0x600e (Corey's OK of 2026-11-16) in three commands, each sent before
  the next one's reads, so that a read that hangs loses only what follows: when it returned and how often;
  what was pending, whether CPU0's timer was firing, the handshake bits, and the low bytes of 0x2922c0040,
  0x2922c0044 and PHASE; then how many of SIO's 43 power words, the five PCIe words, ANS2 and the PMP's read
  off. Every address is one this module has read in this state before. The only write besides the PMU's bytes
  and nub-spmi0's acks is CPU0's timer control, set to 0.
- Nothing is armed that could end that WFI, so a return in run A means a pending source: the stall the M4
  drivers found. All 0 means CPU0 never came back to send, which is not a proof that it was powered off.
- `m1-crumbs` prints the PMU's 0x0201 with every record, and `--clear` zeroes 0x6008-0x600e only after one of
  this run's stages.

**What run B adds (`ll_wake=1`): three things armed, each with its own reading.**
- The PMU's alarm 20 s after the commit's second, not an hour. An SoC that slept comes out through its reset
  vector, which is the boot loader's here: a restart at about 20 s, well before the marker.
- CPU0's own timer (the host's physical timer, CNTHP at EL2), 25 s after its last step, unmasked. If the WFI
  returns for it, the seven bytes are a look at the engine 25 s in: whether B2's gate bit reads set with
  every other core down, where PHASE is, how far the power words went. A return doesn't show that the core
  was never down: Linux's own deep idle on this chip (`cpuidle-apple.c`) is ended by the core's timer and
  comes back at the next instruction, without its registers, which is why CPU0's WFI here keeps x18 to x30
  on its stack as that driver does. Whether a core that went down under this recipe's override bits comes
  back for its timer at all isn't known.
- The keyboard backlight set blinking in step 8, 100 ms on and 100 ms off: FPWM0's power word (0x28e580190)
  asked on, and, only if it reads on within 10 ms, the PWM's on cycles, off cycles and control, in the
  kernel driver's order and with its control value (`drivers/pwm/pwm-apple.c`, read from omacom/linux-aurora).
  Four new writes. B2 switches FPWM0 off as the tenth of its 43 SIO words. A blink was chosen over a steady
  light because a gated PWM may freeze its pin either way: a blink that stops is the sign, whichever level it
  stops at. `e1-hook/kbd-blink` makes the three PWM writes awake, to see the blink first.
- It differs from macOS's state at the commit in one more word (FPWM0 on), and an armed timer may itself
  keep a core from gating. That is why A runs first and alone.

**How an ending reads** (one line each; `m1-crumbs` says which applies):
- restart at about 70 s, bytes 0: the marker; B2 short of its op 224, and CPU0 never came back. As run 17.
- restart at about 70 s, bytes set: CPU0's WFI returned. In A, a pending source, and the bytes name it. In
  B at 22 to 30 s with the timer firing, CPU0 came back for its timer, and the bytes are the look; whether
  it had been down in between they don't say.
- restart well before 70 s: something else, new here (Naeem Malik's M1 does this 5 s after its release).
- about 120 s, or 150 s or more, or dark: B2 past its op 224, read as `--m1-mark`'s.
- in B, the light: blinking to the restart, B2 short of its tenth SIO word; stopping earlier, when.

**Left out of this rung, against the plan's wording.** The cluster status words (0x28e09a000 + 4n) aren't read:
nothing has read them, the record has no bytes left for three words, and a first read under step 9's mask
could leave an SError pending, which would itself end CPU0's WFI. They go to the returning batch.

**Checked without hardware.** `tests/m1_crumbs_test.py` compiles the header and the four functions and runs
them against pages that log every access; the mutants of the new code are all caught, after the holes they
found were closed. The tests found one bug before any review: after the hundredth return every further one
would have been sent.

**What the adversarial pass found** (one subagent, read-only; all taken). The run could not have loaded:
`m1_ll_init()` ran before `m1_wd2_init()`, which maps the engine's page, and refused. The tests passed with
it, since they checked its place only against `m1_wd1_init()`; they now check it against the initializer
that maps each page it needs. And the code after CPU0's WFI kept its flags and pointers in registers a
deep WFI may not keep (the point above). Smaller ones: the power word is now written as the power-state
driver writes it, with the hardware's busy bit never written back; an `isb` after the PWM's writes; four
places where the decoder said more or less than the bytes do. Open after it, for the plan: after run B the
alarm has fired and its event stays latched (`--clear` writes only its control), and nothing here knows
whether a latched event holds a wake source up at the next run's commit.

**What Pullfrog found** (on #109's first commit; all taken). The same load order. The second command had no
mark of its own, so a record cut off after 0x6009 read as measured zeros in the gate words and PHASE: bit 7
of 0x600c is now set with PHASE's low seven bits, and without it 0x6009-0x600c aren't read. The alarm's
interval in the decoder's reading of the second (20 s in run B, not an hour). Two tests held only for one
order of evaluating a call's arguments, which C leaves open: the reads now go into locals, one after the
other. And 37 older mutants had gone stale, their lines moved by this change; they'd have shown only in a
run of the whole list, about an hour and a half. All 998 are caught again, and `tests/mutants.py` now checks
the whole list against the tree before it runs anything.

### The nights, switched on after a second flat battery (2026-11-18)

The Mac went to sleep on 2026-11-16 at 13:59 with 98% (the sleep log's last `pre` line), before the nights'
hook was installed, and was found flat on 2026-11-18: 61 Wh in under 44 hours, which is what about 2 W gives
in 31. The PMU's clock read 1970 again and was set again. Then, in that boot: `pkexec bash install.sh` (from
`rung/last-look` with main merged in) and `sleep-drain-nights on 25`. The unit started, `load` swapped the
alarm drivers in on the running system and the toggle reports rtc0 as the alarm RTC. Not seen yet: the
drivers loaded by the unit at boot, and a lid closed on battery that ends in a power-off.

**Both seen the same day** (Corey's trial at 2 minutes, lid closed on battery at 100%). The hook's log:
`12:39:06 pre armed ... minutes=2`, `12:41:06 post power-off why=alarm-time mode=closed now=1795002065
alarm_at=1795002065 try=1`, `12:41:11 finish power-off-asked`. The alarm ended the sleep in its own second,
120 s after the close, and the power-off was asked for 5 s later. The next boot (12:42:28) had the unit load
the drivers six seconds in (`done: rtc0, wakealarm present, wakeup enabled`), and the clock was right: a
power-off keeps the PMU's RTC, which only a flat battery loses. The setting is back at 25.

### `sep_ro=1` and `pw_noread=1` as built: the tenth run, with the enclave's driver read-only (2026-11-24)

**Why.** "The `disp_hs=1` run", below: the reference never sleeps with the SEP's power word off actual 0, the
ninth run was made with the enclave up, and the hypothesis is that the PMGR's wait for the SEP's power gate
has stood unmet in every run. Corey chose the state for the test (2026-11-24): `apple_sep` loaded with its
store read-only, the wait bit cleared only while the SEP's power word reads off.

**What they are.**
- **`sleep-drain-safe boot --sep-ro`:** a third GRUB entry (`sleep-drain-safe-sepro`, written with the other
  two every time), the regular line with `apple_sep.xart_writes=0 apple_sep.provision_keybag=0` (the driver's
  probe refuses the first without the second). A regular line that already sets either parameter stops no
  mode: `boot` and `boot --no-sep` take the line as it is, and only the `--sep-ro` entry drops the line's
  own setting (`-` read as `_`, with a value or bare) and adds ours, each parameter once, and says so
  (Pullfrog on #134: the first build refused all three modes). Refused before anything is written:
  `--sep-ro` where the line's own blacklist keeps the driver out. `status` says
  the store is read-only only on the bound driver's word, "OPEN TO WRITES" when that word is "enabled", NOT
  VERIFIED otherwise.
- **The proof of read-only is the binding's own:** the SEP device's `diag/xart` attribute, "enabled" or
  "disabled", which omacom's driver sets at probe from the mode it opened the store in
  (`diag::register(dev, ..., data.xart_writable(), ...)`, `diag_shim.c`'s `xart_show()`) and takes away when
  it unbinds. Pullfrog's pointer, after it had turned down the boot's words (a request) and the probe's log
  line (before the probe ends, and a log can lose it). It reads "enabled" in a regular boot here; that it
  reads "disabled" with `xart_writes=0` is from the source, not yet seen on this Mac. `xart_store.rs` opens
  the block handle read-only then and refuses `write()` and the delete.
- **`e1_hook sep_ro=1`** (needs `sep_pg=1`, never with `wake_all=1`): opts in and proves nothing. With it the
  module asks `diag/xart` of every `apple,sep` device itself, at the load and again at the first suspend's
  prepare before the DCPs' shutdown, and needs exactly "disabled\n" from at least one and from every one
  that has a driver: anything else refuses the load, or calls the replay off the way `cancel` does (nothing
  stopped, nothing written, the Mac wakes: NO FLOOR). No driver bound at all is refused too: no binding, no
  proof. It asks the device and not a path (`m1_sro_xart()`; Pullfrog on #134: a path is resolved in the
  task's own view of the filesystem, and the first build opened "/sys/..." with `filp_open()`): under the
  device's lock, waited for at the load and only tried at the prepare, the bound driver's name has to be
  exactly `apple_sep`, the device's own sysfs node has to have a `diag` that is an attribute group of the
  device's own kobject and in it a file `xart` of an attribute's size whose node is a text attribute's
  (kernfs's `KERNFS_HAS_SEQ_SHOW`: a binary attribute's node hasn't it, and its `priv` must never reach a
  text show; Pullfrog's second pass. omacom's `fs/sysfs/file.c` gives a device's text attribute
  `sysfs_file_kfops_rw`, which has `seq_show`, and `fs/kernfs/file.c` sets the flag from that), and that
  attribute's show is called as sysfs calls it, into a zeroed page. The lock stands in for the reference sysfs holds over a read: a
  driver is unbound, and its attributes taken away, only under it. A lock that is held at the prepare, a
  driver of another name, a node that isn't what it should be: no proof. Then, at both places, the SEP's power word has to
  read actual 0, polled up to 5 s as the reference's `prepare_late()` polls it. Step 7 waits for nothing
  (interrupts off, WD1 armed; a 5 s wait there would also have eaten the slack step 8's look gives the
  marker): `m1_sp_clear()` reads the word once and writes only if it reads off, as before, and with
  `sep_ro=1` reads 0x2922c0044 right before that decision as well as after the read-back.
- **`e1_hook pw_noread=1`** (needs `ps_words=1` and `atc_down=1`; never with `sio_down=1`, `pcie_down=1`,
  `last_look=1` or `probe=1`, whose own steps read one of the four or APCIE_GP after step 5:
  `m1_pwn_reader()`, after the adversarial review found the first build let them load): `m1_pw_write()` writes the four
  fabric-group words (PCIE0_REF, APCIE_GP_SYS, AFNC1_LW2, AFNC2_LW1) and their `dsb` as before and doesn't
  read them back; step 8 leaves AFNC2_LW1 out of `gates_mark=1`'s reads. The reference: "reading the three
  apcie PS words this late on the way down left the SoC stuck before sleep in 4 runs out of 4".
- **`run-floor --m1-sep-ro`:** `--m1-disp-hs`'s line without `a2_pre=1`, with `sep_ro=1 pw_noread=1`. It
  refuses, before anything is loaded or written, unless `apple_sep` is loaded and every device bound to it,
  at least one, has `diag/xart` reading exactly "disabled".

**What step 8 does without `a2_pre=1`** (the stages are `gates_mark=1`'s, 0xcb then 0xd5 or 0xd4): the record
at 0x6010-0x6016, `sep_pg=1`'s byte to 0x6009, then `sep_ro=1`'s sample to 0x6008 (the run's first result
first: a PMU command that fails stops every later one), WD1's three words read, the
stage, 0x6012-0x6013, the six gate-status words read (0x28e5a0050, 0x28e5a0064, 0x28e5a030c, 0x28e09c310,
0x28e09c338, 0x28e09c34c: not power words), their byte to 0x6016, then the light (FPWM0's power word read,
written and polled up to 10 ms, and three PWM writes). None of A2's three moves: the fabric stays at 0x333
from step 5, as the reference has it at its commit. FPWM0 is a power word read this late, in SIO's page and
not of the fabric group, as in every run with the light.

**The record.** 0x6008: K1 leaves 0x10; `0x80 | nibble`, 0x2922c0044 right before step 7's write decision.
0x6009: `sep_pg=1`'s byte as before, its nibble the sample right after the read-back. The decoder says "bit
2 clear before the write and set after it" only when both landed and the clear read back; a clear that was
skipped or didn't read back is said to be no test of the wait bit. 0x6013: top bits 0x80 with bit 5 clear
under `pw_noread=1`, read as "not read", never "read back as written" (the mark's last free pattern, which
is why the option needs `atc_down=1`). 0x600a-0x600d: `disp_hs=1`'s. 0x6016: the gate byte.

**How `--m1-sep-ro` reads** (the keyboard filmed; WD1's marker at 90 s):
- **NO FLOOR, the Mac awake again:** the store wasn't shown read-only (the attribute said otherwise, the
  driver had another name, or the device's lock was held at prepare), or the enclave read powered for 5 s at
  the load or at prepare (this kernel's `apple_sep` holds captures and entropy at its own suspend
  notifier; whether the enclave still gates then isn't known). Nothing was stopped or written by the module.
  The module's load comes after `wd2-bite`'s store in the launcher, so a refusal there still wants a restart
  before another try; the launcher's own check of `diag/xart` comes before the store. An earlier look at the
  SEP's power word in the launcher wasn't built: it has no reader of its own for that word.
- **the two samples:** bit 2 clear at 0x6008 and set at 0x6009 with the clear confirmed puts the gate
  word's change between two reads of one step, around one write. Clear in both: the bit doesn't follow the
  control bit with CPU0 up; the engine may still stop waiting.
- **the light stops:** B2 passed its opening for the first time. Evidence about the combination (the
  enclave gated, the bit cleared, no `a2_pre=1`, no late reads), not about one of them. The restart's time
  says how far: 90 s, short of its op 223; later, past it; none in five minutes, the entry may have
  completed (hold the power button).
- **the light to the restart at 90 s** with the clear confirmed and the enclave gated: the SEP's wait isn't
  the hold, or not the only one. Next is then the probe of the reference's whole display and GPU step.
- **never a light, dark, no restart:** as `--m1-disp-hs`: fits a stall in K3b, and the record narrows it.

**In that boot the fingerprint sensor is left alone.** The driver's own text: "Touch ID: enrol and verify
need xart_writes=1; the enclave saves anti-replay state after every match and stops responding when that
save is refused". An enclave that stops responding may not gate, and the load would refuse. The password
works. `xart_guard=0` (this Mac's modprobe.d) still applies there and has nothing to refuse with writes
off (inferred).

**What is new on hardware.** No register write is new, and the write-site count stays 80. New reads: the
SEP's power word polled at the load and at prepare, 0x2922c0044 once more in step 7, the driver's attribute
show called from inside the kernel under the device's lock, at the load and (lock tried) at prepare
(reasoned safe: process context; the show takes no lock and reads a flag, by the installed `apple-sep.ko`'s
disassembly and omacom's `diag_shim.c`, `DEVICE_ATTR_RO(xart)`; to look at again after a kernel update; not
exercised on hardware). Reads gone: the four fabric-group words after their writes, AFNC2_LW1 in step 8.
Writes gone: `a2_pre=1`'s three. One more PMU scratch byte (0x6008), inside the range OK'd on 2026-11-16.
New in the boot: `apple_sep` bound, its store opened read-only.

**Checked.** Built by a subagent from a written brief and three amendments (Pullfrog's passes on the plan,
#133), then read here. Reviewed twice on #134: Pullfrog (the proof bound to the device, `pw_noread=1`'s
refusals, the inherited parameters, and in two passes what the decoder may say under stage 0x39: only that
it is the last stage the record kept, and that a later command was acked where 0x6012-0x6013 carry the
outcomes' marker, which `decode()` now reads as outcomes there and not as K1's second; and 0x6008 counts
as evidence about 0x6009's command only where the decoder itself reads it) and one adversarial pass by a
subagent, which couldn't get a run armed with a writable store and found the decoder saying "`a2_pre=1` is
next" for this run's record (fixed) and the byte order (changed). After those fixes (2026-11-25):
`tests/m1_crumbs_test.py` 1,677 ok; `tests/safe_boot_boot_test.py` 273 ok; `tests/e1a_launcher_test.py` 506
ok for `--m1-sep-ro`, `--m1-sep-pg` and `--m1-disp-hs` (the launcher wasn't changed by the fixes; its full
test, 3,444 ok, was the day before); the other test files pass. Mutants: none stale of 1,708; the 234 that
bear on the first round's changes and the 40 on the second's all caught, none by a build failure only; the
rest not run again. The
launcher's line was also walked through `e1_check_params()` by hand: it differs from `--m1-disp-hs`'s, which
loaded on 11-24, by `a2_pre` off (no rule needs it on) and the two new options, whose rules it meets.
Not checked by anything here: the attribute's value with writes off; that kmod hands the command line's
two words to the module (if it doesn't, the attribute reads "enabled" or the probe fails, and the run
refuses); the enclave's power word at prepare.

### The `disp_hs=1` run: both handshakes made, bit 2 never set, and the enclave read powered (2026-11-24)

`run-floor --no-reading --m1-disp-hs` in a safe boot started with `sleep-drain-safe boot --no-sep`, on
battery at about 98%, nothing in the USB-C ports, the nights off around it, loaded 11:36
(`results/disp-hs-20261124/`; the module built at 59d3252). The ninth one-way run since the strategy review.

- **Corey:** "I have video but it looked like 90 seconds." The light to the restart.
- **The boot kept the driver out:** `module_blacklist=apple_sep` on the command line, the launcher's check of
  the kernel's own list passed, nothing bound to the SEP's device at the load or at prepare.
- **The record is whole**, and but for the new bytes it is the `a2_pre=1` run's: stage 0x9b, 0x6013 = 0x5f,
  0x6016 = 0xff, WD2 armed with the 120 s bite, `PHASE` 4, the restart 89.7 to 91.7 s after the commit: WD1's
  marker. 0x0201 = 0x02.
- **`disp_hs=1`, 0x600a-0x600d = e8 88 e8 00:** both set-up tables written and both handshakes made (both FE
  words read powered at their turn, 0xff at the load); byte B says both displays' words read off afterwards.
  0x2922c0044's low nibble read **8 in every sample kept**: at K3b's start, after disp0's place, after
  dispext0's table, after dispext0's place, and in step 7 right before the commit. 0x600d (after disp0's
  table) came back 0x00, lost across the restart as bytes there have been before. **Bit 2 read set in no
  sample.** No block write faulted or stalled.
- **`sep_pg=1`, 0x6009 = 0xc8:** the control word's bit 2 was set as found; **the SEP's power word read
  powered**, so by the guard the write wasn't made, and the wait was left set. At the load it read
  0x1f0000ff (target f, actual f, auto f, auto-enable); on 11-23, in a regular boot with `apple_sep` loaded,
  it read 0x1000030f (target f, **actual 0**, both "was gated" flags, auto 0).

**What it shows.**
- The display step as made here doesn't set B2's first poll with CPU0 up. Either the comment's cause is
  another step of the reference's (see the differences listed in the as-built section: the GPU's port and
  dispext1's block, macOS's power-word values, the order against `slpa`), or the bit only moves in a state
  this run wasn't in.
- **This run was made in a state the reference never sleeps in.** Read again with the result in hand,
  `apple_s2r_prepare_late()` waits up to 5 s for the SEP's power word to read actual 0 and *refuses the
  sleep* if it doesn't, whether a driver is bound or not ("Read only: macOS polls the actual level and
  never writes the SEP PS"). Its clearing of the wait bit is for an enclave that is already down with
  nobody to say so (still down from the last sleep), not for one that is up. So on Ante042's Mac the enclave
  is gated at every commit; here, with the driver kept out, it was up (nothing in this boot ever let it
  gate: the word's auto level is f), and the wait for its power gate stood. The section "The weekend's
  answers" read the fallback as "no driver, so clear the bit", which missed that wait.
- Runs 1 to 8 were made with `apple_sep` loaded. What the SEP's power word read at their commits was never
  read; awake in such a boot it read gated once (11-23). This kernel's `apple_sep` sends the enclave no SLEEP.
- So two things are now tangled in the opening: what sets bit 2, and whether the engine can pass with the
  enclave up. Neither is shown to be the hold.

**The guard did its job and cost the run its second question:** the bit wasn't cleared under a live
enclave, which is what the review asked for, and so nothing was learnt about the bit. Nine of the review's
ten one-way runs are used.

**A second opinion, the same day.** Corey asked for the result to be argued over with another model (Google's
Gemini, through its command-line agent, read-only on this repository and the reference's files, advice
only; the prompts and answers are in `~/.local/share/sleep-drain/spar-20261124/`). What came of two rounds,
each claim checked here against `s2r.c` before it was taken:
- **A hypothesis: the SEP's wait may have stood unmet in every run.** The reference's `s2r_sep_down`
  comment says an enclave that is down with nobody telling it to power-gate leaves "a PMGR still waiting
  for that" which "never sleeps", so the driver stops waiting (clears the bit) for it. Here the bit reads 4
  awake and nothing ever cleared it; runs 1 to 8 had `apple_sep` loaded, which sends no SLEEP; run 9 had
  the enclave up. What is measured is less than that: only run 9 read the bit and the SEP's power word
  near its commit, and the 0x1000030f with the driver loaded is one awake reading of 11-23, not a reading
  at any earlier commit. And where in the engine that wait sits isn't known: that it is B2's opening, or
  that 0x2922c0044's bit 2 is its status, is a guess (the reference never reads the word, and its comment
  rests on a trace nobody here has seen). The tenth run tests it.
- **Which state to clear the bit in.** Mine was the no-driver boot with the guard dropped. Against it: the
  reference refuses to sleep with the enclave up, so that state is one nobody has slept in, and a run that
  failed there would say nothing. Better: `apple_sep` loaded with `xart_writes=0` (omacom's driver has the
  parameter: "set to 0 for read-only operation"; it is how Ante042 runs theirs), where the enclave gates
  itself as in normal use and the AP can't write the store, and the bit cleared under the guard as it is.
  **Corey OK'd that state for the tenth run** (asked with both, 2026-11-24: "Driver read-only").
- **Two things in the recipe the reference marks as harmful**, both on the desk list of 11-23 and now to go:
  `a2_pre=1`'s three moves (the reference has the fabric at 0x333 at its commit, where they leave it at 2),
  and the read-backs of PCIE0_REF and APCIE_GP_SYS after `ps_words=1`'s writes ("reading the three apcie PS
  words this late on the way down left the SoC stuck before sleep in 4 runs out of 4"); nothing uses what
  those reads return but a bit of 0x6013 that has failed in every run.
- **An ordering difference the as-built section called neutral and isn't shown to be:** here the displays'
  PMP requests are withdrawn at noirq, before the set-up tables, and K3b comes after `slpa` and after the
  PMP's sleep word is 1 and its DVFS words idled; the reference writes each table with the request still
  up, drops it, then lowers the words, all before `slpa`, with the sleep word at 4. Not in the tenth run:
  it would move the PMP's writes into the module.
- **Not bundled, each able to hang or fault by itself:** the PMP's power word (never-write list), the GPU's
  port (its firmware parked from here), the memory controller's drain, `ps_late[]`.
- Offered and not taken for now: a probe that makes the reference's whole display and GPU step without a
  commit, sampling the gate word after every write. It answers "does bit 2 move before the commit", which
  matters less if the SEP's wait is the hold; it is the next desk item if the tenth run's light blinks on.

### `disp_hs=1` and `sep_pg=1` as built: the reference's two steps for the engine's opening (2026-11-23)

**Why.** "The weekend's answers", below, and the comparison made the same day of Ante042's driver with this
recipe (`~/.local/share/sleep-drain/ante042-20261123/diff-vs-recipe.md`, by a subagent, read here). Two things
that driver does and this recipe never did sit at B2's opening:
- **The display blocks' fabric handshake.** macOS takes each display block down as: its CPU0 word down, then
  block + 0x8014 := 1 and block + 0x82bc := 1, 2, 4, then its FE word down; before that the driver writes a
  set-up table to the block ("Linux leaves 0x28a/0x28d/0x38c000000 at reset values"). Its comment: "The
  dispext0 handshake sets 0x2922c0044 bit 2, which the PMGR S2R engine 1 waits for after GO (hv trace 1/11)."
  **Bit 2 set on 0x2922c0044 is B2's first poll.** The recipe's K3b lowers the two words of each stopped DCP
  with no handshake; maralcbr's tables, which the recipe copies, are macOS's late writes and don't have the
  displays' teardown, which macOS makes before `slpa`. The same handshake on APCIe's block (0x58c008014,
  0x58c0082bc) is in those tables and has been made by every run.
- **The PMGR's wait for the SEP's power gate** (0x28e0cc200, bit 2), set here (it reads 4).
If the comment is right, the reading of 11-18 and -19 was wrong: the gate word's bit 2 is not "the third
complex down", it can be set with every core up, and a core can read it. The fit to a count stood on four
readings in which bits 0 and 1 moved and bit 2 never did; what bits 0 and 1 are is open again. This run
samples the word around each block's writes, with CPU0 up: it can show the bit set where the comment says
it would be, not which write set it.

**What they are.**
- `e1_hook disp_hs=1` (needs `disp_off=1` and `gates_mark=1`). In K3b, for each DCP that stopped, disp0 then
  dispext0: if its FE power word reads powered, the reference's 65 set-up writes to its fabric block
  (0x38c000000 with `disp_init_38c[]`, 0x28a000000 with `disp_init_28a[]`, copied from Ante042's `s2r.c` and
  checked against it by the test, with a checksum pinned), then 0x2922c0044 read; the CPU0 word down, as
  K3b does it now; if that word read off and the FE word still reads powered, the handshake's four writes;
  the FE word down. A block is written only right after its FE word read powered, and that read and the
  writes behind it are made with interrupts off ("the fabric block is only there while its fe is powered: a
  write to it otherwise is an SError"). Neither block is ever read. 0x2922c0044 is also read at K3b's start
  and after each DCP's place in the loop.
  The table has the handshake's four writes in it already, at its entries 29, 41, 53 and 63 of the 65
  (from 0), so a block gets them twice: in the table with its CPU0 word up, and again after that word went
  down. So "the handshake wasn't made" doesn't mean nothing was written: the table's flag says.
- `e1_hook sep_pg=1` (needs `gates_mark=1`). In step 7, where the wake masks are written: 0x28e0cc200 and
  the SEP's power word (0x28e580c00) read; the control word written back with bit 2 clear **only if the bit
  is set and the SEP's power word reads actual state 0**; the control word read again; 0x2922c0044 read.
  A bit that is set under an enclave that reads powered is left, and the byte says so.
- **Neither is made with the SEP's driver in the boot**: the run is made after `pkexec sleep-drain-safe boot
  --no-sep` (the section below says why: on this Mac the enclave's store, xART, is open to its driver and
  the safe boot's read-only root doesn't cover it). Four things hold that, for both options: `run-floor`
  refuses while `apple_sep` is loaded; it refuses unless the kernel's own `module_blacklist` parameter, read from
  `/sys/module/kernel/parameters/`, lists `apple_sep` (not the command line's text: an argument after `--`
  or a list over 1024 bytes shows there and sets nothing); the module refuses to load with a driver bound to the SEP's device; and at the first suspend's
  prepare, before the DCPs' shutdown, it looks again, and a driver bound by then calls the replay off with
  nothing stopped or written (the suspend fails at prepare_late and the Mac wakes).

**Where K3b differs from the reference**, each a reason the run could miss what the reference gets:
- the reference takes the displays down before `slpa`; K3b is after it;
- it makes the handshake and the FE word's power-down even if the CPU0 word didn't read off; here neither
  is made then;
- it takes down every block whose FE word reads powered, whatever its DCP's state; here only the blocks of
  DCPs that stopped;
- it refuses to sleep if a display's word doesn't read off; here the run goes on (byte B's bit 4 says);
- it also takes dispext1's block and the GPU's port down;
- its power-word values are macOS's (0x0f0400ff, 0x0f0403f0; 0x3f0); `m1_ps_off()` clears the target as
  the power-state driver does;
- it requires the SEP's power word to read 0 within 5 s after `apple_sep` told the enclave to sleep (its
  tested path); here no driver ever spoke to the enclave in this boot, which is its fallback, and how
  often that ran on their Mac isn't known.
One thing was listed as no difference and isn't shown to be one (2026-11-24, "The `disp_hs=1` run", above:
the order matters in the reference): the two displays' requests at the PMP are withdrawn here too, and earlier
(`sio_on=1` has `run-floor` run `sleep-drain-pmp release 0xc 0xd`, made at the suspend's noirq), so the
tables are written with the request gone and the PMP still running (K4 stops it after K3b).

**The record**, in the bytes of 0x6008-0x600e the family doesn't use, each field with K1's mark until it lands:

| bytes | |
|---|---|
| 0x6009 (`sep_pg=1`) | bit 7; bit 6 the wait bit was set as found; bit 5 the word read back with it clear and the rest as found; bit 4 the SEP's power word read actual 0, before the write; bits 3:0 0x2922c0044's low nibble after it. Bit 6 without bits 5 and 4: the enclave read powered and the write wasn't made |
| 0x600a (`disp_hs=1`) | bit 7; bit 6 dispext0's handshake was made; bit 5 disp0's; bits 3:0 the nibble at K3b's start |
| 0x600b | bits 7:4 the nibble after dispext0's place in K3b; bits 3:0 after disp0's |
| 0x600c | bit 7; bit 6 dispext0's set-up table was written; bit 5 disp0's; bits 3:0 the nibble right after dispext0's table |
| 0x600d (best effort) | bit 7; bits 3:0 the nibble right after disp0's table. 0x600d was lost across the restart in the runs that wrote it: 0x00 reads as "not kept" |

The four `disp_hs` bytes go out in one command before K3b's stage. `run-floor --m1-sep-pg` is
`--m1-a2-pre-blink`'s line with `sep_pg=1`; `--m1-disp-hs` is that with `disp_hs=1`, and is the one to run.

**How `--m1-disp-hs` reads** (the keyboard filmed or watched; WD1's marker at 90 s):
- **the samples first, whatever the light does**, in the order taken: K3b's start; after disp0's table;
  after disp0's place; after dispext0's table; after dispext0's place; step 7's (0x6009), the last before
  the commit. The decoder names the first of them in which bit 2 read set. That says where the bit was
  first *seen* set: between two samples lie a table, or a CPU0 power-down, a handshake and an FE
  power-down, and the samples can't tell those apart, nor say the bit stayed set until B2 polled it. Set
  after dispext0's table or place fits the reference's comment. Clear in every sample says the steps as
  made here don't set it with CPU0 up: see the differences above.
- **the flags** (handshake made, table written) say what each block got; a block with neither had no power
  when its turn came, or its DCP didn't stop.
- **the light stops:** B2 reached its tenth SIO word, for the first time in any run. Evidence about the
  three changes together (the display steps, the bit, no `apple_sep`); the samples and 0x6009 say what the
  writes did. Then the restart's time says how far it went: WD1's marker at 90 s if B2 stopped before its
  op 223; up to about 150 s after that op if it zeroed WD1's count; WD2's 120 s if A2 never closed.
- **the light stops and no restart comes:** the entry may have completed. Nothing this recipe leaves armed
  is known to wake the Mac (nub-spmi0's enables are zeroed, every AP wake source but the PMU's is masked,
  the alarm is an hour out): wait five minutes from the dark screen, then hold the power button.
- **the light to the restart at 90 s:** B2 still short of its power-downs, with the samples to say whether
  its first poll was met.
- **no light at all, dark, no restart:** new with this run. No watchdog is armed at K3b and the light isn't
  on yet (WD1's marker is step 4's, the light step 8's): a block write that stalls, or one that faults
  where the panic's restart fails, leaves the Mac dark with the light never having blinked. Hold the power
  button. A dark Mac that never blinked fits a K3b stall and doesn't prove one: the light is skipped if
  its PWM's power word doesn't come up in 10 ms, and the replay commits all the same (it came up in every
  run since B). The record narrows it and doesn't settle it: stage 0x02 with K1's marks is the last record
  that landed, which fits a stall in K3b and also a run that went on with K3b's two commands lost (they go
  through the kernel's path, where a failure stops nothing after it); a later stage is a run that went on
  without its light.
- **a restart about 10 s after the dark screen, stage 0x02 with 0x01 at 0x600a:** a write to a display
  block faulted (inferred from `kernel.panic=10`; not seen).
Odds that the light stops, mine: one in two.

**What is new on hardware.** Writes to two fabric blocks nothing here has written: up to 69 in each of
0x38c000000 and 0x28a000000 (the table's 65, the handshake's four), each group behind the FE word's read
with interrupts off. One bit of one PMGR control word, and only under an enclave that reads off. Reads new
in this state: the control word, the SEP's power word, and 0x2922c0044 in step 7 and in K3b. Five more PMU
scratch bytes (0x6009-0x600d), inside the range OK'd on 2026-11-16. Nothing outlasts the restart. The
MMIO-write tripwire goes from 74 to 80, each site named in the test.

**Checked.** Built by a subagent from written briefs in its own worktree, then read here; reviewed by
Pullfrog and by one adversarial subagent, whose findings became eight fixes (commit 7421677): the boot's
own policy checked by the launcher and the SEP's device again at prepare (before: a module absent at the
launch could have been loaded later, and `disp_hs=1` alone loaded with the driver bound); no clear while
the enclave reads powered; interrupts off around each guarded group; a sample after each table; the
decoder and this section saying where the bit was first seen, not what set it; the docs' errors (the PMP
requests *are* withdrawn; where the handshake sits in the table; the dark ending); a 64-bit span check.
`tests/m1_crumbs_test.py` (1,595 ok): the header's helpers; `m1_sp_clear()` and K3b compiled in user space
against fake pages with a write log (at most one write, of the word less bit 2, never with the SEP's word
reading powered, in 28 runs of steps 7 and 8; per block the table, the sample, the CPU0 word, the
handshake, the FE word in that order, every block write inside an interrupts-off group that opens with
that block's FE read; an FE that reads off: no write to the block; without `disp_hs`, K3b's accesses
identical to a copy of the function before the change, in eight scenarios); the tables against the
reference's file and their checksums; `m1_pmu_ok()`, K1 and the undo over every combination of the three
options, with the real functions; the load's refusals and the recheck at prepare; the decoder over each
field, together and alone, leftovers, the stages each record can stand under, `--clear`.
`tests/e1a_launcher_test.py` (3,300 ok in full, after Pullfrog's second round, which had the launcher ask the
kernel for its blacklist and not the command line; `sleep-drain-safe` refuses a regular line with `--` or a
list over 1,024 bytes, `tests/safe_boot_boot_test.py`, 63 ok; the 45 launcher and `sp` decoder mutants run
again, none missed): the two options, their refusals with `apple_sep` loaded
and over eleven values of the kernel's blacklist, every other option unchanged. 95 `sp` and 96 `dh` mutants: in the run of
197 (with older ones whose lines moved) one was missed and two were caught only by a build failure; the
three were rewritten or given checks and are each caught by a failing check. The 197 weren't run again
after those last edits, which touched only the tests; the stale check and both test files were.
Not checked by anything here: that the FE words read powered at K3b on this Mac (if not, no block is
written and the record says so), and the read of the SEP's device without its lock at prepare, which
rests on reading the kernel (user space frozen, probes deferred from the suspend's `dpm_prepare()` on).

### The weekend's answers: a J314s that completes the entry, and a bit that makes the engine wait for the SEP (2026-11-23)

Two replies on aurora-silicon/linux#2198, read on Monday before the `ll_retn=1` run was run. It wasn't run:
the second reply names a likelier hold, and the ninth run goes to that.

**RyanTheTide, 2026-11-20** (from `drivers/soc/apple/sleep.c` on `integration/aurora`; his words in quotes):
- Bit 3 of the count word: "no name for it in our tree, and we have no T600x reading to compare against."
- The wake masks: their driver "writes every AP wake-mask word to all ones and then unmasks only the sources
  asked for"; "macOS reads all ones outside the ADT's `ap-wake-sources` (measured). So leaving the PMU's
  source open is how macOS has it." That is about the SoCs `sleep.c` supports, whose mask builder has a
  last word of its own per SoC: it says nothing of T6000's word 6, whose bits 31:13 didn't take ones in the
  `wake_all=1` run and stay unexplained. What that run verified is the named sources, masked.
- A pending source: their entry checks for one before the cores' window, "not as a stall it has seen"; "the
  stalls we hit were power words left on". The `wake_all=1` run had already put this aside here.

**Ante042, 2026-11-22: "I have a J314s (M1 Pro, T6000) that does complete the entry and comes back, and has
done so daily since early November."** A reference branch, not submitted: Ante042/linux
`t600x-s2r-reference` (integration/aurora of 11 November plus `drivers/soc/apple/s2r.c`,
`GPL-2.0-only OR MIT`, and `S2R-T600X-NOTES.md`), with an m1n1 side of its own. By its notes: several hundred
sleeps, every wake source, nights at 130 to 170 mW against 150 to 185 mW for macOS measured the same way.
This is the reference driver Naeem Malik's commit of 11-12 cited and nobody could find (inferred: the same
facts, the same model). What the reply says that touches the stall here, in its words:
- "A PMGR control bit set at boot makes the sequencer wait for the SEP to power-gate (`T6000_PMGR_SEP_PG`,
  bit 2). With the SEP driver bound the enclave gets SLEEP and gates itself; without it the branch clears the
  bit for the sleep. I do not know that it is what stops your entry, but a sequencer waiting on a gate that
  never opens fits a commit followed by the watchdog."
- The boot core's RVBAR is locked by iBoot to the boot loader on their J314s too, read back; every wake
  starts in m1n1 and resumes through a mailbox it publishes. That agrees with this Mac's reading of 11-19.
- "Secondaries have to stay parked until the boot core commits, because the global deep sleep bit hangs a
  core that idles in WFI before then." The recipe's `hold=1` does that since run 5.

**In the driver** (`s2r.c`, read with Corey's OK of the day): `T6000_PMGR_SEP_PG` is 0x28e0cc200 ("ADT
/arm-io/pmgr reg[0] + 0x4c200"), the bit `BIT(2)`; "macOS sends the SEP its sleep message instead and only
clears the bit for the wait-sep-pg=0 boot-arg (AppleT6000PMGR::_enableWaitSEPPG)". `apple_s2r_sep_pg_clear()`
reads the word, writes it back without the bit and reads it again, on the boot CPU alone where the wake mask
is written, before `cpu_suspend()`; the driver also waits up to 5 s for the SEP's power word (0x28e580c00) to
read actual 0 before `slpa`.

**On this Mac, awake, read-only** (`results/sep-pg-peek-20261123/`, 09:43, `apple_sep` loaded): **0x28e0cc200
reads 0x00000004: the bit is set.** The SEP's power word reads 0x1000030f (target f, actual 0), as on
2026-11-04; 0x2922c0044 reads 8 and 0x2922c0040 reads 3, as ever. So the engine here is told to wait for the
SEP's power gate, and nothing in this recipe has ever touched that word or sent the SEP a sleep message
(RESEARCH.md's own list of macOS's entry has the message: control endpoint 0, type 0x0c, param 2; the recipe
never sent it). Whether this kernel's `apple_sep` sends one on suspend isn't known; that the power word
reads actual 0 awake doesn't say the engine's wait is met (which signal the engine waits on isn't known: the
gate word's bit 3, set in every reading, is a candidate and nothing more).

**The reference's own SEP path isn't the one this run takes.** By its notes the tested setup loads `apple_sep`
(with `xart_writes=0 provision_keybag=0`) from a boot service, the enclave "gets SLEEP" in the
`PM_SUSPEND_PREPARE` notifiers, and the driver waits for the SEP's power word before `slpa`: the hundreds of
sleeps ran that way. Clearing the wait bit is its fallback for a SEP with no driver, and nothing in the
notes says how often that path ran (Pullfrog on #130). Here the SEP has a driver of another kind: this
kernel's `apple_sep` (`drivers/soc/apple/sep.rs` and `pm.rs` on omacom's `integration/aurora`, read today;
the installed 7.2.11-15 build is taken to match: inferred) holds Touch ID captures and entropy requests over
a suspend and keeps the node's power domains on across it, and sends the enclave no sleep message. So the
run as first proposed, in an ordinary safe boot, would have cleared the wait with the enclave up and a
driver bound, which neither macOS nor the reference's tested path does. What the enclave sees when the rest
of the SoC goes down around it isn't known. It is not shown to be the same as a watchdog restart, though
every run so far has ended in one with the enclave in this same state. **That proposal is superseded** by
the next paragraph: no run is made that way.

**And on this Mac the enclave's store is open to it** (Pullfrog on #130, then read here). The guard's default
is narrower than "refuses xART writes": it protects records this Linux didn't create and still lets Linux's
own and the shared roots change. And this Mac doesn't run the default: `/etc/modprobe.d/apple-sep.conf` sets
`xart_guard=0` (Corey's Touch ID fix of 2026-11-19, its risk written in the file: "the enclave may rewrite
session records macOS also uses. If macOS rejects one, FileVault volumes may not unlock and recovery is a DFU
restore"), and this boot's log says "xART guard off" and "writes ENABLED". The store is an APFS extent the
driver writes directly; the safe boot makes the LUKS root read-only, not that. So a restart undoes the PMGR
bit but not a write that reached the store, and a run that lets the SoC go down around a live enclave is
not to be made with that driver loaded. **The run is therefore made with `apple_sep` kept from loading** (the
safe boot with `module_blacklist=apple_sep`; `run-floor` refuses the option if the module is there): the
enclave is then never started by Linux, nothing can service an xART write, and the state is the one the
reference's fallback is written for, a SEP with no driver. Every earlier run had the driver loaded and ended
in a hard restart; none of them let the engine past its opening.

**Corey's two OKs of 2026-11-23**, asked because the conventions name the SEP:
- the write: "Yes, build it": the next run clears bit 2 of 0x28e0cc200 before the commit, one PMGR register,
  not a write to the enclave, undone by the restart. The question as put to Corey said the enclave then
  loses power untold "as at every watchdog restart": that equivalence isn't shown (the paragraphs above),
  Corey was told so the same morning, and the run as built doesn't rest on it: no enclave driver in that
  boot.
- reading Ante042's kernel branch: "read whatever you need to read! I don't see why I would limit your
  ambition here, you're in the driver's seat :)". Taken for that branch, credited; m1n1 and its forks stay
  off limits by CLAUDE.md's rule, which rests on Asahi's policy and which Corey didn't name.

**What changes** (judgment). Until Sunday the hold was in a state no core reads and no candidate was better
than one in twenty. Now there is a named register that reads as the reference says on this very Mac, a
working driver for this model to compare the whole recipe with, and its author offering to read registers.
Odds that the light stops with the bit cleared, mine: one in two. And the way to an upstream fix is no
longer to find the entry: it is to port what works onto `sleep.c`, whose author has said a `t6000` entry
needs one completed entry shown.

### `ll_retn=1` as built: CPU0's last WFI with retention left on, and a look if it comes back (2026-11-20)

**Why.** "The desk step of 2026-11-20", below: the hold is in a state no core reads, and a core that goes
down the recipe's way (CYC_OVRD's bit 0 set: no retention) has only its reset vector to come back to. Linux's
idle leaves bit 0 clear and comes back on the core's timer, but takes no complex down. Untried anywhere here:
the recipe's last core with ACC_OVRD's deep-sleep fields as they are *and* retention left on. If the E complex
still goes down in the engine's sense and CPU0 comes back, CPU0 reads what B2 did meanwhile. The candidate
for the ninth one-way run since the review; built so that it is ready, not yet handed over.

**What it is.** `e1_hook ll_retn=1`, with `ll_wake=1` (run B's family: the alarm at 20 s, CPU0's timer at
25 s, the keyboard light), refusing `gate_look=1`. One value changes: in step 10 CPU0 writes CYC_OVRD
0x3000000 where every run writes 0x3000001, and takes its WFI. From there:
- a return with nothing in ISR_EL1 and its timer not firing is counted and the WFI taken again (Linux's
  loop does the same on ISR_EL1);
- at the first return with something pending or the timer's own status set (a timer that ended the WFI
  without showing in ISR_EL1 must not read as a WFI that never held), or the first one 30 s or more after
  the WFI was first taken, CPU0 makes **the look**, once: what a `last_look=1` return reads (the time, ISR_EL1, its timer, the
  handshake words, 0x2922c0040, 0x2922c0044, PHASE, B2's power words) and two words CPU0 hasn't read in this
  state: the E complex's state word (0x28e09a000) and EACC's soc-cluster word (0x28e098000), which the
  observer read after a commit on 11-19;
- then CYC_OVRD goes back to 0x3000001 and the run ends as run B does.
The bound is by the clock, not by a count: a WFI that returns at once every time would otherwise use up any
count in under a second and test nothing (the builder's own note, taken).

**The record**, under stages of its own (0x14, 0x18, 0x1c; with `pmp_out=1`'s bit 0x94, 0x98, 0x9c), which
tell the decoder the form:

| byte | |
|---|---|
| 0x6008 | bit 7; bits 6:4 the time of the look's return; bits 3:2 how many empty returns were followed by another WFI before it (none, 1-9, 10-999, 1,000 or more: a count, which says nothing of how long the WFI held between them); bits 1:0 how often it has returned since |
| 0x6009 | as `last_look=1`'s: ISR_EL1's F, I, A, the timer firing, HS_A's bit 24, HS_B's bits 0, 1, 31 |
| 0x600a | the E complex's state word, low byte |
| 0x600b | bits 3:0 0x2922c0044's; bits 5:4 0x2922c0040's bits 1:0; bit 6 one of the five words has a bit its field doesn't carry; bit 7 the look was made at the time bound, with nothing pending and the timer not firing |
| 0x600c | bit 7; bits 6:4 EACC's bits 6:4; bits 3:0 PHASE's low nibble |
| 0x600d, 0x600e | as `last_look=1`'s (B2's power words): sent, and lost across the restart in three runs so far |

`run-floor --m1-ll-retn` is `--m1-atc-down`'s line with `ll_retn=1`: run B with ATC0's chain lowered, and
this. WD1's marker is at 70 s in this family.

**How it reads** (the keyboard filmed or watched to the restart, as in run B):
- **all zero, the light to 70 s:** CPU0 didn't come back in the 70 s. With ACC_OVRD's deep-sleep fields a
  core goes down for good whatever bit 0 says, or its timer goes with it. The idea is closed.
- **a return near 25 s, the state word 0x44, 0x2922c0040 at 3:** CPU0 was held and came back, and the E
  complex was never cycled: retention keeps the complex up, so the gate stayed closed as with a core up. The
  idea is closed, cleanly.
- **a return with the state word no longer 0x44** (0x40 is what an up sequence ends on, by the SRAM's
  listing; not yet seen on this complex), **or 0x2922c0040 no longer 3, or PHASE past 4:** the complex went
  down with every core gone and CPU0 is back to say what B2 did. That is the look; what it shows decides
  everything after.
- **the empty returns' count** decides none of these by itself: it is cumulative, so a thousand quick
  returns and then a 24 s wait read like a WFI that never held. The time of the look's return and the words
  it read are what count (Pullfrog on #129).
- **a count of returns past one after the look** can be the look's own doing: a read of its that faulted
  leaves an SError pending, and every later WFI then returns at once.
- **the light stops:** B2 reached its tenth SIO word, in this run of all runs. Then CPU0 may never report
  (B2 lowers 0x2922c0040, the AP's partition, before that); the light and the restart's time are the result.
- **a restart later than 70 s, or none:** if retention on the last core was itself what held the engine, B2
  can move after the look, zero WD1's count at its op 223 and put the restart as late as about three minutes
  after the commit; an entry that then completes leaves the Mac dark with no timed wake (the alarm fired at
  20 s, the look switched CPU0's timer off): the power button. The good endings, both.
- **a restart between 25 and 70 s with all zeros:** not the alarm's wake: a return that had lost what the
  code after it needs faults before its first command, and the panic restarts.
Odds, mine: a look into the dark state one in ten; a clean close most of the rest.

**What is new on hardware.** One system-register value on CPU0, for one WFI: CYC_OVRD's bit 0 clear, as
this kernel's idle has it all day, with the recipe's ACC_OVRD beside it, which nothing here has combined.
Two reads CPU0 hasn't made in this state. No MMIO write is new (the tripwire's count is unchanged), and
nothing outlasts the restart. What isn't known: whether CPU0's system registers, its timer among them,
survive that WFI under the recipe's ACC_OVRD; the code after the WFI keeps what it needs in the registers
`m1_ll_wfi()` saves (the same thirteen as the kernel's `apple_cpu_deep_wfi`) and on the stack, checked in
the built object.

**Checked.** Built by a subagent from a written brief in its own worktree. `tests/m1_crumbs_test.py` (1,376
ok): the header's helpers; `m1_deep_wfi()` compiled in user space with its three inline-asm statements
replaced by text and the WFI scripted (the C around the instructions runs, the instructions don't): the
timer at once, 1 to 20,000 empty returns then the timer, empty returns to the bound and a tick short of it,
never returning, later returns, the other cores and a plain `last_look=1` run unchanged; K1, the undo and
`m1_pmu_ok()` over this family; the decoder over each field and over the stages where the form does and
doesn't apply. `tests/e1a_launcher_test.py` (2,933 ok in full on the last
commit). 45 `llr` mutants, none missed, each by a failing check; no stale mutant in the list of 1,271.

**The two reviews.** Pullfrog: the decoder and this section had read residency out of the empty returns'
count; they don't now. One adversarial pass by a subagent given the code and five claims to break: nothing
blocking. In the built object nothing live crosses either call of `m1_ll_wfi()` outside x19 to x28 and the
stack; the other cores and every other run get 0x3000001 and the plain WFI as before; the stages, `--clear`,
the alarm's and the marker's texts, the parameter chain and the launcher's line hold. Its findings, all
taken: the timer's status as a second way out of the phase; the SError caveat that `last_look=1`'s decoder
has and this form's lacked; the look's own return counted among the empty ones; the gate word's bits above
3 outside "wide"; two mutants caught for the wrong reason (one by an unbound variable, one by the launcher
test crashing: that test now fails a check where a launcher dies before its hand-off). Its judgment on what
could end worse than run B's restart at 70 s: nothing that hangs past the watchdog from CPU0's side, nothing
that damages the record, nothing that outlasts the restart; the two endings added to the list above.

### The `wake_all=1` run: every named source masked, the light to the end, the same 90 s (2026-11-20)

`run-floor --no-reading --m1-wake-all` in a safe boot, on battery at about 80%, nothing in the USB-C ports,
the nights off around it, loaded 12:58 (`results/wake-all-20261120/`; the module built at 00892e9). The eighth
one-way run since the strategy review.

- **Corey:** "the keyboard blinked for all 90 seconds from what I can see." Watched to the restart.
- **The record is whole**, and but for one byte it is the `a2_pre=1` run's of 11-19: stage 0x9b, every step's
  outcome as before, 0x6013 = 0x5f (the 22 words that aren't the fabric's read actual 0, `atc_down=1`'s six among them;
  the four fabric words, written target f, didn't all read back as written, as in every run since 12), 0x6016 = 0xff (A2's three moves took and settled),
  WD2 armed and counting with the 120 s bite, `PHASE` 4, the restart 89.3 to 91.3 s after the commit: WD1's
  marker. 0x0201 = 0x02.
- **The byte, 0x6008 = 0xe0:**
  - bit 5 set: words 0 to 5 read back 0xffffffff and word 6's low 13 bits all set. So **source 54, the one
    the recipe had left open, was masked at the commit**, with every other named source.
  - bit 4 clear: word 6's bits 31:13 didn't all read back set after a write of ones. They are the ids 0xcd to
    0xdf, which have no name and which macOS's own value leaves clear: most likely bits that don't exist
    (inferred; a source that exists and can't be masked would read the same).
  - bits 2:0 = 0: none of the seven words inferred to be the wake status read non-zero after the masks.
- **The reads were new in this state and went through:** the seven masks and the seven words after them, in
  `hold=1`'s recipe with SIO's firmware stopped and the power words lowered.

**What it shows.** With no named wake source open, B2 still doesn't reach its tenth SIO word, and the restart
is the marker's. A pending source that the masks let through is not what holds the engine, as far as this
register can mask one. The follow-up of 11-19 asked upstream whether the PMU's source should be masked: for
the stall it makes no difference. (For a wake it will matter: with source 54 masked nothing would bring a
sleeping Mac back.)

**What is left, unchanged:** B2's opening in the state no core reads (the count word's bit 2, its bit 3
clear after the delay, 0x2922c0040's bit 1) and the first handshake. Eight of the review's ten one-way runs
are used. The three asks out are for macOS's state at its commit, not for more writes to copy.

**Upstream, after the as-built section was written, with Corey's OK** ("I think you can post the comment to
maralcbr"): the ask to maralcbr went up as a comment on our own drain issue with a mention of them, for the
J314s trace or a register dump near the commit, or one snapshot taken with T6000's address list
([omacom/linux-aurora#290](https://github.com/omacom/linux-aurora/issues/290#issuecomment-7038912610)).
So two asks for the commit state are posted on 11-20, to Naeem (#77 in his repository) and to maralcbr.

### `wake_all=1` as built: every AP wake source masked, and the ask for macOS's commit state (2026-11-20)

**Why.** "The desk step of 2026-11-20", below: the recipe's wake masks leave two things open, source 54
(NUB.SPMISw3IRQ, the PMU's line: word 1's bit 22) and the ids 0xcd to 0xdf of word 6 (bits 31:13, which
macOS's own value leaves clear and which no source is named for). The M4 Pro's driver says a pending source
the masks let through would hold the first gate; the follow-up of 11-19 asked upstream whether the PMU's
needs masking and has no answer. One run closes the class.

**What it is.** `e1_hook wake_all=1` changes the value of step 7's seven writes and nothing else: each of
0x292004008 to 0x292004020 is written 0xffffffff where `m1_wake_mask[]` has 0xffbfffff in word 1 and
0x00001fff in word 6. No address is new. Right after, still in step 7, `m1_wa_read()` reads the seven back
and the seven words after them (0x292004024 on, inferred to be the AP's wake status) into one byte, which
goes to the PMU's 0x6008 after step 8's record (0x6010-0x6016), so that a command that fails there costs
nothing already sent; the family's stage and `a2_pre=1`'s own bytes still come after it:

| bits | |
|---|---|
| 7:6 | both set: the byte is `wake_all=1`'s |
| 5 | words 0 to 5 read back 0xffffffff, and word 6's bits 12:0 all set |
| 4 | word 6's bits 31:13 read back all set too (whether they exist isn't known) |
| 3 | 0 |
| 2:0 | how many of the seven status words read non-zero right after |

K1 zeroes 0x6008-0x600e and leaves 0x20 at 0x6008 until that byte, so one that never landed shows. It needs
`gates_mark=1` and refuses `observe=1`, whose record is in the same bytes. `m1-crumbs` reads the byte under
that family's stages and under step 7's own (0x39, still the stage when the byte goes out; since
2026-11-25 the decoder says only that it is the last stage kept, not that nothing after it landed), and `--clear` takes the byte or the mark with the rest
when the six bytes after it are zero. At the load `m1_wa_init()` logs both sets of seven as they read awake.

**One change to every run: K1 and the undo now zero 0x6008-0x600e whatever the options** (seven zeros, the
only write `m1_pmu_ok()` lets a run without a record of its own make there). The family shares its stages,
and step 7's 0x39 is shared wider still, so a byte or a mark left by a run that wasn't cleared would have
read as the next run's (Pullfrog on #127 and the adversarial pass, the case of #116 for observers). Those
bytes are the ones Corey OK'd on 2026-11-16 for the run's record; zeros at a run's start are part of keeping
that record honest, and no run's other writes change.

**The reads are new in this state.** The seven masks have been written in the recipe's state by every run and
read only awake, at the load; the status words were read around a commit only in observer runs, which lack
`hold=1`, `sio_stop=1`, `ps_words=1` and `atc_down=1`. A read that faults would panic with SError unmasked:
stage 0xb9 with 0x20 at 0x6008, and a restart about 10 s after the dark screen where 90 s is expected
(judgment: unlikely, the same page takes writes two lines before in every run).

`run-floor --m1-wake-all` is `--m1-a2-pre-blink`'s line with `wake_all=1`: A2's three moves made by the AP,
ATC0's chain lowered, the keyboard light set blinking in step 8, WD1's marker at 90 s, and now no wake source
open. The alarm an hour out is still armed by `alarm=1`, so the nights are off around the run as before.

**How it reads** (Corey films the keyboard to the restart, or watches it all the way):
- the light blinks for the 90 s and the Mac restarts: no source that the masks left open was holding the
  engine. With bit 5 of the byte set that closes the class; without it the masks didn't take and the run
  says nothing.
- the light stops before the restart: B2 reached its tenth SIO word with every source masked. Then a wake
  source was the hold; which one, a run with source 54 alone masked would say.
- the light stops and no restart comes: the entry may have completed. Nothing wakes this Mac then, no key,
  no lid and no alarm: wait two minutes past the 90 s, then hold the power button until it goes off, and
  start it. The PMU's 0x0201 and the record say how far it got.
- the status count: a fact for the notebook, no reading by itself. The words' place is inferred, they are
  read microseconds after the masks, and whether they show raw or masked sources isn't known.
Odds that the light stops, mine: one in twenty.

**Writes.** The seven mask words, as every run since M1 run 1 writes them, with 0xffffffff in all. New in
the values: word 1's bit 22 and word 6's bits 31:13, both undone by the restart (the masks read Linux's
values again after every run so far). One new PMU byte, 0x6008, within Corey's OK of 2026-11-16 for
0x6008-0x600e. Everything else is `--m1-a2-pre-blink`'s.

**Checked.** Built by a subagent from a written brief, in its own worktree, then read line by line here.
`tests/m1_crumbs_test.py` (1,311 ok): `wa_byte()` and `m1_wa_read()` compiled in user space against fake
pages (all ones; word 6 reading 0x00001fff back; a word that doesn't take; status words non-zero), step 7's
writes in both modes (the first test of `m1_wake_mask[]`'s values at all), the byte's place after step 8's
record with the module's own `m1_crumb()` over a stub for the PMU's answer, the real `m1_pmu_start()` and
`m1_pmu_undo()` behind the real `m1_pmu_ok()` (every address, length and a set of contents, four kinds of
run), a plain family run's K1 over a byte and a mark left behind, the refusals, the decoder over landed,
armed and junk bytes and over the stages where the byte is and isn't the run's. `tests/e1a_launcher_test.py`
(2,817 ok at the first commit; the launcher's logic didn't change after, and its two options' 224 checks
were run again). 91 `wa` mutants and the 13 older ones inside the three PMU functions, none missed;
`tests/mutants.py` can now mutate `run-floor`, and the launcher test takes option names to run only those.

**The two reviews.** Pullfrog: the stale byte under a later run's stages, above. One adversarial pass by a
subagent that was given the code and the four claims to break (the module does only this on hardware; every
other run is unchanged; the decoder never gives bytes to the wrong run and `--clear` never zeroes what isn't
a run's; the launcher loads `--m1-a2-pre-blink`'s line and `wake_all=1`): nothing against the module or the
launcher (step 7 and the raw SPMI path, `m1_pmu_ok()`, K1 and the undo, the parameter chain that keeps
0x6008 from any other record, the load line's 37 arguments, the failure path's unmapping); three defects in
the decoder, all needing bytes left by an earlier run: the stale byte again, K1's mark read as "never
landed" under stages no such run sends, and `--clear` zeroing six bytes it had just called not a run's. All
taken, with its notes: the byte sent after step 8's record, the reads' precedent said as it is, a test
that had exercised its own mock, a check that couldn't be reached.

**Upstream, the same day, with Corey's OK ("post both please").** To Naeem Malik, on our open question in
his repository: whether the tracer behind his commit-state comparison could be run on a J314s given T6000's
addresses, or whether he would take one snapshot under macOS on a T600x
([malik-na/linux-aurora#77](https://github.com/malik-na/linux-aurora/issues/77#issuecomment-7035969390)).
The second, to maralcbr, for the J314s trace or a dump near the commit, **was not posted**: maralcbr/omarchy-mx-mac
is archived now (#281, where the ask of 11-04 was, is deleted) and maralcbr/linux has no issues. They are
active on omacom/linux-aurora; a comment on #290 that mentions them is a new place and needs Corey's word.
Corey also has macOS installed on this Mac (26.6.2, which can't be the hypervisor's guest on this chip: SPTM)
and can boot it, and asked whether they can run m1n1 themselves: yes, on this Mac, from their Linux desktop
(PLAN.md, A3 and A4); the script that takes the snapshot is m1n1's Python and not Claude's to write, and
whether Claude may read the result is still theirs to decide.

### The desk step of 2026-11-20: Linux's own idle moved no complex's state word in 12 s of samples, the eighth run closes the wake sources, and what the others found

**The night before.** The first night the nights' power-off covered in ordinary use: the lid closed on battery
at 17:55:57 (48%), the alarm armed for 25 minutes, `post power-off why=alarm-time` at 18:20:58, and the Mac
was off until Corey started it at 09:45 (the Apple logo, and a new session, as the setting says). That sleep
was `valid=yes`: 1,501 s at `avg_mw_cc` 1,931.

**Upstream.** No answer yet on aurora-silicon/linux#2198 to the follow-up of 11-19; nothing new on
omacom/linux-aurora#351, #454, #504 or #290, and no reply on malik-na/linux-aurora#77.

**What the others did, 2026-11-16 to -20** (public threads, PR lists and commit messages; no code read, nothing
posted). aurora-silicon/linux has nothing for T600x's sleep beyond our issue. Naeem Malik's T8103 branch
(`m1power/s2r-rs1-135`) has four commits past the sweep of 11-16, the last on 11-17; his words in quotes, his
chip's values, leads here and not facts about T6000:
- He now has both systems' state at the commit. "Linux run40 under the m1n1 hypervisor with real S2R restarts
  ~5 s after the commit; macOS 13.5 through the same commit path wakes. Both systems' state was then read by
  the same tracer when the last core reached its commit (249 power-state, always-on and clock words ...): 54
  differ." That diff is what nobody has for a T600x (#2198's first answer), and it is made with a tool this
  project's assistant doesn't build (CLAUDE.md, m1n1).
- "At its commit macOS 13.5 has DCSn+0x7b0 = 0 on all eight DRAM controllers, with their power domains gating
  themselves; Linux has 3 and the domains always on. The engine's program reads that field." Here the sequence
  after B2 waits, untimed, for the 16 DCS power words to read off, and nothing in B2 lowers them ("B2 read
  against the M4 classes", point 4): if this Mac's DRAM controllers don't gate themselves under Linux either,
  that is where an entry that got through B2 would stop next.
- "Two hypervisor captures of macOS 13.5's sleep entry with every device page trapped ... show the same 135
  writes in the memory controller's space in the last 50 ms before the commit. Linux did the first five."
  Whether his run 43 (those writes made) ends his restart isn't in a commit. His lab repository
  (malik-na/m1-power-lab, "workbench for investigating Apple M1 power management through m1n1") is public
  and was pushed on 11-20; it wasn't read.

**Before the eighth run: is there a look that comes back?** The stall is in the state no core sees, and the
plan asked for this to be weighed before another one-way run. One idea, checked at the desk: its plain form
found no support, and the form with the recipe's own settings is untested:
- *The idea.* Linux's own deep idle on this chip comes back by the core's timer. If a complex whose cores idle
  that way counted as down for the engine's gate word, the last core could idle so after the commit, come back
  on its timer, and read what B2 did meanwhile (0x2922c0040 is lowered by nothing but B2's ops 4 to 6).
- *Bit 0 of CYC_OVRD is "disable WFI retention"*, not "no return" as the module's comments have it
  (xnu-12377.121.6, `osfmk/arm64/machine_routines_asm.s`: `ARM64_REG_CYC_OVRD_disWfiRetn`, set with "OK to
  power down" in `arm64_prepare_for_sleep()`; `arm64_retention_wfi` returns to the next instruction with the
  general registers lost). The recipe sets it, as macOS does, so its WFIs can't return that way. And for a
  core put down *not* for deep sleep XNU masks IRQ and FIQ in the same register "to avoid spurious wakeups":
  for deep sleep it leaves them, as the recipe does (modes 0).
- *This kernel's idle* (`drivers/cpuidle/cpuidle-apple.c` on omacom's `integration/aurora`, state "CPU PD",
  "CPU/cluster powered down"): CYC_OVRD's bits 25:24 set to 3 and a WFI, retention left on, ACC_OVRD untouched.
- *The check* (`results/idle-watch-20261120/`: a read-only sampler through /dev/mem, words the looks have read
  awake before): 0x2922c0044, 0x2922c0040, the three complexes' state words and their soc-cluster words, read
  as fast as a loop goes for 6 s from CPU0 and 6 s from CPU7, 2.3 million samples: **one value throughout**,
  8, 3, 0x44 0x40 0x40, 0x0f 0x2f 0x2f. In a like 6 s a quarter of an hour later P1's three cores spent 4.75,
  5.27 and 6.2 s in CPU PD (`cpuidle.txt`), so they were all in it together for most of such a window (inferred:
  the counters weren't read around the sampler itself). **So in these samples, awake, Linux's deep idle moved
  no complex's state word and the gate word didn't count it.** That is this kernel's idle and nothing more: the
  complexes went down in the runs where their cores had ACC_OVRD's deep-sleep fields *and* retention disabled,
  and which of the two it takes isn't known. A core without retention has nothing to come back to but its
  reset vector; a core with ACC_OVRD's deep-sleep fields and retention left on hasn't been tried anywhere here.
- *What is left of it, not built.* (a) That untried case, as one bit changed in run B's recipe: CPU0's last
  WFI with CYC_OVRD's bit 0 clear and its timer at 25 s, where `m1_ll_ret()` already records a return and what
  it reads. The gate word can't be the witness: CPU0 is up again when it reads it, so "stayed up" and "went
  down and came back" read the same there (the decoder says so of a return already). What keeps a trace is
  the E complex's state word, 0x44 since boot and never cycled by the sequencer, which an up sequence would
  leave at 0x40 (by the SRAM's listing; not seen on this complex), and 0x2922c0040, which only B2 lowers. So
  the run needs the state word added to the return's record, and reads three ways: state word 0x40 or
  0x2922c0040 changed, the complex went down with every core gone and this is the look; a return with the
  state word still 0x44 and 0x2922c0040 at 3, retention kept the complex up; no return, as run B. (b) A secondary core as the last one down with its reset vector (RVBAR, not
  locked on the seven) pointed at a stub of ours, and something to start it with no core up. XNU's "spurious
  wakeups" says an interrupt can start a core that is down; whether one can with its whole complex down, and
  from which source, isn't known. A day's build with two unknown mechanisms in it: kept as an idea.

**The eighth run, chosen: every AP wake source masked** (`wake_all=1`, being built; its own section when it
is). Checked against the notebook first: no run here has masked source 54 (NUB.SPMISw3IRQ, the PMU's line) or
word 6's ids 0xcd-0xdf; the one sample of the words inferred to be the wake status after a commit (run 2,
CPU7 up) showed no new bit; "run 38 masks every wake source" is Naeem's, on T8103. The M4 Pro's driver names
an unmasked pending source as a holder of the first gate, and the follow-up of 11-19 asked upstream about
exactly this, so the run closes a named class whatever it shows. Odds that the light stops, mine: one in
twenty. The AIC is masked whole in steps 2 and 4 and nub-spmi0's enables are zeroed in step 7, so the one
source left open may never have had a way to assert.

**Where that leaves the method** (judgment). What stands between the last WFI and B2's power-downs is the gate
word in a state no core reads, and every difference from macOS that the recipe copies is a *write* macOS made:
its *state* at the commit has never been seen on this chip. On T8103 that state, read under the hypervisor in
both systems, gave 54 differing words where replaying writes had run dry. After this run two of the ten
one-way runs remain, and no candidate on file is better than one in twenty. So the next thing worth more than
a run is macOS's commit state for T6000: from whoever has the hypervisor set up for it (maralcbr traced this
very model; Naeem's lab is public), or made on this Mac by Corey, since the tool is theirs to use and not
this assistant's to build. Asking is Corey's call each time.

### The `a2_pre=1` run with the light: the moves made, the same 91 s, and a light seen only in part (2026-11-19)

`run-floor --no-reading --m1-a2-pre-blink` in a safe boot, on battery at about 60%, nothing in the USB-C
ports, the nights off around it, loaded 17:17 (`results/a2-pre-blink-20261119/`; the module built at 9f2bcfe).
Corey, at the end of the day: "I turned away but I believe it flashed as before".

**The record.** Stage 0x9b, the run's last by design, and every byte as on 2026-11-14 but 0x6013, which has
`atc_down=1`'s mark: 0x5f, the six off. 0x6016 = 0xff: A2's three moves took and read as A2 waits for them
at the last read before the commit (and the five muxes didn't all read parked, as then). The restart 91 s
after the commit (90.0 to 92.0 s), WD1's marker. 0x0201 = 0x02. The load had FPWM0 on awake (0xff), so the
blink's writes had what they need; whether they were made isn't in the record, by design.

**What it says, and how far.** The light did blink from the dark screen (so the blink runs with A2's moves
made, which the adversarial pass had left open), and it was not seen to stop: but it was not watched for the
whole 90 s. Read with that weight: with A2's moves found made and ATC0's chain off, B2 still doesn't reach
its tenth SIO word, as far as an observation made in part can say. A light that went out late and
unseen isn't excluded by it; the marker says only that B2 didn't reach op 223. If this run's reading
comes to carry a decision, it is run again, filmed.

**Where that leaves the stretch between the last WFI and B2's power-downs:** A2's moves are less likely as
the hold (not excluded: A2 writes and waits again with every core down), and what stays is B2's opening
(the count word's bit 2, then its bit 3 clear after a delay, then 0x2922c0040's bit 1) and the first
handshake. None of it can be read by a core.

**Upstream, the same evening.** With Corey's OK ("you can follow up on the threads and post as me if you
want to. go ahead"), a follow-up on aurora-silicon/linux#2198 (`#issuecomment-7014628444`; the text is on
this MacBook in `~/.local/share/sleep-drain/drafts-20261119/4-aurora-silicon-2198-followup.md`): each
complex's own sequence finishes; the gate word's low bits read as a count, which corrects the table of the
data comment ("bit 0 follows P complex 0, bit 1 P complex 1"); ATC0's chain lowered and the same ending;
and two questions: what bit 3 of that word is, and whether the PMU's wake source, the one this recipe's
mask leaves open, should be masked too. Nothing new was on the other threads.

**After the run:** the record cleared (the alarm, armed an hour out, with it), two saves collected, the
nights on again at 25.

### `blink=1` as built: the light in `a2_pre=1`'s run (2026-11-19)

**Why.** "The `atc_down=1` run", below: the light has only been in the `last_look=1` family, and the rungs that
made the engine's steps ahead of it were judged by the marker at B2's op 223. `a2_pre=1` makes A2's three
moves before the commit; the light says whether B2 then gets to its tenth SIO word.

**What it is.** `e1_hook blink=1`: `ll_wake=1`'s light and nothing else of it. In step 8, after `a2_pre=1`'s
moves and before step 9's mask, `m1_ll_blink()` as run B ran it: FPWM0's power word (0x28e580190) asked on,
up to 10 ms for it, then the PWM's on cycles, off cycles and control (0x39b03001c, 0x18, 0x00), 100 ms on
and 100 ms off. It needs `gates_mark=1` and refuses with `sio_down=1` or `sio_steps=1`, which switch FPWM0
off themselves. `m1_blink_init()` makes at the load the checks `m1_ll_init()` makes for `ll_wake=1`'s light:
none of the four addresses on the never-write list, the device tree's `apple,s5l-fpwm` at that address, the
page mapped, the PWM's registers read only if FPWM0 reads on. **Nothing of the light is in the record:** in
this family 0x6016 is the run's own byte, not the alarm's, so the bit `ll_wake=1` keeps there has no place.
The light is its own record: it blinks from the dark screen, or the writes weren't made.

`run-floor --m1-a2-pre-blink` is `--m1-a2-pre`'s line with `atc_down=1` and `blink=1`: the run of 2026-11-14
with macOS's state for ATC0's chain, as every run from here has it, and the light. No alarm at 20 s and no
timer in this one, but `alarm=1` still leaves the PMU's alarm armed an hour out across the restart, and
no run has met the nights' alarm driver with that: the nights off around it, as for every run that sets
the alarm, and the record read and cleared before the lid closes. WD1's marker is at 90 s in this recipe.

**How it reads** (Corey films the keyboard; the record is `a2_pre=1`'s, with 0x6013's mark `atc_down=1`'s):
- the light stops before the restart: with A2's moves made, B2 reaches its tenth SIO word. Then the hold
  of runs A and B is A2's own moves with every core down, and when it stops says how soon B2 got there.
- the light blinks for the 90 s: B2 still doesn't reach FPWM0. A2's waits are then less likely and not
  excluded (A2 writes and waits again with every core down); B2's ops 1 to 6 and the first handshake
  stay open beside them.
- no light at all from the dark screen: FPWM0 didn't read on in 10 ms, or the writes were made and the
  PWM doesn't run with A2's moves made (the blink comes after them: its four writes have only been made
  with the fabric at 3). The record can't tell the two apart, and the run then says nothing about B2.
- stage 0x99 with its byte whole and a restart at 90 s, no light: a write of the blink's hung, which the
  decoder's text for that stage doesn't name. One that faults panics, about 10 s after the dark screen.
Odds that the light stops, mine: one in four.

The blink comes after `a2_pre=1`'s moves on purpose: before them, a move that stopped the PWM would show
as one flash and read as progress.

**Writes.** The same four as `ll_wake=1`'s, made in runs B, the gate-look run and the `atc_down=1` run:
FPWM0's power word asked on and three PWM registers, undone by the restart. And `atc_down=1`'s six and
`a2_pre=1`'s three, each run before. No new address and no new value.

**Checked.** `tests/m1_crumbs_test.py`: the load's refusals, `m1_blink_init()` against `m1_ll_init()`'s checks
and their order, that it writes nothing and reads the PWM only behind FPWM0 on, the one place in step 8,
that the two options can't meet. `tests/e1a_launcher_test.py` for the new option. 12 `blink` mutants, all
caught. One adversarial pass by a subagent: no defect in the module or the launcher (the chain of options
that maps `m1_sio` before the blink can run, that `blink=1` and `ll_wake=1` can't both load, the load's
checks line by line against `m1_ll_init()`'s, nothing after the blink that touches FPWM0, SIO or the PWM,
fabricated records through m1-crumbs under `a2_pre=1`'s stages with `atc_down=1`'s mark, the launcher's
line and checks, nothing new that outlasts a restart). Its two notes are in the text above: what a dark
keyboard can mean, and the nights off around the run.

### The `atc_down=1` run: the six read off, and the ending is run B's (2026-11-19)

`run-floor --no-reading --m1-atc-down` in a safe boot, on battery at about 88%, nothing in the USB-C ports,
the nights off around it, loaded 16:11 (`results/atc-down-20261119/`; the module built at a5449f5). Corey:
"keyboard blinked for 70 seconds with the screen off and then rebooted".

**The record.** Stage 0x88, every byte as in run B but one: **0x6013 = 0x5f**, `atc_down=1`'s mark with every
group's bit but the fabric's set. So ATC0_USB to ATC3_USB, ATC0_USB_AON and ATC0_COMMON each read actual 0
within 10 ms of its write, as the twenty did (the load had them at 0xff awake, ATC3_USB at 0x2ff). The
restart 70 s after the commit (69.4 to 71.4 s), WD1's marker. The alarm fired at its second (0x1401 =
0x03, 0x140c the commit's second + 20) and nothing followed. 0x6008-0x600e all 0: no return of CPU0's WFI
recorded. 0x0201 = 0x02.

**What it says.** With ATC0's chain and the four controllers off, as the table has the other ports', B2
still doesn't reach its tenth SIO word, and the ending doesn't move by a second. The one difference from
macOS's power words that was on file and explained is spent. It isn't proof that the six are without
effect (the as-built entry below says why a light that blinks on can't be); it is the state the next runs
start from: `atc_down=1` stays in the line of any run built on `ps_words=1` from here, being macOS's state
as far as it is known.

**Where that leaves the entry, and what the light hasn't been pointed at.** The light has only ever been
in the `last_look=1` family, run 17's recipe. The rungs that made the engine's own steps ahead of it
(`sio_down=1`, `pcie_down=1`, `sio_steps=1`, `a2_pre=1`, `acc_last=1`) were all judged by WD1's marker
alone, which B2 passes at its op 223; the light goes out at about op 44. So "the same 90 s" in those
runs says B2 stopped short of op 223, not that it stopped where runs A and B did. `a2_pre=1` is the one
that bears on the place the light bounds: between B2's first poll and its tenth SIO word lie the gate's
polls, B2's first handshake, and A2's own three moves (DCS to 2, the fabric to 2, 0x28e20c400 to 0, each
waited for with no timeout), after which A2 writes PHASE 5 and B2, which waits for that, starts its
power-downs. In runs A, B and this one the engine has to make those three moves itself, with every core
down. With `a2_pre=1` it finds them made, writes them again and waits again. Whether the light then goes
out has never been looked at. A light that goes out would say that B2 gets to its power-downs with the
moves made beforehand. One that blinks on would not clear A2's waits: met by the AP with a core up, they
may still not be met when the engine waits on them with every core down (Pullfrog on #123).

**After the run:** the record cleared, three saves collected, the nights on again at 25 (the alarm reads
disarmed, its event clear, with the driver loaded).

### `atc_down=1` as built: ATC0's chain and the USB controllers' words lowered with the others (2026-11-19)

**Why.** The desk diff under "The observer's second run", below: the recipe's power words are macOS's from a
trace made under m1n1's hypervisor, which kept ATC0's port up, so ATC0_COMMON and ATC0_USB_AON are on at
the commit where the other three ports' are at 0x300; and ATC0_USB, ATC1_USB and ATC2_USB stay on through
Linux's suspend, children of words `ps_words=1` lowers. Nothing lowers them, B2 neither. The stall of runs
A and B is in the state no core sees, before B2's tenth SIO word, and a word left on that holds something
up is what stalled T6050's entry. One difference from macOS's state that is on file and was never tried.

**What it is.** `e1_hook atc_down=1`, with `ps_words=1`; `run-floor --m1-atc-down` is `--m1-last-look-wake`'s
line with it, run B's: the light blinking, the alarm 20 s out, CPU0's timer at 25 s, WD1's marker at 70 s.
At the end of step 5, interrupts off, six more words go to 0x300, the value `lab_ps_off` has for the other
ports' (target 0), each with up to 10 ms to read actual 0, as `m1_pw_write()` gives a word:

| Word | Address | When |
|---|---|---|
| ATC0_USB, ATC1_USB, ATC2_USB, ATC3_USB | 0x2922800d0, 0xd8, 0xe0, 0xe8 | before `m1_pw[]`'s words: each hangs under its port's USB_AON and COMMON |
| ATC0_USB_AON | 0x292280088 | after the four, before `m1_pw[]`'s |
| ATC0_COMMON | 0x28e080238 | after `m1_pw[]`'s, which lower ATC0_PCIE |

The live device tree has the parents: `atcN_usb` under `atcN_usb_aon` and `atcN_common`; `atc0_common` over
`atc0_usb`, `atc0_cio` (off awake, with its two children) and `atc0_pcie`. ATC3_USB is off after Linux's
suspend already (0x3ff after a sleep on 11-04); it is written with the others so that all four stand the
same. The record: 0x6013's top two bits are 0x40 in place of `ps_words=1`'s 0xc0, and a word of the six that
doesn't read off clears its group's bit there, USB_AON's or COMMON's. `m1-crumbs` reads the mark.

**How it reads.** The light is the result (Corey films the keyboard):
- it stops before the restart: B2 got to its tenth SIO word, further than any run. Then the marker's time
  and 0x0201 say how much further (70 s: short of op 224; later or dark: past it).
- it blinks to the restart at 70 s: with these six off as macOS has the other ports', B2 still doesn't get
  there. That doesn't clear them: lowering them could take one hold away and leave or add another (on the
  M4 Pro the engine stalled raising ATC0_COMMON under a fabric that had gated itself, "The M4 Pro's and M4
  Max's sleep drivers, read"; here macOS's own table has ATC1-3_COMMON off, so the premise stands). A
  light that stops can't be a false result; one that blinks on can be. Next in PLAN.md: the other words
  on at the commit that B2 doesn't lower.
- dark at stage 0x28 (0xa8), no restart at the marker: a write among the 26 power words hung, and the
  record can't say which: all of them sit between that stage and the next. Run B passed this point
  without the six, so that ending would point at them as a group.
- 0x6013's USB_AON or COMMON bit clear: a word didn't go off in 10 ms, and the run didn't test the state
  it meant to.
Odds that the light stops, mine: one in five.

**Writes.** Six power words to target 0, of the kind `ps_words=1` has written since run 12 (Corey's OK of
2026-11-09 for those; these under the standing go-ahead of 2026-11-10), in a one-way run in a safe boot,
undone by the restart. None is on the never-write list (0x28e0802d8, 0x28e700000) or beside it, and the
load refuses if one were. Nothing in the USB-C ports for this run: ATC0's port loses its PHY's power.

**Checked.** `tests/m1_crumbs_test.py`: `m1_atc[]` against the six words and their values, none of them
`m1_pw[]`'s; `m1_atc_write()` compiled as it stands against words that log every access, clean and with each
kind of word stuck on (its group's bit, about 10 ms of polls, every other word still written); the order
around `m1_pw_write()`; the load's checks; the decoder on the mark, on each outcome, and on run B's own
record with either mark. The count of the module's MMIO writes is 74, from 73. `tests/e1a_launcher_test.py`
for the new option. 30 mutants (`atc`), all caught. One adversarial pass by a subagent, as for every build
that writes to hardware: no defect in the writes, their order, the load's checks, the record or the
launcher (each address checked against the reading of 11-04, the live tree and macOS's; every child before
its parent by both trees; nothing new that outlasts a restart). Its two points on the wording are the two
bullets above; its third, that the built module was older than its source, is the rebuild after the merge.

### The observer's second run: the E complex's sequence finishes, and 0x2922c0044's low bits count the complexes that are down (2026-11-19)

`run-floor --no-reading --m1-obs-gate` in a safe boot, on battery at 100%, loaded 15:03, the nights left on
(`results/obs-clus-20261119/`; the module built at fcca811). Corey: "was about 30 sec of sleep".

**The load, awake** (the state words' first read on this Mac; no fault): the E complex's 0x28e09a000 reads
0x44, P complex 0's and P complex 1's 0x40, the value an up sequence ends on. EACC's soc-cluster word 0x0f,
the two PACCs' 0x2f. 0x2922c0044 0x08, 0x2922c0040 0x03.

**The record.** Stage 0x7f, as on 11-18: CPU0 off within 500 ms of the hand-over, ECPM within 500 ms more,
WD2 armed with 30 s to go, the restart 31 s after the commit, 0x6015 = 0xa4 (PACC1's and DISPLAY's words
still pending at the end of CPU7's watch). 0x6008-0x600e = `97 80 1a 1a 83 00 00`:

| | awake | first sample | 500 ms later |
|---|---|---|---|
| CPU0, ECPM, PCPM0, PCPM1 power words | on | off, off, off, **on** | not read again |
| EACC's soc-cluster word, bits 6:0 | 0x0f | **0x00** | bits 6:4 000 |
| the E complex's state word | 0x44 | lost (0x600d) | **0x1a** |
| P complex 0's state word | 0x40 | not read | **0x1a** |
| 0x2922c0044, bits 2:0 | 000 | lost (0x600e) | **011** |

**What it says.**
- **The E complex's own down sequence runs to its end with CPU0 down.** Its state word reads 0x1a, the
  value the sequence ends on, the same as P complex 0's, and EACC's word reads 0 with nothing pending
  (bits 4, 5 and 6 clear). The hole of the desk step is closed: the E complex goes down in the sequencer's
  sense too, not only by its power word. The first of the three outcomes.
- **Bit 1 of 0x2922c0044 is not P complex 1's.** It reads set here with CPU7 running in P complex 1 and
  that complex's power word on. On 11-18, in the same run right after ECPM's poll, the word read 001.
- **What fits all four readings: the low bits count the complexes that are down**, one more bit for each:
  000 awake; 011 with the two P complexes down and CPU0 up (the gate-look run); 001 with P complex 0 down
  and the E complex's sequence still on its way, right after ECPM's power word read off (11-18; inferred:
  that sample read no state word); 011 with P complex 0 and the E complex down and P complex 1 up (here).
  No assignment of one bit to one complex fits both 011s. Then **bit 2, B2's first poll, is the third
  complex down: all three**, and no core can ever read it set. That is a fit, not a reading: 001 and 011
  come from two runs, and this run's own first sample was in the two bytes that don't last.
- So the question left by run 6 is answered as far as a core can answer it: with a core up the gate is
  closed because the core is up. Each complex goes down by itself when its cores are gone (P complex 0 in
  every run, P complex 1 with CPU0 up, the E complex with CPU7 up). What the three do together, with the
  last core gone, nothing here has seen.
- **Where runs A and B stopped, then:** either the last complex's sequence doesn't finish when no core is
  left (bit 2 never sets), or it does, B2 passes its first poll, and is held before its tenth SIO word:
  at op 3 (bit 3 of the same word clear, after a delay), at op 6 (0x2922c0040's bit 1 clear), or in the
  first handshake. Bit 3 has read set in every reading that kept it. A word that is still on and holds
  something up behind the CPUs is the kind of cause aurora-silicon/linux#2075 found on T6050.

**0x600d and 0x600e were lost a third time**, in a run where every other command landed. The later sample
was in the bytes that last, as built for.

**Checked in XNU after the run, and closed** (xnu-12377.121.6, `osfmk/arm64/cpu.c`, `ml_arm_sleep()`): every
core clears bit 0 of its EDPRCR ("tell debugger to not prevent power gating") before its last WFI, a write
this recipe doesn't make. But `configure_coresight_registers()` maps that block only on chips that aren't
production-fused ("Skip debug-only registers on production chips"), so macOS doesn't make it on this Mac
either. `arm64_prepare_for_sleep()`'s ACC_OVRD and CYC_OVRD fields are the ones the recipe sets. What a
core can still read for nothing: DBGPRCR_EL1's bit 0 on each core, awake (not read yet).

**The diff that was "still to make" on 11-18, made at the desk** (the 206 power words of
`results/sleep-peek-20261104T120619/`, B2's 53 written words from the local listing, `m1_pw[]`): 108 words
read on awake; B2 writes 53, the SIO subtree, the PCIe words, ANS2, the PMP, AIC, GPIO and DWI. Of the
rest, what the recipe leaves on at the commit, apart from the DRAM's and the fabric's words and the AON
blocks:
- **ATC0's chain is on, where ATC1's to ATC3's are lowered.** `ps_words=1` writes ATC1-3_COMMON and
  ATC1-3_USB_AON to 0x300, macOS's values in maralcbr's table. ATC0_COMMON (0x28e080238) and ATC0_USB_AON
  (0x292280088) aren't in that table, and the entry of 2026-11-04 already says why: the trace ran under
  m1n1's hypervisor, which keeps the USB port it talks over powered, with its parents. So the table's "on"
  for ATC0 is the tracer's, not macOS's, and the recipe has copied it since run 12. They read 0xff awake
  and after a sleep: on, never gated.
- **ATC0_USB, ATC1_USB and ATC2_USB (0x2922800d0, 0xd8, 0xe0) are on through Linux's suspend** (the finding
  of October: the wakeup-enabled glue device keeps each `atcN_usb` domain on; 0xff after a sleep, never
  gated). Nothing in the recipe lowers them, and B2 doesn't. Whether macOS has them off at its commit isn't
  in the table either way; with their parents off they can't be on.
- Also on and untouched, with nothing known about macOS's side: DEBUG (0x292280058, 0x2ff), SOC_DPE,
  NUB_GPIO, PCIE1_REF, CLVR_SPMI0-4.
A power word left on that holds something up behind it is what stalled T6050's entry
(aurora-silicon/linux#2075), and ATC0's two are in the AON block beside NUB_FABRIC, the word every
complex's down sequence raises first and releases last. That the stall here is of that kind isn't shown;
it is the one difference from macOS's state that is on file, explained, and never tried.

**After the run:** the record cleared, the save collected (`safe-journal-20261119T150316.txt`), the nights
untouched.

### The desk step: the E complex's own sequence has never been looked at, and `obs_gate=1` now reads it (2026-11-19)

Corey, after the session was compacted: "okay lets continue with the 'pick up here' in the plan.md file". Its
first item is a desk step: which run names what bit 2 of 0x2922c0044 waits for. Nothing upstream had moved
(aurora-silicon/linux#2198, omacom/linux-aurora#351, #454, #504, #290, malik-na/linux-aurora#77, read the
same hour).

**Read again in the SRAM's decode** (the local listings; the bytes, not new reads):
- **Each CPU complex's down sequence**, in the clusters' sequencer's table and not in the entry pair:
  0x2922800b0 := 0xf; wait, untimed, for bit 6 of the complex's soc-cluster word (0x28e098000 + 4n); write
  0x51 to its state word (0x28e09a000 + 4n); wait, untimed, for bit 5 of that to clear; 0x28e09a024 + 4n := 1
  and a counted wait on a ClkGen word; the state word through 0x53, 0x5b and 0x1b; the complex's clock mux
  parked in four steps; 0x2922800b0 := 0x1f0000ff; and last the state word := 0x1a. An up sequence takes
  the state word through 0x5b, 0x53, 0x51 and 0x41 and ends on 0x40. The E complex's and the two P
  complexes' are the same but for their addresses. None of the six touches the engine's page 0x2922c0000.
- **0x2922c0040 is raised by nothing in the wake but B1's last ops** (:= 1, wait for bit 1 set, then
  0x2922c0008 := 1), and lowered by nothing but B2's ops 4 to 6. So it is the last thing the wake switches
  on and the first thing the entry switches off, right behind the gate.
- **B1 polls 0x2922c0044 too**: bit 1 clear, with no timeout, right after it raises 0x2922c003c (0x7b1c).
  So in the wake that bit is waited for clear before any P complex is brought up by anything on file:
  "bit 1 is P complex 1 off" is what two readings here show, not all the bit is.

**What the observer's runs did and didn't see of the E complex.** Runs 2 and 3 and the gate run of 11-18
read CPU0's and ECPM's *power words* off. Of EACC's soc-cluster word CPU7 has only ever kept bit 4 (a change
asked for and not finished), which read clear at its first read: never seen pending, so "pending, then
done" can't be told from "never pending" (said under run 3). Bits 5 and 6 were never recorded. EACC reads
0x0f awake, state bits 0 ("down or never cycled": iBoot brought the complex up, not the sequencer), where
PACC0 and PACC1 read 0x2f. The table writes EACC := 0 and the two PACCs := 0x20. And the state words,
0x28e09a000 + 4n, have never been read at all: put off on 11-10 and again on 11-16.

**So the reading of 11-18 has a hole.** "Bit 2 is clear with the E complex's power word off" doesn't say the
E complex's own sequence ran: if the sequencer never took the E complex down (its soc-cluster word already
says "down"), or is held at one of its two untimed waits, the power word can read off while the state word
still reads up. Bits 0 and 1 of 0x2922c0044 would then be the P complexes' sequences done, and bit 2 the E
complex's, after all: in a sense its power word doesn't carry, the second of the three readings left open
on 11-18. That is checkable with a core up, which "all three off together" is not.

**Checked against the notebook before proposing it** (the memory note): no run has read a state word; CPU7
has read EACC's word after a commit, for bit 4 alone; the engine's page is the first form's. Not a repeat.

**The other two candidates, and why they come after.** (a) The keyboard's light in an observer's run says
whether B2 gets further when a P complex is last: worth a run once the E complex's sequence is shown to
finish (then the last complex is the question), and it needs the blink taken out of `ll_wake=1`, four writes
and a review. (c) The observer under today's recipe: `observe=1` excludes `hold=1`, `alarm=1` and
everything built on them, so that is a larger build. What it would take away is DISPLAY's cluster word,
still pending at the end of CPU7's watch in run 3's recipe (0x6015 = 0xa4 on 11-18). That it holds the E
complex's sequence up isn't likely: AVEMSR's, written after DISPLAY's, finishes with DISPLAY's pending, and
PACC0's with PACC1's pending, so the sequences don't queue behind one another (inferred from the order of
the table's writes).

**`obs_gate=1`'s second form, as built** (`run-floor --m1-obs-gate`, the same option and the same run, run 3's
line: `replay=15 oneway=1 quiet=1 m1=1 observe=1 pmgr_late=1 obs_gate=1`). After its two polls and its first
record CPU7 reads the four power words, EACC's and PACC0's soc-cluster words and 0x2922c0044, and sends
0x6008-0x6009; then the E complex's state word, and sends 0x600d-0x600e, the two bytes that haven't
outlasted a restart yet; 500 ms later the E complex's and P complex 0's state words, EACC's word and
0x2922c0044 again, and sends 0x600a-0x600c. The bytes that have always come through carry the later sample
on purpose: P complex 0's cluster word was seen pending for up to half a second after its cores went
(T6020, and run 3's buckets), so a sample taken right after ECPM's power word reads off could catch the E
complex's sequence on its way, and a sequence that is only slow must not read as one that is held.
`e1-hook/last_look.h` has the bytes. Bit 4 of 0x6008 marks the form, and `m1-crumbs` reads both. The first form's words (HS_A, HS_B, 0x2922c0040, PHASE) read the same
in every run with a core up and are left out; of 0x2922c0044 its low three bits are kept.

**What each outcome says** (the E complex's state word against P complex 0's, the complex that reads off):
- the same value, and EACC's word settled (about 45%, mine): the E complex's sequence finishes with CPU0
  down as P complex 0's does, and bit 2 isn't it. Then bit 2 needs all three, or isn't the CPUs', no core
  can see it, and the next run is the light with a P complex last.
- another value, or EACC's bit 4 or bit 6 standing (about 45%): the E complex's sequence hasn't run to its
  end with its power word off, and the bytes say where it is: not started (the awake value), at its first
  wait (bit 6 of EACC's word), or at its second (0x51 with bit 5). That is a wait with a name and an
  address, which the entry has not had before.
- the first command and not the second (about 10%): a state word doesn't read after a commit.
If P complex 0's own value isn't the down sequence's last (0x1a), the words don't mean what the sequences'
writes suggest, and the comparison is all there is.

**Reads only.** No register is written. The two state words are in the soc-cluster words' page, which CPU7
reads after every commit; they are read for the first time here, awake at the load (with P complex 1's),
logged, before K1 changes anything: if such a read faults, it faults in the safe boot with nothing done. To
the PMU: the same seven bytes as the first form, the same commands' path. No adversarial pass: reads, on
reviewed code.

**Checked.** `tests/m1_crumbs_test.py`: `m1_og_look()` compiled as it stands against pages that log every
access (a seventh page for the soc clusters'), the order of its reads and its three commands, the header's
four new functions over every input that reaches a field, the decoder on the second form whole and stopped
short at each command, and on the first form's record of 11-18, which it still reads. The `ogl:` mutants
brought up to the new code (68, all caught).

### CPU0's RVBAR is locked here, the data posted, and the SMC's keys say nothing about a run (2026-11-19)

**RVBAR, read** (`rvbar-look` in a safe boot at 13:07, Corey's run; `results/rvbar-20261119/rvbar.txt`). Each
core's cpu-impl-reg + 0x0, one 64-bit load made on the core itself. All eight read cleanly:

| Core | RVBAR |
|---|---|
| cpu0 (E) | 0x00100100059e4001 |
| cpu1 (E) | 0x00111100059e4000 |
| cpu2, cpu3, cpu4 (P0) | 0x01100100059e4000, 0x01121100059e4000, 0x01132100059e4000 |
| cpu5, cpu6, cpu7 (P1) | 0x02100100059e4000, 0x02111100059e4000, 0x02122100059e4000 |

One address in all eight, 0x100059e4000 (in RAM, 0x59e4000 above its base: the boot object's entry, by
Asahi's boot protocol; not checked against m1n1-aurora's load address), bit 0 set on the boot core alone,
and upper bits that differ by core (the complex in bits 59:56, a per-core field below it: not decoded).
Bit 0 as the lock is Asahi's reading of this register. So the notebook's "iBoot locks CPU0's RVBAR" holds
on this boot chain, and aurora-silicon's "T6000's RVBAR isn't locked" is about another chain, or about
another bit. What it means for the way back: after a completed entry the engine would bring the boot core
out of reset at m1n1-aurora's entry, and what happens there is the loader's.

**Posted**, with Corey's OK ("please post the comments"): the entry's data as one comment on
aurora-silicon/linux#2198 (`#issuecomment-7008916763`; the text is on this MacBook in
`~/.local/share/sleep-drain/drafts-20261119/3-aurora-silicon-2198-data.md`): how it's measured, the SRAM's
tables and the entry pair, nine measured points, what was tried, what isn't known, and this table.

**The SMC's keys across a run** (the strategy review's "never diffed"). A dump after a plain restart
(`results/smc-dump-20261119T131936/`, the boot after `rvbar-look`'s safe boot, on the charger) against the
four taken after one-way runs on 2026-11-18: 86 keys that aren't floats read the same after all four runs
and differently after the plain restart, and every one is the charger's or the battery's (`AC-*`, `CH*`,
`D3*` for the port the charger is on, `B0*`, `BC*`, `CM*`, the `zS*` counters, `KIN*`, `LCAD`). No key
that names how the last boot ended. That lead is spent.

### Answers from aurora-silicon, a fourth PMU for the alarm, and a night the nights didn't cover (2026-11-19)

**aurora-silicon/linux#2198, answered** (RyanTheTide, 2026-11-19, "from `integration/aurora` 0abc2f385e54
and aura main"; his words in quotes):
1. macOS's commit state on T600x: "We don't have it. No PMGR power-word dump at macOS's commit has been
   taken on an M1 Pro or Max [...] The J813 and K116s lists are from other SoCs and won't carry over." So
   the diff of 2026-11-18's plan has no other side, from anyone.
2. The mailbox: "Nothing in `sleep.c` is T600x-specific about `/chosen/aurora,s2r-mailbox`. The kernel uses
   whatever the loader publishes. aura publishes it on SoCs whose RVBAR is not locked (aura#217), and that
   includes T6000: on an M1 Pro under aura the node is published and the boot core parks. Under m1n1-aurora
   nothing publishes the node [...] A loader that wants to send the boot core back through it needs the
   same RVBAR setup aura#217 does." This notebook had CPU0's RVBAR as locked by iBoot, from XNU's words and
   never from a read here: by his account it isn't on this chip. Still to read on this Mac.
3. A completed entry on T600x: "No. No M1 Pro or Max has come back through the reset vector after a
   completed entry. `sleep.c` has SoC operations only for T8140, T8132, T8142 and T6050 [...] on T6000 the
   J314s qualification covers s2idle only."
4. Data: "T6000 data is welcome now, here: the entry pair's structure, its polls and gates, and what was
   ruled out. It doesn't need to wait for a completed entry. A `t6000` SoC entry in `sleep.c` needs one".
Replied the same day with Corey's OK ("yes, go ahead, thank you"; `#issuecomment-7008106882`): thanks,
the data to follow in that issue as one comment, the two results of 11-18 (B2 never at its power-downs;
0x2922c0044's bits against the complexes), and whether the word is known from another SoC. The data
comment itself is still to write and to show Corey first.

**omacom/linux-aurora#351** (enriquebv, 2026-11-18): the series' rtc-macsmc works on a fourth PMU, Sera,
on a J293, with a T8103 description in #504 on top; T8103's nub-spmi numbers its own events from 0xc0 as
T6000's does, so `apple,t8103-spmi` needs `apple_spmi_t6000`, a one-line change he carries in #504. About
0.8 W in s2idle there. Replied with Corey's OK (`#issuecomment-7008107415`): keep it in #504, as with
#454. Still no maintainer review and no `ci:build` on #351. Nothing on omacom/linux-aurora#290 or
malik-na/linux-aurora#77.

**The night of 11-18 to 11-19: the nights weren't armed.** The lid was closed at 17:00 at 76% on the
charger (`pre not-armed why=on-charger`), the charger was pulled while the Mac slept, and it was opened
at 12:33 the next day at 41%: 70,365 s in one s2idle. By design the hook left a sleep that starts on the
charger alone; a laptop taken off its charger asleep is exactly the case it was built for. Changed the
same day: on the charger a closed lid is armed too, with a deadline of its own. The alarm then ends that
sleep every N minutes; while the charger is still there post clears and logind suspends again; the first
of those wakes that finds the Mac on battery with the lid closed powers it off. A lid open on the charger
isn't armed (that sleep would end by itself with nobody asking), nor a charger that can't be read. Cost:
a Mac asleep on its charger with the lid closed wakes for a few seconds every 25 minutes.

### The observer's gate run: bits 0 and 1 are the two P complexes'; bit 2 isn't the E complex's power word (2026-11-18)

`run-floor --no-reading --m1-obs-gate` in a safe boot, on battery at about 47%, loaded 16:06, the nights
left on (`results/obs-gate-20261118/`). Run 3's line with CPU7's look added.

**The record.** Stage 0x7f: CPU7 recorded everything, nothing pending. As in run 3: CPU0 read off within
500 ms of the hand-over and ECPM within 500 ms more, CPU1 to CPU6 off at their release, PCPM0 off, WD2 armed
with 30 s to go, and the restart 31 s after the commit. 0x6008-0x600e = `87 a8 03 09 84 00 00`.

| | awake | gate-look run: CPU0 awake | this run: CPU7 awake |
|---|---|---|---|
| CPU0's and ECPM's power words | on | on (CPU0 running) | **off, off** |
| PCPM0's | on | off (runs 1, 5, 6) | off |
| PCPM1's | on | off | **on** (CPU7 in it) |
| 0x2922c0044, low nibble | 1000 | 1011 | **1001** |
| 0x2922c0040, low byte | 0x03 | 0x03 | 0x03 |
| PHASE | 0 | 4 | 4 |
| HS_A bits 31, 24 | 0, 0 | 1, 0 | 1, 0 |
| HS_B bits 31, 1, 0 | 1, 0, 0 | 1, 0, 1 | 1, 0, 1 |

**What it says.**
- **Bit 0 is P complex 0's and bit 1 is P complex 1's**, each set when the complex is off: bit 1 was set
  with PCPM1 off and is clear with CPU7 awake in it, and bit 0 is set in both. Two readings, one bit
  changing with the one complex that changed.
- **Bit 2 is not the E complex's power word.** CPU0's and ECPM's words read off and bit 2 is still clear.
  So the reading of the gate-look run was half right: a bit per P complex, and bit 2, B2's first poll,
  is something the E complex going off (as its power word shows it) doesn't set while a P complex is up.
  0x0d and 0x0e, the outcomes that would have named bit 2 the E complex's, didn't come; nor did 0x0b.
- What bit 2 is isn't shown. What fits the three readings: all three complexes off together (then it has
  never been set where anyone could read it); or the E complex's in a sense its power word doesn't carry
  (its own sequence's end, or its being the last); or nothing of the CPUs' at all. Bit 3 was set in all
  three. None of these says where runs A and B stopped: they bound B2 before its tenth SIO word, which
  fits B2 past op 1 and held at op 3 or in the first handshake as well as B2 held at op 1, and nothing
  sampled the word after CPU0's WFI. So nothing here says the last complex stays on (Pullfrog on #117).
- **The rest is the same with either core up:** PHASE 4, A2 raised and unanswered (HS_A 31 up, 24 clear),
  HS_B's bit 0 up, 0x2922c0040's bit 1 still set. And this under run 3's recipe as under today's, so
  the recipe's additions since run 3 don't show in these words.

**The second command didn't land, again, and this time CPU7 went on.** 0x600d and 0x600e read 0, as in the
gate-look run. Here the commands after it all landed (stages 0x72 and 0x7f, 0x6012, 0x6015), and
`m1_crumb()` stops at the first command that fails: so the two-byte command to 0x600d was sent and
acknowledged, and its bytes aren't there at the next boot. Awake, through the kernel's path, all seven bytes
take a write and read back (`write-test.txt`: each byte alone, the two-byte write to 0x600d, five and seven
bytes from 0x6008). So either a raw write to those two after the commit doesn't take, or the bytes don't
outlast the restart: the boot chain may clear them, which would also be why they read 0 in every log
before 2026-11-16. **For the gate-look run this takes back one thing only:** its lost second command is
no proof that CPU0 hung in the look. It isn't proof that CPU0 got through it either: that look read SIO's,
the PCIe's, ANS2's and the PMP's power words before the command, not the four CPU words CPU7 read here,
and that run has no later command to show progress. Whether CPU0 reached its timer and its WFI there stays
open, as the decoder says (Pullfrog on #117). Until a restart with test values in them says which bytes
survive, a record uses 0x6008-0x600c.

**After the run:** the record cleared, the nights untouched, an SMC dump in
`results/smc-dump-20261118T161027/`, redacted.

### `obs_gate=1` as built: the observer reads the gate word with CPU0 and the E complex down (2026-11-18)

**Why.** The gate-look run read 0x2922c0044 as 0x0b with CPU0 awake and the two P complexes off: bits 0
and 1 up, bit 2, B2's first poll, clear. The reading that fits, one bit per CPU complex, is tested by
moving the core that stays up into another complex. Runs 2 and 3 did that for the power words: CPU7, in P
complex 1, stayed up through the commit and saw CPU0's and the E complex's power words read off within
500 ms each. Neither read the engine's page. This run is run 3's with that read added.

**What each outcome says** (the word's low nibble, with CPU0 and ECPM off, PCPM0 off, PCPM1 up):
- bit 2 set and one of bits 0 and 1 clear (0x0d or 0x0e): a bit per complex, and the E complex's is bit 2:
  B2's first poll is the E complex down, which the recipe reaches whenever CPU0 isn't the core kept up.
  Then 0x2922c0040's bit 1 and PHASE say whether B2 moved past its ops 1 to 6 with a P core still up, and
  the stall of runs A and B lies after the first poll, at op 3's bit or the handshake.
- 0x0b again: the bits don't follow the complexes that are down. Bit 2 is then the last one, whichever
  complex that is, or something else, and the two set bits were not the P complexes'.
- anything else is new by itself. The second sample, 500 ms on, says whether the word was still moving.
Estimate, mine: the first about 55%, the second 30%.

**What it is.** `e1_hook obs_gate=1`, with `observe=1`; `run-floor --m1-obs-gate` is `--m1-late`'s line
with it (`replay=15 oneway=1 quiet=1 m1=1 observe=1 pmgr_late=1 obs_gate=1`). In `m1_observe()`, after
both polls and CPU7's first record (stage 0x71 with the "off" bits), `m1_og_look()` reads CPU0's, ECPM's,
PCPM0's and PCPM1's power words, HS_A, HS_B, 0x2922c0040, 0x2922c0044 and PHASE and sends 0x6008-0x600c;
waits 500 ms on the counter; reads the four, 0x2922c0044 and 0x2922c0040 again and sends 0x600d-0x600e.
K1 leaves 0x6008 as 0x40, CPU7's first command writes over it. `--clear` zeroes the seven bytes after such
a record. Run 3's recipe otherwise: no alarm, no hold, no marker, WD2 at its own 30 s; CPU7 goes to its
own WFI about half a second later than in run 3.

**Writes.** None to a register. To the PMU: 0x6008-0x600e, the bytes OK'd on 2026-11-16, zeroed and
marked at K1 through the kernel's path and written by CPU7's two commands; `m1_pmu_ok()` lets those
through with `obs_gate=1` alone and nothing that starts below 0x6008. No power-domain word is read that
CPU7 or CPU0 hasn't polled after a commit before (the gate-look run's second command, after SIO's and the
PCIe's words, never landed; none of those is read here).

**Checked.** `tests/m1_crumbs_test.py`: `m1_og_look()` compiled as it stands against pages that log every
access, `m1_pmu_ok()` over every address and length with it, CPU7's record through m1-crumbs under each of
its stages. 34 new mutants (`ogl:`), all caught, one after a test was added for it; two older ones brought
up to the code. `tests/e1a_launcher_test.py` for the new option. No full mutant run and no adversarial
pass: reads on reviewed code.

**What Pullfrog found** (on #116; taken). CPU7's stages are an ordinary `--m1-observe` or `--m1-late`
run's too, and those runs' K1 left 0x6008-0x600e alone: an `obs_gate=1` record left uncleared would have
read as the next ordinary observer's. K1 now zeroes the seven bytes in every observer's run (and the undo
does, and `m1_pmu_ok()` allows it there), and only `obs_gate=1` writes the mark. K1's mark by itself under
a stage that isn't CPU7's is said to be possibly an earlier run's. A test runs the real `m1_pmu_start()`
over a retained record for each kind of run, and the decoder on what an ordinary observer then leaves.

### The gate-look run: 0x2922c0044 reads 0x0b with CPU0 awake, two new bits up and bit 2 still clear (2026-11-18)

`run-floor --no-reading --m1-gate-look` in a safe boot, on battery at about 75%, loaded 14:47, the nights
off around it (`results/gate-look-20261118/`). Corey: "seemed like the same as before": the keyboard
blinking from the dark screen to the Apple logo.

**The record.** Stage 0x08, and everything before the commit as in runs A and B. The restart 70 s after the
commit (69.2 to 71.2 s). The alarm fired again (0x1401 = 0x03). 0x0201 = 0x02. And 0x6008-0x600e =
`08 a8 03 0b 84 00 00`: the look's first command landed, 1 s after the commit with CPU0 awake; its second
didn't.

| Word | Awake, at the load | At the look | Runs 6 to 10, CPU0 awake |
|---|---|---|---|
| 0x2922c0044, low byte | 0x08 | **0x0b**: bits 0, 1 and 3 set, bit 2 clear | bit 2 clear, bit 3 set (the two bits kept) |
| 0x2922c0040, low byte | 0x03 | 0x03 | bit 1 set |
| PHASE | 0 | 4 | 4 |
| HS_A | 0x00010000 at run 7's load | bit 31 up, bit 24 clear | the same |
| HS_B | 0x80000000 | bits 0 and 31 set, bit 1 clear | not read |
| pending for CPU0 (ISR_EL1) | | nothing | |

**What it says.**
- **B2's first poll is still closed with CPU0 up**, under today's recipe: bit 2 of 0x2922c0044 clear, bit 3
  set, as in runs 6 to 10. The additions since run 10 didn't open it. The one-in-four outcome didn't come.
- **Two bits of that word are new.** Bits 0 and 1 are set at the look and clear awake. Runs 6 to 10 kept
  only bits 2 and 3, so whether they were up then isn't known. HS_B's bit 0 is up too, where awake it is
  clear.
- **A reading that fits, and isn't shown** (mine): the word's three low bits are the three CPU complexes',
  each set when its complex is down, and bit 3 says one is still up. Awake, all three up: 0x08. At the
  look, the two P complexes off (their cores parked; runs 1, 5 and 6 read their power words off) and the E
  complex up, since CPU0 is awake in it: two bits set, one clear, bit 3 set: 0x0b. B2's first poll would
  then be the last complex's bit, here the E complex's, and its third op the "none up" bit. It would make
  the gate the cores' after all, with the P complexes' part of it met by the recipe. Which bit is which
  complex's isn't known either (the E complex is index 0 among the cluster words, and it's bit 2 that is
  clear). Run 2's observer, CPU7, saw CPU0's and the E complex's power words read off after the commit:
  the same observer reading this word would show which bit follows which complex.
- **Then the question is the last complex.** If the bits are the complexes', runs A and B say that with
  CPU0 in its last WFI the third bit still doesn't come up, or comes up and something after it holds: no
  run has had anyone awake to see the last complex go.

**The look's second command didn't land** (0x600d and 0x600e read 0). After the first command CPU0 reads
SIO's 43 power words, FPWM0's, the PCIe five, ANS2's and the PMP's, and sends two bytes: one of those reads
hung, or the command failed. Which isn't shown, and it matters for what this run's ending is: a CPU0 that
hung in the look never set its timer or took its WFI, so the 70 s and the light here are the look's own
ending, not a third instance of run B's (the decoder says so). For a later look after the commit: the
engine's page reads cleanly with a core up; the power words are not shown to. (Qualified the same day,
"The observer's gate run", above: the two bytes were lost again in a run whose later commands landed, so
their loss alone doesn't show a hang; whether CPU0 got through this look stays open.)

**After the run:** the record cleared, the alarm disarmed, the nights on again at 25 (the alarm reads
`ctrl=0x01 event=0x00 mask=0x01` with the driver loaded), and an SMC dump in
`results/smc-dump-20261118T145759/`, redacted.

### `gate_look=1` as built: the gate's words read once more with CPU0 awake, under today's recipe (2026-11-18)

After run B, Corey: "so what is the next step then?", and, to the answer below, "okay go ahead and build it".

**Why this read, when runs 6 to 10 made it.** They sampled the engine's words with CPU0 awake 1 s and 25 s
after the commit: 0x2922c0044's bit 2 clear and bit 3 set, 0x2922c0040's bit 1 set, HS_A's bit 31 up and bit
24 clear, PHASE 4; and couldn't say whether the awake core itself held B2's first poll. From run 11 on CPU0
has gone straight to its WFI, and the recipe has gained SIO's firmware stopped (run 11), the 20 power words
(run 12), the cluster words' ack (run 16) and the fabric back at 3 (run 17): each a candidate for a poll
that waits on more than the cores. Under that recipe the words have never been read after the commit. Runs
A and B say B2 is held before its power-downs with CPU0 in its last WFI too. So one read, with two
outcomes: bit 2 of 0x2922c0044 set with CPU0 up takes the cores out of B2's first poll, and the other bytes
then say how far B2 and A2 are; the old bits again say the additions since run 10 didn't open it, and
nothing about the stall after the WFI (Pullfrog on #113: a reading with a core up isn't that stall's).
Estimate, mine: the first about one in four.

**What it is.** `e1_hook gate_look=1`, with `last_look=1`; `run-floor --m1-gate-look` is run B's line with
it. In step 10, before `m1_ll_timer()` and the WFI, `m1_ll_look()` waits on the counter until 1 s after the
commit's last write, then makes `m1_ll_ret()`'s reads and sends them in two commands: 0x6008 := 0x08 with
0x6009-0x600c (the handshake bits, 0x2922c0040's low byte, 0x2922c0044's, PHASE under bit 7), then 0x600d
and 0x600e (SIO's count and FPWM0, the PCIe five, ANS2, the PMP). Bit 3 of 0x6009 carries HS_A's bit 31,
where the return's form has CPU0's timer. K1 writes 0x6008 as 0x04, so that 04 and six zeros say a look was
to come and never landed, where seven zeros would read as a WFI that didn't return. A WFI that returns
after the look sends 0x6008 alone with bit 3 kept. No stage of its own. Everything else is run B's: the
marker at 70 s, the alarm at 20 s, CPU0's timer (now 26 s after the commit), the light.

**Writes.** None to a register. One more PMU write, 0x6008 := 0x04 at K1 through the kernel's path, inside
the bytes OK'd on 2026-11-16. The reads are words this module has read after the commit with a core up
(runs 6 to 10: the engine's page, HS_A, SIO_CPU's word) or reads at every load (the power words); HS_B is
in the engine's page and wasn't among run 6's.

**Checked.** `tests/m1_crumbs_test.py`: `m1_ll_look()` compiled as it stands against pages that log every
access, `m1_ll_ret()` after a look, the record's new forms through m1-crumbs. 28 new mutants (`glk:`), all
caught; two older ones brought up to the code, found at once by the list's check against the tree.
`tests/e1a_launcher_test.py` for the new option. No full mutant run and no adversarial pass: a read-only
step on reviewed code (PLAN.md, "Tokens").

**What Pullfrog found** (on #114; taken). The look's marker alone doesn't show that CPU0 set its timer or reached
its WFI: a power word's read that hung would end CPU0 inside the look, and the decoder read 0x08 as a WFI that
didn't return. It now says no return was recorded, says the timer and the WFI follow the look only where the
look landed whole, and names a look that stopped short as an ending of the look's own making; the two stage
lines no longer say the WFI was taken.

### Run B, the last look with a wake: the light blinked to the end, the alarm fired into nothing (2026-11-18)

`run-floor --no-reading --m1-last-look-wake` in a safe boot, on battery at 98%, loaded 13:07, the nights
switched off for it (`results/last-look-wake-20261118/`). Run A with three things added: the PMU's alarm 20 s
after the commit's second, CPU0's own timer 25 s out, and the keyboard's PWM set blinking in step 8 (awake
it read control 0x4001, off cycles 0x3c0, on cycles 0).

**What Corey saw**, filmed: "it went black and almost at the same time the keyboard started blinking and
blinked for 70 seconds then I got the apple logo."

**The record.** Stage 0x08, the last. Everything before the commit as in run A (0x6012 = 0xff, 0x6013 =
0xdf, 0x6007 = 0xf5, 0x6005 = 0x7d: PHASE 4, WD2 counting with its 120 s bite). 0x6016 = 0xfd: the alarm's
commands acked, and bit 6, the PWM's three writes made. The restart 70 s after the commit (68.8 to 70.8 s).
0x6008-0x600e all 0. The alarm, read with the stock drivers before anything loaded: 0x1401 = 0x03, its
event bit set and its enable gone, 0x140c the commit's second + 20. PMU 0x0201 = 0x02.

**What it says.**
- **B2 never got to its power-downs.** FPWM0 is the tenth of the 43 SIO words B2 switches off, from its op
  35 on, and the light it clocks blinked until the restart. With WD1's count never zeroed (the restart at
  70 s again), B2 is held before its tenth SIO word for the whole 70 s: at its first poll (op 1,
  0x2922c0044's bit 2), at op 3's, in the first handshake, or not started; the light doesn't say which. This is the witness the plan asked for
  before any more work on B2's later ops: none of the AP-made steps on its power-downs (runs 18 on) could
  have mattered, because B2 doesn't get there. What rests on an assumption: that B2's write to FPWM0's
  word would have stopped the PWM though the run had asked the word on.
- **The SoC wasn't asleep at 20 s.** The alarm fired (0x03) and nothing followed: no restart at 20 s, which
  is what a sleep the PMU can end would have shown. The clocks ran all 70 s, since the PWM did.
- **CPU0 didn't come back for its timer** either: no bytes, and the restart came 45 s after the timer's
  time. By the record's own terms a core that was still powered would have come back, so this is the
  first sign that the last core does go down; not a proof (a first command that failed reads the same,
  and a core that deep may not take its timer).
- So the picture after the commit: seven cores parked and CPU0 in its last WFI (whether it is powered
  down is a hypothesis, above), the engine in A2 (PHASE 4, 10 ms after the release) with WD2 counting,
  B2 not as far as its tenth SIO word, and WD1 ending it at 70 s. Which of B2's opening polls or the
  first handshake holds it isn't shown: nothing sampled the gate's words after CPU0's WFI. Runs 6 to 9
  sampled them with CPU0 awake, where the awake core may itself hold the gate ("What it doesn't
  settle", above), so those readings aren't this stall's until something shows the two are the same.

**After the run:** `m1-crumbs --clear` disarmed the alarm (0x1401 = 0x01); the nights went on again at 25,
and with the alarm driver loaded `pmu-alarm status` reads `ctrl=0x01 event=0x00 mask=0x01`: nothing of
run B's alarm is left latched. An SMC dump after it is in `results/smc-dump-20261118T131403/` (redacted);
against the one after run A, outside sensors and the battery's keys: `CLWK`, `SAQw`/`SARw` (0x1600 to
0x900), `SAQy`/`SARy` (3 to 1), `SEs6` (0 to 0x20) and a few `z` keys. Not read further.

### Run A, the last look: the restart moved with the marker, and CPU0's WFI didn't return (2026-11-18)

`run-floor --no-reading --m1-last-look` in a safe boot, on battery at 100%, loaded 12:52:30
(`results/last-look-20261118/`: `load.txt`, `crumbs.txt`, `clear-collect.txt`). Run 17's recipe with WD1's
count written as 80 s and CPU0's record armed; nothing else new, and no alarm near, no timer, no light.

**The record.** Stage 0x0b, the last. Everything before the commit as in run 17: 0x6012 = 0xff, 0x6013 =
0xdf, 0x6007 = 0xf5 (WD1 stood just before the commit: armed, bite 150 s, its count past 80 s and running),
0x6005 = 0x7d (WD2's bite written as 120 s, armed and counting, PHASE 4 at CPU0's last read). The restart
came 70 to 71 s after the commit by the PMU's RTC (69.6 to 71.6 s at the first read, 69.2 to 71.2 s at the
second); Corey: "around 70 seconds". 0x6008-0x600e all 0. PMU 0x0201 = 0x02. Awake, at load: 0x2922c0040
= 3, 0x2922c0044 = 8, PHASE 0, HS_B 0x80000000, 26 of SIO's 43 power words off, FPWM0 0xff, two of the
PCIe five off, ANS2 0x1f0003ff, the PMP 0x1f0f03ff.

**What it says.**
- **The restart is WD1's.** With the count at 60 s it came at 90 s (runs 17 on); with 80 s, at 70. The
  other reading of 90 s, a pair that finished and a later A sequence arming WD2 with its own 30 s, has no
  reason to move with WD1's count, and is out. So is a WD1 that can't reset the SoC in this state. B2
  doesn't reach its ops 223-224, which zero WD1's count, within 70 s of the commit.
- **CPU0's last WFI didn't return** in those 70 s, as far as the record goes (it stayed in its WFI or went
  down; a first command that failed would read the same). Nothing was armed, so nothing pending ends it:
  the AP isn't what's holding the entry open by coming back. Not a proof that the core was powered off.
- Still not shown: where B2 stands (its first poll, the first handshake, or its power-down). Run B's light
  says whether it got to the tenth of SIO's words.

**The alarm's line in this record is wrong for this boot.** The decoder says "the reset or the boot
firmware cleared the enable (new)": 0x1401 read 0x01 where every run before showed the armed alarm
surviving the restart. What is new is the nights: `sleep-drain-alarm.service` loads the alarm driver six
seconds into a regular boot, and its probe disarms the alarm and clears a latched event
(`rtc-macsmc-alarm.c`, "Firmware can leave the alarm armed or its event latched"). So since 2026-11-18 the
alarm's registers are no longer the run's by the time the record is read. For run B, whose alarm is part
of the result, the nights are switched off before the run and on again after the read; switching them on
also clears the event that run B leaves latched, which was the open point about a run after B. To build:
the alarm's four registers saved before the unit loads the driver, and a decoder that says when the driver
is bound.

**An SMC dump after the run** (`results/smc-dump-20261118T125830/`, three minutes into the boot), for the
diff against 2026-11-16's. Without the control, a dump after a plain restart, the 530 differing keys are
mostly sensors and the battery's; one that stood out, `CLWK`, a 64-bit count. Not read further yet.

**Three values that name this Mac were in the public repo.** The dump of 2026-11-16 went in with the
battery's serial (`BMSN`), the chip's ID (`RECI`) and an ID string (`zSEm`) in it, against the practice of
11-04, which had itself left `RECI` in three files. They are out of the tree now (`smc-keys redact`, and a
test that fails on a dump in `results/` that has one); they are still in the history from 8756cd9 and
0a8234b on. Corey, the same day, asked whether to rewrite `main` for it: "you can leave the device identifies, no
problem." So the history stays as it is.

### Upstream: the two questions posted, and two results elsewhere (2026-11-18)

**Posted**, with Corey's OK of 2026-11-18 ("you can post the upstream drafts but maybe check for news and
updates first"); the texts as posted are on this MacBook in `~/.local/share/sleep-drain/posted-20261118/`:
- omacom/linux-aurora#290, a comment (`#issuecomment-6977293933`): where the entry stands, and three
  questions: maralcbr's own ending on his J314s; macOS's power words at its commit on a T600x; whether
  linux-aurora means to carry deep sleep on t600x.
- aurora-silicon/linux#2198, an issue, not the Q&A discussion that was drafted: that category has no post
  at all, the discussions there are a hardware directory, and questions go in as issues (#1547, #2075).
  Four questions: macOS's power words at its commit on a T600x; whether anything about T600x keeps a loader
  from using the resume mailbox (#1769 keeps the mailbox in the shared part of `sleep.c`); whether an M1
  Pro or Max under aura has come back from a completed entry; whether T6000 data is wanted before an entry
  completes.
Against the drafts of 11-16: the question about macOS's commit state is new, after the two results below;
the firmware line is new and says both versions.

**Nothing new** on malik-na/linux-aurora#77 (no reply), omacom/linux-aurora#351 (no review, no `ci:build`)
and #454, JJRPF/Aurora-DeepIdle#9; maralcbr/linux was last pushed on 11-05.

**A T6050 with this Mac's symptom, solved** (aurora-silicon/linux#2075, axboe; a K116s, 16-inch M5 Pro, under
aura; the issue's text only was read, none of its branches). "The whole public entry now completes on this
board; the SoC then does not sleep": the commit word written, and a reset 5.56 s later, "the commit
watchdog, not a sleep". The cause, a day later: "iBoot leaves VENC0_SYS on (`0xf0003ff`). The K116s DT has
the video encoder's power states disabled [...] so nothing turned it off and FAB0_SOC stayed up behind it."
Found by running macOS as a guest on the same machine with its commit withheld, and a diff of the register
state at that point against Linux's before its commit word. Power words lowered by hand on the way (the
PMP's own, SIO's) were not it, and he withdrew them: "not needed and macOS does not lower them". His chip
and his loader, so a lead here and not a fact about T6000; what carries over is the kind of cause, a word
left on that holds a fabric up, and the method, which needs macOS's side.

**Naeem Malik's M1, runs 39 to 43** (`m1power/s2r-rs1-135`, to 6bd8eaf6ad of 11-17; commit messages only,
his words in quotes, T8103's values). "Linux run40 under the m1n1 hypervisor with real S2R restarts ~5 s
after the commit; macOS 13.5 through the same commit path wakes." So on his M1 the 13.5 firmware does
sleep, and the restart is Linux's state. "Both systems' state was then read by the same tracer when the
last core reached its commit (249 power-state, always-on and clock words [...]): 54 differ." Run 41 sets
those; run 42 puts the DRAM controllers "into the self-gating state macOS runs them in" ("The engine's
program reads that field": DCSn+0x7b0, 0 under macOS, 3 under Linux); run 43 makes the 130 writes in the
memory controller's space that macOS makes in the last 50 ms before its commit and Linux didn't. How run
43 ended isn't in the branch.

**What both say for here.** Two other chips had this ending, a reset a few seconds after a commit that the
entry reached, and both went on by a diff of the state at the commit against macOS's. Here the reset comes
at the watchdog's own time and B2 hasn't been shown past its first gate, so the place may be earlier than
theirs; the kind of cause is the same candidate. This Mac's side of such a diff can be read; macOS's can't
be had from here without a trace, which is why both posts ask for it.

A first look at this Mac's side, from the reading of 2026-11-04 (`results/sleep-peek-20261104T120619/
pmgr-awake-2.txt`, 206 power words by macOS's names) against the live device tree: 94 read on awake. Linux
has a node for every one of them but the seven CPU and cluster words, and none is disabled, so #2075's
exact case (a word on, its node disabled) isn't here. 61 of the 94 are marked `apple,always-on`, which
Linux never lowers: the 16 DCS and 4 AMCC words among them (auto flag set, actual f), the AFNC and AFI
fabric words, PMS, PMP, the ATC commons. Which of them B2 lowers itself, and which macOS has off before
its commit, is the diff that is still to make.

**A correction.** "This Mac's system firmware is 13.5 too" (the entry of 11-16 above) named the wrong
property. `/chosen` here has `asahi,system-fw-version` = 26.6.2 and `asahi,os-fw-version` = 13.5 (iBoot2
iBoot-8422.141.2): Linux boots with the 13.5 firmware set, coprocessor firmware included, under system
firmware 26.6.2. His "13.5 and its coprocessor firmware" reads as that set; which of the two wrote the
reconfig engine's sequences on this Mac isn't known.

### Before the feasibility run: what the desk says (2026-11-09)

Subagents' work, in parallel with the build; what each rests on is said. None of it is from m1n1 or Aura.

**The kernel update changed nothing this Mac runs.** aurora15 is omacom/linux-aurora b798f2b0 (aurora14:
4fdd0fc5): 12 commits, 11 files, in apple-dart's `of_xlate`, the DCP driver (a J313 panel's power cycle
and the 14.7 firmware's swap layout) and device trees for t8103 and t8122. Between the two packages:
`t6000-j314s.dtb` is byte for byte the same; so are the config, the headers, `Module.symvers` and the
kernel's BTF; one function differs in vmlinux (`apple_dart_of_xlate`) and one module has code changes
(appledrm), neither reachable here (no J314s consumer has `apple,dma-range`; this panel is a J314 on DCP
firmware 13.5). e1_hook, pmp_sleep_release and smc_peek build to the same code. So run 13 is read against
run 12 as on one kernel. The builds are stale all the same: the modules `install.sh` installs and
e1_hook.ko carry aurora14's version until rebuilt.

**The bite word, checked twice more.** Two more decodes of the dump, one from `recfg.h` alone and one
set on breaking the claim, agree on the place and the six words. In the 64 KiB: the old bite is at
exactly four places, the four A openings; nothing else writes WD2's bite or count; no B sequence touches
WD2 (B2's one watchdog op, late, zeroes WD0's and WD1's counts); nothing points at, overlaps or checksums
A2's fifth word. What a dump can't show: that the SRAM takes the store, that the engine fetches the word
when it runs A2, and what the other 6/7 of the SRAM holds.

**The locks (PongoOS's `recfg_soc.c` and `patches.S`, read).** On A9-A11 and T2 there are three
registers: the table's lock at AON + 0x2c0214, and the SRAM's range lock at AON + 0x200 (two 15-bit
fields, in 0x40-byte blocks) with its set bit at + 0x204. iBoot sets them; PongoOS takes iBoot's
`reconfig_lock` out and stops ("Recfg is already locked") rather than write a locked SRAM; XNU's published
source never touches them. On this Mac the table lock's analogue reads 0, and where the range lock's
analogue is isn't known (AON + 0x200 unshifted, or shifted with the table's block: neither is in a window
Linux knows). PongoOS edits the sequences in place and counts on the engine running the edited bytes, with
no checksum and no shadow copy in its code or in XNU's; that this holds on T6000 is inferred. A cold boot
through iBoot writes the sequences again; a warm wake runs the SRAM as it is. Whether this Mac's boot
path rewrites them every time is what `recfg-bite --after` measures.

**What runs table B: not found, and two facts that narrow it.**
- The SRAM has ten descriptor blocks, 0x100 apart: A's at 0x1000, B's at 0x1100, and eight more at
  0x1200-0x1900, whose addresses B0 writes, as address >> 8, to 0x28e099004 + 8n, 0x1000 above the
  soc-cluster words. All 37 pointer targets past the 64 KiB hang off those eight. So the walker is one
  of a family, each with a table register (inferred), and the eight look like the soc clusters' own
  (inferred): their sequences are in the part of the SRAM nobody has read.
- B's page, 0x2922c0000, is AON + 0x2c0000, where PongoOS has the classic engine's block (table register
  at + 0x200, lock at + 0x214). A's block is at + 0x5c0000. 0x2922c0200 and 0x2922c0214 have never
  been read.
- T8140's author (Diljit Singh, aurora-silicon/linux#1208) writes that a system sleep's engine "waits
  for every core to power down", that an awake core holds it until WD2 resets, and that a complex has a
  sequence "with its untimed wait", run right before the CPU complexes'.
- Judgment on B2's gate (0x2922c0044 bits 2 and 3): the AP's cores and complexes, about 55%; a wider
  rollup (coprocessors, fabric, clock requests), about 20%; an always-on party's answer (the SMC's, or
  the AOP's through 0x2925c0014, the one word of A's block its DART filter lets through), about 10%.

**Reads that would be worth an OK:** the SRAM past 64 KiB (what the eight blocks' sequences poll, which
is where a complex's untimed wait would be); 0x2922c0200 and 0x2922c0214; 0x2925c0014.
