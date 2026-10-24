"""Deterministic TeX → speech for simple cards (PLAN §4.7 speakify, arithmetic tier).

`speak(field)` returns what a careful reader would say, or None when anything in the
field falls outside the small grammar below. None means "not audio-ready"; there is
no partial output. Numerals stay numerals: iOS TTS reads "4096" and "0.125" correctly.
Fractions and ordinals are spelled out, because TTS reads "1/8" as a date.
"""
import html
import re

SMALL = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
DENOM = {2: ("half", "halves"), 3: ("third", "thirds"), 4: ("quarter", "quarters"), 5: ("fifth", "fifths"),
         6: ("sixth", "sixths"), 7: ("seventh", "sevenths"), 8: ("eighth", "eighths"), 9: ("ninth", "ninths"),
         10: ("tenth", "tenths"), 11: ("eleventh", "elevenths"), 12: ("twelfth", "twelfths"),
         16: ("sixteenth", "sixteenths")}
FUNCS = {"sin": "sine", "cos": "cosine", "tan": "tangent", "sec": "secant", "csc": "cosecant", "cot": "cotangent",
         "ln": "the natural log of"}
SIMPLE = {"times": "times", "cdot": "times", "pi": "pi", "approx": "about", "pm": "plus or minus",
          "mu": "mu", "degree": "degrees", "infty": "infinity"}
SKIP = {"displaystyle", "left", "right", "quad"}
TOKEN = re.compile(r"\\[A-Za-z]+|\\.|\d+\.?\d*|\.\d+|[A-Za-z]|\S")
PLAIN = re.compile(r"^[A-Za-z0-9 ,.'’?!:;/()\-]*$")
ROMAN = {"(I)": "one", "(II)": "two", "(III)": "three", "(IV)": "four"}


class Unspeakable(Exception):
    pass


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fraction(num: int, den: int) -> str | None:
    if den not in DENOM or not 0 < num <= 20:
        return None
    return f"{SMALL[num]} {DENOM[den][0] if num == 1 else DENOM[den][1]}"


class Parser:
    def __init__(self, tex: str) -> None:
        self.toks = [t for t in TOKEN.findall(tex) if t[1:] not in SKIP and t not in ("\\,", "\\ ", "\\;", "\\!")]
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> str:
        if self.i >= len(self.toks):
            raise Unspeakable("unexpected end")
        self.i += 1
        return self.toks[self.i - 1]

    def group(self) -> list[str]:
        """Raw tokens of a {…} group, or of one token."""
        if self.peek() != "{":
            return [self.take()]
        self.take()
        depth, out = 1, []
        while True:
            t = self.take()
            depth += {"{": 1, "}": -1}.get(t, 0)
            if depth == 0:
                return out
            out.append(t)

    def sub(self, toks: list[str]) -> str:
        p = Parser("")
        p.toks = toks
        return p.expr()

    def expr(self) -> str:
        words: list[str] = []
        while self.peek() is not None:
            words.append(self.atom(first=not words))
        return " ".join(w for w in words if w)

    def atom(self, first: bool) -> str:
        t = self.take()
        if re.fullmatch(r"\d+\.?\d*|\.\d+", t):
            return self.number(t)
        if t == "-":
            return "negative" if first else "minus"
        if t in {"+": 1, "=": 1}:
            return {"+": "plus", "=": "equals"}[t]
        if t in ("(", ")"):
            return ""
        if t == "\\%" or t == "%":
            return "percent"
        name = t[1:] if t.startswith("\\") else None
        if name in ("frac", "dfrac", "tfrac"):
            num, den = self.group(), self.group()
            simple = len(num) == 1 and len(den) == 1 and num[0].isdigit() and den[0].isdigit()
            if simple and (words := fraction(int(num[0]), int(den[0]))):
                return words
            return f"{self.sub(num)} over {self.sub(den)}"
        if name == "sqrt":
            if self.peek() == "[":
                raise Unspeakable("nth root")
            arg = self.sub(self.group())
            return f"root {arg}" if re.fullmatch(r"\d+", arg) else f"the square root of {arg}"
        if name in FUNCS:
            power = ""
            if self.peek() == "^":
                self.take()
                power = " " + self.power(self.group())
            if self.peek() == "(":
                arg = self.paren()
            elif self.peek() == "{":
                arg = self.sub(self.group())
            else:
                arg = self.atom(first=False)
            return f"{FUNCS[name]}{power} of {arg}".replace("of of", "of")
        if name == "log":
            base = ""
            if self.peek() == "_":
                self.take()
                base = f" base {self.sub(self.group())}"
            return f"log{base} of {self.atom(first=False)}"
        if name == "text":
            inner = "".join(self.group())
            if not PLAIN.match(inner):
                raise Unspeakable(inner)
            return inner
        if name == "overline":
            raise Unspeakable("overline outside a decimal")
        if name in SIMPLE:
            return SIMPLE[name]
        if t == "e":
            return "e"
        raise Unspeakable(t)

    def paren(self) -> str:
        self.take()
        toks = []
        while (t := self.take()) != ")":
            toks.append(t)
        return self.sub(toks)

    def power(self, exp: list[str]) -> str:
        s = "".join(exp)
        if s == "2":
            return "squared"
        if s == "3":
            return "cubed"
        if re.fullmatch(r"-?\d+", s):
            return f"to the minus {s[1:]}" if s.startswith("-") else f"to the {ordinal(int(s))}"
        if s == "\\circ":
            return "degrees"
        raise Unspeakable(f"power {s}")

    def number(self, t: str) -> str:
        if self.peek() == "\\overline":
            self.take()
            rep = "".join(self.group())
            if not rep.isdigit():
                raise Unspeakable("overline")
            whole, _, dec = t.partition(".")
            lead = " ".join(dec)
            said = f"{whole or 0} point {lead + ', ' if lead else ', '}{' '.join(rep)} repeating,"  # trailing comma: a pause
            return said.replace("point , ", "point, ")
        if self.peek() == "^":
            self.take()
            return f"{t} {self.power(self.group())}"
        return t


def speak_tex(tex: str) -> str | None:
    try:
        return Parser(tex).expr() or None
    except Unspeakable:
        return None


def speak(field: str) -> str | None:
    """Whole field: plain text and \\( \\) math, in order. None if any part can't be spoken."""
    if "<img" in field or "{{c" in field or "[sound:" in field:
        return None
    s = html.unescape(re.sub(r"<br\s*/?>", ", ", re.sub(r"<(?!br)[^>]+>", "", field))).replace("\xa0", " ")
    parts: list[str] = []
    for i, chunk in enumerate(re.split(r"\\\((.*?)\\\)", s)):
        chunk = chunk.strip()
        if not chunk:
            continue
        if i % 2:
            said = speak_tex(chunk)
            if said is None:
                return None
            parts.append(said)
        else:
            for roman, words in ROMAN.items():
                chunk = chunk.replace(roman, words)
            chunk = chunk.replace(" / ", " or ")
            if not PLAIN.match(chunk) or "\\" in chunk:
                return None
            parts.append(re.sub(r"^\((.*)\)$", r", \1", chunk))
    out = re.sub(r"\s+", " ", " ".join(parts)).replace(" ,", ",").strip(" ,")
    return out or None


# --- domain-specific prompts -------------------------------------------------
SI_PREFIX = re.compile(r"^\s*(?:\\\(\\(mu)\\\)|([A-Za-z]{1,2}))\s*\((\w+)\)\s*$")
ION_PART = re.compile(r"\\text\{([A-Za-z()]+)\}(?:_(?:(\d)|\{(\d+)\}))?")
ION_CHARGE = re.compile(r"\^(?:\{(\d?[+-])\}|([+-]))$")


def speak_si_prefix(front: str) -> str | None:
    """'k (kilo)' → 'kilo, lowercase k'. Symbol case is the whole point of these cards."""
    m = SI_PREFIX.match(html.unescape(front).replace("\xa0", " "))
    if not m:
        return None
    if m[1]:
        return f"{m[3]}, symbol mu"
    letters = ", ".join(("capital " if ch.isupper() else "lowercase ") + ch.upper() for ch in m[2])
    return f"{m[3]}, {letters}"


def speak_ion(back: str) -> str | None:
    """'\\(\\text{Fe(CN)}_6^{4-}\\)' → 'F E, C N, 6, 4 minus'; also C_2H_3O_2^- written as several \\text{} parts."""
    m = re.fullmatch(r"\s*\\\((.*)\\\)\s*", html.unescape(back))
    if not m:
        return None
    tex = m[1].replace(" ", "")
    charge = ION_CHARGE.search(tex)
    if not charge:
        return None
    body, words = tex[:charge.start()], []
    pos = 0
    for part in ION_PART.finditer(body):
        if part.start() != pos:
            return None
        pos = part.end()
        words += [" ".join(ch.upper() for ch in el) for el in re.findall(r"[A-Z][a-z]?", part[1])]
        if sub := part[2] or part[3]:
            words.append(sub)
    if pos != len(body) or not words:
        return None
    sign = charge[1] or charge[2]
    polarity = "plus" if sign.endswith("+") else "minus"
    return f"{', '.join(words)}, {sign[:-1] + ' ' if sign[:-1] else ''}{polarity}"
