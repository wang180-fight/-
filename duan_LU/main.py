#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序入口: 启动 DL/T 645 剩余电流断路器远程监控 GUI。

用法:
    python main.py

顶部可选通信模式:
    - 串口模式: 通过串口(RS-485, 8E1, 9600)连接真实设备进行远程监控;
    - 仿真模式: 无需硬件, 使用内置虚拟断路器离线演示全部功能。
"""
from dl645_breaker.gui import main

if __name__ == "__main__":
    main()
