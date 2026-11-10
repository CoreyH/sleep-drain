#!/usr/bin/python3 -I
"""tests/mutants.py: break the one-way runs' code one way at a time and see that tests/m1_crumbs_test.py notices.

  python3 -I -B tests/mutants.py            every mutant (a few minutes: six at a time)
  python3 -I -B tests/mutants.py wd1 fab    only those whose name has one of these words

Each mutant is one change to e1-hook/e1_hook.c, one of its headers or e1-hook/m1-crumbs: a wrong value, a write
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
C, CRUMBS, FAB, WD1 = "e1_hook.c", "m1-crumbs", "fabric_up.h", "wd1_mark.h"

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
    ("wd1 decoder: finished without WD2 seen armed", CRUMBS, "        if not seen:\n            return (f\"{head}{x:.0f} s is WD1's", "        if False:\n            return (f\"{head}{x:.0f} s is WD1's"),
    ("fab decoder: the byte read without the outcomes' marker", CRUMBS, "            elif s in FAB_STAGES and fab_landed(b):\n", "            elif s in FAB_STAGES:\n"),
    ("fab decoder: any 0x6013 counts as the marker", CRUMBS, "    return b[0x13] == K_MARK or b[0x13] & W_MARK == W_MARK", "    return True"),
    ("wd1 decoder: a count of 30 s", CRUMBS, "WD1_BITE_S, WD1_COUNT_S, WD2_BITE_S = 150, 60, 120", "WD1_BITE_S, WD1_COUNT_S, WD2_BITE_S = 150, 30, 120"),
    ("wd1 decoder: 90 s without its other reading", CRUMBS, "                \" a later A sequence starting about 60 s after the commit and arming WD2 with its own 30 s, gives the same time\")",
     "                \"\")"),
]


def one(mutant, base):
    name, rel, old, new = mutant
    with tempfile.TemporaryDirectory(dir=base) as copy:
        shutil.copytree(E1, os.path.join(copy, "e1-hook"), ignore=shutil.ignore_patterns("*.o", "*.ko", "*.mod*", ".*", "Module.symvers", "modules.order"))
        os.makedirs(os.path.join(copy, "tests"))
        shutil.copy(os.path.join(HERE, "m1_crumbs_test.py"), os.path.join(copy, "tests"))
        path = os.path.join(copy, "e1-hook", rel)
        text = open(path).read()
        if text.count(old) != 1:
            return name, f"STALE (its text is in {rel} {text.count(old)} times): fix the list"
        with open(path, "w") as f:
            f.write(text.replace(old, new))
        r = subprocess.run([sys.executable, "-I", "-B", os.path.join(copy, "tests", "m1_crumbs_test.py")], capture_output=True, text=True)
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
