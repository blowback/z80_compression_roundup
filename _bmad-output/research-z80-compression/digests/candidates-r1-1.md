# Z80 compression candidates: digest r1-1

Accessed 2026-11-08. Primary benchmark source: uniabis/z80depacker readme (corpus: 16 x 16 KB MSX BIOS ROM banks = 262,144 bytes; decoders timed memory-to-memory in T-states, reported as a multiple of LDIR). The corpus is binary ROM code (~60% ratio). It is not CP/M text, so ratios on the real workload will differ.

## Per-candidate table

| Candidate | Canonical repo / latest release or commit | License | Z80 decoders (variants, author) | Direction | Max offset / window | Maintenance | Better/alt compressor | Packed size on corpus | Fastest Z80 decode (LDIR x) |
|---|---|---|---|---|---|---|---|---|---|
| ZX0 (v2 format) | github.com/einar-saukas/ZX0, no tags; last commit 2021-12-17, uniabis lists v2.2 | BSD-3 (compressor); decoders free with attribution | z80/dzx0_standard (68 B), turbo (126 B), fast (187 B), mega (673 B), plus _back variants for standard/turbo/mega; OLD_V1 dir. Einar Saukas; fast by introspec/spke + uniabis | Forward and backward (`-b`) | 32,640 (MAX_OFFSET_ZX0 in src) | Dormant since 2021 (repo pushed 2023-09) | salvador (fast, near-optimal) | 162,051 (61.82%) | 3.21 (mega), 4.53 (standard) |
| ZX0 v1 "classic" | same, `-c` flag | same | z80/OLD_V1 | fwd/back | same | legacy | — | — | — |
| salvador | github.com/emmanuel-marty/salvador, 1.4.2 (2023-03-23) | Zlib (+ CC0 matchfinder) | asm/Z80/unzx0v1_fast.asm only (spke, uniabis); README says use `-classic` for Z80 | — | 31.5 KB | Dormant since 2023-03 | — (within 0.02% of zx0 output) | ~ZX0 | ~ZX0 |
| ZX7 | spectrumcomputing.co.uk entry 27996 (no GitHub repo found) | not verified | dzx7_standard (69 B), turbo, lom_v1(p1); zx7b by Antonio Villena (CC-BY-SA) | zx7b is backward | 2,176 (MAX_OFFSET_ZX7 in ZX0 src) | Superseded by ZX0 (author's words) | ZX0 | 170,296 (64.96%) | 3.65 |
| ZX1 | github.com/einar-saukas/ZX1, last push 2022-10-25 | BSD-3 | dzx1_standard/turbo/mega | not checked | not checked | dormant | — | 167,204 (63.78%) | 2.76 (mega) |
| ZX2 | github.com/einar-saukas/ZX2, last push 2021-11-16 | BSD-3 | dzx2_nano | not checked | not checked | dormant; for very small files | — | 182,390 (69.58%) | not extracted |
| ZX5 | github.com/einar-saukas/ZX5, last push 2023-08-08 ("experimental") | BSD-3 | dzx5_standard/turbo/turbo_rom | not checked | not checked | dormant | — | 162,096 (61.83%) | 4.02 |
| LZSA1 | github.com/emmanuel-marty/lzsa, 1.4.1 (2023-02-27), last push 2023-12-24 | Zlib (+ CC0 matchfinder) | asm/z80 unlzsa1_fast (113 B) / small (67 B), introspec, optimizations by uniabis | Forward; `-r` raw/backward used in benchmark (backward not verified) | 64 KB | Dormant since 2023 | — | 182,295 (69.54%) | 1.60 |
| LZSA2 | same | same | unlzsa2_fast (207 B) / small (134 B) | same | 64 KB (5/9/13/16-bit offsets) | same | — | 167,410 (63.86%) | 2.56 |
| Exomizer 3 | bitbucket.org/magli143/exomizer, tag 3.1.2 (2023-11-25) | LGPL (per uniabis) | deexo3p7, deexoopt_p7 (Antonio Villena's deexo), uniabis variants | not verified | not verified | 3.1.2 is latest; uniabis added Exomizer 3.1.2 depacker 2026-09-30 | — | 159,597 (60.88%, -P39) | 10.49 (-P7) |
| aPLib / apultra | apultra: github.com/emmanuel-marty/apultra, 1.4.8 (2022-10-11), push 2023-05; aPLib: ibsensoftware.com | apultra Zlib | apultra asm/Z80 unaplib_fast (235 B, spke & uniabis), unaplib_small (139 B) | not verified | not verified | apultra dormant; OaPACK active | OaPACK (gitlab.com/eugene77/oapack, exhaustive optimal, active 2026-04-18) | 161,422 (61.57%, OaPACK) | 4.86 |
| upkr | github.com/exoticorn/upkr, v0.2.2 (2023-08-25), push 2025-07-16 | Unlicense | repo z80_unpacker (Ped7g, size-focused); uniabis unpack_upkr_z80_size/speed, minusquare (702 B), difave (720 B, 2026-09-29) | Forward and backward (compile option) | unlimited by default (`max_offset: usize::MAX`), `--max-offset N` | Low activity; Z80 format "may change" | — | 151,192 (57.68%, `--z80`) | 139.3 (minusquare) |
| Shrinkler | github.com/askeksa/Shrinkler, v4.7 (2022-02-22); Z80 port on cpcrulez.fr (author not verified) | not verified (NOASSERTION on GitHub) | deshrinkler, deshrinkler_np (uniabis), shrinkler_recall_209 | not verified | not verified | Amiga-focused | upkr (2x faster, similar ratio per upkr README) | 152,298 (58.10%, -b) | 431.6 |
| Pletter | xl2s.tk (v0.5c1) | MIT (per uniabis) | unpletter (170 B), unpletter_fast_180 (2026-09-29) | not verified | not verified | old | — | 167,494 (63.89%) | 4.77 |
| MegaLZ | v4.89; unmegalz at hype.retroscene.org/blog/933.html | not verified | unmegalz_fast (229 B) / small (88 B), megalz_dec40 | not verified | not verified | old | — | 167,609 (63.94%) | 3.29 |
| LZ4 | lz4ultra github.com/emmanuel-marty/lz4ultra 1.3.0 (2020-05-31); smallz4 github.com/stbrumme/smallz4 (MIT, push 2020-04) | lz4ultra NOASSERTION on GitHub | unlz4_spke / _fast / _small, lz4dec | Forward | 64 KB (LZ4 format, not verified this run) | dormant | LZSA1 replaces it (LZSA README) | 191,240 (72.95%) | 1.60 |
| hrust (hruST/oh1c) | gitlab.com/eugene77/optimal-hrust-compressor; mhmt github.com/lvd2/mhmt (GPLv3) | mhmt GPLv3 | dehrust_ix/hl/stk, dehrust8_hl | not verified | not verified | old | — | 162,217 (61.88%) | 6.34 |
| RIP (screened, new) | gitlab.com/eugene77/rip, v2023.03.30, last activity 2023-03-31 | custom license (compressor); Z80 decoders unlicensed | z80/ dir; uniabis derip_fast (231 B) | not verified | `--max-offset` option; default not verified | dormant | mRIP (simplified, 2022) | 154,535 (58.95%) | 21.90 |
| LZEe (screened) | github.com/uniabis/lzee v0.0.4 | not checked | dlzeee_fast3 | not verified | not verified | — | — | 174,118 (66.42%) | 2.50 |
| Bitbuster / Bitbuster2 | github.com/abekermsx/BitBuster-1.2, Bitbuster-2 | MIT (Bitbuster) | several | not verified | not verified | old | — | 172,080 / 171,783 | not extracted |

## Derived throughput check (my arithmetic, not a source claim)

From uniabis absolute timings: aPLib fast = 29,350,495 T at LDIR x 4.86, so 1 LDIR unit on this corpus is about 6.04 M T. At 8 MHz with no wait states, and with the serial link at 1,920 B/s:

| Format | Serial time for packed corpus | Z80 decode time | Which limits |
|---|---|---|---|
| RIP | 154,535 / 1920 = 80.5 s | 21.9 x 6.04 M = 132 M T = 16.5 s | serial |
| Exomizer 3.1 | 83.1 s | ~63 M T = 7.9 s | serial |
| aPLib (OaPACK) | 84.1 s | 3.7 s | serial |
| ZX0 | 84.4 s | 2.4 s (mega) | serial |
| upkr --z80 | 78.7 s | 139.3 x 6.04 M = 841 M T = 105 s | **CPU** (upkr consumes ~1,440 B/s, below the 1,920 B/s link) |
| Shrinkler | 79.3 s | ~2.6 G T = 326 s | **CPU** |
| Uncompressed | 136.5 s | 0 | serial |

So on this corpus, the entropy-coded packers (upkr, Shrinkler) are too slow for an 8 MHz Z80 to keep up with 19200 baud, and the best LZ+Huffman/LZ formats with fast decoders (RIP, Exomizer, aPLib, ZX0) fall within about 5% of each other on total time. Caveats: memory-to-memory timings exclude per-byte serial-receive overhead; real hardware wait states are unknown; text data compresses better, which shortens serial time and makes decoder speed matter more.

## Findings

1. ZX0 format changed in v2 (smaller, slightly faster decoders); `-c` produces v1 "classic". Source: https://github.com/einar-saukas/ZX0 (README), Einar Saukas, last commit 2021-12-17, accessed 2026-11-08, high, format.
2. ZX0 Z80 decoders: standard 68 B, turbo 126 B (~21% faster), fast 187 B (~25%), mega 673 B (~28%). Backward variants exist for standard/turbo/mega. Source: ZX0 README + z80/ dir listing, Einar Saukas, 2021-12-17, high, format.
3. ZX0 max offset 32,640; ZX7 2,176 (constants in src/zx0.c). Source: https://raw.githubusercontent.com/einar-saukas/ZX0/main/src/zx0.c, 2021, high, format.
4. ZX0 compressor BSD-3; decoders free with attribution. Source: ZX0 README, high, license.
5. ZX7 is "now superseded by ZX0" (the author's own words); ZX1 trades ~1.5% ratio for ~15% speed; ZX5 is experimental. Source: ZX0 README, high, maintenance.
6. ZX0 repo has no release tags; last commit 2021-12-17; uniabis labels it v2.2. Source: `gh api repos/einar-saukas/ZX0/commits`, high, version/maintenance.
7. salvador 1.4.2 (2023-03-23), Zlib, within 0.02% of zx0 size, 31.5 KB window, defaults to V2; bundled Z80 decoder is v1-only ("use -classic for Z80"). Source: https://github.com/emmanuel-marty/salvador, Emmanuel Marty, 2023-03-23, high, version/license/format.
8. LZSA 1.4.1 (2023-02-27), Zlib; 64 KB blocks and back-references; Z80 decoders by introspec with uniabis optimizations; LZSA1 is meant to replace LZ4, LZSA2 to replace ZX7. Source: https://github.com/emmanuel-marty/lzsa, 2023, high, version/license/format.
9. apultra 1.4.8 (2022-10-11), Zlib, 5-7% smaller than appack; Z80 unaplib_fast 235 B by spke & uniabis. Source: https://github.com/emmanuel-marty/apultra, 2022, high, version/license.
10. OaPACK (exhaustive optimal aPLib packer) is active, last activity 2026-04-18; uniabis uses OaPACK 2026.04.09 for its aPLib row. Source: gitlab.com/eugene77/oapack API + uniabis readme, Eugene Larchenko, 2026-04, high, maintenance.
11. upkr v0.2.2 (2023-08-25), Unlicense; ratio on par with Shrinkler; Z80 unpacker about twice as fast as Shrinkler's; `--z80` preset; Z80 format "may change"; forward and backward supported; max_offset unlimited by default. Source: https://github.com/exoticorn/upkr (README, z80_unpacker/readme.txt, src/lib.rs), Dennis Ranke (exoticorn) / Ped7g, 2023-08/2025-07, high, version/format.
12. Exomizer official home is bitbucket.org/magli143/exomizer; latest tag 3.1.2 (2023-11-25). Source: Bitbucket refs API; bitshifters/exomizer README, high, version.
13. Corpus sizes (262,144 B): upkr 57.68%, Shrinkler -b 58.10%, RIP 58.95%, Exomizer3.1 60.88%, aPLib/OaPACK 61.57%, ZX0 61.82%, hrust 61.88%, LZSA2 63.86%, Pletter 63.89%, MegaLZ 63.94%, ZX7 64.96%, LZSA1 69.54%, LZ4 72.95%. Source: https://github.com/uniabis/z80depacker readme.md, uniabis, commit 2026-09-30, high, performance.
14. Decode speed (LDIR multiples): LZSA1 1.60, LZ4 1.60, LZSA2 2.56, ZX0 mega 3.21, MegaLZ fast 3.29, aPLib fast 4.86, hrust8 6.34, Exomizer deexoopt 10.49, RIP 21.9, upkr 139.3-188.5, Shrinkler ~432. Source: same, high, performance.
15. RIP (LZ + Huffman + offset reuse) by Eugene Larchenko, v2023.03.30; the compressor needs up to 4N^2 bytes of RAM and "is unlikely to swallow files larger than 100 kB"; Z80 decoders need a 1,474-byte work area; custom license, decoders unlicensed. Source: https://gitlab.com/eugene77/rip README, 2023-03, high, format/license.
16. uniabis repo still active: Exomizer 3.1.2 depacker 2026-09-30, upkr difave + unpletter_fast_180 2026-09-29. Source: uniabis commits API, high, maintenance.
17. A web search found no new Z80-specific compressor announced in 2025. Source: WebSearch "new Z80 compressor 2025" (results: [ZX5](https://github.com/einar-saukas/ZX5), [LZPACK](https://github.com/johnsonjh/LZPACK), [bumbershootsoft LZ4 article 2026-05-09](https://bumbershootsoft.wordpress.com/2026/05/09/comparing-an-lz4-decompressor-on-four-legacy-cpus/)), low (one shallow query), maintenance.

## Superseded

- ZX7 -> ZX0 (author's statement). ZX7 at 2,176 max offset also has a much smaller window.
- LZ4 -> LZSA1 on Z80: same decode speed (1.60x), 3.4 points better ratio.
- Shrinkler -> upkr on Z80: smaller ratio penalty, ~3x faster decoder. Both are still too slow for 19200 baud at 8 MHz on this corpus.
- appack -> apultra -> OaPACK for aPLib-format output.
- salvador is a faster equivalent of zx0, not a better one; it does not improve ratio.

## Leads worth chasing

- RIP/mRIP: best ratio among decoders fast enough for the link, but the compressor is limited to ~100 KB inputs and needs a lot of RAM. Test on real CP/M files and check the default max offset.
- Exomizer max offset and whether a forward-streaming Z80 decoder exists (deexo variants).
- Streaming-friendly decoders: all benchmarked decoders read packed data from memory via HL. Find out how cheaply each can be adapted to pull bytes from a UART ring buffer (bit-oriented formats such as ZX0, aPLib and Exomizer need a byte-fetch hook in the bit-refill path).
- Re-run uniabis's benchmark on representative CP/M payloads (text, .COM). Better ratios shift the balance toward decoder speed.
- upkr with a larger/faster Z80 decoder, or Z80 at higher clocks: it is the only format whose ratio leads by 3+ points.
- The introspec/spke comparison at cpcwiki (linked from the ZX0 README): https://www.cpcwiki.eu/forum/programming/new-cruncher-zx0/msg197727/#msg197727
- lzee/LZEe (uniabis) and the bumbershootsoft 2026 LZ4 article; not read.

## Looked for, could not find

- A ZX7 GitHub repo by einar-saukas (404); ZX7 lives at spectrumcomputing.co.uk.
- Official aPLib repo on GitHub (ibsensoftware/aplib 404); aPLib license and max offset not verified.
- Authorship and license of the Shrinkler Z80 port (cpcrulez.fr page not fetched).
- Max offset for Exomizer, aPLib, Pletter, MegaLZ, hrust, RIP default.
- Any 2022-2026 "ZX0 v3" or new Einar Saukas format beyond ZX1/ZX2/ZX5 (none seen in repo metadata).
- PuCrunch Z80 and Bitbuster windows/directions: not checked.
