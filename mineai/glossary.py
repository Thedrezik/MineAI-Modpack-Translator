"""Source-to-target terminology rules for Russian Minecraft localization."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any


SUPPORTED_SCHEMA_VERSION = 1
SUPPORTED_LANGUAGE = "ru_ru"
VALID_APPLY_MODES = frozenset({"protect", "exact", "phrase"})
ORIGIN_RANK = {"builtin": 1, "generated": 2, "user": 3}


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    target: str
    scope: tuple[str, ...] = ("*",)
    apply: str = "exact"
    case_sensitive: bool = False
    priority: int = 0
    note: str = ""
    origin: str = "builtin"

    def applies_to_scope(self, scope: str) -> bool:
        normalized = normalize_scope(scope)
        return "*" in self.scope or "global" in self.scope or normalized in self.scope

    def scope_specificity(self, scope: str) -> int:
        return int(normalize_scope(scope) in self.scope)


@dataclass
class GlossaryStats:
    exact_matches: int = 0
    term_substitutions: int = 0


@dataclass
class SmartGlossary:
    entries: list[GlossaryEntry] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)
    conflicts: int = 0
    warnings: list[str] = field(default_factory=list)
    stats: GlossaryStats = field(default_factory=GlossaryStats)
    _exact_casefold: dict[str, list[GlossaryEntry]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _exact_case_sensitive: dict[str, list[GlossaryEntry]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _term_patterns: list[tuple[GlossaryEntry, re.Pattern[str]]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _fingerprint: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rebuild_indexes()

    @classmethod
    def load(
        cls,
        language: str = SUPPORTED_LANGUAGE,
        root: str | Path = "glossaries",
    ) -> "SmartGlossary":
        """Load bundled, generated, and user rules without failing the job."""
        if language != SUPPORTED_LANGUAGE:
            return cls()

        glossary = cls(source_counts={"builtin": 0, "generated": 0, "user": 0})
        sources: list[tuple[str, Any]] = []

        try:
            bundled = (
                resources.files("mineai.data.glossaries")
                .joinpath(f"{language}.json")
                .read_text(encoding="utf-8")
            )
            sources.append(("builtin", bundled))
        except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
            glossary.warnings.append(f"Встроенный глоссарий недоступен: {exc}")

        lang_dir = Path(root) / language
        ensure_user_glossary(lang_dir / "user.json", language)
        for origin, path in (
            ("generated", lang_dir / "generated.json"),
            ("user", lang_dir / "user.json"),
        ):
            if path.exists():
                sources.append((origin, path))

        for origin, source in sources:
            payload = _read_payload(source, origin, glossary.warnings)
            if payload is None:
                continue
            if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
                glossary.warnings.append(
                    f"{origin}: неподдерживаемая версия схемы "
                    f"{payload.get('schema_version')!r}"
                )
                continue
            if payload.get("language") != language:
                glossary.warnings.append(
                    f"{origin}: ожидался язык {language}, получен "
                    f"{payload.get('language')!r}"
                )
                continue
            raw_entries = payload.get("entries", [])
            if not isinstance(raw_entries, list):
                glossary.warnings.append(f"{origin}: поле entries должно быть списком")
                continue
            seen_entries: set[tuple[str, tuple[str, ...], str, bool]] = set()
            for raw in raw_entries:
                entry = _parse_entry(raw, origin, glossary.warnings)
                if entry is not None:
                    duplicate_key = (
                        entry.source if entry.case_sensitive else entry.source.casefold(),
                        entry.scope,
                        entry.apply,
                        entry.case_sensitive,
                    )
                    if duplicate_key in seen_entries:
                        glossary.warnings.append(
                            f"{origin}: дублирующееся правило {entry.source!r}"
                        )
                    seen_entries.add(duplicate_key)
                    glossary.entries.append(entry)
                    glossary.source_counts[origin] += 1
            if origin == "generated":
                conflicts = payload.get("conflicts", [])
                if isinstance(conflicts, list):
                    glossary.conflicts = len(conflicts)

        glossary._rebuild_indexes()
        return glossary

    @property
    def fingerprint(self) -> str:
        if not self.entries:
            return ""
        if self._fingerprint is not None:
            return self._fingerprint
        canonical = [
            {
                "source": entry.source,
                "target": entry.target,
                "scope": list(entry.scope),
                "apply": entry.apply,
                "case_sensitive": entry.case_sensitive,
                "priority": entry.priority,
                "origin": entry.origin,
            }
            for entry in sorted(
                self.entries,
                key=lambda item: (
                    item.source.casefold(),
                    item.scope,
                    item.apply,
                    item.origin,
                    item.priority,
                    item.target,
                ),
            )
        ]
        blob = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._fingerprint = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]
        return self._fingerprint

    def exact_translation(self, text: str, scope: str) -> str | None:
        candidates = [
            *self._exact_case_sensitive.get(text, ()),
            *self._exact_casefold.get(text.casefold(), ()),
        ]
        candidates = [entry for entry in candidates if entry.applies_to_scope(scope)]
        if not candidates:
            return None
        winner = max(candidates, key=lambda entry: _entry_rank(entry, scope))
        self.stats.exact_matches += 1
        return winner.target

    def mask_terms(
        self,
        text: str,
        mapping: dict[str, str],
        scope: str,
    ) -> tuple[str, int]:
        """Mask phrase/protect entries, restoring tokens to approved targets."""
        candidates = [
            (entry, pattern)
            for entry, pattern in self._term_patterns
            if entry.applies_to_scope(scope)
        ]
        candidates.sort(
            key=lambda item: (
                len(item[0].source),
                *_entry_rank(item[0], scope),
            ),
            reverse=True,
        )

        substitutions = 0
        for entry, pattern in candidates:
            def replace(_match: re.Match[str], target: str = entry.target) -> str:
                nonlocal substitutions
                token = f"[#{len(mapping)}#]"
                mapping[token] = target
                substitutions += 1
                return token

            text = pattern.sub(replace, text)

        self.stats.term_substitutions += substitutions
        return text, substitutions

    def _rebuild_indexes(self) -> None:
        self._exact_casefold = {}
        self._exact_case_sensitive = {}
        self._term_patterns = []
        self._fingerprint = None
        for entry in self.entries:
            if entry.case_sensitive:
                index = self._exact_case_sensitive
                key = entry.source
            else:
                index = self._exact_casefold
                key = entry.source.casefold()
            index.setdefault(key, []).append(entry)

            if entry.apply in {"phrase", "protect"}:
                flags = 0 if entry.case_sensitive else re.IGNORECASE
                pattern = re.compile(
                    rf"(?<!\w){re.escape(entry.source)}(?!\w)",
                    flags=flags,
                )
                self._term_patterns.append((entry, pattern))


def normalize_scope(scope: str) -> str:
    value = (scope or "*").strip().lower()
    return value or "*"


def ensure_user_glossary(path: Path, language: str = SUPPORTED_LANGUAGE) -> None:
    """Create an empty user glossary atomically on first use."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "language": language,
        "entries": [],
    }
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_payload(source: Any, origin: str, warnings: list[str]) -> dict | None:
    try:
        if isinstance(source, Path):
            text = source.read_text(encoding="utf-8")
        else:
            text = str(source)
        if not text.strip():
            warnings.append(f"{origin}: файл пуст")
            return None
        payload = json.loads(text)
        if not isinstance(payload, dict):
            warnings.append(f"{origin}: корнем JSON должен быть объект")
            return None
        return payload
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(f"{origin}: не удалось загрузить JSON: {exc}")
        return None


def _parse_entry(
    raw: Any,
    origin: str,
    warnings: list[str],
) -> GlossaryEntry | None:
    if not isinstance(raw, dict):
        warnings.append(f"{origin}: пропущено правило, которое не является объектом")
        return None
    source = raw.get("source")
    target = raw.get("target")
    apply_mode = raw.get("apply", "exact")
    if not isinstance(source, str) or not source.strip():
        warnings.append(f"{origin}: правило без source пропущено")
        return None
    if not isinstance(target, str) or not target.strip():
        warnings.append(f"{origin}: правило {source!r} без target пропущено")
        return None
    if apply_mode not in VALID_APPLY_MODES:
        warnings.append(f"{origin}: правило {source!r} имеет неизвестный apply")
        return None
    raw_scope = raw.get("scope", ["*"])
    if isinstance(raw_scope, str):
        raw_scope = [raw_scope]
    if not isinstance(raw_scope, list) or not all(isinstance(item, str) for item in raw_scope):
        warnings.append(f"{origin}: правило {source!r} имеет неверный scope")
        return None
    scopes = tuple(dict.fromkeys(normalize_scope(item) for item in raw_scope)) or ("*",)
    priority = raw.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        priority = 0
    case_sensitive = raw.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        warnings.append(
            f"{origin}: правило {source!r} имеет неверный case_sensitive"
        )
        case_sensitive = False
    return GlossaryEntry(
        source=source.strip(),
        target=target.strip(),
        scope=scopes,
        apply=apply_mode,
        case_sensitive=case_sensitive,
        priority=priority,
        note=str(raw.get("note", "")),
        origin=origin,
    )


def _entry_rank(entry: GlossaryEntry, scope: str) -> tuple[int, int, int]:
    return (
        ORIGIN_RANK.get(entry.origin, 0),
        entry.scope_specificity(scope),
        entry.priority,
    )
