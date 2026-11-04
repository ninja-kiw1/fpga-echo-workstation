//=============================================================================
// 模块名   : mode_map_old
// 中文名   : 五种图像处理模式的映射
//-----------------------------------------------------------------------------
// 输入: gray_frame_store_old 给的 3x3 灰度窗口 + 本帧 min/max
// 输出: 32bit 像素 {8'h00, R, G, B}, 与输入同节奏(输入是 1 像素 / 3 周期)
//
// 模式(3bit):
//   3'd0 灰度归一化 : norm = ((g - min) * recip[range]) >> 8, clamp 255; 输出 {n,n,n}
//   3'd1 阈值分割   : g >= thr ? 255 : 0;  输出 {v,v,v}
//   3'd2 边缘增强   : Sobel 幅度 |gx| + |gy|, clamp 255; 输出 {v,v,v}
//   3'd3 伪彩色映射 : jet[g] -> RGB
//   3'd4 组合       : 先归一化, 再把归一化结果送 jet
//
// 三级流水(S1 算和 / S2 算幅度 / S3 查表与选择)。输入 3 周期一个像素,
// 三级延迟不影响吞吐, 只是输出整体滞后 3 个时钟。
//
// 为什么不用除法: 归一化要除以 (max-min), PL 里做除法又大又慢。
// 改成查倒数表 + 乘法 + 移位(recip_lut_old), 逐像素只剩一次 8x16 乘法。
//=============================================================================
`timescale 1ns / 1ps

module mode_map_old (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [2:0]  mode,        // 处理模式 0..4, 见文件头
    input  wire [7:0]  thr,         // 阈值, 默认 128

    input  wire [7:0]  frame_min,   // 本帧最小灰度(来自 gray_frame_store_old)
    input  wire [7:0]  frame_max,   // 本帧最大灰度

    input  wire        in_valid,
    input  wire        in_last,
    input  wire        stall,       // 拉高时冻结整条流水线(下游没取走输出)
    input  wire [7:0]  win_tl, win_tc, win_tr,
    input  wire [7:0]  win_ml, win_mc, win_mr,
    input  wire [7:0]  win_bl, win_bc, win_br,

    output reg         out_valid,
    output reg         out_last,
    output reg  [31:0] out_pixel
);

    // ---------------------------------------------------------------------
    // 归一化除数与倒数(range = 0 时兜底成 1, 结果恒为 0)
    // ---------------------------------------------------------------------
    wire [7:0]  norm_range = (frame_max > frame_min) ? (frame_max - frame_min) : 8'd1;
    wire [15:0] recip;

    recip_lut_old u_recip (
        .range (norm_range),
        .recip (recip)
    );

    // ---------------------------------------------------------------------
    // S1 组合: Sobel 的四个方向和 + 归一化 + 阈值比较
    //   每个和 = 三个抽头, 其中上下/左右各乘 2(左移一位)
    // ---------------------------------------------------------------------
    wire [10:0] col_l = {1'b0, win_tl} + {win_ml, 1'b0} + {1'b0, win_bl};   // 左列
    wire [10:0] col_r = {1'b0, win_tr} + {win_mr, 1'b0} + {1'b0, win_br};   // 右列
    wire [10:0] row_t = {1'b0, win_tl} + {win_tc, 1'b0} + {1'b0, win_tr};   // 上行
    wire [10:0] row_b = {1'b0, win_bl} + {win_bc, 1'b0} + {1'b0, win_br};   // 下行

    wire [7:0]  gray_s1 = win_mc;                                          // 中心像素

    wire [23:0] nprod   = ({8'd0, gray_s1} - {8'd0, frame_min}) * recip;   // 8x16 -> 24
    wire [7:0]  norm_s1 = (nprod[23:8] > 16'd255) ? 8'hFF : nprod[15:8];

    wire [7:0]  thr_s1  = (gray_s1 >= thr) ? 8'hFF : 8'h00;

    // ---------------------------------------------------------------------
    // S1 寄存器
    // ---------------------------------------------------------------------
    reg [10:0] col_l_r, col_r_r, row_t_r, row_b_r;
    reg [7:0]  gray_r1, norm_r1, thr_r1;
    reg        vld_r1, lst_r1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            col_l_r <= 11'd0; col_r_r <= 11'd0; row_t_r <= 11'd0; row_b_r <= 11'd0;
            gray_r1 <= 8'd0;  norm_r1 <= 8'd0;  thr_r1  <= 8'd0;
            vld_r1  <= 1'b0;  lst_r1  <= 1'b0;
        end else if (!stall) begin
            col_l_r <= col_l;   col_r_r <= col_r;
            row_t_r <= row_t;   row_b_r <= row_b;
            gray_r1 <= gray_s1;
            norm_r1 <= norm_s1;
            thr_r1  <= thr_s1;
            vld_r1  <= in_valid;
            lst_r1  <= in_valid && in_last;
        end
    end

    // ---------------------------------------------------------------------
    // S2 组合: |gx| + |gy|
    //   12bit 两补数: 差值绝对值最大 1020 < 2048, 所以最高位就是符号位
    // ---------------------------------------------------------------------
    wire [11:0] gx   = {1'b0, col_l_r} - {1'b0, col_r_r};
    wire [11:0] gy   = {1'b0, row_t_r} - {1'b0, row_b_r};
    wire [11:0] magx = gx[11] ? (~gx + 12'd1) : gx;
    wire [11:0] magy = gy[11] ? (~gy + 12'd1) : gy;
    wire [12:0] mag  = {1'b0, magx} + {1'b0, magy};       // 最大 2040

    wire [7:0]  sobel_s2 = (mag > 13'd255) ? 8'hFF : mag[7:0];

    // ---------------------------------------------------------------------
    // S2 寄存器
    // ---------------------------------------------------------------------
    reg [7:0] gray_r2, norm_r2, thr_r2, sobel_r2;
    reg       vld_r2, lst_r2;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gray_r2 <= 8'd0; norm_r2 <= 8'd0; thr_r2 <= 8'd0; sobel_r2 <= 8'd0;
            vld_r2  <= 1'b0; lst_r2  <= 1'b0;
        end else if (!stall) begin
            gray_r2  <= gray_r1;
            norm_r2  <= norm_r1;
            thr_r2   <= thr_r1;
            sobel_r2 <= sobel_s2;
            vld_r2   <= vld_r1;
            lst_r2   <= lst_r1;
        end
    end

    // ---------------------------------------------------------------------
    // S3: 两张 jet 查表(原始灰度 / 归一化灰度) + 模式选择
    // ---------------------------------------------------------------------
    wire [23:0] pcolor_raw;     // jet[gray_r2]
    wire [23:0] pcolor_nrm;     // jet[norm_r2]

    pcolor_lut_old u_pc_raw (
        .gray   (gray_r2),
        .pcolor (pcolor_raw)
    );

    pcolor_lut_old u_pc_nrm (
        .gray   (norm_r2),
        .pcolor (pcolor_nrm)
    );

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            out_last  <= 1'b0;
            out_pixel <= 32'd0;
        end else if (!stall) begin
            out_valid <= vld_r2;
            out_last  <= lst_r2;

            case (mode)
                3'd0:    out_pixel <= {8'h00, norm_r2,  norm_r2,  norm_r2};
                3'd1:    out_pixel <= {8'h00, thr_r2,   thr_r2,   thr_r2};
                3'd2:    out_pixel <= {8'h00, sobel_r2, sobel_r2, sobel_r2};
                3'd3:    out_pixel <= {8'h00, pcolor_raw};
                3'd4:    out_pixel <= {8'h00, pcolor_nrm};
                default: out_pixel <= {8'h00, gray_r2,  gray_r2,  gray_r2};
            endcase
        end
    end

endmodule
