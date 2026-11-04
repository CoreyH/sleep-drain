#!/usr/bin/python3 -I
"""Hardware-free checks for the A2 decoders: pmgr-map (macOS's device tree
into PMGR power-state registers) and smc-keys (smc-peek's dumps).

  python3 -I tests/peek_decoders_test.py      (from the repo root)

pmgr-map runs on a small synthetic ioreg dump: an arm-io with T6000's ranges,
a pmgr1,t6000 node with two register windows, three ps-regs banks and four
devices (two with a register, one no_ps, one u16 id parent), so every address
is known in advance. If this Mac's own dump is present, the real decode is
checked against what A2 found in it: 355 devices, 187 registers, the CPU cores
at PMGR + 0x0. smc-keys decodes and diffs synthetic smc-peek lines.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_ADT = "/boot/macos-session/iodevicetree.txt"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


pmgr_map = load("pmgr_map", os.path.join(ROOT, "pmgr-map"))
smc_keys = load("smc_keys", os.path.join(ROOT, "smc-keys"))


def device(name, flags, dev_id, parents=(0, 0), psreg=0, psidx=0):
    """One 48-byte PMGRDevices entry (m1n1 adt.py), u16 ids."""
    d = bytearray(48)
    d[0] = flags
    d[3] = 0  # id1: the same for every device, so ids are u16 (id2)
    struct.pack_into("<HH", d, 4, *parents)
    d[10], d[11] = psidx, psreg
    struct.pack_into("<H", d, 26, dev_id)
    d[32:32 + len(name)] = name.encode()
    return bytes(d)


def prop(name, data):
    return f'    | | |   "{name}" = <{data.hex()}>\n'


def synthetic_adt():
    ranges = struct.pack("<QQQ", 0, 0x200000000, 0x400000000)
    reg = struct.pack("<QQQQ", 0x8e080000, 0x80000, 0x92280000, 0x74000)
    psregs = struct.pack("<III", 1, 0, 0) + struct.pack("<III", 0, 0x0, 0x3) + \
        struct.pack("<III", 0, 0x100, 0x1)
    devs = (device("ECPU0_0", 0x00, 1, psreg=1, psidx=0)
            + device("AIC", 0x09, 2, psreg=2, psidx=1)           # on, critical
            + device("NO_REG", 0x19, 3)                          # on, critical, no_ps
            + device("NUB_X", 0x01, 300, parents=(2, 0), psreg=0, psidx=11))
    text = "+-o Root  <class IORegistryEntry, id 0x1>\n"
    text += "  +-o device-tree  <class IOPlatformExpertDevice, id 0x2>\n"
    text += "    +-o arm-io  <class IOPlatformDevice, id 0x3>\n"
    text += "    | {\n" + prop("ranges", ranges).replace("| | |", "|") + "    | }\n"
    text += "    | +-o pmgr@8E080000  <class AppleARMIODevice, id 0x4>\n"
    text += "    | | {\n"
    text += '    | | |   "compatible" = <"pmgr1,t6000">\n'
    text += prop("reg", reg) + prop("ps-regs", psregs) + prop("devices", devs)
    text += prop("power-domains", b"")
    text += "    | | }\n"
    return text


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     {detail}"))
    return ok


def test_pmgr_synthetic():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(synthetic_adt())
    try:
        pm = pmgr_map.PMGR(f.name)
    finally:
        os.unlink(f.name)
    got = {d["name"]: d for d in pm.devs}
    ok = check("synthetic: arm-io ranges translate the pmgr windows",
               pm.reg[0][0] == 0x28e080000 and pm.reg[1][0] == 0x292280000, pm.reg)
    ok &= check("synthetic: ECPU0_0 at bank 1 slot 0 = PMGR + 0x0",
                got["ECPU0_0"]["addr"] == 0x28e080000, hex(got["ECPU0_0"]["addr"] or 0))
    ok &= check("synthetic: AIC at bank 2 slot 1 = PMGR + 0x108",
                got["AIC"]["addr"] == 0x28e080108, hex(got["AIC"]["addr"] or 0))
    ok &= check("synthetic: a no_ps device has no register", got["NO_REG"]["addr"] is None)
    ok &= check("synthetic: bank 0 is in the second window (0x292280000 + 11 * 8)",
                got["NUB_X"]["addr"] == 0x292280058, hex(got["NUB_X"]["addr"] or 0))
    ok &= check("synthetic: u16 ids and parents",
                got["NUB_X"]["id"] == 300 and got["NUB_X"]["parents"] == [2],
                (got["NUB_X"]["id"], got["NUB_X"]["parents"]))
    ok &= check("synthetic: flag bits (on, critical, no_ps)",
                got["NO_REG"]["flagnames"] == ["on", "critical", "no_ps"],
                got["NO_REG"]["flagnames"])
    return ok


def test_pmgr_real():
    if not os.path.exists(REAL_ADT):
        print(f"skip real ADT: no {REAL_ADT}")
        return True
    pm = pmgr_map.PMGR(REAL_ADT)
    addrs = {d["name"]: d["addr"] for d in pm.devs}
    ok = check("real: 355 devices, 187 with a register",
               len(pm.devs) == 355 and sum(a is not None for a in addrs.values()) == 187)
    ok &= check("real: cores and cluster managers at PMGR + 0x0..0x70",
                addrs["ECPU0_0"] == 0x28e080000 and addrs["PCPU1_3"] == 0x28e080058
                and addrs["PCPM1"] == 0x28e080070)
    ok &= check("real: AIC and SEP where Linux has them",
                addrs["AIC"] == 0x28e080108 and addrs["SEP"] == 0x28e580c00)
    fused = sorted(d["name"] for d in pm.devs if d.get("fused_off"))
    ok &= check("real: this 8-core part's fused-off cores", fused == ["PCPU0_3", "PCPU1_3"], fused)
    return ok


def test_ps_decode():
    v = 0x100000ff | (1 << 9)
    return check("PS decode: target, actual, auto, was_clkgated",
                 pmgr_map.decode(v) == "tgt=f act=f auto was_clkgated", pmgr_map.decode(v))


SMC_A = """# smc-peek key_count=8
0 #KEY ui32 4 80 00000008
1 MBSE ui32 4 d0 3166666f
2 BNum ui8  1 80 01
3 TB0T flt  4 80 0000c841
4 RGEN ch8* 4 80 41424300
5 NTAP flag 1 50 func
6 EMP1 ui16 2 80 empty
7 EMP2 ui16 2 80 
# end keys=8 read=7 errors=0
"""


def test_smc_decode():
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
        with open(a, "w") as f:
            f.write(SMC_A)
        with open(b, "w") as f:
            f.write(SMC_A.replace("3166666f", "77706c73")
                    .replace("4 RGEN", "9 RGEN").replace("5 NTAP flag 1 50 func\n", ""))
        keys, meta = smc_keys.load(a)
        ok = check("smc: eight keys and two meta lines", len(keys) == 8 and len(meta) == 2,
                   (len(keys), meta))
        ok &= check("smc: an empty value, printed or not, is 'empty'",
                    keys["EMP1"]["value"] == "empty" and keys["EMP2"]["value"] == "empty")
        ok &= check("smc: #KEY is big-endian", smc_keys.decode("ui32", "00000008", "#KEY")
                    .startswith("8 (0x8)"), smc_keys.decode("ui32", "00000008", "#KEY"))
        ok &= check("smc: MBSE's ui32 reads as 'off1', raw bytes shown",
                    "'off1' [3166666f]" in smc_keys.decode("ui32", keys["MBSE"]["value"], "MBSE"),
                    smc_keys.decode("ui32", keys["MBSE"]["value"], "MBSE"))
        ok &= check("smc: flt little-endian", smc_keys.decode("flt ", "0000c841") == "25 [0000c841]",
                    smc_keys.decode("flt ", "0000c841"))
        ok &= check("smc: ch8* string", smc_keys.decode("ch8*", "41424300") == "'ABC'")
        ok &= check("smc: flags r-f/rwf", smc_keys.flagstr(0x50) == "-wf"
                    and smc_keys.flagstr(0xd0) == "rwf")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            smc_keys.diff(a, b, None)
        lines = [l for l in out.getvalue().splitlines() if not l.startswith(("/", a, b))]
        ok &= check("smc: diff shows MBSE off1 -> slpw and NTAP gone",
                    any("MBSE" in l and "'off1'" in l and "'slpw'" in l for l in lines)
                    and any(l.startswith("- NTAP") for l in lines), lines)
    return ok


if __name__ == "__main__":
    results = [test_pmgr_synthetic(), test_pmgr_real(), test_ps_decode(), test_smc_decode()]
    sys.exit(0 if all(results) else 1)
