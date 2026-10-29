
import numpy as np
import json
import os

N = 1024
rng = np.random.default_rng(20261025)

# Shift a signal without changing its length.
# Positive delay means the signal arrives later.
def delay_signal(signal, delay):
    out = np.zeros(N, dtype=np.int32)

    if delay > 0:
        out[delay:] = signal[:N - delay]
    elif delay < 0:
        out[:N + delay] = signal[-delay:]
    else:
        out[:] = signal

    return out


# Save one complete test case
def save_test(name, ch1, ch2, ch3, expected):
    folder = os.path.join("task0_tests", name)
    os.makedirs(folder, exist_ok=True)

    channels = [ch1, ch2, ch3]

    for i, channel in enumerate(channels, start=1):
        with open(os.path.join(folder, f"ch{i}.mem"), "w") as f:
            for sample in channel:
                sample = int(sample) & 0xFFFF
                f.write(format(sample, "04X") + "\n")

    with open(os.path.join(folder, "expected.json"), "w") as f:
        json.dump(expected, f, indent=4)

    print("Created:", folder)


# Expected pairwise lags.
# Lag ch1_ch2 = delay of ch2 minus delay of ch1.
def make_expected(delays):
    return {
        "ch1_ch2": delays[1] - delays[0],
        "ch1_ch3": delays[2] - delays[0],
        "ch2_ch3": delays[2] - delays[1]
    }


# Create channels from one common signal
def make_channels(signal, delays):
    return [
        delay_signal(signal, delays[0]),
        delay_signal(signal, delays[1]),
        delay_signal(signal, delays[2])
    ]


# 1. Positive lags: +3, +5, +2
delays = [0, 3, 5]
signal = rng.integers(-10000, 10001, N)
save_test("positive_lags",
          *make_channels(signal, delays),
          make_expected(delays))


# 2. Negative lag: ch1 arrives 3 samples after ch2
delays = [3, 0, 5]
signal = rng.integers(-10000, 10001, N)
save_test("negative_lags",
          *make_channels(signal, delays),
          make_expected(delays))


# 3. Zero lag: all channels identical
signal = rng.integers(-10000, 10001, N)
save_test("zero_lag", signal, signal.copy(), signal.copy(),
          {"ch1_ch2": 0, "ch1_ch3": 0, "ch2_ch3": 0})


# 4. Edge lag +13 between ch1 and ch2
delays = [0, 13, 5]
signal = rng.integers(-10000, 10001, N)
save_test("edge_positive_13",
          *make_channels(signal, delays),
          make_expected(delays))


# 5. Edge lag -13 between ch1 and ch2
delays = [13, 0, 5]
signal = rng.integers(-10000, 10001, N)
save_test("edge_negative_13",
          *make_channels(signal, delays),
          make_expected(delays))


# 6. Full-scale constant: stresses the accumulator
full = np.full(N, -32768, dtype=np.int32)
save_test("full_scale_constant", full, full.copy(), full.copy(),
          {"ch1_ch2": 0, "ch1_ch3": 0, "ch2_ch3": 0})


# 7. Full-scale random: only -32768 or +32767
signal = rng.choice([-32768, 32767], N)
delays = [0, 3, 5]
save_test("full_scale_random",
          *make_channels(signal, delays),
          make_expected(delays))


# 8. All zeros: every lag score ties
zeros = np.zeros(N, dtype=np.int32)
save_test("all_zeros", zeros, zeros.copy(), zeros.copy(),
          {"ch1_ch2": -13, "ch1_ch3": -13, "ch2_ch3": -13})


# 9. Noisy delayed signals
signal = rng.integers(-8000, 8001, N)
delays = [0, 3, 5]
channels = make_channels(signal, delays)

noisy_channels = []
for channel in channels:
    noise = rng.normal(0, 1000, N)
    noisy = np.clip(channel + noise, -32768, 32767)
    noisy_channels.append(noisy.astype(np.int32))

save_test("noisy_case",
          *noisy_channels,
          make_expected(delays))


# 10. Three separate windows with the same delays
delays = [0, 3, 5]
parts = [[], [], []]
for window in range(3):
    signal = rng.integers(-10000, 10001, N)
    chans = make_channels(signal, delays)
    for k in range(3):
        parts[k].append(chans[k])

save_test("windows",
          *[np.concatenate(p) for p in parts],
          make_expected(delays))


print("\nAll test files generated!")
