# eltwise E2b（当前板上固件 xu2dp_e2b 用的 eltwise IP）

= `../opt_hls_elt/`（E2）的源码 + `gmem_a`、`gmem_b` 两个 m_axi pragma 加 `num_read_outstanding=2`，别的一字未改。
为什么、怎么验证、上板结果全部写在 **`../opt_hls_elt/README.md`**。

- 运行：`run_hls_elt.bat csim|synth|cosim|export [随机用例数]`，日志 `_e2b_<stage>.log`；IP 导出到 `ai/build/elt_ip_e2b/`。
- 整系统：`ai/bd/run_build_xu2dp_e2b.bat` → `bitstream/ai_sys_xu2dp_e2b.{bit,bin}`（bin `3bbd9396…`）；
  101.376 MHz 复核 `E:/PLD/environment/inspect_xu2dp_e2b.bat`。
- 上板验收：`E:/PLD/board_acceptance_e2b/验收报告.md`（10-12 通过、已部署）。
- 以后改 PL（例如 P3 提频）请以 `ai/bd/build_ai_sys_xu2dp_e2b.tcl` 为起点。
