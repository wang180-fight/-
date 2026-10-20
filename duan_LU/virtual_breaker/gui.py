# -*- coding: utf-8 -*-
"""虚拟断路器实训软件 —— tkinter 界面。

布局:
    顶部   标题 + 运行状态灯
    左侧   通讯设置(端口/波特率/数据位/校验/停止位/从站地址/监听)
    右侧   页签
             1) 综合实训台   系统参数测量 | 系统设备控制 | 模拟测试系统
             2) 实时监测     三相电气量 / 波形 / 闸位 / 故障注入
             3) 通信日志     报文表格 + 帧结构分解 + 校验和计算过程
             4) 协议教学     帧格式 / 控制字位表 / DI 目录 / 状态字位表
"""

import math
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox

from dl645_breaker import protocol as P
from dl645_breaker.device import make_operator, make_password

from . import teaching as T
from .engine import FAULT_KINDS, VirtualDevice
from .station import (LoopbackLink, MasterSerialLink, SlaveServer,
                      available_ports)

# ---- 主题(与上位机同一视觉体系, 强调色改为青色以区分"设备侧") ----
C_BG, C_PANEL, C_LINE = "#eef1f6", "#ffffff", "#d7dde8"
C_ACCENT, C_ACCENT2 = "#0f766e", "#115e59"
C_GREEN, C_RED, C_ORANGE = "#16a34a", "#dc2626", "#ea580c"
C_GRAY, C_MUTED, C_TEXT = "#475569", "#94a3b8", "#0f172a"
C_VALUE_BG = "#f1f5f9"


def pick_font(*names):
    import tkinter.font as tkfont
    fams = set(tkfont.families())
    for n in names:
        if n in fams:
            return n
    return "TkDefaultFont"


def apply_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    font = pick_font("Microsoft YaHei UI", "Noto Sans CJK SC",
                     "PingFang SC", "WenQuanYi Micro Hei", "Segoe UI")
    root.FONT = font
    style.configure(".", background=C_BG, foreground=C_TEXT, font=(font, 10))
    style.configure("TFrame", background=C_BG)
    style.configure("TLabel", background=C_BG, foreground=C_TEXT)
    style.configure("Title.TLabel", background=C_BG, foreground=C_ACCENT,
                    font=(font, 15, "bold"))
    style.configure("Sub.TLabel", background=C_BG, foreground=C_MUTED,
                    font=(font, 9))
    style.configure("Card.TFrame", background=C_PANEL)
    style.configure("Card.TLabel", background=C_PANEL, foreground=C_TEXT)
    style.configure("CardMuted.TLabel", background=C_PANEL,
                    foreground=C_MUTED)
    style.configure("TLabelframe", background=C_PANEL, bordercolor=C_LINE,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=C_PANEL,
                    foreground=C_ACCENT, font=(font, 10, "bold"))
    style.configure("TButton", background=C_ACCENT, foreground="#ffffff",
                    padding=(10, 5), borderwidth=0, focusthickness=0,
                    font=(font, 10))
    style.map("TButton", background=[("pressed", C_ACCENT2),
                                     ("active", C_ACCENT2)])
    style.configure("Secondary.TButton", background="#e2e8f0",
                    foreground=C_TEXT)
    style.map("Secondary.TButton",
              background=[("pressed", "#cbd5e1"), ("active", "#cbd5e1")])
    style.configure("Danger.TButton", background=C_RED, foreground="#ffffff")
    style.map("Danger.TButton",
              background=[("pressed", "#b91c1c"), ("active", "#b91c1c")])
    style.configure("TNotebook", background=C_BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(16, 7), font=(font, 10))
    style.map("TNotebook.Tab",
              background=[("selected", C_PANEL)],
              foreground=[("selected", C_ACCENT)])
    style.configure("Treeview", background=C_PANEL, fieldbackground=C_PANEL,
                    rowheight=24, bordercolor=C_LINE)
    style.configure("Treeview.Heading", font=(font, 9, "bold"))
    style.configure("Status.TLabel", background=C_BG, foreground=C_GRAY)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("虚拟断路器模拟器 —— 电气设备检测与控制实训系统")
        self.geometry("1300x780")
        self.minsize(1120, 700)
        apply_theme(self)
        self.configure(bg=C_BG)
        self._set_icon()

        self.device = VirtualDevice("000000000001")
        self.server = None
        self.master_link = None
        self._master_q = queue.Queue()
        self._last_audit = None
        self._test_running = False
        self._auto_seq = 0
        self._wave_t = 0.0

        self._build_header()
        self._build_body()
        self._build_statusbar()

        self._refresh_ports()
        self.after(120, self._poll_master)
        self.after(200, self._poll_device)
        self.after(300, self._poll_audit)
        self.after(100, self._anim)

    # ------------------------------------------------------------------
    # 窗口基础
    # ------------------------------------------------------------------
    def _set_icon(self):
        try:
            if getattr(sys, "frozen", False):
                ico = os.path.join(getattr(sys, "_MEIPASS", ""), "app.ico")
            else:
                ico = os.path.abspath(os.path.join(
                    os.path.dirname(__file__), "..", "app.ico"))
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

    def _build_header(self):
        bar = ttk.Frame(self, padding=(14, 8, 14, 4))
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="虚拟断路器模拟器", style="Title.TLabel"
                  ).pack(side=tk.LEFT)
        ttk.Label(bar, text="DL/T 645 带漏电保护塑壳断路器 · 从站实训设备",
                  style="Sub.TLabel").pack(side=tk.LEFT, padx=(12, 0),
                                           pady=(6, 0))
        self.var_clock = tk.StringVar(value=datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"))
        ttk.Label(bar, textvariable=self.var_clock, style="Sub.TLabel"
                  ).pack(side=tk.RIGHT)

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(10, 4))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.var_status = tk.StringVar(value="就绪: 请选择串口后点击[开始监听]")
        ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel"
                  ).pack(side=tk.LEFT)
        self.var_stats = tk.StringVar(value="收帧 0 / 应答 0")
        ttk.Label(bar, textvariable=self.var_stats, style="Status.TLabel"
                  ).pack(side=tk.RIGHT)

    def _build_body(self):
        body = ttk.Frame(self, padding=(8, 0, 8, 4))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._build_sidebar(body)
        nb = ttk.Notebook(body)
        nb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.nb = nb

        self.tab_bench = ttk.Frame(nb, padding=8)
        self.tab_monitor = ttk.Frame(nb, padding=8)
        self.tab_log = ttk.Frame(nb, padding=6)
        self.tab_teach = ttk.Frame(nb, padding=6)
        nb.add(self.tab_bench, text="综合实训台")
        nb.add(self.tab_monitor, text="实时监测")
        nb.add(self.tab_log, text="通信日志")
        nb.add(self.tab_teach, text="协议教学")

        self._build_tab_bench()
        self._build_tab_monitor()
        self._build_tab_log()
        self._build_tab_teach()


    # ------------------------------------------------------------------
    # 左侧: 通讯设置
    # ------------------------------------------------------------------
    def _build_sidebar(self, parent):
        side = ttk.Frame(parent, width=318)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)

        box = ttk.LabelFrame(side, text="通讯设置(从站)", padding=10)
        box.pack(fill=tk.X)

        self.var_port = tk.StringVar(value="")
        self.var_baud = tk.StringVar(value="9600")
        self.var_bytesize = tk.StringVar(value="8")
        self.var_parity = tk.StringVar(value="偶校验")
        self.var_stopbits = tk.StringVar(value="1")
        self.var_slave_addr = tk.StringVar(value="000000000001")
        self.port_map = {}

        def row(r, text, widget):
            ttk.Label(box, text=text, style="Card.TLabel").grid(
                row=r, column=0, sticky=tk.W, pady=4)
            widget.grid(row=r, column=1, sticky="ew", pady=4)
            return widget

        box.columnconfigure(1, weight=1)
        self.cmb_port = ttk.Combobox(box, textvariable=self.var_port,
                                     width=22)
        row(0, "端口号:", self.cmb_port)
        port_row = ttk.Frame(box, style="Card.TFrame")
        port_row.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(port_row, text="刷新端口", style="Secondary.TButton",
                   command=self._refresh_ports).pack(side=tk.LEFT)
        ttk.Label(port_row, text="(虚拟串口对的一端)", style="CardMuted.TLabel"
                  ).pack(side=tk.LEFT, padx=6)

        row(2, "波特率:", ttk.Combobox(
            box, textvariable=self.var_baud,
            values=["1200", "2400", "4800", "9600", "19200", "38400"],
            width=10))
        row(3, "数据位:", ttk.Combobox(
            box, textvariable=self.var_bytesize, values=["8", "7", "6", "5"],
            width=10))
        row(4, "校验:", ttk.Combobox(
            box, textvariable=self.var_parity,
            values=["偶校验", "奇校验", "无校验"], width=10))
        row(5, "停止位:", ttk.Combobox(
            box, textvariable=self.var_stopbits, values=["1", "2"], width=10))
        row(6, "从站地址:", ttk.Entry(box, textvariable=self.var_slave_addr,
                                      width=14))

        btns = ttk.Frame(box, style="Card.TFrame")
        btns.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        self.btn_listen = ttk.Button(btns, text="开始监听",
                                     command=self._start_server)
        self.btn_listen.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_stop = ttk.Button(btns, text="停止", style="Secondary.TButton",
                                   command=self._stop_server,
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(6, 0))


        # ---- 运行状态灯 ----
        state = ttk.Frame(side, padding=(2, 10, 2, 0))
        state.pack(fill=tk.X)
        self.lamp = tk.Canvas(state, width=14, height=14, highlightthickness=0,
                              bg=C_BG)
        self.lamp.pack(side=tk.LEFT)
        self._lamp_id = self.lamp.create_oval(2, 2, 12, 12, fill="#cbd5e1",
                                              outline="")
        self.var_link_text = tk.StringVar(value="未监听")
        ttk.Label(state, textvariable=self.var_link_text).pack(
            side=tk.LEFT, padx=(6, 0))
        self.var_rx = tk.StringVar(value="收帧 0 · 应答 0")
        ttk.Label(side, textvariable=self.var_rx, style="Sub.TLabel"
                  ).pack(anchor="w", padx=2)

        # ---- 主站调试链路 ----
        link = ttk.LabelFrame(side, text="调试主站链路", padding=10)
        link.pack(fill=tk.X, pady=(10, 0))
        self.var_link_mode = tk.StringVar(value="loop")
        ttk.Radiobutton(link, text="内置回环(不需串口)",
                        variable=self.var_link_mode, value="loop",
                        command=self._on_link_change).pack(anchor="w")
        ttk.Radiobutton(link, text="串口(需串口对另一端)",
                        variable=self.var_link_mode, value="serial",
                        command=self._on_link_change).pack(anchor="w")
        self.var_master_port = tk.StringVar(value="")
        self.cmb_master = ttk.Combobox(link, textvariable=self.var_master_port,
                                       values=[], width=24)
        self.cmb_master.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(link, text="提示: 上位机连 COM4 时本软件监听 COM5;\n"
                             "串口自测需再备一对虚拟串口。",
                  style="CardMuted.TLabel", justify=tk.LEFT).pack(
            anchor="w", pady=(6, 0))

        tip = ttk.LabelFrame(side, text="课堂联调步骤", padding=8)
        tip.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        tk.Label(tip, justify=tk.LEFT, anchor="nw", bg=C_PANEL,
                 fg=C_GRAY, font=(self.FONT, 9), wraplength=285,
                 text=("1. 用 com0com 建虚拟串口对, 如 COM4<->COM5;\n"
                       "2. 本软件监听 COM5, 8E1/9600, 点击[开始监听];\n"
                       "3. 打开上位机选 COM4 并连接, 即可读实时量、\n"
                       "   远程合分闸、读写参数、读事件记录;\n"
                       "4. 在[实时监测]注入漏电/过载, 观察上位机\n"
                       "   告警、跳闸与事件记录;\n"
                       "5. 在[综合实训台]手工改帧(含校验码), 观察\n"
                       "   设备丢帧、异常应答或校验失败。")).pack(
            fill=tk.BOTH, expand=True)
        self._on_link_change()

    def _refresh_ports(self):
        ports = available_ports()
        self.port_map = {}
        labels = []
        for dev, desc in ports:
            label = "%s  %s" % (dev, desc[:28])
            self.port_map[label] = dev
            self.port_map[dev] = dev
            labels.append(label)
        self.cmb_port.configure(values=labels)
        self.cmb_master.configure(values=labels)
        if labels and not self.var_master_port.get():
            self.var_master_port.set(labels[-1])
        if not labels:
            self.var_status.set("未发现串口: 可先用[内置回环]做协议实验")
        else:
            self.var_status.set("发现 %d 个串口, 请选择虚拟串口对的一端后"
                                "点击[开始监听]" % len(labels))

    def _port_name(self, text):
        return self.port_map.get(text, str(text).split()[0])

    def _on_link_change(self):
        serial_mode = self.var_link_mode.get() == "serial"
        self.cmb_master.configure(state=tk.NORMAL if serial_mode
                                  else tk.DISABLED)
        if self.master_link is not None:
            try:
                self.master_link.close()
            except Exception:
                pass
            self.master_link = None


    # ------------------------------------------------------------------
    # 从站服务启停
    # ------------------------------------------------------------------
    def _start_server(self):
        if self.server is not None and self.server.is_open:
            return
        port = self._port_name(self.var_port.get())
        if not port:
            messagebox.showwarning("缺少端口", "请先选择串口(虚拟串口对的一端)。")
            return
        try:
            self.device.addr = P.encode_addr(self.var_slave_addr.get())
        except ValueError as exc:
            messagebox.showerror("地址错误", str(exc))
            return
        try:
            self.server = SlaveServer(
                self.device, port,
                baudrate=int(self.var_baud.get()),
                bytesize=int(self.var_bytesize.get()),
                parity=self.var_parity.get(),
                stopbits=self.var_stopbits.get(),
                on_event=self._server_event)
        except Exception as exc:
            messagebox.showerror("打开串口失败",
                                 "%s\n\n请确认串口未被占用, 且已安装 pyserial。"
                                 % exc)
            return
        self.server.start()
        self.btn_listen.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.var_link_text.set("监听中: %s" % port)
        self.lamp.itemconfigure(self._lamp_id, fill=C_GREEN)
        self.var_status.set("从站正在监听 %s, 等待上位机请求 ..." % port)

    def _stop_server(self):
        if self.server is not None:
            self.server.stop()
            try:
                self.server.join(timeout=1.0)
            except Exception:
                pass
            self.server = None
        self.btn_listen.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        self.var_link_text.set("未监听")
        self.lamp.itemconfigure(self._lamp_id, fill="#cbd5e1")
        self.var_status.set("从站已停止")

    def _server_event(self, text):
        # 由串口线程调用: 只记录消息, 由界面轮询显示
        self._server_msg = text
        try:
            self.after(0, lambda: self.var_status.set(text))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 主站链路
    # ------------------------------------------------------------------
    def _current_link(self):
        if self.var_link_mode.get() == "loop":
            return LoopbackLink(self.device)
        if self.master_link is None:
            port = self._port_name(self.var_master_port.get())
            if not port:
                raise ValueError("请选择调试主站使用的串口")
            self.master_link = MasterSerialLink(
                port, baudrate=int(self.var_baud.get()),
                bytesize=int(self.var_bytesize.get()),
                parity=self.var_parity.get(),
                stopbits=self.var_stopbits.get())
        return self.master_link

    def _send_frame(self, frame, tag="read"):
        try:
            link = self._current_link()
        except Exception as exc:
            messagebox.showerror("链路不可用", str(exc))
            return
        threading.Thread(target=self._send_worker,
                         args=(link, bytes(frame), tag), daemon=True).start()
        self.var_status.set("已发送 %d 字节, 等待应答 ..." % len(frame))

    def _send_worker(self, link, frame, tag):
        try:
            raw = link.send(frame)
            self._master_q.put((tag, frame, bytes(raw or b""), None))
        except Exception as exc:
            self._master_q.put((tag, frame, b"", str(exc)))

    def _poll_master(self):
        try:
            while True:
                tag, frame, raw, err = self._master_q.get_nowait()
                self._handle_master_result(tag, frame, raw, err)
        except queue.Empty:
            pass
        self.after(120, self._poll_master)

    def _handle_master_result(self, tag, frame, raw, err):
        echo = T.hexs(raw) if raw else ("(无应答) %s" % (err or ""))
        box = (self.txt_echo_read if tag in ("read", "auto")
               else self.txt_echo_ctrl)
        if tag == "auto":
            self.var_test_state.set("已收到第 %d 次自动测试应答, %d 字节"
                                    % (self._auto_seq, len(raw)) if raw
                                    else "自动测试无应答: %s" % (err or "-"))
            self._auto_seq += 1
        else:
            self._set_text(box, echo + "\n")
        self._set_text(self.txt_source, T.frame_to_source(frame) + "\n")
        self.var_oper.set(T.control_explain(frame[8])["name"]
                          if len(frame) > 8 else "-")
        # 访问对象(请求帧数据域前 4 字节为 DI0-DI3)
        obj = "-"
        if len(frame) >= 14:
            try:
                data = [(b - 0x33) & 0xFF for b in frame[10:10 + frame[9]]]
                di = P.di_canon(data[0:4])
                item = T.di_info(di)
                obj = "%s (%s)" % (item["name"] if item else "未知项",
                                   T.hex4(di))
            except Exception:
                obj = "-"
        self.var_object.set(obj)
        parts, summ = T.frame_parts(frame)
        self.var_crc.set("CS=%02XH / 应为 %02XH %s"
                         % (summ["cs"] or 0, summ["cs_calc"] or 0,
                            "正确" if summ["cs"] == summ["cs_calc"]
                            else "错误"))
        self._set_text(self.txt_parse, T.describe_response(raw) + "\n")
        target = (self.txt_parse_read if tag in ("read", "auto")
                  else self.txt_parse_ctrl)
        self._set_text(target, T.describe_response(raw) + "\n")
        if raw:
            self.var_status.set("收到应答 %d 字节, 校验%s"
                                % (len(raw), "通过" if summ["ok"] else "失败"))
        else:
            self.var_status.set("无应答: %s" % (err or "从站未响应"))

    @staticmethod
    def _set_text(widget, text, replace=True):
        if replace:
            widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)


    # ------------------------------------------------------------------
    # 页签 1: 综合实训台
    # ------------------------------------------------------------------
    def _build_tab_bench(self):
        f = self.tab_bench
        for c in range(3):
            f.columnconfigure(c, weight=1, uniform="col")
        f.rowconfigure(0, weight=1)
        self._bench_read(f, 0)
        self._bench_ctrl(f, 1)
        self._bench_test(f, 2)

    def _bench_read(self, parent, col):
        box = ttk.LabelFrame(parent, text="系统参数测量", padding=10)
        box.grid(row=0, column=col, sticky="nsew", padx=(0, 8))
        box.columnconfigure(1, weight=1)

        self.var_addr_read = tk.StringVar(value="000000000001")
        self.var_param_read = tk.StringVar(value=T.di_choices("read")[0])
        self.var_cmd_read = tk.StringVar(value="")

        ttk.Label(box, text="选择设备:", style="Card.TLabel").grid(
            row=0, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(box, textvariable=self.var_addr_read,
                     values=["000000000001"], width=16).grid(
            row=0, column=1, sticky="ew", pady=4)
        ttk.Label(box, text="选择参数:", style="Card.TLabel").grid(
            row=1, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(box, textvariable=self.var_param_read,
                     values=T.di_choices("read"), width=24).grid(
            row=1, column=1, sticky="ew", pady=4)

        ttk.Button(box, text="生成指令", command=self._gen_read).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(box, text="指令(可手工修改):", style="CardMuted.TLabel"
                  ).grid(row=3, column=0, columnspan=2, sticky=tk.W)
        ttk.Entry(box, textvariable=self.var_cmd_read, font=(self.FONT, 9)
                  ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(box, text="发送指令",
                   command=lambda: self._send_entry(self.var_cmd_read,
                                                    "read")).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=4)

        ttk.Label(box, text="回显:", style="Card.TLabel").grid(
            row=6, column=0, sticky=tk.W)
        self.txt_echo_read = self._text(box, 3)
        self.txt_echo_read.grid(row=7, column=0, columnspan=2, sticky="ew")

        ttk.Label(box, text="解析:", style="Card.TLabel").grid(
            row=8, column=0, sticky=tk.W, pady=(6, 0))
        self.txt_parse_read = self._text(box, 7)
        self.txt_parse_read.grid(row=9, column=0, columnspan=2, sticky="nsew")
        box.rowconfigure(9, weight=1)
        self._bench_note(box, 10, "读数据控制码 11H：DATA=DI0..DI3；\n"
                                  "应答 91H：DATA=DI0..DI3+数据。")

    def _text(self, parent, rows):
        return tk.Text(parent, height=rows, width=16, wrap=tk.WORD,
                       font=(self.FONT, 9), bg="#f8fafc", fg=C_TEXT,
                       relief="solid", bd=1, padx=4, pady=3)


    def _bench_ctrl(self, parent, col):
        box = ttk.LabelFrame(parent, text="系统设备控制", padding=10)
        box.grid(row=0, column=col, sticky="nsew", padx=(0, 8))
        box.columnconfigure(1, weight=1)

        self.var_addr_ctrl = tk.StringVar(value="000000000001")
        self.var_param_ctrl = tk.StringVar(value=T.di_choices("control")[0])
        self.var_freq = tk.StringVar(value="50")
        self.var_delay = tk.StringVar(value="0")
        self.var_delay_unit = tk.StringVar(value="立即")
        self.var_write_value = tk.StringVar(value="")
        self.var_cmd_ctrl = tk.StringVar(value="")

        ttk.Label(box, text="选择设备:", style="Card.TLabel").grid(
            row=0, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(box, textvariable=self.var_addr_ctrl,
                     values=["000000000001"], width=16).grid(
            row=0, column=1, sticky="ew", pady=4)
        ttk.Label(box, text="选择参数:", style="Card.TLabel").grid(
            row=1, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(box, textvariable=self.var_param_ctrl,
                     values=T.di_choices("control") + T.di_choices("write"),
                     width=24).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(box, text="设定频率:", style="Card.TLabel").grid(
            row=2, column=0, sticky=tk.W, pady=4)
        rowf = ttk.Frame(box, style="Card.TFrame")
        rowf.grid(row=2, column=1, sticky="ew")
        ttk.Combobox(rowf, textvariable=self.var_freq, values=["50", "60"],
                     width=6).pack(side=tk.LEFT)
        ttk.Label(rowf, text="Hz (仿真电网频率)", style="CardMuted.TLabel"
                  ).pack(side=tk.LEFT, padx=4)

        ttk.Label(box, text="预约延迟:", style="Card.TLabel").grid(
            row=3, column=0, sticky=tk.W, pady=4)
        rowd = ttk.Frame(box, style="Card.TFrame")
        rowd.grid(row=3, column=1, sticky="ew")
        tk.Spinbox(rowd, from_=0, to=99, width=5, textvariable=self.var_delay,
                   relief="solid", bd=1).pack(side=tk.LEFT)
        ttk.Combobox(rowd, textvariable=self.var_delay_unit,
                     values=["立即", "分钟", "小时"], width=7,
                     state="readonly").pack(side=tk.LEFT, padx=4)

        ttk.Label(box, text="设定值:", style="Card.TLabel").grid(
            row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(box, textvariable=self.var_write_value).grid(
            row=4, column=1, sticky="ew", pady=4)
        ttk.Label(box, text="写参数时填 HEX 字节, 如 5A 或 01 02",
                  style="CardMuted.TLabel").grid(
            row=5, column=0, columnspan=2, sticky=tk.W)

        ttk.Button(box, text="生成指令", command=self._gen_ctrl).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(box, text="指令(可手工修改):", style="CardMuted.TLabel"
                  ).grid(row=7, column=0, columnspan=2, sticky=tk.W)
        ttk.Entry(box, textvariable=self.var_cmd_ctrl, font=(self.FONT, 9)
                  ).grid(row=8, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(box, text="发送指令",
                   command=lambda: self._send_entry(self.var_cmd_ctrl,
                                                    "ctrl")).grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=4)

        ttk.Label(box, text="回显:", style="Card.TLabel").grid(
            row=10, column=0, sticky=tk.W)
        self.txt_echo_ctrl = self._text(box, 3)
        self.txt_echo_ctrl.grid(row=11, column=0, columnspan=2, sticky="ew")

        ttk.Label(box, text="解析:", style="Card.TLabel").grid(
            row=12, column=0, sticky=tk.W, pady=(6, 0))
        self.txt_parse_ctrl = self._text(box, 6)
        self.txt_parse_ctrl.grid(row=13, column=0, columnspan=2,
                                 sticky="nsew")
        box.rowconfigure(13, weight=1)
        self._bench_note(box, 14, "控制码 1CH：DATA=DI+pwd+操作者+NN+单位；\n"
                                  "写参数 14H：DATA=DI+pwd+操作者+数据。")

    def _bench_note(self, box, row, text):
        ttk.Label(box, text=text, style="CardMuted.TLabel",
                  justify=tk.LEFT).grid(row=row, column=0, columnspan=2,
                                        sticky="w", pady=(8, 0))


    def _bench_test(self, parent, col):
        box = ttk.LabelFrame(parent, text="模拟测试系统", padding=10)
        box.grid(row=0, column=col, sticky="nsew")
        box.columnconfigure(0, weight=1)

        btns = ttk.Frame(box, style="Card.TFrame")
        btns.grid(row=0, column=0, sticky="ew")
        self.btn_test_on = ttk.Button(btns, text="启动测试",
                                      command=self._start_auto_test)
        self.btn_test_on.pack(side=tk.LEFT)
        self.btn_test_off = ttk.Button(btns, text="结束测试",
                                       style="Secondary.TButton",
                                       command=self._stop_auto_test)
        self.btn_test_off.pack(side=tk.LEFT, padx=(8, 0))
        self.var_test_state = tk.StringVar(value="未启动")
        ttk.Label(box, textvariable=self.var_test_state,
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w",
                                                 pady=(4, 6))

        ttk.Label(box, text="指令源码(field breakdown):",
                  style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.txt_source = self._text(box, 7)
        self.txt_source.grid(row=3, column=0, sticky="nsew")
        self._set_text(self.txt_source, "尚未发送指令。\n"
                                        "点击左侧[生成指令]或[启动测试]。")

        def field(row, label, var):
            ttk.Label(box, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=2)
            ttk.Entry(box, textvariable=var, state="readonly",
                      font=(self.FONT, 9)).grid(row=row + 1, column=0,
                                                sticky="ew")
            return row + 2

        self.var_object = tk.StringVar(value="-")
        self.var_oper = tk.StringVar(value="-")
        self.var_crc = tk.StringVar(value="-")
        r = field(4, "访问对象:", self.var_object)
        r = field(r, "读写操作:", self.var_oper)
        r = field(r, "CRC 校验(CS):", self.var_crc)

        ttk.Label(box, text="指令解析:", style="Card.TLabel").grid(
            row=r, column=0, sticky="w", pady=(4, 0))
        self.txt_parse = self._text(box, 8)
        self.txt_parse.grid(row=r + 1, column=0, sticky="nsew")
        box.rowconfigure(r + 1, weight=1)

        rnd = ttk.LabelFrame(box, text="随机行为", padding=6)
        rnd.grid(row=r + 2, column=0, sticky="ew", pady=(8, 0))
        self.var_random = tk.StringVar(value="关闭")
        self.var_random_prob = tk.StringVar(value="0.5")
        self.var_random_interval = tk.StringVar(value="6")
        ttk.Combobox(rnd, textvariable=self.var_random, width=10,
                     values=["关闭", "数值抖动", "随机跳闸", "混合"],
                     state="readonly").pack(side=tk.LEFT)
        ttk.Label(rnd, text="概率", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(6, 2))
        tk.Spinbox(rnd, from_=0.0, to=1.0, increment=0.1, width=4,
                   textvariable=self.var_random_prob, relief="solid",
                   bd=1).pack(side=tk.LEFT)
        ttk.Label(rnd, text="间隔(s)", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(6, 2))
        tk.Spinbox(rnd, from_=1, to=120, width=4,
                   textvariable=self.var_random_interval, relief="solid",
                   bd=1).pack(side=tk.LEFT)
        ttk.Button(rnd, text="应用", style="Secondary.TButton",
                   command=self._apply_random).pack(side=tk.LEFT, padx=6)


    # ---- 指令生成 ----
    def _gen_read(self):
        di = T.di_from_choice(self.var_param_read.get())
        if di is None:
            messagebox.showwarning("参数无效", "请从下拉列表选择参数。")
            return
        try:
            addr = P.encode_addr(self.var_addr_read.get())
        except ValueError as exc:
            messagebox.showerror("地址错误", str(exc))
            return
        frame = P.build_read_request(addr, di)
        self.var_cmd_read.set(T.hexs(frame))
        info = T.di_info(di)
        self.var_status.set("已生成读指令: %s (%s), %d 字节"
                            % (info["name"] if info else T.hex4(di),
                               T.hex4_wire(di), len(frame)))

    def _gen_ctrl(self):
        di = T.di_from_choice(self.var_param_ctrl.get())
        if di is None:
            messagebox.showwarning("参数无效", "请从下拉列表选择参数。")
            return
        try:
            addr = P.encode_addr(self.var_addr_ctrl.get())
            self.device.freq = float(self.var_freq.get())
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        info = T.di_info(di) or {"kind": "control", "name": T.hex4(di)}
        pwd = make_password(0, "000000")          # 0 级密码: 00 00 00 00
        op = make_operator("00000001")
        if info["kind"] == "control":
            data = []
            delay = int(float(self.var_delay.get() or 0))
            unit = {"分钟": 0x02, "小时": 0x03}.get(self.var_delay_unit.get())
            if delay > 0 and unit:
                data = P.bcd_encode(delay, 1) + P.bcd_encode(unit, 1)
            frame = P.build_control_request(addr, di, pwd, op, data)
        else:
            try:
                body = self._parse_hex(self.var_write_value.get())
            except ValueError as exc:
                messagebox.showerror("设定值错误", str(exc))
                return
            frame = P.build_write_request(addr, di, pwd, op, body)
        self.var_cmd_ctrl.set(T.hexs(frame))
        self.var_status.set("已生成%s: %s (%s), %d 字节"
                            % ("控制指令" if info["kind"] == "control"
                               else "写数据指令",
                               info["name"], T.hex4_wire(di), len(frame)))

    @staticmethod
    def _parse_hex(text):
        """'5A 01 02' / '0x5A' -> [0x5A,0x01,0x02]; 空串返回 [0]。"""
        s = str(text).replace("0x", " ").replace("0X", " ")
        s = s.replace(",", " ").replace("，", " ")
        parts = s.split()
        if len(parts) == 1 and len(parts[0]) > 2:
            raw = parts[0]
            parts = [raw[i:i + 2] for i in range(0, len(raw), 2)]
        out = []
        for p in parts:
            try:
                out.append(int(p, 16) & 0xFF)
            except ValueError:
                raise ValueError("无法识别的 HEX 字节: %s" % p)
        return out or [0]

    def _send_entry(self, var, tag):
        text = var.get().strip()
        if not text:
            messagebox.showwarning("没有指令", "请先生成指令或手工输入 HEX 帧。")
            return
        try:
            frame = bytes(int(x, 16) for x in text.replace(",", " ").split())
        except ValueError:
            messagebox.showerror("格式错误",
                                 "指令需为空格分隔的 HEX, 如\n"
                                 "68 01 00 00 00 00 00 68 11 04 ... 16")
            return
        self._send_frame(frame, tag)

    # ---- 自动测试 / 随机行为 ----
    def _apply_random(self):
        mode = {"关闭": "off", "数值抖动": "jitter",
                "随机跳闸": "trip", "混合": "mixed"}.get(
            self.var_random.get(), "off")
        self.device.random_mode = mode
        try:
            self.device.random_prob = float(self.var_random_prob.get())
            self.device.random_interval = float(
                self.var_random_interval.get())
        except ValueError:
            pass
        self.var_status.set("随机行为: %s, 概率 %.2f, 间隔 %.1f s"
                            % (self.var_random.get(),
                               self.device.random_prob,
                               self.device.random_interval))

    def _start_auto_test(self):
        if self._test_running:
            return
        self._test_running = True
        self.var_test_state.set("测试运行中: 循环读取实时量与状态字 ...")
        threading.Thread(target=self._auto_test_loop, daemon=True).start()
        self._apply_random()

    def _stop_auto_test(self):
        self._test_running = False
        self.var_test_state.set("已结束测试")
        self.var_status.set("自动测试已结束")

    def _auto_test_loop(self):
        seq = [(0x02, 0x01, 0xFF, 0x00), (0x02, 0x02, 0xFF, 0x00),
               (0x02, 0x90, 0xFF, 0x00), (0x04, 0x00, 0x05, 0xFF),
               (0x02, 0x91, 0xFF, 0x00), (0x04, 0x00, 0x10, 0xFF),
               (0x04, 0x00, 0x05, 0x01)]
        idx = 0
        while self._test_running:
            try:
                link = self._current_link()
                try:
                    addr = P.encode_addr(self.var_addr_read.get())
                except ValueError:
                    addr = self.device.addr
                frame = P.build_read_request(addr, seq[idx % len(seq)])
                idx += 1
                try:
                    raw = link.send(frame)
                    self._master_q.put(("auto", frame, bytes(raw or b""),
                                        None))
                except Exception as exc:
                    self._master_q.put(("auto", frame, b"", str(exc)))
            except Exception as exc:
                self.var_test_state.set("测试中断: %s" % exc)
                self._test_running = False
                break
            for _ in range(10):                  # 0.8s, 可随时停止
                if not self._test_running:
                    break
                threading.Event().wait(0.08)


    # ------------------------------------------------------------------
    # 页签 2: 实时监测
    # ------------------------------------------------------------------
    def _build_tab_monitor(self):
        f = self.tab_monitor
        f.columnconfigure(0, weight=0)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        card = ttk.LabelFrame(f, text="实时电气量(设备内部状态)", padding=10)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.lbl = {}
        self._build_electric_card(card)

        right = ttk.Frame(f)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        wave = ttk.LabelFrame(right, text="三相电流波形 / 剩余电流", padding=6)
        wave.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(wave, height=250, width=300, bg="#0b1220",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        ctrl = ttk.LabelFrame(right, text="就地操作与故障注入", padding=10)
        ctrl.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ctrl.columnconfigure(1, weight=1)

        ttk.Label(ctrl, text="就地操作:", style="Card.TLabel").grid(
            row=0, column=0, sticky=tk.W)
        ops = ttk.Frame(ctrl, style="Card.TFrame")
        ops.grid(row=0, column=1, sticky="w")
        ttk.Button(ops, text="合闸", width=8,
                   command=lambda: self._local_control("close")).pack(
            side=tk.LEFT)
        ttk.Button(ops, text="分闸", width=8, style="Danger.TButton",
                   command=lambda: self._local_control("trip")).pack(
            side=tk.LEFT, padx=6)
        ttk.Button(ops, text="试跳", width=8, style="Secondary.TButton",
                   command=lambda: self._local_control("test")).pack(
            side=tk.LEFT)
        ttk.Button(ops, text="自检", width=8, style="Secondary.TButton",
                   command=lambda: self._local_control("selftest")).pack(
            side=tk.LEFT, padx=6)

        ttk.Label(ctrl, text="注入工况:", style="Card.TLabel").grid(
            row=1, column=0, sticky=tk.W, pady=(8, 0))
        inj = ttk.Frame(ctrl, style="Card.TFrame")
        inj.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self.var_fault = tk.StringVar(value="正常工况")
        self.var_fault_phase = tk.StringVar(value="A")
        self.var_fault_value = tk.StringVar(value="")
        ttk.Combobox(inj, textvariable=self.var_fault, width=18,
                     values=[n for _, n in FAULT_KINDS],
                     state="readonly").pack(side=tk.LEFT)
        ttk.Combobox(inj, textvariable=self.var_fault_phase, width=3,
                     values=["A", "B", "C"], state="readonly").pack(
            side=tk.LEFT, padx=6)
        ttk.Entry(inj, textvariable=self.var_fault_value, width=8).pack(
            side=tk.LEFT)
        ttk.Label(inj, text="mA(漏电值)", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=4)
        ttk.Button(inj, text="注入", command=self._inject_fault).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(inj, text="恢复正常", style="Secondary.TButton",
                   command=self._clear_fault).pack(side=tk.LEFT, padx=6)


        sim = ttk.LabelFrame(ctrl, text="工况参数", padding=8)
        sim.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        sim.columnconfigure(1, weight=1)

        self.var_load = tk.DoubleVar(value=0.35)
        ttk.Label(sim, text="负荷率", style="Card.TLabel").grid(
            row=0, column=0, sticky=tk.W)
        tk.Scale(sim, from_=0.05, to=1.2, resolution=0.05,
                 orient=tk.HORIZONTAL, variable=self.var_load, showvalue=True,
                 length=180, bg=C_PANEL, highlightthickness=0).grid(
            row=0, column=1, sticky="ew")
        self.var_time_scale = tk.StringVar(value="10")
        ttk.Label(sim, text="保护时间加速", style="Card.TLabel").grid(
            row=1, column=0, sticky=tk.W)
        tk.Spinbox(sim, from_=1, to=60, width=6,
                   textvariable=self.var_time_scale, relief="solid",
                   bd=1).grid(row=1, column=1, sticky="w", pady=2)
        self.var_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(sim, text="启用保护逻辑(漏电/过载/过压/缺相自动跳闸)",
                        variable=self.var_auto).grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.var_run = tk.BooleanVar(value=True)
        ttk.Checkbutton(sim, text="设备运行(取消=停电, 电压电流为 0)",
                        variable=self.var_run).grid(
            row=3, column=0, columnspan=2, sticky="w")
        self.var_reclose = tk.StringVar(value="8")
        ttk.Label(sim, text="重合闸延时(s)", style="Card.TLabel").grid(
            row=4, column=0, sticky=tk.W)
        tk.Spinbox(sim, from_=1, to=120, width=6,
                   textvariable=self.var_reclose, relief="solid",
                   bd=1).grid(row=4, column=1, sticky="w", pady=2)

    def _build_electric_card(self, card):
        rows = [
            ("v_a", "A 相电压", "V"), ("v_b", "B 相电压", "V"),
            ("v_c", "C 相电压", "V"),
            ("i_a", "A 相电流", "A"), ("i_b", "B 相电流", "A"),
            ("i_c", "C 相电流", "A"),
            ("res", "剩余电流", "mA"), ("rated", "额定剩余电流", "mA"),
            ("rt", "极限不驱动时间", "ms"),
            ("st1", "运行状态字 1", ""), ("st2", "运行状态字 2", ""),
            ("reason", "跳闸/告警原因", ""),
        ]
        for c, text in enumerate(("项目", "数值", "单位")):
            ttk.Label(card, text=text, style="Card.TLabel",
                      font=(self.FONT, 10, "bold")).grid(
                row=0, column=c, sticky="w", padx=(16 if c == 1 else 0, 4))
        for r, (key, name, unit) in enumerate(rows, start=1):
            ttk.Label(card, text=name, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=3)
            val = tk.Label(card, text="--", bg=C_VALUE_BG, fg=C_TEXT,
                           font=(self.FONT, 11, "bold"), width=11,
                           anchor="e", padx=6, pady=2)
            val.grid(row=r, column=1, sticky="ew", padx=(16, 4))
            ttk.Label(card, text=unit, style="CardMuted.TLabel").grid(
                row=r, column=2, sticky="w", padx=(2, 0))
            self.lbl[key] = val
        card.columnconfigure(1, weight=1)

        seat = tk.Label(card, text="合闸", bg=C_GREEN, fg="#ffffff",
                        font=(self.FONT, 16, "bold"), padx=18, pady=6)
        seat.grid(row=len(rows) + 1, column=0, columnspan=3,
                  sticky="ew", pady=(12, 4))
        self.lbl_seat = seat

    # ---- 就地操作 / 故障注入 ----
    def _local_control(self, action):
        with self.device.lock:
            self.device._now = datetime.now()
            ok = self.device._do_control(action)
        self._refresh_device_view()
        self.var_status.set("就地%s: %s"
                            % ({"close": "合闸", "trip": "分闸",
                                "test": "试跳", "selftest": "自检"}[action],
                               "成功" if ok else "无变化/失败"))

    def _inject_fault(self):
        name = self.var_fault.get()
        kind = dict((n, k) for k, n in FAULT_KINDS).get(name, "none")
        value = None
        if self.var_fault_value.get().strip():
            try:
                value = float(self.var_fault_value.get())
            except ValueError:
                messagebox.showwarning("数值无效", "漏电值请输入数字(mA)。")
                return
        self.device.inject(kind, value, self.var_fault_phase.get())
        self.var_status.set("已注入工况: %s (%s相)" % (name,
                                                      self.var_fault_phase.get()))

    def _clear_fault(self):
        self.device.clear_fault()
        self.var_fault.set("正常工况")
        self.var_status.set("工况已恢复正常")


    # ------------------------------------------------------------------
    # 页签 3: 通信日志
    # ------------------------------------------------------------------
    def _build_tab_log(self):
        f = self.tab_log
        f.rowconfigure(0, weight=3)
        f.rowconfigure(1, weight=2)
        f.columnconfigure(0, weight=1)

        top = ttk.LabelFrame(f, text="报文记录(从站视角: RX=主站请求, TX=从站应答)",
                             padding=4)
        top.grid(row=0, column=0, sticky="nsew")
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)
        cols = ("time", "dir", "addr", "ctrl", "len", "data", "note")
        heads = ("时间", "方向", "地址", "控制码", "L", "整帧(HEX)", "说明")
        widths = (96, 44, 132, 130, 36, 300, 220)
        self.tree_log = ttk.Treeview(top, columns=cols, show="headings",
                                     height=12)
        for c, h, w in zip(cols, heads, widths):
            self.tree_log.heading(c, text=h)
            self.tree_log.column(c, width=w, anchor="w",
                                 stretch=(c in ("data", "note")))
        ys = ttk.Scrollbar(top, orient="vertical",
                           command=self.tree_log.yview)
        self.tree_log.configure(yscrollcommand=ys.set)
        self.tree_log.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        self.tree_log.bind("<<TreeviewSelect>>", self._on_log_select)

        bar = ttk.Frame(top)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(bar, text="清空日志", style="Secondary.TButton",
                   command=self._clear_log).pack(side=tk.LEFT)
        ttk.Label(bar, text="选中一行可查看逐字段分解与校验和累加过程",
                  style="CardMuted.TLabel").pack(side=tk.LEFT, padx=8)

        bottom = ttk.Frame(f)
        bottom.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        bottom.columnconfigure(0, weight=3)
        bottom.columnconfigure(1, weight=2)
        bottom.rowconfigure(0, weight=1)

        pbox = ttk.LabelFrame(bottom, text="帧结构分解", padding=4)
        pbox.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        pbox.rowconfigure(0, weight=1)
        pbox.columnconfigure(0, weight=1)
        pcols = ("field", "range", "value", "note")
        self.tree_parts = ttk.Treeview(pbox, columns=pcols, show="headings",
                                       height=8)
        for c, h, w in (("field", "协议域", 110), ("range", "字节范围", 80),
                        ("value", "值(HEX)", 230), ("note", "说明", 320)):
            self.tree_parts.heading(c, text=h)
            self.tree_parts.column(c, width=w, anchor="w",
                                   stretch=(c == "note"))
        self.tree_parts.grid(row=0, column=0, sticky="nsew")

        cbox = ttk.LabelFrame(bottom, text="校验和(CS)计算过程", padding=4)
        cbox.grid(row=0, column=1, sticky="nsew")
        cbox.rowconfigure(0, weight=1)
        cbox.columnconfigure(0, weight=1)
        self.txt_cs = self._text(cbox, 8)
        self.txt_cs.grid(row=0, column=0, sticky="nsew")
        self._set_text(self.txt_cs,
                       "CS = 从第一个 68H 到 CS 前一字节的算术和 mod 256\n"
                       "数据域每字节传输前 +33H, 接收后 -33H。\n"
                       "选中上方报文即可看到逐字节累加。")

    def _clear_log(self):
        for item in self.tree_log.get_children():
            self.tree_log.delete(item)

    def _on_log_select(self, _event=None):
        sel = self.tree_log.selection()
        if not sel:
            return
        raw_hex = self.tree_log.set(sel[0], "data").strip()
        for item in self.tree_parts.get_children():
            self.tree_parts.delete(item)
        if not raw_hex:
            self._set_text(self.txt_cs,
                           "该行为设备内部事件(工况注入 / 保护跳闸 / 自动重合闸),\n"
                           "没有收发报文, 因此无需做帧分解。\n"
                           "请选择 RX / TX 报文行查看字段与校验和。")
            return
        try:
            raw = bytes(int(x, 16) for x in raw_hex.split())
        except ValueError:
            return
        parts, summ = T.frame_parts(raw)
        for p in parts:
            self.tree_parts.insert("", tk.END,
                                   values=(p["name"], p["range"], p["value"],
                                           p["note"]))
        trace, total = T.checksum_trace(raw)
        lines = ["逐字节累加(下标从 0 开始, 到 CS 前一字节):"]
        run = 0
        for idx, b in trace:
            run = (run + b) & 0xFF
            lines.append("  [%2d] %02XH  累加=%02XH" % (idx, b, run))
        lines.append("最终 CS = %02XH" % total)
        if summ["cs"] is not None:
            lines.append("帧内 CS = %02XH -> %s"
                         % (summ["cs"],
                            "一致, 校验通过" if summ["cs"] == total
                            else "不一致, 校验失败"))
        self._set_text(self.txt_cs, "\n".join(lines))


    # ------------------------------------------------------------------
    # 页签 4: 协议教学
    # ------------------------------------------------------------------
    def _build_tab_teach(self):
        f = self.tab_teach
        f.columnconfigure(0, weight=2)
        f.columnconfigure(1, weight=3)
        f.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(f, text="DL/T 645 帧格式与编码规则", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.txt_teach = self._text(left, 30)
        self.txt_teach.grid(row=0, column=0, sticky="nsew")
        self._set_text(self.txt_teach, FRAME_TEXT)

        right = ttk.Notebook(f)
        right.grid(row=0, column=1, sticky="nsew")
        self._teach_bits(right)
        self._teach_di(right)

    def _teach_bits(self, nb):
        tab = ttk.Frame(nb, padding=4)
        nb.add(tab, text="控制字 / 状态字位表")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        cols = ("word", "bit", "name", "value")
        tree = ttk.Treeview(tab, columns=cols, show="headings")
        for c, h, w in (("word", "字/字节", 90), ("bit", "位", 70),
                        ("name", "功能", 200), ("value", "取值含义", 300)):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w", stretch=(c == "value"))
        ys = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")

        rows = []
        for idx, table in ((1, T.CTRL1_TABLE), (2, T.CTRL2_TABLE),
                           (3, T.CTRL3_TABLE)):
            for shift, width, name, mapping in table:
                bits = "%d" % shift if width == 1 else \
                    "%d-%d" % (shift + width - 1, shift)
                val = ", ".join("%d=%s" % (k, v)
                                for k, v in sorted(mapping.items()))
                rows.append(("控制字%d" % idx, bits, name, val))
        rows += [
            ("控制字4", "7-4", "额定剩余电流动作值档位",
             "0000=1档 ... 0111=8档, 1111=连续可调"),
            ("控制字4", "3-2", "额定极限不驱动时间档位",
             "00=1档, 01=2档, 10=3档, 11=连续可调"),
            ("控制字4", "1-0", "剩余电流保护",
             "00=不跳不告警, 01=不跳闸告警, 10=跳闸不告警"),
            ("控制字5", "7-4", "保留", "-"),
            ("控制字5", "3-0", "特征字", "厂商自定义"),
            ("状态字1", "7", "保留", "-"),
            ("状态字1", "6-5", "闸位状态", "00=分闸, 01=合闸"),
            ("状态字1", "4-0", "跳闸/告警原因",
             "00=正常, 02=剩余电流, 05=过载, 06=短路短延时, 07=缺相, "
             "08=欠压, 09=过压, 0B=停电, 0D=远程试验, 16=短路瞬时, "
             "1E=软遥控"),
            ("状态字2", "7", "自检事件标识", "1=有新事件"),
            ("状态字2", "6", "异常告警发生/恢复", "1=有变化"),
            ("状态字2", "5", "闸位状态变化", "1=有变化"),
            ("状态字2", "4", "保护功能退出/恢复", "1=有变化"),
            ("状态字2", "3", "高压线路失/复电", "1=有变化"),
            ("状态字2", "2", "保护动作标识", "1=有新保护动作"),
            ("状态字2", "1-0", "保留", "-"),
        ]
        for r in rows:
            tree.insert("", tk.END, values=r)

    def _teach_di(self, nb):
        tab = ttk.Frame(nb, padding=4)
        nb.add(tab, text="数据标识(DI)目录")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        cols = ("name", "di", "len", "kind", "note")
        tree = ttk.Treeview(tab, columns=cols, show="headings")
        heads = ("名称", "DI(DI3..DI0)", "长度", "类别", "说明")
        widths = (210, 100, 52, 60, 280)
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w",
                        stretch=(c in ("name", "note")))
        ys = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        kind_name = {"read": "读", "write": "写", "control": "控制"}
        for d in T.DI_CATALOG:
            tree.insert("", tk.END, values=(d["name"], T.hex4(d["di"]),
                                            d["len"] or "-",
                                            kind_name.get(d["kind"], "读"),
                                            d["note"]))




    # ------------------------------------------------------------------
    # 轮询与界面刷新
    # ------------------------------------------------------------------
    def _poll_device(self):
        try:
            self.device.running = bool(self.var_run.get())
            self.device.auto_protect = bool(self.var_auto.get())
            self.device.load_rate = max(float(self.var_load.get()), 0.01)
            self.device.time_scale = max(
                float(self.var_time_scale.get() or 10), 1.0)
            self.device.reclose_delay = max(
                float(self.var_reclose.get() or 8), 1.0)
            self.device.freq = float(self.var_freq.get() or 50)
            self.device.tick()
            self._apply_snapshot(self.device.snapshot())
        except Exception:
            pass
        self.var_clock.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.after(200, self._poll_device)

    def _refresh_device_view(self):
        self._apply_snapshot(self.device.snapshot())

    def _apply_snapshot(self, s):
        self._last_snap = s
        v3, i3 = s["v3"], s["i3"]
        for key, text in (("v_a", "%.1f" % v3[0]), ("v_b", "%.1f" % v3[1]),
                          ("v_c", "%.1f" % v3[2]),
                          ("i_a", "%.2f" % i3[0]), ("i_b", "%.2f" % i3[1]),
                          ("i_c", "%.2f" % i3[2]),
                          ("res", "%.0f" % s["residual"]),
                          ("rated", "%d" % s["rated_res"]),
                          ("rt", "%d" % s["rated_t"]),
                          ("st1", "%02XH" % s["status1"]),
                          ("st2", "%02XH" % s["status2"])):
            self.lbl[key].configure(text=text)
        self.lbl["reason"].configure(
            text=T.decode_status1(s["status1"]).split("原因=")[-1])
        if s["switch"]:
            self.lbl_seat.configure(text="合  闸", bg=C_GREEN)
        else:
            self.lbl_seat.configure(text="分  闸", bg=C_RED)
        self.var_rx.set("收帧 %d · 应答 %d · 校验错 %d · 丢帧 %d"
                        % (s["rx"], s["tx"], s["cs_err"], s["drop"]))
        self.var_stats.set("收帧 %d / 应答 %d / 校验错 %d / 丢弃 %d"
                           % (s["rx"], s["tx"], s["cs_err"], s["drop"]))

    def _poll_audit(self):
        entries = list(self.device.audit)
        if entries:
            idx = None
            if self._last_audit is not None:
                idx = next((i for i, x in enumerate(entries)
                            if x is self._last_audit), None)
            start = 0 if idx is None else idx + 1
            for e in entries[start:]:
                self._insert_log(e)
            self._last_audit = entries[-1]
        self.after(300, self._poll_audit)

    def _insert_log(self, e):
        raw = e["raw"]
        if raw and len(raw) >= 12:
            parts, summ = T.frame_parts(raw)
            addr = summ["addr"] or "-"
            ctrl = ("%02XH %s" % (raw[8], T.control_explain(raw[8])["name"])
                    if len(raw) > 8 else "-")
            length = str(summ["length"]) if summ["length"] is not None else "-"
        else:
            addr, ctrl, length = "-", "-", "-"
        tag = {"rx": "RX", "tx": "TX"}.get(e["dir"], "事件")
        data = T.hexs(raw) if raw else ""
        self.tree_log.insert("", tk.END, values=(
            e["ts"], tag, addr, ctrl, length, data, e["note"]))
        children = self.tree_log.get_children()
        if len(children) > 400:
            self.tree_log.delete(children[0])
        self.tree_log.see(children[-1])

    # ------------------------------------------------------------------
    # 波形动画
    # ------------------------------------------------------------------
    def _anim(self):
        self._wave_t += 0.06
        s = getattr(self, "_last_snap", None)
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = max(c.winfo_height(), 120)
        mid = h // 2
        for k in range(1, 4):
            c.create_line(0, mid - k * 34, w, mid - k * 34, fill="#1e293b")
            c.create_line(0, mid + k * 34, w, mid + k * 34, fill="#1e293b")
        c.create_line(0, mid, w, mid, fill="#334155")
        if s is None:
            self.after(100, self._anim)
            return
        i3 = s["i3"]
        ir1 = max(s["ir1"], 1.0)
        amp = [min(v / max(ir1 * 0.6, 1.0), 1.5) * 70 for v in i3]
        colors = ("#f87171", "#4ade80", "#60a5fa")
        labels = ("A", "B", "C")
        if s["switch"]:
            for k in range(3):
                pts = []
                for x in range(0, w, 3):
                    y = mid - amp[k] * math.sin(
                        2 * math.pi * (x / 120.0) + k * 2.094)
                    pts += [x, y]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=colors[k], width=2)
            for k in range(3):
                c.create_text(10, 10 + k * 17, anchor="nw",
                              text="%s相 %.2f A" % (labels[k], i3[k]),
                              fill=colors[k], font=(self.FONT, 9))
        else:
            c.create_text(w // 2, mid - 40, text="分闸状态 · 无负荷电流",
                          fill="#94a3b8", font=(self.FONT, 13, "bold"))
        res = s["residual"]
        rated = max(s["rated_res"], 1)
        ratio = min(res / rated, 1.5)
        bar_w = int((w - 40) * min(ratio / 1.5, 1.0))
        y0 = h - 34
        c.create_rectangle(20, y0, w - 20, y0 + 14, fill="#1e293b", outline="")
        color = "#f59e0b" if ratio < 1 else "#ef4444"
        c.create_rectangle(20, y0, 20 + bar_w, y0 + 14, fill=color,
                           outline="")
        c.create_text(22, y0 - 10, anchor="w",
                      text="剩余电流 %.0f mA / 额定 %d mA"
                           % (res, s["rated_res"]),
                      fill="#e2e8f0", font=(self.FONT, 9))
        state = "合闸" if s["switch"] else "分闸"
        c.create_text(w - 16, 16, anchor="ne",
                      text="%s · 状态字1 %02XH" % (state, s["status1"]),
                      fill="#e2e8f0", font=(self.FONT, 10, "bold"))
        self.after(100, self._anim)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def destroy(self):
        self._test_running = False
        try:
            self._stop_server()
        except Exception:
            pass
        if self.master_link is not None:
            try:
                self.master_link.close()
            except Exception:
                pass
            self.master_link = None
        super().destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()


FRAME_TEXT = """一、字节格式(物理层 8E1)
    每字节 8 位数据 + 1 位偶校验 + 1 位停止位, 先传低位 D0..D7。
    缺省速率 9600 bps, RS-485 半双工, 一主多从。

二、帧格式(数据链路层)
    68H | A0-A5 | 68H | C | L | DATA | CS | 16H
    起始符      68H, 固定
    地址域      A0-A5, 6 字节 BCD, 低字节在前, 12 位十进制
                999999999999=广播; AAH=缩位寻址通配符
    起始符      68H, 固定
    控制码 C    D7 方向 / D6 应答 / D5 后续 / D4-D0 功能
    长度 L      DATA 的字节数(不含 CS/16H)
    数据域      DI0-DI3(标识码, 低字节在前) + 密码 + 操作者 + 数据
                传输时每字节 +33H, 接收时 -33H
    校验 CS     从第一个 68H 到 CS 前一字节的算术和 mod 256
    结束符      16H, 固定

三、常用控制码
    11H 读数据        91H 读数据应答      D1H 读异常应答
    14H 写数据        94H 写数据应答      D4H 写异常应答
    1CH 控制指令      9CH 控制应答        DCH 控制异常应答
    1BH 数据清零      9BH 清零应答        DBH 清零异常
    18H 修改密码      98H 改密应答        D8H 改密异常
    13H 读通信地址    93H 地址应答
    08H 广播校时      (从站不应答)

四、异常应答信息字(数据域首字节)
    01H 数据非法      02H 无请求数据项
    04H 密码错/未授权  08H 速率不能更改
    10H 远程控制失败   20H 升级包错误

五、读数据的过程(以三相电压 0201FF00 为例)
    请求: 68 A0..A5 68 11 04 [DI0 DI1 DI2 DI3]+33H CS 16
    应答: 68 A0..A5 68 91 0A [DI0..DI3 + 6 字节电压]+33H CS 16
    电压: A/B/C 各 2 字节 BCD, 低字节在前, 单位 0.1V
        例 45 08 -> 0x0845 -> 212.1 V

六、数值量 vs 分量型
    数值量(电压/电流/剩余电流/整定值): 多字节低字节在前
    分量型(日期 YYMMDDWW、时间 hhmmss、密码、操作者、自检 DDhhmm):
        按书写顺序传输, 不做字节倒置
"""
