#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""程序入口: 启动虚拟断路器设备(从站) —— 一台可操作的智能塑壳断路器。

用法:
    python virtual_main.py

与上位机(duan_LU/main.py) 的关系:
    本软件扮演"断路器设备", 通过串口(建议 com0com 虚拟串口对)接收上位机
    的 DL/T 645 请求帧, 按规约应答; 界面上的手柄、按钮、拨钮就是设备本体的
    就地操作部件, 保护跳闸/脱扣/复位与真实断路器一致。
"""
from virtual_breaker.gui import main

if __name__ == "__main__":
    main()
