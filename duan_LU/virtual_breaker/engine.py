# -*- coding: utf-8 -*-
"""虚拟断路器设备内核。

在 dl645_breaker.simulator.VirtualBreaker(纯协议应答)之上增加:
    * 电气量动态仿真: 三相电压/电流正弦变化、剩余电流漂移、分闸后电流归零;
    * 保护逻辑: 剩余电流 / 过载 / 短路(瞬时+短延时) / 过压 / 欠压 / 缺相,
      动作方式受控制字 2/3/4 约束, 时间可按 time_scale 加速(便于课堂演示);
    * 故障注入: 漏电、过载、短路、缺相、过压、欠压、停电, 以及随机行为;
    * 链路异常: 随机丢帧 / 破坏校验码 / 应答延迟, 用于讲解"超时与校验失败";
    * 审计与统计: 最近若干帧的收发记录、收帧/应答/校验错计数。

所有对外状态访问都在 RLock 内完成: 串口线程调用 handle_frame(),
界面线程调用 tick()/snapshot()/inject()。
"""

import math
import random
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from dl645_breaker import protocol as P
from dl645_breaker.simulator import VirtualBreaker

# 跳闸原因 -> 规约表 A.2.2 跳闸次数模块中的下标
TRIP_INDEX = {
    0x02: 2,    # 剩余电流保护
    0x05: 3,    # 过载
    0x09: 4,    # 过压
    0x06: 8,    # 短路短延时
    0x16: 9,    # 短路瞬时
    0x08: 10,   # 欠压
    0x07: 11,   # 缺相
    0x04: 6,    # 缺零
    0x0D: 7,    # 远程试验
    0x1E: 5,    # 软遥控(远程分闸)
}

PHASE_NO = {"A": 1, "B": 2, "C": 3}

FAULT_KINDS = [
    ("none", "正常工况"),
    ("residual", "剩余电流(漏电)"),
    ("overload", "过载"),
    ("short", "短路"),
    ("phase_loss", "缺相"),
    ("overvoltage", "过压"),
    ("undervoltage", "欠压"),
    ("power_loss", "停电"),
]


class VirtualDevice(VirtualBreaker):
    """一台可交互的虚拟断路器(从站地址默认 000000000001)。"""

    def __init__(self, addr_str=VirtualBreaker.DEFAULT_ADDR):
        super().__init__(addr_str)
        self.lock = threading.RLock()

        # ---- 仿真参数 ----
        self.running = True              # False = 设备"停机"(全部电气量为 0)
        self.freq = 50.0                 # 电网频率 Hz(影响波形与显示)
        self.load_rate = 0.35            # 负荷率(相对 Ir1)
        self.residual_base = 18.0        # 正常剩余电流 mA
        self.time_scale = 10.0           # 保护延时加速倍率(教学便于观察)
        self.auto_protect = True         # 保护逻辑总开关
        self.reclose_delay = 8.0         # 自动重合闸延时 s

        # 教学默认控制字: 让各类保护都处于"可跳闸"状态, 便于课堂演示
        #   控制字3 = 0x6A: 预警投入 / 短路瞬时 / 短路短延时 / 过流 均跳闸
        #   控制字4 = 0x32: 额定值 3 档(300mA), 不驱动时间 1 档(100ms),
        #                   剩余电流保护 = 10(跳闸, 不告警)
        self.ctrl3 = 0x6A
        self.ctrl4 = 0x32

        # ---- 故障注入 ----
        self.fault = "none"
        self.fault_phase = "A"
        self.fault_value = None
        self._fault_since = {}

        # ---- 链路异常(教学: 随机丢帧 / 错校验 / 延迟) ----
        self.link = {"drop": 0.0, "bad_cs": 0.0, "delay_ms": 0}
        self.random_mode = "off"
        self.random_interval = 6.0
        self.random_prob = 0.5
        self._random_next = time.monotonic() + self.random_interval

        # ---- 统计与审计 ----
        self.rx_count = 0
        self.tx_count = 0
        self.cs_error_count = 0
        self.addr_miss_count = 0
        self.drop_count = 0
        self.audit = deque(maxlen=400)

        # ---- 仿真时钟 ----
        self._t = 0.0
        self._last = time.monotonic()
        self._rand = random.Random()
        self._trip_time = None

    # ------------------------------------------------------------------
    # 时钟 / 电气量
    # ------------------------------------------------------------------
    def tick(self, dt=None):
        """推进仿真; 串口线程与界面定时器都会调用(可重入)。"""
        with self.lock:
            now = time.monotonic()
            if dt is None:
                dt = now - self._last
            dt = min(max(dt, 0.0), 0.5)
            self._last = now
            self._t += dt
            self._simulate(dt)
            self._protect(dt)
            self._random_behavior()
            self._tick()                 # 父类: 刷新 _now 并执行预约控制

    def _simulate(self, dt):
        """按当前工况计算三相电压/电流/剩余电流。"""
        if not self.running:
            self.va = self.vb = self.vc = 0.0
            self.ia = self.ib = self.ic = 0.0
            self.residual = 0.0
            return

        # 电压: 220V 基值 + 缓慢波动 + 少量噪声
        wobble = 0.004 * math.sin(self._t * 1.7)
        v = 220.0 * (1.0 + wobble) + self._rand.uniform(-0.25, 0.25)
        va = vb = vc = v

        if self.fault == "overvoltage":
            va = vb = vc = v * 1.22
        elif self.fault == "undervoltage":
            va = vb = vc = v * 0.68
        elif self.fault == "phase_loss":
            if self.fault_phase == "A":
                va = 0.0
            elif self.fault_phase == "B":
                vb = 0.0
            else:
                vc = 0.0
        elif self.fault == "power_loss":
            va = vb = vc = 0.0

        # 电流: 合闸时按负荷率生成, 分闸后为 0
        if self.switch_state:
            base_i = max(self.ir1 * self.load_rate, 0.5)
            ia = base_i * (1.0 + 0.02 * math.sin(self._t * 2.1))
            ib = base_i * (1.0 + 0.02 * math.sin(self._t * 2.1 - 2.094))
            ic = base_i * (1.0 + 0.02 * math.sin(self._t * 2.1 + 2.094))
            if self.fault == "overload":
                ia = ib = ic = max(self.ir1 * 1.35, base_i * 1.6)
            elif self.fault == "short":
                ia = ib = ic = max(self.ir1 * self.ir3 * 1.2,
                                   base_i * 12.0)
            elif self.fault == "phase_loss":
                if self.fault_phase == "A":
                    ia = 0.0
                elif self.fault_phase == "B":
                    ib = 0.0
                else:
                    ic = 0.0
            if self.fault == "power_loss":
                ia = ib = ic = 0.0
        else:
            ia = ib = ic = 0.0

        # 剩余电流: 正常微小漂移; 漏电故障时注入设定值
        res = self.residual_base + self._rand.uniform(-1.5, 1.5) \
            + 3.0 * math.sin(self._t * 0.7)
        if self.fault == "residual":
            res = self.fault_value if self.fault_value else max(
                self._rated_res() * 1.1, 35.0)
        elif self.fault in ("power_loss", "phase_loss"):
            res = 0.0
        self.residual = max(res, 0.0)
        if self.residual > 0.5:
            self.residual_phase = PHASE_NO.get(self.fault_phase, 1)

        self.va, self.vb, self.vc = va, vb, vc
        self.ia, self.ib, self.ic = ia, ib, ic


    # ------------------------------------------------------------------
    # 保护逻辑
    # ------------------------------------------------------------------
    def _protect(self, dt):
        if not self.auto_protect or not self.switch_state:
            self._fault_since.clear()
            return
        step = dt * max(self.time_scale, 0.1)
        v3 = (self.va, self.vb, self.vc)
        i3 = (self.ia, self.ib, self.ic)

        # 停电: 失压脱扣(原因 0B)
        if self.fault == "power_loss":
            if self.switch_state:
                self._trip(0x0B, 0, v3, i3)
            return

        def elapsed(key, need):
            self._fault_since[key] = self._fault_since.get(key, 0.0) + step
            return self._fault_since[key] >= need

        # ---- 剩余电流保护(控制字4 的 bit1-0 动作方式) ----
        mode_res = self.ctrl4 & 0x03
        if self.residual >= self._rated_res() and mode_res != 0:
            need = max(self._rated_t() / 1000.0, 0.05)
            if elapsed("res", need):
                if mode_res == 2:
                    self._trip(0x02, self.residual_phase, v3, i3)
                else:
                    self._alarm(0x01, self.residual_phase, v3, i3)
                self._fault_since.pop("res", None)
                return

        # ---- 电流类保护(控制字3) ----
        imax = max(i3)
        if imax > self.ir1 * self.ir3:
            mode = (self.ctrl3 >> 4) & 0x03
            if elapsed("short_i", max(self.t2 / 1000.0, 0.02)):
                if mode != 0:
                    self._trip(0x16, self._phase_of_max(i3), v3, i3)
                self._fault_since.pop("short_i", None)
                return
        elif imax > self.ir1 * self.ir2:
            mode = (self.ctrl3 >> 2) & 0x03
            if elapsed("short_d", max(self.t2 / 1000.0, 0.05)):
                if mode != 0:
                    self._trip(0x06, self._phase_of_max(i3), v3, i3)
                self._fault_since.pop("short_d", None)
                return
        elif imax > self.ir1:
            mode = self.ctrl3 & 0x03
            if elapsed("overload", max(self.t1, 0.05)):
                if mode != 0:
                    self._trip(0x05, self._phase_of_max(i3), v3, i3)
                self._fault_since.pop("overload", None)
                return
        else:
            self._fault_since.pop("overload", None)
            self._fault_since.pop("short_d", None)
            self._fault_since.pop("short_i", None)

        # ---- 电压类保护(控制字2: bit7-6 欠压 / bit5-4 过压 / bit3-2 缺相) ----
        vmax = max(v3)
        vmin = min(v3)
        if self.va and self.vb and self.vc and vmax > self.over_v:
            mode = (self.ctrl2 >> 4) & 0x03
            if elapsed("over_v", 0.4):
                if mode == 2:
                    self._trip(0x09, 0, v3, i3)
                elif mode == 1:
                    self._alarm(0x01, 0, v3, i3)
                self._fault_since.pop("over_v", None)
                return
        elif self.va and self.vb and self.vc and vmin < self.under_v:
            mode = (self.ctrl2 >> 6) & 0x03
            if elapsed("under_v", 0.6):
                if mode == 2:
                    self._trip(0x08, 0, v3, i3)
                elif mode == 1:
                    self._alarm(0x01, 0, v3, i3)
                self._fault_since.pop("under_v", None)
                return
        else:
            self._fault_since.pop("over_v", None)
            self._fault_since.pop("under_v", None)

        # ---- 缺相(任一相电压低于阈值) ----
        lost = [i for i, v in enumerate(v3) if v < self.nophase_v]
        if lost:
            mode = (self.ctrl2 >> 2) & 0x03
            if elapsed("phase_loss", 0.5):
                if mode == 2:
                    self._trip(0x07, lost[0] + 1, v3, i3)
                elif mode == 1:
                    self._alarm(0x01, lost[0] + 1, v3, i3)
                self._fault_since.pop("phase_loss", None)
        else:
            self._fault_since.pop("phase_loss", None)

    @staticmethod
    def _phase_of_max(i3):
        return i3.index(max(i3)) + 1

    def _trip(self, reason, phase, v3, i3):
        """保护跳闸: 更新闸位/原因/计数/事件, 并按控制字1 决定是否重合闸。"""
        now = datetime.now()
        self.switch_state = 0
        self.fault_reason = reason
        self.trip_counts[0x00] = self.trip_counts.get(0x00, 0) + 1
        idx = TRIP_INDEX.get(reason)
        if idx:
            self.trip_counts[idx] = self.trip_counts.get(idx, 0) + 1
        self.ev_switch.insert(0, self._pack_switch(
            0x00, reason, phase, now, self.residual, v3, i3))
        self.ev_prot_act.insert(0, self._pack_prot_act(
            reason, phase, now, self.residual, v3, i3, self.dir))
        self.new_counts[0x02] = self.new_counts.get(0x02, 0) + 1
        self.new_counts[0x04] = self.new_counts.get(0x04, 0) + 1
        self._trip_time = now
        self._audit("event", b"", "保护跳闸: %s" % self._reason_text(reason))
        # 自动重合闸: 控制字1 bit1 = 0 允许 (仅剩余电流保护跳闸)
        if reason == 0x02 and not (self.ctrl1 & 0x02):
            self._pending.append(
                (now + timedelta(seconds=self.reclose_delay), "close"))
            self._audit("event", b"", "自动重合闸将在 %.1f s 后执行"
                        % self.reclose_delay)

    def _alarm(self, flag, phase, v3, i3):
        now = datetime.now()
        reason = self.fault_reason or 0x01
        self.ev_alarm.insert(0, self._pack_alarm(
            flag, reason, phase, now, self.residual, v3, i3))
        self.new_counts[0x01] = self.new_counts.get(0x01, 0) + 1
        self._audit("event", b"", "告警: %s" % self._reason_text(reason))

    @staticmethod
    def _reason_text(reason):
        from dl645_breaker.simulator import REASON_TABLE
        return REASON_TABLE.get(reason, "原因%02X" % reason)


    # ------------------------------------------------------------------
    # 故障注入 / random
    # ------------------------------------------------------------------
    def inject(self, kind, value=None, phase="A"):
        """注入工况。kind 见 FAULT_KINDS。"""
        with self.lock:
            self.fault = kind if kind in dict(FAULT_KINDS) else "none"
            self.fault_value = value
            self.fault_phase = phase if phase in PHASE_NO else "A"
            self._fault_since.clear()
            self._audit("event", b"", "注入工况: %s%s"
                        % (dict(FAULT_KINDS).get(self.fault, self.fault),
                           " %s相" % phase if kind in
                           ("phase_loss", "residual") else ""))
            self._simulate(0.0)

    def clear_fault(self):
        with self.lock:
            self.fault = "none"
            self.fault_value = None
            self._fault_since.clear()
            self._audit("event", b"", "工况恢复正常")
            self._simulate(0.0)

    def _random_behavior(self):
        """随机行为: 数值抖动 / 随机跳闸。"""
        if self.random_mode == "off":
            return
        now = time.monotonic()
        if now < self._random_next:
            return
        self._random_next = now + max(self.random_interval, 1.0)
        if self.random_mode in ("trip", "mixed") and self.switch_state:
            if self._rand.random() < self.random_prob:
                reasons = [0x02, 0x05, 0x07, 0x08, 0x09, 0x16]
                reason = self._rand.choice(reasons)
                self._trip(reason, self._rand.randint(1, 3),
                           (self.va, self.vb, self.vc),
                           (self.ia, self.ib, self.ic))
        if self.random_mode in ("jitter", "mixed"):
            scale = 1.0 + self._rand.uniform(-0.15, 0.15)
            self.va, self.vb, self.vc = (self.va * scale, self.vb * scale,
                                         self.vc * scale)

    # ------------------------------------------------------------------
    # 读数据应答(覆盖父类: 不支持的 DI 按规约回 02H=无请求数据项)
    # ------------------------------------------------------------------
    def _resp_read(self, data):
        if len(data) < 4:
            return P.build_frame(self.addr, 0xD1, [0x02])
        di_wire = tuple(data[0:4])
        di = P.di_canon(di_wire)
        payload = self._read_payload(di)
        if payload is None:
            return P.build_frame(self.addr, 0xD1, [0x02])
        return P.build_frame(self.addr, 0x91, list(di_wire) + payload)

    # ------------------------------------------------------------------
    # 帧处理(串口线程入口)
    # ------------------------------------------------------------------
    def handle_frame(self, frame):
        """收一帧请求 -> 返回应答帧; 广播/地址不符/丢帧返回 b''。"""
        with self.lock:
            self.rx_count += 1
            self.tick()
            raw = bytes(frame)
            # 链路异常: 丢帧(模拟总线碰撞/从站忙)
            if self.link.get("drop", 0.0) > 0 \
                    and self._rand.random() < self.link["drop"]:
                self.drop_count += 1
                self._audit("rx", raw, "已收帧, 按设定随机丢弃(不应答)")
                return b""
            delay = self.link.get("delay_ms", 0)
            if delay:
                time.sleep(delay / 1000.0)
            try:
                f = P.parse_frame(raw)
            except ValueError as exc:
                self.cs_error_count += 1
                self._audit("rx", raw, "帧解析失败: %s" % exc)
                return b""
            if not self._addr_match(f["addr"]):
                self.addr_miss_count += 1
                self._audit("rx", raw, "地址不符(本机 %s), 不应答"
                            % P.decode_addr(self.addr))
                return b""
            self._audit("rx", raw, "收到请求: C=%02XH, 数据域 %d 字节"
                        % (f["control"], f["length"]))
            resp = self._dispatch(f)
            if self.link.get("bad_cs", 0.0) > 0 and resp \
                    and self._rand.random() < self.link["bad_cs"]:
                resp = self._corrupt(resp)
                self._audit("tx", resp, "应答已按设定破坏校验码")
            else:
                self._audit("tx", resp, "正常应答")
            if resp:
                self.tx_count += 1
            return resp

    @staticmethod
    def _corrupt(resp):
        """把一个应答帧的校验码加 1, 用于演示主站"校验和错误"分支。"""
        b = bytearray(resp)
        if len(b) >= 2:
            b[-2] = (b[-2] + 1) & 0xFF
        return bytes(b)

    def _audit(self, direction, raw, note):
        """记录一条收发/事件审计(界面日志使用)。"""
        self.audit.append({
            "ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "dir": direction,
            "raw": bytes(raw),
            "note": note,
        })

    # ------------------------------------------------------------------
    # 界面快照
    # ------------------------------------------------------------------
    def snapshot(self):
        with self.lock:
            return {
                "addr": P.decode_addr(self.addr),
                "switch": self.switch_state,
                "reason": self.fault_reason,
                "v3": (self.va, self.vb, self.vc),
                "i3": (self.ia, self.ib, self.ic),
                "residual": self.residual,
                "res_phase": self.residual_phase,
                "rated_res": self._rated_res(),
                "rated_t": self._rated_t(),
                "status1": self._status1(),
                "status2": self._status2(),
                "ctrl": (self.ctrl1, self.ctrl2, self.ctrl3,
                         self.ctrl4, self.ctrl5),
                "ir1": self.ir1,
                "over_v": self.over_v,
                "under_v": self.under_v,
                "fault": self.fault,
                "freq": self.freq,
                "load_rate": self.load_rate,
                "running": self.running,
                "auto_protect": self.auto_protect,
                "time_scale": self.time_scale,
                "rx": self.rx_count,
                "tx": self.tx_count,
                "cs_err": self.cs_error_count,
                "addr_miss": self.addr_miss_count,
                "drop": self.drop_count,
                "trip_total": self.trip_counts.get(0x00, 0),
                "pending": len(self._pending),
                "events": (len(self.ev_prot_act), len(self.ev_switch),
                           len(self.ev_alarm)),
            }


