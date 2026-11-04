/* axilproc.h -- PS<->PL register (AXI4-Lite) frame processor driver.
 *
 * This board's factory PS configuration enables no PL->PS slave port, so an
 * AXI DMA in the PL can never write results back to DDR.  The PS->PL master
 * does work, so the image path is turned around: the PS pushes each pixel into
 * the PL through a register file and reads the processed pixels back out.
 *
 * Register map (base e.g. 0xA0000000):
 *   0x00 W CTRL     [2:0]=mode [15:8]=threshold; writing resets the frame counter
 *   0x04 R STATUS   [0]=in_ready [1]=out_full [2]=proc_busy [3]=frame_done
 *                   [15:8]=frame min [23:16]=frame max
 *   0x08 W IN_PIX   one 32-bit word = **4 gray pixels** (byte0..byte3 in order)
 *   0x0C R OUT_PIX  gray modes (0/1/2): one 32-bit word = **4 processed pixels**
 *                   (low byte each);
 *                   pseudo-color modes (3/4): one 32-bit word = **1 full pixel**
 *                   {8'h00,R,G,B} -- 彩色的 3 个通道不能只取低字节, 否则整幅变黑白
 *   0x10 R VERSION  0x494D_5003
 *
 * 2026-10-01 加速: 一次搬 4 个像素(原来是 1 个/次)。单次 32bit 访存 ~183 ns 是整个
 * 往返的瓶颈, 搬 4 个事务数就 /4。灰度在 PS 侧按 (77R+150G+29B)>>8 算好(和 PL 里一致),
 * 所以 PL 收到 {0,g,g,g} 走同一条 img_proc_axis, 结果仍是 g。
 */
#ifndef AXILPROC_H
#define AXILPROC_H

#include <stdint.h>

#define AXIL_VERSION     0x494D5003u
#define AXIL_IMG_PIXELS  (512u * 512u)
#define AXIL_WORDS       (AXIL_IMG_PIXELS / 4u)   /* 灰度: 一次搬 4 个像素 */

/* 伪彩模式(3 伪彩色映射 / 4 组合)输出的是真彩色, 一个像素一个 32bit 字 */
#define AXIL_MODE_IS_COLOR(m)  (((m) == 3) || ((m) == 4))

/* Map the register file and verify VERSION.  Returns 0 on success. */
int  axilproc_init(uint64_t base);

void axilproc_close(void);

/* Non-zero once axilproc_init() has succeeded. */
int  axilproc_ready(void);

/* Last frame's PL-side 灰度 min/max (valid after axilproc_frame). */
void axilproc_get_minmax(unsigned *mn, unsigned *mx);

/* Push one 512x512 RGB888 frame through the PL and pull the processed one.
 * rgb_in / rgb_out are IMG_TOTAL_BYTES (786432) each.  Returns 0 on success,
 * negative on a stuck PL (never spins forever -- an AXI write only completes
 * when the PL handshakes, so an unbounded wait would wedge the CPU). */
int  axilproc_frame(const uint8_t *rgb_in, uint8_t *rgb_out, int mode, int thr);

#endif /* AXILPROC_H */
