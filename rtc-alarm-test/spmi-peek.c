// spmi-peek (J314s, as root): nub-spmi0's status word and its interrupt mask
// (0x20-0x40) and ack (0x60-0x80) words, read-only, as aligned 32-bit loads
// through /dev/mem. It never touches 0x8, the reply register, which may pop the
// RX FIFO. The mapping starts on a 16 KiB boundary, the kernel's page size.
//
//   cc -O1 -Wall -o spmi-peek spmi-peek.c && pkexec ./spmi-peek
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#define PAGE 0x2920a0000UL
#define REGS 0x1300

static uint32_t rd(volatile uint8_t *p)
{
	uint32_t v;
	asm volatile("ldr %w0, [%1]" : "=r"(v) : "r"(p) : "memory");
	return v;
}

int main(void)
{
	int fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) { perror("/dev/mem"); return 1; }
	uint8_t *m = mmap(NULL, 0x4000, PROT_READ, MAP_SHARED, fd, PAGE);
	if (m == MAP_FAILED) { perror("mmap"); return 1; }
	volatile uint8_t *r = m + REGS;
	uint32_t st = rd(r);
	printf("status: %08x (rx fifo %s)\n", st, st & (1u << 24) ? "empty" : "NOT empty");
	for (int w = 0; w < 9; w++) {
		uint32_t mask = rd(r + 0x20 + 4 * w), ack = rd(r + 0x60 + 4 * w);
		printf("word %d (irqs %3d-%3d): mask@%#x %08x  ack@%#x %08x\n",
		       w, 32 * w, 32 * w + 31, 0x20 + 4 * w, mask, 0x60 + 4 * w, ack);
	}
	return 0;
}
