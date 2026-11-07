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
 * returns 0, so it never fails or aborts a suspend, but for two cases: with
 * replay 13 or 15, a prepare_late that doesn't run the replay to its end fails
 * the suspend with -EIO (below); and with m1=1, a begin that can't pin the
 * suspending task to CPU0 fails it with that error (end then has nothing to
 * unpin). The floor's prepare_late never returns at all. .check and
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
 *   pkexec e1-hook/run-floor --m1                 (E2's M1 recipe: m1=1, below)
 *   pkexec e1-hook/run-floor --m1-observe         (its run 2: m1=1 observe=1)
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/atomic.h>
#include <linux/bits.h>
#include <linux/build_bug.h>
#include <linux/cpumask.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/kallsyms.h>
#include <linux/kprobes.h>
#include <linux/mfd/macsmc.h>
#include <linux/minmax.h>
#include <linux/module.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/preempt.h>
#include <linux/sched.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <linux/suspend.h>
#include <linux/time64.h>
#include <linux/trace_clock.h>
#include <asm/apple_m1_pmu.h>
#include <asm/barrier.h>
#include <asm/cputype.h>
#include <asm/daifflags.h>
#include <asm/sysreg.h>
#include <asm/virt.h>
#include <trace/events/power.h>

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
 * 13 and 15 belong to the first suspend after load, and only to it. A probe
 * on the suspend_resume tracepoint counts every suspend at enter_state()'s
 * first line (suspend.c L580), before its mutex, its filesystem sync and
 * PM_SUSPEND_PREPARE. So a suspend that ends anywhere short of prepare_late
 * (a sync cut short by a wakeup, tasks that won't freeze, a device's suspend
 * error, pm_test=freezer or devices) uses the replay up: the next suspend
 * calls it off (replay_done=2) as it begins, and it can't run away from its
 * reading. Writing 1 to cancel calls it off at once; run-floor does, when its
 * suspend doesn't come or comes back. And while 13 or 15 is loaded, a suspend
 * that doesn't run the replay fails with -EIO at prepare_late, so the Mac
 * never sleeps a plain s2idle that would look like the floor: it's in the
 * floor or awake. They load only in a safe boot, with sleepdrain.safe on the
 * kernel command line, since the reset that ends them could damage a
 * writable disk.
 *
 * Their 500 ms is kept, with its two reads: their fast mode always makes it
 * (it maps the AFNC page whenever it maps the table, L816), so it's part of
 * the timing this copies, though the reads change nothing. What they read is
 * in afnc, readable with 13 only. quiet=1 (with 15 only: E2's first rung,
 * PLAN.md) leaves the 500 ms and its reads out, and the AFNC page unmapped:
 * the table's last write, 1 to 0x2925c4000 (the S2R timer, which commits the
 * PMGR's entry), is then the replay's last MMIO access: nothing in it
 * touches the hardware between the commit and every CPU's power-down. Not
 * sealed, though: every CPU keeps its interrupts on until e1_cpu_down()
 * masks them (this one because smp_call_function() needs them; the others,
 * idle, until they take their IPI), and AIC2 delivers to all of them. The
 * coprocessor mailboxes' interrupts stay armed through suspend
 * (IRQF_NO_SUSPEND), so a message in that window, or a handler already
 * running at the commit, can still touch the AIC and a mailbox after it.
 * Taking the other CPUs down before the writes, the next rung, would let
 * this CPU mask its interrupts before the commit with no other CPU up.
 * On T8103, macOS adds only the RTC alarm and the same write twice more
 * before its cores power down, and a last core that touched a device after
 * the commit hung there, so the entry never completed (Naeem's try26-31,
 * malik-na/m1-power-lab). The load checks that the commit is the table's
 * last entry. Left out, as diagnostics: their other reads
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
static bool quiet;
module_param(quiet, bool, 0444);
MODULE_PARM_DESC(quiet, "replay=15 only: leave out maralcbr's 500 ms and its two AFNC reads, so that the S2R-timer commit is the replay's last MMIO access before every CPU's power-down");
/* E2's M1 recipe, after e1_after_slpa() below, which it replaces. */
static bool m1;
module_param(m1, bool, 0444);
MODULE_PARM_DESC(m1, "With replay=15 oneway=1 quiet=1 only: E2's M1 recipe (Naeem Malik's T8103 S2R entry, mapped onto T6000): the other CPUs parked before the post-slpa writes and stopped one by one before the commit, every interrupt masked, the AP wake mask, boot stage 0x50 and breadcrumbs in the PMU, the commit three times");
static int m1_ret = 1, m1_undo = 1;
module_param(m1_ret, int, 0444);
module_param(m1_undo, int, 0444);
MODULE_PARM_DESC(m1_ret, "m1: the PMU at the replay's start (breadcrumbs zeroed, boot stage 0x50, stage 0x01): 0, -errno, or 1 if not sent");
MODULE_PARM_DESC(m1_undo, "m1: on a way back after 0x50 went out, boot stage 0x30 and the breadcrumbs zeroed: 0, -errno, or 1 if not needed");
/* E2's M1 run 2, the observer: m1, with CPU7 kept up through the commit to watch. */
static bool observe;
module_param(observe, bool, 0444);
MODULE_PARM_DESC(observe, "With m1=1 only: CPU7 isn't released before the commit; after it, CPU7 watches CPU0 and the E complex power down, reads the watchdogs, the soc clusters, SIO_CPU and the wake status, records them in the PMU's breadcrumbs, and then stops");
/* m1: the suspending task's CPUs before begin pinned it to CPU0. */
static struct cpumask m1_saved_mask;
static bool m1_pinned;
static unsigned int replay_done;
module_param(replay_done, uint, 0444);
MODULE_PARM_DESC(replay_done, "0 not yet, 1 the replay has run at a prepare_late, 2 called off (13 and 15: a suspend began after the first, which never reached prepare_late; or cancel)");
static atomic_t attempt_count = ATOMIC_INIT(0);
static unsigned int attempts;
module_param(attempts, uint, 0444);
MODULE_PARM_DESC(attempts, "13 and 15: how many suspends have begun since load; only the first can replay");

/* Writing 1 calls the replay off, if it hasn't run. */
static int e1_cancel_set(const char *val, const struct kernel_param *kp)
{
	bool v;

	if (kstrtobool(val, &v) || !v)
		return -EINVAL;
	if (!READ_ONCE(replay_done)) {
		WRITE_ONCE(replay_done, 2);
		pr_emerg("replay: called off by a write to cancel; nothing sent\n");
	}
	return 0;
}

static const struct kernel_param_ops e1_cancel_ops = {
	.set = e1_cancel_set,
};
module_param_cb(cancel, &e1_cancel_ops, NULL, 0200);
MODULE_PARM_DESC(cancel, "Write 1 to call the replay off, if it hasn't run (13 and 15: every suspend then fails)");
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

/* The table's last write: the S2R timer, the PMGR entry's commit (quiet=1). */
#define E1_S2R_COMMIT		0x2925c4000ULL

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

/*
 * m1: the suspending task pinned to CPU0 while the replay can still run, so
 * that its prepare_late runs there; end unpins it. A failure fails the
 * suspend (the PM core then calls end, which unpins only what this pinned).
 */
static int e1_begin(void)
{
	u64 t = trace_clock_local();
	int ret;

	log_op("begin", ++begin_calls, t);
	if (!m1 || READ_ONCE(replay_done))
		return 0;
	cpumask_copy(&m1_saved_mask, current->cpus_ptr);
	ret = set_cpus_allowed_ptr(current, cpumask_of(0));
	if (ret) {
		pr_emerg("m1: can't pin the suspending task to CPU0: %d; this suspend fails\n", ret);
		return ret;
	}
	m1_pinned = true;
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

	pr_emerg("replay: fast: %u watchdog and error-handler writes now; from MBSE=slpa on, no output: the other %zu writes%s%s\n",
		 post_eh, ARRAY_SIZE(e1_post_slpa) - post_eh,
		 m1 ? ", the other CPUs parked through them and stopped one by one, the commit last" :
		 quiet ? ", the commit last" : ", 500 ms",
		 observe ? ", then CPU0's power-down, watched by CPU7" :
		 m1 ? ", then CPU0's power-down" : (replay & 2) ? ", then every CPU's power-down" : "");
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
 * (L348-361), then dsb(sy), then their 500 ms with its two reads (L883-894),
 * unless quiet. With bit 1, every CPU's power-down follows (L895-898): the others through
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
	if (!quiet) {
		mdelay(100);
		afnc[0] = readl(afnc_map + 0x160);
		afnc[1] = readl(afnc_map + 0x178);
		mdelay(400);
		afnc[2] = readl(afnc_map + 0x160);
		afnc[3] = readl(afnc_map + 0x178);
	}
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
 * m1=1, with replay=15 oneway=1 quiet=1 (run-floor --m1): E2's main bet, the
 * M1 recipe (PLAN.md). The floor's S2R entry doesn't complete (RESEARCH.md,
 * "The floor"). Naeem Malik's Linux entry does, on T8103: his apple-s2r.c
 * and its deep stop in smp_apple.c and smp_apple_asm.S (malik-na/linux-aurora
 * aca68b693bd2, branch m1power/s2r-mem; GPL-2.0-only, used here under
 * GPL-2.0, with thanks; RESEARCH.md, "The team"). This maps his entry onto
 * T6000 around maralcbr's keys and table, which stay as the floor has them.
 * In order:
 *
 *   begin   the suspending task is pinned to CPU0, the boot CPU, so that
 *           prepare_late runs there; end unpins it
 *   prepare_late, on CPU0, with interrupts on, as the floor:
 *     K1    the PMU, through the kernel's own SPMI path (its nvmem):
 *           the breadcrumb bytes zeroed, boot stage 0x6001 = 0x50 and stage
 *           0x6004 = 0x01, both read back, and the RTC's second at 0x6010
 *     K2    the floor's replay as far as MBSE=slpa: the table's 31 watchdog
 *           and error-handler entries, the PMP words and the keys
 *     K3    stage 0x02: SPMI still answers after slpa (a failure sets bit 3
 *           of 0x6014, written in step 8 if raw SPMI works)
 *   then, never returning, without a lock, a print or a WFI or WFE (every
 *   wait spins on the counter, CNTVCT):
 *     1     CPU0 turns preemption and interrupts (IRQ, FIQ) off
 *     2     AIC2: every IRQ masked, the words the driver masks at boot
 *     3     park: each other CPU in turn (1, 2, 3, 5, 6, 4, 7), by IPI, masks
 *           every exception, turns off its own interrupt sources (its
 *           timers, its PMC's and the guest timers' FIQs, a pending fast
 *           IPI, as aic_init_cpu() masks them at bring-up), takes any event
 *           off its AIC event register, reports, and spins, awake; CPU0
 *           waits up to 10 ms for each
 *     4     AIC2 masked again, now that no other CPU can unmask an IRQ at
 *           its handler's end; WD1 stopped if it runs (apple_wdt's own stop);
 *           raw SPMI checked, if all 7 parked; stage 0x10
 *     5     the table's other 42 entries, the commit held back; stage 0x20
 *     6     release: each parked core in turn, in the same order, clears
 *           its fast IPI and AIC events again, records ISR_EL1, writes its
 *           own bit to the CPU-start block's stop word, arms its cluster's
 *           ACC_OVRD deep sleep if it's the last core of P0 or P1, sets
 *           CYC_OVRD as the floor does, and waits in WFI for good; CPU0
 *           waits up to 10 ms for its PMGR power state to read off (and for
 *           PCPM0's after CPU4, PCPM1's after CPU7); stages 0x21-0x27
 *     7     stage 0x30 with what 3-6 saw; the AP wake mask: every source but
 *           the PMU's (54); nub-spmi0's interrupt enables all off
 *     8     CPU0 turns off its own interrupt sources as the others did; the
 *           RTC's second and what CPU0 has pending, at 0x6010; stage 0x50,
 *           the last SPMI command
 *     9     nub-spmi0's events acked; every exception masked; the commit,
 *           1 to 0x2925c4000, three times (Naeem's, as macOS 27 on T8103)
 *     10    CPU0 arms its own cluster's deep sleep, sets CYC_OVRD and waits in
 *           WFI for good, with no stop bit (CPU0 restarts through m1n1's
 *           locked RVBAR, and macOS never stops it through the PMGR)
 *
 * Why the other cores park awake through 5 and power down after it, though
 * Naeem's are down long before his finisher: on H13, which takes in T6000
 * and T8103, XNU keeps its other cores awake, stalled in its sleep path,
 * until the boot CPU's ml_arm_sleep() releases them, after the platform's
 * sleep writes (xnu-12377.121.6 osfmk/arm64/cpu.c: __ARM_GLOBAL_SLEEP_BIT__,
 * which H13.h sets), and 15 of the table's entries write the P clusters' own
 * DVFS and CPM blocks. The risk: on T6020, DeepIdle's R20 wrote the same
 * cluster APSC disable as e1_post_slpa[43..48] (RESEARCH.md's entries 44-49)
 * while their other cores were parked in WFI, and most of those cores (7 of
 * 9) never woke to sleep, PCPM0 stayed on, and the S2R timer didn't reset the
 * SoC: it hung, dark, for 28 minutes until its owner held the power key
 * (JJRPF/Aurora-DeepIdle 75d4729189). Here the cores spin through those
 * writes and need no wake, as XNU's H13 stall doesn't. If they still fail to
 * power down, this rung may hang the same way instead of resetting: the run
 * needs Corey at the power key.
 *
 * The stop bits: Naeem's deep stop writes its core's bit, and only its own,
 * to CPU-start + 0x0 just before ACC_OVRD, CYC_OVRD and the WFI
 * (smp_apple_asm.S), and macOS writes the other cores' bits after slpa on
 * T8103 and T6020 (Naeem's and DeepIdle's trace notes). On T6000 the block is
 * at PMGR reg[0] + 0x54000, Asahi's docs/hw/cpu/smp.md offset for the M1
 * series, which linux-aurora PR #170 also uses on a J314s; + 0x0's role is
 * Naeem's. Each core's bit is 1 << (4 * cluster + core): + 0x4 reads 0x880
 * here, exactly the two fused-off cores in that numbering.
 * CPU0's bit and the fused-off cores' are never written. The ACC_OVRD rule
 * is Naeem's fix for his first run's hang (3bbb16ebac56): only the last core
 * of a cluster CPU0 isn't in arms its deep sleep, and CPU0 arms its own last.
 * XNU calls the same register global; either way no core here runs an
 * ordinary WFI once one is armed. The cores' power states, read to check
 * each stop, are PMGR words that read 0x100 (off) for idle P cores here, and
 * share their page with ps_pmp: only their offsets are ever read.
 *
 * SPMI: from 3 on, CPU0 drives nub-spmi0 itself, polled, as aurora's
 * spmi-apple-controller (gen 1, at 4fdd0fc5) does, only if every other core
 * parked (so none can be mid-transfer), after a FIFO flush, and only once a
 * raw read of 0x6001 gives back K1's 0x50; it stops at its first error. Every
 * PMU access, either path, goes through m1_pmu_ok(): writes only to 0x6001
 * (0x50 or 0x30), 0x6004-0x6007 and 0x6010-0x6016, the spare legacy-scratch
 * bytes DeepIdle uses (Corey's OK of 2026-11-07, PLAN.md); reads only there
 * and of the RTC. Every way back from the replay after 0x50 went out writes
 * 0x30 again and zeroes the breadcrumbs (m1_undo says how that went).
 *
 * The breadcrumbs (e1-hook/m1-crumbs reads and clears them). Each is the last
 * one that reached the PMU, so the run got at least that far: raw SPMI stops
 * at its first error, and CPU0 goes on without it.
 *   0x6001  boot stage 0x50 from K1 on; macsmc-reboot's probe writes 0x30
 *           at the next boot (0x6003, the boot-error stage, keeps what an
 *           unclean reset found there, as it did for the floor)
 *   0x6004  the stage: 0x01 K1, 0x02 K3, 0x10 parked (4), 0x20 the table
 *           (5), 0x21-0x27 releasing the order's first to last core, 0x30
 *           released (7), 0x50 the last before the commit (8)
 *   0x6005  the cores that parked (bit n: CPUn), 0x6006 those that left
 *           their spin to stop, 0x6007 those whose power state read off
 *   0x6010  the RTC's second (4 bytes, LE): K1's, then step 8's at 0x50
 *   0x6014  bits 0-2 CPU0's ISR_EL1 F, I, A before the commit; 3 K3 failed;
 *           4 PCPM0 and 5 PCPM1 read off; 6 WD1 was running; 7 set
 *   0x6015  the cores that had an interrupt pending (ISR_EL1) at their stop
 *   0x6016  how many AIC events every core took off (up to 255)
 *
 * observe=1 (run-floor --m1-observe, E2's M1 run 2; PLAN.md): M1 run 1 saw
 * CPU1-7 and both P complexes power down and still got the reset about 31 s
 * after the commit; nothing after CPU0's 0x50 was recorded. On T8103 an
 * entry completed only once every core, the last included, reached its deep
 * WFI (Naeem's mac27-hv try26-32), and DeepIdle's boot core is unverified
 * too. So CPU7, P1's last core, isn't released in 6 (stages 0x21-0x26 only;
 * CPU0 doesn't wait for PCPM1), and 0x6005's bit 0, never a parked core,
 * marks the run as observe=1 in the crumbs of 4 and 7. In 9, before the
 * commit, CPU0 reads the AP wake status for CPU7 and writes CPU7's stop bit
 * while CPU7 still spins, as macOS writes the other cores' bits while they
 * stall in WFE; after the commit it hands over (go, with observe set, all of
 * it cleaned) and goes to 10, counting any WFI that returns. CPU7
 * (m1_observe()) then polls CPU0's power state for up to 500 ms, then the E
 * complex's (ECPM) for up to 500 ms more, reads the three watchdogs, the soc
 * clusters, SIO_CPU, the wake status and two words that may be the S2R
 * engine's progress, records them, records what it has pending itself, acks
 * nub-spmi0 and stops. Several words it reads are the replay's own targets:
 * the table zeroes WD0's control and writes the soc-cluster words in K2,
 * step 4 stops WD1, and step 5 writes SIO_CPU (0x400024f). So a watchdog
 * armed after the commit was armed again by something else. The stages and
 * bytes are such that each stage says which bytes hold what, whichever SPMI
 * command fails or lands last: a stage and its bytes go in one command, or
 * the stage that announces new meanings goes first.
 *   0x6004  0x60 CPU7 began; 0x61 0x6005-0x6007 are CPU7's (one command
 *           with them); 0x62 0x6012-0x6013 and 0x6015-0x6016 are being
 *           rewritten (CPU0's or CPU7's, not known which); 0x6f all landed,
 *           and CPU7 had nothing pending just before its acks and WFI; 0x6e
 *           all landed, but it had an interrupt or SError pending
 *   0x6005  below 0x61, as in run 1 (bit 0 the observe mark); from 0x61,
 *           bit 0 CPU0 read off within 500 ms; bits 1-6 the cores that read
 *           off at their release (0x6007 below 0x61); bit 7 ECPM read off
 *           within 500 ms more
 *   0x6006  (0x61 on) bits 0-2 WD2's control (0 IRQ_EN, 1 IRQ_STATUS,
 *           2 RESET_EN); 3 WD0's RESET_EN; 4-6 WD1's control; 7 CPU0's last
 *           WFI had returned by then
 *   0x6007  (0x61 on) the seconds before the first armed watchdog resets
 *           the SoC (bite minus count, rounded), 255 if none is armed
 *   0x6010  the commit's RTC second as in run 1; from 0x62 only its low
 *           two bytes are sure, and from 0x6e 0x6012 and 0x6013 are the low
 *           bytes of the wdt page's + 0x28 and + 0x40 (T8103's progress
 *           registers' offsets)
 *   0x6014  as above, at every stage (CPU7 never writes it); bit 5 (PCPM1)
 *           stays 0, since P1 is up at 8
 *   0x6015  (0x6e on) bits 0-6 the soc clusters still busy (bit 4 of their
 *           word): EACC, PACC0, PACC1, ANE0, AFR, DISPLAY, AVEMSR (PACC1
 *           expected, CPU7 being up); bit 7 SIO_CPU's actual state not off
 *   0x6016  (0x6e on) bits 0-6 the AP wake-status words with a bit set that
 *           wasn't just before the commit; bit 7 always set (a 0 here at
 *           0x6e or 0x6f means the byte didn't survive the reset)
 *
 * Left out: Naeem's RTC alarm and his WD1 dead-man, so that a completed
 * entry stays dark for a BQCC floor (the alarm's PMU writes, OK'd on
 * 2026-11-07, wait for their own rung, PLAN.md, and its wake would land in
 * m1n1); his report of idle clusters to the
 * PMP, which has no known T6000 counterpart; his CPM power gating (0x80000000
 * to pmgr + 0x1c080 and + 0x1c088), with none found either (the table's
 * writes to each cluster's CPM block may be its T6000 form); his zeros to the
 * watchdog block's + 0x8008 and + 0x800c, whose T6000 forms and roles are
 * unknown; his L1 clean by set and way, since nothing comes back; his clkgen
 * park and counter blocks, which the table already writes (its own forms,
 * before the keys). Never written:
 * MBSe, ps_pmp (0x28e0802d8), 0x28e700000, WD2, nub-spmi1, the SEP.
 * observe=1 adds no address to what m1 writes: CPU0 writes CPU7's stop bit,
 * before the commit, instead of CPU7 in 6, and CPU7 writes breadcrumbs and
 * nub-spmi0's acks as CPU0 does.
 */
#define M1_NCPUS		8
#define M1_OTHERS		0xfe
#define M1_P0			(BIT(2) | BIT(3) | BIT(4))
#define M1_P1			(BIT(5) | BIT(6) | BIT(7))
/* PMGR reg[0] + 0x54000: + 0x0, the stop word, a bit per core. */
#define M1_CPU_START		0x28e0d4000ULL
/* PMGR reg[0]: the cores' and complexes' power states, [7:4] 0 off. Read only. */
#define M1_PMGR_PS		0x28e080000ULL
#define M1_PS_PCPM0		0x68
#define M1_PS_PCPM1		0x70
/* AIC2: reg[0], 0xc000 (INFO1 + 0x4, INFO3 + 0xc), and reg[1], the event register. */
#define M1_AIC			0x28e100000ULL
#define M1_AIC_SIZE		0xc000
#define M1_AIC_EVENT		0x28e10c000ULL
/* PMGR reg[7] + 8: the AP wake-source masks, 7 words, a set bit masks one. */
#define M1_AON_WAKE		0x292004008ULL
/* wdt reg[0], WD1's control at + 0x1c (apple_wdt's watchdog). */
#define M1_WDT			0x2922b0000ULL
#define M1_WD1_CTRL		0x1c
/* nub-spmi0, gen 1, as aurora's spmi-apple-controller.c lays it out. */
#define M1_SPMI			0x2920a1300ULL
#define M1_SPMI_STATUS		0x0
#define M1_SPMI_CMD		0x4
#define M1_SPMI_RSP		0x8
#define M1_SPMI_EN(w)		(0x20 + 4 * (w))
#define M1_SPMI_ACK(w)		(0x60 + 4 * (w))
#define M1_SPMI_ACT		0xa4
#define M1_SPMI_WORDS		9
#define M1_SPMI_RX_EMPTY	BIT(24)
#define M1_SPMI_ACT_FLUSH	BIT(0)
#define M1_SPMI_REPLY_ACK	BIT(15)
#define M1_SPMI_EXT_WRITEL	0x30
#define M1_SPMI_EXT_READL	0x38
#define M1_PMU_SID		0xf
#define M1_PMU_NODE		"/soc/spmi@2920a1300/pmic@f"
#define M1_SPMI_NODE		"/soc/spmi@2920a1300"
/* The PMU's RTC: 6 bytes, LE, the seconds in the upper four (pmu-alarm). */
#define M1_PMU_RTC		0x1406
/* Each core's own interrupt sources, as irq-apple-aic.c names them. */
#define M1_IPI_SR_EL1		sys_reg(3, 5, 15, 1, 1)
#define M1_IPI_SR_PENDING	BIT(0)
#define M1_VM_TMR_FIQ_ENA_EL2	sys_reg(3, 5, 15, 1, 3)
#define M1_VM_TMR_FIQ_V		BIT(0)
#define M1_VM_TMR_FIQ_P		BIT(1)
#define M1_UPMCR0_EL1		sys_reg(3, 7, 15, 0, 4)
#define M1_UPMCR0_IMODE		GENMASK(18, 16)

/*
 * observe=1 only, read through mappings of their own (but the first two).
 * CPU0's and the E complex's power states, in M1_PMGR_PS's page (ECPU0_0 and
 * ECPM, as pmgr-map names them).
 */
#define M1_PS_CPU0		0x00
#define M1_PS_ECPM		0x60
/* PMGR reg[0] + 0x18000: the soc-cluster words, 4 * n (the ADT's soc-clusters, first byte), bit 4 busy. */
#define M1_SOC_CLUSTERS		0x28e098000ULL
/* PMGR reg[2] + 0x180 and + 0x188: SIO's and SIO_CPU's power states, [7:4] the actual one. */
#define M1_PS_SIO_PAGE		0x28e580000ULL
#define M1_PS_SIO		0x180
#define M1_PS_SIO_CPU		0x188
/* wdt reg[0]: WD0, WD1 (apple_wdt's) and WD2 (the S2R engine's): count, bite, control (bit 2 RESET_EN). */
#define M1_WD_CUR(n)		(0x10 * (n))
#define M1_WD_BITE(n)		(0x10 * (n) + 0x4)
#define M1_WD_CTRL(n)		(0x10 * (n) + 0xc)
#define M1_WD_RESET_EN		BIT(2)
/*
 * The wdt node's second page: its ADT reg[1-4] are the words at + 0x8,
 * + 0x20 and + 0x2c. + 0x28 and + 0x40 are inferred: T8103's AON engine has
 * its progress registers at those offsets in its own page (Naeem's
 * linux-s2r README at malik-na/m1-power-lab 5d77d5e45980).
 */
#define M1_WDX			0x2922b8000ULL
#define M1_WDX_PROG0		0x28
#define M1_WDX_PROG1		0x40
/* PMGR reg[7] + 0x24: the AP wake status, inferred: 7 words right after the masks, as on T8103 and T6020. */
#define M1_AON_WAKE_STATUS	0x292004024ULL
/*
 * PMGR reg[0] + 0x1c000, logged at load only: where T6000's CPM gating may
 * be, all inferred. + 0x80 and + 0x88 are T8103's (Naeem's pmgr + 0x1c080
 * and + 0x1c088, 0x80000000 before his commit); + 0x30, + 0x34 and + 0x38
 * are T6020's enableCPMPowerDomainGating words (PMGR reg[0] + 0xc030-0xc038,
 * 0x80000000 under Linux; DeepIdle's worklog, 2026-11-06), moved by the
 * 0x10000 between T6020's soc-cluster words (0x28e088000) and T6000's.
 */
#define M1_CPM_GATE		0x28e09c000ULL

/*
 * This Mac's CPUs, as Linux numbers them, checked at load: MPIDR, the stop
 * bit (1 << (4 * Aff1 + Aff0)) and the power state's offset in M1_PMGR_PS.
 * CPU5-7 are the ADT's cpu6-8: each P cluster's fourth core is fused off.
 */
static const struct m1_cpu {
	u64 mpidr;
	u32 stop;
	u32 ps;
} m1_cpus[M1_NCPUS] = {
	{ 0x00000, 0x001, 0x00 },	/* CPU0, E: never stopped this way */
	{ 0x00001, 0x002, 0x08 },	/* CPU1, E */
	{ 0x10100, 0x010, 0x20 },	/* CPU2-4, P0 */
	{ 0x10101, 0x020, 0x28 },
	{ 0x10102, 0x040, 0x30 },
	{ 0x10200, 0x100, 0x40 },	/* CPU5-7, P1 */
	{ 0x10201, 0x200, 0x48 },
	{ 0x10202, 0x400, 0x50 },
};
/* Parked and released in this order: CPU4 and CPU7 are their clusters' last. */
static const u8 m1_order[M1_NCPUS - 1] = { 1, 2, 3, 5, 6, 4, 7 };

/*
 * Every AP wake source masked but 54, NUB.SPMISw3IRQ (nub-spmi0's PMU
 * interrupt), as Naeem's entry leaves its T8103 counterpart. Word 6 is
 * macOS's own value. Linux runs with 0, 0x40, 0, 0, 0, 0, 0; maralcbr's bit
 * 15 wrote macOS's values to these words on a J314s.
 */
static const u32 m1_wake_mask[7] = {
	0xffffffff, 0xffbfffff, 0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff, 0x00001fff,
};

/*
 * One per core, in a line of its own: written by it, or by CPU0 (arm, go,
 * and observe's fields). Everything stays in the first 64 bytes, the cache
 * line that one m1_clean() writes back.
 */
struct m1_core {
	u32 parked;	/* it quieted itself and spins */
	u32 arm;	/* CPU0: arm its cluster's deep sleep before the WFI */
	u32 go;		/* CPU0: stop now */
	u32 left;	/* it left its spin; isr and drained are written */
	u32 isr;	/* ISR_EL1 [8:6] just before its stop bit */
	u32 drained;	/* AIC events it took off */
	u32 observe;	/* CPU0, CPU7's only: watch, then stop (observe=1) */
	u32 raw;	/* CPU0, for CPU7: raw SPMI works (m1_raw at the commit) */
	u32 down;	/* CPU0, for CPU7: the cores whose power state read off at release */
	u32 wfi_ret;	/* CPU0's own, observe=1: how often its last WFI returned */
} __aligned(128);
static_assert(sizeof(struct m1_core) <= 128 && offsetof(struct m1_core, wfi_ret) + sizeof(u32) <= 64);
static struct m1_core m1_core[M1_NCPUS];
/* observe=1: the AP wake status just before the commit, CPU0's for CPU7, in a line of its own. */
static u32 m1_wake_pre[7] __aligned(128);
static call_single_data_t m1_csd[M1_NCPUS];

static void __iomem *m1_cpu_start, *m1_ps, *m1_aic, *m1_aic_event, *m1_aon, *m1_wdt, *m1_spmi_page;
#define m1_spmi			(m1_spmi_page + (M1_SPMI & ~PAGE_MASK))
/* observe=1 only: read, never written. */
static void __iomem *m1_soc, *m1_sio, *m1_wdx, *m1_cpm;
#define m1_wake_status		(m1_aon + (M1_AON_WAKE_STATUS - M1_AON_WAKE))
static unsigned int m1_aic_mask, m1_aic_words;
static struct nvmem_device *m1_nvmem;
/* The stock SPMI controller's module, held while this module is loaded. */
static struct module *m1_spmi_owner;
static u64 m1_tps;
static bool m1_sent_50, m1_raw;

static __always_inline u64 m1_now(void)
{
	isb();
	return read_sysreg(cntvct_el0);
}

/* A record CPU0 reads, cleaned to the point of coherency: its core may power down first. */
static __always_inline void m1_clean(const void *p)
{
	asm volatile("dc civac, %0" : : "r"(p) : "memory");
	dsb(sy);
}

/*
 * The only PMU bytes either path touches: writes to the spare scratch bytes
 * and boot stage 0x50 or 0x30, reads of the scratch block and the RTC.
 */
static bool m1_pmu_ok(bool write, unsigned int addr, const u8 *b, unsigned int n)
{
	unsigned int end = addr + n;

	if (!n || n > 8)
		return false;
	if (!write)
		return (addr >= 0x6000 && end <= 0x6020) || (addr == M1_PMU_RTC && n == 6);
	if (addr == 0x6001)
		return n == 1 && (b[0] == 0x50 || b[0] == 0x30);
	return (addr >= 0x6004 && end <= 0x6008) || (addr >= 0x6010 && end <= 0x6017);
}

/* The kernel's SPMI path, through the PMU's nvmem: sleeps, so prepare_late only. */
static int m1_kwrite(unsigned int addr, const u8 *b, unsigned int n)
{
	int ret;

	if (!m1_pmu_ok(true, addr, b, n))
		return -EPERM;
	ret = nvmem_device_write(m1_nvmem, addr, n, (void *)b);
	return ret == n ? 0 : ret < 0 ? ret : -EIO;
}

static int m1_kread(unsigned int addr, u8 *b, unsigned int n)
{
	int ret;

	if (!m1_pmu_ok(false, addr, b, n))
		return -EPERM;
	ret = nvmem_device_read(m1_nvmem, addr, n, b);
	return ret == n ? 0 : ret < 0 ? ret : -EIO;
}

/* K1: the breadcrumbs zeroed, boot stage 0x50, stage 0x01, both read back, the RTC's second. */
static int m1_pmu_start(void)
{
	static const u8 zero[7];
	u8 v, rtc[6];
	int ret;

	ret = m1_kwrite(0x6004, zero, 4);
	if (!ret)
		ret = m1_kwrite(0x6010, zero, 7);
	if (ret)
		return ret;
	m1_sent_50 = true;
	v = 0x50;
	ret = m1_kwrite(0x6001, &v, 1);
	if (!ret) {
		v = 0x01;
		ret = m1_kwrite(0x6004, &v, 1);
	}
	if (!ret) {
		ret = m1_kread(0x6001, &v, 1);
		if (!ret && v != 0x50)
			ret = -EIO;
	}
	if (!ret) {
		ret = m1_kread(0x6004, &v, 1);
		if (!ret && v != 0x01)
			ret = -EIO;
	}
	if (!ret)
		ret = m1_kread(M1_PMU_RTC, rtc, 6);
	if (!ret)
		ret = m1_kwrite(0x6010, rtc + 2, 4);
	return ret;
}

/*
 * Every way back from the replay once 0x50 has gone out, whether or not that
 * write said it landed: boot stage 0x30 again (a second try if the first
 * fails), and the breadcrumbs zeroed. m1_undo keeps the first error.
 */
static void m1_pmu_undo(void)
{
	static const u8 v30 = 0x30, zero[7];
	int ret, err;

	if (!m1_sent_50)
		return;
	ret = m1_kwrite(0x6001, &v30, 1);
	if (ret)
		ret = m1_kwrite(0x6001, &v30, 1);
	err = m1_kwrite(0x6004, zero, 4);
	ret = ret ?: err;
	err = m1_kwrite(0x6010, zero, 7);
	ret = ret ?: err;
	m1_undo = ret;
	pr_emerg("m1: boot stage 0x30 again and the breadcrumbs zeroed: %d\n", ret);
}

/*
 * One raw command to the PMU on nub-spmi0, polled, as aurora's spmi_raw_cmd()
 * sends it on gen 1: the command word, then the data words, LE; then the
 * reply word (ACK for a write, a parity bit per byte read), the data words,
 * and the FIFO empty again. 50 ms for the reply, as aurora's poll. CPU0
 * only, from step 4 on, with every other core parked.
 */
static int m1_spmi_raw(bool write, unsigned int addr, u8 *b, unsigned int n)
{
	void __iomem *s = m1_spmi;
	u32 cmd, reply, w;
	unsigned int i;
	u64 t0;

	if (!m1_pmu_ok(write, addr, b, n))
		return -EPERM;
	cmd = (write ? M1_SPMI_EXT_WRITEL : M1_SPMI_EXT_READL) | (n - 1);
	cmd |= M1_PMU_SID << 8 | BIT(15) | addr << 16;
	writel(cmd, s + M1_SPMI_CMD);
	for (i = 0; write && i < n; i += 4) {
		w = 0;
		memcpy(&w, b + i, min(4U, n - i));
		writel(w, s + M1_SPMI_CMD);
	}
	t0 = m1_now();
	while (readl(s + M1_SPMI_STATUS) & M1_SPMI_RX_EMPTY) {
		if (m1_now() - t0 > m1_tps / 20)
			return -ETIMEDOUT;
		cpu_relax();
	}
	reply = readl(s + M1_SPMI_RSP);
	for (i = 0; !write && i < n; i += 4) {
		if (readl(s + M1_SPMI_STATUS) & M1_SPMI_RX_EMPTY)
			return -EIO;
		w = readl(s + M1_SPMI_RSP);
		memcpy(b + i, &w, min(4U, n - i));
	}
	if (!(readl(s + M1_SPMI_STATUS) & M1_SPMI_RX_EMPTY))
		return -EIO;
	if (write ? !(reply & M1_SPMI_REPLY_ACK) : (~(reply >> 16) & (BIT(n) - 1)))
		return -EIO;
	return 0;
}

/* Step 4: the FIFO flushed and every event acked, as aurora recovers, then 0x6001 must read 0x50. */
static bool m1_raw_start(void)
{
	unsigned int i;
	u8 v = 0;

	writel(M1_SPMI_ACT_FLUSH, m1_spmi + M1_SPMI_ACT);
	for (i = 0; i < M1_SPMI_WORDS; i++)
		writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));
	return !m1_spmi_raw(false, 0x6001, &v, 1) && v == 0x50;
}

/* A breadcrumb, raw; the first error stops them all. */
static void m1_crumb(unsigned int addr, const u8 *b, unsigned int n)
{
	if (m1_raw && m1_spmi_raw(true, addr, (u8 *)b, n))
		m1_raw = false;
}

static void m1_stage(u8 s)
{
	m1_crumb(0x6004, &s, 1);
}

/* Only the cores' and the CPU complexes' words: this page also holds ps_pmp. */
static unsigned int m1_ps_state(unsigned int off)
{
	switch (off) {
	case M1_PS_CPU0: case 0x08: case 0x20: case 0x28: case 0x30: case 0x40: case 0x48: case 0x50:
	case M1_PS_ECPM: case M1_PS_PCPM0: case M1_PS_PCPM1:
		return (readl(m1_ps + off) >> 4) & 0xf;
	}
	return 0xff;
}

/* Up to 10 ms for a power state to read off. */
static bool m1_wait_off(unsigned int off)
{
	u64 t0 = m1_now();

	while (m1_ps_state(off)) {
		if (m1_now() - t0 > m1_tps / 100)
			return false;
		cpu_relax();
	}
	return true;
}

static void m1_aic_mask_all(void)
{
	unsigned int i;

	for (i = 0; i < m1_aic_words; i++)
		writel_relaxed(U32_MAX, m1_aic + m1_aic_mask + 4 * i);
	dsb(sy);
}

/* Each read of the event register takes this core's next event; an IRQ stays masked (AIC2 masks it on delivery). */
static unsigned int m1_drain_aic(void)
{
	unsigned int n = 0;

	while (n < 64 && readl(m1_aic_event))
		n++;
	return n;
}

/*
 * This core's own interrupt sources, off, as aic_init_cpu() masks them when
 * Linux brings a CPU up (irq-apple-aic.c at 4fdd0fc5): its two timers (the
 * host's, at EL2 with VHE), the guest timers' FIQ, the PMC's and the uncore
 * PMC's FIQ, and a pending fast IPI. A pending interrupt ends a WFI even
 * with every exception masked: PR #170's CPU1, on a J314s, never powered off
 * for one.
 */
static void m1_quiet_core(void)
{
	write_sysreg(0, cntp_ctl_el0);
	write_sysreg(0, cntv_ctl_el0);
	if (is_kernel_in_hyp_mode())
		sysreg_clear_set_s(M1_VM_TMR_FIQ_ENA_EL2, M1_VM_TMR_FIQ_V | M1_VM_TMR_FIQ_P, 0);
	sysreg_clear_set_s(SYS_IMP_APL_PMCR0_EL1, PMCR0_IMODE | PMCR0_IACT, 0);
	sysreg_clear_set_s(M1_UPMCR0_EL1, M1_UPMCR0_IMODE, 0);
	write_sysreg_s(M1_IPI_SR_PENDING, M1_IPI_SR_EL1);
	isb();
}

/* ACC_OVRD's deep-sleep fields, as e1_cpu_down() sets them. */
static void m1_arm_deep_sleep(void)
{
	u64 acc;

	asm volatile("mrs %0, s3_5_c15_c6_0" : "=r"(acc));
	acc &= ~(GENMASK_ULL(18, 13) | GENMASK_ULL(26, 25));
	acc |= (3ULL << 13) | (2ULL << 15) | (3ULL << 17) | (3ULL << 25) |
	       BIT_ULL(32) | BIT_ULL(34);
	asm volatile("msr s3_5_c15_c6_0, %0; isb" : : "r"(acc));
}

/*
 * CYC_OVRD as e1_cpu_down() sets it, then WFI for good, with Naeem's and
 * XNU's ISB. With mark (CPU0, observe=1), each WFI that returns counts in
 * mark's wfi_ret, cleaned for CPU7: Naeem's test 8 reads a WFI that returns
 * as a core that wasn't power-gated.
 */
static void __noreturn m1_deep_wfi(struct m1_core *mark)
{
	u64 cyc;

	asm volatile("mrs %0, s3_5_c15_c5_0" : "=r"(cyc));
	cyc &= ~(GENMASK_ULL(25, 20) | BIT_ULL(0));
	cyc |= 0x3000001ULL;
	asm volatile("msr s3_5_c15_c5_0, %0; isb" : : "r"(cyc));
	for (;;) {
		asm volatile("dsb sy; isb; wfi" : : : "memory");
		if (mark) {
			WRITE_ONCE(mark->wfi_ret, READ_ONCE(mark->wfi_ret) + 1);
			m1_clean(mark);
		}
	}
}

/* The seconds before watchdog n resets the SoC, rounded, if it's armed to (RESET_EN); else U32_MAX. */
static u32 m1_wd_left(unsigned int n)
{
	u32 cur = readl(m1_wdt + M1_WD_CUR(n)), bite = readl(m1_wdt + M1_WD_BITE(n));

	if (!(readl(m1_wdt + M1_WD_CTRL(n)) & M1_WD_RESET_EN))
		return U32_MAX;
	return bite > cur ? DIV_ROUND_CLOSEST_ULL((u64)(bite - cur), m1_tps) : 0;
}

/*
 * observe=1, on CPU7 from the commit on (CPU0 wrote CPU7's stop bit before
 * it, and is in its own deep WFI by now): watch, record, stop. Every
 * exception is masked and its interrupt sources are quiet since the park;
 * it touches nothing behind a clock the table parked: PMGR and AON reads,
 * raw SPMI to the OK'd bytes (as Naeem's run 2 and macOS's alarm talk SPMI
 * after the commit), nub-spmi0's acks, then ACC_OVRD (if P1's last), CYC_OVRD
 * and its WFI. Up to 1 s of polls. The breadcrumbs, and the order that keeps
 * each stage's meaning sure, are in the m1 comment.
 */
static void __noreturn m1_observe(struct m1_core *c)
{
	static const u8 soc[7] = { 0, 1, 2, 3, 5, 6, 8 };	/* EACC PACC0 PACC1 ANE0 AFR DISPLAY AVEMSR */
	u32 left = U32_MAX, ctrl0, ctrl1, ctrl2, wake;
	u8 b[4], off = 0, cl = 0, wk = BIT(7), isr;
	unsigned int i;
	u64 t0;

	write_sysreg_s(M1_IPI_SR_PENDING, M1_IPI_SR_EL1);
	isb();
	m1_drain_aic();
	m1_raw = READ_ONCE(c->raw);
	m1_stage(0x60);

	t0 = m1_now();
	while (m1_ps_state(M1_PS_CPU0) && m1_now() - t0 < m1_tps / 2)
		cpu_relax();
	if (!m1_ps_state(M1_PS_CPU0))
		off |= BIT(0);
	t0 = m1_now();
	while (m1_ps_state(M1_PS_ECPM) && m1_now() - t0 < m1_tps / 2)
		cpu_relax();
	if (!m1_ps_state(M1_PS_ECPM))
		off |= BIT(7);
	off |= READ_ONCE(c->down) & 0x7e;

	ctrl0 = readl(m1_wdt + M1_WD_CTRL(0));
	ctrl1 = readl(m1_wdt + M1_WD_CTRL(1));
	ctrl2 = readl(m1_wdt + M1_WD_CTRL(2));
	for (i = 0; i < 3; i++)
		left = min(left, m1_wd_left(i));
	/* The stage with its bytes, in one command, as step 7 sends 0x30. */
	b[0] = 0x61;
	b[1] = off;
	b[2] = (ctrl2 & 7) | (ctrl0 & M1_WD_RESET_EN ? BIT(3) : 0) | (ctrl1 & 7) << 4 |
	       (READ_ONCE(m1_core[0].wfi_ret) ? BIT(7) : 0);
	b[3] = min(left, 255U);
	m1_crumb(0x6004, b, 4);

	for (i = 0; i < ARRAY_SIZE(soc); i++) {
		if (readl(m1_soc + 4 * soc[i]) & BIT(4))
			cl |= BIT(i);
	}
	if ((readl(m1_sio + M1_PS_SIO_CPU) >> 4) & 0xf)
		cl |= BIT(7);
	for (i = 0; i < ARRAY_SIZE(m1_wake_pre); i++) {
		wake = readl(m1_wake_status + 4 * i);
		if (wake & ~READ_ONCE(m1_wake_pre[i]))
			wk |= BIT(i);
	}
	/* 0x6012-0x6016 change meaning from here: say so before the first byte does. */
	m1_stage(0x62);
	b[0] = readl(m1_wdx + M1_WDX_PROG0);
	b[1] = readl(m1_wdx + M1_WDX_PROG1);
	m1_crumb(0x6012, b, 2);
	b[0] = cl;
	b[1] = wk;
	m1_crumb(0x6015, b, 2);

	/* What m1_park records at a stop: anything pending would end its WFI. */
	write_sysreg_s(M1_IPI_SR_PENDING, M1_IPI_SR_EL1);
	isb();
	m1_drain_aic();
	dsb(sy);
	isb();
	isr = (read_sysreg(isr_el1) >> 6) & 7;
	m1_stage(isr ? 0x6e : 0x6f);

	for (i = 0; i < M1_SPMI_WORDS; i++)
		writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));
	dsb(sy);
	isb();
	if (READ_ONCE(c->arm))
		m1_arm_deep_sleep();
	m1_deep_wfi(NULL);
}

/*
 * Steps 3 and 6 on each other core, from CPU0's IPI (an async call: its csd
 * is free before this runs, so never returning blocks nothing): park, and
 * when CPU0 says go, stop for good.
 */
static void m1_park(void *data)
{
	struct m1_core *c = data;
	unsigned int cpu = c - m1_core;
	unsigned int n;

	local_daif_mask();
	m1_quiet_core();
	n = m1_drain_aic();
	c->drained = n;
	smp_store_release(&c->parked, 1);
	m1_clean(c);
	while (!smp_load_acquire(&c->go))
		cpu_relax();
	if (READ_ONCE(c->observe))
		m1_observe(c);

	write_sysreg_s(M1_IPI_SR_PENDING, M1_IPI_SR_EL1);
	isb();
	c->drained = n + m1_drain_aic();
	c->isr = (read_sysreg(isr_el1) >> 6) & 7;
	smp_store_release(&c->left, 1);
	m1_clean(c);
	writel_relaxed(m1_cpus[cpu].stop, m1_cpu_start);
	dsb(sy);
	isb();
	if (READ_ONCE(c->arm))
		m1_arm_deep_sleep();
	m1_deep_wfi(NULL);
}

/* Steps K3 and 1-10, on CPU0 right after MBSE=slpa was accepted. Never returns. */
static void __noreturn m1_after_slpa(void)
{
	static const u8 two = 0x02;
	void __iomem *commit = post_map[ARRAY_SIZE(e1_post_slpa) - 1];
	u8 parked = 0, left = 0, down = 0, isrs = 0, flags = BIT(7), b[7], rtc[6] = {};
	struct m1_core *o = &m1_core[7];
	unsigned int i, cpu, drained = 0;
	bool obs;
	u64 t0;

	/* K3 */
	if (m1_kwrite(0x6004, &two, 1))
		flags |= BIT(3);

	/* 1, 2 */
	preempt_disable();
	local_irq_disable();
	m1_aic_mask_all();

	/* 3: park */
	for (i = 0; i < ARRAY_SIZE(m1_order); i++) {
		cpu = m1_order[i];
		if (smp_call_function_single_async(cpu, &m1_csd[cpu]))
			continue;
		t0 = m1_now();
		while (!smp_load_acquire(&m1_core[cpu].parked) && m1_now() - t0 < m1_tps / 100)
			cpu_relax();
		if (smp_load_acquire(&m1_core[cpu].parked))
			parked |= BIT(cpu);
	}

	/* 4 */
	m1_aic_mask_all();
	if (readl(m1_wdt + M1_WD1_CTRL)) {
		writel(0, m1_wdt + M1_WD1_CTRL);
		flags |= BIT(6);
	}
	m1_raw = parked == M1_OTHERS && m1_raw_start();
	/* observe=1: CPU7, P1's last, stays up through the commit if it parked. */
	obs = observe && (parked & BIT(7));
	b[0] = 0x10;
	b[1] = parked | (obs ? BIT(0) : 0);	/* CPU0 never parks: bit 0 marks observe */
	m1_crumb(0x6004, b, 2);

	/* 5: the table's other entries, the commit held back */
	for (i = 0; i < ARRAY_SIZE(e1_post_slpa) - 1; i++) {
		const struct e1_mmio_write *w = &e1_post_slpa[i];

		if (e1_post_is_eh(w))
			continue;
		if (w->width == 8)
			writeq_relaxed(w->val, post_map[i]);
		else
			writel_relaxed(w->val, post_map[i]);
	}
	dsb(sy);
	m1_stage(0x20);

	/* 6: release */
	for (i = 0; i < ARRAY_SIZE(m1_order); i++) {
		struct m1_core *c;

		cpu = m1_order[i];
		c = &m1_core[cpu];
		if (!(parked & BIT(cpu)) || (obs && cpu == 7))
			continue;
		m1_stage(0x21 + i);
		if ((cpu == 4 && (parked & M1_P0) == M1_P0) || (cpu == 7 && (parked & M1_P1) == M1_P1))
			WRITE_ONCE(c->arm, 1);
		smp_store_release(&c->go, 1);
		dsb(sy);
		if (m1_wait_off(m1_cpus[cpu].ps))
			down |= BIT(cpu);
		if (smp_load_acquire(&c->left)) {
			left |= BIT(cpu);
			if (c->isr)
				isrs |= BIT(cpu);
			drained += c->drained;
		}
		if (cpu == 4 && m1_wait_off(M1_PS_PCPM0))
			flags |= BIT(4);
		if (cpu == 7 && m1_wait_off(M1_PS_PCPM1))
			flags |= BIT(5);
	}

	/* 7 */
	b[0] = 0x30;
	b[1] = parked | (obs ? BIT(0) : 0);
	b[2] = left;
	b[3] = down;
	m1_crumb(0x6004, b, 4);
	for (i = 0; i < ARRAY_SIZE(m1_wake_mask); i++)
		writel(m1_wake_mask[i], m1_aon + 4 * i);
	for (i = 0; i < M1_SPMI_WORDS; i++)
		writel(0, m1_spmi + M1_SPMI_EN(i));

	/* 8 */
	m1_quiet_core();
	drained += m1_drain_aic();
	flags |= (read_sysreg(isr_el1) >> 6) & 7;
	if (m1_raw && m1_spmi_raw(false, M1_PMU_RTC, rtc, 6))
		m1_raw = false;
	memcpy(b, rtc + 2, 4);
	b[4] = flags;
	b[5] = isrs;
	b[6] = min(drained, 255U);
	m1_crumb(0x6010, b, 7);
	m1_stage(0x50);

	/* 9 */
	for (i = 0; i < M1_SPMI_WORDS; i++)
		writel(U32_MAX, m1_spmi + M1_SPMI_ACK(i));
	dsb(sy);
	local_daif_mask();
	if (obs) {
		/* macOS's order: the stop bit of a core that is still up, before the commit. */
		for (i = 0; i < ARRAY_SIZE(m1_wake_pre); i++)
			m1_wake_pre[i] = readl(m1_wake_status + 4 * i);
		m1_clean(m1_wake_pre);
		if ((parked & M1_P1) == M1_P1)
			WRITE_ONCE(o->arm, 1);
		writel_relaxed(m1_cpus[7].stop, m1_cpu_start);
		dsb(sy);
	}
	writel_relaxed(1, commit);
	writel_relaxed(1, commit);
	writel_relaxed(1, commit);
	dsb(sy);
	if (obs) {
		o->raw = m1_raw;
		o->down = down;
		WRITE_ONCE(o->observe, 1);
		smp_store_release(&o->go, 1);
		m1_clean(o);
	}

	/* 10 */
	m1_arm_deep_sleep();
	m1_deep_wfi(obs ? &m1_core[0] : NULL);
}

static void m1_read_mpidr(void *p)
{
	*(u64 *)p = read_cpuid_mpidr() & MPIDR_HWID_BITMASK;
}

static int m1_match_node(struct device *dev, const void *np)
{
	return dev->of_node == np;
}

/* Any of n words from pa on the never-write list. */
static bool __init m1_never(u64 pa, unsigned int n)
{
	unsigned int i;

	for (i = 0; i < n; i++) {
		if (e1_never_write(pa + 4 * i))
			return true;
	}
	return false;
}

/*
 * observe=1 at load: what CPU7 reads after the commit, mapped, and logged as
 * it is now, awake, for comparison. Reads only. Also the candidates for
 * T6000's CPM gating (M1_CPM_GATE, inferred).
 */
static int __init m1_observe_init(void)
{
	static const char * const soc[9] = { "EACC", "PACC0", "PACC1", "ANE0", "4", "AFR", "DISPLAY", "7", "AVEMSR" };
	unsigned int i;

	m1_soc = ioremap_np(M1_SOC_CLUSTERS, PAGE_SIZE);
	m1_sio = ioremap_np(M1_PS_SIO_PAGE, PAGE_SIZE);
	m1_wdx = ioremap_np(M1_WDX, PAGE_SIZE);
	m1_cpm = ioremap_np(M1_CPM_GATE, PAGE_SIZE);
	if (!m1_soc || !m1_sio || !m1_wdx || !m1_cpm) {
		pr_err("m1: observe: a mapping failed; refusing\n");
		return -ENOMEM;
	}
	for (i = 0; i < 3; i++)
		pr_info("m1: observe: WD%u count %08x bite %08x ctrl %x\n", i, readl(m1_wdt + M1_WD_CUR(i)),
			readl(m1_wdt + M1_WD_BITE(i)), readl(m1_wdt + M1_WD_CTRL(i)));
	pr_info("m1: observe: wdt page 2: +0x8 %08x +0xc %08x +0x20 %08x +0x28 %08x +0x2c %08x +0x40 %08x\n",
		readl(m1_wdx + 0x8), readl(m1_wdx + 0xc), readl(m1_wdx + 0x20), readl(m1_wdx + M1_WDX_PROG0),
		readl(m1_wdx + 0x2c), readl(m1_wdx + M1_WDX_PROG1));
	pr_info("m1: observe: AP wake status %08x %08x %08x %08x %08x %08x %08x\n",
		readl(m1_wake_status), readl(m1_wake_status + 4), readl(m1_wake_status + 8),
		readl(m1_wake_status + 12), readl(m1_wake_status + 16), readl(m1_wake_status + 20),
		readl(m1_wake_status + 24));
	for (i = 0; i < ARRAY_SIZE(soc); i++)
		pr_info("m1: observe: soc cluster %u (%s): %08x\n", i, soc[i], readl(m1_soc + 4 * i));
	pr_info("m1: observe: SIO %08x SIO_CPU %08x; CPU0 %08x ECPM %08x\n", readl(m1_sio + M1_PS_SIO),
		readl(m1_sio + M1_PS_SIO_CPU), readl(m1_ps + M1_PS_CPU0), readl(m1_ps + M1_PS_ECPM));
	pr_info("m1: observe: 0x%llx +0x30 %08x +0x34 %08x +0x38 %08x +0x80 %08x +0x88 %08x\n", M1_CPM_GATE,
		readl(m1_cpm + 0x30), readl(m1_cpm + 0x34), readl(m1_cpm + 0x38), readl(m1_cpm + 0x80),
		readl(m1_cpm + 0x88));
	return 0;
}

/*
 * m1 at load: the CPUs as m1_cpus has them, AIC2's layout (one die, 4096
 * IRQs), the stock SPMI controller (rtc-alarm-test's isn't, and has its own
 * interrupt), held loaded from here on, so that rtc-alarm-test/load's rmmod
 * of it fails rather than leave the PMU's nvmem without its controller; the
 * PMU's nvmem with boot stage 0x30; and every address m1 writes off the
 * never-write list. Then everything it reads or writes, mapped. Reads only:
 * the words it writes later are logged as they are now.
 */
static int __init m1_init(void)
{
	struct platform_device *pdev;
	struct device_driver *drv;
	struct device_node *np;
	u32 info1, info3, max_irq, nr_irq;
	u8 v = 0, crumbs[0x20];
	unsigned int cpu, i;

	m1_tps = read_sysreg(cntfrq_el0);
	if (m1_tps < 1000000) {
		pr_err("m1: CNTFRQ_EL0 is %llu; refusing\n", m1_tps);
		return -ENODEV;
	}
	if (num_possible_cpus() != M1_NCPUS || num_online_cpus() != M1_NCPUS) {
		pr_err("m1: %u CPUs possible and %u online, not %u; refusing\n",
		       num_possible_cpus(), num_online_cpus(), M1_NCPUS);
		return -ENODEV;
	}
	for (cpu = 0; cpu < M1_NCPUS; cpu++) {
		const struct m1_cpu *c = &m1_cpus[cpu];
		u64 aff1 = MPIDR_AFFINITY_LEVEL(c->mpidr, 1), aff0 = MPIDR_AFFINITY_LEVEL(c->mpidr, 0);
		u64 mpidr = ~0ULL;

		if (smp_call_function_single(cpu, m1_read_mpidr, &mpidr, 1) || mpidr != c->mpidr ||
		    c->stop != BIT(4 * aff1 + aff0) || c->ps != 0x20 * aff1 + 8 * aff0) {
			pr_err("m1: CPU%u is MPIDR 0x%llx, not 0x%llx as m1_cpus has it; refusing\n",
			       cpu, mpidr, c->mpidr);
			return -ENODEV;
		}
		INIT_CSD(&m1_csd[cpu], m1_park, &m1_core[cpu]);
	}

	np = of_find_node_by_path(M1_SPMI_NODE);
	pdev = np ? of_find_device_by_node(np) : NULL;
	of_node_put(np);
	if (pdev) {
		device_lock(&pdev->dev);
		drv = pdev->dev.driver;
		if (drv && drv->owner && !strcmp(module_name(drv->owner), "spmi_apple_controller") &&
		    try_module_get(drv->owner))
			m1_spmi_owner = drv->owner;
		device_unlock(&pdev->dev);
		put_device(&pdev->dev);
	}
	if (!m1_spmi_owner) {
		pr_err("m1: %s isn't bound to the stock spmi_apple_controller (rtc-alarm-test loaded?); refusing\n",
		       M1_SPMI_NODE);
		return -ENODEV;
	}
	np = of_find_node_by_path(M1_PMU_NODE);
	m1_nvmem = np ? nvmem_device_find(np, m1_match_node) : ERR_PTR(-ENODEV);
	of_node_put(np);
	if (IS_ERR(m1_nvmem)) {
		pr_err("m1: no nvmem for %s: %ld; refusing\n", M1_PMU_NODE, PTR_ERR(m1_nvmem));
		m1_nvmem = NULL;
		return -ENODEV;
	}
	if (m1_kread(0x6001, &v, 1) || v != 0x30 || m1_kread(0x6000, crumbs, 8) ||
	    m1_kread(0x6008, crumbs + 8, 8) || m1_kread(0x6010, crumbs + 16, 8) ||
	    m1_kread(0x6018, crumbs + 24, 8)) {
		pr_err("m1: the PMU's boot stage isn't 0x30 (0x%02x), or its scratch didn't read; refusing\n", v);
		return -EIO;
	}
	pr_info("m1: PMU 0x6000: %*ph\n", 32, crumbs);

	m1_cpu_start = ioremap_np(M1_CPU_START, PAGE_SIZE);
	m1_ps = ioremap_np(M1_PMGR_PS, PAGE_SIZE);
	m1_aic = ioremap_np(M1_AIC, M1_AIC_SIZE);
	m1_aic_event = ioremap_np(M1_AIC_EVENT, PAGE_SIZE);
	m1_aon = ioremap_np(M1_AON_WAKE & PAGE_MASK, PAGE_SIZE);
	m1_wdt = ioremap_np(M1_WDT, PAGE_SIZE);
	m1_spmi_page = ioremap_np(M1_SPMI & PAGE_MASK, PAGE_SIZE);
	if (!m1_cpu_start || !m1_ps || !m1_aic || !m1_aic_event || !m1_aon || !m1_wdt || !m1_spmi_page) {
		pr_err("m1: a mapping failed; refusing\n");
		return -ENOMEM;
	}
	m1_aon += M1_AON_WAKE & ~PAGE_MASK;

	info1 = readl(m1_aic + 0x4);
	info3 = readl(m1_aic + 0xc);
	nr_irq = info1 & 0xffff;
	max_irq = info3 & 0xffff;
	if (((info1 >> 24) & 0xf) || max_irq != 4096 || !nr_irq || nr_irq > max_irq) {
		pr_err("m1: AIC2 INFO1 0x%08x INFO3 0x%08x: not one die of 4096 IRQs; refusing\n", info1, info3);
		return -ENODEV;
	}
	/* After IRQ_CFG (a word per IRQ), SW_SET and SW_CLR (a bit each). */
	m1_aic_mask = 0x2000 + 4 * max_irq + 2 * 4 * (max_irq / 32);
	m1_aic_words = DIV_ROUND_UP(nr_irq, 32);
	if (m1_aic_mask + 4 * m1_aic_words > M1_AIC_SIZE) {
		pr_err("m1: AIC2 MASK_SET past its window; refusing\n");
		return -ENODEV;
	}
	if (m1_never(M1_CPU_START, 1) || m1_never(M1_AIC + m1_aic_mask, m1_aic_words) ||
	    m1_never(M1_AON_WAKE, ARRAY_SIZE(m1_wake_mask)) || m1_never(M1_WDT + M1_WD1_CTRL, 1) ||
	    m1_never(M1_SPMI + M1_SPMI_CMD, 1) || m1_never(M1_SPMI + M1_SPMI_EN(0), M1_SPMI_WORDS) ||
	    m1_never(M1_SPMI + M1_SPMI_ACK(0), M1_SPMI_WORDS) || m1_never(M1_SPMI + M1_SPMI_ACT, 1)) {
		pr_err("m1: a write would touch the never-write list; refusing\n");
		return -EPERM;
	}

	pr_info("m1: %u IRQs, MASK_SET 0x%llx (%u words); WD1 ctrl 0x%x; AP wake masks %08x %08x %08x %08x %08x %08x %08x\n",
		nr_irq, M1_AIC + m1_aic_mask, m1_aic_words, readl(m1_wdt + M1_WD1_CTRL),
		readl(m1_aon), readl(m1_aon + 4), readl(m1_aon + 8), readl(m1_aon + 12),
		readl(m1_aon + 16), readl(m1_aon + 20), readl(m1_aon + 24));
	for (i = 0; i < M1_SPMI_WORDS; i++)
		pr_info("m1: nub-spmi0 word %u: enable %08x, pending %08x\n", i,
			readl(m1_spmi + M1_SPMI_EN(i)), readl(m1_spmi + M1_SPMI_ACK(i)));
	for (cpu = 1; cpu < M1_NCPUS; cpu++)
		pr_info("m1: CPU%u: stop bit 0x%03x, power state %08x\n", cpu, m1_cpus[cpu].stop,
			readl(m1_ps + m1_cpus[cpu].ps));
	return observe ? m1_observe_init() : 0;
}

static void m1_unmap(void)
{
	if (m1_cpu_start)
		iounmap(m1_cpu_start);
	if (m1_ps)
		iounmap(m1_ps);
	if (m1_aic)
		iounmap(m1_aic);
	if (m1_aic_event)
		iounmap(m1_aic_event);
	if (m1_aon)
		iounmap((void __iomem *)((unsigned long)m1_aon & PAGE_MASK));
	if (m1_wdt)
		iounmap(m1_wdt);
	if (m1_spmi_page)
		iounmap(m1_spmi_page);
	if (m1_soc)
		iounmap(m1_soc);
	if (m1_sio)
		iounmap(m1_sio);
	if (m1_wdx)
		iounmap(m1_wdx);
	if (m1_cpm)
		iounmap(m1_cpm);
	if (m1_nvmem)
		nvmem_device_put(m1_nvmem);
	if (m1_spmi_owner)
		module_put(m1_spmi_owner);
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
	/* m1: CPU0, pinned there since begin, with every CPU online. */
	if (m1 && (raw_smp_processor_id() != 0 || num_online_cpus() != M1_NCPUS)) {
		pr_emerg("m1: prepare_late is on CPU%u with %u CPUs online, not on CPU0 with all %u; nothing sent\n",
			 raw_smp_processor_id(), num_online_cpus(), M1_NCPUS);
		return false;
	}
	if (!device_trylock(dev)) {
		pr_emerg("replay: the SMC's device lock is busy; nothing sent\n");
		return false;
	}
	if (!dev->driver || strcmp(dev->driver->name, SMC_DRIVER) || dev_get_drvdata(dev) != smc) {
		pr_emerg("replay: macsmc isn't bound; nothing sent\n");
		goto unlock;
	}
	if (m1) {
		m1_ret = m1_pmu_start();
		pr_emerg("m1: the PMU, breadcrumbs zeroed, boot stage 0x50, stage 0x01: %d%s\n", m1_ret,
			 m1_ret ? "; nothing more sent" : "");
		if (m1_ret)
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
		if (m1)
			m1_after_slpa();
		if (replay & 8)
			e1_after_slpa();
		done = true;
	}
unlock:
	if (m1 && !done)
		m1_pmu_undo();
	device_unlock(dev);
	return done;
}

/*
 * Step 4, after the noirq pass: where E1's replay runs. With 13 or 15, this
 * suspend replays or fails (see the replays, above): it fails with -EIO if the
 * replay has run already, was called off, or stops short.
 */
static int e1_prepare_late(void)
{
	u64 t = trace_clock_local();
	unsigned int n = ++prepare_late_calls;
	unsigned int done;

	late_t_ns = t;
	late_ret = read_b0av(&late_mv, &late_read_ns);
	log_read("prepare_late", n, t, late_ret, late_mv, late_read_ns);
	if (!(replay & 8)) {
		if (replay && !replay_done)
			e1_replay();
		return 0;
	}
	done = READ_ONCE(replay_done);
	if (done) {
		pr_emerg("replay: %s; this suspend fails with -EIO: with replay=%u loaded, nothing sleeps but the replay\n",
			 done == 1 ? "run already" : "called off", replay);
		return -EIO;
	}
	if (!e1_replay()) {
		pr_emerg("replay: stopped short; this suspend fails with -EIO, so that the Mac wakes with this log\n");
		return -EIO;
	}
	return 0;
}

/*
 * Registered for 13 and 15 only: the suspend_resume tracepoint's
 * suspend_enter, start, is enter_state()'s first line, for every suspend
 * (suspend.c L580). A suspend that begins while the replay hasn't run means
 * an earlier one ended short of prepare_late: the replay is called off. A
 * tracepoint probe can't sleep; this doesn't.
 */
static void e1_suspend_resume(void *data, const char *action, int val, bool start)
{
	unsigned int n;

	if (!start || strcmp(action, "suspend_enter"))
		return;
	n = atomic_inc_return(&attempt_count);
	WRITE_ONCE(attempts, n);
	if (n > 1 && !READ_ONCE(replay_done)) {
		WRITE_ONCE(replay_done, 2);
		pr_emerg("replay: suspend %u began, and the first never reached prepare_late; called off, nothing sent\n",
			 n);
	}
}

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
	if (m1_pinned) {
		set_cpus_allowed_ptr(current, &m1_saved_mask);
		m1_pinned = false;
	}
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
	const struct e1_mmio_write *last;
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
	if (quiet && replay != 15) {
		pr_err("quiet=1 changes only the floor's end: it needs replay=15\n");
		return -EINVAL;
	}
	last = &e1_post_slpa[ARRAY_SIZE(e1_post_slpa) - 1];
	if (quiet && (last->addr != E1_S2R_COMMIT || last->width != 4 || last->val != 1 ||
		      e1_post_is_eh(last))) {
		pr_err("quiet=1: the post-slpa table doesn't end with the commit (1 to 0x%llx); refusing\n",
		       E1_S2R_COMMIT);
		return -EINVAL;
	}
	if (m1 && (replay != 15 || !quiet)) {
		pr_err("m1=1 is built on the quiet floor: it needs replay=15 quiet=1 (and oneway=1)\n");
		return -EINVAL;
	}
	if (observe && !m1) {
		pr_err("observe=1 is part of the M1 recipe: it needs m1=1 (and replay=15 oneway=1 quiet=1)\n");
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
	m1_unmap();
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
		if (!quiet) {
			afnc_map = ioremap_np(E1_AFNC_PAGE, PAGE_SIZE);
			if (!afnc_map) {
				pr_err("can't map 0x%llx (AFNC2/3); refusing\n", E1_AFNC_PAGE);
				ret = -ENOMEM;
				goto unmap;
			}
		}
	}
	if (m1) {
		ret = m1_init();
		if (ret)
			goto unmap;
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
		ret = register_trace_suspend_resume(e1_suspend_resume, NULL);
		if (ret) {
			pr_err("can't register the suspend_resume probe: %d\n", ret);
			goto unmap;
		}
	}
	set_ops(&e1_ops);
	pr_info("loaded: s2idle ops set through %s; times are trace_clock_local(); replay=%u%s%s%s\n",
		sym, replay, quiet ? " quiet=1" : "", m1 ? " m1=1" : "", observe ? " observe=1" : "");
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
	if (replay & 8) {
		unregister_trace_suspend_resume(e1_suspend_resume, NULL);
		tracepoint_synchronize_unregister();
	}
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
