// recfg_peek (T6000, as root, in a safe boot): the AON engine's reconfig SRAM
// and the table that points into it, through /dev/mem, read-only. One stage per
// run; e1-hook/recfg-peek runs them in order and saves after each.
//
//   stage 1: the table-offset register's analogue (0x2925c0200) and its
//            lock's (0x2925c0214), inside the ADT's window 0x2925c0000 + 0x14000
//            (pmgr reg[36], the block the commit, 0x2925c4000, is in)
//   stage 2: the SRAM's first word (0x292f00000)
//   stage 3: the table: 16 words at the SRAM + the offset from stage 1 (or
//            SRAM + 0 if that doesn't fit), each one a sequence's address >> 4
//   stage 4: the SRAM's first 0x10000 bytes, to a file
//   stage 5: as far as the table's sequences say (the last one + 0x10000),
//            to a second file
//
// The addresses are inferred (PLAN.md, "the engine's reconfig SRAM"). PongoOS
// (recfg_soc.c, MIT) has T8012's and T8015's table offset register at AON +
// 0x2c0200, its lock at + 0x2c0214, the SRAM at AON + 0xf00000, and each table
// entry a sequence's physical address >> 4. The commit is at AON + 0x5c4000
// on this Mac and + 0x2c4000 on T8103, which is where the first two addresses
// come from. The SRAM's address and length come from macOS's device tree:
// carveout-memory-map's region-id-29 and region-id-36 are both 0x292f00000,
// 0x70000 bytes. It isn't described by any node's reg, so the read is outside
// the ADT windows Linux and its drivers know.
//
// It can read nothing else: there's no address argument. Each read is one
// aligned 32-bit ldr from a /dev/mem mapping, which arm64 makes device memory
// (nGnRnE), as pmgr-peek's are. Nothing is written. Every stage prints and
// flushes as it goes, so if a read faults the output shows which one it was.
// An unmapped address can raise an SError and panic the kernel: that's why it
// runs in a safe boot, and only with Corey's OK (2026-11-08).
//
// A stage's exit status: 0, go on; 3, it found nothing more worth reading
// and the next stage shouldn't run; anything else, an error.
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

static int mem_fd = -1;

static uint32_t rd32(const volatile void *p)
{
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
	return rd32(map_range(pa, 4));
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

		words[i] = rd32(t + i * 4);
		pa = (uint64_t)words[i] << 4;
		if (in_sram(pa)) {
			valid++;
			if (pa > *highest)
				*highest = pa;
		}
	}
	return valid;
}

static void dump(const char *dir, uint64_t off, uint64_t end)
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
		uint32_t v = rd32(m + (i - off));

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

int main(int argc, char **argv)
{
	const char *why;
	uint32_t cfg, words[TABLE_WORDS];
	uint64_t base, highest, end;
	int stage, i, valid;

	if (argc != 3 || !argv[1][0] || argv[1][1]) {
		fprintf(stderr, "usage: recfg_peek STAGE DIR (STAGE 1-5; DIR for its dumps)\n");
		return 2;
	}
	stage = argv[1][0] - '0';
	if (stage < 1 || stage > 5) {
		fprintf(stderr, "recfg_peek: no stage %s\n", argv[1]);
		return 2;
	}
	setvbuf(stdout, NULL, _IONBF, 0);
	mem_fd = open(RECFG_MEM, O_RDONLY | O_SYNC);
	if (mem_fd < 0) {
		fprintf(stderr, "recfg_peek: %s: %s\n", RECFG_MEM, strerror(errno));
		return 1;
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
		dump(argv[2], 0, FIRST_DUMP);
		return 0;
	case 5:
		cfg = read_word(CFG_REG);
		base = table_base(cfg, &why);
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
		dump(argv[2], FIRST_DUMP, end);
		return 0;
	}
	return 2;
}
