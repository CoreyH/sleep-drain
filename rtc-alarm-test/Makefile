# The PMU RTC alarm test on J314s, against the running kernel's headers (see
# load): rtc-macsmc with the PMU alarm (aurora #274) and the Maverick event
# register, the integration branch's SPMI controller, and the module that
# describes the alarm in the live device tree.
obj-m := rtc-macsmc-alarm.o spmi-apple-wake.o pmu-alarm-dt.o
KDIR ?= /usr/lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) LLVM=1 modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) LLVM=1 clean
