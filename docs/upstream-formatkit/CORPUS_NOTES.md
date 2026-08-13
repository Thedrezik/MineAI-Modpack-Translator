# AE2 / GuideME corpus notes

This document records facts observed from the canonical user-supplied
`ae2guide` corpus. These observations drive tests; they are not inferred from
the broken translation output.

## Inventory

- 125 Markdown pages
- 123 SNBT scene/structure files
- 40 PNG assets

The broken translated tree contains the same 125 Markdown relative paths but
not the original SNBT/PNG service assets.

## Markdown features observed

- 125 YAML `navigation.title` values
- 442 headings
- 731 unordered-list lines
- 109 ordered-list lines
- 95 table-like pipe rows
- 434 Markdown link/image occurrences in the initial inventory pass
- GuideME/HTML-like components such as `ItemLink`, `GameScene`,
  `ImportStructure`, `BoxAnnotation`, `RecipeFor`, `ItemImage`, `Row`,
  `IsometricCamera`, `LineAnnotation`, `BlockImage`, and others
- horizontal-rule `---` occurrences after front matter in real pages
- trailing two-space Markdown hard breaks
- inline code and relative asset/page destinations

A critical corpus-derived rule is that `---` must be treated as YAML only for
the initial front matter. Later `---` lines are ordinary Markdown structure.

## Current extraction audit

The v0.1 GuideME adapter extracts:

- 2,819 translation units total
- 125 YAML-title units
- 2,598 ordinary Markdown-line units
- 96 table-cell units
- 913 units containing one or more protected technical fragments

All 125 original Markdown pages pass:

1. byte-exact identity round-trip;
2. synthetic payload replacement while preserving the structural fingerprint.

The structural validator rejects all 125 files from the supplied broken
translation tree when compared to their original counterpart. This does not
mean every translated sentence is bad; it means every broken-tree page differs
in at least one invariant currently considered unsafe (most commonly line
structure/wrapping).

## Design consequence

The adapter uses source spans instead of parse-and-render. Untouched syntax is
never regenerated. Translation can only replace semantic payload spans and is
rejected if it loses placeholders, introduces newlines, changes protected
components/paths, or breaks table delimiters.

## Repository fixture strategy

The full canonical archive has SHA-256:

`47e0fadbcabcff090a479f4c72ed0640e5bdaaba977221a8fd14d5d35c090c43`

Ordinary CI uses five unmodified pages extracted from that archive. The full
archive remains an optional fixture; dropping it at
`tests/fixtures/ae2guide-original-reference.zip` automatically enables the
125-page corpus tests and service-asset inventory checks.
