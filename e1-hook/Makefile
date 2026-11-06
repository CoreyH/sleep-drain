# Out-of-tree build against the running kernel's headers (linux-aurora-headers).
# The kernel is built with clang, so the module is too.
obj-m := e1_hook.o
KDIR ?= /usr/lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) LLVM=1 modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) LLVM=1 clean
