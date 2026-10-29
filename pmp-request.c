// pmp-request: show the PMP report requests on an M1 Pro/Max (T600x): the
// status, target and actual masks of pmp_report@28e3c0000, and which request
// ids are set in each. 0xc is pmp-disp0, 0xd pmp-dispext0, 0xe pmp-dispext1.
//
// Read-only. Withdrawing a request is the pmp_sleep_release module's job: the
// kernel driver updates the same mask under a spinlock userspace can't take, so
// a userspace write could lose a concurrent driver update.
//
// Every access is an aligned 64-bit load through a /dev/mem mapping, which arm64
// maps as device memory, as the driver's readq is. It refuses to run unless the
// live device tree describes this exact report block.
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define REPORT_BASE 0x28e3c0000ULL
#define REPORT_SIZE 0x20000ULL
#define DT_NODE "/sys/firmware/devicetree/base/soc/pmp_report@28e3c0000"
#define DT_COMPATIBLE "apple,t6000-pmp-v2-report"

// apple_pmp_offsets_t600x
#define STATUS 0x10
#define TGT_READ 0xf80
#define ACTUAL 0x1000
#define REPORT_READY 0x1

#ifndef __aarch64__
#error "pmp-request is for arm64 Macs"
#endif

static volatile uint8_t *regs;

// One 64-bit ldr, as the kernel's __raw_readq: the compiler may not merge,
// split or widen it.
static __attribute__((noinline)) uint64_t rd(uint64_t off)
{
	uint64_t val;

	asm volatile("ldr %0, [%1]" : "=r"(val) : "r"(regs + off) : "memory");
	return val;
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

int main(int argc, char **argv)
{
	void *map;
	int fd;

	(void)argv;
	if (argc != 1) {
		fprintf(stderr, "usage: pmp-request\n");
		return 2;
	}
	if (check_device_tree())
		return 2;
	fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) {
		perror("pmp-request: /dev/mem");
		return 2;
	}
	map = mmap(NULL, REPORT_SIZE, PROT_READ, MAP_SHARED, fd, REPORT_BASE);
	close(fd);
	if (map == MAP_FAILED) {
		perror("pmp-request: mmap");
		return 2;
	}
	regs = map;
	show();
	return 0;
}
