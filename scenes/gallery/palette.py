"""Gallery: the palette roles as they really render, before you choose which role carries what.

Each role as a line, a fill, and its own name, with its contrast against GROUND and the roles it can't be told apart
from. All of it is computed from the colors mn_style loaded (Aman's tokens when brand.py is beside it), so the still
is the truth for the render it came from.
"""

from amanim import confusable, contrast_ratio
from mn_style import *

CLIP = {
    "title": "Gallery: the palette roles",
    "technique": "palette swatch: every role on GROUND, its contrast, and which roles look alike",
    "watch_for": "Roles under 3:1 can't carry a line; pairs marked with ≈ read as one color, so don't give them two "
                 "things the viewer must tell apart.",
    "scene": "Palette",
}

ROLES = {"INK": INK, "MUTED": MUTED, "GRID": GRID, "ACCENT": ACCENT, "WARM": WARM, "FLOW": FLOW, "WARN": WARN,
         "STRUCT": STRUCT, "GOOD": GOOD, "BAD": BAD}
LOOK_ALIKE = 20.0    # CIEDE2000 under this reads as one color at line widths


def swatch(name: str, color: ManimColor, alike: list[str]) -> VGroup:
    """One row: a stroke, a fill, the name in its own color, and the numbers in INK."""
    ratio = contrast_ratio(color, GROUND)
    line = Line(ORIGIN, 1.1 * RIGHT, color=color, stroke_width=8)
    chip = Square(side_length=0.45, stroke_width=0, fill_color=color, fill_opacity=1)
    label = words(rf"\texttt{{{name}}}", size=SMALL, color=color)
    numbers = words(f"{ratio:.1f}:1", size=SMALL, color=INK if ratio >= 3 else BAD)
    row = VGroup(line, chip, label, numbers).arrange(RIGHT, buff=0.3)
    label.align_to(chip, LEFT).shift((chip.width + 0.3) * RIGHT)   # names line up whatever their width
    numbers.move_to(chip.get_center() + 3.1 * RIGHT)
    if alike:
        row.add(words(r"$\approx$ " + ", ".join(alike), size=SMALL, color=MUTED).next_to(numbers, RIGHT, buff=0.3))
    return row


class Palette(MnScene):
    def construct(self) -> None:
        pairs = confusable({k: v for k, v in ROLES.items()}, threshold=LOOK_ALIKE)
        alike: dict[str, list[str]] = {k: [] for k in ROLES}
        for a, b, _ in pairs:
            alike[a].append(b)
            alike[b].append(a)

        self.next_section("roles")
        head = words("palette roles on GROUND" + ("" if BRANDED else " (fallback: no brand.py)"), size=SMALL,
                     color=MUTED).to_edge(UP, buff=SAFE + 0.1)
        rows = VGroup(*(swatch(k, v, alike[k]) for k, v in ROLES.items()))
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.22).next_to(head, DOWN, buff=0.35).to_edge(LEFT, buff=1.2)
        self.play(FadeIn(head), LaggedStart(*(FadeIn(r, shift=0.1 * RIGHT) for r in rows), lag_ratio=0.08))
        self.wait(1)

        # The question a scene actually has: how many things can be told apart at once?
        self.next_section("how many at once")
        self.clean()
        distinct = self.distinct_set()
        lines = VGroup(*(VGroup(Line(ORIGIN, 3 * RIGHT, color=ROLES[k], stroke_width=8),
                                words(rf"\texttt{{{k}}}", size=SMALL, color=ROLES[k])).arrange(RIGHT, buff=0.4)
                         for k in distinct)).arrange(DOWN, aligned_edge=LEFT, buff=0.35).move_to(0.3 * UP)
        self.play(LaggedStart(*(Create(g) for g in lines), lag_ratio=0.15))
        self.note(f"{len(distinct)} line colors that read as different: " + ", ".join(distinct) + ".")
        self.wait(1.5)

    @staticmethod
    def distinct_set() -> list[str]:
        """Greedy: the roles a line can be drawn in (3:1 or better), dropping any that looks like one already kept.
        INK and MUTED are left out: they're text and axes, not things to tell apart."""
        kept: list[str] = []
        for k in ("ACCENT", "FLOW", "GOOD", "BAD", "WARM", "WARN", "STRUCT"):
            if contrast_ratio(ROLES[k], GROUND) < 3:
                continue
            if all(not confusable({k: ROLES[k], j: ROLES[j]}, threshold=LOOK_ALIKE) for j in kept):
                kept.append(k)
        return kept
