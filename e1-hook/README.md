# e1-hook: the one-way sleep-entry experiments

What each parameter of `e1_hook.c`, each `run-floor` option and each tool here does. It's the reference that
used to sit in CLAUDE.md, sentence for sentence; PLAN.md says what's next and RESEARCH.md what each run found.

## The module

E1's module (PLAN.md, A5). `e1_hook.c` is an out-of-tree module (`make -C e1-hook`, against the running
kernel's headers) that registers its own s2idle ops, so that its `prepare_late` runs after the whole noirq
pass. `s2idle_set_ops` isn't exported, so it finds it through a kprobe that's registered disabled and never
armed: lab only, never for upstream. With `replay=0`, the default, it only logs each op and reads one SMC key,
`B0AV`, at `prepare` and `prepare_late`; read-only parameters count the ops and keep the reads. `pkexec
e1-hook/check`, in a safe boot only, runs that under `pm_test=platform` and in a 60 s alarm sleep, with
ftrace's power events on the module's clock; `e1-hook/verdict` says whether `prepare_late` ran after the last
noirq callback and whether the SMC answered there. It saves `/var/log/sleep-drain/e1-hook-<time>/` with
`sleep-drain-safe save`.

## The replay's rungs

Each builds on the one before it, and `run-floor`'s options add them in this order.

- `replay` is maralcbr's one-way replay, ported from their `lab_s2r_run`, at the first `prepare_late`: `5` is
  E1a, their keys and PMP words (`e1-hook/run-e1a`); `15` is the floor, adding their 74 post-`slpa` writes and
  every CPU powered down for good (`e1-hook/run-floor`: the Mac never wakes from it; its header says how to
  end it with the power key).
- `quiet=1` (`run-floor --quiet`, with `15` only) leaves out maralcbr's 500 ms and its two AFNC reads after
  the post-`slpa` writes, so the S2R-timer commit is the replay's last MMIO access before the CPUs power down.
- `m1=1` (`run-floor --m1`, with `15` and `quiet=1` only) is E2's M1 recipe, Naeem Malik's T8103 entry mapped
  onto T6000: the suspend pinned to CPU0, the other CPUs parked awake before the post-`slpa` writes and
  stopped one by one before the commit, interrupts masked, the AP wake mask, boot stage 0x50 and breadcrumbs
  in the PMU's spare scratch bytes; `e1-hook/m1-crumbs` reads and (`--clear`, root) zeroes them at the next
  boot, and says when the SoC restarted.
- `observe=1` (`run-floor --m1-observe`, with `m1=1` only) is its run 2: CPU7 stays up through the commit,
  watches CPU0 and the E complex power down, reads the watchdogs and other state, and records them in the same
  breadcrumbs.
- `pmgr_late=1` (`run-floor --m1-late`, which adds it to `--m1-observe`'s; with `m1=1` only) is its run 3: the
  table's PMGR quiesce goes out after `slpa`, in its place in the table, with macOS's waits, and the
  breadcrumbs say which waits timed out and when EACC and PACC0 finished powering down.
- `alarm=1` (`run-floor --m1-alarm`, with `m1=1 pmgr_late=1` and no `observe=1`) is its run 4: the PMU's RTC
  alarm, an hour after the commit, written between the commit's first write and the other two, with no
  observer; `m1-crumbs --clear` disarms the alarm the run armed at the next boot.
- `hold=1` (`run-floor --m1-hold`, with `alarm=1` and no `observe=1`) is its hold rung: every other CPU writes
  its own stop bit and holds, spinning, through the commit, CPU0 lets go after the third commit write, and
  each then arms deep sleep and takes its deep WFI, CPU0 reading them down and going last (`hold_nowatch=1`,
  `run-floor --m1-hold-nowatch`: no such reading).
- `pmp_out=1` (`run-floor --m1-pmp`, on `hold=1`; `pmp_stop=0`, `--m1-pmp-idle`, is the idle pair alone) is
  its PMP rung: right after `slpa`, the PMP's RTKit idle pair once and, if its ASC then reads idle, one write
  that clears its CPU's RUN bit; bit 7 of every later stage byte says 0x6015 is that step's byte.
- `position=1` (`run-floor --m1-position`, on `hold=1`; `e1-hook/m1_position.h` has the bytes) is its position
  read: read-only, CPU0 stays up after watching the others go down and samples where the engine is, about 1 s
  and 25 s after the third commit, in the same breadcrumbs (stages 0x40-0x45); run-floor refuses it without
  `recfg-peek --position` having run in that boot.
- `sio_on=1` (`run-floor --m1-sio-pmp`, with `position=1 pmp_out=1`) is its combined rung: ps_sio, UART_P and
  SPI_P raised just before the table's SIO_CPU word (Corey's OK of 2026-11-09), the launcher withdrawing the
  PMP's display report requests, and one sample at about 1 s (stages 0xc0-0xc5; 0x6007's bit 0 says the raises
  read on).
- `disp_off=1` (`run-floor --m1-disp-off`, with `sio_on=1`) is its display rung: before noirq, both DCPs shut
  down through RTKit and their CPUs stopped, and after `slpa` their domains off (Corey's OKs of 2026-11-09;
  stages 0xc8-0xcd).
- `one_commit=1` and `smc_idle=1` (`run-floor --m1-macos-end`, on `disp_off=1`) write the commit once and give
  the SMC its AP idle notice after K4.
- `sio_stop=1` (`run-floor --m1-sio-stop`, on those with `hold_nowatch=1` and no `position=1`) stops SIO's
  firmware before noirq (Corey's OK of 2026-11-09); stage 0x4f, and 0x6012 has the steps' outcomes (0x6013 =
  0xa5 says they landed).
- `ps_words=1` (`run-floor --m1-power-words`, on `sio_stop=1`) writes macOS's remaining 20 power words at the
  end of step 5 (Corey's OK of 2026-11-09), and 0x6013 = 0xc0 | one bit per group says how they went.
- `recfg_bite=1` (`run-floor --m1-recfg-bite`, on `ps_words=1`) is the feasibility run for instrumenting the
  engine: `e1-hook/recfg-bite` first changes one word of the reconfig SRAM, A2's WD2 bite, from 30 s to 45 s
  (Corey's OK of 2026-11-09; as one 64-bit store since run 13, Corey's OK of 2026-11-10), and after the
  commit, once it has let the others go, CPU0 reads WD2's bite and PHASE (stage 0x47, or 0x46 with an
  exception pending, with its byte at 0x6005). The module never writes the SRAM, and nothing writes the old
  value back by itself.
- `wd2_bite=1` (`run-floor --m1-wd2-bite`, on `ps_words=1`, never with `recfg_bite=1`) moves the reset's time
  without the SRAM, which dropped both stores: WD2's bite register, 0x2922b0024, written to 120 s (Corey's OK
  of 2026-11-10 for that register and value, twice). `e1-hook/wd2-bite` writes it first, once, from user
  space, awake, the control runs 13 and 14 lacked; after the commit, once it has let the others go and has
  seen WD2 armed, CPU0 writes it once more, the module's one write to WD2, and records what WD2 read around
  that (stage 0x57, or 0x56 with an exception pending, with its byte at 0x6005).
- `cluster_ack=1` (`run-floor --m1-cluster-ack`, on `wd2_bite=1`, `wd2-bite`'s store first as there) acks the
  "up done" flag that the replay is thought to leave set in three soc-cluster words (inferred, never read):
  step 5 writes ANE0's, AFR's and AVEMSR's (0x28e09800c, 0x28e098014, 0x28e098020) as 0x20, not the table's 0
  (Corey's OK of 2026-11-10 for those three words and that value), and the four cluster waits, DISPLAY's too,
  ask for the whole word to read 0: for one that doesn't, 0x6006's bits 2-5 say whose flag was still set and
  0x6014's bit 5 that something else was left in a word; 0x6014's bit 4 marks the run.
- `fabric_up=1` (`run-floor --m1-fabric-up`, on `cluster_ack=1`) puts the fabric's p-state back where macOS
  has it: at the start of step 5, one write to 0x28e20c000, the word as read with its low nibble := 3, its
  awake value, which this kernel's `suspend_noirq` lowers to 1 at every suspend (Corey's OK of 2026-11-10 for
  that word and value), then up to 10 ms for it to read 0x333, and one more read at step 5's end. The load
  refuses unless the word reads 0x333 awake. From step 8's command on the run has stages of its own (0x52
  for 0x4e, 0x53 and 0x51 for 0x57 and 0x56), because 0x6007 becomes `fabric_up.h`'s byte there: the sio
  bit, "not all seven held", what the word read before the write, whether it read 0x333 after it and at
  step 5's end, and `wd1_mark=1`'s two bits. Step 7 leaves the cores that held in the same place, so
  `m1-crumbs` reads the byte only if the steps' outcomes landed after its command, and nothing past a set
  "not all held" bit, which step 7's byte has set whenever CPU1 held.
- `wd1_mark=1` (`run-floor --m1-mark`, on `fabric_up=1`) makes WD1, the kernel's own watchdog, a marker of
  how far B2 gets with every core down. The kernel keeps WD1 armed and pinged while awake and stops it at
  suspend, so it's set in step 4, a moment before the commit: its bite := 150 s, its count := 60 s (a
  non-zero count write, new in kind), then RESET_EN (`wd1_mark.h`; built on Corey's standing go-ahead of
  2026-11-10). B2 zeroes WD1's count at its op 224 of 246, so with WD2 at 120 s the restart reads: about
  90 s, never zeroed; about 120 s, zeroed, and the pair stopped in its last ops; 150 s or later, or none,
  the pair finished. Only if the marker stood: step 8 reads WD1 back, and bits 7:6 of 0x6007's byte say
  whether it was armed with 150 s and its count had run on past 60 s. `m1-crumbs` reads the restart's time
  against it only at the run's last stage and as far as `wd2_bite=1`'s sample bears a reading out, and says
  what else 90 s and 120 s could be. With the marker set, a hang anywhere from step 4 to the commit ends in
  WD1's restart about 90 s later, not in a dark Mac.
- `run-floor --quick` ahead of any recipe makes the reading 1 minute. `13` is the floor with the CPUs left up.

## What holds for every one-way run

`13` and `15` load only with `oneway=1` and in a safe boot (`sleepdrain.safe` on the command line). Only the
first suspend after their load can replay (a `suspend_resume` tracepoint probe counts suspends from
`enter_state()`'s first line), and any suspend that doesn't replay to its end fails with `-EIO`, so with them
loaded the Mac is in the floor or awake, never in a plain s2idle; writing 1 to `cancel` calls the replay off.
run-floor waits for its suspend (`/sys/power/suspend_stats`) and, if it fails, comes back or never begins,
calls the replay off, lights the keyboard and prints "NO FLOOR" with what happened. The module won't build or
load if any of its writes would touch the never-write list (0x28e0802d8, `ps_pmp`; 0x28e700000). Both
launchers run in a safe boot only, take the reading and suspend, and unload the module on any other way out
before the suspend is asked for; after that, run-floor calls the replay off instead
(`tests/e1a_launcher_test.py`).

## Tools beside the module

- `e1-hook/e1a-platform` runs E1a's replay under `pm_test=platform` instead and prints what each key write
  returned, for a photo, since the NVMe is gone after `slpa`. Not installed by `install.sh`.
- `e1-hook/recfg-peek` (root, safe boot only, read-only; `cc -O2 -Wall -o e1-hook/recfg_peek
  e1-hook/recfg_peek.c` first) reads the AON engine's reconfig SRAM, in stages, saving after each: the table
  offset register's analogue, the SRAM's first word, the table, then 0x10000 bytes (`--rest`: on to the
  table's last sequence + 0x10000, after that dump is decoded). `--to-end` (Corey's OK of 2026-11-10) reads
  what's past those 0x10000 bytes, to the SRAM's end at 0x70000, in 24 pieces of 16 KiB, each a run of the
  reader, saved after every fourth piece and at any stop, with `kernel.panic` set so that a read whose fault
  panics the kernel restarts the Mac: the other eight descriptor blocks' sequences are out there. A piece that
  reads all ones stops it. The dumps stay out of the repo. `--position` reads the position read's words awake,
  one at a time, in the safe boot of the run; `--position-after` repeats them once in the first regular boot
  after the run's reset (Corey's OK of 2026-11-09), the one read of this kind outside a safe boot.
- `e1-hook/recfg-bite` (root; `cc -O2 -Wall -o e1-hook/recfg_poke e1-hook/recfg_poke.c` first, and
  `recfg_peek` as above) is the feasibility run's one SRAM word, A2's WD2 bite at 0x292f02010 (`recfg_bite.h`
  has where it is and both values). In a safe boot it runs `recfg_peek`'s read-only stage 6, saves, then
  `recfg_poke patch`, the only thing in the repo that writes the SRAM (one word, one of two values, nothing
  taken from its arguments, all ten words read again after), and saves. Run 13's 32-bit store was dropped, so
  the write is now one aligned 64-bit store: the bite, and above it the next word, 0x292f02014, stored as the
  4 the check has just read there (Corey's OK of 2026-11-10). Its put-back, if the ten words read wrong after
  the write, is that store with the old bite: both words as they were found, whatever they read then.
  `run-floor --m1-recfg-bite` runs that itself. The OK is one write, so nothing undoes it automatically:
  `--restore` is for a person, in a safe boot. `--after` reads that one word once in the first regular boot
  after the run (`recfg_peek`'s stage 7), and only if the collected record says it read 45 s when the Mac went
  down, with nothing left to collect. run-floor refuses an `e1_hook.ko` older than its sources.
- `e1-hook/wd2-bite` (root, safe boot only; `cc -O2 -Wall -o e1-hook/wd2_poke e1-hook/wd2_poke.c` first) is
  `--m1-wd2-bite`'s control (`wd2_bite.h` has the register, its values and the module's byte): `wd2_poke
  check`, read-only (WD2's control, count and bite), saved, then `wd2_poke patch`, one 32-bit store of 120 s
  to WD2's bite through /dev/mem, made only if WD2 is as it is awake (control 0, bite 90 s), the three words
  read again, and saved. It exits 0 if the store took and 5 if it didn't, each only once its record is saved,
  and run-floor goes on after those (and after 7: the bite read 120 s before any store in this boot, so none
  was made). It makes one store in a boot: a mark in /run says how that one went, and a later run gives its
  result without storing again. Nothing writes the 90 s back: the OK is for one value, and a restart undoes
  it.
