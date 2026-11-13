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
  two functions that make them against a page that logs every write. For gates_mark=1 it compiles
  `gates_mark.h`'s byte and stage map against an independent reading, and runs `m1_gates_read()` and
  `m1_gates_init()` as they stand against pages that say what was read, with no `writel()` to call: the seven
  words in order, and the load refused, nothing read, when a page isn't mapped. It pins step 8's order (the
  stage, the outcomes, the reads, dsb and isb, the byte), the alarm's last command carrying the byte, and the
  load's refusals; reads the byte at 0x4b, 0x54 and 0x55 for every value, never where the record doesn't say
  it's there, the time against the marker as at fabric_up=1's stages; and `--clear` never takes it for the
  alarm's result. For pcie_down=1 it compiles `pcie_down.h`'s list, stage map and byte against a second
  writing-out of them (1,600 cases), runs `m1_pcie_down()` as it stands against a model of the power page and the
  gate page in 28 scenarios (bit 11 kept, kept for 30 ms or for 130 ms, the word staying up or dropping late, found busy or found
  gated, a storage word busy or up, a parent staying up, the gate closing or not): five writes, each a 0, in B2's
  order, the byte after the third and after the fifth, nothing written back, `ps_pmp`'s word untouched; the load's
  refusals; the byte through m1-crumbs for every value at each stage; and `--clear`. For sio_steps=1 it compiles
  `sio_steps.h`'s constants, stage map and both layouts of the byte against a second writing-out of them, runs
  `m1_sio_steps()` as it stands against a model of its six pages in 26 scenarios (the gate closing with GPIO's
  cycle, with the 22, before anything, or never; GPIO not coming up or keeping bit 11; a word of the subtree busy or
  up; a word of the 22 not taking; a word not coming back, or only after 50 ms; a gate that stays closed): every
  write in B2's order and no other, GPIO's 0 right after the 38th, UART_N never polled, a byte after each group,
  the 22 only after a switch-off that took with the gate open, the 43 back in reverse, `ps_pmp`'s word untouched;
  the load's refusals (a page that isn't there, an address on the never-write list, a list out of B2's order or
  short); the byte through m1-crumbs for every value at each stage, with SIO's firmware stopped and not; and
  `--clear`. For sio_down=1 it compiles `sio_down.h`'s list, stage map and byte against a second writing-out
  of B2's order and of the byte's two layouts (each place alone and 400 drawn cases), and runs `m1_sio_down()` with
  its poll and write-back functions as they stand against a model of SIO's page and the gate pages in 28 scenarios
  (the gate closing at once, late, never, or staying closed; closed before, when nothing may be written; a poll never
  met; a word that stays on; bits 27:24 reading f in the five words that have the field, kept elsewhere, or an
  auto-enable kept; a word that doesn't come back, in its auto fields or only in its actual state; UART_N busy): the
  43 read as found, 43
  writes of 0 in B2's order and groups with each group's polls after its writes and none of UART_N, the two gates
  polled in order, the byte before any write-back, 43 write-backs in reverse with the value found less bits 11, 9
  and 8, each waited for, the byte again, and no other write. It runs `m1_siod_init()` against a list with a word twice, outside
  the subtree or off its place, groups that don't add up, an unmapped page and a word on the never-write list;
  pins step 8's order and the parameter's guards; reads the byte at 0x3d, 0x3e and 0x3f for every value; and
  `--clear` never takes it for the alarm's result. For probe=1 it compiles
  `asc_probe.h`'s bytes and `m1_probe_word()` against a scripted page, runs `m1_probe_prepare()` and
  `m1_probe_late()` as they stand against a model of the registers in sixteen scenarios (no word read
  with its domain off, every step marked before it's taken, ANS2 left as found, `ps_pmp` never
  written, a read that wasn't made never called clear), pins the guards that keep the replay from
  running and what the load checks, and decodes stage 0x0e. For look=1 it runs `m1_look()` and
  `m1_look_init()` as they stand against pages that say what was read, with no `writel()` for them to
  call: the 120 words of PLAN.md's list and no other (the 22 B2 writes first last, a mark to a page), each group after its mark, each logged under its
  address, and the load's start-up from the pointers a load can find (one not yet mapped, a mapping
  that fails). For gate=1 it runs `m1_gate_test()` as it stands against a model of the six power words,
  the gate and the PMU's bytes in twenty scenarios: twelve writes and no other in a test that's made and
  none in one that isn't (SIO on, a UART in use), the write-back always made with each word's own found
  value, each step after its mark, the lane's three reads in the PMU with their mark before anything is
  put back, and an earlier test's bytes never read as this one's, whether or not the PMU acks; the load's
  never-write check; the decoder's verdict for every byte, each bit read in its own line; and `--clear`
  with either of its writes failing, which leaves the stage. For pre_test=1 it compiles `pre_test.h`
  against a second writing-out, and runs `m1_pre_test()` as it stands against a model of the 22 words in
  21 scenarios (bit 30 clearing at once, late or never; a word not taking, slow, going back, or reading
  with more bits; words not coming back; PMU writes not acked; old bytes left): 44 writes and no other,
  or none if the PMU doesn't take the zeroing of 0x6005 or the first write's mark, B2's order
  out and B1's back, each step after its mark, the six bytes in the PMU before anything is put back, the
  log's rows as the model has them; the load's never-write check and its read of the two + 0 words; and
  the decoder's reading of every case.
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
