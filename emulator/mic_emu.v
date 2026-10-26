// mic_emu.v
// Pretends to be ONE I2S microphone (INMP441 style, left slot).
// It listens to BCLK and WS like a real mic and puts stored 24-bit samples on 'sd'.
//
// Use one instance per mic, each with its own .mem file.
// Real FPGA logic: works in simulation and on the board.

module mic_emu #(
    parameter MEM_FILE  = "mic0.mem",  // file with the stored samples (hex, one per line)
    parameter DEPTH     = 256,         // number of samples in the file
    parameter SLOT_SEL  = 1'b0,        // WS level in which this mic talks (0 = left)
    parameter DATA_BITS = 24
)(
    input  wire clk,    // 100 MHz system clock (only used to watch BCLK/WS and drive sd)
    input  wire bclk,   // from the divider (or a pin)
    input  wire ws,     // from the divider (or a pin)
    output reg  sd      // the mic's data wire
);

    // 1) The stored samples
    reg [DATA_BITS-1:0] rom [0:DEPTH-1];
    initial $readmemh(MEM_FILE, rom);

    // 2) Watch BCLK and WS: delay them (synchroniser), then keep one older copy
    //    so we can see when they change.
    reg bclk_s1 = 0, bclk_s2 = 0, bclk_prev = 0;
    reg ws_s1   = 0, ws_s2   = 0, ws_prev   = 0;
    always @(posedge clk) begin
        bclk_s1 <= bclk;   bclk_s2 <= bclk_s1;   bclk_prev <= bclk_s2;
        ws_s1   <= ws;     ws_s2   <= ws_s1;     ws_prev   <= ws_s2;
    end
    wire bclk_fall  = bclk_prev & ~bclk_s2;   // BCLK just went 1 -> 0
    wire ws_changed = ws_s2 != ws_prev;       // WS just flipped

    // 3) State
    reg [DATA_BITS-1:0]           shreg  = 0;  // current sample, being pushed out MSB first
    reg [$clog2(DATA_BITS+1)-1:0] bitcnt = 0;  // how many bits already sent
    reg                           active = 0;  // 1 = this is our slot, send bits
    reg [$clog2(DEPTH)-1:0]       addr   = 0;  // which sample in the table comes next
    initial sd = 1'b0;

    always @(posedge clk) begin
        if (ws_changed) begin
            // A new slot starts. (A real mic puts nothing on the wire in this edge.)
            sd     <= 1'b0;
            bitcnt <= 0;
            active <= (ws_s2 == SLOT_SEL);
            if (ws_s2 == SLOT_SEL) begin
                shreg <= rom[addr];                               // load the next sample
                addr  <= (addr == DEPTH-1) ? 0 : addr + 1'b1;     // move on, wrap at the end
            end
        end else if (bclk_fall) begin
            if (active && bitcnt < DATA_BITS) begin
                sd     <= shreg[DATA_BITS-1];                     // put the biggest bit on the wire
                shreg  <= {shreg[DATA_BITS-2:0], 1'b0};           // shift the rest up
                bitcnt <= bitcnt + 1'b1;
            end else begin
                sd <= 1'b0;                                       // padding bits / other slot
            end
        end
    end

endmodule
