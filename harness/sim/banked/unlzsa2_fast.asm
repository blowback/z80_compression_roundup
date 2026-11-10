; -----------------------------------------------------------------------------
; Banked port of unlzsa2_fast.asm (speed-optimized LZSA2 decompressor by spke
; & uniabis, ver.08p2 26/05/2022) from uniabis/z80depacker, for
; harness/linesim --mode banked.
;
;  Of course, LZSA2 compression algorithms are (c) 2019 Emmanuel Marty,
;  see https://github.com/emmanuel-marty/lzsa for more information
;
;  This software is provided 'as-is', without any express or implied
;  warranty.  In no event will the authors be held liable for any damages
;  arising from the use of this software.
;
;  Permission is granted to anyone to use this software for any purpose,
;  including commercial applications, and to alter it and redistribute it
;  freely, subject to the following restrictions:
;
;  1. The origin of this software must not be misrepresented; you must not
;     claim that you wrote the original software. If you use this software
;     in a product, an acknowledgment in the product documentation would be
;     appreciated but is not required.
;  2. Altered source versions must be plainly marked as such, and must not be
;     misrepresented as being the original software.
;  3. This notice may not be removed or altered from any source distribution.
;
; ALTERED SOURCE. Only input reads and copies changed (helpers in
; harness/sim/banked/banked.inc). Build as in the flat run: forward,
; self-modifying code, UNROLL_LONG_MATCHES off. The unused IFDEF branches
; are dropped.
;
; Reads come in two kinds. A consume read proves the byte (RCHK_HL) and
; NEXT_HL moves past it. A peek reads a byte that a later instruction reads
; again and consumes: the first touch proves it, and the NEXT_HL that
; consumes it is plain. Every path from a peek consumes it exactly once.
;
; Edit sites (input pointer HL in the ring, counter IYL), each marked
; "BANKED":
;   1.  ManyLiterals: add (hl) -> RCHK_HL first; flags come from the add.
;   2.  ManyLiterals: ld c,(hl) -> RCHK_HL.
;   3.  ManyLiterals: ld b,(hl) peek, consumed by ReadToken.NEXTHLuseBC.
;   4.  MoreLiterals: ld b,(hl) re-reads the token ReadToken proved: plain.
;   5.  MoreLiterals nibble: ld a,(hl) peek -> RCHK_HL, consumed just after.
;   6.  CopyMoreLiterals: ldi : ldi : ldir -> copy_lit (same bytes: C >= 3
;       here, and BC = 0 copies 65,536 either way).
;   7.  CASE110: ld b,(hl) -> RCHK_HL.
;   8.  NoLiterals: or (hl) re-reads the proved token: plain, flags as before.
;   9.  ReadOffsetC: ld c,(hl) -> RCHK_HL.
;   10. CopyMatch: ldi : ldir -> OCHK_HL : ldi : copy_match (same bytes for
;       every BC).
;   11. ReadToken: ld a,(hl) peek -> RCHK_HL; consumed at .NEXTHLuseBC,
;       MoreLiterals or NoLiterals.
;   12. ReadToken.NEXTHLuseBC: ldir -> copy_lit.
;   13. CASE10x nibble: peek -> RCHK_HL.
;   14. CASE00x nibble: peek -> RCHK_HL.
;   15. LongerMatch nibble: peek -> RCHK_HL.
;   16. LongMatch: add (hl) -> RCHK_HL first.
;   17. LongMatch: ld c,(hl) -> RCHK_HL_KEEPF (Z from the add is tested
;       by jr nz two reads later).
;   18. LongMatch: ld b,(hl) -> RCHK_HL_KEEPF.
;   19. The entry jr and two jr in LongMatch became jp: the checks moved their targets out
;       of range (same flags; jp is 10 T, jr 12 taken / 7 not).
; -----------------------------------------------------------------------------

        INCLUDE "banked.inc"

		MACRO NEXT_HL
		inc hl
		ENDM

		MACRO ADD_OFFSET
		add hl,de
		ENDM

banked_decoder:
@DecompressLZSA2:
		; A' stores next nibble as %1111.... or assumed to contain trash
		; B is assumed to be 0 in many places

		ld b,0 : scf : exa : jp ReadToken			; BANKED (19): jr -> jp




ManyLiterals:	RCHK_HL							; BANKED (1)
		ld a,18 : add (hl) : NEXT_HL : jr nc,CopyMoreLiterals
		RCHK_HL							; BANKED (2)
		ld c,(hl) : NEXT_HL
		ld a,b
		RCHK_HL							; BANKED (3): peek
		ld b,(hl)
		jr ReadToken.NEXTHLuseBC




MoreLiterals:	ld b,(hl) : NEXT_HL					; BANKED (4): token, proved at ReadToken
		scf : exa : jr nc,.noUpdate

			; nibbles are read left-to-right; spare nibbles are kept in AF'
			; and flag NC indicates that a nibble is available
			RCHK_HL						; BANKED (5): peek
			ld a,(hl) : or a : exa
			ld a,(hl) : NEXT_HL
			rrca : rrca : rrca : rrca

.noUpdate	or #F0
		;sub #F0-3 : cp 15+3 : jr z,ManyLiterals
		inc a : jr z,ManyLiterals : sub #F0-3+1

CopyMoreLiterals:	ld c,a : ld a,b : ld b,0
			call copy_lit					; BANKED (6): was COPY1 : COPY1 : COPYBC

		or a : jp p,CASE0xx

		cp %11000000 : jr c,CASE10x

		; "111": repeated offset
CASE11x		cp %11100000 : jr nc,MatchLen

		; "110": 16-bit offset
CASE110:	RCHK_HL							; BANKED (7)
		ld b,(hl) : NEXT_HL : jr ReadOffsetC





Literals0011:	jr nz,MoreLiterals

		; if "LL" of the byte token is equal to 0,
		; there are no literals to copy
NoLiterals:	or (hl) : NEXT_HL					; BANKED (8): token, proved at ReadToken
		jp m,CASE1xx

		; short (5 or 9 bit long) offsets
CASE0xx		cp %01000000 : jr c,CASE00x

			; "01x": the case of the 9-bit offset
CASE01x:		cp %01100000
.doDECB			dec b : rl b

ReadOffsetC		RCHK_HL						; BANKED (9)
			ld c,(hl) : NEXT_HL

SaveOffset		ld (CopyMatch.PrevOffset),bc : ld b,0

MatchLen		inc a : and %00000111 : jr z,LongerMatch : inc a

CopyMatch:		ld c,a
.useC			push hl
.PrevOffset		EQU $+1 : ld hl,0
			ADD_OFFSET
			OCHK_HL : ldi : call copy_match			; BANKED (10): was COPY1 : COPYBC
.popSrc			pop hl

		; compressed data stream contains records
		; each record begins with the byte token "XYZ|LL|MMM"
ReadToken:	RCHK_HL							; BANKED (11): peek
		ld a,(hl) : and %00011000 : jp pe,Literals0011		; process the cases 00 and 11 separately

			rrca : rrca : rrca

			ld c,a : ld a,(hl)					; token is re-read for further processing
.NEXTHLuseBC		NEXT_HL
			call copy_lit					; BANKED (12): was COPYBC

		; the token and literals are followed by the offset
		or a : jp p,CASE0xx

CASE1xx		cp %11000000 : jr nc,CASE11x

		; "10x": the case of the 13-bit offset
CASE10x:	ld c,a : exa : jr nc,.noUpdate

			RCHK_HL						; BANKED (13): peek
			ld a,(hl) : or a : exa
			ld a,(hl) : NEXT_HL
			rrca : rrca : rrca : rrca

.noUpdate	or #F0 : ld b,a : ld a,c
		cp %10100000 : jr CASE01x.doDECB





		; "00x": the case of the 5-bit offset
CASE00x:	ld b,a : exa : jr nc,.noUpdate

			RCHK_HL						; BANKED (14): peek
			ld a,(hl) : or a : exa
			ld a,(hl) : NEXT_HL
			rrca : rrca : rrca : rrca

.noUpdate	or #F0 : ld c,a : ld a,b
		cp %00100000 : rl c
		ld b,#FF : jr SaveOffset





LongerMatch:	scf : exa : jr nc,.noUpdate

			RCHK_HL						; BANKED (15): peek
			ld a,(hl) : or a : exa
			ld a,(hl) : NEXT_HL
			rrca : rrca : rrca : rrca

.noUpdate	or #F0 : sub #F0-9 : cp 15+9 : jr c,CopyMatch

LongMatch:	RCHK_HL							; BANKED (16)
		add (hl) : NEXT_HL : jp nc,CopyMatch			; BANKED (19): jr -> jp
		RCHK_HL_KEEPF						; BANKED (17)
		ld c,(hl) : NEXT_HL
		RCHK_HL_KEEPF						; BANKED (18)
		ld b,(hl) : NEXT_HL : jp nz,CopyMatch.useC		; BANKED (19): jr -> jp
		ret
