import os
import json
import csv
from math import gcd

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
import pyroomacoustics as pra


#the environment settings

FS = 16000
C = 343.0
N = 1024 

SPEECH_FILE = "speech.wav"
OUTPUT_DIR = "pyroomacoustics_dataset"

ROOM = [6.0, 5.0, 3.0]
SOURCE_RADIUS = 1.5
ARRAY_CENTER = np.array([3.0, 2.5, 1.5])

ANGLES = range(0, 360, 10)    # source angle, degrees
RT60_LIST = [0.0, 0.25, 0.5]  # echo level. 0.0 = anechoic (direct path only). RT refers to reverberation time
SNR_LIST = [30, 20, 10, 0]    # noise level in dB
WINDOWS_PER_CASE = 1          

TARGET_PEAK = 30000          
MAX_ORDER_CAP = 30           
BASE_SEED = 20261025

# the req triangle setup M1-M2 = 8 cm, M1-M3 = 5 cm, M2-M3 = 6.4 cm
MIC_RAW = np.array([
    [0.00, 0.00, 0.00],
    [0.08, 0.00, 0.00],
    [0.03, 0.04, 0.00],
])
MIC_RELATIVE = MIC_RAW - MIC_RAW.mean(axis=0)


#speech processing from recorded audio

def load_speech(filename):
    if not os.path.isfile(filename):
        raise FileNotFoundError(f"Put {filename} next to this script.")

    rate, audio = wavfile.read(filename)

    if np.issubdtype(audio.dtype, np.integer):
        info = np.iinfo(audio.dtype)
        audio = audio.astype(np.float64) / max(abs(info.min), abs(info.max))
    else:
        audio = audio.astype(np.float64)

    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    if rate != FS:
        g = gcd(int(rate), FS)
        audio = resample_poly(audio, FS // g, int(rate) // g)

    audio = audio - audio.mean()
    peak = np.max(np.abs(audio))
    if peak == 0:
        raise ValueError("Speech file is silent.")
    return audio / peak


def true_delays(mic_pos, src_pos):
    """Direct-path lags in samples. Positive = second channel arrives later."""
    d = np.linalg.norm(mic_pos - src_pos, axis=1)
    pairs = {"ch1_ch2": (0, 1), "ch1_ch3": (0, 2), "ch2_ch3": (1, 2)}
    return {k: float((d[j] - d[i]) / C * FS) for k, (i, j) in pairs.items()}


#simulation

def simulate(speech, angle_deg, rt60):
    a = np.deg2rad(angle_deg)
    src = ARRAY_CENTER + SOURCE_RADIUS * np.array([np.cos(a), np.sin(a), 0.0])
    mics = ARRAY_CENTER + MIC_RELATIVE

    if rt60 == 0.0:
        room = pra.ShoeBox(ROOM, fs=FS, max_order=0)   # direct path only
        order = 0
    else:
        absorption, order = pra.inverse_sabine(rt60, ROOM)
        order = min(int(order), MAX_ORDER_CAP)
        room = pra.ShoeBox(ROOM, fs=FS,
                           materials=pra.Material(absorption),
                           max_order=order)

    room.add_source(src, signal=speech)
    room.add_microphone_array(mics.T)          # shape (3 coords, 3 mics)
    room.compute_rir()
    room.simulate()

    clean = np.asarray(room.mic_array.signals, dtype=np.float64)
    return clean, src, mics, order

#windows and noise

def pick_windows(clean, k):
    """Pick the k loudest non-overlapping N-sample windows (so we never get silence)."""
    power = np.mean(clean ** 2, axis=0)
    cs = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(0, len(power) - N, N // 2)
    energy = cs[starts + N] - cs[starts]

    chosen = []
    for idx in np.argsort(energy)[::-1]:
        s = int(starts[idx])
        if all(abs(s - c) >= N for c in chosen):
            chosen.append(s)
        if len(chosen) == k:
            break
    return sorted(chosen)


def add_noise(window, snr_db, rng):
    """Independent white noise per mic. SNR is measured on THIS window."""
    sig_power = np.mean(window ** 2)
    noise_power = sig_power / (10 ** (snr_db / 10))
    return window + rng.normal(0.0, np.sqrt(noise_power), window.shape)

#datasave

def save_mem(path, samples):
    with open(path, "w") as f:
        for s in samples:
            f.write(f"{int(s) & 0xFFFF:04X}\n")


def save_case(folder, window, expected, meta):
    os.makedirs(folder, exist_ok=True)

    peak = np.max(np.abs(window))
    gain = TARGET_PEAK / peak if peak > 0 else 1.0
    pcm = np.clip(np.rint(window * gain), -32768, 32767).astype(np.int32)

    assert pcm.shape == (3, N), "each channel must be exactly N samples"

    for i in range(3):
        save_mem(os.path.join(folder, f"ch{i + 1}.mem"), pcm[i])

    # expected.json: ONLY the three pair lags (Subhodeep's format)
    with open(os.path.join(folder, "expected.json"), "w") as f:
        json.dump(expected, f, indent=4)

    # everything else goes in a separate file
    with open(os.path.join(folder, "meta.json"), "w") as f:
        json.dump(meta, f, indent=4)


def main():
    speech = load_speech(SPEECH_FILE)
    print(f"Speech: {len(speech) / FS:.1f} s at {FS} Hz")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    rows = []

    for rt60 in RT60_LIST:
        for angle in ANGLES:
            clean, src, mics, order = simulate(speech, angle, rt60)
            truth = true_delays(mics, src)
            expected = {k: int(np.rint(v)) for k, v in truth.items()}
            starts = pick_windows(clean, WINDOWS_PER_CASE)

            for snr in SNR_LIST:
                for w, start in enumerate(starts, start=1):
                    seed = [BASE_SEED, int(angle), int(rt60 * 100), int(snr) + 100, w]
                    rng = np.random.default_rng(seed)

                    window = add_noise(clean[:, start:start + N], snr, rng)

                    snr_label = f"m{abs(snr)}" if snr < 0 else str(snr)
                    name = f"RT{int(rt60 * 100)}_SNR{snr_label}_A{angle:03d}" 
                    meta = {
                        "source_angle_deg": angle,
                        "angle_convention": "counter-clockwise from +x axis, measured at array centroid",
                        "rt60_s": rt60,
                        "snr_db": snr,
                        "window_start_sample": start,
                        "fs_hz": FS,
                        "speed_of_sound_m_s": C,
                        "ism_max_order": order,
                        "source_position_m": src.tolist(),
                        "mic_positions_m": mics.tolist(),
                        "true_lags_samples_fractional": truth,
                        "lag_sign": "positive = second channel arrives later",
                        "ground_truth_type": "rounded direct-path geometry",
                    }
                    save_case(os.path.join(OUTPUT_DIR, name), window, expected, meta)

                    rows.append({
                        "folder": name, "angle": angle, "rt60": rt60, "snr_db": snr,
                        **expected,
                        **{k + "_frac": round(v, 3) for k, v in truth.items()},
                    })

        print(f"RT60 {rt60}: done")

    with open(os.path.join(OUTPUT_DIR, "index.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)

    print(f"Generated {len(rows)} test cases in {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()