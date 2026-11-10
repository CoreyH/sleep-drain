#!/usr/bin/python3 -I
"""tests/mutants.py: break the one-way runs' code one way at a time and see that the tests notice (tests/m1_crumbs_test.py;
tests/e1_hook_probe_test.py for the launcher e1-hook/probe).

  python3 -I -B tests/mutants.py            every mutant (a few minutes: six at a time)
  python3 -I -B tests/mutants.py wd1 fab    only those whose name has one of these words

Each mutant is one change to e1-hook/e1_hook.c, one of its headers, e1-hook/m1-crumbs or e1-hook/probe: a wrong value, a write
that isn't gated, a check dropped, a bit read wrong. It's applied in a copy of e1-hook/ and tests/, never in the
tree, and the tests run there. A mutant is caught if a check fails or the tests can't build what they compile; it
survived if they still say "all ok", and then the tests have a hole. A mutant whose text isn't in the file any more
is reported as stale: the code moved, and the list needs the same change. Exit status: 0 if every one was caught.

Add the mutants a new rung calls for when it's built: the ones a review would ask about.
"""
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
E1 = os.path.realpath(os.path.join(HERE, "..", "e1-hook"))
C, CRUMBS, FAB, WD1, ASC, PROBE, LOOK, GATE = "e1_hook.c", "m1-crumbs", "fabric_up.h", "wd1_mark.h", "asc_probe.h", "probe", "look.h", "gate_test.h"
# The test that has to notice a mutant of each file: the launcher e1-hook/probe has its own.
TESTS = {PROBE: "e1_hook_probe_test.py"}

# (name, file, the text to change, what it becomes); the text must occur exactly once.
MUTANTS = [
    # cluster_ack=1: the three values, the waits, the record
    ("ack: on by default", C, "static bool cluster_ack;", "static bool cluster_ack = true;"),
    ("ack: parameter writable (0644)", C, "module_param(cluster_ack, bool, 0444);", "module_param(cluster_ack, bool, 0644);"),
    ("ack: value 0x2f", C, "#define M1_ACK_VAL\t\t0x20U", "#define M1_ACK_VAL\t\t0x2fU"),
    ("ack: a fourth entry (EACC)", C, "i == M1_ACK_AFR ||", "i == M1_ACK_AFR || i == 52 ||"),
    ("ack: wrong entry (56)", C, "#define M1_ACK_AFR\t\t57", "#define M1_ACK_AFR\t\t56"),
    ("ack: without the parameter", C, "if (cluster_ack && (i == M1_ACK_ANE0", "if ((i == M1_ACK_ANE0"),
    ("ack: one write bypasses m1_post_val()", C, "writel_relaxed(m1_post_val(i), post_map[i]);", "writel_relaxed(w->val, post_map[i]);"),
    ("ack: a write of 0x20 at load", C, "\tfor (i = 0; i < ARRAY_SIZE(ack); i++)\n\t\tpr_info(\"m1: cluster_ack: entry",
     "\tfor (i = 0; i < ARRAY_SIZE(ack); i++)\n\t\twritel(M1_ACK_VAL, post_map[ack[i]]);\n\tfor (i = 0; i < ARRAY_SIZE(ack); i++)\n\t\tpr_info(\"m1: cluster_ack: entry"),
    ("ack: whole-word wait without the parameter", C, "\t\tif (cluster_ack)\n\t\t\treturn !v;", "\t\treturn !v;"),
    ("ack: cluster wait still bit 4 only", C, "\t\t\treturn !v;", "\t\t\treturn !(v & w->mask);"),
    ("ack: a word reading 0x20 counts as met", C, "\t\tmet = m1_wait_met(w, v);", "\t\tmet = m1_wait_met(w, v) || (cluster_ack && v == M1_ACK_VAL);"),
    ("ack: *last from another read", C, "\t*last = v;\n\treturn met;", "\t*last = readl(p);\n\treturn met;"),
    ("ack: 100 ms under cluster_ack", C, "\t\tif (met || m1_now() - t0 > m1_tps / 100)", "\t\tif (met || m1_now() - t0 > m1_tps / (cluster_ack ? 10 : 100))"),
    ("ack: qto bit on busy, not the flag", C, "\tif (v & M1_ACK_FLAG)\n\t\t*qto |= w->qto;", "\tif (v & M1_ACK_BUSY)\n\t\t*qto |= w->qto;"),
    ("ack: qto bit for any unmet cluster wait", C, "\tif (v & M1_ACK_FLAG)\n\t\t*qto |= w->qto;", "\t*qto |= w->qto;"),
    ("ack: bit 5 for the flag too", C, "\tif (v & ~M1_ACK_FLAG)\n\t\t*flags |= M1_F_ACK_REST;", "\tif (v)\n\t\t*flags |= M1_F_ACK_REST;"),
    ("ack: step 5 sets qto itself", C, "\t\t\t\tm1_wait_unmet(&m1_waits[k], v, &qto, &flags);", "\t\t\t\tqto |= m1_waits[k].qto;"),
    ("ack: mark set always", C, "\tif (cluster_ack)\n\t\tflags |= M1_F_ACK;", "\tflags |= M1_F_ACK;"),
    ("ack: mark in another bit", C, "#define M1_F_ACK\t\tBIT(4)", "#define M1_F_ACK\t\tBIT(3)"),
    ("ack: mark set after step 8's command", C, "\tm1_crumb(0x6010, b, 7);\n", "\tm1_crumb(0x6010, b, 7);\n\tif (cluster_ack)\n\t\tflags |= M1_F_ACK;\n"),
    ("ack: a second setter of bit 5", C, "\tmemcpy(b, rtc + 2, 4);\n\tb[4] = flags;", "\tflags |= BIT(5);\n\tmemcpy(b, rtc + 2, 4);\n\tb[4] = flags;"),
    ("ack: loads without wd2_bite", C, "if (cluster_ack && (!wd2_bite || !pmp_stop)) {", "if (cluster_ack && (!ps_words || !pmp_stop)) {"),
    ("ack: loads with pmp_stop=0", C, "if (cluster_ack && (!wd2_bite || !pmp_stop)) {", "if (cluster_ack && !wd2_bite) {"),
    ("ack: the recipe check weakened (&& !m1)", C, "if (cluster_ack && (!wd2_bite || !pmp_stop)) {", "if (cluster_ack && (!wd2_bite || !pmp_stop) && !m1) {"),
    ("ack: the wait-kind check dropped", C, "\t\t\tif (k == ARRAY_SIZE(m1_waits))\n\t\t\t\tgoto bad_ack;\n", ""),
    ("ack: the three-values count dropped", C, "\t\tif (n != ARRAY_SIZE(ack))\n\t\t\tgoto bad_ack;\n", ""),
    ("ack decoder: mark read without bit 7", CRUMBS, "        ack = bool(b[0x14] & F_ACK) if b[0x14] & 0x80 else None", "        ack = bool(b[0x14] & F_ACK)"),
    ("ack decoder: mark read at any sio_stop=1 stage", CRUMBS, "    if pmp and s in ACK_MARKED:", "    if s in SIO_STOP_STAGES:"),
    ("ack decoder: mark read without pmp_out=1's bit", CRUMBS, "    if pmp and s in ACK_MARKED:", "    if s in ACK_MARKED:"),
    ("ack decoder: the note only with a cluster bit set", CRUMBS, "            if ack is None:", "            if ack is None and b[6] & CLUSTER_WAITS:"),
    ("ack decoder: the note at 0x38 too", CRUMBS, "ACK_EARLY = (0x28, 0x31,", "ACK_EARLY = (0x28, 0x38, 0x31,"),
    ("ack decoder: the note without pmp_out=1's bit", CRUMBS, "        ack = None if pmp and s in ACK_EARLY else False", "        ack = None if s in ACK_EARLY else False"),
    ("ack decoder: no lost-byte check", CRUMBS, "    if not held and not q:", "    if False:"),
    ("ack decoder: other waits read as flags", CRUMBS, "    c = q & CLUSTER_WAITS", "    c = q"),
    ("ack decoder: DISPLAY's flag blamed on the run's words", CRUMBS, "    elif c & ACK_WROTE:", "    elif c:"),
    ("ack decoder: read 0 claimed with bit 5", CRUMBS, "    elif rest:\n        out.append(\"  so: no flag stayed", "    elif False:\n        out.append(\"  so: no flag stayed"),
    ("ack decoder: cluster bits still read as macOS's waits", CRUMBS, "if q >> i & 1 and not (ack and CLUSTER_WAITS >> i & 1)]", "if q >> i & 1]"),
    # fabric_up=1: the one write, where it goes, what's recorded
    ("fab: on by default", C, "static bool fabric_up;", "static bool fabric_up = true;"),
    ("fab: writes 3 as the whole word", C, "\twritel(m1_fab_up(v), p);", "\twritel(FAB_AWAKE, p);"),
    ("fab: two writes", C, "\twritel(m1_fab_up(v), p);", "\twritel(m1_fab_up(v), p);\n\twritel(m1_fab_up(v), p);"),
    ("fab: a write per poll", C, "\t\tv = readl(p);\n\t\tif (m1_fab_awake(v) ||", "\t\twritel(m1_fab_up(v), p);\n\t\tv = readl(p);\n\t\tif (m1_fab_awake(v) ||"),
    ("fab: said to settle whatever it read", C, "\treturn m1_fab_awake(v);\n}\n", "\treturn true;\n}\n"),
    ("fab: *pre is the last read, not the first", C, "\t*pre = v;\n\twritel(m1_fab_up(v), p);", "\twritel(m1_fab_up(v), p);\n\t*pre = readl(p);"),
    ("fab: polls for 100 ms", C, "\t\tif (m1_fab_awake(v) || m1_now() - t0 > m1_tps / 100)", "\t\tif (m1_fab_awake(v) || m1_now() - t0 > m1_tps / 10)"),
    ("fab: the nibble := 2", FAB, "#define FAB_AWAKE\t\t3U", "#define FAB_AWAKE\t\t2U"),
    ("fab: awake whatever bit 31 reads", FAB, "#define FAB_WORD\t\t0x80000fffU", "#define FAB_WORD\t\t0x00000fffU"),
    ("fab: the write keeps the old nibble", FAB, "#define FAB_UP(v)\t\t(((v) & ~FAB_ASKED) | FAB_AWAKE)", "#define FAB_UP(v)\t\t((v) | FAB_AWAKE)"),
    ("fab: the byte's two outcome bits swapped", FAB, "(set ? 1U << 4 : 0) | (end ? 1U << 5 : 0)", "(set ? 1U << 5 : 0) | (end ? 1U << 4 : 0)"),
    ("fab: bit 1 clear unless no core held", FAB, "((held & 0xfeU) != 0xfeU ? 1U << 1 : 0)", "((held & 0xfeU) ? 0 : 1U << 1)"),
    ("fab: bit 1 set when all held, as step 7's byte has it", FAB, "((held & 0xfeU) != 0xfeU ? 1U << 1 : 0)", "((held & 0xfeU) == 0xfeU ? 1U << 1 : 0)"),
    ("fab: reads another word before the write", C, "\tu32 v = readl(p);\n\tu64 t0;\n\n\t*pre = v;", "\tu32 v = readl(p + 0x800);\n\tu64 t0;\n\n\t*pre = v;"),
    ("fab: polls another word", C, "\t\tv = readl(p);\n\t\tif (m1_fab_awake(v) ||", "\t\tv = readl(p + 0x800);\n\t\tif (m1_fab_awake(v) ||"),
    ("fab: m1_fab() is DCS's word", C, "\treturn post_map[m1_waits[0].idx] - (m1_waits[0].addr - M1_FABRIC_PS);", "\treturn post_map[m1_waits[0].idx];"),
    ("fab: m1_fab() is 0x400 off", C, "\treturn post_map[m1_waits[0].idx] - (m1_waits[0].addr - M1_FABRIC_PS);", "\treturn post_map[m1_waits[0].idx] - (m1_waits[0].addr - M1_FABRIC_PS) + 0x400;"),
    ("fab: the load ignores its refusal", C, "\tif (fabric_up) {\n\t\tint ret = m1_fab_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n", "\tif (fabric_up)\n\t\tm1_fab_init();\n"),
    ("fab: a second write through another name", C, "\tif (fabric_up)\n\t\tfab_end = m1_fab_awake(readl(m1_fab()));\n", "\tif (fabric_up)\n\t\tfab_end = m1_fab_awake(readl(m1_fab()));\n\twritel_relaxed(0x333, post_map[49] - 0x800);\n"),
    ("fab: without the parameter", C, "\tif (fabric_up)\n\t\tfab_set = m1_fab_raise(m1_fab(), &fab_pre);", "\tfab_set = m1_fab_raise(m1_fab(), &fab_pre);"),
    ("fab: written after the table, not before", C, "\tif (fabric_up)\n\t\tfab_set = m1_fab_raise(m1_fab(), &fab_pre);\n\tfor (i = 0, k = 0;", "\tfor (i = 0, k = 0;"),
    ("fab: the last read before the power words", C, "\tif (ps_words)\n\t\tm1_pw_bits = m1_pw_write();\n",
     "\tif (fabric_up)\n\t\tfab_end = m1_fab_awake(readl(m1_fab()));\n\tif (ps_words)\n\t\tm1_pw_bits = m1_pw_write();\n"),
    ("fab: the stage before the commit not its own", C, "\t\tb[0] = fabric_up ? M1_FAB_STAGE_PRE : sio_stop ? 0x4e :", "\t\tb[0] = sio_stop ? 0x4e :"),
    ("fab: the last stage not its own", C, "\t\tb[0] = fabric_up ? M1_FAB_STAGE : M1_WD2_STAGE;", "\t\tb[0] = M1_WD2_STAGE;"),
    ("fab: the byte under the old stage's meaning", C, "\t\tb[3] = fabric_up ? m1_fab_byte(held, fab_pre, fab_set, fab_end, wd1) : hold ? held : down;", "\t\tb[3] = hold ? held : down;"),
    ("fab: loads whatever the word reads awake", C, "\tif (!m1_fab_awake(v)) {\n\t\tpr_err(", "\tif (0) {\n\t\tpr_err("),
    ("fab: loads without cluster_ack", C, "\tif (fabric_up && !cluster_ack) {", "\tif (0) {"),
    ("fab: the never-write check dropped", C, "\tif (e1_never_write(FAB_PS)) {", "\tif (0) {"),
    ("fab decoder: the byte read at every sio_stop=1 stage", CRUMBS, "            elif s in FAB_STAGES and fab_landed(b):\n                out += fab_lines(b[7])", "            elif s in SIO_STOP_STAGES and fab_landed(b):\n                out += fab_lines(b[7])"),
    ("fab decoder: 'after the write' and 'at the end' swapped", CRUMBS, "    was, took, kept = v >> 2 & 3, bool(v & 0x10), bool(v & 0x20)", "    was, took, kept = v >> 2 & 3, bool(v & 0x20), bool(v & 0x10)"),
    ("fab decoder: at 3 claimed without the last read", CRUMBS, "    elif took and kept:\n        out.append(\"  so: the fabric was at 3", "    elif took:\n        out.append(\"  so: the fabric was at 3"),
    ("fab decoder: the pending stage read as the clean one", CRUMBS, "WD2_PEND = (0x56, FAB_WD2[0])", "WD2_PEND = (0x56, FAB_WD2[1])"),
    ("fab decoder: no 'nothing records the commit' at 0x52", CRUMBS, "            if s in (0x59, 0x5a, 0x4e, FAB_PRE) and", "            if s in (0x59, 0x5a, 0x4e) and"),
    ("fab decoder: the cluster mark not read at its stages", CRUMBS, "ACK_MARKED = (0x4e, 0x56, 0x57) + FAB_STAGES", "ACK_MARKED = (0x4e, 0x56, 0x57)"),
    # wd1_mark=1: the three writes, their order and place, and when the marker counts as standing
    ("wd1: on by default", C, "static bool wd1_mark;", "static bool wd1_mark = true;"),
    ("wd1: the bite written to the count", C, "\twritel(WD1_MARK_BITE, wdt + WD1_BITE);", "\twritel(WD1_MARK_BITE, wdt + WD1_COUNT);"),
    ("wd1: armed before the count is in", C, "\twritel(WD1_MARK_COUNT, wdt + WD1_COUNT);\n\twritel(WD1_RESET_EN, wdt + WD1_CTRL);", "\twritel(WD1_RESET_EN, wdt + WD1_CTRL);\n\twritel(WD1_MARK_COUNT, wdt + WD1_COUNT);"),
    ("wd1: not stopped first when it reads armed", C, "\tif (was)\n\t\twritel(0, wdt + WD1_CTRL);\n", ""),
    ("wd1: the count written as 0", C, "\twritel(WD1_MARK_COUNT, wdt + WD1_COUNT);", "\twritel(0, wdt + WD1_COUNT);"),
    ("wd1: a bite of 151 s", WD1, "#define WD1_MARK_BITE\t\t(150U * WD1_HZ)", "#define WD1_MARK_BITE\t\t(151U * WD1_HZ)"),
    ("wd1: a count of 30 s", WD1, "#define WD1_MARK_COUNT\t\t(60U * WD1_HZ)", "#define WD1_MARK_COUNT\t\t(30U * WD1_HZ)"),
    ("wd1: armed counts whatever the bite", WD1, "((ctl & WD1_RESET_EN) && bite == WD1_MARK_BITE ? M1_WD1_ARMED : 0)", "((ctl & WD1_RESET_EN) ? M1_WD1_ARMED : 0)"),
    ("wd1: any count past 60 s counts", WD1, "(count > WD1_MARK_COUNT && count - WD1_MARK_COUNT <= WD1_MARK_SLACK ? M1_WD1_LEAD : 0)", "(count > WD1_MARK_COUNT ? M1_WD1_LEAD : 0)"),
    ("wd1: a count just under 60 s counts", WD1, "(count > WD1_MARK_COUNT && count - WD1_MARK_COUNT <= WD1_MARK_SLACK ? M1_WD1_LEAD : 0)", "(count - WD1_MARK_COUNT + 1 <= WD1_MARK_SLACK + 1 ? M1_WD1_LEAD : 0)"),
    ("wd1: a count that isn't running counts", WD1, "(count > WD1_MARK_COUNT && count - WD1_MARK_COUNT <= WD1_MARK_SLACK ? M1_WD1_LEAD : 0)", "(count >= WD1_MARK_COUNT && count - WD1_MARK_COUNT <= WD1_MARK_SLACK ? M1_WD1_LEAD : 0)"),
    ("wd1: 'armed' read from the count", C, "\tbool was = readl(wdt + WD1_CTRL);", "\tbool was = readl(wdt + WD1_COUNT);"),
    ("wd1: the load ignores its refusal", C, "\tif (wd1_mark) {\n\t\tint ret = m1_wd1_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n", "\tif (wd1_mark)\n\t\tm1_wd1_init();\n"),
    ("wd1: its count zeroed through another name after the release", C, "\t/* 10 */\n\tm1_arm_deep_sleep();", "\t/* 10 */\n\t{\n\t\tvoid __iomem *w = m1_wdt;\n\n\t\twritel(0, w + 0x10);\n\t}\n\tm1_arm_deep_sleep();"),
    ("wd1: set without the parameter", C, "\tif (wd1_mark) {\n\t\tif (m1_wd1_set(m1_wdt))", "\tif (1) {\n\t\tif (m1_wd1_set(m1_wdt))"),
    ("wd1: stopped again before the commit", C, "\tif (wd1_mark)\n\t\twd1 = m1_wd1_bits(", "\tif (wd1_mark)\n\t\twritel(0, m1_wdt + M1_WD1_CTRL);\n\tif (wd1_mark)\n\t\twd1 = m1_wd1_bits("),
    ("wd1: the look taken in step 4, not last", C, "\t\tif (m1_wd1_set(m1_wdt))\n\t\t\tflags |= BIT(6);\n",
     "\t\tif (m1_wd1_set(m1_wdt))\n\t\t\tflags |= BIT(6);\n\t\twd1 = m1_wd1_bits(readl(m1_wdt + WD1_CTRL), readl(m1_wdt + WD1_BITE), readl(m1_wdt + WD1_COUNT));\n"),
    ("wd1: the two bits never reach the byte", C, "m1_fab_byte(held, fab_pre, fab_set, fab_end, wd1)", "m1_fab_byte(held, fab_pre, fab_set, fab_end, 0)"),
    ("wd1: loads without fabric_up", C, "\tif (wd1_mark && !fabric_up) {", "\tif (0) {"),
    ("wd1: the never-write check dropped", C, "\tif (m1_never(WD1_WDT + WD1_COUNT, 1) || m1_never(WD1_WDT + WD1_BITE, 1) || m1_never(WD1_WDT + WD1_CTRL, 1)) {", "\tif (0) {"),
    ("wd1 decoder: stands with one bit", CRUMBS, "    if w == WD1_STANDS:", "    if w:"),
    ("wd1 decoder: the time read against a marker that didn't stand", CRUMBS, "        if s in FAB_STAGES and pmp and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:", "        if s in FAB_STAGES and pmp and fab_landed(b) and not b[7] & 2:"),
    ("wd1 decoder: the time read at any stage", CRUMBS, "        if s in FAB_STAGES and pmp and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:", "        if pmp and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:"),
    ("wd1 decoder: the time read against a byte that may be step 7's", CRUMBS, "        if s in FAB_STAGES and pmp and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:", "        if s in FAB_STAGES and pmp and b[7] & (WD1_STANDS | 2) == WD1_STANDS:"),
    ("wd1 decoder: the time read with bit 1 set", CRUMBS, "        if s in FAB_STAGES and pmp and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:", "        if s in FAB_STAGES and pmp and fab_landed(b) and b[7] & WD1_STANDS == WD1_STANDS:"),
    ("wd1 decoder: the time read without pmp_out=1's bit", CRUMBS, "        if s in FAB_STAGES and pmp and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:", "        if s in FAB_STAGES and fab_landed(b) and b[7] & (WD1_STANDS | 2) == WD1_STANDS:"),
    ("wd1 decoder: a finish later than 154 s isn't one", CRUMBS, "    if x >= WD1_BITE_S - 4:", "    if abs(x - WD1_BITE_S) <= 4:"),
    ("fab decoder: read past a set bit 1", CRUMBS, "    if v & 2:\n        # The bad case by design", "    if False:\n        # The bad case by design"),
    ("fab decoder: read without pmp_out=1's bit", CRUMBS, "            elif s in FAB_STAGES and not pmp:", "            elif False:"),
    ("wd1 decoder: 16 s either way is close enough", CRUMBS, "    if abs(x - unzeroed) <= 4:", "    if abs(x - unzeroed) <= 16:"),
    ("wd1 decoder: read at the stage before the commit", CRUMBS, "    if s == FAB_PRE:\n        return (f\"{head}not read.", "    if False:\n        return (f\"{head}not read."),
    ("wd1 decoder: read with an exception pending", CRUMBS, "    if s in WD2_PEND:\n        return (f\"{head}not read.", "    if False:\n        return (f\"{head}not read."),
    ("wd1 decoder: 120 s read without WD2's bite moved", CRUMBS, "        if not moved:\n            return (f\"{head}about {WD2_BITE_S} s, but wd2_bite=1's sample", "        if False:\n            return (f\"{head}about {WD2_BITE_S} s, but wd2_bite=1's sample"),
    ("wd1 decoder: 120 s read whatever PHASE read", CRUMBS, "        if code not in (1, 2):\n", "        if False:\n"),
    ("wd1 decoder: finished whatever WD2's sample", CRUMBS, "        if not live:\n            return (f\"{head}{x:.0f} s is WD1's", "        if False:\n            return (f\"{head}{x:.0f} s is WD1's"),
    ("wd1 decoder: finished with a WD2 that wasn't counting", CRUMBS, "    live = bool(v & 4 and v & 0x10 and v & 0x20 and v != 0xfe)", "    live = bool(v & 4 and v & 0x10 and v != 0xfe)"),
    ("wd1 decoder: finished with a WD2 no longer armed", CRUMBS, "    live = bool(v & 4 and v & 0x10 and v & 0x20 and v != 0xfe)", "    live = bool(v & 4 and v & 0x20 and v != 0xfe)"),
    ("wd1 decoder: the parked cores read as a sample", CRUMBS, "    live = bool(v & 4 and v & 0x10 and v & 0x20 and v != 0xfe)", "    live = bool(v & 4 and v & 0x10 and v & 0x20)"),
    ("fab decoder: the byte read without the outcomes' marker", CRUMBS, "            elif s in FAB_STAGES and fab_landed(b):\n", "            elif s in FAB_STAGES:\n"),
    ("fab decoder: any 0x6013 counts as the marker", CRUMBS, "    return b[0x13] == K_MARK or b[0x13] & W_MARK == W_MARK", "    return True"),
    ("wd1 decoder: a count of 30 s", CRUMBS, "WD1_BITE_S, WD1_COUNT_S, WD2_BITE_S = 150, 60, 120", "WD1_BITE_S, WD1_COUNT_S, WD2_BITE_S = 150, 30, 120"),
    ("wd1 decoder: 90 s without its other reading", CRUMBS, "                \" a later A sequence starting about 60 s after the commit and arming WD2 with its own 30 s, gives the same time\")",
     "                \"\")"),
    # probe=1: it must never replay, never let the Mac sleep, and leave ANS2 as it found it
    ("probe: on by default", C, "static bool probe;", "static bool probe = true;"),
    ("probe: the replay can still run", C, "\tif (probe) {\n\t\tpr_info(\"replay: probe=1 is loaded: no replay\\n\");\n\t\treturn false;\n\t}\n", ""),
    ("probe: the replay's guard after it marks itself run", C, "\t\treturn false;\n\t}\n\treplay_done = 1;\n", "\t}\n\treplay_done = 1;\n"),
    ("probe: prepare_late goes on to the replay", C, "\t\t\t\"it ran at an earlier suspend\");\n\t\treturn -EIO;\n\t}", "\t\t\t\"it ran at an earlier suspend\");\n\t}"),
    ("probe: the suspend isn't failed, so the Mac sleeps", C, "\t\t\t\"it ran at an earlier suspend\");\n\t\treturn -EIO;", "\t\t\t\"it ran at an earlier suspend\");\n\t\treturn 0;"),
    ("probe: the second half in a later suspend than the first", C, "\t\tbool same = n == 1 && READ_ONCE(prepare_calls) == 1;", "\t\tbool same = n == 1;"),
    ("probe: a second suspend calls the replay off, and replay_done reads 2", C, "\tif (n > 1 && !probe && !READ_ONCE(replay_done)) {", "\tif (n > 1 && !READ_ONCE(replay_done)) {"),
    ("probe: a line at pr_emerg, broadcast over the photo", C, "\tpr_info(\"probe: SUMMARY: bit 1 in the state B2 finds", "\tpr_emerg(\"probe: SUMMARY: bit 1 in the state B2 finds"),
    ("probe: prepare takes the replay's steps too", C, "\t\t\tm1_probe_prepare();\n\t\treturn 0;\n\t}", "\t\t\tm1_probe_prepare();\n\t}"),
    ("probe: it runs at every suspend", C, "\t\t} else if (!look && same) {\n\t\t\tm1_probe_late();", "\t\t} else if (!look) {\n\t\t\tm1_probe_late();"),
    ("probe: ANS2 isn't put back", C, "\t\twritel(ps & ~ASC_PS_NOT_WRITTEN, m1_sio + M1_PS_ANS2);\n", ""),
    ("probe: ANS2 raised though it read on", C, "\toff = !asc_ps_on(ps);", "\toff = true;"),
    ("probe: ps_pmp written", C, "\tps = readl(m1_ps + M1_PS_PMP_RO);\n\tm1_pr.pmp_on = asc_ps_on(ps);", "\twritel(0x0f00024f, m1_ps + M1_PS_PMP_RO);\n\tps = readl(m1_ps + M1_PS_PMP_RO);\n\tm1_pr.pmp_on = asc_ps_on(ps);"),
    ("probe: a word read with its domain off", C, "\tif (!asc_ps_on(ps)) {\n\t\tpr_info(\"probe: %s, %s: its power word reads", "\tif (0) {\n\t\tpr_info(\"probe: %s, %s: its power word reads"),
    ("probe: a word polled for a second", C, "n < ASC_TRIES && m1_now() - t0 < m1_tps / 10) {", "n < ASC_TRIES && m1_now() - t0 < m1_tps) {"),
    ("probe: SIO's RUN cleared after a failed idle pair", C, "\tif (!ret) {\n\t\tm1_probe_at(ASC_AT_SIO_STOP);", "\tif (1) {\n\t\tm1_probe_at(ASC_AT_SIO_STOP);"),
    ("probe: no stage at the start, so a Mac that goes down in it says nothing", C, "\tm1_kwrite(0x6004, &stage, 1);\n\tm1_probe_at(ASC_AT_PREPARE);\n", "\tm1_probe_at(ASC_AT_PREPARE);\n"),
    ("probe: loads without the latest recipe", C, "\tif (probe && (!wd1_mark || !pmp_stop)) {", "\tif (0) {"),
    ("probe: the load ignores its refusal", C, "\tif (probe) {\n\t\tint ret = m1_probe_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n", "\tif (probe)\n\t\tm1_probe_init();\n"),
    ("probe: the SUMMARY calls a read that wasn't made clear", C, "asc_final_str(asc_sio_final(b[0])), asc_final_str(asc_ans_final(b[2])), asc_final_str(asc_pmp_final(b[1]))",
     "m1_pr.sio_late ? \"SET\" : \"clear\", m1_pr.ans_late ? \"SET\" : \"clear\", m1_pr.pmp_stopped ? \"SET\" : \"clear\""),
    ("probe: the SUMMARY has ANS's and the PMP's the wrong way round", C, "asc_final_str(asc_ans_final(b[2])), asc_final_str(asc_pmp_final(b[1]))", "asc_final_str(asc_ans_final(b[1])), asc_final_str(asc_pmp_final(b[2]))"),
    ("probe: ANS's word read on a later look at ANS2 than the byte's", C, "\t\t\t\t       m1_ans_mbox, up, true);", "\t\t\t\t       m1_ans_mbox, readl(m1_sio + M1_PS_ANS2), true);"),
    ("probe: ANS2 written back as raised", C, "\t\twritel(ps & ~ASC_PS_NOT_WRITTEN, m1_sio + M1_PS_ANS2);\n", "\t\twritel(up & ~ASC_PS_NOT_WRITTEN, m1_sio + M1_PS_ANS2);\n"),
    ("probe: the PMP's RUN cleared though it never read idle", C, "\tif (!ret && (st & M1_ASC_IDLE)) {\n\t\tm1_probe_at(ASC_AT_PMP_STOP);", "\tif (!ret) {\n\t\tm1_probe_at(ASC_AT_PMP_STOP);"),
    ("probe: the PMP's idle pair sent to an RTKit that isn't running", C, "\tret = apple_rtkit_is_running(m1_pmp_rtk) ? apple_rtkit_idle(m1_pmp_rtk) : -ENODEV;\n\tm1_pr.pmp_idle_ok = !ret;", "\tret = apple_rtkit_idle(m1_pmp_rtk);\n\tm1_pr.pmp_idle_ok = !ret;"),
    ("probe: SIO's word read after noirq whatever SIO_CPU reads", C, "m1_sio_mbox, ps, true);\n\tm1_probe_at(ASC_AT_DONE);", "m1_sio_mbox, 0xf0, true);\n\tm1_probe_at(ASC_AT_DONE);"),
    ("probe: SIO's parents not raised before SIO_CPU", C, "\traised = m1_sio_raise();\n", "\traised = true;\n"),
    ("probe: a step taken before its mark", C, "\t\tm1_probe_at(ASC_AT_ANS_UP);\n\t\twritel(ASC_ANS2_UP, m1_sio + M1_PS_ANS2);", "\t\twritel(ASC_ANS2_UP, m1_sio + M1_PS_ANS2);\n\t\tm1_probe_at(ASC_AT_ANS_UP);"),
    ("probe: the PMP's last read counted without RUN cleared (header)", ASC, "\treturn !(b & ASC_PROBE_MARK) || !(b & (1U << 6)) || !(b & (1U << 5)) ? -1 : !!(b & (1U << 3));", "\treturn !(b & ASC_PROBE_MARK) || !(b & (1U << 6)) ? -1 : !!(b & (1U << 4));"),
    ("probe: ANS's last read counted though ANS2 never read on (header)", ASC, "\treturn !(b & ASC_PROBE_MARK) || !(b & ((1U << 1) | (1U << 3))) ? -1 : !!(b & (1U << 2));", "\treturn !(b & ASC_PROBE_MARK) ? -1 : !!(b & (1U << 2));"),
    ("probe: SIO's last read counted with SIO_CPU off (header)", ASC, "\treturn !(b & ASC_PROBE_MARK) || !(b & (1U << 6)) ? -1 : !!(b & (1U << 3));\n}\n\nstatic inline int asc_pmp_final", "\treturn !(b & ASC_PROBE_MARK) ? -1 : !!(b & (1U << 3));\n}\n\nstatic inline int asc_pmp_final"),
    ("probe: not read spelt clear", ASC, "\treturn final < 0 ? \"NOT READ\" : final ? \"SET\" : \"clear\";", "\treturn final > 0 ? \"SET\" : \"clear\";"),
    ("probe: a domain is on by its target", ASC, "\treturn ((ps >> 4) & 0xf) == 0xf;", "\treturn (ps & 0xf) == 0xf;"),
    ("probe: the stage is the observer's", ASC, "#define ASC_PROBE_STAGE\t\t0x0e", "#define ASC_PROBE_STAGE\t\t0x71"),
    ("probe: SIO's byte with two bits swapped", ASC, "(late ? 1U << 3 : 0) |\n\t\t    (idle_ok ? 1U << 4 : 0) | (stop_ok ? 1U << 5 : 0) | (on ? 1U << 6 : 0));\n}\n\nstatic inline u8 asc_pmp_byte",
     "(late ? 1U << 6 : 0) |\n\t\t    (idle_ok ? 1U << 4 : 0) | (stop_ok ? 1U << 5 : 0) | (on ? 1U << 3 : 0));\n}\n\nstatic inline u8 asc_pmp_byte"),
    ("probe decoder: its stage is the observer's", CRUMBS, "PROBE_STAGE = 0x0e", "PROBE_STAGE = 0x71"),
    ("probe decoder: SIO's last read counted with SIO_CPU off", CRUMBS, "        return bool(b & 8) if b & 0x40 else None\n    if who == \"PMP\":", "        return bool(b & 8)\n    if who == \"PMP\":"),
    ("probe decoder: the PMP's last read counted without RUN cleared", CRUMBS, "        return bool(b & 8) if b & 0x40 and b & 0x20 else None", "        return bool(b & 8) if b & 0x40 else None"),
    ("probe decoder: ANS's last read counted though ANS2 never read on", CRUMBS, "    return bool(b & 4) if b & 0x0a else None", "    return bool(b & 4)"),
    ("probe decoder: a byte that didn't land read all the same", CRUMBS, "    if not b & PROBE_MARK:\n        return None\n    if who == \"SIO\":", "    if who == \"SIO\":"),
    ("probe decoder: all three set with one unread", CRUMBS, "    elif unread:\n        out.append(\"  so: \" + (\"nothing was read", "    elif False:\n        out.append(\"  so: \" + (\"nothing was read"),
    ("probe decoder: a byte read without its mark", CRUMBS, "    if sio & PROBE_MARK:", "    if True:"),
    ("probe: a word polled past B2's count", C, "\twhile (poll && !(w & ASC_READY) && n < ASC_TRIES && ", "\twhile (poll && !(w & ASC_READY) && "),
    ("probe: a word never polled", C, "\twhile (poll && !(w & ASC_READY)", "\twhile (0 && !(w & ASC_READY)"),
    ("probe: ANS's word polled at load, its firmware in use", C, "m1_ans_mbox, ps, false);", "m1_ans_mbox, ps, true);"),
    ("probe: ANS2 written back with BUSY and the two flags", C, "\t\twritel(ps & ~ASC_PS_NOT_WRITTEN, m1_sio + M1_PS_ANS2);\n", "\t\twritel(ps, m1_sio + M1_PS_ANS2);\n"),
    ("probe: a write-back that didn't take passed as taken", C, "\t\tm1_pr.ans_back = !((up ^ ps) & ASC_PS_STATE);", "\t\tm1_pr.ans_back = true;"),
    ("probe: a step never marked", C, "\tm1_probe_at(ASC_AT_PMP_IDLE);\n", ""),
    ("probe: the steps marked at a PMU byte the module may not write", C, "\tm1_kwrite(ASC_PROBE_AT, &step, 1);", "\tm1_kwrite(0x6017, &step, 1);"),
    ("probe: the steps marked somewhere else", C, "\tm1_kwrite(ASC_PROBE_AT, &step, 1);", "\tm1_kwrite(0x6011, &step, 1);"),
    ("probe: ANS's late read by SIO_CPU's power word", C, "\tup = readl(m1_sio + M1_PS_ANS2);\n\tm1_pr.ans_was_on = !off && asc_ps_on(up);", "\tup = readl(m1_sio + M1_PS_SIO_CPU);\n\tm1_pr.ans_was_on = !off && asc_ps_on(up);"),
    ("probe: the PMP's prepare read made on ANS's page", C, "\"at prepare, its firmware running\", m1_pmp_mbox, readl(m1_ps + M1_PS_PMP_RO), true);", "\"at prepare, its firmware running\", m1_ans_mbox, readl(m1_ps + M1_PS_PMP_RO), true);"),
    ("probe: the PMP's page mapped at SIO's", C, "\tm1_pmp_mbox = ioremap_np(ASC_PMP_MBOX, PAGE_SIZE);", "\tm1_pmp_mbox = ioremap_np(ASC_SIO_MBOX, PAGE_SIZE);"),
    ("probe: the PMP's byte where ANS's goes", C, "\tret = m1_kwrite(0x6005, b, 3);", "\tb[0] ^= b[1] ^ b[2]; b[1] ^= b[2]; b[2] ^= b[1]; b[1] ^= b[2]; b[0] ^= b[1] ^ b[2];\n\tret = m1_kwrite(0x6005, b, 3);"),
    ("probe: ANS2's raise passed as made", C, "\tm1_pr.ans_raised = off && asc_ps_on(up);", "\tm1_pr.ans_raised = off;"),
    ("probe: ps_pmp passed as on", C, "\tm1_pr.pmp_on = asc_ps_on(ps);\n\tm1_pr.pmp_late", "\tm1_pr.pmp_on = true;\n\tm1_pr.pmp_late"),
    ("probe: ps_pmp's later reads not counted", C, "\t\tm1_pr.pmp_on = m1_pr.pmp_on && asc_ps_on(ps);\n\t\tm1_pr.pmp_stopped", "\t\tm1_pr.pmp_stopped"),
    ("probe: SIO_CPU passed as on", C, "\tm1_pr.sio_on = asc_ps_on(ps);", "\tm1_pr.sio_on = true;"),
    ("probe: the load's never-write check of ANS2 dropped", C, "\tif (m1_never(M1_PS_SIO_PAGE + M1_PS_ANS2, 1)) {", "\tif (0) {"),
    ("probe: ANS2's and ps_pmp's addresses unpinned", C, "static_assert(M1_PS_SIO_PAGE + M1_PS_ANS2 == 0x28e5803b8ULL && M1_PMGR_PS + M1_PS_PMP_RO == 0x28e0802d8ULL);\n", ""),
    ("probe: the load's check of the table's entry dropped", C, "\tif (w->addr != M1_PS_SIO_PAGE + M1_PS_SIO_CPU || w->width != 4 || (w->val & 0xf) != 0xf) {", "\tif (0) {"),
    ("probe: ANS's load read not kept", C, "\tm1_pr.ans_load = m1_probe_word(\"ANS\", ", "\tm1_probe_word(\"ANS\", "),
    ("probe: the PMP's idle poll without its last read", C, "\tif (!ret && !(st & M1_ASC_IDLE))\n\t\tst = readl(m1_pmp_asc + M1_ASC_CPU_STATUS);\n\tpr_info(\"probe: PMP: its idle pair", "\tpr_info(\"probe: PMP: its idle pair"),
    ("probe: more tries than B2's", ASC, "#define ASC_TRIES\t\t255", "#define ASC_TRIES\t\t1000"),
    ("probe: BUSY written back", ASC, "#define ASC_PS_NOT_WRITTEN\t((1U << 11) | (1U << 9) | (1U << 8))", "#define ASC_PS_NOT_WRITTEN\t((1U << 9) | (1U << 8))"),
    ("probe: ANS's byte with two bits swapped", ASC, "(load ? 1U << 4 : 0) | (load_read ? 1U << 5 : 0) | (back ? 1U << 6 : 0));", "(load ? 1U << 6 : 0) | (load_read ? 1U << 5 : 0) | (back ? 1U << 4 : 0));"),
    ("probe decoder: a step off by one", CRUMBS, "    step = PROBE_AT[at] if at <= PROBE_DONE else", "    step = PROBE_AT[at - 1] if at <= PROBE_DONE else"),
    ("probe decoder: an unfinished probe not said", CRUMBS, "    if at == PROBE_DONE:\n        return f\"0x6010 = 0x{at:02x}: every step was taken, and the write", "    if True:\n        return f\"0x6010 = 0x{at:02x}: every step was taken, and the write"),
    ("probe decoder: ANS's load read counted though it wasn't made", CRUMBS, "{bit(ans, 4, bool(ans & 0x20))}", "{bit(ans, 4, True)}"),
    ("probe decoder: a write-back that didn't take not said", CRUMBS, "        if not ans & 0x40:\n", "        if False:\n"),
    ("probe decoder: the step read from another byte", CRUMBS, "] + probe_lines(b[5], b[6], b[7], b[0x10])", "] + probe_lines(b[5], b[6], b[7], b[0x11])"),
    ("probe launcher: says unloaded of a module that didn't unload", PROBE, "  rmmod e1_hook 2> /dev/null && die \"e1_hook didn't take probe=1, and was unloaded", "  rmmod e1_hook 2> /dev/null; die \"e1_hook didn't take probe=1, and was unloaded"),
    ("probe launcher: the replay not called off before the unload", PROBE, "  if printf 1 > \"$MODDIR/parameters/cancel\" 2> /dev/null; then", "  if true; then"),
    ("probe launcher: a suspend asked for though another has run the probe", PROBE, "for try in 1 2 3; do\n  [[ $(get \"$MODDIR/parameters/$count\") == 0 ]] || break\n", "for try in 1 2 3; do\n"),
    ("probe launcher: klog's programs not in memory before the suspend", PROBE, "dmesg 2>&1 | grep -F -- \"e1_hook: probe:\" > /dev/null; sleep 0\n", ""),
    # look=1 (e1-hook/probe --look): it must stop nothing and write no register, and read what PLAN.md lists and no more
    ("look: on by default", C, "static bool look;", "static bool look = true;"),
    ("look: the probe's stops taken all the same", C, "\t\tif (n == 1 && !look)\n", "\t\tif (n == 1)\n"),
    ("look: the probe's second half run in its place", C, "\t\t} else if (!look && same) {\n", "\t\t} else if (same) {\n"),
    ("look: the look after noirq never run", C, "\t\tif (look && n == 1) {\n\t\t\tm1_look(\"after noirq\", 0);", "\t\tif (0) {\n\t\t\tm1_look(\"after noirq\", 0);"),
    ("look: the look at every failed suspend", C, "\t\tif (look && n == 1) {\n", "\t\tif (look) {\n"),
    ("look: the load's marks not told from the suspend's", C, "\tm1_look(\"awake, at load\", LOOK_LOAD);", "\tm1_look(\"awake, at load\", 0);"),
    ("look: a mark without its pass", C, "\tm1_look_at(pass | LOOK_AT_CLKGEN);", "\tm1_look_at(LOOK_AT_CLKGEN);"),
    ("look: the words the engine only writes read first", C, "\tm1_look_at(pass | LOOK_AT_AON);\n\tfor (i = 0; i < ARRAY_SIZE(aon); i++)\n\t\tv[i] = readl(m1_pos + aon[i]);", "\tm1_look_at(pass | LOOK_AT_AON);\n\treadl(m1_pos + aonw[0]);\n\tfor (i = 0; i < ARRAY_SIZE(aon); i++)\n\t\tv[i] = readl(m1_pos + aon[i]);"),
    ("look: loads without probe=1, where nothing keeps the replay away", C, "\tif (look && !probe) {", "\tif (0) {"),
    ("look: a register written", C, "\tm1_look_at(pass | LOOK_AT_DONE);\n", "\twritel(0, m1_look_clk + 4);\n\tm1_look_at(pass | LOOK_AT_DONE);\n"),
    ("look: a group read before its mark", C, "\tm1_look_at(pass | LOOK_AT_GATES);\n\tfor (i = 0; i < ARRAY_SIZE(ga); i++)\n\t\tv[i] = readl(m1_look_ga + ga[i]);\n",
     "\tfor (i = 0; i < ARRAY_SIZE(ga); i++)\n\t\tv[i] = readl(m1_look_ga + ga[i]);\n\tm1_look_at(pass | LOOK_AT_GATES);\n"),
    ("look: no stage, so a faulting read says nothing", C, "\tm1_kwrite(0x6004, &stage, 1);\n\t/* Each read into a variable first", "\t/* Each read into a variable first"),
    ("look: the second gate page read through the first", C, "\t\tv[3 + i] = readl(m1_look_gb + gb[i]);\n\tpr_info(\"look: %s: gates", "\t\tv[3 + i] = readl(m1_look_ga + gb[i]);\n\tpr_info(\"look: %s: gates"),
    ("gate: its log of the gates reads the second page through the first", C, "\t\tv[3 + i] = readl(m1_look_gb + gb[i]);\n\tpr_info(\"look: gate: %s: gates", "\t\tv[3 + i] = readl(m1_look_ga + gb[i]);\n\tpr_info(\"look: gate: %s: gates"),
    ("look: a clock generator's second word read as its first", C, "\t\tv[1] = readl(m1_look_clk + i * LOOK_CLKGEN_STEP + 4);", "\t\tv[1] = readl(m1_look_clk + i * LOOK_CLKGEN_STEP);"),
    ("look: ANS2's word logged where a first-page word goes", C, "when, v[7], v[0], v[1], v[2], v[3], v[4], v[5], v[6]);", "when, v[0], v[7], v[1], v[2], v[3], v[4], v[5], v[6]);"),
    ("look: the load's marks left in the PMU", C, "\tm1_look(\"awake, at load\", LOOK_LOAD);\n\tm1_look_at(LOOK_AT_NONE);\n\tm1_kwrite(0x6004, &zero, 1);\n", "\tm1_look(\"awake, at load\", LOOK_LOAD);\n"),
    ("look: the gate page mapped at the clock generators'", C, "\tm1_look_ga = ioremap_np(LOOK_GATE_A, PAGE_SIZE);", "\tm1_look_ga = ioremap_np(LOOK_CLKGEN, PAGE_SIZE);"),
    ("look: the engine's page read unmapped at the load", C, "\tif (!m1_pos)\n\t\tm1_pos = ioremap_np(M1_POS_PAGE, PAGE_SIZE);\n\tif (!m1_look_clk || !m1_look_ga || !m1_look_gb || !m1_pos) {", "\tif (!m1_look_clk || !m1_look_ga || !m1_look_gb) {"),
    ("look: the engine's page not checked", C, "|| !m1_look_gb || !m1_pos) {", "|| !m1_look_gb) {"),
    ("look: SIO's page and the p-state page not checked", C, "\tif (!m1_sio || !m1_ps || !post_map[m1_waits[0].idx]) {", "\tif (0) {"),
    ("look: the load's look before the watchdog's start-up, which maps the engine's page", C, "\t/* Last: it reads through pages the initializers above map, the engine's among them (m1_wd2_init()'s here). */\n\tif (look) {\n\t\tint ret = m1_look_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n\treturn 0;\n}", "\treturn look ? m1_look_init() : 0;\n\treturn 0;\n}"),
    ("look: the load ignores a failed mapping", C, "\tif (look) {\n\t\tint ret = m1_look_init();\n\n\t\tif (ret)\n\t\t\treturn ret;\n\t}\n", "\tif (look)\n\t\tm1_look_init();\n"),
    ("look: one clock generator too many", LOOK, "#define LOOK_CLKGEN_N\t\t15", "#define LOOK_CLKGEN_N\t\t16"),
    ("look: a power word past AES's", LOOK, "#define LOOK_SIO_LAST\t\t0x2d0", "#define LOOK_SIO_LAST\t\t0x2d8"),
    ("look: a gate word's offset", LOOK, "#define LOOK_GATE_B_OFFS\t{ 0x310, 0x338, 0x34c }", "#define LOOK_GATE_B_OFFS\t{ 0x310, 0x338, 0x348 }"),
    ("look: an AON word that isn't on the list", LOOK, "#define LOOK_AON_OFFS\t\t{ 0x038, 0x03c,", "#define LOOK_AON_OFFS\t\t{ 0x030, 0x03c,"),
    ("look: the load's bit among the groups' numbers", LOOK, "#define LOOK_LOAD\t\t0x10", "#define LOOK_LOAD\t\t0x04"),
    ("look: the probe's stage", LOOK, "#define LOOK_STAGE\t\t0x0f", "#define LOOK_STAGE\t\t0x0e"),
    ("look decoder: an unfinished look passed as finished", CRUMBS, "    if n == LOOK_DONE and not load:\n", "    if not load:\n"),
    ("look decoder: a group off by one", CRUMBS, "    group = LOOK_AT[n] if n <= LOOK_DONE else", "    group = LOOK_AT[n - 1] if n <= LOOK_DONE else"),
    ("look decoder: the load's look taken for the suspend's", CRUMBS, "    load, n = bool(at & LOOK_LOAD), at & ~LOOK_LOAD", "    load, n = False, at & ~LOOK_LOAD"),
    ("look decoder: its stage read as the probe's", CRUMBS, "LOOK_STAGE = 0x0f", "LOOK_STAGE = 0x1f"),
    ("look launcher: goes by prepare, where the look does nothing", PROBE, "((look)) && count=prepare_late_calls\n", ""),
    ("look launcher: the load's look not saved before the suspend", PROBE, "  if keep \"${out%.txt}-load.txt\"; then log \"(saved: the look at the load", "  if true; then log \"(saved: the look at the load"),
    ("look launcher: the load's look saved under the whole record's name", PROBE, "  if keep \"${out%.txt}-load.txt\"; then", "  if keep \"$out\"; then"),
    ("probe launcher: the growing log kept where every save archives it", PROBE, "live=$RUNDIR/asc-probe-$stamp.txt", "live=$LOGDIR/asc-probe-$stamp-live.txt"),
    ("probe launcher: a file saved a second time over itself", PROBE, "  [[ ! -e $1 ]] || return 1\n", ""),
    ("probe launcher: the record copied and not saved", PROBE, "  \"$SAVE\" save \"$1\" > /dev/null 2>&1\n}", "  :\n}"),
    ("look launcher: the load's look not printed before the suspend", PROBE, "  log \"kernel log, the look awake, at the load:\"\n  klog \"e1_hook: look: awake, at load\"\n", ""),
    ("look launcher: goes on with pmp_sleep_release loaded", PROBE, "  [[ ! -d $PMPMOD ]] || die", "  [[ ! -d $PMPMOD ]] || echo"),
    ("look launcher: a build without look not refused before the load", PROBE, "    die \"$ko is an older build, without look: make -C $here\"", "    echo \"$ko is an older build, without look: make -C $here\""),
    ("look launcher: a look at another suspend passed as the plain case", PROBE, "  [[ $plc == 1 ]] || log \"THE LOOK AFTER NOIRQ RAN AT ANOTHER SUSPEND", "  [[ $plc != 0 ]] || log \"THE LOOK AFTER NOIRQ RAN AT ANOTHER SUSPEND"),
    ("look launcher: no look=1 in the load", PROBE, "  PARAMS+=(look=1)\n", ""),
    ("look launcher: the PMP's requests withdrawn for a look", PROBE, "if ((!look)); then\n  \"$PMP\" release", "if true; then\n  \"$PMP\" release"),
    ("look launcher: goes on with a module that would stop the firmware", PROBE, "if ((look)) && [[ $(get \"$MODDIR/parameters/look\") != Y ]]; then", "if false; then"),
    ("look launcher: says unloaded of a module that didn't unload", PROBE, "  rmmod e1_hook 2> /dev/null && die \"e1_hook didn't take look=1, and was unloaded", "  rmmod e1_hook 2> /dev/null; die \"e1_hook didn't take look=1, and was unloaded"),
    ("look launcher: the probe's lines printed for a look", PROBE, "if ((look)); then\n  [[ $plc == 1 ]] || log", "if false; then\n  [[ $plc == 1 ]] || log"),
    # gate=1 (e1-hook/probe --gate): six power words out and back, never more, and always back
    ("gate: on by default", C, "static bool gate;", "static bool gate = true;"),
    ("gate: the test run by a plain look", C, "\t\t\tif (gate)\n\t\t\t\tm1_gate_test();", "\t\t\tm1_gate_test();"),
    ("gate: loads without look=1", C, "\tif (gate && !look) {", "\tif (0) {"),
    ("gate: a UART written a value the runs don't write", GATE, "#define GATE_UART_VAL\t\t0x00000200U", "#define GATE_UART_VAL\t\t0x00000000U"),
    ("gate: the lane written another value than macOS's", GATE, "#define GATE_LANE_VAL\t\t0x1000030fU", "#define GATE_LANE_VAL\t\t0x0000030fU"),
    ("gate: another word taken for the lane", GATE, "#define GATE_LANE\t\t0x160", "#define GATE_LANE\t\t0x158"),
    ("gate: a sixth UART", GATE, "#define GATE_UARTS\t\t{ 0x250, 0x258, 0x260, 0x268, 0x270 }", "#define GATE_UARTS\t\t{ 0x248, 0x258, 0x260, 0x268, 0x270 }"),
    ("gate: another gate word polled", GATE, "#define GATE_SIO\t\t0x050", "#define GATE_SIO\t\t0x064"),
    ("gate: took by the target alone", GATE, "#define GATE_PS_SET\t\t((1U << 28) | (0xfU << 24) | 0xfU)", "#define GATE_PS_SET\t\t0xfU"),
    ("gate: back as found without the actual state", GATE, "\treturn !((v ^ found) & (GATE_PS_SET | GATE_PS_ACTUAL));", "\treturn !((v ^ found) & GATE_PS_SET);"),
    ("gate: the byte with two bits swapped", GATE, "(closed1 ? 1U << 1 : 0) | (lane_took ? 1U << 2 : 0)", "(closed1 ? 1U << 2 : 0) | (lane_took ? 1U << 1 : 0)"),
    ("gate: the lane not written back", C, "\tv = m1_gate_write(m1_sio + GATE_LANE, lane & ~ASC_PS_NOT_WRITTEN, gate_back, lane, &first);\n\tlane_back = gate_back(v, lane);", "\tlane_back = gate_back(v, lane);"),
    ("gate: the UARTs not written back", C, "\t\tv = m1_gate_write(m1_sio + uart[i], was[i] & ~ASC_PS_NOT_WRITTEN, gate_back, was[i], &first);\n", "\t\tv = was[i];\n"),
    ("gate: the lane written back with BUSY and its flags", C, "m1_gate_write(m1_sio + GATE_LANE, lane & ~ASC_PS_NOT_WRITTEN, gate_back, lane, &first)", "m1_gate_write(m1_sio + GATE_LANE, lane, gate_back, lane, &first)"),
    ("gate: the UARTs written back as the lane was found", C, "m1_gate_write(m1_sio + uart[i], was[i] & ~ASC_PS_NOT_WRITTEN, gate_back, was[i], &first)", "m1_gate_write(m1_sio + uart[i], lane & ~ASC_PS_NOT_WRITTEN, gate_back, was[i], &first)"),
    ("gate: a word written twice", C, "\twritel(val, p);\n\tdsb(sy);\n\t*first = v = readl(p);", "\twritel(val, p);\n\twritel(val, p);\n\tdsb(sy);\n\t*first = v = readl(p);"),
    ("gate: a write polled for a second", C, "\twhile (!done(v, want) && m1_now() - t0 < m1_tps / 100) {", "\twhile (!done(v, want) && m1_now() - t0 < m1_tps) {"),
    ("gate: the gate polled for a second", C, "& GATE_CLOSED) && m1_now() - t0 < m1_tps / 10)", "& GATE_CLOSED) && m1_now() - t0 < m1_tps)"),
    ("gate: the lane before the UARTs", C, "\tm1_look_at(GATE_PASS | GATE_AT_UARTS);\n", "\tm1_gate_write(m1_sio + GATE_LANE, GATE_LANE_VAL, gate_took, GATE_LANE_VAL, &first);\n\tm1_look_at(GATE_PASS | GATE_AT_UARTS);\n"),
    ("gate: a step taken before its mark", C, "\tm1_look_at(GATE_PASS | GATE_AT_LANE);\n\tv = m1_gate_write(m1_sio + GATE_LANE, GATE_LANE_VAL, gate_took, GATE_LANE_VAL, &first);", "\tv = m1_gate_write(m1_sio + GATE_LANE, GATE_LANE_VAL, gate_took, GATE_LANE_VAL, &first);\n\tm1_look_at(GATE_PASS | GATE_AT_LANE);"),
    ("gate: its marks taken for the look's", C, "\tm1_look_at(GATE_PASS | GATE_AT_DONE);", "\tm1_look_at(GATE_AT_DONE);"),
    ("gate: the first poll's outcome reported as the second's", C, "closed1 ? \"SET\" : \"clear\", lane_took ? \"took\" : \"did NOT take\", closed2 ? \"SET\" : \"clear\"", "closed2 ? \"SET\" : \"clear\", lane_took ? \"took\" : \"did NOT take\", closed1 ? \"SET\" : \"clear\""),
    ("gate: the byte not written", C, "\ti = m1_kwrite(0x6005, &res, 1);", "\ti = 0;"),
    ("gate: ps_pmp's page written", C, "m1_gate_write(m1_sio + uart[i], GATE_UART_VAL, gate_actual, 0, &first)", "m1_gate_write(m1_ps + uart[i], GATE_UART_VAL, gate_actual, 0, &first)"),
    ("gate decoder: an unfinished test passed as finished", CRUMBS, "    if n == GATE_DONE:\n", "    if True:\n"),
    ("gate decoder: a byte that didn't land read all the same", CRUMBS, "    if not v & GATE_MARK:\n        return out + ", "    if False:\n        return out + "),
    ("gate decoder: the gate closing by itself taken for needing the lane", CRUMBS, "    elif c1:\n", "    elif False:\n"),
    ("gate decoder: a lane that didn't take passed over", CRUMBS, "    elif took and c2:\n", "    elif c2:\n"),
    ("gate decoder: its record read as a plain look's", CRUMBS, "] + (gate_lines(b[0x10], b[5]) if b[0x10] & GATE_PASS else look_lines(b[0x10]))", "] + look_lines(b[0x10])"),
    ("gate launcher: no gate=1 in the load", PROBE, "  PARAMS+=(look=1 gate=1)\n", "  PARAMS+=(look=1)\n"),
    ("gate launcher: goes on with a module that won't run the test", PROBE, "if ((gate)) && [[ $(get \"$MODDIR/parameters/gate\") != Y ]]; then", "if false; then"),
    ("gate launcher: a build without gate not refused before the load", PROBE, "    die \"$ko is an older build, without gate: make -C $here\"", "    echo \"$ko is an older build, without gate: make -C $here\""),
    ("gate launcher: the look's lines in the photo block where the test's go", PROBE, "  if ((gate)); then\n    log \"kernel log, the gate test", "  if false; then\n    log \"kernel log, the gate test"),
    ("probe launcher: the PMP idled with its display report requests asserted, as no run has", PROBE, "\"$PMP\" release 0xc 0xd > /dev/null 2>&1 || die ", ": || die "),
    ("probe launcher: goes on without the release module", PROBE, "\"$PMP\" release 0xc 0xd > /dev/null 2>&1 || die ", "\"$PMP\" release 0xc 0xd > /dev/null 2>&1 || : "),
    ("probe launcher: no probe=1 in the parameters", PROBE, " fabric_up=1 wd1_mark=1 probe=1)", " fabric_up=1 wd1_mark=1)"),
    ("probe launcher: goes on with a module that didn't take probe=1", PROBE, "  die \"e1_hook didn't take probe=1 and DID NOT UNLOAD", "  : \"e1_hook didn't take probe=1 and DID NOT UNLOAD"),
    ("probe launcher: the check for probe=Y reads another parameter", PROBE, "if [[ $(get \"$MODDIR/parameters/probe\") != Y ]]; then", "if [[ $(get \"$MODDIR/parameters/replay_done\") != 0 ]]; then"),
    ("probe launcher: outside a safe boot", PROBE, "[[ -f $SAFE_FLAG ]] || die", "[[ -f $SAFE_FLAG ]] || echo"),
    ("probe launcher: with breadcrumbs left", PROBE, "grep -q \"^stage 0x6004 = 0x00:\" || die", "grep -q \"^stage 0x6004 = 0x00:\" || echo"),
    ("probe launcher: a stale module", PROBE, "  [[ $ko -nt $here/$src ]] || die", "  [[ $ko -nt $here/$src ]] || echo"),
    ("probe launcher: a second suspend after the module's prepare ran", PROBE, "  rc=$?\n  [[ $(get \"$MODDIR/parameters/$count\") == 0 ]] || break\n", "  rc=$?\n"),
    ("probe launcher: one try only", PROBE, "for try in 1 2 3; do", "for try in 1; do"),
    ("probe launcher: a photo of nothing", PROBE, "if [[ $began == 0 ]]; then", "if false; then"),
    ("probe launcher: the module left loaded with nothing probed", PROBE, "  if rmmod e1_hook; then", "  if true; then"),
    ("probe launcher: a part-run passed off as whole", PROBE, "[[ $pc == 1 && $plc == 1 ]] || log", "[[ $pc == 1 ]] || log"),
]


def one(mutant, base):
    name, rel, old, new = mutant
    with tempfile.TemporaryDirectory(dir=base) as copy:
        shutil.copytree(E1, os.path.join(copy, "e1-hook"), ignore=shutil.ignore_patterns("*.o", "*.ko", "*.mod*", ".*", "Module.symvers", "modules.order"))
        os.makedirs(os.path.join(copy, "tests"))
        test = TESTS.get(rel, "m1_crumbs_test.py")
        shutil.copy(os.path.join(HERE, test), os.path.join(copy, "tests"))
        path = os.path.join(copy, "e1-hook", rel)
        text = open(path).read()
        if text.count(old) != 1:
            return name, f"STALE (its text is in {rel} {text.count(old)} times): fix the list"
        with open(path, "w") as f:
            f.write(text.replace(old, new))
        r = subprocess.run([sys.executable, "-I", "-B", os.path.join(copy, "tests", test)], capture_output=True, text=True)
    fails = sum(line.startswith("FAIL ") for line in r.stdout.splitlines())
    if fails:
        return name, f"caught ({fails} failing)"
    if r.returncode != 0 or "all ok" not in r.stdout:
        return name, "caught (the tests couldn't build or finish)"
    return name, "SURVIVED: the tests have a hole here"


def main():
    words = sys.argv[1:]
    chosen = [m for m in MUTANTS if not words or any(w in m[0] for w in words)]
    if len({m[0] for m in MUTANTS}) != len(MUTANTS):
        print("two mutants share a name", file=sys.stderr)
        return 2
    bad = 0
    with tempfile.TemporaryDirectory() as base, concurrent.futures.ThreadPoolExecutor(6) as pool:
        for name, verdict in pool.map(lambda m: one(m, base), chosen):
            print(f"{verdict}: {name}", flush=True)
            bad += not verdict.startswith("caught")
    print(f"{len(chosen)} mutants, {bad} not caught")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
