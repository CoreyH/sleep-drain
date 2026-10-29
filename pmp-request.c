// pmp-request: read, withdraw or assert one PMP report request on an M1 Pro/Max
// (T600x), the requests the kernel's pmp-report driver makes on behalf of a
// block such as an external display processor.
//
//   pmp-request               print the status, target and actual masks
//   pmp-request clear <id>    withdraw request <id> (0xd is pmp-dispext0)
//   pmp-request set <id>      assert request <id> again
//
// The register sequence is the driver's own (apple_pmp_report_set_state() in
// drivers/pmdomain/apple/pmp-report.c): read the target mask, change one bit,
// write it back, and wait up to 50 ms for the PMP to show the change in its
// actual mask. Every access is an aligned 64-bit load or store through a
// /dev/mem mapping, which arm64 maps as device memory, as the driver's readq and
// writeq are. It refuses to run unless the live device tree describes this
// exact report block, so it can't write to the wrong address on another Mac.
//
// The kernel driver changes the same mask under its own spinlock, which
// userspace can't take. So this refuses to write while any request is still
// settling (target != actual: the driver may be mid-update), does its
// read-modify-write as back-to-back instructions at SCHED_FIFO priority, and
// reports if the mask changed anywhere else while the write was acknowledged.
// What remains is a window of a few instructions. If the driver updated another
// request inside it, that update would be lost and its power-on or power-off
// would time out once. This is an experiment tool; the fix belongs in the
// driver (aurora #274, patch 6), under its lock.
//
// Exit status: 0 changed and acknowledged; 1 written but not acknowledged in
// time; 2 usage or setup error; 3 already in that state, nothing written;
// 4 another request was still settling, nothing written; 5 changed and
// acknowledged, but the mask also changed elsewhere meanwhile.
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define REPORT_BASE 0x28e3c0000ULL
#define REPORT_SIZE 0x20000ULL
#define DT_NODE "/sys/firmware/devicetree/base/soc/pmp_report@28e3c0000"
#define DT_COMPATIBLE "apple,t6000-pmp-v2-report"

// apple_pmp_offsets_t600x
#define STATUS 0x10
#define TGT_READ 0xf80
#define ACTUAL 0x1000
#define TGT_WRITE 0x107c0
#define REPORT_READY 0x1

#ifndef __aarch64__
#error "pmp-request is for arm64 Macs"
#endif

static volatile uint8_t *regs;

// One 64-bit ldr/str each, as the kernel's __raw_readq/__raw_writeq: the
// compiler may not merge, split or widen them.
static __attribute__((noinline)) uint64_t rd(uint64_t off)
{
	uint64_t val;

	asm volatile("ldr %0, [%1]" : "=r"(val) : "r"(regs + off) : "memory");
	return val;
}

static __attribute__((noinline)) void wr(uint64_t off, uint64_t val)
{
	asm volatile("str %0, [%1]" : : "r"(val), "r"(regs + off) : "memory");
}

static int read_file(const char *path, unsigned char *buf, size_t len)
{
	FILE *f = fopen(path, "rb");
	size_t n;

	if (!f)
		return -1;
	n = fread(buf, 1, len, f);
	fclose(f);
	return (int)n;
}

// The node must be this report block: same compatible, same reg.
static int check_device_tree(void)
{
	static const unsigned char reg[16] = {
		0x00, 0x00, 0x00, 0x02, 0x8e, 0x3c, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00,
	};
	unsigned char buf[64];
	int n;

	n = read_file(DT_NODE "/compatible", buf, sizeof(buf) - 1);
	if (n <= 0 || memcmp(buf, DT_COMPATIBLE, sizeof(DT_COMPATIBLE)) != 0) {
		fprintf(stderr, "pmp-request: %s is not %s; refusing\n", DT_NODE, DT_COMPATIBLE);
		return -1;
	}
	n = read_file(DT_NODE "/reg", buf, sizeof(buf));
	if (n != (int)sizeof(reg) || memcmp(buf, reg, sizeof(reg)) != 0) {
		fprintf(stderr, "pmp-request: %s has an unexpected reg; refusing\n", DT_NODE);
		return -1;
	}
	return 0;
}

static void show(void)
{
	uint64_t target = rd(TGT_READ), actual = rd(ACTUAL);

	printf("status=0x%llx target=0x%016llx actual=0x%016llx\n",
	       (unsigned long long)rd(STATUS), (unsigned long long)target,
	       (unsigned long long)actual);
	for (int id = 0; id < 64; id++)
		if ((target | actual) & (1ULL << id))
			printf("  request 0x%x: target=%d actual=%d\n", id,
			       !!(target & (1ULL << id)), !!(actual & (1ULL << id)));
}

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int change(unsigned int id, int enable)
{
	struct sched_param fifo = { .sched_priority = sched_get_priority_max(SCHED_FIFO) };
	uint64_t bit = 1ULL << id, actual, target, expect;
	double until;

	if (!(rd(STATUS) & REPORT_READY)) {
		fprintf(stderr, "pmp-request: report not ready; leaving it alone\n");
		return 2;
	}
	// Best effort: nothing in the window below may be preempted by a normal task.
	if (sched_setscheduler(0, SCHED_FIFO, &fifo))
		perror("pmp-request: SCHED_FIFO (continuing)");

	actual = rd(ACTUAL);
	target = rd(TGT_READ);
	if (target != actual) {
		fprintf(stderr, "pmp-request: a request is still settling (target 0x%llx, actual 0x%llx); "
			"leaving it alone\n", (unsigned long long)target, (unsigned long long)actual);
		return 4;
	}
	if (!!(target & bit) == enable)
		return 3;
	expect = enable ? target | bit : target & ~bit;
	wr(TGT_WRITE, expect);

	for (until = now() + 0.05; now() < until; usleep(100)) {
		if (!!(rd(ACTUAL) & bit) != enable)
			continue;
		if (rd(TGT_READ) != expect) {
			fprintf(stderr, "pmp-request: request 0x%x changed, but the mask changed elsewhere "
				"meanwhile (now 0x%llx)\n", id, (unsigned long long)rd(TGT_READ));
			return 5;
		}
		return 0;
	}
	fprintf(stderr, "pmp-request: request 0x%x not acknowledged within 50 ms\n", id);
	return 1;
}

int main(int argc, char **argv)
{
	unsigned long id = 0;
	int fd, enable = -1;
	char *end;
	void *map;

	if (argc == 3 && (!strcmp(argv[1], "set") || !strcmp(argv[1], "clear"))) {
		enable = !strcmp(argv[1], "set");
		errno = 0;
		id = strtoul(argv[2], &end, 0);
		if (errno || *end || id > 63) {
			fprintf(stderr, "pmp-request: bad request id %s\n", argv[2]);
			return 2;
		}
	} else if (argc != 1) {
		fprintf(stderr, "usage: pmp-request [set|clear <id>]\n");
		return 2;
	}

	if (check_device_tree())
		return 2;
	fd = open("/dev/mem", (enable < 0 ? O_RDONLY : O_RDWR) | O_SYNC);
	if (fd < 0) {
		perror("pmp-request: /dev/mem");
		return 2;
	}
	map = mmap(NULL, REPORT_SIZE, PROT_READ | (enable < 0 ? 0 : PROT_WRITE), MAP_SHARED,
		   fd, REPORT_BASE);
	close(fd);
	if (map == MAP_FAILED) {
		perror("pmp-request: mmap");
		return 2;
	}
	regs = map;

	if (enable < 0) {
		show();
		return 0;
	}
	return change((unsigned int)id, enable);
}
