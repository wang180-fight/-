"""DL/T 645 扩展规约 —— 具有自动重合闸功能的剩余电流保护断路器 远程监控软件包。

模块划分:
    protocol.py  帧编解码 / BCD / 标识码等底层实现
    device.py    通信传输层与设备操作 API
    simulator.py 虚拟断路器仿真器(教学无硬件模式)
    gui.py       tkinter 图形界面
"""

__version__ = "1.0.0"
