"""Settings backup module for the StalZone Optimizer.

Captures a snapshot of all system settings the optimizer can change, saves it
to a JSON file next to the executable (or main.py), and can restore it later.
"""
import json
import os
import platform
import re
import socket
import sys
from datetime import datetime
from typing import List, Optional, Tuple

from .models import (
    ApplyResult,
    RegistryEntry,
    ServiceState,
    SettingsBackup,
)
from .optimizations import (
    _enumerate_tcpip_interfaces,
    _reg_add,
    _run,
    _run_ps,
)

__all__ = [
    "get_backup_path",
    "capture_backup",
    "save_backup",
    "load_backup",
    "restore_backup",
    "backup_exists",
    "create_backup_if_not_exists",
    "REGISTRY_KEYS_TO_BACKUP",
    "SERVICES_TO_BACKUP",
]

_is_windows = platform.system().lower() == "windows"

BACKUP_VERSION = "1.0"
BACKUP_FILENAME = "stalzone_settings_backup.json"

# Visual effects / transparency paths (used both in capture and restore).
_VISUAL_EFFECTS_PATH = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"
_TRANSPARENCY_PATH = r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"

# All registry paths/values the optimizer touches. Each must be captured so
# they can be restored. (Nagle values under Tcpip\Parameters\Interfaces\{GUID}
# are enumerated dynamically — see capture_backup.)
REGISTRY_KEYS_TO_BACKUP: List[Tuple[str, str]] = [
    # Game DVR
    (r"HKCU\System\GameConfigStore", "GameDVR_Enabled"),
    (r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR", "FTRenabled"),
    # Memory
    (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management", "LargeSystemCache"),
    # GPU
    (r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "TdrDelay"),
    (r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "TdrLevel"),
    (r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode"),
    (r"HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000", "PerfLevelSrc"),
    # Network
    # (Nagle: dynamically enumerated interfaces under
    #  HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\{GUID})
    (r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile", "NetworkThrottlingIndex"),
    # Visual
    (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting"),
    (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency"),
    # Game
    (r"HKCU\Software\Microsoft\GameBar", "AutoGameModeEnabled"),
]

# Windows services the optimizer can disable/stop. Each must be captured so
# its start mode and running state can be restored.
SERVICES_TO_BACKUP: List[str] = [
    "WSearch",      # Windows Search
    "SysMain",      # Superfetch
    "DiagTrack",    # Diagnostic Tracking (telemetry)
]

# PowerShell's Get-Service StartType values ("Automatic", "Manual",
# "Disabled") normalized to the ServiceState.start_mode canonical form
# ("auto", "manual", "disabled").
_STARTTYPE_MAP = {
    "automatic": "auto",
    "auto": "auto",
    "manual": "manual",
    "demand": "manual",
    "disabled": "disabled",
}

# ServiceState.start_mode ("auto"|"manual"|"disabled") mapped to the
# `sc config <name> start= <mode>` token expected by sc.exe.
_SC_MODE_MAP = {
    "auto": "auto",
    "manual": "demand",
    "disabled": "disabled",
}


# =========================================================================
# Path / existence helpers
# =========================================================================

def get_backup_path() -> str:
    """Return the path to stalzone_settings_backup.json.

    Located next to the executable (if frozen with PyInstaller) or main.py.
    """
    if getattr(sys, "frozen", False):
        # Running as compiled .exe — use the exe's directory.
        base = os.path.dirname(sys.executable)
    else:
        # Running as script — this file lives in src/, go up one level to
        # reach main.py's directory.
        base = os.path.dirname(os.path.abspath(__file__))
        base = os.path.dirname(base)
    return os.path.join(base, BACKUP_FILENAME)


def backup_exists() -> bool:
    """Check if a backup file exists at the default path."""
    try:
        return os.path.isfile(get_backup_path())
    except Exception:
        return False


def create_backup_if_not_exists() -> Optional[str]:
    """On first launch: if no backup exists, capture + save one.

    Returns the path if created, None if a backup already existed or on error.
    """
    if backup_exists():
        return None
    try:
        backup = capture_backup()
        return save_backup(backup)
    except Exception:
        return None


# =========================================================================
# Capture helpers
# =========================================================================

# Matches a "reg query" value line: optional leading whitespace, name, type
# (REG_*), then the value (rest of the line). Example:
#     "    VisualFXSetting    REG_DWORD    0x0"
_REG_VALUE_RE = re.compile(r"^\s*(\S+)\s+(REG_\w+)\s+(.+?)\s*$")

# Matches a GUID like "381b4222-f694-41f0-9685-ff5bb260df2e".
_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _read_registry_value(path: str, name: str) -> Optional[RegistryEntry]:
    """Read a single registry value via `reg query`.

    Returns a RegistryEntry. If the value (or its key) does not exist,
    returns an entry with `existed=False`. Returns None on hard failure
    (e.g. non-Windows platform).
    """
    if not _is_windows:
        return None
    ok, out = _run(f'reg query "{path}" /v "{name}"', timeout=10)
    if not ok:
        # Value or key doesn't exist (reg.exe returns non-zero) — record
        # the absence so we can `reg delete` it on restore if needed.
        return RegistryEntry(
            path=path, name=name, value="",
            value_type="REG_SZ", existed=False,
        )
    for line in (out or "").split("\n"):
        m = _REG_VALUE_RE.match(line)
        if not m:
            continue
        vname, vtype, vvalue = m.group(1), m.group(2), m.group(3)
        if vname.lower() == name.lower():
            return RegistryEntry(
                path=path, name=name,
                value=vvalue, value_type=vtype, existed=True,
            )
    # Query succeeded but the expected value name wasn't found.
    return RegistryEntry(
        path=path, name=name, value="",
        value_type="REG_SZ", existed=False,
    )


def _read_service_state(name: str) -> Optional[ServiceState]:
    """Read service StartType + Status via PowerShell Get-Service.

    Returns a ServiceState with start_mode normalized to "auto"|"manual"|
    "disabled" and was_running=True if Status=="Running". Returns None if
    the service doesn't exist or PowerShell failed.
    """
    if not _is_windows:
        return None
    ok, out = _run_ps(
        f"Get-Service -Name '{name}' -ErrorAction SilentlyContinue | "
        f"Select-Object StartType,Status | ConvertTo-Json",
        timeout=10,
    )
    if not ok or not out:
        return None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        # Get-Service returns an array if multiple matched; take the first.
        if isinstance(data, list) and data:
            data = data[0]
        else:
            return None
    start_raw = str(data.get("StartType", "")).strip().lower()
    status_raw = str(data.get("Status", "")).strip().lower()
    start_mode = _STARTTYPE_MAP.get(start_raw, start_raw or "auto")
    return ServiceState(
        name=name,
        start_mode=start_mode,
        was_running=(status_raw == "running"),
    )


def _read_active_power_plan() -> Optional[str]:
    """Get the active power plan GUID via `powercfg /getactivescheme`.

    Output looks like: "Power Scheme GUID: 381b4222-...-ff5bb260df2e  (Balanced)"
    Returns the GUID string, or None if it can't be parsed.
    """
    if not _is_windows:
        return None
    ok, out = _run("powercfg /getactivescheme", timeout=10)
    if not ok or not out:
        return None
    m = _GUID_RE.search(out)
    return m.group(0) if m else None


def _dword_to_int(value: str) -> Optional[int]:
    """Parse a reg.exe DWORD value string ("0x1", "0xffffffff", "1") to int."""
    if value is None:
        return None
    try:
        v = value.strip()
        if v.lower().startswith("0x"):
            return int(v, 16)
        return int(v)
    except (ValueError, TypeError):
        return None


def _read_visual_effects_setting() -> Optional[int]:
    """Read VisualFXSetting as int (0..3) or None if not present."""
    entry = _read_registry_value(_VISUAL_EFFECTS_PATH, "VisualFXSetting")
    if entry is None or not entry.existed:
        return None
    return _dword_to_int(entry.value)


def _read_transparency() -> Optional[bool]:
    """Read EnableTransparency as bool or None if not present."""
    entry = _read_registry_value(_TRANSPARENCY_PATH, "EnableTransparency")
    if entry is None or not entry.existed:
        return None
    val = _dword_to_int(entry.value)
    if val is None:
        return None
    return val != 0


def _read_pagefile_state() -> Optional[bool]:
    """Read AutomaticManagedPagefile from CIM Win32_ComputerSystem."""
    if not _is_windows:
        return None
    ok, out = _run_ps(
        "Get-CimInstance Win32_ComputerSystem | "
        "Select-Object -ExpandProperty AutomaticManagedPagefile",
        timeout=10,
    )
    if not ok or out is None:
        return None
    s = out.strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return None


def _capture_registry_entries() -> List[RegistryEntry]:
    """Capture all REGISTRY_KEYS_TO_BACKUP entries plus Nagle values.

    Duplicate (path, name) pairs in REGISTRY_KEYS_TO_BACKUP are de-duplicated.
    Nagle: enumerate interfaces under Tcpip\\Parameters\\Interfaces\\{GUID} and
    back up any existing TcpAckFrequency / TCPNoDelay values (they may not
    exist by default — we only record them when they do).
    """
    entries: List[RegistryEntry] = []
    seen = set()
    for path, name in REGISTRY_KEYS_TO_BACKUP:
        key = (path.lower(), name.lower())
        if key in seen:
            # Spec list may contain duplicates — only capture once.
            continue
        seen.add(key)
        try:
            entry = _read_registry_value(path, name)
            if entry is not None:
                entries.append(entry)
            else:
                entries.append(RegistryEntry(
                    path=path, name=name, value="",
                    value_type="REG_SZ", existed=False,
                ))
        except Exception:
            entries.append(RegistryEntry(
                path=path, name=name, value="",
                value_type="REG_SZ", existed=False,
            ))

    # Nagle algorithm: dynamically enumerate network adapter GUIDs and back
    # up existing TcpAckFrequency / TCPNoDelay values for each.
    try:
        iface_paths = _enumerate_tcpip_interfaces()
    except Exception:
        iface_paths = []
    for iface_path in iface_paths:
        for value_name in ("TcpAckFrequency", "TCPNoDelay"):
            try:
                entry = _read_registry_value(iface_path, value_name)
            except Exception:
                continue
            if entry is not None and entry.existed:
                entries.append(entry)
    return entries


def _capture_services() -> List[ServiceState]:
    """Capture start mode + running state for every SERVICES_TO_BACKUP entry."""
    services: List[ServiceState] = []
    for name in SERVICES_TO_BACKUP:
        try:
            st = _read_service_state(name)
            if st is not None:
                services.append(st)
            else:
                # Service not found or PS failed — record a safe default so
                # restore_backup can still attempt recovery.
                services.append(ServiceState(
                    name=name, start_mode="auto", was_running=False,
                ))
        except Exception:
            services.append(ServiceState(
                name=name, start_mode="auto", was_running=False,
            ))
    return services


def capture_backup() -> SettingsBackup:
    """Capture current system settings into a SettingsBackup object.

    Captures:
    - Active power plan GUID (powercfg /getactivescheme)
    - Visual effects setting (HKCU\\\\...\\\\Explorer\\\\VisualEffects\\\\VisualFXSetting)
    - Transparency setting (HKCU\\\\...\\\\Themes\\\\Personalize\\\\EnableTransparency)
    - AutomaticManagedPagefile state (CIM Win32_ComputerSystem)
    - All registry entries the optimizer touches (see REGISTRY_KEYS_TO_BACKUP)
    - All Windows services the optimizer touches (see SERVICES_TO_BACKUP)

    On non-Windows platforms, returns an empty SettingsBackup with a note
    explaining that capture was skipped. Never raises.
    """
    if not _is_windows:
        return SettingsBackup(
            version=BACKUP_VERSION,
            created_at=datetime.now().isoformat(timespec="seconds"),
            hostname=socket.gethostname(),
            notes="Non-Windows platform: backup is empty (capture skipped).",
        )
    try:
        return SettingsBackup(
            version=BACKUP_VERSION,
            created_at=datetime.now().isoformat(timespec="seconds"),
            hostname=socket.gethostname(),
            registry_entries=_capture_registry_entries(),
            services=_capture_services(),
            power_plan_guid=_read_active_power_plan(),
            visual_effects_setting=_read_visual_effects_setting(),
            transparency_enabled=_read_transparency(),
            automatic_managed_pagefile=_read_pagefile_state(),
            notes="",
        )
    except Exception as e:
        # Last-resort safety: never crash the GUI on capture.
        return SettingsBackup(
            version=BACKUP_VERSION,
            created_at=datetime.now().isoformat(timespec="seconds"),
            hostname=socket.gethostname(),
            notes=f"Capture incomplete: {e}",
        )


# =========================================================================
# Save / Load
# =========================================================================

def save_backup(backup: SettingsBackup, path: str = None) -> str:
    """Save backup to JSON file. Returns the path used.

    If path is None, uses get_backup_path().
    """
    if path is None:
        path = get_backup_path()
    payload = json.dumps(backup.to_dict(), indent=2, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)
    return path


def load_backup(path: str = None) -> Optional[SettingsBackup]:
    """Load backup from JSON file. Returns None if file doesn't exist or is corrupt.

    If path is None, uses get_backup_path().
    """
    if path is None:
        path = get_backup_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return SettingsBackup.from_dict(data)
    except Exception:
        return None


# =========================================================================
# Restore
# =========================================================================

def _emit(result: ApplyResult, results: List[ApplyResult],
          progress_callback) -> None:
    """Append a result and invoke the progress callback (swallowing errors)."""
    results.append(result)
    if progress_callback is not None:
        try:
            progress_callback(result)
        except Exception:
            pass


def _restore_registry_entry(entry: RegistryEntry) -> ApplyResult:
    """Restore a single registry entry (reg add with original value, or
    reg delete if it didn't originally exist)."""
    item_id = f"backup_reg_{entry.name}"
    if not _is_windows:
        return ApplyResult(
            toggle_id=item_id, success=False,
            message="Windows-only: registry restore skipped",
            action="restored",
        )
    if not entry.existed:
        # Value didn't exist originally — remove whatever the optimizer added.
        ok, msg = _run(
            f'reg delete "{entry.path}" /v "{entry.name}" /f',
            timeout=10,
        )
        if ok:
            return ApplyResult(
                toggle_id=item_id, success=True,
                message=f"Restored: deleted {entry.name} (was not present originally)",
                action="restored",
            )
        return ApplyResult(
            toggle_id=item_id, success=False,
            message=f"Error deleting {entry.name}: {msg}",
            action="restored",
        )
    # Value existed — re-add it with the original value and type.
    ok, msg = _reg_add(entry.path, entry.name, entry.value, entry.value_type)
    if ok:
        return ApplyResult(
            toggle_id=item_id, success=True,
            message=f"Restored: {entry.name} = {entry.value} ({entry.value_type})",
            action="restored",
        )
    return ApplyResult(
        toggle_id=item_id, success=False,
        message=f"Error restoring {entry.name}: {msg}",
        action="restored",
    )


def _restore_service(svc: ServiceState) -> ApplyResult:
    """Restore a service's start mode (sc config) and start it if was_running."""
    item_id = f"backup_svc_{svc.name}"
    if not _is_windows:
        return ApplyResult(
            toggle_id=item_id, success=False,
            message="Windows-only: service restore skipped",
            action="restored",
        )
    sc_mode = _SC_MODE_MAP.get((svc.start_mode or "auto").lower(), "auto")
    ok, msg = _run(
        f'sc config {svc.name} start= {sc_mode}',
        timeout=15,
    )
    if not ok:
        return ApplyResult(
            toggle_id=item_id, success=False,
            message=f"Error restoring {svc.name} start mode ({sc_mode}): {msg}",
            action="restored",
        )
    extra = ""
    if svc.was_running:
        ok2, msg2 = _run(f'sc start {svc.name}', timeout=30)
        if ok2:
            extra = " + started"
        else:
            # Start may legitimately fail (e.g. service already running, or
            # requires manual trigger). The config restore itself succeeded.
            extra = f" (start failed: {msg2})"
    return ApplyResult(
        toggle_id=item_id, success=True,
        message=f"Restored: {svc.name} start mode = {sc_mode}{extra}",
        action="restored",
    )


def restore_backup(backup: SettingsBackup,
                   progress_callback=None) -> List[ApplyResult]:
    """Restore all settings from a backup.

    - Restores active power plan
    - Restores visual effects setting
    - Restores transparency
    - Restores AutomaticManagedPagefile
    - Restores each registry entry (reg add with original value, or reg delete
      if it didn't exist)
    - Restores each service start mode (sc config) and starts it if was_running

    progress_callback(result: ApplyResult) is called after each restoration
    step so the UI can show live progress.

    Returns list of ApplyResult (one per restored item).
    """
    results: List[ApplyResult] = []

    if not _is_windows:
        # On non-Windows: emit one Windows-only skip result per non-empty
        # backup field so the UI can show what would have been restored.
        if backup.power_plan_guid is not None:
            _emit(ApplyResult(
                toggle_id="backup_power_plan", success=False,
                message="Windows-only: power plan restore skipped",
                action="restored",
            ), results, progress_callback)
        if backup.visual_effects_setting is not None:
            _emit(ApplyResult(
                toggle_id="backup_visual_effects", success=False,
                message="Windows-only: visual effects restore skipped",
                action="restored",
            ), results, progress_callback)
        if backup.transparency_enabled is not None:
            _emit(ApplyResult(
                toggle_id="backup_transparency", success=False,
                message="Windows-only: transparency restore skipped",
                action="restored",
            ), results, progress_callback)
        if backup.automatic_managed_pagefile is not None:
            _emit(ApplyResult(
                toggle_id="backup_pagefile", success=False,
                message="Windows-only: pagefile restore skipped",
                action="restored",
            ), results, progress_callback)
        for entry in backup.registry_entries:
            _emit(_restore_registry_entry(entry), results, progress_callback)
        for svc in backup.services:
            _emit(_restore_service(svc), results, progress_callback)
        return results

    # 1. Power plan
    if backup.power_plan_guid:
        ok, msg = _run(
            f"powercfg /setactive {backup.power_plan_guid}",
            timeout=15,
        )
        _emit(ApplyResult(
            toggle_id="backup_power_plan", success=ok,
            message=(f"Restored: power plan {backup.power_plan_guid}"
                     if ok else f"Error: {msg}"),
            action="restored",
        ), results, progress_callback)

    # 2. Visual effects
    if backup.visual_effects_setting is not None:
        ok, msg = _reg_add(
            _VISUAL_EFFECTS_PATH, "VisualFXSetting",
            str(backup.visual_effects_setting), "REG_DWORD",
        )
        _emit(ApplyResult(
            toggle_id="backup_visual_effects", success=ok,
            message=(f"Restored: VisualFXSetting = {backup.visual_effects_setting}"
                     if ok else f"Error: {msg}"),
            action="restored",
        ), results, progress_callback)

    # 3. Transparency
    if backup.transparency_enabled is not None:
        val = "1" if backup.transparency_enabled else "0"
        ok, msg = _reg_add(
            _TRANSPARENCY_PATH, "EnableTransparency", val, "REG_DWORD",
        )
        _emit(ApplyResult(
            toggle_id="backup_transparency", success=ok,
            message=(f"Restored: EnableTransparency = {val}"
                     if ok else f"Error: {msg}"),
            action="restored",
        ), results, progress_callback)

    # 4. AutomaticManagedPagefile
    if backup.automatic_managed_pagefile is not None:
        bool_ps = "$true" if backup.automatic_managed_pagefile else "$false"
        ok, msg = _run_ps(
            "Set-CimInstance -Query 'Select * from Win32_ComputerSystem' "
            f"-Property @{{AutomaticManagedPagefile={bool_ps}}}",
            timeout=15,
        )
        _emit(ApplyResult(
            toggle_id="backup_pagefile", success=ok,
            message=(f"Restored: AutomaticManagedPagefile = "
                     f"{backup.automatic_managed_pagefile}"
                     if ok else f"Error: {msg}"),
            action="restored",
        ), results, progress_callback)

    # 5. Registry entries
    for entry in backup.registry_entries:
        _emit(_restore_registry_entry(entry), results, progress_callback)

    # 6. Services
    for svc in backup.services:
        _emit(_restore_service(svc), results, progress_callback)

    return results
