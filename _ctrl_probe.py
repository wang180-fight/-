# -*- coding: utf-8 -*-
"""远程控制诊断探针 —— 直接连真实断路器, 定位 0x04(密码错/未授权) 根因。

用法:
    python _ctrl_probe.py COM3 [通信地址12位] [波特率]

功能:
    1) 读设备标识(型号/地址/协议/固件/硬件)与 状态字1/2、控制字1~5, 原样打印;
    2) 尝试免密读取 0/1/2 级密码(多数固件会回02=禁读);
    3) 用候选出厂密码在 0 级下试发“分闸(trip)”控制, 逐条打印设备返回的
       原始控制码 + 原始 ERR 字节, 以便肉眼区分:
           0x04=密码错/未授权  0x10=远程控制失败(就地/远方等)  0x01/0x02=数据标识问题

安全提示:
    * 本脚本只做“分闸”的授权验证, 密码错误时设备不会动作, 只会回错误字;
      但绝大多数 DL645 设备在连续密码错误达到一定次数后会【锁定】,
      锁定期间即使密码正确也回 0x04。若之前已多次试错, 请先给设备【断电复位】。
    * 候选密码务必“少量、精准”, 不要盲目穷举。
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from duan_LU.dl645_breaker import protocol as P
from duan_LU.dl645_breaker.device import (
    SerialTransport, BreakerClient, make_password, make_operator)

TRIP_DI = (0x06, 0x01, 0x01, 0x01)          # 远程分闸(与 GUI 一致)

ERR = {
    0x01: "数据非法(bit0)",
    0x02: "无请求数据项(bit1)",
    0x04: "密码错/未授权(bit2)",
    0x08: "通信速率不能更改(bit3)",
    0x10: "远程控制失败(bit4)",
    0x20: "升级包错误(bit5)",
}


def err_text(byte):
    hits = [v for k, v in sorted(ERR.items()) if byte & k]
    return (" + ".join(hits)) if hits else ("未知ERR 0x%02X" % byte)


def show_hex(tag, data):
    print("  %-10s [%dB] %s" % (tag, len(data),
                                " ".join("%02X" % b for b in data)))


def read_raw(tr, addr, di):
    """返回 (跑通?, control, data) —— 直接底层解析以保留原始字节。"""
    frame = P.build_read_request(addr, di)
    raw = tr.send(frame)
    r = P.parse_response(raw)
    return (r["control"] & 0xF0) != 0xD0, r["control"], r["data"]


def ascii_of(data):
    return "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in data)


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    addr_str = sys.argv[2] if len(sys.argv) > 2 else "000000000001"
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else 9600

    print("=" * 66)
    print("远程控制诊断探针  端口=%s  地址=%s  波特率=%d" % (port, addr_str, baud))
    print("=" * 66)

    tr = SerialTransport(port, baud, timeout=1.5)
    addr = P.encode_addr(addr_str)

    # ---- 1) 设备标识 / 状态 / 控制字 ----
    print("\n[1] 设备标识与运行状态")
    items = [
        ("型号", (0x04, 0x00, 0x04, 0x0B)),
        ("通信地址", (0x04, 0x00, 0x04, 0x01)),
        ("协议版本", (0x04, 0x00, 0x04, 0x0D)),
        ("固件版本", (0x04, 0x00, 0x04, 0x0F)),
        ("硬件版本", (0x04, 0x00, 0x04, 0x10)),
        ("运行状态字1", (0x04, 0x00, 0x05, 0x01)),
        ("运行状态字2", (0x04, 0x00, 0x05, 0x02)),
    ]
    for name, di in items:
        try:
            ok, ctrl, data = read_raw(tr, addr, di)
            text = "OK " if ok else "ERR"
            print("  %-10s %s  data=%s   ASCII=%r" % (
                name, text,
                " ".join("%02X" % b for b in data[4:]),
                ascii_of(data[4:])))
        except Exception as exc:
            print("  %-10s 读取异常: %s" % (name, exc))

    print("\n[1b] 控制字(检查是否存在禁遥控的厂商扩展位)")
    for i, name in enumerate(("控制字1", "控制字2", "控制字3",
                              "控制字4", "控制字5"), 1):
        try:
            ok, ctrl, data = read_raw(tr, addr, (0x04, 0x00, 0x10, i))
            print("  %-10s %s data=%s" % (
                name, "OK " if ok else "ERR",
                " ".join("%02X" % b for b in data[4:])))
        except Exception as exc:
            print("  %-10s 读取异常: %s" % (name, exc))

    # ---- 2) 免密读取密码 ----
    print("\n[2] 尝试免密读取各级密码(04000C01/02/03)")
    for level, di in ((0, (0x04, 0x00, 0x0C, 0x01)),
                      (1, (0x04, 0x00, 0x0C, 0x02)),
                      (2, (0x04, 0x00, 0x0C, 0x03))):
        try:
            ok, ctrl, data = read_raw(tr, addr, di)
            if not ok:
                print("  %d级密码  异常 control=0x%02X err=%s" % (
                    level, ctrl, err_text(data[0] if data else 0)))
                continue
            payload = data[4:]
            if len(payload) < 4 or payload[0] == 0xFF:
                print("  %d级密码  不可读: %s" % (level,
                      " ".join("%02X" % b for b in payload)))
            else:
                print("  %d级密码  可读! PA=0x%02X P0P1P2=%s" % (
                    level, payload[0],
                    " ".join("%02X" % b for b in payload[1:4])))
        except Exception as exc:
            print("  %d级密码  读取异常: %s" % (level, exc))

    # ---- 3) 0级候选密码试发分闸 ----
    print("\n[3] 0级候选出厂密码 试发【分闸】(每个均打印设备原始应答 ERR)")
    cands = ["000000", "111111", "888888", "000001", "123456",
             "999999", "666666", "000002", "123123"]
    # 增加“地址后6位 / 全地址”派生的候选
    for extra in (addr_str[-6:], addr_str[-12:][-6:], addr_str.lstrip("0") or "0"):
        if extra.isdigit() and extra.zfill(6) not in cands:
            cands.append(extra.zfill(6))

    op = make_operator("00000000")
    for code in cands:
        pwd = make_password(0, code)
        try:
            frame = P.build_control_request(addr, TRIP_DI, pwd, op, [])
            raw = tr.send(frame)
            r = P.parse_response(raw)
            ok = (r["control"] & 0xF0) != 0xD0
            eb = r["data"][0] if (r["data"] and not ok) else 0
            print("  0级 pwd=%s  ctrl=0x%02X  %s" % (
                code, r["control"],
                "成功(闸已动作)" if ok else err_text(eb)))
            if ok:
                print("    >>> 找到有效 0 级密码: %s <<<" % code)
                break
        except Exception as exc:
            print("  0级 pwd=%s 发送异常: %s" % (code, exc))

    tr.close()
    print("\n诊断结束。")


if __name__ == "__main__":
    main()