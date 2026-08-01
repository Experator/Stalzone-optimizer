from dataclasses import dataclass, field
from typing import List, Optional

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
    "disk", "services", "visual", "game",
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
