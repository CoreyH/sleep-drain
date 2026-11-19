// rvbar-peek (J314s, as root): each core's RVBAR, read-only, as one aligned 64-bit
// load through /dev/mem, made on the core itself (the process is moved to it first).
//
//   cc -O1 -Wall -o rvbar-peek rvbar-peek.c     (build; it doesn't run it)
//   pkexec ./rvbar-look                         (in a safe boot: the read, shown and saved)
//
// macOS's device tree gives each core a cpu-impl-reg block, with RVBAR at + 0x0
// (RESEARCH.md, "The CPUs"): 0x210050000 and 0x210150000 for the two E cores,
// 0x211050000, 0x211150000, 0x211250000 and 0x212050000, 0x212150000, 0x212250000
// for the P cores, in Linux's cpu0 to cpu7. XNU's sleep path has the reconfig
// engine bring the boot core out of reset at this vector; whether iBoot leaves it
// locked on this chip (bit 0) decides who can point it somewhere. The first read
// was made in a safe boot, through rvbar-look, on 2026-11-19 (PLAN.md's rule for
// a first read): all eight read cleanly (results/rvbar-20261119/). Run it through
// rvbar-look; by that rule a word read cleanly in this state may also be read from
// a regular boot, as pkexec ./rvbar-peek. Nothing is written. It refuses anything
// but a J314s.
#define _GNU_SOURCE
#include <fcntl.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static const uint64_t impl[8] = {
	0x210050000UL, 0x210150000UL, 0x211050000UL, 0x211150000UL,
	0x211250000UL, 0x212050000UL, 0x212150000UL, 0x212250000UL,
};

static uint64_t rd64(volatile uint8_t *p)
{
	uint64_t v;
	asm volatile("ldr %0, [%1]" : "=r"(v) : "r"(p) : "memory");
	return v;
}

int main(void)
{
	char compat[256];
	FILE *f = fopen("/proc/device-tree/compatible", "r");
	size_t n = f ? fread(compat, 1, sizeof(compat) - 1, f) : 0;
	int j314s = 0;

	if (f)
		fclose(f);
	compat[n] = 0;
	for (size_t i = 0; i < n; i += strlen(compat + i) + 1)
		j314s |= !strcmp(compat + i, "apple,j314s");
	if (!j314s) {
		fprintf(stderr, "rvbar-peek: this isn't a J314s\n");
		return 2;
	}
	int fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) { perror("/dev/mem"); return 1; }
	for (int cpu = 0; cpu < 8; cpu++) {
		cpu_set_t set;

		CPU_ZERO(&set);
		CPU_SET(cpu, &set);
		if (sched_setaffinity(0, sizeof(set), &set) || sched_getcpu() != cpu) {
			printf("cpu%d: not read (couldn't move to it)\n", cpu);
			continue;
		}
		uint8_t *m = mmap(NULL, 0x4000, PROT_READ, MAP_SHARED, fd, impl[cpu]);
		if (m == MAP_FAILED) { perror("mmap"); return 1; }
		uint64_t v = rd64(m);
		printf("cpu%d: RVBAR at %#lx = %#018lx (bit 0 %s), read on cpu%d\n", cpu, (unsigned long)impl[cpu],
		       (unsigned long)v, v & 1 ? "set" : "clear", sched_getcpu());
		fflush(stdout);
		munmap(m, 0x4000);
	}
	return 0;
}
