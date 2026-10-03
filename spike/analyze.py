#!/usr/bin/env python3
"""Inspect recording outputs: format, channels, duration, and per-channel tone timeline."""
import json, subprocess, sys, pathlib
import numpy as np

def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "{}")

def decode(p, ch):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "f32le", "-ar", "8000", "-ac", str(ch), "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, ch)

def tone_power(x, f, sr=8000):
    n = np.arange(len(x)); return abs(np.dot(x, np.exp(-2j * np.pi * f * n / sr))) / max(len(x), 1)

def timeline(x):
    out = []
    for i in range(0, len(x), 4000):  # 0.5 s windows
        w = x[i:i+4000]
        p4, p8 = tone_power(w, 440), tone_power(w, 880)
        out.append("4" if p4 > 0.01 and p4 > 3*p8 else "8" if p8 > 0.01 and p8 > 3*p4 else "B" if p4 > 0.01 and p8 > 0.01 else "-")
    return "".join(out)

def main():
  for root in sys.argv[1:]:
      for p in sorted(pathlib.Path(root).rglob("*")):
          if not p.is_file():
              continue
          print(f"\n### {p} ({p.stat().st_size} bytes)")
          if p.suffix in (".wav", ".mp3", ".ulaw", ".pcap", ".opus", ".flac"):
              if p.suffix == ".ulaw":
                  r = subprocess.run(["ffmpeg", "-v", "error", "-f", "mulaw", "-ar", "8000", "-ac", "1", "-i", str(p), "-f", "f32le", "-"], capture_output=True)
                  x = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 1)
                  print(f"  raw ulaw: {len(x)/8000:.2f}s  timeline(0.5s bins, 4=440Hz 8=880Hz B=both -=silence): {timeline(x[:,0])}")
                  continue
              if p.suffix == ".pcap":
                  continue
              info = probe(p)
              for s in info.get("streams", []):
                  print(f"  codec={s.get('codec_name')} sr={s.get('sample_rate')} ch={s.get('channels')} layout={s.get('channel_layout')} dur={s.get('duration')}")
              tags = info.get("format", {}).get("tags")
              if tags: print("  tags:", tags)
              ch = int((info.get("streams") or [{}])[0].get("channels", 1))
              x = decode(p, ch)
              for c in range(ch):
                  print(f"  ch{c}: {len(x)/8000:.2f}s timeline(0.5s bins, 4=440Hz 8=880Hz B=both -=silence): {timeline(x[:,c])}")
          elif p.stat().st_size < 20000:
              try:
                  print("  " + p.read_text(errors="replace").replace("\n", "\n  ")[:3000])
              except Exception as e:
                  print("  (unreadable)", e)

if __name__ == '__main__':
    main()
