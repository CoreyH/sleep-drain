// SPDX-License-Identifier: GPL-2.0
/*
 * pmp_sleep_release: an experiment for the M1 Pro/Max (T600x). It withdraws
 * the PMP report requests named in `ids` for system sleep and asserts them
 * again on resume, the way omacom/linux-aurora #274 (patch 6) does for report
 * entries marked apple,release-for-sleep:
 *
 * - in the noirq phase, after every device's suspend callback has run, so the
 *   display processors are already suspended, and before any resume callback;
 * - only a request the PMP has acknowledged;
 * - under the pmp-report driver's own lock, with its own register sequence, so
 *   it can't race the driver's updates of the same mask.
 *
 * pmp-report is built in and keeps its state private. This reaches the report
 * through its device's drvdata (pmp-report.c sets it to the report), using the
 * leading members of struct apple_pmp_report as the running kernel's BTF shows
 * them (7.2.11.aurora9: dev at 0, offsets at 8, base at 16, lock at 24). Load
 * refuses unless that view checks out: the report's dev must be its device, the
 * offsets must be T600x's, and every id must be an enabled entry under the
 * live device tree's report node. It is built against this kernel's headers,
 * so another kernel won't load it at all.
 *
 *   insmod pmp_sleep_release.ko ids=0xd        0xd is pmp-dispext0
 *
 * Read-only parameters report what happened: last_released (mask withdrawn in
 * the last sleep), sleeps (sleeps that withdrew anything) and failures
 * (writes the PMP didn't acknowledge in time).
 */
#include <linux/bits.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/spinlock.h>
#include <linux/string.h>

#define REPORT_COMPATIBLE "apple,t6000-pmp-v2-report"
#define ENTRY_COMPATIBLE "apple,t6000-pmp-v2-report-entry"
#define REPORT_DRIVER "apple-pmp-report"
#define PMP_REPORT_READY 0x1

/* The leading members of pmp-report.c's structs; nothing after them is used. */
struct apple_pmp_report_offsets {
	u32 tgt_read;
	u32 tgt_write;
	u32 actual;
	u32 status;
};

struct apple_pmp_report {
	struct device *dev;
	const struct apple_pmp_report_offsets *offsets;
	void __iomem *base;
	spinlock_t lock;
};

static char *ids = "0xd";
module_param(ids, charp, 0444);
MODULE_PARM_DESC(ids, "PMP report request ids to withdraw for sleep, comma-separated");

static unsigned long last_released;
module_param(last_released, ulong, 0444);
static unsigned int sleeps;
module_param(sleeps, uint, 0444);
static unsigned int failures;
module_param(failures, uint, 0444);

static struct platform_device *report_pdev;
static struct apple_pmp_report *rep;
static u64 release_mask, released;

/* apple_pmp_report_set_state() from pmp-report.c. */
static int report_set(u32 id, bool enable)
{
	u64 bit = BIT_ULL(id), val;
	unsigned long flags;

	spin_lock_irqsave(&rep->lock, flags);
	val = readq(rep->base + rep->offsets->tgt_read);
	val &= ~bit;
	if (enable)
		val |= bit;
	writeq(val, rep->base + rep->offsets->tgt_write);
	spin_unlock_irqrestore(&rep->lock, flags);
	val = readq(rep->base + rep->offsets->status);
	if (!(val & PMP_REPORT_READY))
		return 0;
	return readq_poll_timeout_atomic(rep->base + rep->offsets->actual, val,
					 !!(val & bit) == enable, 100, 50000);
}

static int pmp_sleep_suspend_noirq(struct device *dev)
{
	u32 id;

	released = 0;
	for (id = 0; id < 64; id++) {
		if (!(release_mask & BIT_ULL(id)))
			continue;
		/* Only an acknowledged request, as patch 6. */
		if (!(readq(rep->base + rep->offsets->status) & PMP_REPORT_READY) ||
		    !(readq(rep->base + rep->offsets->actual) & BIT_ULL(id)))
			continue;
		if (report_set(id, false)) {
			failures++;
			dev_warn(dev, "request 0x%x not withdrawn in time\n", id);
		}
		/* Asserted again on resume all the same. */
		released |= BIT_ULL(id);
	}
	last_released = released;
	if (released)
		sleeps++;
	return 0;
}

static int pmp_sleep_resume_noirq(struct device *dev)
{
	u32 id;

	for (id = 0; id < 64; id++) {
		if (!(released & BIT_ULL(id)))
			continue;
		/* A display processor resuming without its request would starve. */
		if (report_set(id, true) && report_set(id, true)) {
			failures++;
			dev_err(dev, "request 0x%x not asserted again after sleep\n", id);
		}
	}
	released = 0;
	return 0;
}

static const struct dev_pm_ops pmp_sleep_pm_ops = {
	NOIRQ_SYSTEM_SLEEP_PM_OPS(pmp_sleep_suspend_noirq, pmp_sleep_resume_noirq)
};

static struct platform_driver pmp_sleep_driver = {
	.driver = {
		.name = "pmp-sleep-release",
		.pm = pm_sleep_ptr(&pmp_sleep_pm_ops),
	},
};

static struct platform_device *pmp_sleep_pdev;

/* Every id must be an enabled request entry under the report node. */
static int parse_ids(struct device_node *report)
{
	char *copy, *cur, *tok;
	int ret = 0;

	copy = kstrdup(ids, GFP_KERNEL);
	if (!copy)
		return -ENOMEM;
	cur = copy;
	while ((tok = strsep(&cur, ",")) && !ret) {
		struct device_node *child;
		bool found = false;
		u32 id, reg;

		if (!*tok)
			continue;
		if (kstrtou32(tok, 0, &id) || id > 63) {
			pr_err("pmp_sleep_release: bad id %s\n", tok);
			ret = -EINVAL;
			break;
		}
		for_each_available_child_of_node(report, child) {
			if (!of_property_read_u32(child, "reg", &reg) && reg == id &&
			    of_device_is_compatible(child, ENTRY_COMPATIBLE)) {
				found = true;
				of_node_put(child);
				break;
			}
		}
		if (!found) {
			pr_err("pmp_sleep_release: 0x%x is no enabled report entry\n", id);
			ret = -EINVAL;
			break;
		}
		release_mask |= BIT_ULL(id);
	}
	kfree(copy);
	if (!ret && !release_mask)
		ret = -EINVAL;
	return ret;
}

static int __init pmp_sleep_init(void)
{
	struct device_node *np;
	int ret;

	np = of_find_compatible_node(NULL, NULL, REPORT_COMPATIBLE);
	if (!np)
		return -ENODEV;
	report_pdev = of_find_device_by_node(np);
	ret = parse_ids(np);
	of_node_put(np);
	if (ret)
		goto put;
	ret = -ENODEV;
	if (!report_pdev || !report_pdev->dev.driver ||
	    strcmp(report_pdev->dev.driver->name, REPORT_DRIVER))
		goto put;
	rep = platform_get_drvdata(report_pdev);
	/* The layout this module assumes, checked against the live report. */
	if (!rep || rep->dev != &report_pdev->dev || !rep->base || !rep->offsets ||
	    rep->offsets->tgt_read != 0xf80 || rep->offsets->tgt_write != 0x107c0 ||
	    rep->offsets->actual != 0x1000 || rep->offsets->status != 0x10) {
		pr_err("pmp_sleep_release: pmp-report isn't laid out as expected; refusing\n");
		goto put;
	}

	ret = platform_driver_register(&pmp_sleep_driver);
	if (ret)
		goto put;
	pmp_sleep_pdev = platform_device_register_simple("pmp-sleep-release", PLATFORM_DEVID_NONE,
							 NULL, 0);
	if (IS_ERR(pmp_sleep_pdev)) {
		ret = PTR_ERR(pmp_sleep_pdev);
		platform_driver_unregister(&pmp_sleep_driver);
		goto put;
	}
	pr_info("pmp_sleep_release: withdrawing 0x%llx for sleep\n", release_mask);
	return 0;

put:
	if (report_pdev)
		put_device(&report_pdev->dev);
	return ret;
}

static void __exit pmp_sleep_exit(void)
{
	platform_device_unregister(pmp_sleep_pdev);
	platform_driver_unregister(&pmp_sleep_driver);
	put_device(&report_pdev->dev);
}

module_init(pmp_sleep_init);
module_exit(pmp_sleep_exit);
MODULE_DESCRIPTION("Experiment: withdraw T600x PMP report requests for system sleep");
MODULE_LICENSE("GPL");
