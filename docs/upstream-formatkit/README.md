# MineAI-FormatKit

[**English**](README.md) | [Русский](README_RU.md)

Structure-safe, embeddable format adapters for **MineAI-Modpack-Translator** and other Minecraft translation tools.

FormatKit is an SDK/library, not a standalone translator. A host application chooses and scans a `.minecraft` directory, modpack, JAR, resource pack, quest folder or loose file, then passes FormatKit a logical path plus decoded text. FormatKit owns only format detection, safe text extraction, technical-fragment protection, span-safe reconstruction and structural validation.

```text
host discovery / file IO / UI / translation provider
                    ↓
              path + source text
                    ↓
             FormatKit analyze
                    ↓
       TranslationPlan + TranslationUnit[]
                    ↓
          external translation service
                    ↓
             FormatKit apply
                    ↓
      validated text + optional target path
                    ↓
          host packaging/output policy
```

## Responsibility boundary

| FormatKit owns | Host application owns |
| --- | --- |
| format detection for a supplied path | choosing/scanning `.minecraft`, modpacks, folders and files |
| safe `TranslationUnit` extraction | LLM/API/provider selection and prompts |
| placeholder/runtime-markup protection | caching, batching, concurrency, progress and ETA |
| format-defined target-path hints | GUI and user workflow |
| reconstruction and structural validation | resource-pack vs JAR vs loose-file output policy |
| adapter capabilities and diagnostics | retry/skip/report policy |
| reusable JAR safety inspection | deciding when/how archives are packaged |

FormatKit never calls OpenRouter, Google, DeepL or a local LLM itself.

## Core principles

1. **Canonical English drives structure.** `en_us.json`, `en_us.snbt`, localized book trees and original MDX/Markdown sources define the current structure. Existing target translations are optional reusable wording, never structural truth.
2. **Containers are not text formats.** A JAR can contain thousands of JSON files without containing thousands of translatable files.
3. **Adapters are deliberately narrow.** A format is supported only after real corpus evidence proves which fields are player-visible and which are technical.
4. **Span-preserving reconstruction.** Where possible, only exact source spans are replaced; untouched syntax, ordering and whitespace remain byte-identical.
5. **Protected fragments are mandatory.** Placeholders, runtime directives, URLs, formatting boundaries, component metadata and technical IDs must survive translation.
6. **Fail closed.** Invalid syntax, conflicting duplicate keys, lost placeholders, unsupported structured values, signed-JAR mutation and ambiguous archive entries are rejected rather than guessed.

## Current audited baseline

The complete FTB Evolution `mods/` audit currently covers **518 unique readable JARs**.

- 417 canonical `assets/<namespace>/lang/en_us.json` files;
- about 90.5k top-level locale entries;
- 417/417 canonical locales pass byte-exact identity reconstruction;
- 417/417 pass synthetic translation + structural validation;
- 235 real EN→RU sibling locale pairs complete merge planning without aborting;
- 1,247 Patchouli EN category/entry JSON files;
- 779 GuideME Markdown pages;
- 187 Oracle Index MDX/meta files;
- 117 Immersive Engineering manual files;
- 27,457 advancement JSON files, including 279 direct literal title/description fields already covered by the advancement adapter.

The instance/config corpus additionally proved exact runtime locale roots for Collapsible Groups and JAOPCA under `config/.../lang/en_us.json`.

Third-party corpora are used locally for regression testing and are not redistributed when licensing/redistribution rights are unclear.

## Built-in adapters

`FormatRegistry.default()` currently registers **15** built-ins:

| Adapter | Proven source surface |
| --- | --- |
| `GuideMeMarkdownAdapter` | AE2-style GuideME Markdown source trees |
| `DataDrivenGuideMeMarkdownAdapter` | `assets/<ns>/guides/<guide-id>/.../*.md` |
| `CollapsibleGroupsLangJsonAdapter` | bundled `assets/collapsible_groups/group_lang/en_us.json` |
| `CollapsibleGroupsConfigLangJsonAdapter` | deployed `config/collapsiblegroups/lang/en_us.json` |
| `JaopcaConfigLangJsonAdapter` | `config/jaopca/lang/en_us.json` |
| `CrashAssistantLocalizationAdapter` | `crash_assistant_localization/en_us.json` |
| `MinecraftLangJsonAdapter` | canonical Minecraft `assets/<ns>/lang/en_us.json` |
| `MinecraftAdvancementTextAdapter` | direct advancement display title/description Components |
| `MinecraftTextComponentAdapter` | selected advancement/loot/enchantment JSON Components |
| `FtbQuestsLangAdapter` | `config/ftbquests/quests/lang/en_us.snbt` |
| `FtbQuestsChapterAdapter` | allow-listed direct chapter SNBT text |
| `PatchouliBookJsonAdapter` | localized Patchouli category/entry JSON |
| `OracleIndexMdxAdapter` | Oracle Index / ModdedMC.wiki-style MDX |
| `OracleIndexMetaJsonAdapter` | Oracle Index `_meta.json` navigation labels |
| `ImmersiveEngineeringManualAdapter` | `assets/<ns>/manual/en_us/*.txt` |

The public SDK also exposes `LocaleMergePlanner`, `FtbQuestsLocaleMergePlanner`, `JarContainer`, diagnostics/capabilities and low-level adapter classes.

## Minecraft locale safety

### Ordinary strings

`MinecraftLangJsonAdapter` preserves top-level keys, ordering and source formatting while translating safe player-visible values. It protects corpus-proven runtime syntax including:

- Java/printf and MessageFormat placeholders;
- vanilla Minecraft `§` formatting codes;
- FancyMenu `$$variables`;
- Hexerei `%kkey...%` keybind tokens;
- boundary-safe slash commands with Unicode-aware command boundaries;
- URLs;
- FTB `&...` formatting boundaries and `{image:...}` directives;
- BMP Private Use Area glyphs used by custom fonts;
- proven Malum codex runtime markup.

Natural prose such as `300% Larger`, `75% chance`, `and/or`, `стрелками/Tab` or `Документация/Wiki` is not misclassified as a runtime placeholder/command.

### Duplicate locale keys

Repeated ordinary locale keys are accepted only when every occurrence is a string with the same decoded value. One logical translation unit is exposed and a changed translation is written back to every identical source occurrence. Conflicting duplicate values remain fail-closed.

### Strict structured locale Components

Structured values are **not** recursively translated. Only independently proven Component schemas are supported:

- the GAG root-list schema using visible `text`/`extra` leaves with `strikethrough` metadata;
- the Tempad root-list schema using visible `text` leaves with immutable `color` and `index` metadata.

Only visible text leaves become translation units. Array order, object shape and technical fields remain canonical-English structure. Unknown arrays/objects still appear in `unsupported_non_string_keys` diagnostics.

### Serialized Components inside ordinary locale strings

Actually Additions and Iron Furnaces proved another narrow case: a normal locale string may itself contain a serialized JSON Component array. FormatKit recognizes only the proven schema, maps inner decoded text nodes back to exact outer JSON-string spans, and exposes only visible text leaves while preserving `%s`, colors, punctuation and `clickEvent` technical data.

### Existing-target merge

`LocaleMergePlanner` keeps output structure canonical-English:

| Mode | Behavior |
| --- | --- |
| `append` | Reuse technically safe target wording; translate missing/empty/English-identical/unsafe units. |
| `force` | Ignore existing target content and translate every canonical source unit. |
| `skip` | Preserve any technically safe existing target wording; translate only absent/empty/unsafe units. |

Malformed optional target locales never replace canonical source structure. `force` ignores target text entirely; append/skip may discard malformed reuse data while keeping the parse error available for diagnostics.

For structured Components, reuse compares semantic technical shape rather than insignificant whitespace/object-key ordering differences.

## Runtime config locales

Two config-root formats are now supported because the actual mods proved that these files are runtime locale catalogs and that the target filename is the locale code:

```text
config/collapsiblegroups/lang/en_us.json
→ config/collapsiblegroups/lang/ru_ru.json

config/jaopca/lang/en_us.json
→ config/jaopca/lang/ru_ru.json
```

`CollapsibleGroupsConfigLangJsonAdapter` and `JaopcaConfigLangJsonAdapter` inherit the hardened public Minecraft locale pipeline, including target merge and structured/runtime protections. Other arbitrary `config/<mod>/lang/en_us.json` roots are **not** matched automatically.

Collapsible Groups also has a separate bundled runtime catalog under `assets/collapsible_groups/group_lang/en_us.json`, handled by `CollapsibleGroupsLangJsonAdapter`.

## Markdown, guides and manuals

### GuideME

GuideME adapters preserve front matter, Markdown structure, inline/fenced code, link destinations, images and GuideME/HTML/MDX components. Real `*italic*` and `**bold**` delimiter boundaries are protected without enabling broad underscore heuristics that would collide with resource IDs such as `data_center`.

Typical target layouts:

```text
assets/ae2/ae2guide/page.md
→ assets/ae2/ae2guide/_ru_ru/page.md

assets/demo/guides/demo/guide/page.md
→ assets/demo/guides/demo/guide/_ru_ru/page.md
```

### Oracle Index

`OracleIndexMdxAdapter` uses the hardened GuideME/Markdown layer for `.mdx` pages and excludes `.translated/<locale>/` output from source discovery. `OracleIndexMetaJsonAdapter` supports both legacy string labels and the newer nested `{name, icon, ...}` metadata shape while keeping navigation keys/icons immutable.

### Immersive Engineering manual

`ImmersiveEngineeringManualAdapter` translates plain prose plus proven visible branches of IE manual directives while preserving link targets/anchors, config keys, keybinds, formatting codes and other machine syntax.

## Patchouli

`PatchouliBookJsonAdapter` translates only proven localized display fields:

- top-level `name` / `description`;
- page `text`, `title`, `heading`, `name`;
- custom-page string keys ending in `.text` or `.heading`.

Patchouli `$(...)` / `/$` runtime markup is protected exactly. Machine fields such as page type, icons, recipes, items, rituals, advancements, entities, anchors and resource locations remain immutable.

`link_text` is intentionally not generalized yet: real corpora prove that it can be either literal player text or a translation key, so a simple whitelist would be unsafe.

## Special locales

`CrashAssistantLocalizationAdapter` handles `crash_assistant_localization/en_us.json` and additionally protects CrashAssistant `$...$` macros, HTML tags and URLs.

## FTB Quests

`FtbQuestsLangAdapter` parses the canonical `en_us.snbt` locale and handles nested JSON Components encoded inside SNBT strings. `FtbQuestsChapterAdapter` exposes only the proven direct chapter fields `feedback_message`, `description`, `minecraft:custom_name` and `minecraft:lore`.

`FtbQuestsLocaleMergePlanner` follows the same canonical-English rule as ordinary locale merge and rejects unsafe existing target reuse when protected fragments or unit layout do not match.

Quest IDs, graph dependencies, coordinates, tasks/rewards and unrelated configuration remain technical data.

## JAR safety

`JarContainer` separates archive safety from text parsing:

- duplicate ZIP-entry detection;
- JAR signature detection (`META-INF/*.SF`, `*.RSA`, `*.DSA`, `*.EC`);
- supported-entry discovery through `FormatRegistry`;
- CRC validation after rebuild;
- signed-JAR mutation refusal with resource-pack/overlay as the safe host strategy;
- explicit **one-level** nested JAR inspection through `inspect_nested()` using the same registry.

Nested inspection is discovery/diagnostics only. FormatKit does not rewrite nested JARs automatically; packaging policy remains the host's responsibility.

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

`FormatAnalysis` exposes:

- `supported`, `ready`, `has_errors`;
- adapter name and stable `AdapterCapabilities`;
- `units` / `unit_count`;
- optional format-defined target path;
- structured diagnostics.

Low-level adapters remain available through the stable contract:

```text
matches(path)
prepare(path, source_text) -> TranslationPlan
translate(unit.text) externally
apply(plan, translations) -> validated target text
```

## Intentionally unsupported / not generalized

The following are deliberate safety boundaries, not accidental omissions:

- arbitrary recursive JSON/SNBT translation;
- arbitrary structured locale arrays/objects outside proven schemas;
- generic `<...>` protection or translation;
- FancyMenu custom `§x/§y/§z` semantics without stronger parser proof;
- hard-coded Modonomicon book strings in non-locale-specific source files;
- executable/template DSL content such as arbitrary KubeJS JavaScript strings;
- conflicting duplicate locale values;
- automatic nested-JAR mutation;
- arbitrary unproven config locale roots.

Unsupported data should remain unsupported until canonical source corpora prove safe extraction/reconstruction boundaries.

## Real corpus matrix

| Corpus | Main finding |
| --- | --- |
| AE2 GuideME | 125 canonical pages pass byte-exact identity and structural synthetic reconstruction. |
| Rechiseled 1.2.5 | 3,656 ordinary locale values; safe creation of a new target locale. |
| The Bumblezone | Natural-percent regression and stale/incomplete target evidence. |
| SecurityCraft | Equal EN/RU counts can still hide key drift. |
| Refurbished Furniture | Signed JAR; in-place mutation must be blocked. |
| Create LTAB | Visible text also exists in structured advancement/loot Components. |
| FTB Evolution quests | 7,615 locale units + 60 direct chapter units; existing RU is structurally stale. |
| Genetics Resequenced | Patchouli + Oracle Index canonical source/target parity and markup damage evidence. |
| MPLOCmods v39 | Broad target-only malformed/duplicate/structured-locale negative evidence. |
| FTB Evolution instance/config | 5,964 files; FTB runtime syntax, GuideME emphasis, duplicate aliases, PUA glyphs and proven config locale roots. |
| FTB Evolution full mods corpus | 518 unique JARs; 417 canonical locales; full identity/synthetic locale pass plus corpus-proven hardening. |

See [`MOD_CORPUS_NOTES.md`](MOD_CORPUS_NOTES.md) for detailed measurements and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the parser/reconstruction model.

## Development

Runtime code has no required third-party dependencies.

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

GitHub Actions runs the suite on Ubuntu and Windows with Python 3.10 and 3.13.

## Fixture and licensing policy

- Canonical original files define format behavior.
- Broken translations are negative regression evidence, not source truth.
- Full third-party binaries stay local when redistribution rights are unclear.
- Successful parsing alone is insufficient: support requires reconstruction and structural validation evidence.

## Roadmap

1. keep the public SDK/adapter contract stable;
2. add new schemas only from canonical real-world corpora;
3. keep protection primitives narrow and corpus-proven;
4. add additional quest/book/config formats only when safe output semantics are understood;
5. integrate into host applications incrementally without moving UI/provider/product policy into FormatKit.

FormatKit deliberately prefers explicit adapters and fail-closed diagnostics over a heuristic “translate every string” engine.
