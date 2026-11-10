// wd2_poke (T6000, as root, in a safe boot): one write to WD2's bite register
// from user space, through /dev/mem, awake. Corey's OK of 2026-11-10, for this
// register and this value; PLAN.md ("The order after run 14") and RESEARCH.md
// ("M1 run 14") say what it's for. e1-hook/wd2-bite runs it.
//
//   wd2_poke check   WD2's control, count and bite, read only, and what they say
//   wd2_poke patch   the same reads, then 0x2922b0024 := 0xaba95000 (120 s) if
//                    WD2 is as it is awake, and the three words read again
//
// It's the control runs 13 and 14 lacked. recfg_poke's stores to the reconfig
// SRAM were dropped without a fault, and nothing had shown that a store made
// that way, from user space through a /dev/mem mapping, changes any register on
// this Mac: every write seen to land was the kernel's. WD2's bite is a register
// the same kind of store can be tried on with nothing at stake: awake, WD2 isn't
// armed, and the engine's sequences write the bite themselves before they arm
// it. If the bite reads 120 s after the store, a store like recfg_poke's works
// here, and the SRAM's refusal is the SRAM's. If it still reads 90 s, runs 13
// and 14 say nothing about the SRAM.
//
// There's no address argument and no value argument: the register and its
// values are wd2_bite.h's. patch writes only if the control reads 0 and the bite
// its awake 90 s; with 120 s there already it writes nothing and says so; with
// anything else (WD2 armed, another bite) it writes nothing. The write is one
// aligned 32-bit str, the width the kernel writes this register with. Nothing
// puts the 90 s back: the OK is for one value, the bite of a watchdog that isn't
// armed changes nothing, and a restart brings the 90 s back (inferred: it has
// read 90 s in every boot so far). A line is printed and flushed before every
// read and before the write, so that if one faults the output shows which.
//
// It refuses outside a safe boot (sleepdrain.safe on the kernel's command line
// and the safe boot's flag in /run), as recfg_poke does.
//
// Exit status: 0, WD2 is as it is awake (check), or the bite reads 120 s
// (patch: written now, or there already); 3, WD2 wasn't as expected and nothing
// was written; 5, the write was made and the bite still reads 90 s, the control
// unchanged: a result; 6, the write was made and the three words read some other
// way; 2, usage, not a safe boot, or the memory device didn't open.
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "wd2_bite.h"

#ifndef __aarch64__
#ifndef WD2_MEM
#error "wd2_poke is for arm64 Macs"
#endif
#endif

#ifndef WD2_MEM
#define WD2_MEM "/dev/mem"
#endif
#ifndef WD2_SAFE_FLAG
#define WD2_SAFE_FLAG "/run/sleep-drain-safe/active"
#endif
#ifndef WD2_CMDLINE
#define WD2_CMDLINE "/proc/cmdline"
#endif

#define BITE_PA ((uint64_t)(WD2_WDT + WD2_BITE))

_Static_assert(BITE_PA == UINT64_C(0x2922b0024) && WD2_BITE_NEW == UINT32_C(0xaba95000),
	       "the register and the value Corey OK'd");
_Static_assert((BITE_PA & 3) == 0, "the 32-bit store has to be aligned");

/*
 * Test builds log every access (tests/wd2_bite_test.py), and can drop or change
 * the store: a register that ignores it, or takes another value.
 */
#ifdef WD2_TRACE
static void trace(char op, uint64_t pa, uint32_t v)
{
	static FILE *t;

	if (!t)
		t = fopen(WD2_TRACE, "a");
	if (t) {
		fprintf(t, "%c 0x%" PRIx64 " 0x%08x\n", op, pa, v);
		fflush(t);
	}
}
#else
#define trace(op, pa, v) ((void)0)
#endif
#ifndef WD2_POKE_DROP
#define WD2_POKE_DROP 0
#endif
#ifndef WD2_POKE_STORE
#define WD2_POKE_STORE(v) (v)
#endif

static int mem_fd = -1;

/*
 * 16 KiB around the word, mapped for one access; arm64 makes a /dev/mem mapping
 * device memory (nGnRnE). 16 KiB whatever the process's own page size, as in
 * recfg_poke.
 */
#define MAP_UNIT UINT64_C(0x4000)
static volatile void *map_word(uint64_t pa, int prot)
{
	uint64_t base = pa & ~(MAP_UNIT - 1);
	void *m;

	m = mmap(NULL, (size_t)MAP_UNIT, prot, MAP_SHARED, mem_fd, (off_t)base);
	if (m == MAP_FAILED) {
		fprintf(stderr, "wd2_poke: mmap %#" PRIx64 ": %s\n", pa, strerror(errno));
		exit(2);
	}
	return (volatile char *)m + (pa - base);
}

static uint32_t rd32(uint64_t pa)
{
	const volatile void *p = map_word(pa, PROT_READ);
	uint32_t v;

#ifdef __aarch64__
	asm volatile("ldr %w0, [%1]" : "=r"(v) : "r"(p) : "memory");
#else
	v = *(const volatile uint32_t *)p;
#endif
	trace('r', pa, v);
	return v;
}

/* The only store in this program, and BITE_PA the only address it's ever given. */
static void wr32(uint64_t pa, uint32_t v)
{
	volatile void *p = map_word(pa, PROT_READ | PROT_WRITE);

	trace('w', pa, v);
	if (WD2_POKE_DROP)
		return;
	v = WD2_POKE_STORE(v);
#ifdef __aarch64__
	asm volatile("str %w0, [%1]\n\tdsb sy" : : "r"(v), "r"(p) : "memory");
#else
	*(volatile uint32_t *)p = v;
#endif
}

enum wd2_verdict {
	WD2_AWAKE,	/* not armed, the bite 90 s */
	WD2_NEW,	/* not armed, the bite 120 s */
	WD2_ARMED,	/* the control shows RESET_EN */
	WD2_ODD,	/* another control, or another bite */
};

static const char *verdict_str(enum wd2_verdict v)
{
	switch (v) {
	case WD2_AWAKE:
		return "as it is awake: not armed, its bite 90 s";
	case WD2_NEW:
		return "not armed, its bite the new value, 120 s";
	case WD2_ARMED:
		return "ARMED: its control shows RESET_EN";
	case WD2_ODD:
		break;
	}
	return "not as expected: its control isn't 0, or its bite is neither 90 s nor 120 s";
}

/* The three words, each announced before its read, and what they say. Sets *ctl. */
static enum wd2_verdict look(uint32_t *ctl)
{
	static const struct {
		uint64_t pa;
		const char *what;
	} w[3] = {
		{ WD2_WDT + WD2_CTRL, "WD2's control" },
		{ WD2_WDT + WD2_COUNT, "WD2's count" },
		{ BITE_PA, "WD2's bite" },
	};
	enum wd2_verdict verdict;
	uint32_t v[3];
	unsigned int i;

	for (i = 0; i < 3; i++) {
		printf("reading 0x%" PRIx64 "\n", w[i].pa);
		v[i] = rd32(w[i].pa);
		printf("0x%" PRIx64 " = 0x%08x  %s\n", w[i].pa, v[i], w[i].what);
	}
	*ctl = v[0];
	if (v[0] & WD2_RESET_EN)
		verdict = WD2_ARMED;
	else if (v[0])
		verdict = WD2_ODD;
	else if (v[2] == WD2_BITE_AWAKE)
		verdict = WD2_AWAKE;
	else if (v[2] == WD2_BITE_NEW)
		verdict = WD2_NEW;
	else
		verdict = WD2_ODD;
	printf("WD2: %s\n", verdict_str(verdict));
	return verdict;
}

/* The safe boot's flag, and its word on the kernel's command line, as e1_hook asks for it. */
static int safe_boot(void)
{
	char buf[4096], *p;
	size_t n;
	FILE *f;

	if (access(WD2_SAFE_FLAG, F_OK))
		return 0;
	f = fopen(WD2_CMDLINE, "r");
	if (!f)
		return 0;
	n = fread(buf, 1, sizeof(buf) - 1, f);
	fclose(f);
	buf[n] = 0;
	for (p = strtok(buf, " \n"); p; p = strtok(NULL, " \n")) {
		if (!strcmp(p, "sleepdrain.safe"))
			return 1;
	}
	return 0;
}

int main(int argc, char **argv)
{
	enum wd2_verdict verdict, after;
	uint32_t ctl, ctl_after;
	int patch;

	if (argc != 2 || (strcmp(argv[1], "check") && strcmp(argv[1], "patch"))) {
		fprintf(stderr, "usage: wd2_poke check | patch\n");
		return 2;
	}
	patch = !strcmp(argv[1], "patch");
	setvbuf(stdout, NULL, _IONBF, 0);
	if (!safe_boot()) {
		fprintf(stderr, "wd2_poke: this runs in a safe boot only (%s, and sleepdrain.safe in %s)\n",
			WD2_SAFE_FLAG, WD2_CMDLINE);
		return 2;
	}
	mem_fd = open(WD2_MEM, patch ? O_RDWR | O_SYNC : O_RDONLY | O_SYNC);
	if (mem_fd < 0) {
		fprintf(stderr, "wd2_poke: %s: %s\n", WD2_MEM, strerror(errno));
		return 2;
	}
	/* Started with a standard descriptor closed, the device would be this program's stdout or stderr: every line it prints would be a write to it. */
	if (mem_fd <= 2)
		return 2;

	verdict = look(&ctl);
	if (!patch)
		return verdict == WD2_AWAKE || verdict == WD2_NEW ? 0 : 3;
	if (verdict == WD2_NEW) {
		printf("already the new value: nothing written\n");
		return 0;
	}
	if (verdict != WD2_AWAKE) {
		printf("not as it is awake: nothing written\n");
		return 3;
	}
	printf("writing 0x%08x to 0x%" PRIx64 ", one 32-bit store\n", WD2_BITE_NEW, BITE_PA);
	wr32(BITE_PA, WD2_BITE_NEW);
	printf("the three words again, after the write:\n");
	after = look(&ctl_after);
	if (after == WD2_NEW) {
		printf("the register took the write: the bite reads the new value, 120 s, and WD2 still isn't armed\n");
		return 0;
	}
	if (after == WD2_AWAKE) {
		printf("THE WRITE DIDN'T TAKE: the bite still reads 90 s. Nothing is changed\n");
		return 5;
	}
	printf("WD2 READS NEITHER AS BEFORE NOR AS INTENDED after the write (control 0x%08x before, 0x%08x now). Nothing more is written\n",
	       ctl, ctl_after);
	return 6;
}
