//=============================================================================
// tb_gfs.v -- gray_frame_store 读出对齐 testbench
//   用 8x8 图, 像素值 = row*W + col (每个像素唯一), 写完读出, 逐像素比对:
//     win_mid_c 必须 = 本输出像素的值 (行、列都对齐)
//     win_top_c/bot_c 为上下邻行(边界复制), win_mid_l/r 为左右邻列(边界复制)
//   期望打印: outputs=64 errors=0  TB_PASS
//=============================================================================
`timescale 1ns / 1ps

module tb_gfs;

    localparam integer W = 8;
    localparam integer H = 8;
    localparam integer N = W * H;

    reg        clk = 0;
    reg        rst_n = 0;
    reg        wr_valid = 0;
    reg  [7:0] wr_pixel = 0;
    reg        wr_last = 0;
    reg        rd_start = 0;
    reg        rd_hold = 0;

    wire       wr_ready, rd_busy, rd_valid, rd_last;
    wire [7:0] t_l, t_c, t_r, m_l, m_c, m_r, b_l, b_c, b_r;
    wire [7:0] stat_min, stat_max;
    wire       stat_valid;

    gray_frame_store #(.IMG_W(W), .IMG_H(H), .GW(8)) dut (
        .clk(clk), .rst_n(rst_n),
        .wr_valid(wr_valid), .wr_pixel(wr_pixel), .wr_last(wr_last), .wr_ready(wr_ready),
        .stat_min(stat_min), .stat_max(stat_max), .stat_valid(stat_valid),
        .rd_start(rd_start), .rd_hold(rd_hold), .rd_busy(rd_busy),
        .rd_valid(rd_valid), .rd_last(rd_last),
        .win_top_l(t_l), .win_top_c(t_c), .win_top_r(t_r),
        .win_mid_l(m_l), .win_mid_c(m_c), .win_mid_r(m_r),
        .win_bot_l(b_l), .win_bot_c(b_c), .win_bot_r(b_r)
    );

    always #5 clk = ~clk;

    integer i, k, row, col, errs, guard;

    // 期望值(行/列都按边界复制钳位)
    function integer val(input integer r, input integer c);
        integer rr, cc;
        begin
            rr = (r < 0) ? 0 : ((r > H-1) ? H-1 : r);
            cc = (c < 0) ? 0 : ((c > W-1) ? W-1 : c);
            val = rr * W + cc;
        end
    endfunction

    initial begin
        errs = 0;
        rst_n = 0;
        repeat (4) @(negedge clk);
        rst_n = 1;
        repeat (2) @(negedge clk);

        // ---- 写一帧: 像素值 = row*W + col ----
        for (i = 0; i < N; i = i + 1) begin
            @(negedge clk);
            wr_valid = 1'b1;
            wr_pixel = i[7:0];
            wr_last  = (i == N-1);
        end
        @(negedge clk);
        wr_valid = 1'b0;
        wr_last  = 1'b0;

        repeat (4) @(negedge clk);
        $display("stat: min=%0d max=%0d (expect 0 / %0d)", stat_min, stat_max, N-1);

        // ---- 发起读出 ----
        @(negedge clk); rd_start = 1'b1;
        @(negedge clk); rd_start = 1'b0;

        // ---- 收 64 个输出并比对 ----
        k = 0;
        guard = 0;
        while (k < N && guard < 5000) begin
            @(negedge clk);
            guard = guard + 1;
            if (rd_valid) begin
                row = k / W;
                col = k % W;
                if (m_c !== val(row, col)) begin
                    $display("ERR k=%0d (r%0d c%0d) mid_c=%0d exp=%0d", k, row, col, m_c, val(row, col));
                    errs = errs + 1;
                end
                if (t_c !== val(row-1, col)) begin
                    $display("ERR k=%0d top_c=%0d exp=%0d", k, t_c, val(row-1, col));
                    errs = errs + 1;
                end
                if (b_c !== val(row+1, col)) begin
                    $display("ERR k=%0d bot_c=%0d exp=%0d", k, b_c, val(row+1, col));
                    errs = errs + 1;
                end
                if (m_l !== val(row, col-1)) begin
                    $display("ERR k=%0d mid_l=%0d exp=%0d", k, m_l, val(row, col-1));
                    errs = errs + 1;
                end
                if (m_r !== val(row, col+1)) begin
                    $display("ERR k=%0d mid_r=%0d exp=%0d", k, m_r, val(row, col+1));
                    errs = errs + 1;
                end
                if (k < 4 || k > N-4)
                    $display("  k=%0d out: mid_c=%0d top_c=%0d bot_c=%0d mid_l=%0d mid_r=%0d",
                             k, m_c, t_c, b_c, m_l, m_r);
                k = k + 1;
            end
        end

        $display("outputs=%0d errors=%0d", k, errs);
        if (errs == 0 && k == N) $display("TB_PASS");
        else                      $display("TB_FAIL");
        $finish;
    end

endmodule
