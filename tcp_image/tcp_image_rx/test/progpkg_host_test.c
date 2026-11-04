/* progpkg_host_test.c -- host test of src/progpkg.c with the PL driver stubbed out.
 *
 * The stubbed unet_run() returns the expected logits stored at the end of the
 * package being loaded (or a corrupted copy when g_corrupt is set), so this
 * checks parsing, validation, registration and the self-test comparison; the
 * real PL run is checked on the board (tcp_image_ui/verify_board_programs.py).
 *
 *   gcc -std=c99 -D_GNU_SOURCE -Wall -Wextra -Isrc test/progpkg_host_test.c src/progpkg.c src/unet_models.c \
 *       src/unet_net_data.c -o progpkg_host_test && ./progpkg_host_test <dir with good.upk ...>
 *
 * <dir> is prepared by make_program_pkg.py tests (see verify_board_programs.py --host-dir).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "progpkg.h"
#include "unet_ai.h"

static const unet_model_t *g_cur = &unet_models[0];
static int g_shift = 2, g_corrupt, g_runs;
static const char *g_dir;

int unet_ready(void) { return 1; }
int unet_get_shift(void) { return g_shift; }
int unet_set_shift(int s) { g_shift = s; return 0; }
int unet_select_model(int i) { g_cur = &unet_models[i]; return 0; }
int unet_use_model(const unet_model_t *m) { g_cur = m; return 0; }

/* expected logits = last npx bytes of the package whose self-test input is `rgb` */
int unet_run(const uint8_t *rgb, int8_t *logits, unet_stats_t *st)
{
    size_t npx = (size_t)(512 >> g_shift) * (size_t)(512 >> g_shift);

    memcpy(logits, rgb + npx * 3u, npx);
    if (g_corrupt) {
        logits[npx / 2u] ^= 1;
    }
    memset(st, 0, sizeof(*st));
    g_runs++;
    return 0;
}

static int expect(int cond, const char *what)
{
    printf("%s  %s\n", cond ? "PASS" : "FAIL", what);
    return cond ? 0 : 1;
}

static int loaded_line_has(const char *file, const char *text)
{
    char path[512], line[512];
    FILE *f;
    int hit = 0;

    snprintf(path, sizeof(path), "%s/LOADED.txt", g_dir);
    f = fopen(path, "r");
    while (f != NULL && fgets(line, sizeof(line), f) != NULL) {
        if (strncmp(line, file, strlen(file)) == 0 && strstr(line, text) != NULL) {
            hit = 1;
        }
    }
    if (f != NULL) {
        fclose(f);
    }
    return hit;
}

int main(int argc, char **argv)
{
    int fails = 0, n;

    if (argc < 2) {
        printf("usage: %s <dir>\n", argv[0]);
        return 2;
    }
    g_dir = argv[1];
    n = prog_load_dir(g_dir);
    fails += expect(n == 1, "exactly the one good package is registered");
    fails += expect(g_cur == &unet_models[0] && g_shift == 2, "model 0 and the old shift restored after loading");
    fails += expect(prog_model(33) != NULL && strcmp(prog_model(33)->name, "skin_pkg") == 0, "id 33 = skin_pkg");
    fails += expect(prog_model(33) != NULL && memcmp(prog_model(33)->params, unet_models[1].params,
                    1000u) == 0 && prog_model(33)->default_thr == 1, "parameters equal the built-in skin model");
    fails += expect(prog_model(1) == &unet_models[1] && prog_model(17) == NULL && prog_model(255) == NULL,
                    "built-ins keep their ids, unknown ids are NULL");
    fails += expect(prog_select(33) == 0 && g_cur == prog_model(33) && prog_select(200) == -1, "prog_select");
    fails += expect(prog_crc(33) != 0u && prog_crc(1) == 0u, "prog_crc");
    fails += expect(loaded_line_has("a_good.upk", "\tOK "), "LOADED.txt: good package OK");
    fails += expect(loaded_line_has("b_badcrc.upk", "CRC-32 mismatch"), "corrupted body rejected");
    fails += expect(loaded_line_has("c_dupid.upk", "already taken"), "duplicate id rejected");
    fails += expect(loaded_line_has("d_lowid.upk", "outside"), "built-in id range rejected");
    fails += expect(loaded_line_has("e_struct.upk", "parameters out of range"), "wrong parameter sizes rejected");
    fails += expect(loaded_line_has("f_trunc.upk", "header says"), "truncated file rejected");
    fails += expect(loaded_line_has("g_dupname.upk", "already used"), "duplicate name rejected");

    g_corrupt = 1;
    n = prog_load_dir(g_dir);
    fails += expect(n == 0 && prog_model(33) == NULL, "one differing logit fails the self-test");
    fails += expect(loaded_line_has("a_good.upk", "1/16384 logits differ"), "LOADED.txt reports the difference");
    g_corrupt = 0;
    n = prog_load_dir("/nonexistent_dir_for_test");
    fails += expect(n == 0 && prog_model(33) == NULL, "missing directory: built-ins only, old packages dropped");

    printf(fails ? "PROGPKG_HOST_FAIL (%d)\n" : "PROGPKG_HOST_PASS\n", fails);
    return fails != 0;
}
