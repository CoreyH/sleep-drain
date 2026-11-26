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
- `tests/peek_decoders_test.py` checks `pmgr-map` and `smc-keys` on synthetic input (and that no dump in
  `results/` still has the battery's serial, the chip's ID or `zSEm`: `smc-keys redact`), and `pmgr-map` on this
  Mac's ADT dump when it's there.
- `tests/safe_boot_hook_test.py` runs the safe boot's initramfs hook in mkinitcpio's busybox ash, in a
  bubblewrap sandbox with stub mount and blockdev, one failure per case.
- `tests/safe_boot_boot_test.py` runs `sleep-drain-safe boot`, `boot --no-sep` and `boot --sep-ro` with its fixed
  paths pointed into a scratch directory and GRUB's and mkinitcpio's tools replaced by stubs: the three entries it
  writes, each with its own id (the regular command line; the same with `apple_sep` added to the last
  `module_blacklist=`'s list; the same with `apple_sep.xart_writes=0 apple_sep.provision_keybag=0`), which one
  each command selects (`--no-sep` and `--sep-ro` together are a usage error), that a selection queued earlier outlasts a later command that fails,
  that a regeneration is made again after one that failed, that a candidate `grub.cfg` without the blacklist is
  refused before it replaces the live one, and the refusals (a regular line with `--`, after which nothing is the
  kernel's; one with any character outside the plain set, which GRUB may read another way than the script: a quote, a backslash (a line
  carried on to the next among them), a carriage return, `#`, `;` or one outside ASCII; a blacklist over the kernel's 1024 bytes; `--sep-ro` alone on a line whose own blacklist keeps `apple_sep` out; a third entry one byte over the
  kernel's line, though the other two fit; a regeneration that would drop either of the two words); a regular
  line that already sets `apple_sep`'s `xart_writes` or `provision_keybag`, in any spelling of the module's name
  or the parameter's, with a value or bare, once or several times, which stops no mode: the plain and the no-SEP
  entry inherit the line, and the third entry's line has those words left out and exactly one setting of each,
  the script's; and `status`
  in a fake safe boot: kept out only by the kernel's own list, and not verified when that doesn't read, whatever
  the command line asks for; and the enclave's store, read-only only if every device bound to `apple_sep`, at
  least one, has `diag/xart` reading exactly `disabled` and a newline (the driver's directory in sysfs is a
  directory the test fills: 27 cases, `enabled`, no driver, no device, no attribute, one that can't be read, an
  empty one, no newline, more after it, another case, two devices, and what the command line asks for said
  beside it and proving nothing). No boot is made. And `sepro-cmdline` (`sepro_cmdline_tests()`): run without
  root in a copy whose `need_root` is the real one; its line held against the third entry `boot --sep-ro` then
  writes, before and after that command, over the regular lines that set either parameter already; refused in
  `boot --sep-ro`'s own words, with nothing printed and nothing written, over the lines that command refuses (a
  blacklisted `apple_sep`, quotes, backslashes, control characters, a GRUB variable, `--`); the length counted
  with the kernel path it is given (the last byte that fits and one over, for three paths; a line that fits the
  regular kernel's entry and not a test kernel's); no regular entry to copy; no `grub.cfg`; a kernel path that
  isn't a plain absolute one.
- `tests/test_kernel_test.py` runs `test-kernel` as uid 0 in a bubblewrap user namespace with its `TK_*` paths in
  a scratch directory, a real `.tar.zst` package made there, the real `sleep-drain-safe` (a copy reading the
  scratch `grub.cfg`) for `sepro-cmdline`, and stubs for mkinitcpio, GRUB's tools, df, pacman and uname. `install`
  with the hook there: two entries in the one script, the safe one's line what `sepro-cmdline` says for this
  kernel's path, each with its own initramfs, the safe image built with `-A sleepdrain-safe`, the state file's
  `entries=`; `boot`, and `boot --safe-sepro` in both argument orders, each read back from GRUB's environment (a
  `grub-reboot` that fails, one that returns 0 and writes nothing, an environment that can't be read);
  `--safe-sepro` refused for a safe entry that lacks either word, has `=1`, a second setting, no
  `sleepdrain.safe`, a word after it, a `--`, a quote, another kernel or the plain initramfs, for a missing image
  or one without the hook, and for an entry made from an older regular line; `regen_grub`'s rule with two entries
  (a regeneration that changes the regular section, at install and at remove; one that would publish the safe
  entry without a word); `remove` (both entries, both images, a queued safe entry unselected); `list`. The plain
  entry alone, with the reason said, in thirteen cases (no hook, a line `sleep-drain-safe` refuses, a blacklisted
  driver, a `sleep-drain-safe` without the command or missing, one that prints a line that isn't a safe one in
  five ways, mkinitcpio failing for the safe image, an image without the hook, the regular line changing during
  the builds), and for each of them `install --safe-only` failing with nothing left behind: every file under the
  scratch boot partition, grub.d, modules and state compared before and after. `--safe-only` when it works: one
  entry, no plain entry or plain initramfs anywhere, `boot` without `--safe-sepro` refused; its failures on
  GRUB's side (three), again with nothing left. The space check with one and with two images counted (one MiB
  short and exactly enough, six cases), names ending in `-safe`, an existing module tree, a state file from
  before the safe entry, and every command without root. What isn't the install's own (`not_ours()`, Pullfrog on
  #136): a file in the image directory of that name with no record and a `grub-mkconfig` that fails, the
  reviewer's case, refused before anything is written, in both modes; one case per thing found beforehand (an
  image directory, an empty one, a file or a dangling link in its place, a GRUB script, a dangling link in its
  place, both), each said and left as it was; and each of the four things (the module tree, the record, the
  image directory, the script) appearing between the first checks and its creation, in both modes: the creation
  fails, what the run had created by then is taken back and named, and what appeared is still there with its
  contents. The snapshot compared is every directory, file and link under the scratch boot partition, grub.d,
  modules and state. `unselect`: one of the tool's entries selected (the plain one, the safe one, one of a
  kernel no longer installed) cleared and read back; another tool's entry (five ids, `sleep-drain-safe-sepro`
  and near-misses of the prefix among them) left alone and said; nothing selected; an unset that fails, one
  that returns 0 and clears nothing, an environment that can't be read.
- `tests/ref_loader_test.py` runs `ref-loader` with its `REFL_*` paths in a scratch directory (a directory for
  the boot partition, made-up m1n1.bin, U-Boot and device trees with real headers, a made-up
  `/etc/default/update-m1n1`), the root commands as uid 0 in a bubblewrap user namespace; findmnt, df, mount,
  umount, mountpoint, lsblk and findfs are stubs, and cp, mv and sync wrappers that log, run the real one, and
  fail or damage their output on request. The recipe: `build`'s bytes piece by piece (m1n1.bin, the device trees
  in the C locale's order under a language's locale or in a list's own order, one gzip member that unpacks to
  U-Boot with the file's name and time in its header, the configuration's accepted lines) and, where
  `/usr/bin/update-m1n1` is installed, that script itself run on the same inputs in a sandbox: the same bytes,
  and `verify-recipe` says match (it says so and skips if the script isn't there). `build`'s sixteen refusals
  (not a device tree by its magic or its size, none that names this Mac, an output in the live directory);
  `verify-recipe`'s mismatch for one bit in each kind of piece, with the piece named, and its refusals.
  `install`: refused without `--recovery-checked`, root or not, in words that name the write check from macOS;
  fourteen refusals with nothing written (a safe boot by the marker or by the command line, `/boot` not mounted or
  not FAT, no bundle or an empty one, a live file the recipe doesn't rebuild, the stock bundle, a bare m1n1.bin, a bundle cut short, with
  another U-Boot byte, without the configuration or without this Mac's device tree, one byte too little room);
  the install with exactly the room it needs, its twelve logged steps in order (each file written beside its
  name, fsync'd, renamed, the filesystem synced; boot.bin last), the two records, the way back printed;
  `status`; a second install over the bundle; `restore`, twice; the kept stock found without a record, with a
  record that isn't a hash, damaged, older than the recipe, or recorded and missing. Each write fault, at the
  stock's copy and at the bundle's (a copy that doesn't read back, an fsync or a rename that fails, a file that
  reads back wrong after its rename: the stock put back, or `BOOT.BIN IS NOT VERIFIED`), and the same for
  `restore`. The gate (`sync_gate()`, Pullfrog on #136): with every sync of the filesystem failing once boot.bin
  has been renamed over, the reviewer's fault, `install` ends in `DO NOT RESTART` with the file reading as the
  stock through the cache, and `verify-recipe`, `status`, `restore` and a second `install` each fail saying the
  partition's writeback isn't confirmed, with no match and no "the stock" said, until the sync succeeds, when
  `verify-recipe` gives a fresh match; a filesystem that can't be synced before anything was written; the kind
  of read said on both lines, and where no direct read is possible (a dd that fails) a match "read through the
  cache", exit 0, never called confirmed on the partition; `restore` not saying "already the stock" without the
  sync. A safe boot: `install` refused, `status` and `restore` mounting the boot partition of the disk's own
  fstab and unmounting it on every way out, the mount failing, the unmount failing. `recovery-notes`: the
  partition's identity, the scratch-file check, that the macOS steps weren't tried, what the flag stands for.
  `install.sh` installs it; `uninstall.sh` keeps it while a stock loader is kept.
- `tests/ref_sleep_test.py` runs `ref-sleep` as uid 0 in a bubblewrap user namespace with its `RS_*` paths in a
  scratch directory and stubs for dmesg, journalctl, rtcwake, `sleep-drain-safe` and cat (how the script reads
  sysfs: the stub answers for the kernel, so a written `deep` reads back `s2idle [deep]`, and logs each read with
  what enable, mem_sleep and state held then). `check`: the five findings and what `go` would do, nothing
  changed. `go`'s order from that log: 1 to enable before anything, deep after it, the save before any alarm,
  the alarm, the xART attribute read as the last thing before state holds mem, then the reading again and the
  second save, mem_sleep put back and the alarm switched off. Each precondition failing alone, for `check` and
  `go` (seven ways for the first four, fourteen states of the SEP driver's directory), all five at once, the
  mailbox line only in the journal, no root: nothing written. Each step of `go` failing (an enable or a deep that
  doesn't take, the save, rtcwake, the xART attribute changing after the alarm is armed, the write of mem, the
  proof gone or the save failing after the wake, mem_sleep not going back, a kernel log that can't be marked), no
  PMU RTC in three forms (said, and the attempt made), SECONDS out of range, `pm_test` set. One `go` at a time
  (`one_at_a_time()`, Pullfrog on #136): with the lock held by the test, `go` ends at once with nothing read or
  written while `check` still runs; every stub call of a `go` finds the lock held, from the first reading to the
  last line of the EXIT trap, also when it stops early; and a second `go` started while the first is held in the
  middle of its save ends at once and touches nothing, after which the first finishes with its own alarm armed
  and switched off once and mem_sleep back to s2idle. The write of mem
  returns at once there: no suspend is made, and what the reference kernel does with it isn't modelled.
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
  `--clear`. For acc_last=1 it compiles `acc_last.h`'s entries, code, byte and stage map against a second
  writing-out (every byte, every stage, the code's bounds and a tick either side of each), runs `m1_acl_pre()`,
  `m1_acl_write()` and `m1_acl_out()` with the module's own table, waits and `m1_post_val()` against a model that
  logs every read, write, clock read and PMU byte (30 pairings of which muxes read parked and how long the run
  took, and once with the PMU refusing the second send: five reads and a byte before any write, the clock, the
  three cluster words and nothing else, the clock, the byte, the acks), `m1_acl_init()`'s refusals, where
  `e1_hook.c` calls each, and the byte through m1-crumbs for every value at each stage. For last_look=1 and
  ll_wake=1 it compiles `last_look.h` against a second writing-out (the time's code at every edge, each byte's
  bits, the stage map), runs `m1_ll_ret()` through 105 returns against pages that log every access (the three
  commands in order, each before the next one's reads, the acks after each, 0x6008 again at the second and the
  hundredth return and at no other, nothing written but the acks and the timer's control), `m1_ll_blink()`
  with FPWM0 coming on and never coming on, `m1_ll_timer()`, and `m1_pmu_ok()` over every address and length
  with and without last_look=1; then where `e1_hook.c` calls each, the load's refusals, and the record through
  m1-crumbs: each field, records that stop short, both runs' stages, the restart's time against the marker at
  70 s, 0x0201, and `--clear`, which zeroes 0x6008-0x600e only after one of that run's stages. For gate_look=1
  it runs `m1_ll_look()` against the same pages (the wait on the counter, the two commands, nothing written
  but the acks) and `m1_ll_ret()` after a look (0x6008 alone, bit 3 kept), and the look's form of the record
  through m1-crumbs: the look with and without a return, K1's mark alone, records that stop short. For
  obs_gate=1 it runs `m1_og_look()` against the same pages and one more for the soc clusters' (the four
  power words, the two soc-cluster words and the gate word, the first command, only then the E complex's
  state word, the second, 500 ms, both state words, the third), `m1_pmu_ok()` with it, and CPU7's record through m1-crumbs under
  each of its stages, in both of its forms, with `--clear`. For atc_down=1 it checks `m1_atc[]` against the six words, runs
  `m1_atc_write()` against words that log every access (clean, and with each kind of word stuck on), the order
  around `m1_pw_write()`, and the decoder on its mark and on run B's own record with either mark. For blink=1: the load's refusals,
  `m1_blink_init()`'s checks and their order against `m1_ll_init()`'s, and its one place in step 8. For wake_all=1 it compiles
  `last_look.h`'s `wa_byte()` and `wa_ok()` against a second writing-out of them, and runs the module's own lines of steps 7
  and 8 (the masks' loop with `m1_wake_mask[]`, the enables' loop, the read's call; the record at 0x6010 and the byte after
  it), `m1_wa_read()`, `m1_crumb()` and `m1_wa_init()` as they stand against words that log every access and a stub for the
  raw SPMI command under `m1_crumb()`: the table's values without it, nothing read and no byte; 0xffffffff seven times with
  it, the seven read back and the seven status words in step 7, the byte after step 8's record and before the stage; the
  byte for every part that doesn't take (word 6 reading 0x00001fff back, its bit 12, 13 or 31 alone, a bit of words 0, 1 or
  5, both parts) and for one, two and seven status words set; a byte whose command isn't acked (the record is out, the
  stage after it isn't sent), a record whose command isn't acked, and raw SPMI off beforehand. (The stub stands for the
  PMU's answer; `m1_spmi_raw()` itself isn't run.) Then the two steps' order in the source, the one write to the masks, and
  K1 and the undo compiled behind the real `m1_pmu_ok()` in each kind of run over what a wake_all=1 run leaves at
  0x6008-0x600e: zeroed in every run, the mark written with wake_all=1 alone, no write refused; a family run without
  wake_all=1 after one with it then decodes with nothing of it (Pullfrog on #127). `m1_pmu_ok()` over every address and
  length with eight contents and, at 0x6008, every value of one byte: seven zeros in any run and nothing else there
  without last_look=1 or observe=1; with wake_all=1 one byte more, the mark or a byte with bits 7:6 set and bit 3 clear.
  The load's log (`m1_wa_init()` refuses nothing: `m1_init()` has returned by then if the page didn't map), the refusals,
  and the byte through m1-crumbs on the a2_pre=1 run with the light's own record: each field, under each stage of the
  gates_mark=1 family and under step 7's own; K1's mark under a stage the run sends after the byte, one it can be at before
  it, and fourteen other runs' stages, where it is not read; junk in the other six bytes, bit 3; never as wake_all=1's
  under one of last_look=1's stages or an observer's, obs_gate=1's mark and record as before; and `--clear`, which zeroes
  the seven only after a byte or a mark with six zeros behind it and bit 3 clear, under stage 0x00 and the probe's too.
  For sep_pg=1 it compiles `last_look.h`'s `sp_byte()`, `sp_ok()` and `sp_writes()` against a second writing-out (7,056
  cases: the word read again as found, less bit 2, with it, with another bit flipped; the SEP's actual state; every
  nibble of the gate word; and when step 7 writes at all: bit 2 set and the SEP's power word off), and runs the module's
  own lines of steps 7 and 8 with `m1_sp_clear()`, `m1_wa_read()`, `m1_crumb()` and `m1_pmu_ok()` as they stand, in the
  harness wake_all=1's tests use, in 28 runs: neither option (the two steps as they were, nothing of either), wake_all=1
  alone (as before), sep_pg=1 alone and both (wake_all=1's accesses first, then the control word read, the SEP's power
  word read, the control word written once and read again, 0x2922c0044; in step 8 the record, wake_all=1's byte,
  sep_pg=1's at 0x6009, the stage); a word with other bits set (written back as found); a word found clear (no write at
  all); **the SEP's power word reading powered, actual state f or 1, with the bit set, with other bits, clear, or
  clearing by itself, and with wake_all=1: no write to the control word anywhere in the log**; a bit that doesn't take
  the write; another bit that reads changed after it; a word found clear that reads otherwise the second time; five
  gate words; a record, a first byte or a second byte that isn't acked; raw SPMI off beforehand (the write is still
  made). Every access is logged, so each run shows exactly one write, to that word, of the value found less bit 2, or
  none. (What is mocked: the three words are words of the harness's own pages, and the raw SPMI command is a stub that
  asks the real `m1_pmu_ok()` first, as `m1_spmi_raw()` does, and stands for the PMU's answer.) Then the two steps'
  order in the source, the one write through the word's pointer in the file, the SEP's word read before the write it
  decides, and the MMIO-write tripwire (75 with this option; 80 since disp_hs=1; 11 in `m1_after_slpa()` itself).
  `m1_sp_init()` and `m1_never()` as they stand, against stand-ins for the never-write list and the mapping: bit 2 set
  or clear awake, the SEP's power word reading powered awake (each said in the log, and the load goes on), the word on
  the list, SIO's power page or the engine's missing, the word's page not mapping; and the module's own
  `e1_never_write()`, compiled by itself, says the word isn't on the list and the list's two still are. **The SEP's
  device**, for sep_pg=1 and disp_hs=1: `m1_sep_bound()`, `m1_sep_recheck()` and the last lines of `e1_prepare()` as they
  stand, against stand-ins for the device tree, the driver core and the two firmware stops (which only log): a driver
  on the first such node's device or the third's, no such node, no device; under each device's lock at the load and
  with no lock at prepare; at the first prepare with either option a bound driver calls the replay off (`replay_done`
  2, a line that says why) and neither the DCPs' shutdown nor SIO's stop is made; without either option, at a later
  prepare, or with the replay already off, nothing is looked at. (`e1_prepare()`'s head, its read of the SMC and the
  probe's branch, isn't run; that prepare_late then fails the suspend is pinned in the source, not run.) `m1_pmu_ok()`
  over every address from 0x5ff8 to 0x6027, every length, written and read, with fifteen contents, and one byte of every
  value at each of the seven, in the four combinations of wake_all=1 and sep_pg=1, against a second writing-out; the
  real `m1_pmu_start()` and `m1_pmu_undo()` behind it in seven kinds of run over six leftovers. And the record through
  m1-crumbs: every value of the byte against a second writing-out of its four lines (the write made, not made for a
  word found clear, or not made because the SEP's word read powered; the nibble as the last sample before the commit,
  bit 2 named); under each of the family's stages and step 7's own; both options' bytes together, each alone
  (wake_all=1's alone reads exactly as it did), one landed and the other's mark left; K1's mark under a later stage,
  the stages a run passes before its byte, and fourteen other runs' stages (not read, and the byte there not read
  either); anything a run can't have left elsewhere in the seven (no field of either byte read); last_look=1's,
  ll_retn=1's and an observer's stages (never read as sep_pg=1's); a family run without the option after one with it;
  and `--clear` in fourteen cases that zero the seven and fourteen that leave them.
  For disp_hs=1 it compiles `last_look.h`'s `dh_b0()` to `dh_b3()` and `dh_ok()` against a second writing-out (every
  first byte, every second, and all 65,536 pairs of third and fourth: 289 pass); holds the two set-up tables in
  `e1_hook.c` against the reference driver's own file, entry for entry, if it is where Corey keeps it
  (`~/.local/share/sleep-drain/ante042-20261123/s2r.c`; it says so and skips that check if not), and in any case against
  a sha256 of each as copied (65 entries each, which the build asserts), and finds the handshake's own four writes at
  entries 29, 41, 53 and 63 of each; and runs K3b as it stands, `m1_disp_stop()` with `m1_dh_down()`, `m1_dh_on()`,
  `m1_dh_setup()`, `m1_dh_shake()` and `m1_ps_off()`, against a model of the two DCPs' power words, the two fabric blocks
  and the gate word that logs every access, in eighteen runs with the option, each against a second writing-out of K3b
  written in the test, its whole log: both DCPs stopped and powered (per block, in this exact order: interrupts off,
  the FE word read, the table's 65 writes, interrupts on, the gate word read; the CPU0 word down; interrupts off, the
  FE word read again, the handshake's four writes, interrupts on; the FE word down; the gate word read; the gate word
  read first of all; the four bytes and then the stage last); the gate word's bit 2 flipping at either table, at either
  handshake, at a table and back at the handshake, or never (the record tells them apart); an FE word off as found (no
  write to that block at all, no sample after a table that wasn't written); an FE word that goes off with its CPU0
  word (the table and its sample, no handshake); a DCP that didn't stop, or neither; a CPU0 word or an FE word that
  stays up; the four bytes' command failing (the stage still goes out). Every write to a block lies inside an
  interrupts-off group that opens with the read of that block's FE word and holds nothing else. And in eight runs
  without the option, beside the function as it was before it, kept in the test word for word: the same accesses, one
  for one, and interrupts never off. (What is mocked: every word is the harness's own; a power word written with
  target 0 reads off afterwards unless told to stay up; a write to a block whose FE word doesn't read powered is logged
  as a fault, where the Mac would take an SError, and a read of a block as illegal; `local_irq_save()` and
  `local_irq_restore()` only log; `m1_kwrite()` is a stand-in that asks the real `m1_pmu_ok()`.) The MMIO-write tripwire
  stands at 80 in the file (the table's one write in its loop and the handshake's four) and 11 in `m1_after_slpa()`.
  `m1_dh_init()` and `m1_never()` as they stand: the 134 addresses asked of the list, in order; the refusals for a
  listed table entry or handshake word in either block, for the engine's page or an FE word's mapping missing, for a
  block that doesn't map; and, over a copy with one thing changed, for a block paired with another DCP's words or
  another ASC and for a table entry that isn't a word inside the span; no word of either block is read. `m1_pmu_ok()`
  over every address, length and twenty-five contents, one byte of every value at each of the seven, and four bytes at
  each place with every value of each in turn, in the eight combinations of wake_all=1, sep_pg=1 and disp_hs=1,
  against a second writing-out; the real K1 and undo behind it in eleven kinds of run over six leftovers. And the
  record through m1-crumbs: 64 first bytes by six second ones by the 323 third and fourth bytes a record can have (a
  fourth of 0x00, lost across the restart, among them), every second byte, and sep_pg=1's nibble as a sixth sample,
  through the decoder's own function against a second writing-out: the handshake flags and the set-up flags said
  separately, the samples in the order they were taken, the first with bit 2 set named, and no line that says which
  write set it or that it stayed set; the four bytes under each stage a run can have them at, K3's with its own line;
  all three options' records together and in pairs; K1's mark under each kind of stage (under K3's: no watchdog, no
  light yet); other runs' stages, K1's stage and none (not read); anything a run can't have left in any of the other
  fields, and four bytes that aren't a record's (no field of any record read); a family run without the option after
  one with it; and `--clear` in fourteen cases that zero the seven and eighteen that leave them.
  For sep_ro=1 and pw_noread=1 (`sep_ro_tests()`) it compiles `last_look.h`'s `sro_byte()`, `sro_ok()` and
  `sro_xart_ro()` against a second writing-out (the comparison of what the SEP device's `diag/xart` says, in
  eighteen cases: `disabled` and a newline with the string's end after it, and nothing else: not `enabled`,
  nothing, the word without its newline, the word with something after it, counted in what the show returned or
  not, something longer, a show that failed, another case, a space, a count one short); runs steps 7 and 8 in the
  harness sep_pg=1's tests use, with the real `m1_sp_clear()`, `m1_crumb()` and `m1_pmu_ok()`, in seventeen runs
  (the gate word read before the decision and after the read-back, 0x6008's byte sent after the record and after
  0x6009's; a gate word that moves with the write, the other way, or by itself with no write; **the SEP's word
  reading powered: no write and no wait**; a bit found clear; a write that doesn't take; each command not acked);
  and pins that `m1_sp_clear()` has no loop, no clock and no wait. `m1_sro_xart()`, `m1_sro_proof()`,
  `m1_sro_wait()`, `m1_sep_recheck()` and the last lines of `e1_prepare()` as they stand, over stand-ins for the
  device tree, the driver core, kernfs, the device's lock, the page allocator and the clock: one bound device in 27
  states of its own corner of sysfs, with the lock waited for and only tried, each against a second writing-out of
  every call (the lock, the two nodes found with a reference each, the zeroed page, the device kobject's show
  called with the node's attribute under the lock, everything undone on every way out: no reference, page or lock
  left in any run); no proof for anything but `disabled` and a newline with the string's end after it, for a
  directory that is a file, another kobject's or a kobject of its own, an `xart` that is a directory, of another
  size or without an attribute, a binary attribute's node of exactly one page or a text one's without kernfs's
  `KERNFS_HAS_SEQ_SHOW` (each refused with no page had and no show called, though the mock would have said
  `disabled`), an owner with no type, operations or show, no node, no page; no driver, a driver
  of six other names or none; three devices in eight combinations; a lock somebody holds at the prepare (no proof,
  nothing asked, nothing dropped); the wait's sleeps counted (off at once, after 30 ms, at 4.99 s, at 5 s, never); and the
  prepare: with the proof and the word off both firmware stops are made though a driver is bound; without the
  proof, or with the word still powered after 5 s, the replay is called off and neither stop is made; without
  sep_ro=1 nothing has changed. (What is mocked: the nodes, kobjects, locks and pages, which log; the attribute says what it is told to, by a letter per device; the
  clock moves only when the code sleeps; the SEP's power word reads powered until a time given.) `m1_sp_init()`
  with the wait (off at once, after 250 ms, at 5 s, never: the refusal, with nothing else read), and the load's
  order in the source. For pw_noread=1: `m1_pw_write()` as it stands over one word per entry of `m1_pw[]`, in
  nine runs against a second writing-out (the same twenty writes and `dsb`s with or without it; no read of the
  fabric group's four with it, the other sixteen polled as before; the group's bit always clear), and
  `m1_gates_read()` with and without a place for the lane: step 8's seven reads, six with pw_noread=1; and
  `m1_pwn_reader()` in all sixteen combinations of sio_down=1, pcie_down=1, last_look=1 and probe=1, the load's
  refusal on it pinned, with the set of the file's functions that touch those words (seventeen): a new one fails
  the check until the rule knows it.
  `m1_pmu_ok()` in the eight combinations of wake_all=1, sep_pg=1 and sep_ro=1, the real K1 and undo in five kinds
  of run over five leftovers. And through m1-crumbs: every value of sep_pg=1's byte with every value of
  sep_ro=1's, with K1's mark and without, through the decoder's own function against a second writing-out (the
  two samples side by side only when both landed; "bit 2 clear before the write and set after it" in 64 of the
  2,048 pairs, exactly those with the write made and read back; a clear skipped or not read back said to be no
  test of the wait bit); the run's own record under `gates_mark=1`'s stage; K1's marks under eleven stages; a
  stage no such run sends; one byte landed and the other's mark left (the mark at 0x6009 with 0x6008's byte
  landed: acked and lost, but only if sep_ro=1's own reading takes 0x6008's byte: the reviewer's record, 88 02 00 05
  under stage 0x39, where it is NOT READ and proves nothing); both under step 7's own stage, each with a line that
  says 0x39 is the last stage the record kept: with the outcomes' marker at 0x6013, that the next stage's command
  was acked and the stage not kept (0x6010-0x6013 are then read as the outcomes and not as a second), but only
  for a run that 0x6008-0x600e show to be one that sends outcomes (`later_landed()`: ten records of the four
  options' bytes and marks read whole, eleven that show nothing, the rules and K1's zeroing it rests on pinned
  in `e1_hook.c`); otherwise AMBIGUOUS and read as no recipe's (`step7_ambiguous()`): run 7's archived record,
  a position=1 run's whose sample is `14 dd`, with only the stage changed to 0xb9 or 0x39 (no ps_words=1
  outcomes, no sample, nothing acked), the plain hold path's masks there, a gates_mark=1 run without the four
  options, and that archived record as it is, still decoded line for line; with a second there, that the record doesn't say why no newer stage is
  there; a mark under an earlier stage said the same way, in wake_all=1's, sep_pg=1's and disp_hs=1's tests too;
  the gate byte's line for a later recipe's record, the reviewer's among them
  (stage 0xd5, 0x6013 0x9f, 0x6016 0x81, 0x6008 0x88, 0x6009 0xfc: no word of a2_pre=1), and for `--m1-gates`'
  own; what a run can't have left; last_look=1's
  and an observer's stages; `--clear` in eleven cases that zero the seven and eight that leave them; every value
  of 0x6013 ("not read (pw_noread=1)" for 0x80 to 0x9f, never "read back as written"); and every gate byte under
  four markers (the lane "not read" by 0x6013's mark, not by its own bit).
  For ll_retn=1 it compiles `last_look.h`'s `llr_empty_code()`, `llr_b0()`, `llr_past()`, `llr_b2()`, `llr_b3()`, `llr_b4()`,
  `llr_wide()` and `ll_stage()`'s three sets against a second writing-out (every bit of each word alone for `llr_wide()`, the
  time bound to the tick, the empties' code at each edge), and runs `m1_deep_wfi()`, `m1_llr_empty()`,
  `m1_llr_look()`, `m1_ll_ret()` and `m1_llr_init()` as they stand against the pages that log every access, a logged
  ISR_EL1 and timer control, and a logged variable for CYC_OVRD. What is mocked: `m1_deep_wfi()`'s three inline-asm
  statements are replaced in its text by exact match (its read of CYC_OVRD, its two writes with their isb, the other
  cores' dsb-isb-wfi), `m1_ll_wfi()` is a stub, and each WFI returns by a script, which moves the clock too; so the C
  around the instructions is what runs, not the instructions. Thirty-one scripts against a model written from the
  brief: the timer at once; 1, 9, 10, 999, 1000 and 20000 empty returns and then the timer; empty returns a second
  apart until the time bound, 30 s after CPU0's first WFI (which the script puts half a second after the commit); a
  tick short of it, and at it to the tick; a WFI that holds for 31 s and returns once with nothing pending (the look's
  own return isn't among the empty ones: none, with the bound's flag), and nine empty returns before such a one (nine,
  not ten); the timer firing with ISR_EL1 reading 0, at once, after three empty returns and 27 s in (the look there and
  then, the timer firing and no FIQ in 0x6009, no bound flag); the timer 26 s in; never; a second return after the look, pending or not;
  103 after it; returns after ten empty ones and after a look at the bound (0x6008's bits 3:2 kept); an IRQ and an
  SError; a last_look=1 run without it; the other cores with and without a mark. They pin CYC_OVRD's two values
  (0x3000000 only for CPU0, only with ll_retn=1 and only until the look; 0x3000001 written again right after it, and
  for every later WFI), an empty return reading ISR_EL1 and the timer's control and nothing else, however many there are, the empties' code,
  the bound's flag, the look's order (0x6008 before any MMIO read, the two new reads last in
  the second command, the power words after it), each word that sets "wide", raw SPMI already failed, and that nothing
  is written but the acks, the timer's control and CYC_OVRD. Then the source (where bit 0 is cleared and set, the one
  caller of each function, the refusals, the load line), `m1_llr_init()` with the page mapped, not mapped, failing to
  map and with either other page missing, the one unmap and the load's failure paths; K1 and the undo over an ll_retn=1
  record in each kind of run and `m1_pmu_ok()` for the look's three commands (all three are `last_look=1`'s, and none
  names ll_retn); and the record through m1-crumbs under its three stages: every value of each of the first five
  bytes (0x6008's bits 3:2 as the empties' code there, and as gate_look=1's two bits under last_look=1's and ll_wake=1's
  stages, as before; the empties' code named as a count and no more, with the harness's own record of a thousand
  empty returns and then 24 s until the timer decoded to show that nothing concludes from it how long the WFI held; a
  count past one with last_look=1's caveat that a faulted read of the look's can cause it), the third command with and without its marks, all 0, the stage before the commit, the pending stage, the
  alarm at 20 s, the light and the marker at 70 s, `--clear`, the same bytes under last_look=1's, ll_wake=1's,
  --m1-mark's, the gates_mark=1 family's and an observer's stages (never read in this form), and the other runs'
  records and marks under these (never read in theirs).
  For a2_pre=1 it compiles `a2_pre.h`'s words, values, waits, stage map and byte against a second
  writing-out of A2's ops 11 to 16 (every pairing of each word's two bits at every count of moves made, and 400
  drawn cases), and runs `m1_a2_pre()` with the module's own table of waits against a model of the p-state page
  and the muxes' page in 29 scenarios (a write dropped, a word that settles late or never or keeps bit 31, DCS put
  back after its send, a mux unparked or pending) and once with the PMU refusing the byte from its third send on:
  the five muxes read and a byte before any write, three writes in A2's order,
  each a whole word, a byte after each with every earlier word read anew, nothing written back and nothing written
  to the muxes' page; the load's refusals (a word on the never-write list, the three out of order or with other
  values, mux rows that aren't the five the table parks); the byte through m1-crumbs for every value at each stage;
  and `--clear`. For sio_down=1 it compiles `sio_down.h`'s list, stage map and byte against a second writing-out
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
- `tests/off_after_test.py` runs the `sleep-drain-off-after` hook against a fake charger, lid and alarm RTC,
  with stubs for the power-off, the sleep between checks and what starts its `finish` step: when it arms and
  when it doesn't (no flag, safe boot, charger, no alarm RTC, an alarm that isn't ours), the minutes, the
  deadline kept through early wakes, every case that powers off and every case that must not, the re-check,
  a power-off that logind refuses, and the real `sleep-drain-nights off` run in the middle of the re-check
  and of the refused requests (no power-off asked for after it, no state written again). It also checks
  `sleep-drain-alarm.service`'s conditions against the safe boot's initramfs hook, `install.sh` and
  `uninstall.sh` for the new files, runs `install.sh`'s two functions that start the unit after a rebuild,
  and runs `sleep-drain-nights` with stub `sudo` and `systemctl`, also as uid 0 in a bubblewrap user
  namespace with only the stubs on PATH, as pkexec starts it.
- `tests/e1a_launcher_test.py` runs `e1-hook/run-e1a` and every `run-floor` option as uid 0 in a bubblewrap
  user namespace against stub `insmod`, `rmmod`, `modinfo` and `sleep-drain-reading`: the parameters and the
  label each option loads with (`--m1-wake-all`: `--m1-a2-pre-blink`'s line and `wake_all=1`; `--m1-ll-retn`:
  `--m1-atc-down`'s line and `ll_retn=1`; `--m1-sep-pg`: `--m1-a2-pre-blink`'s line and `sep_pg=1`; `--m1-disp-hs`:
  `--m1-sep-pg`'s line and `disp_hs=1`; `--m1-sep-ro`: `--m1-disp-hs`' line without `a2_pre=1`, with `sep_ro=1
  pw_noread=1`), the hand-off
  and the watch after it, every refusal (an older build without the option's parameter, a build that
  ignored it, two options together; `--m1-sep-pg` and `--m1-disp-hs` with `apple_sep` loaded, a directory beside
  the fake `e1_hook`'s, which every other option runs past; and those two against the kernel's `module_blacklist` parameter, a file the
  test points the launcher at: taken only if it lists `apple_sep` as a whole name,
  eleven values in all, unset, other modules' names, quoting, an empty value and no file among them, which every other option runs past too;
  and `--m1-sep-ro`'s own three conditions, each before anything is loaded or written: `apple_sep` not loaded; then the SEP driver's
  directory in sysfs, another directory the test fills, in eighteen states: one bound device or two whose `diag/xart` says
  `disabled` (the run goes on), and `enabled`, no driver's directory, no bound device, the attribute only under the driver's link
  to its module, no attribute, one that can't be read, an empty one, no newline, more after it, another case, and one of two
  devices saying otherwise (refused, with what was read); the kernel's module blacklist isn't asked by that option), and every way out. A launcher that ends before the hand-off, where a check needs
  it running, fails a check that shows what it printed, and that function's other checks are skipped: it doesn't stop
  the test. It takes about half a minute a launcher; options as
  arguments run only those launchers (`run-floor` by itself is the plain floor, which has the usage cases).
- `tests/mutants.py` isn't a test of the code but of the tests: it breaks `e1_hook.c`, its headers,
  `m1-crumbs`, `e1-hook/probe` and `e1-hook/run-floor` one way at a time in a copy (a wrong value, a write that
  isn't gated, a check dropped, a bit read wrong) and says which of those `tests/m1_crumbs_test.py` (for the
  launchers, `tests/e1_hook_probe_test.py` and, for the launchers its `ARGS` names, `tests/e1a_launcher_test.py`)
  didn't notice. Run it after changing any of them, and give it the
  mutants a new rung calls for. A word as its argument runs only the mutants whose names have it
  (wake_all=1's are `"wa:" "wa "`: `wa` by itself is in ninety other names; ll_retn=1's are `llr`; sep_pg=1's are
  `"sp:" "sp header" "sp decoder" "sp launcher"`, disp_hs=1's `"dh:" "dh header" "dh decoder" "dh launcher"`, and
  sep_ro=1's and pw_noread=1's all begin `sro`). Its
  `ARGS` names eight launchers for a mutant of `run-floor` (the plain floor, `--m1-a2-pre-blink`, `--m1-wake-all`,
  `--m1-atc-down`, `--m1-ll-retn`, `--m1-sep-pg`, `--m1-disp-hs` and `--m1-sep-ro`): about four minutes each.
  Either way it first checks the whole list against the tree, in a second, and stops if a mutant's text
  isn't there exactly once: a rung that moved an older mutant's line fixes the list before anything runs.
