# -*- coding: utf-8 -*-
"""GUI 远程监控软件 —— DL/T 645 扩展规约 剩余电流保护断路器(带自动重合闸)。

功能模块:
    实时监测   三相电压/电流/剩余电流/额定值/运行状态字
    远程控制   远程合闸/分闸/试跳/自检(支持预约执行)
    参数设置   控制字1-5、整定参数(电流/电压/预警/自检)
    事件与统计 跳闸次数、事件记录读取、数据清零
    密码管理   修改各级别密码(0-2级)
    通信日志   记录收发帧(HEX)与操作结果

通信模式(界面顶部单选):
    串口模式   SerialTransport(RS-485, 8E1) 连接真实设备
    仿真模式   VirtualTransport + 内置虚拟断路器, 无需硬件即可演示全部功能
"""

import math
import queue
import threading
from datetime import datetime

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

try:
    from serial.tools import list_ports   # 用于串口自动扫描, 缺失时需手动填写端口
except Exception:
    list_ports = None

from . import protocol as P
from . import simulator as S
from .device import (BreakerClient, SerialTransport, VirtualTransport,
                     make_password, make_operator)

REASON_TABLE = S.REASON_TABLE

# 跳闸次数模块(038100FF) 0x00-0x0B 按规约表A.2.2的分类, 与故障原因编码不同
TRIP_COUNT_NAMES = [
    "总跳闸次数",
    "剩余电流闭锁跳闸",
    "剩余电流保护跳闸",
    "过载保护跳闸",
    "过压保护跳闸",
    "手动跳闸",
    "缺零保护跳闸",
    "试验跳闸",
    "短路短延时跳闸",
    "短路瞬时跳闸",
    "欠压保护跳闸",
    "缺相保护跳闸",
]

# ---------------------------------------------------------------------------
# 主题 / 美化
# ---------------------------------------------------------------------------
C_BG, C_PANEL, C_LINE = "#eef1f6", "#ffffff", "#d7dde8"
C_ACCENT, C_ACCENT2 = "#2563eb", "#1d4ed8"
C_GREEN, C_RED, C_ORANGE = "#16a34a", "#dc2626", "#ea580c"
C_GRAY, C_MUTED, C_TEXT = "#475569", "#94a3b8", "#0f172a"


def pick_font(*names):
    import tkinter.font as tkfont
    fams = set(tkfont.families())
    for n in names:
        if n in fams:
            return n
    return "TkDefaultFont"


def apply_theme(root):
    """全局 ttk 样式: 现代浅色主题(蓝白)。"""
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
                    font=(font, 13, "bold"))
    style.configure("H2.TLabel", background=C_PANEL, foreground=C_TEXT,
                    font=(font, 10, "bold"))
    # 卡片内普通标签: 与卡片同为白色底, 避免露出页面灰底造成"空白"观感
    style.configure("Card.TLabel", background=C_PANEL, foreground=C_TEXT)
    style.configure("CardMuted.TLabel", background=C_PANEL,
                    foreground=C_MUTED)
    # 卡片内部容器: 白底, 避免嵌套 TFrame 露出灰色页面底
    style.configure("Card.TFrame", background=C_PANEL)
    style.configure("TLabelframe", background=C_PANEL, bordercolor=C_LINE,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=C_PANEL,
                    foreground=C_ACCENT, font=(font, 10, "bold"))
    style.configure("TButton", background=C_ACCENT, foreground="#ffffff",
                    padding=(12, 6), borderwidth=0, focusthickness=0,
                    font=(font, 10))
    style.map("TButton", background=[("pressed", C_ACCENT2),
                                     ("active", C_ACCENT2)])
    style.configure("Secondary.TButton", background="#e2e8f0",
                    foreground=C_TEXT)
    style.map("Secondary.TButton",
              background=[("pressed", "#cbd5e1"), ("active", "#cbd5e1")])
    style.configure("Danger.TButton", background=C_RED, foreground="#fff")
    style.map("Danger.TButton", background=[("pressed", "#b91c1c"),
                                            ("active", "#b91c1c")])
    style.configure("TNotebook", background=C_BG, borderwidth=0)
    style.configure("TNotebook.Tab", background="#dbe2ee", foreground=C_TEXT,
                    padding=(14, 8), font=(font, 10))
    style.map("TNotebook.Tab", background=[("selected", C_PANEL)],
              foreground=[("selected", C_ACCENT)])
    style.configure("TEntry", fieldbackground="#ffffff", bordercolor=C_LINE,
                    padding=4)
    style.configure("TCombobox", fieldbackground="#ffffff",
                    bordercolor=C_LINE, padding=4)
    style.configure("TSpinbox", fieldbackground="#ffffff",
                    bordercolor=C_LINE, padding=4)
    style.configure("TCheckbutton", background=C_BG)
    style.configure("Status.TLabel", background=C_BG, foreground=C_GRAY)


# ---------------------------------------------------------------------------
# 通用解码辅助
# ---------------------------------------------------------------------------
def bcd_int(data):
    """BCD字节序列 -> 整数(分量型字段: 日期时间/密码等按书写顺序)。"""
    return P.bcd_decode(list(data))


def bcd_num(data):
    """多字节BCD数值量(DL645 低字节在前) -> 整数。

    电压/电流/剩余电流/额定值/累计次数/整定参数等数值量在线上低字节在前;
    日期时间(YYMMDDhhmmss / hhmmss / DDhhmm 等分量型)字段仍用 bcd_int()。
    """
    return P.bcd_decode_lf(list(data))


def ascii_str(data):
    """ASCII数据(去掉尾随0x20/0x00)。"""
    return "".join(chr(b) for b in data if b) \
        .rstrip(" ").strip("\x00")


def dec_status1(v):
    """运行状态字1: bit6-5 闸位, bit4-0 跳闸/告警原因。"""
    pos = (v >> 5) & 0x03
    reason = v & 0x1F
    pos_txt = {0: "分闸", 1: "合闸", 2: "故障跳闸", 3: "未知"}.get(pos, "未知")
    return "闸位:%s | 原因:%s" % (pos_txt, REASON_TABLE.get(reason,
                                                           "未知(0x%02X)" % reason))


def dec_status2(v):
    """运行状态字2: 各事件新增标识(规约表B.1.2)。"""
    bits = [
        (0x80, "自检事件"),
        (0x40, "异常告警"),
        (0x20, "闸位变化"),
        (0x10, "保护投退"),
        (0x08, "高压失/复电"),
        (0x04, "保护动作"),
    ]
    marks = [t for m, t in bits if v & m]
    return ("新增: " + "、".join(marks)) if marks else "无新增事件"


def fmt_dt6(b):
    """6字节 BCD 时间 -> 'YY-MM-DD HH:MM:SS'。"""
    s = "%012d" % bcd_int(b)
    return "%s-%s-%s %s:%s:%s" % (s[0:2], s[2:4], s[4:6],
                                  s[6:8], s[8:10], s[10:12])


def bcd_time(b):
    """bcd_time 与 fmt_dt6 等价, 用于事件记录解码。"""
    return fmt_dt6(b)


# ---------------------------------------------------------------------------
# 日志传输包装: 记录每一帧收发
# ---------------------------------------------------------------------------
class LoggingTransport:
    def __init__(self, inner, log_cb):
        self._inner = inner
        self._log = log_cb

    def send(self, frame):
        self._log("TX", frame.hex())
        resp = self._inner.send(frame)
        self._log("RX", resp.hex())
        return resp

    def close(self):
        try:
            self._inner.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 后台通信线程: 串行执行设备操作, 结果放入队列由 UI 轮询
# ---------------------------------------------------------------------------
class CommWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="comm-worker")
        self._cmd_q = queue.Queue()
        self.results = queue.Queue()
        self.client = None
        self.connected = False
        self.fail_streak = 0          # 连续读取失败次数(成功即清零)
        self.start()

    def run(self):
        while True:
            fn = self._cmd_q.get()
            try:
                fn(self)
            except Exception as exc:
                self.results.put(("error", str(exc)))

    def submit(self, fn):
        self._cmd_q.put(fn)

    def _log(self, direction, hexstr):
        self.results.put(("frame", direction, hexstr))

    # ---- 连接 / 断开 ----
    def connect(self, addr, port, baud, mode="serial"):
        def _op(w):
            try:
                if w.client is not None:
                    w.client.transport.close()
            except Exception:
                pass
            w.client = None
            w.connected = False
            try:
                if mode == "sim":
                    # 仿真模式: 用内置虚拟断路器代替真实串口, 无硬件也能演示
                    dev = S.VirtualBreaker(addr)
                    inner = VirtualTransport(dev)
                else:
                    inner = SerialTransport(port, baud)
                w.client = BreakerClient(LoggingTransport(inner, w._log), addr)
            except Exception as exc:
                # 打开串口失败(端口选错/不存在/被占用等): 上报专用事件, 让 UI 解锁按钮
                w.results.put(("connect_failed", str(exc)))
                return
            w.fail_streak = 0
            w.connected = True
            w.results.put(("connected", mode))
        self.submit(_op)

    def disconnect(self):
        def _op(w):
            try:
                if w.client is not None:
                    w.client.transport.close()
            except Exception:
                pass
            w.client = None
            w.connected = False
            w.results.put(("disconnected",))
        self.submit(_op)

    # ---- 读数据(带 di 回传) ----
    def read(self, di):
        def _op(w):
            try:
                data = w.client.read(di)
            except Exception:
                w.fail_streak += 1
                raise                     # 交由 run() 上报 error
            w.fail_streak = 0
            w.results.put(("read", di, data))
        self.submit(_op)

    # ---- 读事件记录(带 di 回传, 与普通读区分) ----
    def read_event(self, di):
        def _op(w):
            data = w.client.read(di)
            w.results.put(("event", di, data))
        self.submit(_op)

    # ---- 通用操作: 控制/写/清零/改密 ----
    def op(self, fn, tag):
        def _op(w):
            fn(w.client)
            w.results.put(("op", tag, True))
        self.submit(_op)
# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("低压塑壳断路器远程控制软件")
        self.geometry("1200x780")
        self.minsize(1040, 700)
        apply_theme(self)
        self.configure(bg=C_BG)
        self._set_icon()
        self.FONT = pick_font("Microsoft YaHei UI", "Noto Sans CJK SC",
                              "PingFang SC", "WenQuanYi Micro Hei",
                              "Segoe UI")

        self.worker = CommWorker()
        # ---- 动画 / 实时状态缓存 ----
        self._st1 = 0x20
        self._residual = 0.0
        self._rated_res = 300
        self._i3 = (0.0, 0.0, 0.0)
        self._v3 = (0.0, 0.0, 0.0)
        self._anim_tick_no = 0
        self._arc_left = None            # 自动重合闸倒计时(s), None=未计时
        self._stale = False              # 设备无应答, 监测数值已清零
        self._exclusive = 0              # >0 时暂停监测轮询(密码等独占操作)
        self._mode = "serial"            # 当前通信模式: serial(串口) / sim(仿真)

        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()
        self._refresh_ports()            # 启动时自动扫描一次可用串口
        self._on_mode_change()           # 依据默认模式设置端口控件可用性

        self.after(120, self._drain)
        self.after(1500, self._poll)
        self.after(100, self._anim_tick)

    def _set_icon(self):
        """设置窗口/任务栏图标; 打包成单文件后从临时目录读取内嵌的 app.ico。"""
        try:
            import os
            import sys
            if getattr(sys, "frozen", False):
                ico = os.path.join(getattr(sys, "_MEIPASS", ""), "app.ico")
            else:
                ico = os.path.abspath(os.path.join(
                    os.path.dirname(__file__), "..", "app.ico"))
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

    # ==================== 界面构建 ====================
    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        self.var_addr = tk.StringVar(value="000000000001")
        self.var_port = tk.StringVar(value="")
        self.var_baud = tk.StringVar(value="9600")
        self.var_pwd_level = tk.StringVar(value="0")
        self.var_pwd_code = tk.StringVar(value="000000")
        self.var_oper = tk.StringVar(value="00000000")

        # ---- 第1行: 通信模式 串口(真实设备) / 仿真(无硬件演示) ----
        row1 = ttk.Frame(bar)
        row1.pack(fill=tk.X)
        self.var_mode = tk.StringVar(value="serial")
        ttk.Label(row1, text="通信模式:").pack(side=tk.LEFT)
        ttk.Radiobutton(row1, text="串口(真实设备)", value="serial",
                        variable=self.var_mode,
                        command=self._on_mode_change).pack(side=tk.LEFT,
                                                           padx=(4, 0))
        ttk.Radiobutton(row1, text="仿真(无设备演示)", value="sim",
                        variable=self.var_mode,
                        command=self._on_mode_change).pack(side=tk.LEFT)

        # ---- 第2行: 地址 / 端口 / 波特率 / 连接 ----
        row2 = ttk.Frame(bar)
        row2.pack(fill=tk.X, pady=(6, 0))

        ttk.Label(row2, text="通信地址:").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.var_addr, width=14
                  ).pack(side=tk.LEFT)

        ttk.Label(row2, text="端口:").pack(side=tk.LEFT, padx=(10, 2))
        self.port_entry = ttk.Combobox(row2, textvariable=self.var_port,
                                       width=34)
        self.port_entry.pack(side=tk.LEFT, padx=(0, 2))
        self.btn_refresh = ttk.Button(row2, text="刷新", width=4,
                                      command=self._refresh_ports)
        self.btn_refresh.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(row2, text="波特率:").pack(side=tk.LEFT)
        self.baud_entry = ttk.Combobox(row2, textvariable=self.var_baud,
                                       width=7,
                                       values=["1200", "2400", "4800", "9600",
                                               "19200", "38400"])
        self.baud_entry.pack(side=tk.LEFT)

        self.btn_connect = ttk.Button(row2, text="连接设备",
                                      command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=(12, 4))
        self.btn_disconnect = ttk.Button(row2, text="断开",
                                         command=self._on_disconnect,
                                         state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT)

    def _on_mode_change(self):
        """切换串口/仿真模式: 仿真模式禁用端口与波特率控件。"""
        sim = self.var_mode.get() == "sim"
        state = tk.DISABLED if sim else tk.NORMAL
        self.port_entry.configure(state=state)
        self.btn_refresh.configure(state=state)
        self.baud_entry.configure(state=state)
        if sim:
            self.var_status.set(
                "仿真模式: 无需硬件, 点击[连接设备]即可演示全部功能。")
        else:
            self.var_status.set("串口模式: 请选择串口后点击[连接设备]。")

    def _build_notebook(self):
        nb = ttk.Notebook(self)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(4, 0))

        self.tab_monitor = ttk.Frame(nb, padding=10)
        self.tab_params = ttk.Frame(nb, padding=10)
        self.tab_events = ttk.Frame(nb, padding=10)
        self.tab_pwd = ttk.Frame(nb, padding=10)
        self.tab_log = ttk.Frame(nb, padding=6)

        nb.add(self.tab_monitor, text="实时监测与控制")
        nb.add(self.tab_params, text="参数设置")
        nb.add(self.tab_events, text="事件与统计")
        nb.add(self.tab_pwd, text="密码管理")
        nb.add(self.tab_log, text="通信日志")

        self._build_tab_monitor()
        self._build_tab_params()
        self._build_tab_events()
        self._build_tab_pwd()
        self._build_tab_log()

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.var_status = tk.StringVar(value="未连接。请选择串口后点击[连接设备]。")
        ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel"
                  ).pack(side=tk.LEFT)
        self.var_clock = tk.StringVar(value=datetime.now().strftime("%H:%M:%S"))
        ttk.Label(bar, textvariable=self.var_clock).pack(side=tk.RIGHT)

    # ==================== 实时监测 ====================
    def _build_tab_monitor(self):
        """实时监测页: 两列栅格布局, 自适应铺满整个标签页(无空白背景)。

        列1: 实时电气量(上, 按内容高度) + 三相电路动画(正下方, 铺满余下高度)
        列2: 设备信息 + 远程控制(定宽)
        """
        f = self.tab_monitor
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)                # 左列: 电气量 + 动画
        f.columnconfigure(1, weight=0, minsize=368)   # 右列: 信息 + 控制

        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=0)                # 电气量: 按内容高度
        left.rowconfigure(1, weight=1, minsize=300)   # 动画: 占满余下高度
        left.columnconfigure(0, weight=1)
        self._build_values_card(left)
        self._build_anim_panel(left)

        right = ttk.Frame(f)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self._build_info_card(right)
        self._build_control_panel(right)

    def _card(self, parent, title):
        """统一样式卡片: 带标题面板。"""
        return ttk.LabelFrame(parent, text=" %s " % title, padding=10)

    def _build_values_card(self, parent):
        """实时电气量卡片: 标题/数值同行紧凑对齐, 内附说明(消除底部游离空白)。"""
        box = self._card(parent, "实时电气量")
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        box.columnconfigure(2, weight=1)
        box.columnconfigure(3, weight=1)

        self.lb = {}
        self.lb_w = {}
        rows = [
            ("三相电压", "V  (相电压)", 3),
            ("三相电流", "A  (相电流)", 3),
            ("剩余电流", "mA", 1),
            ("额定动作值", "mA / ms", 2),
            ("运行状态字1", "", 1),
            ("运行状态字2", "", 1),
        ]
        for gr, (title, unit, n) in enumerate(rows):
            ttk.Label(box, text=title, style="H2.TLabel").grid(
                row=gr, column=0, sticky=tk.W, padx=(0, 8), pady=1)
            span = 4 if title.startswith("运行状态字") else 1
            for c in range(n):
                var = tk.StringVar(value="--")
                self.lb[(title, c)] = var
                lbl = ttk.Label(box, textvariable=var, style="Card.TLabel",
                                font=(self.FONT, 10, "bold"), anchor=tk.W)
                lbl.grid(row=gr, column=1 + c, columnspan=span,
                         sticky="ew", padx=(2, 0))
                self.lb_w[(title, c)] = lbl
            if unit:
                ttk.Label(box, text=unit, style="CardMuted.TLabel").grid(
                    row=gr, column=4, sticky=tk.W, padx=(8, 0))

        ttk.Separator(box, orient="horizontal").grid(
            row=len(rows), column=0, columnspan=5, sticky="ew",
            pady=(6, 4))
        ttk.Label(box, style="CardMuted.TLabel", justify=tk.LEFT,
                  wraplength=720,
                  text=("状态字1: bit6-5=闸位(00分/01合), bit4-0=跳闸原因; "
                        "额定值由控制字4档位决定。\n"
                        "剩余电流: 寄存器0290FF00(最大相及剩余电流值)。")
                  ).grid(row=len(rows) + 1, column=0, columnspan=5,
                         sticky="w")

    def _build_info_card(self, parent):
        """设备信息卡片(两列紧凑网格)。"""
        info = self._card(parent, "设备信息")
        info.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        info.columnconfigure(1, weight=1)
        info.columnconfigure(3, weight=1)
        pairs = [("型号", "固件"), ("地址", "硬件"), ("协议", None)]
        for i, (k1, k2) in enumerate(pairs):
            ttk.Label(info, text=k1 + ":", style="H2.TLabel").grid(
                row=i, column=0, sticky=tk.W, pady=2)
            ttk.Label(info, textvariable=self._new_var(k1),
                      style="Card.TLabel").grid(
                row=i, column=1, sticky=tk.W, padx=(4, 16))
            if k2:
                ttk.Label(info, text=k2 + ":", style="H2.TLabel").grid(
                    row=i, column=2, sticky=tk.W, pady=2)
                ttk.Label(info, textvariable=self._new_var(k2),
                          style="Card.TLabel").grid(
                    row=i, column=3, sticky=tk.W, padx=(4, 0))

    def _new_var(self, key):
        var = tk.StringVar(value="--")
        setattr(self, "var_" + key, var)
        return var

    def _lb(self, title, c=0):
        return self.lb.get((title, c))

    def _color_lb(self, title, color, c=0):
        w = self.lb_w.get((title, c))
        if w:
            w.configure(foreground=color)

    def _set_monitor_text(self, text):
        """将实时监测全部数值清为 text, 并重置标签颜色。"""
        for (title, _c), var in self.lb.items():
            var.set(text)
        fg = C_GRAY if text == "通讯中断" else C_TEXT
        for (title, _c), w in self.lb_w.items():
            w.configure(foreground=fg)

    def _res_color(self, res):
        if self._rated_res and res >= self._rated_res:
            return C_RED
        if self._rated_res and res >= 0.7 * self._rated_res:
            return C_ORANGE
        return C_ACCENT

    def _st_color(self):
        """状态字1 配色(与动画一致): 合闸=红(带电), 分闸=绿, 故障跳闸=橙。"""
        pos, reason = (self._st1 >> 5) & 0x03, self._st1 & 0x1F
        if pos == 1:
            return C_RED
        if pos == 0:
            return C_ORANGE if reason else C_GREEN
        return C_ORANGE

    # ==================== 三相电路动画(内嵌于实时监测页) ====================
    def _build_anim_panel(self, parent):
        """动画卡片: 标题工具条 + 画布, 整体铺满所在列。"""
        card = self._card(parent, "断路器状态动画 · 三相电路")
        card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)

        bar = ttk.Frame(card, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.var_arc_enable = tk.BooleanVar(value=False)
        self.var_arc_delay = tk.StringVar(value="3")
        ttk.Label(bar, text="L1/L2/L3 三相 + N, 反映设备真实分/合闸与故障原因",
                  style="CardMuted.TLabel").pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="演示自动重合闸", variable=self.var_arc_enable
                        ).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Label(bar, text="秒", style="Card.TLabel").pack(side=tk.RIGHT)
        ttk.Spinbox(bar, from_=1, to=10, width=3,
                    textvariable=self.var_arc_delay).pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(card, bg="#fbfcfe", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")

    def _anim_state(self):
        """由状态字1推导 (pos, reason): pos 0=分闸 1=合闸 2=故障 3=未知。"""
        v = self._st1
        return (v >> 5) & 0x03, v & 0x1F

    def _anim_tick(self):
        self._anim_tick_no += 1
        try:
            self._draw_anim()
        except tk.TclError:
            return
        self.after(100, self._anim_tick)

    def _draw_anim(self):
        c = self.canvas
        c.delete("all")
        W = max(360, c.winfo_width())
        H = max(220, c.winfo_height())
        c.create_rectangle(0, 0, W, H, fill="#fbfcfe", outline="")

        # 连接状态 / 分合闸状态
        live = self.worker.connected and not self._stale
        pos, reason = self._anim_state()
        energized = live and pos == 1
        PH = ["#d97706", "#16a34a", "#dc2626"]      # L1黄 L2绿 L3红
        PN = "#1d4ed8"                              # N 蓝
        GRAY = "#cbd5e1"                            # 未带电/未知

        # ---- 纵向布局: 横幅 / 标题+电路体 / 底部状态条(互不重叠) ----
        m = 14
        y_ban_top, y_ban_bot = 8, 40
        strip_h = 46
        strip_top = H - strip_h
        y_box_top = 48
        y_box_bot = strip_top - 58                # 框底, 下方留"互感器名/漏电路径"
        y_title1 = y_box_top + 14                 # 框内标题(两行, 不压边框)
        y_title2 = y_box_top + 28
        y_body_top = y_box_top + 44               # 标题与电路体之间留白
        y_body_bot = y_box_bot - 6
        ys = [y_body_top + k * (y_body_bot - y_body_top) / 3.0
              for k in range(4)]                  # L1 L2 L3 N
        yc = (ys[0] + ys[3]) / 2.0
        amp = min(7.0, (ys[1] - ys[0]) * 0.34)

        # ---- 横向布局: 电源 | 断路器 | 互感器 | 负载 (按可用宽度自适应) ----
        avail = W - 2 * m
        # 各段基础比例(电源/间隙/断路器/间隙/互感器/间隙/负载)
        src_w = max(96, min(150, avail * 0.20))
        brk_w = max(88, min(132, avail * 0.18))
        ct_w = max(60, min(84, avail * 0.11))
        gap = max(20, min(46, avail * 0.06))
        load_w = avail - src_w - brk_w - ct_w - 3 * gap
        if load_w < 96:                       # 太窄时压缩间隙回收空间
            gap = max(14, gap - (96 - load_w) / 3.0)
            load_w = avail - src_w - brk_w - ct_w - 3 * gap
        load_w = max(80, load_w)
        x_src_l = m
        x_src_r = x_src_l + src_w
        x_brk_l = x_src_r + gap
        x_brk_r = x_brk_l + brk_w
        x_sw = (x_brk_l + x_brk_r) / 2.0
        x_ct_l = x_brk_r + gap
        x_ct_r = x_ct_l + ct_w
        x_ct = (x_ct_l + x_ct_r) / 2.0
        x_load_l = x_ct_r + gap
        x_load_r = W - m

        # ---- 顶部状态横幅(合闸=红, 分闸=绿; 故障跳闸红/暗红闪烁) ----
        if not self.worker.connected:
            banner, bcol = "未连接设备 · 请先连接设备", "#64748b"
        elif self._stale:
            banner, bcol = "通讯中断 · 等待设备恢复应答", C_ORANGE
        elif energized:
            banner, bcol = "合闸运行", C_RED
        elif pos == 0 and reason:
            banner = "故障跳闸 · %s" % REASON_TABLE.get(
                reason, "原因0x%02X" % reason)
            bcol = C_RED if (self._anim_tick_no // 6) % 2 == 0 else "#b91c1c"
        elif pos == 0:
            banner, bcol = "分闸(正常)", C_GREEN
        else:
            banner, bcol = "状态未知", C_ORANGE
        c.create_rectangle(m, y_ban_top, W - m, y_ban_bot, fill=bcol,
                           outline="")
        c.create_text(W // 2, (y_ban_top + y_ban_bot) // 2, text=banner,
                      fill="#ffffff", font=(self.FONT, 15, "bold"))

        # ---- 主线路(先画导线, 其它元素覆盖其上): 电源侧常带电 ----
        pw = PH if energized else [GRAY] * 3

        def seg_src():
            return [(x_src_r, x_sw - 12)]

        def seg_dn():
            return [(x_sw + 12, x_ct_l - 2), (x_ct_r + 2, x_load_l)]

        for i in range(3):
            for (a, b) in seg_src():
                c.create_line(a, ys[i], b, ys[i], fill=PH[i], width=4)
        for i in range(3):
            for (a, b) in seg_dn():
                c.create_line(a, ys[i], b, ys[i], fill=pw[i], width=4)
        c.create_line(x_src_r, ys[3], x_load_r, ys[3], fill=PN, width=3)

        # ---- 三相电源框(相序标签/正弦波均在框内, 顶部两行标题) ----
        c.create_rectangle(x_src_l, y_box_top, x_src_r, y_box_bot,
                           fill="#eef2ff", outline=C_ACCENT, width=2)
        c.create_text((x_src_l + x_src_r) / 2, y_title1,
                      text="三相电源", fill=C_TEXT,
                      font=(self.FONT, 9, "bold"))
        c.create_text((x_src_l + x_src_r) / 2, y_title2,
                      text="AC380V 50Hz", fill=C_GRAY,
                      font=(self.FONT, 8))
        lbl_x = x_src_l + 6
        wave_x0 = x_src_l + 22
        wave_x1 = x_src_r - 8
        for i in range(3):
            c.create_text(lbl_x, ys[i], text=["L1", "L2", "L3"][i],
                          fill=PH[i], font=(self.FONT, 8, "bold"),
                          anchor=tk.W)
            pts = []
            for t in range(0, 33):
                ang = t / 32.0 * 2.0 * math.pi
                px = wave_x0 + t / 32.0 * (wave_x1 - wave_x0)
                py = ys[i] - amp * math.sin(ang + i * 2.0 * math.pi / 3.0)
                pts.append((px, py))
            c.create_line(pts, fill=PH[i], width=2, smooth=True)
        c.create_text(lbl_x, ys[3], text="N", fill=PN,
                      font=(self.FONT, 8, "bold"), anchor=tk.W)

        # ---- 三相负载框 ----
        c.create_rectangle(x_load_l, y_box_top, x_load_r, y_box_bot,
                           fill="#fefce8", outline="#ca8a04", width=2)
        c.create_text((x_load_l + x_load_r) / 2, y_title1,
                      text="三相负载", fill="#a16207",
                      font=(self.FONT, 9, "bold"))
        c.create_text((x_load_l + x_load_r) / 2, y_title2,
                      text="各相阻抗", fill="#a16207",
                      font=(self.FONT, 8))
        zamp = min(7.0, (ys[1] - ys[0]) * 0.3)
        for i in range(3):
            zx0, zx1 = x_load_l + 8, x_load_r - 8
            pts = [(zx0, ys[i])]
            for k in range(5):
                pts.append((zx0 + (zx1 - zx0) * (k + 0.5) / 5.0,
                            ys[i] + zamp * (-1 if k % 2 == 0 else 1)))
            pts.append((zx1, ys[i]))
            c.create_line(pts, fill=(PH[i] if energized else GRAY), width=2)

        # ---- 断路器 QF(三极联动): 合闸=红, 分闸=绿, 故障跳闸红闪 ----
        if not live:
            bcol = "#cbd5e1"
        elif pos == 1:
            bcol = C_RED
        elif pos == 0 and reason:
            blink = (self._anim_tick_no // 6) % 2 == 0
            bcol = C_RED if blink else "#fecaca"
        else:
            bcol = C_GREEN
        c.create_rectangle(x_brk_l, y_box_top, x_brk_r, y_box_bot,
                           fill="#ffffff", outline=bcol, width=3)
        c.create_text(x_brk_l + 10, y_title1, text="断路器 QF",
                      fill=C_TEXT, font=(self.FONT, 9, "bold"),
                      anchor=tk.W)
        c.create_text(x_brk_l + 10, y_title2, text="(三极联动)",
                      fill=C_GRAY, font=(self.FONT, 8), anchor=tk.W)
        if not live:
            ccol = GRAY                             # 未连接/通讯中断
        elif pos == 1 or (pos == 0 and reason):
            ccol = C_RED                            # 合闸(闭合)/故障跳闸: 红
        else:
            ccol = C_GREEN                          # 分闸(断开): 绿
        # 三极联动连杆(虚线)示意三极同步动作
        c.create_line(x_sw + 20, ys[0] - 4, x_sw + 20, ys[2] + 4,
                      fill="#94a3b8", width=2, dash=(4, 3))
        for i in range(3):
            y = ys[i]
            hx, fx = x_sw - 10, x_sw + 10
            c.create_oval(hx - 4, y - 4, hx + 4, y + 4, fill=ccol,
                          outline="")
            c.create_oval(fx - 4, y - 4, fx + 4, y + 4, fill=ccol,
                          outline="")
            if energized:
                c.create_line(hx, y, fx, y, fill=ccol, width=4)
            else:
                # 分闸/未连接/通讯中断: 触头断开(上翘)
                c.create_line(hx, y, fx - 6, y - 16, fill=ccol, width=4)

        # ---- 剩余电流互感器(L1/L2/L3/N 全部穿过铁芯窗口) ----
        c.create_rectangle(x_ct_l, y_body_top - 10, x_ct_r, y_body_bot + 2,
                           fill="#eff6ff", outline=C_ACCENT, width=3)
        c.create_rectangle(x_ct - 20, y_body_top - 4, x_ct + 20,
                           y_body_bot - 2, fill="#fbfcfe", outline="#bfdbfe",
                           width=1)
        c.create_text(x_ct - 26, yc, text="ΔI", fill=C_ACCENT,
                      font=(self.FONT, 11, "bold"))
        c.create_text(x_ct, y_box_bot + 16, text="剩余电流互感器",
                      fill=C_GRAY, font=(self.FONT, 9), anchor=tk.CENTER)

        # ---- 漏电指示(在线且剩余电流>0 时画对地漏电路径) ----
        if live and self._residual > 0:
            gx = x_load_l + 55
            gy = y_box_bot + 8 + 22
            c.create_line(gx, y_box_bot + 8, gx, gy, fill=C_RED, width=2,
                          dash=(5, 3))
            c.create_text(gx + 6, (y_box_bot + 8 + gy) / 2,
                          text="漏电电流 %.0f mA" % self._residual,
                          fill=C_RED, font=(self.FONT, 8), anchor=tk.W)
            for k, wdt in enumerate((22, 15, 8)):
                c.create_line(gx - wdt, gy + 6 + k * 6, gx + wdt,
                              gy + 6 + k * 6, fill=C_RED, width=2)
            c.create_text(gx, gy + 27, text="PE/大地", fill=C_RED,
                          font=(self.FONT, 8))

        # ---- 电流流动动画(合闸带电时, 白色亮点流动) ----
        if energized:
            spd = 14 if max(self._i3) > 1 else 24
            off = (self._anim_tick_no // 2) % spd
            seg = 20
            for i in range(3):
                for (a, b) in seg_dn():
                    x = a + off
                    while x + seg <= b:
                        c.create_line(x, ys[i], x + seg, ys[i], fill="#ffffff",
                                      width=3, capstyle="round")
                        x += spd
            # N 回流(负载 -> 电源)
            x = x_load_r - off
            while x - seg >= x_src_r:
                c.create_line(x, ys[3], x - seg, ys[3], fill="#ffffff",
                              width=3, capstyle="round")
                x -= spd

        # ---- 底部状态条(文本按实际宽度流动布局, 避免重叠) ----
        c.create_rectangle(m, strip_top, W - m, H - 8, fill="#f8fafc",
                           outline=C_LINE, width=2)
        if not self.worker.connected:
            st_text, st_col = "未连接", C_GRAY
        elif self._stale:
            st_text, st_col = "通讯中断", C_ORANGE
        elif energized:
            st_text, st_col = "合闸运行", C_RED
        elif pos == 0 and reason:
            st_text, st_col = "故障跳闸", C_RED
        else:
            st_text, st_col = "分闸", C_GREEN
        led = (C_RED if energized else
               (C_RED if (live and pos == 0 and reason) else
                (C_GREEN if live else C_GRAY)))
        c.create_oval(28, strip_top + 9, 44, strip_top + 25, fill=led,
                      outline="")

        f10 = tkfont.Font(family=self.FONT, size=10)
        f10b = tkfont.Font(family=self.FONT, size=10, weight="bold")
        xcur = 52
        items = [
            ("保护状态: %s" % st_text, C_TEXT, f10),
            ("剩余电流: %.1f mA" % self._residual,
             self._res_color(self._residual) if live else C_MUTED, f10b),
            ("额定动作值: %d mA" % self._rated_res,
             C_TEXT if live else C_MUTED, f10),
        ]
        for txt, col, fnt in items:
            c.create_text(xcur, strip_top + 17, text=txt, fill=col,
                          font=fnt, anchor=tk.W)
            xcur += fnt.measure(txt) + 26

        if self._arc_left is not None and live:
            total = max(1, int(self.var_arc_delay.get() or 3))
            frac = max(0.0, min(1.0, self._arc_left / total))
            bw = 200
            bx = W - m - bw
            c.create_rectangle(bx, strip_top + 9, bx + bw, strip_top + 25,
                               outline=C_LINE, width=2)
            c.create_rectangle(bx, strip_top + 9, bx + bw * frac,
                               strip_top + 25, fill=C_RED, outline="")
            c.create_text(bx + bw / 2, strip_top + 17,
                          text="自动重合闸 %ds" % self._arc_left,
                          fill="#ffffff", font=(self.FONT, 9, "bold"))

    def _maybe_arc(self):
        """故障跳闸后按设定延时自动合闸(演示自动重合闸)。"""
        pos, reason = self._anim_state()
        if not self.var_arc_enable.get():
            self._arc_left = None
            return
        fault = (pos == 0 and reason != 0) or pos == 2
        if fault and self._arc_left is None and self.worker.connected \
                and not self._stale:
            try:
                self._arc_left = max(1, int(self.var_arc_delay.get() or 3))
            except ValueError:
                self._arc_left = 3
            self.after(1000, self._arc_tick)
        elif pos == 1:
            self._arc_left = None

    def _arc_tick(self):
        if self._arc_left is None:
            return
        self._arc_left -= 1
        pos, reason = self._anim_state()
        if self._arc_left <= 0:
            self._arc_left = None
            if self.worker.connected:
                self._ctrl("close")
            self._log("自动重合闸(演示): 延时到, 自动下发远程合闸")
            return
        if pos == 1:
            self._arc_left = None
            return
        self.after(1000, self._arc_tick)

    # ==================== 远程控制(内嵌于实时监测页) ====================
    def _build_control_panel(self, parent):
        """远程控制: 认证 / 立即执行 / 预约执行 三张卡片竖排, 均匀铺满右列。"""
        f = parent

        auth = self._card(f, "身份认证 · 分/合闸须用0级密码")
        auth.grid(row=1, column=0, sticky="ew")
        row = ttk.Frame(auth, style="Card.TFrame")
        row.pack(fill=tk.X)
        ttk.Label(row, text="级别:", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.var_pwd_level, width=3,
                     values=["0", "1", "2"]).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row, text="密码:", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.var_pwd_code, width=8
                  ).pack(side=tk.LEFT, padx=(4, 0))
        row2 = ttk.Frame(auth, style="Card.TFrame")
        row2.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(row2, text="操作者代码:", style="Card.TLabel"
                  ).pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.var_oper, width=12
                  ).pack(side=tk.LEFT, padx=(4, 0))

        act = self._card(f, "立即执行")
        act.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        grid1 = ttk.Frame(act, style="Card.TFrame")
        grid1.pack(fill=tk.X)
        for i, (txt, act_key) in enumerate(
                [("远程合闸", "close"), ("远程分闸", "trip"),
                 ("模拟试跳", "test"), ("自检", "selftest")]):
            ttk.Button(grid1, text=txt,
                       command=lambda k=act_key: self._ctrl(k)
                       ).grid(row=i // 2, column=i % 2, sticky="ew",
                              padx=(0 if i % 2 == 0 else 6, 0),
                              pady=(0 if i < 2 else 6, 0))
        grid1.columnconfigure(0, weight=1)
        grid1.columnconfigure(1, weight=1)

        sch = self._card(f, "预约执行 · 延时后自动执行")
        sch.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        top = ttk.Frame(sch, style="Card.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="延迟:", style="Card.TLabel").pack(side=tk.LEFT)
        self.var_delay = tk.StringVar(value="1")
        ttk.Spinbox(top, from_=0, to=99, width=4,
                    textvariable=self.var_delay).pack(side=tk.LEFT, padx=4)
        self.var_delay_unit = tk.StringVar(value="分钟")
        ttk.Combobox(top, textvariable=self.var_delay_unit, width=5,
                     values=["分钟", "小时"]).pack(side=tk.LEFT)
        g2 = ttk.Frame(sch, style="Card.TFrame")
        g2.pack(fill=tk.X, pady=(8, 0))
        for i, (txt, act_key) in enumerate(
                [("预约合闸", "close"), ("预约分闸", "trip"),
                 ("预约试跳", "test")]):
            ttk.Button(g2, text=txt,
                       command=lambda k=act_key: self._ctrl(k, True)
                       ).grid(row=0, column=i, sticky="ew",
                              padx=(0 if i == 0 else 6, 0))
            g2.columnconfigure(i, weight=1)
        ttk.Button(sch, text="取消预约", style="Secondary.TButton",
                   command=self._cancel_schedule).pack(fill=tk.X,
                                                       pady=(8, 0))

        warn = self._card(f, "安全提醒 · 教学演示")
        warn.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        ttk.Label(warn, justify=tk.LEFT, wraplength=330,
                  style="Card.TLabel", foreground="#92400e",
                  text=("1. 合/分闸、试跳均为真实断路器动作, 操作前确认现场无人作业。\n"
                        "2. 试跳模拟剩余电流故障, 断路器将分闸, 用于检验保护动作。\n"
                        "3. 预约控制: 延迟+单位(分/时), 到点自动执行, 可[取消预约]。\n"
                        "4. 所有写操作需正确密码与操作者代码, 并存入事件记录。")
                  ).pack(anchor=tk.W)
        # 右列: 认证/立即/预约固定高, 安全提醒吸收剩余空间
        f.rowconfigure(4, weight=1)

    # ---- 控制操作 ----
    def _ctrl(self, action, scheduled=False):
        if not self._require_client():
            return
        di_map = {"close": (0x06, 0x01, 0x02, 0x01),
                  "trip": (0x06, 0x01, 0x01, 0x01),
                  "test": (0x06, 0x01, 0x03, 0x01),
                  "selftest": (0x06, 0x01, 0x04, 0x01)}
        pwd = self._pwd()
        op = self._oper()
        # 规约表“控制命令”: 该 DI 必须带 NN(预约)+uu(单位) 两字节, NN=0 立即执行。
        if scheduled:
            nn = P.bcd_encode(int(self.var_delay.get() or 0) % 100, 1)[0]
            uu = 0x03 if self.var_delay_unit.get() == "小时" else 0x02
            data = [nn, uu]
        else:
            data = [0x00, 0x00]          # NN=0 -> 立即合/分闸
        tag = "预约%s" if scheduled else "%s"
        tag = (tag % action)
        self.worker.op(lambda c: c.control(di_map[action], data, pwd, op), tag)
        self.var_status.set("已下发: %s ..." % tag)

    def _cancel_schedule(self):
        if not self._require_client():
            return
        for act, di in (("close", (0x06, 0x01, 0x02, 0x02)),
                        ("trip", (0x06, 0x01, 0x01, 0x02))):
            self.worker.op(lambda c, d=di: c.control(d, [], self._pwd(),
                                                     self._oper()), "取消预约")
        self.var_status.set("已下发取消预约指令 ...")

    # ==================== 参数设置 ====================
    def _build_tab_params(self):
        f = self.tab_params
        self.param_vars = {}
        self.param_dis = {}
        ttk.Label(f, text="参数设置", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 6))

        def add_param(parent, label, di, unit, nbytes, scale=1):
            row = len(self.param_vars)
            ttk.Label(parent, text=label).grid(row=row, column=0,
                                               sticky=tk.W, pady=3)
            var = tk.StringVar(value="")
            ttk.Entry(parent, textvariable=var, width=12).grid(
                row=row, column=1, sticky=tk.W, padx=6)
            ttk.Label(parent, text=unit).grid(row=row, column=2, sticky=tk.W)
            self.param_vars[label] = var
            self.param_dis[label] = (di, nbytes, scale)

        ctrl = ttk.LabelFrame(f, text="控制字(0x10 参数模块)", padding=10)
        ctrl.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        for i in range(1, 6):
            add_param(ctrl, "控制字%d" % i, (0x04, 0x00, 0x10, i),
                      "HEX 1字节", 1)

        cur = ttk.LabelFrame(f, text="电流整定(0x14)", padding=10)
        cur.grid(row=1, column=1, sticky="nsew", padx=(0, 8))
        add_param(cur, "Ir1 过载动作电流", (0x04, 0x00, 0x14, 0x01), "A", 3, 0.1)
        add_param(cur, "t1 长延时时间", (0x04, 0x00, 0x14, 0x03), "s", 1)
        add_param(cur, "Ir2 短延时倍数", (0x04, 0x00, 0x14, 0x04), "×Ir1", 1)
        add_param(cur, "t2 短延时时间", (0x04, 0x00, 0x14, 0x05), "ms", 2)
        add_param(cur, "Ir3 瞬时倍数", (0x04, 0x00, 0x14, 0x06), "×Ir1", 1)

        volt = ttk.LabelFrame(f, text="电压整定(0x13)", padding=10)
        volt.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(8, 0))
        add_param(volt, "过电压整定值", (0x04, 0x00, 0x13, 0x01), "V", 2, 0.1)
        add_param(volt, "欠电压整定值", (0x04, 0x00, 0x13, 0x02), "V", 2, 0.1)
        add_param(volt, "缺相电压整定值", (0x04, 0x00, 0x13, 0x03), "V", 2, 0.1)

        misc = ttk.LabelFrame(f, text="告警与自检", padding=10)
        misc.grid(row=2, column=1, sticky="nsew", padx=(0, 8), pady=(8, 0))
        add_param(misc, "剩余电流预警(百分比)", (0x04, 0x00, 0x11, 0x04),
                  "%", 1)
        add_param(misc, "预警持续时间", (0x04, 0x00, 0x11, 0x05), "s", 1)
        add_param(misc, "定时自检(DDHHMM)", (0x04, 0x00, 0x12, 0x01),
                  "", 3)

        btns = ttk.Frame(f)
        btns.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=10)
        ttk.Button(btns, text="读取全部参数", width=16,
                   command=self._read_params).pack(side=tk.LEFT)
        ttk.Button(btns, text="写入全部参数", width=16,
                   command=self._write_params).pack(side=tk.LEFT, padx=10)
        ttk.Label(btns, text="(控制字为1字节HEX; 其余按 BCD 编码写入)",
                  foreground="#666").pack(side=tk.LEFT)

        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

    # ---- 参数读写 ----
    def _read_params(self):
        if not self._require_client():
            return
        for label, (di, nbytes, scale) in self.param_dis.items():
            self.worker.read(di)
        self.var_status.set("正在读取全部参数 ...")

    def _write_params(self):
        if not self._require_client():
            return
        pwd = self._pwd()
        op = self._oper()
        for label, (di, nbytes, scale) in self.param_dis.items():
            raw = self.param_vars[label].get().strip()
            if not raw:
                continue
            try:
                if nbytes == 1 and di[2] == 0x10:      # 控制字: 按HEX
                    value = int(raw, 16) & 0xFF
                    data = [value]
                elif di == (0x04, 0x00, 0x12, 0x01):
                    # 定时自检 DDhhmm 为分量型, 按书写顺序编码
                    value = int(round(float(raw) / scale))
                    data = P.bcd_encode(value, nbytes)
                else:
                    # 数值量: 多字节低字节在前
                    num = float(raw)
                    value = int(round(num / scale))
                    data = P.bcd_encode_lf(value, nbytes)
            except ValueError:
                messagebox.showwarning("参数写入", "%s 输入无效: %r"
                                       % (label, raw))
                continue
            self.worker.op(
                lambda c, d=di, b=data: c.write(d, b, pwd, op), label)
        self.var_status.set("已下发参数写入指令 ...")

    # ==================== 事件与统计 ====================
    def _build_tab_events(self):
        f = self.tab_events
        ttk.Label(f, text="事件与统计", style="Title.TLabel").pack(anchor=tk.W)
        stats = ttk.LabelFrame(f, text="统计信息", padding=10)
        stats.pack(fill=tk.X, pady=(6, 0))
        self.var_stat = tk.StringVar(value="--")
        self.var_stat2 = tk.StringVar(value="--")
        ttk.Label(stats, textvariable=self.var_stat).pack(side=tk.LEFT)
        ttk.Label(stats, textvariable=self.var_stat2).pack(side=tk.LEFT,
                                                           padx=24)
        ttk.Button(stats, text="刷新统计", command=self._read_stats
                   ).pack(side=tk.RIGHT)
        ttk.Button(stats, text="数据清零(全部事件)", command=self._clear_data
                   ).pack(side=tk.RIGHT, padx=8)

        trip = ttk.LabelFrame(f, text="跳闸次数(按原因 0x00-0x0B)", padding=10)
        trip.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.txt_trip = tk.Text(trip, height=6, font=("TkFixedFont", 10))
        self.txt_trip.pack(fill=tk.BOTH, expand=True)
        ttk.Button(trip, text="读取跳闸次数", command=self._read_trip_counts
                   ).pack(anchor=tk.E, pady=(6, 0))

        ev = ttk.LabelFrame(f, text="事件记录读取", padding=10)
        ev.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        top = ttk.Frame(ev)
        top.pack(fill=tk.X)
        ttk.Label(top, text="事件类型:").pack(side=tk.LEFT)
        self.var_ev_type = tk.StringVar(value="保护动作")
        ttk.Combobox(top, textvariable=self.var_ev_type, width=12, values=[
            "保护动作", "自检事件", "闸位变化", "告警事件",
            "高压失/复电", "保护投退"]).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="序号:").pack(side=tk.LEFT)
        self.var_ev_idx = tk.StringVar(value="1")
        ttk.Spinbox(top, from_=1, to=50, width=5,
                    textvariable=self.var_ev_idx).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="读取", command=self._read_event
                   ).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="读取最近5条", command=self._read_events5
                   ).pack(side=tk.LEFT)
        self.txt_ev = tk.Text(ev, height=9, font=("TkFixedFont", 10))
        self.txt_ev.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

    EV_DIS = {
        "保护动作": (0x03, 0x8E, 0x00),
        "自检事件": (0x03, 0x8D, 0x00),
        "闸位变化": (0x03, 0x91, 0x00),
        "告警事件": (0x03, 0x92, 0x00),
        "高压失/复电": (0x03, 0x93, 0x00),
        "保护投退": (0x03, 0x8F, 0x00),
    }

    def _read_stats(self):
        if not self._require_client():
            return
        self.worker.read((0x03, 0x30, 0x01, 0x00))      # 清零次数
        self.worker.read((0x03, 0x81, 0x01, 0x01))      # 运行时间(分)

    def _read_trip_counts(self):
        if not self._require_client():
            return
        self.worker.read((0x03, 0x81, 0x00, 0xFF))

    def _read_event(self):
        if not self._require_client():
            return
        try:
            idx = int(self.var_ev_idx.get())
        except ValueError:
            idx = 1
        self._fetch_event(self.var_ev_type.get(), idx)

    def _read_events5(self):
        if not self._require_client():
            return
        etype = self.var_ev_type.get()
        for i in range(1, 6):
            self._fetch_event(etype, i)

    def _fetch_event(self, etype, idx):
        di3 = self.EV_DIS[etype]
        di = di3 + (idx,)
        self.worker.read_event(di)
        self.var_status.set("正在读取事件: %s 序号%d ..." % (etype, idx))

    # ==================== 密码管理 ====================
    def _build_tab_pwd(self):
        f = self.tab_pwd
        ttk.Label(f, text="密码管理", style="Title.TLabel").pack(anchor=tk.W)

        box = ttk.LabelFrame(f, text="修改密码", padding=14)
        box.pack(anchor=tk.W, pady=(6, 10))
        ttk.Label(box, text="目标级别:").grid(row=0, column=0, sticky=tk.E)
        ttk.Combobox(box, textvariable=self.var_pwd_level, width=4,
                     values=["0", "1", "2"]).grid(row=0, column=1, padx=6,
                                                  pady=4, sticky=tk.W)
        ttk.Label(box, text="原密码(级别+6位):").grid(row=1, column=0,
                                                  sticky=tk.E)
        self.var_pwd_old = tk.StringVar(value="000000")
        ttk.Entry(box, textvariable=self.var_pwd_old, width=10
                  ).grid(row=1, column=1, padx=6, pady=4, sticky=tk.W)
        ttk.Label(box, text="新密码(6位):").grid(row=2, column=0, sticky=tk.E)
        self.var_pwd_new = tk.StringVar(value="")
        ttk.Entry(box, textvariable=self.var_pwd_new, width=10, show="*"
                  ).grid(row=2, column=1, padx=6, pady=4, sticky=tk.W)
        ttk.Label(box, text="确认新密码:").grid(row=3, column=0, sticky=tk.E)
        self.var_pwd_new2 = tk.StringVar(value="")
        ttk.Entry(box, textvariable=self.var_pwd_new2, width=10, show="*"
                  ).grid(row=3, column=1, padx=6, pady=4, sticky=tk.W)
        ttk.Button(box, text="修改密码", command=self._change_pwd
                   ).grid(row=4, column=0, columnspan=2, pady=10)

        tip = ttk.LabelFrame(f, text="密码规则说明", padding=10)
        tip.pack(fill=tk.X)
        ttk.Label(tip, justify=tk.LEFT, wraplength=900, text=(
            "1. 密码为4字节: 级别字节 + 3字节BCD(6位十进制)。\n"
            "2. 级别权限(级别号越小权限越高):\n"
            "   0级=控制/分合闸、清零、写数据、修改密码、软件升级\n"
            "   1级=清零、写数据、修改密码\n"
            "   2级=写数据、修改密码\n"
            "   > 远程分/合闸(控制指令)必须使用 0 级密码！\n"
            "3. 低号(高权限)级别可修改自身及低权限级别密码, 反之不可。\n"
            "4. 修改密码需提供 原密码 与 新密码, 新密码级别与[目标级别]一致。\n"
            "5. 出厂密码因厂家而异: 常见为 000000/111111/888888/序列号后6位, 并非统一 000000。\n"
                  "6. 连续试错密码可能触发设备锁定, 锁定后即使密码正确也回 0x04, 需先断电复位。"
        )).pack(anchor=tk.W)

    def _change_pwd(self):
        if not self._require_client():
            return
        level = int(self.var_pwd_level.get())
        old = self.var_pwd_old.get().strip() or "000000"
        new = self.var_pwd_new.get().strip()
        if len(new) != 6 or not new.isdigit():
            messagebox.showwarning("修改密码", "新密码必须为6位数字")
            return
        if new != self.var_pwd_new2.get().strip():
            messagebox.showwarning("修改密码", "两次输入的新密码不一致")
            return
        old_pwd = make_password(level, old)
        new_pwd = make_password(level, new)
        self.worker.op(lambda c: c.change_password(old_pwd, new_pwd),
                       "修改密码")

    # ==================== 通信日志 ====================
    def _build_tab_log(self):
        f = self.tab_log
        ttk.Label(f, text="通信日志", style="Title.TLabel").pack(anchor=tk.W)
        self.txt_log = tk.Text(f, state=tk.DISABLED, height=18,
                               font=(self.FONT, 9), bg="#ffffff",
                               fg=C_TEXT, relief="flat", padx=6, pady=4,
                               insertbackground=C_TEXT)
        sb = ttk.Scrollbar(f, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(bar, text="清空日志", command=self._clear_log
                   ).pack(side=tk.LEFT)
        ttk.Label(bar, text="TX=请求帧  RX=应答帧", foreground="#666"
                  ).pack(side=tk.LEFT, padx=12)

    def _log(self, text):
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.insert(tk.END, text + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    def _clear_log(self):
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.configure(state=tk.DISABLED)

    # ==================== 连接管理 ====================
    MONITOR_DIS = [
        (0x02, 0x01, 0xFF, 0x00),
        (0x02, 0x02, 0xFF, 0x00),
        (0x02, 0x90, 0xFF, 0x00),
        (0x02, 0x91, 0xFF, 0x00),
        (0x04, 0x00, 0x05, 0x01),
        (0x04, 0x00, 0x05, 0x02),
    ]
    INFO_DIS = [
        ((0x04, 0x00, 0x04, 0x0B), "型号"),
        ((0x04, 0x00, 0x04, 0x01), "地址"),
        ((0x04, 0x00, 0x04, 0x0D), "协议"),
        ((0x04, 0x00, 0x04, 0x0F), "固件"),
        ((0x04, 0x00, 0x04, 0x10), "硬件"),
    ]
    CTRL_NAMES = {
        0x08: "广播校时", 0x11: "读数据", 0x12: "读后续", 0x13: "读地址",
        0x14: "写数据", 0x17: "改波特率", 0x18: "改密码", 0x1B: "数据清零",
        0x1C: "控制指令", 0x1D: "软件升级",
        0x91: "应答-读", 0x94: "应答-写", 0x98: "应答-改密",
        0x9B: "应答-清零", 0x9C: "应答-控制",
        0xD1: "异常-读", 0xD4: "异常-写", 0xD8: "异常-改密",
        0xDB: "异常-清零", 0xDC: "异常-控制", 0xDD: "异常-升级",
    }

    # ---- 串口自动扫描 ----
    @staticmethod
    def _scan_ports():
        """枚举系统可用串口, 返回 [(端口名, 描述), ...]; 无 pyserial/出错返回 []。"""
        if list_ports is None:
            return []
        try:
            return [(p.device, p.description or "")
                    for p in list_ports.comports() if p.device]
        except Exception:
            return []

    def _refresh_ports(self):
        """扫描可用串口并把 端口 + 设备名 显示到下拉框(可手动编辑, 兼容未枚举端口)。"""
        ports = self._scan_ports()
        self._port_map = {}
        displays = []
        for dev, desc in ports:
            display = "%s  (%s)" % (dev, desc) if desc else dev
            self._port_map[display] = dev
            displays.append(display)
        self.port_entry["values"] = displays
        if ports:
            devices = [d for d, _ in ports]
            cur = self.var_port.get().strip()
            if self._port_map.get(cur, cur) not in devices:
                self.var_port.set(displays[0])       # 默认选中第一个可用串口
            elif cur in devices and cur not in self._port_map:
                self.var_port.set(displays[devices.index(cur)])
            self._log("已扫描到串口: " + ", ".join(displays))
        else:
            self._log("未扫描到串口, 请检查 USB-RS485 适配器是否连接/驱动正常")
        self.var_status.set("已扫描到 %d 个串口" % len(ports))

    def _on_connect(self):
        addr = self.var_addr.get().strip()
        if not addr.isdigit() or len(addr) > 12:
            messagebox.showwarning("连接", "通信地址须为12位以内的十进制数字")
            return
        addr = addr.zfill(12)[-12:]
        try:
            P.encode_addr(addr)
        except ValueError as e:
            messagebox.showwarning("连接", str(e))
            return
        mode = self.var_mode.get()
        if mode == "sim":
            # 仿真模式无需端口, 直接用内置虚拟断路器
            self.btn_connect.configure(state=tk.DISABLED)
            self.var_status.set("正在连接[仿真模式] ...")
            self.worker.connect(addr, "", 9600, mode="sim")
            return
        port = self.var_port.get().strip()
        port = getattr(self, "_port_map", {}).get(port, port)   # 显示名 -> 实际端口
        if not port:
            messagebox.showwarning(
                "连接", "请选择或填写串口端口(如 COM3), 或点击[刷新]自动扫描")
            return
        try:
            baud = int(self.var_baud.get())
        except ValueError:
            baud = 9600
        self.btn_connect.configure(state=tk.DISABLED)
        self.var_status.set("正在连接[串口 %s] ..." % port)
        self.worker.connect(addr, port, baud, mode="serial")

    def _on_disconnect(self):
        self.worker.disconnect()
        self.btn_disconnect.configure(state=tk.DISABLED)
        self.var_status.set("正在断开 ...")

    def _require_client(self):
        if not self.worker.connected:
            messagebox.showinfo("提示", "请先连接设备")
            return False
        return True

    def _pwd(self):
        return make_password(int(self.var_pwd_level.get() or 0),
                             self.var_pwd_code.get())

    def _oper(self):
        return make_operator(self.var_oper.get())

    # ==================== 轮询 / 结果分发 ====================
    def _exclusive_begin(self):
        """进入独占操作: 暂停监测轮询, 让密码等操作的帧与日志不被淹没。"""
        self._exclusive += 1

    def _exclusive_end(self):
        self._exclusive = max(0, self._exclusive - 1)

    def _poll(self):
        # 独占操作(如读密码/改密)期间不发监测帧, 完成后自动恢复
        if self.worker.connected and self._exclusive == 0:
            if self._stale:
                # 无应答期间只发一个探测读, 避免队列积压; 恢复后自动回到全量轮询
                self.worker.read(self.MONITOR_DIS[0])
            else:
                for di in self.MONITOR_DIS:
                    self.worker.read(di)
        self.after(1500, self._poll)

    def _drain(self):
        self.var_clock.set(datetime.now().strftime("%H:%M:%S"))
        # 连续多次读取失败(设备断电/通信中断) -> 监测数值清零, 避免显示过期值
        if (self.worker.connected and self.worker.fail_streak >= 3
                and not self._stale):
            self._stale = True
            self._set_monitor_text("通讯中断")
            self.var_status.set("设备无应答，监测数值已清零，等待恢复 ...")
        try:
            while True:
                item = self.worker.results.get_nowait()
                self._dispatch(*item)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _dispatch(self, kind, *args):
        if kind == "connected":
            mode = args[0] if args else "serial"
            self._mode = mode
            self.btn_disconnect.configure(state=tk.NORMAL)
            if mode == "sim":
                self.var_status.set("已连接[仿真模式] · 无硬件演示")
                self._log("==== 已连接 [仿真模式] ====")
                self._log("提示: 仿真模式下所有指令由内置虚拟断路器应答, "
                          "可安全演示分/合闸、试跳、参数读写等全部功能。")
            else:
                self.var_status.set("已连接[串口]")
                self._log("==== 已连接 [串口] ====")
            self._stale = False
            self._set_monitor_text("--")
            for di, _name in self.INFO_DIS:
                self.worker.read(di)
        elif kind == "disconnected":
            self.btn_connect.configure(state=tk.NORMAL)
            self.var_status.set("已断开连接")
            self._log("==== 连接已断开 ====")
            self._stale = False
            self._set_monitor_text("--")
            self._arc_left = None
        elif kind == "frame":
            self._on_frame(args[0], args[1])
        elif kind == "read":
            self._on_read(args[0], args[1])
        elif kind == "op":
            self._on_op(args[0], args[1])
        elif kind == "event":
            self._on_event(args[0], args[1])
        elif kind == "connect_failed":
            self.btn_connect.configure(state=tk.NORMAL)
            self.btn_disconnect.configure(state=tk.DISABLED)
            self.var_status.set("连接失败: %s" % args[0])
            self._log("[ERR] 连接失败: %s" % args[0])
        elif kind == "error":
            self._on_error(args[0])
        elif kind == "exclusive_end":
            self._exclusive_end()

    def _on_frame(self, direction, hexstr):
        try:
            ctrl = int(hexstr[16:18], 16)
            name = self.CTRL_NAMES.get(ctrl, "?")
        except Exception:
            name = ""
        t = datetime.now().strftime("%H:%M:%S")
        self._log("[%s %s%s] %s" % (t, direction, " " + name, hexstr))

    def _on_op(self, tag, ok):
        self.var_status.set("操作成功: %s" % tag)
        self._log("[OK] %s" % tag)

    def _on_error(self, msg):
        if self._stale:
            self.var_status.set("设备无应答，监测数值已清零 (%s)" % msg)
        else:
            self.var_status.set("失败: %s" % msg)
        self._log("[ERR] %s" % msg)

    # ==================== 读数据解析 ====================
    def _on_read(self, di, data):
        if self._stale:
            # 任一读取成功即视为设备恢复, 数值随后按轮询自动刷新
            self._stale = False
            self.var_status.set("设备已恢复应答")
            for w in self.lb_w.values():
                w.configure(foreground=C_TEXT)
        if di == (0x02, 0x01, 0xFF, 0x00):      # 三相电压
            vals = []
            for i in range(3):
                v = bcd_num(data[i * 2:i * 2 + 2]) / 10.0
                vals.append(v)
                self._lb("三相电压", i).set("%s相 %5.1f V" % ("ABC"[i], v))
            self._v3 = tuple(vals)
        elif di == (0x02, 0x02, 0xFF, 0x00):    # 三相电流
            vals = []
            for i in range(3):
                v = bcd_num(data[i * 3:i * 3 + 3]) / 10.0
                vals.append(v)
                self._lb("三相电流", i).set("%s相 %5.2f A" % ("ABC"[i], v))
            self._i3 = tuple(vals)
        elif di == (0x02, 0x90, 0xFF, 0x00):    # 剩余电流
            phase = data[0] if len(data) > 0 else 0
            res = bcd_num(data[1:3]) if len(data) >= 3 else 0
            self._residual = float(res)
            pn = {0: "-", 1: "A", 2: "B", 4: "C"}.get(phase, "?")
            self._lb("剩余电流").set("%s相 %5.1f mA" % (pn, res))
            self._color_lb("剩余电流", self._res_color(res))
        elif di == (0x02, 0x91, 0xFF, 0x00):    # 额定值
            rated = bcd_num(data[0:2]) if len(data) >= 2 else 0
            t = bcd_num(data[2:4]) if len(data) >= 4 else 0
            self._rated_res = float(rated)
            self._lb("额定动作值", 0).set("%d mA" % rated)
            self._lb("额定动作值", 1).set("%d ms" % t)
            self._color_lb("剩余电流", self._res_color(self._residual))
        elif di == (0x04, 0x00, 0x05, 0x01):    # 状态字1
            v = data[0] if data else 0
            self._st1 = v
            self._lb("运行状态字1").set("0x%02X  %s" % (v, dec_status1(v)))
            self._color_lb("运行状态字1", self._st_color())
            self._maybe_arc()
        elif di == (0x04, 0x00, 0x05, 0x02):    # 状态字2
            v = data[0] if data else 0
            self._lb("运行状态字2").set("0x%02X  %s" % (v, dec_status2(v)))
        elif di == (0x03, 0x30, 0x01, 0x00):    # 清零次数
            self.var_stat.set("数据清零次数: %d 次" % bcd_num(data))
        elif di == (0x03, 0x81, 0x01, 0x01):    # 运行时间(分)
            m = bcd_num(data)
            self.var_stat2.set("累计运行: %d 天 %d 小时 %d 分"
                               % (m // 1440, (m % 1440) // 60, m % 60))
        elif di == (0x03, 0x81, 0x00, 0xFF):    # 跳闸次数模块
            self._show_trip_counts(data)
        else:
            # 设备信息 / 参数
            for d, name in self.INFO_DIS:
                if d == di:
                    if d == (0x04, 0x00, 0x04, 0x01):   # 通信地址: 12位BCD(A0在前)
                        text = P.decode_addr(data)
                    else:
                        text = ascii_str(data)
                    getattr(self, "var_" + name).set(text)
                    return
            self._on_param_read(di, data)

    def _on_param_read(self, di, data):
        for label, (pdi, nbytes, scale) in self.param_dis.items():
            if pdi == di:
                if nbytes == 1 and di[2] == 0x10:
                    self.param_vars[label].set("%02X" % (data[0] & 0xFF))
                elif di == (0x04, 0x00, 0x12, 0x01):
                    # 定时自检 DDhhmm 为分量型, 按书写顺序解码
                    self.param_vars[label].set("%g" % (bcd_int(data) * scale))
                else:
                    # 数值量: 多字节低字节在前
                    self.param_vars[label].set("%g" % (bcd_num(data) * scale))
                return

    def _show_trip_counts(self, data):
        lines = []
        for k in range(12):
            if k * 2 + 1 < len(data):
                n = bcd_num(data[k * 2:k * 2 + 2])
            else:
                n = 0
            name = TRIP_COUNT_NAMES[k] if k < len(TRIP_COUNT_NAMES) \
                else "原因%02X" % k
            lines.append("%02X %-12s %4d 次" % (k, name, n))
        self.txt_trip.delete("1.0", tk.END)
        self.txt_trip.insert(tk.END, "\n".join(lines))

    # ==================== 事件记录 ====================
    def _on_event(self, di, data):
        if data is None:
            self._log("[ERR] 事件读取失败")
            return
        self._log("读取事件 %s 序号%d: %d 字节"
                  % (".".join("%02X" % x for x in di[:3]), di[3], len(data)))
        text = self._decode_event(di, data)
        if text is None:
            text = "未定义的事件格式: %s" % " ".join("%02X" % b
                                                 for b in data)
        self.txt_ev.insert(tk.END, text + "\n")
        self.txt_ev.see(tk.END)

    def _decode_event(self, di, data):
        d0 = di[0:3]
        if d0 == (0x03, 0x8E, 0x00):            # 保护动作
            reason = REASON_TABLE.get(data[0], "原因%02X" % data[0])
            ph = {0: "无", 1: "A", 2: "B", 3: "C"}.get(data[1], "?")
            t = bcd_time(data[2:8])
            res = bcd_num(data[8:10])
            v3 = " ".join("%.1f" % (bcd_num(data[10 + i * 2:12 + i * 2])
                                    / 10.0) for i in range(3))
            i3 = " ".join("%.2f" % (bcd_num(data[16 + i * 3:19 + i * 3])
                                    / 10.0) for i in range(3))
            dr = {0: "--", 1: "自动", 2: "手动"}.get(data[25], "?")
            return ("保护动作[%s] %s相 时刻%s | 剩余电流 %d mA | "
                    "电压[%s]V 电流[%s]A | 方式:%s"
                    % (reason, ph, t, res, v3, i3, dr))
        if d0 == (0x03, 0x8D, 0x00):            # 自检
            result = {0x11: "正常", 0x22: "故障"}.get(data[0], "%02X"
                                                     % data[0])
            t = bcd_time(data[1:7])
            way = {1: "自动", 2: "手动"}.get(data[7], "%02X" % data[7])
            return "自检事件[%s] 时刻%s 方式:%s" % (result, t, way)
        if d0 == (0x03, 0x91, 0x00):            # 闸位变化
            st = {0: "分闸", 1: "合闸"}.get(data[0], "?")
            t = bcd_time(data[3:9])
            return "闸位变化->%s 时刻%s" % (st, t)
        if d0 == (0x03, 0x92, 0x00):            # 告警
            flag = "动作" if data[0] == 0x01 else "恢复"
            reason = REASON_TABLE.get(data[1], "原因%02X" % data[1])
            t = bcd_time(data[3:9])
            return "告警[%s] %s 时刻%s" % (flag, reason, t)
        if d0 == (0x03, 0x93, 0x00):            # 高压失/复电
            t1 = bcd_time(data[0:6])
            t2 = bcd_time(data[6:12])
            return "高压失电 时刻%s -> 复电 时刻%s" % (t1, t2)
        if d0 == (0x03, 0x8F, 0x00):            # 保护投退
            act = {0: "退出", 1: "投入"}.get(data[6], "?")
            t = bcd_time(data[0:6])
            return "保护%s 时刻%s" % (act, t)
        return None

    def _clear_data(self):
        if not self._require_client():
            return
        if not messagebox.askyesno("数据清零",
                                   "确定清零全部事件记录?\n该操作不可恢复。"):
            return
        self.worker.op(lambda c: c.clear_data((0xFF, 0xFF, 0xFF, 0xFF),
                                              self._pwd(), self._oper()),
                       "数据清零")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

