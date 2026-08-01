class Colors:
    # Base
    BG_DARKEST = "#0a0a0b"       
    BG_DARK = "#161618"          
    BG_PANEL = "#1c1c1f"         
    BG_PANEL_LIGHT = "#242428"   
    BG_INPUT = "#0d0d0f"         

    # Borders
    BORDER = "#2a2a2e"
    BORDER_LIGHT = "#3a3a3e"

    # Text
    TEXT_PRIMARY = "#f4f4f5"
    TEXT_SECONDARY = "#a1a1aa"
    TEXT_MUTED = "#71717a"

    # Accents
    AMBER = "#dad4d4"
    AMBER_DARK = "#d97706"
    AMBER_LIGHT = "#ddbe6e"
    ORANGE = "#ea580c"
    RED = "#ef4444"
    RED_DARK = "#b91c1c"
    EMERALD = "#10b981"
    EMERALD_DARK = "#059669"
    CYAN = "#06b6d4"
    YELLOW = "#eab308"

    # Tier colors
    TIER_LOW = "#ef4444"
    TIER_MID = "#f59e0b"
    TIER_HIGH = "#10b981"
    TIER_ENTHUSIAST = "#06b6d4"
    TIER_UNKNOWN = "#71717a"

    # Impact colors
    IMPACT_LOW = "#71717a"
    IMPACT_MEDIUM = "#b99c6a"
    IMPACT_HIGH = "#e94d4d"
    IMPACT_CRITICAL = "#e94d4d"

# Category metadata
CATEGORY_LABELS = {
    "power": "Питание",
    "cpu": "Процессор",
    "memory": "Память",
    "gpu": "Видеокарта",
    "network": "Сеть",
    "disk": "Диск",
    "services": "Сервисы",
    "visual": "Визуальные",
    "game": "Игра",
}

CATEGORY_ORDER = [
    "power", "cpu", "memory", "gpu", "network",
    "disk", "services", "visual", "game",
]

CATEGORY_ICONS = {
    "power": "⚡",
    "cpu": "🔲",
    "memory": "💾",
    "gpu": "🎮",
    "network": "🌐",
    "disk": "💿",
    "services": "⚙",
    "visual": "🖥",
    "game": "🎯",
}

TIER_INFO = {
    "low": {
        "label": "LOW-END",
        "color": Colors.TIER_LOW,
        "description": "Ограниченное железо. Агрессивная оптимизация.",
    },
    "mid": {
        "label": "MID-RANGE",
        "color": Colors.TIER_MID,
        "description": "Сбалансированная система. Средние настройки.",
    },
    "high": {
        "label": "HIGH-END",
        "color": Colors.TIER_HIGH,
        "description": "Мощная система. Высокие настройки.",
    },
    "enthusiast": {
        "label": "ENTHUSIAST",
        "color": Colors.TIER_ENTHUSIAST,
        "description": "Топовое железо. Ультра настройки.",
    },
    "unknown": {
        "label": "UNKNOWN",
        "color": Colors.TIER_UNKNOWN,
        "description": "Характеристики не определены.",
    },
}

IMPACT_COLORS = {
    "low": Colors.IMPACT_LOW,
    "medium": Colors.IMPACT_MEDIUM,
    "high": Colors.IMPACT_HIGH,
    "critical": Colors.IMPACT_CRITICAL,
}

IMPACT_LABELS = {
    "low": "НИЗКИЙ",
    "medium": "СРЕДНИЙ",
    "high": "ВЫСОКИЙ",
    "critical": "КРИТИЧ",
}

# Fonts
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

FONTS = {
    "title": (FONT_FAMILY, 20, "bold"),
    "heading": (FONT_FAMILY, 16, "bold"),
    "subheading": (FONT_FAMILY, 13, "bold"),
    "body": (FONT_FAMILY, 12),
    "body_bold": (FONT_FAMILY, 12, "bold"),
    "small": (FONT_FAMILY, 11),
    "tiny": (FONT_FAMILY, 10),
    "mono": (FONT_MONO, 11),
    "mono_small": (FONT_MONO, 10),
    "tier_big": (FONT_FAMILY, 24, "bold"),
    "score_big": (FONT_FAMILY, 28, "bold"),
}
