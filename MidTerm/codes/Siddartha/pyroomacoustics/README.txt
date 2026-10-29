TASK 5 DATASET — FOLDER NAMING

Folder format: RT<RT60>_SNR<SNR>_A<ANGLE>

RT   = RT60 in hundredths of a second:
       RT0 = 0.00 s, RT25 = 0.25 s, RT50 = 0.50 s
SNR  = signal-to-noise ratio in dB:
       SNR20 = +20 dB, SNR0 = 0 dB, SNRm10 = -10 dB
A    = source angle in degrees (3 digits):
       A060 = 60 degrees

Each case folder contains:
- ch1.mem, ch2.mem, ch3.mem: microphone samples
- expected.json: expected pairwise delays
- meta.json: case metadata

index.csv lists all generated test cases.
