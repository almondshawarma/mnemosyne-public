"""Gallery: a circuit with its currents moving along the wires, solved from the circuit itself.

amanim.circuits draws the symbols (resistor, cell, wire) from node coordinates and solves the network from its graph
(branch_currents: AᵀCA x = AᵀCb). flow_dots moves dots along each branch at a speed ∝ its current, against the
branch's direction when the current is negative, all driven by one ValueTracker so the flow runs through every later
play. The circuit is mesh-matrix's, so the numbers can be checked against that clip.
"""

from amanim import branch_currents, cell, flow_dots, resistor, wire
from mn_style import *

CLIP = {
    "title": "Gallery: currents moving through a circuit",
    "technique": "networks: amanim.circuits symbols; branch_currents solves the graph; flow_dots speed ∝ current",
    "watch_for": "The middle branch carries the sum of the other two (KCL at the top node), and its dots move "
                 "fastest.",
    "scene": "Circuit",
}

R1, R2, R3 = 2, 4, 2           # ohms: left, right, middle
E1, E2 = 10, 10                # volts, + at the top in both outer branches

# Nodes: 0 bottom, 1 top. Each branch is an edge oriented bottom -> top: left (E1, R1), middle (R3), right (E2, R2).
A = [[-1, 1], [-1, 1], [-1, 1]]
_, I = branch_currents(A, conductance=[1 / R1, 1 / R3, 1 / R2], sources=[E1, 0, E2])

TL, TM, TR = np.array([-5.0, 1.9, 0]), np.array([-2.5, 1.9, 0]), np.array([0.0, 1.9, 0])
BL, BM, BR = np.array([-5.0, -2.3, 0]), np.array([-2.5, -2.3, 0]), np.array([0.0, -2.3, 0])


def amps(i: float) -> str:
    return f"{abs(i):g}" + r"\,\mathrm{A}"


class Circuit(MnScene):
    def construct(self) -> None:
        self.next_section("the circuit")
        parts = VGroup(
            cell(BL, TL, plus_toward_b=True, color=INK, plus_color=MUTED), resistor(TL, TM, color=INK),
            resistor(TR, TM, color=INK), cell(BR, TR, plus_toward_b=True, color=INK, plus_color=MUTED),
            resistor(TM, BM, color=INK), wire(BL, BR, color=INK))
        values = VGroup(tex(rf"{E1}\,\mathrm{{V}}", size=SMALL, color=MUTED).next_to((TL + BL) / 2, LEFT, buff=0.55),
                        tex(rf"{R1}\,\Omega", size=SMALL, color=MUTED).next_to((TL + TM) / 2, UP, buff=0.3),
                        tex(rf"{R2}\,\Omega", size=SMALL, color=MUTED).next_to((TM + TR) / 2, UP, buff=0.3),
                        tex(rf"{R3}\,\Omega", size=SMALL, color=MUTED).next_to((TM + BM) / 2, RIGHT, buff=0.3),
                        tex(rf"{E2}\,\mathrm{{V}}", size=SMALL, color=MUTED).next_to((TR + BR) / 2, RIGHT, buff=0.8))
        self.keep_clear(parts)
        self.play(Create(parts), FadeIn(values))
        self.note("Two batteries, three resistors: mesh-matrix's circuit.")

        self.next_section("the currents")
        # Each path runs bottom -> top along its branch, the edge's own direction, so flow_dots' sign is I's sign.
        paths = [wire(BM, BL, TL, TM), wire(BM, TM), wire(BM, BR, TR, TM)]
        clock = ValueTracker(0)
        flows = [flow_dots(p, clock, current=i, color=FLOW) for p, i in zip(paths, I, strict=True)]
        algebra = arrange_rows([
            tex(r"A^TCA\,x=A^TCb"),
            tex(r"I_{\text{left}}=", amps(I[0]), r"\ \uparrow", color=FLOW),
            tex(r"I_{\text{mid}}=", amps(I[1]), r"\ \downarrow" if I[1] < 0 else r"\ \uparrow", color=FLOW),
            tex(r"I_{\text{right}}=", amps(I[2]), r"\ \uparrow", color=FLOW),
        ]).move_to(3.6 * RIGHT + 0.2 * UP)
        self.play(*(FadeIn(f) for f in flows), FadeIn(algebra),
                  clock.animate(rate_func=linear).set_value(1.5), run_time=1.5)
        self.note(rf"KCL at the top: ${amps(I[0])}+{amps(I[2])}$ in, ${amps(I[1])}$ down the middle.")
        self.play(clock.animate.set_value(5), rate_func=linear, run_time=3.5)
