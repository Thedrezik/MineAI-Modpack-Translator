# Beta30 FormatKit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use test-driven-development task-by-task.

**Goal:** Выделить безопасный FormatKit, исправить AE2/GuideME и IE manual и
выпустить проверенную Beta30.

**Architecture:** Независимый `formatkit` принимает только logical path и text,
строит span-based TranslationPlan и возвращает валидированный target text.
MineAI адаптирует этот контракт к существующим переводчикам и PackWriter.

**Tech Stack:** Python 3.12, stdlib dataclasses/re/hashlib/json, unittest,
PyInstaller, реальные Minecraft JAR как read-only fixtures.

---

### Task 1: Контракт FormatKit

**Files:** Create `formatkit/contracts.py`, `formatkit/registry.py`,
`tests/test_formatkit_contract.py`.

1. Написать тесты no-op round-trip, непересекающихся span и отказа при
   повреждённом anchor.
2. Запустить тесты и подтвердить ожидаемый import failure.
3. Реализовать минимальные dataclass и registry.
4. Повторить тесты до PASS.

### Task 2: GuideME adapter

**Files:** Create `formatkit/adapters/guideme.py`, modify
`mineai/processors/book_paths.py`, test `tests/test_formatkit_guideme.py`.

1. Зафиксировать падающие тесты для `_ru_ru`, `ImportStructure`, `ItemLink`,
   Markdown links и таблиц.
2. Реализовать target path и lossless tokenizer.
3. Проверить no-op byte identity и неизменность всех ссылок.

### Task 3: IE manual adapter

**Files:** Create `formatkit/adapters/ie_manual.py`, test
`tests/test_formatkit_ie_manual.py`.

1. Воспроизвести реальные строки с `Engineer's Workbench`, `<&recipe>`,
   `<np>` и `<br>`.
2. Проверить, что ID/разделители защищены, а подпись ссылки переводима.
3. Реализовать адаптер и fail-closed validation.

### Task 4: Интеграция книг

**Files:** Modify `mineai/processors/jar.py`, `analyzer.py`, `estimator.py`,
`mineai/engines/service.py`; test `tests/test_formatkit_integration.py`.

1. Написать тест единого плана для анализа/estimate/process.
2. Подключить registry и перевод payload целиком.
3. Изолировать кэш Beta30 adapter scope.
4. Проверить пустые `The`/`a`/`an`, `'s`, обычные слова с конечной точкой.

### Task 5: IE metadata и архивная валидация

**Files:** Modify `mineai/processors/jar.py`, `mineai/output/pack_writer.py`;
test `tests/test_formatkit_pack_validation.py`.

1. Зафиксировать отсутствующие `manual.immersiveengineering.*` при выборе книг.
2. Добавить только зависимые ключи manual, не весь интерфейс мода.
3. Проверить target paths, ссылки и отсутствие duplicate ZIP entries.

### Task 6: Реальные сборки

1. Read-only найти JAR во всех `PrismLauncher/instances/*/minecraft/mods`.
2. Для каждого поддержанного файла выполнить plan + no-op apply + validation.
3. Для AE2/IE выполнить тестовые подстановки без записи в сборки.
4. Сохранить агрегированный отчёт в `build/formatkit_realworld_report.json`.

### Task 7: Релиз

**Files:** Modify `mineai/__init__.py`, `CHANGELOG.md`; create
`MineAI_Translator_Beta30.spec`.

1. Запустить полный `unittest discover`.
2. Собрать PyInstaller EXE.
3. Проверить запуск/import/version и hash артефакта.
4. Не коммитить автоматически: рабочее дерево содержит изменения пользователя.

