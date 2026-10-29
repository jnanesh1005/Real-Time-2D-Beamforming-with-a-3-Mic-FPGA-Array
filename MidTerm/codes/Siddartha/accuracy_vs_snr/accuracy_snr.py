import csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from math import gcd
from scipy.io import wavfile
from scipy.signal import resample_poly
import pyroomacoustics as pra

# The environment for replication of results
FS = 16000
C = 343.0
N = 1024                               # window length (samples)
ROOM = [6.0, 5.0, 3.0]
CENTRE = np.array([3.0, 2.5, 1.5])     # array position in the room
RADIUS = 1.5                           # source distance from array (m)

SPEECH_FILE = "speech.wav"
SPEECH_SECONDS = 10                    # use the first 10 s of the recording
N_WINDOWS = 40                         # how many different speech windows to test on

SNR_LIST = list(range(-10, 11, 2))     # dB
RT60_LIST = [0.0, 0.4]                 # 0 = no echo, 0.4 = echoey room
TRIALS = 200                           # random tests per point
TOLERANCE_DEG = 10                     # "correct" = within 10 degrees
SEED = 1


# Setup of mics
def circle(n_mics, radius):
    a = 2 * np.pi * np.arange(n_mics) / n_mics
    return np.c_[radius * np.cos(a), radius * np.sin(a)]

triangle = np.array([[0.00, 0.00], [0.08, 0.00], [0.03, 0.04]])   # our 3-mic array
triangle = triangle - triangle.mean(axis=0)

ARRAYS = {
    "3-mic triangle":          triangle,
    "10-mic circle, r = 5 cm":  circle(10, 0.05),    # same size as the triangle
    "10-mic circle, r = 10 cm": circle(10, 0.10),    # bigger
}


# Speech processing of recorded audio
rate, speech = wavfile.read(SPEECH_FILE)
speech = speech.astype(float)
if speech.ndim == 2:
    speech = speech.mean(axis=1)
if rate != FS:                         # convert to 16 kHz if needed
    g = gcd(int(rate), FS)
    speech = resample_poly(speech, FS // g, int(rate) // g)
speech = speech[: FS * SPEECH_SECONDS]
speech = (speech - speech.mean()) / np.max(np.abs(speech))

# Starting points of the loudest 30% so we never test on silence
starts = np.arange(0, len(speech) - N - 400, 512)
energy = np.array([np.mean(speech[s:s + N] ** 2) for s in starts])
loud_starts = starts[energy > np.quantile(energy, 0.7)]

# Pick N_WINDOWS of them once. We only keep these short slices in memory (saves RAM).
pick = np.random.default_rng(SEED).choice(loud_starts, N_WINDOWS,
                                          replace=len(loud_starts) < N_WINDOWS)
window_starts = pick + 300             # +300 skips the start-up delay of the room


# Room simulation
def simulate(mic_xy, angle_deg, rt60):
    """Returns the clean signal at every microphone, shape (n_mics, samples)."""
    a = np.deg2rad(angle_deg)
    source = CENTRE + RADIUS * np.array([np.cos(a), np.sin(a), 0.0])

    if rt60 == 0:
        room = pra.ShoeBox(ROOM, fs=FS, max_order=0)          # direct path only
    else:
        absorption, order = pra.inverse_sabine(rt60, ROOM)
        room = pra.ShoeBox(ROOM, fs=FS, materials=pra.Material(absorption),
                           max_order=min(int(order), 30))

    room.add_source(source, signal=speech)
    mics_3d = np.c_[mic_xy, np.zeros(len(mic_xy))] + CENTRE
    room.add_microphone_array(mics_3d.T)
    room.compute_rir()
    room.simulate()
    return np.asarray(room.mic_array.signals)


def get_windows(mic_xy, angle_deg, rt60):
    """Simulate, then keep only the short windows we need. Shape (windows, mics, N)."""
    signal = simulate(mic_xy, angle_deg, rt60)
    return np.stack([signal[:, s:s + N] for s in window_starts]).astype(np.float32)


# delay and sum
def make_steering(mic_xy):
    """For each guessed angle, the phase shifts that line the microphones up."""
    angles = np.deg2rad(np.arange(360))
    direction = np.c_[np.cos(angles), np.sin(angles)]        # (360, 2)
    arrival = -(direction @ mic_xy.T) / C                    # seconds, (360, mics)
    freqs = np.arange(N // 2 + 1) * FS / N
    return np.exp(2j * np.pi * arrival[:, :, None] * freqs[None, None, :])


def estimate_angle(window, steering):
    """Delay-and-sum for every angle; the loudest output wins."""
    spectrum = np.fft.rfft(window, axis=1)                   # (mics, freqs)
    summed = np.einsum("amf,mf->af", steering, spectrum)     # shift + add the mics
    power = (np.abs(summed) ** 2).sum(axis=1)                # output power per angle
    return int(np.argmax(power))


def angle_error(a, b):
    return abs((a - b + 180) % 360 - 180)


# run
rng = np.random.default_rng(SEED)
results = {}          # (rt60, array name) -> list of accuracy (%) per SNR

for rt60 in RT60_LIST:
    for name, mic_xy in ARRAYS.items():
        print(f"RT60 {rt60}: {name}")
        clean = {a: get_windows(mic_xy, a, rt60) for a in range(0, 360, 10)}
        steering = make_steering(mic_xy)

        accuracy = []
        for snr in SNR_LIST:
            hits = 0
            for _ in range(TRIALS):
                true_angle = 10 * int(rng.integers(0, 36))
                which = int(rng.integers(0, N_WINDOWS))
                window = clean[true_angle][which].astype(np.float64)

                noise_power = np.mean(window ** 2) / 10 ** (snr / 10)
                window = window + rng.normal(0, np.sqrt(noise_power), window.shape)

                guess = estimate_angle(window, steering)
                hits += angle_error(guess, true_angle) <= TOLERANCE_DEG
            accuracy.append(100 * hits / TRIALS)
        results[(rt60, name)] = accuracy


# csv file write
with open("accuracy_vs_snr.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["rt60", "array"] + [f"snr_{s}dB" for s in SNR_LIST])
    for (rt60, name), acc in results.items():
        writer.writerow([rt60, name] + acc)


# plot
fig, axes = plt.subplots(1, len(RT60_LIST), figsize=(12, 4.5), sharey=True)
for ax, rt60 in zip(axes, RT60_LIST):
    for name in ARRAYS:
        ax.plot(SNR_LIST, results[(rt60, name)], marker="o", label=name)
    ax.set_title("No echo (anechoic)" if rt60 == 0 else f"Echoey room (RT60 = {rt60} s)")
    ax.set_xlabel("SNR (dB)")
    ax.grid(alpha=0.3)
axes[0].set_ylabel(f"Correct direction (within {TOLERANCE_DEG} deg) (%)")
axes[0].legend(loc="lower right")
plt.tight_layout()
plt.savefig("accuracy_vs_snr.png", dpi=150)

# Trends: save SNR needed to reach 90% accuracy to CSV
with open("accuracy_trends.csv", "w", newline="") as f:
    writer = csv.writer(f)

    writer.writerow([
        "rt60_s",
        "array",
        "target_accuracy_percent",
        "minimum_tested_snr_db",
        "result"
    ])

    for rt60 in RT60_LIST:
        for name in ARRAYS:
            acc = results[(rt60, name)]

            needed = next(
                (s for s, a in zip(SNR_LIST, acc) if a >= 90),
                None
            )

            if needed is not None:
                result = f"{needed:+d} dB"
                needed_csv = needed
            else:
                result = f"Never (above {max(SNR_LIST):+d} dB)"
                needed_csv = ""

            writer.writerow([
                rt60,
                name,
                90,
                needed_csv,
                result
            ])
