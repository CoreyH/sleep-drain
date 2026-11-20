// idle-watch (T6000, as root, awake): does a CPU complex go down in Linux's own idle?
//
// Read-only. For SECS seconds, from CPU0, it reads in a tight loop words that
// e1_hook's looks have read awake before, and counts each distinct tuple:
//   0x2922c0044  the engine's gate word (B2's first poll)
//   0x2922c0040  the word B2 lowers right behind it
//   0x28e09a000, +4, +8   the E, P0 and P1 complexes' state words
//   0x28e098000, +4, +8   their soc-cluster words
// Nothing is written. One aligned 32-bit load per word from a /dev/mem mapping.
#define _GNU_SOURCE
#include <fcntl.h>
#include <inttypes.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define PG 0x4000UL
#define NW 8
#define MAXT 512

struct tup { uint32_t w[NW]; uint64_t n; double first, last; };
static struct tup t[MAXT];
static int nt;

static volatile uint32_t *map(int fd, uint64_t pa)
{
	void *p = mmap(NULL, PG, PROT_READ, MAP_SHARED, fd, (off_t)pa);
	if (p == MAP_FAILED) { perror("mmap"); exit(1); }
	return p;
}

static inline uint32_t rd(volatile uint32_t *p, unsigned off)
{
	uint32_t v;
	__asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"((const void *)((const char *)p + off)));
	return v;
}

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
	double secs = argc > 1 ? atof(argv[1]) : 5, t0, tn;
	int cpu = argc > 2 ? atoi(argv[2]) : 0;
	cpu_set_t set;
	uint64_t total = 0;
	int fd, i;

	CPU_ZERO(&set);
	CPU_SET(cpu, &set);
	if (sched_setaffinity(0, sizeof(set), &set)) { perror("sched_setaffinity"); return 1; }
	fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) { perror("/dev/mem"); return 1; }
	volatile uint32_t *eng = map(fd, 0x2922c0000ULL), *soc = map(fd, 0x28e098000ULL);

	t0 = now();
	do {
		uint32_t w[NW];
		w[0] = rd(eng, 0x44);
		w[1] = rd(eng, 0x40);
		w[2] = rd(soc, 0x2000);
		w[3] = rd(soc, 0x2004);
		w[4] = rd(soc, 0x2008);
		w[5] = rd(soc, 0x0);
		w[6] = rd(soc, 0x4);
		w[7] = rd(soc, 0x8);
		tn = now();
		for (i = 0; i < nt; i++)
			if (!memcmp(t[i].w, w, sizeof(w)))
				break;
		if (i == nt) {
			if (nt == MAXT) { fprintf(stderr, "more than %d tuples\n", MAXT); break; }
			memcpy(t[nt].w, w, sizeof(w));
			t[nt].first = tn - t0;
			nt++;
		}
		t[i].n++;
		t[i].last = tn - t0;
		total++;
	} while (tn - t0 < secs);

	printf("idle-watch: %.1f s on cpu%d, %" PRIu64 " samples, %d distinct\n", secs, cpu, total, nt);
	printf("%10s %8s %8s  %-8s %-8s %-8s %-8s %-8s %-8s %-8s %-8s\n", "count", "first_s", "last_s",
	       "gate44", "eng40", "st_E", "st_P0", "st_P1", "EACC", "PACC0", "PACC1");
	for (i = 0; i < nt; i++)
		printf("%10" PRIu64 " %8.4f %8.4f  %08x %08x %08x %08x %08x %08x %08x %08x\n", t[i].n, t[i].first,
		       t[i].last, t[i].w[0], t[i].w[1], t[i].w[2], t[i].w[3], t[i].w[4], t[i].w[5], t[i].w[6], t[i].w[7]);
	return 0;
}
