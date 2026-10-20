#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序入口: 启动虚拟断路器模拟器(从站) + 协议教学实训台。

用法:
    python virtual_main.py

与上位机(duan_LU/main.py) 的关系:
    本软件扮演"断路器设备", 通过串口(建议 com0com 虚拟串口对)接收上位机
    的 DL/T 645 请求帧, 按规约应答; 也可在[综合实训台]里以主站身份主动
    发送指令(内置回环或串口), 便于单机教学。
"""
from virtual_breaker.gui import main

if __name__ == "__main__":
    main()
