---
title: 'Z80 compression benchmark harness'
type: feature
ticket: ''
created: '2026-11-08'
status: built
baseline_revision: 'NO_VCS'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/research-z80-compression/research-z80-compression.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** We need to choose a compression format for host-to-Z80 transfers over 19200 baud. Published benchmarks don't use CP/M `.COM` files or `.vbm`/`.vbp` bitmaps, so we have no ratios for our own data.

**Approach:** Build pinned versions of each candidate compressor from source on Linux. Compress every file in `test_files/` with each one and check the round trip with the host decompressor where one exists. Report compressed size, host compression time and estimated transfer time per file, per file type and overall.

## Boundaries & Constraints

**Always:**
- Candidates: none (baseline), ZX0 v2 (`zx0`) and salvador, LZSA2, aPLib via OaPACK and apultra, Exomizer 3.1.2 `raw -P39`, RIP, LZ4 via lz4ultra (raw blocks), and upkr 0.2.2 `--z80` and Shrinkler 4.7 (data mode) as decoder-bound reference points.
- Pin every tool to a tag, or to a full commit SHA when the repo has no tags (ZX0, RIP, OaPACK). Record the pin in the manifest.
- Estimated transfer time = max(line time, decode time). Line time = (compressed bytes + per-file overhead) ÷ (baud ÷ 10). Decode time = original bytes × the format's T-states per output byte ÷ CPU Hz. Use the T-states per output byte from the research's "Measured performance" table. Defaults: 19200 baud, 8 MHz, 0 overhead. All three can be changed on the command line.
- Label every time as an estimate. Name the limiting factor (line or Z80) in each row.
- Every compressor run has a timeout (default 600 s). A timeout, a crash or a round-trip mismatch is recorded as that run's status, and the remaining runs continue.
- Use the Python standard library only. Tool builds use the system gcc/g++/make and cargo.

**Never:**
- No Z80 emulator and no simulated line with decoder stalls (research recommendation 2). That is follow-up work.
- Don't vendor tool sources into the project. Clone them into a git-ignored tools directory.
- Don't edit any file in `test_files/`.
- Don't report a size as valid when its round trip failed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | All tools built, `test_files/` | CSV and Markdown report with one row per (file, candidate) | No error expected |
| Tool missing | A binary isn't built | That candidate's rows have status `missing` | Warn once and continue |
| Too large | RIP on the 256 KB `v80000.vbm` | Row status `timeout` or `error` | Other runs continue |
| Round trip fails | Decompressed output differs from the input | Row status `mismatch`; size left out of totals and rankings | Continue |
| No host decompressor | Shrinkler, OaPACK without apultra | Status `ok-unverified`; size counted and marked in the report | Continue |
| Chunked mode | `--chunk 16384` | Each file is compressed as independent 16 KB pieces and the sizes summed | Same rules apply per piece |
| Tiny file | `v00.vbp` (128 B) | Baseline may win; the report shows it | No error expected |

</frozen-after-approval>

## Code Map

The project is new. The only existing content is:

- `test_files/` -- the corpus. Four `.COM` files (716 B–30 KB), one `.vbp`, eight `.vbm` (768 B–256 KB). Read only.
- `_bmad-output/research-z80-compression/research-z80-compression.md` -- candidate list, repos, versions, flags and the T-states per output byte table. Take numbers from it; don't re-research them.

Found during investigation:

- Toolchains present: gcc, make, cmake, cargo, Python 3.14, git. Network access to GitHub, GitLab and Bitbucket works.
- No compressor is installed. All of them must be built.
- RIP (`gitlab.com/eugene77/rip`) and OaPACK (`gitlab.com/eugene77/oapack`) have no tags and ship only Visual Studio project files. Build them with `g++ -O2 *.cpp`. RIP has its own decompressor. OaPACK doesn't, so check its output with `apultra -d`.
- Exomizer lives at `bitbucket.org/magli143/exomizer`, tag 3.1.2, with a makefile in `src/`. Its round trip is `exomizer raw -d`.
- upkr: `cargo build --release` at tag v0.2.2. Its round trip is `upkr -d` with the same `--z80` flag.
- Check each tool's `--help` for its decompress and raw-mode flags before wiring it up. Don't guess them.

## Tasks & Acceptance

**Execution:**
- [x] `harness/tools.json` -- manifest: one entry per tool with its repo, pinned ref, build commands and binary paths -- one place to see and bump versions
- [x] `harness/build_tools.py` -- clone each tool into `harness/tools/src/<name>` at its pin, build it, and copy the binaries to `harness/tools/bin`. Skip tools that are already built unless `--force` is given. Print a built/failed summary -- reproducible tool setup
- [x] `harness/candidates.py` -- one candidate definition each: compress and decompress command templates, the T-states per output byte, and the build tool it uses -- keeps tool details out of the runner
- [x] `harness/bench.py` -- CLI (`--files`, `--baud`, `--cpu-hz`, `--overhead`, `--chunk`, `--timeout`, `--out`). Runs each candidate on each file in temp dirs, times it, checks the round trip and computes the estimate. Writes `results.csv` and `report.md` -- the deliverable
- [x] `harness/tests/test_bench.py` -- unit tests for the estimate maths, the line/Z80 limiter, chunking, group totals, and the `missing`/`timeout`/`mismatch`/`ok-unverified` statuses, using fake compressor scripts -- covers the I/O matrix without the real tools
- [x] `harness/README.md` -- how to build the tools and run the harness, the estimate model and its limits
- [x] `.gitignore` -- ignore `harness/tools/` and `harness/results/`

**Acceptance Criteria:**
- Given a clean checkout, when `python3 harness/build_tools.py` runs, then every candidate's binaries exist in `harness/tools/bin`, or the summary names the tool that failed and why.
- Given built tools, when `python3 harness/bench.py` runs, then `report.md` has a per-file table ranked by estimated time with a winner per file, a per-type table (`.COM` against `.vbm`/`.vbp`), an overall table, and a pinned-versions table.
- Given the 256 KB file, when the harness runs, then upkr and Shrinkler show `Z80` as the limiting factor and the LZ candidates show `line`.

## Design Notes

Grouping: `.COM` is "executable", while `.vbm` and `.vbp` are "bitmap". Totals sum sizes and estimated times over the files where the candidate's status is `ok` or `ok-unverified`. A candidate that has any failure in a group gets an "incomplete" flag in that group, so it never looks better than it is because a file is missing.

## Verification

**Commands:**
- `python3 -m unittest discover -s harness/tests` -- expected: all tests pass
- `python3 harness/build_tools.py` -- expected: summary with every tool built (if one fails, record why in Implementation Notes)
- `python3 harness/bench.py --out harness/results` -- expected: `results.csv` and `report.md` written. Every non-baseline row's status is `ok` or `ok-unverified`, apart from the documented RIP size limit
- `python3 harness/bench.py --chunk 16384 --out harness/results-chunked` -- expected: the report is written

## Implementation Notes

- **Pins resolved (2026-11-08):** zx0 `ecde3a2a…` (v2.2), salvador `1.4.2`, lzsa `1.4.1`, apultra `1.4.8`, OaPACK `29d34485…` (2026.04.09), Exomizer `3.1.2`, RIP `5861ffbc…` (2023.03.30), lz4ultra `1.3.0`, upkr `v0.2.2`, Shrinkler `v4.7`. Full SHAs in `harness/tools.json`; built commits are stamped in `harness/tools/bin/.<tool>.stamp.json` and printed in the report.
- **RIP and OaPACK don't build with plain `g++ -O2 *.cpp`.** They use MSVC-only C++: `std::exception("msg")`, `__int64`, `1i64` literals, a lambda taking `string&` bound to a temporary, and `byte` clashing with `std::byte` under C++17. Fix: the manifest applies regex patches to the clean checkout (no sources vendored), builds with `-std=gnu++14 -fpermissive`, and force-includes `harness/compat/msvc_compat.h`. A patch that matches nothing fails the build.
- **All ten tools build** from a clean `harness/tools/` in about 20 s.
- **Flags checked from `--help`:** zx0 `-f`/dzx0; salvador `-d`; lzsa `-f 2 -r`/`-d`; apultra `-d`; OaPACK takes no decompress flag (checked with `apultra -d`); Exomizer `raw -P39 -o`, `raw -d -P39`; RIP `-d`; lz4ultra `-r`/`-d -r`; upkr `--z80`, `-d --z80`; Shrinkler `-d -b -p` (data, no parity context, no progress), no host decompressor.
- **LZSA2 raw blocks are limited to 64 KB** (`lzsa -r` refuses larger input). Above 64 KB the candidate falls back to the LZSA stream format, and the row's note says so. The Z80 raw-block decoders would need a frame-parsing wrapper for that case.
- **OaPACK is limited to 64 KB input** (exit 3, "Max supported file size is 65536 bytes"), so `v80000.vbm` is `error` for OaPACK as well as RIP. This is a second size limit besides RIP's.
- **RIP on `v80000.vbm`** asks for 53,761 MiB and fails with `std::bad_alloc` under the per-run address-space limit. I added `--mem-limit-mb` (default 12288) so RIP fails fast instead of pushing the machine into swap. It's not in the plan's CLI list.
- **OaPACK and RIP time out (600 s) on `v48000.vbm`**, a 16 KB file of alternating `00`/`08` bytes. Every other tool handles it in under 1 s.
- **Acceptance criterion 3 doesn't hold on this corpus, and that comes from the data, not a bug.** `v80000.vbm` is 262,144 copies of byte `0x11`. Every compressor gets it under 1.1 KB, so line time is under 0.6 s, while decode time at the research's per-output-byte figures is 1.2–8.4 s. So upkr and Shrinkler are Z80-limited as expected, but so is every LZ candidate. The research's 256 KB ROM numbers do put LZ formats on `line` (a unit test covers that). The per-byte decode figures are averages over ROM data and overstate the cost of one long run, so the Z80 estimate for this file is pessimistic.
- **Full run results (`harness/results/`):** 132 rows. Non-baseline failures: OaPACK and RIP `timeout` on `v48000.vbm`, OaPACK and RIP `error` on `v80000.vbm`. All other rows are `ok`; Shrinkler is `ok-unverified`. The full run takes about 30 min, mostly the two timeouts and zx0's 191 s on `v80000.vbm`.
- **Chunked run (`--chunk 16384`, `harness/results-chunked/`):** report written. RIP now handles `v80000.vbm` (16 KB pieces fit its memory need) but still times out on `v48000.vbm`. OaPACK times out on both (a 16 KB piece of one repeated byte is as slow for it as the alternating pattern).
- **Unit tests:** 28 pass (`python3 -m unittest discover -s harness/tests`). They cover the estimate maths and limiter, chunk splitting and summing, size-based variant selection, every status, group totals and incomplete flags, and that `|` in tool output doesn't break the Markdown tables.
- `lz4ultra`'s Makefile creates an empty `harness/tools/bin/posix` directory as a side effect. It's harmless and inside the ignored tools directory.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-11-09):** 10 findings. high 0, medium 0, low 7, false 1, maybe-false 0, rejected on the plan-edit rule 2. 5 patched.

| # | Finding | Verdict | Route | Evidence / action |
|---|---|---|---|---|
| 1 | AC 3 not met: every candidate is Z80-limited on `v80000.vbm` | real, data-caused | rejected (fix is a plan edit) | `v80000.vbm` is 262,144 × `0x11`. The estimate code applies the frozen formula correctly. Only the AC can change, so the decision is raised with the human at presentation. |
| 2 | OaPACK and RIP fail beyond the documented RIP size limit; possible wiring problem | false (wiring); deviation rejected (plan edit) | rejected | `oapack` on `v48000.vbm` ran at 100% CPU with 2.3 GB RSS after 15 s, with stdin on /dev/null, so it is computing and not waiting on input. The same binary packs `v4C000.vbm`/`v7C000.vbm` in 6–10 s. These are tool limits, raised at presentation. |
| 3 | `_limit_memory` hard-limit `setrlimit` raises in the child and aborts the run | low | patch | Real whenever the hard `ulimit -v` < 12 GB (here it is unlimited). The fix is a direct correction: set only the soft limit, capped at the hard limit. |
| 4 | `_ratio(0, 0)` ZeroDivisionError for an empty input file | low | rejected | Real, but there are no empty files in the corpus and the fix adds a guard. |
| 5 | Duplicate notes per piece in chunked mode | low | patch | `r.note not in notes` compares against `"piece N: ..."` strings and never matches. Visible in every chunked report. |
| 6 | Rows keyed by basename merge same-named files from different directories | low | rejected | Real, but unlikely in normal use, and the fix adds keying complexity. |
| 7 | No validation of `--baud 0`, `--cpu-hz 0` or a negative `--chunk` | low | rejected | Real, but unlikely in normal use, and the fix adds guards. |
| 8 | `build_tools.run` strict UTF-8 decode can skip the summary | low | patch | `text=True` decodes strictly. The fix is a direct one-argument correction (`errors="replace"`). |
| 9 | A failed rebuild leaves new binaries beside the old stamp | low | patch | The stamp is written only after all binaries are copied. The fix is a direct correction: remove the stamp before copying. |
| 10 | CSV and per-file table time columns not labelled as estimates | low | patch | Frozen rule "Label every time as an estimate". Only the report's intro paragraph says so. Rename the columns and headers. |
