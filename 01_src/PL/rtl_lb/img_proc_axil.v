//=============================================================================
// 模块名   : img_proc_axil
// 中文名   : 图像处理顶层 —— AXI4-Lite 寄存器接口, 行缓冲版(2026-10-13, r1)
//-----------------------------------------------------------------------------
// 端口和 BD 里的接法与 rtl/img_proc_axil.v 完全相同(模块名也相同, BD 脚本不用改连线)。
// 变化在内部和使用方法(VERSION 0x494D_5004, PS 驱动按版本选走法, 见 src/axilproc.c):
//
//   原版: 整帧缓冲, PS 先写完 65536 个字, 再读 65536 个字(或 262144 个彩色字)。
//   本版: 两行行缓冲 + 一行多的输出 FIFO。输出比输入晚一行, 所以 PS 要**逐行交替**:
//         写第 r 行(128 个字) -> 读第 r-1 行的输出 -> ... -> 写完第 511 行后读第 510 行,
//         帧末 PL 自己冲刷出第 511 行, 再读它。
//         模式 0 / 4(归一化)先送一遍"只统计"(CTRL[3]=1), 再按上面的走法送第二遍。
//
// 寄存器
// ------
//     偏移  访问  名称        说明
//     0x00  W     CTRL        [2:0]=模式 [3]=只统计这一遍 [15:8]=阈值;
//                             写入 = 新的一帧(清引擎、统计、输出 FIFO、粘滞错误位)
//     0x04  R     STATUS      [0]=in_ready [1]=out_full [2]=proc_busy [3]=frame_done
//                             [4]=读空(粘滞: 读 OUT_PIX 时没有数据)
//                             [5]=溢出(粘滞: 输出 FIFO 满了还在写)
//                             [15:8]=最近一遍的最小灰度 [23:16]=最大灰度
//     0x08  W     IN_PIX      一个字 = 4 个灰度像素(byte0 在前)
//     0x0C  R     OUT_PIX     灰度模式: 一个字 = 4 个结果像素; 伪彩模式(3/4): 一个字 = 1 个 {0,R,G,B}
//                             还没攒满时这次读最多等 64 拍(不会无限挂住总线), 等不到置 STATUS[4]
//     0x10  R     VERSION     0x494D_5004
//     0x14  R     OUT_LEVEL   [11:0] = 还没被读走的输出像素数(FIFO + 打包寄存器)
//     0x18  R     DBGCNT      {引擎 y, 引擎 x, ...} 调试用
//     0x1C  R     DBGWD       最后一次写入的数据
//
// frame_done: 统计遍收完最后一个像素时置位; 处理遍最后一个输出像素进 FIFO 时置位。
//=============================================================================
`timescale 1ns / 1ps

module img_proc_axil (
    input  wire        s_axi_aclk,
    input  wire        s_axi_aresetn,

    // ---------------- AXI4-Lite 从机 ----------------
    input  wire [4:0]  s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [4:0]  s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready
);

    localparam [4:0] A_CTRL   = 5'h00;
    localparam [4:0] A_STATUS = 5'h04;
    localparam [4:0] A_IN     = 5'h08;
    localparam [4:0] A_OUT    = 5'h0C;
    localparam [4:0] A_VER    = 5'h10;
    localparam [4:0] A_LEVEL  = 5'h14;
    localparam [4:0] A_DBGCNT = 5'h18;
    localparam [4:0] A_DBGWD  = 5'h1C;

    localparam integer PIX_CNT  = 512 * 512;
    localparam [17:0]  LAST_CNT = PIX_CNT - 1;
    localparam [31:0]  VER      = 32'h494D_5004;  // 行缓冲 + 归一化送两遍

    localparam integer FDEPTH   = 1024;           // 输出 FIFO 深度(像素); 一行 512 + 余量
    localparam [10:0]  FSPACE   = 11'd1000;       // 低于这个才让引擎走(流水线里最多 5 个)

    // ---------------------------------------------------------------------
    // 寄存器
    // ---------------------------------------------------------------------
    reg  [15:0] ctrl_r;
    reg         stat_only_r;
    reg         frame_rst;      // 写 CTRL 的下一拍给核心一拍复位
    reg  [2:0]  drain_cnt;      // 复位后几拍内丢掉 mode_map 流水线里的残留输出
    reg  [17:0] in_cnt;
    reg  [31:0] in_word_r;
    reg  [1:0]  in_sel;
    reg         in_active;

    reg         frame_done;
    reg         err_under;
    reg         err_over;
    reg  [7:0]  stat_min_r;
    reg  [7:0]  stat_max_r;

    reg         bvalid_r;
    reg         aw_got;
    reg         w_got;
    reg  [4:0]  awaddr_r;
    reg  [31:0] wdata_r;
    reg         rvalid_r;
    reg  [31:0] rdata_r;
    reg         rd_wait;        // 读 OUT_PIX 时打包寄存器还没满: 最多等 64 拍
    reg  [5:0]  rd_tmo;

    // 输出 FIFO(块 RAM, 24bit 像素) + 打包寄存器
    (* ram_style = "block" *) reg [23:0] fmem [0:FDEPTH-1];
    reg  [9:0]  f_wp, f_rp;
    reg  [10:0] f_cnt;          // FIFO 里的像素数(不含已经弹出、在路上的)
    reg  [23:0] f_q;
    reg         pop_pend;       // 上一拍弹出了一个, 这一拍 f_q 有效
    reg  [31:0] out_word_r;
    reg  [2:0]  pk_cnt;         // 打包寄存器里已有的像素数

    // ---------------------------------------------------------------------
    // 核心
    // ---------------------------------------------------------------------
    wire        s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire        m_axis_tvalid;
    wire        m_axis_tlast;
    wire [7:0]  stat_min_w;
    wire [7:0]  stat_max_w;
    wire        stat_valid_w;
    wire        proc_busy_w;
    wire [19:0] dbg_xy_w;

    wire [7:0]  in_byte = (in_sel == 2'd0) ? in_word_r[7:0]   :
                          (in_sel == 2'd1) ? in_word_r[15:8]  :
                          (in_sel == 2'd2) ? in_word_r[23:16] : in_word_r[31:24];
    wire [31:0] in_pix_fed = {8'h00, in_byte, in_byte, in_byte};

    wire in_last  = (in_cnt == LAST_CNT);
    wire in_fire  = in_active && s_axis_tready;
    wire in_ready = ~in_active;                 // 字寄存器空着就能收下一个字

    wire        color_out = (ctrl_r[2:0] == 3'd3) || (ctrl_r[2:0] == 3'd4);
    wire [2:0]  pk_need   = color_out ? 3'd1 : 3'd4;
    wire        out_full  = (pk_cnt == pk_need);

    wire        out_space_ok = (f_cnt < FSPACE);

    img_proc_axis u_proc (
        .aclk         (s_axi_aclk),
        .aresetn      (s_axi_aresetn),
        .s_axis_tdata (in_pix_fed),
        .s_axis_tvalid(in_active),
        .s_axis_tready(s_axis_tready),
        .s_axis_tlast (in_last),
        .m_axis_tdata (m_axis_tdata),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tlast (m_axis_tlast),
        .out_space_ok (out_space_ok),
        .ctrl         (ctrl_r),
        .stat_only    (stat_only_r),
        .frame_rst    (frame_rst),
        .stat_min     (stat_min_w),
        .stat_max     (stat_max_w),
        .stat_valid   (stat_valid_w),
        .proc_busy    (proc_busy_w),
        .dbg_xy       (dbg_xy_w)
    );

    // FIFO 推入 / 弹出条件
    wire f_push = m_axis_tvalid && (drain_cnt == 3'd0) && (f_cnt != FDEPTH);
    wire f_pop  = (f_cnt != 11'd0) && ({1'b0, pk_cnt} + {3'd0, pop_pend} < {1'b0, pk_need});

    // ---------------------------------------------------------------------
    // AXI-Lite 握手(与原版相同的写法)
    // ---------------------------------------------------------------------
    wire wr_go = aw_got && w_got && (~bvalid_r) &&
                 ((awaddr_r != A_IN) || in_ready);

    assign s_axi_awready = ~aw_got;
    assign s_axi_wready  = ~w_got;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;

    wire   ar_hs    = s_axi_arready && s_axi_arvalid;
    wire   out_read = ar_hs && (s_axi_araddr == A_OUT);
    // 读走一整字: 地址到时已满, 或者在等的过程中满了。等满 64 拍还不满就回旧值并记"读空"。
    wire   out_take = (out_read && out_full) || (rd_wait && out_full);

    assign s_axi_arready = ~s_axi_rvalid && ~rd_wait;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rvalid  = rvalid_r;
    assign s_axi_rdata   = rdata_r;

    wire [11:0] out_level = {1'b0, f_cnt} + {11'd0, pop_pend} + {9'd0, pk_cnt};

    // FIFO 存储 —— 无复位块
    always @(posedge s_axi_aclk) begin
        if (f_push) fmem[f_wp] <= m_axis_tdata[23:0];
        if (f_pop)  f_q <= fmem[f_rp];
    end

    // ---------------------------------------------------------------------
    // 时序逻辑
    // ---------------------------------------------------------------------
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            ctrl_r      <= 16'd0;
            stat_only_r <= 1'b0;
            frame_rst   <= 1'b0;
            drain_cnt   <= 3'd0;
            in_cnt      <= 18'd0;
            in_word_r   <= 32'd0;
            in_sel      <= 2'd0;
            in_active   <= 1'b0;
            frame_done  <= 1'b0;
            err_under   <= 1'b0;
            err_over    <= 1'b0;
            stat_min_r  <= 8'd0;
            stat_max_r  <= 8'd0;
            bvalid_r    <= 1'b0;
            rvalid_r    <= 1'b0;
            rdata_r     <= 32'd0;
            rd_wait     <= 1'b0;
            rd_tmo      <= 6'd0;
            aw_got      <= 1'b0;
            w_got       <= 1'b0;
            awaddr_r    <= 5'd0;
            wdata_r     <= 32'd0;
            f_wp        <= 10'd0;
            f_rp        <= 10'd0;
            f_cnt       <= 11'd0;
            pop_pend    <= 1'b0;
            out_word_r  <= 32'd0;
            pk_cnt      <= 3'd0;
        end else begin
            frame_rst <= 1'b0;
            if (drain_cnt != 3'd0) drain_cnt <= drain_cnt - 3'd1;

            // ---- 输入字拆成 4 个像素喂核心 ----
            if (in_fire) begin
                if (in_sel == 2'd3) begin
                    in_active <= 1'b0;
                    in_sel    <= 2'd0;
                end else begin
                    in_sel <= in_sel + 2'd1;
                end
                in_cnt <= in_last ? 18'd0 : (in_cnt + 18'd1);
            end

            // ---- 输出 FIFO 计数 / 指针 ----
            if (f_push) f_wp <= f_wp + 10'd1;
            if (f_pop)  f_rp <= f_rp + 10'd1;
            case ({f_push, f_pop})
                2'b10:   f_cnt <= f_cnt + 11'd1;
                2'b01:   f_cnt <= f_cnt - 11'd1;
                default: ;
            endcase
            if (m_axis_tvalid && (drain_cnt == 3'd0) && (f_cnt == FDEPTH)) err_over <= 1'b1;
            if (m_axis_tvalid && m_axis_tlast && (drain_cnt == 3'd0)) frame_done <= 1'b1;
            pop_pend <= f_pop;

            // ---- 打包: 弹出的像素落进打包寄存器; PS 读走一整字就清空 ----
            if (out_take) begin
                pk_cnt <= 3'd0;
            end else if (pop_pend) begin
                if (color_out) begin
                    out_word_r <= {8'h00, f_q};
                end else begin
                    case (pk_cnt[1:0])
                        2'd0: out_word_r <= {out_word_r[31:8],  f_q[7:0]};
                        2'd1: out_word_r <= {out_word_r[31:16], f_q[7:0], out_word_r[7:0]};
                        2'd2: out_word_r <= {out_word_r[31:24], f_q[7:0], out_word_r[15:0]};
                        default: out_word_r <= {f_q[7:0], out_word_r[23:0]};
                    endcase
                end
                pk_cnt <= pk_cnt + 3'd1;
            end

            // ---- 写: AW / W 分别寄存 ----
            if (s_axi_awready && s_axi_awvalid) begin
                awaddr_r <= s_axi_awaddr;
                aw_got   <= 1'b1;
            end
            if (s_axi_wready && s_axi_wvalid) begin
                wdata_r <= s_axi_wdata;
                w_got   <= 1'b1;
            end

            if (wr_go) begin
                aw_got   <= 1'b0;
                w_got    <= 1'b0;
                bvalid_r <= 1'b1;
                case (awaddr_r)
                    A_CTRL: begin
                        ctrl_r      <= wdata_r[15:0];
                        stat_only_r <= wdata_r[3];
                        frame_rst   <= 1'b1;
                        drain_cnt   <= 3'd7;
                        in_cnt      <= 18'd0;
                        in_active   <= 1'b0;
                        in_sel      <= 2'd0;
                        frame_done  <= 1'b0;
                        err_under   <= 1'b0;
                        err_over    <= 1'b0;
                        f_wp        <= 10'd0;
                        f_rp        <= 10'd0;
                        f_cnt       <= 11'd0;
                        pop_pend    <= 1'b0;
                        pk_cnt      <= 3'd0;
                    end
                    A_IN: begin
                        in_word_r <= wdata_r;
                        in_sel    <= 2'd0;
                        in_active <= 1'b1;
                    end
                    default: ;
                endcase
            end else if (bvalid_r && s_axi_bready) begin
                bvalid_r <= 1'b0;
            end

            // ---- 读 ----
            if (rd_wait) begin
                if (out_full || (rd_tmo == 6'd0)) begin
                    rd_wait  <= 1'b0;
                    rvalid_r <= 1'b1;
                    rdata_r  <= out_word_r;
                    if (~out_full) err_under <= 1'b1;
                end else begin
                    rd_tmo <= rd_tmo - 6'd1;
                end
            end else if (out_read && ~out_full) begin
                rd_wait <= 1'b1;
                rd_tmo  <= 6'd63;
            end else if (ar_hs) begin
                rvalid_r <= 1'b1;
                case (s_axi_araddr)
                    A_STATUS: rdata_r <= {8'd0, stat_max_r, stat_min_r, 2'd0, err_over, err_under,
                                          frame_done, proc_busy_w, out_full, in_ready};
                    A_OUT:    rdata_r <= out_word_r;
                    A_VER:    rdata_r <= VER;
                    A_LEVEL:  rdata_r <= {20'd0, out_level};
                    A_DBGCNT: rdata_r <= {dbg_xy_w, 1'b0, stat_only_r, s_axis_tready,
                                          in_active, in_sel, in_cnt[5:0]};
                    A_DBGWD:  rdata_r <= wdata_r;
                    default:  rdata_r <= 32'd0;
                endcase
            end else if (rvalid_r && s_axi_rready) begin
                rvalid_r <= 1'b0;
            end

            // ---- 统计锁存; 统计遍收完即 frame_done ----
            if (stat_valid_w) begin
                stat_min_r <= stat_min_w;
                stat_max_r <= stat_max_w;
                if (stat_only_r) frame_done <= 1'b1;
            end
        end
    end

endmodule
