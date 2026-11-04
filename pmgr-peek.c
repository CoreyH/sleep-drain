// pmgr-peek (T6000, as root): read PMGR registers through /dev/mem, read-only.
//
//   pmgr-map | pkexec pmgr-peek
//
// It reads a list of physical addresses from stdin, one per line, "ADDR LABEL"
// (ADDR in hex, the label optional), and prints "ADDR VALUE LABEL" for each.
// Before reading anything it checks every address. It refuses the whole list
// unless each one is 4-byte aligned and inside one of the PMGR windows the
// live device tree describes: the reg of each power-management@ node that is
// "apple,t6000-pmgr". Those windows hold the power-state registers the kernel's
// pmgr-pwrstate driver reads and writes.
//
// With --cpu-start it also reads the CPU-start block's words at PMGR + 0x54000
// + 0x4, 0x8, 0xc and 0x10: the system-wide start word and one per cluster
// (Asahi's smp.md; m1n1's smp.c writes them to start each core, and T6000 has
// three clusters). That block is outside the windows Linux maps. It never
// reads + 0x0, which m1n1 writes to stop a core.
//
// With --sleep-regs it reads, last, nine words that macOS 15.6.1 writes on its
// way into sleep on a J314s (maralcbr's m1n1 hypervisor trace, replayed in
// maralcbr/linux 37be2668, drivers/power/reset/macsmc-reboot.c), from the best
// known to the least:
// - 0x292004008-0x292004020, the AP wake-source masks (ADT pmgr reg[7]; a set
//   bit masks a source), which maralcbr's replay reads before writing;
// - 0x28e224000 (pmgr reg[46]), which macOS steps through 0x219, 0x21b,
//   0x8000021a and 0x21a just before its last write;
// - 0x2925c4000 (pmgr reg[36] + 0x4000), that last write (1): the S2R timer,
//   by analogy with T6020, where macOS starts it with a 1 at MPMGR_AON + 0x4000.
// No Linux driver maps any of them. Every line is flushed and synced as it's
// printed, so if a read faults, the output shows which read it was.
//
// Every read is one aligned 32-bit ldr from a /dev/mem mapping. arm64 maps
// /dev/mem's non-RAM pages as device memory (nGnRnE), as the kernel maps this
// SoC's MMIO for readl. Nothing is ever written. The mapping is per 16 KiB
// page, the kernel's page size.
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#ifndef __aarch64__
#error "pmgr-peek is for arm64 Macs"
#endif

#define DT_SOC "/sys/firmware/devicetree/base/soc"
#define PMGR_COMPATIBLE "apple,t6000-pmgr"
#define PMGR0 0x28e080000ULL
#define CPU_START (PMGR0 + 0x54000)
#define WAKE_MASKS 0x292004008ULL
#define PMGR46 0x28e224000ULL
#define S2R_TIMER 0x2925c4000ULL
#define MAX_WINDOWS 16
#define MAX_ADDRS 2048
#define LABEL_LEN 64

struct window {
	uint64_t base, size;
};

struct entry {
	uint64_t addr;
	char label[LABEL_LEN];
};

static struct window windows[MAX_WINDOWS];
static int nwindows;
static struct entry entries[MAX_ADDRS];
static int nentries;

static uint32_t rd32(const volatile void *p)
{
	uint32_t v;

	asm volatile("ldr %w0, [%1]" : "=r"(v) : "r"(p) : "memory");
	return v;
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

static int has_compatible(const char *node, const char *want)
{
	unsigned char buf[256];
	char path[600];
	int n, i = 0;

	snprintf(path, sizeof(path), "%s/compatible", node);
	n = read_file(path, buf, sizeof(buf) - 1);
	if (n <= 0)
		return 0;
	buf[n] = '\0';
	while (i < n) {
		if (!strcmp((char *)buf + i, want))
			return 1;
		i += strlen((char *)buf + i) + 1;
	}
	return 0;
}

static uint64_t be64(const unsigned char *p)
{
	uint64_t v = 0;

	for (int i = 0; i < 8; i++)
		v = v << 8 | p[i];
	return v;
}

// The PMGR windows: reg of each "apple,t6000-pmgr" node (#address-cells and
// #size-cells are 2 under /soc).
static int load_windows(void)
{
	DIR *d = opendir(DT_SOC);
	struct dirent *e;
	int pmgr0 = 0;

	if (!d) {
		perror("pmgr-peek: " DT_SOC);
		return -1;
	}
	while ((e = readdir(d))) {
		unsigned char reg[16 * MAX_WINDOWS];
		char node[512], path[600];
		int n;

		if (strncmp(e->d_name, "power-management@", 17))
			continue;
		snprintf(node, sizeof(node), DT_SOC "/%s", e->d_name);
		if (!has_compatible(node, PMGR_COMPATIBLE))
			continue;
		snprintf(path, sizeof(path), "%s/reg", node);
		n = read_file(path, reg, sizeof(reg));
		if (n <= 0 || n % 16) {
			fprintf(stderr, "pmgr-peek: %s has an unexpected reg; refusing\n", node);
			closedir(d);
			return -1;
		}
		for (int i = 0; i < n; i += 16) {
			if (nwindows == MAX_WINDOWS)
				break;
			windows[nwindows].base = be64(reg + i);
			windows[nwindows].size = be64(reg + i + 8);
			if (windows[nwindows].base == PMGR0)
				pmgr0 = 1;
			nwindows++;
		}
	}
	closedir(d);
	if (!nwindows || !pmgr0) {
		fprintf(stderr, "pmgr-peek: no %s at %#llx in the device tree; refusing\n",
			PMGR_COMPATIBLE, (unsigned long long)PMGR0);
		return -1;
	}
	return 0;
}

// Compared without sums, so no address can wrap around into a window.
static int allowed(uint64_t addr)
{
	if (addr % 4)
		return 0;
	for (int i = 0; i < nwindows; i++)
		if (windows[i].size >= 4 && addr >= windows[i].base &&
		    addr - windows[i].base <= windows[i].size - 4)
			return 1;
	return 0;
}

static int add(uint64_t addr, const char *label)
{
	if (nentries == MAX_ADDRS) {
		fprintf(stderr, "pmgr-peek: more than %d addresses\n", MAX_ADDRS);
		return -1;
	}
	entries[nentries].addr = addr;
	snprintf(entries[nentries].label, LABEL_LEN, "%s", label);
	nentries++;
	return 0;
}

static int load_stdin(void)
{
	char line[256];
	int lineno = 0;

	while (fgets(line, sizeof(line), stdin)) {
		char *end, *label;
		uint64_t addr;

		lineno++;
		line[strcspn(line, "\n")] = '\0';
		if (!line[0] || line[0] == '#')
			continue;
		errno = 0;
		addr = strtoull(line, &end, 16);
		// A hex digit first: strtoull would take "-4" or " +4" too.
		if (!isxdigit((unsigned char)line[0]) || errno || end == line ||
		    (*end && *end != ' ' && *end != '\t')) {
			fprintf(stderr, "pmgr-peek: line %d: bad address\n", lineno);
			return -1;
		}
		label = end + strspn(end, " \t");
		if (!allowed(addr)) {
			fprintf(stderr, "pmgr-peek: line %d: %#" PRIx64
				" is unaligned or outside the PMGR windows; refusing\n", lineno, addr);
			return -1;
		}
		if (add(addr, label))
			return -1;
	}
	return 0;
}

int main(int argc, char **argv)
{
	long page = sysconf(_SC_PAGESIZE);
	int cpu_start = 0, sleep_regs = 0, fd;

	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--cpu-start")) {
			cpu_start = 1;
		} else if (!strcmp(argv[i], "--sleep-regs")) {
			sleep_regs = 1;
		} else {
			fprintf(stderr, "usage: pmgr-peek [--cpu-start] [--sleep-regs]"
				" < 'ADDR LABEL' lines\n");
			return 2;
		}
	}
	if (load_windows() || load_stdin())
		return 2;
	if (cpu_start) {
		static const char *const names[] = {
			"cpu-start+0x4", "cpu-start+0x8", "cpu-start+0xc", "cpu-start+0x10",
		};

		for (int i = 0; i < 4; i++)
			if (add(CPU_START + 4 + 4 * i, names[i]))
				return 2;
	}
	if (sleep_regs) {
		char name[LABEL_LEN];

		for (int i = 0; i < 7; i++) {
			snprintf(name, sizeof(name), "ap-wake-mask%d", i);
			if (add(WAKE_MASKS + 4 * i, name))
				return 2;
		}
		if (add(PMGR46, "pmgr46+0x0") || add(S2R_TIMER, "s2r-timer?"))
			return 2;
	}

	fd = open("/dev/mem", O_RDONLY | O_SYNC);
	if (fd < 0) {
		perror("pmgr-peek: /dev/mem");
		return 2;
	}
	for (int i = 0; i < nentries; i++) {
		uint64_t pa = entries[i].addr & ~(uint64_t)(page - 1);
		volatile uint8_t *m;

		m = mmap(NULL, page, PROT_READ, MAP_SHARED, fd, (off_t)pa);
		if (m == MAP_FAILED) {
			fprintf(stderr, "pmgr-peek: mmap %#" PRIx64 ": %s\n", pa, strerror(errno));
			return 2;
		}
		printf("%#" PRIx64 " %08" PRIx32 " %s\n", entries[i].addr,
		       rd32(m + (entries[i].addr - pa)), entries[i].label);
		munmap((void *)m, page);
		// On a pipe or a tty fsync fails, which is fine: it's for a file.
		fflush(stdout);
		fsync(STDOUT_FILENO);
	}
	close(fd);
	return 0;
}
