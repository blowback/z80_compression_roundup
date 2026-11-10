"""Tests for linesim (the C emulator driver) and linesim.py.

Most use tiny hand-assembled Z80 programs instead of the real decoders, so
they only need the linesim binary (python3 harness/build_tools.py z80).
The banked-helper tests also need sjasmplus, and the torture tests need the
assembled banked decoders and the compressors; each is skipped when its
tools are not built.
"""

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bench  # noqa: E402
import candidates  # noqa: E402
import decoders  # noqa: E402
import linesim  # noqa: E402

LINESIM = linesim.LINESIM
T_BYTE = 8_000_000 * 10 / 19200          # 4166.67 T-states at the defaults
RX = 150

# Stream format for the test copiers: 2-byte little-endian count, then data.
# HL = source, DE = destination.
#   ld c,(hl) / inc hl / ld b,(hl) / inc hl / ldir / ret
FAST_COPIER = bytes([0x4E, 0x23, 0x46, 0x23, 0xED, 0xB0, 0xC9])

# Same stream, but about 6,700 T-states per byte (two 256-step DJNZ loops),
# slower than the 4,167 T-states a byte takes on the line.
SLOW_COPIER = bytes([
    0x4E, 0x23, 0x46, 0x23,       # BC = count
    0x7E, 0x23, 0x12, 0x13,       # loop: ld a,(hl) / inc hl / ld (de),a / inc de
    0xC5, 0x06, 0x00,             # push bc / ld b,0
    0x10, 0xFE, 0x10, 0xFE,       # djnz $ / djnz $
    0xC1, 0x0B, 0x78, 0xB1,       # pop bc / dec bc / ld a,b / or c
    0x20, 0xEF,                   # jr nz,loop
    0xC9])                        # ret

SPIN = bytes([0x18, 0xFE])                    # jr $
JUMP_OUT = bytes([0xC3, 0x00, 0x00])          # jp 0x0000

# Paging probe. Window 1 -> page 0x25, write 0xAA at 0x4000; window 0 ->
# page 0x26, write 0x55 at 0x0000; restore both. Then window 2 -> page 0x25
# and copy 0x8000 to output byte 0 (expect 0xAA); restore window 2 and copy
# 0x4000 (page 0x21, untouched) to output byte 1 (expect 0x00).
PAGING_PROBE = bytes([
    0x3E, 0x25, 0xD3, 0x71,       # ld a,0x25 / out (0x71),a
    0x3E, 0xAA, 0x32, 0x00, 0x40,  # ld a,0xAA / ld (0x4000),a
    0x3E, 0x26, 0xD3, 0x70,       # ld a,0x26 / out (0x70),a
    0x3E, 0x55, 0x32, 0x00, 0x00,  # ld a,0x55 / ld (0x0000),a
    0x3E, 0x20, 0xD3, 0x70,       # ld a,0x20 / out (0x70),a
    0x3E, 0x21, 0xD3, 0x71,       # ld a,0x21 / out (0x71),a
    0x3E, 0x25, 0xD3, 0x72,       # ld a,0x25 / out (0x72),a
    0x3A, 0x00, 0x80, 0x32, 0x00, 0x01,  # ld a,(0x8000) / ld (0x0100),a
    0x3E, 0x22, 0xD3, 0x72,       # ld a,0x22 / out (0x72),a
    0x3A, 0x00, 0x40, 0x32, 0x01, 0x01,  # ld a,(0x4000) / ld (0x0101),a
    0xC9])

# Ring reader: ring of 16 bytes at 0x8000, write pointer at 0x9000, read
# pointer at 0x9002. IX = destination, BC = byte count. Spins until the
# write pointer moves, copies one byte, advances and stores the read
# pointer, then (slow variant) burns about 6,700 T-states.
RING_ADDR, RING_SIZE, WP, RP = 0x8000, 16, 0x9000, 0x9002


def ring_reader(slow):
    body = bytes([
        0x2A, 0x02, 0x90,             # loop: ld hl,(RP)
        0xED, 0x5B, 0x00, 0x90,       # wait: ld de,(WP)
        0x7D, 0xBB, 0x20, 0x04,       # ld a,l / cp e / jr nz,have
        0x7C, 0xBA, 0x28, 0xF4,       # ld a,h / cp d / jr z,wait
        0x7E,                         # have: ld a,(hl)
        0xDD, 0x77, 0x00, 0xDD, 0x23,  # ld (ix+0),a / inc ix
        0x23, 0x7D, 0xE6, 0x0F, 0x6F,  # inc hl / ld a,l / and 15 / ld l,a
        0x22, 0x02, 0x90])            # ld (RP),hl
    delay = bytes([0xC5, 0x06, 0x00, 0x10, 0xFE, 0x10, 0xFE, 0xC1]) \
        if slow else b""
    tail_len = 5                      # dec bc / ld a,b / or c / jr nz,loop
    back = -(len(body) + len(delay) + tail_len)
    tail = bytes([0x0B, 0x78, 0xB1, 0x20, back & 0xFF])
    return body + delay + tail + bytes([0xC9])


def copier_stream(data):
    return len(data).to_bytes(2, "little") + data


@unittest.skipUnless(os.access(LINESIM, os.X_OK),
                     "linesim not built (python3 harness/build_tools.py z80)")
class SimCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def path(self, name, data=None):
        p = os.path.join(self.tmp, name)
        if data is not None:
            with open(p, "wb") as f:
                f.write(data)
        return p

    def run_raw(self, code, pieces, regs, mode="flat", extra=(), org=0xC000):
        """Run linesim directly. pieces: (in_addr, stream, out_addr, out_len).
        Returns (json result, list of output bytes)."""
        prog = self.path("prog.bin", code)
        cmd = [LINESIM, "--mode", mode, "--load", f"{org}:{prog}",
               "--entry", str(org), "--code", f"{org}:{org + len(code)}",
               "--sp", "0xFFF0", "--sentinel", "0xFF00"]
        for k, v in regs.items():
            cmd += ["--reg", f"{k}={v}"]
        outs = []
        for i, (in_addr, stream, out_addr, out_len) in enumerate(pieces):
            s = self.path(f"s{i}.bin", stream)
            o = self.path(f"o{i}.bin")
            outs.append(o)
            cmd += ["--piece", f"{in_addr}:{s}:{out_addr}:{out_len}:{o}"]
        cmd += list(extra)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        data = []
        for o in outs:
            if os.path.exists(o):
                with open(o, "rb") as f:
                    data.append(f.read())
        return res, data

    # --- linesim.py driving fake decoders ---------------------------------

    def fake_decoder(self, name, code, regs=None, org=0xC000, wait=None):
        ddir = os.path.join(self.tmp, "decoders")
        os.makedirs(ddir, exist_ok=True)
        binp = os.path.join(ddir, name + ".bin")
        with open(binp, "wb") as f:
            f.write(code)
        meta = {"name": name, "source": name + ".asm", "bin": binp,
                "org": org, "size": len(code), "entry": org,
                "code_lo": org, "code_hi": org + len(code),
                "regs": {"HL": "@in", "DE": "@out"} if regs is None else regs,
                "defines": [], "rewrites": [], "note": "test"}
        if wait:
            meta.update(banked=True, wait_lo=wait[0], wait_hi=wait[1])
        with open(os.path.join(ddir, name + ".json"), "w") as f:
            json.dump(meta, f)
        return ddir

    def sim_row(self, code, original, packed_pieces, candidate="fake",
                status="ok", chunk=0, est=1.0, write_packed=True,
                org=0xC000, regs=None, wait=None, **kw):
        ddir = self.fake_decoder("dec", code, regs=regs, org=org, wait=wait)
        pdir = os.path.join(self.tmp, "packed")
        os.makedirs(pdir, exist_ok=True)
        if write_packed:
            for i, p in enumerate(packed_pieces):
                suffix = "" if len(packed_pieces) == 1 else f".{i}"
                with open(os.path.join(pdir, f"F.bin.{candidate}{suffix}"),
                          "wb") as f:
                    f.write(p)
        brow = {"file": "F.bin", "type": "other", "candidate": candidate,
                "status": status, "original_bytes": str(len(original)),
                "compressed_bytes": str(sum(map(len, packed_pieces))),
                "est_transfer_s": str(est), "limited_by": "line",
                "pieces": str(len(packed_pieces)), "note": ""}
        settings = linesim.SimSettings(decoder_dir=ddir, **kw)
        old = dict(decoders.CANDIDATE_DECODER)
        decoders.CANDIDATE_DECODER["fake"] = "dec"
        try:
            return linesim.simulate_row(brow, original, pdir, chunk, settings)
        finally:
            decoders.CANDIDATE_DECODER.clear()
            decoders.CANDIDATE_DECODER.update(old)


class ArrivalModelTests(SimCase):
    def test_last_byte_due_time(self):
        data = bytes(range(200))
        stream = copier_stream(data)
        res, out = self.run_raw(FAST_COPIER, [(0x4000, stream, 0x0100, 200)],
                                {"HL": "@in", "DE": "@out"})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(out[0], data)
        due_last = math.ceil(len(stream) * T_BYTE) + RX
        # LDIR picks up the last byte as soon as it is due, then RET + HALT.
        self.assertGreaterEqual(res["cycles"], due_last)
        self.assertLess(res["cycles"] - due_last, 60)
        self.assertGreater(res["stall"], res["cycles"] * 0.95)

    def test_settings_change_t_byte(self):
        stream = copier_stream(b"x" * 50)
        res, _ = self.run_raw(FAST_COPIER, [(0x4000, stream, 0x0100, 50)],
                              {"HL": "@in", "DE": "@out"},
                              extra=["--baud", "9600", "--cpu-hz", "4e6",
                                     "--rx-cost", "0"])
        self.assertAlmostEqual(res["t_byte"], 4166.6667, places=3)
        self.assertLess(res["cycles"] - len(stream) * 4166.67, 60)

    def test_receive_cost_taken_from_busy_decoder(self):
        data = b"abcdefgh" * 4
        res, out = self.run_raw(SLOW_COPIER,
                                [(0x4000, copier_stream(data), 0x0100,
                                  len(data))],
                                {"HL": "@in", "DE": "@out"})
        self.assertEqual(out[0], data)
        # The decoder is never idle once the first bytes are in, so nearly
        # every interrupt is charged to it.
        self.assertGreater(res["rx"], (len(data) - 4) * RX)
        self.assertLessEqual(res["rx"], (len(data) + 2) * RX)


class MatrixTests(SimCase):
    def test_happy_path(self):
        data = bytes(range(256)) * 4
        row = self.sim_row(FAST_COPIER, data, [copier_stream(data)])
        self.assertEqual(row["status"], "ok", row["reason"])
        for k in ("sim_s", "stall_t", "rx_t", "rx_total_t", "limited_by",
                  "est_transfer_s", "diff_s"):
            self.assertNotEqual(row[k], "", k)
        self.assertEqual(row["rx_total_t"], (len(data) + 2) * RX)

    def test_line_bound(self):
        data = bytes(range(256)) * 8
        row = self.sim_row(FAST_COPIER, data, [copier_stream(data)])
        self.assertEqual(row["limited_by"], "line")
        self.assertGreaterEqual(row["sim_s"], row["line_s"])
        self.assertLess(row["sim_s"], row["line_s"] * 1.10)

    def test_decoder_bound(self):
        data = bytes(range(256))
        row = self.sim_row(SLOW_COPIER, data, [copier_stream(data)])
        self.assertEqual(row["status"], "ok", row["reason"])
        self.assertEqual(row["limited_by"], "Z80")
        self.assertGreater(row["sim_s"], row["line_s"] * 1.4)

    def test_too_large(self):
        data = bytes(30000)
        row = self.sim_row(FAST_COPIER, data, [copier_stream(data)])
        self.assertEqual(row["status"], "too-large")
        self.assertEqual(row["sim_s"], "")

    def test_fits_just_below_reserved_area(self):
        n = (0xC000 - 0x0100 - 2) // 2
        data = bytes(n)
        stream = copier_stream(data)
        self.assertEqual(0x0100 + n + len(stream), 0xC000)
        row = self.sim_row(FAST_COPIER, data, [stream])
        self.assertEqual(row["status"], "ok", row["reason"])
        # The input ends at the code: code fetches must not count as
        # overread or stall.
        self.assertEqual(row["overread"], 0)
        due_last = math.ceil(len(stream) * T_BYTE) + RX
        self.assertLess(row["sim_cycles"] - due_last, 60)

    def test_mismatch(self):
        data = b"hello world" * 20
        row = self.sim_row(FAST_COPIER, data,
                           [copier_stream(data[:-1] + b"X")])
        self.assertEqual(row["status"], "mismatch")
        self.assertEqual(row["sim_s"], "")

    def test_cycle_limit(self):
        row = self.sim_row(SPIN, b"abc", [copier_stream(b"abc")],
                           max_cycles=1e6)
        self.assertEqual(row["status"], "error")
        self.assertIn("cycle limit", row["reason"])

    def test_pc_leaves_decoder(self):
        row = self.sim_row(JUMP_OUT, b"abc", [copier_stream(b"abc")])
        self.assertEqual(row["status"], "error")
        self.assertIn("PC left the decoder at 0x0000", row["reason"])

    def test_bench_failure_skipped(self):
        row = self.sim_row(FAST_COPIER, b"abc", [copier_stream(b"abc")],
                           status="timeout")
        self.assertEqual(row["status"], "skipped")
        self.assertIn("timeout", row["reason"])

    def test_missing_packed_file_skipped(self):
        row = self.sim_row(FAST_COPIER, b"abc", [copier_stream(b"abc")],
                           write_packed=False)
        self.assertEqual(row["status"], "skipped")
        self.assertIn("packed file missing", row["reason"])

    def test_chunked_pieces_share_the_line(self):
        data = bytes(range(256)) * 3
        pieces = [data[:512], data[512:]]
        streams = [copier_stream(p) for p in pieces]
        row = self.sim_row(FAST_COPIER, data, streams, chunk=512)
        self.assertEqual(row["status"], "ok", row["reason"])
        self.assertEqual(row["pieces"], 2)
        # Back to back on one line: ends when the last byte of the second
        # piece is due, not when the second piece alone would have finished.
        total = sum(map(len, streams))
        due_last = math.ceil(total * T_BYTE) + RX
        self.assertGreaterEqual(row["sim_cycles"], due_last)
        self.assertLess(row["sim_cycles"] - due_last, 100)

    def test_chunked_piece_mismatch(self):
        data = bytes(range(200))
        streams = [copier_stream(data[:100]), copier_stream(b"\0" * 100)]
        row = self.sim_row(FAST_COPIER, data, streams, chunk=100)
        self.assertEqual(row["status"], "mismatch")
        self.assertIn("piece 1", row["reason"])

    def test_chunked_pieces_restart_decoder(self):
        data = bytes(range(100)) * 2
        streams = [copier_stream(data[:100]), copier_stream(data[100:])]
        res, out = self.run_raw(
            FAST_COPIER,
            [(0x4000, streams[0], 0x0100, 100),
             (0x4000, streams[1], 0x0100, 100)],
            {"HL": "@in", "DE": "@out"})
        self.assertEqual(out, [data[:100], data[100:]])
        p0, p1 = res["pieces"]
        self.assertEqual(p1["start"], p0["end"])

    def test_overread_padding_shifts_later_pieces(self):
        # Copier that then reads one byte past its stream: ld a,(hl).
        code = FAST_COPIER[:-1] + bytes([0x7E, 0xC9])
        a, b = bytes(range(100)), bytes(range(100, 180))
        sa, sb = copier_stream(a), copier_stream(b)
        res, out = self.run_raw(code, [(0x4000, sa, 0x0100, len(a)),
                                       (0x4000, sb, 0x0100, len(b))],
                                {"HL": "@in", "DE": "@out"})
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(out, [a, b])
        p0, p1 = res["pieces"]
        self.assertEqual((p0["overread"], p1["overread"]), (1, 1))
        # Piece 1's bytes come after piece 0's padding byte: its last
        # padding read is due at line byte len(sa) + 1 + len(sb) + 1.
        due = math.ceil((len(sa) + 1 + len(sb) + 1) * T_BYTE) + RX
        self.assertGreaterEqual(p1["end"], due)
        self.assertLess(p1["end"] - due, 60)
        self.assertEqual(res["line_bytes"], len(sa) + 1 + len(sb))

    def test_baseline(self):
        data = bytes(1000)
        row = self.sim_row(FAST_COPIER, data, [data], candidate="none",
                           write_packed=False)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["sim_cycles"],
                         math.ceil(1000 * T_BYTE) + RX)
        self.assertEqual(row["limited_by"], "line")

    def test_paging_ports(self):
        res, out = self.run_raw(
            PAGING_PROBE, [(0x6000, b"\0", 0x0100, 2)], {},
            extra=["--dump-phys", f"{0x25 << 14}:1:{self.path('p25')}",
                   "--dump-phys", f"{0x26 << 14}:1:{self.path('p26')}",
                   "--dump-phys", f"{0x21 << 14}:1:{self.path('p21')}",
                   "--dump-phys", f"{0x20 << 14}:1:{self.path('p20')}"])
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(out[0], b"\xAA\x00")
        for page, want in (("p25", b"\xAA"), ("p26", b"\x55"),
                           ("p21", b"\x00"), ("p20", b"\x00")):
            with open(self.path(page), "rb") as f:
                self.assertEqual(f.read(), want, page)

    def test_paging_window_3(self):
        # Runs from window 1, because window 3 holds the stack. Window 3 ->
        # page 0x27, write 0x77 at 0xC000, window 3 back to 0x23, return.
        code = bytes([0x3E, 0x27, 0xD3, 0x73, 0x3E, 0x77, 0x32, 0x00, 0xC0,
                      0x3E, 0x23, 0xD3, 0x73, 0xC9])
        res, _ = self.run_raw(
            code, [(0x6000, b"\0", 0x0100, 1)], {}, org=0x4000,
            extra=["--dump-phys", f"{0x27 << 14}:1:{self.path('p27')}",
                   "--dump-phys", f"{0x23 << 14}:1:{self.path('p23')}"])
        self.assertEqual(res["status"], "ok", res["reason"])
        for page, want in (("p27", b"\x77"), ("p23", b"\x00")):
            with open(self.path(page), "rb") as f:
                self.assertEqual(f.read(), want, page)

    def test_paging_off_maps_rom(self):
        # ld a,0 / out (0x74),a: paging off, so the next fetch comes from
        # ROM (unprogrammed, reads 0 = NOP) and runs out of the code range.
        code = bytes([0x3E, 0x00, 0xD3, 0x74, 0x3E, 0x01, 0xD3, 0x74, 0xC9])
        res, _ = self.run_raw(code, [(0x4000, b"\0", 0x0100, 1)], {})
        self.assertEqual(res["status"], "error")
        self.assertIn("PC left the decoder at 0x", res["reason"])
        # Writing 1 keeps paging on, and the program returns normally.
        code = bytes([0x3E, 0x01, 0xD3, 0x74, 0xC9])
        res, _ = self.run_raw(code, [(0x4000, b"\0", 0x0100, 1)], {})
        self.assertEqual(res["status"], "ok", res["reason"])

    def ring_run(self, slow, data):
        return self.run_raw(
            ring_reader(slow), [(0, data, 0x0100, len(data))],
            {"IX": "@out", "BC": str(len(data))}, mode="ring",
            extra=["--ring", f"{RING_ADDR}:{RING_SIZE}:{WP}:{RP}"])

    def test_ring_full_pauses_line(self):
        data = bytes((i * 7 + 3) & 0xFF for i in range(100))
        res, out = self.ring_run(True, data)
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(out[0], data)               # bytes unchanged
        self.assertEqual(res["bytes_received"], len(data))
        self.assertGreater(res["flow_pause"], 0)
        # Later arrivals shift: the transfer ends well after the plain
        # line time.
        self.assertGreater(res["cycles"],
                           len(data) * T_BYTE + RX + res["flow_pause"] * 0.5)

    def test_ring_fast_reader_never_pauses(self):
        data = bytes(range(100))
        res, out = self.ring_run(False, data)
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(out[0], data)
        self.assertEqual(res["flow_pause"], 0)
        self.assertLess(res["cycles"] - (len(data) * T_BYTE + RX), 200)


# --- banked mode --------------------------------------------------------------

B_RING = (f"{decoders.BANKED_RING}:{decoders.BANKED_RING_SIZE}:"
          f"{decoders.BANKED_WP}:{decoders.BANKED_RP}")
B_WP, B_RP = decoders.BANKED_WP, decoders.BANKED_RP
B_ORG = decoders.BANKED_CODE_ORG
STATS_PHYS = (0x3F << 14) | (decoders.BANKED_REBANKS & 0x3FFF)


def phys(page, addr=0):
    return (page << 14) | (addr & 0x3FFF)


# Writes 0xA1 at 0x0000, 0xA2 at 0x4000, 0xA3 at 0x8000 and 0xA4 at 0xF000,
# so the dumps show which page each window starts on.
START_PROBE = bytes([
    0x3E, 0xA1, 0x32, 0x00, 0x00,      # ld a,0xA1 / ld (0x0000),a
    0x3E, 0xA2, 0x32, 0x00, 0x40,      # ld a,0xA2 / ld (0x4000),a
    0x3E, 0xA3, 0x32, 0x00, 0x80,      # ld a,0xA3 / ld (0x8000),a
    0x3E, 0xA4, 0x32, 0x00, 0xF0,      # ld a,0xA4 / ld (0xF000),a
    0xC9])

# Fills logical 0x0000-0xBFFF with (H ^ L), shifts the windows down a page
# with OUT writes (0x21, 0x22, 0x23), then fills logical 0x8000-0xBFFF (now
# page 0x23) with (H ^ L ^ 0x5A).
SHIFT_PROBE = bytes([
    0x21, 0x00, 0x00, 0x01, 0x00, 0xC0,              # ld hl,0 / ld bc,0xC000
    0x7C, 0xAD, 0x77, 0x23, 0x0B, 0x78, 0xB1, 0x20, 0xF7,  # loop1
    0x3E, 0x21, 0xD3, 0x70, 0x3E, 0x22, 0xD3, 0x71,
    0x3E, 0x23, 0xD3, 0x72,
    0x21, 0x00, 0x80, 0x01, 0x00, 0x40,              # ld hl,0x8000 / ld bc,0x4000
    0x7C, 0xAD, 0xEE, 0x5A, 0x77, 0x23, 0x0B, 0x78, 0xB1, 0x20, 0xF5,
    0xC9])


def shift_probe_expected():
    first = bytes(((a >> 8) ^ a) & 0xFF for a in range(0xC000))
    last = bytes(((a >> 8) ^ a ^ 0x5A) & 0xFF for a in range(0x8000, 0xC000))
    return first + last


# Waits until WP moves past RP (one byte arrived), then returns. The wait
# loop is 0xE003-0xE00E.
WAIT_ONE = bytes([
    0x2A, B_RP & 0xFF, B_RP >> 8,                    # ld hl,(RP)
    0xED, 0x5B, B_WP & 0xFF, B_WP >> 8,              # wait: ld de,(WP)
    0x7D, 0xBB, 0x20, 0x04,                          # ld a,l / cp e / jr nz,done
    0x7C, 0xBA, 0x28, 0xF4,                          # ld a,h / cp d / jr z,wait
    0xC9])                                           # done: ret
WAIT_ONE_RANGE = (B_ORG + 3, B_ORG + 15)

# Waits until WP reaches RP + 2, i.e. for a second byte: with a 1-byte
# stream it never comes. The wait loop is 0xE005-0xE010.
WAIT_FOREVER = bytes([
    0x2A, B_RP & 0xFF, B_RP >> 8,                    # ld hl,(RP)
    0x23, 0x23,                                      # inc hl / inc hl
    0xED, 0x5B, B_WP & 0xFF, B_WP >> 8,              # wait: ld de,(WP)
    0x7D, 0xBB, 0x20, 0xF8,                          # ld a,l / cp e / jr nz,wait
    0x7C, 0xBA, 0x20, 0xF4,                          # ld a,h / cp d / jr nz,wait
    0xC9])

# ld hl,(V_LEN) / ld (0x0000),hl / ld hl,(V_LAST) / ld (0x0002),hl / ret
LEN_PROBE = bytes([
    0x2A, decoders.BANKED_LEN & 0xFF, decoders.BANKED_LEN >> 8,
    0x22, 0x00, 0x00,
    0x2A, decoders.BANKED_LAST & 0xFF, decoders.BANKED_LAST >> 8,
    0x22, 0x02, 0x00,
    0xC9])


class BankedLinesimTests(SimCase):
    def run_banked(self, code, streams, out_lens, extra=(), wait=None):
        w = ["--wait", f"{wait[0]}:{wait[1]}"] if wait else []
        return self.run_raw(
            code, [(0, s, 0, n) for s, n in zip(streams, out_lens)], {},
            mode="banked", org=B_ORG,
            extra=["--ring", B_RING,
                   "--word", f"{decoders.BANKED_LEN}=@len",
                   "--word", f"{decoders.BANKED_LAST}=@last"] + w
                  + list(extra))

    def dump(self, name):
        with open(self.path(name), "rb") as f:
            return f.read()

    def test_start_state(self):
        res, out = self.run_banked(
            START_PROBE, [b"\0"], [1],
            extra=["--dump-phys", f"{phys(0x21)}:1:{self.path('w1')}",
                   "--dump-phys", f"{phys(0x22)}:1:{self.path('w2')}",
                   "--dump-phys", f"{phys(0x3F, 0xF000)}:1:{self.path('w3')}",
                   "--dump-phys", f"{phys(0x23)}:1:{self.path('p23')}"])
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(out[0], b"\xA1")            # window 0 = page 0x20
        self.assertEqual(self.dump("w1"), b"\xA2")   # window 1 = page 0x21
        self.assertEqual(self.dump("w2"), b"\xA3")   # window 2 = page 0x22
        self.assertEqual(self.dump("w3"), b"\xA4")   # window 3 = page 0x3F
        self.assertEqual(self.dump("p23"), b"\0")    # flat would use 0x23

    def test_output_read_from_physical_pages(self):
        want = shift_probe_expected()
        res, out = self.run_banked(SHIFT_PROBE, [b"\0"], [len(want)])
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(len(out[0]), 0x10000)
        self.assertEqual(out[0], want)

    def test_wait_range_counts_as_stall(self):
        res, _ = self.run_banked(WAIT_ONE, [b"x"], [1], wait=WAIT_ONE_RANGE)
        self.assertEqual(res["status"], "ok", res["reason"])
        due = math.ceil(T_BYTE) + RX
        self.assertGreaterEqual(res["cycles"], due)
        self.assertLess(res["cycles"] - due, 100)
        self.assertGreater(res["stall"], res["cycles"] - 100)
        self.assertEqual(res["rx"], 0)       # the interrupt fell in the wait
        res, _ = self.run_banked(WAIT_ONE, [b"x"], [1])
        self.assertEqual(res["stall"], 0)    # no --wait: plain decoder time
        self.assertEqual(res["rx"], RX)

    def test_len_and_last_words(self):
        res, out = self.run_banked(LEN_PROBE, [b"a" * 300, b"b" * 7],
                                   [4, 4])
        self.assertEqual(res["status"], "ok", res["reason"])
        self.assertEqual(out[0], (300).to_bytes(2, "little") + b"\0\0")
        self.assertEqual(out[1], (7).to_bytes(2, "little") + b"\1\0")

    def test_banked_too_large(self):
        row = self.sim_row(WAIT_ONE, bytes(decoders.BANKED_OUT_MAX + 1),
                           [b"x"], org=B_ORG, regs={},
                           wait=WAIT_ONE_RANGE, mode="banked")
        self.assertEqual(row["status"], "too-large")
        self.assertIn("RAM pages 0x20-0x3E", row["reason"])

    def test_banked_runaway_is_error(self):
        row = self.sim_row(WAIT_FOREVER, b"x", [b"x"], org=B_ORG, regs={},
                           wait=(B_ORG + 5, B_ORG + 17), mode="banked",
                           max_cycles=2e6)
        self.assertEqual(row["status"], "error")
        self.assertIn("cycle limit", row["reason"])

    def test_banked_row_through_linesim_py(self):
        row = self.sim_row(START_PROBE, b"\xA1", [b"\0"], org=B_ORG, regs={},
                           wait=(B_ORG, B_ORG), mode="banked")
        self.assertEqual(row["status"], "ok", row["reason"])
        self.assertEqual(set(row), set(linesim.SIM_FIELDS))


# Test programs built on banked.inc (need sjasmplus).
ECHO_OVERREAD = """
        ORG 0xE000
        INCLUDE "banked.inc"
banked_decoder:                 ; copies the stream and 3 bytes past its end
        ld      bc,(V_LEN)
        inc     bc
        inc     bc
        inc     bc
.loop:  RCHK_HL
        ld      a,(hl)
        inc     hl
        OCHK
        ld      (de),a
        inc     de
        dec     bc
        ld      a,b
        or      c
        jr      nz,.loop
        ret
"""

COPY_HELPERS = """
        ORG 0xE000
        INCLUDE "banked.inc"
banked_decoder:                 ; stream by copy_lit, then 40000 B at -32768
        ld      bc,(V_LEN)
        call    copy_lit
        ld      h,d
        ld      l,e
        ld      bc,32768
        or      a
        sbc     hl,bc
        ld      bc,40000
        call    copy_match
        ret
"""


@unittest.skipUnless(os.access(decoders.SJASMPLUS, os.X_OK),
                     "sjasmplus not built")
class BankedHelperTests(SimCase):
    def build(self, source):
        src = self.path("t.asm")
        with open(src, "w") as f:
            f.write(source)
        proc = subprocess.run(
            [decoders.SJASMPLUS, "--nologo", "--raw=t.bin", "--sym=t.sym",
             "-i", decoders.BANKED_SRC_DIR, "t.asm"],
            cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.path("t.bin"), "rb") as f:
            code = f.read()
        return code, decoders.parse_sym(self.path("t.sym"))

    def run_prog(self, source, stream, want):
        code, syms = self.build(source)
        meta = {"org": B_ORG, "bin": self.path("prog.bin", code),
                "entry": syms["banked_entry"], "code_lo": B_ORG,
                "code_hi": B_ORG + len(code), "regs": {},
                "wait_lo": syms["ring_wait_loop"],
                "wait_hi": syms["ring_wait_exit"]}
        status, res, reason = linesim.run_linesim_banked(
            meta, [(want, self.path("stream.bin", stream))],
            linesim.SimSettings(mode="banked"), self.tmp,
            extra=["--dump-phys", f"{STATS_PHYS}:4:{self.path('stats')}"])
        st = self.dump_stats()
        return status, res, reason, st

    def dump_stats(self):
        with open(self.path("stats"), "rb") as f:
            st = f.read()
        return int.from_bytes(st[:2], "little"), int.from_bytes(st[2:],
                                                                "little")

    def test_overread_no_deadlock_and_fast_reader_is_line_bound(self):
        rnd = random.Random(5)
        stream = bytes(rnd.getrandbits(8) for _ in range(60000))
        # The 3 bytes past the end are whatever the ring holds there, so
        # only the stream part of the output is compared.
        status, res, reason, (rebanks, wraps) = self.run_prog(
            ECHO_OVERREAD, stream, bytes(len(stream) + 3))
        self.assertIsNotNone(res, reason)
        self.assertEqual(res["status"], "ok", res["reason"])
        with open(self.path("out0.bin"), "rb") as f:
            out = f.read()
        self.assertEqual(out[:len(stream)], stream)
        self.assertGreaterEqual(rebanks, 1)
        self.assertGreaterEqual(wraps, 1)
        self.assertEqual(res["flow_pause"], 0)
        # Waits are counted as stall and dominate: the reader keeps up and
        # returns soon after the last byte; the 3 bytes past the end do not
        # hold it up.
        self.assertGreater(res["stall"], res["cycles"] / 2)
        due_last = math.ceil(len(stream) * T_BYTE) + RX
        self.assertLess(res["cycles"] - due_last, 2000)

    def test_copy_helpers_split_at_limits(self):
        rnd = random.Random(6)
        stream = bytes(rnd.getrandbits(8) for _ in range(40000))
        want = bytearray(stream)
        for _ in range(40000):
            want.append(want[len(want) - 32768])
        status, res, reason, (rebanks, wraps) = self.run_prog(
            COPY_HELPERS, stream, bytes(want))
        self.assertEqual(status, "ok", reason)
        self.assertGreaterEqual(rebanks, 2)
        self.assertGreaterEqual(wraps, 4)


# --- torture file through every banked decoder -----------------------------

def torture_data(full=True):
    """~96 KB: a 22,400 B random block, 10 KB of incompressible bytes, the
    block again (so it repeats at offset 32,640) and a 40 KB run of one
    byte. full=False cuts the run to 10,000 B (65,040 B in all) for the
    compressors that take at most 64 KB (lzsa2 raw blocks) or need too much
    memory above that (rip)."""
    rnd = random.Random(1234)
    block = bytes(rnd.getrandbits(8) for _ in range(22400))
    noise = bytes(rnd.getrandbits(8) for _ in range(10240))
    return block + noise + block + b"\x55" * (40960 if full else 10000)


TORTURE_CACHE = os.path.join(decoders.TOOLS_DIR, "torture-cache")


def packed_torture(candidate, data):
    """Compress data with the candidate's bench command, cached under
    harness/tools/torture-cache/. Returns (path or None, why)."""
    os.makedirs(TORTURE_CACHE, exist_ok=True)
    key = hashlib.sha1(data).hexdigest()[:12]
    path = os.path.join(TORTURE_CACHE, f"{candidate}-{key}")
    if os.path.isfile(path):
        return path, ""
    cand = candidates.by_name([candidate])[0]
    variant = cand.variant_for(len(data))
    if bench.missing_bins(variant.compress, bench.DEFAULT_BIN):
        return None, f"{candidate} compressor not built"
    with tempfile.TemporaryDirectory() as tmp:
        inp, outp = os.path.join(tmp, "in.bin"), os.path.join(tmp, "out.bin")
        with open(inp, "wb") as f:
            f.write(data)
        st, _, msg = bench.run_cmd(
            bench.expand(variant.compress, bench.DEFAULT_BIN, inp, outp),
            tmp, 600)
        if st != bench.OK or not os.path.isfile(outp):
            return None, f"{candidate} compressor failed: {msg}"
        shutil.copyfile(outp, path)
    return path, ""


@unittest.skipUnless(os.access(LINESIM, os.X_OK), "linesim not built")
class BankedTortureTests(unittest.TestCase):
    def check(self, dec_name, candidate, full=True):
        meta = decoders.load(dec_name, decoders.BANKED_OUT_DIR)
        if meta is None:
            self.skipTest(f"banked {dec_name} not assembled")
        data = torture_data(full)
        packed, why = packed_torture(candidate, data)
        if packed is None:
            self.skipTest(why)
        self.assertGreater(os.path.getsize(packed),
                           decoders.BANKED_RING_SIZE)   # so the ring wraps
        with tempfile.TemporaryDirectory() as tmp:
            stats = os.path.join(tmp, "stats")
            status, res, reason = linesim.run_linesim_banked(
                meta, [(data, packed)], linesim.SimSettings(mode="banked"),
                tmp, extra=["--dump-phys", f"{STATS_PHYS}:4:{stats}"])
            with open(stats, "rb") as f:
                st = f.read()
        self.assertEqual(status, "ok", reason)
        self.assertGreaterEqual(int.from_bytes(st[:2], "little"), 1,
                                "no window shift")
        self.assertGreaterEqual(int.from_bytes(st[2:], "little"), 1,
                                "no ring wrap")
        return res

    def test_dzx0_mega(self):
        self.check("dzx0_mega", "salvador")

    def test_unlzsa2_fast(self):
        self.check("unlzsa2_fast", "lzsa2", full=False)

    def test_unaplib_fast(self):
        self.check("unaplib_fast", "aplib-apultra")

    def test_deexoopt_x3_p39(self):
        self.check("deexoopt_x3_p39", "exomizer")

    def test_derip_fast(self):
        self.check("derip_fast", "rip", full=False)

    def test_unlz4_spke_fast_is_line_bound(self):
        res = self.check("unlz4_spke_fast", "lz4")
        self.assertGreater(res["stall"], res["cycles"] / 2)
        self.assertEqual(res["flow_pause"], 0)

    def test_unpack_upkr_z80_speed(self):
        res = self.check("unpack_upkr_z80_speed", "upkr")
        self.assertLess(res["stall"], res["cycles"] / 2)

    def test_deshrinkler_np_pauses_the_line(self):
        res = self.check("deshrinkler_np", "shrinkler")
        self.assertGreater(res["flow_pause"], 0)      # the ring filled
        self.assertLess(res["stall"], res["cycles"] / 2)


class ReportTests(SimCase):
    def test_csv_and_report(self):
        data = bytes(range(256))
        rows = [self.sim_row(FAST_COPIER, data, [copier_stream(data)]),
                self.sim_row(SLOW_COPIER, data, [copier_stream(data)]),
                self.sim_row(FAST_COPIER, data, [data], candidate="none",
                             write_packed=False),
                self.sim_row(FAST_COPIER, bytes(30000),
                             [copier_stream(bytes(30000))])]
        rows[1]["candidate"] = "slow"
        rows[3]["file"] = "G.bin"
        csv_path, md_path = self.path("sim.csv"), self.path("sim-report.md")
        linesim.write_csv(rows, csv_path)
        linesim.write_report(rows, linesim.SimSettings(), md_path, self.tmp,
                             {"baud": 19200, "cpu_hz": 8e6, "overhead": 0,
                              "chunk": 0})
        with open(md_path) as f:
            md = f.read()
        self.assertIn("Simulated (s)", md)
        self.assertIn("Est. (s)", md)
        self.assertIn("## Winner per file (simulated)", md)
        self.assertIn("## Overall", md)
        self.assertIn("**too-large**", md)
        self.assertIn("not measured on hardware", md)
        with open(csv_path) as f:
            self.assertEqual(f.readline().strip().split(","),
                             linesim.SIM_FIELDS)
        self.assertIn("Memory mode: **flat", md)
        self.assertNotIn("## Banked vs flat", md)

    def test_banked_report_compares_with_flat(self):
        data = bytes(range(256))
        flat = [self.sim_row(FAST_COPIER, data, [copier_stream(data)])]
        banked = [dict(flat[0], sim_s=flat[0]["sim_s"] + 0.5)]
        md_path = self.path("sim-report.md")
        linesim.write_report(banked, linesim.SimSettings(mode="banked"),
                             md_path, self.tmp, None, flat_rows=flat)
        with open(md_path) as f:
            md = f.read()
        self.assertIn("Memory mode: **banked", md)
        self.assertIn("## Banked vs flat", md)
        line = next(x for x in md.splitlines() if x.startswith("| fake |"))
        cells = [c.strip() for c in line.strip("|").split("|")]
        self.assertEqual(cells[1], "1")
        self.assertEqual(cells[4], "+0.5000")


if __name__ == "__main__":
    unittest.main()
