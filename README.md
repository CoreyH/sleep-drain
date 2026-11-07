# sleep-drain

Research and tooling for one problem on one machine. A 14-inch MacBook Pro
with an M1 Pro (J314s, T6000), running [Omarchy](https://omarchy.org) on the
[omacom/linux-aurora](https://github.com/omacom/linux-aurora) kernel, drains
its battery at about 2 W in Linux's suspend (s2idle, about 157-162 mA). macOS
sleeps on the same Mac at about 0.1 W. The goal is an upstream fix: the right
default for every Apple Silicon Mac on aurora, not a patch for this one.

## Where to start

- [RESEARCH.md](RESEARCH.md) is the lab notebook: the problem, every
  experiment and its result, and what's known so far.
- [PLAN.md](PLAN.md) is what's next, in order.
- [CLAUDE.md](CLAUDE.md) is the working conventions and the map of every tool
  in this repo. It's written for the AI assistant that does most of the work
  here (below), and it's also the best description of the layout.
- `results/` holds the raw output of the experiments that RESEARCH.md cites.

The current work, E2 in PLAN.md, tries to make the SoC's own suspend-to-RAM
entry complete from Linux, as macOS does. It builds on the work credited
below. RESEARCH.md's last sections give the latest run.

## This is lab code

Several tools here read or write hardware registers on this one machine.
`e1-hook/run-floor` and its variants are one way: they put the SoC into a
sleep entry it doesn't come back from, and each run ends in a reset or with
the power key held down. The module refuses them except on a J314s, in this
repo's safe boot, which keeps the disk read-only. Even so, don't run them on
a Mac you can't afford to hard-reset, and never on another model without
reading the code. Each tool's header, and CLAUDE.md, say what it touches.

## Credits

This builds on other people's work, cited where it's used:

- maralcbr ([maralcbr/linux](https://github.com/maralcbr/linux)): the replay
  of macOS's sleep entry on a J314s, whose keys and register table the
  `e1-hook` experiments port.
- Naeem Malik ([malik-na/linux-aurora](https://github.com/malik-na/linux-aurora),
  branch `m1power/s2r-mem`, and
  [malik-na/m1-power-lab](https://github.com/malik-na/m1-power-lab)): the first
  Linux suspend-to-RAM entry that completes, on the M1 (T8103). The M1 recipe
  here maps it onto the M1 Pro.
- DeepIdle ([JJRPF/Aurora-DeepIdle](https://github.com/JJRPF/Aurora-DeepIdle)):
  the same work on the M2 Pro (T6020), and much of what's known about the
  sleep entry's commit and its watchdog.
- [Asahi Linux](https://asahilinux.org): the hardware documentation and the
  drivers everything here runs on.

m1n1 is used only as a tool. No m1n1 code is in this repo, and none was
written here: Asahi's m1n1 asks AI tools not to work on it.

## How it's made

Most of the code and the notes here were written by Claude, Anthropic's
model, in Claude Code. Corey directs the work and runs every experiment on
the hardware. Most commits carry a `Co-Authored-By` line, and
[Pullfrog](https://pullfrog.com) reviews every pull request.

## License

GPL-2.0-only ([LICENSE](LICENSE)), except where a file's SPDX header says
otherwise: `rtc-alarm-test/rtc-macsmc-alarm.c` is GPL-2.0-only OR MIT. The
kernel modules carry their own GPL headers, and the series in `patches/` are
changes to the Linux kernel, under its licence.
