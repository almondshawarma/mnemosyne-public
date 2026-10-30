# Storyboard: Faraday rotation in a magnetized plasma

**What it teaches:** a magnetized plasma turns the plane of linearly polarized light, because the two circular halves
of the light see different refractive indices. The electrons gyrate one way, so the half that turns with them is held
back differently. The amount, ψ = C λ² ∫ n_e B∥ dl, is what polarimetry measures in a tokamak, and what your
ionosphere station will measure in the sky.

**Length:** about 65 s, in five sections. **Look:** `"look": "ethereal"`, no backdrop (the plasma column is the
setting).

## What's computed (D-014)

| What | How (amanim) | Checked by |
|---|---|---|
| n_R, n_L along B | `plasma.n_rl`, cold plasma (Chen §4.18) | cutoffs, the ω_c resonance, n → 1 limits (`tests/test_plasma.py`) |
| The field E(x, t) everywhere | half R plus half L, phases integrated through vacuum → plasma → vacuum (`plasma.linear_from_phases`) | the sum always lies on a line at ψ = (φ_L − φ_R)/2 |
| The twist rate inside | ψ′ = (ω/2c)(n_L − n_R), with ω = 3ω_c, ω_p = 1.2ω_c (normalized) | the exact angle against the ω_p²ω_c/2cω² limit |
| Which way electrons gyrate | `lorentz_path` with q < 0, B along +x | the R wave turns the same way (a test, not a guess) |
| The two real-world angles | `plasma.rotation`, CODATA via `C_RM` = 2.63×10⁻¹³ | a tokamak FIR beam ≈ 21°, the exact angle within 0.1% |

**Honest scale:** ω is only 3ω_c here, so the twist fits on screen (59° over the column). A real tokamak laser is at
ω ≈ 600ω_c, and the twist is about 21° over 2 m. Time is slowed: the wave and the electrons keep their true 3 : 1
ratio of rates.

## The look: executive decisions

- **The light is the hottest thing on screen** (ACCENT, bright, full halo): the E vectors and the curve through their
  tips. The polarization plane is a faint glowing ribbon behind them, so the eye reads "the plane" without being told.
- **The R and L halves are coloured by physics:** R is in the electrons' colour (FLOW), because it turns with them; L
  is WARM.
- **Electrons pulse.** Small sparks circling field lines, each pulsing gently at its own phase (`pulse`), so the plasma
  shimmers instead of blinking. A medium glow.
- **Field lines are a dim, steady glow** (thin, low halo): structure, not subject. The B arrow is brighter only when
  it's named.
- **Glow varies by role:** subject > actors > structure. Text never blooms hard (ETHEREAL's threshold).
- **Motion:**
  - The wave never stops (it's light).
  - Electrons never stop.
  - Arrivals use `settle`.
  - One camera move only, a slow push in while the twist reveals itself (budgeted: about 4 s of camera time).
- **Space:** the 3D wave runs left to right through the middle. The phasor inset (looking down the beam) sits in the
  upper-left corner, pinned flat, so 2D and 3D never fight for the same space.

## Shots

**1 · Light, polarized (≈ 10 s).**
- **Picture:** a computed oblique view of the beam axis.
  - A linearly polarized wave runs along it: about 60 E vectors in one plane, travelling.
  - A faint flat ribbon marks the plane.
- **Caption:** "Linearly polarized light: E swings in one plane."

**2 · Two circles make a line (≈ 13 s).**
- **Picture:** the inset fades in, a view down the beam axis.
  - An R arrow turns counter-clockwise and an L arrow clockwise, both half length.
  - Their sum (bright) slides back and forth along one line. Tip trails show the circles.
- **Caption:** "Any line is two opposite circles, added: half R, half L."

**3 · Enter the plasma (≈ 12 s).**
- **Picture:**
  - A column of plasma settles in around the middle third of the beam: dim field lines along the beam, with B
    labelled.
  - Electrons fade in, circling the lines counter-clockwise as seen from +B, pulsing.
  - A small R-arrow ghost in the inset turns with them.
- **Caption:** "In B, electrons gyrate one way. The R half turns with them."

**4 · The plane turns (≈ 18 s).**
- **Picture:**
  - The wave now runs through the column. Inside, the R half runs ahead (n_R < n_L), and the vectors' plane twists
    along the beam.
  - The ribbon becomes a twisted strip, and the exit plane is turned by ψ.
  - The inset shows the two arrows slipping in phase and the sum's line rotating, with ψ marked by an arc.
  - The camera pushes in slowly.
  - The equation fades in: ψ = (ω/2c)(n_L − n_R) L.
- **Caption:** "Inside, the two halves travel at different speeds: the plane turns. Faraday rotation."

**5 · What it measures (≈ 12 s).**
- **Picture:** the 3D scene dims back, and two readouts appear, computed:
  - **Tokamak polarimetry:** λ = 118.8 µm, n_e = 10²⁰ m⁻³, B∥ = 0.5 T, 2 m → ψ ≈ 21°.
  - **Your ionosphere station:** 150 MHz, TEC = 10¹⁷ m⁻², B∥ = 50 µT → ψ ≈ 5.3 rad.
  - Above them: ψ = C λ² ∫ n_e B∥ dl, with C = e³/(8π²ε₀m_e²c³) = 2.63×10⁻¹³.
- **Caption:** "Measure ψ, and you've measured ∫ n_e B∥ dl: polarimetry."

## Risks, and what to check in the stills

- The twisted ribbon can turn edge-on at the wrong place in the view: compute the view from the beam axis and the
  mid-column plane.
- Labels in 3D must face the camera (`label`), and the inset must not cover the column.
- Render cost: about 60 `vec3`s plus the ribbon, redrawn every frame, with about 4 s of camera motion. Expect 6–9
  minutes at 720p30 with the look; draft at `-ql` first.
