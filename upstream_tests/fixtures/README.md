# Fixtures

`original_samples/` contains a small set of unmodified AE2 GuideME pages chosen
from the canonical user-supplied original corpus. They cover large component
pages, tables, links, YAML front matter, and a real horizontal rule after front
matter.

`broken_samples/` contains intentionally non-canonical translated output used
only to prove that structural regressions are rejected.

The full original corpus is optional and intentionally not tracked in ordinary
commits:

`ae2guide-original-reference.zip`

When that file is present, full-corpus tests automatically run against all 125
Markdown pages and also verify the 123 SNBT + 40 PNG inventory. Its expected
SHA-256 is stored in `ae2guide-original-reference.sha256`.
