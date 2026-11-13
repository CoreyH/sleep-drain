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
- `gates_mark=1` (`run-floor --m1-gates`, on `wd1_mark=1`, never with `probe=1`; `e1-hook/gates_mark.h` has the
  words, the byte and the stages) only reads, and writes no register: in step 8, after the stage and the steps'
  outcomes and right before the commit, CPU0 reads the six gate-status words B2 polls (0x28e5a0050, 0x28e5a0064,
  0x28e5a030c, 0x28e09c310, 0x28e09c338, 0x28e09c34c, `look.h`'s order) and AFNC2_LW1, the fabric lane above SIO,
  in the state the recipe leaves, which nothing had read them in, and sends one byte to 0x6016: bits 0-5 bit 30 of
  each gate (set is closed), bit 6 the lane at exactly macOS's 0x1000030f, bit 7 always. 0x6016 was the alarm's
  result, the byte the ending needs least: the alarm's last command, after the commit, sends the gate byte again
  in its place, so nothing after the commit changes but that value, and the alarm's own registers at the next
  boot say whether it was written. So from step 8 on the run has stages of its own, fabric_up=1's with that one
  difference: 0x4b, 0x55 and 0x54 for 0x52, 0x53 and 0x51 (0xcb, 0xd5, 0xd4 with K4's bit). The load maps the two
  gate pages, refuses unless they and SIO's page are mapped, and logs the seven words awake. `m1-crumbs` reads the
  byte only at those stages, with K4's bit, the outcomes' marker at 0x6013 and its own bit 7, and says what each
  gate read and, for 0x28e5a0050 (B2's op 140), what follows: closed, that poll is cleared and `a2_pre=1`
  is next; open, as in the one run, nothing, since the recipe leaves SIO's parents and SIO_CPU on and B2
  switches them off itself before the poll. A read that faults or never returns leaves stage 0xcb with 0x6016 still 0,
  which `m1-crumbs` calls out, and a restart about 10 s later (the SError's panic, if its restart works) or about
  90 s after step 4 (WD1's marker); a byte whose own command failed leaves the same record, and the run then went
  on unrecorded. The rest of the ending reads as `--m1-mark`'s.
- `sio_steps=1` (`run-floor --m1-sio-steps`, on `gates_mark=1`, never with `sio_down=1`, `pcie_down=1` or
  `probe=1`; `e1-hook/sio_steps.h` has the words, the order, the byte and the stages) asks what lets the gate
  0x28e5a0050 (B2's op 140) close, which `sio_down=1`'s switch-off left open twice. In step 8, where
  `gates_mark=1`'s reads would be, CPU0 makes that switch-off of SIO's 43 words with B2's cycle of GPIO's power
  word in B2's place (0x28e080120 := 0x8000000f before the first 0, := 0 after the first 38 writes, bit 11 polled),
  polls the two gates and sends a byte; if all 43 went off and the gate is still open, it writes B2's ops 8 to 29
  (0x28e03801c := 0x80101100, 1 to 0x28e0a0004-1c, 1 to 0x28e5b0004-38 in B2's order), a byte after each group,
  reads each back and polls the gate again; then it writes the 43 back as found (up to 100 ms each), polls the gate
  open, and sends the byte a last time. New writes: two to GPIO's word and the 22, all left as B2 itself writes
  them. The byte has two layouts, told apart by bit 6. Set, the result: bit 0 the gate closed after the switch-off
  with GPIO's cycle, bit 1 closed after the 22, bit 2 the subtree off and every poll met, bit 3 GPIO's cycle made
  (up within 100 ms, then off and idle), bit 4 the 22 read back as written, bit 5 the 43 back and the gate open
  again. Clear, the pass wasn't through: bits 0, 2 and 3 as above and, in bits 4 and 1, how many of the 22's three
  groups were written; 0xa0 is a gate that read closed before anything was written. 0xfc is everything made and
  the gate open throughout; 0xed closed with GPIO's cycle; 0xfe closed once the 22 were in. Stages 0x95, then 0x97
  or 0x96. It adds up to about 9.5 s before the commit. The rest of the ending reads as `--m1-gates`'.
- `a2_pre=1` (`run-floor --m1-a2-pre`, on `gates_mark=1`, never with `sio_down=1`, `pcie_down=1`, `sio_steps=1`
  or `probe=1`; `e1-hook/a2_pre.h` has the words, the values, the waits, the byte and the stages) makes from the AP
  the three moves A2 makes by itself after its first handshake, and leaves them. In step 8, where `gates_mark=1`'s
  reads would be, CPU0 first reads the five ClkGen muxes the table parked in step 5 (a rider: nothing had read them
  after their write) and sends a byte, then writes 2 to DCS's p-state word (0x28e20c800), 2 to the fabric's
  (0x28e20c000) and 0 to 0x28e20c400, A2's ops 11, 13 and 15, each the whole word, each followed by up to 100 ms
  for the condition A2 waits on with no bound and by the byte. Nothing is written back. New writes: those three,
  with values no run has written. The byte: bit 0 NOT all five muxes read 0x80100000; bits 1 and 2 DCS's word read
  asked for 2, and as A2 waits for it; bits 3 and 4 the same for the fabric; bits 5 and 6 for the third word (asked
  for 0); bit 7 always. A move's bits are 0 before it's made and are read anew for every later send. 0xfe is all
  three taken and settled with the muxes parked; 0x80, none read as written or as A2 waits for it (what they did
  read isn't in the byte). Stages 0x99, then 0x9b or 0x9a. A byte that stops short at stage 0x99 is the last thing
  recorded, not a proven stop: CPU0 didn't come through the next move, or a send failed and the run went on
  unrecorded. It adds at most about 0.3 s before the commit. The rest of the ending reads as `--m1-gates`'. Not run
  yet.
- `pcie_down=1` (`run-floor --m1-pcie-down`, on `gates_mark=1`, never with `sio_down=1` or `probe=1`;
  `e1-hook/pcie_down.h` has the words, their order, the byte and the stages) makes B2's own switch-off of the PCIe
  power words from the AP, before the commit, and leaves them off. In step 8, where `gates_mark=1`'s reads would be
  (they aren't made), CPU0 reads APCIE_GP_SYS as found, writes 0 to APCIE_ST_SYS, APCIE_ST1_SYS and APCIE_GP_SYS
  (0x28e0802e8, 0x28e0802f0, 0x28e080320; B2's ops 141 to 143), polls each for bit 11 clear as B2 does, up to
  100 ms, sends one byte to 0x6016, writes 0 to APCIE_ST and APCIE_GP (0x28e080148, 0x28e080150; B2's ops 147 and
  148), gives each up to 100 ms to read off, polls the gate 0x28e09c34c (B2's op 156) for bit 30, and sends the
  byte again. Five new writes, each a 0; nothing is written back, and no PCIe register is touched. The byte: bit 0
  APCIE_GP_SYS found up, bit 1 found with bit 11 set, bit 2 its poll met, bit 3 it read off and idle at the send's
  last read, bit 4 the two storage words' polls met and both off and idle, bit 5 APCIE_ST and APCIE_GP off, bit 6 the gate
  closed, bit 7 always; bits 5 and 6 are never set in the first send. 0xbd is a clean switch-off with the gate
  open, 0xfd with it closed. Stages of its own: 0x1d before the commit, then 0x1f, or 0x1e with an exception
  pending (0x9d, 0x9f, 0x9e under K4's bit). The commit finds the words off, so a restart at about 120 s in place
  of 90 s says B2 got past its op 224. The load refuses if one of the five is on the never-write list (`ps_pmp`'s
  word is in the same page), and the build pins the five addresses.
- `sio_down=1` (`run-floor --m1-sio-down`, on `gates_mark=1`, never with `probe=1`; `e1-hook/sio_down.h` has the
  words, their order, the byte and the stages) makes B2's own switch-off of SIO's subtree from the AP, before the
  commit, and puts it back. In step 8, where `gates_mark=1`'s reads would be (they aren't made), CPU0 reads the 43
  power words from 0x28e580180 to 0x28e5802d0 as found, writes each 0 in B2's order and groups (SIO_CPU first, SIO
  last), polls each for bit 11 clear as B2 does (not UART_N), polls 0x28e5a0064 and then 0x28e5a0050, the gate B2
  waits for at its op 140, for bit 30, up to 100 ms each, reads the 43 and the six gate words, and sends one byte to
  0x6016. The byte has two layouts (its second form: the first run's, 0x96, couldn't say which part of "all off"
  or of "back" failed). If all 43 read target 0 and actual state 0 and every poll was met, bit 6 is clear: bit 0
  that gate closed; bits 2:1 what the words' auto fields read after the 0 (3 all 0; 2 auto-enable clear everywhere
  and bits 27:24 f only in SIO, SIO_CPU or SIO_SPMI0-2, which is what those five read after any switch-off on file;
  1 bits 27:24 something else; 0 an auto-enable still set); bit 7 always. If not, bit 6 is set and bits 5:0 are a
  place: 0 to 42, the first word in B2's order whose target or actual state didn't read 0; 62, a poll unmet; 63,
  the gate read closed before the first write, and then nothing is written at all. Then it writes all 43 back
  in reverse order, each as found less the bits the kernel never writes back, and sends the byte again, in the
  first layout with bit 3 if they all read as found in target and actual state within 10 ms each, bit 4 if in
  auto-enable (bits 27:24 and the two was-gated flags, bits 8 and 9, aren't compared), and bit 5
  if the gate then read open again; the alarm's last command
  sends it a third time. The new writes are those
  86 (built on Corey's standing go-ahead of 2026-11-10); B2's GPIO cycle and its raise of SIO_CPU aren't made. So
  the commit finds the subtree as the recipe leaves it, and the ending reads as `--m1-gates`' as long as bits 3,
  4 and 5 are set. The run's stages are its own from step 8 on: 0x3d, 0x3f and 0x3e for 0x4b, 0x55 and 0x54 (0xbd, 0xbf, 0xbe
  with K4's bit). The load refuses unless the list is exactly those 43 words, none on the never-write list, with
  SIO's page and the gate pages mapped, and logs the 43 awake. `m1-crumbs` reads the byte only at those stages and
  reads it by its layout. A place names the word that stayed up (the lead is what holds it), the poll, or the gate
  closed before; none of those gives a reading of the gate. Otherwise: inconclusive unless 0x6012 says SIO's
  firmware was stopped (the switch-off doesn't check that itself); inconclusive on the gate if an auto-enable
  survived or bits 27:24 read something unexpected (which word comes first); else closed (op 140 less likely the
  stall, `a2_pre=1` next) or open (a lead on op 140, GPIO's power word next), with f in the five's bits 27:24 said
  beside it. Either is the AP's state before the commit, not B2's at op 140. The restart's time compares with
  `--m1-gates`' only if bits 3, 4 and 5 are set. It normally adds a few ms before the commit and 100 ms while the gate
  stays open, about 5 s at the very worst. A run that stops inside it leaves stage 0xbd with 0x6016 at 0, or the
  byte with none of bits 3 to 5.
- `run-floor --quick` ahead of any recipe makes the reading 1 minute. `13` is the floor with the CPUs left up.

## The probe that isn't a run

`probe=1` (`pkexec e1-hook/probe`, in a safe boot; needs `wd1_mark=1`, so the launcher loads `--m1-mark`'s
parameters with it) reads the three coprocessor words B2 polls before its op 224, bit 1 of SIO's, ANS's
and the PMP's ASC + 0x8000, in the state the recipe leaves those coprocessors in (`asc_probe.h` has the
ops, the steps and the bytes; RESEARCH.md, "After run 17", why, and "The probe as built" the rest). With
it loaded nothing replays and nothing sleeps: `e1_replay()` returns at once, `prepare` takes none of the
replay's steps, and `prepare_late` fails every suspend with -EIO.

- At load: the table's entry 61 has to be a raise of SIO_CPU and ANS2 off the never-write list; ANS's
  and the PMP's words are read once each, with no poll. ANS's read there is the only one with its
  firmware running (the NVMe driver's suspend shuts it down before `prepare`), and it's kept for its byte.
- In the first suspend to reach `prepare`: SIO_CPU fully on with the table's own word, then
  `sio_stop=1`'s steps on SIO; after noirq ANS2 raised if it's off, `pmp_out=1`'s steps on the PMP and
  `sio_on=1`'s raise. Each word is read before, between and after, only while its domain reads actual
  f, and again while bit 1 reads clear (B2's 255 tries, about 100 ms).
- The one word it writes that no run does is ANS2: B2's own 0x0f00030f, then the value found less BUSY
  and the two write-1-to-clear flags, checked for 10 ms. It leaves the DCPs, the SMC and the cores
  alone.
- Its lines are `pr_info` (journald broadcasts `pr_emerg` over the terminal). In the PMU: stage 0x0e,
  which no run uses, at the start of `prepare`; the step about to be taken at 0x6010 from then on, so
  that a Mac that goes down inside the probe says where; three bytes at 0x6005 to 0x6007 at the end.
  The SUMMARY line is read from those bytes, as `m1-crumbs` reads them: SET, clear, or NOT READ where
  the read in the state B2 finds wasn't made (the domain didn't read actual f, or the PMP's RUN wasn't
  cleared).
- What a read says is a lead: B2 polls later, after its own raises.

`e1-hook/probe` refuses outside a safe boot, with the module loaded, a stale or foreign `e1_hook.ko`,
`pm_test` set, or breadcrumbs left. Before the load it has `pmp_sleep_release` withdraw the PMP's
display report requests at noirq (`sleep-drain-pmp release 0xc 0xd`), as run-floor does, so that the PMP
is idled as in the runs; at resume that module asserts them again to a PMP that's stopped by then, which
times out and is logged. A module that doesn't read `probe=Y` once loaded has its replay called off and
is unloaded, and no suspend is asked for. It asks for the suspend through
`/sys/power/state` (no sleep hook, so no line in the sleep log and the radios as they are), again,
three times at most, only while the module's `prepare` hasn't run (nothing is stopped until then), and
unloads the module if none got there; then prints the module's lines for a photo and saves them with
`sleep-drain-safe save`. After a probe SIO's firmware and the PMP's stay stopped (the kernel's drivers
do nothing about either at resume), so the Mac is restarted, and the breadcrumbs cleared as after a
run. The charger may stay in (`tests/e1_hook_probe_test.py`).

`probe=1 look=1` (`pkexec e1-hook/probe --look`; `look.h` has the list) is the probe with none of its
steps: nothing is idled or stopped, ANS2 isn't raised, and no register is written. Awake at the load and
again at the first `prepare_late`, after noirq, it reads 120 words: the 98 the pair's remaining waits
depend on (the three p-state words, six words of the engine's page, the power words B2 switches off or
polls, six gate-status words, fifteen clock generators' first two words, then the two words of the
engine's page that its sequences only write), and last of all, since 2026-11-11, the 22 words B2 writes
before its first handshake (0x28e03801c, 0x28e0a0004 to 0x28e0a001c, 0x28e5b0004 to 0x28e5b0038; the
last two pages had never been read here, and each has a mark of its own). It logs each in full; the
suspend is then failed as the probe fails it. In the PMU it leaves stage 0x0f and, at 0x6010, the group it's reading, with 0x10 set at the load;
the load's look zeroes both again once it's through. Its start-up is the last of the load's steps and
refuses unless every page it reads through is mapped. The launcher doesn't load `pmp_sleep_release` for
it and refuses if it's loaded, refuses to go on unless the loaded module reads `look=Y` (without it the
module would stop the firmware), prints and saves the load's look before it asks for the suspend, goes
by `prepare_late_calls` for asking again, and prints and saves the look after noirq. Its log grows in
`/run` and goes into `/var/log/sleep-drain` whole, once for each save (`-load.txt`, then the record):
a file there never changes between two saves, since `collect` keeps the first copy it has. Nothing is stopped,
so the Mac comes back as from any failed suspend: it did both times (2026-11-10 with the 98 words,
2026-11-11 with the 120, neither new page faulting); why the probe itself didn't come back isn't
explained (RESEARCH.md, "The probe's run").

`probe=1 look=1 gate=1` (`pkexec e1-hook/probe --gate`; `gate_test.h`) is the look and one test after
it, the only part of a look that writes registers. After the look that follows noirq it reads the six
words it may write and SIO's own, and stops there, writing nothing, if SIO reads on or a UART is in
use. Otherwise it switches off the five UARTs Linux's suspend leaves at 4 (0x200, `ps_words=1`'s
value), waits up to 100 ms for bit 30 of 0x28e5a0050, the gate word B2 polls after SIO's subtree, reads
the lane and the gate once more, gives the fabric lane above SIO, AFNC2_LW1, macOS's 0x1000030f and
reads it back, waits for the gate again, and writes all six words back as found, less the bits the
kernel never writes back. Each word goes out once and back once, through one function, and the load
refuses if one of them is on the never-write list.

`probe=1 look=1 pre_test=1` (`pkexec e1-hook/probe --pre`; `pre_test.h`; never with `gate=1`) is the
look and another test with writes: the 22 clock words B2 writes first (its ops 8 to 29), by themselves,
with nothing stopped. After the look that follows noirq it reads the two pages' + 0 words (0x28e0a0000
and 0x28e5b0000, also read awake at the load), the 22 and the six gate words as found; writes
0x28e03801c := 0x80101100 and gives it up to 100 ms to read so, bit 30 clear; writes 1 to the seven
from 0x28e0a0004 and the fourteen from 0x28e5b0004, in B2's order, reading each back; reads everything
again 100 ms later; then writes all 22 back as found (0x28e03801c first and less its bit 30, the 21 by
address: B1's order) and reads them again. 44 writes, to the 22 words and no other, and the load
refuses if one is on the never-write list; none is made if the PMU doesn't ack the zeroing of 0x6005 or
the mark of the first write. Every word is logged in full. The PMU keeps the step at
0x6010 with 0x80 set, six bytes at 0x6011-0x6016 sent before anything is written back (two bits a
select: as written, as found, as written with more bits set, or something else; the same for
0x28e03801c at the end of its poll and 100 ms on; whether bit 30 was seen set), and a byte at 0x6005
after the write-back (bit 7 always, bit 6 0x28e03801c back, bits 4:0 the first select that isn't back,
bit 5 more than one; 0xc0 is all back). `m1-crumbs` reads the record whether or not the Mac came
back. **Run once (2026-11-13): it came back, and not one of the 22 took the write** (RESEARCH.md, "The
`pre_test=1` run"). A run in which they did take would be a different one: these are clock words B2
writes with every core down, so that one is still to be treated as one-way.

**The gate test doesn't come back.** Run once, it went dark and restarted by itself at about 30 s though the gate
never read closed (RESEARCH.md, "The gate test's run"): treat it as one-way. The reason given when it
was built, which still holds if the gate does close: if the gate closes, SIO's island loses power with SIO's firmware running, and
the kernel can't restart that firmware: the screen may stay dark, as after the probe (the launcher says
so, and what to do then, before it asks for the suspend). So the record is
made to stand without the kernel log: the step at 0x6010 with 0x20 set, the result byte at 0x6005, and
the lane's three reads, two bytes each, at 0x6011-0x6016. Those bytes are zeroed when the test starts,
and the reads go out before anything is put back, in one command with step 6's mark: `m1-crumbs` reads
them as this test's only under that mark or a later one (0x40 in a later mark says the command wasn't
acked), and a test that isn't made leaves mark 9. `m1-crumbs` reads all of it after the restart; the
launcher refuses to start unless 0x6010-0x6016 are zero as well as the stage, and `m1-crumbs --clear`
zeroes the stage last, so a clear that stopped halfway doesn't pass for one that finished. When it does come back,
everything is in the kernel log in full, and the launcher prints the look's lines above the photo's
block and the test's lines in it, as it asks the module for `gate=Y` as it does for `look=Y`.
RESEARCH.md, "The gate test as built", has why, and what the review of #80 changed.

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
