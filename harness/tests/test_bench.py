"""Unit tests for bench.py, using fake compressor scripts instead of the
real tools."""

import csv
import re
import os
import stat
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bench  # noqa: E402
import candidates  # noqa: E402
from candidates import Candidate, Variant  # noqa: E402

# Fake tools. "pack" prepends one byte; "unpack" drops it.
FAKES = {
    "pack": """
        data = open(sys.argv[1], 'rb').read()
        open(sys.argv[2], 'wb').write(b'P' + data)
    """,
    "unpack": """
        data = open(sys.argv[1], 'rb').read()
        open(sys.argv[2], 'wb').write(data[1:])
    """,
    "badunpack": """
        data = open(sys.argv[1], 'rb').read()
        open(sys.argv[2], 'wb').write(data[1:] + b'x')
    """,
    "sleep": """
        import time
        time.sleep(30)
    """,
    "crash": """
        sys.stderr.write('boom\\n')
        sys.exit(3)
    """,
    "silent": """
        pass
    """,
    "pipecrash": """
        print('line one | with a pipe')
        print('line two | another')
        sys.exit(8)
    """,
}


def make_fakes(bin_dir):
    for name, body in FAKES.items():
        path = os.path.join(bin_dir, name)
        with open(path, "w") as f:
            f.write(f"#!{sys.executable}\nimport sys\n")
            f.write(textwrap.dedent(body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def cand(name, comp, decomp="unpack", tstates=100.0):
    return Candidate(
        name=name, tool=name, tstates_per_byte=tstates,
        variants=[Variant(["{bin:%s}" % comp, "{in}", "{out}"],
                          None if decomp is None
                          else ["{bin:%s}" % decomp, "{in}", "{out}"])])


class FakeToolCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        make_fakes(self.bin)
        self.settings = bench.Settings(timeout=5, bin_dir=self.bin,
                                       mem_limit_mb=0)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def run_one(self, c, data=b"hello world" * 10, name="X.COM"):
        return bench.run_candidate(c, self.write(name, data), data,
                                   self.settings)


class EstimateTests(unittest.TestCase):
    def test_line_time(self):
        # 1920 bytes at 19200 baud (1920 B/s) is one second.
        line, decode, est, lim = bench.estimate(1920, 4000, 0, 19200, 8e6, 0)
        self.assertAlmostEqual(line, 1.0)
        self.assertEqual(decode, 0)
        self.assertAlmostEqual(est, 1.0)
        self.assertEqual(lim, "line")

    def test_overhead_added_to_line_time(self):
        line, _, _, _ = bench.estimate(1900, 100, 0, 19200, 8e6, 20)
        self.assertAlmostEqual(line, 1.0)

    def test_decode_time(self):
        # 8000 output bytes at 1000 T-states each on 8 MHz is one second.
        _, decode, _, _ = bench.estimate(10, 8000, 1000, 19200, 8e6, 0)
        self.assertAlmostEqual(decode, 1.0)

    def test_z80_limited(self):
        line, decode, est, lim = bench.estimate(192, 8000, 1000, 19200, 8e6, 0)
        self.assertAlmostEqual(line, 0.1)
        self.assertEqual(est, decode)
        self.assertEqual(lim, "Z80")

    def test_line_limited(self):
        line, decode, est, lim = bench.estimate(19200, 8000, 100, 19200, 8e6, 0)
        self.assertEqual(est, line)
        self.assertEqual(lim, "line")

    def test_baud_and_cpu_change_result(self):
        line, decode, _, _ = bench.estimate(960, 8000, 1000, 9600, 4e6, 0)
        self.assertAlmostEqual(line, 1.0)
        self.assertAlmostEqual(decode, 2.0)

    def test_256k_reference_points(self):
        # Research table: upkr and Shrinkler are Z80-bound even at their
        # published ratios on 256 KB.
        for tst, ratio in ((3200, 0.5768), (9900, 0.5810)):
            _, _, _, lim = bench.estimate(int(262144 * ratio), 262144, tst,
                                          19200, 8e6, 0)
            self.assertEqual(lim, "Z80")
        _, _, _, lim = bench.estimate(int(262144 * 0.6182), 262144, 73.9,
                                      19200, 8e6, 0)
        self.assertEqual(lim, "line")


class ChunkTests(FakeToolCase):
    def test_split(self):
        self.assertEqual(bench.split_chunks(b"abcdefghij", 4),
                         [b"abcd", b"efgh", b"ij"])
        self.assertEqual(bench.split_chunks(b"abc", 0), [b"abc"])
        self.assertEqual(bench.split_chunks(b"abc", 16384), [b"abc"])

    def test_sizes_summed_per_piece(self):
        self.settings.chunk = 4
        row = self.run_one(cand("p", "pack"), data=b"abcdefghij")
        self.assertEqual(row["status"], bench.OK)
        self.assertEqual(row["pieces"], 3)
        self.assertEqual(row["compressed_bytes"], 13)  # 3 pieces + 1 byte each
        self.assertEqual(row["original_bytes"], 10)

    def test_piece_failure_fails_row(self):
        self.settings.chunk = 4
        row = self.run_one(cand("p", "pack", "badunpack"), data=b"abcdefghij")
        self.assertEqual(row["status"], bench.MISMATCH)
        self.assertEqual(row["compressed_bytes"], "")

    def test_variant_selected_by_piece_size(self):
        c = Candidate("v", "v", 1, [
            Variant(["{bin:pack}", "{in}", "{out}"],
                    ["{bin:unpack}", "{in}", "{out}"], max_bytes=4),
            Variant(["{bin:crash}", "{in}", "{out}"], None)])
        self.assertEqual(self.run_one(c, data=b"abcdefgh")["status"],
                         bench.ERROR)
        self.settings.chunk = 4
        self.assertEqual(self.run_one(c, data=b"abcdefgh")["status"],
                         bench.OK)

    def test_piece_notes_not_repeated(self):
        self.settings.chunk = 4
        c = Candidate("n", "n", 1, [Variant(
            ["{bin:pack}", "{in}", "{out}"],
            ["{bin:unpack}", "{in}", "{out}"], note="raw block")])
        row = self.run_one(c, data=b"abcdefghij")
        self.assertEqual(row["note"], "piece 0: raw block")

    def test_lzsa2_switches_format_above_64k(self):
        lz = candidates.by_name(["lzsa2"])[0]
        self.assertIn("-r", lz.variant_for(65536).compress)
        self.assertNotIn("-r", lz.variant_for(65537).compress)


class StatusTests(FakeToolCase):
    def test_ok(self):
        row = self.run_one(cand("p", "pack"))
        self.assertEqual(row["status"], bench.OK)
        self.assertEqual(row["compressed_bytes"], 111)
        self.assertIn(row["limited_by"], ("line", "Z80"))
        self.assertNotEqual(row["est_transfer_s"], "")

    def test_baseline(self):
        row = self.run_one(candidates.by_name(["none"])[0], data=b"x" * 1920)
        self.assertEqual(row["status"], bench.OK)
        self.assertEqual(row["compressed_bytes"], 1920)
        self.assertAlmostEqual(row["est_transfer_s"], 1.0)
        self.assertEqual(row["limited_by"], "line")

    def test_missing(self):
        row = self.run_one(cand("m", "not-built"))
        self.assertEqual(row["status"], bench.MISSING)
        self.assertEqual(row["compressed_bytes"], "")
        self.assertEqual(row["est_transfer_s"], "")

    def test_timeout(self):
        self.settings.timeout = 0.5
        row = self.run_one(cand("s", "sleep"))
        self.assertEqual(row["status"], bench.TIMEOUT)
        self.assertLess(row["compress_s"], 5)
        self.assertEqual(row["compressed_bytes"], "")

    def test_crash_is_error(self):
        row = self.run_one(cand("c", "crash"))
        self.assertEqual(row["status"], bench.ERROR)
        self.assertIn("exit 3", row["note"])

    def test_no_output_is_error(self):
        row = self.run_one(cand("s", "silent"))
        self.assertEqual(row["status"], bench.ERROR)

    def test_mismatch_has_no_size(self):
        row = self.run_one(cand("b", "pack", "badunpack"))
        self.assertEqual(row["status"], bench.MISMATCH)
        self.assertEqual(row["compressed_bytes"], "")
        self.assertEqual(row["est_transfer_s"], "")

    def test_failing_decompressor_is_mismatch(self):
        row = self.run_one(cand("b", "pack", "crash"))
        self.assertEqual(row["status"], bench.MISMATCH)

    def test_no_host_decompressor_is_unverified(self):
        row = self.run_one(cand("u", "pack", None))
        self.assertEqual(row["status"], bench.UNVERIFIED)
        self.assertEqual(row["compressed_bytes"], 111)

    def test_decompressor_not_built_is_unverified(self):
        row = self.run_one(cand("u", "pack", "not-built"))
        self.assertEqual(row["status"], bench.UNVERIFIED)
        self.assertIn("not built", row["note"])

    def test_run_continues_after_failures(self):
        files = [self.write("A.COM", b"a" * 50), self.write("b.vbm", b"b" * 50)]
        cands = [cand("bad", "crash"), cand("slow", "sleep"),
                 cand("good", "pack")]
        self.settings.timeout = 0.5
        with open(os.devnull, "w") as log:
            rows = bench.run_all(files, cands, self.settings, log=log)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["status"] for r in rows if r["candidate"] == "good"],
                         [bench.OK, bench.OK])


    def test_missing_warns_once_and_continues(self):
        files = [self.write("A.COM", b"a" * 50), self.write("b.vbm", b"b" * 50)]
        cands = [cand("m", "not-built"), cand("good", "pack")]
        log_path = os.path.join(self.tmp, "log.txt")
        with open(log_path, "w") as log:
            rows = bench.run_all(files, cands, self.settings, log=log)
        with open(log_path) as f:
            warnings = [l for l in f if l.startswith("warning: m:")]
        self.assertEqual(len(warnings), 1)
        self.assertEqual([r["status"] for r in rows if r["candidate"] == "m"],
                         [bench.MISSING, bench.MISSING])
        self.assertEqual([r["status"] for r in rows if r["candidate"] == "good"],
                         [bench.OK, bench.OK])

    def test_tiny_file_baseline_can_win(self):
        # "pack" adds a byte, so on a 128 B file no compression is fastest.
        data = b"\0" * 128
        path = self.write("v00.vbp", data)
        rows = [bench.run_candidate(c, path, data, self.settings)
                for c in (candidates.by_name(["none"])[0], cand("p", "pack"))]
        good, _ = bench.rank_file(rows)
        self.assertEqual(good[0]["candidate"], "none")


def row(file, typ, cand_name, status, orig=100, comp=50, est=1.0, lim="line"):
    good = status in bench.GOOD
    return {"file": file, "type": typ, "candidate": cand_name,
            "status": status, "original_bytes": orig,
            "compressed_bytes": comp if good else "",
            "est_transfer_s": est if good else "", "compress_s": 0.1,
            "limited_by": lim if good else ""}


class KeepPackedTests(FakeToolCase):
    def setUp(self):
        super().setUp()
        self.packed = os.path.join(self.tmp, "packed")
        self.settings.keep_packed = self.packed

    def test_whole_file_saved(self):
        data = b"abc" * 50
        row = self.run_one(cand("fake", "pack"), data)
        self.assertEqual(row["status"], "ok")
        with open(os.path.join(self.packed, "X.COM.fake"), "rb") as f:
            self.assertEqual(f.read(), b"P" + data)

    def test_pieces_saved_with_index(self):
        self.settings.chunk = 10
        data = bytes(range(25))
        self.run_one(cand("fake", "pack"), data)
        self.assertEqual(sorted(os.listdir(self.packed)),
                         ["X.COM.fake.0", "X.COM.fake.1", "X.COM.fake.2"])
        with open(os.path.join(self.packed, "X.COM.fake.2"), "rb") as f:
            self.assertEqual(f.read(), b"P" + data[20:])

    def test_old_files_replaced(self):
        os.makedirs(self.packed)
        for name in ("X.COM.fake.0", "X.COM.fake.7", "X.COM.fakeother"):
            open(os.path.join(self.packed, name), "w").close()
        self.run_one(cand("fake", "pack"), b"abc")
        self.assertEqual(sorted(os.listdir(self.packed)),
                         ["X.COM.fake", "X.COM.fakeother"])

    def test_off_by_default(self):
        self.settings.keep_packed = None
        self.run_one(cand("fake", "pack"))
        self.assertFalse(os.path.exists(self.packed))

    def test_estimate_columns_unchanged(self):
        row = self.run_one(cand("fake", "pack"))
        self.assertEqual(list(row), bench.CSV_FIELDS)


class GroupTests(unittest.TestCase):
    ROWS = [
        row("A.COM", "executable", "x", "ok", 100, 50, 1.0),
        row("B.COM", "executable", "x", "ok", 200, 80, 2.0, "Z80"),
        row("a.vbm", "bitmap", "x", "mismatch"),
        row("A.COM", "executable", "y", "ok-unverified", 100, 40, 0.5),
        row("B.COM", "executable", "y", "ok", 200, 60, 0.7),
        row("a.vbm", "bitmap", "y", "ok", 300, 30, 0.2),
    ]

    def test_executable_totals(self):
        t = {g["candidate"]: g for g in bench.group_totals(self.ROWS, "executable")}
        self.assertEqual(t["x"]["original"], 300)
        self.assertEqual(t["x"]["compressed"], 130)
        self.assertAlmostEqual(t["x"]["est_s"], 3.0)
        self.assertEqual(t["x"]["z80_limited"], 1)
        self.assertFalse(t["x"]["incomplete"])
        self.assertTrue(t["y"]["unverified"])

    def test_failure_marks_incomplete_and_is_excluded(self):
        t = {g["candidate"]: g for g in bench.group_totals(self.ROWS, None)}
        self.assertTrue(t["x"]["incomplete"])
        self.assertEqual(t["x"]["counted"], 2)
        self.assertEqual(t["x"]["files"], 3)
        self.assertEqual(t["x"]["compressed"], 130)
        self.assertFalse(t["y"]["incomplete"])

    def test_incomplete_ranked_after_complete(self):
        rows = [row("A.COM", "executable", "fast", "ok", 100, 1, 0.01),
                row("B.COM", "executable", "fast", "timeout"),
                row("A.COM", "executable", "slow", "ok", 100, 90, 5.0),
                row("B.COM", "executable", "slow", "ok", 100, 90, 5.0)]
        order = [g["candidate"] for g in bench.group_totals(rows, None)]
        self.assertEqual(order, ["slow", "fast"])

    def test_rank_file_excludes_failures(self):
        rows = [row("A", "bitmap", "a", "ok", est=2.0),
                row("A", "bitmap", "b", "mismatch"),
                row("A", "bitmap", "c", "ok-unverified", est=1.0)]
        good, bad = bench.rank_file(rows)
        self.assertEqual([r["candidate"] for r in good], ["c", "a"])
        self.assertEqual([r["candidate"] for r in bad], ["b"])


class ReportTests(FakeToolCase):
    def test_csv_and_report_written(self):
        files = [self.write("A.COM", b"a" * 300), self.write("v.vbp", b"\0" * 128)]
        cands = [candidates.by_name(["none"])[0], cand("good", "pack"),
                 cand("unv", "pack", None), cand("bad", "pack", "badunpack"),
                 cand("pipes", "pipecrash")]
        with open(os.devnull, "w") as log:
            rows = bench.run_all(files, cands, self.settings, log=log)
        out = os.path.join(self.tmp, "out")
        os.mkdir(out)
        bench.write_csv(rows, os.path.join(out, "results.csv"))
        bench.write_report(rows, cands, self.settings,
                           os.path.join(out, "report.md"), "test")
        with open(os.path.join(out, "results.csv")) as f:
            got = list(csv.DictReader(f))
        self.assertEqual(len(got), 10)
        for col in ("est_line_s", "est_decode_s", "est_transfer_s"):
            self.assertIn(col, got[0])
        self.assertNotIn("line_s", got[0])
        bad = [r for r in got if r["candidate"] == "bad"]
        self.assertTrue(all(r["compressed_bytes"] == "" for r in bad))
        with open(os.path.join(out, "report.md")) as f:
            md = f.read()
        for heading in ("## Winner per file", "## Per file",
                        "## Per type: executable", "## Per type: bitmap",
                        "## Overall", "## Pinned versions"):
            self.assertIn(heading, md)
        self.assertIn("estimates", md)
        self.assertIn("Est. line (s)", md)
        self.assertIn("Est. decode (s)", md)
        self.assertIn("(winner)", md)
        self.assertIn("**incomplete**", md)
        self.assertIn("† unverified", md)
        # Tool output containing "|" must not add table columns.
        cols = None
        for line in md.splitlines():
            if not line.startswith("|"):
                cols = None
                continue
            n = len(re.findall(r"(?<!\\)\|", line))
            if cols is None:
                cols = n
            self.assertEqual(n, cols, line)


if __name__ == "__main__":
    unittest.main()
