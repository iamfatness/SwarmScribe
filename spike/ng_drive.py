#!/usr/bin/env python3
"""rtpengine feasibility spike: drive a 2-party SDES-SRTP call over the NG protocol.

A sends 440 Hz (0..6 s), B sends 880 Hz (starts 1 s late, 1 s gap at 3..4 s).
Optionally attaches a 'subscribe' consumer (live tap) and records what it gets.
"""
import argparse, json, math, os, random, socket, struct, sys, threading, time, base64

# ---------------- bencode ----------------
def benc(x):
    if isinstance(x, bool):
        x = "yes" if x else "no"
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode()
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(benc(i) for i in x) + b"e"
    if isinstance(x, dict):
        return b"d" + b"".join(benc(k) + benc(v) for k, v in sorted(x.items())) + b"e"
    raise TypeError(x)

def bdec(b, i=0):
    c = b[i:i+1]
    if c == b"i":
        e = b.index(b"e", i); return int(b[i+1:e]), e + 1
    if c == b"l":
        i += 1; out = []
        while b[i:i+1] != b"e":
            v, i = bdec(b, i); out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1; out = {}
        while b[i:i+1] != b"e":
            k, i = bdec(b, i); v, i = bdec(b, i); out[k if isinstance(k, str) else k.decode()] = v
        return out, i + 1
    col = b.index(b":", i); n = int(b[i:col]); s = b[col+1:col+1+n]
    try:
        s = s.decode()
    except UnicodeDecodeError:
        pass
    return s, col + 1 + n

NG = ("127.0.0.1", 2223)
_ngsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_ngsock.settimeout(5)

def ng(cmd, **kw):
    kw["command"] = cmd
    cookie = "%08x" % random.getrandbits(32)
    _ngsock.sendto(cookie.encode() + b" " + benc(kw), NG)
    data, _ = _ngsock.recvfrom(65535)
    ck, _, body = data.partition(b" ")
    resp, _ = bdec(body)
    shown = {k: v for k, v in resp.items()}
    print(f"\n=== NG {cmd} -> result={resp.get('result')} ===")
    print(json.dumps(shown, indent=1, default=str)[:4000])
    return resp

# ---------------- audio ----------------
def lin2ulaw(s):
    BIAS = 0x84; CLIP = 32635
    sign = 0x80 if s < 0 else 0
    if s < 0: s = -s
    s = min(s, CLIP) + BIAS
    exp = 7; mask = 0x4000
    while exp > 0 and not (s & mask):
        exp -= 1; mask >>= 1
    mant = (s >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mant)) & 0xFF

def tone_frame(freq, n0, amp=8000):
    return bytes(lin2ulaw(int(amp * math.sin(2 * math.pi * freq * (n0 + k) / 8000))) for k in range(160))

# ---------------- SRTP ----------------
def mk_key():
    return os.urandom(30)

def crypto_line(key):
    return "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:" + base64.b64encode(key).decode()

def parse_sdp(sdp):
    port = None; keys = []; proto = None; mlines = []
    for line in sdp.splitlines():
        if line.startswith("m="):
            f = line[2:].split(); mlines.append(line)
            if port is None:
                port = int(f[1]); proto = f[2]
        if line.startswith("a=crypto:") and "AES_CM_128_HMAC_SHA1_80" in line:
            keys.append(base64.b64decode(line.split("inline:")[1].split("|")[0].strip()))
    return port, (keys[0] if keys else None), proto, mlines

def sdp_for(ip, port, key, sess, srtp=True):
    proto = "RTP/SAVP" if srtp else "RTP/AVP"
    s = (f"v=0\r\no=- {sess} 1 IN IP4 {ip}\r\ns=spike\r\nc=IN IP4 {ip}\r\nt=0 0\r\n"
         f"m=audio {port} {proto} 0\r\na=rtpmap:0 PCMU/8000\r\na=sendrecv\r\na=ptime:20\r\n")
    if srtp:
        s += crypto_line(key) + "\r\n"
    return s

class Leg:
    def __init__(self, name, port, srtp):
        self.name = name; self.port = port; self.srtp = srtp
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port)); self.sock.settimeout(0.2)
        self.key = mk_key(); self.rx = 0; self.rx_ok = 0; self.stop = False
        self.tx_session = None; self.rx_session = None

    def setup_srtp(self, peer_key):
        if not self.srtp: return
        import pylibsrtp
        p = pylibsrtp.Policy(key=self.key, ssrc_type=pylibsrtp.Policy.SSRC_ANY_OUTBOUND,
                             srtp_profile=pylibsrtp.Policy.SRTP_PROFILE_AES128_CM_SHA1_80)
        self.tx_session = pylibsrtp.Session(policy=p)
        if peer_key:
            p2 = pylibsrtp.Policy(key=peer_key, ssrc_type=pylibsrtp.Policy.SSRC_ANY_INBOUND,
                                  srtp_profile=pylibsrtp.Policy.SRTP_PROFILE_AES128_CM_SHA1_80)
            self.rx_session = pylibsrtp.Session(policy=p2)

    def rx_loop(self):
        while not self.stop:
            try:
                d, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            self.rx += 1
            if self.rx_session:
                try:
                    self.rx_session.unprotect(d); self.rx_ok += 1
                except Exception:
                    pass
            else:
                self.rx_ok += 1

def send_stream(leg, dest, freq, t_start, t_end, gaps, t0):
    ssrc = random.getrandbits(32); seq = random.getrandbits(16); ts = random.getrandbits(32)
    n = 0
    total = int(t_end * 50)
    for i in range(total):
        tnow = i * 0.02
        target = t0 + tnow
        dt = target - time.monotonic()
        if dt > 0: time.sleep(dt)
        in_gap = tnow < t_start or any(a <= tnow < b for a, b in gaps)
        if not in_gap:
            payload = tone_frame(freq, n)
            hdr = struct.pack("!BBHII", 0x80, 0, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
            pkt = hdr + payload
            if leg.tx_session:
                pkt = leg.tx_session.protect(pkt)
            leg.sock.sendto(pkt, dest)
            seq += 1
        # RTP timestamp advances with wallclock (like a real hold/VAD gap)
        ts += 160; n += 160

def sdp_m(port, key, label, direction, srtp=True):
    proto = "RTP/SAVP" if srtp else "RTP/AVP"
    s = f"m=audio {port} {proto} 0\r\na=rtpmap:0 PCMU/8000\r\na={direction}\r\na=label:{label}\r\n"
    if srtp: s += crypto_line(key) + "\r\n"
    return s

def sdp_head(sess):
    return f"v=0\r\no=- {sess} 1 IN IP4 127.0.0.1\r\ns=siprec\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"

def parse_all(sdp):
    """Return list of (port, key) per m-line."""
    out = []
    for line in sdp.splitlines():
        if line.startswith("m="):
            out.append([int(line.split()[1]), None, []])
        elif out and line.startswith("a=crypto:") and out[-1][1] is None:
            out[-1][1] = base64.b64decode(line.split("inline:")[1].split("|")[0].strip())
        elif out and line.startswith("a="):
            out[-1][2].append(line)
    return out

def srs(a):
    """SIPREC-recording-server style: SRC (us) sends two sendonly labelled streams; rtpengine is the sink."""
    srtp = not a.plain
    A = Leg("A", 30000, srtp); B = Leg("B", 30002, srtp)
    flags = [f for f in a.extra_flags.split(",") if f]
    if a.mode == "srs-publish":
        sdp = sdp_head(1) + sdp_m(A.port, A.key, "1", "sendonly", srtp) + sdp_m(B.port, B.key, "2", "sendonly", srtp)
        r = ng("publish", **{"call-id": a.callid, "from-tag": "src", "sdp": sdp, "record call": "yes", "flags": flags,
                             "metadata": "mode=publish"})
        if r.get("result") != "ok":
            return
        ng("start recording", **{"call-id": a.callid})
        ms = parse_all(r["sdp"])
        print("publish answer m-lines:", [(m[0], m[1] is not None, m[2]) for m in ms])
        pA, kA = ms[0][0], ms[0][1]; pB, kB = ms[1][0], ms[1][1]
    else:  # srs-split (drachtio style): stream 1 = offer leg, stream 2 = answer leg
        off = ng("offer", **{"call-id": a.callid, "from-tag": "tagA", "label": "1",
                             "sdp": sdp_head(1) + sdp_m(A.port, A.key, "1", "sendonly", srtp),
                             "record call": "yes", "flags": flags, "metadata": "mode=split"})
        ans = ng("answer", **{"call-id": a.callid, "from-tag": "tagA", "to-tag": "tagB", "label": "2",
                              "sdp": sdp_head(2) + sdp_m(B.port, B.key, "2", "sendonly", srtp),
                              "record call": "yes", "flags": flags})
        mo = parse_all(off["sdp"]); ma = parse_all(ans["sdp"])
        print("offer-out m:", [(m[0], m[2]) for m in mo]); print("answer-out m:", [(m[0], m[2]) for m in ma])
        # SRC sends stream A to the port in the answer-out SDP (rtpengine side facing A), B to the offer-out port
        pA, kA = ma[0][0], ma[0][1]; pB, kB = mo[0][0], mo[0][1]
    A.setup_srtp(kA); B.setup_srtp(kB)
    for leg in (A, B):
        threading.Thread(target=leg.rx_loop, daemon=True).start()
    t0 = time.monotonic() + 0.1
    ta = threading.Thread(target=send_stream, args=(A, ("127.0.0.1", pA), 440, 0.0, 6.0, [], t0))
    tb = threading.Thread(target=send_stream, args=(B, ("127.0.0.1", pB), 880, 1.0, 6.0, [(3.0, 4.0)], t0))
    ta.start(); tb.start(); time.sleep(3.5)
    ng("query", **{"call-id": a.callid})
    ta.join(); tb.join(); time.sleep(0.5); A.stop = B.stop = True
    print(f"bounce-back to SRC: A sock got {A.rx} pkts, B sock got {B.rx} pkts")
    ng("delete", **{"call-id": a.callid, "from-tag": "src" if a.mode == "srs-publish" else "tagA"})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="call", choices=["call", "srs-split", "srs-publish"])
    ap.add_argument("--plain", action="store_true", help="plain RTP instead of SDES-SRTP")
    ap.add_argument("--subscribe", action="store_true")
    ap.add_argument("--forward", action="store_true")
    ap.add_argument("--callid", default="spike-%d" % int(time.time()))
    ap.add_argument("--extra-flags", default="")
    a = ap.parse_args()
    if a.mode != "call":
        return srs(a)
    srtp = not a.plain
    A = Leg("A", 30000, srtp); B = Leg("B", 30002, srtp)
    flags = [f for f in a.extra_flags.split(",") if f]

    print("ping:", ng("ping"))
    off = ng("offer", **{"call-id": a.callid, "from-tag": "tagA", "sdp": sdp_for("127.0.0.1", A.port, A.key, 1, srtp),
                         "record call": "yes", "flags": flags,
                         "metadata": "spike=1|leg_a=alice|leg_b=bob"})
    pB, kB_from_rtpe, protoB, _ = parse_sdp(off["sdp"])
    ans = ng("answer", **{"call-id": a.callid, "from-tag": "tagA", "to-tag": "tagB",
                          "sdp": sdp_for("127.0.0.1", B.port, B.key, 2, srtp), "record call": "yes", "flags": flags})
    pA, kA_from_rtpe, protoA, _ = parse_sdp(ans["sdp"])
    print(f"rtpengine ports: A->{pA} ({protoA}), B->{pB} ({protoB}); keys present A={kA_from_rtpe is not None} B={kB_from_rtpe is not None}")
    A.setup_srtp(kA_from_rtpe); B.setup_srtp(kB_from_rtpe)

    if a.forward:
        ng("start forwarding", **{"call-id": a.callid})
    tap = None
    if a.subscribe:
        try:
            sr = ng("subscribe request", **{"call-id": a.callid, "from-tags": ["tagA", "tagB"], "flags": ["SIPREC"]})
            print("subscribe request SDP:\n" + str(sr.get("sdp")))
            # answer with a plain RTP consumer; one m-line per offered m-line
            mls = [l for l in str(sr.get("sdp", "")).splitlines() if l.startswith("m=")]
            tap_socks = []
            body = "v=0\r\no=- 3 1 IN IP4 127.0.0.1\r\ns=tap\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
            for i, ml in enumerate(mls):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("127.0.0.1", 31000 + 2*i)); s.settimeout(0.2)
                tap_socks.append(s)
                body += f"m=audio {31000+2*i} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=recvonly\r\n"
            sa = ng("subscribe answer", **{"call-id": a.callid, "to-tag": sr.get("to-tag"), "sdp": body})
            tap = {"socks": tap_socks, "counts": [0]*len(tap_socks), "payloads": [bytearray() for _ in tap_socks], "stop": False}
            def tap_loop(i):
                while not tap["stop"]:
                    try:
                        d, _ = tap["socks"][i].recvfrom(4096)
                    except socket.timeout:
                        continue
                    tap["counts"][i] += 1; tap["payloads"][i] += d[12:]
            for i in range(len(tap_socks)):
                threading.Thread(target=tap_loop, args=(i,), daemon=True).start()
        except Exception as e:
            print("subscribe failed:", repr(e))

    for leg in (A, B):
        threading.Thread(target=leg.rx_loop, daemon=True).start()
    t0 = time.monotonic() + 0.1
    ta = threading.Thread(target=send_stream, args=(A, ("127.0.0.1", pA), 440, 0.0, 6.0, [], t0))
    tb = threading.Thread(target=send_stream, args=(B, ("127.0.0.1", pB), 880, 1.0, 6.0, [(3.0, 4.0)], t0))
    ta.start(); tb.start()
    time.sleep(3.5)
    ng("query", **{"call-id": a.callid})
    ta.join(); tb.join(); time.sleep(0.5)
    A.stop = B.stop = True
    print(f"A received {A.rx} pkts ({A.rx_ok} decrypted ok); B received {B.rx} pkts ({B.rx_ok} decrypted ok)")
    if tap:
        tap["stop"] = True
        print("TAP packet counts per m-line:", tap["counts"])
        os.makedirs("out/tap", exist_ok=True)
        for i, p in enumerate(tap["payloads"]):
            open(f"out/tap/tap_{i}.ulaw", "wb").write(bytes(p))
    ng("delete", **{"call-id": a.callid, "from-tag": "tagA"})

if __name__ == "__main__":
    main()
