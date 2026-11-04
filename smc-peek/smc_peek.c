// SPDX-License-Identifier: GPL-2.0
/*
 * smc_peek: list the Apple SMC's keys, read-only. Groundwork for the deep-sleep
 * track on J314s (PLAN.md, A2): which keys exist, and what the sleep keys
 * (MBSE, MBSW and their neighbours) hold awake and around an s2idle sleep.
 *
 * It finds the SMC that macsmc has bound and, each time one of its debugfs
 * files is opened, walks the SMC's key table by index:
 *
 *   /sys/kernel/debug/smc-peek/keys     every key: name, type, size, flags
 *   /sys/kernel/debug/smc-peek/values   the same, plus the value of each key
 *                                       that is readable and not a function
 *
 * It sends only the three read-only commands macsmc exports: get key by index,
 * get key info, and read key. It never writes a key. It doesn't read a key
 * flagged as a function (APPLE_SMC_FUNCTION), whose read may act, unless the
 * key is named in read_funcs; and it never reads a key whose read is known to
 * change something (denied(), whatever read_funcs says). Each walk runs once
 * per open, into a buffer that the file's reads then serve.
 *
 *   insmod smc_peek.ko && cat /sys/kernel/debug/smc-peek/values && rmmod smc_peek
 *   insmod smc_peek.ko read_funcs=MBSE,MBSW     also read these function keys
 *
 * One line per key, fields separated by single spaces:
 *   <index> <key> <type> <size> <flags> <value>
 * key and type are FourCCs ('.' for a byte that isn't printable ASCII), size is
 * decimal, flags two hex digits, and value is the bytes the SMC returned, in
 * hex, in the order it returned them, or one of:
 *   -        not asked for (the keys file), or a zero-size key
 *   func     a function key: not read
 *   deny     a key whose read changes something: not read
 *   noread   not readable
 *   empty    the read succeeded with no bytes
 *   errN     the read failed with -N
 * A line "<index> ! errN" is a key whose name or info couldn't be had. The
 * first line is "# smc-peek key_count=N"; the last, "# end" with counts, is
 * missing if the walk stopped early (it says why on a "# stopped" line). It
 * stops at the first timeout: see drain_after_timeout().
 */
#include <linux/ctype.h>
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/hex.h>
#include <linux/mfd/macsmc.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/sched/signal.h>
#include <linux/slab.h>
#include <linux/string.h>

#define SMC_COMPATIBLE "apple,smc"
#define SMC_DRIVER "macsmc"
#define MAX_KEYS 8192
/* A key's size is a u8. */
#define MAX_VALUE 255
/* "8191 KEY_ TYPE 255 ff " plus 255 bytes in hex and a newline. */
#define LINE_MAX_LEN 600
/* After a timeout, how long the SMC's mutex is held for its late reply. */
#define DRAIN_MS 1000

#define MAX_FUNCS 16

static char *read_funcs = "";
module_param(read_funcs, charp, 0444);
MODULE_PARM_DESC(read_funcs, "Function keys whose value may be read, comma-separated (default none)");

static smc_key funcs[MAX_FUNCS];
static unsigned int nfuncs;

static struct platform_device *smc_pdev;
static struct apple_smc *smc;
/* smc->key_count at load: read without the device lock, unlike smc. */
static u32 key_count;
static struct module *smc_owner;
static struct dentry *dir;

struct walk {
	char *buf;
	size_t len, size;
	u8 val[MAX_VALUE];
};

static __printf(2, 3) void emit(struct walk *w, const char *fmt, ...)
{
	va_list args;

	va_start(args, fmt);
	w->len += vscnprintf(w->buf + w->len, w->size - w->len, fmt, args);
	va_end(args);
}

/* %*ph stops at 64 bytes; a key can hold 255. */
static void emit_hex(struct walk *w, const u8 *val, size_t n)
{
	if (w->size - w->len < 2 * n + 2)
		return;
	bin2hex(w->buf + w->len, val, n);
	w->len += 2 * n;
	emit(w, "\n");
}

static void fourcc(u32 v, char out[5])
{
	int i;

	for (i = 0; i < 4; i++) {
		char c = v >> (24 - 8 * i);

		out[i] = isascii(c) && isprint(c) ? c : '.';
	}
	out[4] = '\0';
}

/*
 * Keys whose read changes something, never read (Asahi's docs, hw/soc/smc.md):
 * MBSe, the power button's pressed-since-last-read flag, which a read clears,
 * and MBSw, its unexplained sibling, by analogy; and the GPIO keys gP?? and
 * gp??, which latch, so that only the first read after a write returns the
 * pin's state, a state gpio-macsmc relies on.
 */
static bool denied(smc_key key)
{
	u16 prefix = key >> 16;

	return key == SMC_KEY(MBSe) || key == SMC_KEY(MBSw) ||
	       prefix == ('g' << 8 | 'P') || prefix == ('g' << 8 | 'p');
}

static bool may_read_func(smc_key key)
{
	unsigned int i;

	for (i = 0; i < nfuncs; i++)
		if (funcs[i] == key)
			return true;
	return false;
}

/*
 * macsmc has one reply slot and no way to resynchronise: a reply that comes
 * after its command timed out completes whichever command is sent next. So
 * the walk stops at its first timeout, takes the SMC's mutex at once and holds
 * it for a while, so that the late reply most likely finds no other driver's
 * command waiting.
 *
 * Only most likely: the exported calls release the mutex before they return
 * -ETIMEDOUT, so a battery or hwmon command already queued on it can go first
 * and take the late reply (one -EIO, "Command sequence mismatch"). Closing
 * that needs macsmc to drain late replies itself, under its own lock. A
 * timeout is rare: none in 37 boots' logs, nor in a walk so far.
 */
static void drain_after_timeout(void)
{
	mutex_lock(&smc->mutex);
	msleep(DRAIN_MS);
	mutex_unlock(&smc->mutex);
}

static void walk(struct walk *w, bool values)
{
	unsigned int done = 0, read = 0, errors = 0;
	u32 count = key_count;
	u32 i;

	emit(w, "# smc-peek key_count=%u\n", count);
	for (i = 0; i < count; i++) {
		struct apple_smc_key_info info;
		char k[5], t[5];
		smc_key key;
		int ret;

		if (smc->boot_stage != APPLE_SMC_INITIALIZED) {
			emit(w, "# stopped at %u: SMC boot stage %d\n", i, smc->boot_stage);
			return;
		}
		if (fatal_signal_pending(current)) {
			emit(w, "# stopped at %u: interrupted\n", i);
			return;
		}
		cond_resched();

		ret = apple_smc_get_key_by_index(smc, i, &key);
		if (ret >= 0)
			ret = apple_smc_get_key_info(smc, key, &info);
		if (ret == -ETIMEDOUT) {
			drain_after_timeout();
			emit(w, "%u ! err%d\n# stopped at %u: the SMC timed out\n", i, ETIMEDOUT, i);
			return;
		}
		if (ret < 0) {
			emit(w, "%u ! err%d\n", i, -ret);
			errors++;
			continue;
		}
		fourcc(key, k);
		fourcc(info.type_code, t);
		emit(w, "%u %s %s %u %02x ", i, k, t, info.size, info.flags);
		done++;

		if (!values || !info.size) {
			emit(w, "-\n");
		} else if (denied(key)) {
			emit(w, "deny\n");
		} else if ((info.flags & APPLE_SMC_FUNCTION) && !may_read_func(key)) {
			emit(w, "func\n");
		} else if (!(info.flags & APPLE_SMC_READABLE)) {
			emit(w, "noread\n");
		} else {
			ret = apple_smc_read(smc, key, w->val, info.size);
			if (ret == -ETIMEDOUT) {
				drain_after_timeout();
				emit(w, "err%d\n# stopped at %u: the SMC timed out\n", ETIMEDOUT, i);
				return;
			}
			if (ret < 0) {
				emit(w, "err%d\n", -ret);
				errors++;
				continue;
			}
			if (ret)
				emit_hex(w, w->val, ret);
			else
				emit(w, "empty\n");
			read++;
		}
	}
	emit(w, "# end keys=%u read=%u errors=%u\n", done, read, errors);
}

static int peek_open(struct inode *inode, struct file *file)
{
	bool values = (uintptr_t)inode->i_private;
	struct device *dev = &smc_pdev->dev;
	struct walk *w;
	int ret = 0;

	w = kzalloc(sizeof(*w), GFP_KERNEL);
	if (!w)
		return -ENOMEM;
	w->size = (size_t)key_count * LINE_MAX_LEN + 256;
	w->buf = kvmalloc(w->size, GFP_KERNEL);
	if (!w->buf) {
		kfree(w);
		return -ENOMEM;
	}

	/* macsmc stays bound for the whole walk: an unbind would free smc. */
	device_lock(dev);
	if (dev->driver && !strcmp(dev->driver->name, SMC_DRIVER) &&
	    dev_get_drvdata(dev) == smc)
		walk(w, values);
	else
		ret = -ENODEV;
	device_unlock(dev);

	if (ret) {
		kvfree(w->buf);
		kfree(w);
		return ret;
	}
	file->private_data = w;
	return 0;
}

static ssize_t peek_read(struct file *file, char __user *ubuf, size_t len, loff_t *pos)
{
	struct walk *w = file->private_data;

	return simple_read_from_buffer(ubuf, len, pos, w->buf, w->len);
}

static int peek_release(struct inode *inode, struct file *file)
{
	struct walk *w = file->private_data;

	kvfree(w->buf);
	kfree(w);
	return 0;
}

static const struct file_operations peek_fops = {
	.owner = THIS_MODULE,
	.open = peek_open,
	.read = peek_read,
	.release = peek_release,
	.llseek = default_llseek,
};

/* read_funcs: four-character key names, comma-separated. */
static int parse_funcs(void)
{
	char *copy, *cur, *tok;
	int ret = 0;

	copy = kstrdup(read_funcs, GFP_KERNEL);
	if (!copy)
		return -ENOMEM;
	cur = copy;
	while ((tok = strsep(&cur, ","))) {
		if (!*tok)
			continue;
		if (strlen(tok) != 4 || nfuncs == MAX_FUNCS || denied(_SMC_KEY(tok))) {
			pr_err("smc_peek: bad read_funcs entry %s\n", tok);
			ret = -EINVAL;
			break;
		}
		funcs[nfuncs++] = _SMC_KEY(tok);
	}
	kfree(copy);
	return ret;
}

static int __init smc_peek_init(void)
{
	struct device_node *np;
	struct device *dev;
	int ret;

	ret = parse_funcs();
	if (ret)
		return ret;
	ret = -ENODEV;
	np = of_find_compatible_node(NULL, NULL, SMC_COMPATIBLE);
	if (!np)
		return -ENODEV;
	smc_pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!smc_pdev)
		return -ENODEV;
	dev = &smc_pdev->dev;

	device_lock(dev);
	if (!dev->driver || strcmp(dev->driver->name, SMC_DRIVER)) {
		pr_err("smc_peek: the SMC isn't bound to %s\n", SMC_DRIVER);
		goto unlock;
	}
	smc = dev_get_drvdata(dev);
	/* The layout this module was built against, checked on the live SMC. */
	if (!smc || smc->dev != dev || smc->boot_stage != APPLE_SMC_INITIALIZED ||
	    !smc->key_count || smc->key_count > MAX_KEYS) {
		pr_err("smc_peek: the SMC isn't as expected (key_count %u); refusing\n",
		       smc ? smc->key_count : 0);
		goto unlock;
	}
	/* Keep macsmc loaded while this module can reach into it. */
	smc_owner = dev->driver->owner;
	if (!try_module_get(smc_owner))
		goto unlock;
	key_count = smc->key_count;
	pr_info("smc_peek: %u keys\n", key_count);
	ret = 0;
unlock:
	device_unlock(dev);
	if (ret)
		goto put;

	dir = debugfs_create_dir("smc-peek", NULL);
	debugfs_create_file("keys", 0400, dir, (void *)0, &peek_fops);
	debugfs_create_file("values", 0400, dir, (void *)1, &peek_fops);
	return 0;

put:
	put_device(dev);
	return ret;
}

static void __exit smc_peek_exit(void)
{
	/* Waits for any walk in progress. */
	debugfs_remove(dir);
	module_put(smc_owner);
	put_device(&smc_pdev->dev);
}

module_init(smc_peek_init);
module_exit(smc_peek_exit);
MODULE_DESCRIPTION("List the Apple SMC's keys and values, read-only");
MODULE_LICENSE("GPL");
