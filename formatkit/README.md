# FormatKit 1.0.0-beta32

FormatKit — независимая встраиваемая библиотека безопасного разбора текстовых
форматов Minecraft. Она не сканирует `.minecraft`, не открывает JAR/ZIP, не
вызывает переводчики и не выбирает выходной архив. Всё это остаётся задачей
вызывающего приложения.

## Контракт

Вызывающая программа передаёт логический путь, уже декодированный английский
текст и целевую locale. `FormatRegistry` выбирает адаптер и возвращает
`TranslationPlan`. Внешний переводчик получает только `TranslationUnit.payload`.
После перевода `apply()` восстанавливает исходные ссылки, теги, игровые коды,
переносы и возвращает проверенный текст вместе с рекомендуемым `target_path`.

```python
from formatkit import FormatRegistry

registry = FormatRegistry.default()
plan = registry.plan(
    "assets/ae2/ae2guide/index.md",
    decoded_english_text,
    "ru_ru",
)

payloads = {unit.id: unit.payload for unit in plan.units}
translations = external_translator(payloads)
result = plan.apply(translations)

assert result.validation.ok
target_path = result.target_path
target_text = result.text
```

Если перенос документа в locale-каталог меняет базовый путь относительных
ресурсов, `relocated_dependencies()` возвращает пары исходного и целевого
логических путей. Библиотека ничего не читает и не копирует: вызывающее
приложение проверяет наличие исходного ресурса и само добавляет его в output.

`apply_resilient()` дополнительно сохраняет все корректные единицы, а для
единиц, нарушивших структуру, возвращает английский оригинал и причины отказа.
Это позволяет не терять целую страницу из-за одного ответа модели.

## Встроенные адаптеры

- `guideme-v2`: Markdown/MDX GuideME, обязательный каталог `_ru_ru`, сцены,
  импорты структур, таблицы, ссылки и теги;
- `ie-manual-v1`: текстовые страницы Immersive Engineering, включая
  `<link;target;label>`, `<&...>`, `<np>`, `<br>` и цветовые коды;
- `markdown-v2`: остальные книжные `.md`, `.markdown` и `.txt`, включая
  braced-ссылки `{label|target}` из книг Alex's Caves/Citadel.
- `properties-v1`: локализованные `key=value`/`key:value` файлы `.lang`;
- `xml-text-v1`: текстовые узлы локализованных XML без изменения тегов,
  атрибутов, entities, отступов и переносов.
- `heracles-quest-v1`: квесты Heracles/Odyssey Quests из
  `config/heracles/quests`; переводятся только компоненты отображения,
  описания, безопасные подписи задач/наград и названия групп, а JSON ID,
  команды, NBT, зависимости, условия и награды остаются неизменными;
- `heracles-groups-v1` и `heracles-tutorial-v1`: lossless-перевод
  `groups.txt` и видимого текста `tutorial.html` без изменения HTML-разметки.
- `modonomicon-json-v1`: lossless JSON книг Modonomicon, включая Gson-lenient
  многострочные строки; литеральный текст предназначен для datapack, а
  связанные lang-ключи возвращаются вызывающему приложению для resource pack.

Общий `formatkit.tokenizer` распознаёт коды `§`/`&`, Patchouli `$()`, printf,
placeholders, экранирование и другие неизменяемые игровые фрагменты.

## Гарантии

- no-op round-trip возвращает исходный Unicode-текст точно;
- диапазоны единиц не пересекаются;
- порядок и значения защищённых якорей проверяются;
- Markdown/IE skeleton подтверждается SHA-256 fingerprint до и после сборки;
- исходный стиль `LF`, `CRLF` или `CR` не меняется;
- связанные `.snbt`, `.nbt`, изображения и другие нетекстовые ресурсы можно
  перенести вслед за документом без эвристик по названию мода;
- неизвестная или повреждённая структура приводит к явной ошибке, а не к
  записи потенциально сломанного файла.

Библиотека не зависит от PyQt, CustomTkinter, LM Studio, KoboldCpp или Google и
может импортироваться другими Python-инструментами отдельно от GUI MineAI.
