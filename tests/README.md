# tests: hardware-free checks

Each runs as `python3 -I -B tests/<name>.py` and prints one line per check. What each one covers (the list that
used to sit in CLAUDE.md, entry for entry):

- `python3 -I tests/pmu_alarm_test.py` runs `pmu-alarm`'s real test against a fake PMU.
- `tests/rtc_alarm_load_test.py` runs `rtc-alarm-test/load` as root in a bubblewrap sandbox against a fake
  /sys, with one failure injected per case.
- `tests/rtc_alarm_check_test.py` runs `rtc-alarm-test/check` the same way against a stub rtcwake.
- `tests/e1_hook_check_test.py` runs `e1-hook/check` the same way against a fake kernel that plays both runs,
  and `tests/e1_hook_verdict_test.py` runs `e1-hook/verdict` on made-up captures, one fault per case.
- `tests/sleep_drain_valid_test.py` runs `sleep-drain-log`'s resume summary (drain and validity) on recorded
  snapshots.
- `tests/peek_decoders_test.py` checks `pmgr-map` and `smc-keys` on synthetic input, and `pmgr-map` on this
  Mac's ADT dump when it's there.
- `tests/safe_boot_hook_test.py` runs the safe boot's initramfs hook in mkinitcpio's busybox ash, in a
  bubblewrap sandbox with stub mount and blockdev, one failure per case.
- `tests/safe_boot_save_test.py` runs `sleep-drain-safe save` in a fake safe boot and `collect` in a fake next
  boot.
- `tests/m1_crumbs_test.py` runs `e1-hook/m1-crumbs` on a fake PMU nvmem file, position=1's samples made by
  compiling `e1-hook/m1_position.h` itself, cluster_ack=1's three values and its waits by compiling
  `e1_hook.c`'s own table and wait functions, and fabric_up=1's and wd1_mark=1's writes by compiling the
  two functions that make them against a page that logs every write. For probe=1 it compiles
  `asc_probe.h`'s bytes and `m1_probe_word()` against a scripted page, pins the guards that keep the
  replay from running and the probe's six MMIO writes, and decodes stage 0x0e.
- `tests/e1_hook_probe_test.py` runs `e1-hook/probe` as uid 0 in a bubblewrap user namespace against stub
  `insmod`, `rmmod`, `modinfo`, `dmesg`, `sleep`, `m1-crumbs` and `sleep-drain-safe`: the parameters it
  loads, that a module which doesn't read `probe=Y` is unloaded with no suspend ever asked for, when it
  asks again and when it doesn't, and every refusal.
- `tests/recfg_peek_test.py` runs `e1-hook/recfg_peek` on a sparse file standing in for /dev/mem and its
  launcher with stubs.
- `tests/recfg_bite_test.py` runs `e1-hook/recfg_poke` on the same kind of file (exactly one word changes, or
  none; the store is 64 bits) and `e1-hook/recfg-bite` with stubs.
- `tests/wd2_bite_test.py` runs `e1-hook/wd2_poke` that way too (one word changes, or none; its store is 32
  bits) and `e1-hook/wd2-bite` with stubs.
- `tests/sleep_drain_reading_test.py` runs `sleep-drain-reading` as uid 0 in a bubblewrap user namespace
  against a fake battery, with stub `sleep-drain-safe` and `systemctl`.
- `tests/mutants.py` isn't a test of the code but of the tests: it breaks `e1_hook.c`, its headers,
  `m1-crumbs` and `e1-hook/probe` one way at a time in a copy (a wrong value, a write that isn't gated, a
  check dropped, a bit read wrong) and says which of those `tests/m1_crumbs_test.py` (for the launcher,
  `tests/e1_hook_probe_test.py`) didn't notice. Run it after changing any of them, and give it the
  mutants a new rung calls for. A word as its argument runs only the mutants whose names have it.
