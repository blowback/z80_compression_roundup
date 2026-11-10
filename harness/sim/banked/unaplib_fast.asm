; -----------------------------------------------------------------------------
; Banked port of unaplib_fast.asm (speed-optimized ApLib decompressor by spke
; & uniabis, ver.06 01-05/06/2020) from uniabis/z80depacker, for
; harness/linesim --mode banked.
;
; The original Z80 decompressors for ApLib were written by Dan Weiss (Dwedit),
; then tweaked by Francisco Javier Pena Pareja (utopian),
; and optimized by Jaime Tejedor Gomez (Metalbrain) and Antonio Villena.
; Of course, ApLib compression algorithms are (c) 1998-2014 Joergen Ibsen,
; see http://www.ibsensoftware.com/ for more information
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
; ALTERED SOURCE. Only input reads, output writes and copies changed (helpers
; in harness/sim/banked/banked.inc). Build: forward, Z80 (no HD64180), and
; SupportLongOffsets defined (decoders.py passes -DSupportLongOffsets), since
; banked output can exceed 32 KB. The unused IFDEF branches are dropped.
;
; Edit sites (input pointer HL in the ring, counter IYL; IX keeps the offset),
; each marked "BANKED":
;   1.  Decompress: first literal ldi -> LDI_LIT.
;   2.  RELOAD_A macro (used 6 times, sites 2-7): check before ld a,(hl);
;       the check keeps carry for the rla.
;   8.  LWM0.CASE0: literal ldi -> LDI_LIT.
;   9.  LWM0.CASE110: ld c,(hl) is a peek (EOF returns before NEXT_HL):
;       check first; carry (NC here) survives for rr c.
;   10. LengthIs2: ldi x2 from earlier output -> OCHK_HL before each.
;   11. LengthIs3: ldi x3 -> OCHK_HL before each.
;   12. AfterLWM: ld c,(hl) -> RCHK_HL.
;   13. CopyMatch: ldi + ldir -> OCHK_HL : ldi : copy_match (same bytes
;       for every BC, as copy_match copies 65,536 for BC = 0 like ldir).
;   14. LWM1.CASE111: output pointer check up front (flags are free here):
;       the byte is written through HL after ex de,hl ("swapped write").
;   15. GetGammaCoded: RELOAD_A sites (counted in 2-7).
;   Four jr became jp because the checks moved their targets out of range
;   (same flags; jp is 10 T taken or not, jr 12 taken / 7 not).
; -----------------------------------------------------------------------------

        INCLUDE "banked.inc"

		MACRO NEXT_HL
		inc hl
		ENDM

		MACRO RELOAD_A
		RCHK_HL							; BANKED (2-7): keeps carry
		ld a,(hl) : NEXT_HL : rla
		ENDM

banked_decoder:
@Decompress:		LDI_LIT : scf					; BANKED (1): was COPY_1

;==================================================================================================================
;==================================================================================================================
;==================================================================================================================

LWM0:			;LWM = 0 (LWM stands for "Last Was Match"; a flag that we did not have a match)

.ReloadByteC0		RELOAD_A : jr c,.Check2ndBit

;
;  case "0"+BYTE: copy a single literal

.CASE0:			LDI_LIT						; BANKED (8): first byte is always copied as literal

;
;  main decompressor loop

.MainLoop:		add a : jr nc,.CASE0 : jr z,.ReloadByteC0	; "0"+BYTE = copy literal
.Check2ndBit		add a : jr nc,.CASE10 : jr z,.ReloadByteC1	; "10"+gamma(offset/256)+BYTE+gamma(length) = the main matching mechanism
.Check3rdBit		add a : call z,ReloadByte : jp c,LWM1.CASE111	; "110"+[oooooool] = matched 2-3 bytes with a small offset

;
;  branch "110"+[oooooool]: copy two or three bytes (bit "l") with the offset -1..-127 (bits "ooooooo"), or stop

.CASE110:		; "use 7 bit offset, length = 2 or 3"
			; "if a zero is found here, it's EOF"
			RCHK_HL						; BANKED (9): peek; keeps carry
			ld c,(hl) : rr c : ret z			; process EOF
			NEXT_HL
			ld b,0

			ld ixl,c : ld ixh,b				; save offset for future LWMs

			push hl						; save src
			ld h,d : ld l,e					; HL = dest
			jr c,.LengthIs3

.LengthIs2
			sbc hl,bc
			OCHK_HL : ldi : OCHK_HL : ldi			; BANKED (10): was COPY_1 : COPY_1
			jr .PreMainLoop

.LengthIs3
			or a : sbc hl,bc
			OCHK_HL : ldi : OCHK_HL : ldi : OCHK_HL : ldi	; BANKED (11): was COPY_1 x3
			jr .PreMainLoop

.ReloadByteC1		RELOAD_A : jr c,.Check3rdBit

;
;  branch "10"+gamma(offset/256)+BYTE+gamma(length): the main matching mechanism

.CASE10:		; "use a gamma code * 256 for offset, another gamma code for length"
			call GetGammaCoded

			; the original decompressor contains
			;
			; if ((LWM == 0) && (offs == 2)) { ... }
			; else {
			;	if (LWM == 0) { offs -= 3; }
			;	else { offs -= 2; }
			; }
			;
			; so, the idea here is to use the fact that GetGammaCoded returns (offset/256)+2,
			; and to split the first condition by noticing that C-1 can never be zero
			dec c : dec c : jp z,LWM1.KickInLWM	; BANKED: jr -> jp (code grew)

.AfterLWM		dec c : ld b,c
			RCHK_HL						; BANKED (12)
			ld c,(hl) : NEXT_HL				; BC = offset

			ld ixl,c : ld ixh,b : push bc

			call GetGammaCoded			; BC = len*

			ex (sp),hl

			; interpretation of length value is offset-dependent:
			; if (offs >= 32000) len++; if (offs >= 1280) len++; if (offs < 128) len+=2;
			; in other words,
			; (1 <= offs < 128) +=2
			; (128 <= offs < 1280) +=0
			; (1280 <= offs < 31999) +=1
			; NB offsets over 32000 need one more check, but other Z80 decompressors seem to ignore it. is it not needed?

			; interpretation of length value is offset-dependent
			exa : ld a,h
	IFDEF	SupportLongOffsets
			; NB offsets over 32000 require an additional check, which is skipped in most
			; Z80 decompressors (seemingly as a performance optimization)
			cp 32000/256 : jr nc,.Add2
	ENDIF
			cp 5 : jr nc,.Add1
			or a : jr nz,.Add0
			bit 7,l : jr nz,.Add0
.Add2			inc bc
.Add1			inc bc
.Add0			; for offs<128 : 4+4+7+7 + 4+7 + 8+7 + 6+6 = 60t
			; for offs>=1280 : 4+4+7+12 + 6 = 33t
			; for 128<=offs<1280 : 4+4+7+7 + 4+12 = 38t OR 4+4+7+7 + 4+7+8+12 = 53t

.CopyMatch:		; this assumes that BC = len, DE = dest, HL = offset
			; and also that (SP) = src, while having NC
			ld a,e : sub l : ld l,a
			ld a,d : sbc h
			ld h,a : exa
			OCHK_HL : ldi : call copy_match			; BANKED (13): was COPY_1 : COPY_BC
.PreMainLoop		pop hl					; recover src

;==================================================================================================================
;==================================================================================================================
;==================================================================================================================

LWM1:			; LWM = 1

;
;  main decompressor loop

.MainLoop:		add a : jp nc,LWM0.CASE0 : jr z,.ReloadByteC0		; "0"+BYTE = copy literal; BANKED: jr -> jp
.Check2ndBit		add a : jr nc,.CASE10 : jr z,.ReloadByteC1		; "10"+gamma(offset/256)+BYTE+gamma(length) = the main matching mechanism
.Check3rdBit		add a : call z,ReloadByte : jp nc,LWM0.CASE110		; "110"+[oooooool] = matched 2-3 bytes with a small offset

;
;  case "111"+"oooo": copy a byte with offset -1..-15, or write zero to dest

.CASE111:		OCHK						; BANKED (14): DE = dest; flags are free here
			ld bc,%11100000
			DUP 4
			add a : call z,ReloadByte : rl c	; read short offset (4 bits)
			EDUP
			ex de,hl : jr z,.WriteZero		; zero offset means "write zero" (NB: B is zero here)

			; "write a previous byte (1-15 away from dest)"
			push hl					; BC = offset, DE = src, HL = dest
			sbc hl,bc				; HL = dest-offset (SBC works because branching above ensured NC)
			ld c,(hl)
			pop hl

.WriteZero		ld (hl),c : NEXT_HL
			ex de,hl : jp LWM0.MainLoop		; 10+4*(4+10+8)+4+7 + 11+15+7+10 + 7+4+6+10 = 179t

.ReloadByteC0		RELOAD_A : jp nc,LWM0.CASE0
			jr .Check2ndBit

.ReloadByteC1		RELOAD_A : jr c,.Check3rdBit

;
;  branch "10"+gamma(offset/256)+BYTE+gamma(length): the main matching mechanism

.CASE10:		; "use a gamma code * 256 for offset, another gamma code for length"
			call GetGammaCoded

			; the original decompressor contains
			;
			; if ((LWM == 0) && (offs == 2)) { ... }
			; else {
			;	if (LWM == 0) { offs -= 3; }
			;	else { offs -= 2; }
			; }
			;
			; so, the idea here is to use the fact that GetGammaCoded returns (offset/256)+2,
			; and to split the first condition by noticing that C-1 can never be zero
			dec c : jp LWM0.AfterLWM				; BANKED: jr -> jp

;
;  the re-use of the previous offset (LWM magic)

.KickInLWM:		; "and a new gamma code for length"
			inc c : call GetGammaCoded.ReadGamma		; BC = len

			push ix : ex (sp),hl : exa
			jr LWM0.CopyMatch

;==================================================================================================================
;==================================================================================================================
;==================================================================================================================

;
;  interlaced gamma code reader
;  x0 -> 1x
;  x1y0 -> 1xy
;  x1y1z0 -> 1xyz etc
;  (technically, this is a 2-based variation of Exp-Golomb-1)

GetGammaCoded:		ld bc,1
.ReadGamma		add a : jr z,.ReloadByteRG1
			rl c : rl b
			add a : ret nc				; NB: flag NC immediately says we do not need to reload our byte...
			jr nz,.ReadGamma			; ...even better, flag NZ then automatically means flag C :)

.ReloadByteRG2		RELOAD_A : ret nc : jr .ReadGamma

.ReloadByteRG1		RELOAD_A : rl c : rl b
			add a : ret nc : jr .ReadGamma

;
;  pretty usual getbit for mixed datastreams

ReloadByte:		RELOAD_A : ret
