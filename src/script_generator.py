"""
StalZone Optimizer — Script generator module.

Generates a standalone Python optimization script as a string. The generated
script applies all enabled optimizations from the profile, waits for the game
to exit, then runs cleanup to restore the original system state.

Ported from /home/z/my-project/src/lib/optimizer/script-generator.ts (942 lines).
Public API: ``generate_script(profile, report, tier) -> str``.
"""

import json
import textwrap
from datetime import datetime
from typing import List

from .models import OptimizationProfile, HardwareReport, TierAssessment


# =============================================================================
# Toggle ordering — all 27 IDs, grouped by category.
# =============================================================================

_OPTIMIZATION_ORDER: List[str] = [
    # POWER (3)
    "power_plan", "timer_resolution", "core_parking_off",
    # CPU (3)
    "cpu_affinity_physical", "process_priority_high", "disable_game_dvr",
    # MEMORY (4)
    "ram_standby_cleanup", "ram_periodic_cleanup", "disable_swap_file",
    "large_system_cache",
    # GPU (3)
    "gpu_power_management", "disable_hardware_acceleration", "tdr_delay_increase",
    # NETWORK (3)
    "disable_nagle", "flush_dns", "network_throttling_off",
    # DISK (3)
    "disable_indexing", "defrag_hdd", "ssd_trim",
    # SERVICES (3)
    "disable_sysmain", "disable_diagnostic_tracking", "disable_windows_search",
    # VISUAL (2)
    "visual_effects_performance", "disable_transparency",
    # GAME (3)
    "kill_background_apps", "game_mode_on", "hardware_gpu_scheduler",
]

def _lines(source: str) -> List[str]:
    """Convert a triple-quoted source block to a list of lines.

    Dedents common indentation, strips leading/trailing blank lines,
    and appends a single trailing blank line for spacing.
    """
    return textwrap.dedent(source).strip("\n").split("\n") + [""]


def _py_str(s) -> str:
    """Render a value as a Python string literal (double-quoted, JSON-safe)."""
    return json.dumps(str(s))


def _py_str_list(arr) -> str:
    """Render a list of strings as a Python list literal."""
    return "[" + ", ".join(json.dumps(str(s)) for s in arr) + "]"


def _py_num_list(arr) -> str:
    """Render a list of numbers as a Python list literal."""
    return "[" + ", ".join(str(n) for n in arr) + "]"


def _py_bool(b) -> str:
    """Render a boolean as Python True/False."""
    return "True" if b else "False"


# =============================================================================
# Helpers source — always included in the generated script.
# Defines runtime state, log(), is_admin(), run(), set_reg()/restore_reg(),
# stop_service()/restore_service(), find_game_process(), compute_affinity(),
# PRIORITY_MAP, trim_working_sets(), PeriodicCleanup class,
# set_timer_resolution(), get_drive_letters_by_media_type(), parse_args().
# =============================================================================

_HELPERS_SOURCE = r'''
# --- SECTION: Runtime state ---
DRY_RUN = False
RESTORE_STATES = {}     # service_name -> start_mode
REG_BACKUP = {}         # (key, value_name) -> (type, value)
STOP_EVENT = threading.Event()
TIMER_WAS_SET = False
CLEANUP_THREAD = None


# --- SECTION: Logging ---
def log(msg):
    """Print message with timestamp prefix [HH:MM:SS]."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# --- SECTION: Admin Check ---
def is_admin():
    """Return True if running with admin/root rights."""
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


# --- SECTION: Subprocess helper ---
def run(cmd):
    """Run a command (list or str), return (success, output)."""
    try:
        if isinstance(cmd, str):
            r = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=60)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        return (r.returncode == 0, out.strip())
    except Exception as e:
        return (False, str(e))


# --- SECTION: Registry helper ---
def set_reg(key_path, value_name, value, value_type, backup=True):
    """Set a registry value via reg.exe; optionally backup previous value."""
    if DRY_RUN:
        log(f"  [DRY] reg add {key_path}\\{value_name} = {value} ({value_type})")
        return
    if backup and (key_path, value_name) not in REG_BACKUP:
        try:
            r = subprocess.run(["reg", "query", key_path, "/v", value_name],
                               capture_output=True, text=True)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if value_name in line and "REG_" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            REG_BACKUP[(key_path, value_name)] = (parts[-2], parts[-1])
                        break
        except Exception:
            pass
    try:
        cmd = ["reg", "add", key_path, "/v", value_name, "/t", value_type,
               "/d", str(value), "/f"]
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        log(f"  reg add FAIL: {key_path}\\{value_name} - {err}")
    except Exception as e:
        log(f"  reg add FAIL: {key_path}\\{value_name} - {e}")


def restore_reg(key_path, value_name):
    """Restore a backed-up registry value."""
    key = (key_path, value_name)
    if key not in REG_BACKUP:
        try:
            subprocess.run(["reg", "delete", key_path, "/v", value_name, "/f"],
                           capture_output=True)
        except Exception:
            pass
        return
    vt, vv = REG_BACKUP[key]
    try:
        subprocess.run(["reg", "add", key_path, "/v", value_name,
                        "/t", vt, "/d", vv, "/f"], capture_output=True)
        log(f"  Restored reg: {key_path}\\{value_name} = {vv}")
    except Exception as e:
        log(f"  restore_reg FAIL: {e}")


# --- SECTION: Service helper ---
def stop_service(name, disable=True, restore_mode=None):
    """Stop and optionally disable a Windows service."""
    if DRY_RUN:
        log(f"  [DRY] sc stop {name}; sc config {name} start= disabled")
        if restore_mode:
            RESTORE_STATES[name] = restore_mode
        return
    try:
        subprocess.run(["sc", "stop", name], capture_output=True)
        if disable:
            subprocess.run(["sc", "config", name, "start=", "disabled"],
                           capture_output=True)
        if restore_mode:
            RESTORE_STATES[name] = restore_mode
        log(f"  Service stopped: {name}")
    except Exception as e:
        log(f"  stop_service FAIL: {name}: {e}")


def restore_service(name):
    """Restore a previously-stopped service to its original start mode."""
    mode = RESTORE_STATES.get(name, "auto")
    try:
        subprocess.run(["sc", "config", name, "start=", mode],
                       capture_output=True)
        log(f"  Service restored: {name} start={mode}")
    except Exception:
        pass


# --- SECTION: Process finder ---
def find_game_process(possible_names):
    """Find a game process by list of possible names.

    Returns a psutil.Process instance or None.
    """
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"] or ""
            if any(t.lower() in name.lower() for t in possible_names):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def compute_affinity():
    """Compute CPU affinity list based on CPU_AFFINITY_MODE."""
    if CPU_AFFINITY_MODE == "custom":
        if CUSTOM_AFFINITY_CORES:
            return list(CUSTOM_AFFINITY_CORES)
        log("  CPU_AFFINITY_MODE=custom but CUSTOM_AFFINITY_CORES empty - fallback to physical")
    if CPU_AFFINITY_MODE == "all":
        return list(range(psutil.cpu_count(logical=True) or 1))
    physical = psutil.cpu_count(logical=False) or 1
    return list(range(physical))


PRIORITY_MAP = {}
if sys.platform == "win32":
    try:
        PRIORITY_MAP["above_normal"] = psutil.ABOVE_NORMAL_PRIORITY_CLASS
        PRIORITY_MAP["high"] = psutil.HIGH_PRIORITY_CLASS
        PRIORITY_MAP["realtime"] = psutil.REALTIME_PRIORITY_CLASS
    except AttributeError:
        pass


# --- SECTION: Memory cleanup ---
def trim_working_sets():
    """Purge standby memory and/or trim working sets of all processes."""
    try:
        exe = "EmptyStandbyList.exe"
        script_dir = os.path.dirname(os.path.abspath(__file__))
        local = os.path.join(script_dir, exe)
        target = local if os.path.exists(local) else exe
        r = subprocess.run([target, "standbylist"], capture_output=True, timeout=15)
        if r.returncode == 0:
            log("  Standby purged via EmptyStandbyList.exe")
            return
    except FileNotFoundError:
        log("  EmptyStandbyList.exe not found - fallback to trim working sets")
    except Exception as e:
        log(f"  EmptyStandbyList error: {e} - fallback")
    try:
        psapi = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32
        PROCESS_SET_QUOTA = 0x0080
        PROCESS_QUERY_INFORMATION = 0x0400
        processed = 0
        for p in psutil.process_iter(["pid"]):
            try:
                pid = p.info["pid"]
                if pid is None or pid == 0:
                    continue
                h = kernel32.OpenProcess(
                    PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
                if h:
                    psapi.EmptyWorkingSet(h)
                    kernel32.CloseHandle(h)
                    processed += 1
            except Exception:
                continue
        log(f"  Trimmed working sets: {processed} processes")
    except Exception as e:
        log(f"  trim_working_sets FAIL: {e}")


class PeriodicCleanup(threading.Thread):
    """Background thread that periodically trims working sets."""

    def __init__(self, interval_sec):
        super().__init__(daemon=True)
        self.interval = interval_sec

    def run(self):
        log(f"  PeriodicCleanup thread started (interval={self.interval}s)")
        while not STOP_EVENT.wait(self.interval):
            try:
                trim_working_sets()
            except Exception as e:
                log(f"  Periodic cleanup error: {e}")


def set_timer_resolution():
    """Set Windows timer resolution via timeBeginPeriod (called early in main)."""
    global TIMER_WAS_SET
    if sys.platform != "win32":
        return
    try:
        res = ctypes.windll.winmm.timeBeginPeriod(int(TIMER_RESOLUTION_MS))
        if res == 0:
            TIMER_WAS_SET = True
            log(f"  Timer resolution: {TIMER_RESOLUTION_MS} ms")
        else:
            log(f"  timeBeginPeriod error code: {res}")
    except Exception as e:
        log(f"  set_timer_resolution FAIL: {e}")


# --- SECTION: Disk helpers ---
def get_drive_letters_by_media_type(media_type):
    """Return list of drive letters (C:) for given media type via PowerShell."""
    ps_cmd = (
        "Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object {"
        "  $d = Get-Disk -Number $_.DiskNumber;"
        "  $p = Get-PhysicalDisk | Where-Object { $_.DeviceId -eq $d.Number.ToString() };"
        '  if ($p.MediaType -eq "' + media_type + '") { ($_.DriveLetter.ToString() + ":") }'
        "}"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                           capture_output=True, text=True)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception as e:
        log(f"  get_drive_letters FAIL: {e}")
        return []


# --- SECTION: argparse ---
def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="StalZone Optimizer v2.0")
    parser.add_argument("--game", action="append",
                        help="Game process name (can repeat)")
    parser.add_argument("--no-admin-check", action="store_true",
                        help="Skip admin rights check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log only, do not change anything")
    parser.add_argument("--cleanup-only", action="store_true",
                        help="Restore settings and exit")
    return parser.parse_args()
'''


# =============================================================================
# Optimization templates — one entry per toggle ID (27 total).
# Each entry is a list of source lines defining ``_apply_<toggle_id>()``.
# Every template follows the same pattern:
#   1. log("Applying: <id>")
#   2. Check DRY_RUN -> early return
#   3. Check sys.platform == "win32" -> skip with warning
#   4. try/except wrapping the actual logic
# =============================================================================

_OPTIMIZATION_TEMPLATES = {

    # ---------- POWER ----------

    "power_plan": _lines(r'''
def _apply_power_plan():
    """Switch Windows power plan to Best Performance."""
    log("Applying: power_plan")
    if DRY_RUN:
        log("  [DRY] powercfg /setactive " + HIGH_PERF_GUID)
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        ok, msg = run(["powercfg", "/setactive", HIGH_PERF_GUID])
        if ok:
            log("  Power plan: Best Performance")
            return
        # Fallback: duplicate the scheme if GUID is not registered
        log("  HIGH_PERF_GUID not found, trying to duplicate scheme...")
        ok2, out = run(["powercfg", "-duplicatescheme", HIGH_PERF_GUID])
        new_guid = ""
        for line in out.splitlines():
            if "GUID:" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    tail = parts[1].strip()
                    if tail:
                        new_guid = tail.split()[0]
                break
        if new_guid:
            ok3, _ = run(["powercfg", "/setactive", new_guid])
            if ok3:
                log(f"  Power plan: duplicated GUID {new_guid}")
            else:
                log("  ERROR: could not activate duplicated scheme")
        else:
            log("  ERROR: could not duplicate power scheme")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "timer_resolution": _lines(r'''
def _apply_timer_resolution():
    """Set Windows timer resolution via timeBeginPeriod."""
    log("Applying: timer_resolution")
    if DRY_RUN:
        log(f"  [DRY] timeBeginPeriod({TIMER_RESOLUTION_MS})")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        global TIMER_WAS_SET
        res = ctypes.windll.winmm.timeBeginPeriod(int(TIMER_RESOLUTION_MS))
        if res == 0:
            TIMER_WAS_SET = True
            log(f"  Timer resolution: {TIMER_RESOLUTION_MS} ms")
        else:
            log(f"  ERROR: timeBeginPeriod returned {res}")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "core_parking_off": _lines(r'''
def _apply_core_parking_off():
    """Disable Core Parking via powercfg (CPMINCORES=100 AC+DC)."""
    log("Applying: core_parking_off")
    if DRY_RUN:
        log("  [DRY] powercfg CPMINCORES=100 (AC+DC)")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT",
             "SUB_PROCESSOR", "CPMINCORES", "100"])
        run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT",
             "SUB_PROCESSOR", "CPMINCORES", "100"])
        run(["powercfg", "/setactive", "SCHEME_CURRENT"])
        log("  Core Parking disabled (CPMINCORES=100).")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- CPU ----------

    "cpu_affinity_physical": _lines(r'''
def _apply_cpu_affinity_physical():
    """Pin the game process to the configured CPU affinity set."""
    log("Applying: cpu_affinity_physical")
    if DRY_RUN:
        log(f"  [DRY] cpu_affinity(mode={CPU_AFFINITY_MODE})")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        proc = find_game_process(GAME_PROCESS_NAMES)
        if not proc:
            log("  Game process not found yet - affinity will apply on detection")
            return
        affinity = compute_affinity()
        old = proc.cpu_affinity()
        proc.cpu_affinity(affinity)
        log(f"  Affinity: {list(old)} -> {affinity}")
    except psutil.AccessDenied:
        log("  AccessDenied - admin required")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "process_priority_high": _lines(r'''
def _apply_process_priority_high():
    """Raise the game process scheduler priority."""
    log("Applying: process_priority_high")
    if DRY_RUN:
        log(f"  [DRY] nice({PROCESS_PRIORITY})")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        proc = find_game_process(GAME_PROCESS_NAMES)
        if not proc:
            log("  Game process not found yet - priority will apply on detection")
            return
        prio = PRIORITY_MAP.get(PROCESS_PRIORITY)
        if prio is not None:
            proc.nice(prio)
            log(f"  Priority: {PROCESS_PRIORITY}")
        else:
            log("  Priority: unsupported on this platform")
    except psutil.AccessDenied:
        log("  AccessDenied - admin required")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "disable_game_dvr": _lines(r'''
def _apply_disable_game_dvr():
    """Disable Xbox Game DVR via registry."""
    log("Applying: disable_game_dvr")
    if DRY_RUN:
        log("  [DRY] GameDVR_Enabled=0, FTRenabled=0")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", 0, "REG_DWORD")
        set_reg(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\GameDVR",
                "FTRenabled", 0, "REG_DWORD")
        log("  Game DVR disabled.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- MEMORY ----------

    "ram_standby_cleanup": _lines(r'''
def _apply_ram_standby_cleanup():
    """Purge standby memory / trim working sets once."""
    log("Applying: ram_standby_cleanup")
    if DRY_RUN:
        log("  [DRY] trim_working_sets()")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        trim_working_sets()
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "ram_periodic_cleanup": _lines(r'''
def _apply_ram_periodic_cleanup():
    """Start the periodic RAM cleanup thread."""
    log("Applying: ram_periodic_cleanup")
    if DRY_RUN:
        log(f"  [DRY] PeriodicCleanup(interval={MEMORY_CLEANUP_INTERVAL_SEC}s)")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        global CLEANUP_THREAD
        if CLEANUP_THREAD is None or not CLEANUP_THREAD.is_alive():
            CLEANUP_THREAD = PeriodicCleanup(MEMORY_CLEANUP_INTERVAL_SEC)
            CLEANUP_THREAD.start()
            log(f"  Periodic cleanup thread started (interval={MEMORY_CLEANUP_INTERVAL_SEC}s)")
        else:
            log("  Periodic cleanup thread already running")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "disable_swap_file": _lines(r'''
def _apply_disable_swap_file():
    """Disable pagefile (ONLY if RAM >= 16GB and confirmation flag)."""
    log("Applying: disable_swap_file")
    if DRY_RUN:
        log("  [DRY] wmic AutomaticManagedPagefile=False; pagefile=0,0")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
        if total_gb < MIN_RAM_GB_FOR_SWAP_DISABLE:
            log(f"  SKIP: RAM={total_gb:.1f}GB < {MIN_RAM_GB_FOR_SWAP_DISABLE}GB. Disabling swap is unsafe.")
            return
        if not CONFIRM_DISABLE_SWAP:
            log("  SKIP: CONFIRM_DISABLE_SWAP=False. Set flag to apply.")
            return
        computer = os.environ.get("COMPUTERNAME", "")
        ok1, _ = run(["wmic", "computersystem", "where",
                      f'name="{computer}"', "set",
                      "AutomaticManagedPagefile=False"])
        ok2, _ = run(["wmic", "pagefilesetting", "where",
                      'name="C:\\pagefile.sys"', "set",
                      "InitialSize=0,MaximumSize=0"])
        if ok1 and ok2:
            log("  Swap file disabled (reboot required).")
        else:
            log("  ERROR: wmic failed to disable pagefile.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "large_system_cache": _lines(r'''
def _apply_large_system_cache():
    """Enable LargeSystemCache=1 (useful for HDD systems)."""
    log("Applying: large_system_cache")
    if DRY_RUN:
        log("  [DRY] LargeSystemCache=1")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
                "LargeSystemCache", 1, "REG_DWORD")
        log("  LargeSystemCache=1 (reboot required for effect).")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- GPU ----------

    "gpu_power_management": _lines(r'''
def _apply_gpu_power_management():
    """Set GPUPREFERENCEPOWERMODE=2 (Prefer Maximum Performance)."""
    log("Applying: gpu_power_management")
    if DRY_RUN:
        log("  [DRY] GPUPREFERENCEPOWERMODE=2")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT",
             "SUB_VIDEO", "GPUPREFERENCEPOWERMODE", "2"])
        run(["powercfg", "/setactive", "SCHEME_CURRENT"])
        log("  GPU power: Prefer Maximum Performance.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "disable_hardware_acceleration": _lines(r'''
def _apply_disable_hardware_acceleration():
    """Terminate browsers and Discord (reduces GPU contention)."""
    log("Applying: disable_hardware_acceleration")
    if DRY_RUN:
        log(f"  [DRY] terminate {HWA_APP_NAMES}")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        killed = 0
        targets = [n.lower() for n in HWA_APP_NAMES]
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info["name"] or ""
                if name.lower() in targets:
                    proc.terminate()
                    killed += 1
                    log(f"  Terminated: {name} (PID {proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
        log(f"  HWA apps terminated: {killed}")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "tdr_delay_increase": _lines(r'''
def _apply_tdr_delay_increase():
    """Increase TdrDelay=10 and TdrLevel=1 (avoids false GPU resets)."""
    log("Applying: tdr_delay_increase")
    if DRY_RUN:
        log("  [DRY] TdrDelay=10, TdrLevel=1")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                "TdrDelay", 10, "REG_DWORD")
        set_reg(r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                "TdrLevel", 1, "REG_DWORD")
        log("  TdrDelay=10, TdrLevel=1.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- NETWORK ----------

    "disable_nagle": _lines(r'''
def _apply_disable_nagle():
    """Disable Nagle algorithm on all TCP adapters."""
    log("Applying: disable_nagle")
    if DRY_RUN:
        log("  [DRY] TcpAckFrequency=1, TCPNoDelay=1 on all adapters")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        base = r"HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
        ok, out = run(["reg", "query", base])
        if not ok:
            log(f"  ERROR: reg query failed: {out}")
            return
        subkeys = [ln.strip() for ln in out.splitlines()
                   if ln.strip().startswith("HKEY")]
        for sk in subkeys:
            set_reg(sk, "TcpAckFrequency", 1, "REG_DWORD", backup=False)
            set_reg(sk, "TCPNoDelay", 1, "REG_DWORD", backup=False)
        log(f"  Nagle disabled on {len(subkeys)} adapters.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "flush_dns": _lines(r'''
def _apply_flush_dns():
    """Flush DNS cache via ipconfig /flushdns."""
    log("Applying: flush_dns")
    if DRY_RUN:
        log("  [DRY] ipconfig /flushdns")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        ok, _ = run(["ipconfig", "/flushdns"])
        if ok:
            log("  DNS cache flushed.")
        else:
            log("  ERROR: ipconfig /flushdns failed.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "network_throttling_off": _lines(r'''
def _apply_network_throttling_off():
    """NetworkThrottlingIndex=ffffffff (remove multimedia network cap)."""
    log("Applying: network_throttling_off")
    if DRY_RUN:
        log("  [DRY] NetworkThrottlingIndex=ffffffff")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                "NetworkThrottlingIndex", 0xffffffff, "REG_DWORD")
        log("  NetworkThrottlingIndex=ffffffff.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- DISK ----------

    "disable_indexing": _lines(r'''
def _apply_disable_indexing():
    """Stop and disable Windows Search indexer (WSearch)."""
    log("Applying: disable_indexing")
    if DRY_RUN:
        log("  [DRY] sc stop WSearch; start= disabled (restore=demand)")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        stop_service("WSearch", disable=True, restore_mode="demand")
        log("  Indexing service stopped (WSearch).")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "defrag_hdd": _lines(r'''
def _apply_defrag_hdd():
    """Defragment HDD drives (SSDs/NVMe skipped)."""
    log("Applying: defrag_hdd")
    if DRY_RUN:
        log("  [DRY] defrag /H /U /V <HDD drives>")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        drives = get_drive_letters_by_media_type("HDD")
        if not drives:
            log("  No HDD drives detected - defrag skipped.")
            return
        for d in drives:
            log(f"  Defrag: {d} ...")
            ok, _ = run(["defrag", d, "/H", "/U", "/V"])
            if ok:
                log(f"  Defrag {d} done.")
            else:
                log(f"  Defrag {d} failed.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "ssd_trim": _lines(r'''
def _apply_ssd_trim():
    """Run TRIM (retrim) on SSD/NVMe drives via defrag /L."""
    log("Applying: ssd_trim")
    if DRY_RUN:
        log("  [DRY] defrag /L <SSD drives>")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        drives = get_drive_letters_by_media_type("SSD")
        if not drives:
            log("  No SSD/NVMe drives detected - TRIM skipped.")
            return
        for d in drives:
            log(f"  TRIM: {d} ...")
            ok, _ = run(["defrag", d, "/L"])
            if ok:
                log(f"  TRIM {d} done.")
            else:
                log(f"  TRIM {d} failed.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- SERVICES ----------

    "disable_sysmain": _lines(r'''
def _apply_disable_sysmain():
    """Stop and disable SysMain (Superfetch)."""
    log("Applying: disable_sysmain")
    if DRY_RUN:
        log("  [DRY] sc stop SysMain; start= disabled (restore=auto)")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        stop_service("SysMain", disable=True, restore_mode="auto")
        log("  SysMain stopped.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "disable_diagnostic_tracking": _lines(r'''
def _apply_disable_diagnostic_tracking():
    """Stop and disable DiagTrack (telemetry)."""
    log("Applying: disable_diagnostic_tracking")
    if DRY_RUN:
        log("  [DRY] sc stop DiagTrack; start= disabled (restore=auto)")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        stop_service("DiagTrack", disable=True, restore_mode="auto")
        log("  DiagTrack stopped.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "disable_windows_search": _lines(r'''
def _apply_disable_windows_search():
    """Stop Windows Search service (no disable - auto-restarts on reboot)."""
    log("Applying: disable_windows_search")
    if DRY_RUN:
        log("  [DRY] sc stop WSearch (no disable)")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        stop_service("WSearch", disable=False)
        log("  Windows Search stopped (will auto-restart on reboot).")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- VISUAL ----------

    "visual_effects_performance": _lines(r'''
def _apply_visual_effects_performance():
    """VisualFXSetting=2 (Best Performance)."""
    log("Applying: visual_effects_performance")
    if DRY_RUN:
        log("  [DRY] VisualFXSetting=2")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                "VisualFXSetting", 2, "REG_DWORD")
        log("  VisualFXSetting=2 (Best Performance).")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "disable_transparency": _lines(r'''
def _apply_disable_transparency():
    """EnableTransparency=0 (remove acrylic effects)."""
    log("Applying: disable_transparency")
    if DRY_RUN:
        log("  [DRY] EnableTransparency=0")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                "EnableTransparency", 0, "REG_DWORD")
        log("  Transparency disabled.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    # ---------- GAME ----------

    "kill_background_apps": _lines(r'''
def _apply_kill_background_apps():
    """Terminate known resource-heavy background apps."""
    log("Applying: kill_background_apps")
    if DRY_RUN:
        log(f"  [DRY] terminate {KILL_APP_NAMES}")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        killed = 0
        targets = [n.lower() for n in KILL_APP_NAMES]
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info["name"] or ""
                if name.lower() in targets:
                    proc.terminate()
                    killed += 1
                    log(f"  Terminated: {name}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                continue
        log(f"  Background apps terminated: {killed}")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "game_mode_on": _lines(r'''
def _apply_game_mode_on():
    """Enable Windows Game Mode (AutoGameModeEnabled=1)."""
    log("Applying: game_mode_on")
    if DRY_RUN:
        log("  [DRY] AutoGameModeEnabled=1")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKCU\Software\Microsoft\GameBar",
                "AutoGameModeEnabled", 1, "REG_DWORD")
        log("  Game Mode enabled.")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),

    "hardware_gpu_scheduler": _lines(r'''
def _apply_hardware_gpu_scheduler():
    """Enable HAGS (Hardware GPU Scheduling, HwSchMode=2)."""
    log("Applying: hardware_gpu_scheduler")
    if DRY_RUN:
        log("  [DRY] HwSchMode=2")
        return
    if sys.platform != "win32":
        log("  skipped (Windows only)")
        return
    try:
        set_reg(r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers",
                "HwSchMode", 2, "REG_DWORD")
        log("  HAGS enabled (reboot required).")
    except Exception as e:
        log(f"  EXCEPTION: {e}")
'''),
}


# =============================================================================
# Generator entry point
# =============================================================================

def generate_script(profile: OptimizationProfile,
                    report: HardwareReport,
                    tier: TierAssessment) -> str:
    """Generate a standalone Python optimization script as a string.

    The returned script is syntactically valid Python and can be saved to
    disk and executed independently (e.g. ``python stalzone_optimizer.py``).
    Only optimizations whose toggle ``enabled`` flag is True are included.
    """
    lines: List[str] = []

    # Build set of enabled toggle IDs in catalog order
    toggle_map = {t.id: t for t in profile.toggles}
    enabled_ids = [
        tid for tid in _OPTIMIZATION_ORDER
        if tid in toggle_map and toggle_map[tid].enabled
    ]
    enabled_set = set(enabled_ids)
    requires_admin_any = any(
        toggle_map[tid].requires_admin for tid in enabled_ids
    )

    # ---- Detected hardware summary strings ----
    cpu_brand = report.cpu.brand or "Unknown CPU"
    cpu_short = f"{report.cpu.cores_physical}C/{report.cpu.cores_logical}T @ {report.cpu.speed_ghz}GHz"
    discrete_gpus = [g for g in report.gpus if not g.is_integrated]
    gpu = discrete_gpus[0] if discrete_gpus else (report.gpus[0] if report.gpus else None)
    gpu_name = gpu.model if gpu else "Unknown GPU"
    ram_gb = report.ram.total_gb
    disk_summary = (
        ", ".join(f"{d.device}={d.type}" for d in report.disks)
        if report.disks else "Unknown"
    )
    build_ts = datetime.now().isoformat()

    # ===== SHEBANG + DOCSTRING =====
    lines.append("#!/usr/bin/env python3")
    lines.append("# -*- coding: utf-8 -*-")
    lines.append('"""')
    lines.append("StalZone Optimizer v2.0 — Improved Windows optimization toolkit")
    lines.append("Сгенерировано приложением StalZone Optimizer.")
    lines.append("")
    lines.append(f"Build timestamp: {build_ts}")
    lines.append(f"Tier: {tier.tier} ({tier.label}, score {tier.score}/100)")
    lines.append(f"Enabled optimizations ({len(enabled_ids)}):")
    for eid in enabled_ids:
        lines.append(f"  - {eid}")
    lines.append("")
    lines.append("Usage:")
    lines.append("    python stalzone_optimizer.py                       # default config")
    lines.append("    python stalzone_optimizer.py --game Stalcraft.exe  # override game name(s)")
    lines.append("    python stalzone_optimizer.py --dry-run             # print without changing")
    lines.append("    python stalzone_optimizer.py --cleanup-only        # restore settings and exit")
    lines.append("    python stalzone_optimizer.py --no-admin-check      # skip admin verification")
    lines.append("")
    lines.append("Run as Administrator for full effect.")
    lines.append('"""')
    lines.append("")

    # ===== IMPORTS =====
    lines.append("# --- SECTION: Imports ---")
    lines.append("import argparse")
    lines.append("import ctypes")
    lines.append("import json")
    lines.append("import os")
    lines.append("import subprocess")
    lines.append("import sys")
    lines.append("import threading")
    lines.append("import time")
    lines.append("from datetime import datetime")
    lines.append("")
    lines.append("try:")
    lines.append("    import psutil")
    lines.append("except ImportError:")
    lines.append("    print('ERROR: psutil not installed. Run:  pip install psutil')")
    lines.append("    sys.exit(1)")
    lines.append("")

    # ===== CONFIGURATION =====
    lines.append("# --- SECTION: Configuration ---")
    lines.append("# Power")
    lines.append(f"HIGH_PERF_GUID = {_py_str('fc936f94-8d9e-4d27-b579-28b6178adddf')}")
    lines.append("# Game")
    default_games = profile.game_process_names or [
        "Stalcraft.exe", "Stalcraftw.exe", "Stalzone.exe", "Stalzonew.exe"
    ]
    lines.append(f"GAME_PROCESS_NAMES = {_py_str_list(default_games)}")
    lines.append("# Timer / scheduling")
    lines.append(f"TIMER_RESOLUTION_MS = {profile.timer_resolution_ms}")
    lines.append(f"PROCESS_PRIORITY = {_py_str(profile.process_priority)}")
    lines.append(f"CPU_AFFINITY_MODE = {_py_str(profile.cpu_affinity_mode)}")
    lines.append(f"CUSTOM_AFFINITY_CORES = {_py_num_list(profile.custom_affinity_cores)}")
    lines.append("# Memory")
    lines.append(f"MEMORY_CLEANUP_INTERVAL_SEC = {profile.memory_cleanup_interval_sec}")
    lines.append(f"AGGRESSIVE_RAM_CLEANUP = {_py_bool(profile.aggressive_ram_cleanup)}")
    lines.append(f"RAM_TOTAL_GB_DETECTED = {ram_gb}")
    lines.append("MIN_RAM_GB_FOR_SWAP_DISABLE = 16")
    lines.append(
        f"CONFIRM_DISABLE_SWAP = {_py_bool(profile.aggressive_ram_cleanup and ram_gb >= 16)}"
    )
    lines.append("# Background apps to terminate (graceful)")
    lines.append(
        'KILL_APP_NAMES = ' + _py_str_list([
            "OneDrive.exe", "Skype.exe", "Spotify.exe",
            "EpicGamesLauncher.exe", "Dropbox.exe", "TeamViewer.exe",
            "AnyDesk.exe", "Zoom.exe", "Teams.exe", "Slack.exe",
        ])
    )
    lines.append("# Hardware-accelerated apps to terminate")
    lines.append(
        'HWA_APP_NAMES = ' + _py_str_list(["chrome.exe", "msedge.exe", "discord.exe"])
    )
    lines.append("")
    lines.append("# Detected hardware (baked into script at generation time)")
    lines.append(f"HW_CPU = {_py_str(f'{cpu_brand} ({cpu_short})')}")
    lines.append(f"HW_GPU = {_py_str(gpu_name)}")
    lines.append(f"HW_RAM_GB = {ram_gb}")
    lines.append(f"HW_DISKS = {_py_str(disk_summary)}")
    lines.append(f"HW_TIER = {_py_str(tier.tier)}")
    lines.append(f"HW_TIER_LABEL = {_py_str(tier.label)}")
    lines.append(f"HW_TIER_SCORE = {tier.score}")
    lines.append("")
    lines.append("# Enabled optimizations list (drives apply_all)")
    lines.append(f"ENABLED_OPTIMIZATIONS = {_py_str_list(enabled_ids)}")
    lines.append(f"ADMIN_REQUIRED = {_py_bool(requires_admin_any)}")
    lines.append("")

    # ===== HELPERS (always included) =====
    lines.extend(_lines(_HELPERS_SOURCE))

    # ===== OPTIMIZATION FUNCTIONS (only enabled toggles) =====
    for toggle_id in _OPTIMIZATION_ORDER:
        if toggle_id in enabled_set:
            template = _OPTIMIZATION_TEMPLATES.get(toggle_id)
            if template:
                lines.extend(template)

    # ===== APPLY ALL =====
    lines.append("# --- SECTION: Apply All Optimizations ---")
    lines.append("def apply_all():")
    lines.append('    """Apply all enabled optimizations in catalog order."""')
    lines.append('    log("=== Applying optimizations ===")')
    for toggle_id in _OPTIMIZATION_ORDER:
        if toggle_id in enabled_set:
            lines.append(f"    _apply_{toggle_id}()")
    lines.append('    log("=== All optimizations applied ===")')
    lines.append("")

    # ===== CLEANUP =====
    lines.append("# --- SECTION: Cleanup ---")
    lines.append("def cleanup():")
    lines.append('    """Restore settings: timer, services, registry."""')
    lines.append('    log("=== Cleanup: restoring settings ===")')
    lines.append("    try:")
    lines.append("        global TIMER_WAS_SET")
    lines.append("        # Restore timer resolution")
    lines.append("        if TIMER_WAS_SET:")
    lines.append("            try:")
    lines.append("                ctypes.windll.winmm.timeEndPeriod(int(TIMER_RESOLUTION_MS))")
    lines.append("                TIMER_WAS_SET = False")
    lines.append('                log("  Timer restored.")')
    lines.append("            except Exception:")
    lines.append("                pass")
    lines.append("        # Restore services (WSearch->demand, SysMain->auto, DiagTrack->auto)")
    lines.append("        for name in list(RESTORE_STATES):")
    lines.append("            restore_service(name)")
    lines.append("        # Restore registry values")
    lines.append("        for (kp, vn) in list(REG_BACKUP):")
    lines.append("            restore_reg(kp, vn)")
    lines.append("        # Stop periodic cleanup thread")
    lines.append("        STOP_EVENT.set()")
    lines.append('        log("Cleanup complete.")')
    lines.append("    except Exception as e:")
    lines.append('        log(f"Cleanup error: {e}")')
    lines.append("")

    # ===== MAIN =====
    lines.append("# --- SECTION: Main ---")
    lines.append("def main():")
    lines.append("    global DRY_RUN, GAME_PROCESS_NAMES")
    lines.append("    args = parse_args()")
    lines.append("    DRY_RUN = args.dry_run")
    lines.append("    if args.cleanup_only:")
    lines.append("        cleanup()")
    lines.append("        return")
    lines.append("    if args.game:")
    lines.append("        GAME_PROCESS_NAMES = args.game")
    lines.append("    if not args.no_admin_check and not is_admin():")
    lines.append('        print("WARNING: Run as Administrator for full effect.")')
    if "timer_resolution" in enabled_set:
        lines.append("    if not args.dry_run:")
        lines.append("        set_timer_resolution()")
    lines.append("    apply_all()")
    lines.append("    try:")
    lines.append("        # Wait for game process")
    lines.append('        log(f"Waiting for game process: {GAME_PROCESS_NAMES}")')
    lines.append("        game_proc = None")
    lines.append("        while not game_proc:")
    lines.append("            game_proc = find_game_process(GAME_PROCESS_NAMES)")
    lines.append("            if not game_proc:")
    lines.append("                if STOP_EVENT.wait(2):")
    lines.append("                    break")
    lines.append("        if game_proc:")
    lines.append('            log(f"Game detected: {game_proc.name()} (PID {game_proc.pid})")')
    lines.append('            log("Optimizations applied. Waiting for game to exit...")')
    lines.append("            while game_proc.is_running():")
    lines.append("                if STOP_EVENT.wait(5):")
    lines.append("                    break")
    lines.append('            log("Game exited.")')
    lines.append("        else:")
    lines.append('            log("Wait interrupted before game start.")')
    lines.append("    except KeyboardInterrupt:")
    lines.append('        log("Interrupted by user.")')
    lines.append("    finally:")
    lines.append("        STOP_EVENT.set()")
    lines.append("        cleanup()")
    lines.append("")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    main()")
    lines.append("")

    return "\n".join(lines)
