#include <stdint.h>
#include "stm8l.h"

#define TRIG_PIN (1 << 1) // PB1
#define SUCCESS_PIN (1 << 6) // PB6
#define RESET_PIN (1 << 5) // PB5

#define RST_SR_BORF (1 << 5)
#define RST_SR_PORF (1 << 0)  // Power-On Reset flag

void main(void)
{
    CLK_PCKENR1 = 0xff;

    // set output
	PB_DDR |= TRIG_PIN | SUCCESS_PIN | RESET_PIN; // PB1, PB4, PB5 as outputs
	PB_CR1 |= TRIG_PIN | SUCCESS_PIN | RESET_PIN; // push-pull
	PB_CR2 |= TRIG_PIN | SUCCESS_PIN | RESET_PIN; // fast

    // Create trigger on PB1
    PB_ODR |= TRIG_PIN;

    // The first part of the bootloader: getting to rdp_check
__asm
	sim // 1 cycle
	ld A, 0x8000 // 2
	cp A, #0x82 // 1
	jreq bootl_check // 4 if taken, 2 otherwise
	cp A, #0xac // 1
	jreq bootl_check // 2/4
	jra rdp_check // 2

bootl_check:
	ld A, 0x480b // 2
	cp A, #0x55 //
	jreq rdp_check //
	jra enter_app 

rdp_check:
    bset 0x5005, #0x6 // Set success pin (PB6) to indicate RDP check

enter_app:
    nop
__endasm;

	// PB_ODR &= ~TRIG_PIN;

#ifdef ALWAYS_SUCCESS
__asm
	bset 0x5005, #0x6 // Set success pin (PB6)
__endasm;
#endif

    {
        uint8_t rst = RST_SR;

		if ((rst & RST_SR_BORF)) {
			PB_ODR |= RESET_PIN;
			RST_SR = 0xFF; // Clear all reset flags
		}
    }

    for (;;)
		;
}