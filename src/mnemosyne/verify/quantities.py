"""The numeric verify gate (invariant 6): every `checks` entry is recomputed with pint from CODATA
(scipy.constants) or curated anchors, never trusted from the model.

A check is {"quantity": "1 u c^2", "value": "931.494 MeV", "tolerance": "0.05%"}. The quantity is a
pint expression, where names like "molar gas constant" resolve through data/anchors.yaml and then
scipy's CODATA table.
"""

import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import pint
import yaml
from scipy import constants as sc  # pyright: ignore[reportMissingTypeStubs]

Status = Literal["pass", "fail", "skip"]
UREG = pint.UnitRegistry()
NUMBER = r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
UNIT = r"[A-Za-z_ΩμÅ°][A-Za-z_0-9ΩμÅ°]*(?:\^-?\d+)?"
# "12 V / 9.6585e-3 ohm" means (12 V) / (9.6585e-3 ohm); pint alone reads it left to right.
TERM = re.compile(rf"(?<![\w.^])(?<!\^-)({NUMBER})\s+({UNIT}(?:[ *]{UNIT})*)")


@dataclass(frozen=True)
class Verdict:
    status: Status
    detail: str


@cache
def anchors(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    consts: dict[str, dict[str, Any]] = data.get("constants") or {}
    return {str(k).lower(): str(v["value"]) for k, v in consts.items()}


@cache
def codata() -> dict[str, str]:
    table: dict[str, tuple[float, str, float]] = sc.physical_constants  # pyright: ignore[reportUnknownMemberType]
    return {name.lower(): f"{value!r} {unit}".strip() for name, (value, unit, _) in table.items()}


def _names(anchor_file: Path) -> list[tuple[str, str]]:
    merged = {**codata(), **anchors(anchor_file)}
    return sorted(merged.items(), key=lambda kv: -len(kv[0]))


def group_terms(expr: str) -> str:
    return TERM.sub(r"(\1 \2)", expr)


def evaluate(expr: str, anchor_file: Path) -> Any:  # a pint Quantity; pint is loosely typed
    text = expr.strip()
    low = text.lower()
    for name, value in _names(anchor_file):
        while name in low:                     # every occurrence: "m_a/(m_a + m_n)" names m_a twice
            start = low.index(name)
            text = text[:start] + f"({value})" + text[start + len(name):]
            low = text.lower()
    q: Any = UREG.parse_expression(group_terms(text))
    return q if hasattr(q, "dimensionality") else UREG.Quantity(float(q))


def tolerance(tol: str, expected: Any) -> float:
    """Relative tolerance: "0.05%" → 5e-4; an absolute "0.01 MeV" is converted relative to the expected value."""
    t = tol.strip()
    if t.endswith("%"):
        return float(t[:-1]) / 100
    q: Any = UREG.parse_expression(group_terms(t))
    return abs(float((q / expected).to("dimensionless").magnitude))


def check(entry: dict[str, str], anchor_file: Path) -> Verdict:
    try:
        expected = evaluate(entry["value"], anchor_file)
    except Exception as e:  # noqa: BLE001 -- anything unparseable is a skip, which sends the card to review
        return Verdict("skip", f"can't parse value {entry.get('value')!r}: {e}")
    try:
        actual = evaluate(entry["quantity"], anchor_file)
    except Exception as e:  # noqa: BLE001
        return Verdict("skip", f"can't compute {entry.get('quantity')!r}: {e}")
    if actual.dimensionality != expected.dimensionality:
        return Verdict("fail", f"{entry['quantity']} has units {actual.units:~}, not those of {entry['value']}")
    try:
        tol = tolerance(entry.get("tolerance", "1%"), expected)
    except Exception:  # noqa: BLE001
        tol = 0.01
    a = float(actual.to(expected.units).magnitude)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    e = float(expected.magnitude)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    rel = abs(a - e) / abs(e) if e else abs(a)
    ok = math.isfinite(rel) and rel <= tol
    return Verdict("pass" if ok else "fail",
                   f"{entry['quantity']} = {a:.6g} {expected.units:~} vs {entry['value']} ({rel:.2%} off, tolerance {tol:.2%})")


def needs_checks(note_type: str, tags: list[str]) -> bool:
    return note_type == "M-Estimate" or "type::anchor" in tags


def verify_card(note_type: str, tags: list[str], checks: list[dict[str, str]], anchor_file: Path) -> Verdict:
    """pass: every check holds (or there's no number to check); fail: some check is wrong; skip: can't tell."""
    if not checks:
        return Verdict("skip", "numeric card without checks") if needs_checks(note_type, tags) else Verdict("pass", "")
    verdicts = [check(c, anchor_file) for c in checks]
    for status in ("fail", "skip"):
        bad = [v.detail for v in verdicts if v.status == status]
        if bad:
            return Verdict(status, "; ".join(bad))  # type: ignore[arg-type]
    return Verdict("pass", "; ".join(v.detail for v in verdicts))
