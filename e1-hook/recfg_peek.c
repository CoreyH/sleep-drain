// recfg_peek (T6000, as root): the AON engine's reconfig SRAM and the table that
// points into it, and the words that say where the engine is, through /dev/mem,
// read-only. One stage per run; e1-hook/recfg-peek runs them in order and saves
// after each. Stages 1-5 run in a safe boot only; so does the first pass over
// stages a-h, and the launcher repeats those a-h once in the regular boot after
// a run, when the safe-boot pass read them all without a fault. Stages 6 and 7
// are e1-hook/recfg-bite's: 6 in a safe boot, 7 once in the regular boot after
// its run. Stage 8 is the rest of the SRAM, a 16 KiB piece a run, in a safe boot.
//
//   stage 1: the table-offset register's analogue (0x2925c0200) and its
//            lock's (0x2925c0214), inside the ADT's window 0x2925c0000 + 0x14000
//            (pmgr reg[36], the block the commit, 0x2925c4000, is in)
//   stage 2: the SRAM's first word (0x292f00000)
//   stage 3: the table: 16 words at the SRAM + the offset from stage 1 (or
//            SRAM + 0 if that doesn't fit), each one a sequence's address >> 4
//            (16 is a guess at the table's length: PongoOS walks 8 entries on
//            T8012 and T8015, and T8103 has 12 sequences; the words are only
//            counted and bounded, never followed)
//   stage 4: the SRAM's first 0x10000 bytes, to a file
//   stage 5: as far as the table's sequences say (the last one + 0x10000,
//            capped at the SRAM's 0x70000), to a file of its own. This is the
//            read most exposed to an SRAM smaller than 0x70000 (T8103's is
//            128 KiB), so the launcher runs it only when asked (--rest), after
//            someone has decoded stage 4's dump
//   stage 6: the ten words that say where A2's WD2 bite is, and the bite
//            (recfg_bite.h): the offset register's analogue and its lock again,
//            table A's entry 2, and A2's first six words, all of which stages
//            1, 3 and 4 read on 2026-11-08. It says whether they're as expected
//            and whether the bite is the old 30 s or the new 45 s
//   stage 7: that one word alone, 0x292f02010, and which of the two values it
//            reads: the read Corey OK'd for the regular boot after the run
//   stage 8: one 16 KiB piece of the SRAM past stage 4's 0x10000 bytes, to a
//            file of its own: piece N, 0 to 23, is the 0x4000 bytes at SRAM +
//            0x10000 + N * 0x4000, so the 24 of them end where the SRAM does, at
//            0x70000 (Corey's OK of 2026-11-10 for the rest of the SRAM, read-only:
//            37 of the 45 sequences its pointer blocks name are out there, the
//            eight other descriptor blocks'). It says how many of the piece's
//            words read 0, and stops the pass if every one read all ones
//   stages a-h: the words of the position read (PLAN.md), one stage a word,
//            but for a: WD2's control and count and SIO_CPU, which runs 2 and
//            3 read after the commit. b-h are the seven nothing had read: the
//            state word, HS_A, the DCS page's word, the gate words, HS_B, PHASE.
//
// The addresses are inferred (PLAN.md, "the engine's reconfig SRAM"). PongoOS
// (recfg_soc.c, MIT) has T8012's and T8015's table offset register at AON +
// 0x2c0200, its lock at + 0x2c0214, the SRAM at AON + 0xf00000, and each table
// entry a sequence's physical address >> 4. The commit is at AON + 0x5c4000
// on this Mac and + 0x2c4000 on T8103, which is where the first two addresses
// come from. For the SRAM's address and length, macOS's device tree has
// carveout-memory-map's region-id-29 and region-id-36, both 0x292f00000,
// 0x70000 bytes; their names are empty, so that this carveout is the reconfig
// SRAM is inferred, and that it's listed doesn't make it safe to read (the
// carveout map lists MMIO too: region-id-32 is the 0x28e700000 of the
// never-write list). No node's reg covers it, so the read is outside the ADT
// windows Linux and its drivers know.
//
// It can read nothing else: there's no address argument. Stage 8's piece is an
// index, 0 to 23, and every piece is inside the SRAM. Each read is one
// aligned 32-bit ldr from a /dev/mem mapping, which arm64 makes device memory
// (nGnRnE), as pmgr-peek's are. Nothing is written. A line is printed and
// flushed before each read or, in the table and the dumps, before each 16
// KiB page of reads, so if a read faults the output shows which one or which
// page it was.
// An unmapped address can raise an SError and panic the kernel: that's why it
// runs in a safe boot, and only with Corey's OK (2026-11-08; 2026-11-09 for
// stages a-h and their one repeat in a regular boot, and for stage 7, the one
// word, in the regular boot after the run that changes it).
//
// A stage's exit status: 0, go on; 3, it found nothing more worth reading
// and the next stage shouldn't run (stage 6: the words aren't as expected;
// stage 7: the word is neither value); anything else, an error.
//
// The dumps stay in /var/log/sleep-drain: they aren't for the repo, which is
// public.
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "recfg_bite.h"

#ifndef __aarch64__
#ifndef RECFG_MEM
#error "recfg_peek is for arm64 Macs"
#endif
#endif

#ifndef RECFG_MEM
#define RECFG_MEM "/dev/mem"
#endif

#define CFG_REG		UINT64_C(0x2925c0200)
#define CFG_LOCK	UINT64_C(0x2925c0214)
#define SRAM		UINT64_C(0x292f00000)
#define SRAM_LEN	UINT64_C(0x70000)
#define FIRST_DUMP	UINT64_C(0x10000)
#define TABLE_WORDS	16
#define MIN_ENTRIES	4
#define TAIL		UINT64_C(0x10000)
#define PAGE_ROUND	UINT64_C(0x4000)
/* Stage 8's pieces: what's left of the SRAM after the first dump, 16 KiB each. */
#define REST_PIECES	((int)((SRAM_LEN - FIRST_DUMP) / PAGE_ROUND))

_Static_assert(FIRST_DUMP % PAGE_ROUND == 0 && SRAM_LEN % PAGE_ROUND == 0 && FIRST_DUMP + 24 * PAGE_ROUND == SRAM_LEN,
	       "24 pieces of 16 KiB take the first dump's end to the SRAM's");

/* The position read's words, in the order they're read. */
static const struct pos_word {
	char stage;
	uint64_t pa;
	const char *what;
} pos_words[] = {
	{ 'a', UINT64_C(0x2922b002c), "WD2's control" },
	{ 'a', UINT64_C(0x2922b0020), "WD2's count" },
	{ 'a', UINT64_C(0x28e580188), "SIO_CPU's power state" },
	{ 'b', UINT64_C(0x2922b801c), "the state word" },
	{ 'c', UINT64_C(0x2925c4004), "HS_A (the commit + 4)" },
	{ 'd', UINT64_C(0x28e20c400), "the DCS p-state page's word that A2 clears and polls" },
	{ 'e', UINT64_C(0x2922c0040), "the gate word B2 clears" },
	{ 'f', UINT64_C(0x2922c0044), "B2's first poll" },
	{ 'g', UINT64_C(0x2922c010c), "HS_B" },
	{ 'h', UINT64_C(0x2922c0110), "PHASE" },
};

static int mem_fd = -1;

/* Test builds log every read's physical address (tests/recfg_peek_test.py). */
#ifdef RECFG_TRACE
static void trace_read(uint64_t pa)
{
	static FILE *t;

	if (!t)
		t = fopen(RECFG_TRACE, "a");
	if (t) {
		fprintf(t, "0x%" PRIx64 "\n", pa);
		fflush(t);
	}
}
#else
#define trace_read(pa) ((void)(pa))
#endif

static uint32_t rd32(const volatile void *p, uint64_t pa)
{
	trace_read(pa);
#ifdef __aarch64__
	uint32_t v;

	asm volatile("ldr %w0, [%1]" : "=r"(v) : "r"(p) : "memory");
	return v;
#else
	return *(const volatile uint32_t *)p;
#endif
}

static const volatile void *map_range(uint64_t pa, size_t len)
{
	long page = sysconf(_SC_PAGESIZE);
	uint64_t base = pa & ~(uint64_t)(page - 1);
	size_t off = pa - base;
	void *m;

	m = mmap(NULL, len + off, PROT_READ, MAP_SHARED, mem_fd, (off_t)base);
	if (m == MAP_FAILED) {
		fprintf(stderr, "recfg_peek: mmap %#" PRIx64 ": %s\n", pa, strerror(errno));
		exit(1);
	}
	return (const volatile char *)m + off;
}

static uint32_t read_word(uint64_t pa)
{
	return rd32(map_range(pa, 4), pa);
}

static int in_sram(uint64_t pa)
{
	return pa >= SRAM && pa < SRAM + SRAM_LEN;
}

/* Where the table starts: at the SRAM + the offset register, if that fits. */
static uint64_t table_base(uint32_t cfg, const char **why)
{
	if (cfg % 4 == 0 && (uint64_t)cfg + TABLE_WORDS * 4 <= SRAM_LEN) {
		*why = "SRAM + the offset register";
		return SRAM + cfg;
	}
	*why = "SRAM + 0: the offset register doesn't fit in the SRAM";
	return SRAM;
}

/* Reads the table; returns how many entries point into the SRAM, and the highest. */
static int read_table(uint64_t base, uint32_t *words, uint64_t *highest)
{
	const volatile char *t = map_range(base, TABLE_WORDS * 4);
	int valid = 0, i;

	*highest = 0;
	for (i = 0; i < TABLE_WORDS; i++) {
		uint64_t pa;

		words[i] = rd32(t + i * 4, base + i * 4);
		pa = (uint64_t)words[i] << 4;
		if (in_sram(pa)) {
			valid++;
			if (pa > *highest)
				*highest = pa;
		}
	}
	return valid;
}

/* The SRAM from off to end, to a file in dir. zeros and ones, if given: how many of its words read 0 and all ones. */
static void dump(const char *dir, uint64_t off, uint64_t end, unsigned int *zeros, unsigned int *ones)
{
	char path[4096];
	const volatile char *m = map_range(SRAM + off, end - off);
	uint64_t i;
	int fd;

	snprintf(path, sizeof(path), "%s/sram-%05" PRIx64 ".bin", dir, off);
	fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);
	if (fd < 0) {
		fprintf(stderr, "recfg_peek: %s: %s\n", path, strerror(errno));
		exit(1);
	}
	for (i = off; i < end; i += 4) {
		uint32_t v;

		if (i % PAGE_ROUND == 0)
			printf("reading 0x%" PRIx64 "\n", SRAM + i);
		v = rd32(m + (i - off), SRAM + i);
		if (zeros && !v)
			++*zeros;
		if (ones && v == 0xffffffff)
			++*ones;

		if (write(fd, &v, 4) != 4) {
			fprintf(stderr, "recfg_peek: write %s: %s\n", path, strerror(errno));
			exit(1);
		}
	}
	if (fsync(fd) || close(fd)) {
		fprintf(stderr, "recfg_peek: sync %s: %s\n", path, strerror(errno));
		exit(1);
	}
	printf("0x%" PRIx64 "-0x%" PRIx64 " read, %s\n", SRAM + off, SRAM + end - 1, path);
}

/* Stage 8: piece n of the rest of the SRAM. Nothing read here is followed. */
static int rest_stage(const char *dir, int n)
{
	const uint64_t off = FIRST_DUMP + (uint64_t)n * PAGE_ROUND;
	const unsigned int words = PAGE_ROUND / 4;
	unsigned int zeros = 0, ones = 0;

	printf("piece %d of %d: 0x%" PRIx64 "-0x%" PRIx64 "\n", n, REST_PIECES, SRAM + off, SRAM + off + PAGE_ROUND - 1);
	dump(dir, off, off + PAGE_ROUND, &zeros, &ones);
	printf("%u of its %u words read 0, %u read all ones\n", zeros, words, ones);
	if (ones == words) {
		printf("all ones: nothing is answering there, or the SRAM ends before it. Stopping.\n");
		return 3;
	}
	return 0;
}

/* A piece's index: one or two digits, 0 to REST_PIECES - 1, and nothing else. -1 otherwise. */
static int piece_index(const char *a)
{
	int n;

	if (a[0] < '0' || a[0] > '9')
		return -1;
	n = a[0] - '0';
	if (a[1]) {
		if (!n || a[1] < '0' || a[1] > '9' || a[2])
			return -1;
		n = n * 10 + a[1] - '0';
	}
	return n < REST_PIECES ? n : -1;
}

/* Stage 6: where A2's WD2 bite is, and what it reads. The addresses are fixed; nothing read here is followed. */
static int bite_stage(void)
{
	const struct recfg_word *w = recfg_bite_words;
	uint32_t v[RECFG_BITE_WORDS];
	enum recfg_verdict verdict;
	unsigned int i;

	for (i = 0; i < RECFG_BITE_WORDS; i++) {
		printf("reading 0x%" PRIx64 "\n", w[i].pa);
		v[i] = read_word(w[i].pa);
		printf("0x%" PRIx64 " = 0x%08x  %s\n", w[i].pa, v[i], w[i].what);
	}
	verdict = recfg_bite_verdict(v[0], v[1], v + 2, v + 4);
	printf("A2's WD2 bite word, 0x%" PRIx64 ": %s\n", (uint64_t)(RECFG_SRAM + RECFG_BITE), recfg_verdict_str(verdict));
	return verdict == RECFG_OLD || verdict == RECFG_NEW ? 0 : 3;
}

/* Stage 7: the bite word alone. */
static int bite_word_stage(void)
{
	const uint64_t pa = RECFG_SRAM + RECFG_BITE;
	uint32_t v;

	printf("reading 0x%" PRIx64 "\n", pa);
	v = read_word(pa);
	printf("0x%" PRIx64 " = 0x%08x  A2's WD2 bite word: %s\n", pa, v,
	       v == RECFG_BITE_OLD ? "the old value, 30 s" : v == RECFG_BITE_NEW ? "the new value, 45 s" : "neither the old value nor the new one");
	return v == RECFG_BITE_OLD || v == RECFG_BITE_NEW ? 0 : 3;
}

int main(int argc, char **argv)
{
	const char *why;
	uint32_t cfg, words[TABLE_WORDS];
	uint64_t base, highest, end;
	int stage, i, valid, letter, piece = -1;

	if ((argc != 3 && argc != 4) || !argv[1][0] || argv[1][1]) {
		fprintf(stderr, "usage: recfg_peek STAGE DIR (STAGE 1-7 or a-h; DIR for its dumps), or recfg_peek 8 DIR PIECE (0-%d)\n", REST_PIECES - 1);
		return 2;
	}
	stage = argv[1][0] - '0';
	letter = argv[1][0] >= 'a' && argv[1][0] <= 'h';
	if (!letter && (stage < 1 || stage > 8)) {
		fprintf(stderr, "recfg_peek: no stage %s\n", argv[1]);
		return 2;
	}
	/* Stage 8 alone takes a third argument, and must have it. */
	if ((argc == 4) != (!letter && stage == 8)) {
		fprintf(stderr, "recfg_peek: stage 8 takes a piece, 0-%d, and no other stage does\n", REST_PIECES - 1);
		return 2;
	}
	if (argc == 4) {
		piece = piece_index(argv[3]);
		if (piece < 0) {
			fprintf(stderr, "recfg_peek: no piece %s (0-%d)\n", argv[3], REST_PIECES - 1);
			return 2;
		}
	}
	setvbuf(stdout, NULL, _IONBF, 0);
	mem_fd = open(RECFG_MEM, O_RDONLY | O_SYNC);
	if (mem_fd < 0) {
		fprintf(stderr, "recfg_peek: %s: %s\n", RECFG_MEM, strerror(errno));
		return 1;
	}
	/* Started with a standard descriptor closed, the device would be this program's stdout or stderr. */
	if (mem_fd <= 2)
		return 1;

	if (letter) {
		for (i = 0; i < (int)(sizeof(pos_words) / sizeof(pos_words[0])); i++) {
			if (pos_words[i].stage != argv[1][0])
				continue;
			printf("reading 0x%" PRIx64 "\n", pos_words[i].pa);
			cfg = read_word(pos_words[i].pa);
			printf("0x%" PRIx64 " = 0x%08x  %s\n", pos_words[i].pa, cfg, pos_words[i].what);
		}
		return 0;
	}

	switch (stage) {
	case 1:
		printf("reading 0x%" PRIx64 "\n", CFG_REG);
		cfg = read_word(CFG_REG);
		printf("0x%" PRIx64 " = 0x%08x  the table offset register's analogue\n", CFG_REG, cfg);
		printf("reading 0x%" PRIx64 "\n", CFG_LOCK);
		printf("0x%" PRIx64 " = 0x%08x  its lock's analogue\n", CFG_LOCK, read_word(CFG_LOCK));
		return 0;
	case 2:
		printf("reading 0x%" PRIx64 "\n", SRAM);
		cfg = read_word(SRAM);
		printf("0x%" PRIx64 " = 0x%08x  the SRAM's first word\n", SRAM, cfg);
		if (cfg == 0xffffffff) {
			printf("all ones: nothing is answering there, or it isn't what we thought. Stopping.\n");
			return 3;
		}
		return 0;
	case 3:
		cfg = read_word(CFG_REG);
		base = table_base(cfg, &why);
		printf("the table's place: 0x%" PRIx64 " (%s; the register reads 0x%08x)\n", base, why, cfg);
		printf("reading 16 words at 0x%" PRIx64 "\n", base);
		valid = read_table(base, words, &highest);
		for (i = 0; i < TABLE_WORDS; i++) {
			uint64_t pa = (uint64_t)words[i] << 4;

			printf("  table[%2d] = 0x%08x -> 0x%" PRIx64 "%s\n", i, words[i], pa, in_sram(pa) ? "  in the SRAM" : "");
		}
		printf("%d of %d entries point into the SRAM (need %d)\n", valid, TABLE_WORDS, MIN_ENTRIES);
		if (valid < MIN_ENTRIES) {
			printf("no table here, so no dump. Stopping.\n");
			return 3;
		}
		return 0;
	case 4:
		dump(argv[2], 0, FIRST_DUMP, NULL, NULL);
		return 0;
	case 5:
		cfg = read_word(CFG_REG);
		base = table_base(cfg, &why);
		printf("reading 16 words at 0x%" PRIx64 "\n", base);
		valid = read_table(base, words, &highest);
		if (valid < MIN_ENTRIES) {
			printf("no table here (%d entries), so nothing further.\n", valid);
			return 3;
		}
		end = highest - SRAM + TAIL;
		end = (end + PAGE_ROUND - 1) & ~(PAGE_ROUND - 1);
		if (end > SRAM_LEN)
			end = SRAM_LEN;
		if (end <= FIRST_DUMP) {
			printf("the table's last sequence is at 0x%" PRIx64 ": the first 0x%" PRIx64 " bytes hold it, with its tail.\n", highest, FIRST_DUMP);
			return 0;
		}
		printf("reading on to 0x%" PRIx64 " (the table's last sequence is at 0x%" PRIx64 ")\n", SRAM + end - 1, highest);
		dump(argv[2], FIRST_DUMP, end, NULL, NULL);
		return 0;
	case 6:
		return bite_stage();
	case 7:
		return bite_word_stage();
	case 8:
		return rest_stage(argv[2], piece);
	}
	return 2;
}
