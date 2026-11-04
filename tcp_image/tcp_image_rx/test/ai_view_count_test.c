/* Known-answer regression for indices beyond uint16_t at 512x512. */
#include <stdio.h>
#include <string.h>
#include "ai_view.h"

static int8_t logits[512 * 512];
static int failures;

static void check(const char *name, int width, int height,
                  uint32_t want_vessels, uint32_t want_components)
{
    uint32_t components = 0;
    uint32_t vessels = ai_view_count(logits, width, height, 0, &components);
    int ok = vessels == want_vessels && components == want_components;
    printf("%s %s: vessels=%u components=%u (expected %u/%u)\n",
           ok ? "PASS" : "FAIL", name, vessels, components,
           want_vessels, want_components);
    failures += !ok;
}

int main(void)
{
    int y;
    memset(logits, -1, sizeof(logits));
    check("empty", 512, 512, 0, 0);
    memset(logits, 1, sizeof(logits));
    check("full_512", 512, 512, 262144, 1);
    check("full_128", 128, 128, 16384, 1);
    memset(logits, -1, sizeof(logits));
    for (y = 0; y < 512; ++y) { logits[y * 512 + 511] = 1; }
    check("vertical_cross_65535", 512, 512, 512, 1);
    memset(logits, -1, sizeof(logits));
    for (y = 200; y < 210; ++y) {
        memset(logits + y * 512 + 100, 1, 10);
        memset(logits + y * 512 + 300, 1, 10);
    }
    check("two_blobs_beyond_65535", 512, 512, 200, 2);

    /* F5 tile sums: tile k (4x4 tiles of 128x128) holds the value k - 3 everywhere, and one
     * pixel of tile 15 is 127: sum[k] = (k - 3) * 16384, sum[15] += 127 - 12. */
    {
        uint8_t blk[REG_BLOCK_BYTES];
        int k, x, ok = 1;

        for (y = 0; y < 512; ++y) {
            for (x = 0; x < 512; ++x) {
                logits[y * 512 + x] = (int8_t)((y / 128) * 4 + (x / 128) - 3);
            }
        }
        logits[511 * 512 + 511] = 127;
        ai_view_fill_reg(blk, logits, 512, 512, 7u);
        for (k = 0; k < 16; ++k) {
            int32_t got = (int32_t)((uint32_t)blk[32 + 4 * k] | ((uint32_t)blk[33 + 4 * k] << 8) |
                                    ((uint32_t)blk[34 + 4 * k] << 16) | ((uint32_t)blk[35 + 4 * k] << 24));
            int32_t want = (k - 3) * 16384 + (k == 15 ? 127 - 12 : 0);
            ok &= got == want;
        }
        ok &= blk[0] == 'R' && blk[1] == 'E' && blk[2] == 'G' && blk[3] == '1' && blk[8] == REG_GRID && blk[20] == 7;
        printf("%s tile_sums_4x4: block layout and 16 signed sums\n", ok ? "PASS" : "FAIL");
        failures += !ok;
    }
    return failures ? 1 : 0;
}
