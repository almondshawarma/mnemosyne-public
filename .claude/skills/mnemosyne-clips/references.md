# Work worth dissecting

Videos and channels Aman has flagged or that came out of a search for his subjects (2026-10-24). Credit the creator
when a finding or a frame is used. This is a reading list for the dissection loop in SKILL.md. What's been learned
from it so far is the taste guide, `TASTE.md` in the amanim repo.

## The look he's after: glowing, flowing, spacey

- **Arithmetes** ([YouTube](https://www.youtube.com/@Arithmetes)): *The Riemann Hypothesis is Barely True*
  (`JJp_g5d-KEQ`), *Continuing Where Euler Left Off* (`-zpNoT_6OLA`). The creator says it's entirely custom ManimGL,
  with no After Effects.
  - Strokes with a white-hot core and a wide halo, plus colour fringing.
  - Large blurred math deep behind the action, and a camera that drifts.
  - His [r/manim post](https://www.reddit.com/r/manim/comments/1vqljml/little_something_im_working_on_for_my_youtube/)
    (Willan's formula, a preview of his next video, saved and studied locally) moves fast-out with a long settle and
    never fully holds.
- **Knowable** ([Instagram](https://www.instagram.com/knowable_app/)): Griffiths electrodynamics examples as Reels:
  the metal ball in a uniform field (`DexJQjtuiL6`), the linear dielectric ball (`De0QslkuvCY`), Examples 3.3 and
  4.2.
  - Hundreds of cream streamlines that flow all the time (no holds at all).
  - A sphere shaded amber to blue by its surface charge.
  - A navy-to-black gradient that fades the field out where the equations sit.
  - Quick cuts through cases in the hook (1.6 s each), then one long explaining shot.

- **FloatHeadPhysics** ([YouTube](https://www.youtube.com/@FloatHeadPhysics)): *I finally understand the Pauli
  exclusion principle!* (`S5TCzvfJntA`). Studied 2026-10-25 (taste guide 20–28).
  - Manim-looking diagrams on a navy-to-violet backdrop, with glass line icons.
  - One apparatus carried for half an hour, and a case table whose input changes.
  - Zooms and the presenter card keyframed in an editor.

## His subjects, visualized

- **3Blue1Brown**:
  - *Divergence and curl* (`rB83DpBJQsE`): fields, Maxwell.
  - *How wiggling charges give rise to light* (`aXRTczANuIs`).
  - *But why would light "slow down"?* (`KTzGBJPuJwM`).
  - *But what is the Fourier Transform?* (`spUNpyF58BY`): radio.
  - *The determinant* (`Ip3X9LOh2dk`).
  - *But what is a Laplace Transform?* (`j0wJBEZdwLs`): its s-plane surface (7:19, 11:14) is the model for making
    3D look good.
- **ScienceClic English**: particle physics in 3D, closest to "spacey":
  - *Quantum Field Theory visualized* (`MmG2ah5Df4g`).
  - *Quantum Electrodynamics and Feynman Diagrams* (`X-FEU4mQWtE`).
  - *Quarks and Chromodynamics* (`oA3Sbm2PIGI`).
  - *The Standard Model* (`44k7cMj_kpY`).
- **Plasma and fusion.** Little is well made, so these are things to port and improve rather than copy:
  - *Animations for Physics and Astronomy*: *Magnetic Confinement Concepts* (`Sf1MGTD9xGY`).
  - Improbable Matter: *How nuclear fusion works (3)* (`gwOrbr8KWDs`).
  - NASA's magnetic reconnection visualizations (`mgUZwoR0gcE`).
  - Max Planck IPP's GENE-3D turbulence renders of Wendelstein 7-X.
- **Nuclear:** Higgsino physics, *Nuclear Fission Visually Explained* (`ojkeBfOckIk`).
- **Code to read** (Manim, particle and nuclear): NeutrinoHit/dvnanima (`feynman_scattering`, `BBN`, `pp_cycle`), and
  PTremper/feynmanim (a Feynman-diagram class for Manim CE).

## Studying them

- **YouTube, Vimeo, Instagram Reels, direct .mp4:** `uv run scripts/watch.py <link> --study 0:00-1:05`. Reels give
  no length, so a span past the end is cut to what's there. Portrait video gets tall filmstrip cells.
- **A saved file** (Reddit needs a login, so download it yourself):
  `uv run scripts/watch.py <file.mp4> --study 0:00-0:14 --creator "<who>" --title "<what>" --link "<post>"`. The
  file is read in place and never copied. It's studied on this machine, not the Obelisk.
