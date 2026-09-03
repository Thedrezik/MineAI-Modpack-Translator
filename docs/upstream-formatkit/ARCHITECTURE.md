# MineAI-FormatKit architecture

## Responsibility boundary

FormatKit owns **format safety**, not translation quality or product workflow.

```text
host-selected input
      ↓
host discovery / file IO / UI
      ↓
logical path + decoded source text
      ↓
FormatKit detect / analyze
      ↓
TranslationPlan + TranslationUnit[]
      ↓
external translator / LLM / API
      ↓
translated unit payloads
      ↓
FormatKit apply / merge / validation
      ↓
validated target text + optional target path
      ↓
host packaging / output policy
```

No adapter calls OpenRouter, Google, DeepL or a local LLM. FormatKit does not decide whether the user selected a whole modpack, `.minecraft`, one JAR, a resource pack or one file. The host normalizes those product workflows into paths and decoded content.

## Core data model

### `TranslationUnit`

A semantic player-facing payload plus an exact source span/locator and optional protected fragments. Technical syntax may be replaced with validated placeholders before the unit reaches a translation service.

Nested formats may expose several semantic units that map back into one outer token, but reconstruction still patches only explicit source spans.

### `TranslationPlan`

Immutable source text plus extracted units and adapter metadata. The plan is the canonical contract for counting, translating and reconstructing the file.

A host analyzer, estimator and processor should consume the **same plan** so discovery, progress estimates and actual translation cannot disagree about what is translatable.

### Structural fingerprint / skeleton

Adapters model the parts of a document that must remain unchanged. Depending on the format this may include source key/order/whitespace skeletons, JSON/SNBT shape, nested Component locators, Markdown/MDX structure, protected placeholder multisets, runtime-markup boundaries and line-break shape.

Identity reconstruction should be byte-exact for span-preserving adapters. Synthetic translation must still pass structural validation.

## Canonical-source rule

Current output structure always comes from canonical source content, normally English.

Examples:

```text
assets/<namespace>/lang/en_us.json
config/collapsiblegroups/lang/en_us.json
config/jaopca/lang/en_us.json
config/ftbquests/quests/lang/en_us.snbt
patchouli_books/<book>/en_us/...
assets/<namespace>/manual/en_us/*.txt
original GuideME / Oracle Index source trees
```

Existing `ru_ru` or other targets may be incomplete, stale, malformed, machine-translated or structurally inconsistent. They are optional wording that may be reused only when the current source plan proves it technically compatible.

Malformed optional targets therefore cannot invalidate a valid canonical source. `force` mode does not depend on target parsing; append/skip may discard malformed reuse data while exposing diagnostics to the host.

## Public SDK layer

The stable host-facing entry points are:

- `FormatRegistry.default()` — built-in adapter registry;
- `DetectedFormat` + `AdapterCapabilities` — stable detection metadata;
- `FormatKit.default()` — ready-to-embed facade;
- `FormatKit.detect()` / `FormatKit.analyze()` — detection and safe plan preparation;
- `FormatAnalysis` — supported/ready state, plan, units, capabilities, target path and diagnostics;
- `FormatKit.apply()` — validated reconstruction;
- low-level `matches/prepare/apply` adapters for callers that need direct control.

Capabilities are descriptive. They tell the host whether an adapter defines a target path, contains nested formats or supports existing-target merge; they do not prescribe UI or packaging policy.

## Default registry composition

`FormatRegistry.default()` currently registers **15 built-in content adapters**:

1. `GuideMeMarkdownAdapter`;
2. `DataDrivenGuideMeMarkdownAdapter`;
3. `CollapsibleGroupsLangJsonAdapter`;
4. `CollapsibleGroupsConfigLangJsonAdapter`;
5. `JaopcaConfigLangJsonAdapter`;
6. `CrashAssistantLocalizationAdapter`;
7. `MinecraftLangJsonAdapter`;
8. `MinecraftAdvancementTextAdapter`;
9. `MinecraftTextComponentAdapter`;
10. `FtbQuestsLangAdapter`;
11. `FtbQuestsChapterAdapter`;
12. `PatchouliBookJsonAdapter`;
13. `OracleIndexMdxAdapter`;
14. `OracleIndexMetaJsonAdapter`;
15. `ImmersiveEngineeringManualAdapter`.

A path match means “this adapter understands this file family”, not “every string in this file is translatable”.

## Minecraft locale architecture

The ordinary locale implementation is intentionally layered instead of being one monolithic parser:

```text
minecraft_lang.py
  strict top-level JSON parser + source-span reconstruction
        ↓
runtime_locale.py
  corpus-proven runtime token protection
        ↓
structured_locale.py
  strict non-string Component schemas + structured target reuse
        ↓
locale_safe.py
  live-modpack hardening: identical duplicate aliases,
  FTB runtime syntax, private-use glyphs and public merge behavior
```

The public `MinecraftLangJsonAdapter` and `LocaleMergePlanner` are imported from the top safety layer. Lower reviewed layers remain reusable and deliberately narrow.

### Base locale parser

`minecraft_lang.py` owns the strict top-level JSON object parser, source spans, exact reconstruction and ordinary value skeleton. It does not recursively translate arbitrary arrays/objects.

### Runtime protection layer

`runtime_locale.py` adds runtime syntax proven by real mods:

- FancyMenu `$$variables`;
- Hexerei `%kkey...%` tokens;
- URLs;
- slash commands.

Slash-command boundaries are Unicode-aware: real commands such as `/track`, `/create` or formatted `§a/track` are protected, while ordinary prose such as `стрелками/Tab`, `Документация/Wiki` and CJK-equivalent slash text is not misclassified.

### Structured locale layer

`structured_locale.py` supports only strict, independently proven Minecraft Component schemas. It never becomes a generic recursive JSON translator.

Currently proven schemas include:

- GAG root-list Components with `text`, `extra` and boolean `strikethrough`;
- Tempad root-list Components with visible `text` and immutable `color` / `index` metadata.

Only visible text leaves become `TranslationUnit`s. Locator sets, array order and technical values are structural data.

Existing target reuse compares semantic technical shape so harmless whitespace/object-key ordering differences do not invalidate an otherwise compatible translated Component. Technical field changes still invalidate reuse.

### Live locale safety layer

`locale_safe.py` adds corpus-proven behavior from the FTB Evolution instance/mod audit:

- repeated ordinary keys are accepted only when all repeated values are identical strings;
- one logical unit is exposed while all duplicate source spans are retained as aliases;
- changed translations are written back to every identical occurrence;
- conflicting duplicate values remain fail-closed;
- FTB `&...` formatting boundaries are protected;
- FTB `{image:...}` directives are exact runtime-critical tokens;
- BMP Private Use Area glyphs are protected;
- proven Malum codex runtime markup is protected;
- existing-target validation checks runtime-critical shape before reusing old wording.

### Serialized Components inside ordinary locale strings

Actually Additions and Iron Furnaces proved that a top-level locale value may be a **string whose decoded contents are themselves a JSON Component array**.

The adapter recognizes only the proven schema. Inner decoded JSON nodes are mapped back to exact spans inside the outer JSON string, so only visible inner `text` leaves reach the translation service. Technical component values such as colors, `%s`, punctuation and `clickEvent` action/value fields remain immutable.

This is nested parsing, not generic “parse every string as JSON”.

## Runtime config locale adapters

`config_locales.py` reuses the hardened public `MinecraftLangJsonAdapter` for exactly two runtime roots independently proven from the actual mods:

```text
config/collapsiblegroups/lang/en_us.json
config/jaopca/lang/en_us.json
```

`CollapsibleGroupsConfigLangJsonAdapter` and `JaopcaConfigLangJsonAdapter` replace only the terminal locale filename for target paths and support existing-target merge through the same locale planner.

This is an explicit path allow-list. The registry does **not** treat arbitrary `config/<mod>/lang/en_us.json` as a locale format.

Collapsible Groups also ships a separate bundled catalog:

```text
assets/collapsible_groups/group_lang/en_us.json
```

which remains a separate `CollapsibleGroupsLangJsonAdapter` surface.

## Locale merge planning

`LocaleMergePlanner` compares the canonical source plan with optional target wording:

- `append`: reuse technically safe target units; regenerate missing/empty/English-identical/unsafe units;
- `force`: ignore existing target wording and regenerate every canonical unit;
- `skip`: preserve any technically safe target wording and generate only absent/empty/unsafe units.

The source key/order/shape always defines output. Existing target text is rejected for reuse when critical placeholders/runtime markup/structured shape do not match.

The same planner can be instantiated with the proven config-locale adapters because they inherit the hardened locale pipeline.

FTB-specific locale merge uses the same principle through `FtbQuestsLocaleMergePlanner`, but retains FTB Quests-specific SNBT/unit-layout rules.

## Minecraft text Components outside locale files

### `MinecraftAdvancementTextAdapter`

Narrow path adapter for direct advancement `display.title` / `display.description` literal Component surfaces proven by real mods. It extends selected Minecraft Component handling without making all advancement JSON strings translatable.

### `MinecraftTextComponentAdapter`

Strict parser for selected advancement/loot/enchantment JSON contexts such as literal component `text`, `translate` fallbacks, `minecraft:set_name`, `minecraft:set_lore` and proven nested custom-name/component contexts.

Resource locations, function IDs and component metadata remain immutable.

## GuideME / Markdown architecture

`GuideMeMarkdownAdapter` is source-span based. It preserves YAML structure, headings/list structure, link destinations, images, inline code, fenced code and GuideME/HTML components.

`guideme_safe.py` composes additional live-corpus hardening above the base parser:

- paired `*italic*` and `**bold**` delimiter boundaries are protected;
- underscore emphasis is intentionally not generalized because the same corpus contains resource IDs/JSX attributes such as `data_center` and `data_io`.

`DataDrivenGuideMeMarkdownAdapter` reuses the same safety layer for proven `assets/<ns>/guides/<guide-id>/...` trees and inserts `_<locale>/` under the guide root for target paths.

## Oracle Index

`OracleIndexMdxAdapter` reuses the hardened GuideME/Markdown machinery while applying Oracle source/translated-tree path rules.

```text
oracle_index/books/<project>/.content/page.mdx
  → oracle_index/books/<project>/.translated/ru_ru/.content/page.mdx
```

`OracleIndexMetaJsonAdapter` supports both legacy string navigation labels and the newer nested metadata form containing fields such as `name` and `icon`. Only visible label text is replaceable; keys/icons/metadata remain immutable.

## Patchouli

`PatchouliBookJsonAdapter` is a strict localized category/entry parser.

Proven replaceable fields:

- top-level `name` / `description`;
- page `text`, `title`, `heading`, `name`;
- custom-page keys ending in `.text` / `.heading`.

Machine fields stay immutable. Patchouli `$(...)` / `/$` markup is protected before translation.

`link_text` remains intentionally excluded from the generic field allow-list because real corpora prove two meanings: literal player text in some books and a translation key in others. Safe support requires semantic disambiguation rather than a broad whitelist.

## Immersive Engineering manual

`ImmersiveEngineeringManualAdapter` handles `assets/<ns>/manual/en_us/*.txt` with source spans.

It translates plain prose and proven visible branches of IE manual syntax while keeping link targets/anchors, config keys, keybind identifiers, directives and formatting technical.

## Special locale formats

### Collapsible Groups bundled catalog

`CollapsibleGroupsLangJsonAdapter` owns the proven bundled runtime catalog under `assets/collapsible_groups/group_lang/en_us.json`.

### CrashAssistant

`CrashAssistantLocalizationAdapter` owns `crash_assistant_localization/en_us.json` and protects CrashAssistant `$...$` macros, HTML tags and URLs in addition to ordinary locale placeholders.

## FTB Quests

### Locale SNBT

`FtbQuestsLangAdapter` owns canonical `config/ftbquests/quests/lang/en_us.snbt`.

FTB values may contain strings/lists plus nested JSON Components encoded inside SNBT strings:

```text
SNBT source span
  → decoded SNBT string
  → optional JSON Component
  → visible text leaves
```

Only visible leaves are translated. SNBT keys/list shape, JSON colors/click URLs/keybind metadata, FTB directives and unrelated machine data remain immutable before reconstruction back into the exact source span.

### Chapter SNBT

`FtbQuestsChapterAdapter` recognizes only the proven direct player-facing chapter surface: `feedback_message`, `description`, `minecraft:custom_name` and `minecraft:lore`.

Quest graph IDs, dependencies, coordinates, task/reward configuration and unrelated strings are technical by default.

## Container layer

### `JarContainer`

JAR/ZIP is transport, not a content format. The container layer:

1. inspects ZIP entries;
2. reports/rejects ambiguous duplicate paths for unsafe rebuilds;
3. detects signature material (`META-INF/*.SF`, `*.RSA`, `*.DSA`, `*.EC`);
4. asks `FormatRegistry` which entries have supported content adapters;
5. preserves untouched payloads;
6. validates rebuilt ZIP CRC;
7. refuses signed-JAR mutation;
8. supports overlay/resource-pack output as a safe host strategy;
9. discovers embedded JAR entries and exposes explicit **one-level** `inspect_nested()` using the normal registry.

Nested inspection is read-only analysis. FormatKit does not recursively mutate or repack nested JARs automatically.

## Diagnostics and failure model

Unsupported paths are normal SDK results, not exceptions at the product level. Known-format preparation failures become structured diagnostics through `FormatKit.analyze()`.

Examples of safe failure/diagnostic behavior:

- malformed canonical source → not ready / validation error;
- malformed optional target → canonical source work remains possible, target reuse discarded;
- unsupported structured locale value → diagnostic and leave untouched;
- conflicting duplicate key → fail closed;
- lost protected placeholder → fail reconstruction;
- signed JAR mutation → `SignedJarError`;
- invalid nested archive → nested inspection reports failure without guessing.

## Nested formats and protected fragments

Real formats often contain one syntax inside another:

```text
Minecraft locale JSON -> serialized JSON Component -> visible text
FTB Quests SNBT        -> JSON Component            -> visible text
Patchouli JSON         -> Patchouli markup          -> visible text
GuideME/Oracle MDX     -> Markdown + JSX            -> visible text
IE manual text         -> IE directives             -> visible text
```

The outer adapter owns source spans. Inner syntax is parsed or protected before semantic text reaches the translation provider. Translation is rejected if protected-fragment contracts or structural skeletons drift.

## Structural validation rules

Across adapters:

- canonical source keys and technical IDs are immutable unless explicitly modeled otherwise;
- protected placeholders must survive generated translations;
- duplicate/ambiguous syntax fails closed unless a narrow identical-duplicate rule is proven;
- reconstructed syntax must parse successfully;
- structural fingerprints/skeletons must remain stable;
- identity translation should be byte-exact for span-preserving adapters;
- nested formats receive nested validation instead of blind whole-string translation;
- unsupported value types are diagnosed rather than recursively guessed.

## Why target packs are useful but not canonical

Large translated packs are excellent negative/edge-case corpora. They reveal malformed JSON, duplicate keys, lost placeholders, stale keys and structured target variants. They do **not** prove the meaning of a new source schema by themselves.

New parser behavior therefore requires canonical source examples and safe reconstruction evidence, not merely a target-language file that happens to contain a similar shape.

## Intentionally unsupported architecture boundaries

The following should stay outside current generic behavior until new canonical corpus evidence proves a safe contract:

- arbitrary recursive JSON/SNBT translation;
- arbitrary structured locale object/list schemas;
- generic `<...>` token handling;
- FancyMenu custom `§x/§y/§z` semantics;
- hard-coded Modonomicon text in non-locale-specific book JSON;
- arbitrary executable/template DSL strings such as KubeJS JavaScript;
- automatic nested-JAR mutation;
- arbitrary unproven config locale roots.

The architectural preference is always a narrow adapter or explicit unsupported diagnostic over a broad heuristic.

## Integration target

MineAI-Modpack-Translator or another host can integrate FormatKit incrementally:

```text
host discovery
    ↓
FormatKit analyze (shadow/parity mode if desired)
    ↓
shared TranslationPlan for counting/extraction
    ↓
host translation provider/cache/batching
    ↓
FormatKit apply + validation
    ↓
host resource-pack/JAR/output policy
```

FormatKit does not need to own GUI state, batching, caching, provider selection, progress or modpack scanning in order to provide one format-safe source of truth.

## Evolution rule

New support should follow this sequence:

```text
real canonical corpus
  → reproducible unsupported/unsafe case
  → narrow parser/protection rule
  → identity reconstruction
  → synthetic reconstruction
  → regression tests
  → Windows/Linux CI
  → only then public support
```

This rule is more important than maximizing the number of strings extracted.
