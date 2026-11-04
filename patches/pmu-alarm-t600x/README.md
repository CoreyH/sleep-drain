# PMU RTC alarm on T600x: the patch series (PLAN.md, B2)

These five patches turn `rtc-alarm-test/` into changes to omacom/linux-aurora.
They're against `integration/aurora` at 396f2d63 (each patch has a
`base-commit:` line). They're posted as
[omacom/linux-aurora#351](https://github.com/omacom/linux-aurora/pull/351)
(B3), and these files are what was posted.

1. `spmi: apple:` T6000's SPMI controller numbers its own events (notify
   first) after 192 peripheral interrupts, not 256.
2. `dt-bindings: rtc: apple,smc-rtc:` allow a fifth cell, `rtc_alarm_event`.
3. `rtc: macsmc:` clear a Maverick PMU's alarm event through that cell.
4. `rtc: macsmc:` arm the alarm a second later when the tick offset's
   fraction is under 1/32 s (B1's fix).
5. `arm64: dts: apple: t600x:` describe nub-spmi0's interrupt, the alarm's
   cells and the RTC's interrupt and wakeup-source.

## What was checked, and how

- **Hardware.** Every change has run on J314s (aurora14) as the test modules
  and the live device-tree change in `rtc-alarm-test/`. Patch 5 describes
  exactly what `pmu-alarm-dt.ko` adds to the live tree: the compiled J314s
  DTB matches it property for property. Results are in RESEARCH.md ("The PMU's
  RTC wake alarm" and B1).
- **Applies.** `git am` applies the series cleanly to 396f2d63.
- **Builds.** Patches 1, 3 and 4 build warning-free with `W=1` (LLVM) as
  modules against this kernel's headers.
- **DTBs.** All six T600x board DTBs compile (j314s, j316s, j314c, j316c,
  j375c, j375d), with no new dtc warnings. The upstream dtc's float cells
  were neutralised to integers for the test, since the system dtc lacks
  them.
- **Schemas.** dt-schema 2026.9: `dt-doc-validate` passes the binding, and
  `dt-validate` finds nothing on the J314s RTC and SPMI nodes. A swapped
  cell order is rejected, which shows the check runs.
- **checkpatch.** `checkpatch.pl --strict`: clean.
- **Sign-off.** Each patch carries `Assisted-by` (Documentation/process/
  coding-assistants.rst) and Corey's `Signed-off-by`, which only the human
  submitter can give.

## Still to do

A boot of a kernel built from the series (PLAN.md, B2). It can't boot patch 5's
tree yet: m1n1's boot.bin serves the pacman-owned linux-aurora's trees to every
boot, and the regular kernel loses the PMU with patch 5's tree until it has
patch 1. So the CI kernel's own drivers (patches 1, 3 and 4) run with patch 5
applied live by `pmu-alarm-dt.ko`.
