/* Minimal shims for the hand-built PMUFW link.
 *
 * Xil_DCacheInvalidateRange() is an ARM-side cache API; the MicroBlaze BSP
 * shipped for the PMU never defines it, yet the collected xilfpga driver
 * references it.  The PMU firmware runs with its caches already configured by
 * the boot ROM, so a no-op is adequate here.
 */
#include "xil_types.h"

void Xil_DCacheInvalidateRange(INTPTR adr, u32 len);
void Xil_DCacheFlushRange(INTPTR adr, u32 len);

void Xil_DCacheInvalidateRange(INTPTR adr, u32 len)
{
    (void)adr;
    (void)len;
}

void Xil_DCacheFlushRange(INTPTR adr, u32 len)
{
    (void)adr;
    (void)len;
}
