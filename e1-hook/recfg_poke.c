// recfg_poke (T6000, as root, in a safe boot): the one write to the AON
// engine's reconfig SRAM, through /dev/mem. Corey's OK of 2026-11-09, for this
// word and this value; PLAN.md and RESEARCH.md ("A different approach") say
// what it's for. e1-hook/recfg-bite runs it.
//
//   recfg_poke patch     A2's WD2 bite word, 0x292f02010: 30 s to 45 s
//   recfg_poke restore   back to 30 s
//
// There's no address argument and no value argument: the word and both values
// are recfg_bite.h's. Each run first reads the ten words recfg_peek's stage 6
// reads (the table offset register's analogue and its lock, table A's entry 2,
// A2's first six words) and goes by recfg_bite.h's verdict:
//   patch    writes only if everything is as expected and the bite reads 30 s;
//            with 45 s there already, it writes nothing and says so
//   restore  writes only if everything else is as expected and the bite isn't
//            30 s; with 30 s there already, it writes nothing
// A write is one aligned 32-bit str to that one address, then a read of it. A
// line is printed and flushed before every read and before the write, so that
// if one faults the output shows which. A patch that doesn't read back as
// written puts the old value back, unless that's what the word reads.
//
// It refuses outside a safe boot: an access nothing answers can raise an
// SError and panic the kernel, and whether this SRAM takes a write from here
// is the question being asked. PongoOS (recfg_soc.c, MIT) patches the same
// kind of sequence in place on A9-A11, and stops if the SRAM's range lock or
// the table's lock is set; only the table lock's analogue is known here (it
// reads 0), so a write that's dropped and one that faults are both possible.
//
// Exit status: 0, the word reads what was asked for (written now, or there
// already); 3, something isn't as expected and nothing was written; 5, a write
// was made and the word doesn't read back as written; 2, usage, not a safe
// boot, or the memory device didn't open.
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

#define BITE_PA ((uint64_t)(RECFG_SRAM + RECFG_BITE))

/*
 * Test builds log every access (tests/recfg_poke_test.py), and can drop or
 * change the nth store: an SRAM that's locked, or one that takes another value.
 */
#ifdef RECFG_TRACE
static void trace(char op, uint64_t pa, uint32_t v)
{
	static FILE *t;

	if (!t)
		t = fopen(RECFG_TRACE, "a");
	if (t) {
		fprintf(t, "%c 0x%" PRIx64 " 0x%08x\n", op, pa, v);
		fflush(t);
	}
}
#else
#define trace(op, pa, v) ((void)0)
#endif
#ifndef RECFG_POKE_DROP
#define RECFG_POKE_DROP(n) 0
#endif
#ifndef RECFG_POKE_STORE
#define RECFG_POKE_STORE(v, n) (v)
#endif

static int mem_fd = -1;

/* One page, mapped for one access; arm64 makes a /dev/mem mapping device memory (nGnRnE), as recfg_peek's are. */
static volatile void *map_word(uint64_t pa, int prot)
{
	long page = sysconf(_SC_PAGESIZE);
	uint64_t base = pa & ~(uint64_t)(page - 1);
	void *m;

	m = mmap(NULL, (size_t)page, prot, MAP_SHARED, mem_fd, (off_t)base);
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
	trace('r', pa, v);
	return v;
}

/* The only store in this program, and BITE_PA the only address it's ever given. */
static void wr32(uint64_t pa, uint32_t v)
{
	static int stores;
	volatile void *p = map_word(pa, PROT_READ | PROT_WRITE);
	int n = stores++;

	trace('w', pa, v);
	(void)n;	/* the test macros may not use it */
	if (RECFG_POKE_DROP(n))
		return;
	v = RECFG_POKE_STORE(v, n);
#ifdef __aarch64__
	asm volatile("str %w0, [%1]\n\tdsb sy" : : "r"(v), "r"(p) : "memory");
#else
	*(volatile uint32_t *)p = v;
#endif
}

/* Writes value to the bite word and reads it once; returns what it read. */
static uint32_t put(uint32_t value)
{
	uint32_t back;

	printf("writing 0x%08x to 0x%" PRIx64 "\n", value, BITE_PA);
	wr32(BITE_PA, value);
	printf("reading 0x%" PRIx64 "\n", BITE_PA);
	back = rd32(BITE_PA);
	printf("0x%" PRIx64 " = 0x%08x\n", BITE_PA, back);
	return back;
}

int main(int argc, char **argv)
{
	const struct recfg_word *w = recfg_bite_words;
	uint32_t v[RECFG_BITE_WORDS], back;
	enum recfg_verdict verdict;
	unsigned int i;
	int patch;

	if (argc != 2 || (strcmp(argv[1], "patch") && strcmp(argv[1], "restore"))) {
		fprintf(stderr, "usage: recfg_poke patch | restore\n");
		return 2;
	}
	patch = !strcmp(argv[1], "patch");
	setvbuf(stdout, NULL, _IONBF, 0);
	if (access(RECFG_SAFE_FLAG, F_OK)) {
		fprintf(stderr, "recfg_poke: this runs in a safe boot only (no %s)\n", RECFG_SAFE_FLAG);
		return 2;
	}
	mem_fd = open(RECFG_MEM, O_RDWR | O_SYNC);
	if (mem_fd < 0) {
		fprintf(stderr, "recfg_poke: %s: %s\n", RECFG_MEM, strerror(errno));
		return 2;
	}

	for (i = 0; i < RECFG_BITE_WORDS; i++) {
		printf("reading 0x%" PRIx64 "\n", w[i].pa);
		v[i] = rd32(w[i].pa);
		printf("0x%" PRIx64 " = 0x%08x  %s\n", w[i].pa, v[i], w[i].what);
	}
	verdict = recfg_bite_verdict(v[0], v[1], v + 2, v + 4);
	printf("A2's WD2 bite word, 0x%" PRIx64 ": %s\n", BITE_PA, recfg_verdict_str(verdict));

	if (patch) {
		if (verdict == RECFG_NEW) {
			printf("already the new value: nothing written\n");
			return 0;
		}
		if (verdict != RECFG_OLD) {
			printf("not as expected: nothing written\n");
			return 3;
		}
		back = put(RECFG_BITE_NEW);
		if (back == RECFG_BITE_NEW) {
			printf("the word took the write: it reads the new value, 45 s\n");
			return 0;
		}
		if (back == RECFG_BITE_OLD) {
			printf("THE WRITE DIDN'T TAKE: the word still reads the old value, 30 s. Nothing is changed\n");
			return 5;
		}
		printf("THE WORD READS NEITHER VALUE after the write; putting the old value back\n");
		printf(put(RECFG_BITE_OLD) == RECFG_BITE_OLD ? "the word reads the old value, 30 s, again\n" :
		       "THE OLD VALUE DIDN'T GO BACK EITHER\n");
		return 5;
	}

	if (verdict == RECFG_OLD) {
		printf("already the old value: nothing written\n");
		return 0;
	}
	if (verdict != RECFG_NEW && verdict != RECFG_BAD_BITE) {
		printf("not as expected: nothing written\n");
		return 3;
	}
	if (put(RECFG_BITE_OLD) == RECFG_BITE_OLD) {
		printf("the word reads the old value, 30 s, again\n");
		return 0;
	}
	printf("THE OLD VALUE DIDN'T GO BACK\n");
	return 5;
}
