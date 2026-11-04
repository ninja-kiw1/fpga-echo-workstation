//=============================================================================
// 模块名   : img_proc_axil_old
// 中文名   : 图像处理顶层 —— AXI4-Lite 寄存器接口版(不用 AXI DMA)
//-----------------------------------------------------------------------------
// 为什么有这个版本
// ----------------
// 本板出厂的 PS 配置里**没有使能任何 PL→PS 的 AXI 从口**, 所以 PL 侧的 master
// (AXI DMA) 根本读不到 DDR —— 实测 S_AXI_HPC0/HP0/HP1/ACP 四个口全部只"悬挂"、
// 连 SLVERR 都没有。而 **PS→PL 的主口是通的**(AXI GPIO 读写已验证)。
// 于是把数据通路翻过来: **由 PS 主动推/拉**, 完全不用 DMA、不碰 DDR 图像区。
//
// 接口
// ----
//   依然是 AXI4-Lite 从机(挂到 PS 的主口上), 寄存器:
//
//     偏移  访问  名称        说明
//     ----  ----  ----------  ------------------------------------------------
//     0x00  W     CTRL        [2:0]=模式 [15:8]=阈值; 写入即复位帧计数器
//     0x04  R     STATUS      [0]=in_ready [1]=out_full [2]=proc_busy
//                             [3]=frame_done(读到最后一个输出后置位)
//                             [15:8]=帧最小灰度 [23:16]=帧最大灰度
//     0x08  W     IN_PIX      **一次写 4 个像素的灰度**:
//                             byte0(bits[7:0])=第 1 个, byte1=第 2 个,
//                             byte2=第 3 个, byte3(bits[31:24])=第 4 个。
//                             只有 in_ready=1 时才被接收(否则 AXI 写背压住 CPU)。
//     0x0C  R     OUT_PIX     **一次读 4 个处理后的像素**(每个只取低 8bit):
//                             读走即弹出。同样按 byte0..byte3 的顺序。
//     0x10  R     VERSION     0x494D_5002
//     0x14/0x18/0x1C  R        调试寄存器 DBGPIX / DBGCNT / DBGWD(定位用)
//
// 一帧的用法(板端程序):
//   写 CTRL -> 循环 65536 次 { 等 in_ready; 写 IN_PIX(4 个灰度) }
//           -> 循环 65536 次 { 等 out_full; 读 OUT_PIX(4 个像素) }
//
//   为什么一次搬 4 个: PS↔PL 单次 32bit 访存约 183 ns(A53 无缓存访问 + 互联 + 协议转换),
//   是整个往返的瓶颈。一次搬 4 个像素把事务数压到 1/4(实测往返 230ms→98ms 是去轮询的功劳,
//   再加宽 4 倍可再砍到 ~1/4)。灰度在 PS 侧算好(和 PL 里 (77R+150G+29B)>>8 完全一致),
//   所以 PL 里喂 {8'h00,g,g,g} 走同一条 img_proc_axis_old, 结果还是 g。
//
// 内部
//----
//   IN_PIX 拆包 -> 4 个 {8'h00,g,g,g} 依次喂 img_proc_axis_old.s_axis
//   img_proc_axis_old.m_axis -> 4 个输出灰度打包进一个 32bit 寄存器 -> OUT_PIX
//   处理本身仍是 img_proc_axis_old(灰度/统计/3x3/mode_map_old), 一行都没改。
//=============================================================================
`timescale 1ns / 1ps

module img_proc_axil_old (
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

    // ---------------------------------------------------------------------
    // 寄存器地址
    // ---------------------------------------------------------------------
    localparam [4:0] A_CTRL   = 5'h00;
    localparam [4:0] A_STATUS = 5'h04;
    localparam [4:0] A_IN     = 5'h08;
    localparam [4:0] A_OUT    = 5'h0C;
    localparam [4:0] A_VER    = 5'h10;
    localparam [4:0] A_DBGPIX = 5'h14;
    localparam [4:0] A_DBGCNT = 5'h18;
    localparam [4:0] A_DBGWD  = 5'h1C;

    localparam integer PIX_CNT  = 512 * 512;      // 262144 个像素
    localparam [17:0]  LAST_CNT = PIX_CNT - 1;
    localparam [31:0]  VER      = 32'h494D_5003;  // 4 像素/次 + 伪彩按整像素传

    // ---------------------------------------------------------------------
    // 寄存器声明(Verilog-2001: 全部放开头)
    // ---------------------------------------------------------------------
    reg  [15:0] ctrl_r;
    reg  [17:0] in_cnt;         // 已喂进处理的**像素**计数
    reg  [31:0] in_word_r;      // PS 写进来的一整字(4 个灰度)
    reg  [1:0]  in_sel;         // 正在喂这个字的第几个字节
    reg         in_active;      // 正在把这 4 个拆开喂进去

    reg  [31:0] out_word_r;     // 攒 4 个输出像素
    reg  [1:0]  out_sel;
    reg         out_full;

    reg         frame_done;
    reg  [7:0]  stat_min_r;
    reg  [7:0]  stat_max_r;

    reg         bvalid_r;
    reg         aw_got;
    reg         w_got;
    reg  [4:0]  awaddr_r;
    reg  [31:0] wdata_r;
    reg         rvalid_r;
    reg  [31:0] rdata_r;

    // ---------------------------------------------------------------------
    // img_proc_axis_old 的连线
    // ---------------------------------------------------------------------
    wire        s_axis_tready;
    wire [31:0] m_axis_tdata;
    wire        m_axis_tvalid;
    wire [7:0]  stat_min_w;
    wire [7:0]  stat_max_w;
    wire        stat_valid_w;
    wire        proc_busy_w;

    // ---- 输入拆包: 取出当前要喂的那个字节, 复制成 {00,g,g,g} ----
    wire [7:0]  in_byte = (in_sel == 2'd0) ? in_word_r[7:0]   :
                          (in_sel == 2'd1) ? in_word_r[15:8]  :
                          (in_sel == 2'd2) ? in_word_r[23:16] : in_word_r[31:24];
    wire [31:0] in_pix_fed = {8'h00, in_byte, in_byte, in_byte};

    wire in_last  = (in_cnt == LAST_CNT);
    wire in_fire  = in_active && s_axis_tready;
    wire in_ready = (~in_active) && s_axis_tready;

    // ---- 输出打包: 攒满 4 个像素才置 out_full ----
    //   灰度模式(0/1/2, 以及默认)输出 {0,v,v,v}, 一次攒 4 个像素(每像素只取低字节);
    //   伪彩模式(3/4) 输出 {0,R,G,B} **真彩色**, 只能一个像素一个字 ——
    //   否则只取低字节就是只拿了蓝通道, 整幅会变成黑白!
    wire        color_out = (ctrl_r[2:0] == 3'd3) || (ctrl_r[2:0] == 3'd4);
    wire        ar_hs     = s_axi_arready && s_axi_arvalid;
    wire        out_take  = ar_hs && (s_axi_araddr == A_OUT) && out_full;
    wire        out_free  = ~out_full;      // 上一组被读走前不再收
    wire        m_tready  = out_free;

    img_proc_axis_old u_proc (
        .aclk         (s_axi_aclk),
        .aresetn      (s_axi_aresetn),
        .s_axis_tdata (in_pix_fed),
        .s_axis_tvalid(in_active),
        .s_axis_tready(s_axis_tready),
        .s_axis_tlast (in_last),
        .m_axis_tdata (m_axis_tdata),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_tready),
        .ctrl         (ctrl_r),
        .stat_min     (stat_min_w),
        .stat_max     (stat_max_w),
        .stat_valid   (stat_valid_w),
        .proc_busy    (proc_busy_w)
    );

    // ---------------------------------------------------------------------
    // 写通道: AW / W 各自独立握手并寄存, 两路都到齐后再执行。
    //   (原实现把 awready/wready 做成"同时到齐"的组合握手, 单测能过, 但接进 BD 后
    //    PS 是 128bit、从机 32bit, 互联插了协议/位宽转换器, 会把 wdata 丢成 0。
    //    改成规范写法后 wdata 正常。详见 测试报告 §9。)
    // ---------------------------------------------------------------------
    wire wr_go = aw_got && w_got && (~bvalid_r) &&
                 ((awaddr_r != A_IN) || in_ready);

    assign s_axi_awready = ~aw_got;
    assign s_axi_wready  = ~w_got;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;

    // ---------------------------------------------------------------------
    // 读通道: 读走 OUT_PIX 那一拍就把它弹掉
    // ---------------------------------------------------------------------
    assign s_axi_arready = ~s_axi_rvalid;
    assign s_axi_rresp   = 2'b00;

    assign s_axi_rvalid = rvalid_r;
    assign s_axi_rdata  = rdata_r;

    // ---------------------------------------------------------------------
    // 时序逻辑
    // ---------------------------------------------------------------------
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            ctrl_r      <= 16'd0;
            in_cnt      <= 18'd0;
            in_word_r   <= 32'd0;
            in_sel      <= 2'd0;
            in_active   <= 1'b0;
            out_word_r  <= 32'd0;
            out_sel     <= 2'd0;
            out_full    <= 1'b0;
            frame_done  <= 1'b0;
            stat_min_r  <= 8'd0;
            stat_max_r  <= 8'd0;
            bvalid_r    <= 1'b0;
            rvalid_r    <= 1'b0;
            rdata_r     <= 32'd0;
            aw_got      <= 1'b0;
            w_got       <= 1'b0;
            awaddr_r    <= 5'd0;
            wdata_r     <= 32'd0;
        end else begin
            // ---- 输入像素被处理流水线接走 ----
            if (in_fire) begin
                if (in_sel == 2'd3) begin
                    in_active <= 1'b0;          // 这个字的 4 个喂完了
                    in_sel    <= 2'd0;
                end else begin
                    in_sel <= in_sel + 2'd1;
                end
                in_cnt <= in_last ? 18'd0 : (in_cnt + 18'd1);
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
                        ctrl_r     <= wdata_r[15:0];
                        in_cnt     <= 18'd0;      /* 新的一帧, 让 tlast 重新数 */
                        frame_done <= 1'b0;
                    end
                    A_IN: begin
                        in_word_r <= wdata_r;     /* 一整字 = 4 个灰度 */
                        in_sel    <= 2'd0;
                        in_active <= 1'b1;
                    end
                    default: ;                    /* STATUS/OUT/VERSION 是只读 */
                endcase
            end else if (bvalid_r && s_axi_bready) begin
                bvalid_r <= 1'b0;
            end

            // ---- 读 ----
            if (ar_hs) begin
                rvalid_r <= 1'b1;
                case (s_axi_araddr)
                    A_STATUS: rdata_r <= {8'd0, stat_max_r, stat_min_r, 4'd0,
                                          frame_done, proc_busy_w, out_full, in_ready};
                    A_OUT:    rdata_r <= out_word_r;
                    A_VER:    rdata_r <= VER;
                    A_DBGPIX: rdata_r <= in_word_r;
                    A_DBGCNT: rdata_r <= {awaddr_r, 7'd0, s_axis_tready, in_active, in_sel, in_cnt[15:0]};
                    A_DBGWD:  rdata_r <= wdata_r;
                    default:  rdata_r <= 32'd0;
                endcase
            end else if (rvalid_r && s_axi_rready) begin
                rvalid_r <= 1'b0;
            end

            // ---- 输出打包: 攒 4 个输出像素 ----
            if (out_take) begin
                out_full <= 1'b0;
            end
            if (m_axis_tvalid && m_tready) begin
                if (color_out) begin
                    out_word_r <= m_axis_tdata;     /* 整像素 {8'h00,R,G,B} */
                    out_sel    <= 2'd0;
                    out_full   <= 1'b1;             /* 一个像素就是一个"字" */
                end else begin
                    case (out_sel)
                        2'd0: out_word_r <= {out_word_r[31:8],  m_axis_tdata[7:0]};
                        2'd1: out_word_r <= {out_word_r[31:16], m_axis_tdata[7:0], out_word_r[7:0]};
                        2'd2: out_word_r <= {out_word_r[31:24], m_axis_tdata[7:0], out_word_r[15:0]};
                        default: out_word_r <= {m_axis_tdata[7:0], out_word_r[23:0]};
                    endcase
                    if (out_sel == 2'd3) begin
                        out_sel  <= 2'd0;
                        out_full <= 1'b1;           /* 这一组齐了, 等 PS 来读 */
                    end else begin
                        out_sel <= out_sel + 2'd1;
                    end
                end
            end

            // ---- 帧统计锁存 ----
            if (stat_valid_w) begin
                stat_min_r <= stat_min_w;
                stat_max_r <= stat_max_w;
            end
        end
    end

endmodule
