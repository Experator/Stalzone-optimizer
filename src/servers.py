import os
import json
import subprocess
import threading
import queue
import platform
import socket
import time
import urllib.request
import urllib.error
import ssl
import customtkinter as ctk
from concurrent.futures import ThreadPoolExecutor

from src.theme import Colors, FONTS

class ServerBlockerTab(ctk.CTkFrame):
    def __init__(self, master, log_func=None, toast_func=None, **kwargs):
        super().__init__(master, fg_color=Colors.BG_DARK, corner_radius=10, **kwargs)
        self.log_func = log_func
        self.toast_func = toast_func
        self.tunnels = {}
        self._apply_queue = queue.Queue()
        self._stop_event = threading.Event()
        
        self.total_to_apply = 0
        self.applied_count = 0
        
        self.total_to_ping = 0
        self.pinged_count = 0
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()
        self._build_list()
        
        self.after(100, self._drain_queue)
        self._load_servers()

    def _build_topbar(self):
        topbar = ctk.CTkFrame(self, fg_color=Colors.BG_PANEL, corner_radius=8, height=44)
        topbar.grid(row=0, column=0, sticky="ew", pady=(10, 8), padx=10)
        topbar.grid_columnconfigure(0, weight=1)
        topbar.grid_propagate(False)

        self.status_label = ctk.CTkLabel(
            topbar, text="Блокировщик серверов / туннелей",
            font=FONTS["body_bold"], text_color=Colors.AMBER
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=14, pady=8)

        btns_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        btns_frame.grid(row=0, column=1, sticky="e", padx=10, pady=6)

        self.btn_ping = ctk.CTkButton(
            btns_frame, text="Проверить пинг", width=130, height=28,
            font=FONTS["small"], fg_color=Colors.BG_PANEL_LIGHT,
            hover_color=Colors.BORDER_LIGHT, text_color=Colors.TEXT_PRIMARY,
            command=self.on_check_ping
        )
        self.btn_ping.grid(row=0, column=0, padx=4)

        self.btn_apply = ctk.CTkButton(
            btns_frame, text="Применить туннели", width=150, height=28,
            font=FONTS["small"], fg_color=Colors.EMERALD,
            hover_color=Colors.EMERALD_DARK, text_color=Colors.TEXT_PRIMARY,
            command=self.on_apply
        )
        self.btn_apply.grid(row=0, column=1, padx=4)

    def _build_list(self):
        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color=Colors.BG_DARK, corner_radius=0,
            scrollbar_button_color=Colors.BORDER_LIGHT,
            scrollbar_button_hover_color=Colors.AMBER,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.scroll.grid_columnconfigure(0, weight=1)

    def _load_servers(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.json_path = os.path.join(base_dir, "servers.json")

        ctk.CTkLabel(
            self.scroll, text="⏳ Загрузка серверов...", 
            font=FONTS["body"], text_color=Colors.TEXT_MUTED
        ).pack(pady=40)
        
        self._set_toast("Загрузка списка серверов...", Colors.AMBER)
        threading.Thread(target=self._fetch_servers_worker, daemon=True).start()

    def _fetch_servers_worker(self):
        url = "https://backend.stalcraftx.ru/address_list?login=Hi"
        data = None
        err_msg = ""
        
        try:
            ctx = ssl._create_unverified_context()
            
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Accept': 'application/json'
            })
            with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
                content = response.read().decode('utf-8')
                data = json.loads(content)
                
                with open(self.json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                
                self._apply_queue.put(("fetch_done", {"success": True, "data": data, "msg": "Список серверов обновлен из сети"}))
                return
                
        except urllib.error.HTTPError as e:
            err_msg = f"HTTP Ошибка {e.code} ({e.reason})"
        except urllib.error.URLError as e:
            err_msg = f"Ошибка сети/URL ({e.reason})"
        except json.JSONDecodeError:
            err_msg = "Сервер вернул некорректный формат (не JSON)"
        except Exception as e:
            err_msg = f"Неизвестная ошибка: {e}"
            
        self._apply_queue.put(("fetch_status", {"msg": f"Загрузка из сети не удалась: {err_msg}. Ищем кэш..."}))
        
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._apply_queue.put(("fetch_done", {"success": True, "data": data, "msg": "Серверы загружены из локального кэша (servers.json)"}))
                return
            except Exception as e:
                self._apply_queue.put(("fetch_done", {"success": False, "data": None, "msg": f"Ошибка чтения servers.json: {e}"}))
                return
        
        txt_path = os.path.join(os.path.dirname(self.json_path), "servers.txt")
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                with open(self.json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                self._apply_queue.put(("fetch_done", {"success": True, "data": data, "msg": "Серверы загружены из servers.txt"}))
                return
            except Exception:
                pass
                
        self._apply_queue.put(("fetch_done", {"success": False, "data": None, "msg": f"Не удалось загрузить серверы. Причина: {err_msg}. Кэш отсутствует."}))

    def _normalize_data(self, raw_data):
        """Приводит данные от API к единому формату {pools: [...]}"""
        if isinstance(raw_data, dict) and "pools" in raw_data:
            return raw_data
        
        if isinstance(raw_data, list):
            tunnels = []
            for item in raw_data:
                if isinstance(item, dict) and "address" in item:
                    t_name = item.get("name", item.get("address"))
                    tunnels.append({"name": t_name, "address": item.get("address")})
                elif isinstance(item, str) and ":" in item:
                    tunnels.append({"name": item, "address": item})
            
            if tunnels:
                return {"pools": [{"name": "Все серверы", "tunnels": tunnels}]}
                
        return raw_data

    def _render_servers(self, raw_data):
        for child in self.scroll.winfo_children():
            child.destroy()
        self.tunnels.clear()

        data = self._normalize_data(raw_data)
        if not isinstance(data, dict) or "pools" not in data:
            ctk.CTkLabel(self.scroll, text="Неверный формат данных серверов!", text_color=Colors.RED).pack(pady=20)
            return

        pools = data.get("pools", [])
        row_idx = 0
        for pool in pools:
            pool_name = pool.get("name", "Unknown")
            tunnels = pool.get("tunnels", [])

            pool_frame = ctk.CTkFrame(self.scroll, fg_color=Colors.BG_PANEL, corner_radius=6)
            pool_frame.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=(8, 4))
            pool_frame.grid_columnconfigure(0, weight=1)
            row_idx += 1

            header_row = ctk.CTkFrame(pool_frame, fg_color="transparent")
            header_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
            header_row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                header_row, text=f"Имя сервера: {pool_name}", font=FONTS["subheading"],
                text_color=Colors.AMBER
            ).grid(row=0, column=0, sticky="w")

            pool_switches = []
            pool_switch = ctk.CTkSwitch(
                header_row, text="Блок. все", width=46, height=22,
                progress_color=Colors.EMERALD, button_color=Colors.TEXT_PRIMARY,
                button_hover_color=Colors.TEXT_SECONDARY, fg_color=Colors.BG_DARK,
                font=FONTS["small"]
            )
            pool_switch.grid(row=0, column=1, sticky="e", padx=4, pady=4)

            for t in tunnels:
                t_name = t.get("name", "")
                t_addr = t.get("address", "")
                
                tunnel_row = ctk.CTkFrame(pool_frame, fg_color=Colors.BG_PANEL_LIGHT, corner_radius=6)
                tunnel_row.grid(sticky="ew", padx=4, pady=2)
                tunnel_row.grid_columnconfigure(1, weight=1)

                ctk.CTkLabel(
                    tunnel_row, text=t_name, font=FONTS["body_bold"],
                    text_color=Colors.TEXT_PRIMARY, width=160, anchor="w"
                ).grid(row=0, column=0, sticky="w", padx=8, pady=6)

                ctk.CTkLabel(
                    tunnel_row, text=t_addr, font=FONTS["mono_small"],
                    text_color=Colors.TEXT_MUTED, width=150, anchor="w"
                ).grid(row=0, column=1, sticky="w", padx=8, pady=6)

                ping_label = ctk.CTkLabel(
                    tunnel_row, text="Ожидание...", font=FONTS["mono_small"],
                    text_color=Colors.TEXT_MUTED, width=130, anchor="w"
                )
                ping_label.grid(row=0, column=2, sticky="w", padx=4, pady=6)

                switch = ctk.CTkSwitch(
                    tunnel_row, text="Блок", width=46, height=22,
                    progress_color=Colors.EMERALD, button_color=Colors.TEXT_PRIMARY,
                    button_hover_color=Colors.TEXT_SECONDARY, fg_color=Colors.BG_DARK
                )
                switch.grid(row=0, column=3, sticky="e", padx=12, pady=6)
                
                pool_switches.append(switch)
                self.tunnels[t_name] = {
                    "switch": switch, 
                    "addr": t_addr, 
                    "ping_label": ping_label
                }
            
            pool_switch.configure(command=lambda ps=pool_switch, sws=pool_switches: self._toggle_pool(ps, sws))
            ctk.CTkLabel(pool_frame, text="", height=2).grid()

    def _toggle_pool(self, pool_switch, switches):
        is_on = bool(pool_switch.get())
        for sw in switches:
            if is_on:
                sw.select()
            else:
                sw.deselect()

    def on_check_ping(self):
        self.btn_ping.configure(state="disabled", text="Проверка...")
        self.total_to_ping = len(self.tunnels)
        self.pinged_count = 0
        
        for data in self.tunnels.values():
            data["ping_label"].configure(text="Проверка...", text_color=Colors.TEXT_MUTED)
            
        self._set_toast(f"Проверка пинга: 0/{self.total_to_ping}", Colors.AMBER)
        self._log("Начата проверка пинга серверов (TCP)", "INFO")
        threading.Thread(target=self._ping_worker, daemon=True).start()

    def _ping_worker(self):
        def check_server_tcp(name, addr):
            try:
                ip, port_str = addr.split(":")
                port = int(port_str)
            except:
                return name, 9999, 100
                
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            start = time.time()
            try:
                s.connect((ip, port))
                s.close()
                ping_ms = (time.time() - start) * 1000
                return name, int(ping_ms), 0
            except Exception:
                return name, 9999, 100

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = []
            for name, data in self.tunnels.items():
                futures.append(executor.submit(check_server_tcp, name, data["addr"]))
            
            for future in futures:
                name, ping, loss = future.result()
                self._apply_queue.put(("ping_update", {"name": name, "ping": ping, "loss": loss}))
        
        self._apply_queue.put(("ping_done", None))

    def on_apply(self):
        self.btn_apply.configure(state="disabled", text="Применение...")
        self.total_to_apply = len(self.tunnels)
        self.applied_count = 0
        self._set_toast(f"Применение блокировок: 0/{self.total_to_apply}", Colors.AMBER)
        self._log("Начато применение блокировок туннелей", "INFO")
        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _apply_worker(self):
        actions = []
        for name, data in self.tunnels.items():
            is_on = bool(data["switch"].get())
            actions.append({"name": name, "addr": data["addr"], "block": is_on})
        
        for act in actions:
            ip_port = act["addr"]
            ip = ip_port.split(":")[0]
            rule_name = f"StalZone_Block_{act['name']}"
            
            try:
                if act["block"]:
                    self._run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}"')
                    ok, msg = self._run_netsh(f'netsh advfirewall firewall add rule name="{rule_name}" dir=out action=block remoteip={ip}')
                    self._run_netsh(f'netsh advfirewall firewall add rule name="{rule_name}_in" dir=in action=block remoteip={ip}')
                    success = ok
                else:
                    self._run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}"')
                    self._run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_in"')
                    success = True
                
                self._apply_queue.put(("progress", {"name": act["name"], "block": act["block"], "success": success}))
            except Exception as e:
                self._apply_queue.put(("progress", {"name": act["name"], "block": act["block"], "success": False, "msg": str(e)}))
        
        self._apply_queue.put(("done", None))

    def _run_netsh(self, cmd):
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 15}
            if platform.system().lower() == "windows":
                kwargs["creationflags"] = 0x08000000
            r = subprocess.run(cmd, shell=True, **kwargs)
            return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
        except Exception as e:
            return False, str(e)

    def _drain_queue(self):
        try:
            while True:
                try:
                    msg = self._apply_queue.get_nowait()
                except queue.Empty:
                    break
                
                kind, data = msg
                
                if kind == "fetch_status":
                    self._log(data["msg"], "WARNING")
                
                elif kind == "fetch_done":
                    for child in self.scroll.winfo_children():
                        child.destroy() 
                    
                    if data["success"]:
                        self._render_servers(data["data"])
                        self._set_toast("Серверы загружены", Colors.EMERALD)
                        self._log(data["msg"], "INFO")
                    else:
                        ctk.CTkLabel(self.scroll, text=data["msg"], text_color=Colors.RED).pack(pady=20)
                        self._set_toast("Ошибка загрузки серверов", Colors.RED)
                        self._log(data["msg"], "ERROR")

                elif kind == "ping_update":
                    self.pinged_count += 1
                    self._set_toast(f"Проверка пинга: {self.pinged_count}/{self.total_to_ping}", Colors.AMBER)
                    
                    name = data["name"]
                    ping = data["ping"]
                    loss = data["loss"]
                    
                    if name in self.tunnels:
                        lbl = self.tunnels[name]["ping_label"]
                        
                        if ping == 9999:
                            lbl.configure(text="Нет ответа", text_color=Colors.RED)
                        elif loss > 0:
                            lbl.configure(text=f"{ping} ms | {loss}% потерь", text_color=Colors.RED)
                        elif ping <= 50:
                            lbl.configure(text=f"{ping} ms | {loss}%", text_color=Colors.TIER_HIGH)
                        elif ping <= 100:
                            lbl.configure(text=f"{ping} ms | {loss}%", text_color=Colors.TIER_MID)
                        else:
                            lbl.configure(text=f"{ping} ms | {loss}%", text_color=Colors.RED)

                elif kind == "ping_done":
                    self.btn_ping.configure(state="normal", text="Проверить пинг")
                    self._set_toast(f"Проверка пинга завершена ({self.pinged_count})", Colors.EMERALD)
                    self._log("Проверка пинга завершена", "SUCCESS")

                elif kind == "progress":
                    self.applied_count += 1
                    self._set_toast(f"Применение блокировок: {self.applied_count}/{self.total_to_apply}", Colors.AMBER)
                    status = "✓" if data["success"] else "✗"
                    act = "заблокирован" if data["block"] else "разблокирован"
                    level = "SUCCESS" if data["success"] else "ERROR"
                    self._log(f"  {status} {data['name']}: {act}", level)
                    
                elif kind == "done":
                    self.btn_apply.configure(state="normal", text="Применить туннели")
                    self._set_toast(f"Блокировки применены ({self.applied_count})", Colors.EMERALD)
                    self._log("Применение блокировок завершено", "SUCCESS")
                    
        except Exception:
            pass
        
        if not self._stop_event.is_set():
            self.after(100, self._drain_queue)

    def _log(self, message, level="INFO"):
        if self.log_func:
            self.log_func(message, level)

    def _set_toast(self, text, color=Colors.TEXT_SECONDARY):
        if self.toast_func:
            self.toast_func(text, color)

    def stop(self):
        self._stop_event.set()
