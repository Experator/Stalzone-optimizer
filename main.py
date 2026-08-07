import customtkinter as ctk
import threading
import queue
import os
import sys
import ctypes
from datetime import datetime
from tkinter import filedialog, messagebox

from customtkinter import CTkFont

font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Arimo-SemiBold.ttf")
if os.path.exists(font_path):
    try:
        ctypes.windll.gdi32.AddFontResourceW(font_path)
        hwnd = ctypes.windll.user32.GetDesktopWindow()
        ctypes.windll.user32.SendMessageW(hwnd, 0x001D, 0, 0) # WM_FONTCHANGE
    except Exception:
        pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.theme import (
    Colors, CATEGORY_LABELS, CATEGORY_ORDER, CATEGORY_ICONS,
    TIER_INFO, IMPACT_COLORS, IMPACT_LABELS, FONTS,
    PROCESS_CATEGORY_LABELS, PROCESS_CATEGORY_COLORS, PROCESS_ACTION_LABELS,
)
from src.models import (
    HardwareReport, TierAssessment, OptimizationToggle,
    OptimizationProfile, LiveMetrics, ApplyResult,
    ProcessInfo, ProcessAnalysis, SettingsBackup,
)
from src.hardware import detect_hardware, get_live_metrics
from src.analyzer import assess_tier, get_default_toggles
from src.optimizations import apply_optimizations, revert_optimizations
from src.script_generator import generate_script
from src.settings_backup import (
    capture_backup, save_backup, load_backup, restore_backup,
    backup_exists, create_backup_if_not_exists, get_backup_path,
)
from src.process_optimizer import (
    analyze_processes, optimize_processes, kill_background_apps,
    get_process_category, get_process_description,
)

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False

def progress_color_for(percent: float) -> str:
    if percent < 60:
        return Colors.EMERALD
    if percent < 85:
        return Colors.AMBER
    return Colors.RED

class LogSystem:
    LEVELS = {"DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
    LEVEL_COLORS = {
        "DEBUG": Colors.LOG_DEBUG,
        "INFO": Colors.LOG_INFO,
        "SUCCESS": Colors.LOG_SUCCESS,
        "WARNING": Colors.LOG_WARNING,
        "ERROR": Colors.LOG_ERROR,
        "CRITICAL": Colors.LOG_CRITICAL,
    }

    def __init__(self, max_lines: int = 1000):
        self._queue: "queue.Queue" = queue.Queue()
        self._max_lines = max_lines
        self._lines: list = []
        self._textbox = None

    def attach(self, textbox):
        self._textbox = textbox

    def log(self, message: str, level: str = "INFO"):
        if level not in self.LEVELS:
            level = "INFO"
        ts = datetime.now().strftime("%H:%M:%S")
        entry = (ts, level, message)
        self._queue.put(entry)
        self._lines.append(entry)
        if len(self._lines) > self._max_lines:
            self._lines = self._lines[-self._max_lines:]

    def drain(self):
        if self._textbox is None:
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
            return
        count = 0
        while count < 50:
            try:
                ts, level, message = self._queue.get_nowait()
            except queue.Empty:
                break
            color = self.LEVEL_COLORS.get(level, Colors.TEXT_SECONDARY)
            tag = f"[{ts}] {level:8s}"
            line = f"{tag} | {message}\n"
            try:
                self._textbox.configure(state="normal")
                self._textbox.insert("end", f"{tag} | ", level)
                self._textbox.insert("end", f"{message}\n")
                self._textbox.tag_config(level, foreground=color)
                self._textbox.tag_add(level, "end-2l linestart", "end-2l lineend")
                self._textbox.configure(state="disabled")
                self._textbox.see("end")
            except Exception:
                pass
            count += 1

    def clear(self):
        self._lines.clear()
        if self._textbox:
            try:
                self._textbox.configure(state="normal")
                self._textbox.delete("1.0", "end")
                self._textbox.configure(state="disabled")
            except Exception:
                pass

log_system = LogSystem()

def log(message: str, level: str = "INFO"):
    log_system.log(message, level)

# MAIN

class StalZoneApp(ctk.CTk):
    POLL_INTERVAL_SEC = 3
    DEFAULT_GAME_NAMES = ["Stalcraft.exe", "Stalcraftw.exe", "Stalzone.exe", "Stalzonew.exe"]
    
    def __init__(self):
        super().__init__()
        self.app_state = {
            "report": None,
            "tier": None,
            "toggles": [],
            "profile": self._make_default_profile(),
            "metrics": None,
            "loading": True,
            "applying": False,
            "game_names": list(self.DEFAULT_GAME_NAMES),
            "admin": is_admin(),
            "backup": None,
            "backup_path": None,
            "process_analysis": None,
        }

        self.w = {}
        self.toggle_switches = {}
        self.toggle_rows = {}
        self.core_bars = []
        self._stop_event = threading.Event()
        self._hw_queue: "queue.Queue" = queue.Queue()
        self._metrics_queue: "queue.Queue" = queue.Queue()
        self._apply_queue: "queue.Queue" = queue.Queue()
        self._proc_queue: "queue.Queue" = queue.Queue()
        self._backup_queue: "queue.Queue" = queue.Queue()
        
        self._x = None
        self._y = None

        self.setup_window()
        self.build_ui()
        self.start_background_threads()

        self.after(150, self._drain_logs)
        self.after(100, self._drain_queues)

    def _make_default_profile(self) -> OptimizationProfile:
        return OptimizationProfile(
            toggles=[],
            game_process_names=list(self.DEFAULT_GAME_NAMES),
            timer_resolution_ms=0.5,
            process_priority="high",
            cpu_affinity_mode="physical",
            custom_affinity_cores=[],
            memory_cleanup_interval_sec=300,
            aggressive_ram_cleanup=False,
        )

    def setup_window(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        try:
            ctk.ThemeManager("dark-blue").configure(
                button_color=Colors.AMBER,
                button_hover_color=Colors.AMBER_DARK,
            )
        except Exception:
            pass

        self.title("STALZONE OPTIMIZER")
        self.overrideredirect(True)
        self.configure(fg_color="#010101")
        if sys.platform == "win32":
            self.wm_attributes("-transparentcolor", "#010101")

        self.geometry("1100x900")
        self.minsize(900, 640)
        
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - 1100) // 2
        y = (sh - 640) // 2
        self.geometry(f"1100x670+{x}+{y}")

        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(default=icon_path)
        except Exception:
            pass

    def build_ui(self):
        self.main_frame = ctk.CTkFrame(self, corner_radius=15, fg_color=Colors.BG_DARK)
        self.main_frame.pack(fill="both", expand=True)
        
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=0)   
        self.main_frame.grid_rowconfigure(1, weight=1)   
        self.main_frame.grid_rowconfigure(2, weight=0)   

        self._build_header()
        self._build_main_content()
        self._build_footer()

    def _build_header(self):
        header = ctk.CTkFrame(
            self.main_frame, height=52, fg_color=Colors.BG_DARK,
            corner_radius=15, border_width=0,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)
 
        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=14)
        ctk.CTkLabel(
            left, text="STALZONE OPTIMIZER",
            font=FONTS["heading"], text_color=Colors.TEXT_PRIMARY,
        ).grid(row=0, column=1, padx=(0, 6), pady=8)

        self.w["toast"] = ctk.CTkLabel(
            header, text="Инициализация...",
            font=FONTS["small"], text_color=Colors.TEXT_SECONDARY,
        )
        self.w["toast"].grid(row=0, column=1, sticky="ew", pady=8)

        self.w["game_status"] = ctk.CTkLabel(
            header, text="❌ Игра не запущена",
            font=FONTS["small"], text_color=Colors.TEXT_MUTED,
        )
        self.w["game_status"].grid(row=0, column=2, sticky="e", padx=(0, 14), pady=8)

        ctrl_frame = ctk.CTkFrame(header, fg_color="transparent")
        ctrl_frame.grid(row=0, column=3, sticky="e", padx=(0, 10), pady=6)

        btn_min = ctk.CTkButton(
            ctrl_frame, text="—", width=30, height=30,
            font=FONTS["body_bold"], fg_color="transparent",
            hover_color=Colors.BG_PANEL_LIGHT, text_color=Colors.TEXT_SECONDARY,
            corner_radius=6, command=self._minimize_window
        )
        btn_min.grid(row=0, column=0, padx=(0, 4))

        btn_close = ctk.CTkButton(
            ctrl_frame, text="✕", width=30, height=30,
            font=FONTS["body_bold"], fg_color="transparent",
            hover_color=Colors.RED, text_color=Colors.TEXT_SECONDARY,
            corner_radius=6, command=self._close_window
        )
        btn_close.grid(row=0, column=1)

        for widget in [header, left, self.w["toast"], self.w["game_status"]]:
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<ButtonRelease-1>", self._stop_move)
            widget.bind("<B1-Motion>", self._on_move)

        ctk.CTkFrame(
            header, height=2, fg_color=Colors.AMBER, corner_radius=0,
        ).grid(row=1, column=0, columnspan=4, sticky="ew")

    def _start_move(self, event):
        self._x = event.x
        self._y = event.y

    def _stop_move(self, event):
        self._x = None
        self._y = None

    def _on_move(self, event):
        deltax = event.x - self._x
        deltay = event.y - self._y
        x = self.winfo_x() + deltax
        y = self.winfo_y() + deltay
        self.geometry(f"+{x}+{y}")

    def _minimize_window(self):
        self.bind('<Map>', self._on_restore_from_minimize)
        self.overrideredirect(False)
        self.state('iconic')

    def _on_restore_from_minimize(self, event):
        if self.state() == 'normal':
            self.overrideredirect(True)
            self.unbind('<Map>')
            if sys.platform == "win32":
                self.wm_attributes("-transparentcolor", "#010101")

    def _close_window(self):
        self.destroy()

    def _set_toast(self, text: str, color: str = Colors.TEXT_SECONDARY):
        lbl = self.w.get("toast")
        if lbl:
            lbl.configure(text=text, text_color=color)

    def _build_main_content(self):
        main_area = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        main_area.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        main_area.grid_columnconfigure(1, weight=1)
        main_area.grid_rowconfigure(0, weight=1)

        # SIDEBAR

        sidebar = ctk.CTkFrame(main_area, width=180, corner_radius=10, fg_color=Colors.BG_PANEL)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        sidebar.grid_propagate(False)

        self.nav_buttons = {}
        nav_items = [
            ("ОБЗОР", self.show_overview, ""),
            ("ОПТИМИЗАЦИЯ", self.show_optimizations, ""),
            ("ПРОЦЕССЫ", self.show_processes, ""),
            ("НАСТРОЙКИ", self.show_settings, ""),
            ("ЛОГИ", self.show_logs, ""),
        ]

        for i, (text, cmd, icon) in enumerate(nav_items):
            btn = ctk.CTkButton(
                sidebar, text=f"{icon}  {text}", height=50, width=160,
                font=FONTS["body_bold"], fg_color="transparent",
                hover_color=Colors.BG_PANEL_LIGHT, text_color=Colors.TEXT_SECONDARY,
                corner_radius=8, anchor="w",
                command=cmd
            )
            btn.grid(row=i, column=0, pady=5, padx=10)
            self.nav_buttons[text] = btn

        # --- Область контента ---
        self.content_frame = ctk.CTkFrame(main_area, fg_color="transparent")
        self.content_frame.grid(row=0, column=1, sticky="nsew")
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        self.tab_overview = ctk.CTkFrame(self.content_frame, fg_color=Colors.BG_DARK, corner_radius=10)
        self.tab_optimizations = ctk.CTkFrame(self.content_frame, fg_color=Colors.BG_DARK, corner_radius=10)
        self.tab_processes = ctk.CTkFrame(self.content_frame, fg_color=Colors.BG_DARK, corner_radius=10)
        self.tab_settings = ctk.CTkFrame(self.content_frame, fg_color=Colors.BG_DARK, corner_radius=10)
        self.tab_logs = ctk.CTkFrame(self.content_frame, fg_color=Colors.BG_DARK, corner_radius=10)

        self._build_overview_tab(self.tab_overview)
        self._build_optimizations_tab(self.tab_optimizations)
        self._build_processes_tab(self.tab_processes)
        self._build_settings_tab(self.tab_settings)
        self._build_logs_tab(self.tab_logs)

        self.show_overview()

    def show_overview(self):
        self._show_tab(self.tab_overview, "ОБЗОР")

    def show_optimizations(self):
        self._show_tab(self.tab_optimizations, "ОПТИМИЗАЦИЯ")

    def show_processes(self):
        self._show_tab(self.tab_processes, "ПРОЦЕССЫ")

    def show_settings(self):
        self._show_tab(self.tab_settings, "НАСТРОЙКИ")

    def show_logs(self):
        self._show_tab(self.tab_logs, "ЛОГИ")

    def _show_tab(self, tab_frame, name):
        for f in [self.tab_overview, self.tab_optimizations, self.tab_processes, self.tab_settings, self.tab_logs]:
            f.grid_forget()
        tab_frame.grid(row=0, column=0, sticky="nsew")
        
        for n, btn in self.nav_buttons.items():
            if n == name:
                btn.configure(fg_color=Colors.EMERALD_DARK, text_color=Colors.TEXT_PRIMARY)
            else:
                btn.configure(fg_color="transparent", text_color=Colors.TEXT_SECONDARY)

    def _build_overview_tab(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)
       
        left = ctk.CTkFrame(parent, fg_color=Colors.BG_PANEL, corner_radius=8)
        left.grid(row=0, column=0, sticky="nsw", padx=(10, 8), pady=10)
        left.grid_propagate(False)
        left.configure(width=280) 

        ctk.CTkLabel(
            left, text="Характеристики ПК", font=FONTS["subheading"],
            text_color=Colors.AMBER,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        self.w["specs_grid"] = ctk.CTkFrame(left, fg_color="transparent")
        self.w["specs_grid"].grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        left.grid_columnconfigure(0, weight=1)
        
        self.w["specs_loading"] = ctk.CTkLabel(
            left, text="⏳ Определение оборудования...",
            font=FONTS["body"], text_color=Colors.TEXT_MUTED,
        )
        self.w["specs_loading"].grid(row=2, column=0, columnspan=2, padx=12, pady=20)
        
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        
        tier_card = ctk.CTkFrame(right, fg_color=Colors.BG_PANEL, corner_radius=8)
        tier_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tier_card.grid_columnconfigure(0, weight=1)

        self.w["tier_badge"] = ctk.CTkLabel(
            tier_card, text="—", font=FONTS["tier_big"],
            text_color=Colors.TEXT_PRIMARY, fg_color=Colors.BG_PANEL_LIGHT,
            corner_radius=6, height=46,
        )
        self.w["tier_badge"].grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        info_row = ctk.CTkFrame(tier_card, fg_color="transparent")
        info_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        info_row.grid_columnconfigure(0, weight=1)
        info_row.grid_columnconfigure(1, weight=1)

        self.w["tier_score"] = ctk.CTkLabel(
            info_row, text="Score: —", font=FONTS["body_bold"],
            text_color=Colors.TEXT_SECONDARY,
        )
        self.w["tier_score"].grid(row=0, column=0, sticky="w")

        self.w["tier_fps"] = ctk.CTkLabel(
            info_row, text="FPS: —", font=FONTS["body_bold"],
            text_color=Colors.TEXT_SECONDARY,
        )
        self.w["tier_fps"].grid(row=0, column=1, sticky="e")

        self.w["tier_desc"] = ctk.CTkLabel(
            tier_card, text="", font=FONTS["small"],
            text_color=Colors.TEXT_MUTED, wraplength=500, justify="left",
        )
        self.w["tier_desc"].grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        monitor_card = ctk.CTkFrame(right, fg_color=Colors.BG_PANEL, corner_radius=8)
        monitor_card.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        monitor_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            monitor_card, text="Мониторинг ресурсов компьютера", font=FONTS["subheading"],
            text_color=Colors.AMBER,
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))

        self._build_metric_bar(monitor_card, 1, "CPU", "cpu_label", "cpu_bar")
        self._build_metric_bar(monitor_card, 2, "RAM", "ram_label", "ram_bar")
        self._build_metric_bar(monitor_card, 3, "Swap", "swap_label", "swap_bar")
        self.w["swap_row_frame"] = self.w["swap_bar"].master
        self.w["swap_row_frame"].grid_remove()

        cores_frame = ctk.CTkFrame(monitor_card, fg_color="transparent")
        cores_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 6))
        cores_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cores_frame, text="По ядрам:", font=FONTS["small"],
            text_color=Colors.TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w")
        self.w["core_bars_frame"] = ctk.CTkFrame(cores_frame, fg_color="transparent")
        self.w["core_bars_frame"].grid(row=1, column=0, sticky="ew")

        self.w["game_proc_label"] = ctk.CTkLabel(
            monitor_card, text="Процесс игры: —", font=FONTS["small"],
            text_color=Colors.TEXT_SECONDARY, justify="left", anchor="w",
        )
        self.w["game_proc_label"].grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 6))

    def _build_metric_bar(self, parent, row, label_text, label_key, bar_key):
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=2)
        row_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            row_frame, text=label_text, font=FONTS["small"],
            text_color=Colors.TEXT_SECONDARY, width=42,
        ).grid(row=0, column=0, sticky="w")

        self.w[bar_key] = ctk.CTkProgressBar(
            row_frame, height=12, fg_color=Colors.BG_DARK,
            progress_color=Colors.EMERALD, border_width=0,
        )
        self.w[bar_key].set(0.0)
        self.w[bar_key].grid(row=0, column=1, sticky="ew", padx=(8, 8))

        self.w[label_key] = ctk.CTkLabel(
            row_frame, text="0%", font=FONTS["mono_small"],
            text_color=Colors.TEXT_PRIMARY, width=52, anchor="e",
        )
        self.w[label_key].grid(row=0, column=2, sticky="e")

    def _build_optimizations_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        topbar = ctk.CTkFrame(parent, fg_color=Colors.BG_PANEL, corner_radius=8, height=44)
        topbar.grid(row=0, column=0, sticky="ew", pady=(10, 8), padx=10)
        topbar.grid_columnconfigure(0, weight=1)
        topbar.grid_propagate(False)

        self.w["active_count"] = ctk.CTkLabel(
            topbar, text="Активно: 0/0", font=FONTS["body_bold"],
            text_color=Colors.AMBER,
        )
        self.w["active_count"].grid(row=0, column=0, sticky="w", padx=14, pady=8)

        btns = ctk.CTkFrame(topbar, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e", padx=10, pady=6)
        ctk.CTkButton(
            btns, text="Только рекомендованные", width=170, height=28,
            font=FONTS["small"], fg_color=Colors.EMERALD,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_select_recommended,
        ).grid(row=0, column=0, padx=4)
        ctk.CTkButton(
            btns, text="Включить все", width=110, height=28,
            font=FONTS["small"], fg_color=Colors.EMERALD_DARK,
            hover_color=Colors.EMERALD, text_color=Colors.TEXT_PRIMARY,
            command=self.on_select_all,
        ).grid(row=0, column=1, padx=4)
        ctk.CTkButton(
            btns, text="Выключить все", width=110, height=28,
            font=FONTS["small"], fg_color=Colors.RED_DARK,
            hover_color=Colors.BG_INPUT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_select_none,
        ).grid(row=0, column=2, padx=4)

        scroll = ctk.CTkScrollableFrame(
            parent, fg_color=Colors.BG_DARK, corner_radius=0,
            scrollbar_button_color=Colors.BORDER_LIGHT,
            scrollbar_button_hover_color=Colors.AMBER,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        scroll.grid_columnconfigure(0, weight=1)
        self.w["opt_scroll"] = scroll

    def _render_optimizations(self):
        scroll = self.w.get("opt_scroll")
        if scroll is None:
            return
        
        for child in scroll.winfo_children():
            child.destroy()
        self.toggle_switches.clear()
        self.toggle_rows.clear()

        toggles = self.app_state.get("toggles") or []
        
        by_cat = {}
        for t in toggles:
            by_cat.setdefault(t.category, []).append(t)

        row_idx = 0
        for cat in CATEGORY_ORDER:
            cat_toggles = by_cat.get(cat, [])
            if not cat_toggles:
                continue
            active = sum(1 for t in cat_toggles if t.enabled)
            icon = CATEGORY_ICONS.get(cat, "•")
            label = CATEGORY_LABELS.get(cat, cat)
            header_text = f"{icon}  {label}  ({active}/{len(cat_toggles)})"

            cat_frame = ctk.CTkFrame(
                scroll, fg_color=Colors.BG_PANEL, corner_radius=6,
            )
            cat_frame.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=(8, 4))
            cat_frame.grid_columnconfigure(0, weight=1)
            row_idx += 1

            ctk.CTkLabel(
                cat_frame, text=header_text, font=FONTS["subheading"],
                text_color=Colors.AMBER,
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 4))

            for t in cat_toggles:
                toggle_row = self._build_toggle_row(cat_frame, t)
                toggle_row.grid(sticky="ew", padx=4, pady=2)
            
            ctk.CTkLabel(cat_frame, text="", height=2).grid()

        self._update_active_count()

    def _build_toggle_row(self, parent, toggle: OptimizationToggle):
        row = ctk.CTkFrame(
            parent, fg_color=Colors.BG_PANEL_LIGHT, corner_radius=6,
        )
        row.grid_columnconfigure(0, weight=1)
        self.toggle_rows[toggle.id] = row
        
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        left.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.grid_columnconfigure(0, weight=1)

        title_text = toggle.title
        if toggle.recommended:
            title_text = f"* {title_text}"
        ctk.CTkLabel(
            title_row, text=title_text, font=FONTS["body_bold"],
            text_color=Colors.TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")
        
        badge_col = ctk.CTkFrame(title_row, fg_color="transparent")
        badge_col.grid(row=0, column=1, sticky="e", padx=(6, 0))

        impact_color = IMPACT_COLORS.get(toggle.impact, Colors.TEXT_MUTED)
        impact_label = IMPACT_LABELS.get(toggle.impact, toggle.impact.upper())
        ctk.CTkLabel(
            badge_col, text=f" {impact_label} ", font=FONTS["tiny"],
            text_color=Colors.TEXT_PRIMARY, fg_color=impact_color,
            corner_radius=20, padx=2, pady=0, anchor="center", justify="center",
        ).grid(row=0, column=0, padx=2)

        if toggle.requires_admin:
            ctk.CTkLabel(
                badge_col, text=" admin ", font=FONTS["tiny"],
                text_color=Colors.TEXT_PRIMARY, fg_color=Colors.IMPACT_CRITICAL,
                corner_radius=20, padx=2, pady=0,anchor="center", justify="center"
            ).grid(row=0, column=1, padx=2)

        ctk.CTkLabel(
            left, text=toggle.description, font=FONTS["small"],
            text_color=Colors.TEXT_MUTED, wraplength=450, justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        
        switch = ctk.CTkSwitch(
            row, text="", width=46, height=22,
            progress_color=Colors.EMERALD, button_color=Colors.TEXT_PRIMARY,
            button_hover_color=Colors.TEXT_SECONDARY, fg_color=Colors.BG_DARK,
            command=lambda tid=toggle.id: self.on_toggle_changed(tid),
        )
        switch.grid(row=0, column=1, sticky="e", padx=12, pady=6)
        if toggle.enabled:
            switch.select()
        self.toggle_switches[toggle.id] = switch
        return row

    def _update_active_count(self):
        toggles = self.app_state.get("toggles") or []
        active = sum(1 for t in toggles if t.enabled)
        lbl = self.w.get("active_count")
        if lbl:
            lbl.configure(text=f"Активно: {active}/{len(toggles)}")

    def _build_settings_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        card = ctk.CTkFrame(parent, fg_color=Colors.BG_PANEL, corner_radius=8)
        card.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Настройки профиля оптимизации",
            font=FONTS["subheading"], text_color=Colors.AMBER,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        row = 1
        ctk.CTkLabel(
            card, text="Имена процессов игры", font=FONTS["body"],
            text_color=Colors.TEXT_SECONDARY, anchor="w",
        ).grid(row=row, column=0, sticky="nw", padx=14, pady=6)
        self.w["game_names_text"] = ctk.CTkTextbox(
            card, height=64, font=FONTS["mono_small"],
            fg_color=Colors.BG_DARK, text_color=Colors.TEXT_PRIMARY,
            
        )
        self.w["game_names_text"].grid(row=row, column=1, sticky="ew", padx=14, pady=6)
        self.w["game_names_text"].insert("1.0", "\n".join(self.app_state["game_names"]))
        row += 1

        ctk.CTkLabel(
            card, text="Приоритет процесса", font=FONTS["body"],
            text_color=Colors.TEXT_SECONDARY,
        ).grid(row=row, column=0, sticky="w", padx=14, pady=6)
        self.w["priority_menu"] = ctk.CTkOptionMenu(
            card, values=["Выше обычного", "Высокий", "Реального времени"],
            fg_color=Colors.BG_DARK, button_color=Colors.AMBER,
            button_hover_color=Colors.AMBER_DARK, text_color=Colors.TEXT_PRIMARY,
            dropdown_fg_color=Colors.BG_PANEL_LIGHT,
            dropdown_hover_color=Colors.BORDER_LIGHT,
            dropdown_text_color=Colors.TEXT_PRIMARY,
            command=self.on_priority_changed,
        )
        self.w["priority_menu"].set("Высокий")
        self.w["priority_menu"].grid(row=row, column=1, sticky="w", padx=14, pady=6)
        row += 1

        ctk.CTkLabel(
            card, text="Режим CPU Affinity", font=FONTS["body"],
            text_color=Colors.TEXT_SECONDARY,
        ).grid(row=row, column=0, sticky="w", padx=14, pady=6)
        self.w["affinity_menu"] = ctk.CTkOptionMenu(
            card, values=["Только физические ядра", "Все логические ядра"],
            fg_color=Colors.BG_DARK, button_color=Colors.AMBER,
            button_hover_color=Colors.AMBER_DARK, text_color=Colors.TEXT_PRIMARY,
            dropdown_fg_color=Colors.BG_PANEL_LIGHT,
            dropdown_hover_color=Colors.BORDER_LIGHT,
            dropdown_text_color=Colors.TEXT_PRIMARY,
            command=self.on_affinity_changed,
        )
        self.w["affinity_menu"].set("Только физические ядра")
        self.w["affinity_menu"].grid(row=row, column=1, sticky="w", padx=14, pady=6)
        row += 1

        ctk.CTkLabel(
            card, text="Разрешение таймера (мс)", font=FONTS["body"],
            text_color=Colors.TEXT_SECONDARY,
        ).grid(row=row, column=0, sticky="w", padx=14, pady=6)
        slider_row = ctk.CTkFrame(card, fg_color="transparent")
        slider_row.grid(row=row, column=1, sticky="ew", padx=14, pady=6)
        slider_row.grid_columnconfigure(0, weight=1)
        self.w["timer_slider"] = ctk.CTkSlider(
            slider_row, from_=0.5, to=1.5, number_of_steps=20,
            progress_color=Colors.AMBER, button_color=Colors.AMBER,
            button_hover_color=Colors.AMBER_LIGHT,
            command=self.on_timer_changed,
        )
        self.w["timer_slider"].set(0.5)
        self.w["timer_slider"].grid(row=0, column=0, sticky="ew")
        self.w["timer_value"] = ctk.CTkLabel(
            slider_row, text="0.5 мс", font=FONTS["mono_small"],
            text_color=Colors.TEXT_PRIMARY, width=70, anchor="e",
        )
        self.w["timer_value"].grid(row=0, column=1, padx=(8, 0))
        row += 1

        ctk.CTkLabel(
            card, text="Интервал очистки RAM (сек)", font=FONTS["body"],
            text_color=Colors.TEXT_SECONDARY,
        ).grid(row=row, column=0, sticky="w", padx=14, pady=6)
        slider_row2 = ctk.CTkFrame(card, fg_color="transparent")
        slider_row2.grid(row=row, column=1, sticky="ew", padx=14, pady=6)
        slider_row2.grid_columnconfigure(0, weight=1)
        self.w["ram_interval_slider"] = ctk.CTkSlider(
            slider_row2, from_=60, to=900, number_of_steps=28,
            progress_color=Colors.AMBER, button_color=Colors.AMBER,
            button_hover_color=Colors.AMBER_LIGHT,
            command=self.on_ram_interval_changed,
        )
        self.w["ram_interval_slider"].set(300)
        self.w["ram_interval_slider"].grid(row=0, column=0, sticky="ew")
        self.w["ram_interval_value"] = ctk.CTkLabel(
            slider_row2, text="300 сек", font=FONTS["mono_small"],
            text_color=Colors.TEXT_PRIMARY, width=80, anchor="e",
        )
        self.w["ram_interval_value"].grid(row=0, column=1, padx=(8, 0))
        row += 1

        ctk.CTkLabel(
            card, text="Агрессивная очистка RAM", font=FONTS["body"],
            text_color=Colors.TEXT_SECONDARY,
        ).grid(row=row, column=0, sticky="w", padx=14, pady=6)
        agg_row = ctk.CTkFrame(card, fg_color="transparent")
        agg_row.grid(row=row, column=1, sticky="ew", padx=14, pady=6)
        agg_row.grid_columnconfigure(1, weight=1)
        self.w["aggressive_switch"] = ctk.CTkSwitch(
            agg_row, text="", progress_color=Colors.EMERALD,
            button_color=Colors.TEXT_PRIMARY, button_hover_color=Colors.TEXT_SECONDARY,
            fg_color=Colors.BG_DARK,
            command=self.on_aggressive_changed,
        )
        self.w["aggressive_switch"].grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            agg_row, text="Может вызывать кратковременные фризы в игре",
            font=FONTS["small"], text_color=Colors.RED, anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=10)
        row += 1

        separator = ctk.CTkFrame(card, height=1, fg_color=Colors.BORDER)
        separator.grid(row=row, column=0, columnspan=2, sticky="ew", padx=14, pady=10)
        row += 1

        ctk.CTkLabel(
            card, text="Резервная копия настроек",
            font=FONTS["subheading"], text_color=Colors.AMBER,
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 8))
        row += 1

        ctk.CTkLabel(
            card, text="Сохраняет текущие настройки системы (реестр, сервисы, схема питания)\nв JSON-файл рядом с программой. Можно восстановить позже.",
            font=FONTS["small"], text_color=Colors.TEXT_MUTED, justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 8))
        row += 1

        self.w["backup_status"] = ctk.CTkLabel(
            card, text="Проверка резервной копии...",
            font=FONTS["small"], text_color=Colors.TEXT_MUTED, anchor="w",
        )
        self.w["backup_status"].grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=4)
        row += 1

        backup_btns = ctk.CTkFrame(card, fg_color="transparent")
        backup_btns.grid(row=row, column=0, columnspan=2, sticky="ew", padx=14, pady=6)
        backup_btns.grid_columnconfigure(4, weight=1)

        self.w["btn_create_backup"] = ctk.CTkButton(
            backup_btns, text="Создать копию", width=130, height=30,
            font=FONTS["small"], fg_color=Colors.EMERALD_DARK,
            hover_color=Colors.EMERALD, text_color=Colors.TEXT_PRIMARY,
            command=self.on_create_backup,
        )
        self.w["btn_create_backup"].grid(row=0, column=0, padx=3)

        self.w["btn_import_backup"] = ctk.CTkButton(
            backup_btns, text="Импорт из файла", width=130, height=30,
            font=FONTS["small"], fg_color=Colors.BG_PANEL_LIGHT,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_import_backup,
        )
        self.w["btn_import_backup"].grid(row=0, column=1, padx=3)

        self.w["btn_export_backup"] = ctk.CTkButton(
            backup_btns, text="Экспорт в файл", width=130, height=30,
            font=FONTS["small"], fg_color=Colors.BG_PANEL_LIGHT,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_export_backup,
        )
        self.w["btn_export_backup"].grid(row=0, column=2, padx=3)

        self.w["btn_restore_backup"] = ctk.CTkButton(
            backup_btns, text="Восстановить из копии", width=180, height=30,
            font=FONTS["small"], fg_color=Colors.AMBER_DARK,
            hover_color=Colors.AMBER, text_color=Colors.TEXT_PRIMARY,
            command=self.on_restore_backup,
        )
        self.w["btn_restore_backup"].grid(row=0, column=3, padx=3)
        row += 1

    def _build_processes_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        topbar = ctk.CTkFrame(parent, fg_color=Colors.BG_PANEL, corner_radius=8, height=48)
        topbar.grid(row=0, column=0, sticky="ew", pady=(10, 8), padx=10)
        topbar.grid_columnconfigure(3, weight=1)
        topbar.grid_propagate(False)

        self.w["proc_stats"] = ctk.CTkLabel(
            topbar, text="Процессов: — | CPU: —% | RAM: — МБ",
            font=FONTS["body_bold"], text_color=Colors.AMBER,
        )
        self.w["proc_stats"].grid(row=0, column=0, sticky="w", padx=14, pady=10)

        self.w["btn_refresh_procs"] = ctk.CTkButton(
            topbar, text="Обновить", width=90, height=28,
            font=FONTS["small"], fg_color=Colors.BG_PANEL_LIGHT,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_refresh_processes,
        )
        self.w["btn_refresh_procs"].grid(row=0, column=1, padx=4, pady=8)

        self.w["btn_kill_bg"] = ctk.CTkButton(
            topbar, text="Закрыть фоновые", width=130, height=28,
            font=FONTS["small"], fg_color=Colors.RED_DARK,
            hover_color=Colors.RED, text_color=Colors.TEXT_PRIMARY,
            command=self.on_kill_background,
        )
        self.w["btn_kill_bg"].grid(row=0, column=2, padx=4, pady=8)

        scroll = ctk.CTkScrollableFrame(
            parent, fg_color=Colors.BG_DARK, corner_radius=0,
            scrollbar_button_color=Colors.BORDER_LIGHT,
            scrollbar_button_hover_color=Colors.AMBER,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        scroll.grid_columnconfigure(0, weight=1)
        self.w["proc_scroll"] = scroll

        ctk.CTkLabel(
            scroll, text="Нажмите «Обновить» для анализа процессов",
            font=FONTS["body"], text_color=Colors.TEXT_MUTED,
        ).pack(pady=40)

    def on_refresh_processes(self):
        if self.app_state.get("analyzing_procs"):
            return
        self.app_state["analyzing_procs"] = True
        self._set_toast("Анализ процессов...", Colors.AMBER)
        log("Начат анализ процессов", "INFO")
        if self.w.get("btn_refresh_procs"):
            self.w["btn_refresh_procs"].configure(state="disabled", text="Анализ...")
        threading.Thread(target=self._analyze_processes_worker, daemon=True, name="proc_analyze").start()

    def _analyze_processes_worker(self):
        try:
            game_names = list(self.app_state.get("game_names") or [])
            analysis = analyze_processes(game_names)
            self._proc_queue.put(("done", analysis))
        except Exception as e:
            self._proc_queue.put(("error", str(e)))

    def _on_process_analysis_ready(self, payload):
        self.app_state["analyzing_procs"] = False
        if self.w.get("btn_refresh_procs"):
            self.w["btn_refresh_procs"].configure(state="normal", text="Обновить")
        kind, data = payload if isinstance(payload, tuple) else ("error", "unknown")
        if kind == "error":
            self._set_toast(f"Ошибка анализа: {data}", Colors.RED)
            log(f"Ошибка анализа процессов: {data}", "ERROR")
            return
        analysis: ProcessAnalysis = data
        self.app_state["process_analysis"] = analysis
        self._render_processes(analysis)
        self._set_toast(
            f"Процессов: {analysis.total_processes} | Найдено для оптимизации: {len(analysis.optimizable_processes)}",
            Colors.AMBER,
        )
        log(f"Анализ завершён: {analysis.total_processes} процессов, CPU {analysis.total_cpu_usage:.1f}%, RAM {analysis.total_memory_mb:.0f} МБ", "SUCCESS")

    def _render_processes(self, analysis: ProcessAnalysis):
        scroll = self.w.get("proc_scroll")
        if scroll is None:
            return
        for child in scroll.winfo_children():
            child.destroy()

        stats_lbl = self.w.get("proc_stats")
        if stats_lbl:
            stats_lbl.configure(
                text=f"Процессов: {analysis.total_processes} | "
                     f"CPU: {analysis.total_cpu_usage:.1f}% | "
                     f"RAM: {analysis.total_memory_mb:.0f} МБ"
            )

        header = ctk.CTkFrame(scroll, fg_color=Colors.BG_PANEL, corner_radius=6)
        header.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 6))
        header.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(header, text="PID", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=60).grid(row=0, column=0, padx=4)
        ctk.CTkLabel(header, text="Имя", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=180).grid(row=0, column=1, padx=4, sticky="w")
        ctk.CTkLabel(header, text="Описание", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED).grid(row=0, column=2, padx=4, sticky="w")
        ctk.CTkLabel(header, text="CPU%", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=55).grid(row=0, column=3, padx=4)
        ctk.CTkLabel(header, text="RAM МБ", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=60).grid(row=0, column=4, padx=4)
        ctk.CTkLabel(header, text="Категория", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=90).grid(row=0, column=5, padx=4)
        ctk.CTkLabel(header, text="Действие", font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=120).grid(row=0, column=6, padx=4)

        procs = analysis.heavy_processes[:20]
        for i, p in enumerate(procs):
            row = ctk.CTkFrame(scroll, fg_color=Colors.BG_PANEL_LIGHT, corner_radius=4)
            row.grid(row=i + 1, column=0, sticky="ew", padx=4, pady=2)
            row.grid_columnconfigure(2, weight=1)

            ctk.CTkLabel(row, text=str(p.pid), font=FONTS["mono_small"], text_color=Colors.TEXT_MUTED, width=60).grid(row=0, column=0, padx=4)
            ctk.CTkLabel(row, text=p.name, font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=180, anchor="w").grid(row=0, column=1, padx=4, sticky="w")
            desc = get_process_description(p.name)
            ctk.CTkLabel(row, text=desc, font=FONTS["tiny"], text_color=Colors.TEXT_SECONDARY, anchor="w").grid(row=0, column=2, padx=4, sticky="w")
            cpu_color = progress_color_for(p.cpu_percent)
            ctk.CTkLabel(row, text=f"{p.cpu_percent:.1f}", font=FONTS["mono_small"], text_color=cpu_color, width=55).grid(row=0, column=3, padx=4)
            ctk.CTkLabel(row, text=f"{p.memory_mb:.0f}", font=FONTS["mono_small"], text_color=Colors.TEXT_PRIMARY, width=60).grid(row=0, column=4, padx=4)
            cat_color = PROCESS_CATEGORY_COLORS.get(p.category, Colors.TEXT_MUTED)
            ctk.CTkLabel(row, text=PROCESS_CATEGORY_LABELS.get(p.category, p.category), font=FONTS["tiny"], text_color=cat_color, width=90).grid(row=0, column=5, padx=4)

            action_text = PROCESS_ACTION_LABELS.get(p.recommended_action, p.recommended_action)
            if p.recommended_action == "keep":
                ctk.CTkLabel(row, text=action_text, font=FONTS["tiny"], text_color=Colors.TEXT_MUTED, width=120).grid(row=0, column=6, padx=4)
            else:
                ctk.CTkButton(
                    row, text=action_text, width=110, height=22,
                    font=FONTS["tiny"],
                    fg_color=Colors.RED_DARK if p.recommended_action == "kill" else Colors.AMBER_DARK,
                    hover_color=Colors.RED if p.recommended_action == "kill" else Colors.AMBER,
                    text_color=Colors.TEXT_PRIMARY,
                    command=lambda pid=p.pid, action=p.recommended_action: self.on_optimize_single_process(pid, action),
                ).grid(row=0, column=6, padx=4)

    def on_optimize_single_process(self, pid: int, action: str):
        log(f"Оптимизация процесса PID={pid} действие={action}", "INFO")
        actions = [{"pid": pid, "action": action}]
        threading.Thread(target=self._optimize_processes_worker, args=(actions,), daemon=True).start()

    def on_kill_background(self):
        if self.app_state.get("applying"):
            self._set_toast("Дождитесь завершения текущей операции", Colors.AMBER)
            return
        self.app_state["applying"] = True
        self._set_toast("Закрытие фоновых приложений...", Colors.AMBER)
        log("Закрытие фоновых приложений", "INFO")
        if self.w.get("btn_kill_bg"):
            self.w["btn_kill_bg"].configure(state="disabled", text="Закрытие...")
        threading.Thread(target=self._kill_background_worker, daemon=True).start()

    def _kill_background_worker(self):
        def on_progress(result):
            self._apply_queue.put(("progress", result))
        try:
            results = kill_background_apps(progress_callback=on_progress)
            self._apply_queue.put(("done", results))
        except Exception as e:
            self._apply_queue.put(("error", str(e)))

    def _optimize_processes_worker(self, actions):
        def on_progress(result):
            self._apply_queue.put(("progress", result))
        try:
            results = optimize_processes(actions, progress_callback=on_progress)
            self._apply_queue.put(("done", results))
        except Exception as e:
            self._apply_queue.put(("error", str(e)))

    def _build_logs_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        topbar = ctk.CTkFrame(parent, fg_color=Colors.BG_PANEL, corner_radius=8, height=44)
        topbar.grid(row=0, column=0, sticky="ew", pady=(10, 8), padx=10)
        topbar.grid_columnconfigure(1, weight=1)
        topbar.grid_propagate(False)

        ctk.CTkLabel(
            topbar, text="📋 Консоль логов", font=FONTS["body_bold"],
            text_color=Colors.AMBER,
        ).grid(row=0, column=0, sticky="w", padx=14, pady=8)

        btns = ctk.CTkFrame(topbar, fg_color="transparent")
        btns.grid(row=0, column=2, sticky="e", padx=10, pady=6)
        ctk.CTkButton(
            btns, text="Очистить", width=90, height=28,
            font=FONTS["small"], fg_color=Colors.BG_PANEL_LIGHT,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=lambda: log_system.clear(),
        ).grid(row=0, column=0, padx=4)
        ctk.CTkButton(
            btns, text="Сохранить в файл", width=130, height=28,
            font=FONTS["small"], fg_color=Colors.BG_PANEL_LIGHT,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_save_logs,
        ).grid(row=0, column=1, padx=4)

        self.w["full_log"] = ctk.CTkTextbox(
            parent, font=FONTS["log"],
            fg_color=Colors.BG_PANEL, text_color=Colors.TEXT_SECONDARY,
            state="disabled", wrap="word", corner_radius=10
        )
        self.w["full_log"].grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        log_system.attach(self.w["full_log"])

    def on_save_logs(self):
        try:
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
                initialfile=f"stalzone_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            )
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                for ts, level, msg in log_system._lines:
                    f.write(f"[{ts}] {level:8s} | {msg}\n")
            self._set_toast(f"Логи сохранены: {os.path.basename(path)}", Colors.EMERALD)
            log(f"Логи сохранены в {path}", "SUCCESS")
        except Exception as e:
            self._set_toast(f"Ошибка: {e}", Colors.RED)

    def _drain_logs(self):
        try:
            log_system.drain()
        except Exception:
            pass
        if not self._stop_event.is_set():
            self.after(150, self._drain_logs)

    def on_create_backup(self):
        if self.app_state.get("applying"):
            self._set_toast("Дождитесь завершения текущей операции", Colors.AMBER)
            return
        self.app_state["applying"] = True
        self._set_toast("Создание резервной копии...", Colors.AMBER)
        log("Создание резервной копии настроек", "INFO")
        if self.w.get("btn_create_backup"):
            self.w["btn_create_backup"].configure(state="disabled", text="Создание...")
        threading.Thread(target=self._create_backup_worker, daemon=True).start()

    def _create_backup_worker(self):
        try:
            backup = capture_backup()
            path = save_backup(backup)
            self._backup_queue.put(("created", (backup, path)))
        except Exception as e:
            self._backup_queue.put(("error", str(e)))

    def on_restore_backup(self):
        if self.app_state.get("applying"):
            self._set_toast("Дождитесь завершения текущей операции", Colors.AMBER)
            return
        backup = self.app_state.get("backup")
        if backup is None:
            backup = load_backup()
            if backup is None:
                self._set_toast("Резервная копия не найдена. Создайте сначала.", Colors.AMBER)
                return
            self.app_state["backup"] = backup
        self.app_state["applying"] = True
        self._set_toast("Восстановление из копии...", Colors.AMBER)
        log("Восстановление настроек из резервной копии", "INFO")
        if self.w.get("btn_restore_backup"):
            self.w["btn_restore_backup"].configure(state="disabled", text="Восстановление...")
        threading.Thread(target=self._restore_backup_worker, args=(backup,), daemon=True).start()

    def _restore_backup_worker(self, backup):
        def on_progress(result):
            self._apply_queue.put(("progress", result))
        try:
            results = restore_backup(backup, progress_callback=on_progress)
            self._apply_queue.put(("done", results))
        except Exception as e:
            self._apply_queue.put(("error", str(e)))

    def on_import_backup(self):
        try:
            path = filedialog.askopenfilename(
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                title="Выберите файл резервной копии",
            )
            if not path:
                return
            backup = load_backup(path)
            if backup is None:
                self._set_toast("Не удалось загрузить файл", Colors.RED)
                return
            self.app_state["backup"] = backup
            self.app_state["backup_path"] = path
            self._update_backup_status()
            self._set_toast(f"Импортировано: {os.path.basename(path)}", Colors.EMERALD)
            log(f"Импортирована резервная копия из {path}", "SUCCESS")
        except Exception as e:
            self._set_toast(f"Ошибка: {e}", Colors.RED)

    def on_export_backup(self):
        backup = self.app_state.get("backup")
        if backup is None:
            self._set_toast("Нет данных для экспорта. Создайте копию сначала.", Colors.AMBER)
            return
        try:
            path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                initialfile=f"stalzone_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
            if not path:
                return
            save_backup(backup, path)
            self._set_toast(f"Экспортировано: {os.path.basename(path)}", Colors.EMERALD)
            log(f"Резервная копия экспортирована в {path}", "SUCCESS")
        except Exception as e:
            self._set_toast(f"Ошибка: {e}", Colors.RED)

    def _update_backup_status(self):
        lbl = self.w.get("backup_status")
        if lbl is None:
            return
        backup = self.app_state.get("backup")
        if backup is None:
            lbl.configure(
                text="⚠ Резервная копия не создана. Нажмите «Создать копию».",
                text_color=Colors.RED,
            )
        else:
            reg_count = len(backup.registry_entries)
            svc_count = len(backup.services)
            lbl.configure(
                text=f"✓ Копия от {backup.created_at[:19] if backup.created_at else '—'} | "
                     f"Реестр: {reg_count} | Сервисы: {svc_count} | "
                     f"Хост: {backup.hostname or '—'}",
                text_color=Colors.AMBER,
            )

    def _on_backup_message(self, payload):
        kind, data = payload if isinstance(payload, tuple) else ("error", "unknown")
        if kind in ("created", "auto_created", "loaded"):
            backup, path = data
            self.app_state["backup"] = backup
            self.app_state["backup_path"] = path
            if kind == "created":
                self.app_state["applying"] = False
                if self.w.get("btn_create_backup"):
                    self.w["btn_create_backup"].configure(state="normal", text="Создать копию")
            self._update_backup_status()
            if kind == "auto_created":
                log(f"Автоматически создана резервная копия (первый запуск): {path}", "SUCCESS")
                log(f"  Реестр: {len(backup.registry_entries)} записей, Сервисы: {len(backup.services)}", "INFO")
            elif kind == "loaded":
                log(f"Загружена существующая резервная копия: {path}", "INFO")
            elif kind == "created":
                self._set_toast(f"Резервная копия создана: {os.path.basename(path)}", Colors.EMERALD)
                log(f"Резервная копия создана: {path} (реестр: {len(backup.registry_entries)}, сервисы: {len(backup.services)})", "SUCCESS")
        elif kind == "error":
            self.app_state["applying"] = False
            if self.w.get("btn_create_backup"):
                self.w["btn_create_backup"].configure(state="normal", text="Создать копию")
            if self.w.get("btn_restore_backup"):
                self.w["btn_restore_backup"].configure(state="normal", text="Восстановить из копии")
            self._set_toast(f"Ошибка backup: {data}", Colors.RED)
            log(f"Ошибка backup: {data}", "ERROR")

    def _build_footer(self):
        footer = ctk.CTkFrame(
            self.main_frame, height=56, fg_color=Colors.BG_DARK,
            corner_radius=15, border_width=0,
        )
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(1, weight=1)

        self.w["admin_status"] = ctk.CTkLabel(
            footer, text="⚠ Требуются права администратора",
            font=FONTS["body_bold"], text_color=Colors.RED,
        )
        self.w["admin_status"].grid(row=0, column=0, sticky="w", padx=14, pady=12)
        self._update_admin_status()

        btns = ctk.CTkFrame(footer, fg_color="transparent")
        btns.grid(row=0, column=2, sticky="e", padx=14, pady=10)

        self.w["btn_apply"] = ctk.CTkButton(
            btns, text="Применить оптимизации", width=180, height=34,
            font=FONTS["body_bold"], fg_color=Colors.EMERALD,
            hover_color=Colors.EMERALD_DARK, text_color=Colors.TEXT_PRIMARY,
            command=self.on_apply_clicked,
        )
        self.w["btn_apply"].grid(row=0, column=0, padx=4)

        self.w["btn_revert"] = ctk.CTkButton(
            btns, text="Отменить изменения", width=160, height=34,
            font=FONTS["body"], fg_color=Colors.RED_DARK,
            hover_color=Colors.BG_INPUT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_revert_clicked,
        )
        self.w["btn_revert"].grid(row=0, column=1, padx=4)

        ctk.CTkFrame(
            footer, height=1, fg_color=Colors.BORDER, corner_radius=0,
        ).grid(row=1, column=0, columnspan=3, sticky="ew")

    def _update_admin_status(self):
        lbl = self.w.get("admin_status")
        if lbl is None:
            return
        if self.app_state.get("admin"):
            lbl.configure(
                text="✓ Права администратора",
                text_color=Colors.PROC_GAME,
            )
        else:
            lbl.configure(
                text="⚠ Требуются права администратора",
                text_color=Colors.RED,
            )

    def start_background_threads(self):
        threading.Thread(target=self._detect_hardware_worker, daemon=True, name="hw_detect").start()
        threading.Thread(target=self._live_metrics_worker, daemon=True, name="metrics_poll").start()
        threading.Thread(target=self._auto_backup_worker, daemon=True, name="auto_backup").start()
        log("StalZone Optimizer запущен", "INFO")
        log(f"Права администратора: {'да' if self.app_state['admin'] else 'НЕТ'}", "WARNING" if not self.app_state["admin"] else "INFO")

    def _auto_backup_worker(self):
        try:
            path = create_backup_if_not_exists()
            if path:
                backup = load_backup(path)
                if backup:
                    self._backup_queue.put(("auto_created", (backup, path)))
            else:
                backup = load_backup()
                if backup:
                    self._backup_queue.put(("loaded", (backup, get_backup_path())))
        except Exception as e:
            self._backup_queue.put(("error", str(e)))

    def _detect_hardware_worker(self):
        try:
            report = detect_hardware()
            tier = assess_tier(report)
            toggles = get_default_toggles(report)
            profile = self.app_state["profile"]
            profile.toggles = toggles
            self._hw_queue.put(("ok", {"report": report, "tier": tier, "toggles": toggles}))
        except Exception as e:  
            self._hw_queue.put(("error", str(e)))

    def _live_metrics_worker(self):
        while not self._stop_event.is_set():
            try:
                metrics = get_live_metrics(list(self.app_state.get("game_names") or []))
                self._metrics_queue.put(metrics)
            except Exception as e:  
                self._metrics_queue.put(None)
            
            slept = 0
            while slept < self.POLL_INTERVAL_SEC and not self._stop_event.is_set():
                self._stop_event.wait(0.2)
                slept += 0.2

    def _drain_queues(self):
        try:
            while True:
                try:
                    kind, payload = self._hw_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "ok":
                        self._on_hardware_ready(payload)
                    else:
                        self._on_hardware_error(payload)
                except Exception:
                    pass  
        except Exception:
            pass

        try:
            while True:
                try:
                    m = self._metrics_queue.get_nowait()
                except queue.Empty:
                    break
                if m is not None:
                    try:
                        self._on_metrics_ready(m)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            while True:
                try:
                    res = self._apply_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._on_apply_message(res)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            while True:
                try:
                    res = self._proc_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._on_process_analysis_ready(res)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            while True:
                try:
                    res = self._backup_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._on_backup_message(res)
                except Exception:
                    pass
        except Exception:
            pass

        if not self._stop_event.is_set():
            self.after(100, self._drain_queues)

    def _on_hardware_ready(self, payload: dict):
        self.app_state["report"] = payload["report"]
        self.app_state["tier"] = payload["tier"]
        self.app_state["toggles"] = payload["toggles"]
        self.app_state["profile"].toggles = payload["toggles"]
        self.app_state["loading"] = False

        loading = self.w.get("specs_loading")
        if loading:
            loading.grid_forget()

        self._populate_specs(payload["report"])
        self._populate_tier(payload["tier"])
        self._render_optimizations()

    def _on_hardware_error(self, err: str):
        loading = self.w.get("specs_loading")
        if loading:
            loading.configure(
                text=f"⚠ Ошибка определения:\n{err}",
                text_color=Colors.RED,
            )
        self._set_toast(f"Ошибка: {err}", Colors.RED)

    def _populate_specs(self, report: HardwareReport):
        grid = self.w.get("specs_grid")
        if grid is None:
            return
        for child in grid.winfo_children():
            child.destroy()
        grid.grid_columnconfigure(1, weight=1)

        rows = []
        cpu = report.cpu
        rows.append(("🔲", "CPU", f"{cpu.brand}"))
        rows.append(("", "Ядра (физ/лог)", f"{cpu.cores_physical} / {cpu.cores_logical}"))
        rows.append(("", "Частота", f"{cpu.speed_ghz:.2f} ГГц"))
        ram = report.ram
        rows.append(("💾", "RAM", f"{ram.total_gb:.1f} ГБ"))
        rows.append(("", "Свободно", f"{ram.free_gb:.1f} ГБ"))
        if report.gpus:
            g = report.gpus[0]
            vram = f"{g.vram_mb} МБ" if g.vram_mb else "—"
            rows.append(("🎮", "GPU", g.model))
            rows.append(("", "VRAM", vram))
        disk_count = len(report.disks)
        disk_types = ", ".join(sorted({d.type for d in report.disks})) or "—"
        rows.append(("💿", "Диски", f"{disk_count} шт. ({disk_types})"))
        os_info = report.os
        rows.append(("🖥", "ОС", f"{os_info.distro} {os_info.release}"))
        rows.append(("", "Архитектура", os_info.arch))
        if report.display_resolution:
            rows.append(("🖥", "Дисплей", f"{report.display_resolution} @ {int(report.display_refresh_rate or 0)} Гц"))

        for i, (icon, label, value) in enumerate(rows):
            ctk.CTkLabel(
                grid, text=icon, font=FONTS["body"],
                text_color=Colors.AMBER, width=24,
            ).grid(row=i, column=0, sticky="w", padx=(8, 4), pady=1)
            ctk.CTkLabel(
                grid, text=label, font=FONTS["small"],
                text_color=Colors.TEXT_MUTED, width=120, anchor="w",
            ).grid(row=i, column=1, sticky="w", padx=4, pady=1)
            ctk.CTkLabel(
                grid, text=value, font=FONTS["mono_small"],
                text_color=Colors.TEXT_PRIMARY, anchor="w",
            ).grid(row=i, column=2, sticky="w", padx=4, pady=1)

    def _populate_tier(self, tier: TierAssessment):
        info = TIER_INFO.get(tier.tier, TIER_INFO["unknown"])
        badge = self.w.get("tier_badge")
        if badge:
            badge.configure(
                text=info.get("label", tier.label),
                fg_color=info.get("color", Colors.TIER_UNKNOWN),
                text_color=Colors.BG_DARKEST,
            )
        if self.w.get("tier_score"):
            self.w["tier_score"].configure(
                text=f"Score: {tier.score}/100",
                text_color=info.get("color", Colors.TEXT_SECONDARY),
            )
        if self.w.get("tier_fps"):
            self.w["tier_fps"].configure(
                text=f"FPS: ~{tier.estimated_fps_min}–{tier.estimated_fps_max}",
                text_color=info.get("color", Colors.TEXT_SECONDARY),
            )
        
        self._populate_list(self.w.get("strengths_list"), tier.strengths, prefix="✓ ", color=Colors.EMERALD)
        self._populate_list(self.w.get("bottlenecks_list"), tier.bottlenecks, prefix="⚠ ", color=Colors.RED)

    def _populate_list(self, container, items, prefix="", color=None):
        if container is None:
            return
        for child in container.winfo_children():
            child.destroy()
        if not items:
            ctk.CTkLabel(
                container, text="—", font=FONTS["small"],
                text_color=Colors.TEXT_MUTED,
            ).pack(anchor="w", padx=8, pady=2)
            return
        for item in items:
            ctk.CTkLabel(
                container, text=f"{prefix}{item}", font=FONTS["small"],
                text_color=color or Colors.TEXT_SECONDARY,
                wraplength=270, justify="left", anchor="w",
            ).pack(anchor="w", padx=8, pady=2)

    def _on_metrics_ready(self, m: LiveMetrics):
        self.app_state["metrics"] = m

        gs = self.w.get("game_status")
        if gs:
            if m.game_process_running and m.game_process_name:
                gs.configure(
                    text=f"● Игра запущена: {m.game_process_name}",
                    text_color=Colors.EMERALD,
                )
            else:
                gs.configure(
                    text="❌ Игра не запущена",
                    text_color=Colors.TEXT_MUTED,
                )

        gpl = self.w.get("game_proc_label")
        if gpl:
            if m.game_process_running and m.game_process_name:
                gpl.configure(
                    text=(
                        f"Процесс игры: {m.game_process_name}\n"
                        f"PID: {m.game_process_pid or '—'}   "
                        f"CPU: {m.game_process_cpu or 0:.1f}%   "
                        f"RAM: {m.game_process_memory_mb or 0:.0f} МБ"
                    ),
                    text_color=Colors.TEXT_PRIMARY,
                )
            else:
                gpl.configure(
                    text="Процесс игры: не обнаружен",
                    text_color=Colors.TEXT_MUTED,
                )

        cpu_pct = max(0.0, min(100.0, float(m.cpu_load or 0.0)))
        if self.w.get("cpu_bar"):
            self.w["cpu_bar"].configure(progress_color=progress_color_for(cpu_pct))
            self.w["cpu_bar"].set(cpu_pct / 100.0)
        if self.w.get("cpu_label"):
            self.w["cpu_label"].configure(text=f"{cpu_pct:.0f}%")

        ram_pct = max(0.0, min(100.0, float(m.ram_used_percent or 0.0)))
        if self.w.get("ram_bar"):
            self.w["ram_bar"].configure(progress_color=progress_color_for(ram_pct))
            self.w["ram_bar"].set(ram_pct / 100.0)
        if self.w.get("ram_label"):
            self.w["ram_label"].configure(text=f"{ram_pct:.0f}%  ({m.ram_used_gb:.1f}/{m.ram_total_gb:.1f} ГБ)")

        swap_row = self.w.get("swap_row_frame")
        report = self.app_state.get("report")
        swap_configured = (bool(report and report.ram and report.ram.swap_total_gb > 0))
        swap_pct = max(0.0, min(100.0, float(m.swap_used_percent or 0.0)))
        show_swap = swap_configured or swap_pct > 0
        if swap_row is not None:
            if show_swap and not swap_row.winfo_ismapped():
                swap_row.grid()
            elif not show_swap and swap_row.winfo_ismapped():
                swap_row.grid_remove()
        if show_swap:
            if self.w.get("swap_bar"):
                self.w["swap_bar"].configure(progress_color=progress_color_for(swap_pct))
                self.w["swap_bar"].set(swap_pct / 100.0)
            if self.w.get("swap_label"):
                self.w["swap_label"].configure(text=f"{swap_pct:.0f}%")

        cores = m.cpu_per_core or []
        if len(cores) != len(self.core_bars):
            self._rebuild_core_bars(len(cores))
        for bar, val in zip(self.core_bars, cores):
            v = max(0.0, min(100.0, float(val or 0.0)))
            bar.set(v / 100.0)
            bar.configure(progress_color=progress_color_for(v))

    def _rebuild_core_bars(self, count: int):
        container = self.w.get("core_bars_frame")
        if container is None:
            return
        for child in container.winfo_children():
            child.destroy()
        self.core_bars = []
        if count == 0:
            ctk.CTkLabel(
                container, text="—", font=FONTS["small"],
                text_color=Colors.TEXT_MUTED,
            ).pack(side="left", padx=2)
            return
        for i in range(count):
            bar = ctk.CTkProgressBar(
                container, width=24, height=10, orientation="vertical",
                fg_color=Colors.BG_DARK, progress_color=Colors.EMERALD,
                border_width=0,
            )
            bar.pack(side="left", padx=1, pady=2)
            bar.set(0.0)
            self.core_bars.append(bar)

    def on_toggle_changed(self, toggle_id: str):
        sw = self.toggle_switches.get(toggle_id)
        if sw is None:
            return
        enabled = bool(sw.get())
        for t in self.app_state.get("toggles", []):
            if t.id == toggle_id:
                t.enabled = enabled
                break
        
        self.app_state["profile"].toggles = self.app_state["toggles"]
        self._update_active_count()
        self._refresh_category_headers()

    def _refresh_category_headers(self):
        scroll = self.w.get("opt_scroll")
        if scroll is None:
            return
        for cat_frame in scroll.winfo_children():
            slaves = cat_frame.winfo_children()
            if not slaves:
                continue
            header_label = slaves[0]
        
        by_cat = {}
        for t in self.app_state.get("toggles", []):
            by_cat.setdefault(t.category, []).append(t)
        for cat_frame in scroll.winfo_children():
            children = cat_frame.winfo_children()
            if not children:
                continue
            header = children[0]
            
            current_text = header.cget("text") if hasattr(header, "cget") else ""
            for cat in CATEGORY_ORDER:
                icon = CATEGORY_ICONS.get(cat, "•")
                label = CATEGORY_LABELS.get(cat, cat)
                prefix = f"{icon}  {label}  ("
                if current_text.startswith(prefix):
                    lst = by_cat.get(cat, [])
                    active = sum(1 for t in lst if t.enabled)
                    header.configure(text=f"{prefix}{active}/{len(lst)})")
                    break

    def on_select_all(self):
        for t in self.app_state.get("toggles", []):
            t.enabled = True
        for sw in self.toggle_switches.values():
            sw.select()
        self._update_active_count()
        self._refresh_category_headers()
        self._set_toast("Включены все оптимизации", Colors.EMERALD)

    def on_select_none(self):
        for t in self.app_state.get("toggles", []):
            t.enabled = False
        for sw in self.toggle_switches.values():
            sw.deselect()
        self._update_active_count()
        self._refresh_category_headers()
        self._set_toast("Все оптимизации выключены", Colors.TEXT_SECONDARY)

    def on_select_recommended(self):
        for t in self.app_state.get("toggles", []):
            t.enabled = bool(t.recommended)
        for tid, sw in self.toggle_switches.items():
            for t in self.app_state["toggles"]:
                if t.id == tid:
                    if t.recommended:
                        sw.select()
                    else:
                        sw.deselect()
                    break
        self._update_active_count()
        self._refresh_category_headers()
        self._set_toast("Включены только рекомендованные", Colors.AMBER)

    def _sync_game_names_from_textbox(self):
        tb = self.w.get("game_names_text")
        if tb is None:
            return
        raw = tb.get("1.0", "end").strip()
        parts = [p.strip() for p in raw.replace(",", "\n").splitlines() if p.strip()]
        self.app_state["game_names"] = parts or list(self.DEFAULT_GAME_NAMES)
        self.app_state["profile"].game_process_names = self.app_state["game_names"]

    def on_priority_changed(self, value: str):
        mapping = {
            "Выше обычного": "above_normal",
            "Высокий": "high",
            "Реального времени": "realtime",
        }
        self.app_state["profile"].process_priority = mapping.get(value, "high")

    def on_affinity_changed(self, value: str):
        if value.startswith("Только"):
            self.app_state["profile"].cpu_affinity_mode = "physical"
        else:
            self.app_state["profile"].cpu_affinity_mode = "all"

    def on_timer_changed(self, value: float):
        v = round(float(value), 2)
        self.app_state["profile"].timer_resolution_ms = v
        if self.w.get("timer_value"):
            self.w["timer_value"].configure(text=f"{v:.2f} мс")

    def on_ram_interval_changed(self, value: float):
        v = int(float(value))
        self.app_state["profile"].memory_cleanup_interval_sec = v
        if self.w.get("ram_interval_value"):
            self.w["ram_interval_value"].configure(text=f"{v} сек")

    def on_aggressive_changed(self):
        sw = self.w.get("aggressive_switch")
        if sw is None:
            return
        self.app_state["profile"].aggressive_ram_cleanup = bool(sw.get())

    def on_apply_clicked(self):
        if self.app_state.get("applying"):
            return
        if not self.app_state.get("toggles"):
            self._set_toast("Нет доступных оптимизаций", Colors.AMBER)
            return
        self._sync_game_names_from_textbox()
        self.app_state["applying"] = True
        self.app_state["apply_done"] = 0
        self.app_state["apply_total"] = sum(1 for t in self.app_state["toggles"] if t.enabled)
        self._set_toast(
            f"Применение оптимизаций (0/{self.app_state['apply_total']})...",
            Colors.AMBER,
        )
        if self.w.get("btn_apply"):
            self.w["btn_apply"].configure(state="disabled", text="Применение...")
        if self.w.get("btn_revert"):
            self.w["btn_revert"].configure(state="disabled")
        threading.Thread(target=self._apply_worker, daemon=True, name="apply").start()

    def _apply_worker(self):
        def on_progress(result):
            self._apply_queue.put(("progress", result))
        try:
            results = apply_optimizations(
                self.app_state["toggles"],
                self.app_state["profile"],
                self.app_state["report"],
                progress_callback=on_progress,
            )
            self._apply_queue.put(("done", results))
        except Exception as e: 
            self._apply_queue.put(("error", str(e)))

    def on_revert_clicked(self):
        if self.app_state.get("applying"):
            return
        if not self.app_state.get("toggles"):
            self._set_toast("Нет активных оптимизаций", Colors.AMBER)
            return
        self.app_state["applying"] = True
        self.app_state["apply_done"] = 0
        self.app_state["apply_total"] = sum(1 for t in self.app_state["toggles"] if t.enabled)
        self._set_toast(
            f"Отмена изменений (0/{self.app_state['apply_total']})...",
            Colors.AMBER,
        )
        if self.w.get("btn_apply"):
            self.w["btn_apply"].configure(state="disabled")
        if self.w.get("btn_revert"):
            self.w["btn_revert"].configure(state="disabled", text="Отмена...")
        threading.Thread(target=self._revert_worker, daemon=True, name="revert").start()

    def _revert_worker(self):
        def on_progress(result):
            self._apply_queue.put(("progress", result))
        try:
            results = revert_optimizations(
                self.app_state["toggles"],
                progress_callback=on_progress,
            )
            self._apply_queue.put(("done", results))
        except Exception as e: 
            self._apply_queue.put(("error", str(e)))

    def _on_apply_message(self, msg):
        kind = msg[0] if isinstance(msg, tuple) and msg else "error"
        data = msg[1] if isinstance(msg, tuple) and len(msg) > 1 else None

        if kind == "progress":
            done = self.app_state.get("apply_done", 0) + 1
            total = self.app_state.get("apply_total", 0)
            self.app_state["apply_done"] = done
            label = "Применение" if (data and data.action == "applied") else "Отмена"
            self._set_toast(
                f"{label} оптимизаций ({done}/{total})...",
                Colors.AMBER,
            )
            if data and hasattr(data, "toggle_id"):
                status = "✓" if data.success else "✗"
                level = "SUCCESS" if data.success else "WARNING"
                log(f"  {status} {data.toggle_id}: {data.message}", level)
            return

        self.app_state["applying"] = False
        if self.w.get("btn_apply"):
            self.w["btn_apply"].configure(state="normal", text="Применить оптимизации")
        if self.w.get("btn_revert"):
            self.w["btn_revert"].configure(state="normal", text="Отменить изменения")
        if self.w.get("btn_kill_bg"):
            self.w["btn_kill_bg"].configure(state="normal", text="Закрыть фоновые")

        if kind == "error":
            self._set_toast(f"Ошибка: {data}", Colors.RED)
            log(f"Ошибка применения: {data}", "ERROR")
            return

        results = data or []
        ok = sum(1 for r in results if r.success)
        fail = len(results) - ok
        is_revert = bool(results) and results[0].action == "reverted"
        if is_revert:
            msg_text = f"Отменено: {ok} успешно"
            log(f"=== Откат завершён: {ok} успешно, {fail} с ошибкой ===", "SUCCESS" if not fail else "WARNING")
        else:
            msg_text = f"Применено: {ok} успешно"
            log(f"=== Применение завершено: {ok} успешно, {fail} с ошибкой ===", "SUCCESS" if not fail else "WARNING")
        if fail:
            msg_text += f", {fail} с ошибкой"
        self._set_toast(msg_text, Colors.EMERALD if not fail else Colors.AMBER)

    def destroy(self):
        self._stop_event.set()
        try:
            super().destroy()
        except Exception:
            pass

def main():
    app = StalZoneApp()
    app.resizable(False, False) 
    app.mainloop()

if __name__ == "__main__":
    main()
