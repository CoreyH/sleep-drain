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
  unrecorded. It adds at most about 0.3 s before the commit. The rest of the ending reads as `--m1-gates`'. Run once,
  2026-11-14: the byte 0xff (all three took and settled; the muxes didn't all read parked), restart at 91 s.
- `acc_last=1` (`run-floor --m1-acc-last`, on `gates_mark=1`, never with `sio_down=1`, `pcie_down=1`, `sio_steps=1`,
  `a2_pre=1`, `observe=1` or `probe=1`; `e1-hook/acc_last.h` has the entries, the time's code, the byte and the
  stages) writes the three CPU complexes' soc-cluster words last. Step 5 leaves out the table's entries 52 to 54
  (0x28e098000 := 0, 0x28e098004 := 0x20, 0x28e098008 := 0x20: EACC, PACC0, PACC1), and step 9 writes them, the
  same values in the same order, under the mask and right before the commit's write. After the release and
  `wd2_bite=1`'s stage one more command goes out: the byte with the time from before the first cluster word, as a
  code. In step 8, where `gates_mark=1`'s reads would be, CPU0 reads the five ClkGen muxes the table parked. No new
  address and no new value; what changes is the three writes' place, after the table's entries 55 to 72 where
  macOS has them before. The byte: bits 4:0, mux n in the table's order (0x28e038164, 16c, 168, 15c, 170) read
  0x80100000; bits 7:5 the time's code: 0 never, 1 to 5 under 5, 10, 20, 50 and 100 ms, 6 that or more, 7 "not
  yet" (the send before the cluster words). The span runs to CPU0's read of the clock after the last stage went
  out: it covers the commit, the alarm's commands, the release, the wait for WD2's arming (up to 300 ms) and the
  stage's command, and not the byte's own command or CPU0's WFI, so only a code of 3 or less (under 20 ms) is
  read as under T8103's 52 ms, and 4 as too close to call. Stages 0x91, then 0x93 or 0x92; at 0x92 CPU0 likely
  never went down and the limit isn't tested. Why: on T8103
  the chip starts its own block at the first cluster word and every core has to be in its wait within tens of ms
  (Naeem Malik's second driver); nothing shows T6000 has such a limit. The rest of the ending reads as
  `--m1-gates`'. Run once, 2026-11-14: the byte 0x20 (under 5 ms; none of the five muxes read parked), restart
  at 90 s.
- `last_look=1` (`run-floor --m1-last-look`, on `wd1_mark=1`, never with `gates_mark=1`, `observe=1`,
  `position=1`, `recfg_bite=1` or `probe=1`; `e1-hook/last_look.h` has the bytes, the times and the stages) is
  `--m1-mark`'s run, run 17's, with none of the later steps on B2's ops, and a record of CPU0's own last WFI.
  Step 4 writes WD1's count as 80 s, not 60: the marker restarts the SoC about 70 s after the screen goes dark.
  The first time CPU0's WFI returns, CPU0 sends seven bytes to the PMU's 0x6008-0x600e (Corey's OK of
  2026-11-16; all 0 if it never returned) in three commands, each before the next one's reads: 0x6008, bit 7,
  the time since the commit in bits 6:4 (under 10 ms, 100 ms, 1 s, 5 s, 15 s, 22 s, 30 s, or more) and a count
  in bits 1:0 (once, twice or more, a hundred or more; sent again at the second and the hundredth return);
  0x6009, what was pending (ISR_EL1's F, I, A), whether CPU0's timer was firing, HS_A's bit 24 and HS_B's bits
  0, 1, 31; 0x600a-0x600c, the low bytes of 0x2922c0040 and 0x2922c0044 and PHASE's low seven bits under a bit 7 that says
  the command landed whole; 0x600d, how many of SIO's 43
  power words read off and whether FPWM0's did; 0x600e, the same for the five PCIe words, ANS2 and the PMP.
  Reads only, but CPU0's timer control, written 0. Nothing is armed that could end the WFI, so a return means
  a pending source. Stages 0x89, then 0x8b or 0x8a.
- `ll_wake=1` (`run-floor --m1-last-look-wake`, on `last_look=1`) arms three things in that run: the PMU's
  alarm 20 s after the commit in place of an hour (an SoC that slept comes out in the boot loader: a restart
  at about 20 s); CPU0's own timer 25 s after step 10, unmasked (a core still powered returns for it, and the
  seven bytes are then a look at the engine 25 s in); and, in step 8, the keyboard light set blinking, 100 ms
  on and 100 ms off: FPWM0's power word (0x28e580190) asked on and, if it reads on within 10 ms, the PWM's on
  cycles, off cycles and control (0x39b03001c, 0x39b030018, 0x39b030000), four new writes. B2 switches FPWM0
  off as the tenth of its 43 SIO words, so a light that blinks until the restart says B2 never got that far.
  Bit 6 of the alarm's byte at 0x6016 says the PWM's writes were made. Stages 0x86, then 0x88 or 0x87.
  `e1-hook/kbd-blink` (root, awake) makes the PWM's three writes for a few seconds, to see the blink first.
- `gate_look=1` (`run-floor --m1-gate-look`, on `last_look=1`; the launcher sets `ll_wake=1` with it) adds one
  read to that run: in step 10, 1 s after the commit's last write and before CPU0's last WFI, CPU0 reads what
  a return of that WFI would read (the engine's two gate words, PHASE, HS_A, HS_B, SIO's 43 power words, the
  PCIe five, ANS2, the PMP's) and sends it itself, in two commands: 0x6008 := 0x08 with 0x6009-0x600c, then
  0x600d and 0x600e. Bit 3 of 0x6009 is HS_A's bit 31 there. K1 leaves 0x6008 as 0x04, so a look that never
  landed shows. A WFI that returns afterwards sends 0x6008 alone, bit 3 kept. Runs 6 to 10 read these words
  with CPU0 awake under an earlier recipe; this is that read under today's. It is a reading with a core up,
  which may itself hold B2's first poll: not the stall's own state after the WFI. Reads only; no stage of its
  own (the stages are `ll_wake=1`'s).
- `obs_gate=1` (`run-floor --m1-obs-gate`, on `observe=1`; the launcher sets `pmgr_late=1` with it, run 3's
  line) uses the same seven PMU bytes in the observer's run: after CPU7 has polled CPU0's and the E complex's
  power words and sent its first record, it makes a look and sends it there. K1 zeroes the seven bytes in
  every `observe=1` run, so that an earlier record can't read as a later observer's, and leaves 0x6008 as 0x40
  with `obs_gate=1`. The look has had two forms, and `m1-crumbs` reads both (bit 4 of 0x6008 marks the
  second). The first (the run of 2026-11-18): the four CPU power words (CPU0, ECPM, PCPM0, PCPM1), HS_A, HS_B,
  the engine's two gate words and PHASE, and the gate words and the four again 500 ms later; it read
  0x2922c0044 as 0x09 with CPU0 and the E complex off and P complex 1 up. The second, what the module does
  since 2026-11-19: the four power words, EACC's and PACC0's soc-cluster words and 0x2922c0044 (0x6008,
  0x6009); then the E complex's state word, 0x28e09a000, which the complex's own down sequence steps and
  nothing here had read (0x600d, 0x600e, the two bytes that haven't outlasted a restart yet); 500 ms later
  that again with P complex 0's, 0x28e09a004, EACC's word and 0x2922c0044 (0x600a-0x600c: the later sample
  is in the bytes that last). The load reads the three state words awake first and logs them. It is to see
  whether the E complex's own sequence runs to its end with CPU0 down, as P complex 0's does. Reads only;
  the stages are run 3's, and the restart comes at WD2's 30 s, with no marker.
- `atc_down=1` (`run-floor --m1-atc-down`, on `ps_words=1`; the launcher's line is `--m1-last-look-wake`'s with
  it) lowers six more power words at the end of step 5, each to 0x300 with up to 10 ms to read actual 0: the
  four USB controllers' (ATC0_USB to ATC3_USB, 0x2922800d0 to 0xe8) and ATC0_USB_AON (0x292280088) before
  `ps_words=1`'s, ATC0_COMMON (0x28e080238) after them. macOS's table has ATC0's two on because its trace ran
  under a hypervisor on that port, and the controllers' words stay on through Linux's suspend; nothing else
  lowers them. 0x6013's top two bits are then 0x40 in place of 0xc0, and a word that doesn't read off clears
  its group's bit there (USB_AON's or COMMON's). The stages are `ll_wake=1`'s; the keyboard's light is the
  result. Run it with nothing in the USB-C ports.
- `blink=1` (`run-floor --m1-a2-pre-blink`, on `gates_mark=1`, never with `sio_down=1` or `sio_steps=1`; the
  launcher's line is `--m1-a2-pre`'s with `atc_down=1` and it) is `ll_wake=1`'s light without its alarm and
  timer: in step 8 FPWM0's power word is asked on and the keyboard's PWM set to blink, `m1_ll_blink()`'s four
  writes. Nothing of it goes to the record (0x6016 is that family's own byte): the light blinking from the
  dark screen is the sign the writes were made, and its stopping before the restart the sign that B2 reached
  its tenth SIO word.
- `wake_all=1` (`run-floor --m1-wake-all`, on `gates_mark=1`, never with `observe=1`; the launcher's line is
  `--m1-a2-pre-blink`'s with it; `e1-hook/last_look.h` has the byte) changes what step 7's seven writes
  carry and nothing else: all seven AP wake-mask words, 0x292004008 to 0x292004020, are written 0xffffffff,
  where the recipe leaves source 54 (the PMU's interrupt, word 1's bit 22) and word 6's bits 31:13 (ids 0xcd
  to 0xdf, none with a name; the word is macOS's own 0x00001fff) open. It asks whether a pending wake source
  that the masks let through holds the engine before its power-downs. No address is new. Right after, still
  in step 7, `m1_wa_read()` reads the seven back and the seven words after them (0x292004024 on, the AP's wake
  status, inferred); their byte goes to the PMU's 0x6008 in step 8, right after that step's record at 0x6010
  and before its stage, so that a command that failed there couldn't cost the run that record (a failed
  command stops every later one; the stage, the outcomes and `a2_pre=1`'s byte still follow it). Neither read
  has been made in this state: every m1 run has written the masks in step 7, and they have only been read
  awake, at the load; the status words have been read around the commit only in `observe=1`'s runs, which
  have none of `hold=1`, `sio_stop=1`, `ps_words=1` and `atc_down=1`. A read that faulted would panic there,
  SError still unmasked: stage 0xb9 with 0x20 at 0x6008, and a restart about 10 s after the dark screen. K1
  zeroes 0x6008-0x600e first and writes its mark; since this option K1 and the undo zero those seven bytes in
  every m1 run, whatever its options, so that a byte or a mark an earlier run left can't read as a later
  run's under the stages they share, and `m1_pmu_ok()` lets through there seven zeros in any run and, with
  `wake_all=1`, one byte at 0x6008 that is the mark or has bits 7:6 set and bit 3 clear. So 0x6008 reads one
  of three ways after such a run: 0x00, the run
  didn't get as far as K1; 0x20, K1's mark, the byte never landed; or the byte, bits 7:6 both set, with
  bit 5 saying words 0 to 5 read back 0xffffffff and word 6's bits 12:0 all set, bit 4 that word 6's bits
  31:13 read back all set too (whether those bits exist isn't known), bit 3 always 0, and bits 2:0 how many
  of the seven status words read non-zero. `m1-crumbs` reads the byte under that family's stages and under
  step 7's own, 0x39, which is still the stage when the byte goes out. K1's mark it reads by the stage: one
  the run can be at before its byte (the run stopped before the byte's command, or that command failed), one
  of the family's (the command was acked and the byte didn't land), or another run's (a mark an earlier run
  left: not read). `--clear` zeroes the seven bytes after the byte or the mark only if the six after it are 0
  (or hold `sep_pg=1`'s byte and `disp_hs=1`'s four, below, as those options leave them) and bit 3 is clear;
  anything else there it leaves, and says so. The load logs both sets of seven awake. The
  stages are `a2_pre=1`'s and the keyboard's light
  is the witness, as in `--m1-a2-pre-blink`. With every wake source masked, a Mac whose entry completed would
  not wake for a key, the lid or the PMU's alarm: it comes back only by holding the power button. Run it
  with `--no-reading`, the nights off around it and nothing in the USB-C ports.
- `sep_pg=1` (`run-floor --m1-sep-pg`, on `gates_mark=1`, never with `observe=1`, with `wake_all=1` or without
  it; the launcher's line is `--m1-a2-pre-blink`'s with it; `e1-hook/last_look.h` has the byte) adds one write
  to step 7 and changes nothing else. Every run has ended with the reconfig engine stuck before its
  power-downs. Ante042, whose J314s completes the entry with a driver of their own, wrote on 2026-11-22 that
  a PMGR control word has a bit, set at boot, that makes the S2R sequencer wait for the SEP to power-gate,
  which never comes if nothing puts the SEP to sleep. The word and the bit are from their
  `drivers/soc/apple/s2r.c` (Ante042/linux, branch `t600x-s2r-reference`, `GPL-2.0-only OR MIT`, read on
  2026-11-23 with Corey's OK, credited): `T6000_PMGR_SEP_PG` 0x28e0cc200 and `PMGR_WAIT_SEP_PG`, bit 2;
  macOS clears it only for its `wait-sep-pg=0` boot-arg, and their `apple_s2r_sep_pg_clear()` reads the
  word, writes it back with bit 2 clear and reads it again where the wake mask is written, on the boot CPU
  alone with IRQs off. Here, right after the wake masks, nub-spmi0's enables and `wake_all=1`'s reads, still
  in step 7, `m1_sp_clear()` reads the word and the SEP's power word (0x28e580c00), writes the control word
  back with bit 2 clear **only if the bit is set and the SEP's power word reads actual state 0**, reads it
  again, and reads 0x2922c0044. The wait is never cleared while the enclave reads powered: the reference
  refuses to sleep at all unless that word reads 0, and in a boot without the enclave's driver it may read
  otherwise than it did with it (awake on 2026-11-23, with `apple_sep` loaded: 0x1000030f, target f, actual
  0). **What is new on the hardware:** one write, of one bit, in one PMGR control word: 0x28e0cc200's bit 2,
  the word 0x00000004 before and 0x00000000 after as it read awake (whatever its other bits read, they are
  written back as found; a word found with bit 2 clear, or found while the SEP's power word reads powered,
  gets no write at all). It is a PMGR register, not a write to the SEP; the restart undoes it; Corey OK'd it
  on 2026-11-23. Two reads are new in this state: the control word and the SEP's power word have only been
  read awake. 0x2922c0044 CPU0 has read awake and after a commit, not in step 7 either. A read that faulted
  would panic there, SError still unmasked: stage 0xb9 with 0x02 at 0x6009, and a restart about 10 s after
  the dark screen. And one more byte of the PMU's scratch is written, 0x6009, inside the range OK'd on
  2026-11-16: K1 zeroes 0x6008-0x600e as in every run and leaves 0x02 there; the byte goes out in step 8
  after that step's record at 0x6010 and after `wake_all=1`'s byte, and `m1_pmu_ok()` lets through, with
  `sep_pg=1`, one byte at 0x6009 that is the mark or has bit 7 set. So 0x6009 reads one of three ways after
  such a run: 0x00, the run didn't get as far as K1; 0x02, the byte never landed; or the byte, bit 7 set,
  with bit 6 saying the control word's bit 2 was set as found, bit 5 that the word read again had bit 2
  clear and every other bit as found, bit 4 that the SEP's power word read actual state 0, before the write
  (bit 6 set with bit 4 clear: the enclave read powered and the write was not made), and bits 3:0
  0x2922c0044's low nibble after it: the last sample of that word before the commit, bit 2 of which is the
  engine's first poll (awake the nibble reads 8). `m1-crumbs` reads 0x6008 and 0x6009 each by itself under
  the family's stages and step 7's own, by the rules `wake_all=1`'s byte has: a mark by the stage, a byte or
  a mark under another run's stage not at all, and no field of either if the rest of the seven holds
  anything a run of the family can't have left. `--clear` zeroes the seven when every field is its
  option's, or 0, with at least one there, and the rest are 0; anything else it leaves, and says so. At
  the load `m1_sp_init()` checks the word against the never-write list, refuses without SIO's power page or
  the engine's, maps the word's page and logs the three words awake; a word whose bit 2 reads clear
  already, or a SEP's power word that reads powered, is said in the log, with what step 7 will then do,
  and the load goes on.
  **The run is only made in a safe boot started without the SEP's driver, `pkexec sleep-drain-safe boot
  --no-sep`:** `apple_sep` writes the enclave's key store (xART, which macOS shares), which the safe boot's
  read-only root doesn't cover, and this run lets the SoC go down around the enclave. Four things hold
  that, for this option and for `disp_hs=1` alike. `run-floor` refuses, before anything is loaded or
  written, while `apple_sep` is loaded; and it refuses a boot that could still load it: the kernel's
  own `module_blacklist` parameter (`/sys/module/kernel/parameters/module_blacklist`, readable by root) has
  to list `apple_sep`, as it does in the boot the entry `--no-sep` selects. The command line's text isn't
  asked: an argument after `--` goes to init and a list over 1024 bytes is dropped, and `/proc/cmdline`
  shows both as if they counted. The
  module refuses to load while a driver is bound to the SEP's device (any node the live tree marks
  `apple,sep`; `m1_sep_bound()`). And at the first suspend's prepare, before the DCPs' shutdown or anything
  else that is one-way, it looks again (`m1_sep_recheck()`): a driver bound by then calls the replay off
  the way a write to `cancel` does, with nothing stopped and nothing written, and the suspend fails at
  prepare_late with its line in the log, so `run-floor` prints NO FLOOR. The stages are `a2_pre=1`'s and
  the keyboard's light is the witness, as in `--m1-a2-pre-blink`. It ends one of three ways: the light
  blinks until the restart, 90 s after the dark screen, as before; the light stops and the restart comes
  later than 90 s (B2 moved: it zeroes WD1's count at its op 223, so WD1's bite comes up to about 150 s
  after that; WD2's bite is 120 s after the commit); or the light stops and no restart comes: the entry
  may have completed, and nothing this recipe leaves armed is known to wake the Mac (nub-spmi0's enables
  are zeroed and every AP wake source but the PMU's is masked), so hold the power button. Run it with
  `--no-reading`, the nights off around it, nothing in the USB-C ports, and the keyboard filmed.
- `disp_hs=1` (`run-floor --m1-disp-hs`, on `disp_off=1` and `gates_mark=1`, never with `observe=1`, with
  `wake_all=1` and `sep_pg=1` or without them; the launcher's line is `--m1-sep-pg`'s with it;
  `e1-hook/last_look.h` has the four bytes) changes K3b, the display step, and nothing else. The reference
  driver (Ante042/linux, branch `t600x-s2r-reference`, `drivers/soc/apple/s2r.c`, `GPL-2.0-only OR MIT`,
  credited) says in `apple_s2r_disp_down()`: "macOS order and values per block: cpu0 0xf0400ff, 0xf0403f0,
  the fabric handshake +0x8014 = 1, +0x82bc = 1, 2, 4, then fe 0x3f0. The dispext0 handshake sets
  0x2922c0044 bit 2, which the PMGR S2R engine 1 waits for after GO (hv trace 1/11)." Bit 2 of 0x2922c0044
  is B2's first poll, which every run here has ended behind, and K3b lowers each stopped DCP's two power
  words with no handshake. With `disp_hs=1` K3b does, for each DCP that stopped at prepare, disp0's (the
  internal display's) first and dispext0's (the external's) second: if the DCP's FE power word reads
  actual state non-zero, the reference's set-up table to its fabric block (`m1_dh_setup()`); the CPU0
  power word down, the call every run makes; if that word read off and the FE word still reads actual
  state non-zero, the handshake (`m1_dh_shake()`); the FE power word down, as in every run.
  **What is new on the hardware:** two fabric blocks that nothing here has written or read before,
  0x38c000000 (disp0's) and 0x28a000000 (dispext0's), 0xc000 mapped of each. To each, up to 69 writes: the
  65 of its table, `disp_init_38c[]` or `disp_init_28a[]`, copied from the reference entry for entry
  (offsets 0x0 to 0x8428; "hv trace 1/11, macOS 13.7.8 boot": the set-up macOS writes when it powers the
  block up, which Linux leaves at reset values), and the handshake's four: block + 0x8014 := 1, then
  block + 0x82bc := 1, 2, 4. Each table already has those four writes in it, at its entries 29, 41, 53 and
  63 of the 65 (counted from 0; its last entry is another word), so a block gets them twice: once in the
  table, with its CPU0 word still up, and once after that word went down. Neither block is ever read.
  **The guard:** the reference's, "The fabric block is only there while its fe is powered: a write to it
  otherwise is an SError", which here would panic the safe boot: a block is written only right after its
  FE power word (disp0_fe 0x28e580328, dispext0_fe 0x28e080258) read actual state non-zero, once before
  the table and once more before the handshake, and each of those reads and the writes it guards are made
  with interrupts off on that CPU, so that nothing can come between them (`m1_ps_off()`'s polls are not).
  A DCP that didn't stop, or whose FE word reads off, gets no write to its block at all, and as in every
  run a CPU0 word that doesn't read off within 10 ms leaves its FE word, and now its handshake, alone.
  **No watchdog is armed at K3b, and the keyboard's light isn't on yet** (WD1's marker is step 4's, the
  light step 8's): a block write that faults where the panic's restart fails, or one that stalls, leaves
  the Mac dark with no light ever having blinked and no restart. The record then is stage 0x02 with K1's
  marks (0x02 at 0x6009, 0x01 at 0x600a). A dark Mac that never blinked fits that ending and doesn't prove
  it: `m1_ll_blink()` skips the light if FPWM0's power word doesn't come up in 10 ms, and the replay
  commits all the same. The stage and the marks kept in the PMU are the last record that landed, not
  proof of where the run stopped: K3b sends its bytes and its stage through the kernel's path, where a
  command that fails stops nothing after it, so a run can go on past K3b with stage 0x02 and both marks
  still there.
  **The record:** K3b reads 0x2922c0044 before the first DCP, right after each table it wrote, and after
  each DCP's place in the loop, stopped or not, and sends four bytes to the PMU's 0x600a-0x600d in one
  command through the kernel's path, before its own stage (inside the range OK'd on 2026-11-16; K1 leaves
  0x01 at 0x600a after the seven zeros and the other options' marks; `m1_pmu_ok()` lets through, with
  `disp_hs=1`, that mark by itself or four bytes at 0x600a that `dh_ok()` passes). 0x600a: bit 7; bit 6
  dispext0's handshake was made, bit 5 disp0's; bits 3:0 the word's low nibble before K3b touched
  anything. 0x600b: the nibble after dispext0's place in bits 7:4, after disp0's in bits 3:0. 0x600c: bit
  7; bit 6 dispext0's set-up table was written, bit 5 disp0's; bits 3:0 the nibble right after dispext0's
  table (0 if it wasn't written). 0x600d: bit 7; bits 3:0 the nibble right after disp0's table. 0x600d is
  best effort: bytes at 0x600d and 0x600e were lost across the restart in three runs, and one that reads
  0x00 is taken for lost. The sample after the table is what tells a table from a handshake, since the
  table has the handshake's writes in it. A handshake that wasn't made doesn't mean nothing was written:
  the table may have been (the CPU0 word didn't read off, or the FE word went off with it), and 0x600c's
  flags say.
  **The PMP's requests.** The run does withdraw the two displays' requests at the PMP, earlier than the
  reference does: `sio_on=1` makes `run-floor` run `sleep-drain-pmp release 0xc 0xd`, and
  `pmp-sleep-release` withdraws them at the suspend's noirq and waits for the ack (the reference's
  `PTD_PS_REQ` bits 0x1000 and 0x2000). So the set-up table is written with the request already withdrawn
  and the PMP still running (K4 stops it after K3b).
  **Where this differs from the reference:** it takes the displays down before `slpa`, and K3b is after
  it; it makes the handshake and the FE word's power-down even if the CPU0 word didn't read off, and here
  neither is made then; it takes down every block whose FE word reads powered, whatever its DCP's state,
  and here only the blocks of DCPs that stopped; it refuses to sleep if a display's word doesn't read off,
  and here the run goes on (byte B's bit 4 says); it also takes dispext1 and the GPU's port down; and its
  power-word values are macOS's (0xf0400ff, 0xf0403f0; 0x3f0), where `m1_ps_off()` clears the target as
  the power-state driver does.
  `m1-crumbs` reads the four bytes by themselves, as it reads the other two options' bytes: under K3's
  stage (still the stage when K3b sends them: they landed and K3b's own stage didn't), K3b's own, 0x84,
  and every later stage of such a run; K1's mark alone under none, K1's or K3's stage (the run stopped
  before the bytes, in K3b's writes perhaps), or under a later one (K3b ran and its command failed, which
  on the kernel's path stops nothing after it); under another run's stage neither is read. It says the
  handshake flags and the set-up flags separately, lists the samples in the order they were taken (K3b's
  start; after disp0's set-up; after disp0's place; after dispext0's set-up; after dispext0's place; and
  step 7's, from `sep_pg=1`'s byte, if that is there), and names the first of them in which bit 2 read
  set. It doesn't say which write set the bit, or that it stayed set: the samples can't. `--clear` zeroes
  the seven when each of the three fields is its option's or 0, with at least one there, and 0x600e is 0.
  At the load `m1_dh_init()` checks that the module's two tables agree on which block goes with which DCP
  and power words (`m1_dh[]` against `m1_dcp_info[]`: a check of the module's own tables, not of the live
  device tree), that every table entry is a whole word inside the span mapped, and every address K3b
  writes, the two handshake words included, against the never-write list; it refuses without the engine's
  page or an FE word's mapping, maps the two blocks, and logs whether each FE word reads powered. The
  SEP's device and the boot's policy are checked as for `sep_pg=1`, whose line this one has: the module
  refuses `disp_hs=1` by itself too while a driver is bound there, and looks again at prepare. The run is
  `--m1-sep-pg`'s otherwise, with its endings and the one above, and is only made in a safe boot started
  with `pkexec sleep-drain-safe boot --no-sep`.
- `sep_ro=1` (`run-floor --m1-sep-ro`, on `sep_pg=1`, never with `wake_all=1`; `e1-hook/last_look.h` has why,
  the evidence and the byte) is the tenth run's: **the one run made WITH the enclave's driver in the boot,
  its store read-only.** Run 9, in the boot without the driver, read the SEP's power word (0x28e580c00)
  0x1f0000ff in step 7, powered, so the wait bit was left: nothing gates an enclave that has no driver. With
  omacom's `apple_sep` loaded the word reads 0x1000030f awake, actual state 0. The reference refuses to sleep
  unless that word reads 0 and polls it for up to 5 s (`S2R_SEP_WAIT_MS`). Corey OK'd on 2026-11-24 the run
  with `apple_sep` loaded and `xart_writes=0`, the wait bit cleared only while the SEP's power word reads
  actual 0; clearing under a powered enclave is not OK'd, and `sp_writes()` stays as it is.
  What the parameter changes, and nothing else:
  - The module no longer refuses a driver bound to the SEP's device, at the load or at the first suspend's
    prepare, **if the binding itself says its store is read-only.** The parameter opts in and proves
    nothing (an insmod by hand passes the launcher by; the SEP's power word says nothing of the write
    policy; the driver's line in the kernel log and the words on the command line don't identify the
    binding that is there now). The proof is the SEP device's sysfs attribute `diag/xart` (omacom's
    `drivers/soc/apple/diag_shim.c`, as the brief for this build quotes it: `"enabled"` or `"disabled"`,
    registered at probe from the mode the store was opened in, removed at unbind; on this Mac
    `/sys/bus/platform/devices/396400000.sep/diag/xart`, which read `enabled` in the regular boot of
    2026-11-24). `m1_sro_proof()` asks it of the device itself, not of a path: a path is resolved in the
    task's own view of the filesystem, and says nothing about whose bytes come back (Pullfrog on #134).
    For every device under an `apple,sep` node, `m1_sro_xart()` takes the device's lock (waited for at the
    load; only tried at the prepare, where a lock that is held is no proof) and under it needs: a driver
    bound, named exactly `apple_sep`; the device's own node in sysfs (`dev->kobj.sd`) with a directory
    `diag` that is an attribute group of the device's own kobject (the driver imports
    `devm_device_add_group()`; a directory owned by any other kobject is no proof); in it a file `xart` of
    an attribute's size; and that attribute's show, called as sysfs calls it (the device kobject's
    `sysfs_ops`, with the `struct attribute` the node carries) into a zeroed page, returning exactly
    `disabled` and a newline with the string's end after it (`sro_xart_ro()`). Both node references are
    put back, the page freed and the lock dropped on every way out. There has to be at least one such
    device, and one that has a driver and doesn't pass is no proof for all. No driver, another driver, no
    such node, a lock that can't be had at the prepare, `enabled` or anything else: the load is refused
    (-EBUSY), and at the prepare the replay is called off as it is for a bound driver without `sep_ro=1`
    (`replay_done` 2, nothing stopped or written, prepare_late fails the suspend and the Mac wakes).
    Why the show is called and the file not opened: the kernel exports `kernfs_find_and_get_ns()` and
    `kernfs_put()` to GPL modules, and nothing that ties an open file to its kernfs node
    (`kernfs_node_from_dentry()` isn't exported), so a file opened by path couldn't be proved to be that
    node. The lock stands in for the active reference a read through sysfs holds: a driver is unbound, and
    its devres group removed, only under that lock. It rests on two things about the driver's show: it
    takes no lock of the device's (it would never return), and it reads a flag and touches no hardware.
    Both hold for the `apple-sep.ko` of 7.2.11-15-1-ARCH by its disassembly (2026-11-24; the source wasn't
    read): `xart_show()` loads one byte of the module's own data, picks one of two strings and calls
    `sysfs_emit()`. A later build of the driver isn't covered by that: look again after a kernel update
    (`objdump -dr --disassemble=xart_show` on the unpacked module).
  - Both places then poll the SEP's power word for actual state 0, up to 5 s in sleeping steps of 10 ms
    (`m1_sro_wait()`): `m1_sp_init()` refuses the load if it never reads 0 ("the enclave isn't power-gated
    in this boot ... the run would learn nothing"), and the prepare calls the replay off the same way,
    before the DCPs' shutdown.
  - Step 7 waits for nothing. It runs with interrupts off after WD1's marker is armed, and step 8 takes the
    marker to stand only if its count has run on by no more than 5 s. `m1_sp_clear()` reads the SEP's word
    once, as in every `sep_pg=1` run, and writes only if `sp_writes()` says so. With `sep_ro=1` it also
    reads 0x2922c0044 right before that decision (the word is read right after the control word's
    read-back in every `sep_pg=1` run), so that the clear lies between two samples of one interrupts-off
    stretch. That first sample goes to the PMU's 0x6008 as `0x80 | nibble` (`sro_byte()`), in step 8, after
    that step's record and after 0x6009's byte (the run's first result goes first: a command that fails
    stops every later one); K1 leaves 0x10 there until it lands (`SRO_ARMED`), and
    `m1_pmu_ok()` lets a `sep_ro=1` run write only those there (`sro_ok()`). It is in `wake_all=1`'s place,
    which is why the two never load together.
  `e1-hook/m1-crumbs` reads 0x6008 under the stages it reads 0x6009 under, sets the two samples side by
  side when both landed, and says "bit 2 clear before the write and set after it" only if the write was
  made, the clear read back, the first sample had bit 2 clear and the second had it set. A clear that was
  skipped (the enclave read powered) or that didn't read back is said to be no test of the wait bit, and
  nothing is concluded from the samples. Under step 7's own stage (0x39) a byte that landed is read with a
  line saying what that stage establishes: no newer stage was kept, and nothing about which of the two
  bytes could have landed. K1's mark still at 0x6009 with 0x6008's byte landed is said to be a command
  that was acked and lost, since 0x6008's goes out after it. And the gate byte's own line (0x6016) gives
  the reading of 0x28e5a0050 and leaves what follows to PLAN.md whenever the record is a later recipe's
  (`pw_noread=1`'s mark, or `sep_ro=1`'s, `sep_pg=1`'s or `disp_hs=1`'s bytes or marks): only
  `--m1-gates`' own record still says `a2_pre=1` is next. The proof at the prepare is asked by the task
  that asked for the suspend, with user space frozen and the devices suspended: it may allocate there
  (K3b's and K5's steps do), and it tries each lock and waits for none. No MMIO write is new.
- `pw_noread=1` (`run-floor --m1-sep-ro`, on `ps_words=1` and `atc_down=1`; never with `sio_down=1`,
  `pcie_down=1`, `last_look=1` or `probe=1`, whose own steps read one of the words or APCIE_GP 0x28e080150
  after step 5: the load is refused, `m1_pwn_reader()`; `ll_wake=1`, `gate_look=1` and `ll_retn=1` need
  `last_look=1`) leaves out every read of the four
  fabric-group power words once they are written: PCIE0_REF (0x28e080138), APCIE_GP_SYS (0x28e080320),
  AFNC1_LW2 (0x28e080220) and AFNC2_LW1 (0x28e580160). The reference says: "reading the three apcie PS words
  this late on the way down left the SoC stuck before sleep in 4 runs out of 4" (its three are 0x28e080138,
  0x28e080150 and 0x28e080320; this recipe writes two of them and never touched 0x28e080150 in this line).
  `m1_pw_write()` makes the same twenty writes, each with its `dsb`, in the same order, and reads back only
  the other sixteen. And step 8's own reads: without `a2_pre=1` that step makes `gates_mark=1`'s, the six
  gate-status words (0x28e5a0050, 0x28e5a0064, 0x28e5a030c, 0x28e09c310, 0x28e09c338, 0x28e09c34c: not
  power words) and then AFNC2_LW1, the fourth of the group; with `pw_noread=1` the six are read and
  AFNC2_LW1 is not. The loads still read all four awake. The record says so: 0x6013's top two bits are
  0x80 (`M1_PWN_MARK`) in `atc_down=1`'s 0x40's place, with bit 5, the fabric group's, always clear, so the
  byte is 0x80 to 0x9f and never 0xa5; and 0x6016's bit 6 stays clear. `m1-crumbs` then says "not read
  (pw_noread=1)" for the group and for the lane, never "read back as written" or "did NOT read". That mark
  has one pattern left to use, which is why the option needs `atc_down=1`: the mark can't also say whether
  the six were written.
- `run-floor --m1-sep-ro` is `--m1-disp-hs`'s line **without `a2_pre=1`** and with `sep_ro=1 pw_noread=1`:
  `... gates_mark=1 atc_down=1 blink=1 sep_pg=1 disp_hs=1 sep_ro=1 pw_noread=1`. Without `a2_pre=1` step 8
  makes none of A2's three moves; its place is `gates_mark=1`'s reads, above, and the stages from step 8 on
  are `--m1-gates'` (0xcb, then 0xd5 or 0xd4), with the gate byte at 0x6016. `blink=1` needs only
  `gates_mark=1` (and neither `sio_down=1` nor `sio_steps=1`), so the light is as before. The option's three
  conditions replace `--m1-sep-pg`'s two, before anything is loaded or written, `wd2-bite`'s store included:
  `apple_sep` is loaded; at least one device is bound to it
  (`/sys/bus/platform/drivers/apple_sep/<device>`); and every such device's `diag/xart` reads exactly
  `disabled` and a newline. A driver's directory that isn't there, no bound device, no attribute, one that
  can't be read, `enabled` or anything else refuses, with what was read. The kernel's module blacklist
  isn't asked. **The run is only made in a safe boot started with `pkexec sleep-drain-safe boot --sep-ro`**
  (the regular line with `apple_sep.xart_writes=0 apple_sep.provision_keybag=0`; `sleep-drain-safe status`
  there says whether the driver took it). It ends as `--m1-disp-hs` does, the dark never-blinked ending
  included (K1's marks are then 0x10 at 0x6008, 0x02 at 0x6009 and 0x01 at 0x600a). In that boot the
  enclave's driver is there with its store read-only: Touch ID may fail, and the password works. Its label
  is `floor-m1-sep-ro`.
- `ll_retn=1` (`run-floor --m1-ll-retn`, on `ll_wake=1`, never with `gate_look=1`; the launcher's line is
  `--m1-atc-down`'s with it; `e1-hook/last_look.h` has why, the record's form and the stages) changes how CPU0
  takes its very last WFI, and nothing before it. CYC_OVRD's bit 0 is XNU's `disWfiRetn`, "disable WFI
  retention": the recipe sets it (0x3000001), as macOS does, so a WFI can't come back at the next instruction
  with its state kept; Linux's own idle on this chip leaves it clear and comes back on the core's timer. With
  `ll_retn=1` CPU0 writes 0x3000000 (bits 25:24 alone) before that WFI, ACC_OVRD's deep-sleep fields set as in
  every run and `ll_wake=1`'s timer 25 s out. A return with nothing pending (ISR_EL1 0: Linux's idle loops on
  those) and the timer not reading as firing is counted and the WFI taken again, however many there are, until
  30 s (the timer's 25 s and 5) after CPU0 first took the WFI, with nothing read but ISR_EL1, the timer's
  control and the counter, and nothing sent. A return at which the timer reads as firing ends the phase
  whatever ISR_EL1 reads, so that a timer which doesn't show there can't pass for a WFI that never held. The bound is a time
  and not a count: a WFI that returned at once every time would use up a count in well under a second, and
  the look would come before B2 had done anything.
  At the first return with something pending or the timer firing, or the first past that time with neither, CPU0 makes its look, once: the clock,
  ISR_EL1, the timer's status, its control written 0, and three commands to 0x6008-0x600e in `last_look=1`'s
  order, each before the next one's reads, in a form of this run's own. 0x6008 and 0x6009 are `last_look=1`'s
  (the count takes the look's return as the first), but for 0x6008's bits 3:2, which in this form alone say how
  many empty returns came before the look: 0 none, 1 one to nine, 2 ten to 999, 3 a thousand or more. That is
  a count of the returns after which the WFI was taken again, the look's own not among them, and not a measure
  of how long the WFI held: a thousand at once and then 24 s of holding is code 3 too, and `m1-crumbs` concludes
  nothing about residency from it (they are `gate_look=1`'s two bits in the
  other forms; this run refuses it, and the later returns' sends of 0x6008 keep them); 0x600a is the low byte of the E complex's state word,
  0x28e09a000 (0x44 awake since boot; a down sequence ends on 0x1a, an up sequence on 0x40); 0x600b has
  0x2922c0044's low nibble, 0x2922c0040's bits 1:0 (3 awake) in bits 5:4, bit 6 if the state word, 0x2922c0040,
  0x2922c0044, PHASE or EACC's word has a bit the bytes don't carry, and bit 7 if the look was made at the time
  bound, with nothing pending and the timer not firing (with code 0 in 0x6008's bits 3:2, a WFI that held past
  the bound and returned once with nothing pending); 0x600c has bit 7, the bits 6:4 of EACC's soc-cluster word, 0x28e098000, and PHASE's low nibble;
  0x600d and 0x600e are `last_look=1`'s, best effort (they were lost across the restart in three runs). Then
  CPU0 sets bit 0 again, 0x3000001, and goes on as a `last_look=1` run does after a first return. **New on
  the hardware: one system register's value on CPU0, CYC_OVRD's bit 0 clear for its last WFI until the look,
  and two reads CPU0 hasn't made in this state**, the state word and EACC's word, the last reads of the second
  command (the observer read both after a commit; the load maps their page if nothing has, and reads and logs
  them awake with the other two complexes'). Nothing else: no MMIO write is new (the tests' count of the
  module's MMIO write calls is unchanged, 74), the other cores' deep WFI is as it was, and K1, the undo and
  `m1_pmu_ok()` are `last_look=1`'s. Stages of its own, 0x94, then 0x9c or 0x98, by which `m1-crumbs` knows
  the form: under them it reads each field as read and names it, and all 0 as "the WFI didn't return, or the
  first command failed" (a WFI that returned only with nothing pending, with the restart before the time
  bound, leaves that too); it applies this form under no other run's stage and no other run's form under these; the alarm
  (20 s), the timer, the light and the marker (70 s) read as `ll_wake=1`'s. `m1_ll_wfi()` keeps x18 to x30 on
  the stack across the WFI, as the kernel's `apple_cpu_deep_wfi()` does, and like it needs the stack pointer
  and the system registers to survive; whether they do with the recipe's ACC_OVRD is part of what the run
  asks. Run it with `--no-reading`, the nights off around it and nothing in the USB-C ports, and film the
  keyboard.
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
- `run-floor --quick` ahead of any recipe makes the reading 1 minute; `--no-reading` in its place takes none (one sample, the save, the suspend). `13` is the floor with the CPUs left up.

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
