# -*- coding: utf-8 -*-
"""教学解析模块: 把一串 HEX 报文拆成学生看得懂的协议字段。

设计原则:
    1. 只做"展示/解释", 不改动任何字节, 与 dl645_breaker.protocol 的组帧规则一致;
    2. 所有解释都给出依据(字段名 + 偏移 + 取值含义), 便于课堂逐字节讲解;
    3. DI 目录同时服务于主站调试台的下拉框, 避免界面与协议表脱节。
"""

from dl645_breaker import protocol as P
from dl645_breaker.simulator import REASON_TABLE

# ---------------------------------------------------------------------------
# 基础格式化
# ---------------------------------------------------------------------------
def hexs(data):
    """字节序列 -> '68 01 00 ...' 形式的大写 HEX 字符串。"""
    return " ".join("%02X" % (b & 0xFF) for b in data)


def hex4(di):
    """数据标识(规范顺序 DI3 DI2 DI1 DI0) -> '0400 1001' 形式。"""
    return "".join("%02X" % (b & 0xFF) for b in di)


def hex4_wire(di):
    """数据标识 -> 线上传输顺序(DI0 DI1 DI2 DI3)的字符串。"""
    return "".join("%02X" % (b & 0xFF) for b in P.di_wire(di))


# ---------------------------------------------------------------------------
# 控制码
# ---------------------------------------------------------------------------
CONTROL_NAMES = {
    0x08: "广播校时",
    0x11: "读数据",
    0x12: "读后续数据",
    0x13: "读通信地址",
    0x14: "写数据",
    0x15: "写通信地址",
    0x17: "更改通信速率",
    0x18: "修改密码",
    0x19: "查询/应答告警",
    0x1B: "数据清零",
    0x1C: "控制指令",
    0x1D: "远程软件升级",
}


def control_explain(code):
    """解释控制码的位含义(D7 传输方向 / D6 从站应答 / D5 后续 / D4-D0 功能)。

    返回 dict: text(一句话), bits(各位说明), name(功能名), dir(方向)
    """
    code &= 0xFF
    base = code & 0x1F
    is_resp = bool(code & 0x40)
    is_err = (code & 0xF0) == 0xD0
    more = bool(code & 0x20)
    name = CONTROL_NAMES.get(base, "厂商自定义/未知功能")
    if is_err:
        name = "异常应答(%s)" % name
    direction = "从站 → 主站" if (code & 0x80) else "主站 → 从站"
    bits = [
        ("D7", (code >> 7) & 1, "传输方向: 0=主站发出, 1=从站发出"),
        ("D6", (code >> 6) & 1, "从站应答标志: 0=请求, 1=应答"),
        ("D5", (code >> 5) & 1, "后续帧标志: 0=无后续, 1=有后续数据"),
        ("D4-D0", base, "功能码: %s" % CONTROL_NAMES.get(base, "未定义")),
    ]
    if is_err:
        text = ("%s 异常应答(C=%02XH): 数据域首字节为错误信息字"
                % (name, code))
    else:
        text = "%s %s(C=%02XH)" % (direction, name, code)
    return {"text": text, "bits": bits, "name": name,
            "dir": direction, "more": more, "error": is_err}


# ---------------------------------------------------------------------------
# 帧结构分解(逐字段)
# ---------------------------------------------------------------------------
def frame_parts(raw):
    """把整帧拆成 7 个协议域, 返回 (parts, summary)。

    parts 每项: {"name","range","value","note","ok"}
        range 形如 "0-0" 表示字节下标区间(含首尾)
    summary: {"ok","error","cs","cs_calc","length","addr","control"}
    帧长不足时也能尽量分解(教学场景常见: 手工输入的半截帧)。
    """
    raw = list(bytes(raw)) if not isinstance(raw, list) else list(raw)
    parts = []
    summary = {"ok": False, "error": "", "cs": None, "cs_calc": None,
               "length": None, "addr": None, "control": None}

    def add(name, start, end, note, ok=True):
        seg = raw[start:end + 1]
        parts.append({"name": name,
                      "range": "%d-%d" % (start, end),
                      "value": hexs(seg),
                      "note": note, "ok": ok})

    if not raw:
        summary["error"] = "空帧"
        return parts, summary

    # 起始符
    if len(raw) >= 1:
        add("帧起始符", 0, 0, "固定 68H",
            ok=(raw[0] == P.START))
        if raw[0] != P.START:
            summary["error"] = "起始符不是 68H"
    # 地址域
    if len(raw) >= 7:
        add("地址域 A0-A5", 1, 6,
            "低字节在前, 12 位 BCD; 解析地址=%s"
            % P.decode_addr(raw[1:7]))
        summary["addr"] = P.decode_addr(raw[1:7])
        if len(raw) >= 8:
            add("数据区起始符", 7, 7, "固定 68H", ok=(raw[7] == P.START))
            if raw[7] != P.START:
                summary["error"] = "数据区起始符不是 68H"
    # 控制码 / 长度
    if len(raw) >= 9:
        c = raw[8]
        summary["control"] = c
        add("控制码 C", 8, 8, control_explain(c)["text"])
    if len(raw) >= 10:
        summary["length"] = raw[9]
        add("数据域长度 L", 9, 9,
            "L=%d, 即数据域占 %d 字节" % (raw[9], raw[9]))
    # 数据域
    if len(raw) >= 10:
        end = 9 + raw[9]
        if end <= len(raw):
            add("数据域 DATA", 10, end,
                "每字节已加 33H; 解析时需减 33H")
        else:
            add("数据域 DATA", 10, len(raw) - 1,
                "帧不完整: 应到第 %d 字节, 实际只到 %d" % (end, len(raw) - 1),
                ok=False)
            summary["error"] = "数据域长度不足"
    # 校验 / 结束符
    if len(raw) >= 2:
        cs_idx = len(raw) - 2
        calc = P.checksum(raw[:cs_idx])
        summary["cs"] = raw[cs_idx]
        summary["cs_calc"] = calc
        ok = (raw[cs_idx] == calc)
        add("校验码 CS", cs_idx, cs_idx,
            "应为 %02XH (从第一个 68H 起逐字节求和 mod 256)%s"
            % (calc, "" if ok else ", 与实际不符"), ok=ok)
        add("结束符", len(raw) - 1, len(raw) - 1, "固定 16H",
            ok=(raw[-1] == P.END))
        if not ok:
            summary["error"] = summary["error"] or "校验码错误"
        if raw[-1] != P.END:
            summary["error"] = summary["error"] or "结束符不是 16H"

    summary["ok"] = (not summary["error"])
    return parts, summary


def checksum_trace(raw):
    """校验和计算过程: 返回 [(下标, 字节值, 累计和)] 与最终结果。"""
    raw = list(bytes(raw)) if not isinstance(raw, list) else list(raw)
    if len(raw) < 2:
        return [], 0
    body = raw[:-2]
    out, total = [], 0
    for i, b in enumerate(body):
        total = (total + b) & 0xFF
        out.append((i, b, total))
    return out, total


# ---------------------------------------------------------------------------
# 数据标识(DI)目录 —— 与 dl645_breaker.simulator 实际支持的读/写项一一对应
# ---------------------------------------------------------------------------
def _di(name, di, nbytes, kind="read", note="", unit=""):
    return {"name": name, "di": tuple(di), "len": nbytes,
            "kind": kind, "note": note, "unit": unit}


DI_CATALOG = [
    # ---- 实时测量(表 A.2.1) ----
    _di("三相电压", (0x02, 0x01, 0xFF, 0x00), 6, note="A/B/C 各 2 字节 BCD, 低字节在前, 0.1V"),
    _di("三相电流", (0x02, 0x02, 0xFF, 0x00), 9, note="A/B/C 各 3 字节 BCD, 0.01A"),
    _di("剩余电流(最大相+值)", (0x02, 0x90, 0xFF, 0x00), 3, note="首字节相别, 后 2 字节 mA"),
    _di("剩余电流最大相", (0x02, 0x90, 0x00, 0x00), 1, note="00 无 / 01 A / 02 B / 03 C"),
    _di("剩余电流值", (0x02, 0x90, 0x01, 0x00), 2, note="BCD, 单位 mA"),
    _di("额定剩余电流动作值+极限不驱动时间", (0x02, 0x91, 0xFF, 0x00), 4,
        note="2+2 字节, mA / ms"),
    _di("额定剩余电流动作值", (0x02, 0x91, 0x01, 0x00), 2, note="mA"),
    _di("额定极限不驱动时间", (0x02, 0x91, 0x02, 0x00), 2, note="ms"),
    _di("电流方向", (0x02, 0x92, 0x01, 0x00), 1),
    # ---- 累计记录(表 A.2.2) ----
    _di("数据清零总次数", (0x03, 0x30, 0x01, 0x00), 3),
    _di("跳闸次数(全部 12 项)", (0x03, 0x81, 0x00, 0xFF), 24,
        note="每项 2 字节, 顺序见协议表 A.2.2"),
    _di("运行时间总累计(分)", (0x03, 0x81, 0x01, 0x01), 4),
    _di("新增次数-保护动作", (0x03, 0x81, 0x02, 0x01), 1),
    _di("新增次数-异常告警", (0x03, 0x81, 0x02, 0x02), 1),
    _di("新增次数-闸位变化", (0x03, 0x81, 0x02, 0x03), 1),
    _di("新增次数-自检", (0x03, 0x81, 0x02, 0x05), 1),
    # ---- 事件记录(表 A.2.4) ----
    _di("保护动作记录(第1条)", (0x03, 0x8E, 0x00, 0x01), 26,
        note="末字节为序号 01-10; 保护动作事件 26 字节"),
    _di("自检事件记录(第1条)", (0x03, 0x8D, 0x00, 0x01), 27),
    _di("闸位变化记录(第1条)", (0x03, 0x91, 0x00, 0x01), 27),
    _di("异常告警记录(第1条)", (0x03, 0x92, 0x00, 0x01), 27),
    _di("高压失/复电记录(第1条)", (0x03, 0x93, 0x00, 0x01), 12),
    _di("保护功能投退记录(第1条)", (0x03, 0x8F, 0x00, 0x01), 8),
    # ---- 参数变量(表 A.2.5) ----
    _di("当前日期 YYMMDDWW", (0x04, 0x00, 0x01, 0x01), 4),
    _di("当前时间 hhmmss", (0x04, 0x00, 0x01, 0x02), 3),
    _di("通信地址 A0-A5", (0x04, 0x00, 0x04, 0x01), 6, "write"),
    _di("设备号(12 位 BCD)", (0x04, 0x00, 0x04, 0x02), 6),
    _di("额定电压(ASCII)", (0x04, 0x00, 0x04, 0x04), 6),
    _di("额定电流(ASCII)", (0x04, 0x00, 0x04, 0x05), 6),
    _di("最大(壳架)电流(ASCII)", (0x04, 0x00, 0x04, 0x06), 6),
    _di("设备型号(ASCII)", (0x04, 0x00, 0x04, 0x0B), 10),
    _di("生产日期(ASCII)", (0x04, 0x00, 0x04, 0x0C), 10),
    _di("协议版本号(ASCII)", (0x04, 0x00, 0x04, 0x0D), 16),
    _di("固件版本号(ASCII)", (0x04, 0x00, 0x04, 0x0F), 32),
    _di("硬件版本号(ASCII)", (0x04, 0x00, 0x04, 0x10), 32),
    _di("额定剩余电流动作值参数组", (0x04, 0x00, 0x04, 0x11), 20,
        note="6 档 × 2 字节, 不足用 FFH 填充"),
    _di("额定极限不驱动时间参数组", (0x04, 0x00, 0x04, 0x12), 12),
    _di("自动重合闸时间范围(ASCII)", (0x04, 0x00, 0x04, 0x14), 24),
    _di("剩余电流动作特性(ASCII)", (0x04, 0x00, 0x04, 0x15), 24),
    _di("运行状态字 1", (0x04, 0x00, 0x05, 0x01), 1,
        note="bit6-5 闸位, bit4-0 跳闸/告警原因"),
    _di("运行状态字 2", (0x04, 0x00, 0x05, 0x02), 1,
        note="各事件新增标识位"),
    _di("运行状态字 1+2", (0x04, 0x00, 0x05, 0xFF), 2),
    _di("通信波特率特征字", (0x04, 0x00, 0x07, 0x03), 1),
    _di("0 级密码", (0x04, 0x00, 0x0C, 0x01), 4),
    _di("1 级密码", (0x04, 0x00, 0x0C, 0x02), 4),
    _di("2 级密码", (0x04, 0x00, 0x0C, 0x03), 4),
    _di("控制字 1", (0x04, 0x00, 0x10, 0x01), 1, "write", "告警总开关/灯光/声音/重合闸"),
    _di("控制字 2", (0x04, 0x00, 0x10, 0x02), 1, "write", "欠压/过压/缺相/缺零 动作方式"),
    _di("控制字 3", (0x04, 0x00, 0x10, 0x03), 1, "write", "试跳源/预警/短路瞬时/短延时/过流"),
    _di("控制字 4", (0x04, 0x00, 0x10, 0x04), 1, "write", "剩余电流动作值档位/不驱动时间档位"),
    _di("控制字 5", (0x04, 0x00, 0x10, 0x05), 1, "write", "保留 + 特征字"),
    _di("控制字 1-5", (0x04, 0x00, 0x10, 0xFF), 5, "write"),
    _di("剩余电流预警值(%)", (0x04, 0x00, 0x11, 0x04), 1, "write", "0-100"),
    _di("预警持续时间(s)", (0x04, 0x00, 0x11, 0x05), 1, "write"),
    _di("定时自检 DDhhmm", (0x04, 0x00, 0x12, 0x01), 3, "write"),
    _di("过压整定值", (0x04, 0x00, 0x13, 0x01), 2, "write", "BCD 0.1V"),
    _di("欠压整定值", (0x04, 0x00, 0x13, 0x02), 2, "write", "BCD 0.1V"),
    _di("缺相电压阈值", (0x04, 0x00, 0x13, 0x03), 2, "write", "BCD 0.1V"),
    _di("Ir1 过载动作电流", (0x04, 0x00, 0x14, 0x01), 3, "write", "BCD 0.1A"),
    _di("t1 长延时时间", (0x04, 0x00, 0x14, 0x03), 1, "write", "s"),
    _di("Ir2 短延时倍数", (0x04, 0x00, 0x14, 0x04), 1, "write", "×Ir1"),
    _di("t2 短延时时间", (0x04, 0x00, 0x14, 0x05), 2, "write", "ms"),
    _di("Ir3 瞬时倍数", (0x04, 0x00, 0x14, 0x06), 1, "write", "×Ir1"),
    # ---- 控制指令(1CH) ----
    _di("远程跳闸(预约)", (0x06, 0x01, 0x01, 0x01), 0, "control", "数据域可附 NN+单位 预约"),
    _di("取消跳闸预约", (0x06, 0x01, 0x01, 0x02), 0, "control"),
    _di("远程合闸(预约)", (0x06, 0x01, 0x02, 0x01), 0, "control"),
    _di("取消合闸预约", (0x06, 0x01, 0x02, 0x02), 0, "control"),
    _di("模拟试跳", (0x06, 0x01, 0x03, 0x01), 0, "control", "跳闸并记录\"远程试验\""),
    _di("取消试跳预约", (0x06, 0x01, 0x03, 0x02), 0, "control"),
    _di("自检", (0x06, 0x01, 0x04, 0x01), 0, "control"),
    _di("取消自检预约", (0x06, 0x01, 0x04, 0x02), 0, "control"),
]

DI_INDEX = {hex4(d["di"]): d for d in DI_CATALOG}


def di_choices(kind=None):
    """下拉框选项: ['三相电压  0201FF00', ...]; kind=None 返回全部。"""
    out = []
    for d in DI_CATALOG:
        if kind and d["kind"] != kind:
            continue
        out.append("%s  %s" % (d["name"], hex4(d["di"])))
    return out


def di_from_choice(text):
    """把下拉框文本还原为 DI 元组(取末尾 8 位 HEX)。"""
    key = str(text).strip().split()[-1].upper()
    item = DI_INDEX.get(key)
    return item["di"] if item else None


def di_info(di):
    return DI_INDEX.get(hex4(di))


# ---------------------------------------------------------------------------
# 值解码(教学展示用; 与上位机的显示口径保持一致)
# ---------------------------------------------------------------------------
def bcd_num(data):
    """数值量: 低字节在前 -> 整数。"""
    return P.bcd_decode_lf(data)


def bcd_int(data):
    """分量型: 书写顺序 -> 整数。"""
    return P.bcd_decode(data)


def ascii_str(data):
    return bytes(bytearray(b & 0xFF for b in data)).decode(
        "utf-8", "replace").strip()


def decode_status1(b):
    """运行状态字 1: bit6-5 闸位, bit4-0 原因。"""
    pos = {0b00: "分闸", 0b01: "合闸", 0b10: "保留(10)",
           0b11: "保留(11)"}.get((b >> 5) & 0x03, "?")
    reason = b & 0x1F
    return "闸位=%s, 原因=%s(%02XH)" % (
        pos, REASON_TABLE.get(reason, "未定义"), reason)


def decode_status2(b):
    """运行状态字 2 位表(按规约表 B.1.2)。"""
    names = [
        (0x80, "自检事件标识"),
        (0x40, "异常告警发生/恢复"),
        (0x20, "闸位状态变化"),
        (0x10, "保护功能退出/恢复"),
        (0x08, "高压线路失/复电"),
        (0x04, "保护动作标识"),
    ]
    on = [n for bit, n in names if b & bit]
    return ("置位: " + "、".join(on)) if on else "无新增标识"


def _bits(b, table):
    """按 [(起始位, 位宽, 名称, {值: 含义})] 解释字节。"""
    out = []
    for shift, width, name, mapping in table:
        v = (b >> shift) & ((1 << width) - 1)
        out.append("%s=%s" % (name, mapping.get(v, "%d" % v)))
    return ", ".join(out)


CTRL1_TABLE = [
    (6, 1, "数据告警总开关", {0: "全禁止", 1: "全允许"}),
    (5, 1, "报警灯光", {0: "禁止", 1: "允许"}),
    (4, 1, "报警声音", {0: "禁止", 1: "允许"}),
    (3, 1, "定时自检", {0: "禁止", 1: "允许"}),
    (2, 1, "档位返回", {0: "允许", 1: "禁止"}),
    (1, 1, "重合闸", {0: "允许", 1: "禁止"}),
]

CTRL2_TABLE = [
    (6, 2, "欠压保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
    (4, 2, "过压保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
    (2, 2, "缺相保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
    (0, 2, "缺零保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
]

CTRL3_TABLE = [
    (7, 1, "试跳源", {0: "内部", 1: "外部"}),
    (6, 1, "剩余电流保护预警", {0: "退出", 1: "投入"}),
    (4, 2, "短路瞬时保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
    (2, 2, "短路短延时保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
    (0, 2, "过流保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
]

CTRL4_TABLE = [
    (4, 4, "剩余电流动作值档位", {0xF: "连续可调"}),
    (2, 2, "极限不驱动时间档位", {3: "连续可调"}),
    (0, 2, "剩余电流保护", {0: "不跳不告警", 1: "不跳闸告警", 2: "跳闸不告警"}),
]


def decode_ctrl(index, b):
    """解释控制字 1-5。index: 1..5"""
    if index == 1:
        return _bits(b, CTRL1_TABLE)
    if index == 2:
        return _bits(b, CTRL2_TABLE)
    if index == 3:
        return _bits(b, CTRL3_TABLE)
    if index == 4:
        gear = (b >> 4) & 0x0F
        tgear = (b >> 2) & 0x03
        return ("剩余电流动作值档位=%s, 极限不驱动时间档位=%s, %s"
                % ("连续可调" if gear == 0xF else "%d 档" % (gear + 1),
                   "连续可调" if tgear == 3 else "%d 档" % (tgear + 1),
                   _bits(b & 0x03, [(0, 2, "剩余电流保护",
                                     {0: "不跳不告警", 1: "不跳闸告警",
                                      2: "跳闸不告警"})])))
    return "bit7-4=保留(%XH), 特征字=%XH" % ((b >> 4) & 0x0F, b & 0x0F)


def decode_value(di, payload):
    """按数据标识解释数据域(不含 DI 的负载), 返回中文描述。"""
    di = tuple(di)
    d = list(payload or [])
    try:
        if di == (0x02, 0x01, 0xFF, 0x00) and len(d) >= 6:
            return "A相 %.1f V, B相 %.1f V, C相 %.1f V" % tuple(
                bcd_num(d[i * 2:i * 2 + 2]) / 10.0 for i in range(3))
        if di == (0x02, 0x02, 0xFF, 0x00) and len(d) >= 9:
            return "A相 %.2f A, B相 %.2f A, C相 %.2f A" % tuple(
                bcd_num(d[i * 3:i * 3 + 3]) / 100.0 for i in range(3))
        if di == (0x02, 0x90, 0xFF, 0x00) and len(d) >= 3:
            ph = {0: "无", 1: "A", 2: "B", 3: "C"}.get(d[0], "?")
            return "最大相 %s, 剩余电流 %d mA" % (ph, bcd_num(d[1:3]))
        if di == (0x02, 0x90, 0x00, 0x00) and d:
            return "最大相 " + {0: "无", 1: "A", 2: "B", 3: "C"}.get(d[0], "?")
        if di == (0x02, 0x90, 0x01, 0x00) and len(d) >= 2:
            return "剩余电流 %d mA" % bcd_num(d[:2])
        if di == (0x02, 0x91, 0xFF, 0x00) and len(d) >= 4:
            return "额定剩余电流 %d mA, 极限不驱动时间 %d ms" % (
                bcd_num(d[0:2]), bcd_num(d[2:4]))
        if di == (0x02, 0x91, 0x01, 0x00) and len(d) >= 2:
            return "额定剩余电流 %d mA" % bcd_num(d[:2])
        if di == (0x02, 0x91, 0x02, 0x00) and len(d) >= 2:
            return "极限不驱动时间 %d ms" % bcd_num(d[:2])
        if di == (0x02, 0x92, 0x01, 0x00) and d:
            return {0: "无方向/未定义", 1: "正向", 2: "反向"}.get(
                d[0], "方向字节 %02XH" % d[0])
        if di == (0x03, 0x30, 0x01, 0x00) and len(d) >= 3:
            return "数据清零总次数 %d 次" % bcd_num(d[:3])
        if di == (0x03, 0x81, 0x00, 0xFF) and len(d) >= 24:
            names = ["总跳闸", "剩余电流闭锁", "剩余电流保护", "过载保护",
                     "过压保护", "手动跳闸", "缺零保护", "试验跳闸",
                     "短路短延时", "短路瞬时", "欠压保护", "缺相保护"]
            return ", ".join("%s %d" % (names[i], bcd_num(d[i * 2:i * 2 + 2]))
                             for i in range(12))
        if di == (0x03, 0x81, 0x01, 0x01) and len(d) >= 4:
            return "运行时间累计 %d 分钟" % bcd_num(d[:4])
        if di[0:3] == (0x03, 0x81, 0x02) and d:
            return "新增次数 %d" % bcd_num(d[:1])
        if di == (0x04, 0x00, 0x01, 0x01) and len(d) >= 4:
            s = "".join("%02X" % b for b in d[:4])
            return "20%s-%s-%s 星期%s" % (s[0:2], s[2:4], s[4:6], s[6:8])
        if di == (0x04, 0x00, 0x01, 0x02) and len(d) >= 3:
            s = "".join("%02X" % b for b in d[:3])
            return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])
        if di == (0x04, 0x00, 0x04, 0x01) and len(d) >= 6:
            return "通信地址 " + P.decode_addr(d[:6])
        if di == (0x04, 0x00, 0x04, 0x02) and len(d) >= 6:
            return "设备号 %012d" % bcd_num(d[:6])
        if di in ((0x04, 0x00, 0x04, 0x04), (0x04, 0x00, 0x04, 0x05),
                  (0x04, 0x00, 0x04, 0x06), (0x04, 0x00, 0x04, 0x0B),
                  (0x04, 0x00, 0x04, 0x0C), (0x04, 0x00, 0x04, 0x0D),
                  (0x04, 0x00, 0x04, 0x0F), (0x04, 0x00, 0x04, 0x10),
                  (0x04, 0x00, 0x04, 0x14), (0x04, 0x00, 0x04, 0x15)):
            return ascii_str(d)
        if di == (0x04, 0x00, 0x04, 0x11) and len(d) >= 12:
            gears = [bcd_num(d[i * 2:i * 2 + 2]) for i in range(6)]
            return "额定剩余电流档位组: " + ", ".join("%d mA" % g for g in gears)
        if di == (0x04, 0x00, 0x04, 0x12) and len(d) >= 6:
            gears = [bcd_num(d[i * 2:i * 2 + 2]) for i in range(3)]
            return "极限不驱动时间档位组: " + ", ".join("%d ms" % g for g in gears)
        if di == (0x04, 0x00, 0x05, 0x01) and d:
            return "状态字1=%02XH: %s" % (d[0], decode_status1(d[0]))
        if di == (0x04, 0x00, 0x05, 0x02) and d:
            return "状态字2=%02XH: %s" % (d[0], decode_status2(d[0]))
        if di == (0x04, 0x00, 0x05, 0xFF) and len(d) >= 2:
            return "状态字1=%02XH[%s]; 状态字2=%02XH[%s]" % (
                d[0], decode_status1(d[0]), d[1], decode_status2(d[1]))
        if di == (0x04, 0x00, 0x07, 0x03) and d:
            rate = {0x10: "1200", 0x20: "2400", 0x40: "4800", 0x60: "9600",
                    0x80: "19200"}.get(d[0], "特征字%02XH" % d[0])
            return "波特率特征字 %02XH (%s bps)" % (d[0], rate)
        if di[0:3] == (0x04, 0x00, 0x0C) and len(d) >= 4:
            return "%d 级密码: %s" % (di[3] - 1, hexs(d[:4]))
        if di[0:3] == (0x04, 0x00, 0x10) and di[3] == 0xFF and len(d) >= 5:
            return "; ".join("控制字%d=%02XH[%s]" % (i + 1, d[i],
                                                     decode_ctrl(i + 1, d[i]))
                             for i in range(5))
        if di[0:3] == (0x04, 0x00, 0x10) and 1 <= di[3] <= 5 and d:
            return "控制字%d=%02XH: %s" % (di[3], d[0], decode_ctrl(di[3], d[0]))
        if di == (0x04, 0x00, 0x11, 0x04) and d:
            return "剩余电流预警值 %d %%" % bcd_num(d[:1])
        if di == (0x04, 0x00, 0x11, 0x05) and d:
            return "预警持续时间 %d s" % bcd_num(d[:1])
        if di == (0x04, 0x00, 0x12, 0x01) and len(d) >= 3:
            s = "".join("%02X" % b for b in d[:3])
            return "定时自检 %s日 %s:%s" % (s[0:2], s[2:4], s[4:6])
        if di[0:3] == (0x04, 0x00, 0x13) and len(d) >= 2:
            return "整定值 %.1f V" % (bcd_num(d[:2]) / 10.0)
        if di == (0x04, 0x00, 0x14, 0x01) and len(d) >= 3:
            return "Ir1 = %.1f A" % (bcd_num(d[:3]) / 10.0)
        if di in ((0x04, 0x00, 0x14, 0x03), (0x04, 0x00, 0x14, 0x04),
                  (0x04, 0x00, 0x14, 0x06)) and d:
            return "值 = %d" % bcd_num(d[:1])
        if di == (0x04, 0x00, 0x14, 0x05) and len(d) >= 2:
            return "t2 = %d ms" % bcd_num(d[:2])
        if di[0:3] in ((0x03, 0x8E, 0x00), (0x03, 0x8D, 0x00),
                       (0x03, 0x91, 0x00), (0x03, 0x92, 0x00),
                       (0x03, 0x93, 0x00), (0x03, 0x8F, 0x00)):
            return decode_event(di, d) or "事件记录原始数据: " + hexs(d)
    except Exception as exc:                    # 数据不足时不要炸界面
        return "解码失败: %s (原始 %s)" % (exc, hexs(d))
    return "原始数据(%d 字节): %s" % (len(d), hexs(d))


def _bcd_time(data):
    s = "".join("%02X" % b for b in data)
    return "20%s-%s-%s %s:%s:%s" % (s[0:2], s[2:4], s[4:6],
                                    s[6:8], s[8:10], s[10:12])


def decode_event(di, data):
    """事件记录解码(与上位机 _decode_event 口径一致)。"""
    d0 = tuple(di[0:3])
    d = list(data or [])
    if d0 == (0x03, 0x8E, 0x00) and len(d) >= 26:
        reason = REASON_TABLE.get(d[0], "原因%02X" % d[0])
        ph = {0: "无", 1: "A", 2: "B", 3: "C"}.get(d[1], "?")
        v3 = " ".join("%.1f" % (bcd_num(d[10 + i * 2:12 + i * 2]) / 10.0)
                      for i in range(3))
        i3 = " ".join("%.2f" % (bcd_num(d[16 + i * 3:19 + i * 3]) / 100.0)
                      for i in range(3))
        dr = {0: "--", 1: "自动", 2: "手动"}.get(d[25], "?")
        return ("保护动作[%s] %s相 时刻%s | 剩余电流 %d mA | 电压[%s]V "
                "电流[%s]A | 方式:%s"
                % (reason, ph, _bcd_time(d[2:8]), bcd_num(d[8:10]),
                   v3, i3, dr))
    if d0 == (0x03, 0x8D, 0x00) and len(d) >= 8:
        result = {0x11: "正常", 0x22: "故障"}.get(d[0], "%02X" % d[0])
        way = {1: "自动", 2: "手动"}.get(d[7], "%02X" % d[7])
        return "自检事件[%s] 时刻%s 方式:%s" % (result, _bcd_time(d[1:7]), way)
    if d0 == (0x03, 0x91, 0x00) and len(d) >= 9:
        st = {0: "分闸", 1: "合闸"}.get(d[0], "?")
        return "闸位变化→%s 时刻%s" % (st, _bcd_time(d[3:9]))
    if d0 == (0x03, 0x92, 0x00) and len(d) >= 9:
        flag = "动作" if d[0] == 0x01 else "恢复"
        return "告警[%s] %s 时刻%s" % (
            flag, REASON_TABLE.get(d[1], "原因%02X" % d[1]), _bcd_time(d[3:9]))
    if d0 == (0x03, 0x93, 0x00) and len(d) >= 12:
        return "失电 %s → 复电 %s" % (_bcd_time(d[0:6]), _bcd_time(d[6:12]))
    if d0 == (0x03, 0x8F, 0x00) and len(d) >= 7:
        act = {0: "退出", 1: "投入"}.get(d[6], "?")
        return "保护功能%s 时刻%s" % (act, _bcd_time(d[0:6]))
    return None


# ---------------------------------------------------------------------------
# 应答整体解析(主站调试台"解析"框使用)
# ---------------------------------------------------------------------------
def describe_response(raw):
    """把应答帧解释成多行文本: 字段分解 + 控制码 + 数据标识 + 数据含义。"""
    raw = list(bytes(raw)) if not isinstance(raw, list) else list(raw)
    if not raw:
        return "无应答(从站未响应 / 超时)"
    parts, summary = frame_parts(raw)
    lines = [control_explain(raw[8])["text"] if len(raw) > 8 else "帧不完整"]
    if len(raw) >= 10:
        ctrl = raw[8]
        L = raw[9]
        data = [(b - 0x33) & 0xFF for b in raw[10:10 + L]]
        if (ctrl & 0xF0) == 0xD0:
            err = data[0] if data else 0
            names = {0x01: "数据非法", 0x02: "无请求数据项", 0x04: "密码错/未授权",
                     0x08: "通信速率不能更改", 0x10: "远程控制失败",
                     0x20: "升级包错误"}
            lines.append("异常应答: %s(%02XH)" % (names.get(err, "未知"), err))
        elif data:
            di = P.di_canon(data[0:4])
            lines.append("数据标识 DI=%s (线上顺序 %s)"
                         % (hex4(di), hexs(data[0:4])))
            item = di_info(di)
            if item:
                lines.append("访问对象: %s" % item["name"])
            lines.append("数据域负载(%d 字节): %s"
                         % (len(data) - 4, hexs(data[4:])))
            lines.append("解析: %s" % decode_value(di, data[4:]))
    if summary["error"]:
        lines.append("帧校验: %s (CS=%02XH, 应为 %02XH)"
                     % (summary["error"], summary["cs"] or 0,
                        summary["cs_calc"] or 0))
    else:
        lines.append("帧校验: 通过 (CS=%02XH)" % (summary["cs"] or 0))
    return "\n".join(lines)


def frame_to_source(raw, addr=None, control=None):
    """生成"指令源码"形式的注释文本, 便于课堂上对照组帧过程。"""
    raw = list(bytes(raw)) if not isinstance(raw, list) else list(raw)
    if not raw:
        return ""
    parts, summary = frame_parts(raw)
    lines = []
    for p in parts:
        lines.append("%-14s [%s]\t%s" % (p["name"], p["range"], p["value"]))
    if summary["length"] is not None:
        lines.append("L=%d, 整帧长度=%d 字节" % (summary["length"], len(raw)))
    if summary["cs"] is not None:
        lines.append("CS=%02XH, 计算值=%02XH, %s"
                     % (summary["cs"], summary["cs_calc"] or 0,
                        "正确" if summary["cs"] == summary["cs_calc"]
                        else "错误"))
    return "\n".join(lines)




