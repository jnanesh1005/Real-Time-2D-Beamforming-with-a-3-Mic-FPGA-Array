// i2s_rx.v
// Receives 3 I2S mics (one data wire each, shared BCLK/WS) and outputs three
// 16-bit samples that all arrive together with a 1-cycle 'valid' pulse.
//
// Runs entirely on the 100 MHz clock. It does NOT use BCLK as a clock: it uses
// the 1-cycle pulses (bclk_rise, ws_edge) produced by clk_div.
//
// I2S timing this relies on (INMP441 style):
//   * WS changes on a BCLK falling edge.
//   * The mic puts the MSB on the wire one BCLK AFTER the WS change
//     (i.e. on the next falling edge), then the remaining bits, MSB first.
//   * We read the wire on BCLK rising edges.
//   * Sample = 24 bits, followed by padding in the 32-bit slot.

module i2s_rx #(
    parameter SLOT_SEL  = 1'b0,   // which WS level carries our mics: 0 = left slot, 1 = right slot
    parameter DATA_BITS = 24,     // real bits in each sample
    parameter OUT_BITS  = 16      // bits we keep (the top ones)
)(
    input  wire        clk,        // 100 MHz
    input  wire        rst,

    // from clk_div
    input  wire        bclk_rise,
    input  wire        ws_edge,
    input  wire        ws,

    // data wires from the 3 mics
    input  wire [2:0]  sd,

    // results
    output reg  [OUT_BITS-1:0] mic0,
    output reg  [OUT_BITS-1:0] mic1,
    output reg  [OUT_BITS-1:0] mic2,
    output reg                 valid     // 1 cycle: mic0/1/2 hold a new sample
);

    // 1) Synchroniser: the data wires come from outside the FPGA, so pass them
    //    through two flip-flops before using them.
    reg [2:0] sd_s1, sd_s2;
    always @(posedge clk) begin
        sd_s1 <= sd;
        sd_s2 <= sd_s1;
    end

    // 2) Three shift registers (one per mic) and one shared controller
    reg [DATA_BITS-1:0] sh0, sh1, sh2;
    reg [$clog2(DATA_BITS)-1:0] cnt;   // how many bits collected so far
    reg active;                        // 1 = we are inside the slot we want
    reg skip;                          // 1 = next BCLK rise is the "dead" one

    // what each shift register will hold after taking the next bit
    wire [DATA_BITS-1:0] nxt0 = {sh0[DATA_BITS-2:0], sd_s2[0]};
    wire [DATA_BITS-1:0] nxt1 = {sh1[DATA_BITS-2:0], sd_s2[1]};
    wire [DATA_BITS-1:0] nxt2 = {sh2[DATA_BITS-2:0], sd_s2[2]};

    always @(posedge clk) begin
        valid <= 1'b0;                 // default: pulse lasts one cycle

        if (rst) begin
            active <= 1'b0;
            skip   <= 1'b0;
            cnt    <= 0;
        end else if (ws_edge) begin
            // A new slot starts. Only listen if it is the slot our mics use.
            active <= (ws == SLOT_SEL);
            skip   <= 1'b1;            // first rise after WS change carries no sample bit
            cnt    <= 0;
        end else if (bclk_rise && active) begin
            if (skip) begin
                skip <= 1'b0;          // throw this one away
            end else begin
                sh0 <= nxt0;
                sh1 <= nxt1;
                sh2 <= nxt2;
                cnt <= cnt + 1'b1;

                if (cnt == DATA_BITS-1) begin
                    // that was the last (24th) bit: keep the top OUT_BITS bits
                    mic0   <= nxt0[DATA_BITS-1 -: OUT_BITS];
                    mic1   <= nxt1[DATA_BITS-1 -: OUT_BITS];
                    mic2   <= nxt2[DATA_BITS-1 -: OUT_BITS];
                    valid  <= 1'b1;
                    active <= 1'b0;    // ignore the padding bits
                end
            end
        end
    end

endmodule
