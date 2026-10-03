"""Joins two mono 16 kHz 16-bit WAVs into one stereo WAV: left from 0.5 s, right after left ends
plus 0.5 s. Used by make_stereo_speech.ps1."""

import sys
import wave

import numpy as np

RATE = 16000


def read(path):
    with wave.open(path, "rb") as handle:
        assert (handle.getframerate(), handle.getsampwidth(), handle.getnchannels()) == (RATE, 2, 1)
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    loud = np.flatnonzero(np.abs(samples) > 200)  # trim synthesiser padding to keep the file small
    return samples[loud[0] : loud[-1] + 1]


def main(left_path, right_path, out_path):
    left, right = read(left_path), read(right_path)
    gap = RATE // 2
    right_start = gap + len(left) + gap
    total = right_start + len(right)
    stereo = np.zeros((total, 2), dtype="<i2")
    stereo[gap : gap + len(left), 0] = left
    stereo[right_start:total, 1] = right
    with wave.open(out_path, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(stereo.tobytes())


if __name__ == "__main__":
    main(*sys.argv[1:4])
