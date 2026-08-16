from __future__ import annotations

import json
import re

from .core import ProtectedFragment, ValidationError
from .patchouli_base import PatchouliBookJsonAdapter, _Node


_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")


class ModonomiconGsonSpanJsonAdapter(PatchouliBookJsonAdapter):
    """Patchouli-style span parser with the narrow Gson leniency Modonomicon uses.

    Modonomicon 1.21.1 loads book data through Minecraft's
    ``SimpleJsonResourceReloadListener`` with a normal Gson instance. Legacy
    Gson accepts raw LF/CR/TAB characters inside quoted values. Some real
    Modonomicon books rely on that behavior even though strict RFC JSON parsers
    reject the same bytes.

    Keep the compatibility surface deliberately small: object keys, numbers,
    arrays/objects and all other syntax still use the strict span parser. Only
    quoted *values* may contain the three whitespace controls observed in the
    real corpus. Identity reconstruction preserves the original source bytes;
    changed strings are emitted by the parent adapter through ``json.dumps``
    and therefore become strict JSON.
    """

    def _parse_value(self, text: str, index: int) -> tuple[_Node, int]:
        index = self._skip_ws(text, index)
        if index < len(text) and text[index] == '"':
            end = self._scan_gson_string_end(text, index)
            token = text[index:end]
            try:
                value = json.loads(token)
            except json.JSONDecodeError:
                value = self._decode_gson_lenient_string(token)
            return _Node("string", index, end, value), end
        return super()._parse_value(text, index)

    @staticmethod
    def _scan_gson_string_end(text: str, start: int) -> int:
        index = start + 1
        while index < len(text):
            char = text[index]
            if char == "\\":
                if index + 1 >= len(text):
                    raise ValidationError("Unterminated Modonomicon JSON escape")
                index += 2
                continue
            if char == '"':
                return index + 1
            if ord(char) < 0x20 and char not in "\t\r\n":
                raise ValidationError(
                    "Unsupported control character in Gson-lenient Modonomicon string"
                )
            index += 1
        raise ValidationError("Unterminated Modonomicon JSON string")

    @classmethod
    def _decode_gson_lenient_string(cls, token: str) -> str:
        if len(token) < 2 or token[0] != '"' or token[-1] != '"':
            raise ValidationError("Invalid Modonomicon JSON string token")
        inner = token[1:-1]
        out: list[str] = []
        index = 0
        escapes = {
            '"': '"',
            "'": "'",
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        while index < len(inner):
            char = inner[index]
            if char != "\\":
                if ord(char) < 0x20 and char not in "\t\r\n":
                    raise ValidationError(
                        "Unsupported control character in Gson-lenient Modonomicon string"
                    )
                out.append(char)
                index += 1
                continue

            if index + 1 >= len(inner):
                raise ValidationError("Unterminated Modonomicon JSON escape")
            escaped = inner[index + 1]
            if escaped == "u":
                if index + 6 > len(inner):
                    raise ValidationError("Truncated Unicode escape in Modonomicon JSON string")
                raw = inner[index + 2 : index + 6]
                try:
                    code = int(raw, 16)
                except ValueError as exc:
                    raise ValidationError(
                        "Invalid Unicode escape in Modonomicon JSON string"
                    ) from exc
                index += 6
                if (
                    0xD800 <= code <= 0xDBFF
                    and inner[index : index + 2] == "\\u"
                    and index + 6 <= len(inner)
                ):
                    try:
                        low = int(inner[index + 2 : index + 6], 16)
                    except ValueError:
                        low = -1
                    if 0xDC00 <= low <= 0xDFFF:
                        out.append(chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00))
                        index += 6
                        continue
                out.append(chr(code))
                continue

            # Gson's legacy reader accepts escaped raw line breaks and a few
            # otherwise non-standard escapes. Preserve the escaped character
            # exactly as the runtime presents it to book parsing.
            out.append(escapes.get(escaped, escaped))
            index += 2
        return "".join(out)

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        masked, protected = super()._protect(text)
        # Parent protection already masks CR/LF. Raw TABs are source-owned
        # formatting in the corpus and must not be editable by a translator.
        tab_positions = [index for index, char in enumerate(masked) if char == "\t"]
        if not tab_positions:
            return masked, protected

        existing = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(masked)]
        next_id = max(existing) + 1 if existing else 0
        out: list[str] = []
        extra: list[ProtectedFragment] = []
        cursor = 0
        for offset, start in enumerate(tab_positions):
            out.append(masked[cursor:start])
            placeholder = f"[#{next_id + offset}#]"
            out.append(placeholder)
            extra.append(ProtectedFragment(placeholder, "\t"))
            cursor = start + 1
        out.append(masked[cursor:])
        final_masked = "".join(out)
        fragments = protected + tuple(extra)
        ordered = tuple(
            sorted(fragments, key=lambda fragment: final_masked.index(fragment.placeholder))
        )
        return final_masked, ordered
