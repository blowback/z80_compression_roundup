; -----------------------------------------------------------------------------
; Banked port of derip_fast.asm (RIP depacker, fast version, from
; uniabis/z80depacker; "Compiled size: 231 bytes; +16.4% speed to
; derip_small.asm") for harness/linesim --mode banked.
;
; ALTERED SOURCE. Only input reads, output writes, copies and the work-area
; address changed (helpers in harness/sim/banked/banked.inc). The original
; "OUTPUT derip.C" directive is dropped (it would send the code to another
; file) and workarea moves to the banked buffer at #F000.
;
; Edit sites (input pointer IX in the ring, counter IYL), each marked
; "BANKED":
;   0. Entry: banked.inc passes HL = input, DE = output; RIP wants IX = input
;      and DE = output - 1.
;   1. mWBYTE: ld c,(ix) -> RCHK_IX first; carry (the marker bit shifted
;      out by SRL C) survives for RR C.
;   2. MAINLOOP1: LD (DE),A runs for every code, also for matches, where
;      the byte is thrown away and overwritten by the copy. OCHK before it,
;      so the throwaway write never lands in the ring at 0xC000.
;   3. COPY: LDIR -> copy_match (A, the saved bit buffer, is kept).
;   4. The byte-counted jumps JR Z,$+3 (CPY1), JR NZ,$-8 (LLEN) and
;      JR NC,$+4 (mHFM) now use labels. They skip or repeat the same
;      instructions as before.
; -----------------------------------------------------------------------------

        INCLUDE "banked.inc"

workarea=#F000 ;must be >=#0200, must be even
;workareaSize=#5C2
maintree=workarea
tree=maintree+0x120*4
bitlens2=tree+0x120

banked_decoder:
        PUSH HL                 ; BANKED (0)
        POP IX
        DEC DE

        ;LD IX, src
        PUSH DE ;dest-1

        LD BC,#1201
        LD DE,tree-1
        PUSH DE
L0      LD A,#10
L1      SRL C
        CALL Z, mWBYTE
        RLA
        JR NC, L1
        INC DE
        LD (DE),A
        DJNZ L0

        PUSH BC
        LD DE,18+1
        CALL mMTRE
        POP BC
        POP DE ;tree-1
GETLEN0 CALL mHFMAI
        CP 16
        JR C, GETLEN_
        LD A,(DE)
        INC DE
        LD (DE),A
GETLEN_ INC DE
        LD (DE),A
        JR NZ, GETLEN0

GETLENQ PUSH BC
        LD DE,0x120+1
        CALL mMTRE
        LD HL,bitlens2-2
        LD DE,32+1
        EXX
        LD DE,tree
        CALL mTRE
        ;LD HL,#2758:EXX ;uncomment if returning to Basic
        POP BC

        POP DE ;dest-1
        ;LD DE,dest-1

MAINLOOP2
        INC DE
MAINLOOP1
        CALL mHFMAI
        OCHK                    ; BANKED (2): before the (throwaway) write
        LD (DE),A
        DEC H
        JR NZ, MAINLOOP2
        OR A
        RET Z  ;JP Z,START
        CALL LLEN  ;match len
        PUSH HL
        LD HL,tree
        CALL mHFM
        OR A
        JR Z, CPY1
        CALL LLEN  ;new offset ;cf=0
        LD (MOFFSET+1), HL
        LD A,H
        OR A
CPY1
        LD A,C
        POP BC ;len
        JR Z, COPY ; offset<256 or using last offset?   BANKED (4): was $+3
        INC BC
COPY    ;cf=0
        PUSH DE
        EX DE,HL
MOFFSET LD DE, 0
        SBC HL,DE
        POP DE
        CALL copy_match         ; BANKED (3): was LDIR
        LD C,A ;restore bitbuf
        JR MAINLOOP1

; Read bit
mWBIT   ;SRL C
        ;RET NZ
mWBYTE  RCHK_IX                 ; BANKED (1): keeps carry
        LD C,(IX)
        INC IX
        RR C
        RET

mMTRE
        LD HL,tree-2
        EXX
        LD DE,maintree

; Build tree
; HL' = lengths table-2
; DE' = number of codes+1
; DE = result tree
mTRE    LD H,D
        LD L,E
        XOR A
        PUSH AF
        INC A
        PUSH HL,AF
        LD C,A
mTRE0   EXX
        LD B,D
        LD C,E
        ADD HL,BC
        EXX
mTRE1   LD B,A
        LD A,C
        EXX
        CPDR
        LD A,B
        OR C
        EXX
        LD A,B
        JR NZ, mTREY
        INC C
        JR mTRE0
mTREdip INC E,DE,E,DE
        LD (HL),D ;ptr to children
        INC L
        LD (HL),E
        LD H,D
        LD L,E
        INC A
        PUSH HL,AF
mTREY   CP C
        JR NZ, mTREdip
        EXX
        PUSH BC
        EXX
        POP BC
        DEC BC
        LD (HL),B ;leaf
        INC L
        LD (HL),C
        LD C,A
        POP AF
        RET Z
        POP HL
        INC L,HL
        JR mTRE1

LLEN    ADD A,-5
        RET NC
        LD L,1
        ADC A,L
        RRA
        RL L
LLEN1   SRL C
        CALL Z, mWBYTE
        ADC HL,HL
        DEC A
        JR NZ, LLEN1            ; BANKED (4): was $-8
        INC HL
        RET

; Read code from tree
mHFMAI  LD HL,maintree
mHFM    SRL C
        CALL Z, mWBYTE
        JR NC, mHFM1            ; BANKED (4): was $+4
        INC L,HL
mHFM1   LD A,(HL)
        INC L
        CP H ;H>=2
        LD L,(HL)
        LD H,A
        JR NC, mHFM
        LD A,L
        RET
