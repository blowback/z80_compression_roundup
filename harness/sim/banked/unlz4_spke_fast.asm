; -----------------------------------------------------------------------------
; Banked port of unlz4_spke_fast.asm (LZ4 decompressor by spke, v.1
; 28/08/2017) from uniabis/z80depacker, for harness/linesim --mode banked.
; Altered source: only input reads, output writes, copies and buffer
; addresses changed (harness/sim/banked/banked.inc has the helpers). The
; build configuration of the flat run is kept: ALLOW_LDIR_UNROLLING on,
; DATA_HAS_HEADERS and ALLOW_UNCOMPRESSED off (raw block); the unused
; IFDEF branches are dropped.
;
; Of course, LZ4 decompression algorithm is (c) 2011-2015, Yann Collet.
;
; Edit sites (input pointer HL in the ring, counter IYL):
;   1. ReadToken: token read twice -> one check before both reads.
;   2. ReadLongNumber: ld c,(hl) -> RCHK_HL.
;   3. CopyLiterals: ldir -> copy_lit.
;   4. NoLiterals: sub (hl) -> RCHK_HL first (keeps carry for sbc).
;   5. NoLiterals: sbc (hl) -> RCHK_HL first; carry from sub (hl) survives
;      the check (dec iyl and refill keep carry).
;   6. ReadMatchLen: ld c,(hl) -> RCHK_HL.
;   7. CopyMatch: ldi x3 + ldir -> copy_match (same bytes, split at 0xC000:
;      the length is >= 4 here, and BC = 0 copies 65,536 either way).
; -----------------------------------------------------------------------------

        INCLUDE "banked.inc"

banked_decoder:
DecompressLZ4:
		ld b,0								; B is assumed to zero throughout, which saves on clearing it

DecompressRaw:

		jr ReadToken							; a small price to pay to getting rid of a JP per every short match!

ShortMatch:
CopyMatch:
		ld c,a : ex (sp),hl : ex hl,de					; BC = len, DE = dest, HL = -offset, SP ->[src]
		add hl,de							; BC = len, DE = dest, HL = dest+-offset, SP->[src]
		call copy_match							; BANKED (7): was ldi : ldi : ldi : ldir
		pop hl								; BC = 0, DE = dest, HL = src

ReadToken:	; first a byte token "LLLLMMMM" is read from the stream,
		; where LLLL is the number of literals and MMM is
		; a length of the match that follows the literals
		RCHK_HL								; BANKED (1)
		ld a,(hl) : exa : ld a,(hl) : inc hl				; token is read twice to be re-used later
		and #F0 : jr z,NoLiterals

		; we definitely have literals to copy
		rrca : rrca : rrca : rrca : cp 15 : jr nz,CopyLiterals		; LLLL<15 means 0..14 literals...

		; LLLL=15 indicates a multi-byte number of literals
ReadLongNumber:	RCHK_HL								; BANKED (2)
		ld c,(hl) : inc hl : add c : jr c,NumberOverflow		; overflow does not happen often, hence the ugly branching
NumberContinue:	inc c : jr z,ReadLongNumber

CopyLiterals:	ld c,a : call copy_lit						; BANKED (3): was ldir

		; second, a two-byte offset value is given
		; since every compressed block must end with 5 literals,
		; or more, and since after the end of the block the end
		; marker is given, which is 4 zero bytes, we can use
		; zero offset as a marker of the end of the block
		; (see https://github.com/lz4/lz4/wiki/lz4_Block_format.md)
		xor a
NoLiterals:	push de								; SP -> [dest]
		RCHK_HL								; BANKED (4)
		sub (hl) : inc hl : ld e,a
		RCHK_HL								; BANKED (5): keeps carry
		sbc (hl) : sub e : ld d,a					; DE = -offset
		or e : jr z,BlockEnd						; ugly branching is optimized for speed
		inc hl

		; this is optimized for shorter matches, because
		; they are likely to be a lot more common
ProcessMatch:	exa : and #0F : add 4 : cp 15+4 : jp c,ShortMatch		; MMMM+4<15+4 means match lengths between 0+4 and 14+4

		; MMMM=15 indicates a multi-byte length of the match
LongerMatch:
ReadMatchLen:	RCHK_HL								; BANKED (6)
		ld c,(hl) : inc hl : add c : jr c,MatchIncB			; overflow does not happen often, hence the ugly branching
MatchContinue:	inc c
		jr nz,CopyMatch
		jp ReadMatchLen

NumberOverflow:	inc b : jp NumberContinue

MatchIncB:	inc b : jp MatchContinue

BlockEnd:	pop de : ret
