# -*- coding: utf-8 -*-
"""串口从站服务与主站链路。

本软件有两种"身份":
    1. 从站(设备): SlaveServer 打开一个串口, 按 DL/T 645 帧格式接收上位机
       请求, 交给 VirtualDevice 生成应答后回送; 这是"虚拟断路器"本体。
    2. 主站(调试台): MasterSerialLink / LoopbackLink 让本软件也能主动发指令,
       用于课堂演示"同一台电脑上的主站与从站", 或直接内置回环自测。

帧读取严格按 DL/T 645-2007 数据链路层:
    68H | A0-A5 | 68H | C | L | DATA(L 字节) | CS | 16H
"""

import threading
import time

try:
    import serial
    from serial.tools import list_ports
except Exception:                             # 未安装 pyserial 时不阻塞界面
    serial = None
    list_ports = None

PARITY_MAP = {"N": "N", "E": "E", "O": "O", "偶校验": "E", "奇校验": "O",
              "无校验": "N"}


def available_ports():
    """返回 [(设备名, 描述)]; 未安装 pyserial 时返回空列表。"""
    if list_ports is None:
        return []
    out = []
    try:
        for p in list_ports.comports():
            desc = p.description or ""
            out.append((p.device, desc))
    except Exception:
        pass
    return out


def _serial_kwargs(baudrate, bytesize, parity, stopbits):
    if serial is None:
        raise RuntimeError("未安装 pyserial, 请执行: pip install pyserial")
    parity = PARITY_MAP.get(str(parity).upper(), str(parity).upper())
    parity_const = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                    "O": serial.PARITY_ODD}.get(parity)
    if parity_const is None:
        raise ValueError("不支持的校验方式: %s" % parity)
    size = int(bytesize)
    size_const = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS,
                  8: serial.EIGHTBITS}.get(size)
    if size_const is None:
        raise ValueError("不支持的数据位: %s" % bytesize)
    stop = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO,
            "1": serial.STOPBITS_ONE, "2": serial.STOPBITS_TWO,
            "1.5": serial.STOPBITS_ONE_POINT_FIVE}.get(stopbits)
    if stop is None:
        raise ValueError("不支持的停止位: %s" % stopbits)
    return {"baudrate": int(baudrate), "bytesize": size_const,
            "parity": parity_const, "stopbits": stop}


class SlaveServer(threading.Thread):
    """串口从站: 阻塞读帧 -> VirtualDevice.handle_frame -> 回写应答。"""

    def __init__(self, device, port, baudrate=9600, bytesize=8, parity="E",
                 stopbits=1, on_event=None):
        threading.Thread.__init__(self, daemon=True)
        self.device = device
        self.port = port
        self.on_event = on_event
        self._stop = threading.Event()
        self._ser = None
        self.error = None
        self.frames = 0
        self.replies = 0
        kwargs = _serial_kwargs(baudrate, bytesize, parity, stopbits)
        self._kwargs = kwargs
        self._ser = serial.Serial(port=port, timeout=0.25, **kwargs)
        self._ser.reset_input_buffer()

    @property
    def is_open(self):
        return bool(self._ser and self._ser.is_open)

    def stop(self):
        self._stop.set()
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass

    def _notify(self, text):
        if self.on_event:
            try:
                self.on_event(text)
            except Exception:
                pass

    def _read_frame(self):
        """从串口读一帧; 超时/无数据返回 None。"""
        ser = self._ser
        b = ser.read(1)
        if not b:
            return None
        if b[0] != 0x68:                     # 非起始符: 丢弃并继续
            return None
        head = ser.read(7)                   # A0-A5 + 68H
        if len(head) < 7 or head[6] != 0x68:
            return None
        cl = ser.read(2)                     # C + L
        if len(cl) < 2:
            return None
        length = cl[1]
        rest = ser.read(length + 2)          # DATA + CS + 16H
        if len(rest) < length + 2:
            return None
        return bytes(b) + head + cl + rest

    def run(self):
        self._notify("从站已启动: %s (8%s%d)" % (
            self.port, "E" if self._kwargs["parity"] == "E" else "N",
            self._kwargs["stopbits"]))
        while not self._stop.is_set():
            try:
                frame = self._read_frame()
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.error = str(exc)
                self._notify("串口读错误: %s" % exc)
                time.sleep(0.3)
                continue
            if not frame:
                continue
            self.frames += 1
            try:
                resp = self.device.handle_frame(frame)
            except Exception as exc:
                self.error = str(exc)
                self._notify("设备处理异常: %s" % exc)
                continue
            if not resp:
                continue
            try:
                self._ser.write(resp)
                self._ser.flush()
                self.replies += 1
            except Exception as exc:
                self.error = str(exc)
                self._notify("串口写错误: %s" % exc)
                time.sleep(0.2)
        self._notify("从站已停止")


class MasterSerialLink:
    """主站串口链路: 发送请求帧并读回一帧应答(字节格式可配置)。"""

    def __init__(self, port, baudrate=9600, bytesize=8, parity="E",
                 stopbits=1, timeout=1.2):
        self.port = port
        kwargs = _serial_kwargs(baudrate, bytesize, parity, stopbits)
        self._ser = serial.Serial(port=port, timeout=timeout, **kwargs)
        self._ser.reset_input_buffer()

    def send(self, frame):
        ser = self._ser
        ser.reset_input_buffer()
        ser.write(bytes(frame))
        ser.flush()
        header = ser.read(10)                # 68 + A0-A5 + 68 + C + L
        if len(header) < 10:
            raise TimeoutError("无应答(等待超时)")
        if header[0] != 0x68:
            return header                    # 交给解析器报告起始符错误
        length = header[9]
        rest = ser.read(length + 2)
        if len(rest) < length + 2:
            raise TimeoutError("应答帧不完整(超时)")
        return header + rest

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


class LoopbackLink:
    """内置回环: 不经过串口, 直接把帧交给本机 VirtualDevice。

    用于"没有安装虚拟串口对"的机房: 学生照样能在同一个界面里
    完成组帧 -> 发送 -> 回显 -> 解析 -> 帧分解的完整实验。
    """

    def __init__(self, device):
        self.device = device
        self.port = "内置回环"

    def send(self, frame):
        return self.device.handle_frame(frame)

    def close(self):
        pass


def probe_loop_pair():
    """提示信息: 检测本机是否存在可能成对的虚拟串口(com0com 等)。"""
    ports = available_ports()
    hints = [(d, desc) for d, desc in ports
             if any(k in desc.upper() for k in ("COM0COM", "VIRTUAL",
                                                "PAIR", "虚拟"))]
    return ports, hints

