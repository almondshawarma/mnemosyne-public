"""MnScene3D keeps a real 3D camera, and the layout check runs on what the camera draws, not on world coordinates."""

import json

import numpy as np
import pytest
from manim import (
    DEGREES,
    DOWN,
    OUT,
    RIGHT,
    UP,
    UR,
    Circle,
    DecimalNumber,
    FadeIn,
    FadeOut,
    LaggedStart,
    Line,
    Mobject,
    MovingCameraScene,
    Square,
    Tex,
    Text,
    ThreeDCamera,
    ThreeDScene,
    ValueTracker,
    VGroup,
    tempconfig,
)
from mn_style import MnScene, MnScene3D, words


def layout_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("MN-LAYOUT")]


def test_the_3d_scene_keeps_a_3d_camera() -> None:
    mro = MnScene3D.__mro__
    assert mro.index(MnScene) < mro.index(ThreeDScene)
    assert isinstance(MnScene3D().renderer.camera, ThreeDCamera)


def test_note_pins_the_caption_to_the_frame(capsys: pytest.CaptureFixture[str]) -> None:
    seen = {}

    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=70 * DEGREES, theta=-30 * DEGREES)
            self.note("a caption under a tilted camera")
            cap = self._caption
            assert cap is not None
            seen["world"] = cap.get_center()
            x0, x1, y0, y1 = self.screen_box(cap)
            seen["screen"] = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            seen["fixed"] = all(f in self.renderer.camera.fixed_in_frame_mobjects for f in cap.get_family())

    S().render()
    assert seen["fixed"]
    assert seen["screen"][1] < -2.5                       # at the bottom of the frame, as drawn
    assert np.allclose(seen["screen"][:2], seen["world"][:2], atol=0.3)   # not projected
    assert layout_lines(capsys) == []


def test_a_label_faces_the_camera_at_its_projected_point() -> None:
    seen = {}

    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=60 * DEGREES, theta=-20 * DEGREES)
            at = np.array([1.0, 2.0, 1.5])
            lab = self.label("v", at)
            self.add(lab)
            x0, x1, y0, y1 = self.screen_box(lab)
            seen["center"] = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            seen["projected"] = self.renderer.camera.project_point(at)
            # unrotated and unscaled: the label's drawn size is its own size
            seen["width"] = (x1 - x0, lab.width)

    S().render()
    assert np.allclose(seen["center"][:2], seen["projected"][:2], atol=0.05)
    assert seen["width"][0] == pytest.approx(seen["width"][1], rel=0.05)


def two_labels(phi: float, theta: float) -> type[MnScene3D]:
    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=phi, theta=theta)
            # One label above the other in space: looking straight down, they land on the same spot.
            self.add(self.label("upper", 1.5 * OUT), self.label("lower", -1.5 * OUT))

    return S


def test_labels_that_meet_on_screen_are_caught(capsys: pytest.CaptureFixture[str]) -> None:
    two_labels(0, -90 * DEGREES)().render()          # from straight above
    assert any("text overlaps text" in ln for ln in layout_lines(capsys))


def test_labels_apart_on_screen_pass(capsys: pytest.CaptureFixture[str]) -> None:
    two_labels(90 * DEGREES, -90 * DEGREES)().render()   # from the side: one above the other
    assert layout_lines(capsys) == []


def test_a_caption_over_a_world_point_is_judged_where_it_is_drawn(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=75 * DEGREES, theta=-90 * DEGREES)
            # In world coordinates this label sits right where the caption is; under the tilted camera it is drawn
            # near the middle of the frame. A world-space check would call that an overlap.
            self.add(self.label("floor", (0, -3.4, 0)))
            self.note("a caption that is pinned to the frame")

    S().render()
    assert layout_lines(capsys) == []


def test_2d_overlaps_are_still_caught(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            self.add(Text("one", font_size=32), Text("two", font_size=32).shift(0.1 * OUT))

    S().render()
    assert any("text overlaps text" in ln for ln in layout_lines(capsys))


def test_a_backdrop_is_set_dressing_not_text(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            plate = VGroup(Text("faint", font_size=32), Text("math", font_size=32).shift(0.1 * OUT))
            plate.backdrop = True          # what amanim's depth_plate sets
            self.add(plate, Text("read me", font_size=32).shift(2 * UP))

    S().render()
    assert layout_lines(capsys) == []


def test_a_zoomed_frame_puts_text_off_it(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene, MovingCameraScene):
        def construct(self) -> None:
            self.camera.frame.scale(0.5)                    # zoomed in 2x: the visible frame is about 7 x 4 units
            self.add(words("far right", size=32).move_to((5, 0, 0)), words("center", size=32))

    S().render()
    lines = layout_lines(capsys)
    assert any("off the frame" in ln and "far right" in ln for ln in lines)
    assert not any("center" in ln for ln in lines)


# ---------- the still: each section's last frame ----------

def still_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [ln for ln in layout_lines(capsys) if "clean up at the start" in ln]


def test_the_scene_clock_steps_one_frame_per_frame_across_plays() -> None:
    # a dt clock stalls for a frame at every play (Manim's first frame of a play has dt = 0); self.clock() doesn't
    seen: dict[str, list[float]] = {"dt": [], "scene": []}

    class S(MnScene):
        def construct(self) -> None:
            old = ValueTracker(0.0).add_updater(lambda m, dt: m.increment_value(dt))
            self.add(old)
            new = self.clock()
            probe = Square().add_updater(lambda m: (seen["dt"].append(old.get_value()),
                                                    seen["scene"].append(new.get_value())))
            self.add(probe)
            for _ in range(3):
                self.play(FadeIn(Circle()), run_time=0.5)
                self.wait(0.5)

    with tempconfig({"dry_run": False, "write_to_movie": False, "frame_rate": 10}):
        S().render()
    steps_dt, steps_scene = np.diff(seen["dt"]), np.diff(seen["scene"])
    assert np.isclose(steps_dt, 0).sum() >= 5                        # the stall this replaces, at each play
    moving = steps_scene[steps_scene > 1e-9]
    assert np.allclose(moving, 0.1) and (steps_scene > 1e-9).sum() >= 0.9 * 30


def test_things_on_the_scene_clock_keep_moving_through_a_wait() -> None:
    # with no dt updater anywhere, Manim renders a wait as one frozen frame; the scene clock must not allow that
    seen: list[float] = []

    class S(MnScene):
        def construct(self) -> None:
            clock = self.clock()
            dot = Circle(radius=0.2).add_updater(lambda m: m.move_to((clock.get_value(), 0, 0)))
            dot.add_updater(lambda m: seen.append(m.get_center()[0]))
            self.add(dot)
            self.wait(1.0)

    with tempconfig({"dry_run": False, "write_to_movie": False, "frame_rate": 10}):
        S().render()
    steps = np.diff(seen)
    assert len(seen) >= 9 and np.allclose(steps[steps > 1e-9], 0.1) and (steps > 1e-9).sum() >= 8


def test_text_below_the_floor_is_caught(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            self.add(words("fine", size=32).shift(2 * UP), words("too small", size=26),
                     words("scaled down", size=40).scale(0.6).shift(2 * DOWN), words("scaled ok", size=40)
                     .scale(0.8).shift(3 * RIGHT))

    S().render()
    lines = [ln for ln in layout_lines(capsys) if "SMALL floor" in ln]
    assert len(lines) == 2 and any("too small" in ln for ln in lines) and any("scaled down" in ln for ln in lines)


def test_a_zoomed_scene_judges_text_size_as_drawn(capsys: pytest.CaptureFixture[str]) -> None:
    # the caption is scaled down with the frame so it stays the same size on screen: not below the floor
    from mn_style import MnZoomScene

    class S(MnZoomScene):
        def construct(self) -> None:
            self.add(Square(side_length=0.5))
            self.play(self.camera.frame.animate.scale(0.4))
            self.note("a caption in a zoomed frame")

    S().render()
    assert [ln for ln in layout_lines(capsys) if "SMALL floor" in ln] == []


CLIP = {"title": "A test clip: with a subtitle", "credits": ["A. Author, A Book (2026), ch. 1"]}


@pytest.mark.parametrize("base", ["2d", "3d"])
def test_an_export_opens_with_the_title_and_closes_with_the_credits(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, base: str) -> None:
    import mn_style

    monkeypatch.setattr(mn_style, "CARDS", True)
    seen: dict[str, list[str]] = {}

    class S(MnScene if base == "2d" else MnScene3D):
        def construct(self) -> None:
            seen["at start"] = [t.tex_string for t in self.mobjects if isinstance(t, Tex)]
            self.next_section("the content")
            clock = self.clock()
            self.add(Square(side_length=3, fill_opacity=1).add_updater(lambda m: m.move_to((clock.get_value(), 0, 0))))
            self.wait(0.5)

        def tear_down(self) -> None:
            super().tear_down()
            seen["at end"] = [t.tex_string for top in self.mobjects for t in top.get_family() if isinstance(t, Tex)]

    S().render()
    assert seen["at start"] == []                                  # the title card has come and gone
    assert any("computed" in s for s in seen["at end"])
    assert any("A. Author" in s for s in seen["at end"])           # the scene module's CLIP credits
    out = capsys.readouterr().out.splitlines()
    assert [ln for ln in out if ln.startswith(("MN-LAYOUT", "MN-MOTION"))] == []
    kinds = [json.loads(ln.split(" ", 1)[1])["kind"] for ln in out if ln.startswith("MN-CUE ")]
    assert kinds[0] == "title" and kinds.count("credits") == 1 and "section" in kinds   # the cards cue themselves


def test_a_sound_bed_keeps_sampling_under_the_end_card(capsys: pytest.CaptureFixture[str],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import mn_style

    monkeypatch.setattr(mn_style, "CARDS", True)

    class S(MnScene):
        def construct(self) -> None:
            self.tone("ambience", lambda: (1.0, 0.4), base_hz=73.42, voice="buzz")
            self.wait(0.3)

    S().render()
    out = capsys.readouterr().out.splitlines()
    credits = next(json.loads(ln.split(" ", 1)[1])["t"] for ln in out
                   if ln.startswith("MN-CUE ") and '"credits"' in ln)
    last = max(json.loads(ln.split(" ", 1)[1])[1] for ln in out if ln.startswith("MN-TONE "))
    assert last > credits + 4.0                                     # through the card's 6 s, not cut at its cover
    assert any('"voice": "buzz"' in ln for ln in out if ln.startswith("MN-TONE-TRACK"))


def test_the_clock_starts_at_the_video_time_after_a_title_card(monkeypatch: pytest.MonkeyPatch) -> None:
    # compton-light plans its photon from clock.get_value() in construct(); after the export's title card a clock
    # that started at 0 put the plan 3.6 s in the past, and the photon jumped
    import mn_style

    monkeypatch.setattr(mn_style, "CARDS", True)
    seen: dict[str, float] = {}

    class S(MnScene):
        def construct(self) -> None:
            seen["clock"] = self.clock().get_value()
            seen["renderer"] = self.renderer.time
            self.wait(0.2)

    S().render()
    assert seen["renderer"] > 3.0                                   # the title card ran first
    assert seen["clock"] == pytest.approx(seen["renderer"])


def test_cues_and_tones_print_on_the_scene_clock(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            clock = self.clock()
            self.cue("absorb", at=1.25, kind="absorb", strength=0.6)
            self.tone("photon", lambda: None if clock.get_value() < 0.5 else (2 / 3, 0.8))
            self.wait(1.0)
            self.note(r"A photon ($\lambda_0$) arrives.")
            self.wait(0.2)

    S().render()
    out = capsys.readouterr().out.splitlines()
    cues = [json.loads(ln.split(" ", 1)[1]) for ln in out if ln.startswith("MN-CUE ")]
    assert {"t": 1.25, "kind": "absorb", "label": "absorb", "strength": 0.6} in cues
    caption = next(c for c in cues if c["kind"] == "caption")
    assert caption["label"] == r"A photon ($\lambda_0$) arrives." and caption["t"] == pytest.approx(1.0, abs=0.1)
    tones = [json.loads(ln.split(" ", 1)[1]) for ln in out if ln.startswith("MN-TONE ")]
    times = [t for _, t, _, _ in tones]
    assert times == sorted(times) and times[-1] > 1.0              # sampled every frame, through the wait and caption
    assert all(r is None for _, t, r, _ in tones if t < 0.45)
    assert all(r == pytest.approx(2 / 3) and a == 0.8 for _, t, r, a in tones if t > 0.55)


def test_watch_clips_get_no_cards() -> None:
    seen: list[list[Mobject]] = []

    class S(MnScene):
        def construct(self) -> None:
            seen.append(list(self.mobjects))                # nothing before the scene's own content
            self.next_section("only this")
            self.add(Square(side_length=3, fill_opacity=1))
            self.wait(0.2)

        def tear_down(self) -> None:
            super().tear_down()
            seen.append([f for top in self.mobjects for f in top.get_family()])

    S().render()
    assert seen[0] == [] and not any(isinstance(f, Tex) for f in seen[1]) and len(seen[1]) > 0


def test_a_percent_in_words_is_text_not_a_latex_comment() -> None:
    assert words("within 1% of the centre").tex_string == r"within 1\% of the centre"
    assert words(r"already 1\% escaped").tex_string == r"already 1\% escaped"


def motion_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("MN-MOTION")]


def test_fading_in_something_with_updaters_is_caught(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            clock = ValueTracker(0.0).add_updater(lambda m, dt: m.increment_value(dt))
            self.add(clock)
            dot = Circle(radius=0.2).add_updater(lambda m: m.move_to((clock.get_value(), 0, 0)))
            self.play(LaggedStart(FadeIn(Square()), FadeIn(dot)))       # inside a group counts too

    S().render()
    assert len(motion_lines(capsys)) == 1


def test_fading_in_a_live_thing_that_happens_to_be_still_is_fine(capsys: pytest.CaptureFixture[str]) -> None:
    # a label that follows a dot by an updater, while nothing moves: frozen, but invisibly; don't cry wolf
    class S(MnScene):
        def construct(self) -> None:
            dot = Circle(radius=0.2)
            label = words("here").add_updater(lambda m: m.next_to(dot, UP))
            self.add(dot)
            self.play(FadeIn(label))

    S().render()
    assert motion_lines(capsys) == []


def test_animating_trackers_and_still_things_is_fine(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            clock, appear = ValueTracker(0.0).add_updater(lambda m, dt: m.increment_value(dt)), ValueTracker(0.0)
            self.add(clock)
            dot = Circle(radius=0.2).add_updater(lambda m: m.set_stroke(opacity=appear.get_value()))
            self.add(dot)
            self.play(appear.animate.set_value(1.0), FadeIn(Square()))   # the fix: fade the tracker
            self.play(clock.animate.set_value(3.0))                       # a tracker with a dt updater: its own call

    S().render()
    assert motion_lines(capsys) == []


def test_a_section_that_ends_on_a_fade_out_is_caught(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            self.next_section("tidy at the end")
            square = Square(side_length=4, fill_opacity=1)
            self.play(FadeIn(square))
            self.play(FadeOut(square))
            self.wait(0.4)
            self.next_section("next")
            self.play(FadeIn(Square(side_length=4, fill_opacity=1)))

    S().render()
    lines = still_lines(capsys)
    assert any("[tidy at the end] the still is nearly empty" in ln for ln in lines)
    assert any("[tidy at the end] the section ends on a fade-out" in ln for ln in lines)
    assert not any("[next]" in ln for ln in lines)


def test_a_section_that_ends_full_passes(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            self.next_section("one")
            a, b = Square(side_length=3, fill_opacity=1), Circle(radius=0.3, fill_opacity=1).shift(3 * RIGHT)
            self.play(FadeIn(a), FadeIn(b))
            self.play(FadeOut(b))              # a small tidy-up keeps most of the frame: fine
            self.next_section("two")
            self.clean()                       # cleanup at the start of a section: what the rule asks for
            self.play(FadeIn(Square(side_length=3, fill_opacity=1)))

    S().render()
    assert still_lines(capsys) == []


def test_a_3d_section_that_turns_back_at_the_end_is_caught(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=60 * DEGREES, theta=-30 * DEGREES)
            self.add(Square(side_length=3, fill_opacity=1))
            self.next_section("edge-on")
            self.move_camera(phi=90 * DEGREES, theta=0, run_time=0.4)
            self.move_camera(phi=60 * DEGREES, theta=-30 * DEGREES, run_time=0.4)   # back home: the still misses it
            self.next_section("orbit")
            self.move_camera(theta=60 * DEGREES, run_time=0.4)                      # ends on the new view: fine
            self.next_section("spin")
            self.begin_ambient_camera_rotation(rate=0.5)
            self.wait(0.4)

    S().render()
    lines = still_lines(capsys)
    assert any("[edge-on] the section ends on a camera move back" in ln for ln in lines)
    assert not any("[orbit]" in ln for ln in lines)
    assert any("[spin] the camera is still turning" in ln for ln in lines)


def test_a_pinned_number_stays_pinned_when_its_digits_change(capsys: pytest.CaptureFixture[str]) -> None:
    seen = {}

    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=70 * DEGREES, theta=-30 * DEGREES)
            n = DecimalNumber(5, num_decimal_places=0, font_size=36)
            clock = ValueTracker(5)
            n.add_updater(lambda d: d.set_value(clock.get_value()))
            self.add(self.pin(n.move_to(2 * UR)))
            self.play(clock.animate.set_value(1234), run_time=0.6)     # 1 digit becomes 4: new parts
            seen["fixed"] = all(f in self.renderer.camera.fixed_in_frame_mobjects for f in n.get_family())

    S().render()
    assert seen["fixed"]
    assert not [ln for ln in layout_lines(capsys) if "clean up at the start" not in ln]   # drawn where it was pinned


def test_a_3d_section_that_ends_edge_on_is_not_called_empty(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=0, theta=-90 * DEGREES)
            self.add(Square(side_length=5, fill_opacity=0.6), Line(-2 * OUT, 2 * OUT, stroke_width=6))
            self.next_section("edge-on")
            self.move_camera(phi=90 * DEGREES, run_time=0.4)      # the square becomes a line: that's the point

    S().render()
    assert still_lines(capsys) == []


def test_a_zoom_scene_keeps_its_caption_on_screen(capsys: pytest.CaptureFixture[str]) -> None:
    from mn_style import MnZoomScene

    seen = {}

    class S(MnZoomScene):
        def construct(self) -> None:
            self.add(Square(side_length=6))
            self.note("before the zoom")
            self.play(self.camera.frame.animate.scale(0.3).move_to((2, 1, 0)), run_time=0.4)
            self.note("after the zoom")      # built while zoomed in: still the house caption on screen
            cap = self._caption
            assert cap is not None
            seen["box"] = self.screen_box(cap)

    S().render()
    x0, x1, y0, y1 = seen["box"]
    assert y0 > -4.0 and y1 < -3.0              # at the bottom of the screen, as at full frame
    assert 2.0 < x1 - x0 < 8.0                  # its full-frame size, not 0.3 of it
    assert not [ln for ln in layout_lines(capsys) if "off the frame" in ln or "overlaps" in ln]


def test_a_small_label_on_a_long_line_is_caught(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            line = Line((-6, -1, 0), (6, 1, 0))       # one long Bézier: its samples are 1.5 units apart
            self.keep_clear(line)
            self.add(line, Text("y", font_size=32).move_to((0.37, 0.06, 0)))   # between two samples, on the line

    S().render()
    assert any("kept-clear shape: 'y'" in ln for ln in layout_lines(capsys))


def test_a_kept_clear_group_counts_when_only_its_parts_are_on_screen(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene):
        def construct(self) -> None:
            shapes = VGroup(Line((-3, 0, 0), (3, 0, 0)), Line((0, -3, 0), (0, 3, 0)))
            self.keep_clear(shapes)
            self.add(*shapes)                          # the parts, never the group: how Create(a), Create(b) leaves it
            self.add(Text("x", font_size=32).move_to((1.5, 0, 0)))

    S().render()
    assert any("kept-clear shape: 'x'" in ln for ln in layout_lines(capsys))


# ---------- automatic label placement ----------

def test_place_skips_a_taken_spot_and_the_check_agrees(capsys: pytest.CaptureFixture[str]) -> None:
    seen = {}

    class S(MnScene):
        def construct(self) -> None:
            blocker = Text("taken", font_size=32).move_to((0.9, 0, 0))   # right of the point: the first spot tried
            self.add(blocker)
            lab = self.place("x", (0, 0, 0))
            self.add(lab)
            seen["blocker"], seen["lab"] = self.screen_box(blocker), self.screen_box(lab)

    S().render()
    from mn_style import _overlap
    assert _overlap(seen["blocker"], seen["lab"]) == 0
    assert layout_lines(capsys) == []


def test_place_starts_toward_the_given_direction() -> None:
    seen = {}

    class S(MnScene):
        def construct(self) -> None:
            seen["box"] = self.screen_box(self.place("v", (1, 1, 0), toward=(0, 1, 0)))

    S().render()
    x0, x1, y0, _ = seen["box"]
    assert y0 > 1.0 and x0 < 1.0 < x1                 # straight above the point


def test_two_labels_placed_at_one_point_dont_collide() -> None:
    seen = {}

    class S(MnScene):
        def construct(self) -> None:
            a = self.place("first", (0, 0, 0))
            b = self.place("second", (0, 0, 0))          # neither is shown yet: placed labels still count
            seen["boxes"] = (self.screen_box(a), self.screen_box(b))

    S().render()
    from mn_style import _overlap
    assert _overlap(*seen["boxes"]) == 0


def test_place_keeps_a_label_in_the_frame_at_the_edge() -> None:
    seen = {}

    class S(MnScene):
        def construct(self) -> None:
            seen["box"] = self.screen_box(self.place("edge", (7.0, 3.8, 0)))

    S().render()
    _, x1, _, y1 = seen["box"]
    assert x1 < 7.11 and y1 < 4.0


def test_place_in_3d_steps_off_a_kept_clear_line(capsys: pytest.CaptureFixture[str]) -> None:
    class S(MnScene3D):
        def construct(self) -> None:
            self.set_camera_orientation(phi=60 * DEGREES, theta=-30 * DEGREES)
            n = np.array([1.0, 1.0, 1.0]) / np.sqrt(3)
            line = Line(-3 * n, 3 * n, stroke_width=6)
            self.keep_clear(line)
            self.add(line)
            self.add(self.place("x_n", 1.0 * n, toward=n))   # a point ON the line, the hardest case
            self.add(self.place("N(A)", 2.9 * n, toward=n))

    S().render()
    assert layout_lines(capsys) == []
