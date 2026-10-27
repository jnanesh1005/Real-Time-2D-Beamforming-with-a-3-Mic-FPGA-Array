// i2s_rx.v
// Receives 3 I2S mics (one data wire each, shared BCLK/WS) and outputs three
// 16-bit samples that all arrive together with a 1-cycle 'valid' pulse.

module i2s_rx #(
    parameter SLOT_SEL  = 1'b0,   
    parameter DATA_BITS = 24,     
    parameter OUT_BITS  = 16      
)(
    input  wire        clk,        
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
    output reg                 valid     
);

    reg [2:0] sd_s1, sd_s2;
    always @(posedge clk) begin
        sd_s1 <= sd;
        sd_s2 <= sd_s1;
    end

    reg [DATA_BITS-1:0] sh0, sh1, sh2;
    reg [$clog2(DATA_BITS)-1:0] cnt;  
    reg active;                        
    reg skip;                         

    // what each shift register will hold after taking the next bit
    wire [DATA_BITS-1:0] nxt0 = {sh0[DATA_BITS-2:0], sd_s2[0]};
    wire [DATA_BITS-1:0] nxt1 = {sh1[DATA_BITS-2:0], sd_s2[1]};
    wire [DATA_BITS-1:0] nxt2 = {sh2[DATA_BITS-2:0], sd_s2[2]};

    always @(posedge clk) begin
        valid <= 1'b0;                

        if (rst) begin
            active <= 1'b0;
            skip   <= 1'b0;
            cnt    <= 0;
        end else if (ws_edge) begin
            active <= (ws == SLOT_SEL);
            skip   <= 1'b1;            
            cnt    <= 0;
        end else if (bclk_rise && active) begin
            if (skip) begin
                skip <= 1'b0;          
            end else begin
                sh0 <= nxt0;
                sh1 <= nxt1;
                sh2 <= nxt2;
                cnt <= cnt + 1'b1;

                if (cnt == DATA_BITS-1) begin
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
