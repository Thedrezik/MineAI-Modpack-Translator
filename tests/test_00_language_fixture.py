"""Keep legacy test fixtures compatible with the current language schema.

The production language definitions include a ``regex`` field. The older
``test_llm_common`` fixture predates that field, so importing and extending it
here prevents the test from exercising an invalid schema.
"""

import test_llm_common


test_llm_common.TARGET_LANG.setdefault("regex", r"[А-Яа-яЁё]")
