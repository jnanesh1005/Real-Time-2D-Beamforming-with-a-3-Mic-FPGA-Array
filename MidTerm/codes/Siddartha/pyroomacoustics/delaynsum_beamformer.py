import numpy as np

C = 343.0          # speed of sound 
FS = 16000         # sampling rate 
WIN = 1024         # window length 

# triangle mic positions
MICS = np.array([[0.00, 0.00],
                 [0.08, 0.00],
                 [0.03, 0.04]])


def delays_for_angle(theta_deg):
    """Delay (s) to apply to each mic so a far-field source at theta lines up."""
    theta = np.deg2rad(theta_deg)
    u = np.array([np.cos(theta), np.sin(theta)])   
    return MICS @ u / C                             


def beamform(frame, theta_deg):
    """Delay-and-sum one frame (shape: mics x WIN) for one direction.
    Delays are applied as phase shifts in the frequency domain."""
    spectra = np.fft.rfft(frame, axis=1)                   
    freqs = np.fft.rfftfreq(frame.shape[1], 1 / FS)        
    d = delays_for_angle(theta_deg)                        
    phase = np.exp(-2j * np.pi * freqs[None, :] * d[:, None])
    summed = (spectra * phase).sum(axis=0) / len(MICS)     
    return summed                                          


def estimate_doa(x):
    """x: mics x samples. Returns the angle (0..359 deg) with the highest
    summed output power over all windows."""
    angles = np.arange(360)
    power = np.zeros(360)
    n_frames = x.shape[1] // WIN
    for i in range(n_frames):
        frame = x[:, i * WIN:(i + 1) * WIN]
        for k, a in enumerate(angles):
            y = beamform(frame, a)
            power[k] += np.sum(np.abs(y) ** 2)
    return angles[np.argmax(power)], power


def beamform_signal(x, theta_deg):
    """Time-domain output (one window at a time) steered at theta."""
    out = []
    for i in range(x.shape[1] // WIN):
        frame = x[:, i * WIN:(i + 1) * WIN]
        out.append(np.fft.irfft(beamform(frame, theta_deg), n=WIN))
    return np.concatenate(out)


def angle_error(est, true):
    """Circular angular error in degrees."""
    return abs((est - true + 180) % 360 - 180)


# code to test beamformer
# Test using a synthetic white-noise source with known delays for a 60° direction, then add 0 dB SNR noise and compare the estimated angle with the true angle.
if __name__ == "__main__":
    rng = np.random.default_rng(0)
    true_angle = 60
    n = WIN * 10

    s = rng.standard_normal(n)                 
    S = np.fft.rfft(s)
    f = np.fft.rfftfreq(n, 1 / FS)

    
    d = delays_for_angle(true_angle)
    x = np.array([np.fft.irfft(S * np.exp(2j * np.pi * f * dm), n=n) for dm in d])

    # add noise at a chosen SNR
    snr_db = 0
    noise_power = np.mean(x ** 2) / 10 ** (snr_db / 10)
    x = x + np.sqrt(noise_power) * rng.standard_normal(x.shape)

    est, _ = estimate_doa(x)
    print(f"true = {true_angle} deg, estimated = {est} deg, "
          f"error = {angle_error(est, true_angle)} deg")