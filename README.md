# Z80 compression for MicroBeast serial transfer

The question this project answers: **when sending files from a modern host to
a MicroBeast (8 MHz Z80, CP/M) over a 19200 baud serial line, which
compression format gets the file there and decoded fastest, and is it worth
compressing at all?**

At 19200 baud 8N1 the line moves about 1,920 bytes a second, so the Z80 has
about 4,167 T-states per received byte. A format that shrinks the file
further only helps if its decoder can keep up with the bytes as they arrive.
The project measures both sides: the compressed size on real MicroBeast files,
and the time the real Z80 decoders take when fed at line speed.

**Short answer:** compress everything with **salvador** (ZX0 format) and decode
it while it is being received. Total time falls from 195.6 s to 18.2 s for the
whole corpus, and from 29.7 s to 17.1 s for the `.COM` files. Exomizer is the
same speed overall; the section "Recommendations" gives the reasons to prefer
salvador.

## What is in here

| Path | What it is |
|---|---|
| `test_files/` | The corpus: 4 CP/M executables (`.COM`) and 8 video memory images (`.vbm`/`.vbp`), 375,552 bytes in all |
| `_bmad-output/research-z80-compression/` | The research that picked the candidates and their published decoder costs |
| `harness/` | The benchmark and the line simulation. `harness/README.md` has the full documentation |
| `harness/results*/` | Reports and CSVs from the runs below |

Three tools in `harness/` build on each other:

1. **`bench.py`** compresses every file with every candidate at pinned
   versions and checks the round trip with the host decompressor. It also
   *estimates* the transfer time as max(line time, decode time), using
   published T-states-per-byte averages.
2. **`linesim.py`, flat mode**, runs each candidate's real Z80 decoder (from
   uniabis/z80depacker) in a cycle-counted Z80 emulator. Compressed byte *i*
   arrives at (*i* + 1) × 4,167 T-states plus a 150 T-state receive
   interrupt. The decoder stalls when it reads a byte that has not yet
   arrived, and every interrupt takes time from it. The output is checked
   byte for byte against the original. Input and output must fit together
   below 0xC000, so the 256 KB file cannot run whole.
3. **`linesim.py`, banked mode**, runs ports of the same eight decoders
   against the real MicroBeast memory model:
   - **Output:** written through windows 0–2, shifted one 16 KB page at a
     time with OUT writes to ports 0x70–0x72. That keeps 32 KB of history
     mapped.
   - **Input:** read from an 8 KB receive ring in window 3, which the
     simulated interrupt fills under RTS/CTS flow control.
   - **Checks and waits:** the decoder checks that every byte has arrived and
     waits in its own code, and those checks count toward the time.

   Every file runs whole, up to 496 KB of output.

The line is simulated, not measured on hardware. See "Caveats".

```sh
python3 harness/build_tools.py                                   # compressors, assembler, Z80 core, decoders
python3 harness/bench.py --keep-packed --out harness/results     # sizes + estimates
python3 harness/linesim.py --results harness/results             # flat simulation
python3 harness/linesim.py --mode banked --out harness/results-banked   # banked simulation
python3 -m unittest discover -s harness/tests
```

Add `--chunk 16384` to `bench.py` (with a separate `--out`) to compress each
file as independent 16 KB pieces. Pass that directory to `linesim.py
--results` to simulate it.

## Candidates

| Candidate | Format | Compressor | Z80 decoder | Decoder size flat / banked (B) |
|---|---|---|---|---:|
| none | raw bytes | – | – | 0 |
| zx0 | ZX0 v2 | zx0 (optimal, slow) | dzx0_mega | 673 / 1,272 |
| salvador | ZX0 v2 | salvador (fast) | dzx0_mega | 673 / 1,272 |
| lzsa2 | LZSA2 raw block | lzsa 1.4.1 | unlzsa2_fast | 207 / 755 |
| aplib-oapack | aPLib | OaPACK (optimal) | unaplib_fast | 235 / 816 |
| aplib-apultra | aPLib | apultra 1.4.8 | unaplib_fast | 235 / 816 |
| exomizer | Exomizer 3 raw -P39 | exomizer 3.1.2 | deexoopt_x3 | 244 / 827 |
| rip | RIP | rip 2023.03.30 | derip_fast | 231 / 718 |
| lz4 | LZ4 raw block | lz4ultra 1.3.0 | unlz4_spke_fast | 87 / 580 |
| upkr | upkr (rANS) | upkr 0.2.2 `--z80` | unpack_upkr_z80 | 195 / 685 |
| shrinkler | Shrinkler (range coder) | Shrinkler 4.7 | deshrinkler_np | 206 / 708 |

The banked sizes include about 450 bytes of shared support code: the ring
reader, the window shift and the split copies. dzx0_mega is the largest,
unrolled ZX0 decoder; z80depacker has smaller, slightly slower ones.

## Results

All times are total transfer time in seconds: from the first byte on the wire
to the last byte decoded, at 19200 baud, 8 MHz and 150 T-states per receive
interrupt. Lower is better.

### Compressed size

From `harness/results/report.md`, compressing each file whole.

| Candidate | `.COM` packed (57,088 B in) | Ratio | Bitmaps packed (318,464 B in) | Compress time, all files (s) |
|---|---:|---:|---:|---:|
| upkr | 31,274 | 54.8% | 675 | 18.7 |
| shrinkler | 31,486 | 55.2% | 660 | 1.0 |
| rip | 31,858 | 55.8% | fails on 2 files | 56.1 (10 files) |
| exomizer | 32,556 | 57.0% | 799 | 7.7 |
| zx0 | 32,835 | 57.5% | 701 | 225.0 |
| salvador | 32,836 | 57.5% | 720 | 3.8 |
| aplib-oapack | 32,860 | 57.6% | fails on 2 files | 46.8 (10 files) |
| aplib-apultra | 32,861 | 57.6% | 718 | 2.6 |
| lzsa2 | 34,109 | 59.7% | 837 | 1.8 |
| lz4 | 39,097 | 68.5% | 2,194 | 0.6 |

The bitmaps are mostly empty or repetitive, so every format shrinks them to
under 1% of their size apart from LZ4. RIP and OaPACK time out on
`v48000.vbm` and fail on the 256 KB `v80000.vbm`: OaPACK takes at most 64 KB,
and RIP would need about 53 GB of memory.

### Unbanked (flat) simulation

From `harness/results/sim-report.md`. Flat mode cannot hold the 256 KB
`v80000.vbm`, and LZ4's `ANTFORTH.COM` misses the limit by a few bytes. The
`.COM` table is complete for every candidate except LZ4.

| Candidate | `.COM` simulated (s) | `.COM` estimate (s) | Bitmaps, 7 of 8 files (s) |
|---|---:|---:|---:|
| rip | **16.59** | 16.59 | 0.43 (6 files) |
| exomizer | 16.96 | 16.96 | 0.47 |
| zx0 | 17.10 | 17.10 | 0.43 |
| salvador | 17.10 | 17.10 | 0.43 |
| aplib-oapack | 17.11 | 17.11 | 0.39 (6 files) |
| aplib-apultra | 17.12 | 17.12 | 0.44 |
| lzsa2 | 17.77 | 17.77 | 0.48 |
| upkr | 21.70 | 22.84 | 0.68 |
| shrinkler | 57.03 | 70.65 | 1.57 |
| none | 29.73 | 29.73 | 165.87 (all 8) |
| lz4 | 3 of 4 files | | 0.65 |

To compare every format on every file in flat mode, the chunked run splits
files into 16 KB pieces and sends them back to back. From
`harness/results-chunked/sim-report.md`:

| Candidate | All 12 files, chunked, simulated (s) | Estimate (s) |
|---|---:|---:|
| exomizer | 18.54 | 27.57 |
| zx0 / salvador | 18.56 | 20.55 |
| aplib-apultra | 18.62 | 22.09 |
| lzsa2 | 19.34 | 20.72 |
| lz4 | 22.22 | 22.65 |
| upkr | 23.36 | 150.22 |
| shrinkler | 60.34 | 464.75 |
| none | 195.60 | 195.60 |

### Banked simulation

From `harness/results-banked/sim-report.md`. Every file runs whole.

| Candidate | All 12 files (s) | `.COM` (s) | Bitmaps (s) | Notes |
|---|---:|---:|---:|---|
| exomizer | **18.14** | 16.96 | 1.18 | complete |
| salvador | 18.23 | 17.10 | **1.13** | complete |
| lz4 | 21.83 | 20.36 | 1.47 | complete |
| rip | 17.03 (10 files) | **16.59** | 0.43 (6 files) | compressor fails on 2 bitmaps |
| aplib-oapack | 17.51 (10 files) | 17.12 | 0.39 (6 files) | compressor fails on 2 bitmaps |
| zx0 | 17.53 (11 files) | 17.10 | 0.43 (7 files) | `v80000.vbm` mismatch, see below |
| aplib-apultra | 17.56 (11 files) | 17.12 | 0.44 (7 files) | `v80000.vbm` mismatch |
| lzsa2 | 18.24 (11 files) | 17.77 | 0.48 (7 files) | `v80000.vbm` needs the stream format, which has no Z80 decoder |
| upkr | 22.75 (11 files) | 22.05 | 0.70 (7 files) | `v80000.vbm` mismatch |
| shrinkler | 59.08 (11 files) | 57.50 | 1.58 (7 files) | `v80000.vbm` mismatch |
| none | 195.60 | 29.73 | 165.87 | |

The 256 KB `v80000.vbm` on its own takes 136.5 s uncompressed. salvador gets
it there and decoded in 0.69 s, Exomizer in 0.71 s and LZ4 in 0.82 s, and in
all three the Z80 is the limit, not the line. zx0, apultra, upkr and
Shrinkler encode this file, one byte repeated 262,144 times, as a single match
longer than 65,535 bytes. Their Z80 decoders keep the length in 16 bits, so
the output goes wrong from byte 65,536. salvador writes the same ZX0 format
without such a match, so its stream decodes.

**What banking costs.** On the files that both modes ran, banked time minus
flat time comes to:
- LZ formats: within 0.03%.
- upkr: +1.6%.
- Shrinkler: +0.8%.

The ring checks and window shifts are almost free for the LZ formats, because
those decoders spend most of their time waiting for the line anyway. The
banked chunked run (`harness/results-chunked-banked/`) is within 0.1 s of the
flat chunked one. Sending whole files saves about 0.4 s over 16 KB pieces,
because the pieces lose matches across their boundaries.

### What the simulation changed

- **The estimate was right for LZ formats on executables**: within 0.1%,
  because they are limited by the line and the line is easy to model.
- **The estimate was far too pessimistic for the slow decoders on
  bitmaps.** It used average T-states per output byte, but long runs decode
  much faster than average. upkr's 150 s estimate became 23 s, and
  Shrinkler's 465 s became 60 s.
- **On bitmaps, salvador and Exomizer move ahead of LZ4.** The estimate
  favoured LZ4 because its decoder is fastest. Simulated, the smaller
  streams win anyway.

## Pros and cons

| Scheme | Pros | Cons |
|---|---|---|
| **No compression** | Nothing to run on the Z80; works for any size | 10× slower overall: 195.6 s against about 18 s. Loses on every file, even the 128-byte `v00.vbp` (0.067 s against 0.012 s) |
| **salvador (ZX0)** | Top group on both file types; best on bitmaps. Compresses all 12 files in 3.8 s. Decodes the 256 KB file whole. Same format as zx0, so it can use any ZX0 decoder | Ratio 0.5 points behind Exomizer and 1.7 behind RIP on `.COM`. Offsets reach only 32,640 B back. dzx0_mega is the largest decoder (673 B; smaller variants exist) |
| **zx0** | Identical streams and times to salvador on most files | 60× slower to compress (225 s). Its `v80000.vbm` stream holds a match too long for the Z80 decoder |
| **Exomizer 3.1** | Fastest complete banked total (18.14 s); second on `.COM`. Mature, with a C streaming decoder | Decoder is about 3.5× slower per byte than ZX0 and builds a 156-byte table first. Little margin left at faster baud rates. Raw `-P39` settings must match the decoder build |
| **RIP** | Best `.COM` ratio and time (16.59 s, 3% ahead of salvador) | Compressor fails on two bitmaps: a timeout, and an estimated 53 GB of memory for 256 KB. Slowest LZ decoder. Compressor under a custom licence and Z80 decoder with no licence (see the research) |
| **aPLib (OaPACK / apultra)** | Times level with ZX0; apultra compresses fast | OaPACK takes at most 64 KB and times out on `v48000.vbm`. apultra's `v80000.vbm` stream has a match too long for the decoder |
| **LZSA2** | Small decoder (207 B); fast compressor | 3–4% bigger output than ZX0. Raw blocks stop at 64 KB, and the stream format has no Z80 decoder here |
| **LZ4** | Smallest and fastest decoder (87 B). Very fast compressor. Decodes `v80000.vbm` whole | Worst ratio: 20.36 s on `.COM`, against about 17 s for the others |
| **upkr** | Best ratio (54.8% on `.COM`) | The Z80 is the limit on every file: 3–4 s slower than the LZ group. The length limit hits `v80000.vbm` |
| **Shrinkler** | Second-best ratio | About 3× slower than everything else (57 s on `.COM`). No host decompressor. Same length limit |

## Recommendations

1. **Use salvador (ZX0 format) as the single format.** It is in the top group
   on executables and first on bitmaps. It compresses the whole corpus in
   under 4 s and decodes the 256 KB file whole. Exomizer is the only other
   top-group format that does both, and its lead on the complete banked total
   is 0.09 s over 375 KB, less than 0.5%. That doesn't outweigh salvador's
   decoder, which needs about 3.5× fewer T-states per byte. That keeps the
   most margin if the baud rate goes up.
2. **Decode while receiving, into banked memory, with RTS/CTS.** The banked
   ports show the costs are negligible:
   - an 8 KB ring, with each byte checked before it is read;
   - a window shift every 16 KB of output.

   The LZ decoders finish within 0.03% of the flat simulation's times. Send
   files whole rather than in 16 KB pieces.
3. **Use Exomizer if the last half-second on executables matters more than
   decoder simplicity.** On `.COM` files it is 0.14 s ahead of salvador
   (0.8%). RIP is 0.5 s ahead (3%), but don't build on it: its compressor fails
   on large or highly repetitive files, and its licensing is unclear.
4. **Don't use upkr, Shrinkler or LZ4 for this link.** The first two compress
   best but the Z80 can't keep up, and LZ4 keeps up easily but sends about
   20% more bytes.
5. **Picking a format per file isn't worth it.** The best per-file choice
   (RIP on `.COM`, salvador or apultra on bitmaps) saves under 0.6 s across the
   corpus compared with salvador everywhere. It would mean several decoders on
   the target.
6. **Always compress, even small files.** No file in the corpus is faster
   uncompressed. With a real frame header of a few bytes this still holds for
   everything but files of a few dozen bytes. Rerun `bench.py --overhead N` to
   check against your actual framing.
7. **If you use zx0 instead of salvador,** keep data with very long runs below
   64 KB per stream, or give the decoder a length counter wider than 16 bits.
   Otherwise you hit the `v80000.vbm` mismatch.

## Caveats

- **Simulated, not measured.** The emulator counts cycles exactly. The line
  model, though, assumes:
  - a perfect 19200 baud 8N1 stream;
  - a fixed 150 T-state receive interrupt;
  - no framing or protocol overhead.

  Rerun `linesim.py` with `--baud`, `--cpu-hz` and `--rx-cost` to match
  hardware once you have measured it.
- **The corpus is small:** 12 files, and the bitmaps are very repetitive. Data
  with more texture will separate the formats by ratio more than this corpus
  does.
- **Banked mode has two limits:** at most 32 KB of history, and packed pieces
  of at most 64 KB. Nothing in this corpus exceeds either; any offset beyond
  the window would show up as a `mismatch`.
- Full method, options and limits: `harness/README.md`.
