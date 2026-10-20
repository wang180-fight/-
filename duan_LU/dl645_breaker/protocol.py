# -*- coding: utf-8 -*-
"""DL/T 645-2007 扩展规约(带漏电保护塑壳断路器) 帧编解码底层模块。

帧结构: 68H | A0-A5(地址,低字节在前) | 68H | C(控制码) | L(数据域长度)
        | DATA(数据域, 每字节+33H) | CS(校验和) | 16H

CS = 从第一个68H到校验码之前所有字节的算术和 mod 256。
数据域(标识码、密码、操作者代码、数据)传输时按字节+33H, 接收时-33H。
数据标识编码用4字节 DI3 DI2 DI1 DI0, 线上顺序为 DI0 DI1 DI2 DI3。

字节序约定(重要, 2025-10 实测真机修正):
  - 多字节 BCD 数值量(电压/电流/剩余电流/额定值/次数/整定参数等)传输时低字节在前;
    bcd_encode_lf() / bcd_decode_lf() 用于这类数值量。
  - 分量型字段(日期 YYMMDDWW、时间 hhmmss、广播校时 YYMMDDhhmmss、
    定时自检 DDhhmm、密码 P0P1P2、操作者代码 C0C1C2C3)仍按书写顺序传输,
    使用 bcd_encode() / bcd_decode()。
  - 地址域 A0..A5 低字节在前, 由 encode_addr() / decode_addr() 处理。
"""

# ---- 帧定界 / 常量 ----
START = 0x68          # 帧起始符
END = 0x16            # 帧结束符
ADD33 = 0x33          # 数据域加33H

# ---- 控制码 ----
C_READ           = 0x11   # 读数据
C_READ_MORE      = 0x12   # 读后续数据
C_READ_ADDR      = 0x13   # 读通信地址(仅点对点)
C_WRITE          = 0x14   # 写数据
C_WRITE_ADDR     = 0x15   # 写通信地址
C_CHANGE_BAUD    = 0x17   # 更改通信速率
C_CHANGE_PWD     = 0x18   # 修改密码
C_QUERY_ALARM    = 0x19   # 查询/应答告警
C_CLEAR_DATA     = 0x1B   # 数据清零
C_CONTROL        = 0x1C   # 控制指令(远程合分闸/试跳/自检)
C_UPGRADE        = 0x1D   # 远程软件升级
C_BROADCAST_TIME = 0x08   # 广播校时

# 应答控制码 = 请求码 | 0x80 (正常) 或 | 0xD0 (异常), 另有后续数据再加 0x20


# ---- BCD 编码 / 解码 ----
def bcd_encode(value, nbytes):
    """整数 -> nbytes 字节压缩BCD码(自然顺序, 高位字节在前)。"""
    value = max(int(value), 0)
    s = ("%0*d" % (nbytes * 2, value))[-nbytes * 2:]
    return [int(s[i:i + 2], 16) for i in range(0, nbytes * 2, 2)]


def bcd_decode(data):
    """字节序列(压缩BCD) -> 整数。"""
    return int("".join("%02X" % b for b in data))


def bcd_encode_lf(value, nbytes):
    """整数 -> nbytes 字节压缩BCD码(低字节在前, DL645 数值量约定)。"""
    return list(reversed(bcd_encode(value, nbytes)))


def bcd_decode_lf(data):
    """字节序列(压缩BCD, 低字节在前) -> 整数。"""
    return bcd_decode(list(reversed(data)))


def encode_addr(addr_str):
    """12位十进制地址字符串 -> 6字节地址(低字节在前 A0..A5)。"""
    s = str(addr_str).strip()
    if not s or not s.isdigit():
        raise ValueError("通信地址必须为十进制数字")
    s = s[-12:].rjust(12, "0")
    hi = bcd_encode(int(s), 6)          # [A5,A4,A3,A2,A1,A0]
    return list(reversed(hi))           # [A0,A1,A2,A3,A4,A5] 低字节在前


def decode_addr(addr_bytes):
    """6字节地址(低字节在前) -> 12位字符串。"""
    hi = list(reversed(addr_bytes))
    return "".join("%02X" % b for b in hi)


def checksum(body):
    """校验和: 各字节算术和 mod 256。"""
    return sum(body) & 0xFF


def build_frame(addr, control, data):
    """组帧。

    addr:    6字节地址(低字节在前)列表/字节串
    control: 控制码
    data:    未加33H的数据域字节列表
    返回:    完整帧字节串
    """
    addr = list(addr)
    if len(addr) != 6:
        raise ValueError("地址域必须为6字节")
    body = [START] + addr + [START, control, len(data)]
    body += [(b + ADD33) & 0xFF for b in data]
    body += [checksum(body), END]
    return bytes(body)


def parse_frame(raw):
    """解析一帧。

    返回 dict: addr(6字节), control, length, data(已减33H), cs_ok
    异常: ValueError
    """
    raw = bytes(raw)
    if len(raw) < 12:
        raise ValueError("帧长度过短")
    if raw[0] != START:
        raise ValueError("帧起始符错误")
    if raw[7] != START:
        raise ValueError("数据区起始符错误")
    if raw[-1] != END:
        raise ValueError("帧结束符错误")
    length = raw[9]
    if length != len(raw) - 12:
        raise ValueError("数据域长度不符")
    if checksum(raw[:-2]) != raw[-2]:
        raise ValueError("校验和错误")
    data = [(b - ADD33) & 0xFF for b in raw[10:-2]]
    return {
        "addr": list(raw[1:7]),
        "control": raw[8],
        "length": length,
        "data": data,
    }


def di_wire(di):
    """规范化数据标识为线上顺序 DI0 DI1 DI2 DI3(低位字节在前)。

    规约表格中数据标识按 DI3 DI2 DI1 DI0 书写, 但帧内传输顺序为 DI0 DI1 DI2 DI3。
    """
    return list(reversed(list(di)))


def di_canon(wire):
    """线上顺序(DI0 DI1 DI2 DI3) -> 规范顺序(DI3 DI2 DI1 DI0)。"""
    return tuple(reversed(list(wire)))


# ---- 请求帧构造(主站 -> 从站) ----
def build_read_request(addr, di, control=C_READ):
    """读数据请求: DATA = DI0 DI1 DI2 DI3。"""
    data = di_wire(di)
    return build_frame(addr, control, data)


def build_write_request(addr, di, password, operator, data,
                        control=C_WRITE):
    """写数据请求: DATA = DI0 DI1 DI2 DI3 + PA P0 P1 P2 + C0..C3 + data。

    password: 4字节密码(第1字节为级别 PA)
    operator: 4字节操作者代码
    data:     待写入数据字节列表
    """
    payload = di_wire(di) + list(password) + list(operator) + list(data)
    return build_frame(addr, control, payload)


def build_control_request(addr, di, password, operator, data):
    """控制指令(远程合/分闸、试跳、自检): 控制码 1CH。"""
    return build_write_request(addr, di, password, operator, data,
                               control=C_CONTROL)


def build_clear_request(addr, di, password, operator):
    """数据清零请求: 控制码 1BH。

    事件总清零: di=(FF,FF,FF,FF); 分项清零: di 某字节为 FF。
    """
    payload = di_wire(di) + list(password) + list(operator)
    return build_frame(addr, C_CLEAR_DATA, payload)


def build_change_pwd_request(addr, old_pwd, new_pwd):
    """修改密码请求(控制码18H):
    DATA = DI0 DI1 DI2 DI3 + 原密码4 + 新密码4
    数据标识末字节 = 级别+1(0级->01, 1级->02, 2级->03)。
    """
    di = (0x04, 0x00, 0x0C, (new_pwd[0] & 0x0F) + 1)
    payload = di_wire(di) + list(old_pwd) + list(new_pwd)
    return build_frame(addr, C_CHANGE_PWD, payload)


def build_read_addr_request(addr_aa=(0xAA,) * 6):
    """读通信地址请求(控制码13H), 仅点对点, 地址域 AA..AAH。"""
    return build_frame(addr_aa, C_READ_ADDR, [])


def build_broadcast_time(addr_aa, date_time):
    """广播校时(控制码08H), 从站不应答。

    date_time: (YY, MM, DD, hh, mm, ss) 每个均为两位十进制数
    """
    data = [bcd_encode(v, 1)[0] for v in date_time]
    return build_frame(addr_aa, C_BROADCAST_TIME, data)


# ---- 应答解析 ----
def is_error_response(control):
    """从站异常应答: 控制码高4位为 0xD。"""
    return (control & 0xF0) == 0xD0


def parse_response(pkt):
    """解析从站应答帧, 返回 dict。

    ok:      True/False
    control: 应答控制码
    data:    减33H后的数据域字节
    err:     错误信息字(仅异常应答)
    """
    frame = parse_frame(pkt)
    result = {"ok": True, "control": frame["control"],
              "data": frame["data"], "err": 0}
    if is_error_response(frame["control"]):
        result["ok"] = False
        result["err"] = frame["data"][0] if frame["data"] else 0
    return result


# ---- 错误信息字(附录B.3.2) ----
ERR_TABLE = {
    0x01: "数据非法",
    0x02: "无请求数据项",
    0x04: "密码错 / 未授权",
    0x08: "通信速率不能更改",
    0x10: "远程控制失败",
    0x20: "升级包错误",
}
