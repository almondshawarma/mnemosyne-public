import sqlite3

import pytest

from audit import audit, canon, loose


@pytest.mark.parametrize(("a", "b"), [
    (r"\(\frac{\mathrm{d}}{\mathrm{d}x}\sin(x)\)", r"\(\dfrac{d}{dx}\,\sin x\)"),
    (r"\(\dfrac{d}{dx}\,a^{x}\)", r"\(\frac{\mathrm{d}}{\mathrm{d}x}a^x\)"),
    ("Ideal Gas Law", "Ideal&nbsp;gas law"),
])
def test_canon_collapses_formatting(a: str, b: str) -> None:
    assert canon(a) == canon(b)


@pytest.mark.parametrize(("a", "b"), [
    (r"\(2^{12}\)", r"\(21^2\)"),
    (r"\((a+b)^2\)", r"\((a-b)^2\)"),
    (r"\(\begin{bmatrix}0&amp;-1\\1&amp;0\end{bmatrix}\)", r"\(\begin{bmatrix}0&amp;1\\-1&amp;0\end{bmatrix}\)"),
    ("ज", "ज़"),
    ("अ", "अं"),
])
def test_canon_keeps_meaning(a: str, b: str) -> None:
    assert canon(a) != canon(b)


def test_loose_ignores_filler_words() -> None:
    assert loose(r"Maclaurin series of&nbsp;\(\sin{x}\)") == loose(r"Maclaurin: \(\sin x\)")


def test_audit_dedupe_on_fixture(legacy_db: sqlite3.Connection) -> None:
    d = audit(legacy_db)["dedupe"]
    fronts = sorted(sorted(n["front"] for n in grp) for grp in d["duplicates"])
    assert fronts == [["Ideal Gas Law", "Ideal gas law"],
                      [r"\(\dfrac{d}{dx}\,\sin x\)", r"\(\frac{\mathrm{d}}{\mathrm{d}x}\sin(x)\)"]]
    assert d["ambiguous_prompts"] == []
    assert d["commutative_pairs"]["count"] == 2
