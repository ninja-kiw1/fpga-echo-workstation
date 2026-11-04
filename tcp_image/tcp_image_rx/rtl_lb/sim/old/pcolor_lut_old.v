//=============================================================================
// 模块名   : pcolor_lut_old
// 中文名   : jet 伪彩色查找表
//-----------------------------------------------------------------------------
// 把 8bit 灰度映射成 24bit 颜色 {R,G,B}, 即经典 jet(彩虹)配色。
//
//   x = i / 255
//   r = clamp(1.5 - |4x - 3|, 0, 1)
//   g = clamp(1.5 - |4x - 2|, 0, 1)
//   b = clamp(1.5 - |4x - 1|, 0, 1)
//   每分量按 round(v*255 + 0.5) 取整
//
// 一个模块要例化两次: 一次查"原始灰度", 一次查"归一化后的灰度"(组合模式用)。
// 表不大(256 x 24 = 6 Kb), 两份也就 12 Kb, 用分布式 RAM 实现。
//
// ⚠ 表是自动生成的(公式见 docs/图像处理设计.md 5.5)。改公式必须重新生成本表。
//=============================================================================
`timescale 1ns / 1ps

module pcolor_lut_old (
    input  wire [7:0]   gray,
    output wire [23:0]  pcolor
);

    reg [23:0] pcolor_rom [0:255];

    initial begin
        pcolor_rom[  0] = 24'h000080; pcolor_rom[  1] = 24'h000084;
        pcolor_rom[  2] = 24'h000088; pcolor_rom[  3] = 24'h00008C;
        pcolor_rom[  4] = 24'h000090; pcolor_rom[  5] = 24'h000093;
        pcolor_rom[  6] = 24'h000098; pcolor_rom[  7] = 24'h00009C;
        pcolor_rom[  8] = 24'h0000A0; pcolor_rom[  9] = 24'h0000A3;
        pcolor_rom[ 10] = 24'h0000A8; pcolor_rom[ 11] = 24'h0000AC;
        pcolor_rom[ 12] = 24'h0000B0; pcolor_rom[ 13] = 24'h0000B3;
        pcolor_rom[ 14] = 24'h0000B8; pcolor_rom[ 15] = 24'h0000BC;
        pcolor_rom[ 16] = 24'h0000C0; pcolor_rom[ 17] = 24'h0000C3;
        pcolor_rom[ 18] = 24'h0000C8; pcolor_rom[ 19] = 24'h0000CC;
        pcolor_rom[ 20] = 24'h0000D0; pcolor_rom[ 21] = 24'h0000D3;
        pcolor_rom[ 22] = 24'h0000D8; pcolor_rom[ 23] = 24'h0000DC;
        pcolor_rom[ 24] = 24'h0000E0; pcolor_rom[ 25] = 24'h0000E3;
        pcolor_rom[ 26] = 24'h0000E8; pcolor_rom[ 27] = 24'h0000EC;
        pcolor_rom[ 28] = 24'h0000F0; pcolor_rom[ 29] = 24'h0000F3;
        pcolor_rom[ 30] = 24'h0000F8; pcolor_rom[ 31] = 24'h0000FC;
        pcolor_rom[ 32] = 24'h0000FF; pcolor_rom[ 33] = 24'h0004FF;
        pcolor_rom[ 34] = 24'h0008FF; pcolor_rom[ 35] = 24'h000DFF;
        pcolor_rom[ 36] = 24'h0010FF; pcolor_rom[ 37] = 24'h0015FF;
        pcolor_rom[ 38] = 24'h0019FF; pcolor_rom[ 39] = 24'h001DFF;
        pcolor_rom[ 40] = 24'h0021FF; pcolor_rom[ 41] = 24'h0024FF;
        pcolor_rom[ 42] = 24'h0028FF; pcolor_rom[ 43] = 24'h002DFF;
        pcolor_rom[ 44] = 24'h0031FF; pcolor_rom[ 45] = 24'h0035FF;
        pcolor_rom[ 46] = 24'h0039FF; pcolor_rom[ 47] = 24'h003DFF;
        pcolor_rom[ 48] = 24'h0041FF; pcolor_rom[ 49] = 24'h0044FF;
        pcolor_rom[ 50] = 24'h0048FF; pcolor_rom[ 51] = 24'h004DFF;
        pcolor_rom[ 52] = 24'h0051FF; pcolor_rom[ 53] = 24'h0055FF;
        pcolor_rom[ 54] = 24'h0059FF; pcolor_rom[ 55] = 24'h005DFF;
        pcolor_rom[ 56] = 24'h0061FF; pcolor_rom[ 57] = 24'h0064FF;
        pcolor_rom[ 58] = 24'h0068FF; pcolor_rom[ 59] = 24'h006DFF;
        pcolor_rom[ 60] = 24'h0071FF; pcolor_rom[ 61] = 24'h0075FF;
        pcolor_rom[ 62] = 24'h0079FF; pcolor_rom[ 63] = 24'h007DFF;
        pcolor_rom[ 64] = 24'h0081FF; pcolor_rom[ 65] = 24'h0084FF;
        pcolor_rom[ 66] = 24'h0089FF; pcolor_rom[ 67] = 24'h008DFF;
        pcolor_rom[ 68] = 24'h0091FF; pcolor_rom[ 69] = 24'h0094FF;
        pcolor_rom[ 70] = 24'h0099FF; pcolor_rom[ 71] = 24'h009DFF;
        pcolor_rom[ 72] = 24'h00A1FF; pcolor_rom[ 73] = 24'h00A4FF;
        pcolor_rom[ 74] = 24'h00A9FF; pcolor_rom[ 75] = 24'h00ADFF;
        pcolor_rom[ 76] = 24'h00B1FF; pcolor_rom[ 77] = 24'h00B4FF;
        pcolor_rom[ 78] = 24'h00B9FF; pcolor_rom[ 79] = 24'h00BDFF;
        pcolor_rom[ 80] = 24'h00C1FF; pcolor_rom[ 81] = 24'h00C4FF;
        pcolor_rom[ 82] = 24'h00C9FF; pcolor_rom[ 83] = 24'h00CDFF;
        pcolor_rom[ 84] = 24'h00D1FF; pcolor_rom[ 85] = 24'h00D4FF;
        pcolor_rom[ 86] = 24'h00D9FF; pcolor_rom[ 87] = 24'h00DDFF;
        pcolor_rom[ 88] = 24'h00E1FF; pcolor_rom[ 89] = 24'h00E4FF;
        pcolor_rom[ 90] = 24'h00E9FF; pcolor_rom[ 91] = 24'h00EDFF;
        pcolor_rom[ 92] = 24'h00F1FF; pcolor_rom[ 93] = 24'h00F4FF;
        pcolor_rom[ 94] = 24'h00F9FF; pcolor_rom[ 95] = 24'h00FDFF;
        pcolor_rom[ 96] = 24'h01FFFE; pcolor_rom[ 97] = 24'h05FFFA;
        pcolor_rom[ 98] = 24'h0AFFF5; pcolor_rom[ 99] = 24'h0EFFF2;
        pcolor_rom[100] = 24'h11FFEE; pcolor_rom[101] = 24'h15FFEA;
        pcolor_rom[102] = 24'h1AFFE5; pcolor_rom[103] = 24'h1EFFE2;
        pcolor_rom[104] = 24'h21FFDE; pcolor_rom[105] = 24'h25FFDA;
        pcolor_rom[106] = 24'h2AFFD5; pcolor_rom[107] = 24'h2EFFD2;
        pcolor_rom[108] = 24'h31FFCE; pcolor_rom[109] = 24'h35FFCA;
        pcolor_rom[110] = 24'h3AFFC5; pcolor_rom[111] = 24'h3EFFC2;
        pcolor_rom[112] = 24'h42FFBE; pcolor_rom[113] = 24'h45FFBA;
        pcolor_rom[114] = 24'h4AFFB5; pcolor_rom[115] = 24'h4EFFB2;
        pcolor_rom[116] = 24'h52FFAE; pcolor_rom[117] = 24'h55FFAA;
        pcolor_rom[118] = 24'h5AFFA5; pcolor_rom[119] = 24'h5EFFA2;
        pcolor_rom[120] = 24'h62FF9E; pcolor_rom[121] = 24'h65FF9A;
        pcolor_rom[122] = 24'h6AFF95; pcolor_rom[123] = 24'h6EFF92;
        pcolor_rom[124] = 24'h72FF8E; pcolor_rom[125] = 24'h75FF8A;
        pcolor_rom[126] = 24'h7AFF85; pcolor_rom[127] = 24'h7EFF82;
        pcolor_rom[128] = 24'h82FF7E; pcolor_rom[129] = 24'h85FF7A;
        pcolor_rom[130] = 24'h89FF76; pcolor_rom[131] = 24'h8DFF72;
        pcolor_rom[132] = 24'h92FF6D; pcolor_rom[133] = 24'h96FF69;
        pcolor_rom[134] = 24'h9AFF65; pcolor_rom[135] = 24'h9EFF62;
        pcolor_rom[136] = 24'hA2FF5E; pcolor_rom[137] = 24'hA5FF5A;
        pcolor_rom[138] = 24'hA9FF56; pcolor_rom[139] = 24'hADFF52;
        pcolor_rom[140] = 24'hB2FF4D; pcolor_rom[141] = 24'hB6FF49;
        pcolor_rom[142] = 24'hBAFF45; pcolor_rom[143] = 24'hBEFF42;
        pcolor_rom[144] = 24'hC2FF3E; pcolor_rom[145] = 24'hC5FF3A;
        pcolor_rom[146] = 24'hC9FF36; pcolor_rom[147] = 24'hCDFF32;
        pcolor_rom[148] = 24'hD2FF2D; pcolor_rom[149] = 24'hD6FF29;
        pcolor_rom[150] = 24'hDAFF25; pcolor_rom[151] = 24'hDEFF21;
        pcolor_rom[152] = 24'hE2FF1E; pcolor_rom[153] = 24'hE5FF1A;
        pcolor_rom[154] = 24'hE9FF16; pcolor_rom[155] = 24'hEDFF12;
        pcolor_rom[156] = 24'hF2FF0D; pcolor_rom[157] = 24'hF6FF09;
        pcolor_rom[158] = 24'hFAFF05; pcolor_rom[159] = 24'hFEFF01;
        pcolor_rom[160] = 24'hFFFD00; pcolor_rom[161] = 24'hFFF900;
        pcolor_rom[162] = 24'hFFF500; pcolor_rom[163] = 24'hFFF100;
        pcolor_rom[164] = 24'hFFEC00; pcolor_rom[165] = 24'hFFE800;
        pcolor_rom[166] = 24'hFFE400; pcolor_rom[167] = 24'hFFE100;
        pcolor_rom[168] = 24'hFFDD00; pcolor_rom[169] = 24'hFFD900;
        pcolor_rom[170] = 24'hFFD500; pcolor_rom[171] = 24'hFFD100;
        pcolor_rom[172] = 24'hFFCC00; pcolor_rom[173] = 24'hFFC800;
        pcolor_rom[174] = 24'hFFC400; pcolor_rom[175] = 24'hFFC100;
        pcolor_rom[176] = 24'hFFBD00; pcolor_rom[177] = 24'hFFB900;
        pcolor_rom[178] = 24'hFFB500; pcolor_rom[179] = 24'hFFB100;
        pcolor_rom[180] = 24'hFFAC00; pcolor_rom[181] = 24'hFFA800;
        pcolor_rom[182] = 24'hFFA400; pcolor_rom[183] = 24'hFFA100;
        pcolor_rom[184] = 24'hFF9D00; pcolor_rom[185] = 24'hFF9900;
        pcolor_rom[186] = 24'hFF9500; pcolor_rom[187] = 24'hFF9100;
        pcolor_rom[188] = 24'hFF8C00; pcolor_rom[189] = 24'hFF8800;
        pcolor_rom[190] = 24'hFF8400; pcolor_rom[191] = 24'hFF8100;
        pcolor_rom[192] = 24'hFF7D00; pcolor_rom[193] = 24'hFF7900;
        pcolor_rom[194] = 24'hFF7500; pcolor_rom[195] = 24'hFF7100;
        pcolor_rom[196] = 24'hFF6C00; pcolor_rom[197] = 24'hFF6800;
        pcolor_rom[198] = 24'hFF6400; pcolor_rom[199] = 24'hFF6100;
        pcolor_rom[200] = 24'hFF5D00; pcolor_rom[201] = 24'hFF5900;
        pcolor_rom[202] = 24'hFF5500; pcolor_rom[203] = 24'hFF5100;
        pcolor_rom[204] = 24'hFF4C00; pcolor_rom[205] = 24'hFF4800;
        pcolor_rom[206] = 24'hFF4400; pcolor_rom[207] = 24'hFF4100;
        pcolor_rom[208] = 24'hFF3D00; pcolor_rom[209] = 24'hFF3900;
        pcolor_rom[210] = 24'hFF3500; pcolor_rom[211] = 24'hFF3100;
        pcolor_rom[212] = 24'hFF2C00; pcolor_rom[213] = 24'hFF2800;
        pcolor_rom[214] = 24'hFF2400; pcolor_rom[215] = 24'hFF2100;
        pcolor_rom[216] = 24'hFF1D00; pcolor_rom[217] = 24'hFF1900;
        pcolor_rom[218] = 24'hFF1500; pcolor_rom[219] = 24'hFF1100;
        pcolor_rom[220] = 24'hFF0C00; pcolor_rom[221] = 24'hFF0800;
        pcolor_rom[222] = 24'hFF0400; pcolor_rom[223] = 24'hFF0000;
        pcolor_rom[224] = 24'hFC0000; pcolor_rom[225] = 24'hF80000;
        pcolor_rom[226] = 24'hF40000; pcolor_rom[227] = 24'hF00000;
        pcolor_rom[228] = 24'hEB0000; pcolor_rom[229] = 24'hE70000;
        pcolor_rom[230] = 24'hE30000; pcolor_rom[231] = 24'hE00000;
        pcolor_rom[232] = 24'hDC0000; pcolor_rom[233] = 24'hD80000;
        pcolor_rom[234] = 24'hD40000; pcolor_rom[235] = 24'hD00000;
        pcolor_rom[236] = 24'hCB0000; pcolor_rom[237] = 24'hC70000;
        pcolor_rom[238] = 24'hC30000; pcolor_rom[239] = 24'hC00000;
        pcolor_rom[240] = 24'hBC0000; pcolor_rom[241] = 24'hB80000;
        pcolor_rom[242] = 24'hB40000; pcolor_rom[243] = 24'hB00000;
        pcolor_rom[244] = 24'hAB0000; pcolor_rom[245] = 24'hA70000;
        pcolor_rom[246] = 24'hA30000; pcolor_rom[247] = 24'hA00000;
        pcolor_rom[248] = 24'h9C0000; pcolor_rom[249] = 24'h980000;
        pcolor_rom[250] = 24'h940000; pcolor_rom[251] = 24'h900000;
        pcolor_rom[252] = 24'h8B0000; pcolor_rom[253] = 24'h870000;
        pcolor_rom[254] = 24'h840000; pcolor_rom[255] = 24'h800000;
    end

    assign pcolor = pcolor_rom[gray];

endmodule
