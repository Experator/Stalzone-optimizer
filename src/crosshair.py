import customtkinter as ctk
import tkinter as tk
from tkinter import colorchooser
import threading
import queue
import os
import ctypes
from ctypes import wintypes

try:
    import pygetwindow as gw
    GW_AVAILABLE = True
except ImportError:
    GW_AVAILABLE = False

from src.theme import Colors, FONTS

class CrosshairThread(threading.Thread):
    def __init__(self, settings_queue):
        super().__init__(daemon=True)
        self.settings_queue = settings_queue
        self.running = True
        
        self.target_window = "stalzone.exe"
        self.color = "#00FF00"
        self.shape = "Перекрестие"
        self.length = 12
        self.gap = 5
        self.thickness = 2
        self.radius = 6
        self.outline_enabled = False
        self.outline_color = "#FF0000"
        self.outline_thickness = 1
        self.is_enabled = False
        self.is_visible = False
        
    def run(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", "#010101")
        self.root.config(bg="#010101")
        
        self.canvas = tk.Canvas(self.root, bg="#010101", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.root.update_idletasks()
        self._make_click_through()
        
        self.root.withdraw()
        
        self.root.after(50, self.update_loop)
        self.root.mainloop()
        
    def _make_click_through(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
                
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        except Exception:
            pass
            
    def _get_exact_center(self, target_win):
        try:
            hwnd = target_win._hWnd
            if not hwnd:
                raise AttributeError("No HWND")
                
            client_rect = wintypes.RECT()
            ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client_rect))
            
            point = wintypes.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
            
            cx = point.x + (client_rect.right - client_rect.left) // 2
            cy = point.y + (client_rect.bottom - client_rect.top) // 2
            
            return cx, cy
        except Exception:
            cx = target_win.left + target_win.width // 2
            cy = target_win.top + target_win.height // 2
            return cx, cy
            
    def update_loop(self):
        if not self.running:
            self.root.destroy()
            return
            
        try:
            while True:
                settings = self.settings_queue.get_nowait()
                for key, val in settings.items():
                    setattr(self, key, val)
        except queue.Empty:
            pass
            
        should_show = False
        
        if self.is_enabled and GW_AVAILABLE and self.target_window:
            try:
                search_title = self.target_window.replace(".exe", "").replace(".EXE", "")
                wins = gw.getWindowsWithTitle(search_title)
                if wins:
                    target_win = wins[0]
                    active_win = gw.getActiveWindow()
                    
                    if target_win and active_win:
                        is_minimized = getattr(target_win, 'isMinimized', False)
                        valid_size = target_win.width > 100 and target_win.height > 100
                        valid_pos = target_win.left > -10000 and target_win.top > -10000
                        is_active = False
                        
                        if hasattr(active_win, '_hWnd') and hasattr(target_win, '_hWnd'):
                            is_active = (active_win._hWnd == target_win._hWnd)
                        else:
                            is_active = (active_win.title == target_win.title)
                            
                        if not is_minimized and valid_size and valid_pos and is_active:
                            should_show = True
                            cx, cy = self._get_exact_center(target_win)
                            ovl_size = 200
                            self.root.geometry(f"{ovl_size}x{ovl_size}+{cx - ovl_size//2}+{cy - ovl_size//2}")
                            self._draw_crosshair()
            except Exception:
                pass
                
        if should_show and not self.is_visible:
            self.root.deiconify()
            self.is_visible = True
            self._make_click_through()
        elif not should_show and self.is_visible:
            self.root.withdraw()
            self.is_visible = False
            
        self.root.after(50, self.update_loop)
        
    def _draw_crosshair(self):
        self.canvas.delete("all")
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        cx, cy = w // 2, h // 2
        
        c = self.color
        l = self.length
        g = self.gap
        t = self.thickness
        r = self.radius
        o_c = self.outline_color if self.outline_enabled else ""
        o_w = self.outline_thickness if self.outline_enabled else 0
        
        if self.shape in ("Перекрестие", "Перекрестие + точка"):
            self.canvas.create_line(cx, cy - g - l, cx, cy - g, fill=c, width=t)
            self.canvas.create_line(cx, cy + g, cx, cy + g + l, fill=c, width=t)
            self.canvas.create_line(cx - g - l, cy, cx - g, cy, fill=c, width=t)
            self.canvas.create_line(cx + g, cy, cx + g + l, cy, fill=c, width=t)
            
        if self.shape in ("Точка", "Перекрестие + точка", "Круг с точкой"):
            d = max(2, t)
            self.canvas.create_oval(cx - d, cy - d, cx + d, cy + d, fill=c, outline=o_c, width=o_w)
            
        if self.shape in ("Круг", "Круг с точкой"):
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=c, width=t)
            
        if self.shape == "Квадрат":
            self.canvas.create_rectangle(cx - l, cy - l, cx + l, cy + l, fill=c, outline=o_c, width=o_w)
            
        if self.shape == "Ромб":
            self.canvas.create_polygon(cx, cy - l, cx + l, cy, cx, cy + l, cx - l, cy, fill=c, outline=o_c, width=o_w)
            
        if self.shape == "Т-образный":
            self.canvas.create_line(cx - l, cy - g, cx + l, cy - g, fill=c, width=t)
            self.canvas.create_line(cx, cy - g, cx, cy + g + l, fill=c, width=t)
            
        if self.shape == "Треугольник":
            self.canvas.create_polygon(cx - l, cy + l, cx + l, cy + l, cx, cy - l, fill=c, outline=o_c, width=o_w)
            
        if self.shape == "Крест с засечками":
            self.canvas.create_line(cx, cy - g - l, cx, cy - g, fill=c, width=t)
            self.canvas.create_line(cx, cy + g, cx, cy + g + l, fill=c, width=t)
            self.canvas.create_line(cx - g - l, cy, cx - g, cy, fill=c, width=t)
            self.canvas.create_line(cx + g, cy, cx + g + l, cy, fill=c, width=t)
            
            self.canvas.create_line(cx - 3, cy - g, cx + 3, cy - g, fill=c, width=t)
            self.canvas.create_line(cx - 3, cy + g, cx + 3, cy + g, fill=c, width=t)
            self.canvas.create_line(cx - g, cy - 3, cx - g, cy + 3, fill=c, width=t)
            self.canvas.create_line(cx + g, cy - 3, cx + g, cy + 3, fill=c, width=t)
            
    def stop(self):
        self.running = False

class CrosshairTab(ctk.CTkFrame):
    def __init__(self, master, log_func=None, toast_func=None, **kwargs):
        super().__init__(master, fg_color=Colors.BG_DARK, corner_radius=10, **kwargs)
        self.log_func = log_func
        self.toast_func = toast_func
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.settings_queue = queue.Queue()
        self.crosshair_thread = None
        
        self._build_ui()
        self._sync_settings()
        
    def _build_ui(self):
        topbar = ctk.CTkFrame(self, fg_color=Colors.BG_PANEL, corner_radius=8, height=44)
        topbar.grid(row=0, column=0, sticky="ew", pady=(10, 8), padx=10)
        topbar.grid_columnconfigure(1, weight=1)
        topbar.grid_propagate(False)

        ctk.CTkLabel(
            topbar, text="Настройка игрового прицела", 
            font=FONTS["body_bold"], text_color=Colors.AMBER
        ).grid(row=0, column=0, sticky="w", padx=14, pady=8)

        self.enable_switch = ctk.CTkSwitch(
            topbar, text="Включить прицел", 
            font=FONTS["small"], text_color=Colors.TEXT_PRIMARY,
            progress_color=Colors.EMERALD, button_color=Colors.TEXT_PRIMARY,
            button_hover_color=Colors.TEXT_SECONDARY, fg_color=Colors.BG_DARK,
            command=self.on_toggle
        )
        self.enable_switch.grid(row=0, column=1, sticky="e", padx=14, pady=8)

        panel = ctk.CTkFrame(self, fg_color=Colors.BG_PANEL, corner_radius=8)
        panel.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        panel.grid_columnconfigure(1, weight=1)

        row = 0
        
        ctk.CTkLabel(
            panel, text="Окно игры (заголовок или .exe):", 
            font=FONTS["body"], text_color=Colors.TEXT_SECONDARY, anchor="w"
        ).grid(row=row, column=0, sticky="w", padx=14, pady=8)
        self.target_entry = ctk.CTkEntry(
            panel, font=FONTS["mono_small"], fg_color=Colors.BG_DARK, text_color=Colors.TEXT_PRIMARY
        )
        self.target_entry.insert(0, "stalzone.exe")
        self.target_entry.grid(row=row, column=1, sticky="ew", padx=14, pady=8)
        row += 1

        ctk.CTkLabel(
            panel, text="Тип прицела:", 
            font=FONTS["body"], text_color=Colors.TEXT_SECONDARY, anchor="w"
        ).grid(row=row, column=0, sticky="w", padx=14, pady=8)
        self.shape_menu = ctk.CTkOptionMenu(
            panel, values=["Перекрестие", "Точка", "Круг", "Перекрестие + точка", "Квадрат", "Т-образный", "Круг с точкой", "Ромб", "Треугольник", "Крест с засечками"],
            fg_color=Colors.BG_DARK, button_color=Colors.AMBER,
            button_hover_color=Colors.AMBER_DARK, text_color=Colors.TEXT_PRIMARY,
            command=lambda e: self._sync_settings()
        )
        self.shape_menu.set("Перекрестие")
        self.shape_menu.grid(row=row, column=1, sticky="w", padx=14, pady=8)
        row += 1

        ctk.CTkLabel(
            panel, text="Цвет:", 
            font=FONTS["body"], text_color=Colors.TEXT_SECONDARY, anchor="w"
        ).grid(row=row, column=0, sticky="w", padx=14, pady=8)
        self.color_btn = ctk.CTkButton(
            panel, text="Выбрать цвет", fg_color=Colors.BG_DARK, border_width=1,
            border_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.choose_color
        )
        self.color_btn.grid(row=row, column=1, sticky="w", padx=14, pady=8)
        self.current_color = "#00FF00"
        row += 1

        sliders_frame = ctk.CTkFrame(panel, fg_color="transparent")
        sliders_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        sliders_frame.grid_columnconfigure(1, weight=1)
        row += 1

        ctk.CTkLabel(sliders_frame, text="Длина линий:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.length_slider = ctk.CTkSlider(sliders_frame, from_=2, to=30, command=self._sync_settings)
        self.length_slider.set(12)
        self.length_slider.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        self.length_val = ctk.CTkLabel(sliders_frame, text="12", font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=30)
        self.length_val.grid(row=0, column=2, padx=5)
        
        ctk.CTkLabel(sliders_frame, text="Отступ от центра:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.gap_slider = ctk.CTkSlider(sliders_frame, from_=0, to=20, command=self._sync_settings)
        self.gap_slider.set(5)
        self.gap_slider.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        self.gap_val = ctk.CTkLabel(sliders_frame, text="5", font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=30)
        self.gap_val.grid(row=1, column=2, padx=5)
        
        ctk.CTkLabel(sliders_frame, text="Толщина:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.thick_slider = ctk.CTkSlider(sliders_frame, from_=1, to=10, command=self._sync_settings)
        self.thick_slider.set(2)
        self.thick_slider.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        self.thick_val = ctk.CTkLabel(sliders_frame, text="2", font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=30)
        self.thick_val.grid(row=2, column=2, padx=5)
        
        ctk.CTkLabel(sliders_frame, text="Радиус круга:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.rad_slider = ctk.CTkSlider(sliders_frame, from_=2, to=30, command=self._sync_settings)
        self.rad_slider.set(6)
        self.rad_slider.grid(row=3, column=1, sticky="ew", padx=5, pady=5)
        self.rad_val = ctk.CTkLabel(sliders_frame, text="6", font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=30)
        self.rad_val.grid(row=3, column=2, padx=5)

        ctk.CTkLabel(sliders_frame, text="Обводка:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.outline_switch = ctk.CTkSwitch(
            sliders_frame, text="", 
            progress_color=Colors.EMERALD, button_color=Colors.TEXT_PRIMARY,
            button_hover_color=Colors.TEXT_SECONDARY, fg_color=Colors.BG_DARK,
            command=self._sync_settings
        )
        self.outline_switch.grid(row=4, column=1, sticky="w", padx=5, pady=5)

        ctk.CTkLabel(sliders_frame, text="Цвет обводки:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=5, column=0, sticky="w", padx=5, pady=5)
        self.outline_color_btn = ctk.CTkButton(
            sliders_frame, text="Выбрать цвет", fg_color=Colors.BG_DARK, border_width=1,
            border_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.choose_outline_color
        )
        self.outline_color_btn.grid(row=5, column=1, sticky="w", padx=5, pady=5)
        self.outline_color = "#FF0000"

        ctk.CTkLabel(sliders_frame, text="Толщина обводки:", font=FONTS["small"], text_color=Colors.TEXT_SECONDARY).grid(row=6, column=0, sticky="w", padx=5, pady=5)
        self.out_thick_slider = ctk.CTkSlider(sliders_frame, from_=1, to=10, command=self._sync_settings)
        self.out_thick_slider.set(1)
        self.out_thick_slider.grid(row=6, column=1, sticky="ew", padx=5, pady=5)
        self.out_thick_val = ctk.CTkLabel(sliders_frame, text="1", font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=30)
        self.out_thick_val.grid(row=6, column=2, padx=5)

    def choose_color(self):
        color = colorchooser.askcolor(title="Выбор цвета прицела", initialcolor=self.current_color)
        if color and color[1] and color[1].lower() != "#010101":
            self.current_color = color[1]
            r = int(color[1][1:3], 16)
            g = int(color[1][3:5], 16)
            b = int(color[1][5:7], 16)
            text_color = "#000000" if (r + g + b) / 3 > 128 else "#FFFFFF"
            self.color_btn.configure(fg_color=self.current_color, text_color=text_color)
            self._sync_settings()

    def choose_outline_color(self):
        color = colorchooser.askcolor(title="Выбор цвета обводки", initialcolor=self.outline_color)
        if color and color[1] and color[1].lower() != "#010101":
            self.outline_color = color[1]
            r = int(color[1][1:3], 16)
            g = int(color[1][3:5], 16)
            b = int(color[1][5:7], 16)
            text_color = "#000000" if (r + g + b) / 3 > 128 else "#FFFFFF"
            self.outline_color_btn.configure(fg_color=self.outline_color, text_color=text_color)
            self._sync_settings()

    def _sync_settings(self, *args):
        l = int(self.length_slider.get())
        g = int(self.gap_slider.get())
        t = int(self.thick_slider.get())
        r = int(self.rad_slider.get())
        o_t = int(self.out_thick_slider.get())
        
        self.length_val.configure(text=str(l))
        self.gap_val.configure(text=str(g))
        self.thick_val.configure(text=str(t))
        self.rad_val.configure(text=str(r))
        self.out_thick_val.configure(text=str(o_t))
        
        settings = {
            "shape": self.shape_menu.get(),
            "color": self.current_color,
            "length": l,
            "gap": g,
            "thickness": t,
            "radius": r,
            "outline_enabled": bool(self.outline_switch.get()),
            "outline_color": self.outline_color,
            "outline_thickness": o_t,
            "target": self.target_entry.get(),
            "is_enabled": bool(self.enable_switch.get())
        }
        self.settings_queue.put(settings)

    def on_toggle(self):
        if self.enable_switch.get():
            if self.crosshair_thread is None or not self.crosshair_thread.is_alive():
                self.crosshair_thread = CrosshairThread(self.settings_queue)
                self.crosshair_thread.start()
            if self.toast_func: self.toast_func("Прицел включен", Colors.EMERALD)
            if self.log_func: self.log_func("Crosshair overlay enabled", "INFO")
        else:
            if self.toast_func: self.toast_func("Прицел выключен", Colors.TEXT_SECONDARY)
            if self.log_func: self.log_func("Crosshair overlay disabled", "INFO")
        self._sync_settings()

    def stop_overlay(self):
        if self.crosshair_thread:
            self.crosshair_thread.stop()
            self.crosshair_thread = None
