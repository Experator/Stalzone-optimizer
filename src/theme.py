class Colors:
    # Base
    BG_DARKEST = "#ffffff"       
    BG_DARK = "#1a1d4e"          
    BG_PANEL = "#131233"         
    BG_PANEL_LIGHT = "#17163B"   
    BG_INPUT = "#ff0800"         

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
    AMBER_LIGHT = "#d19a0d"
    ORANGE = "#ea580c"
    RED = "#ef4444"
    RED_DARK = "#db4040"
    EMERALD = "#103db9"
    EMERALD_DARK = "#2C2EA0"
    CYAN = "#06b6d4"
    YELLOW = "#eab308"

    # Tier colors
    TIER_LOW = "#ef4444"
    TIER_MID = "#ffcf31"
    TIER_HIGH = "#10b981"
    TIER_ENTHUSIAST = "#06b6d4"
    TIER_UNKNOWN = "#7b7b8a"

    # Impact colors
    IMPACT_LOW = "#7a7a99"
    IMPACT_MEDIUM = "#ac781f"
    IMPACT_HIGH = "#326e22"
    IMPACT_CRITICAL = "#e94d4d"

    # Log level colors (NEW)
    LOG_DEBUG = "#71717a"
    LOG_INFO = "#06b6d4"
    LOG_SUCCESS = "#10b981"
    LOG_WARNING = "#f59e0b"
    LOG_ERROR = "#ef4444"
    LOG_CRITICAL = "#dc2626"

    # Process category colors (NEW)
    PROC_BROWSER = "#3b82f6"
    PROC_COMMUNICATION = "#8b5cf6"
    PROC_CLOUD = "#06b6d4"
    PROC_MEDIA = "#ec4899"
    PROC_GAME = "#10b981"
    PROC_SYSTEM = "#71717a"
    PROC_OTHER = "#a1a1aa"

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
    "processes": "Процессы",
    "system": "Система",
}

CATEGORY_ORDER = [
    "power", "cpu", "memory", "gpu", "network",
    "disk", "services", "visual", "game", "processes", "system",
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
    "processes": "📊",
    "system": "🔧",
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

PROCESS_CATEGORY_LABELS = {
    "browser": "Браузер",
    "communication": "Коммуникации",
    "cloud": "Облако",
    "media": "Медиа",
    "game": "Игра",
    "system": "Система",
    "other": "Другое",
}

PROCESS_CATEGORY_COLORS = {
    "browser": Colors.PROC_BROWSER,
    "communication": Colors.PROC_COMMUNICATION,
    "cloud": Colors.PROC_CLOUD,
    "media": Colors.PROC_MEDIA,
    "game": Colors.PROC_GAME,
    "system": Colors.PROC_SYSTEM,
    "other": Colors.PROC_OTHER,
}

PROCESS_ACTION_LABELS = {
    "kill": "Завершить",
    "lower_priority": "Снизить приоритет",
    "suspend": "Приостановить",
    "keep": "NONE",
}

FONT_FAMILY = "Arimo SemiBold"
FONT_MONO = "Arimo SemiBold"

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
    "log": (FONT_MONO, 10),
}
