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
  `asc_probe.h`'s bytes and `m1_probe_word()` against a scripted page, runs `m1_probe_prepare()` and
  `m1_probe_late()` as they stand against a model of the registers in sixteen scenarios (no word read
  with its domain off, every step marked before it's taken, ANS2 left as found, `ps_pmp` never
  written, a read that wasn't made never called clear), pins the guards that keep the replay from
  running and what the load checks, and decodes stage 0x0e. For look=1 it runs `m1_look()` and
  `m1_look_init()` as they stand against pages that say what was read, with no `writel()` for them to
  call: the 98 words of PLAN.md's list and no other, each group after its mark, each logged under its
  address, and the load's start-up from the pointers a load can find (one not yet mapped, a mapping
  that fails). For gate=1 it runs `m1_gate_test()` as it stands against a model of the six power words
  and the gate in fifteen scenarios: twelve writes and no other in a test that's made and none in one
  that isn't (SIO on, a UART in use), the write-back always made with each word's own found value, each
  step after its mark, the lane's three reads in the PMU before anything is put back; the load's
  never-write check; and the decoder's verdict for every byte, each bit read in its own line.
- `tests/e1_hook_probe_test.py` runs `e1-hook/probe` as uid 0 in a bubblewrap user namespace against stub
  `insmod`, `rmmod`, `modinfo`, `dmesg`, `sleep`, `m1-crumbs` and `sleep-drain-safe`: the parameters it
  loads, that a module which doesn't read `probe=Y` has its replay called off and is unloaded with no
  suspend ever asked for, when it asks again and when it doesn't, `--look` (no PMP release, and no
  suspend unless the module reads `look=Y`; the load's look saved before the suspend), that no file of
  the log directory changes between two saves, and every refusal. The suspend file and the module's count the launcher goes by are FIFOs with a thread at
  their other ends, so that what the launcher reads depends on how many suspends it has asked for, as
  on the Mac.
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
