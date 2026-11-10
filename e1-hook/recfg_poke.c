// recfg_poke (T6000, as root, in a safe boot): the one write to the AON
// engine's reconfig SRAM, through /dev/mem. Corey's OK of 2026-11-09, for this
// word and this value, and of 2026-11-10 for making it as one 64-bit store;
// PLAN.md and RESEARCH.md ("A different approach", "M1 run 13") say what it's
// for. e1-hook/recfg-bite runs it.
//
//   recfg_poke patch     A2's WD2 bite word, 0x292f02010: 30 s to 45 s
//   recfg_poke restore   back to 30 s, by hand, if that's ever wanted
//
// There's no address argument and no value argument: the word and both values
// are recfg_bite.h's. Each run first reads the ten words recfg_peek's stage 6
// reads (the table offset register's analogue and its lock, table A's entry 2,
// A2's first six words) and goes by recfg_bite.h's verdict:
//   patch    writes only if all ten are as expected with the bite at 30 s;
//            with 45 s there already, it writes nothing and says so
//   restore  writes only if all ten are as expected with the bite at 45 s, the
//            value patch writes; with 30 s there already, it writes nothing
// A write is one aligned 64-bit str at that one address. Run 13's was a 32-bit
// str, and the SRAM dropped it without a fault; whether it takes a 64-bit one
// is this build's question. The store's low half is the bite. Its high half is
// the next word, 0x292f02014, the value A2 gives WD2's control: it's stored as
// the 4 (RESET_EN) the ten words must show there before anything is written,
// so the bite is the only word meant to change. Then all ten words are read
// again, 32 bits each as before: the write counts only if they give the verdict
// it was after, so a store that lands as anything else in either half is caught
// here, not as a Mac that stays dark. A line is printed and flushed before
// every read and before the write, so that if one faults the output shows
// which.
//
// What isn't in the OK, and is here anyway: if the ten words read neither as
// before nor as intended after patch's write, it makes the same store once
// more with the old bite, which is both words as they were found; and restore,
// which nothing runs by itself.
//
// It refuses outside a safe boot (sleepdrain.safe on the kernel's command line
// and the safe boot's flag in /run): an access nothing answers can raise an
// SError and panic the kernel, and whether this SRAM takes a write from here
// is the question being asked. PongoOS (recfg_soc.c, MIT) patches the same
// kind of sequence in place on A9-A11, and stops if the SRAM's range lock or
// the table's lock is set; only the table lock's analogue is known here (it
// reads 0), so a write that's dropped and one that faults are both possible.
// PongoOS's own edit is a 32-bit store, so the width is the weaker suspect.
//
// Exit status: 0, the ten words read as asked for (written now, or there
// already); 3, they weren't as expected and nothing was written; 5, a write
// was made and they don't read as it meant; 2, usage, not a safe boot, or the
// memory device didn't open.
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "recfg_bite.h"

#ifndef __aarch64__
#ifndef RECFG_MEM
#error "recfg_poke is for arm64 Macs"
#endif
#endif

#ifndef RECFG_MEM
#define RECFG_MEM "/dev/mem"
#endif
#ifndef RECFG_SAFE_FLAG
#define RECFG_SAFE_FLAG "/run/sleep-drain-safe/active"
#endif
#ifndef RECFG_CMDLINE
#define RECFG_CMDLINE "/proc/cmdline"
#endif

#define BITE_PA ((uint64_t)(RECFG_SRAM + RECFG_BITE))
/* The word the store's high half covers, and the one 64-bit value for a bite: that word's own 4 above it. */
#define NEXT_PA (BITE_PA + 4)
#define BITE_STORE(bite) ((uint64_t)RECFG_WD_RESET_EN << 32 | (uint64_t)(bite))

_Static_assert((BITE_PA & 7) == 0, "the 64-bit store has to be aligned");
_Static_assert(RECFG_BITE + 4 == RECFG_A2 + (RECFG_SEQ_WORDS - 1) * 4,
	       "the store's high half has to be the last of A2's six checked words");
_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "the bite has to be the store's low half");

/*
 * Test builds log every access (tests/recfg_bite_test.py), a read's 32 bits or
 * a store's 64, and can drop or change the nth store: an SRAM that's locked, or
 * one that takes another value.
 */
#ifdef RECFG_TRACE
static void trace(char op, uint64_t pa, uint64_t v, int digits)
{
	static FILE *t;

	if (!t)
		t = fopen(RECFG_TRACE, "a");
	if (t) {
		fprintf(t, "%c 0x%" PRIx64 " 0x%0*" PRIx64 "\n", op, pa, digits, v);
		fflush(t);
	}
}
#else
#define trace(op, pa, v, digits) ((void)0)
#endif
#ifndef RECFG_POKE_DROP
#define RECFG_POKE_DROP(n) 0
#endif
#ifndef RECFG_POKE_STORE
#define RECFG_POKE_STORE(v, n) (v)
#endif

static int mem_fd = -1;

/*
 * 16 KiB around the word, mapped for one access; arm64 makes a /dev/mem mapping
 * device memory (nGnRnE), as recfg_peek's are. 16 KiB whatever the process's own
 * page size: this kernel can give a process 4 KiB pages, and maps /dev/mem by its
 * own 16 KiB ones either way.
 */
#define MAP_UNIT UINT64_C(0x4000)
static volatile void *map_word(uint64_t pa, int prot)
{
	uint64_t base = pa & ~(MAP_UNIT - 1);
	void *m;

	m = mmap(NULL, (size_t)MAP_UNIT, prot, MAP_SHARED, mem_fd, (off_t)base);
	if (m == MAP_FAILED) {
		fprintf(stderr, "recfg_poke: mmap %#" PRIx64 ": %s\n", pa, strerror(errno));
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
	trace('r', pa, v, 8);
	return v;
}

/*
 * The only store in this program, and BITE_PA the only address it's ever given:
 * one 64-bit access, as device memory is never split or merged.
 */
static void wr64(uint64_t pa, uint64_t v)
{
	static int stores;
	volatile void *p = map_word(pa, PROT_READ | PROT_WRITE);
	int n = stores++;

	trace('w', pa, v, 16);
	(void)n;	/* the test macros may not use it */
	if (RECFG_POKE_DROP(n))
		return;
	v = RECFG_POKE_STORE(v, n);
#ifdef __aarch64__
	asm volatile("str %x0, [%1]\n\tdsb sy" : : "r"(v), "r"(p) : "memory");
#else
	*(volatile uint64_t *)p = v;
#endif
}

/* The ten words, each announced before its read, and what they say. */
static enum recfg_verdict look(void)
{
	const struct recfg_word *w = recfg_bite_words;
	uint32_t v[RECFG_BITE_WORDS];
	enum recfg_verdict verdict;
	unsigned int i;

	for (i = 0; i < RECFG_BITE_WORDS; i++) {
		printf("reading 0x%" PRIx64 "\n", w[i].pa);
		v[i] = rd32(w[i].pa);
		printf("0x%" PRIx64 " = 0x%08x  %s\n", w[i].pa, v[i], w[i].what);
	}
	verdict = recfg_bite_verdict(v[0], v[1], v + 2, v + 4);
	printf("A2's WD2 bite word, 0x%" PRIx64 ": %s\n", BITE_PA, recfg_verdict_str(verdict));
	return verdict;
}

static void store(uint32_t bite)
{
	printf("writing 0x%08x to 0x%" PRIx64 " and 0x%08x to 0x%" PRIx64 ", in one 64-bit store\n",
	       bite, BITE_PA, RECFG_WD_RESET_EN, NEXT_PA);
	wr64(BITE_PA, BITE_STORE(bite));
}

/* The safe boot's flag, and its word on the kernel's command line, as e1_hook asks for it. */
static int safe_boot(void)
{
	char buf[4096], *p;
	size_t n;
	FILE *f;

	if (access(RECFG_SAFE_FLAG, F_OK))
		return 0;
	f = fopen(RECFG_CMDLINE, "r");
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
	enum recfg_verdict verdict, after;
	int patch;

	if (argc != 2 || (strcmp(argv[1], "patch") && strcmp(argv[1], "restore"))) {
		fprintf(stderr, "usage: recfg_poke patch | restore\n");
		return 2;
	}
	patch = !strcmp(argv[1], "patch");
	setvbuf(stdout, NULL, _IONBF, 0);
	if (!safe_boot()) {
		fprintf(stderr, "recfg_poke: this runs in a safe boot only (%s, and sleepdrain.safe in %s)\n",
			RECFG_SAFE_FLAG, RECFG_CMDLINE);
		return 2;
	}
	mem_fd = open(RECFG_MEM, O_RDWR | O_SYNC);
	if (mem_fd < 0) {
		fprintf(stderr, "recfg_poke: %s: %s\n", RECFG_MEM, strerror(errno));
		return 2;
	}
	/* Started with a standard descriptor closed, the device would be this program's stdout or stderr: every line it prints would be a write to it. */
	if (mem_fd <= 2)
		return 2;

	verdict = look();
	if (patch) {
		if (verdict == RECFG_NEW) {
			printf("already the new value: nothing written\n");
			return 0;
		}
		if (verdict != RECFG_OLD) {
			printf("not as expected: nothing written\n");
			return 3;
		}
		store(RECFG_BITE_NEW);
		printf("the ten words again, after the write:\n");
		after = look();
		if (after == RECFG_NEW) {
			printf("the word took the write: it reads the new value, 45 s, and the other nine read as before\n");
			return 0;
		}
		if (after == RECFG_OLD) {
			printf("THE WRITE DIDN'T TAKE: the word still reads the old value, 30 s. Nothing is changed\n");
			return 5;
		}
		printf("THE TEN WORDS READ NEITHER AS BEFORE NOR AS INTENDED after the write; storing the two words as they were found\n");
		store(RECFG_BITE_OLD);
		printf("the ten words again:\n");
		printf(look() == RECFG_OLD ? "the ten words read as they did before the write\n" :
		       "THE TEN WORDS STILL DON'T READ AS THEY DID BEFORE THE WRITE\n");
		return 5;
	}

	if (verdict == RECFG_OLD) {
		printf("already the old value: nothing written\n");
		return 0;
	}
	if (verdict != RECFG_NEW) {
		printf("not as expected (restore only undoes patch's own value): nothing written\n");
		return 3;
	}
	store(RECFG_BITE_OLD);
	printf("the ten words again, after the write:\n");
	if (look() == RECFG_OLD) {
		printf("the word reads the old value, 30 s, again\n");
		return 0;
	}
	printf("THE OLD VALUE DIDN'T GO BACK\n");
	return 5;
}
