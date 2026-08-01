from typing import List
from .models import (
    HardwareReport, TierAssessment, OptimizationToggle,
)

def _score_cpu(report: HardwareReport) -> int:
    cpu = report.cpu
    score = 0

    score += min(35, cpu.cores_physical * 5)

    if cpu.cores_logical >= 12:
        score += 5
    elif cpu.cores_logical >= 8:
        score += 3

    score += min(20, int(cpu.speed_ghz * 4))

    brand = cpu.brand.lower()
    if 'ryzen 9' in brand or 'core i9' in brand or 'ryzen 7 7' in brand:
        score += 10
    elif 'ryzen 5 5' in brand or 'ryzen 5 7' in brand or 'core i7' in brand:
        score += 6
    elif 'ryzen 5' in brand or 'core i5' in brand:
        score += 3
    return min(70, score)


def _score_ram(report: HardwareReport) -> int:
    gb = report.ram.total_gb
    if gb >= 32:
        return 20
    if gb >= 16:
        return 15
    if gb >= 12:
        return 10
    if gb >= 8:
        return 6
    return 2


def _score_gpu(report: HardwareReport) -> int:
    discrete = [g for g in report.gpus if not g.is_integrated]
    if not discrete:
        return 3  

    gpu = discrete[0]
    model = gpu.model.lower()

    vram_score = 0
    if gpu.vram_mb:
        v = gpu.vram_mb
        if v >= 16384:
            vram_score = 25
        elif v >= 12288:
            vram_score = 20
        elif v >= 8192:
            vram_score = 15
        elif v >= 6144:
            vram_score = 11
        elif v >= 4096:
            vram_score = 8
        else:
            vram_score = 4

    tier_score = 5  
    checks = [
        (("rtx 4090", "rx 7900"), 35),
        (("rtx 4080", "rtx 4070 ti", "rx 7800"), 30),
        (("rtx 4070", "rtx 3080", "rx 6800", "rx 7700"), 25),
        (("rtx 3060", "rtx 3070", "rx 6700", "rx 6600"), 18),
        (("rtx 2060", "rtx 2070", "gtx 1660", "gtx 1080"), 13),
        (("gtx 1060", "gtx 1650", "rx 580", "rx 570"), 8),
    ]
    for patterns, pts in checks:
        if any(p in model for p in patterns):
            tier_score = pts
            break

    return min(60, vram_score + tier_score)


def _score_disk(report: HardwareReport) -> int:
    if any(d.type == "NVMe" for d in report.disks):
        return 10
    if any(d.type == "SSD" for d in report.disks):
        return 6
    return 0


def assess_tier(report: HardwareReport) -> TierAssessment:
    cpu_score = _score_cpu(report)
    ram_score = _score_ram(report)
    gpu_score = _score_gpu(report)
    disk_score = _score_disk(report)
    score = min(100, cpu_score + ram_score + gpu_score + disk_score)

    if score >= 75:
        tier = "enthusiast"
    elif score >= 55:
        tier = "high"
    elif score >= 35:
        tier = "mid"
    else:
        tier = "low"

    tier_data = {
        "": ("",    
                "",
                20, 45, "low"),
        "mid": ("Mid-Range System",
                "",
                45, 75, "medium"),
        "high": ("High-End System",
                 "",
                 75, 120, "high"),
        "enthusiast": ("Enthusiast System",
                       "",
                       120, 240, "ultra"),
        "unknown": ("Unknown System",
                    "Не удалось определить характеристики.",
                    0, 0, "medium"),
    }

    label, desc, fps_min, fps_max, preset = tier_data[tier]

    bottlenecks = []
    strengths = []

    if gpu_score < 15:
        bottlenecks.append("GPU слабый")
    if ram_score < 10:
        bottlenecks.append("Недостаточно RAM — возможны фризы при подгрузке")
    if cpu_score < 25:
        bottlenecks.append("Слабый CPU — лимит кадровой частоты")
    if disk_score == 0:
        bottlenecks.append("HDD вместо SSD — (Может быть ошибка)")

    if cpu_score >= 40:
        strengths.append("Мощный CPU")
    if gpu_score >= 25:
        strengths.append("Сильный GPU")
    if ram_score >= 15:
        strengths.append("Достаточно RAM")
    if disk_score >= 6:
        strengths.append("Быстрый накопитель")

    return TierAssessment(
        tier=tier,
        score=score,
        label=label,
        description=desc,
        estimated_fps_min=fps_min,
        estimated_fps_max=fps_max,
        recommended_preset=preset,
        strengths=strengths,
        bottlenecks=bottlenecks,
    )

# Optimization 

def get_default_toggles(report: HardwareReport) -> List[OptimizationToggle]:
    """Return 27 optimization toggles with default enabled state based on hardware."""
    tier = assess_tier(report)
    is_low = tier.tier == "low"
    is_mid = tier.tier == "mid"
    ram_low = report.ram.total_gb < 16
    has_nvme = any(d.type == "NVMe" for d in report.disks)
    has_ssd = any(d.type in ("SSD", "NVMe") for d in report.disks)
    has_hdd = any(d.type == "HDD" for d in report.disks)

    def t(id, cat, title, desc, enabled, recommended, impact, admin, win_only):
        return OptimizationToggle(
            id=id, category=cat, title=title, description=desc,
            enabled=enabled, recommended=recommended, impact=impact,
            requires_admin=admin, windows_only=win_only,
        )

    return [
        # POWER
        t("power_plan", "power", "Схема питания: Максимальная производительность",
          "Переключает Windows на схему Best Performance. Убирает троттлинг CPU.",
          True, True, "high", True, True),
        t("timer_resolution", "power", "Таймер Windows: 1 мс",
          "timeBeginPeriod(1) снижает задержку таймера. Уменьшает input lag.",
          True, True, "medium", False, True),
        t("core_parking_off", "power", "Отключить Core Parking",
          "Все ядра CPU всегда активны. Устраняет микрозадержки ядер.",
          True, True, "medium", True, True),

        # CPU
        t("cpu_affinity_physical", "cpu", "Affinity: только физические ядра",
          "Привязка игры к физическим ядрам. Убирает переключение потоков (SMT/HT).",
          True, True, "medium", False, True),
        t("process_priority_high", "cpu", "Приоритет процесса: HIGH",
          "Повышает приоритет планировщика. Преимущество над фоновыми задачами.",
          True, True, "medium", False, True),
        t("disable_game_dvr", "cpu", "Отключить Xbox Game DVR",
          "Отключает фоновую запись клипов Xbox, которая потребляет CPU/GPU и диск.",
          True, True, "medium", True, True),

        # MEMORY
        t("ram_standby_cleanup", "memory", "Очистка Standby памяти",
          "Очищает standby memory. Освобождает кэш Windows под игру.",
          ram_low, ram_low, "high", True, True),
        t("ram_periodic_cleanup", "memory", "Периодическая очистка RAM (каждые 5 мин)",
          "Автоматически очищает standby memory во время игры по таймеру.",
          is_low or ram_low, is_low or ram_low, "medium", True, True),
        t("disable_swap_file", "memory", "Отключить файл подкачки (при 16+ ГБ)",
          "При достаточной RAM убирает pagefile. ВНИМАНИЕ: только если RAM >= 16 ГБ!",
          report.ram.total_gb >= 16, report.ram.total_gb >= 24, "high", True, True),
        t("large_system_cache", "memory", "LargeSystemCache",
          "Увеличивает кэш файловой системы в RAM. Полезно для HDD-систем.",
          not has_nvme, not has_nvme, "low", True, True),

        # GPU
        t("gpu_power_management", "gpu", "GPU: режим максимальной производительности",
          "Переводит видеокарту в режим Prefer Maximum Performance.",
          True, True, "high", False, True),
        t("disable_hardware_acceleration", "gpu", "Отключить HWA в браузерах/Discord",
          "Снимает нагрузу на GPU с Chrome, Edge, Discord. Освобождает видеопамять.",
          is_low or is_mid, is_low, "medium", False, False),
        t("tdr_delay_increase", "gpu", "Увеличить TdrDelay (таймаут GPU)",
          "TdrDelay=10 сек. Убирает ложные сбои 'Display driver stopped responding'.",
          True, True, "low", True, True),

        # NETWORK
        t("disable_nagle", "network", "Отключить алгоритм Нэгла (TCP)",
          "TcpAckFrequency=1 + TCPNoDelay=1. Уменьшает сетевую задержку в StalZone.",
          True, True, "medium", True, True),
        t("flush_dns", "network", "Очистить DNS-кэш",
          "ipconfig /flushdns. Обновление маршрутов до серверов StalZone.",
          True, True, "low", False, True),
        t("network_throttling_off", "network", "Отключить Network Throttling",
          "NetworkThrottlingIndex=ffffffff. Снимает лимит мультимедиа на сетевой стек.",
          True, True, "low", True, True),

        # DISK
        t("disable_indexing", "disk", "Отключить индексирование диска",
          "Останавливает SearchIndexer. Снижает фоновую I/O нагрузку на диск.",
          not has_nvme, not has_nvme, "low", True, True),
        t("defrag_hdd", "disk", "Дефрагментация HDD (если есть)",
          "Запускает defrag для HDD. Для SSD/NVMe пропускается.",
          has_hdd, has_hdd, "medium", True, True),
        t("ssd_trim", "disk", "TRIM для SSD/NVMe",
          "Оптимизация SSD через retrim. Поддерживает скорость записи.",
          has_ssd, has_ssd, "low", True, True),

        # SERVICES
        t("disable_sysmain", "services", "Отключить SysMain (Superfetch)",
          "SysMain предзагружает приложения, но на SSD бесполезен и мешает игре.",
          has_ssd, has_ssd, "medium", True, True),
        t("disable_diagnostic_tracking", "services", "Отключить DiagTrack (телеметрия)",
          "Останавливает телеметрию Windows. Снижает фоновую нагрузку.",
          True, True, "low", True, True),
        t("disable_windows_search", "services", "Отключить Windows Search (временно)",
          "Только на время игры. Освобождает CPU и диск. После — возвращаем.",
          is_low, is_low, "low", True, True),

        # VISUAL
        t("visual_effects_performance", "visual", "Визуальные эффекты: Best Performance",
          "Отключает анимации Windows, тени, сглаживание. Чисто игровой режим.",
          is_low, is_low, "low", True, True),
        t("disable_transparency", "visual", "Отключить прозрачность",
          "Убирает акриловые эффекты. Снижает нагрузку на DWM.",
          is_low or is_mid, is_low, "low", False, True),

        # GAME
        t("kill_background_apps", "game", "Закрыть фоновые приложения",
          "Завершает OneDrive, Skype, Spotify, и т.п. перед запуском игры.",
          True, True, "medium", False, False),
        t("game_mode_on", "game", "Включить Windows Game Mode",
          "Game Mode приоритизирует игру в планировщике.",
          True, True, "low", False, True),
        t("hardware_gpu_scheduler", "game", "Включить HAGS (Hardware GPU Scheduling)",
          "Аппаратное планирование GPU. Снижает нагрузку CPU, улучшает 1% low FPS.",
          True, True, "medium", True, True),
    ]
