#include <stdint.h>
#include "stm8l.h"

#define TRIG_PIN (1 << 1) // PB1
#define PORF_RESET_PIN (1 << 2) // PB2
#define BOR_RESET_PIN (1 << 3) // PB3
#define SUCCESS_PIN (1 << 4) // PB4

#define RST_SR_BORF (1 << 5) // Brown-Out Reset flag
#define RST_SR_PORF (1 << 0) // Power-On Reset flag

void main(void)
{
    // set output
	PB_DDR |= TRIG_PIN | SUCCESS_PIN | BOR_RESET_PIN | PORF_RESET_PIN; // set as output
	PB_CR1 |= TRIG_PIN | SUCCESS_PIN | BOR_RESET_PIN | PORF_RESET_PIN; // push-pull
	PB_CR2 |= TRIG_PIN | SUCCESS_PIN | BOR_RESET_PIN | PORF_RESET_PIN; // fast

	// first set all pins low
	PB_ODR = 0;
    // Create trigger on PB1
    PB_ODR |= TRIG_PIN;

    // The first part of the bootloader: getting to rdp_check
__asm
	sim // 1 cycle
	ld A, 0x8000 // 2
	cp A, #0x82 // 1
	jreq bootl_check // 2 if taken, 1 otherwise
	cp A, #0xac // 1
	jreq bootl_check // 2/1
	jra rdp_check // 2

bootl_check:
	ld A, 0x480b // 2
	cp A, #0x55 //
	jreq rdp_check //
	jra enter_app 

rdp_check:
    bset 0x5005, #0x4 // Set success pin (PB4) to indicate RDP check

enter_app:
    nop
__endasm;

#ifdef ALWAYS_SUCCESS
__asm
	bset 0x5005, #0x4 // Set success pin (PB4)
__endasm;
#endif

    {
        uint8_t rst = RST_SR;

		if (rst & RST_SR_BORF) {
			PB_ODR |= BOR_RESET_PIN;
		}
		if (rst & RST_SR_PORF) {
			PB_ODR |= PORF_RESET_PIN;
		}

		RST_SR = 0xff; // clear all reset flags
	}
	
	for (;;)
		;
}