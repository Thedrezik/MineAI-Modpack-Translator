# MineAI-FormatKit

[English](README.md) | [**Русский**](README_RU.md)

Безопасные, встраиваемые адаптеры форматов для **MineAI-Modpack-Translator** и других инструментов перевода Minecraft.

FormatKit — это SDK/библиотека, а не самостоятельный переводчик. Вызывающее приложение само выбирает и сканирует `.minecraft`, сборку, JAR, ресурс-пак, каталог квестов или отдельный файл, после чего передаёт FormatKit логический путь и декодированный текст. FormatKit отвечает только за определение формата, безопасное выделение текста, защиту технических фрагментов, точечную реконструкцию и структурную валидацию.

```text
host discovery / file IO / UI / translation provider
                    ↓
              path + source text
                    ↓
             FormatKit analyze
                    ↓
       TranslationPlan + TranslationUnit[]
                    ↓
          внешний сервис перевода
                    ↓
             FormatKit apply
                    ↓
    проверенный текст + optional target path
                    ↓
          host packaging/output policy
```

## Граница ответственности

| FormatKit отвечает за | Вызывающее приложение отвечает за |
| --- | --- |
| определение формата переданного пути | выбор/сканирование `.minecraft`, сборок, папок и файлов |
| безопасное выделение `TranslationUnit` | выбор LLM/API/provider и prompts |
| защиту placeholders/runtime-markup | cache, batching, concurrency, progress и ETA |
| format-defined target-path hints | GUI и пользовательский workflow |
| реконструкцию и structural validation | resource-pack vs JAR vs loose-file output policy |
| adapter capabilities и diagnostics | retry/skip/report policy |
| переиспользуемую проверку безопасности JAR | решение когда и как упаковывать архивы |

FormatKit сам никогда не вызывает OpenRouter, Google, DeepL или локальную LLM.

## Основные принципы

1. **Структуру задаёт канонический английский источник.** `en_us.json`, `en_us.snbt`, локализуемые book trees и оригинальные MDX/Markdown определяют текущую структуру. Existing target — только необязательный источник текста, но не structural truth.
2. **Контейнеры не являются текстовыми форматами.** В JAR могут быть тысячи JSON, но это не означает тысячи переводимых файлов.
3. **Адаптеры намеренно узкие.** Формат считается поддержанным только после того, как реальный corpus доказал, какие поля player-visible, а какие технические.
4. **Точечная реконструкция.** Где возможно, заменяются только точные source spans; остальной синтаксис, порядок и whitespace остаются byte-identical.
5. **Технические фрагменты обязаны быть защищены.** Placeholders, runtime directives, URL, границы форматирования, component metadata и technical IDs должны пережить перевод.
6. **Fail closed.** Invalid syntax, конфликтующие duplicate keys, потерянные placeholders, неподдерживаемые structured values, изменение signed JAR и неоднозначные archive entries отклоняются, а не угадываются.

## Текущая проверенная база

Полный аудит `mods/` из FTB Evolution сейчас покрывает **518 уникальных читаемых JAR**.

- 417 canonical `assets/<namespace>/lang/en_us.json`;
- около 90.5k top-level locale entries;
- 417/417 canonical locales проходят byte-exact identity reconstruction;
- 417/417 проходят synthetic translation + structural validation;
- 235 реальных EN→RU sibling locale pairs проходят merge planning без аварии;
- 1,247 Patchouli EN category/entry JSON;
- 779 GuideME Markdown pages;
- 187 Oracle Index MDX/meta files;
- 117 Immersive Engineering manual files;
- 27,457 advancement JSON, включая 279 direct literal title/description fields, уже покрытых advancement adapter.

Instance/config corpus дополнительно доказал точные runtime locale roots для Collapsible Groups и JAOPCA под `config/.../lang/en_us.json`.

Third-party corpora используются локально для regression testing и не распространяются из репозитория, если права на redistribution неочевидны.

## Встроенные адаптеры

`FormatRegistry.default()` сейчас регистрирует **15** built-ins:

| Adapter | Доказанная source surface |
| --- | --- |
| `GuideMeMarkdownAdapter` | AE2-style GuideME Markdown source trees |
| `DataDrivenGuideMeMarkdownAdapter` | `assets/<ns>/guides/<guide-id>/.../*.md` |
| `CollapsibleGroupsLangJsonAdapter` | bundled `assets/collapsible_groups/group_lang/en_us.json` |
| `CollapsibleGroupsConfigLangJsonAdapter` | deployed `config/collapsiblegroups/lang/en_us.json` |
| `JaopcaConfigLangJsonAdapter` | `config/jaopca/lang/en_us.json` |
| `CrashAssistantLocalizationAdapter` | `crash_assistant_localization/en_us.json` |
| `MinecraftLangJsonAdapter` | canonical Minecraft `assets/<ns>/lang/en_us.json` |
| `MinecraftAdvancementTextAdapter` | direct advancement display title/description Components |
| `MinecraftTextComponentAdapter` | выбранные advancement/loot/enchantment JSON Components |
| `FtbQuestsLangAdapter` | `config/ftbquests/quests/lang/en_us.snbt` |
| `FtbQuestsChapterAdapter` | allow-listed direct chapter SNBT text |
| `PatchouliBookJsonAdapter` | локализованные Patchouli category/entry JSON |
| `OracleIndexMdxAdapter` | Oracle Index / ModdedMC.wiki-style MDX |
| `OracleIndexMetaJsonAdapter` | Oracle Index `_meta.json` navigation labels |
| `ImmersiveEngineeringManualAdapter` | `assets/<ns>/manual/en_us/*.txt` |

Публичный SDK также экспортирует `LocaleMergePlanner`, `FtbQuestsLocaleMergePlanner`, `JarContainer`, diagnostics/capabilities и low-level adapter classes.

## Безопасность Minecraft locale

### Обычные строки

`MinecraftLangJsonAdapter` сохраняет top-level keys, порядок и source formatting, переводя только безопасные player-visible значения. Защищается доказанный реальными модами runtime syntax:

- Java/printf и MessageFormat placeholders;
- vanilla Minecraft `§` formatting codes;
- FancyMenu `$$variables`;
- Hexerei `%kkey...%` keybind tokens;
- slash-команды с Unicode-aware boundary check;
- URL;
- FTB `&...` formatting boundaries и `{image:...}` directives;
- BMP Private Use Area glyphs из custom fonts;
- доказанный Malum codex runtime markup.

Обычные фразы вроде `300% Larger`, `75% chance`, `and/or`, `стрелками/Tab` или `Документация/Wiki` не превращаются в ложные placeholders/команды.

### Duplicate locale keys

Повторяющиеся обычные locale keys принимаются только если каждое occurrence является строкой с одинаковым decoded value. Наружу выдаётся одна логическая translation unit, а изменённый перевод записывается во все идентичные source occurrences. Конфликтующие duplicate values по-прежнему fail-closed.

### Строгие structured locale Components

Structured values **не** переводятся рекурсивно. Поддерживаются только независимо доказанные Component schemas:

- GAG root-list schema с visible `text`/`extra` leaves и `strikethrough` metadata;
- Tempad root-list schema с visible `text` leaves и immutable `color`/`index` metadata.

В translation units попадают только видимые text leaves. Array order, object shape и technical fields остаются структурой канонического английского. Неизвестные arrays/objects продолжают появляться в diagnostics `unsupported_non_string_keys`.

### Serialized Components внутри обычных locale-строк

Actually Additions и Iron Furnaces доказали ещё один узкий случай: обычное locale string value может само содержать сериализованный JSON Component array. FormatKit распознаёт только доказанную схему, отображает внутренние decoded text nodes обратно на точные outer JSON-string spans и отдаёт на перевод только видимый текст, сохраняя `%s`, colors, punctuation и технические данные `clickEvent`.

### Переиспользование existing target

`LocaleMergePlanner` всегда строит output по каноническому английскому источнику:

| Mode | Поведение |
| --- | --- |
| `append` | Переиспользует технически безопасный target; переводит missing/empty/English-identical/unsafe units. |
| `force` | Игнорирует existing target и заново переводит каждую canonical source unit. |
| `skip` | Сохраняет любой technically safe existing target; переводит только absent/empty/unsafe units. |

Malformed optional target locales никогда не заменяют canonical source structure. `force` вообще не зависит от target text; append/skip могут отказаться от malformed reuse data, сохранив parse error для diagnostics.

Для structured Components target reuse сравнивает semantic technical shape, а не несущественные различия whitespace/object-key ordering.

## Runtime config locales

Два config-root формата теперь поддерживаются, потому что реальные моды доказали, что это runtime locale catalogs и что имя target-файла задаётся locale code:

```text
config/collapsiblegroups/lang/en_us.json
→ config/collapsiblegroups/lang/ru_ru.json

config/jaopca/lang/en_us.json
→ config/jaopca/lang/ru_ru.json
```

`CollapsibleGroupsConfigLangJsonAdapter` и `JaopcaConfigLangJsonAdapter` наследуют hardened public Minecraft locale pipeline, включая target merge и structured/runtime protections. Произвольные `config/<mod>/lang/en_us.json` автоматически **не** поддерживаются.

У Collapsible Groups отдельно существует bundled runtime catalog `assets/collapsible_groups/group_lang/en_us.json`, который обрабатывает `CollapsibleGroupsLangJsonAdapter`.

## Markdown, guides и manuals

### GuideME

GuideME adapters сохраняют front matter, Markdown structure, inline/fenced code, link destinations, images и GuideME/HTML/MDX components. Реальные границы `*italic*` и `**bold**` защищаются, но broad underscore heuristics не включаются, потому что они конфликтуют с resource IDs вроде `data_center`.

Типовые target layouts:

```text
assets/ae2/ae2guide/page.md
→ assets/ae2/ae2guide/_ru_ru/page.md

assets/demo/guides/demo/guide/page.md
→ assets/demo/guides/demo/guide/_ru_ru/page.md
```

### Oracle Index

`OracleIndexMdxAdapter` использует hardened GuideME/Markdown layer для `.mdx` и исключает `.translated/<locale>/` из source discovery. `OracleIndexMetaJsonAdapter` поддерживает и legacy string labels, и новую nested `{name, icon, ...}` metadata shape, оставляя navigation keys/icons immutable.

### Immersive Engineering manual

`ImmersiveEngineeringManualAdapter` переводит plain prose и доказанные visible branches IE manual directives, сохраняя link targets/anchors, config keys, keybinds, formatting codes и другую machine syntax.

## Patchouli

`PatchouliBookJsonAdapter` переводит только доказанные localized display fields:

- top-level `name` / `description`;
- page `text`, `title`, `heading`, `name`;
- custom-page string keys, заканчивающиеся на `.text` или `.heading`.

Patchouli `$(...)` / `/$` runtime markup защищается точно. Machine fields вроде page type, icons, recipes, items, rituals, advancements, entities, anchors и resource locations остаются immutable.

`link_text` пока сознательно не обобщён: реальные corpora доказали, что там может находиться и literal player text, и translation key, поэтому простое добавление в whitelist было бы небезопасно.

## Special locales

`CrashAssistantLocalizationAdapter` обрабатывает `crash_assistant_localization/en_us.json` и дополнительно защищает CrashAssistant `$...$` macros, HTML tags и URL.

## FTB Quests

`FtbQuestsLangAdapter` разбирает canonical `en_us.snbt` locale и умеет работать с nested JSON Components, закодированными внутри SNBT strings. `FtbQuestsChapterAdapter` отдаёт только доказанные direct chapter fields `feedback_message`, `description`, `minecraft:custom_name` и `minecraft:lore`.

`FtbQuestsLocaleMergePlanner` следует тому же canonical-English правилу, что и ordinary locale merge, и отклоняет unsafe existing target reuse при несовпадении protected fragments или unit layout.

Quest IDs, graph dependencies, coordinates, tasks/rewards и unrelated configuration остаются technical data.

## Безопасность JAR

`JarContainer` отделяет безопасность архива от разбора текста:

- обнаружение duplicate ZIP entries;
- обнаружение JAR signatures (`META-INF/*.SF`, `*.RSA`, `*.DSA`, `*.EC`);
- поиск поддерживаемых entries через `FormatRegistry`;
- CRC validation после rebuild;
- запрет изменения signed JAR с resource-pack/overlay как безопасной host strategy;
- явная **one-level** проверка nested JAR через `inspect_nested()` с тем же registry.

Nested inspection предназначен для discovery/diagnostics. FormatKit автоматически не переписывает nested JAR; packaging policy остаётся задачей host application.

## Public embedding API

```python
from mineai_formatkit import FormatKit

kit = FormatKit.default()
analysis = kit.analyze(
    "assets/demo/lang/en_us.json",
    source_text,
    target_locale="ru_ru",
)

if analysis.supported and analysis.ready:
    translations = {
        unit.id: translate_externally(unit.text)
        for unit in analysis.units
    }
    target_text = kit.apply(analysis, translations)
    target_path = analysis.target_path
```

`FormatAnalysis` предоставляет:

- `supported`, `ready`, `has_errors`;
- adapter name и стабильные `AdapterCapabilities`;
- `units` / `unit_count`;
- optional format-defined target path;
- structured diagnostics.

Low-level adapters остаются доступны через стабильный контракт:

```text
matches(path)
prepare(path, source_text) -> TranslationPlan
translate(unit.text) externally
apply(plan, translations) -> validated target text
```

## Сознательно неподдерживаемое / не обобщаемое

Это deliberate safety boundaries, а не случайные недоделки:

- arbitrary recursive JSON/SNBT translation;
- arbitrary structured locale arrays/objects вне доказанных schemas;
- generic `<...>` protection/translation;
- FancyMenu custom `§x/§y/§z` semantics без достаточного parser proof;
- hard-coded Modonomicon book strings в non-locale-specific source files;
- executable/template DSL content вроде произвольных KubeJS JavaScript strings;
- conflicting duplicate locale values;
- automatic nested-JAR mutation;
- arbitrary unproven config locale roots.

Unsupported data должно оставаться unsupported, пока canonical source corpora не докажут безопасные границы extraction/reconstruction.

## Матрица реальных corpus-проверок

| Corpus | Основной результат |
| --- | --- |
| AE2 GuideME | 125 canonical pages проходят byte-exact identity и structural synthetic reconstruction. |
| Rechiseled 1.2.5 | 3,656 ordinary locale values; безопасное создание нового target locale. |
| The Bumblezone | Natural-percent regression и stale/incomplete target evidence. |
| SecurityCraft | Одинаковые EN/RU counts могут скрывать key drift. |
| Refurbished Furniture | Signed JAR; in-place mutation должна блокироваться. |
| Create LTAB | Видимый текст существует также в structured advancement/loot Components. |
| FTB Evolution quests | 7,615 locale units + 60 direct chapter units; existing RU структурно устарел. |
| Genetics Resequenced | Patchouli + Oracle Index canonical source/target parity и markup damage evidence. |
| MPLOCmods v39 | Broad target-only malformed/duplicate/structured-locale negative evidence. |
| FTB Evolution instance/config | 5,964 files; FTB runtime syntax, GuideME emphasis, duplicate aliases, PUA glyphs и proven config locale roots. |
| FTB Evolution full mods corpus | 518 unique JAR; 417 canonical locales; полный identity/synthetic locale pass и corpus-proven hardening. |

Подробные измерения находятся в [`MOD_CORPUS_NOTES.md`](MOD_CORPUS_NOTES.md), модель парсеров и реконструкции — в [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Разработка

Runtime-код не требует обязательных third-party dependencies.

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

GitHub Actions запускает suite на Ubuntu и Windows с Python 3.10 и 3.13.

## Fixture и licensing policy

- Canonical original files определяют format behavior.
- Broken translations — negative regression evidence, а не source truth.
- Full third-party binaries остаются локальными, если redistribution rights неочевидны.
- Сам по себе успешный parsing недостаточен: support требует reconstruction и structural validation evidence.

## Roadmap

1. сохранять стабильным public SDK/adapter contract;
2. добавлять новые schemas только по canonical real-world corpora;
3. держать protection primitives узкими и corpus-proven;
4. добавлять новые quest/book/config formats только когда понятны безопасные output semantics;
5. интегрировать FormatKit в host applications постепенно, не перенося в SDK UI/provider/product policy.

FormatKit сознательно предпочитает explicit adapters и fail-closed diagnostics эвристическому движку «перевести любую строку».
