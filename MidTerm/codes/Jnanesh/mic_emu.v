// mic_emu.v
// Pretends to be ONE I2S microphone (INMP441 style, left slot).
// It listens to BCLK and WS like a real mic and puts stored 24-bit samples on 'sd'.
// Use one instance per mic, each with its own .mem file.

module mic_emu #(
    parameter MEM_FILE  = "mic0.mem",  
    parameter DEPTH     = 256,         
    parameter SLOT_SEL  = 1'b0,        
    parameter DATA_BITS = 24
)(
    input  wire clk,    
    input  wire bclk,   
    input  wire ws,     
    output reg  sd      
);

    reg [DATA_BITS-1:0] rom [0:DEPTH-1];
    initial $readmemh(MEM_FILE, rom);

    reg bclk_s1 = 0, bclk_s2 = 0, bclk_prev = 0;
    reg ws_s1   = 0, ws_s2   = 0, ws_prev   = 0;
    always @(posedge clk) begin
        bclk_s1 <= bclk;   bclk_s2 <= bclk_s1;   bclk_prev <= bclk_s2;
        ws_s1   <= ws;     ws_s2   <= ws_s1;     ws_prev   <= ws_s2;
    end
    wire bclk_fall  = bclk_prev & ~bclk_s2;   
    wire ws_changed = ws_s2 != ws_prev;       // WS just flipped

    // 3) State
    reg [DATA_BITS-1:0]           shreg  = 0;  
    reg [$clog2(DATA_BITS+1)-1:0] bitcnt = 0;  
    reg                           active = 0;  
    reg [$clog2(DEPTH)-1:0]       addr   = 0;  
    initial sd = 1'b0;

    always @(posedge clk) begin
        if (ws_changed) begin
            sd     <= 1'b0;
            bitcnt <= 0;
            active <= (ws_s2 == SLOT_SEL);
            if (ws_s2 == SLOT_SEL) begin
                shreg <= rom[addr];                               
                addr  <= (addr == DEPTH-1) ? 0 : addr + 1'b1;     
            end
        end else if (bclk_fall) begin
            if (active && bitcnt < DATA_BITS) begin
                sd     <= shreg[DATA_BITS-1];                     
                shreg  <= {shreg[DATA_BITS-2:0], 1'b0};           // shift the rest up
                bitcnt <= bitcnt + 1'b1;
            end else begin
                sd <= 1'b0;                                       // padding bits
            end
        end
    end

endmodule
