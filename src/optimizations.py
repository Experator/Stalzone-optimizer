import os
import platform
import subprocess
import threading
from typing import List, Optional, Tuple

import psutil

from .models import (
    OptimizationToggle,
    OptimizationProfile,
    HardwareReport,
    ApplyResult,
)

__all__ = ["apply_optimizations", "revert_optimizations", "APPLY_FUNCS", "REVERT_FUNCS"]


_is_windows = platform.system().lower() == "windows"

_cleanup_thread: Optional[threading.Thread] = None
_cleanup_stop_event = threading.Event()

_current_timer_period: Optional[int] = None


GAME_NAMES = ["Stalcraft.exe", "Stalcraftw.exe", "Stalzone.exe", "Stalzonew.exe"]

BACKGROUND_APPS = [
    "OneDrive.exe", "Skype.exe", "Spotify.exe", "EpicGamesLauncher.exe",
    "Dropbox.exe", "TeamViewer.exe", "AnyDesk.exe", "Zoom.exe",
    "Teams.exe", "Slack.exe",
]

HW_ACCEL_APPS = ["chrome.exe", "msedge.exe", "discord.exe"]

def _run(cmd: str, timeout: int = 30) -> Tuple[bool, str]:
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": timeout}
        if _is_windows:
            kwargs["creationflags"] = 0x08000000  
        r = subprocess.run(cmd, shell=True, **kwargs)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, (r.stderr.strip() or r.stdout.strip() or f"Exit code {r.returncode}")
    except subprocess.TimeoutExpired:
        return False, f"Таймаут команды: {cmd}"
    except Exception as e:
        return False, str(e)


def _run_ps(script: str, timeout: int = 30) -> Tuple[bool, str]:
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": timeout}
        if _is_windows:
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            **kwargs,
        )
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, (r.stderr.strip() or r.stdout.strip() or f"PS exit {r.returncode}")
    except subprocess.TimeoutExpired:
        return False, f"Таймаут PowerShell: {script[:80]}"
    except Exception as e:
        return False, str(e)


def _is_hex_value(value: str) -> bool:
    if not value:
        return False
    
    if value.lower().startswith("0x"):
        return False
    
    return all(c in "0123456789abcdefABCDEF" for c in value) and any(c in "abcdefABCDEF" for c in value)


def _reg_add(path: str, name: str, value: str, vtype: str = "REG_DWORD") -> Tuple[bool, str]:
    if vtype == "REG_DWORD" and _is_hex_value(value):
        value = "0x" + value
    return _run(f'reg add "{path}" /v "{name}" /t {vtype} /d {value} /f')


def _win_only_skip(toggle_id: str, action: str) -> ApplyResult:
    return ApplyResult(
        toggle_id=toggle_id,
        success=False,
        message="Оптимизация доступна только на Windows",
        action=action,
    )


def _find_game_process(profile: OptimizationProfile):
    names = profile.game_process_names if profile.game_process_names else GAME_NAMES
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name", "") or ""
            if any(n.lower() in name.lower() for n in names):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _kill_processes(names: List[str]) -> int:
    killed = 0
    lower_names = [n.lower() for n in names]
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name", "") or ""
            name_lower = name.lower()
            if any(name_lower == n or name_lower.endswith(n) for n in lower_names):
                proc.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def _trim_working_sets() -> None:
    if not _is_windows:
        return
    try:
        psapi = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32
        PROCESS_ALL_ACCESS = 0x1F0FFF
        for proc in psutil.process_iter(["pid"]):
            try:
                pid = proc.info.get("pid")
                if not pid:
                    continue
                handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
                if handle:
                    try:
                        psapi.EmptyWorkingSet(handle)
                    except Exception:
                        pass
                    finally:
                        kernel32.CloseHandle(handle)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass


def _run_standby_cleanup() -> Tuple[bool, str]:
    ok, out = _run("EmptyStandbyList.exe standbylist", timeout=20)
    if ok:
        return True, "EmptyStandbyList: standby list очищен"

    _trim_working_sets()
    return True, "Working sets обрезаны (fallback EmptyStandbyList недоступен)"


def _periodic_cleanup_worker(interval: int, stop_event: threading.Event) -> None:
    while not stop_event.wait(interval):
        try:
            _trim_working_sets()
        except Exception:

            pass


def _get_fixed_drives() -> List[str]:
    if not _is_windows:
        return []

    ok, out = _run_ps(
        "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | "
        "Select-Object -ExpandProperty DeviceID"
    )
    if ok and out:
        drives: List[str] = []
        for line in out.split("\n"):
            line = line.strip()
            if len(line) == 2 and line[1] == ":":
                drives.append(line)
        if drives:
            return drives

    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{letter}:"
        if os.path.exists(drive + "\\"):

            try:
                import ctypes
                # DRIVE_FIXED = 3
                dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive + "\\"))
                if dtype == 3:
                    drives.append(drive)
            except Exception:
                drives.append(drive)
    return drives


def _get_drives_by_type() -> dict:
    result = {"HDD": [], "SSD": [], "NVMe": [], "unknown": []}
    if not _is_windows:
        return result
    drives = _get_fixed_drives()
    if not drives:
        return result

    ok, out = _run_ps(
        "Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object { "
        "$disk = Get-PhysicalDisk | Where-Object { $_.DeviceId -eq (Get-Disk -Number $_.DiskNumber).Number }; "
        "'{0}={1}' -f $_.DriveLetter, $disk.MediaType }"
    )
    letter_to_type: dict = {}
    if ok and out:
        for line in out.split("\n"):
            line = line.strip()
            if "=" in line:
                letter_part, media = line.split("=", 1)
                letter = letter_part.strip()
                media = media.strip()
                if letter:
                    drive_letter = letter + ":"
                    letter_to_type[drive_letter] = media
    for drive in drives:
        media = letter_to_type.get(drive, "")
        if media == "HDD":
            result["HDD"].append(drive)
        elif media == "SSD" or media == "SCM":
            result["SSD"].append(drive)
        elif media == "Unspecified":
 
            ok2, out2 = _run(f"defrag {drive} /A", timeout=30)
            if ok2:
                lower = out2.lower()
                if "solid state" in lower or "ssd" in lower:
                    result["SSD"].append(drive)
                elif "hard disk drive" in lower or "hdd" in lower or "rotational" in lower:
                    result["HDD"].append(drive)
                else:
                    result["unknown"].append(drive)
            else:
                result["unknown"].append(drive)
        else:
            result["unknown"].append(drive)
    return result


def _enumerate_tcpip_interfaces() -> List[str]:

    ok, out = _run(
        r'reg query "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"'
    )
    if not ok:
        return []
    paths: List[str] = []
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("HKEY"):
            paths.append(line)
    return paths

import ctypes 

# POWER 

def _apply_power_plan(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _run("powercfg /setactive fc936f94-8d9e-4d27-b579-28b6178adddf")
    return ApplyResult(
        toggle.id, ok,
        "Схема питания: Максимальная производительность" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_power_plan(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _run("powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e")
    return ApplyResult(
        toggle.id, ok,
        "Схема питания: Сбалансированная" if ok else f"Ошибка: {msg}",
        "reverted",
    )


def _apply_timer_resolution(toggle, profile, report) -> ApplyResult:
    global _current_timer_period
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    try:
        # timeBeginPeriod expects an integer in ms; clamp to >= 1.
        period = max(1, int(profile.timer_resolution_ms + 0.5))
        result = ctypes.windll.winmm.timeBeginPeriod(period)
        if result == 0:  # MMSYSERR_NOERROR
            _current_timer_period = period
            return ApplyResult(
                toggle.id, True,
                f"Таймер разрешения: {period} мс",
                "applied",
            )
        return ApplyResult(
            toggle.id, False,
            f"Ошибка timeBeginPeriod: код {result}",
            "applied",
        )
    except Exception as e:
        return ApplyResult(toggle.id, False, f"Исключение: {e}", "applied")


def _revert_timer_resolution(toggle) -> ApplyResult:
    global _current_timer_period
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    try:
        period = _current_timer_period or 1
        ctypes.windll.winmm.timeEndPeriod(period)
        _current_timer_period = None
        return ApplyResult(toggle.id, True, "Таймер разрешения: восстановлен", "reverted")
    except Exception as e:
        return ApplyResult(toggle.id, False, f"Исключение: {e}", "reverted")


def _apply_core_parking_off(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok1, msg1 = _run("powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR CPMINCORES 100")
    ok2, msg2 = _run("powercfg /setactive SCHEME_CURRENT")
    if ok1 and ok2:
        return ApplyResult(
            toggle.id, True,
            "Core Parking: отключен (100% ядер активны)",
            "applied",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {msg1 or msg2}", "applied")


def _revert_core_parking_off(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok1, msg1 = _run("powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR CPMINCORES 0")
    ok2, msg2 = _run("powercfg /setactive SCHEME_CURRENT")
    if ok1 and ok2:
        return ApplyResult(
            toggle.id, True,
            "Core Parking: восстановлен (значение по умолчанию)",
            "reverted",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {msg1 or msg2}", "reverted")

# CPU 

def _apply_cpu_affinity_physical(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    proc = _find_game_process(profile)
    if proc is None:
        return ApplyResult(
            toggle.id, False,
            "Игровой процесс не найден — affinity не применён",
            "applied",
        )
    try:
        mode = (profile.cpu_affinity_mode or "physical").lower()
        if mode == "all":
            logical = report.cpu.cores_logical or psutil.cpu_count(logical=True) or 0
            cores = list(range(logical))
        elif mode == "custom":
            cores = list(profile.custom_affinity_cores or [])
        else:  # physical
            physical = report.cpu.cores_physical or psutil.cpu_count(logical=False) or 0
            cores = list(range(physical))
        if not cores:
            return ApplyResult(
                toggle.id, False,
                "Не удалось определить список ядер CPU",
                "applied",
            )
        proc.cpu_affinity(cores)
        return ApplyResult(
            toggle.id, True,
            f"CPU affinity: ядра {cores} (режим: {mode})",
            "applied",
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return ApplyResult(toggle.id, False, f"Доступ запрещён: {e}", "applied")
    except Exception as e:
        return ApplyResult(toggle.id, False, f"Исключение: {e}", "applied")


def _revert_cpu_affinity_physical(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    
    dummy_profile = OptimizationProfile(
        toggles=[],
        game_process_names=GAME_NAMES,
        timer_resolution_ms=1.0,
        process_priority="high",
        cpu_affinity_mode="physical",
    )
    proc = _find_game_process(dummy_profile)
    if proc is None:
        return ApplyResult(
            toggle.id, True,
            "Игровой процесс не запущен — откат affinity не требуется",
            "reverted",
        )
    try:
        logical = psutil.cpu_count(logical=True) or 0
        proc.cpu_affinity(list(range(logical)))
        return ApplyResult(
            toggle.id, True,
            f"CPU affinity: восстановлен на все ядра ({logical})",
            "reverted",
        )
    except Exception as e:
        return ApplyResult(toggle.id, False, f"Ошибка: {e}", "reverted")


def _apply_process_priority_high(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    proc = _find_game_process(profile)
    if proc is None:
        return ApplyResult(
            toggle.id, False,
            "Игровой процесс не найден — приоритет не применён",
            "applied",
        )
    priority_str = (profile.process_priority or "high").lower()
    priority_map = {
        "above_normal": "ABOVE_NORMAL_PRIORITY_CLASS",
        "high": "HIGH_PRIORITY_CLASS",
        "realtime": "REALTIME_PRIORITY_CLASS",
    }
    attr = priority_map.get(priority_str, "HIGH_PRIORITY_CLASS")
    priority_val = getattr(psutil, attr, None)
    if priority_val is None:
        return ApplyResult(
            toggle.id, False,
            f"Неизвестный приоритет или недоступен на этой платформе: {priority_str}",
            "applied",
        )
    try:
        proc.nice(priority_val)
        return ApplyResult(
            toggle.id, True,
            f"Приоритет процесса: {priority_str}",
            "applied",
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return ApplyResult(toggle.id, False, f"Доступ запрещён: {e}", "applied")
    except Exception as e:
        return ApplyResult(toggle.id, False, f"Исключение: {e}", "applied")


def _revert_process_priority_high(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    dummy_profile = OptimizationProfile(
        toggles=[],
        game_process_names=GAME_NAMES,
        timer_resolution_ms=1.0,
        process_priority="high",
        cpu_affinity_mode="physical",
    )
    proc = _find_game_process(dummy_profile)
    if proc is None:
        return ApplyResult(
            toggle.id, True,
            "Игровой процесс не запущен — откат приоритета не требуется",
            "reverted",
        )
    try:
        normal_val = getattr(psutil, "NORMAL_PRIORITY_CLASS", None)
        if normal_val is None:
            return ApplyResult(
                toggle.id, False,
                "NORMAL_PRIORITY_CLASS недоступен на этой платформе",
                "reverted",
            )
        proc.nice(normal_val)
        return ApplyResult(
            toggle.id, True,
            "Приоритет процесса: восстановлен на Normal",
            "reverted",
        )
    except Exception as e:
        return ApplyResult(toggle.id, False, f"Ошибка: {e}", "reverted")


def _apply_disable_game_dvr(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok1, msg1 = _reg_add(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", "0")
    ok2, msg2 = _reg_add(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR", "FTRenabled", "0"
    )
    if ok1 and ok2:
        return ApplyResult(toggle.id, True, "Game DVR: отключён", "applied")
    return ApplyResult(toggle.id, False, f"Ошибка: {msg1 or msg2}", "applied")

def _revert_disable_game_dvr(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok1, msg1 = _reg_add(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", "1")
    ok2, msg2 = _reg_add(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR", "FTRenabled", "1"
    )
    if ok1 and ok2:
        return ApplyResult(
            toggle.id, True,
            "Game DVR: включён (по умолчанию)",
            "reverted",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {msg1 or msg2}", "reverted")

# MEMORY

def _apply_ram_standby_cleanup(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _run_standby_cleanup()
    return ApplyResult(
        toggle.id, ok,
        "Standby list / working sets: очищены" if ok else f"Ошибка: {msg}",
        "applied",
    )

def _revert_ram_standby_cleanup(toggle) -> ApplyResult:
    return ApplyResult(
        toggle.id, True,
        "Откат не требуется (RAM уже очищена)",
        "reverted",
    )

def _apply_ram_periodic_cleanup(toggle, profile, report) -> ApplyResult:
    global _cleanup_thread, _cleanup_stop_event
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")

    _cleanup_stop_event.set()
    if _cleanup_thread and _cleanup_thread.is_alive():
        _cleanup_thread.join(timeout=2)

    _cleanup_stop_event = threading.Event()
    interval = int(profile.memory_cleanup_interval_sec or 300)
    if interval < 10:
        interval = 10  
    _cleanup_thread = threading.Thread(
        target=_periodic_cleanup_worker,
        args=(_cleanup_interval_for(profile), _cleanup_stop_event),
        daemon=True,
        name="stalzone-ram-cleanup",
    )
    _cleanup_thread.start()
    return ApplyResult(
        toggle.id, True,
        f"Периодическая очистка RAM запущена (каждые {interval} сек)",
        "applied",
    )


def _cleanup_interval_for(profile: OptimizationProfile) -> int:
    interval = int(profile.memory_cleanup_interval_sec or 300)
    return max(10, interval)


def _revert_ram_periodic_cleanup(toggle) -> ApplyResult:
    global _cleanup_stop_event, _cleanup_thread
    _cleanup_stop_event.set()
    if _cleanup_thread and _cleanup_thread.is_alive():
        _cleanup_thread.join(timeout=2)
    _cleanup_thread = None
    return ApplyResult(
        toggle.id, True,
        "Периодическая очистка RAM остановлена",
        "reverted",
    )


def _apply_disable_swap_file(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")

    if report.ram.total_gb < 16:
        return ApplyResult(
            toggle.id, False,
            f"Отключение файла подкачки требует минимум 16 ГБ RAM (у вас {report.ram.total_gb:.1f} ГБ)",
            "applied",
        )

    ok, msg = _run_ps(
        "Set-CimInstance -Query 'Select * from Win32_ComputerSystem' "
        "-Property @{AutomaticManagedPagefile=$false}"
    )
    if not ok:

        ok2, msg2 = _run(
            r'wmic computersystem where name="%computername%" set AutomaticManagedPagefile=False',
            timeout=15,
        )
        if ok2:
            return ApplyResult(
                toggle.id, True,
                "Файл подкачки: авто-управление отключено (через wmic)",
                "applied",
            )
        return ApplyResult(
            toggle.id, False,
            f"Ошибка: {msg or msg2}",
            "applied",
        )
    return ApplyResult(
        toggle.id, True,
        "Файл подкачки: авто-управление отключено",
        "applied",
    )


def _revert_disable_swap_file(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _run_ps(
        "Set-CimInstance -Query 'Select * from Win32_ComputerSystem' "
        "-Property @{AutomaticManagedPagefile=$true}"
    )
    if not ok:

        ok2, msg2 = _run(
            r'wmic computersystem where name="%computername%" set AutomaticManagedPagefile=True',
            timeout=15,
        )
        if ok2:
            return ApplyResult(
                toggle.id, True,
                "Файл подкачки: авто-управление включено (через wmic)",
                "reverted",
            )
        return ApplyResult(
            toggle.id, False,
            f"Ошибка: {msg or msg2}",
            "reverted",
        )
    return ApplyResult(
        toggle.id, True,
        "Файл подкачки: авто-управление включено",
        "reverted",
    )


def _apply_large_system_cache(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
        "LargeSystemCache", "1",
    )
    return ApplyResult(
        toggle.id, ok,
        "LargeSystemCache: включён" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_large_system_cache(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
        "LargeSystemCache", "0",
    )
    return ApplyResult(
        toggle.id, ok,
        "LargeSystemCache: отключён (по умолчанию)" if ok else f"Ошибка: {msg}",
        "reverted",
    )


# GPU 

def _apply_gpu_power_management(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")

    game_paths = _resolve_game_executable_paths(profile)
    reg_ok_count = 0
    for exe_path in game_paths:
        ok, _ = _run(
            f'reg add "HKCU\\SOFTWARE\\Microsoft\\DirectX\\UserGpuPreferences" '
            f'/v "{exe_path}" /t REG_SZ /d "GpuPreference=2;" /f'
        )
        if ok:
            reg_ok_count += 1

    pcfg_aliases = [
        "GPUPREFERENCEPOLICY",
        "GPUPREFERENCEPOWERMODE", 
    ]
    pcfg_ok = False
    pcfg_msg = ""
    for alias in pcfg_aliases:
        ok, out = _run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO {alias} 2")
        if ok:
            pcfg_ok = True
            break
        pcfg_msg = pcfg_msg or out

    _run("powercfg /setactive SCHEME_CURRENT")

    nv_ok, _ = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000",
        "PerfLevelSrc", "0x3322",
    )

    success = reg_ok_count > 0 or pcfg_ok or nv_ok
    details = []
    if reg_ok_count:
        details.append(f"GpuPreference: {reg_ok_count} exe")
    if pcfg_ok:
        details.append("powercfg: GPU preference = High")
    if nv_ok:
        details.append("PowerMizer: max perf")
    if success:
        return ApplyResult(
            toggle.id, True,
            "GPU power management: максимальная производительность (" + ", ".join(details) + ")",
            "applied",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {pcfg_msg}", "applied")


def _resolve_game_executable_paths(profile: OptimizationProfile) -> List[str]:
    """Resolve game executable names (e.g. 'Stalcraft.exe') to full paths via PowerShell."""
    names = profile.game_process_names if profile.game_process_names else GAME_NAMES
    paths: List[str] = []
    for name in names:

        ok, out = _run_ps(
            f"Get-Process -Name '{os.path.splitext(name)[0]}' -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty Path"
        )
        if ok and out and os.path.isfile(out):
            paths.append(out)
            continue

        ok, out = _run_ps(
            f"$found = $null; "
            f"foreach ($base in @('C:\\Program Files','C:\\Program Files (x86)',"
            f"$env:LOCALAPPDATA,$env:USERPROFILE,'D:\\Games','D:\\Program Files')) {{ "
            f"  $p = Join-Path $base '{name}'; "
            f"  if (Test-Path $p) {{ $found = $p; break }} "
            f"  $hit = Get-ChildItem -Path $base -Filter '{name}' -Recurse -ErrorAction SilentlyContinue "
            f"    -Depth 4 | Select-Object -First 1; "
            f"  if ($hit) {{ $found = $hit.FullName; break }} "
            f"}}; if ($found) {{ $found }}"
        )
        if ok and out and os.path.isfile(out):
            paths.append(out)
    return paths


def _revert_gpu_power_management(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    reverted_count = 0

    ok, out = _run('reg query "HKCU\\SOFTWARE\\Microsoft\\DirectX\\UserGpuPreferences"')
    if ok and out:
        for line in out.split("\n"):
            line = line.strip()

            if "GpuPreference=2" in line and ".exe" in line:

                parts = line.split(" REG_SZ ", 1)
                if len(parts) == 2:
                    exe_path = parts[0].strip()
                    ok2, _ = _run(
                        f'reg delete "HKCU\\SOFTWARE\\Microsoft\\DirectX\\UserGpuPreferences" '
                        f'/v "{exe_path}" /f'
                    )
                    if ok2:
                        reverted_count += 1

    for alias in ["GPUPREFERENCEPOLICY", "GPUPREFERENCEPOWERMODE"]:
        _run(f"powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO {alias} 0")
    _run("powercfg /setactive SCHEME_CURRENT")
    return ApplyResult(
        toggle.id, True,
        f"GPU power management: восстановлен (по умолчанию), удалено GpuPreference: {reverted_count}",
        "reverted",
    )

def _apply_disable_hardware_acceleration(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    killed = _kill_processes(HW_ACCEL_APPS)
    return ApplyResult(
        toggle.id, True,
        f"Аппаратное ускорение: завершено процессов ({killed})",
        "applied",
    )


def _revert_disable_hardware_acceleration(toggle) -> ApplyResult:

    return ApplyResult(
        toggle.id, True,
        "Откат не требуется (приложения можно перезапустить вручную)",
        "reverted",
    )


def _apply_tdr_delay_increase(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
        "TdrDelay", "10",
    )
    return ApplyResult(
        toggle.id, ok,
        "TdrDelay: установлен в 10 сек" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_tdr_delay_increase(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
        "TdrDelay", "2",
    )
    return ApplyResult(
        toggle.id, ok,
        "TdrDelay: восстановлен в 2 сек (по умолчанию)" if ok else f"Ошибка: {msg}",
        "reverted",
    )


# NETWORK 

def _apply_disable_nagle(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    paths = _enumerate_tcpip_interfaces()
    if not paths:
        return ApplyResult(
            toggle.id, False,
            "Не удалось получить список сетевых адаптеров",
            "applied",
        )
    count = 0
    for path in paths:
        _reg_add(path, "TcpAckFrequency", "1")
        _reg_add(path, "TCPNoDelay", "1")
        count += 1
    return ApplyResult(
        toggle.id, True,
        f"Nagle отключён на {count} адаптерах",
        "applied",
    )


def _revert_disable_nagle(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    paths = _enumerate_tcpip_interfaces()
    count = 0
    for path in paths:
        _reg_add(path, "TcpAckFrequency", "2")
        _reg_add(path, "TCPNoDelay", "2")
        count += 1
    if count == 0:
        return ApplyResult(
            toggle.id, True,
            "Сетевые адаптеры не найдены — откат не требуется",
            "reverted",
        )
    return ApplyResult(
        toggle.id, True,
        f"Nagle восстановлен на {count} адаптерах",
        "reverted",
    )


def _apply_flush_dns(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _run("ipconfig /flushdns")
    return ApplyResult(
        toggle.id, ok,
        "DNS-кэш: очищен" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_flush_dns(toggle) -> ApplyResult:

    return ApplyResult(
        toggle.id, True,
        "Откат не требуется (DNS-кэш автоматически перестроится)",
        "reverted",
    )


def _apply_network_throttling_off(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")

    ok, msg = _reg_add(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
        "NetworkThrottlingIndex", "0xffffffff",
    )
    return ApplyResult(
        toggle.id, ok,
        "NetworkThrottlingIndex: отключён (0xffffffff)" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_network_throttling_off(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")

    ok, msg = _reg_add(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
        "NetworkThrottlingIndex", "10",
    )
    return ApplyResult(
        toggle.id, ok,
        "NetworkThrottlingIndex: восстановлен в 10 (по умолчанию)" if ok else f"Ошибка: {msg}",
        "reverted",
    )


# DISK

def _apply_disable_indexing(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    _run("sc stop WSearch", timeout=20)
    ok, msg = _run('sc config WSearch start= disabled')
    if ok:
        return ApplyResult(
            toggle.id, True,
            "Служба индексирования (WSearch): остановлена и отключена",
            "applied",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {msg}", "applied")


def _revert_disable_indexing(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, _ = _run('sc config WSearch start= demand')
    _run("sc start WSearch", timeout=20)
    if ok:
        return ApplyResult(
            toggle.id, True,
            "Служба индексирования (WSearch): включена (по требованию)",
            "reverted",
        )
    return ApplyResult(toggle.id, False, "Не удалось восстановить WSearch", "reverted")


def _apply_defrag_hdd(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    
    drives_by_type = _get_drives_by_type()
    hdd_drives = drives_by_type.get("HDD", [])

    candidate_drives = hdd_drives + drives_by_type.get("unknown", [])
    if not candidate_drives:
        return ApplyResult(
            toggle.id, True,
            "HDD не обнаружены — дефрагментация не требуется",
            "applied",
        )
    failed: List[str] = []
    done: List[str] = []
    for d in candidate_drives:

        ok, _ = _run(f"defrag {d} /H /U /V", timeout=600)
        if ok:
            done.append(d)
        else:
            failed.append(d)
    if done:
        return ApplyResult(
            toggle.id, True,
            f"Дефрагментация HDD: выполнена для {', '.join(done)}"
            + (f"; ошибки: {', '.join(failed)}" if failed else ""),
            "applied",
        )
    return ApplyResult(
        toggle.id, False,
        f"Дефрагментация не удалась на дисках: {', '.join(failed)}",
        "applied",
    )


def _revert_defrag_hdd(toggle) -> ApplyResult:

    return ApplyResult(
        toggle.id, True,
        "Откат не требуется (дефрагментация необратима)",
        "reverted",
    )


def _apply_ssd_trim(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")

    drives_by_type = _get_drives_by_type()
    ssd_drives = drives_by_type.get("SSD", [])

    candidate_drives = ssd_drives + drives_by_type.get("unknown", [])
    if not candidate_drives:
        return ApplyResult(
            toggle.id, True,
            "SSD/NVMe не обнаружены — TRIM не требуется",
            "applied",
        )
    failed: List[str] = []
    done: List[str] = []
    for d in candidate_drives:

        ok, _ = _run(f"defrag {d} /L", timeout=600)
        if ok:
            done.append(d)
        else:
            failed.append(d)
    if done:
        return ApplyResult(
            toggle.id, True,
            f"SSD TRIM (retrim): выполнен для {', '.join(done)}"
            + (f"; ошибки: {', '.join(failed)}" if failed else ""),
            "applied",
        )
    return ApplyResult(
        toggle.id, False,
        f"TRIM не удался на дисках: {', '.join(failed)}",
        "applied",
    )


def _revert_ssd_trim(toggle) -> ApplyResult:

    return ApplyResult(
        toggle.id, True,
        "Откат не требуется (TRIM необратим)",
        "reverted",
    )


# SERVICES 

def _apply_disable_sysmain(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    _run("sc stop SysMain", timeout=30)
    ok, msg = _run('sc config SysMain start= disabled')
    if ok:
        return ApplyResult(
            toggle.id, True,
            "SysMain (Superfetch): остановлена и отключена",
            "applied",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {msg}", "applied")


def _revert_disable_sysmain(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, _ = _run('sc config SysMain start= auto')
    _run("sc start SysMain", timeout=30)
    if ok:
        return ApplyResult(
            toggle.id, True,
            "SysMain (Superfetch): восстановлена (авто)",
            "reverted",
        )
    return ApplyResult(toggle.id, False, "Не удалось восстановить SysMain", "reverted")


def _apply_disable_diagnostic_tracking(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    _run("sc stop DiagTrack", timeout=30)
    ok, msg = _run('sc config DiagTrack start= disabled')
    if ok:
        return ApplyResult(
            toggle.id, True,
            "DiagTrack (Телеметрия): остановлена и отключена",
            "applied",
        )
    return ApplyResult(toggle.id, False, f"Ошибка: {msg}", "applied")


def _revert_disable_diagnostic_tracking(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, _ = _run('sc config DiagTrack start= auto')
    _run("sc start DiagTrack", timeout=30)
    if ok:
        return ApplyResult(
            toggle.id, True,
            "DiagTrack (Телеметрия): восстановлена (авто)",
            "reverted",
        )
    return ApplyResult(toggle.id, False, "Не удалось восстановить DiagTrack", "reverted")


def _apply_disable_windows_search(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")

    _run("sc stop WSearch", timeout=20)
    return ApplyResult(
        toggle.id, True,
        "Windows Search (WSearch): остановлена (только текущая сессия)",
        "applied",
    )


def _revert_disable_windows_search(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _run("sc start WSearch", timeout=20)
    return ApplyResult(
        toggle.id, True,
        "Windows Search (WSearch): запущена",
        "reverted",
    )


# VISUAL 

def _apply_visual_effects_performance(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
        "VisualFXSetting", "2",
    )
    return ApplyResult(
        toggle.id, ok,
        "Визуальные эффекты: режим максимальной производительности" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_visual_effects_performance(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _reg_add(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
        "VisualFXSetting", "0",
    )
    return ApplyResult(
        toggle.id, ok,
        "Визуальные эффекты: восстановлены (по умолчанию Windows)" if ok else f"Ошибка: {msg}",
        "reverted",
    )

def _apply_disable_transparency(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "EnableTransparency", "0",
    )
    return ApplyResult(
        toggle.id, ok,
        "Прозрачность: отключена" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_disable_transparency(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _reg_add(
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "EnableTransparency", "1",
    )
    return ApplyResult(
        toggle.id, ok,
        "Прозрачность: включена (по умолчанию)" if ok else f"Ошибка: {msg}",
        "reverted",
    )

# GAME 

def _apply_kill_background_apps(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    killed = _kill_processes(BACKGROUND_APPS)
    return ApplyResult(
        toggle.id, True,
        f"Фоновые приложения: завершено процессов ({killed})",
        "applied",
    )


def _revert_kill_background_apps(toggle) -> ApplyResult:

    return ApplyResult(
        toggle.id, True,
        "Откат не требуется (приложения можно перезапустить вручную)",
        "reverted",
    )


def _apply_game_mode_on(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKCU\Software\Microsoft\GameBar",
        "AutoGameModeEnabled", "1",
    )
    return ApplyResult(
        toggle.id, ok,
        "Game Mode: включён" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_game_mode_on(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _reg_add(
        r"HKCU\Software\Microsoft\GameBar",
        "AutoGameModeEnabled", "0",
    )
    return ApplyResult(
        toggle.id, ok,
        "Game Mode: отключён" if ok else f"Ошибка: {msg}",
        "reverted",
    )


def _apply_hardware_gpu_scheduler(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
        "HwSchMode", "2",
    )
    return ApplyResult(
        toggle.id, ok,
        "HwSchMode (HAGS): включён (требуется перезагрузка)" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_hardware_gpu_scheduler(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    ok, msg = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
        "HwSchMode", "1",
    )
    return ApplyResult(
        toggle.id, ok,
        "HwSchMode (HAGS): отключён (требуется перезагрузка)" if ok else f"Ошибка: {msg}",
        "reverted",
    )


# ============ SYSTEM (NEW) ============

def _apply_disable_usb_power_saving(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    # Disable USB selective suspend
    ok1, _ = _run("powercfg /setacvalueindex SCHEME_CURRENT SUB_USB USBSELSUSP 0")
    _run("powercfg /setactive SCHEME_CURRENT")
    # Also disable in registry for all USB host controllers
    ok2, _ = _reg_add(
        r"HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerSettings\2A737441-1930-4402-8D77-B2BEBBA308A3\48e6b7a6-50f5-4782-a5d4-53bb8f07e226",
        "ACSettingIndex", "0",
    )
    success = ok1 or ok2
    return ApplyResult(
        toggle.id, success,
        "USB selective suspend: отключён" if success else "Ошибка",
        "applied",
    )


def _revert_disable_usb_power_saving(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _run("powercfg /setacvalueindex SCHEME_CURRENT SUB_USB USBSELSUSP 1")
    _run("powercfg /setactive SCHEME_CURRENT")
    return ApplyResult(toggle.id, True, "USB selective suspend: включён", "reverted")


def _apply_disable_pci_express_link_state(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, _ = _run("powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0")
    _run("powercfg /setactive SCHEME_CURRENT")
    return ApplyResult(
        toggle.id, ok,
        "PCIe ASPM: отключён (Link State Power Management)" if ok else "Ошибка",
        "applied",
    )


def _revert_disable_pci_express_link_state(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _run("powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 2")
    _run("powercfg /setactive SCHEME_CURRENT")
    return ApplyResult(toggle.id, True, "PCIe ASPM: восстановлен", "reverted")


def _apply_clear_font_cache(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    # Stop the FontCache service, delete cache files, restart service
    _run("sc stop FontCache", timeout=15)
    cache_dir = r"C:\Windows\ServiceProfiles\LocalService\AppData\Local\FontCache"
    deleted = 0
    try:
        if os.path.isdir(cache_dir):
            for fname in os.listdir(cache_dir):
                if fname.endswith(".dat"):
                    try:
                        os.remove(os.path.join(cache_dir, fname))
                        deleted += 1
                    except Exception:
                        pass
    except Exception:
        pass
    _run("sc start FontCache", timeout=15)
    return ApplyResult(
        toggle.id, True,
        f"Кэш шрифтов: очищен (файлов удалено: {deleted})",
        "applied",
    )


def _revert_clear_font_cache(toggle) -> ApplyResult:
    return ApplyResult(toggle.id, True, "Откат не требуется (кэш перестроится автоматически)", "reverted")


def _apply_disable_windows_defender_realtime(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _run_ps(
        "Set-MpPreference -DisableRealtimeMonitoring $true -ErrorAction SilentlyContinue"
    )
    if not ok:
        # Fallback: registry
        ok2, _ = _reg_add(
            r"HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection",
            "DisableRealtimeMonitoring", "1",
        )
        return ApplyResult(
            toggle.id, ok2,
            "Defender RT: отключён через реестр (требуется перезагрузка)" if ok2
            else f"Ошибка (включена Tamper Protection?): {msg}",
            "applied",
        )
    return ApplyResult(
        toggle.id, True,
        "Windows Defender real-time: временно отключён",
        "applied",
    )


def _revert_disable_windows_defender_realtime(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _run_ps("Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction SilentlyContinue")
    _reg_add(
        r"HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection",
        "DisableRealtimeMonitoring", "0",
    )
    return ApplyResult(toggle.id, True, "Windows Defender real-time: включён", "reverted")


def _apply_disable_windows_update(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    _run("sc stop wuauserv", timeout=20)
    _run("sc stop UsoSvc", timeout=20)
    ok, msg = _run('sc config wuauserv start= disabled')
    _run('sc config UsoSvc start= disabled')
    return ApplyResult(
        toggle.id, ok,
        "Windows Update: остановлен и отключён" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_disable_windows_update(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _run('sc config wuauserv start= demand')
    _run('sc config UsoSvc start= demand')
    _run("sc start wuauserv", timeout=20)
    return ApplyResult(toggle.id, True, "Windows Update: восстановлен (по требованию)", "reverted")


def _apply_clear_temp_files(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    temp_dirs = [
        os.environ.get("TEMP", ""),
        os.environ.get("TMP", ""),
        r"C:\Windows\Temp",
    ]
    deleted = 0
    for temp_dir in temp_dirs:
        if not temp_dir or not os.path.isdir(temp_dir):
            continue
        try:
            for fname in os.listdir(temp_dir):
                fpath = os.path.join(temp_dir, fname)
                try:
                    if os.path.isfile(fpath):
                        os.remove(fpath)
                        deleted += 1
                    elif os.path.isdir(fpath):
                        import shutil
                        shutil.rmtree(fpath, ignore_errors=True)
                        deleted += 1
                except Exception:
                    pass
        except Exception:
            pass
    return ApplyResult(
        toggle.id, True,
        f"Временные файлы: очищено {deleted} объектов",
        "applied",
    )


def _revert_clear_temp_files(toggle) -> ApplyResult:
    return ApplyResult(toggle.id, True, "Откат не требуется (файлы удалены безвозвратно)", "reverted")


def _apply_disable_notifications(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    # Set Focus Assist to "alarms only" via registry
    ok1, _ = _reg_add(
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings",
        "NOC_GLOBAL_SETTING_TOASTS_ENABLED", "0",
    )
    ok2, _ = _reg_add(
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\QuietHours",
        "Enabled", "1",
    )
    success = ok1 or ok2
    return ApplyResult(
        toggle.id, success,
        "Уведомления: отключены (Focus Assist)" if success else "Ошибка",
        "applied",
    )


def _revert_disable_notifications(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _reg_add(
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings",
        "NOC_GLOBAL_SETTING_TOASTS_ENABLED", "1",
    )
    _reg_add(
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\QuietHours",
        "Enabled", "0",
    )
    return ApplyResult(toggle.id, True, "Уведомления: включены", "reverted")


def _apply_set_process_priority_class(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
        "GameMode", "High", "REG_SZ",
    )
    return ApplyResult(
        toggle.id, ok,
        "AppCompat: высокий приоритет для игр" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_set_process_priority_class(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _run('reg delete "HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\AppCompatFlags\\Layers" /v "GameMode" /f', timeout=10)
    return ApplyResult(toggle.id, True, "AppCompat: восстановлен", "reverted")


def _apply_disable_background_apps_global(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
        "GlobalUserDisabled", "1",
    )
    return ApplyResult(
        toggle.id, ok,
        "Фоновые приложения: глобально отключены" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_disable_background_apps_global(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _reg_add(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
        "GlobalUserDisabled", "0",
    )
    return ApplyResult(toggle.id, True, "Фоновые приложения: включены", "reverted")


def _apply_disable_cortana(toggle, profile, report) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "applied")
    ok, msg = _reg_add(
        r"HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search",
        "AllowCortana", "0",
    )
    return ApplyResult(
        toggle.id, ok,
        "Cortana: отключена" if ok else f"Ошибка: {msg}",
        "applied",
    )


def _revert_disable_cortana(toggle) -> ApplyResult:
    if not _is_windows:
        return _win_only_skip(toggle.id, "reverted")
    _reg_add(
        r"HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search",
        "AllowCortana", "1",
    )
    return ApplyResult(toggle.id, True, "Cortana: включена", "reverted")


# Dispatch dicts

APPLY_FUNCS = {
    # POWER
    "power_plan": _apply_power_plan,
    "timer_resolution": _apply_timer_resolution,
    "core_parking_off": _apply_core_parking_off,
    # CPU
    "cpu_affinity_physical": _apply_cpu_affinity_physical,
    "process_priority_high": _apply_process_priority_high,
    "disable_game_dvr": _apply_disable_game_dvr,
    # MEMORY
    "ram_standby_cleanup": _apply_ram_standby_cleanup,
    "ram_periodic_cleanup": _apply_ram_periodic_cleanup,
    "disable_swap_file": _apply_disable_swap_file,
    "large_system_cache": _apply_large_system_cache,
    # GPU
    "gpu_power_management": _apply_gpu_power_management,
    "disable_hardware_acceleration": _apply_disable_hardware_acceleration,
    "tdr_delay_increase": _apply_tdr_delay_increase,
    # NETWORK
    "disable_nagle": _apply_disable_nagle,
    "flush_dns": _apply_flush_dns,
    "network_throttling_off": _apply_network_throttling_off,
    # DISK
    "disable_indexing": _apply_disable_indexing,
    "defrag_hdd": _apply_defrag_hdd,
    "ssd_trim": _apply_ssd_trim,
    # SERVICES
    "disable_sysmain": _apply_disable_sysmain,
    "disable_diagnostic_tracking": _apply_disable_diagnostic_tracking,
    "disable_windows_search": _apply_disable_windows_search,
    # VISUAL
    "visual_effects_performance": _apply_visual_effects_performance,
    "disable_transparency": _apply_disable_transparency,
    # GAME
    "kill_background_apps": _apply_kill_background_apps,
    "game_mode_on": _apply_game_mode_on,
    "hardware_gpu_scheduler": _apply_hardware_gpu_scheduler,
    # SYSTEM (NEW)
    "disable_usb_power_saving": _apply_disable_usb_power_saving,
    "disable_pci_express_link_state": _apply_disable_pci_express_link_state,
    "clear_font_cache": _apply_clear_font_cache,
    "disable_windows_defender_realtime": _apply_disable_windows_defender_realtime,
    "disable_windows_update": _apply_disable_windows_update,
    "clear_temp_files": _apply_clear_temp_files,
    "disable_notifications": _apply_disable_notifications,
    "set_process_priority_class": _apply_set_process_priority_class,
    "disable_background_apps_global": _apply_disable_background_apps_global,
    "disable_cortana": _apply_disable_cortana,
}

REVERT_FUNCS = {
    # POWER
    "power_plan": _revert_power_plan,
    "timer_resolution": _revert_timer_resolution,
    "core_parking_off": _revert_core_parking_off,
    # CPU
    "cpu_affinity_physical": _revert_cpu_affinity_physical,
    "process_priority_high": _revert_process_priority_high,
    "disable_game_dvr": _revert_disable_game_dvr,
    # MEMORY
    "ram_standby_cleanup": _revert_ram_standby_cleanup,
    "ram_periodic_cleanup": _revert_ram_periodic_cleanup,
    "disable_swap_file": _revert_disable_swap_file,
    "large_system_cache": _revert_large_system_cache,
    # GPU
    "gpu_power_management": _revert_gpu_power_management,
    "disable_hardware_acceleration": _revert_disable_hardware_acceleration,
    "tdr_delay_increase": _revert_tdr_delay_increase,
    # NETWORK
    "disable_nagle": _revert_disable_nagle,
    "flush_dns": _revert_flush_dns,
    "network_throttling_off": _revert_network_throttling_off,
    # DISK
    "disable_indexing": _revert_disable_indexing,
    "defrag_hdd": _revert_defrag_hdd,
    "ssd_trim": _revert_ssd_trim,
    # SERVICES
    "disable_sysmain": _revert_disable_sysmain,
    "disable_diagnostic_tracking": _revert_disable_diagnostic_tracking,
    "disable_windows_search": _revert_disable_windows_search,
    # VISUAL
    "visual_effects_performance": _revert_visual_effects_performance,
    "disable_transparency": _revert_disable_transparency,
    # GAME
    "kill_background_apps": _revert_kill_background_apps,
    "game_mode_on": _revert_game_mode_on,
    "hardware_gpu_scheduler": _revert_hardware_gpu_scheduler,
    # SYSTEM (NEW)
    "disable_usb_power_saving": _revert_disable_usb_power_saving,
    "disable_pci_express_link_state": _revert_disable_pci_express_link_state,
    "clear_font_cache": _revert_clear_font_cache,
    "disable_windows_defender_realtime": _revert_disable_windows_defender_realtime,
    "disable_windows_update": _revert_disable_windows_update,
    "clear_temp_files": _revert_clear_temp_files,
    "disable_notifications": _revert_disable_notifications,
    "set_process_priority_class": _revert_set_process_priority_class,
    "disable_background_apps_global": _revert_disable_background_apps_global,
    "disable_cortana": _revert_disable_cortana,
}

assert len(APPLY_FUNCS) == 37, f"Expected 37 apply funcs, got {len(APPLY_FUNCS)}"
assert len(REVERT_FUNCS) == 37, f"Expected 37 revert funcs, got {len(REVERT_FUNCS)}"
assert set(APPLY_FUNCS.keys()) == set(REVERT_FUNCS.keys()), "Apply/Revert key sets differ"


# Public API 

def apply_optimizations(
    toggles: List[OptimizationToggle],
    profile: OptimizationProfile,
    report: HardwareReport,
    progress_callback=None,
) -> List[ApplyResult]:
   
    results: List[ApplyResult] = []
    for t in toggles:
        if not t.enabled:
            continue
        func = APPLY_FUNCS.get(t.id)
        if func is None:
            results.append(ApplyResult(
                toggle_id=t.id, success=False,
                message="Неизвестная оптимизация", action="applied",
            ))
            continue
        try:
            result = func(t, profile, report)
        except Exception as e:
            result = ApplyResult(
                toggle_id=t.id, success=False,
                message=f"Исключение: {e}", action="applied",
            )

        if not isinstance(result, ApplyResult):
            result = ApplyResult(
                toggle_id=t.id, success=False,
                message="Внутренняя ошибка: неверный тип результата",
                action="applied",
            )
        results.append(result)

        if progress_callback is not None:
            try:
                progress_callback(result)
            except Exception:
                pass  
    return results


def revert_optimizations(
    toggles: List[OptimizationToggle],
    progress_callback=None,
) -> List[ApplyResult]:

    global _cleanup_stop_event, _cleanup_thread
    _cleanup_stop_event.set()
    if _cleanup_thread and _cleanup_thread.is_alive():
        _cleanup_thread.join(timeout=2)
    _cleanup_thread = None

    _cleanup_stop_event = threading.Event()

    results: List[ApplyResult] = []
    for t in toggles:
        if not t.enabled:
            continue
        func = REVERT_FUNCS.get(t.id)
        if func is None:
            results.append(ApplyResult(
                toggle_id=t.id, success=False,
                message="Неизвестная оптимизация", action="reverted",
            ))
            continue
        try:
            result = func(t)
        except Exception as e:
            result = ApplyResult(
                toggle_id=t.id, success=False,
                message=f"Исключение: {e}", action="reverted",
            )
        if not isinstance(result, ApplyResult):
            result = ApplyResult(
                toggle_id=t.id, success=False,
                message="Внутренняя ошибка: неверный тип результата",
                action="reverted",
            )
        results.append(result)

        if progress_callback is not None:
            try:
                progress_callback(result)
            except Exception:
                pass  
    return results
