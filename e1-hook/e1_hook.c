// SPDX-License-Identifier: GPL-2.0
/*
 * e1_hook: E1's module (PLAN.md, A5). It registers s2idle ops of its own, so
 * that its prepare_late runs at s2idle's step 4. In suspend_enter()
 * (kernel/power/suspend.c at omacom/linux-aurora 4fdd0fc5, aurora14's):
 *
 *   1. dpm_suspend_late()          every device's suspend_late
 *   2. s2idle_ops->prepare         here: log, and read B0AV (the control)
 *   3. dpm_suspend_noirq()         suspend_device_irqs(), every device's
 *                                  suspend_noirq (async ones included), genpd
 *                                  switching each domain off after its last
 *                                  device
 *   4. s2idle_ops->prepare_late    here: log, and read B0AV; at the first one,
 *                                  the replay that replay asks for (below)
 *      suspend_test(TEST_PLATFORM) pm_test=platform waits here and resumes
 *   5. s2idle_loop()
 *
 * begin comes before dpm_suspend_start(); on the way back, restore_early comes
 * before dpm_resume_noirq(), restore before dpm_resume_early(), end after
 * dpm_resume_end(). Each op logs one line, with its time on the clock ftrace
 * uses by default, so that e1-hook/check can line prepare_late up against the
 * power:* trace events (one line, wrapped here):
 *
 *   e1_hook: op=prepare_late n=1 t=1234.567890 t_ns=1234567890123
 *            key=B0AV ret=0 mv=12634 read_ns=812345
 *
 * t_ns is trace_clock_local() at the op's entry. That's sched_clock(), which is
 * printk's clock here too (arm64 has no unstable sched_clock), and t is the
 * same rounded to microseconds, as the trace prints it. B0AV is the battery
 * voltage in mV (ui16), read with macsmc's ordinary read once at load, then at
 * prepare and at prepare_late: ret is the read's return code, read_ns how long
 * it took.
 *
 * Read-only parameters, in /sys/module/e1_hook/parameters/:
 *   begin_calls, prepare_calls, prepare_late_calls, restore_early_calls,
 *   restore_calls, end_calls      how many times each op has run
 *   prepare_t_ns, prepare_ret, prepare_mv, prepare_read_ns
 *   late_t_ns, late_ret, late_mv, late_read_ns
 *                                 the last read at prepare and at prepare_late,
 *                                 as logged; ret is 1 until the op has run
 *
 * With replay=0 (the default, and what e1-hook/check loads: E1's first check,
 * the hook point, RESEARCH.md "Before E1") it never writes an SMC key and
 * touches no MMIO. The replays, E1a's and the floor's, are below. Every op
 * returns 0, so it never fails or aborts a suspend, but for one case: with
 * replay 13 or 15, a prepare_late whose replay stops short fails the suspend
 * with -EIO (below). The floor's prepare_late never returns at all. .check and
 * .wake stay NULL: s2idle_loop() calls .wake instead of pm_wakeup_pending()
 * when it's set.
 *
 * Lab only, never for upstream: s2idle_set_ops() is declared in
 * include/linux/suspend.h but not exported (it isn't in Module.symvers). The
 * module finds its address with a kprobe registered disabled, so nothing is
 * armed, and unregistered at once; it refuses unless that address is the
 * function's entry. KASLR, module signing, lockdown and kCFI are off on this
 * kernel (CONFIG_ARM64_BTI_KERNEL=y, but the M1 has no BTI). Nothing else
 * registers s2idle ops here: the only callers are ACPI's, and CONFIG_ACPI is
 * off (the build fails if it isn't). So loading replaces nothing, and
 * unloading sets the ops back to NULL. s2idle_set_ops() takes
 * system_transition_mutex, which a suspend holds throughout: neither call can
 * land mid-suspend, and once the NULL call returns, no op can run.
 *
 * The SMC is found and pinned as smc_peek does: macsmc stays loaded while this
 * module is, and each read holds the SMC device's lock and checks that macsmc
 * is still bound to it. From an op that lock is only tried (-EBUSY if it's
 * held), but the read itself still waits for macsmc's mutex and up to 500 ms
 * for the SMC's reply, and after a timeout holds that mutex for DRAIN_MS more,
 * as smc_peek does (drain_after_timeout()). So an op can hold a suspend up for
 * about 1.5 s, though a read never fails it.
 *
 *   make -C e1-hook && pkexec e1-hook/check       (in a safe boot)
 *   pkexec e1-hook/run-e1a, pkexec e1-hook/run-floor   (the same)
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/bits.h>
#include <linux/build_bug.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/kallsyms.h>
#include <linux/kprobes.h>
#include <linux/mfd/macsmc.h>
#include <linux/module.h>
#include <linux/notifier.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/preempt.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <linux/suspend.h>
#include <linux/time64.h>
#include <linux/trace_clock.h>
#include <asm/barrier.h>
#include <asm/daifflags.h>

#if IS_ENABLED(CONFIG_ACPI)
#error "ACPI registers s2idle ops of its own, and this module would replace them"
#endif

#define SMC_COMPATIBLE "apple,smc"
#define SMC_DRIVER "macsmc"
/* After a timeout, how long the SMC's mutex is held for its late reply. */
#define DRAIN_MS 1000

typedef void (*s2idle_set_ops_t)(const struct platform_s2idle_ops *ops);

static s2idle_set_ops_t set_ops;
static struct platform_device *smc_pdev;
static struct apple_smc *smc;
static struct module *smc_owner;

static unsigned int begin_calls, prepare_calls, prepare_late_calls;
static unsigned int restore_early_calls, restore_calls, end_calls;
module_param(begin_calls, uint, 0444);
module_param(prepare_calls, uint, 0444);
module_param(prepare_late_calls, uint, 0444);
module_param(restore_early_calls, uint, 0444);
module_param(restore_calls, uint, 0444);
module_param(end_calls, uint, 0444);
MODULE_PARM_DESC(prepare_late_calls, "How many times prepare_late has run (each op has a *_calls)");

static unsigned long long prepare_t_ns, prepare_read_ns;
static int prepare_ret = 1;
static unsigned int prepare_mv;
module_param(prepare_t_ns, ullong, 0444);
module_param(prepare_ret, int, 0444);
module_param(prepare_mv, uint, 0444);
module_param(prepare_read_ns, ullong, 0444);
MODULE_PARM_DESC(prepare_ret, "The last B0AV read at prepare: 0, -errno, or 1 before the first");

/*
 * The replays (PLAN.md, A5): at the first prepare_late only, maralcbr's
 * one-way replay of macOS 15.6.1's sleep entry on J314s, ported from their
 * lab_s2r_run() (maralcbr/linux 37be2668e36f, drivers/power/reset/
 * macsmc-reboot.c L789-914; GPL-2.0-only OR MIT there, used here under
 * GPL-2.0). replay takes their lab_s2r_floor bits:
 *   bit 0 (1)   the SMC sleep keys
 *   bit 1 (2)   every CPU powered down, never to return
 *   bit 2 (4)   the PMP request words
 *   bit 3 (8)   the 74 post-slpa writes
 * with lab_post_fast and lab_eh_early at their defaults (true, L341 and L329),
 * and none of their other bits. Allowed: 0 (none), 5 (E1a), 13 (E1a and the
 * post-slpa writes, the CPUs left up) and 15 (the floor: all four). Neither
 * bit 1 nor bit 3 can be undone, so 13 and 15 also need oneway=1. In their
 * order:
 *   bit 3   the table's watchdog and error-handler entries (L823-831), each
 *           with writel, then dsb(sy)
 *   bit 2   4 to 0x28e3d8328 (pmgr reg[41] + 0x18328)       (L839-840)
 *   bit 0   MBSE = slpw, pmVC = 0x100, WKTP = 2              (L843-850)
 *   bit 2   1 << 61 to 0x28e3d0860 (SOC-DEV-DVFS + 4), then 1 to 0x28e3d8328
 *                                                            (L851-854)
 *   bit 0   MBSE = slpa                                      (L875-879)
 *   bit 3   the table's other entries, back to back, with writeq_relaxed or
 *           writel_relaxed by width, then dsb(sy)            (L880-881)
 *   bit 3   500 ms, reading AFNC2's and AFNC3's power states 100 ms in and
 *           at the end                                       (L883-894)
 *   bit 1   every other CPU through smp_call_function(), then this one, each
 *           powering itself down for good                    (L895-898)
 *
 * It stops at the first key write that fails: nothing after it runs, so a
 * broken sequence never reaches the PMP words' second half, MBSE=slpa, the
 * post-slpa writes or the CPUs' power-down. (Their lab_s2r_run() carries on
 * past a failed key, L843-850, and always returns 0.) With 5, the sleep then
 * stays an ordinary s2idle that the power key wakes, and the log says what
 * failed. With 13 and 15 the watchdog and error-handler writes have been made
 * by then, and the power-key reset that ends a one-way sleep would wipe the
 * log with the safe boot's RAM. So when their replay stops short, or can't
 * start (the SMC's device lock busy, macsmc unbound), prepare_late fails the
 * suspend with -EIO: the PM core resumes at once, and the Mac wakes with its
 * log rather than sleeping as if the replay had run. That resume, after
 * those writes, is untried. With bit 3, nothing is printed from MBSE=slpa on,
 * unless slpa fails, as in their fast mode (L822, L877-878).
 *
 * 13 and 15 are armed for one suspend: one that ends without reaching
 * prepare_late (tasks that won't freeze, a device's suspend error, an early
 * wakeup, pm_test=freezer or devices) calls them off (replay_done=2), so that
 * they can't run at a later suspend, away from their reading. They load only
 * in a safe boot, with sleepdrain.safe on the kernel command line, since the
 * reset that ends them could damage a writable disk.
 *
 * Their 500 ms is kept, with its two reads: their fast mode always makes it
 * (it maps the AFNC page whenever it maps the table, L816), so it's part of
 * the timing this copies, though the reads change nothing. What they read is
 * in afnc, readable with 13 only. Left out, as diagnostics: their other reads
 * of the watchdog (lab_dump_wdt(), L798) and of AFNC2/3 (lab_afnc(), L841,
 * L859, L862, L867), their prints between writes, and their lab_delay_stage
 * delays (off by default). Their prepare op (L481-489) only reads too.
 * Nothing else in their flow touches the hardware with these four bits.
 * Where they map each page at prepare_late and skip a write whose page didn't
 * map (L808-821, L353, L825), this maps them all at load and refuses to load
 * if one fails. One way: there's no undo, and with bit 1 no resume.
 */
static unsigned int replay;
module_param(replay, uint, 0444);
MODULE_PARM_DESC(replay, "LAB ONLY, safe boot only: at the first prepare_late, maralcbr's replay: 0 none, 5 their keys and PMP words (E1a), 13 those and the 74 post-slpa writes, 15 those and every CPU powered down for good (the floor); 13 and 15 need oneway=1");
static bool oneway;
module_param(oneway, bool, 0444);
MODULE_PARM_DESC(oneway, "Must be 1 for replay=13 or 15, which can't be undone (15 never resumes); ignored for 0 and 5");
static unsigned int replay_done;
module_param(replay_done, uint, 0444);
MODULE_PARM_DESC(replay_done, "0 not yet, 1 the replay has run at a prepare_late, 2 called off (13 and 15: a suspend ended before prepare_late)");
static int replay_ret[4] = { 1, 1, 1, 1 };
static int replay_nret = 4;
module_param_array(replay_ret, int, &replay_nret, 0444);
MODULE_PARM_DESC(replay_ret, "MBSE=slpw, pmVC, WKTP, MBSE=slpa: 0, -errno, or 1 if not sent");
static unsigned int eh_writes, post_writes;
module_param(eh_writes, uint, 0444);
module_param(post_writes, uint, 0444);
MODULE_PARM_DESC(eh_writes, "Bit 3: the post-slpa table's watchdog and error-handler writes, made before the keys");
MODULE_PARM_DESC(post_writes, "Bit 3: the post-slpa table's other writes, made right after MBSE=slpa");

#define E1_PMGR_SLEEP_REQ	0x28e3d8328ULL
#define E1_PMP_DVFS4		0x28e3d0860ULL
static void __iomem *sleep_req_page, *pmp_dvfs_page;

/*
 * The never-write list: nothing here may write either address, nor any other
 * byte of the 8 from it. 0x28e0802d8 is PMP's power state, ps_pmp, whose reset
 * crashed a T6020 (RESEARCH.md, "PMGR power states on this Mac"); 0x28e700000
 * is on the lab's list as well. The PMP words are checked here at build time;
 * the post-slpa table at load, before anything else, whatever replay is: a
 * table entry on the list refuses the module.
 */
#define E1_NEVER_PS_PMP		0x28e0802d8ULL
#define E1_NEVER_28E700000	0x28e700000ULL
static_assert((E1_PMGR_SLEEP_REQ & ~7ULL) != E1_NEVER_PS_PMP &&
	      (E1_PMGR_SLEEP_REQ & ~7ULL) != E1_NEVER_28E700000);
static_assert((E1_PMP_DVFS4 & ~7ULL) != E1_NEVER_PS_PMP &&
	      (E1_PMP_DVFS4 & ~7ULL) != E1_NEVER_28E700000);

static bool e1_never_write(u64 addr)
{
	u64 block = addr & ~7ULL;

	return block == E1_NEVER_PS_PMP || block == E1_NEVER_28E700000;
}

/*
 * maralcbr's lab_post_slpa[] (L235-311), entry for entry: macOS 15.6.1's MMIO
 * writes on J314s between MBSE=slpa and the CPUs' power-down, in the order of
 * their hypervisor trace. Address, width in bytes, value; the comments are
 * theirs.
 */
static const struct e1_mmio_write {
	u64 addr;
	u8 width;
	u64 val;
} e1_post_slpa[] = {
	{ 0x580080000ULL, 4, 0x8fULL },	/* apcie[2]+0x00 */
	{ 0x580080000ULL, 4, 0x8cULL },	/* apcie[2]+0x00 */
	{ 0x2922b000cULL, 4, 0x0ULL },	/* wdt[0]+0x0c */
	{ 0x282000a00ULL, 4, 0x0ULL },	/* error-handler[7]+0xa00 */
	{ 0x304000a00ULL, 4, 0x0ULL },	/* error-handler[8]+0xa00 */
	{ 0x383000a00ULL, 4, 0x0ULL },	/* error-handler[9]+0xa00 */
	{ 0x402000a00ULL, 4, 0x0ULL },	/* error-handler[10]+0xa00 */
	{ 0x28e0ad000ULL, 4, 0x0ULL },	/* error-handler[2]+0x2d000 */
	{ 0x28e0f8000ULL, 4, 0x0ULL },	/* error-handler[2]+0x78000 */
	{ 0x28e0f9000ULL, 4, 0x0ULL },	/* error-handler[2]+0x79000 */
	{ 0x28e0fa000ULL, 4, 0x0ULL },	/* error-handler[2]+0x7a000 */
	{ 0x28e0fb000ULL, 4, 0x0ULL },	/* error-handler[2]+0x7b000 */
	{ 0x28e0fc000ULL, 4, 0x0ULL },	/* error-handler[2]+0x7c000 */
	{ 0x28e0fd000ULL, 4, 0x0ULL },	/* error-handler[2]+0x7d000 */
	{ 0x28e070000ULL, 4, 0x0ULL },	/* error-handler[1]+0x70000 */
	{ 0x28e5d8000ULL, 4, 0x0ULL },	/* pmgr[2]+0x58000 */
	{ 0x210e20660ULL, 8, 0x0ULL },	/* pmgr[8]+0x660 */
	{ 0x210e48080ULL, 8, 0x0ULL },	/* pmgr[10]+0x8080 */
	{ 0x211e20660ULL, 8, 0x0ULL },	/* pmgr[15]+0x660 */
	{ 0x211e48080ULL, 8, 0x0ULL },	/* pmgr[17]+0x8080 */
	{ 0x212e20660ULL, 8, 0x0ULL },	/* pmgr[24]+0x660 */
	{ 0x212e48080ULL, 8, 0x0ULL },	/* pmgr[26]+0x8080 */
	{ 0x58c008014ULL, 4, 0x1ULL },	/* pmgr[56]+0x8014 */
	{ 0x58c0082bcULL, 4, 0x1ULL },	/* pmgr[56]+0x82bc */
	{ 0x58c0082bcULL, 4, 0x2ULL },	/* pmgr[56]+0x82bc */
	{ 0x58c0082bcULL, 4, 0x4ULL },	/* pmgr[56]+0x82bc */
	{ 0x210e20020ULL, 8, 0x100101ULL },	/* pmgr[8]+0x20 */
	{ 0x211e20020ULL, 8, 0x100101ULL },	/* pmgr[15]+0x20 */
	{ 0x210e40250ULL, 8, 0xff092df01000ULL },	/* pmgr[10]+0x250 */
	{ 0x211e40250ULL, 8, 0x10ff092df01000ULL },	/* pmgr[17]+0x250 */
	{ 0x212e40250ULL, 8, 0x10ff092df01000ULL },	/* pmgr[26]+0x250 */
	{ 0x210e40240ULL, 8, 0x10000000ULL },	/* pmgr[10]+0x240 */
	{ 0x211e40240ULL, 8, 0x10000000ULL },	/* pmgr[17]+0x240 */
	{ 0x212e40240ULL, 8, 0x10000000ULL },	/* pmgr[26]+0x240 */
	{ 0x210e48400ULL, 8, 0x5b2002df02000ULL },	/* pmgr[10]+0x8400 */
	{ 0x211e48400ULL, 8, 0x5b2005df05200ULL },	/* pmgr[17]+0x8400 */
	{ 0x212e48400ULL, 8, 0x5b2005df05200ULL },	/* pmgr[26]+0x8400 */
	{ 0x28e5e0000ULL, 4, 0x0ULL },	/* pmgr[2]+0x60000 */
	{ 0x28e5e4000ULL, 4, 0x0ULL },	/* pmgr[2]+0x64000 */
	{ 0x28e0c8200ULL, 4, 0x0ULL },	/* error-handler[2]+0x48200 */
	{ 0x28e0c8400ULL, 4, 0x0ULL },	/* error-handler[2]+0x48400 */
	{ 0x28e0c8600ULL, 4, 0x0ULL },	/* error-handler[2]+0x48600 */
	{ 0x28e0c8000ULL, 4, 0x0ULL },	/* error-handler[2]+0x48000 */
	{ 0x210e20020ULL, 8, 0x40000500101ULL },	/* pmgr[8]+0x20 */
	{ 0x211e20020ULL, 8, 0x40000500101ULL },	/* pmgr[15]+0x20 */
	{ 0x212e20020ULL, 8, 0x40000500101ULL },	/* pmgr[24]+0x20 */
	{ 0x210e20020ULL, 8, 0x40000500101ULL },	/* pmgr[8]+0x20 */
	{ 0x211e20020ULL, 8, 0x40000500101ULL },	/* pmgr[15]+0x20 */
	{ 0x212e20020ULL, 8, 0x40000500101ULL },	/* pmgr[24]+0x20 */
	{ 0x28e20c800ULL, 4, 0x557ULL },	/* pmgr[38]+0x800 */
	{ 0x28e20c800ULL, 4, 0x775ULL },	/* pmgr[38]+0x800 */
	{ 0x28e0801e8ULL, 4, 0x0ULL },	/* error-handler[2]+0x1e8 */
	{ 0x28e098000ULL, 4, 0x0ULL },	/* error-handler[2]+0x18000 */
	{ 0x28e098004ULL, 4, 0x20ULL },	/* error-handler[2]+0x18004 */
	{ 0x28e098008ULL, 4, 0x20ULL },	/* error-handler[2]+0x18008 */
	{ 0x28e09800cULL, 4, 0x0ULL },	/* error-handler[2]+0x1800c */
	{ 0x28e5e4000ULL, 4, 0x0ULL },	/* pmgr[2]+0x64000 */
	{ 0x28e098014ULL, 4, 0x0ULL },	/* error-handler[2]+0x18014 */
	{ 0x28e098018ULL, 4, 0x20ULL },	/* error-handler[2]+0x18018 */
	{ 0x28e0c8400ULL, 4, 0x0ULL },	/* error-handler[2]+0x48400 */
	{ 0x28e098020ULL, 4, 0x0ULL },	/* error-handler[2]+0x18020 */
	{ 0x28e580188ULL, 4, 0x400024fULL },	/* pmgr[2]+0x188 */
	{ 0x28e038164ULL, 4, 0x80100000ULL },	/* error-handler[1]+0x38164 */
	{ 0x28e03816cULL, 4, 0x80100000ULL },	/* error-handler[1]+0x3816c */
	{ 0x28e038168ULL, 4, 0x80100000ULL },	/* error-handler[1]+0x38168 */
	{ 0x28e03815cULL, 4, 0x80100000ULL },	/* error-handler[1]+0x3815c */
	{ 0x28e038170ULL, 4, 0x80100000ULL },	/* error-handler[1]+0x38170 */
	{ 0x28e224000ULL, 4, 0x219ULL },	/* pmgr[46]+0x00 */
	{ 0x28e224000ULL, 4, 0x21bULL },	/* pmgr[46]+0x00 */
	{ 0x28e224000ULL, 4, 0x8000021aULL },	/* pmgr[46]+0x00 */
	{ 0x28e224000ULL, 4, 0x21aULL },	/* pmgr[46]+0x00 */
	{ 0x28e224000ULL, 4, 0x21aULL },	/* pmgr[46]+0x00 */
	{ 0x28e224000ULL, 4, 0x21aULL },	/* pmgr[46]+0x00 */
	{ 0x2925c4000ULL, 4, 0x1ULL },	/* pmgr[36]+0x4000 */
};

static_assert(ARRAY_SIZE(e1_post_slpa) == 74);

/* Each entry's own non-posted mapping, at its address (bit 3 only). */
static void __iomem *post_map[ARRAY_SIZE(e1_post_slpa)];

/*
 * The PMGR page with AFNC2's (+0x160) and AFNC3's (+0x178) power states, which
 * their 500 ms reads (bit 3 only; mapped as their lab_afnc_map, L816).
 */
#define E1_AFNC_PAGE		0x28e580000ULL
static_assert(!(E1_AFNC_PAGE & ~PAGE_MASK));
static void __iomem *afnc_map;
static unsigned int afnc[4];
static int afnc_n = 4;
module_param_array(afnc, uint, &afnc_n, 0444);
MODULE_PARM_DESC(afnc, "Bit 3: AFNC2's and AFNC3's power states 100 ms after the post-slpa writes, then 500 ms after; readable with 13 only");
/* How many of the table's entries e1_post_is_eh() picks, counted at load. */
static unsigned int post_eh;

/*
 * maralcbr's lab_post_is_eh() (L333-339): the watchdog (WD0's control) and
 * error-handler entries, which their default writes before the keys. Its
 * range also takes in 0x28e0801e8, AFR's power state.
 */
static bool e1_post_is_eh(const struct e1_mmio_write *w)
{
	u64 a = w->addr;

	return a == 0x2922b000cULL || (a & 0xfff) == 0xa00 ||
	       (a >= 0x28e030000ULL && a < 0x28e100000ULL);
}

static unsigned long long late_t_ns, late_read_ns;
static int late_ret = 1;
static unsigned int late_mv;
module_param(late_t_ns, ullong, 0444);
module_param(late_ret, int, 0444);
module_param(late_mv, uint, 0444);
module_param(late_read_ns, ullong, 0444);
MODULE_PARM_DESC(late_t_ns, "When prepare_late last began, by trace_clock_local(), in ns");
MODULE_PARM_DESC(late_ret, "The last B0AV read at prepare_late: 0, -errno, or 1 before the first");
MODULE_PARM_DESC(late_mv, "The value it read: the battery voltage in mV");
MODULE_PARM_DESC(late_read_ns, "How long that read took, in ns");

/*
 * macsmc has one reply slot and no way to resynchronise: a reply that comes
 * after its command timed out completes whichever command is sent next, which
 * then fails with -EIO ("Command sequence mismatch"). So, as smc_peek does,
 * this takes the SMC's mutex at once after a timeout and holds it for a while,
 * so that the late reply most likely finds no command waiting: not another
 * driver's (macsmc-power, macsmc-hwmon), nor the next read here. Only most
 * likely: a command already queued on the mutex can still go first.
 */
static void drain_after_timeout(void)
{
	mutex_lock(&smc->mutex);
	msleep(DRAIN_MS);
	mutex_unlock(&smc->mutex);
}

/*
 * One B0AV read through macsmc's ordinary read. The caller holds the SMC
 * device's lock, so macsmc stays bound for it. ns is the read's time alone,
 * without the drain after a timeout.
 */
static int read_b0av_locked(struct device *dev, unsigned int *mv, unsigned long long *ns)
{
	u16 val = 0;
	u64 t0;
	int ret;

	*mv = 0;
	*ns = 0;
	if (!dev->driver || strcmp(dev->driver->name, SMC_DRIVER) || dev_get_drvdata(dev) != smc)
		return -ENODEV;
	t0 = trace_clock_local();
	ret = apple_smc_read_u16(smc, SMC_KEY(B0AV), &val);
	*ns = trace_clock_local() - t0;
	if (ret == -ETIMEDOUT)
		drain_after_timeout();
	if (!ret)
		*mv = val;
	return ret;
}

/*
 * The same from an op, which only tries the device lock; the read still waits
 * for macsmc's mutex and the SMC's reply (see the top).
 */
static int read_b0av(unsigned int *mv, unsigned long long *ns)
{
	struct device *dev = &smc_pdev->dev;
	int ret;

	if (!device_trylock(dev)) {
		*mv = 0;
		*ns = 0;
		return -EBUSY;
	}
	ret = read_b0av_locked(dev, mv, ns);
	device_unlock(dev);
	return ret;
}

/* t: the time as the trace prints it, microseconds rounded as ftrace does. */
static void log_op(const char *op, unsigned int n, u64 t)
{
	u64 us = (t + 500) / 1000;

	pr_info("op=%s n=%u t=%llu.%06llu t_ns=%llu\n", op, n,
		us / USEC_PER_SEC, us % USEC_PER_SEC, t);
}

static void log_read(const char *op, unsigned int n, u64 t, int ret,
		     unsigned int mv, unsigned long long ns)
{
	u64 us = (t + 500) / 1000;

	pr_info("op=%s n=%u t=%llu.%06llu t_ns=%llu key=B0AV ret=%d mv=%u read_ns=%llu\n",
		op, n, us / USEC_PER_SEC, us % USEC_PER_SEC, t, ret, mv, ns);
}

static int e1_begin(void)
{
	u64 t = trace_clock_local();

	log_op("begin", ++begin_calls, t);
	return 0;
}

/* Step 2, before the noirq pass: the control for prepare_late's read. */
static int e1_prepare(void)
{
	u64 t = trace_clock_local();
	unsigned int n = ++prepare_calls;

	prepare_t_ns = t;
	prepare_ret = read_b0av(&prepare_mv, &prepare_read_ns);
	log_read("prepare", n, t, prepare_ret, prepare_mv, prepare_read_ns);
	return 0;
}

/*
 * One of the replay's key writes: logged, and on a failure the drain after a
 * timeout (as for the reads above) and false, so that nothing more is sent.
 * With bit 3, MBSE=slpa (i 3) is logged only if it fails: from slpa on,
 * nothing is printed (their fast mode, L877-878).
 */
static bool replay_write(int i, const char *what, int ret)
{
	replay_ret[i] = ret;
	if (ret || i != 3 || !(replay & 8))
		pr_emerg("replay: %s %d\n", what, ret);
	if (!ret)
		return true;
	if (ret == -ETIMEDOUT)
		drain_after_timeout();
	pr_emerg("replay: %s failed; nothing more sent\n", what);
	return false;
}

/*
 * Bit 3, before the keys: the post-slpa table's watchdog and error-handler
 * entries, in table order, each with writel, then dsb(sy), as their default
 * does (lab_eh_early, L823-831). Each of them is 4 bytes wide (checked at
 * load), so writel is the width.
 */
static void e1_post_slpa_eh(void)
{
	unsigned int i, n = 0;

	pr_emerg("replay: fast: %u watchdog and error-handler writes now; from MBSE=slpa on, no output: the other %zu writes, 500 ms%s\n",
		 post_eh, ARRAY_SIZE(e1_post_slpa) - post_eh,
		 (replay & 2) ? ", then every CPU's power-down" : "");
	for (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++) {
		if (!e1_post_is_eh(&e1_post_slpa[i]))
			continue;
		writel(e1_post_slpa[i].val, post_map[i]);
		n++;
	}
	dsb(sy);
	eh_writes = n;
	pr_emerg("replay: %u watchdog and error-handler writes done\n", n);
}

/*
 * One CPU's power-down, never to return: maralcbr's lab_floor_cpu()
 * (L441-458), unchanged. With every exception masked, it sets ACC_OVRD
 * (s3_5_c15_c6_0) and CYC_OVRD (s3_5_c15_c5_0) for a deep sleep, and waits in
 * WFI for good. The fields, as Asahi's docs/hw/cpu/system-registers.md names
 * them:
 *   ACC_OVRD   [14:13] OK to power down SRM = 3, [16:15] disable L2 flush for
 *              ACC sleep = 2, [18:17] OK to train down link = 3, [26:25] OK to
 *              power down CPM = 3, [32] disable PIO on WFI CPU, [34] enable
 *              deep sleep
 *   CYC_OVRD   [21:20] FIQ mode and [23:22] IRQ mode = 0, [25:24] OK to power
 *              down = 3 (force down), [0] disable WFI return: 0x3000001,
 *              which macOS writes (maralcbr, L452)
 */
static void e1_cpu_down(void *unused)
{
	u64 acc, cyc;

	local_daif_mask();
	asm volatile("mrs %0, s3_5_c15_c6_0" : "=r"(acc));
	acc &= ~(GENMASK_ULL(18, 13) | GENMASK_ULL(26, 25));
	acc |= (3ULL << 13) | (2ULL << 15) | (3ULL << 17) | (3ULL << 25) |
	       BIT_ULL(32) | BIT_ULL(34);
	asm volatile("msr s3_5_c15_c6_0, %0" : : "r"(acc));
	asm volatile("mrs %0, s3_5_c15_c5_0" : "=r"(cyc));
	cyc &= ~(GENMASK_ULL(25, 20) | BIT_ULL(0));
	cyc |= 0x3000001ULL;
	asm volatile("msr s3_5_c15_c5_0, %0; isb" : : "r"(cyc));
	for (;;)
		asm volatile("dsb sy; wfi");
}

/*
 * Bit 3, right after MBSE=slpa was accepted, with no output: the table's
 * other entries, back to back, as lab_replay_post_slpa_fast() writes them
 * (L348-361), then dsb(sy), then their 500 ms with its two reads (L883-894).
 * With bit 1, every CPU's power-down follows (L895-898): the others through
 * smp_call_function() without waiting, then this one, and nothing returns.
 * Preemption stays off from the first write on, so that no other task runs on
 * this CPU and it can't migrate before its own power-down; interrupts still
 * can, until e1_cpu_down() masks them (smp_call_function() needs them on).
 * This CPU powers down right after sending the IPIs, usually before the
 * others have taken theirs.
 */
static void e1_after_slpa(void)
{
	unsigned int i, n = 0;

	preempt_disable();
	for (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++) {
		const struct e1_mmio_write *w = &e1_post_slpa[i];

		if (e1_post_is_eh(w))
			continue;
		if (w->width == 8)
			writeq_relaxed(w->val, post_map[i]);
		else
			writel_relaxed(w->val, post_map[i]);
		n++;
	}
	dsb(sy);
	mdelay(100);
	afnc[0] = readl(afnc_map + 0x160);
	afnc[1] = readl(afnc_map + 0x178);
	mdelay(400);
	afnc[2] = readl(afnc_map + 0x160);
	afnc[3] = readl(afnc_map + 0x178);
	if (replay & 2) {
		smp_call_function(e1_cpu_down, NULL, 0);
		e1_cpu_down(NULL);
	}
	preempt_enable();
	post_writes = n;
	pr_emerg("replay: MBSE=slpa 0, then the other %u post-slpa writes; the CPUs stay up; afnc2 %08x afnc3 %08x at +100 ms, %08x %08x at +500 ms\n",
		 n, afnc[0], afnc[1], afnc[2], afnc[3]);
}

/*
 * The replay, in maralcbr's order (above). Under the SMC device's lock, tried
 * only: if it's busy, or macsmc isn't bound, nothing is written at all. It
 * stops at the first key write that fails, so that a broken sequence never
 * reaches the PMP words' second half, MBSE=slpa or anything after it. True
 * if it ran to its end: MBSE=slpa accepted and, with bit 3, the post-slpa
 * writes made (with bit 1 it never returns then).
 */
static bool e1_replay(void)
{
	struct device *dev = &smc_pdev->dev;
	void __iomem *req = sleep_req_page ? sleep_req_page + (E1_PMGR_SLEEP_REQ & ~PAGE_MASK) : NULL;
	void __iomem *dvfs = pmp_dvfs_page ? pmp_dvfs_page + (E1_PMP_DVFS4 & ~PAGE_MASK) : NULL;
	bool done = false;

	replay_done = 1;
	if (!device_trylock(dev)) {
		pr_emerg("replay: the SMC's device lock is busy; nothing sent\n");
		return false;
	}
	if (!dev->driver || strcmp(dev->driver->name, SMC_DRIVER) || dev_get_drvdata(dev) != smc) {
		pr_emerg("replay: macsmc isn't bound; nothing sent\n");
		goto unlock;
	}
	if (replay & 8)
		e1_post_slpa_eh();
	if ((replay & 4) && req) {
		writel(4, req);
		pr_emerg("replay: 4 -> 0x%llx\n", E1_PMGR_SLEEP_REQ);
	}
	if ((replay & 1) &&
	    (!replay_write(0, "MBSE=slpw", apple_smc_write_u32(smc, SMC_KEY(MBSE), SMC_KEY(slpw))) ||
	     !replay_write(1, "pmVC=0x100", apple_smc_write_u32(smc, SMC_KEY(pmVC), 0x100)) ||
	     !replay_write(2, "WKTP=2", apple_smc_write_u8(smc, SMC_KEY(WKTP), 2))))
		goto unlock;
	if ((replay & 4) && req && dvfs) {
		writeq(1ULL << 61, dvfs);
		writel(1, req);
		pr_emerg("replay: 1<<61 -> 0x%llx, 1 -> 0x%llx\n", E1_PMP_DVFS4, E1_PMGR_SLEEP_REQ);
	}
	if ((replay & 1) &&
	    replay_write(3, "MBSE=slpa", apple_smc_write_u32(smc, SMC_KEY(MBSE), SMC_KEY(slpa)))) {
		if (replay & 8)
			e1_after_slpa();
		done = true;
	}
unlock:
	device_unlock(dev);
	return done;
}

/*
 * Step 4, after the noirq pass: where E1's replay runs. With 13 or 15, a
 * replay that stopped short fails the suspend (see the replays, above).
 */
static int e1_prepare_late(void)
{
	u64 t = trace_clock_local();
	unsigned int n = ++prepare_late_calls;

	late_t_ns = t;
	late_ret = read_b0av(&late_mv, &late_read_ns);
	log_read("prepare_late", n, t, late_ret, late_mv, late_read_ns);
	if (replay && !replay_done && !e1_replay() && (replay & 8)) {
		pr_emerg("replay: stopped short; this suspend fails with -EIO, so that the Mac wakes with this log\n");
		return -EIO;
	}
	return 0;
}

/*
 * Registered for 13 and 15 only. PM_POST_SUSPEND ends every suspend that got
 * as far as PM_SUSPEND_PREPARE, whether it reached prepare_late or not, and
 * this notifier has the highest priority, so that another's refusal of
 * PM_SUSPEND_PREPARE still sends it PM_POST_SUSPEND. A suspend that ended
 * without running the replay calls it off.
 */
static int e1_pm_notify(struct notifier_block *nb, unsigned long action, void *data)
{
	if (action == PM_POST_SUSPEND && !replay_done) {
		replay_done = 2;
		pr_emerg("replay: a suspend ended before prepare_late; called off, nothing sent\n");
	}
	return NOTIFY_DONE;
}

static struct notifier_block e1_pm_nb = {
	.notifier_call = e1_pm_notify,
	.priority = INT_MAX,
};

static void e1_restore_early(void)
{
	u64 t = trace_clock_local();

	log_op("restore_early", ++restore_early_calls, t);
}

static void e1_restore(void)
{
	u64 t = trace_clock_local();

	log_op("restore", ++restore_calls, t);
}

static void e1_end(void)
{
	u64 t = trace_clock_local();

	log_op("end", ++end_calls, t);
}

/*
 * .check and .wake stay NULL: s2idle_loop() calls .wake instead of
 * pm_wakeup_pending() when it's set, and .check before every s2idle_enter().
 */
static const struct platform_s2idle_ops e1_ops = {
	.begin = e1_begin,
	.prepare = e1_prepare,
	.prepare_late = e1_prepare_late,
	.restore_early = e1_restore_early,
	.restore = e1_restore,
	.end = e1_end,
};

/*
 * Whether this is a safe boot: its GRUB entry adds the word sleepdrain.safe
 * to the kernel command line, which /chosen/bootargs carries here.
 */
static bool __init e1_safe_boot(void)
{
	static const char word[] = "sleepdrain.safe";
	const size_t n = sizeof(word) - 1;
	const char *args, *p;

	if (of_property_read_string(of_chosen, "bootargs", &args))
		return false;
	for (p = args; (p = strstr(p, word)); p += n) {
		if ((p == args || p[-1] == ' ') && (!p[n] || p[n] == ' '))
			return true;
	}
	return false;
}

/*
 * Before anything else at load. First the post-slpa table, whatever replay
 * is: an entry on the never-write list refuses the module, as does one this
 * port doesn't write as maralcbr does (a width other than 4 or 8, an
 * unaligned address, a 4-byte value over 32 bits, or a watchdog or
 * error-handler entry that isn't 4 bytes, since those go out with writel).
 * Then replay and oneway, and for 13 and 15 a safe boot.
 */
static int __init e1_check_params(void)
{
	unsigned int i;

	post_eh = 0;
	for (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++) {
		const struct e1_mmio_write *w = &e1_post_slpa[i];

		if (e1_never_write(w->addr)) {
			pr_err("post-slpa write %u is to 0x%llx, on the never-write list; refusing\n",
			       i, w->addr);
			return -EPERM;
		}
		if ((w->width != 4 && w->width != 8) || !IS_ALIGNED(w->addr, w->width) ||
		    (w->width == 4 && w->val > U32_MAX) ||
		    (e1_post_is_eh(w) && w->width != 4)) {
			pr_err("post-slpa write %u (0x%llx, %u bytes) isn't one this port writes as maralcbr does; refusing\n",
			       i, w->addr, w->width);
			return -EINVAL;
		}
		if (e1_post_is_eh(w))
			post_eh++;
	}

	if (replay != 0 && replay != 5 && replay != 13 && replay != 15) {
		pr_err("replay=%u: only 0 (none), 5 (E1a: keys and PMP words), 13 (5 and the post-slpa writes, the CPUs up) and 15 (13 and every CPU's power-down: the floor) exist\n",
		       replay);
		return -EINVAL;
	}
	if ((replay & (2 | 8)) && !oneway) {
		pr_err("replay=%u can't be undone: it writes the post-slpa table%s. Load it with oneway=1 as well\n",
		       replay, (replay & 2) ? " and powers every CPU down for good" : "");
		return -EINVAL;
	}
	if ((replay & (2 | 8)) && !e1_safe_boot()) {
		pr_err("replay=%u ends in a power-key reset, so it loads only in a safe boot (sleepdrain.safe on the kernel command line): pkexec sleep-drain-safe boot, then restart\n",
		       replay);
		return -EPERM;
	}
	if (replay && !of_machine_is_compatible("apple,j314s")) {
		pr_err("replay is maralcbr's J314s sequence, and this isn't a J314s\n");
		return -ENODEV;
	}
	return 0;
}

/* Every mapping made at load; each pointer is NULL if it wasn't made. */
static void e1_unmap(void)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++) {
		if (post_map[i])
			iounmap(post_map[i] - (e1_post_slpa[i].addr & ~PAGE_MASK));
	}
	if (afnc_map)
		iounmap(afnc_map);
	if (sleep_req_page)
		iounmap(sleep_req_page);
	if (pmp_dvfs_page)
		iounmap(pmp_dvfs_page);
}

static int __init e1_hook_init(void)
{
	struct kprobe kp = {
		.symbol_name = "s2idle_set_ops",
		.flags = KPROBE_FLAG_DISABLED,
	};
	char sym[KSYM_SYMBOL_LEN];
	struct device_node *np;
	unsigned long long ns;
	struct device *dev;
	unsigned int i, mv;
	int ret;

	ret = e1_check_params();
	if (ret)
		return ret;

	np = of_find_compatible_node(NULL, NULL, SMC_COMPATIBLE);
	if (!np)
		return -ENODEV;
	smc_pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!smc_pdev)
		return -ENODEV;
	dev = &smc_pdev->dev;

	ret = -ENODEV;
	device_lock(dev);
	if (!dev->driver || strcmp(dev->driver->name, SMC_DRIVER)) {
		pr_err("the SMC isn't bound to %s\n", SMC_DRIVER);
		goto unlock;
	}
	smc = dev_get_drvdata(dev);
	/* The layout this module was built against, checked on the live SMC. */
	if (!smc || smc->dev != dev || smc->boot_stage != APPLE_SMC_INITIALIZED) {
		pr_err("the SMC isn't as expected; refusing\n");
		goto unlock;
	}
	/* Keep macsmc loaded while this module can reach into it. */
	smc_owner = dev->driver->owner;
	if (!try_module_get(smc_owner))
		goto unlock;
	/* The key reads awake, before any suspend relies on it. */
	ret = read_b0av_locked(dev, &mv, &ns);
	pr_info("op=load key=B0AV ret=%d mv=%u read_ns=%llu\n", ret, mv, ns);
	if (ret)
		module_put(smc_owner);
unlock:
	device_unlock(dev);
	if (ret)
		goto put_dev;

	if (replay & 4) {
		/*
		 * Non-posted, as maralcbr maps them (L808-809) and as Apple SoCs
		 * need for on-chip registers: a posted write there can be lost
		 * or raise an SError.
		 */
		sleep_req_page = ioremap_np(E1_PMGR_SLEEP_REQ & PAGE_MASK, PAGE_SIZE);
		pmp_dvfs_page = ioremap_np(E1_PMP_DVFS4 & PAGE_MASK, PAGE_SIZE);
		if (!sleep_req_page || !pmp_dvfs_page) {
			ret = -ENOMEM;
			goto unmap;
		}
	}
	if (replay & 8) {
		/*
		 * A page of its own for each entry, non-posted, as maralcbr maps
		 * them (L816-821), but here, not at prepare_late, which is left
		 * with nothing but the writes.
		 */
		for (i = 0; i < ARRAY_SIZE(e1_post_slpa); i++) {
			u64 a = e1_post_slpa[i].addr;
			void __iomem *p = ioremap_np(a & PAGE_MASK, PAGE_SIZE);

			if (!p) {
				pr_err("can't map post-slpa write %u (0x%llx); refusing\n", i, a);
				ret = -ENOMEM;
				goto unmap;
			}
			post_map[i] = p + (a & ~PAGE_MASK);
		}
		afnc_map = ioremap_np(E1_AFNC_PAGE, PAGE_SIZE);
		if (!afnc_map) {
			pr_err("can't map 0x%llx (AFNC2/3); refusing\n", E1_AFNC_PAGE);
			ret = -ENOMEM;
			goto unmap;
		}
	}

	/* s2idle_set_ops()'s address, from a kprobe that is never armed. */
	ret = register_kprobe(&kp);
	if (ret) {
		pr_err("can't find s2idle_set_ops: register_kprobe %d\n", ret);
		goto unmap;
	}
	set_ops = (s2idle_set_ops_t)(unsigned long)kp.addr;
	unregister_kprobe(&kp);
	snprintf(sym, sizeof(sym), "%pS", set_ops);
	if (!str_has_prefix(sym, "s2idle_set_ops+0x0/")) {
		pr_err("s2idle_set_ops resolved to %s, not its entry; refusing\n", sym);
		ret = -EINVAL;
		goto unmap;
	}

	if (replay & 8) {
		ret = register_pm_notifier(&e1_pm_nb);
		if (ret) {
			pr_err("can't register the PM notifier: %d\n", ret);
			goto unmap;
		}
	}
	set_ops(&e1_ops);
	pr_info("loaded: s2idle ops set through %s; times are trace_clock_local(); replay=%u\n",
		sym, replay);
	return 0;

unmap:
	e1_unmap();
	module_put(smc_owner);
put_dev:
	put_device(dev);
	return ret;
}

static void __exit e1_hook_exit(void)
{
	/*
	 * First, before anything goes: this waits for a suspend in progress, and
	 * once it returns no op can run.
	 */
	set_ops(NULL);
	if (replay & 8)
		unregister_pm_notifier(&e1_pm_nb);
	e1_unmap();
	module_put(smc_owner);
	put_device(&smc_pdev->dev);
	pr_info("unloaded: calls begin=%u prepare=%u prepare_late=%u restore_early=%u restore=%u end=%u\n",
		begin_calls, prepare_calls, prepare_late_calls, restore_early_calls,
		restore_calls, end_calls);
}

module_init(e1_hook_init);
module_exit(e1_hook_exit);
MODULE_DESCRIPTION("E1: s2idle ops at prepare_late, and maralcbr's one-way sleep replay for J314s (lab only)");
MODULE_LICENSE("GPL");
