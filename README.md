# STALZONE OPTIMIZER

**Десктопное приложение для оптимизации игры StalCraft / StalZone.**

Приложение анализирует характеристики ПК, определяет производственный тир, и применяет 37 методов оптимизации: CPU affinity, RAM cleanup, GPU power management, network tweaks, диск, сервисы, визуальные эффекты и игровые настройки.

## Возможности

- **Детальный анализ ПК** — CPU, GPU, RAM, диски, ОС, дисплей
- **Оценка тира производительности** — Low / Mid / High / Enthusiast (score 0-100)
- **37 оптимизаций** в 10 категориях:
  - Питание (power plan, timer resolution, core parking)
  - Процессор (affinity, priority, Game DVR)
  - Память (standby cleanup, periodic cleanup, swap, cache)
  - Видеокарта (power management, HWA, TdrDelay)
  - Сеть (Nagle off, DNS flush, network throttling)
  - Диск (indexing, defrag, TRIM)
  - Сервисы (SysMain, DiagTrack, Windows Search)
  - Визуальные (effects, transparency)
  - Игра (kill bg apps, Game Mode, HAGS)
  - Система (Defender, Windows update, Cortana)
- **Мониторинг загрузки ресурсов** — CPU/RAM/Swap, загрузка по ядрам, статус процесса игры
- **Кастомизация оптимизаций** — включение/отключение каждого метода
- **Прямое применение** — оптимизации применяются непосредственно из GUI
- **Бэкап изменений** — возврат всех изменений при необходимости

## Требования

- **ОС:** Windows 10/11
- **Python:** 3.10+ (для запуска из исходников)
- **Права:** Администратор (для большинства оптимизаций)

## Запуск из релиза
- Перейти в вкладку релизов https://github.com/Experator/Stalzone-optimizer/releases
- Скачать актуальную версию программы - stalzone.optimizer.zip
<img width="917" height="437" alt="image" src="https://github.com/user-attachments/assets/fa9d95f3-95c9-47f8-991c-49e8a81e4c84" />

## Запуск из исходников

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Запустить
python main.py
```

## Компиляция в .exe (Windows)

```cmd
# Двойной клик по build.bat ИЛИ:
build.bat
```

После сборки: `dist\stalzone optimizer.exe` — автономный исполняемый файл, работает на любом Windows ПК без установленного Python.

## Компиляция в Linux/macOS

```bash
chmod +x build.sh
./build.sh
```

Результат: `dist/stalzone optimizer`

## Использование

### Вкладка «Обзор»
- Просмотр характеристик ПК
- Оценка тира производительности (score, FPS, сильные стороны, узкие места)
- Мониторинг CPU/RAM в реальном времени

### Вкладка «Оптимизации»
- 37 переключателей в 10 категориях
- Кнопки: «Только рекомендованные», «Включить все», «Выключить все»
- Каждый тоггл показывает: impact, требуется ли admin, рекомендован ли

### Вкладка «Настройки»
- Имена процессов игры
- Приоритет процесса (Above Normal / High / Realtime)
- Режим CPU Affinity (физические / все ядра)
- Разрешение таймера (0.5–1.5 мс)
- Интервал очистки RAM (60–900 сек)
- Агрессивная очистка RAM

### Вкладка «Процессы»
- Кнопки обновить и закрыть фоновые приложения
- Мини удобный диспетчер задач с понятным описанием приложений
- Удобное регулирование фоновых процессов и снижение приоритетов

### Нижняя панель
- **«Применить оптимизации»** — применяет все включенные оптимизации
- **«Отменить изменения»** — откатывает изменения

## Примечания

- Большинство оптимизаций работают **только на Windows** (powercfg, reg, sc, wmic)
- На Linux/macOS приложение запускается, показывает характеристики, но оптимизации пропускаются
- Для полной функциональности запускайте от имени администратора


# ВАЖНО
**При появлении фризов в игре**

<img width="486" height="148" alt="image" src="https://github.com/user-attachments/assets/680d2e36-4313-456b-813e-b1c0a2b77f5f" />

В диспетчере задач поставить обычный приоритет для stalzone.exe


Так же стоит отключить при фризах следующие параметры: 

<img width="484" height="145" alt="image" src="https://github.com/user-attachments/assets/3fc0fa97-931b-4738-8e68-2ed8086b4aa7" />
<img width="602" height="45" alt="image" src="https://github.com/user-attachments/assets/df0592e6-4aa1-4ecd-906e-9f3ccedb2b41" />

Так же рекомендуется поставить параметр CPU Affinity -> все логические ядра

<img width="410" height="47" alt="image" src="https://github.com/user-attachments/assets/71f7713b-d9c3-4839-a19f-52a6ad05a864" />



  
