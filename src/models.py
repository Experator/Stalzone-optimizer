from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

# Hardware

@dataclass
class CpuInfo:
    manufacturer: str
    brand: str
    speed_ghz: float
    cores_physical: int
    cores_logical: int
    current_load: Optional[float] = None

@dataclass
class GpuInfo:
    model: str
    vendor: str
    vram_mb: Optional[int] = None
    driver_version: Optional[str] = None
    is_integrated: bool = False

@dataclass
class RamInfo:
    total_gb: float
    used_gb: float
    free_gb: float
    used_percent: float
    swap_total_gb: float
    swap_used_gb: float

@dataclass
class DiskInfo:
    device: str
    type: str  # "SSD", "HDD", "NVMe", "Unknown"
    size_gb: float
    interface_type: Optional[str] = None

@dataclass
class OsInfo:
    platform: str
    distro: str
    release: str
    kernel: Optional[str]
    arch: str
    hostname: str

@dataclass
class HardwareReport:
    cpu: CpuInfo
    gpus: List[GpuInfo]
    ram: RamInfo
    disks: List[DiskInfo]
    os: OsInfo
    display_resolution: Optional[str] = None
    display_refresh_rate: Optional[float] = None
    detected_at: str = ""

# Assessment

@dataclass
class TierAssessment:
    tier: str  # "low", "mid", "high", "enthusiast", "unknown"
    score: int  # 0-100
    label: str
    description: str
    estimated_fps_min: int
    estimated_fps_max: int
    recommended_preset: str  # "low", "medium", "high", "ultra"
    strengths: List[str] = field(default_factory=list)
    bottlenecks: List[str] = field(default_factory=list)


# Optimization

VALID_CATEGORIES = [
    "power", "cpu", "memory", "gpu", "network",
    "disk", "services", "visual", "game", "processes", "system",
]

@dataclass
class OptimizationToggle:
    id: str
    category: str  # one of VALID_CATEGORIES
    title: str
    description: str
    enabled: bool
    recommended: bool
    impact: str  # "low", "medium", "high", "critical"
    requires_admin: bool
    windows_only: bool


@dataclass
class OptimizationProfile:
    toggles: List[OptimizationToggle]
    game_process_names: List[str]
    timer_resolution_ms: float
    process_priority: str  # "above_normal", "high", "realtime"
    cpu_affinity_mode: str  # "physical", "all", "custom"
    custom_affinity_cores: List[int] = field(default_factory=list)
    memory_cleanup_interval_sec: int = 300
    aggressive_ram_cleanup: bool = False

# Metrics

@dataclass
class LiveMetrics:
    cpu_load: float
    cpu_per_core: List[float]
    ram_used_percent: float
    ram_used_gb: float
    ram_total_gb: float
    swap_used_percent: float
    game_process_running: bool
    game_process_name: Optional[str] = None
    game_process_cpu: Optional[float] = None
    game_process_memory_mb: Optional[float] = None
    game_process_pid: Optional[int] = None

# Apply Result

@dataclass
class ApplyResult:
    toggle_id: str
    success: bool
    message: str
    action: str  # "applied" or "reverted"


# Process Analysis

@dataclass
class ProcessInfo:
    """Information about a running process for optimization analysis."""
    pid: int
    name: str
    cpu_percent: float
    memory_mb: float
    threads: int
    priority: str  # "low", "below_normal", "normal", "above_normal", "high", "realtime"
    is_background: bool  # True if it's a known resource-hungry background app
    category: str  # "browser", "communication", "cloud", "media", "game", "system", "other"
    recommended_action: str  # "kill", "lower_priority", "suspend", "keep"
    description: str  # human-readable description


@dataclass
class ProcessAnalysis:
    total_processes: int
    total_cpu_usage: float
    total_memory_mb: float
    heavy_processes: List[ProcessInfo]  # top resource consumers
    optimizable_processes: List[ProcessInfo]  # processes that can be optimized
    killed_count: int = 0
    optimized_count: int = 0


# ============== Settings Backup (NEW) ==============

@dataclass
class RegistryEntry:
    path: str
    name: str
    value: str
    value_type: str  # "REG_DWORD", "REG_SZ", "REG_BINARY", etc.
    existed: bool  # True if the value existed before


@dataclass
class ServiceState:
    name: str
    start_mode: str  # "auto", "manual", "disabled"
    was_running: bool


@dataclass
class SettingsBackup:
    version: str
    created_at: str  # ISO timestamp
    hostname: str
    registry_entries: List[RegistryEntry] = field(default_factory=list)
    services: List[ServiceState] = field(default_factory=list)
    power_plan_guid: Optional[str] = None  # active power plan before optimization
    visual_effects_setting: Optional[int] = None  # 0=let windows decide, 1=best appearance, 2=best performance, 3=custom
    transparency_enabled: Optional[bool] = None
    automatic_managed_pagefile: Optional[bool] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "hostname": self.hostname,
            "notes": self.notes,
            "power_plan_guid": self.power_plan_guid,
            "visual_effects_setting": self.visual_effects_setting,
            "transparency_enabled": self.transparency_enabled,
            "automatic_managed_pagefile": self.automatic_managed_pagefile,
            "registry_entries": [
                {"path": r.path, "name": r.name, "value": r.value,
                 "value_type": r.value_type, "existed": r.existed}
                for r in self.registry_entries
            ],
            "services": [
                {"name": s.name, "start_mode": s.start_mode, "was_running": s.was_running}
                for s in self.services
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SettingsBackup":
        return cls(
            version=data.get("version", "1.0"),
            created_at=data.get("created_at", ""),
            hostname=data.get("hostname", ""),
            notes=data.get("notes", ""),
            power_plan_guid=data.get("power_plan_guid"),
            visual_effects_setting=data.get("visual_effects_setting"),
            transparency_enabled=data.get("transparency_enabled"),
            automatic_managed_pagefile=data.get("automatic_managed_pagefile"),
            registry_entries=[
                RegistryEntry(
                    path=r["path"], name=r["name"], value=r["value"],
                    value_type=r.get("value_type", "REG_SZ"),
                    existed=r.get("existed", True),
                )
                for r in data.get("registry_entries", [])
            ],
            services=[
                ServiceState(
                    name=s["name"],
                    start_mode=s.get("start_mode", "auto"),
                    was_running=s.get("was_running", False),
                )
                for s in data.get("services", [])
            ],
        )
