# Real mod corpus notes

These measurements come from JARs, modpacks, resource packs and documentation archives supplied locally for FormatKit testing. Third-party binaries are **not** committed to this repository when licensing/redistribution rights are unclear.

Corpora are used to discover edge cases and prove extraction/reconstruction behavior against real content. Canonical source data defines semantics; existing translations are supporting/negative evidence only.

## FTB Evolution complete mods corpus — 518 unique JARs

This is the largest current acceptance corpus and the basis of the latest hardening pass.

Final audited inventory:

- **518 unique readable JARs**;
- **417** canonical `assets/<namespace>/lang/en_us.json` files;
- about **90.5k top-level locale entries**;
- **417/417** canonical locales pass prepare + byte-exact identity reconstruction;
- **417/417** pass synthetic translation + structural validation;
- **235** real EN→RU sibling locale pairs complete merge planning without aborting;
- **1,247** Patchouli EN category/entry JSON files;
- **779** GuideME Markdown pages;
- **187** Oracle Index MDX/meta files;
- **117** Immersive Engineering manual files;
- **27,457** advancement JSON files;
- **279** direct literal advancement title/description fields already covered by the advancement adapter.

### Corpus-proven locale hardening

#### Unicode-safe slash-command boundaries

Real FancyMenu target text contains ordinary prose where `/Tab` or `/Wiki` immediately follows Cyrillic/CJK text. An ASCII-only left-boundary check could misclassify those fragments as slash commands.

The command matcher is now Unicode-aware:

- real `/create`, `/track`, `/give`, etc. remain protected;
- formatted commands such as `§a/track` remain protected;
- prose such as `стрелками/Tab` and `Документация/Wiki` remains visible to the translator.

#### Tempad strict structured locale Components

Tempad 3.0.4 provided canonical English proof for a new root-list Component schema containing visible `text` leaves plus technical `color` and `index` fields.

Corpus result:

- 10 structured canonical keys;
- 19 visible text units;
- existing translations preserve the same technical structure while translating visible text.

Only the proven schema is supported. Arbitrary object/list locale values remain unsupported.

#### Serialized Components inside ordinary locale strings

Actually Additions and Iron Furnaces store JSON Component arrays **inside ordinary string-valued locale entries**.

Real corpus:

- 8 locale strings across the two mods;
- 20 safe visible nested text units.

The nested parser exposes only visible text leaves while preserving `%s`, colors, punctuation and technical `clickEvent` fields. Inner decoded nodes are mapped back to exact outer JSON-string source spans.

#### Semantic structured-target reuse

Existing translated structured Components may differ only in insignificant whitespace or object-key ordering. Target reuse compares semantic technical shape rather than requiring byte-identical serialization, while array order, locators and technical values still must match canonical English.

#### Malum runtime markup

Malum codex strings proved a narrow runtime syntax requiring protection. Only demonstrated balanced `$i.../$`, `$b.../$` boundaries and numeric `$m<number>/$` scale tokens are protected. This does not become generic `$...` parsing.

### Jar-in-Jar evidence

The corpus contains embedded JARs. `JarContainer` exposes explicit one-level nested inspection using the same format registry.

Nested inspection is deliberately read-only/discovery-oriented:

- invalid embedded archives fail closed;
- nested entries can be analyzed for supported content;
- FormatKit does not automatically rewrite nested JARs;
- packaging remains host policy.

### Oracle Index 1.3.1 evidence

Newer Oracle Index metadata proved a second `_meta.json` representation: nested objects containing visible `name` plus technical fields such as `icon`.

The metadata adapter supports both legacy string values and this newer nested shape. Oracle MDX also uses the hardened GuideME layer.

### Intentionally not generalized from the full corpus

The audit also found surfaces that should remain unsupported until their output semantics are safer:

- FancyMenu custom `§x/§y/§z` tokens;
- generic `<...>` markup;
- hard-coded Modonomicon book strings stored in non-locale-specific JSON;
- arbitrary KubeJS executable/template strings;
- arbitrary unproven config locale roots;
- Patchouli `link_text`, because real corpora prove both literal text and translation-key meanings.

These are useful corpus findings even when the correct implementation decision is “do not translate yet”.

## FTB Evolution instance/config corpus — 5,964 files

Before the full `mods/` audit, five instance archives were tested as one corpus:

- `datapacks/`;
- `kubejs/`;
- `defaultconfigs/`;
- `configureddefaults/`;
- `config/`.

Total: **5,964 files**.

Important findings:

- `config/ftbquests` contains 84/84 files byte-for-byte identical to the separately certified FTB Evolution quest corpus;
- 213 datapack JSON files match Minecraft text-component path families;
- 50 direct loot text units were found: 20 `minecraft:set_name` + 30 lore entries;
- Hostile Networks GuideME exposed 23 safe units and real `*italic*` / `**bold**` emphasis;
- `data/ftbevolution/loot_tables/ingots.json` is exactly 0 bytes and correctly remains malformed/fail-closed.

### Identical duplicate locale keys

`kubejs/assets/immersiveengineering/lang/en_us.json` contains 32 repeated ordinary keys whose repeated values are identical.

The public locale adapter now:

- exposes one logical unit per repeated key;
- records all repeated value spans as aliases;
- writes a changed translation into every alias occurrence;
- keeps conflicting duplicate values fail-closed.

### FTB runtime syntax inside ordinary locale JSON

`kubejs/assets/ftbevolution/lang/en_us.json` contains:

- 1,501 FTB `&...` formatting boundaries across 378 entries;
- 10 `{image:...}` runtime directives.

The formatting boundary count is critical for existing-target reuse, but the exact target color/style code may differ legitimately. `{image:...}` remains an exact runtime-critical token.

### Private Use Area glyphs

`kubejs/assets/minecraft/lang/en_us.json` contains 19 music-disc entries using a custom BMP Private Use Area glyph. Those glyphs are protected and treated as runtime-critical for target reuse.

### GuideME star emphasis

Hostile Networks proved real `*italic*` and `**bold**` prose. Their delimiter boundaries are protected.

Underscore emphasis was intentionally **not** generalized: the same guide contains technical identifiers such as `data_center` and `data_io`, and a broad underscore matcher created false positives.

### Proven runtime config locales

The instance corpus initially exposed two parser-compatible config locale roots:

```text
config/collapsiblegroups/lang/en_us.json
config/jaopca/lang/en_us.json
```

The actual mods later proved their runtime semantics and target naming, so exact adapters are now enabled:

- `CollapsibleGroupsConfigLangJsonAdapter` → `config/collapsiblegroups/lang/<locale>.json`;
- `JaopcaConfigLangJsonAdapter` → `config/jaopca/lang/<locale>.json`.

The Collapsible Groups config catalog contains 1,401 string keys in the supplied instance. The JAOPCA catalog contains 901 keys, with 134 non-empty English strings in that generated/runtime snapshot.

Both adapters inherit the hardened Minecraft locale pipeline and existing-target merge. This does **not** generalize to arbitrary `config/<mod>/lang/` folders.

## Rechiseled 1.2.5 / NeoForge / Minecraft 1.21

- SHA-256: `7bf14cf8a4bfdc4b6c990126a75da29fd2bb7559d1c05b71e29c8fd5ae044435`;
- size: 11,498,611 bytes;
- ZIP entries: 22,900;
- JSON files: 21,430;
- `assets/rechiseled/lang/en_us.json`: 3,656 values;
- existing `ru_ru.json`: none;
- JAR signatures: none;
- duplicate ZIP paths: none.

Checks:

- 3,656/3,656 values extracted;
- byte-exact identity locale round-trip;
- synthetic translation preserves JSON skeleton/key order;
- unsigned-JAR rebuild preserves untouched entry payloads and adds only the requested target locale.

Design lesson: tens of thousands of JSON files do **not** imply tens of thousands of translatable files.

## The Bumblezone 7.15.3+1.21.1 NeoForge

- ZIP entries: 6,837;
- locale files: 13;
- `en_us.json`: 1,790 values;
- `ru_ru.json`: 1,788 source-matching keys.

This corpus exposed the natural-percent regression `Bee Movie But It's 300% Larger`. Placeholder recognition must not treat ordinary prose percentages as printf syntax.

## SecurityCraft 1.10.2.1 / NeoForge / Minecraft 1.21.1

- ZIP entries: 6,418;
- locale files: 27;
- `en_us.json`: 1,564 values;
- `ru_ru.json`: 1,564 values;
- one English key missing from Russian;
- one stale Russian-only key.

Design lesson: equal locale counts do not prove key parity. Canonical English key identity drives current output structure.

## MrCrayfish's Furniture Mod: Refurbished 1.0.22

- ZIP entries: 6,393;
- `en_us.json`: 654 values;
- `ru_ru.json`: none;
- signature material includes `META-INF/MRCRAYFI.SF` and `META-INF/MRCRAYFI.RSA`.

Locale extraction passes, but in-place signed-JAR mutation is rejected. The safe strategy is an overlay/resource pack unless signature-aware packaging is provided by the host.

## Create: Let The Adventure Begin 4.0.0

- ZIP entries: 809;
- `en_us.json`: 40 values;
- `ru_ru.json`: 22 matching keys;
- append: 18 missing locale values;
- 42 recognized JSON files contain 84 additional Minecraft Component units.

This corpus proved that ordinary locale JSON is not always the complete player-visible text surface.

## AmbientSounds 6.3.8

- ZIP entries: 315;
- ordinary `en_us.json`: none;
- GuideME/Markdown source: none.

The correct result is **zero supported translation work**. Human-looking configuration strings can still be technical matching data.

## FTB Evolution 1.41.1 / FTB Quests / Minecraft 1.21.1

Pack fingerprint/runtime metadata:

- SHA-256: `55a553fe73a7003ae6e80228192f238acd8593a127e6818b923824e7fcdf3956`;
- ZIP entries: 6,504;
- Minecraft 1.21.1 / NeoForge 21.1.248;
- FTB Quests data format version 13;
- quest SNBT files: 83;
- chapters: 40;
- reward tables: 36;
- locale files: 5.

Canonical `quests/lang/en_us.snbt`:

- 4,499 top-level keys;
- 2,482 titles;
- 253 subtitles;
- 1,764 descriptions;
- 10,263 physical string values;
- 7,615 exposed translation units;
- 80 embedded JSON Component values / 344 non-empty nested visible-text units;
- 15,303 protected fragments.

Existing `ru_ru.snbt` has 4,198 keys: 302 canonical English keys are missing, one stale Russian-only key exists, and six shared descriptions have different list cardinality.

Across all 40 chapters the strict direct-text allow-list finds exactly 60 additional units: 51 custom names, 2 lore Components, 6 feedback messages and 1 direct description.

Locale and chapter files pass byte-exact identity and synthetic structural reconstruction.

## Genetics Resequenced template corpus / Patchouli + Oracle Index

Archive fingerprint:

- SHA-256: `f8301fe722b365d9a2afd0c6825abe00cfd74bddc46def70f1c35c24a86e9dcb`;
- size: 209,512 bytes;
- 737 archive entries / 655 non-empty file streams.

### Patchouli

- 106 canonical English category/entry JSON files;
- 106 existing Russian counterparts;
- 106/106 byte-exact identity passes;
- 106/106 synthetic structural passes;
- 260 safe display units.

A technical-token comparison against the existing Russian set found 7 markup mismatches, including lost closing `/$`, lost `$(br2)` and reordered link markup. Existing translated books are regression evidence, not structural truth.

### Oracle Index / MDX

- 118 original `.mdx` pages;
- 14 original `_meta.json` files;
- `OracleIndexMdxAdapter`: 118/118 identity + synthetic passes, 1,224 units;
- `OracleIndexMetaJsonAdapter`: 14/14 identity + synthetic passes, 131 labels.

This corpus also exposed another natural-percent edge case (`75% chance`).

## MPLOCmods v39 / broad Russian resource-pack corpus

Archive fingerprint:

- SHA-256: `ea2321607e2c3a15971ff334663b5deea6a4ad7c3e7732b2f797c4ced8e20557`;
- size: 10,308,460 bytes;
- ZIP entries: 7,232;
- files: 2,735;
- `assets/<namespace>/lang/ru_ru.json`: 2,191 files;
- 316,304 top-level locale entries across valid object files.

Target-only diagnostic findings:

- 4 malformed locale JSON files;
- 32 locale files with duplicate top-level keys;
- 8 locale files with array/object values.

This corpus initially exposed structured values from Lavender, owo, Things, Tempad and others. At that stage they correctly remained unsupported because canonical English semantics were missing. Later canonical GAG and Tempad corpora proved two narrow schemas, which are now supported; unrelated structured values remain unsupported.

The same archive contains 502 valid Patchouli JSON files. The Patchouli adapter passes identity/synthetic structural checks across the set and exposes 2,441 safe display units.

Design lesson: a broad translated target pack is excellent for finding malformed/duplicate/structured edge cases, but cannot independently define a new source schema.

## Additional large mod corpora that drove later adapters

Subsequent canonical JAR sets supplied independent evidence for:

- **Minecraft advancements** — direct literal `display.title` / `display.description` surfaces;
- **Collapsible Groups bundled locale** — dedicated `group_lang/en_us.json` runtime catalog;
- **Collapsible Groups config locale** — deployed `config/collapsiblegroups/lang/<locale>.json` catalog;
- **JAOPCA config locale** — runtime/downloader `config/jaopca/lang/<locale>.json` catalog;
- **CrashAssistant** — flat localization plus `$...$` macros, HTML and URLs;
- **data-driven GuideME** — `assets/<ns>/guides/<guide-id>/...` source/target trees;
- **Immersive Engineering Manual** — directive-aware `.txt` pages;
- **FancyMenu / Hexerei** — `$$variables` and `%kkey...%` runtime tokens;
- **GAG** — first strict structured locale Component schema;
- **Tempad** — second independent strict structured locale schema;
- **Actually Additions / Iron Furnaces** — serialized Components inside ordinary locale strings;
- **Malum** — balanced codex runtime markup;
- **Oracle Index 1.3.1** — newer nested metadata shape.

Each feature was added only after identity/synthetic reconstruction and technical-field preservation were demonstrated on canonical source material.

## Corpus conclusions

The real corpus suite now covers these major failure classes:

1. source locale exists while target is absent;
2. target exists but is incomplete/stale;
3. equal EN/RU counts hide key drift;
4. malformed optional target must not block valid canonical source work;
5. no supported text exists and correct behavior is skip;
6. signed archives must not be mutated;
7. duplicate ZIP entries and nested JARs require container-level handling;
8. visible Minecraft Components exist outside ordinary locale files;
9. ordinary locale strings can themselves contain nested serialized Components;
10. strict structured locale Components can be supported only per proven schema;
11. exact config-locale roots can be supported when real runtime semantics are proven;
12. FTB Quests uses SNBT locale catalogs plus a small direct chapter surface;
13. Patchouli/GuideME/Oracle/IE manuals contain their own runtime/document markup;
14. Unicode boundary handling matters for slash-command detection;
15. broad target packs reveal negative evidence but cannot define canonical source semantics;
16. unsupported content is often the safest correct result.

FormatKit is considered safer when a new corpus results in a narrow adapter, protection rule or explicit unsupported diagnostic instead of a broader “translate any string” heuristic.
