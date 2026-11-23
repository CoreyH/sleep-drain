// peek4 (T6000, as root, awake): four read-only 32-bit loads through /dev/mem.
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
#define PG 0x4000UL
static uint32_t rd(int fd, uint64_t pa)
{
	void *p = mmap(NULL, PG, PROT_READ, MAP_SHARED, fd, (off_t)(pa & ~(PG - 1)));
	uint32_t v;
	if (p == MAP_FAILED) { perror("mmap"); exit(1); }
	__asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"((const char *)p + (pa & (PG - 1))));
	munmap(p, PG);
	return v;
}
int main(void)
{
	static const struct { uint64_t pa; const char *what; } w[] = {
		{ 0x2922c0044ULL, "the engine's gate word (B2's first poll)" },
		{ 0x2922c0040ULL, "the word B2 lowers behind it" },
		{ 0x28e580c00ULL, "SEP's power word" },
		{ 0x28e0cc200ULL, "PMGR control word; bit 2 = wait for the SEP's power gate (Ante042's s2r.c)" },
	};
	int fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) { perror("/dev/mem"); return 1; }
	for (unsigned i = 0; i < sizeof(w) / sizeof(w[0]); i++) {
		printf("0x%llx reading...\n", (unsigned long long)w[i].pa); fflush(stdout); fsync(1);
		printf("0x%llx = 0x%08x  %s\n", (unsigned long long)w[i].pa, rd(fd, w[i].pa), w[i].what); fflush(stdout); fsync(1);
	}
	return 0;
}
