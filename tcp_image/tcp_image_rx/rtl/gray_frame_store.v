//=============================================================================
// 模块名   : gray_frame_store
// 中文名   : 灰度帧缓冲与 3x3 窗口读出
//-----------------------------------------------------------------------------
// 功能:
//   写入侧: 逐像素写入 8bit 灰度(顺序地址 0..W*H-1), 同时统计整帧的 min / max。
//   读出侧: 收到 rd_start 后, 按行顺序把整帧读一遍, 每个输出像素给出它的
//           3x3 邻域(9 个 8bit), 供下游做 Sobel 等邻域运算。
//
//   每个输出像素用 4 个周期: R0 读上一行 / R1 读当前行 / R2 读下一行 /
//   EMIT 把这一列的三个行数据移进列移位寄存器并输出窗口。
//
// 端口时序:
//   * 写侧: wr_valid && wr_ready 时写入 wr_pixel; wr_last 表示本帧最后一个像素。
//           一帧写完的下一周期 stat_valid 拉高一个周期, 此时 stat_min/max 有效。
//   * 读侧: rd_start 拉高一周期发起读出; 之后 rd_valid 拉高表示 win_* 有效,
//           rd_last 标记最后一个像素。读出期间 wr_ready 为 0(不能同时写)。
//
// 复位:
//   rst_n 低有效, 异步复位。**帧 RAM 自己不接复位**(块 RAM 不支持异步复位),
//   所以它单独放在一个无复位的 always 块里写。
//
//-----------------------------------------------------------------------------
// 2026-10-01 修复(见 测试报告_2026-10-01.md §11):
//   原实现的读出把"读到的那一拍"当成"数据可用的那一拍"来用移位寄存器 —— 但
//   `rd_data <= ram[rd_addr]` 是**寄存器**(块 RAM 同步读), 数据比状态晚一拍。
//   于是 R0 读到的行落进了"当前行"的抽头里, 窗口中心取到了**上一行**的像素,
//   整幅输出图像**下移一行**(上板实测: out(行 r) = in(行 r-1))。
//   修法: 把三行数据各自落在正确的抽头上 —— 在 R1/R2 拍把晚一拍的 rd_data
//   捕获到 d_top / d_mid, EMIT 拍 rd_data 就是下一行; 再在这三者的基础上
//   移列并输出。这样窗口中心和输出像素严格对齐。
//=============================================================================
`timescale 1ns / 1ps

module gray_frame_store #(
    parameter integer IMG_W = 512,      // 图像宽(像素)
    parameter integer IMG_H = 512,      // 图像高(行)
    parameter integer GW    = 8         // 灰度位宽
) (
    input  wire            clk,
    input  wire            rst_n,

    // ---------------- 写入侧 ----------------
    input  wire            wr_valid,
    input  wire [GW-1:0]   wr_pixel,
    input  wire            wr_last,
    output wire            wr_ready,

    // ---------------- 帧统计 ----------------
    output reg  [GW-1:0]   stat_min,
    output reg  [GW-1:0]   stat_max,
    output reg             stat_valid,

    // ---------------- 读出侧: 3x3 窗口 ----------------
    input  wire            rd_start,
    input  wire            rd_hold,     // 拉高时冻结读出(下游没取走当前像素)
    output wire            rd_busy,     // 读出进行中
    output reg             rd_valid,
    output reg             rd_last,
    output reg  [GW-1:0]   win_top_l,   // 上一行 左
    output reg  [GW-1:0]   win_top_c,   // 上一行 中
    output reg  [GW-1:0]   win_top_r,   // 上一行 右
    output reg  [GW-1:0]   win_mid_l,   // 当前行 左
    output reg  [GW-1:0]   win_mid_c,   // 当前行 中(中心像素)
    output reg  [GW-1:0]   win_mid_r,   // 当前行 右
    output reg  [GW-1:0]   win_bot_l,   // 下一行 左
    output reg  [GW-1:0]   win_bot_c,   // 下一行 中
    output reg  [GW-1:0]   win_bot_r    // 下一行 右
);

    // ---------------------------------------------------------------------
    // 常量
    // ---------------------------------------------------------------------
    localparam integer PIX_CNT  = IMG_W * IMG_H;         // 一帧像素数
    localparam integer AW       = 19;                    // 地址位宽(2^19=524288 > 262144)
    localparam [9:0]   COL_LAST = IMG_W - 1;             // 末列(511)
    localparam [9:0]   ROW_LAST = IMG_H - 1;             // 末行(511)
    localparam [9:0]   COL_END  = IMG_W;                 // 每行读 0..W 共 W+1 列

    localparam [2:0]   ST_IDLE = 3'd0;                   // 等 rd_start
    localparam [2:0]   ST_R0   = 3'd1;                   // 读上一行
    localparam [2:0]   ST_R1   = 3'd2;                   // 读当前行
    localparam [2:0]   ST_R2   = 3'd3;                   // 读下一行
    localparam [2:0]   ST_EMIT = 3'd4;                   // 三行齐 -> 移列 + 输出

    // ---------------------------------------------------------------------
    // 帧 RAM: 262144 x 8 = 2 Mb, 推断成 Block RAM
    // ---------------------------------------------------------------------
    reg [GW-1:0] ram [0:PIX_CNT-1];

    // ---------------------------------------------------------------------
    // 所有寄存器声明集中在此(Verilog-2001 要求先声明后使用)
    // ---------------------------------------------------------------------
    reg  [2:0]    state;
    reg  [2:0]    state_nxt;

    // 写侧
    reg  [AW-1:0] wr_addr;
    reg           wr_first;
    reg  [GW-1:0] min_reg;
    reg  [GW-1:0] max_reg;

    // 读侧
    reg  [9:0]    rd_row;
    reg  [9:0]    rd_col;
    reg  [AW-1:0] rd_addr;
    reg  [GW-1:0] rd_data;

    // 同一列的三个行数据(读出流水, 见文件头"修复"说明)
    reg  [GW-1:0] d_top;
    reg  [GW-1:0] d_mid;

    // 三个行各自的三级移位寄存器(得到左/中/右抽头)
    reg  [GW-1:0] top_sr0, top_sr1, top_sr2;
    reg  [GW-1:0] mid_sr0, mid_sr1, mid_sr2;
    reg  [GW-1:0] bot_sr0, bot_sr1, bot_sr2;

    // ---------------------------------------------------------------------
    // 组合信号
    // ---------------------------------------------------------------------
    // 行地址钳位(边界行重复第一/最后一行, 避免越界并让边缘像素不自成边缘)
    wire [9:0]    row_top = (rd_row == 10'd0)   ? 10'd0    : (rd_row - 10'd1);
    wire [9:0]    row_bot = (rd_row == ROW_LAST) ? ROW_LAST : (rd_row + 10'd1);

    // 列地址: 末列之后钳位到最后一列(读第 W 列时右邻复制末列)。
    wire [9:0]    col_rd  = (rd_col >= COL_END) ? COL_LAST : rd_col;

    wire [AW-1:0] addr_top = (row_top * IMG_W) + col_rd;
    wire [AW-1:0] addr_mid = (rd_row  * IMG_W) + col_rd;
    wire [AW-1:0] addr_bot = (row_bot * IMG_W) + col_rd;

    wire          last_pixel = (rd_row == ROW_LAST) && (rd_col == COL_END);
    wire          ram_wr_en  = wr_valid && (state == ST_IDLE);

    assign wr_ready = (state == ST_IDLE);
    assign rd_busy  = (state != ST_IDLE);

    // ---------------------------------------------------------------------
    // 帧 RAM 写入 —— **单独一个无复位块**
    //   块 RAM 不支持异步复位: 如果把这条写放进带 negedge rst_n 的 always,
    //   Vivado 会报 "RAM is sensitive to asynchronous reset signal" 并退化成
    //   触发器实现(2 Mb 寄存器, 直接爆掉)。这是实测踩过的坑。
    // ---------------------------------------------------------------------
    always @(posedge clk) begin
        if (ram_wr_en) begin
            ram[wr_addr] <= wr_pixel;
        end
    end

    // ---------------------------------------------------------------------
    // 写侧统计与写指针
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_addr    <= {AW{1'b0}};
            wr_first   <= 1'b1;
            min_reg    <= {GW{1'b1}};
            max_reg    <= {GW{1'b0}};
            stat_min   <= {GW{1'b0}};
            stat_max   <= {GW{1'b0}};
            stat_valid <= 1'b0;
        end else begin
            stat_valid <= 1'b0;

            if (ram_wr_en) begin
                if (wr_first) begin
                    // 每帧第一个像素把 min/max 初始化成它自己
                    min_reg  <= wr_pixel;
                    max_reg  <= wr_pixel;
                    wr_first <= 1'b0;
                end else begin
                    if (wr_pixel < min_reg) min_reg <= wr_pixel;
                    if (wr_pixel > max_reg) max_reg <= wr_pixel;
                end

                if (wr_last) begin
                    // 本帧统计完成: 发布 min/max(把最后一个像素也算进去),
                    // 然后复位写指针等下一帧。
                    stat_min   <= (wr_first || (wr_pixel < min_reg)) ? wr_pixel : min_reg;
                    stat_max   <= (wr_first || (wr_pixel > max_reg)) ? wr_pixel : max_reg;
                    stat_valid <= 1'b1;
                    wr_addr    <= {AW{1'b0}};
                    wr_first   <= 1'b1;
                end else begin
                    wr_addr <= wr_addr + 1'b1;
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // 读出状态机: 组合次态
    // ---------------------------------------------------------------------
    always @(*) begin
        state_nxt = state;                      // 默认保持
        case (state)
            ST_IDLE: if (rd_start) state_nxt = ST_R0;
            ST_R0:   state_nxt = ST_R1;
            ST_R1:   state_nxt = ST_R2;
            ST_R2:   state_nxt = ST_EMIT;
            ST_EMIT: state_nxt = last_pixel ? ST_IDLE : ST_R0;
            default: state_nxt = ST_IDLE;
        endcase
    end

    // 当前状态对应的读地址
    always @(*) begin
        case (state)
            ST_R0:   rd_addr = addr_top;
            ST_R1:   rd_addr = addr_mid;
            ST_R2:   rd_addr = addr_bot;
            default: rd_addr = addr_mid;
        endcase
    end

    // ---------------------------------------------------------------------
    // 状态寄存器 + 读数据寄存 + 行列推进
    //   ⚠ rd_data 比 state 晚一拍: 在 R1 拍拿到的 rd_data 是 R0 读的(上一行),
    //     在 R2 拍拿到的是 R1 读的(当前行), 在 EMIT 拍拿到的才是 R2 读的(下一行)。
    //     所以这里在 R1/R2 把前两行分别捕获到 d_top / d_mid, EMIT 拍直接用 rd_data。
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state   <= ST_IDLE;
            rd_row  <= 10'd0;
            rd_col  <= 10'd0;
            rd_data <= {GW{1'b0}};
            d_top   <= {GW{1'b0}};
            d_mid   <= {GW{1'b0}};
        end else if (!rd_hold) begin
            state   <= state_nxt;
            rd_data <= ram[rd_addr];

            if (state == ST_R1) d_top <= rd_data;   // rd_data = R0 读的上一行
            if (state == ST_R2) d_mid <= rd_data;   // rd_data = R1 读的当前行

            if (state == ST_IDLE) begin
                rd_row <= 10'd0;
                rd_col <= 10'd0;
            end else if (state == ST_EMIT) begin
                // 本像素输出完毕, 推进到下一个像素(每行 W+1 列)
                if (last_pixel) begin
                    rd_row <= 10'd0;
                    rd_col <= 10'd0;
                end else if (rd_col == COL_END) begin
                    rd_col <= 10'd0;
                    rd_row <= rd_row + 10'd1;
                end else begin
                    rd_col <= rd_col + 10'd1;
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // 列移位 + 3x3 窗口输出
    //   EMIT 这一拍三个行数据齐了: 上一行 = d_top, 当前行 = d_mid, 下一行 = rd_data。
    //   把它们各推进本行的 3 级移位寄存器; 输出窗口就是移位后的 (sr2, sr1, sr0)。
    //   (用非阻塞赋值, 直接取 sr1/sr0 的旧值 + 本列值 就是移位后的结果。)
    //   每行第 0 列时把三个抽头一起预置成本列值 —— 左边界"复制首列", 而不是上一行残留。
    // ---------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            top_sr0 <= {GW{1'b0}}; top_sr1 <= {GW{1'b0}}; top_sr2 <= {GW{1'b0}};
            mid_sr0 <= {GW{1'b0}}; mid_sr1 <= {GW{1'b0}}; mid_sr2 <= {GW{1'b0}};
            bot_sr0 <= {GW{1'b0}}; bot_sr1 <= {GW{1'b0}}; bot_sr2 <= {GW{1'b0}};

            rd_valid  <= 1'b0;
            rd_last   <= 1'b0;
            win_top_l <= {GW{1'b0}}; win_top_c <= {GW{1'b0}}; win_top_r <= {GW{1'b0}};
            win_mid_l <= {GW{1'b0}}; win_mid_c <= {GW{1'b0}}; win_mid_r <= {GW{1'b0}};
            win_bot_l <= {GW{1'b0}}; win_bot_c <= {GW{1'b0}}; win_bot_r <= {GW{1'b0}};
        end else if (!rd_hold) begin
            if (state == ST_EMIT) begin
                if (rd_col == 10'd0) begin
                    // 首列: 三个抽头一起预置(左边复制首列)
                    top_sr2 <= d_top;    top_sr1 <= d_top;    top_sr0 <= d_top;
                    mid_sr2 <= d_mid;    mid_sr1 <= d_mid;    mid_sr0 <= d_mid;
                    bot_sr2 <= rd_data;  bot_sr1 <= rd_data;  bot_sr0 <= rd_data;
                end else begin
                    top_sr2 <= top_sr1;  top_sr1 <= top_sr0;  top_sr0 <= d_top;
                    mid_sr2 <= mid_sr1;  mid_sr1 <= mid_sr0;  mid_sr0 <= d_mid;
                    bot_sr2 <= bot_sr1;  bot_sr1 <= bot_sr0;  bot_sr0 <= rd_data;
                end
            end

            // 只在 EMIT 且不是第 0 列时输出(第 0 列那次是给首列垫抽头)。
            // 移位后 sr0=本列 c, sr1=c-1, sr2=c-2 -> 窗口中心 = c-1, 正好是本输出像素。
            rd_valid <= (state == ST_EMIT) && (rd_col != 10'd0);
            rd_last  <= (state == ST_EMIT) && last_pixel;

            if (state == ST_EMIT) begin
                win_top_l <= top_sr1;  win_top_c <= top_sr0;  win_top_r <= d_top;
                win_mid_l <= mid_sr1;  win_mid_c <= mid_sr0;  win_mid_r <= d_mid;
                win_bot_l <= bot_sr1;  win_bot_c <= bot_sr0;  win_bot_r <= rd_data;
            end
        end
    end

endmodule
