`timescale 1ns / 1ps
// FZ3B: 25 MHz input; toggle D5 green LED every 0.5 seconds.
module led_blink #(
    parameter integer HALF_PERIOD_CYCLES = 12_500_000
)(
    input wire pl_ref_clk,
    output reg pl_led = 1'b0
);
    localparam integer COUNT_WIDTH = (HALF_PERIOD_CYCLES <= 1)
                                    ? 1 : $clog2(HALF_PERIOD_CYCLES);
    reg [COUNT_WIDTH-1:0] count = 0;

    always @(posedge pl_ref_clk) begin
        if (count == HALF_PERIOD_CYCLES - 1) begin
            count <= 0;
            pl_led <= ~pl_led;
        end else begin
            count <= count + 1'b1;
        end
    end
endmodule
