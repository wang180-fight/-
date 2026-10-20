# -*- coding: utf-8 -*-
"""断路器实物面板。

用 Canvas 画出一台"看得见、点得动"的智能塑壳断路器:
    模压外壳 / 铭牌 / 三位置手柄(合闸-脱扣-分闸) / 脱扣指示窗 /
    6 个指示灯 / 液晶显示 / 合闸-分闸-试验-复位按钮 / 本地-远程拨钮 /
    进线端子 / 出线端子 / RS485 接口, 以及电源、负载、上位机接线示意。

坐标一律使用 1040x640 的设计坐标系, 绘制时按窗口大小等比缩放,
窗口拉大拉小都不变形; 交互区域在 redraw() 里登记到 self._zones,
点击/移动时按设计坐标判断命中的是手柄、按钮还是拨钮。
"""

import math
import tkinter as tk

from dl645_breaker.simulator import REASON_TABLE

# ---- 判定"脱扣"(保护跳闸/试验跳闸)的原因码: 遥控分闸 1EH 不算 ----
TRIP_REASONS = {0x02, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0D, 0x16}

PHASES = (("A", "#d97706"), ("B", "#16a34a"), ("C", "#dc2626"),
          ("N", "#1d4ed8"))
LAMP_COLORS = ("#d97706", "#16a34a", "#dc2626")


class BreakerPanel(tk.Canvas):
    """一台可操作的虚拟断路器(实物外观)。"""

    DW, DH = 1040, 640

    def __init__(self, master, font="Microsoft YaHei UI", on_command=None,
                 **kw):
        tk.Canvas.__init__(self, master, bg="#e7ebf1",
                           highlightthickness=0, **kw)
        self.font = font
        self.on_command = on_command
        self.snap = None
        self.listening = False
        self.link_text = "未监听"
        self._hp = 0.0                 # 手柄位置: 0=分闸 0.5=脱扣 1=合闸
        self._t = 0.0
        self._rx_seen = 0
        self._tx_seen = 0
        self._rx_glow = 0.0
        self._tx_glow = 0.0
        self._zones = []
        self._design = (1.0, 0.0, 0.0)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self.configure(cursor=""))
        self.bind("<Configure>", lambda e: self.redraw())
        self.after(90, self._tick)

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def set_state(self, snap, listening=None, link_text=None):
        """接收设备快照(engine.snapshot())并刷新面板。"""
        if listening is not None:
            self.listening = bool(listening)
        if link_text is not None:
            self.link_text = link_text
        if snap:
            if snap.get("rx", 0) != self._rx_seen:
                self._rx_seen = snap.get("rx", 0)
                self._rx_glow = 1.0
            if snap.get("tx", 0) != self._tx_seen:
                self._tx_seen = snap.get("tx", 0)
                self._tx_glow = 1.0
            self.snap = snap

    # ------------------------------------------------------------------
    # 动画与交互
    # ------------------------------------------------------------------
    def _tick(self):
        self._t += 0.09
        self._rx_glow = max(0.0, self._rx_glow - 0.12)
        self._tx_glow = max(0.0, self._tx_glow - 0.12)
        target = 0.0
        s = self.snap
        if s:
            if s.get("switch"):
                target = 1.0
            elif (s.get("reason", 0) & 0x1F) in TRIP_REASONS:
                target = 0.5
        self._hp += (target - self._hp) * 0.22
        if abs(target - self._hp) < 0.008:
            self._hp = target
        try:
            self.redraw()
        except tk.TclError:
            return
        self.after(90, self._tick)

    def _to_design(self, x, y):
        k, ox, oy = self._design
        if k <= 0:
            return None
        return ((x - ox) / k, (y - oy) / k)

    def _zone_at(self, x, y):
        p = self._to_design(x, y)
        if p is None:
            return None
        for name, x1, y1, x2, y2 in self._zones:
            if x1 <= p[0] <= x2 and y1 <= p[1] <= y2:
                return name
        return None

    def _on_click(self, event):
        name = self._zone_at(event.x, event.y)
        if name and self.on_command:
            self.on_command(name)

    def _on_motion(self, event):
        self.configure(cursor="hand2" if self._zone_at(event.x, event.y)
                       else "")

    def _zone(self, name, x1, y1, x2, y2):
        self._zones.append((name, x1, y1, x2, y2))

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def redraw(self):
        c = self
        try:
            W, H = c.winfo_width(), c.winfo_height()
        except tk.TclError:
            return
        if W < 80 or H < 80:
            return
        k = min(W / float(self.DW), H / float(self.DH))
        ox = (W - self.DW * k) / 2.0
        oy = (H - self.DH * k) / 2.0
        self._design = (k, ox, oy)
        c.delete("all")
        self._zones = []

        def X(x):
            return ox + x * k

        def Y(y):
            return oy + y * k

        def FS(n):
            return max(6, int(round(n * k)))

        def lw(n=1.0):
            return max(1, int(round(n * k)))

        def R(x1, y1, x2, y2, **kw):
            return c.create_rectangle(X(x1), Y(y1), X(x2), Y(y2), **kw)

        def L(x1, y1, x2, y2, **kw):
            kw.setdefault("width", lw())
            return c.create_line(X(x1), Y(y1), X(x2), Y(y2), **kw)

        def O(x1, y1, x2, y2, **kw):
            return c.create_oval(X(x1), Y(y1), X(x2), Y(y2), **kw)

        def TX(x, y, text, size=9, weight="normal", fill="#0f172a",
               anchor="center", family=None, **kw):
            return c.create_text(X(x), Y(y), text=text, fill=fill,
                                 anchor=anchor,
                                 font=(family or self.font, FS(size),
                                       weight), **kw)

        def RR(x1, y1, x2, y2, r=8, **kw):
            rr = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
            pts = [(x1 + rr, y1), (x2 - rr, y1), (x2, y1), (x2, y1 + rr),
                   (x2, y2 - rr), (x2, y2), (x2 - rr, y2), (x1 + rr, y2),
                   (x1, y2), (x1, y2 - rr), (x1, y1 + rr), (x1, y1)]
            flat = []
            for px, py in pts:
                flat.extend((X(px), Y(py)))
            return c.create_polygon(flat, smooth=True, splinesteps=18, **kw)

        s = self.snap or {
            "switch": 0, "reason": 0, "v3": (0.0, 0.0, 0.0),
            "i3": (0.0, 0.0, 0.0), "residual": 0.0, "rated_res": 300,
            "rated_t": 100, "running": True, "rx": 0, "tx": 0,
            "fault": "none", "allow_remote": True, "ir1": 250.0,
        }
        v3 = tuple(s.get("v3") or (0.0, 0.0, 0.0))
        i3 = tuple(s.get("i3") or (0.0, 0.0, 0.0))
        switch = bool(s.get("switch"))
        reason = int(s.get("reason", 0)) & 0x1F
        tripped = (not switch) and reason in TRIP_REASONS
        running = bool(s.get("running", True))
        residual = float(s.get("residual", 0.0))
        rated = int(s.get("rated_res", 300) or 300)
        energized = switch and running and max(i3) > 0.2

        # ============================ 背景 ============================
        R(0, 0, self.DW, self.DH, fill="#e7ebf1", outline="")
        R(0, 0, self.DW, 24, fill="#d3dae3", outline="")
        R(0, self.DH - 20, self.DW, self.DH, fill="#d3dae3", outline="")
        for y1 in (28, self.DH - 46):
            RR(24, y1, self.DW - 24, y1 + 16, 4, fill="#cfd6df",
               outline="#b7bfca")
            for x in range(42, self.DW - 40, 26):
                L(x, y1 + 3, x, y1 + 13, fill="#b7bfca")

        # ============================ 三相电源 ============================
        RR(48, 140, 292, 430, 10, fill="#f8fafc", outline="#c3cad4",
           width=lw(1.5))
        TX(170, 164, "三相电源", 12, "bold", "#0f172a")
        TX(170, 184, "400V / 50Hz", 8, fill="#64748b")
        for name, col in PHASES:
            y = 213 + ["A", "B", "C", "N"].index(name) * 50
            L(72, y, 292, y, fill=col, width=lw(2.4))
            TX(86, y - 15, name, 9, "bold", col, anchor="w")
            TX(104, y - 2, "~", 13, "bold", col)

        # ============================ 三相负载 ============================
        RR(816, 140, 1000, 430, 10, fill="#f8fafc", outline="#c3cad4",
           width=lw(1.5))
        TX(908, 164, "三相负载", 12, "bold", "#0f172a")
        TX(908, 184, "合闸后带电", 8, fill="#64748b")
        for idx, col in enumerate(LAMP_COLORS):
            y = 213 + idx * 50
            L(762, y, 816, y, fill=col, width=lw(2.4))
            if energized:
                O(848, y - 24, 908, y + 24, fill="#fef3c7", outline="")
            O(856, y - 16, 888, y + 16,
              fill="#fde047" if energized else "#e9eef4",
              outline=col, width=lw(1.8))
            TX(872, y, "L%d" % (idx + 1), 8, "bold",
               "#78350f" if energized else "#94a3b8")
        L(762, 363, 816, 363, fill="#1d4ed8", width=lw(2.4))
        TX(800, 352, "N", 7, "bold", "#1d4ed8")
        # 电机: 带电时叶片旋转
        O(878, 352, 938, 412, fill="#eef2f7", outline="#94a3b8",
          width=lw(1.4))
        ang0 = self._t * (9.0 if energized else 0.0)
        for b in range(3):
            a = ang0 + b * 2.0944
            L(908, 382, 908 + 22 * math.cos(a), 382 + 22 * math.sin(a),
              fill="#475569", width=lw(2.2))
        O(902, 376, 914, 388, fill="#475569", outline="")
        TX(908, 424, "M", 9, "bold", "#475569")

        # ============================ 电流流动 ============================
        if energized:
            for idx, (name, col) in enumerate(PHASES[:3]):
                amp = max(0.05, min(1.0, abs(i3[idx])
                                      / max(s.get("ir1") or 250.0, 1.0)))
                speed = 0.5 + amp * 1.6
                for x1, x2 in ((292, 330), (762, 816)):
                    for n in range(3):
                        frac = (self._t * speed + n / 3.0) % 1.0
                        px = x1 + (x2 - x1) * frac
                        y = 213 + idx * 50
                        O(px - 3.2, y - 3.2, px + 3.2, y + 3.2,
                          fill="#fef9c3", outline=col)

        # ============================ 断路器本体 ============================
        RR(338, 98, 770, 566, 16, fill="#c2c9d3", outline="")      # 投影
        RR(330, 88, 762, 556, 16, fill="#aab2bd", outline="")      # 外壳边
        RR(332, 90, 760, 554, 15, fill="#f7f8f5", outline="#9aa4b0",
           width=lw(1.2))
        RR(344, 102, 748, 542, 10, fill="#f1f2ef", outline="#dde1e6")

        # ---- 铭牌 ----
        RR(352, 104, 740, 162, 5, fill="#2b3441", outline="#1b222c")
        TX(366, 122, "DZ20L-250/4300", 14, "bold", "#f8fafc", anchor="w")
        TX(556, 122, "智能型塑壳断路器", 9, "bold", "#9fd8c4", anchor="w")
        TX(366, 146, "Ue 400V   In 250A   IΔn 300mA   Ui 800V   50Hz",
           8, fill="#cbd5e1", anchor="w")
        TX(736, 146, "DL/T 645-2007", 8, fill="#94a3b8", anchor="e")

        # ---- 手柄槽 ----
        RR(366, 176, 472, 424, 12, fill="#242a32", outline="#151a20")
        RR(380, 190, 458, 410, 9, fill="#141920", outline="#0b0f14")
        # 三位置刻度(槽右侧: 上=合闸 中=脱扣 下=分闸)
        for y, cn, en, col in ((200, "合闸", "ON", "#0f172a"),
                               (286, "脱扣", "TRIP", "#b45309"),
                               (372, "分闸", "OFF", "#0f172a")):
            TX(482, y, cn, 9, "bold", col, anchor="w")
            TX(482, y + 13, en, 6, "bold", "#94a3b8", anchor="w")

        # 手柄(随状态上下滑动: 上=合闸 中=脱扣 下=分闸)
        hy = 334 - self._hp * 138
        RR(386, hy + 8, 460, hy + 76, 9, fill="#20262e", outline="")
        RR(386, hy, 458, hy + 68, 9, fill="#3c434d", outline="#20262e")
        RR(386, hy, 458, hy + 32, 9, fill="#4d5763", outline="")
        for i in range(3):
            L(400, hy + 44 + i * 8, 444, hy + 44 + i * 8,
              fill="#2a3037", width=lw(1.6))
        if switch:
            O(414, hy + 12, 430, hy + 28, fill="#16a34a", outline="#ffffff")
        elif tripped:
            O(414, hy + 12, 430, hy + 28, fill="#f59e0b", outline="#ffffff")
        else:
            O(414, hy + 12, 430, hy + 28, fill="#94a3b8", outline="#ffffff")
        self._zone("handle", 366, 176, 472, 424)

        # ---- 脱扣指示窗 ----
        RR(556, 176, 646, 208, 4, fill="#fbfcfd", outline="#c3cad4")
        TX(601, 192, "TRIP" if tripped else "—", 12, "bold",
           "#dc2626" if tripped else "#cbd5e1")
        TX(601, 224, "脱扣指示", 8, fill="#64748b")

        # ---- 液晶显示 ----
        RR(520, 236, 672, 322, 6, fill="#0c3b2e", outline="#08281f")
        if switch:
            st = "合闸 ON"
        elif tripped:
            st = "脱扣 %s" % REASON_TABLE.get(reason, "%02XH" % reason)
        else:
            st = "分闸 OFF"
        TX(530, 250, "U %5.1f %5.1f %5.1f V" % v3, 8,
           fill="#7ef2c8", anchor="w", family="Consolas")
        TX(530, 268, "I %5.2f %5.2f %5.2f A" % i3, 8,
           fill="#7ef2c8", anchor="w", family="Consolas")
        TX(530, 286, "IΔ %4.0f / %d mA" % (residual, rated), 8,
           fill="#fde68a", anchor="w", family="Consolas")
        TX(530, 306, st, 8, "bold",
           "#fca5a5" if tripped else "#7ef2c8", anchor="w")

        # ---- 指示灯 ----
        leds = [
            ("电源", running, "#16a34a"),
            ("运行", running, "#22c55e"),
            ("合闸", switch, "#dc2626"),
            ("跳闸", tripped, "#f59e0b"),
            ("漏电", running and residual >= rated * 0.9, "#eab308"),
            ("故障", tripped, "#dc2626"),
        ]
        for idx, (name, on, col) in enumerate(leds):
            y = 240 + idx * 22
            if on:
                O(678, y - 9, 696, y + 9, fill="#f1f5f9", outline="")
                O(681, y - 6, 693, y + 6, fill=col, outline="#ffffff")
            else:
                O(681, y - 6, 693, y + 6, fill="#dbe1e8", outline="#c3cad4")
            TX(698, y, name, 7, "bold" if on else "normal",
               "#0f172a" if on else "#94a3b8", anchor="w")


        # ---- 操作按钮 ----
        buttons = [
            ("btn_close", 392, "I", "合闸", "#16a34a", "#ffffff"),
            ("btn_open", 462, "O", "分闸", "#dc2626", "#ffffff"),
            ("btn_test", 532, "T", "试验", "#f59e0b", "#ffffff"),
            ("btn_reset", 602, "R", "复位", "#e2e8f0", "#334155"),
        ]
        for name, bx, mark, label, fill, fg in buttons:
            O(bx - 26, 402, bx + 26, 454, fill="#d7dce4", outline="")
            O(bx - 23, 405, bx + 23, 451, fill=fill, outline="#ffffff",
              width=lw(1.4))
            TX(bx, 428, mark, 15, "bold", fg)
            TX(bx, 468, label, 8, "bold", "#334155")
            self._zone(name, bx - 26, 402, bx + 26, 460)

        # ---- 本地 / 远程 拨钮 ----
        remote = bool(s.get("allow_remote", True))
        TX(688, 390, "本地 / 远程", 7, fill="#64748b")
        RR(652, 400, 724, 424, 12, fill="#d8dee7", outline="#b7bfca")
        RR(654, 402, 722, 422, 10,
           fill="#0f766e" if remote else "#cbd5e1", outline="")
        kx = 700 if remote else 656
        RR(kx, 402, kx + 20, 422, 8, fill="#ffffff", outline="#94a3b8")
        self._zone("remote", 648, 396, 728, 428)


        # ---- 进出线端子 ----
        for idx, (name, col) in enumerate(PHASES):
            y = 213 + idx * 50
            for tx1 in (334, 730):
                R(tx1, y - 15, tx1 + 28, y + 15, fill="#6b7280",
                  outline="#4b5563")
                O(tx1 + 6, y - 8, tx1 + 22, y + 8, fill="#d1d5db",
                  outline="#6b7280")
                L(tx1 + 7, y - 7, tx1 + 21, y + 7, fill="#6b7280")
                L(tx1 + 7, y + 7, tx1 + 21, y - 7, fill="#6b7280")
            TX(348, y + 26, name, 7, "bold", col)
            TX(744, y + 26, name, 7, "bold", col)

        TX(500, 496, "就地操作", 7, fill="#94a3b8")

        # ---- RS485 通信模块 ----
        RR(764, 430, 826, 506, 6, fill="#e8edf3", outline="#94a3b8")
        TX(795, 444, "RS485", 8, "bold", "#334155")
        for idx, (tag, y) in enumerate((("收", 466), ("发", 488))):
            glow = self._rx_glow if idx == 0 else self._tx_glow
            col = "#f59e0b" if idx == 0 else "#22c55e"
            O(770, y - 4, 778, y + 4,
              fill=col if glow > 0.15 else "#cbd5e1", outline="")
            TX(786, y, tag, 6, fill="#64748b")
        for name, y in (("A", 468), ("B", 490)):
            R(806, y - 8, 822, y + 8, fill="#9aa4b0", outline="#6b7280")
            TX(800, y, name, 7, "bold", "#334155", anchor="e")

        # ---- 上位机 ----
        RR(850, 430, 1010, 560, 8, fill="#f8fafc", outline="#c3cad4")
        TX(930, 446, "上位机 (主站)", 9, "bold", "#0f172a")
        R(884, 458, 956, 506, fill="#334155", outline="#1f2937")
        R(890, 463, 950, 500, fill="#0b1220", outline="")
        for i in range(3):
            L(894, 470 + i * 9, 894 + (40 - i * 8), 470 + i * 9,
              fill="#22d3ee", width=lw(1.2))
        L(920, 506, 920, 512, fill="#475569", width=lw(2))
        L(904, 514, 936, 514, fill="#475569", width=lw(2.4))
        TX(930, 528, self.link_text, 7,
           fill="#0f766e" if self.listening else "#94a3b8")
        TX(930, 544, "DL/T 645 · 9600 8E1", 6, fill="#94a3b8")
        # A/B 双绞线
        L(826, 468, 850, 468, fill="#0f766e", width=lw(2))
        L(826, 490, 850, 490, fill="#0f766e", width=lw(2))
        L(834, 468, 842, 490, fill="#0f766e", width=lw(1))
        L(842, 468, 834, 490, fill="#0f766e", width=lw(1))

        # ---- 底部提示 ----
        TX(546, 600,
           "点击[手柄]可就地合闸/分闸; 保护脱扣后手柄停在中间, 需按[R]复位",
           8, fill="#64748b")





