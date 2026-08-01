from __future__ import annotations

import logging
import os
import platform
import time
from typing import Any, Callable, Dict, List, Optional

import psutil

from .models import ProcessAnalysis, ProcessInfo, ApplyResult
from .optimizations import BACKGROUND_APPS

__all__ = [
    "analyze_processes",
    "optimize_processes",
    "get_process_category",
    "get_process_description",
    "is_process_safe_to_optimize",
    "kill_background_apps",
    "PROCESS_CATEGORIES",
    "PROCESS_DESCRIPTIONS",
    "SYSTEM_PROCESSES",
    "GAME_LAUNCHERS",
    "DEFAULT_GAME_NAMES",
    "PRIORITY_MAP",
]

logger = logging.getLogger(__name__)

_is_windows = platform.system().lower() == "windows"


# --------------------------------------------------------------------------- #
# Priority-class mapping
# --------------------------------------------------------------------------- #
# On Windows, Process.nice() returns / accepts a psutil priority-class constant
# (REALTIME_PRIORITY_CLASS etc.). On POSIX it returns an integer nice value
# (-20..19). We build the Windows map defensively via getattr() so the module
# imports cleanly on Linux.

_PRIORITY_LABELS = [
    ("IDLE_PRIORITY_CLASS", "low"),
    ("BELOW_NORMAL_PRIORITY_CLASS", "below_normal"),
    ("NORMAL_PRIORITY_CLASS", "normal"),
    ("ABOVE_NORMAL_PRIORITY_CLASS", "above_normal"),
    ("HIGH_PRIORITY_CLASS", "high"),
    ("REALTIME_PRIORITY_CLASS", "realtime"),
]

PRIORITY_MAP: Dict[int, str] = {}
PRIORITY_CONSTANTS: Dict[str, int] = {}
for _const_name, _label in _PRIORITY_LABELS:
    if hasattr(psutil, _const_name):
        _val = getattr(psutil, _const_name)
        PRIORITY_MAP[_val] = _label
        PRIORITY_CONSTANTS[_label] = _val


# --------------------------------------------------------------------------- #
# Process knowledge base
# --------------------------------------------------------------------------- #

PROCESS_CATEGORIES: Dict[str, str] = {
    # Browsers
    "chrome.exe": "browser",
    "msedge.exe": "browser",
    "firefox.exe": "browser",
    "opera.exe": "browser",
    "brave.exe": "browser",
    "vivaldi.exe": "browser",
    # Communication
    "discord.exe": "communication",
    "slack.exe": "communication",
    "teams.exe": "communication",
    "telegram.exe": "communication",
    "skype.exe": "communication",
    "zoom.exe": "communication",
    "viber.exe": "communication",
    "whatsapp.exe": "communication",
    # Cloud sync
    "onedrive.exe": "cloud",
    "dropbox.exe": "cloud",
    "googledrive.exe": "cloud",
    "icloudservices.exe": "cloud",
    "megasync.exe": "cloud",
    # Media players
    "spotify.exe": "media",
    "vlc.exe": "media",
    "potplayermini64.exe": "media",
    "foobar2000.exe": "media",
    "aimp.exe": "media",
    "itunes.exe": "media",
    # Game launchers (classified as "media" so they show up as optimizable
    # background, but GAME_LAUNCHERS below overrides the recommended_action
    # to "lower_priority" instead of "kill").
    "steam.exe": "media",
    "epicgameslauncher.exe": "media",
    "battlenet.exe": "media",
    "uplay.exe": "media",
    "origin.exe": "media",
    "gog galaxy.exe": "media",
}

PROCESS_DESCRIPTIONS: Dict[str, str] = {
    # Browsers
    "chrome.exe": "Google Chrome — браузер, потребляет много RAM",
    "msedge.exe": "Microsoft Edge — браузер",
    "firefox.exe": "Mozilla Firefox — браузер",
    "opera.exe": "Opera — браузер",
    "brave.exe": "Brave — браузер",
    "vivaldi.exe": "Vivaldi — браузер",
    # Communication
    "discord.exe": "Discord — коммуникация, HWA нагружает GPU",
    "slack.exe": "Slack — корпоративная коммуникация",
    "teams.exe": "Microsoft Teams — коммуникация",
    "telegram.exe": "Telegram — мессенджер",
    "skype.exe": "Skype — видеозвонки",
    "zoom.exe": "Zoom — видеоконференции",
    "viber.exe": "Viber — мессенджер",
    "whatsapp.exe": "WhatsApp — мессенджер",
    # Cloud
    "onedrive.exe": "Microsoft OneDrive — облачная синхронизация",
    "dropbox.exe": "Dropbox — облачная синхронизация",
    "googledrive.exe": "Google Drive — облачная синхронизация",
    "icloudservices.exe": "iCloud — облачная синхронизация Apple",
    "megasync.exe": "MEGAsync — облачная синхронизация",
    # Media
    "spotify.exe": "Spotify — музыкальный плеер",
    "vlc.exe": "VLC media player — видеоплеер",
    "potplayermini64.exe": "PotPlayer — видеоплеер",
    "foobar2000.exe": "foobar2000 — аудиоплеер",
    "aimp.exe": "AIMP — аудиоплеер",
    "itunes.exe": "iTunes — медиаплеер Apple",
    # Game launchers
    "steam.exe": "Steam — игровой лаунчер",
    "epicgameslauncher.exe": "Epic Games Launcher — игровой лаунчер",
    "battlenet.exe": "Battle.net — игровой лаунчер Blizzard",
    "uplay.exe": "Ubisoft Connect — игровой лаунчер",
    "origin.exe": "EA Origin — игровой лаунчер",
    "gog galaxy.exe": "GOG Galaxy — игровой лаунчер",
}

SYSTEM_PROCESSES: set = {
    # Windows core (never kill/suspend)
    "system",
    "system idle process",
    "idle",
    "registry",
    "smss.exe",
    "csrss.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "wininit.exe",
    "fontdrvhost.exe",
    "dwm.exe",  # Desktop Window Manager — kills the desktop
    "explorer.exe",  # Windows shell — taskbar
    "spoolsv.exe",
    "spooler.exe",  # print spooler
    "conhost.exe",  # console host
    "taskhostw.exe",
    "taskhost.exe",
    "sihost.exe",  # shell infrastructure host
    "ctfmon.exe",  # text input
    "runtimebroker.exe",  # UWP broker
    "searchui.exe",
    "searchhost.exe",  # search
    "startmenuexperiencehost.exe",  # start menu
    "shellexperiencehost.exe",
    "applicationframehost.exe",
    "windowsinternal.composableshell.experiences.textinput.inputapp.exe",
    # Antivirus
    "msmpeng.exe",
    "msmpwc.exe",  # Windows Defender
    "avp.exe",
    "kavfs.exe",  # Kaspersky
    "avguard.exe",  # Avira
    "avgsvc.exe",  # AVG
    "avshadow.exe",  # Avast
    "mcshield.exe",
    "mcagent.exe",  # McAfee
    "bdagent.exe",  # Bitdefender
    # GPU drivers
    "nvcontainer.exe",
    "nvidia share.exe",
    "nvidia backend.exe",
    "atiesrxx.exe",
    "amdfendrsr.exe",
    # Linux/macOS critical daemons (so the module also runs safely there)
    "init",
    "systemd",
    "systemd-journald",
    "systemd-logind",
    "systemd-udevd",
    "systemd-resolved",
    "dbus-daemon",
    "dbus",
    "kthreadd",
    "ksoftirqd",
    "launchd",
    "finder",
    "dock",
    "windowserver",
    "sshd",
    "cron",
    "getty",
    "login",
}

# Substring keywords used as a fallback AV filter (in case the AV process
# name isn't a perfect match in SYSTEM_PROCESSES).
AV_KEYWORDS = (
    "msmpeng",
    "msmpwc",
    "avp",
    "kav",
    "avg",
    "avira",
    "avast",
    "avshadow",
    "mcafee",
    "mcshield",
    "mcagent",
    "bitdef",
    "bdagent",
    "norton",
    "symantec",
    "eset",
    "nod32",
    "drweb",
)

GAME_LAUNCHERS = {
    "steam.exe",
    "epicgameslauncher.exe",
    "battlenet.exe",
    "uplay.exe",
    "origin.exe",
    "gog galaxy.exe",
}

# Default game names used when callers don't pass an explicit list.
DEFAULT_GAME_NAMES = ["Stalcraft.exe", "Stalcraftw.exe", "Stalzone.exe", "Stalzonew.exe"]


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #

def get_process_category(name: str) -> str:
    if not name:
        return "other"
    key = name.lower().strip()
    if key in SYSTEM_PROCESSES:
        return "system"
    return PROCESS_CATEGORIES.get(key, "other")


def get_process_description(name: str) -> str:
    if not name:
        return ""
    key = name.lower().strip()
    return PROCESS_DESCRIPTIONS.get(key, name)


def is_process_safe_to_optimize(
    proc_info: ProcessInfo,
    game_names: List[str] = None,
) -> bool:
    
    if proc_info is None or not proc_info.name:
        return False

    name_lower = proc_info.name.lower().strip()

    # System-critical — never touch.
    if name_lower in SYSTEM_PROCESSES:
        return False
    if proc_info.category == "system":
        return False

    # Optimizer's own process (avoid self-suicide).
    try:
        own_pid = os.getpid()
        if proc_info.pid == own_pid:
            return False
    except Exception:
        pass

    # Game process itself — must never be touched.
    games = [g.lower().strip() for g in (game_names or DEFAULT_GAME_NAMES)]
    if name_lower in games:
        return False
    for g in games:
        base = g.replace(".exe", "")
        if base and (name_lower == base or name_lower.startswith(base)):
            return False

    # Antivirus substring match (Defender/Kaspersky/Avira/Avast/McAfee/...).
    if any(kw in name_lower for kw in AV_KEYWORDS):
        return False

    return True


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _priority_label(proc: psutil.Process) -> str:
    try:
        nice = proc.nice()
    except psutil.NoSuchProcess:
        return "unknown"
    except psutil.AccessDenied:
        return "unknown"
    except Exception:
        return "unknown"

    if _is_windows and PRIORITY_MAP:
        # On Windows proc.nice() returns a priority-class constant.
        return PRIORITY_MAP.get(nice, "normal")

    # POSIX: nice value from -20 (highest) to 19 (lowest).
    try:
        n = int(nice)
    except (TypeError, ValueError):
        return "normal"
    if n <= -10:
        return "realtime"
    if n < 0:
        return "high"
    if n == 0:
        return "normal"
    if n <= 5:
        return "below_normal"
    return "low"


def _is_game_name(name_lower: str, games_lower: List[str]) -> bool:
    if not name_lower:
        return False
    if name_lower in games_lower:
        return True
    for g in games_lower:
        base = g.replace(".exe", "")
        if base and (name_lower == base or name_lower.startswith(base)):
            return True
    return False


def _recommended_action(
    name: str,
    category: str,
    is_safe: bool,
    cpu_percent: float,
    memory_mb: float,
    game_names: List[str],
) -> str:

    if not is_safe:
        return "keep"

    name_lower = name.lower().strip()
    games_lower = [g.lower().strip() for g in game_names]

    if _is_game_name(name_lower, games_lower):
        return "keep"

    if name_lower in GAME_LAUNCHERS:
        return "lower_priority"

    if category == "browser":
        # Browsers often have unsaved user state; don't kill, just demote.
        return "lower_priority"

    if category in ("communication", "cloud", "media"):
        return "kill"

    # Unknown — be conservative.
    return "lower_priority"


# --------------------------------------------------------------------------- #
# analyze_processes
# --------------------------------------------------------------------------- #

def analyze_processes(game_names: List[str] = None) -> ProcessAnalysis:
    
    games = list(game_names) if game_names else list(DEFAULT_GAME_NAMES)
    games_lower = [g.lower().strip() for g in games]

    # -- Pass 1: prime CPU counters ---------------------------------------- #
    try:
        for proc in psutil.process_iter(["pid"]):
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
    except Exception:
        # Even iterating can fail in some sandboxes — keep going.
        pass

    # Brief sleep so the next cpu_percent() call returns a meaningful delta.
    time.sleep(0.5)

    # -- Pass 2: collect actual values ------------------------------------ #
    process_infos: List[ProcessInfo] = []
    total_cpu = 0.0
    total_mem = 0.0
    total_count = 0

    try:
        iterator = psutil.process_iter(["pid", "name"])
    except Exception:
        iterator = []

    for proc in iterator:
        try:
            pid = proc.info.get("pid")
            name = proc.info.get("name") or ""
            if pid is None:
                continue
            total_count += 1

            # CPU percent — second call returns the delta since pass 1.
            try:
                cpu_pct = float(proc.cpu_percent(interval=None))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cpu_pct = 0.0
            except Exception:
                cpu_pct = 0.0

            # Memory (RSS, MB).
            try:
                mem_info = proc.memory_info()
                mem_mb = float(mem_info.rss) / (1024.0 * 1024.0) if mem_info else 0.0
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                mem_mb = 0.0
            except Exception:
                mem_mb = 0.0

            # Thread count.
            try:
                n_threads = int(proc.num_threads())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                n_threads = 0
            except Exception:
                n_threads = 0

            # Priority label.
            priority = _priority_label(proc)

            name_lower = name.lower().strip()
            if _is_game_name(name_lower, games_lower):
                category = "game"
            else:
                category = get_process_category(name)

            # Known resource-hungry background app?
            bg_apps_lower = [a.lower() for a in BACKGROUND_APPS]
            is_background = (
                name_lower in bg_apps_lower
                or category in ("browser", "communication", "cloud", "media")
            )

            # Build ProcessInfo with a placeholder recommended_action so
            # is_process_safe_to_optimize() can use it.
            info = ProcessInfo(
                pid=pid,
                name=name,
                cpu_percent=round(cpu_pct, 1),
                memory_mb=round(mem_mb, 1),
                threads=n_threads,
                priority=priority,
                is_background=is_background,
                category=category,
                recommended_action="keep",
                description=get_process_description(name),
            )
            safe = is_process_safe_to_optimize(info, games)
            info.recommended_action = _recommended_action(
                name, category, safe, cpu_pct, mem_mb, games,
            )

            process_infos.append(info)
            total_cpu += cpu_pct
            total_mem += mem_mb
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue

    # -- Rank: top 20 by combined CPU% + memory/50 score ------------------ #
    heavy = sorted(
        process_infos,
        key=lambda p: (p.cpu_percent + p.memory_mb / 50.0),
        reverse=True,
    )[:20]

    optimizable = [
        p for p in heavy
        if p.recommended_action in ("kill", "lower_priority", "suspend")
    ]

    return ProcessAnalysis(
        total_processes=total_count,
        total_cpu_usage=round(total_cpu, 1),
        total_memory_mb=round(total_mem, 1),
        heavy_processes=heavy,
        optimizable_processes=optimizable,
        killed_count=0,
        optimized_count=0,
    )


# --------------------------------------------------------------------------- #
# optimize_processes
# --------------------------------------------------------------------------- #

def optimize_processes(
    actions: List[Dict[str, Any]],
    progress_callback: Optional[Callable[[ApplyResult], None]] = None,
) -> List[ApplyResult]:

    results: List[ApplyResult] = []
    if not actions:
        return results

    for action in actions:
        result = _apply_single_action(action)
        results.append(result)
        if progress_callback is not None:
            try:
                progress_callback(result)
            except Exception as exc:
                logger.warning("progress_callback raised: %s", exc)

    return results


def _apply_single_action(action: Dict[str, Any]) -> ApplyResult:
   
    if not isinstance(action, dict):
        return ApplyResult(
            toggle_id="process_unknown",
            success=False,
            message="Некорректное действие (ожидается dict)",
            action="applied",
        )

    pid = action.get("pid")
    act = str(action.get("action") or "keep").lower().strip()

    if pid is None:
        return ApplyResult(
            toggle_id="process_unknown",
            success=False,
            message="Ошибка: PID не указан",
            action="applied",
        )

    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return ApplyResult(
            toggle_id=f"process_{pid}",
            success=False,
            message=f"Некорректный PID: {pid!r}",
            action="applied",
        )

    toggle_id = f"process_{pid_int}"

    # Resolve the Process object.
    try:
        proc = psutil.Process(pid_int)
    except psutil.NoSuchProcess:
        return ApplyResult(
            toggle_id=toggle_id,
            success=False,
            message=f"Процесс не найден: PID {pid_int}",
            action="applied",
        )
    except psutil.AccessDenied:
        return ApplyResult(
            toggle_id=toggle_id,
            success=False,
            message=f"Доступ запрещён: PID {pid_int}",
            action="applied",
        )
    except Exception as exc:
        return ApplyResult(
            toggle_id=toggle_id,
            success=False,
            message=f"Ошибка: {exc}",
            action="applied",
        )

    # Friendly process name for messages.
    try:
        pname = proc.name()
    except Exception:
        pname = f"PID {pid_int}"

    # ---- "keep": no-op -------------------------------------------------- #
    if act == "keep":
        logger.info("Keeping process %s (PID %s)", pname, pid_int)
        return ApplyResult(
            toggle_id=toggle_id,
            success=True,
            message=f"Без изменений: {pname} (PID {pid_int})",
            action="applied",
        )

    # ---- "kill": graceful then forceful --------------------------------- #
    if act == "kill":
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except psutil.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except psutil.TimeoutExpired:
                    pass
                except psutil.NoSuchProcess:
                    pass
            return ApplyResult(
                toggle_id=toggle_id,
                success=True,
                message=f"Завершён: {pname} (PID {pid_int})",
                action="applied",
            )
        except psutil.NoSuchProcess:
            return ApplyResult(
                toggle_id=toggle_id,
                success=True,
                message=f"Уже завершён: {pname} (PID {pid_int})",
                action="applied",
            )
        except psutil.AccessDenied:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=f"Доступ запрещён: {pname} (PID {pid_int})",
                action="applied",
            )
        except Exception as exc:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=f"Ошибка завершения {pname} (PID {pid_int}): {exc}",
                action="applied",
            )

    # ---- "lower_priority" ----------------------------------------------- #
    if act == "lower_priority":
        try:
            if _is_windows:
                below_normal = PRIORITY_CONSTANTS.get("below_normal")
                if below_normal is None:
                    return ApplyResult(
                        toggle_id=toggle_id,
                        success=False,
                        message=(
                            f"Константа приоритета недоступна: "
                            f"{pname} (PID {pid_int})"
                        ),
                        action="applied",
                    )
                proc.nice(below_normal)
                msg = f"Приоритет понижен: {pname} (PID {pid_int})"
            else:
                # POSIX: raise nice value (lower scheduling priority).
                try:
                    current = int(proc.nice())
                except Exception:
                    current = 0
                new_nice = min(19, current + 5)
                proc.nice(new_nice)
                msg = (
                    f"Приоритет понижен (nice={new_nice}): "
                    f"{pname} (PID {pid_int})"
                )
            return ApplyResult(
                toggle_id=toggle_id,
                success=True,
                message=msg,
                action="applied",
            )
        except psutil.NoSuchProcess:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=f"Процесс не найден: {pname} (PID {pid_int})",
                action="applied",
            )
        except psutil.AccessDenied:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=(
                    f"Доступ запрещён (нужны права администратора): "
                    f"{pname} (PID {pid_int})"
                ),
                action="applied",
            )
        except Exception as exc:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=(
                    f"Ошибка изменения приоритета {pname} "
                    f"(PID {pid_int}): {exc}"
                ),
                action="applied",
            )

    # ---- "suspend" ------------------------------------------------------ #
    if act == "suspend":
        try:
            proc.suspend()
            return ApplyResult(
                toggle_id=toggle_id,
                success=True,
                message=f"Приостановлен: {pname} (PID {pid_int})",
                action="applied",
            )
        except psutil.NoSuchProcess:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=f"Процесс не найден: {pname} (PID {pid_int})",
                action="applied",
            )
        except psutil.AccessDenied:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=f"Доступ запрещён: {pname} (PID {pid_int})",
                action="applied",
            )
        except Exception as exc:
            return ApplyResult(
                toggle_id=toggle_id,
                success=False,
                message=f"Ошибка приостановки {pname} (PID {pid_int}): {exc}",
                action="applied",
            )

    # Unknown action.
    return ApplyResult(
        toggle_id=toggle_id,
        success=False,
        message=f"Неизвестное действие: {act} (PID {pid_int})",
        action="applied",
    )


# --------------------------------------------------------------------------- #
# kill_background_apps
# --------------------------------------------------------------------------- #

def kill_background_apps(
    progress_callback: Optional[Callable[[ApplyResult], None]] = None,
) -> List[ApplyResult]:
   
    results: List[ApplyResult] = []

    # Map lowercased app name -> original target name (for matching).
    target_lower: Dict[str, str] = {a.lower(): a for a in BACKGROUND_APPS}

    # Collect matching PIDs per app (case-insensitive, suffix match).
    matches: Dict[str, List[int]] = {a: [] for a in BACKGROUND_APPS}
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name") or ""
                pid = proc.info.get("pid")
                if pid is None or not name:
                    continue
                name_lower = name.lower()
                for tl, original in target_lower.items():
                    if name_lower == tl or name_lower.endswith(tl):
                        matches[original].append(pid)
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
    except Exception:
        pass

    # Kill all matching PIDs per app, then emit one summary ApplyResult.
    for app_name in BACKGROUND_APPS:
        pids = matches[app_name]
        # Stable toggle_id derived from the app name.
        safe_name = app_name.lower().replace(".", "_").replace(" ", "_")
        toggle_id = f"process_bg_{safe_name}"

        if not pids:
            result = ApplyResult(
                toggle_id=toggle_id,
                success=True,
                message=f"Не запущен: {app_name}",
                action="applied",
            )
        else:
            killed = 0
            first_error = ""
            for pid in pids:
                sub = _apply_single_action({"pid": pid, "action": "kill"})
                if sub.success:
                    killed += 1
                elif not first_error:
                    first_error = sub.message

            pid_str = ", ".join(str(p) for p in pids)
            if killed == len(pids):
                msg = f"Завершён: {app_name} (PID {pid_str})"
                success = True
            elif killed > 0:
                msg = (
                    f"Частично завершён: {app_name} "
                    f"({killed}/{len(pids)}, PID {pid_str})"
                )
                success = True
            else:
                msg = (
                    f"Не удалось завершить: {app_name} "
                    f"({first_error or 'ошибка'})"
                )
                success = False

            result = ApplyResult(
                toggle_id=toggle_id,
                success=success,
                message=msg,
                action="applied",
            )

        results.append(result)
        if progress_callback is not None:
            try:
                progress_callback(result)
            except Exception as exc:
                logger.warning("progress_callback raised: %s", exc)

    return results
