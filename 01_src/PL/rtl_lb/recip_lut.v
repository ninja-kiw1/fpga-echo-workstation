//=============================================================================
// 模块名   : recip_lut
// 中文名   : 归一化倒数查找表
//-----------------------------------------------------------------------------
// recip[r] = floor(65280 / r),  r = 1..255   (r=0 由调用方兜底成 1)
//
// 用途: 灰度归一化本来要除以 (max-min)。PL 里做除法很贵, 于是改成
//       norm = ((gray - min) * recip[range]) >> 8, 再 clamp 到 255。
//       逐像素只剩一次 8x16 乘法和一次移位。
//
// ⚠ 表是自动生成的(公式见 docs/图像处理设计.md 5.2)。改公式必须重新生成本表。
//=============================================================================
`timescale 1ns / 1ps

module recip_lut (
    input  wire [7:0]   range,      // 归一化除数 = max - min, 取值 1..255
    output wire [15:0]  recip
);

    reg [15:0] recip_rom [0:255];

    initial begin
        recip_rom[  0] = 16'd0    ; recip_rom[  1] = 16'd65280; recip_rom[  2] = 16'd32640; recip_rom[  3] = 16'd21760;
        recip_rom[  4] = 16'd16320; recip_rom[  5] = 16'd13056; recip_rom[  6] = 16'd10880; recip_rom[  7] = 16'd9325 ;
        recip_rom[  8] = 16'd8160 ; recip_rom[  9] = 16'd7253 ; recip_rom[ 10] = 16'd6528 ; recip_rom[ 11] = 16'd5934 ;
        recip_rom[ 12] = 16'd5440 ; recip_rom[ 13] = 16'd5021 ; recip_rom[ 14] = 16'd4662 ; recip_rom[ 15] = 16'd4352 ;
        recip_rom[ 16] = 16'd4080 ; recip_rom[ 17] = 16'd3840 ; recip_rom[ 18] = 16'd3626 ; recip_rom[ 19] = 16'd3435 ;
        recip_rom[ 20] = 16'd3264 ; recip_rom[ 21] = 16'd3108 ; recip_rom[ 22] = 16'd2967 ; recip_rom[ 23] = 16'd2838 ;
        recip_rom[ 24] = 16'd2720 ; recip_rom[ 25] = 16'd2611 ; recip_rom[ 26] = 16'd2510 ; recip_rom[ 27] = 16'd2417 ;
        recip_rom[ 28] = 16'd2331 ; recip_rom[ 29] = 16'd2251 ; recip_rom[ 30] = 16'd2176 ; recip_rom[ 31] = 16'd2105 ;
        recip_rom[ 32] = 16'd2040 ; recip_rom[ 33] = 16'd1978 ; recip_rom[ 34] = 16'd1920 ; recip_rom[ 35] = 16'd1865 ;
        recip_rom[ 36] = 16'd1813 ; recip_rom[ 37] = 16'd1764 ; recip_rom[ 38] = 16'd1717 ; recip_rom[ 39] = 16'd1673 ;
        recip_rom[ 40] = 16'd1632 ; recip_rom[ 41] = 16'd1592 ; recip_rom[ 42] = 16'd1554 ; recip_rom[ 43] = 16'd1518 ;
        recip_rom[ 44] = 16'd1483 ; recip_rom[ 45] = 16'd1450 ; recip_rom[ 46] = 16'd1419 ; recip_rom[ 47] = 16'd1388 ;
        recip_rom[ 48] = 16'd1360 ; recip_rom[ 49] = 16'd1332 ; recip_rom[ 50] = 16'd1305 ; recip_rom[ 51] = 16'd1280 ;
        recip_rom[ 52] = 16'd1255 ; recip_rom[ 53] = 16'd1231 ; recip_rom[ 54] = 16'd1208 ; recip_rom[ 55] = 16'd1186 ;
        recip_rom[ 56] = 16'd1165 ; recip_rom[ 57] = 16'd1145 ; recip_rom[ 58] = 16'd1125 ; recip_rom[ 59] = 16'd1106 ;
        recip_rom[ 60] = 16'd1088 ; recip_rom[ 61] = 16'd1070 ; recip_rom[ 62] = 16'd1052 ; recip_rom[ 63] = 16'd1036 ;
        recip_rom[ 64] = 16'd1020 ; recip_rom[ 65] = 16'd1004 ; recip_rom[ 66] = 16'd989  ; recip_rom[ 67] = 16'd974  ;
        recip_rom[ 68] = 16'd960  ; recip_rom[ 69] = 16'd946  ; recip_rom[ 70] = 16'd932  ; recip_rom[ 71] = 16'd919  ;
        recip_rom[ 72] = 16'd906  ; recip_rom[ 73] = 16'd894  ; recip_rom[ 74] = 16'd882  ; recip_rom[ 75] = 16'd870  ;
        recip_rom[ 76] = 16'd858  ; recip_rom[ 77] = 16'd847  ; recip_rom[ 78] = 16'd836  ; recip_rom[ 79] = 16'd826  ;
        recip_rom[ 80] = 16'd816  ; recip_rom[ 81] = 16'd805  ; recip_rom[ 82] = 16'd796  ; recip_rom[ 83] = 16'd786  ;
        recip_rom[ 84] = 16'd777  ; recip_rom[ 85] = 16'd768  ; recip_rom[ 86] = 16'd759  ; recip_rom[ 87] = 16'd750  ;
        recip_rom[ 88] = 16'd741  ; recip_rom[ 89] = 16'd733  ; recip_rom[ 90] = 16'd725  ; recip_rom[ 91] = 16'd717  ;
        recip_rom[ 92] = 16'd709  ; recip_rom[ 93] = 16'd701  ; recip_rom[ 94] = 16'd694  ; recip_rom[ 95] = 16'd687  ;
        recip_rom[ 96] = 16'd680  ; recip_rom[ 97] = 16'd672  ; recip_rom[ 98] = 16'd666  ; recip_rom[ 99] = 16'd659  ;
        recip_rom[100] = 16'd652  ; recip_rom[101] = 16'd646  ; recip_rom[102] = 16'd640  ; recip_rom[103] = 16'd633  ;
        recip_rom[104] = 16'd627  ; recip_rom[105] = 16'd621  ; recip_rom[106] = 16'd615  ; recip_rom[107] = 16'd610  ;
        recip_rom[108] = 16'd604  ; recip_rom[109] = 16'd598  ; recip_rom[110] = 16'd593  ; recip_rom[111] = 16'd588  ;
        recip_rom[112] = 16'd582  ; recip_rom[113] = 16'd577  ; recip_rom[114] = 16'd572  ; recip_rom[115] = 16'd567  ;
        recip_rom[116] = 16'd562  ; recip_rom[117] = 16'd557  ; recip_rom[118] = 16'd553  ; recip_rom[119] = 16'd548  ;
        recip_rom[120] = 16'd544  ; recip_rom[121] = 16'd539  ; recip_rom[122] = 16'd535  ; recip_rom[123] = 16'd530  ;
        recip_rom[124] = 16'd526  ; recip_rom[125] = 16'd522  ; recip_rom[126] = 16'd518  ; recip_rom[127] = 16'd514  ;
        recip_rom[128] = 16'd510  ; recip_rom[129] = 16'd506  ; recip_rom[130] = 16'd502  ; recip_rom[131] = 16'd498  ;
        recip_rom[132] = 16'd494  ; recip_rom[133] = 16'd490  ; recip_rom[134] = 16'd487  ; recip_rom[135] = 16'd483  ;
        recip_rom[136] = 16'd480  ; recip_rom[137] = 16'd476  ; recip_rom[138] = 16'd473  ; recip_rom[139] = 16'd469  ;
        recip_rom[140] = 16'd466  ; recip_rom[141] = 16'd462  ; recip_rom[142] = 16'd459  ; recip_rom[143] = 16'd456  ;
        recip_rom[144] = 16'd453  ; recip_rom[145] = 16'd450  ; recip_rom[146] = 16'd447  ; recip_rom[147] = 16'd444  ;
        recip_rom[148] = 16'd441  ; recip_rom[149] = 16'd438  ; recip_rom[150] = 16'd435  ; recip_rom[151] = 16'd432  ;
        recip_rom[152] = 16'd429  ; recip_rom[153] = 16'd426  ; recip_rom[154] = 16'd423  ; recip_rom[155] = 16'd421  ;
        recip_rom[156] = 16'd418  ; recip_rom[157] = 16'd415  ; recip_rom[158] = 16'd413  ; recip_rom[159] = 16'd410  ;
        recip_rom[160] = 16'd408  ; recip_rom[161] = 16'd405  ; recip_rom[162] = 16'd402  ; recip_rom[163] = 16'd400  ;
        recip_rom[164] = 16'd398  ; recip_rom[165] = 16'd395  ; recip_rom[166] = 16'd393  ; recip_rom[167] = 16'd390  ;
        recip_rom[168] = 16'd388  ; recip_rom[169] = 16'd386  ; recip_rom[170] = 16'd384  ; recip_rom[171] = 16'd381  ;
        recip_rom[172] = 16'd379  ; recip_rom[173] = 16'd377  ; recip_rom[174] = 16'd375  ; recip_rom[175] = 16'd373  ;
        recip_rom[176] = 16'd370  ; recip_rom[177] = 16'd368  ; recip_rom[178] = 16'd366  ; recip_rom[179] = 16'd364  ;
        recip_rom[180] = 16'd362  ; recip_rom[181] = 16'd360  ; recip_rom[182] = 16'd358  ; recip_rom[183] = 16'd356  ;
        recip_rom[184] = 16'd354  ; recip_rom[185] = 16'd352  ; recip_rom[186] = 16'd350  ; recip_rom[187] = 16'd349  ;
        recip_rom[188] = 16'd347  ; recip_rom[189] = 16'd345  ; recip_rom[190] = 16'd343  ; recip_rom[191] = 16'd341  ;
        recip_rom[192] = 16'd340  ; recip_rom[193] = 16'd338  ; recip_rom[194] = 16'd336  ; recip_rom[195] = 16'd334  ;
        recip_rom[196] = 16'd333  ; recip_rom[197] = 16'd331  ; recip_rom[198] = 16'd329  ; recip_rom[199] = 16'd328  ;
        recip_rom[200] = 16'd326  ; recip_rom[201] = 16'd324  ; recip_rom[202] = 16'd323  ; recip_rom[203] = 16'd321  ;
        recip_rom[204] = 16'd320  ; recip_rom[205] = 16'd318  ; recip_rom[206] = 16'd316  ; recip_rom[207] = 16'd315  ;
        recip_rom[208] = 16'd313  ; recip_rom[209] = 16'd312  ; recip_rom[210] = 16'd310  ; recip_rom[211] = 16'd309  ;
        recip_rom[212] = 16'd307  ; recip_rom[213] = 16'd306  ; recip_rom[214] = 16'd305  ; recip_rom[215] = 16'd303  ;
        recip_rom[216] = 16'd302  ; recip_rom[217] = 16'd300  ; recip_rom[218] = 16'd299  ; recip_rom[219] = 16'd298  ;
        recip_rom[220] = 16'd296  ; recip_rom[221] = 16'd295  ; recip_rom[222] = 16'd294  ; recip_rom[223] = 16'd292  ;
        recip_rom[224] = 16'd291  ; recip_rom[225] = 16'd290  ; recip_rom[226] = 16'd288  ; recip_rom[227] = 16'd287  ;
        recip_rom[228] = 16'd286  ; recip_rom[229] = 16'd285  ; recip_rom[230] = 16'd283  ; recip_rom[231] = 16'd282  ;
        recip_rom[232] = 16'd281  ; recip_rom[233] = 16'd280  ; recip_rom[234] = 16'd278  ; recip_rom[235] = 16'd277  ;
        recip_rom[236] = 16'd276  ; recip_rom[237] = 16'd275  ; recip_rom[238] = 16'd274  ; recip_rom[239] = 16'd273  ;
        recip_rom[240] = 16'd272  ; recip_rom[241] = 16'd270  ; recip_rom[242] = 16'd269  ; recip_rom[243] = 16'd268  ;
        recip_rom[244] = 16'd267  ; recip_rom[245] = 16'd266  ; recip_rom[246] = 16'd265  ; recip_rom[247] = 16'd264  ;
        recip_rom[248] = 16'd263  ; recip_rom[249] = 16'd262  ; recip_rom[250] = 16'd261  ; recip_rom[251] = 16'd260  ;
        recip_rom[252] = 16'd259  ; recip_rom[253] = 16'd258  ; recip_rom[254] = 16'd257  ; recip_rom[255] = 16'd256  ;
    end

    assign recip = recip_rom[range];

endmodule
