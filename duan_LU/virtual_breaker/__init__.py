# -*- coding: utf-8 -*-
"""虚拟断路器（从站设备仿真）软件包。

模块划分:
    panel.py      设备实物面板: 外壳/手柄/指示灯/按钮/端子/RS485, 可点击操作
    engine.py     设备内核: 在 dl645_breaker.simulator.VirtualBreaker 基础上
                  增加电气量动态仿真、保护逻辑、故障注入与链路异常
    station.py    串口从站服务: 按 DL/T 645 帧格式接收主站请求并回送应答
    teaching.py   教学解析: 帧结构分解、校验和过程、DI 目录、状态字/控制字位表
    gui.py        tkinter 图形界面(设备本体 / 运行数据 / 指令台 /
                  通信日志 / 协议说明)

与上位机软件(duan_LU/main.py) 使用同一套 dl645_breaker.protocol 编码,
因此两者可在虚拟串口对(如 com0com 的 COM4<->COM5)上直接互通。
"""

__version__ = "1.1.0"
