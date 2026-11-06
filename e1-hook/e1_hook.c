// SPDX-License-Identifier: GPL-2.0
/*
 * e1_hook: E1's first check, the hook point (PLAN.md, A5; RESEARCH.md, "Before
 * E1"), in its dry build: no replay. It registers s2idle ops of its own, so
 * that its prepare_late runs at s2idle's step 4. In suspend_enter()
 * (kernel/power/suspend.c at omacom/linux-aurora 4fdd0fc5, aurora14's):
 *
 *   1. dpm_suspend_late()          every device's suspend_late
 *   2. s2idle_ops->prepare         here: log, and read B0AV (the control)
 *   3. dpm_suspend_noirq()         suspend_device_irqs(), every device's
 *                                  suspend_noirq (async ones included), genpd
 *                                  switching each domain off after its last
 *                                  device
 *   4. s2idle_ops->prepare_late    here: log, and read B0AV
 *      suspend_test(TEST_PLATFORM) pm_test=platform waits here and resumes
 *   5. s2idle_loop()
 *
 * begin comes before dpm_suspend_start(); on the way back, restore_early comes
 * before dpm_resume_noirq(), restore before dpm_resume_early(), end after
 * dpm_resume_end(). Each op only logs one line, with its time on the clock
 * ftrace uses by default, so that e1-hook/check can line prepare_late up
 * against the power:* trace events (one line, wrapped here):
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
 * With replay=0 (the default, and what e1-hook/check loads) it never writes an
 * SMC key and touches no MMIO; replay is E1a's, below. Either way it can't fail
 * or abort a suspend: every op returns 0. .check and .wake stay NULL: s2idle_loop() calls
 * .wake instead of pm_wakeup_pending() when it's set.
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
 * about 1.5 s, though it never fails it.
 *
 *   make -C e1-hook && pkexec e1-hook/check       (in a safe boot)
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/delay.h>
#include <linux/device.h>
#include <linux/kallsyms.h>
#include <linux/kprobes.h>
#include <linux/mfd/macsmc.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/string.h>
#include <linux/suspend.h>
#include <linux/time64.h>
#include <linux/trace_clock.h>

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
 * E1a (PLAN.md, A5): replay=5 adds, at the first prepare_late only, maralcbr's
 * sleep-entry keys and PMP request words for J314s, in their order
 * (maralcbr/linux 37be2668, drivers/power/reset/macsmc-reboot.c L839-876):
 *   bit 2: 4 to 0x28e3d8328 (pmgr reg[41] + 0x18328)
 *   bit 0: MBSE = slpw, pmVC = 0x100, WKTP = 2
 *   bit 2: 1 << 61 to 0x28e3d0860 (SOC-DEV-DVFS + 4), then 1 to 0x28e3d8328
 *   bit 0: MBSE = slpa
 * Nothing else: not the 74 post-slpa writes (bit 3), and the CPUs stay up.
 * One way: there's no undo, and a resume may not come. Safe boot only.
 */
static unsigned int replay;
module_param(replay, uint, 0444);
MODULE_PARM_DESC(replay, "LAB ONLY, safe boot only: at the first prepare_late, 1 = maralcbr's SMC sleep keys, 4 = their PMP request words, 5 = both (E1a)");
static unsigned int replay_done;
module_param(replay_done, uint, 0444);
static int replay_ret[4] = { 1, 1, 1, 1 };
static int replay_nret = 4;
module_param_array(replay_ret, int, &replay_nret, 0444);
MODULE_PARM_DESC(replay_ret, "MBSE=slpw, pmVC, WKTP, MBSE=slpa: 0, -errno, or 1 if not sent");

#define E1_PMGR_SLEEP_REQ	0x28e3d8328ULL
#define E1_PMP_DVFS4		0x28e3d0860ULL
static void __iomem *sleep_req_page, *pmp_dvfs_page;

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
 * E1a's replay, in maralcbr's order. Under the SMC device's lock, tried only:
 * if it's busy, nothing is sent. Every write is logged; none can fail the
 * suspend.
 */
static void e1_replay(void)
{
	struct device *dev = &smc_pdev->dev;
	void __iomem *req = sleep_req_page ? sleep_req_page + (E1_PMGR_SLEEP_REQ & ~PAGE_MASK) : NULL;
	void __iomem *dvfs = pmp_dvfs_page ? pmp_dvfs_page + (E1_PMP_DVFS4 & ~PAGE_MASK) : NULL;

	replay_done = 1;
	if (!device_trylock(dev)) {
		pr_emerg("replay: the SMC's device lock is busy; nothing sent\n");
		return;
	}
	if (!dev->driver || strcmp(dev->driver->name, SMC_DRIVER) || dev_get_drvdata(dev) != smc) {
		pr_emerg("replay: macsmc isn't bound; nothing sent\n");
		goto unlock;
	}
	if ((replay & 4) && req) {
		writel(4, req);
		pr_emerg("replay: 4 -> 0x%llx\n", E1_PMGR_SLEEP_REQ);
	}
	if (replay & 1) {
		replay_ret[0] = apple_smc_write_u32(smc, SMC_KEY(MBSE), SMC_KEY(slpw));
		pr_emerg("replay: MBSE=slpw %d\n", replay_ret[0]);
		replay_ret[1] = apple_smc_write_u32(smc, SMC_KEY(pmVC), 0x100);
		pr_emerg("replay: pmVC=0x100 %d\n", replay_ret[1]);
		replay_ret[2] = apple_smc_write_u8(smc, SMC_KEY(WKTP), 2);
		pr_emerg("replay: WKTP=2 %d\n", replay_ret[2]);
	}
	if ((replay & 4) && req && dvfs) {
		writeq(1ULL << 61, dvfs);
		writel(1, req);
		pr_emerg("replay: 1<<61 -> 0x%llx, 1 -> 0x%llx\n", E1_PMP_DVFS4, E1_PMGR_SLEEP_REQ);
	}
	if (replay & 1) {
		replay_ret[3] = apple_smc_write_u32(smc, SMC_KEY(MBSE), SMC_KEY(slpa));
		pr_emerg("replay: MBSE=slpa %d\n", replay_ret[3]);
	}
unlock:
	device_unlock(dev);
}

/* Step 4, after the noirq pass: where E1's replay runs. */
static int e1_prepare_late(void)
{
	u64 t = trace_clock_local();
	unsigned int n = ++prepare_late_calls;

	late_t_ns = t;
	late_ret = read_b0av(&late_mv, &late_read_ns);
	log_read("prepare_late", n, t, late_ret, late_mv, late_read_ns);
	if (replay && !replay_done)
		e1_replay();
	return 0;
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
	unsigned int mv;
	int ret;

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

	if (replay & ~5U) {
		pr_err("replay=%u: only bit 0 (keys) and bit 2 (PMP words) exist\n", replay);
		ret = -EINVAL;
		goto put_mod;
	}
	if (replay && !of_machine_is_compatible("apple,j314s")) {
		pr_err("replay is maralcbr's J314s sequence, and this isn't a J314s\n");
		ret = -ENODEV;
		goto put_mod;
	}
	if (replay & 4) {
		sleep_req_page = ioremap(E1_PMGR_SLEEP_REQ & PAGE_MASK, PAGE_SIZE);
		pmp_dvfs_page = ioremap(E1_PMP_DVFS4 & PAGE_MASK, PAGE_SIZE);
		if (!sleep_req_page || !pmp_dvfs_page) {
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

	set_ops(&e1_ops);
	pr_info("loaded: s2idle ops set through %s; times are trace_clock_local(); replay=%u\n",
		sym, replay);
	return 0;

unmap:
	if (sleep_req_page)
		iounmap(sleep_req_page);
	if (pmp_dvfs_page)
		iounmap(pmp_dvfs_page);
put_mod:
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
	if (sleep_req_page)
		iounmap(sleep_req_page);
	if (pmp_dvfs_page)
		iounmap(pmp_dvfs_page);
	module_put(smc_owner);
	put_device(&smc_pdev->dev);
	pr_info("unloaded: calls begin=%u prepare=%u prepare_late=%u restore_early=%u restore=%u end=%u\n",
		begin_calls, prepare_calls, prepare_late_calls, restore_early_calls,
		restore_calls, end_calls);
}

module_init(e1_hook_init);
module_exit(e1_hook_exit);
MODULE_DESCRIPTION("E1's hook point: s2idle ops that only log and read B0AV (lab only)");
MODULE_LICENSE("GPL");
