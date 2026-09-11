"""Every `path:line` citation in docs/sdks.md must resolve to a real line in a real file.

docs/sdks.md is the T-002 SDK inventory. Its value is that each claim carries a checkable
reference; this test is what makes the references checkable. It deliberately also resolves
paths that git does not track (`.venv/`, the PyInstaller `_internal/` tree under
`third_party/`) because those files exist on disk and are exactly the ones the claims are
about.

Reference syntax (see the header of docs/sdks.md):
  `some/path/inside/the/repo.py:123`     -> resolved against the repo root
  `/abs/path/outside.py:123`             -> used as is
  `~/path/outside.py:123`                -> expanduser
Only backticked tokens that contain a path separator and end in `:<digits>` count, so
prose like `pico_input_hz: 120.0` or `[-2.618, 2.618]` is not mistaken for a reference.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "sdks.md"

# A backticked token; we filter for the `path:line` shape afterwards.
BACKTICKED = re.compile(r"`([^`\n]+)`")
REFERENCE = re.compile(r"^(?P<path>\S.*?):(?P<line>\d+)$")

MIN_REFERENCES = 12

REQUIRED_SECTIONS = (
    "## 2. G1 left arm",
    "## 3. G1 waist",
    "## 4. Paxini DexH15",
    "## 5. DexH15 palm camera",
    "## 6. PxCap Pro glove",
    "## 7. Pico controller pose",
    "### 8.1 Logitech Brio",
    "### 8.2 Orbbec Ego",
)

VERDICT_KEYS = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "U2")


def _doc_text() -> str:
    assert DOC.is_file(), f"{DOC} is missing"
    return DOC.read_text(encoding="utf-8")


def _resolve(raw_path: str) -> Path:
    if raw_path.startswith("~"):
        return Path(raw_path).expanduser()
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def collect_references(text: str) -> list[tuple[str, int]]:
    """Return every (path, line) citation found in the document, in order."""
    references: list[tuple[str, int]] = []
    for token in BACKTICKED.findall(text):
        match = REFERENCE.match(token.strip())
        if match is None:
            continue
        raw_path = match.group("path").strip()
        # A reference is a path: it must contain a separator. This keeps prose out.
        if "/" not in raw_path:
            continue
        references.append((raw_path, int(match.group("line"))))
    return references


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def test_doc_exists_and_has_every_device_section() -> None:
    text = _doc_text()
    missing = [section for section in REQUIRED_SECTIONS if section not in text]
    assert not missing, f"docs/sdks.md is missing sections: {missing}"


def test_every_assumption_has_a_verdict() -> None:
    text = _doc_text()
    verdict_block = text.split("## 9. Verdicts")[-1]
    missing = [key for key in VERDICT_KEYS if f"**{key} " not in verdict_block]
    assert not missing, f"no verdict line for: {missing}"


def test_references_resolve_to_existing_lines() -> None:
    text = _doc_text()
    references = collect_references(text)

    unique = sorted(set(references))
    print(f"\ndocs/sdks.md: checked {len(references)} path:line references ({len(unique)} unique)")

    assert len(references) >= MIN_REFERENCES, (
        f"expected at least {MIN_REFERENCES} path:line references, found {len(references)}"
    )

    problems: list[str] = []
    for raw_path, line_no in unique:
        resolved = _resolve(raw_path)
        if not resolved.is_file():
            problems.append(f"{raw_path}:{line_no} -> no such file ({resolved})")
            continue
        total = _count_lines(resolved)
        if not 1 <= line_no <= total:
            problems.append(f"{raw_path}:{line_no} -> file has {total} lines")
    assert not problems, "broken references in docs/sdks.md:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("device", ["G1 left arm", "G1 waist", "DexH15", "PxCap Pro", "Brio", "Orbbec"])
def test_device_named_in_summary_table(device: str) -> None:
    assert device.split()[-1] in _doc_text()
