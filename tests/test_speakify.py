import pytest

from speakify import speak, speak_ion, speak_si_prefix


@pytest.mark.parametrize(("field", "said"), [
    (r"\(17\times8\)", "17 times 8"),
    ("136", "136"),
    (r"\(21^2\)", "21 squared"),
    (r"\(13^3\)", "13 cubed"),
    (r"\(2^{12}\)", "2 to the 12th"),
    (r"\(3^1\)", "3 to the 1st"),
    (r"\(\sqrt{2}\)", "root 2"),
    (r"\(\approx 1.414\)", "about 1.414"),
    (r"\(\frac{3}{8}\)", "three eighths"),
    (r"\(\frac{1}{2}\)", "one half"),
    (r"\(0.16\overline{6}\)", "0 point 1 6, 6 repeating"),
    (r"\(0.\overline{142857}\)", "0 point, 1 4 2 8 5 7 repeating"),
    (r"\(0.375 = 37.5\%\)", "0.375 equals 37.5 percent"),
    (r"\(\sin(\frac{3\pi}{2})\)", "sine of 3 pi over 2"),
    (r"\(-\frac{\sqrt{2}}{2}\)", "negative root 2 over 2"),
    (r"\(\text{undefined}\)", "undefined"),
    (r"\(\log_{10} 3\)", "log base 10 of 3"),
    (r"\(10^{-6}\)&nbsp;(millionth)", "10 to the minus 6, millionth"),
    ("Divisibility by 3?", "Divisibility by 3?"),
    (r"\(0.\overline{5} \approx 0.556\)", "0 point, 5 repeating, about 0.556"),
    ("mercury (I) / mercurous", "mercury one or mercurous"),
])
def test_speak(field: str, said: str) -> None:
    assert speak(field) == said


@pytest.mark.parametrize("field", [
    r"\(\dfrac{d}{dx}\,\sin x\)",          # variables are out of scope: no guessing
    r"\(\sqrt[3]{8}\)",
    "<img src=\"cone.png\">",
    "The {{c1::ampere}} is the unit of current.",
    r"\(\log a+\log b\) — add logs",
    "",
])
def test_unspeakable_is_none(field: str) -> None:
    assert speak(field) is None


@pytest.mark.parametrize(("front", "said"), [
    ("k (kilo)", "kilo, lowercase K"),
    ("M (mega)", "mega, capital M"),
    ("da (deca)", "deca, lowercase D, lowercase A"),
    (r"\(\mu\)&nbsp;(micro)", "micro, symbol mu"),
    ("kilo", None),
])
def test_si_prefix(front: str, said: str | None) -> None:
    assert speak_si_prefix(front) == said


@pytest.mark.parametrize(("back", "said"), [
    (r"\(\text{OH}^-\)", "O, H, minus"),
    (r"\(\text{SO}_4^{2-}\)", "S, O, 4, 2 minus"),
    (r"\(\text{Fe(CN)}_6^{4-}\)", "F E, C, N, 6, 4 minus"),
    (r"\(\text{C}_2\text{H}_3\text{O}_2^-\)", "C, 2, H, 3, O, 2, minus"),
    (r"\(\text{NH}_4^+\)", "N, H, 4, plus"),
    (r"\(PV = nRT\)", None),
])
def test_ion(back: str, said: str | None) -> None:
    assert speak_ion(back) == said
