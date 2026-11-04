// tb_equiv.sv -- 行缓冲版 img_proc_axil(rtl_lb) 与原整帧版(rtl, 改名 *_old) 逐字节对比
//
// 两个 DUT 各挂一条 AXI-Lite, 各按自己的驱动走法跑同一张图、同一个模式:
//   原版: CTRL -> 写 65536 字 -> (每字先查 out_full) 读 65536 字 / 262144 字(彩色)
//   新版: (模式 0/4 先送一遍只统计) -> CTRL -> 逐行: 写第 r 行 128 字, 等 OUT_LEVEL>=512 读第 r-1 行 ...
// 比对全部输出像素(灰度取低字节, 彩色取 24bit)和 STATUS 的 min/max, 并检查新版的粘滞错误位。
// 新版的访问间隔随机(+GMIN / +GMAX 拍), 默认 0..24, 也覆盖"读得比 PL 产出还快"的情况。
//
// plusargs: +IMGS=<位掩码>  +MODES=<位掩码>  +GMIN= +GMAX=
`timescale 1ns / 1ps

module tb_equiv;
    reg clk = 0;
    reg rstn = 0;
    always #5 clk = ~clk;

    // ---------------- 两条总线 ----------------
    reg  [4:0]  o_awaddr, n_awaddr, o_araddr, n_araddr;
    reg         o_awvalid, n_awvalid, o_wvalid, n_wvalid, o_bready, n_bready, o_arvalid, n_arvalid, o_rready, n_rready;
    reg  [31:0] o_wdata, n_wdata;
    wire        o_awready, n_awready, o_wready, n_wready, o_bvalid, n_bvalid, o_arready, n_arready, o_rvalid, n_rvalid;
    wire [1:0]  o_bresp, n_bresp, o_rresp, n_rresp;
    wire [31:0] o_rdata, n_rdata;

    img_proc_axil_old u_old (
        .s_axi_aclk(clk), .s_axi_aresetn(rstn),
        .s_axi_awaddr(o_awaddr), .s_axi_awvalid(o_awvalid), .s_axi_awready(o_awready),
        .s_axi_wdata(o_wdata), .s_axi_wstrb(4'hF), .s_axi_wvalid(o_wvalid), .s_axi_wready(o_wready),
        .s_axi_bresp(o_bresp), .s_axi_bvalid(o_bvalid), .s_axi_bready(o_bready),
        .s_axi_araddr(o_araddr), .s_axi_arvalid(o_arvalid), .s_axi_arready(o_arready),
        .s_axi_rdata(o_rdata), .s_axi_rresp(o_rresp), .s_axi_rvalid(o_rvalid), .s_axi_rready(o_rready));

    img_proc_axil u_new (
        .s_axi_aclk(clk), .s_axi_aresetn(rstn),
        .s_axi_awaddr(n_awaddr), .s_axi_awvalid(n_awvalid), .s_axi_awready(n_awready),
        .s_axi_wdata(n_wdata), .s_axi_wstrb(4'hF), .s_axi_wvalid(n_wvalid), .s_axi_wready(n_wready),
        .s_axi_bresp(n_bresp), .s_axi_bvalid(n_bvalid), .s_axi_bready(n_bready),
        .s_axi_araddr(n_araddr), .s_axi_arvalid(n_arvalid), .s_axi_arready(n_arready),
        .s_axi_rdata(n_rdata), .s_axi_rresp(n_rresp), .s_axi_rvalid(n_rvalid), .s_axi_rready(n_rready));

    integer gmin = 0, gmax = 24;

    task automatic gap(input integer lo, input integer hi);
        integer k, n;
        begin
            n = (hi > lo) ? (lo + ($urandom % (hi - lo + 1))) : lo;
            for (k = 0; k < n; k = k + 1) @(posedge clk);
        end
    endtask

    // ---- 原版总线 ----
    task automatic owr(input [4:0] a, input [31:0] d);
        begin
            @(posedge clk);
            o_awaddr <= a; o_awvalid <= 1; o_wdata <= d; o_wvalid <= 1; o_bready <= 1;
            fork
                begin @(posedge clk); while (!o_awready) @(posedge clk); o_awvalid <= 0; end
                begin @(posedge clk); while (!o_wready)  @(posedge clk); o_wvalid  <= 0; end
            join
            while (!o_bvalid) @(posedge clk);
            @(posedge clk); o_bready <= 0;
        end
    endtask
    task automatic ord(input [4:0] a, output [31:0] d);
        begin
            @(posedge clk);
            o_araddr <= a; o_arvalid <= 1; o_rready <= 1;
            @(posedge clk); while (!o_arready) @(posedge clk);
            o_arvalid <= 0;
            while (!o_rvalid) @(posedge clk);
            d = o_rdata;
            @(posedge clk); o_rready <= 0;
        end
    endtask
    // ---- 新版总线 ----
    task automatic nwr(input [4:0] a, input [31:0] d);
        begin
            @(posedge clk);
            n_awaddr <= a; n_awvalid <= 1; n_wdata <= d; n_wvalid <= 1; n_bready <= 1;
            fork
                begin @(posedge clk); while (!n_awready) @(posedge clk); n_awvalid <= 0; end
                begin @(posedge clk); while (!n_wready)  @(posedge clk); n_wvalid  <= 0; end
            join
            while (!n_bvalid) @(posedge clk);
            @(posedge clk); n_bready <= 0;
        end
    endtask
    task automatic nrd(input [4:0] a, output [31:0] d);
        begin
            @(posedge clk);
            n_araddr <= a; n_arvalid <= 1; n_rready <= 1;
            @(posedge clk); while (!n_arready) @(posedge clk);
            n_arvalid <= 0;
            while (!n_rvalid) @(posedge clk);
            d = n_rdata;
            @(posedge clk); n_rready <= 0;
        end
    endtask

    reg [7:0]  img  [0:262143];
    reg [23:0] oref [0:262143];
    reg [23:0] onew [0:262143];

    function automatic [31:0] word4(input integer w);
        word4 = {img[4*w+3], img[4*w+2], img[4*w+1], img[4*w]};
    endfunction

    integer total_fail = 0, frames = 0;

    task automatic run_old(input integer mode, input integer thr, output [7:0] mn, output [7:0] mx);
        integer w, k, color, nw;
        reg [31:0] v;
        begin
            color = (mode == 3 || mode == 4);
            owr(5'h00, (mode & 7) | ((thr & 255) << 8));
            for (w = 0; w < 65536; w = w + 1) owr(5'h08, word4(w));
            nw = color ? 262144 : 65536;
            for (w = 0; w < nw; w = w + 1) begin
                v = 0;
                while (!v[1]) ord(5'h04, v);   // out_full
                ord(5'h0C, v);
                if (color) oref[w] = v[23:0];
                else for (k = 0; k < 4; k = k + 1) oref[4*w+k] = {16'd0, v[8*k +: 8]};
            end
            ord(5'h04, v);
            mn = v[15:8]; mx = v[23:16];
        end
    endtask

    task automatic new_read_row(input integer r, input integer color);
        integer w, k, nw, spins;
        reg [31:0] v;
        begin
            nw = color ? 512 : 128;
            v = 0; spins = 0;
            while (v[11:0] < 512) begin
                nrd(5'h14, v);
                spins = spins + 1;
                if (spins > 10000) begin $display("FATAL: OUT_LEVEL stuck at %0d (row %0d)", v[11:0], r); $finish; end
            end
            for (w = 0; w < nw; w = w + 1) begin
                gap(gmin, gmax);
                nrd(5'h0C, v);
                if (color) onew[r*512 + w] = v[23:0];
                else for (k = 0; k < 4; k = k + 1) onew[r*512 + 4*w + k] = {16'd0, v[8*k +: 8]};
            end
        end
    endtask

    task automatic run_new(input integer mode, input integer thr, output [7:0] mn, output [7:0] mx,
                           output integer errs);
        integer w, r, color;
        reg [31:0] v;
        begin
            color = (mode == 3 || mode == 4);
            if (mode == 0 || mode == 4) begin
                // 第一遍: 只统计
                nwr(5'h00, (mode & 7) | 8 | ((thr & 255) << 8));
                for (w = 0; w < 65536; w = w + 1) begin gap(gmin, gmax); nwr(5'h08, word4(w)); end
                v = 0;
                while (!v[3]) nrd(5'h04, v);   // frame_done
            end
            nwr(5'h00, (mode & 7) | ((thr & 255) << 8));
            for (r = 0; r < 512; r = r + 1) begin
                for (w = 0; w < 128; w = w + 1) begin gap(gmin, gmax); nwr(5'h08, word4(r*128 + w)); end
                if (r >= 1) new_read_row(r - 1, color);
            end
            new_read_row(511, color);
            nrd(5'h04, v);
            mn = v[15:8]; mx = v[23:16];
            errs = v[5:4];
            nrd(5'h14, v);
            if (v[11:0] != 0) begin $display("  leftover OUT_LEVEL %0d", v[11:0]); errs = errs + 4; end
        end
    endtask

    integer imask = 15, mmask = 63;
    integer ii, m, thr, i, bad, first, errs;
    reg [7:0] omn, omx, nmn, nmx;
    reg [8*16-1:0] fname;

    initial begin
        if ($value$plusargs("IMGS=%d", imask)) ;
        if ($value$plusargs("MODES=%d", mmask)) ;
        if ($value$plusargs("GMIN=%d", gmin)) ;
        if ($value$plusargs("GMAX=%d", gmax)) ;
        {o_awvalid, o_wvalid, o_bready, o_arvalid, o_rready} = 0;
        {n_awvalid, n_wvalid, n_bready, n_arvalid, n_rready} = 0;
        o_awaddr = 0; o_araddr = 0; o_wdata = 0; n_awaddr = 0; n_araddr = 0; n_wdata = 0;
        repeat (5) @(posedge clk);
        rstn = 1;
        repeat (5) @(posedge clk);

        for (ii = 0; ii < 4; ii = ii + 1) begin
            if (!imask[ii]) continue;
            case (ii)
                0: $readmemh("img0.hex", img);
                1: $readmemh("img1.hex", img);
                2: $readmemh("img2.hex", img);
                default: $readmemh("img3.hex", img);
            endcase
            for (m = 0; m < 6; m = m + 1) begin
                if (!mmask[m]) continue;
                thr = (m == 1 && ii == 3) ? 81 : 128;
                fork
                    run_old(m, thr, omn, omx);
                    run_new(m, thr, nmn, nmx, errs);
                join
                bad = 0; first = -1;
                for (i = 0; i < 262144; i = i + 1)
                    if (oref[i] !== onew[i]) begin bad = bad + 1; if (first < 0) first = i; end
                frames = frames + 1;
                if (bad != 0 || omn !== nmn || omx !== nmx || errs != 0) begin
                    total_fail = total_fail + 1;
                    $display("FAIL img%0d mode%0d thr=%0d: %0d px differ (first %0d: old %06h new %06h), min/max old %0d/%0d new %0d/%0d, err bits %0d",
                             ii, m, thr, bad, first, (first >= 0) ? oref[first] : 0, (first >= 0) ? onew[first] : 0,
                             omn, omx, nmn, nmx, errs);
                end else begin
                    $display("PASS img%0d mode%0d thr=%0d: 262144 px identical, min/max %0d/%0d  (t=%0t)", ii, m, thr, nmn, nmx, $time);
                end
            end
        end
        $display("SUMMARY frames=%0d failures=%0d", frames, total_fail);
        $finish;
    end
endmodule
