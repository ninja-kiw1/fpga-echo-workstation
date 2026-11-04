//=============================================================================
// 模块名   : img_proc_axis_old
// 中文名   : AXI-Stream 图像处理顶层(灰度归一化/阈值分割/边缘增强/伪彩色/组合)
//-----------------------------------------------------------------------------
// 数据通路:
//   S_AXIS (32bit/像素 {8'h00,R,G,B})
//     -> RGB 转灰度 (77R+150G+29B)>>8, 1 级流水
//     -> gray_frame_store_old: 整帧写进 262144x8 的块 RAM, 同时统计 min/max
//     -> 整帧写完后自动读出, 每次给 3x3 邻域
//     -> mode_map_old: 按 ctrl[2:0] 选模式做映射
//     -> M_AXIS (32bit/像素 {8'h00,R,G,B})
//
// 流控:
//   输入侧: s_axis_tready = 帧缓冲可写 && 灰度流水级空。收帧期间恒为高,
//           只在读出阶段(不能同时写)才拉低。
//   输出侧: 读出是"1 像素 / 3 周期"自由跑的, 但下游 m_axis_tready 一拉低,
//           就用 hold 把帧缓冲的读出和 mode_map_old 的流水线**一起冻住**,
//           等下游取走再继续 —— 否则会静默丢像素。
//
// 控制字 ctrl[15:0]:
//   ctrl[2:0]  = 模式 0..4
//   ctrl[15:8] = 阈值(模式 1 用), 默认 128
//   ctrl[7:3]  = 保留
//
// 一帧的节拍: 收满 262144 个像素(tlast) -> 自动读出 262144 个像素 -> 回到空闲,
//             可以接下一帧。读出期间输入侧不 ready。处理模式可以在帧与帧之间改。
//=============================================================================
`timescale 1ns / 1ps

module img_proc_axis_old (
    input  wire        aclk,
    input  wire        aresetn,

    // ---------------- AXI4-Stream 从(输入图) ----------------
    input  wire [31:0] s_axis_tdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,

    // ---------------- AXI4-Stream 主(处理后的图) ----------------
    output wire [31:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire        m_axis_tlast,

    // ---------------- 控制与状态 ----------------
    input  wire [15:0] ctrl,        // [2:0]=模式, [15:8]=阈值
    output wire [7:0]  stat_min,    // 本帧最小灰度
    output wire [7:0]  stat_max,    // 本帧最大灰度
    output wire        stat_valid,  // 统计完成脉冲
    output wire        proc_busy    // 输出阶段进行中
);

    // ---------------------------------------------------------------------
    // 控制字拆解
    // ---------------------------------------------------------------------
    wire [2:0] mode = ctrl[2:0];
    wire [7:0] thr  = ctrl[15:8];

    // ---------------------------------------------------------------------
    // RGB -> 灰度 (77R + 150G + 29B) >> 8, 系数和 = 256 正好右移 8 位
    // ---------------------------------------------------------------------
    wire [7:0]  px_r = s_axis_tdata[23:16];
    wire [7:0]  px_g = s_axis_tdata[15:8];
    wire [7:0]  px_b = s_axis_tdata[7:0];

    wire [15:0] gray_sum = (16'd77  * {8'd0, px_r})
                         + (16'd150 * {8'd0, px_g})
                         + (16'd29  * {8'd0, px_b});
    wire [7:0]  gray_now = gray_sum[15:8];

    // ---------------------------------------------------------------------
    // 灰度一级流水 + 输入握手
    //   s_axis_tready 只在"流水级空"且"帧缓冲能写"时为高, 保证不丢像素。
    // ---------------------------------------------------------------------
    reg  [7:0] gray_q;
    reg        last_q;
    reg        pend_q;              // 流水级里有一个像素还没写进帧缓冲

    wire       pipe_free    = ~pend_q;
    wire       wr_ready;            // 由帧缓冲给出

    assign     s_axis_tready = wr_ready && pipe_free;
    wire       in_accept     = s_axis_tvalid && s_axis_tready;

    // 把流水级里的像素写进帧缓冲
    wire       wr_fire   = pend_q && wr_ready;
    wire       wr_valid  = wr_fire;
    wire [7:0] wr_pixel  = gray_q;
    wire       wr_last   = last_q;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            gray_q <= 8'd0;
            last_q <= 1'b0;
            pend_q <= 1'b0;
        end else if (in_accept) begin
            gray_q <= gray_now;
            last_q <= s_axis_tlast;
            pend_q <= 1'b1;
        end else if (wr_fire) begin
            pend_q <= 1'b0;
        end
    end

    // ---------------------------------------------------------------------
    // 帧缓冲 + 读出
    // ---------------------------------------------------------------------
    wire        rd_start;
    wire        rd_busy;
    wire        rd_valid;
    wire        rd_last;
    wire [7:0]  win_top_l, win_top_c, win_top_r;
    wire [7:0]  win_mid_l, win_mid_c, win_mid_r;
    wire [7:0]  win_bot_l, win_bot_c, win_bot_r;

    // 统计完成后的下一个周期发起读出(stat_valid 是 1 周期脉冲)
    reg         rd_start_r;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            rd_start_r <= 1'b0;
        end else begin
            rd_start_r <= stat_valid;
        end
    end

    assign rd_start = rd_start_r;

    // 输出侧背压: 有有效输出但下游没取走 -> 冻住整条读出链
    wire        out_valid;
    wire        out_last;
    wire [31:0] out_pixel;
    wire        hold = out_valid && !m_axis_tready;

    gray_frame_store_old u_store (
        .clk        (aclk),
        .rst_n      (aresetn),

        .wr_valid   (wr_valid),
        .wr_pixel   (wr_pixel),
        .wr_last    (wr_last),
        .wr_ready   (wr_ready),

        .stat_min   (stat_min),
        .stat_max   (stat_max),
        .stat_valid (stat_valid),

        .rd_start   (rd_start),
        .rd_hold    (hold),
        .rd_busy    (rd_busy),
        .rd_valid   (rd_valid),
        .rd_last    (rd_last),
        .win_top_l  (win_top_l), .win_top_c (win_top_c), .win_top_r (win_top_r),
        .win_mid_l  (win_mid_l), .win_mid_c (win_mid_c), .win_mid_r (win_mid_r),
        .win_bot_l  (win_bot_l), .win_bot_c (win_bot_c), .win_bot_r (win_bot_r)
    );

    // ---------------------------------------------------------------------
    // 模式映射
    // ---------------------------------------------------------------------
    mode_map_old u_map (
        .clk        (aclk),
        .rst_n      (aresetn),

        .mode       (mode),
        .thr        (thr),
        .frame_min  (stat_min),
        .frame_max  (stat_max),

        .in_valid   (rd_valid),
        .in_last    (rd_last),
        .stall      (hold),

        .win_tl     (win_top_l), .win_tc (win_top_c), .win_tr (win_top_r),
        .win_ml     (win_mid_l), .win_mc (win_mid_c), .win_mr (win_mid_r),
        .win_bl     (win_bot_l), .win_bc (win_bot_c), .win_br (win_bot_r),

        .out_valid  (out_valid),
        .out_last   (out_last),
        .out_pixel  (out_pixel)
    );

    // ---------------------------------------------------------------------
    // 输出到 AXI-Stream
    // ---------------------------------------------------------------------
    assign m_axis_tdata  = out_pixel;
    assign m_axis_tvalid = out_valid;
    assign m_axis_tlast  = out_last;

    assign proc_busy = rd_busy;

endmodule
