# Heracles / Odyssey Quests adapter

## Scope

MineAI discovers `config/heracles/quests/**/*.json`, `config/heracles/groups.txt`,
and `config/heracles/tutorial.html` only when quest translation is enabled. These
files are edited in place because Heracles loads them directly from `config` and
does not provide a separate locale for quest descriptions.

## FormatKit contract

`HeraclesQuestAdapter` parses JSON without reserializing it. Translation units
cover only user-facing component literals in `display.title` and
`display.subtitle`, `display.description`, group display keys, and explicit
`title` / `description` fields in task and reward objects. JSON escaping is
applied after translation. Markdown, Hermes HTML, game formatting codes, links,
and line breaks are protected as immutable anchors.

IDs, object keys other than group labels, task/reward types, commands, item and
registry IDs, dependencies, NBT, predicates, coordinates, settings, icons, and
reward data never become translation units. Validation reparses the result and
requires the complete non-translatable tree and all component/description
shapes to remain identical. A no-op round trip is byte-exact.

`groups.txt` and `tutorial.html` use lossless text/markup plans. Repeated group
names share the translation cache, keeping `groups.txt` consistent with JSON
group keys.

## Safe write and modes

Before the first successful write MineAI creates `<file>.bak`. It records hashes
of the baseline and generated output. Append/Skip merge an existing valid output
with the English baseline and submit only untranslated units. Force always starts
from the verified baseline. An unverified stale backup is preserved under a
hash-suffixed name before a new baseline is accepted. Writes are atomic and a
failed FormatKit validation leaves the live file unchanged.

## Integration and verification

Analyzer, estimator, selection, progress accounting, and runtime use the same
discovery list and FormatKit plan. Tests cover 1.20 and 1.21 component forms,
escaped Markdown/HTML descriptions, nested composite/selectable elements,
group keys, dangerous commands/NBT, stale baselines, partial translations, and
estimator/processor parity.
