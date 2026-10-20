# -*- coding: utf-8 -*-
"""Decode the user's logged RX frames against the protocol module."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from dl645_breaker import protocol as P

RX = [
    # (tx_di_desc, rx_hex)
    ("电压块 0201FF00", "6801000000000068910a333234355356b3558456c516"),
    ("电流块 0202FF00", "6801000000000068910d333235353333333333333333330916"),
    ("剩余电流 0290FF00", "680100000000006891073332c3353438336516"),
    ("额定值 0291FF00", "680100000000006891083332c435334933304116"),
    ("状态字1 04000501", "6801000000000068910534383337337016"),
    ("状态字2 04000502", "68010000000000689105353833373b7916"),
    ("型号 0400040B", "6801000000000068910e3e373337636363636363636363632d16"),
    ("地址 04000401", "6801000000000068910a343733373433333333337416"),
    ("协议 0400040D", "6801000000000068911440373337777f6287606563636a535353535353537016"),
    ("固件 0400040F", "680100000000006891244237333763636363636363636363636363636363636363636363636363636363636363339916"),
    ("硬件 04000410", "680100000000006891244337333763636363636363636363636363636363636363636363636363636363636363339a16"),
]

def bcd(bs):
    return int("".join("%02X" % b for b in bs))

def rev(bs):
    return list(reversed(bs))

for desc, hx in RX:
    try:
        f = P.parse_frame(bytes.fromhex(hx))
    except Exception as e:
        print("%-14s ERROR %s" % (desc, e))
        continue
    data = f["data"]
    wire_di = data[0:4]
    di = P.di_canon(wire_di)          # DI3 DI2 DI1 DI0
    payload = data[4:]
    print("%-14s di=%s wire=%s L=%d payload_len=%d" % (
        desc, ".".join("%02X" % x for x in di),
        " ".join("%02X" % x for x in wire_di), f["length"], len(payload)))
    print("          payload_raw= %s" % " ".join("%02X" % b for b in payload))
    print("          payload_bcd= %s" % "".join("%02X" % b for b in payload))
    # interpretations
    if di == (0x02, 0x01, 0xFF, 0x00):   # 3-phase voltage, 2B each
        for i in range(3):
            seg = payload[i*2:i*2+2]
            print("          电压%c 高先=%7.1fV  低先=%7.1fV" % (
                "ABC"[i], bcd(seg)/10.0, bcd(rev(seg))/10.0))
    elif di == (0x02, 0x02, 0xFF, 0x00): # 3-phase current, 3B each
        for i in range(3):
            seg = payload[i*3:i*3+3]
            print("          电流%c 高先=%9.2fA  低先=%9.2fA" % (
                "ABC"[i], bcd(seg)/10.0, bcd(rev(seg))/10.0))
    elif di == (0x02, 0x90, 0xFF, 0x00): # residual: phase(1) + value(2)
        ph = payload[0] if payload else 0
        val = payload[1:3]
        print("          最大相=%d(%s) 剩余电流 高先=%d mA 低先=%d mA" % (
            ph, {1:"A",2:"B",3:"C"}.get(ph,"?"),
            bcd(val) if len(val)==2 else -1,
            bcd(rev(val)) if len(val)==2 else -1))
    elif di == (0x02, 0x91, 0xFF, 0x00): # rated: value(2)+time(2)
        print("          额定动作 高先=%d mA 低先=%d mA  时间 高先=%d ms 低先=%d ms" % (
            bcd(payload[0:2]), bcd(rev(payload[0:2])),
            bcd(payload[2:4]), bcd(rev(payload[2:4]))))
def is_bcd(b):
    return ((b >> 4) <= 9) and ((b & 0x0F) <= 9)

    print()

# ---- brute-force reconstruct the 额定值 RX frame (one hex digit lost in log) ----
hx = "680100000000006891083332c43533493330416"   # 39 chars, L=08
prefix = hx[:20]                                  # 68 + addr + 68 + 91 + 08
tail = hx[20:]                                    # "3332c43533493330416" (19 chars)
print("prefix=%s tail=%s" % (prefix, tail))
import itertools
sols = []
# insert one hex char anywhere in tail to make 20 chars: 4-byte DI + 4-byte data + CS + 16
for pos in range(0, len(tail) + 1):
    for ch in "0123456789abcdefABCDEF":
        cand = tail[:pos] + ch + tail[pos:]
        if not cand.endswith("16"):
            continue
        wire = cand[:-4]
        if len(wire) != 16:
            continue
        raw = bytes.fromhex(prefix + cand)
        try:
            f = P.parse_frame(raw)
        except ValueError:
            continue
        payload = f["data"][4:]
        if is_bcd(payload[0]) and is_bcd(payload[1]) and \
           is_bcd(payload[2]) and is_bcd(payload[3]):
            sols.append((cand, f, payload))

print("valid checksum solutions:")
for cand, f, payload in sols[:10]:
    print("  wire=%s cs_ok data=%s payload=%s" % (
        cand, f["length"],
        " ".join("%02X" % b for b in payload)))
