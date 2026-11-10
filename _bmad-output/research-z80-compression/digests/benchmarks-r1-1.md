# Digest: published Z80 compressor benchmarks (r1-1)

Accessed 2026-11-08. Sources read: about 11 (LZSA, salvador, apultra, ZX0, upkr READMEs; uniabis/z80depacker README; introspec's HYPE 2017 article; spectrumcomputing ZX0 thread; msx.org ZX0 thread p.2; LZ4-legacy-CPU repost; one search summary). cpcwiki.eu returned HTTP 403 / Cloudflare challenge, so introspec's 2021 ZX0 comparison post was not read.

## Table A: uniabis/z80depacker (independent measurement; README pushed 2026-09-30)

Corpus: DEOCM-PLD-CV MSX BIOS ROM, 16 x 16 KB = 262,144 bytes (binary code, fonts and data, not text). Clocks are total "unpacking clocks" over all 16 blocks. LDIR baseline works out to 23.0 clocks/byte, not the 21 T of a bare Z80. That points to MSX M1 wait states being included (my inference). Bare-Z80 figures would be roughly 5-10% lower.

T/out = clocks / 262,144. T/in = clocks / packed size. Both columns are my arithmetic from the README rows. "Fastest" is the fastest listed depacker for that format.

| Format (packer version) | Packed (ratio) | Fastest depacker (bytes) | clk/out | clk/in | Smallest/slowest depacker clk/out |
|---|---|---|---:|---:|---:|
| LZ4 (lz4ultra 1.3.0) | 191,240 (72.95%) | unlz4_spke_fast (87) | 36.8 | 50.5 | 40.6 (lz4dec, 73 B) |
| LZSA1 (1.4.1 -f1 -r) | 182,295 (69.54%) | unlzsa1_fast (113) | 36.9 | 53.1 | 43.1 (small, 67 B) |
| LZSA2 (1.4.1 -f2 -r) | 167,410 (63.86%) | unlzsa2_fast (207) | 59.0 | 92.4 | 69.7 (small_rom, 132 B) |
| ZX1 (1.5) | 167,204 (63.78%) | dzx1_mega (406) | 63.6 | 99.6 | 85.0 (standard, 68 B) |
| ZX0 (2.2) | 162,051 (61.82%) | dzx0_mega (673) | 73.9 | 119.5 | 104.4 (standard, 68 B); turbo 81.7 |
| ZX7 | 170,296 (64.96%) | dzx7_lom_v1p1 (199) | 84.0 | 129.2 | 140.0 (standard) |
| MegaLZ 4.89 | 167,609 (63.94%) | unmegalz_fast (229) | 75.8 | 118.6 | 174.2 |
| Pletter 0.5c1 | 167,494 (63.89%) | unpletter_fast_180 (160) | 98.9 | 154.7 | 109.9 |
| aPLib (OaPACK 2026.04.09) | 161,422 (61.58%) | unaplib_fast (235) | 112.0 | 181.8 | 225.7 (aplib156b) |
| Hrust 1.3 (oh1c) | 162,217 (61.88%) | dehrust_stk_208b | 157.5 | 254.5 | 173.6 |
| Exomizer 3.1.2 raw -P7 | 159,965 (61.02%) | deexoopt_x3_p7 (212) | 232.1 | 380.4 | 309.4 |
| Exomizer 3.1.2 raw -P39 | 159,597 (60.88%) | deexoopt_x3_p39 (244) | 254.9 | 418.7 | 280.7 |
| Exomizer 3.1.2 raw -P0 | 159,966 (61.02%) | deexoopt_x3_p0 (235) | 272.1 | 445.9 | 411.6 (deexo) |
| RIP 2023.03.30 | 154,535 (58.95%) | derip_fast (231) | 503.8 | 854.7 | 589.0 |
| upkr 0.2.2 --z80 | 151,192 (57.68%) | unpack_upkr_z80_minusquare (702) | 3,204.6 | 5,556.3 | 4,339.1 (size_rom, 169 B); speed (195 B) about 3,674 |
| Shrinkler 4.7 -d -9 | 153,136 (58.42%) | deshrinkler_p1 (210) | 9,942.7 | 17,020.3 | 10,102.1 |
| Shrinkler -b (no parity) | 152,298 (58.10%) | deshrinkler_np_p1 (204) | 9,927.9 | 17,088.4 | 10,085.1 |

## Table B: introspec, "Сжатие данных для современного кодинга под Z80", HYPE, 2017-09-08 (independent of most of the tools; introspec later wrote the LZSA Z80 decoders)

Corpus: zxcorpus2017, 1,233,995 bytes (calgary 8, canterbury 5, 30 Spectrum graphics, 24 music, 10 mixed ZX; files over 64 KB excluded). Speed was measured only on the graphics and music subsets, in T-states per unpacked byte. Reference: 7-Zip 550,330, RAR4 559,575.

| Packer | Packed (ratio) | T/out byte | Decoder size |
|---|---|---:|---|
| Exomizer (2.x era) | 596,161 (48.31%) | 248 (deexo_plus), 287 (deexo) | 169/174 |
| aPLib | 606,833 (49.18%) | 100-165 | 156-247 |
| Hrust 1 | 613,602 (49.72%) | 120-132 | 209/234 |
| PuCrunch | 616,855 (49.99%) | 301 avg | 255 |
| Hrust 2 | 620,823 (50.31%) | 127 | 212 |
| Pletter 5 | 635,797 (51.52%) | 75 | 170 |
| MegaLZ | 636,910 (51.61%) | ~131 | 110 |
| Hrum | 641,658 (52.00%) | 97 | 104 |
| ZX7 | 653,879 (52.99%) | 69 (lom_v1) to 107 (standard) | 69-244 |
| LZ4 | 722,522 (58.55%) | 33 (spke) | 72-251 |

## Table C: author claims about their own tools

| Tool | Claim | Source |
|---|---|---|
| LZSA (E. Marty) | ZX Spectrum + C64 workload, 1,289,127 bytes: LZSA2 676,681 (52.49%), 75% of LZ4 speed; ZX7 687,133 (53.30%), 47.73%; LZSA1 735,785 (57.08%), 90%; LZ4_HC 781,049 (60.59%), 100%; MegaLZ 4.89 679,041 (52.68%). Calgary: LZ4_HC 38.40%, LZSA1 36.67%, LZSA2 34.15%. Compressor about 2x slower than LZ4_HC. | LZSA README |
| ZX0 (E. Saukas) | Decoders: standard 68 B; turbo 126 B, ~21% faster; fast 187 B, ~25% faster; mega 673 B, ~28% faster. Optimal compression "can take a few seconds" on typical files; `-q` quick mode is near-instant but non-optimal. ZX1: ~1.5% worse ratio, ~15% faster. | ZX0 README |
| salvador (E. Marty) | Output within 0.02% of zx0 on average, "several orders of magnitude faster", compression speed similar to apultra. 31.5 KB window, so files over 128-256 KB compress better with apultra. Z80 decoder needs `-classic` (v1 format). | salvador README |
| apultra (E. Marty) | 5-7% smaller than appack. vmlinux example: appack 26.39%, gzip 25.66%, apultra 1.4.1 24.75%. | apultra README |
| upkr (exoticorn) | Ratio "on par with Shrinkler"; Z80 unpacker "about twice as fast and smaller than the Shrinkler unpacker". Use `--z80` preset. | upkr README |

## Findings

1. **Claim:** In the largest independent Z80 table found, ratio order on 256 KB of MSX ROM is upkr 57.68% < Shrinkler 58.1-58.4% < RIP 58.95% < Exomizer 60.9-61.0% < aPLib 61.58% < ZX0 61.82% < LZSA2 63.86% < ZX7 64.96% < LZSA1 69.54% < LZ4 72.95%. The spread from best to worst LZ is about 15 points. **Source:** https://github.com/uniabis/z80depacker (README) · uniabis · last pushed 2026-09-30 · accessed 2026-11-08 · **confidence:** high (for this corpus) · **class:** performance
2. **Claim:** Measured decode cost (MSX clocks per output byte, fastest decoder): LZ4 36.8, LZSA1 36.9, LZSA2 59.0, ZX1 63.6, ZX0 73.9 (mega) / 81.7 (turbo) / 104.4 (standard), aPLib 112, Exomizer 3 232-272, RIP 504, upkr 3,205, Shrinkler 9,928. **Source:** same · **confidence:** high · **class:** performance
3. **Claim:** The baseline works out to LDIR = 23 clocks/byte, so the clock counts include MSX M1 wait states (inferred). Bare 8 MHz Z80 figures should be a few percent to ~10% lower. **Source:** same, my arithmetic · **confidence:** medium · **class:** performance
4. **Claim:** Per *input* byte, upkr's fastest Z80 decoder costs ~5,556 clocks and Shrinkler ~17,000. Both exceed the ~4,100 T-state per received byte budget at 19200 baud on an 8 MHz Z80 (Shrinkler by ~4x), so on this corpus they would be decode-bound, not link-bound. Every LZ format in the table costs under 900 clocks per input byte, Exomizer under 450, ZX0 under 120. **Source:** same, my arithmetic against the brief's budget · **confidence:** medium (depends on corpus; on more compressible data, entropy decoders spend more time per input byte) · **class:** performance
5. **Claim:** Worked example on that corpus (my arithmetic, 1,920 B/s, 8 MHz, ignoring wait-state correction): ZX0 is 84.4 s link time against 2.4 s decode; Exomizer -P39 is 83.1 s link against ~8.4 s decode; upkr is 78.7 s link against ~105 s decode (fastest 702 B decoder) or ~142 s (169 B decoder); Shrinkler is 79.8 s link against ~326 s decode; LZ4 is 99.6 s link. Raw transfer is 136.5 s. On this data, the best-ratio LZ formats beat the entropy coders on total time. **Source:** derived from finding 1 and 2 numbers · **confidence:** medium · **class:** performance
6. **Claim:** In 2017, on a mixed Spectrum corpus including Calgary/Canterbury text, Exomizer had the best ratio (48.31%) at 248-287 T/byte, aPLib 49.18% at 100-165, ZX7 52.99% at 69-107, and LZ4 58.55% at 33 T/byte. ZX0, LZSA, upkr and Shrinkler are not in this table. **Source:** https://hype.retroscene.org/blog/dev/740.html · introspec · 2017-09-08 · **confidence:** high (old versions) · **class:** performance / corpus
7. **Claim:** On text-heavy data, gaps between formats are larger than on ROM data. LZSA's README reports Calgary at LZ4_HC 38.40%, LZSA1 36.67%, LZSA2 34.15%, but has no ZX0/Exomizer text numbers. **Source:** https://github.com/emmanuel-marty/lzsa (README) · Emmanuel Marty · undated README · **confidence:** medium (author's claim) · **class:** performance / corpus
8. **Claim:** LZSA author's relative speeds: LZSA1 90%, LZSA2 75%, ZX7 47.73% of LZ4 decode speed. This is consistent with uniabis (LZSA1 ≈ LZ4; LZSA2 at 62% of LZ4 speed, which is slower than the author claims). **Source:** LZSA README vs z80depacker · **confidence:** medium · **class:** performance
9. **Claim:** ZX0 author: optimal compression "can take a few seconds" on typical files, with `-q` for near-instant non-optimal output. Users reported zx0 v1.5 "TO SLOW" on big files (Oct 2021). The author replied that it is slower with many matches, and his test "took under a minute". ZX5 runs took 84 h (then out of memory) and 150 min in quick mode on a 33,887-byte file. **Source:** ZX0 README; https://www.spectrumcomputing.co.uk/forums/viewtopic.php?p=81584 · Einar Saukas / forum users · 2021-10-14 to 2021-10-20 · **confidence:** medium (anecdotal, no hardware stated) · **class:** performance
10. **Claim:** salvador produces ZX0 output within 0.02% of zx0 and is "several orders of magnitude faster". The Z80 decoders in the salvador repo need `-classic` (ZX0 v1 format). **Source:** https://github.com/emmanuel-marty/salvador · Emmanuel Marty · undated · **confidence:** medium (author's claim) · **class:** performance / version
11. **Claim:** A SliTaz package backed out a "salvador 10% speedup" because it "crashes with large files" (Jan 2024). **Source:** https://repos.slitaz.org/wok/file/70750875bc0b/salvador/receipt (via search snippet only, not opened) · SliTaz · 2024-01 · **confidence:** low · **class:** version
12. **Claim:** upkr author: Z80 unpacker is about 2x faster than Shrinkler's. Measured independently: 3,205-4,339 vs 9,928-10,102 clocks/out, so 2.3-3.1x. The claim holds. **Source:** https://github.com/exoticorn/upkr + z80depacker · exoticorn / uniabis · **confidence:** high · **class:** performance
13. **Claim:** Versions used by the independent table: ZX0 2.2, ZX1 1.5, LZSA 1.4.1, lz4ultra 1.3.0, Exomizer 3.1.2, Shrinkler 4.7, upkr 0.2.2, OaPACK 2026.04.09, RIP 2023.03.30, Pletter 0.5c1, MegaLZ 4.89. **Source:** z80depacker README · **confidence:** high · **class:** version
14. **Claim:** A 2021 msx.org points ranking (Metalion, 2021-09-26), built from z80depacker data and weighting decoder size, ratio and speed, put dzx0_standard first (213/300), then the ZX1 variants and unlzsa1_small. It optimises for ROM/game use, not link throughput. **Source:** https://msx.org/fr/node/62438?page=2 · Metalion · 2021-09-26 · **confidence:** medium · **class:** performance

## Leads worth chasing

- introspec/spke's 2021 ZX0 comparison at https://www.cpcwiki.eu/forum/programming/new-cruncher-zx0/msg197727/ was blocked by Cloudflare (403). Try a browser fetch or the Wayback Machine. It likely has ZX0 vs LZSA2/aPLib/Exomizer on zxcorpus with T/byte numbers.
- z80depacker has per-file tables (16 blocks with ratios from 25% to 94%). Use them to fit decode cost as a function of compressibility, especially for upkr and Shrinkler on the most compressible blocks (KANJJ1.B01 at ~24%).
- None of the measured decoders streams input. They are memory-to-memory. A streaming ZX0/LZSA decoder needs a byte-fetch hook (C. Masloch's "streamed LZSA2 depacker" is linked from the LZSA README: https://hg.ulukai.org/ecm/inicomp/file/c1a1f9bd4382/lzsa2.asm).
- No benchmark used a CP/M-typical text/COM corpus. Run the uniabis harness, or just compress representative files with each packer, to get ratios on real payloads.
- The LZSA README's Pareto chart (pareto_graph.png) was not viewed as an image. It may contain introspec's numbers for ZX0/LZSA/aPLib/Exomizer.
- ivagorRetrocomp DeUpkr / DeShrinkler (8080/R800 ports) may carry timing notes.
- bumbershootsoft 2026-05-09 "Comparing an LZ4 decompressor on four legacy CPUs": the original might have cycle counts. The repost had none.

## Looked for, could not find

- Introspec's 2021 ZX0 comparison numbers (source blocked).
- Exomizer, Shrinkler, upkr host compression times as measured numbers (only qualitative claims found).
- salvador vs zx0 measured host timings (only the author's "orders of magnitude" claim).
- Any Z80 benchmark on 8 MHz hardware or on a CP/M text corpus. All numbers are clock counts on MSX/Spectrum-style data.
- A 2022-2026 blog post with fresh comparison tables, other than the continuously updated z80depacker README.
