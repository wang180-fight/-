# -*- coding: utf-8 -*-
"""虚拟断路器仿真器 —— 仅供协议回归测试使用。

在内存中维护一台"具有自动重合闸功能的剩余电流保护断路器"的完整状态,
并严格按照 DL/T 645 扩展规约对请求帧进行应答。
GUI 已改为直接连接真实设备(串口), 不再提供仿真模式; 本模块仅用于
_roundtrip_test.py 对协议栈的自动化回归测试。
"""

import time
from datetime import datetime, timedelta

from . import protocol as P


# ---- 故障原因 / 状态字解码表(附录B) ----
REASON_TABLE = {
    0x00: "正常运行",
    0x01: "剩余电流预警",
    0x02: "剩余电流",
    0x04: "缺零",
    0x05: "过载",
    0x06: "短路短延时",
    0x07: "缺相",
    0x08: "欠压",
    0x09: "过压",
    0x0A: "接地",
    0x0B: "停电",
    0x0D: "远程试验",
    0x0E: "按键试验",
    0x0F: "闭锁",
    0x10: "互感器故障",
    0x11: "合闸失败",
    0x13: "设置更改",
    0x16: "短路瞬时",
    0x17: "跳闸失败",
    0x1D: "硬遥控",
    0x1E: "软遥控",
}


def _bcd2(v, nbytes=2):
    """多字节BCD数值量编码(DL645: 低字节在前)。"""
    return P.bcd_encode_lf(int(v), nbytes)


def _ascii(s, nbytes):
    b = s.encode("utf-8")[:nbytes]
    return list(b) + [0x20] * (nbytes - len(b))


class VirtualBreaker:
    """虚拟断路器。 handle_frame(frame)->应答帧字节串(广播/无需应答返回b'')。"""

    DEFAULT_ADDR = "000000000001"

    def __init__(self, addr_str=DEFAULT_ADDR):
        self.addr = P.encode_addr(addr_str)
        # ---- 运行状态 ----
        self.switch_state = 1                    # 1=合闸, 0=分闸
        self.fault_reason = 0x00                 # 跳闸/告警原因
        self.va, self.vb, self.vc = 220.0, 219.8, 220.1   # V
        self.ia, self.ib, self.ic = 12.5, 11.8, 12.0      # A
        self.residual = 25.0                     # mA
        self.residual_phase = 0x01               # A相
        self.dir = 0x00                          # 电流方向字节
        # ---- 额定值档位参数组 ----
        self.res_gears = [30, 50, 100, 300, 500, 1000]     # mA 1-6档
        self.time_gears = [100, 200, 500]                  # ms  1-3档
        # ---- 控制字(见附录B.2) ----
        self.ctrl1 = 0b01100100
        self.ctrl2 = 0b10101010
        self.ctrl3 = 0b01000000
        self.ctrl4 = 0b00110000
        self.ctrl5 = 0x00
        # ---- 整定参数 ----
        self.over_v = 264.0
        self.under_v = 176.0
        self.nophase_v = 60.0
        self.res_warn = 60                       # %
        self.res_warn_t = 60                     # s
        self.ir1 = 100.0                         # A
        self.t1 = 120                            # s
        self.ir2 = 8                             # *Ir1
        self.t2 = 400                            # ms
        self.ir3 = 12                            # *Ir1
        self.st_hhmm = (9, 0)                    # 定时自检 (hh, mm)
        # ---- 密码(4字节, 第1字节为级别) ----
        self.passwords = {
            0: [0x00, 0x00, 0x00, 0x00],
            1: [0x01, 0x00, 0x00, 0x00],
            2: [0x02, 0x00, 0x00, 0x00],
        }
        # ---- 累计记录 ----
        self.clear_count = 0
        self.runtime_min = 12345
        self.trip_counts = {k: 0 for k in range(0x00, 0x0C)}
        self.trip_counts[0x00] = 5
        self.trip_counts[0x02] = 3
        self.trip_counts[0x07] = 2
        self.new_counts = {k: 0 for k in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06)}
        # ---- 事件记录 ----
        self.ev_prot_act = []
        self.ev_self_test = []
        self.ev_switch = []
        self.ev_alarm = []
        self.ev_power = []
        self.ev_prot_sw = []
        self._now = datetime.now()
        self._seed_events()
        # ---- 预约控制 ----
        self._pending = []

    # ------------------------------------------------------------------
    def _seed_events(self):
        now = self._now
        self.ev_prot_act.append(self._pack_prot_act(
            0x02, 0x01, now - timedelta(hours=2), 150.0,
            (220.0, 219.0, 218.0), (12.5, 11.0, 10.0), 0x00))
        self.ev_prot_act.append(self._pack_prot_act(
            0x05, 0x02, now - timedelta(hours=26), 20.0,
            (221.0, 220.0, 219.0), (15.0, 14.0, 13.0), 0x00))
        self.ev_self_test.append(self._pack_self_test(
            0x11, 0x01, now - timedelta(days=1)))
        self.ev_switch.append(self._pack_switch(
            0x01, 0x1E, 0x01, now - timedelta(hours=2),
            150.0, (220.0, 219.0, 218.0), (12.5, 11.0, 10.0)))
        self.ev_alarm.append(self._pack_alarm(
            0x01, 0x01, 0x01, now - timedelta(hours=1),
            80.0, (220.0, 219.0, 218.0), (12.5, 11.0, 10.0)))

    # ---- 事件记录打包 ----
    @staticmethod
    def _dt(dt):
        """datetime -> YYMMDDhhmmss 6字节BCD。"""
        return P.bcd_encode(int(dt.strftime("%y%m%d%H%M%S")), 6)

    @staticmethod
    def _volt3(v3):
        out = []
        for v in v3:
            out += _bcd2(round(v * 10), 2)
        return out

    @staticmethod
    def _curr3(i3):
        out = []
        for c in i3:
            out += _bcd2(round(c * 10), 3)
        return out

    def _pack_prot_act(self, reason, phase, dt, resid,
                       v3, i3, direc):
        """保护动作事件 26字节 (A.3.5)。"""
        return ([reason, phase] + self._dt(dt) + _bcd2(resid, 2)
                + self._volt3(v3) + self._curr3(i3) + [direc])

    def _pack_self_test(self, result, way, dt):
        """自检事件 27字节 (A.3.4)。"""
        return ([result] + self._dt(dt) + [way]
                + [self.residual_phase] + _bcd2(self.residual, 2)
                + self._volt3((self.va, self.vb, self.vc))
                + self._curr3((self.ia, self.ib, self.ic)) + [self.dir])

    def _pack_switch(self, state, reason, phase, dt, resid, v3, i3):
        """闸位变化事件 27字节 (A.3.8)。"""
        return ([state, reason, phase] + self._dt(dt) + _bcd2(resid, 2)
                + self._volt3(v3) + self._curr3(i3) + [self.dir])

    def _pack_alarm(self, flag, reason, phase, dt, resid, v3, i3):
        """告警事件 27字节 (A.3.9)。"""
        return ([flag, reason, phase] + self._dt(dt) + _bcd2(resid, 2)
                + self._volt3(v3) + self._curr3(i3) + [self.dir])

    def _pack_power(self, start_dt, end_dt):
        """高压失/复电事件 12字节 (A.3.6)。"""
        return self._dt(start_dt) + self._dt(end_dt)

    def _pack_prot_sw(self, dt, action, way):
        """保护功能投退事件 8字节 (A.3.10)。"""
        return self._dt(dt) + [action, way, 0x00, 0x00, 0x00, 0x00]

    # ------------------------------------------------------------------
    def handle_frame(self, frame):
        self._tick()
        try:
            f = P.parse_frame(frame)
        except ValueError:
            return b""
        if not self._addr_match(f["addr"]):
            return b""
        return self._dispatch(f)

    def _addr_match(self, addr):
        if addr == self.addr:
            return True
        if all(b == 0xAA for b in addr):
            return True
        if all(b == 0x99 for b in addr):
            return True
        return False

    def _dispatch(self, f):
        ctrl = f["control"]
        data = f["data"]
        if ctrl == P.C_READ:
            return self._resp_read(data)
        if ctrl == P.C_READ_ADDR:
            return P.build_frame(self.addr, 0x93, self.addr)
        if ctrl == P.C_READ_MORE:
            return P.build_frame(self.addr, 0xD2, [0x04])
        if ctrl == P.C_WRITE:
            return self._resp_write(data)
        if ctrl == P.C_CONTROL:
            return self._resp_control(data)
        if ctrl == P.C_CLEAR_DATA:
            return self._resp_clear(data)
        if ctrl == P.C_CHANGE_PWD:
            return self._resp_change_pwd(data)
        if ctrl == P.C_BROADCAST_TIME:
            return b""
        if ctrl == P.C_CHANGE_BAUD:
            return P.build_frame(self.addr, 0x97, data[:1])
        if ctrl == P.C_UPGRADE:
            return P.build_frame(self.addr, 0xDD, [0x20])
        return b""

    # ------------------------------------------------------------------
    # 读数据应答
    # ------------------------------------------------------------------
    def _read_payload(self, di):
        """返回应答数据(不含标识码), 不支持返回 None。"""
        # ---- 当前变量(表A.2.1) ----
        if di == (0x02, 0x01, 0xFF, 0x00):      # 三相电压
            return self._volt3((self.va, self.vb, self.vc))
        if di == (0x02, 0x02, 0xFF, 0x00):      # 三相电流
            return self._curr3((self.ia, self.ib, self.ic))
        if di == (0x02, 0x90, 0xFF, 0x00):      # 剩余电流最大相+值
            return [self.residual_phase] + _bcd2(self.residual, 2)
        if di == (0x02, 0x90, 0x00, 0x00):
            return [self.residual_phase]
        if di == (0x02, 0x90, 0x01, 0x00):
            return _bcd2(self.residual, 2)
        if di == (0x02, 0x91, 0xFF, 0x00):      # 额定剩余电流动作值+极限不驱动时间
            return _bcd2(self._rated_res(), 2) + _bcd2(self._rated_t(), 2)
        if di == (0x02, 0x91, 0x01, 0x00):
            return _bcd2(self._rated_res(), 2)
        if di == (0x02, 0x91, 0x02, 0x00):
            return _bcd2(self._rated_t(), 2)
        if di == (0x02, 0x92, 0x01, 0x00):      # 电流方向
            return [self.dir]
        # ---- 累计记录(表A.2.2) ----
        if di == (0x03, 0x30, 0x01, 0x00):      # 数据清零总次数
            return _bcd2(self.clear_count, 3)
        if di == (0x03, 0x81, 0x00, 0xFF):      # 跳闸次数参数模块
            out = []
            for k in range(0x00, 0x0C):
                out += _bcd2(self.trip_counts[k], 2)
            return out
        if di[0:3] == (0x03, 0x81, 0x00) and di[3] <= 0x0B:
            return _bcd2(self.trip_counts[di[3]], 2)
        if di == (0x03, 0x81, 0x01, 0x01):      # 运行时间总累计(分)
            return _bcd2(self.runtime_min, 4)
        if di[0:3] == (0x03, 0x81, 0x02) and di[3] in self.new_counts:
            return _bcd2(self.new_counts[di[3]], 1)
        # ---- 参数变量(表A.2.5) ----
        if di == (0x04, 0x00, 0x01, 0x01):      # 当前日期 YYMMDDWW
            return P.bcd_encode(int(self._now.strftime("%y%m%d%w")), 4)
        if di == (0x04, 0x00, 0x01, 0x02):      # 当前时间 hhmmss
            return P.bcd_encode(int(self._now.strftime("%H%M%S")), 3)
        if di == (0x04, 0x00, 0x04, 0x01):      # 通信地址(A0..A5 低字节在前)
            return list(self.addr)
        if di == (0x04, 0x00, 0x04, 0x02):      # 设备号(12位BCD数值量,低字节在前)
            return P.bcd_encode_lf(123456789012, 6)
        if di == (0x04, 0x00, 0x04, 0x04):      # 额定电压
            return _ascii("AC 380V", 6)
        if di == (0x04, 0x00, 0x04, 0x05):      # 额定电流/基本电流
            return _ascii("100A", 6)
        if di == (0x04, 0x00, 0x04, 0x06):      # 最大(壳架)电流
            return _ascii("125A", 6)
        if di == (0x04, 0x00, 0x04, 0x0B):      # 设备型号
            return _ascii("DZ47LE-125", 10)
        if di == (0x04, 0x00, 0x04, 0x0C):      # 生产日期
            return _ascii("2025-06", 10)
        if di == (0x04, 0x00, 0x04, 0x0D):      # 协议版本号
            return _ascii("DL/T645-E1.0", 16)
        if di == (0x04, 0x00, 0x04, 0x0F):      # 固件版本号
            return _ascii("FW 1.02.03", 32)
        if di == (0x04, 0x00, 0x04, 0x10):      # 硬件版本号
            return _ascii("HW 1.00", 32)
        if di == (0x04, 0x00, 0x04, 0x11):      # 额定剩余电流动作值参数组
            out = []
            for g in self.res_gears:
                out += _bcd2(g, 2)
            out += [0xFF] * 8
            return out
        if di == (0x04, 0x00, 0x04, 0x12):      # 额定极限不驱动时间参数组
            out = []
            for g in self.time_gears:
                out += _bcd2(g, 2)
            out += [0xFF] * 4
            return out
        if di == (0x04, 0x00, 0x04, 0x14):      # 自动重合闸时间范围
            return _ascii("0.1-60s", 24)
        if di == (0x04, 0x00, 0x04, 0x15):      # 剩余电流动作特性
            return _ascii("AC型", 24)
        if di == (0x04, 0x00, 0x05, 0x01):      # 运行状态字1
            return [self._status1()]
        if di == (0x04, 0x00, 0x05, 0x02):      # 运行状态字2
            return [self._status2()]
        if di == (0x04, 0x00, 0x05, 0xFF):
            return [self._status1(), self._status2()]
        if di == (0x04, 0x00, 0x07, 0x03):      # 通信波特率特征字
            return [0x20]                        # 9600bps
        # ---- 密码(协议允许免密读取) 04 00 0C 01/02/03 -> 0/1/2级 ----
        if di in ((0x04, 0x00, 0x0C, 0x01),
                  (0x04, 0x00, 0x0C, 0x02),
                  (0x04, 0x00, 0x0C, 0x03)):
            lvl = di[3] - 1                      # 标识末字节 = 级别+1
            return list(self.passwords.get(lvl, [0xFF] * 4))
        if di == (0x04, 0x00, 0x10, 0x01):
            return [self.ctrl1]
        if di == (0x04, 0x00, 0x10, 0x02):
            return [self.ctrl2]
        if di == (0x04, 0x00, 0x10, 0x03):
            return [self.ctrl3]
        if di == (0x04, 0x00, 0x10, 0x04):
            return [self.ctrl4]
        if di == (0x04, 0x00, 0x10, 0x05):
            return [self.ctrl5]
        if di == (0x04, 0x00, 0x10, 0xFF):
            return [self.ctrl1, self.ctrl2, self.ctrl3, self.ctrl4, self.ctrl5]
        if di == (0x04, 0x00, 0x11, 0x04):
            return _bcd2(self.res_warn, 1)
        if di == (0x04, 0x00, 0x11, 0x05):
            return _bcd2(self.res_warn_t, 1)
        if di == (0x04, 0x00, 0x12, 0x01):      # 定时自检 DDhhmm
            d = self._now + timedelta(days=1)
            return P.bcd_encode(int(d.strftime("%d%H%M")), 3)
        if di == (0x04, 0x00, 0x13, 0x01):
            return _bcd2(round(self.over_v * 10), 2)
        if di == (0x04, 0x00, 0x13, 0x02):
            return _bcd2(round(self.under_v * 10), 2)
        if di == (0x04, 0x00, 0x13, 0x03):
            return _bcd2(round(self.nophase_v * 10), 2)
        if di == (0x04, 0x00, 0x14, 0x01):      # Ir1 NNNNN.N
            return _bcd2(round(self.ir1 * 10), 3)
        if di == (0x04, 0x00, 0x14, 0x03):      # t1
            return _bcd2(self.t1, 1)
        if di == (0x04, 0x00, 0x14, 0x04):      # Ir2
            return _bcd2(self.ir2, 1)
        if di == (0x04, 0x00, 0x14, 0x05):      # t2
            return _bcd2(self.t2, 2)
        if di == (0x04, 0x00, 0x14, 0x06):      # Ir3
            return _bcd2(self.ir3, 1)
        # ---- 事件记录(表A.2.4) ----
        if di[0:3] == (0x03, 0x8E, 0x00):
            return self._get_event(self.ev_prot_act, di[3])
        if di[0:3] == (0x03, 0x8D, 0x00):
            return self._get_event(self.ev_self_test, di[3])
        if di[0:3] == (0x03, 0x91, 0x00):
            return self._get_event(self.ev_switch, di[3])
        if di[0:3] == (0x03, 0x92, 0x00):
            return self._get_event(self.ev_alarm, di[3])
        if di[0:3] == (0x03, 0x93, 0x00):
            return self._get_event(self.ev_power, di[3])
        if di[0:3] == (0x03, 0x8F, 0x00):
            return self._get_event(self.ev_prot_sw, di[3])
        return None

    @staticmethod
    def _get_event(events, index):
        if not events or index == 0 or index > len(events):
            return None
        return events[index - 1]

    def _resp_read(self, data):
        if len(data) < 4:
            return P.build_frame(self.addr, 0xD1, [0x04])
        di_wire = tuple(data[0:4])
        di = P.di_canon(di_wire)
        payload = self._read_payload(di)
        if payload is None:
            return P.build_frame(self.addr, 0xD1, [0x04])
        return P.build_frame(self.addr, 0x91, list(di_wire) + payload)

    def _rated_res(self):
        """根据控制字4档位返回当前额定剩余电流动作值(mA)。"""
        gear = (self.ctrl4 >> 4) & 0x0F
        if gear == 0x0F:
            return 300.0
        if 0 <= gear < len(self.res_gears):
            return self.res_gears[gear]
        return 100.0

    def _rated_t(self):
        """根据控制字4返回当前额定极限不驱动时间(ms)。"""
        gear = (self.ctrl4 >> 2) & 0x03
        if gear == 0x03:
            return 200
        if gear < len(self.time_gears):
            return self.time_gears[gear]
        return 200

    def _status1(self):
        """运行状态字1: bit6-5闸位, bit4-0原因。"""
        pos = 0b01 if self.switch_state else 0b00
        return ((pos << 5) & 0x60) | (self.fault_reason & 0x1F)

    def _status2(self):
        """运行状态字2: 各事件新增标识。"""
        v = 0
        if self.new_counts[0x05] > 0:
            v |= 0x80
        if self.new_counts[0x01] > 0:
            v |= 0x40
        if self.new_counts[0x04] > 0:
            v |= 0x20
        if self.new_counts[0x02] > 0:
            v |= 0x10
        if self.new_counts[0x03] > 0:
            v |= 0x08
        if self.new_counts[0x06] > 0:
            v |= 0x04
        return v

    # ------------------------------------------------------------------
    # 写数据应答(14H)
    # ------------------------------------------------------------------
    def _resp_write(self, data):
        if len(data) < 12:
            return P.build_frame(self.addr, 0xD4, [0x01])
        di = P.di_canon(tuple(data[0:4]))
        pwd = data[4:8]
        body = data[12:]                    # 跳过操作者代码 C0..C3
        if not self._password_ok(pwd):
            return P.build_frame(self.addr, 0xD4, [0x02])
        if not self._apply_write(di, body):
            return P.build_frame(self.addr, 0xD4, [0x01])
        return P.build_frame(self.addr, 0x94, [])

    def _password_ok(self, pwd):
        """校验密码: 级别字节 + 后3字节必须与存储一致。"""
        level = pwd[0]
        if level not in self.passwords:
            return False
        return list(pwd) == self.passwords[level]

    def _apply_write(self, di, body):
        if di == (0x04, 0x00, 0x10, 0x01) and len(body) >= 1:
            self.ctrl1 = body[0]
            return True
        if di == (0x04, 0x00, 0x10, 0x02) and len(body) >= 1:
            self.ctrl2 = body[0]
            return True
        if di == (0x04, 0x00, 0x10, 0x03) and len(body) >= 1:
            self.ctrl3 = body[0]
            return True
        if di == (0x04, 0x00, 0x10, 0x04) and len(body) >= 1:
            self.ctrl4 = body[0]
            return True
        if di == (0x04, 0x00, 0x10, 0x05) and len(body) >= 1:
            self.ctrl5 = body[0]
            return True
        if di == (0x04, 0x00, 0x11, 0x04) and len(body) >= 1:
            self.res_warn = P.bcd_decode(body[:1])
            return True
        if di == (0x04, 0x00, 0x11, 0x05) and len(body) >= 1:
            self.res_warn_t = P.bcd_decode(body[:1])
            return True
        if di == (0x04, 0x00, 0x12, 0x01) and len(body) >= 3:
            v = P.bcd_decode(body[:3])
            self.st_hhmm = ((v // 100) % 100, v % 100)
            return True
        if di == (0x04, 0x00, 0x13, 0x01) and len(body) >= 2:
            self.over_v = P.bcd_decode_lf(body[:2]) / 10.0
            return True
        if di == (0x04, 0x00, 0x13, 0x02) and len(body) >= 2:
            self.under_v = P.bcd_decode_lf(body[:2]) / 10.0
            return True
        if di == (0x04, 0x00, 0x13, 0x03) and len(body) >= 2:
            self.nophase_v = P.bcd_decode_lf(body[:2]) / 10.0
            return True
        if di == (0x04, 0x00, 0x14, 0x01) and len(body) >= 3:
            self.ir1 = P.bcd_decode_lf(body[:3]) / 10.0
            return True
        if di == (0x04, 0x00, 0x14, 0x03) and len(body) >= 1:
            self.t1 = P.bcd_decode(body[:1])
            return True
        if di == (0x04, 0x00, 0x14, 0x04) and len(body) >= 1:
            self.ir2 = P.bcd_decode(body[:1])
            return True
        if di == (0x04, 0x00, 0x14, 0x05) and len(body) >= 2:
            self.t2 = P.bcd_decode_lf(body[:2])
            return True
        if di == (0x04, 0x00, 0x14, 0x06) and len(body) >= 1:
            self.ir3 = P.bcd_decode(body[:1])
            return True
        if di == (0x04, 0x00, 0x04, 0x01) and len(body) >= 6:
            new_addr = P.decode_addr(body[:6])
            self.addr = P.encode_addr(new_addr)
            return True
        if di == (0x04, 0x00, 0x01, 0x01) and len(body) >= 4:
            return True
        if di == (0x04, 0x00, 0x01, 0x02) and len(body) >= 3:
            return True
        return False

    # ------------------------------------------------------------------
    # 控制指令(1CH)
    # ------------------------------------------------------------------
    CTRL_ACTIONS = {
        (0x06, 0x01, 0x01, 0x01): "trip",      # 预约远程跳闸
        (0x06, 0x01, 0x02, 0x01): "close",     # 预约远程合闸
        (0x06, 0x01, 0x03, 0x01): "test",      # 预约模拟试跳
        (0x06, 0x01, 0x04, 0x01): "selftest",  # 预约自检
        (0x06, 0x01, 0x01, 0x02): "cancel_trip",
        (0x06, 0x01, 0x02, 0x02): "cancel_close",
        (0x06, 0x01, 0x03, 0x02): "cancel_test",
        (0x06, 0x01, 0x04, 0x02): "cancel_selftest",
    }

    def _resp_control(self, data):
        if len(data) < 12:
            return P.build_frame(self.addr, 0xDC, [0x01])
        di = P.di_canon(tuple(data[0:4]))
        pwd = data[4:8]
        # data[8:12] 为操作者代码 C0..C3
        if di not in self.CTRL_ACTIONS:
            return P.build_frame(self.addr, 0xDC, [0x04])
        if not self._password_ok(pwd):
            return P.build_frame(self.addr, 0xDC, [0x02])
        action = self.CTRL_ACTIONS[di]
        if action.startswith("cancel_"):
            self._pending = [p for p in self._pending if p[1] != action[7:]]
            return P.build_frame(self.addr, 0x9C, [])
        # 预约: 数据域第13-14字节 NN(延迟量) uu(单位:02分/03小时)
        delay = 0
        if len(data) >= 14:
            nn = P.bcd_decode(data[12:13])
            uu = P.bcd_decode(data[13:14])
            if uu == 0x03:
                delay = nn * 3600
            elif uu == 0x02:
                delay = nn * 60
            else:
                delay = nn * 60
        if delay > 0:
            self._pending.append(
                (self._now + timedelta(seconds=delay), action))
        else:
            if not self._do_control(action):
                return P.build_frame(self.addr, 0xDC, [0x10])
        return P.build_frame(self.addr, 0x9C, [])

    def _do_control(self, action):
        now = self._now
        if action == "close":
            if self.switch_state == 1:
                return True
            self.switch_state = 1
            self.fault_reason = 0x00
            self.ev_switch.insert(0, self._pack_switch(
                0x01, 0x1E, 0x00, now, self.residual,
                (self.va, self.vb, self.vc),
                (self.ia, self.ib, self.ic)))
            self.new_counts[0x04] += 1
            return True
        if action == "trip":
            if self.switch_state == 0:
                return True
            self.switch_state = 0
            self.fault_reason = 0x1E          # 软遥控
            self.trip_counts[0x00] += 1
            self.ev_switch.insert(0, self._pack_switch(
                0x00, 0x1E, 0x00, now, self.residual,
                (self.va, self.vb, self.vc),
                (self.ia, self.ib, self.ic)))
            self.new_counts[0x04] += 1
            self.new_counts[0x06] += 1
            self.ev_prot_act.insert(0, self._pack_prot_act(
                0x1E, 0x00, now, self.residual,
                (self.va, self.vb, self.vc),
                (self.ia, self.ib, self.ic), self.dir))
            return True
        if action == "test":
            self.switch_state = 0
            self.fault_reason = 0x0D          # 远程试验
            self.trip_counts[0x00] += 1
            self.trip_counts[0x07] += 1
            self.ev_switch.insert(0, self._pack_switch(
                0x00, 0x0D, 0x00, now, self.residual,
                (self.va, self.vb, self.vc),
                (self.ia, self.ib, self.ic)))
            self.new_counts[0x04] += 1
            self.ev_prot_act.insert(0, self._pack_prot_act(
                0x0D, 0x00, now, self.residual,
                (self.va, self.vb, self.vc),
                (self.ia, self.ib, self.ic), self.dir))
            return True
        if action == "selftest":
            self.ev_self_test.insert(0, self._pack_self_test(
                0x11, 0x01, now))
            self.new_counts[0x05] += 1
            return True
        return False

    # ------------------------------------------------------------------
    # 数据清零(1BH)
    # ------------------------------------------------------------------
    def _resp_clear(self, data):
        if len(data) < 8:
            return P.build_frame(self.addr, 0xDB, [0x01])
        pwd = data[4:8]
        if not self._password_ok(pwd):
            return P.build_frame(self.addr, 0xDB, [0x02])
        # 事件总清零: 标识 FFFFFFFF; 分项: DI0=FF
        self.clear_count += 1
        self.ev_prot_act = []
        self.ev_self_test = []
        self.ev_switch = []
        self.ev_alarm = []
        self.ev_power = []
        self.ev_prot_sw = []
        self.new_counts = {k: 0 for k in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06)}
        return P.build_frame(self.addr, 0x9B, [])

    # ------------------------------------------------------------------
    # 修改密码(18H)
    # ------------------------------------------------------------------
    def _resp_change_pwd(self, data):
        if len(data) < 12:
            return P.build_frame(self.addr, 0xD8, [0x01])
        di = P.di_canon(tuple(data[0:4]))
        old = data[4:8]
        new = data[8:12]
        if di != (0x04, 0x00, 0x0C, (new[0] & 0x0F) + 1):
            return P.build_frame(self.addr, 0xD8, [0x01])
        if not self._password_ok(old):
            return P.build_frame(self.addr, 0xD8, [0x04])
        if new[0] not in self.passwords:
            return P.build_frame(self.addr, 0xD8, [0x01])
        # 级别数字越小权限越高: 同级或更高级别(old数字<=new数字)才允许改
        if old[0] > new[0]:
            return P.build_frame(self.addr, 0xD8, [0x04])
        self.passwords[new[0]] = list(new)
        return P.build_frame(self.addr, 0x98, new)

    # ------------------------------------------------------------------
    def _tick(self):
        self._now = datetime.now()
        self._run_pending()

    def _run_pending(self):
        now = self._now
        for item in self._pending[:]:
            due, action = item
            if now >= due:
                self._pending.remove(item)
                self._do_control(action)

