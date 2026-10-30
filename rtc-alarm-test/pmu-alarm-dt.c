// SPDX-License-Identifier: GPL-2.0-only
/*
 * Describe the PMU's RTC alarm on J314s (M1 Pro) in the running kernel's
 * device tree, as a t600x DT change would, so the alarm driver can be tested
 * without booting another tree. (A tree saved from one boot can't be booted
 * again: iBoot moves the SEP's and the NVMe controller's firmware memory every
 * boot, and m1n1 writes where to the tree.)
 *
 * - nub-spmi0 (spmi@2920a1300) becomes an interrupt controller on AIC 636, the
 *   line macOS's device tree gives it;
 * - pmic@f's nvmem layout gains the alarm's cells, at the offsets in macOS's
 *   spmi-mpmu@F node (info-rtc_alarm_ctrl, info-rtc_alarm_offset,
 *   info-rtc_irq_mask_offset, info-rtc_alarm_event);
 * - the SMC's rtc node gets those cells, the alarm interrupt (peripheral 2 of
 *   nub-spmi0) and wakeup-source.
 *
 * The SPMI controller and the RTC read these at probe, so load this while
 * neither is bound (the load script sees to it). The change stays until the
 * next boot: the module can't be unloaded.
 */

#include <linux/irq.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>

#define SPMI_PATH	"/soc/spmi@2920a1300"
#define LAYOUT_PATH	SPMI_PATH "/pmic@f/nvmem-layout"
#define AIC_PATH	"/soc/interrupt-controller@28e100000"
#define RTC_PATH	"/soc/smc@290400000/rtc"

#define AIC_IRQ_SPMI0		636
#define SPMI_IRQ_RTC_ALARM	2

static const struct {
	const char *node;
	const char *id;
	u32 reg[2];
} cells[] = {
	{ "rtc-ctrl@1401", "rtc_ctrl", { 0x1401, 1 } },
	{ "rtc-alarm@140c", "rtc_alarm", { 0x140c, 4 } },
	{ "rtc-alarm-mask@14c2", "rtc_alarm_mask", { 0x14c2, 1 } },
	{ "rtc-alarm-event@14c0", "rtc_alarm_event", { 0x14c0, 1 } },
};

static phandle max_phandle(void)
{
	struct device_node *np = NULL;
	phandle top = 0;

	while ((np = of_find_all_nodes(np)))
		top = max(top, np->phandle);
	return top;
}

/* Is a driver bound to the platform device for @np? */
static bool bound(struct device_node *np)
{
	struct platform_device *pdev = of_find_device_by_node(np);
	bool ret = pdev && pdev->dev.driver;

	if (pdev)
		put_device(&pdev->dev);
	return ret;
}

static int pmu_alarm_dt_apply(struct device_node *spmi, struct device_node *layout,
			      struct device_node *aic, struct device_node *rtc)
{
	struct device_node *new[ARRAY_SIZE(cells)];
	const char *names[1 + ARRAY_SIZE(cells)] = { "rtc_offset" };
	u32 phandles[1 + ARRAY_SIZE(cells)];
	u32 irq[] = { 0, 0, AIC_IRQ_SPMI0, IRQ_TYPE_LEVEL_HIGH };
	u32 alarm_irq[3];
	struct of_changeset cs;
	phandle next;
	int i, ret;

	if (of_property_read_bool(spmi, "interrupt-controller")) {
		pr_err("%pOF is an interrupt controller already\n", spmi);
		return -EEXIST;
	}
	if (bound(spmi) || bound(rtc)) {
		pr_err("unbind the SPMI controller and the RTC first\n");
		return -EBUSY;
	}
	if (!aic->phandle ||
	    of_property_count_u32_elems(rtc, "nvmem-cells") != 1 ||
	    of_property_match_string(rtc, "nvmem-cell-names", "rtc_offset") != 0 ||
	    of_property_read_u32(rtc, "nvmem-cells", &phandles[0])) {
		pr_err("%pOF isn't as expected\n", rtc);
		return -EINVAL;
	}

	next = max_phandle() + 1;
	alarm_irq[0] = next;
	alarm_irq[1] = SPMI_IRQ_RTC_ALARM;
	alarm_irq[2] = IRQ_TYPE_EDGE_RISING;

	of_changeset_init(&cs);
	ret = of_changeset_add_prop_u32(&cs, spmi, "phandle", next) ?:
	      of_changeset_add_prop_u32(&cs, spmi, "interrupt-parent", aic->phandle) ?:
	      of_changeset_add_prop_u32_array(&cs, spmi, "interrupts", irq, ARRAY_SIZE(irq)) ?:
	      of_changeset_add_prop_bool(&cs, spmi, "interrupt-controller") ?:
	      of_changeset_add_prop_u32(&cs, spmi, "#interrupt-cells", 2);
	if (ret)
		goto out;

	for (i = 0; i < ARRAY_SIZE(cells); i++) {
		new[i] = of_changeset_create_node(&cs, layout, cells[i].node);
		if (!new[i]) {
			ret = -ENOMEM;
			goto out;
		}
		phandles[1 + i] = next + 1 + i;
		names[1 + i] = cells[i].id;
		ret = of_changeset_add_prop_u32_array(&cs, new[i], "reg", cells[i].reg, 2) ?:
		      of_changeset_add_prop_u32(&cs, new[i], "phandle", phandles[1 + i]);
		if (ret)
			goto out;
	}

	ret = of_changeset_remove_property(&cs, rtc, of_find_property(rtc, "nvmem-cells", NULL)) ?:
	      of_changeset_remove_property(&cs, rtc, of_find_property(rtc, "nvmem-cell-names", NULL)) ?:
	      of_changeset_add_prop_u32_array(&cs, rtc, "nvmem-cells", phandles, ARRAY_SIZE(phandles)) ?:
	      of_changeset_add_prop_string_array(&cs, rtc, "nvmem-cell-names", names, ARRAY_SIZE(names)) ?:
	      of_changeset_add_prop_u32_array(&cs, rtc, "interrupts-extended", alarm_irq,
					      ARRAY_SIZE(alarm_irq)) ?:
	      of_changeset_add_prop_bool(&cs, rtc, "wakeup-source");
	if (ret)
		goto out;

	ret = of_changeset_apply(&cs);
	if (ret)
		goto out;

	/*
	 * A node's phandle is taken from its "phandle" property when it is
	 * attached or unflattened, and the changeset adds these properties
	 * after that. Nothing can have looked the phandles up yet.
	 */
	spmi->phandle = next;
	for (i = 0; i < ARRAY_SIZE(cells); i++)
		new[i]->phandle = phandles[1 + i];

	pr_info("alarm described: %pOF is an interrupt controller (phandle %#x), cells %#x-%#x\n",
		spmi, next, next + 1, next + (u32)ARRAY_SIZE(cells));
out:
	/*
	 * Once applied, the change stays. The new nodes keep the reference
	 * of_changeset_create_node() returned, so they are never freed.
	 */
	of_changeset_destroy(&cs);
	return ret;
}

static int __init pmu_alarm_dt_init(void)
{
	struct device_node *spmi, *layout, *aic, *rtc;
	int ret = -ENODEV;

	if (!of_machine_is_compatible("apple,j314s"))
		return -ENODEV;

	spmi = of_find_node_by_path(SPMI_PATH);
	layout = of_find_node_by_path(LAYOUT_PATH);
	aic = of_find_node_by_path(AIC_PATH);
	rtc = of_find_node_by_path(RTC_PATH);
	if (spmi && layout && aic && rtc)
		ret = pmu_alarm_dt_apply(spmi, layout, aic, rtc);
	else
		pr_err("missing a node: spmi %d, nvmem layout %d, aic %d, rtc %d\n",
		       !!spmi, !!layout, !!aic, !!rtc);

	of_node_put(rtc);
	of_node_put(aic);
	of_node_put(layout);
	of_node_put(spmi);
	return ret;
}
module_init(pmu_alarm_dt_init);

MODULE_DESCRIPTION("Describe the J314s PMU RTC alarm in the live device tree (test)");
MODULE_LICENSE("GPL");
