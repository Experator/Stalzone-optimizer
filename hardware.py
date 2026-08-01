import json
import os
import platform
import re
import subprocess
import sys
from typing import List, Optional

import psutil

from .models import (
    CpuInfo,
    DiskInfo,
    GpuInfo,
    HardwareReport,
    LiveMetrics,
    OsInfo,
    RamInfo,
)

# Helpers 

def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _run_cmd(cmd: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _run_powershell(script: str, timeout: int = 20) -> str:
    try:
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
        }
        if _is_windows():
            # CREATE_NO_WINDOW = 0x08000000
            kwargs["creationflags"] = 0x08000000
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            **kwargs,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _ps_json(script: str, timeout: int = 20):
    out = _run_powershell(script, timeout=timeout)
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        try:
            data = json.loads(out.strip().strip('"'))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        data = []
    return data


def _gb(bytes_val: float) -> float:
    return round(bytes_val / (1024 ** 3), 2)


def _to_int(val, default=0) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default


def _to_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# CPU Detection 

def _detect_cpu() -> CpuInfo:
    manufacturer = "Unknown"
    brand = "Unknown"
    speed = 0.0
    physical = 1
    logical = 1

    try:
        physical = psutil.cpu_count(logical=False) or 1
        logical = psutil.cpu_count(logical=True) or 1
    except Exception:
        pass

    try:
        freq = psutil.cpu_freq()
        if freq:
            speed = round(freq.current / 1000, 2)  # MHz -> GHz
    except Exception:
        pass

    if _is_windows():
        brand, manufacturer = _detect_cpu_windows()
    else:
        brand, manufacturer = _detect_cpu_linux()

    return CpuInfo(
        manufacturer=manufacturer,
        brand=brand,
        speed_ghz=speed,
        cores_physical=physical,
        cores_logical=logical,
        current_load=None,
    )


def _detect_cpu_windows() -> tuple:
    # Method 1: Get-CimInstance Win32_Processor
    try:
        data = _ps_json(
            "Get-CimInstance Win32_Processor | "
            "Select-Object Name,Manufacturer | ConvertTo-Json -Depth 3"
        )
        if data:
            item = data[0]
            name = (item.get("Name") or "").strip()
            manuf = (item.get("Manufacturer") or "").strip()
            if name:
                return name, (manuf or "Unknown")
    except Exception:
        pass

    # Method 2: Registry
    try:
        name = _run_powershell(
            "(Get-ItemProperty 'HKLM:\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0' "
            "-ErrorAction SilentlyContinue).ProcessorNameString"
        )
        manuf = _run_powershell(
            "(Get-ItemProperty 'HKLM:\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0' "
            "-ErrorAction SilentlyContinue).VendorIdentifier"
        )
        if name:
            return name.strip(), (manuf.strip() if manuf else "Unknown")
    except Exception:
        pass

    # Method 3: environment variable PROCESSOR_IDENTIFIER
    try:
        ident = os.environ.get("PROCESSOR_IDENTIFIER", "")
        if ident:
            parts = ident.split(",")
            if len(parts) >= 2:
                return parts[0].strip() or "Unknown", parts[1].strip() or "Unknown"
    except Exception:
        pass

    return "Unknown", "Unknown"

def _detect_cpu_linux() -> tuple:
    brand = "Unknown"
    manufacturer = "Unknown"
    try:
        with open("/proc/cpuinfo", "r") as f:
            content = f.read()
        m = re.search(r"model name\s*:\s*(.+)", content)
        if m:
            brand = m.group(1).strip()
        m2 = re.search(r"vendor_id\s*:\s*(.+)", content)
        if m2:
            manufacturer = m2.group(1).strip()
        else:
            if "Intel" in brand:
                manufacturer = "Intel"
            elif "AMD" in brand or "Ryzen" in brand:
                manufacturer = "AMD"
    except Exception:
        pass
    return brand, manufacturer


# GPU Detection 

def _is_integrated(model: str) -> bool:
    lower = model.lower()
    integrated_patterns = [
        "intel(r) uhd", "intel(r) iris", "intel(r) hd",
        "radeon(tm) graphics", "radeon graphics", "radeon integrated",
        "amd radeon(tm) graphics", "mali", "adreno",
    ]

    if "arc" in lower and "uhd" not in lower and "iris" not in lower:
        return False
    return any(p in lower for p in integrated_patterns)

def _vendor_from_model(model: str) -> str:
    lower = model.lower()
    if "nvidia" in lower or "geforce" in lower or "rtx" in lower or "gtx" in lower:
        return "NVIDIA"
    if "amd" in lower or "radeon" in lower:
        return "AMD"
    if "intel" in lower:
        return "Intel"
    return "Unknown"

def _detect_gpus() -> List[GpuInfo]:
    if _is_windows():
        gpus = _detect_gpus_windows()
        if gpus:
            return gpus
    else:
        gpus = _detect_gpus_linux()
        if gpus:
            return gpus
    return [GpuInfo(
        model="Unknown GPU", vendor="unknown",
        vram_mb=None, driver_version=None, is_integrated=False,
    )]

def _detect_gpus_windows() -> List[GpuInfo]:
    gpus: List[GpuInfo] = []

    data = _ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,AdapterRAM,CurrentRefreshRate,"
        "CurrentHorizontalResolution,CurrentVerticalResolution | "
        "ConvertTo-Json -Depth 3"
    )

    for item in data:
        name = (item.get("Name") or "").strip()
        if not name:
            continue
        driver = (item.get("DriverVersion") or "").strip() or None
        vram_mb = _get_gpu_vram_mb_from_registry(name)
        if vram_mb is None:
            adapter_ram = item.get("AdapterRAM")
            if adapter_ram:
                vram_mb = _to_int(adapter_ram) // (1024 * 1024)
                if vram_mb <= 0:
                    vram_mb = None
        gpus.append(GpuInfo(
            model=name,
            vendor=_vendor_from_model(name),
            vram_mb=vram_mb,
            driver_version=driver,
            is_integrated=_is_integrated(name),
        ))

    if gpus:
        return gpus

    try:
        reg_data = _ps_json(
            "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}' "
            "-ErrorAction SilentlyContinue | "
            "Where-Object { $_.PSChildName -match '^\\d{4}$' } | "
            "ForEach-Object { "
            "  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue; "
            "  [PSCustomObject]@{ DriverDesc = $p.DriverDesc; "
            "    MemorySize = $p.'HardwareInformation.qwMemorySize'; "
            "    OldMemSize = $p.'HardwareInformation.MemorySize'; "
            "    DriverVersion = $p.DriverVersion } "
            "} | ConvertTo-Json -Depth 3"
        )
        for item in reg_data:
            name = (item.get("DriverDesc") or "").strip()
            if not name:
                continue
            vram_mb = None
            mem_size = item.get("MemorySize")
            if mem_size:
                bytes_val = _to_int(mem_size)
                if bytes_val > 0:
                    vram_mb = bytes_val // (1024 * 1024)
            if vram_mb is None:
                old_mem = item.get("OldMemSize")
                if old_mem:
                    vram_mb = _to_int(old_mem) // (1024 * 1024)
            gpus.append(GpuInfo(
                model=name,
                vendor=_vendor_from_model(name),
                vram_mb=vram_mb or None,
                driver_version=(item.get("DriverVersion") or "").strip() or None,
                is_integrated=_is_integrated(name),
            ))
    except Exception:
        pass

    return gpus

def _get_gpu_vram_mb_from_registry(gpu_name: str) -> Optional[int]:
    try:
        reg_data = _ps_json(
            "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}' "
            "-ErrorAction SilentlyContinue | "
            "Where-Object { $_.PSChildName -match '^\\d{4}$' } | "
            "ForEach-Object { "
            "  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue; "
            "  [PSCustomObject]@{ DriverDesc = $p.DriverDesc; "
            "    QwMem = $p.'HardwareInformation.qwMemorySize' } "
            "} | ConvertTo-Json -Depth 3"
        )
        target = gpu_name.lower().strip()
        for item in reg_data:
            desc = (item.get("DriverDesc") or "").lower().strip()
            qw = item.get("QwMem")
            if qw and desc and (desc == target or target in desc or desc in target):
                bytes_val = _to_int(qw)
                if bytes_val > 0:
                    return bytes_val // (1024 * 1024)
    except Exception:
        pass
    return None

def _detect_gpus_linux() -> List[GpuInfo]:
    gpus: List[GpuInfo] = []
    out = _run_cmd("lspci 2>/dev/null", timeout=5)
    for line in out.split("\n"):
        lower = line.lower()
        if "vga compatible controller:" in lower or "3d controller:" in lower or "display controller:" in lower:
            model = line.split(":", 2)[-1].strip() if ":" in line else line.strip()

            gpus.append(GpuInfo(
                model=model,
                vendor=_vendor_from_model(model),
                vram_mb=None,
                driver_version=None,
                is_integrated=_is_integrated(model),
            ))
    return gpus

# RAM Detection 

def _detect_ram() -> RamInfo:
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    total = vm.total
    used = vm.used
    return RamInfo(
        total_gb=_gb(total),
        used_gb=_gb(used),
        free_gb=_gb(total - used),
        used_percent=round(vm.percent, 1),
        swap_total_gb=_gb(sm.total),
        swap_used_gb=_gb(sm.used),
    )


# Disk Detection 

# Windows MediaType enum: 0=Unspecified, 3=HDD, 4=SSD, 5=SCM, 6=Unspecified
# Windows BusType enum: 0=Unknown,       1=SCSI, 3=ATA, 7=USB, 8=RAID, 11=SATA,
#                                        14=Virtual, 15=FileBackedVirtual, 17=NVMe


_PS_MEDIATYPE_MAP = {"3": "HDD", "4": "SSD", "5": "SCM"}
_PS_BUSTYPE_NVME = "17"


def _disk_type_from_ps(media_type: str, bus_type: str, model: str) -> str:
    if media_type in _PS_MEDIATYPE_MAP:
        dtype = _PS_MEDIATYPE_MAP[media_type]

        if bus_type == _PS_BUSTYPE_NVME:
            return "NVMe"
        return dtype

    lower = (model or "").lower()
    if "nvme" in lower or "970" in lower or "980" in lower or "960" in lower or "990" in lower:
        return "NVMe"
    if "ssd" in lower or "solid state" in lower:
        return "SSD"
    if "hdd" in lower or "hard disk" in lower:
        return "HDD"
    return "Unknown"


def _detect_disks() -> List[DiskInfo]:
    if _is_windows():
        disks = _detect_disks_windows()
        if disks:
            return disks
    else:
        disks = _detect_disks_linux()
        if disks:
            return disks

    try:
        out = []
        for part in psutil.disk_partitions():
            if part.device:
                out.append(DiskInfo(
                    device=part.device, type="Unknown",
                    size_gb=0, interface_type=None,
                ))
        return out
    except Exception:
        return []

def _detect_disks_windows() -> List[DiskInfo]:
    disks: List[DiskInfo] = []

    data = _ps_json(
        "Get-PhysicalDisk -ErrorAction SilentlyContinue | "
        "Select-Object FriendlyName,MediaType,Size,BusType | "
        "ConvertTo-Json -Depth 3"
    )
    for item in data:
        model = (item.get("FriendlyName") or "").strip() or "Unknown"
        media_type = str(item.get("MediaType") or "")
        bus_type = str(item.get("BusType") or "")
        size_bytes = _to_int(item.get("Size"))
        dtype = _disk_type_from_ps(media_type, bus_type, model)
        iface = _bustype_to_iface(bus_type)
        disks.append(DiskInfo(
            device=model,
            type=dtype,
            size_gb=_gb(size_bytes) if size_bytes > 0 else 0,
            interface_type=iface,
        ))

    if disks:
        return disks

    data = _ps_json(
        "Get-CimInstance Win32_DiskDrive | "
        "Select-Object Model,Size,InterfaceType,MediaType | "
        "ConvertTo-Json -Depth 3"
    )
    for item in data:
        model = (item.get("Model") or "").strip() or "Unknown"
        size_bytes = _to_int(item.get("Size"))
        iface = (item.get("InterfaceType") or "").strip() or None
        media = (item.get("MediaType") or "").strip().lower()
        combined = f"{media} {iface} {model}".lower()
        if "nvme" in combined or "pcie" in combined:
            dtype = "NVMe"
        elif "ssd" in combined or "solid" in combined or "fixed hard disk media" in combined:
            dtype = "SSD"
        elif "hdd" in combined:
            dtype = "HDD"
        elif "nvme" in model.lower() or "970" in model or "980" in model or "990" in model:
            dtype = "NVMe"
        elif "ssd" in model.lower():
            dtype = "SSD"
        else:
            dtype = "Unknown"
        disks.append(DiskInfo(
            device=model,
            type=dtype,
            size_gb=_gb(size_bytes) if size_bytes > 0 else 0,
            interface_type=iface,
        ))

    return disks


def _bustype_to_iface(bus_type: str) -> Optional[str]:
    mapping = {
        "0": "Unknown", "1": "SCSI", "2": "ATAPI", "3": "ATA",
        "7": "USB", "8": "RAID", "11": "SATA", "14": "Virtual",
        "15": "FileBackedVirtual", "17": "NVMe",
    }
    return mapping.get(bus_type)


def _detect_disks_linux() -> List[DiskInfo]:
    disks: List[DiskInfo] = []
    out = _run_cmd("lsblk -d -b -o NAME,SIZE,ROTA,MODEL 2>/dev/null", timeout=5)
    for line in out.split("\n")[1:]:  # skip header
        parts = line.split()
        if len(parts) < 3:
            continue
        name = parts[0]
        try:
            size_bytes = int(parts[1])
        except (ValueError, IndexError):
            size_bytes = 0
        try:
            rota = int(parts[2])  # 1 = HDD (rotational), 0 = SSD
        except (ValueError, IndexError):
            rota = 0
        model = " ".join(parts[3:]) if len(parts) > 3 else name
        is_nvme = "nvme" in name.lower()
        dtype = "NVMe" if is_nvme else ("HDD" if rota == 1 else "SSD")
        if size_bytes > 0:
            disks.append(DiskInfo(
                device=model or name,
                type=dtype,
                size_gb=_gb(size_bytes),
                interface_type="NVMe" if is_nvme else "SATA",
            ))
    return disks

# OS Detection 

def _detect_os() -> OsInfo:
    if _is_windows():
        # Get a clean Windows version via PowerShell (more reliable than platform.release())
        release = _run_powershell(
            "[System.Environment]::OSVersion.Version.ToString()"
        ) or platform.release()
        edition = _run_powershell(
            "(Get-CimInstance Win32_OperatingSystem).Caption"
        )
        distro = (edition or f"Windows {release}").strip()
        return OsInfo(
            platform="Windows",
            distro=distro,
            release=release,
            kernel=None,
            arch=platform.machine(),
            hostname=platform.node(),
        )
    return OsInfo(
        platform=platform.system(),
        distro=platform.version(),
        release=platform.release(),
        kernel=platform.version(),
        arch=platform.machine(),
        hostname=platform.node(),
    )


# Display Detection 

def _detect_display():
    if _is_windows():
        return _detect_display_windows()
    return _detect_display_linux()


def _detect_display_windows():
    resolution = None
    refresh = None

    data = _ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object CurrentRefreshRate,CurrentHorizontalResolution,"
        "CurrentVerticalResolution | ConvertTo-Json -Depth 3"
    )
    for item in data:
        h = _to_int(item.get("CurrentHorizontalResolution"))
        v = _to_int(item.get("CurrentVerticalResolution"))
        r = _to_float(item.get("CurrentRefreshRate"))
        if h > 0 and v > 0:
            resolution = f"{h}x{v}"
        if r > 0:
            refresh = r
            break

    if not resolution:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            if w and h:
                resolution = f"{w}x{h}"
        except Exception:
            pass

    if not refresh or refresh <= 0:
        try:
            reg_out = _run_powershell(
                "Get-ChildItem 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}' "
                "-ErrorAction SilentlyContinue | "
                "Where-Object { $_.PSChildName -match '^\\d{4}$' } | "
                "ForEach-Object { "
                "  (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue)."
                "    'HardwareInformation.CurrentRefresh' "
                "} | Where-Object { $_ -gt 0 } | Select-Object -First 1"
            )
            if reg_out:
                val = _to_float(reg_out)
                if val > 0:
                    refresh = val
        except Exception:
            pass

    return resolution, refresh

def _detect_display_linux():
    resolution = None
    refresh = None
    out = _run_cmd("xrandr --current 2>/dev/null", timeout=5)
    if out:
        # Look for the line with " * " (active mode) e.g. "1920x1080     60.00*+  59.93"
        for line in out.split("\n"):
            if "*" in line and "x" in line.split()[0]:
                m = re.search(r"(\d+x\d+)\s+([\d.]+)\*", line)
                if m:
                    resolution = m.group(1)
                    refresh = float(m.group(2))
                    break
    return resolution, refresh

# Main Detection 

def detect_hardware() -> HardwareReport:
    cpu = _detect_cpu()

    # Current CPU load
    try:
        cpu.current_load = round(psutil.cpu_percent(interval=0.5), 1)
    except Exception:
        pass

    gpus = _detect_gpus()
    ram = _detect_ram()
    disks = _detect_disks()
    os_info = _detect_os()
    resolution, refresh = _detect_display()

    return HardwareReport(
        cpu=cpu,
        gpus=gpus,
        ram=ram,
        disks=disks,
        os=os_info,
        display_resolution=resolution,
        display_refresh_rate=refresh,
        detected_at=__import__("datetime").datetime.now().isoformat(),
    )


# Metrics 
DEFAULT_GAME_NAMES = [
    "Stalcraft.exe", "Stalcraftw.exe", "Stalzone.exe", "Stalzonew.exe",
]

def get_live_metrics(game_names: List[str]) -> LiveMetrics:
    # CPU
    try:
        cpu_load = psutil.cpu_percent(interval=None)
    except Exception:
        cpu_load = 0.0

    try:
        per_core = [round(c, 1) for c in psutil.cpu_percent(interval=None, percpu=True)]
    except Exception:
        per_core = []

    # RAM
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    ram_pct = round(vm.percent, 1)
    ram_used = _gb(vm.used)
    ram_total = _gb(vm.total)
    swap_pct = round(sm.percent, 1) if sm.total > 0 else 0.0

    # Find game process
    names = game_names or DEFAULT_GAME_NAMES
    game_running = False
    game_name = None
    game_cpu = None
    game_mem = None
    game_pid = None

    try:
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                pname = proc.info.get("name", "") or ""
                if any(n.lower() in pname.lower() for n in names):
                    game_running = True
                    game_name = pname
                    game_pid = proc.info.get("pid")
                    game_cpu = proc.info.get("cpu_percent") or 0.0
                    mi = proc.info.get("memory_info")
                    if mi:
                        game_mem = round(mi.rss / (1024 * 1024), 1)
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    return LiveMetrics(
        cpu_load=round(cpu_load, 1),
        cpu_per_core=per_core,
        ram_used_percent=ram_pct,
        ram_used_gb=ram_used,
        ram_total_gb=ram_total,
        swap_used_percent=swap_pct,
        game_process_running=game_running,
        game_process_name=game_name,
        game_process_cpu=game_cpu,
        game_process_memory_mb=game_mem,
        game_process_pid=game_pid,
    )
