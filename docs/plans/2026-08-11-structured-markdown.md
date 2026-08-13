# BETAv27 Structured Markdown Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Исключить повреждение Markdown, Patchouli-кодов, тегов и ссылок при переводе книг и выпустить BETAv27.

**Architecture:** Lossless-токенизатор отделяет неизменяемый синтаксис от переводимого текста. Сервис переводит только текстовые узлы, кэширует их с контекстом страницы и программно собирает исходную структуру. Markdown-проверки вынесены в отдельный модуль форматов.

**Tech Stack:** Python 3, PySide6, pytest, requests, PyInstaller.

---

### Task 1: Зафиксировать реальные регрессии тестами

**Files:**
- Create: `tests/test_rich_text_format.py`
- Modify: `tests/test_markdown_safety.py`
- Modify: `tests/test_lmstudio.py`
- Modify: `tests/test_regex_safety.py`

1. Добавить corpus-тесты для экранированных звёздочек, Patchouli-цветов,
   GuideME-тегов, Markdown-ссылок и `GUI/gui`.
2. Проверить, что модель получает только видимый текст без служебных маркеров.
3. Проверить контекстный кэш и условный JSON-режим LM Studio.
4. Запустить новые тесты и подтвердить ожидаемые падения.

### Task 2: Реализовать lossless-модуль форматированного текста

**Files:**
- Create: `mineai/formats/__init__.py`
- Create: `mineai/formats/rich_text.py`

1. Реализовать однопроходный токенизатор с неизменяемыми и текстовыми частями.
2. Реализовать побайтную обратную сборку.
3. Добавить проверку, запрещающую переводному текстовому узлу вводить новую
   разметку или управляющие коды.
4. Запустить corpus-тесты.

### Task 3: Интегрировать перевод текстовых узлов

**Files:**
- Modify: `mineai/engines/service.py`
- Modify: `mineai/cache.py`
- Modify: `mineai/processors/jar.py`

1. Добавить `translate_formatted_dict()`.
2. Использовать контекст файла и индекс узла в ключе кэша/дедупликации.
3. Перевести Markdown и JSON-книги на новый путь.
4. Передавать `prompt_type="books"` всем книжным запросам.
5. Проверить одинаковый путь для локального ИИ и Google.

### Task 4: Вынести Markdown-логику

**Files:**
- Create: `mineai/formats/markdown.py`
- Modify: `mineai/processors/selection.py`
- Modify: `mineai/processors/jar.py`

1. Перенести разбор и проверку структуры Markdown без изменения публичного API.
2. Оставить совместимые re-export-функции для анализатора и оценщика.
3. Проверить совпадение количества строк анализа и выполнения.

### Task 5: Укрепить транспорт и остаточную защиту

**Files:**
- Modify: `mineai/engines/lmstudio.py`
- Modify: `mineai/text_processing.py`

1. Включать JSON response format для пакетных запросов LM Studio.
2. Сделать защиту технических терминов регистронезависимой на старом пути.
3. Запустить целевые тесты.

### Task 6: Выпустить BETAv27

**Files:**
- Modify: `mineai/__init__.py`
- Modify: `CHANGELOG.md`
- Create: `MineAI_Translator_Beta27.spec`

1. Запустить весь pytest-набор.
2. Прогнать corpus-проверку на тестовой сборке без записи архивов.
3. Установить версию `10.0.0 - BETAv27` и актуализировать changelog.
4. Собрать `dist/MineAI_Translator_Beta27.exe`.
5. Выполнить smoke-запуск EXE и вычислить SHA-256.

