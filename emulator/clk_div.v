// clk_div.v
// Generates the two I2S timing signals (BCLK and WS) from the 100 MHz system clock.
//
//   BCLK frequency = CLK_FREQ / (2 * HALF_PERIOD)   -> 100 MHz / 32 = 3.125 MHz
//   WS   frequency = BCLK / (2 * SLOT_BITS)          -> 3.125 MHz / 64 = 48.83 kHz (sample rate)
//
// Also outputs one-cycle "tick" pulses that line up with the BCLK edges, so the
// rest of the design can stay on the 100 MHz clock (no second clock domain).

module clk_div #(
    parameter HALF_PERIOD = 16,   // sys_clk cycles in HALF a BCLK period
    parameter SLOT_BITS   = 32    // BCLK cycles per WS half (one channel slot)
)(
    input  wire clk,        // 100 MHz system clock
    input  wire rst,        // synchronous reset, active high

    output reg  bclk,       // bit clock  -> goes to the mic(s)
    output reg  ws,         // word select -> goes to the mic(s)

    output reg  bclk_rise,  // 1-cycle pulse, same cycle bclk goes 0 -> 1
    output reg  bclk_fall,  // 1-cycle pulse, same cycle bclk goes 1 -> 0
    output reg  ws_edge     // 1-cycle pulse, same cycle ws changes (start of a slot)
);

    // Counters sized automatically from the parameters
    reg [$clog2(HALF_PERIOD)-1:0] half_cnt;   // counts sys_clk cycles
    reg [$clog2(SLOT_BITS)-1:0]   bit_cnt;    // counts BCLK cycles inside a slot

    always @(posedge clk) begin
        // pulses default to 0 and only go high for one cycle when set below
        bclk_rise <= 1'b0;
        bclk_fall <= 1'b0;
        ws_edge   <= 1'b0;

        if (rst) begin
            half_cnt <= 0;
            bit_cnt  <= 0;
            bclk     <= 1'b0;
            ws       <= 1'b0;
        end else if (half_cnt == HALF_PERIOD - 1) begin
            // half a BCLK period has passed: flip BCLK
            half_cnt <= 0;
            bclk     <= ~bclk;

            if (!bclk) begin
                // bclk is about to go 0 -> 1
                bclk_rise <= 1'b1;
            end else begin
                // bclk is about to go 1 -> 0
                bclk_fall <= 1'b1;
                // I2S: WS changes on the falling edge of BCLK
                if (bit_cnt == SLOT_BITS - 1) begin
                    bit_cnt <= 0;
                    ws      <= ~ws;
                    ws_edge <= 1'b1;
                end else begin
                    bit_cnt <= bit_cnt + 1;
                end
            end
        end else begin
            half_cnt <= half_cnt + 1;
        end
    end

endmodule
