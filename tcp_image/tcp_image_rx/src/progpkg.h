/* progpkg.h -- program packages: U-Net parameter sets loaded at run time.
 *
 * Built-in models keep their indices 0 .. UNET_MODELS_COUNT-1 (unet_models.c).
 * A package (*.upk, written by ai/net/make_program_pkg.py) carries one more
 * parameter set for the same 30-node structure plus a self-test vector; it is
 * registered under the id written in the package (UNET_MODELS_COUNT .. 254)
 * only after the self-test on the PL reproduces the expected logits bit for bit.
 *
 * File layout (little endian, see make_program_pkg.py):
 *   header 64 B : "UPK1", u16 version, u16 header bytes, u8 id, u8 default shift,
 *                 i8 default thr, u8 roi_ok, u8 nodes, u8 self-test shift,
 *                 u16 0, u32 params bytes, u32 meta bytes, u32 body bytes,
 *                 u32 CRC-32 of the body, char name[32]
 *   body        : nodes x 32 B node table, u32 param_off[nodes], u32 param_len[nodes],
 *                 params, meta (UTF-8 JSON), self-test input (s*s*3 RGB),
 *                 expected logits (s*s int8), s = 512 >> self-test shift
 */
#ifndef PROGPKG_H
#define PROGPKG_H

#include <stddef.h>
#include <stdint.h>
#include "unet_models.h"

#define PROG_MAX_ID     254u
#define PROG_DEFAULT_DIR "/root/tcp_image_rx/programs"

/** @brief Model with this id (built-in or loaded), NULL if none. */
const unet_model_t *prog_model(unsigned id);

/** @brief Make model `id` the one used by the next unet_run(). 0 ok, -1 unknown id. */
int prog_select(unsigned id);

/** @brief CRC-32 of a loaded package's body (0 for built-ins / unknown ids). */
uint32_t prog_crc(unsigned id);

/**
 * @brief Drop all loaded packages and load every *.upk in `dir` (needs unet_init()).
 *
 * Each package is checked (format, CRC-32, id range, unique id and name, same
 * structure and parameter sizes as the built-in models) and self-tested on the
 * PL. Results are printed and written to <dir>/LOADED.txt (renamed into place
 * when complete, so readers never see a partial table). Afterwards the
 * built-in model 0 and the previous working size are selected again.
 *
 * @return number of packages registered (0 if `dir` does not exist).
 */
int prog_load_dir(const char *dir);

/** @brief CRC-32 (IEEE 802.3, same as zlib.crc32). */
uint32_t prog_crc32(const uint8_t *p, size_t n);

#endif /* PROGPKG_H */
