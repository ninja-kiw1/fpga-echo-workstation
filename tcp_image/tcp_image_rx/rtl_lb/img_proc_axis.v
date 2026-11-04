//=============================================================================
// 模块名   : img_proc_axis
// 中文名   : 图像处理核心 —— 行缓冲版(2026-10-13, r1)
//-----------------------------------------------------------------------------
// 与 rtl/img_proc_axis.v(整帧缓冲版)的区别
// ----------------------------------------
//   原版把整帧 262144×8 写进块 RAM(64 个 RAMB36), 写完才读出 3×3 窗口。
//   这里改成**两行行缓冲**(一块 512×16 的 RAMB18): 像素边进边出, 输出比输入晚一行。
//   归一化(模式 0 / 4)要整帧 min/max, 流式一趟拿不到 —— 改成 PS **送两遍**:
//     第一遍 stat_only=1: 只统计 min/max(锁存到 norm_min / norm_max), 不出像素;
//     第二遍 stat_only=0: 用第一遍锁存的 min/max 做归一化。
//   其余模式只送一遍。窗口的边界规则(上下左右都"复制边缘")与原版逐像素相同,
//   mode_map / recip_lut / pcolor_lut 原样沿用, 所以输出与原版逐字节相同。
//
// 引擎(每步 2 个时钟: SA 取数 / SB 计算)
// --------------------------------------
//   (y, x) 从 (0, 0) 走到 (512, 512): y = 0..511 的 x = 0..511 每步吃一个输入像素,
//   x = 512 是每行末尾的"虚拟列"(右边界复制末列), y = 512 是帧末的"冲刷行"
//   (下边界复制末行), 这两种步不吃输入。第 y 行的步产出第 y-1 行的输出像素。
//   行 RAM 每个地址存 {第 y-2 行, 第 y-1 行} 的同一列, 读出后写回 {第 y-1 行, 第 y 行}。
//
// 流控
// ----
//   输入: s_axis_tready 只在引擎要吃像素的那一拍(SA 且 out_space_ok)为高。
//   输出: 不反压。下游(img_proc_axil 的输出 FIFO)用 out_space_ok 告诉引擎还有空位;
//         没空位就停在 SA 不走, 已经进流水线的最多 5 个像素一定装得下。
//
// 控制: ctrl[2:0]=模式 0..7, ctrl[15:8]=阈值; stat_only 见上; frame_rst 一拍脉冲
//       = 新的一帧(写 CTRL 时给), 把引擎和统计都清回起点。
//=============================================================================
`timescale 1ns / 1ps

module img_proc_axis (
    input  wire        aclk,
    input  wire        aresetn,

    // ---------------- 输入像素(32bit {8'h00,R,G,B}) ----------------
    input  wire [31:0] s_axis_tdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,

    // ---------------- 输出像素(无反压) ----------------
    output wire [31:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    input  wire        out_space_ok,    // 下游至少还能收 8 个像素

    // ---------------- 控制与状态 ----------------
    input  wire [15:0] ctrl,            // [2:0]=模式, [15:8]=阈值
    input  wire        stat_only,       // 1 = 这一遍只统计 min/max
    input  wire        frame_rst,       // 一拍: 新的一帧
    output reg  [7:0]  stat_min,        // 最近一遍的最小灰度
    output reg  [7:0]  stat_max,        // 最近一遍的最大灰度
    output reg         stat_valid,      // 一遍收完的一拍脉冲
    output wire        proc_busy,       // 引擎在一帧中间
    output wire [19:0] dbg_xy           // {y, x} 调试用
);

    localparam [9:0] W_LAST = 10'd511;
    localparam [9:0] X_VIRT = 10'd512;  // 每行末尾的虚拟列
    localparam [9:0] Y_FLSH = 10'd512;  // 帧末冲刷行

    wire [2:0] mode = ctrl[2:0];
    wire [7:0] thr  = ctrl[15:8];

    // ---------------------------------------------------------------------
    // RGB -> 灰度 (77R + 150G + 29B) >> 8, 与原版相同(PS 喂 {0,g,g,g} 时结果就是 g)
    // ---------------------------------------------------------------------
    wire [15:0] gray_sum = (16'd77  * {8'd0, s_axis_tdata[23:16]})
                         + (16'd150 * {8'd0, s_axis_tdata[15:8]})
                         + (16'd29  * {8'd0, s_axis_tdata[7:0]});
    wire [7:0]  gray_now = gray_sum[15:8];

    // ---------------------------------------------------------------------
    // 寄存器
    // ---------------------------------------------------------------------
    reg        st_b;                    // 0 = SA, 1 = SB
    reg  [9:0] ex, ey;                  // 当前步 (x, y)
    reg  [7:0] cur;                     // SA 吃进来的像素
    reg  [7:0] norm_min, norm_max;      // 统计遍锁存, 归一化用
    reg  [7:0] min_reg, max_reg;
    reg        first;

    // 列移位寄存器(sr0 = 上一列, sr1 = 再上一列), 三行各一组
    reg  [7:0] t0, t1, m0, m1, b0, b1;

    // 送 mode_map 的窗口
    reg  [7:0] w_tl, w_tc, w_tr, w_ml, w_mc, w_mr, w_bl, w_bc, w_br;
    reg        w_vld, w_lst;

    // ---------------------------------------------------------------------
    // 行 RAM: 512 x 16 = {第 y-2 行, 第 y-1 行}, 简单双口(SA 读 / SB 写)
    // ---------------------------------------------------------------------
    (* ram_style = "block" *) reg [15:0] lram [0:511];
    reg  [15:0] lq;                     // 同步读出(SA 发地址, SB 可用)

    wire        real_col  = (ex <= W_LAST);
    wire        need_in   = real_col && (ey != Y_FLSH);
    wire        eng_go_a  = (~st_b) && (~stat_only) && out_space_ok &&
                            ((~need_in) || s_axis_tvalid);

    // 统计遍: 每拍都收; 处理遍: 只在引擎要像素的那一拍收
    assign s_axis_tready = stat_only ? 1'b1 : ((~st_b) && need_in && out_space_ok);
    wire   in_acc        = s_axis_tvalid && s_axis_tready;

    // SB 拍的列数据
    wire [7:0]  l2  = lq[15:8];         // 第 y-2 行
    wire [7:0]  l1  = lq[7:0];          // 第 y-1 行
    wire [7:0]  c_t = real_col ? ((ey >= 10'd2) ? l2 : l1) : t0;   // 上边界复制首行
    wire [7:0]  c_m = real_col ? l1 : m0;
    wire [7:0]  c_b = real_col ? ((ey == Y_FLSH) ? l1 : cur) : b0; // 下边界复制末行
    wire        emit_row  = (ey != 10'd0);                         // 第 0 行只灌行缓冲
    wire        last_step = (ey == Y_FLSH) && (ex == X_VIRT);

    assign proc_busy = (ex != 10'd0) || (ey != 10'd0) || st_b;
    assign dbg_xy    = {ey, ex};

    // 行 RAM 读 / 写 —— 无复位块(块 RAM 不能接异步复位)
    always @(posedge aclk) begin
        if (~st_b) begin
            lq <= lram[ex[8:0]];
        end
        if (st_b && real_col && (ey != Y_FLSH)) begin
            lram[ex[8:0]] <= {l1, cur};
        end
    end

    // ---------------------------------------------------------------------
    // 统计: 每遍都统计并发布; 统计遍另外锁存到 norm_min / norm_max
    // ---------------------------------------------------------------------
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            min_reg    <= 8'hFF;
            max_reg    <= 8'h00;
            first      <= 1'b1;
            stat_min   <= 8'd0;
            stat_max   <= 8'd0;
            stat_valid <= 1'b0;
            norm_min   <= 8'd0;
            norm_max   <= 8'd0;
        end else begin
            stat_valid <= 1'b0;
            if (frame_rst) begin
                first <= 1'b1;
            end else if (in_acc) begin
                if (first || (gray_now < min_reg)) min_reg <= gray_now;
                if (first || (gray_now > max_reg)) max_reg <= gray_now;
                first <= 1'b0;
                if (s_axis_tlast) begin
                    stat_min   <= (first || (gray_now < min_reg)) ? gray_now : min_reg;
                    stat_max   <= (first || (gray_now > max_reg)) ? gray_now : max_reg;
                    stat_valid <= 1'b1;
                    first      <= 1'b1;
                    if (stat_only) begin
                        norm_min <= (first || (gray_now < min_reg)) ? gray_now : min_reg;
                        norm_max <= (first || (gray_now > max_reg)) ? gray_now : max_reg;
                    end
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // 引擎
    // ---------------------------------------------------------------------
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            st_b <= 1'b0;
            ex   <= 10'd0;
            ey   <= 10'd0;
            cur  <= 8'd0;
            t0 <= 8'd0; t1 <= 8'd0; m0 <= 8'd0; m1 <= 8'd0; b0 <= 8'd0; b1 <= 8'd0;
            w_tl <= 8'd0; w_tc <= 8'd0; w_tr <= 8'd0;
            w_ml <= 8'd0; w_mc <= 8'd0; w_mr <= 8'd0;
            w_bl <= 8'd0; w_bc <= 8'd0; w_br <= 8'd0;
            w_vld <= 1'b0;
            w_lst <= 1'b0;
        end else if (frame_rst) begin
            st_b  <= 1'b0;
            ex    <= 10'd0;
            ey    <= 10'd0;
            w_vld <= 1'b0;
            w_lst <= 1'b0;
        end else begin
            w_vld <= 1'b0;
            w_lst <= 1'b0;
            if (~st_b) begin
                if (eng_go_a) begin
                    if (need_in) cur <= gray_now;
                    st_b <= 1'b1;
                end
            end else begin
                st_b <= 1'b0;
                if (emit_row) begin
                    if (ex == 10'd0) begin
                        // 首列: 两级抽头一起预置(左边界复制首列), 不出像素
                        t1 <= c_t; t0 <= c_t;
                        m1 <= c_m; m0 <= c_m;
                        b1 <= c_b; b0 <= c_b;
                    end else begin
                        // 输出第 ey-1 行、第 ex-1 列: 左 = sr1, 中 = sr0, 右 = 本列
                        w_tl <= t1; w_tc <= t0; w_tr <= c_t;
                        w_ml <= m1; w_mc <= m0; w_mr <= c_m;
                        w_bl <= b1; w_bc <= b0; w_br <= c_b;
                        w_vld <= 1'b1;
                        w_lst <= last_step;
                        t1 <= t0; t0 <= c_t;
                        m1 <= m0; m0 <= c_m;
                        b1 <= b0; b0 <= c_b;
                    end
                end
                // 推进 (x, y)
                if (ex == X_VIRT) begin
                    ex <= 10'd0;
                    ey <= last_step ? 10'd0 : (ey + 10'd1);
                end else begin
                    ex <= ex + 10'd1;
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // 模式映射(与原版同一个模块; 归一化用统计遍锁存的 min/max)
    // ---------------------------------------------------------------------
    wire        mm_valid;
    wire        mm_last;
    wire [31:0] mm_pixel;

    mode_map u_map (
        .clk        (aclk),
        .rst_n      (aresetn),
        .mode       (mode),
        .thr        (thr),
        .frame_min  (norm_min),
        .frame_max  (norm_max),
        .in_valid   (w_vld),
        .in_last    (w_lst),
        .stall      (1'b0),
        .win_tl     (w_tl), .win_tc (w_tc), .win_tr (w_tr),
        .win_ml     (w_ml), .win_mc (w_mc), .win_mr (w_mr),
        .win_bl     (w_bl), .win_bc (w_bc), .win_br (w_br),
        .out_valid  (mm_valid),
        .out_last   (mm_last),
        .out_pixel  (mm_pixel)
    );

    assign m_axis_tdata  = mm_pixel;
    assign m_axis_tvalid = mm_valid;
    assign m_axis_tlast  = mm_last;

endmodule
