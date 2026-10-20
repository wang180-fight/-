# -*- coding: utf-8 -*-
"""设备通信层：串口/虚拟传输 + 高层操作 API。"""

import time
import threading

from . import protocol as P


# ---------------------------------------------------------------------------
# 传输层: 串口
# ---------------------------------------------------------------------------
class SerialTransport:
    """RS-485 半双工串口传输。字节格式: 8E1, 偶校验。"""

    def __init__(self, port, baudrate=9600, timeout=1.5):
        import serial
        self._ser = serial.Serial(
            port=port, baudrate=baudrate,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE, timeout=timeout)
        self._lock = threading.Lock()

    def send(self, frame):
        """发送请求帧并读取完整应答帧, 返回应答原始字节。"""
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            self._ser.flush()
            header = self._ser.read(10)          # 68+6地址+68+C+L
            if len(header) < 10:
                raise TimeoutError("无应答(超时)")
            if header[0] != 0x68:
                raise ValueError("应答帧起始符错误: %s" % header.hex())
            length = header[9]
            rest = self._ser.read(length + 2)    # DATA + CS + 16H
            if len(rest) < length + 2:
                raise TimeoutError("应答帧不完整(超时)")
            return header + rest

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 传输层: 虚拟(仿真)传输
# ---------------------------------------------------------------------------
class VirtualTransport:
    """将请求帧直接交给虚拟断路器处理, 用于无硬件教学。"""

    def __init__(self, device):
        self._device = device

    def send(self, frame):
        return self._device.handle_frame(frame)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 设备操作 API
# ---------------------------------------------------------------------------
class BreakerClient:
    """面向 GUI 的高层操作接口。所有方法返回解析后的数据。"""

    def __init__(self, transport, addr_str="000000000001"):
        self.transport = transport
        self.addr = P.encode_addr(addr_str)
        self.addr_str = addr_str

    # -- 基础收发 --
    def _request(self, frame):
        return P.parse_response(self.transport.send(frame))

    # -- 读数据 --
    def read(self, di):
        """读数据, 返回数据域(不含标识码)字节列表。"""
        resp = self._request(P.build_read_request(self.addr, di))
        if not resp["ok"]:
            raise OSError("读数据失败: %s" % self._err_text(resp["err"]))
        return resp["data"][4:]                 # 去掉 DI0-DI3

    def read_raw(self, di):
        """读数据, 返回含标识码的完整数据域。"""
        resp = self._request(P.build_read_request(self.addr, di))
        if not resp["ok"]:
            raise OSError("读数据失败: %s" % self._err_text(resp["err"]))
        return resp["data"]

    # -- 写数据 --
    def write(self, di, data, password=(0, 0, 0, 0),
              operator=(0, 0, 0, 0)):
        resp = self._request(P.build_write_request(
            self.addr, di, password, operator, data))
        if not resp["ok"]:
            raise OSError("写数据失败: %s" % self._err_text(resp["err"]))
        return True

    # -- 控制指令 --
    def control(self, di, data, password=(0, 0, 0, 0),
                operator=(0, 0, 0, 0)):
        resp = self._request(P.build_control_request(
            self.addr, di, password, operator, data))
        if not resp["ok"]:
            raise OSError("控制指令失败: %s" % self._err_text(resp["err"]))
        return True

    # -- 修改密码 --
    def change_password(self, old_pwd, new_pwd):
        resp = self._request(P.build_change_pwd_request(
            self.addr, old_pwd, new_pwd))
        if not resp["ok"]:
            raise OSError("修改密码失败: %s" % self._err_text(resp["err"]))
        return resp["data"]

    # -- 数据清零 --
    def clear_data(self, di, password=(0, 0, 0, 0),
                   operator=(0, 0, 0, 0)):
        resp = self._request(P.build_clear_request(
            self.addr, di, password, operator))
        if not resp["ok"]:
            raise OSError("数据清零失败: %s" % self._err_text(resp["err"]))
        return True

    @staticmethod
    def _err_text(err):
        return P.ERR_TABLE.get(err, "未知错误(0x%02X)" % err)


# ---------------------------------------------------------------------------
# 便捷封装: 密码 / 操作者代码构造
# ---------------------------------------------------------------------------
def make_password(level, code6):
    """构造4字节密码: PA P0 P1 P2, PA为密码级别。

    level: 0/1/2
    code6: 6位十进制密码字符串
    """
    code6 = str(code6).zfill(6)[-6:]
    b = P.bcd_encode(int(code6), 3)      # [P0,P1,P2]
    return [level & 0xFF] + b


def make_operator(code8="00000000"):
    """构造4字节操作者代码(C0 C1 C2 C3)。"""
    code8 = str(code8).zfill(8)[-8:]
    return P.bcd_encode(int(code8), 4)

