# Learn it, make it, keep it

How the pieces fit: learning a concept yourself, making good clips quickly, and leaving behind material that Claude
can teach you with later. (2026-10-24.)

## The idea: one loop, three things out of it

Every time you learn something with this setup, three things come out at once:

1. **Understanding**, in you. To make the picture you have to compute it, and that's where the learning happens.
2. **Content**: a clip on `/watch`, in your style, that you can rewatch or show someone.
3. **Material** for the next time: cards, a gallery pattern, an amanim function, a taste finding, a logged question.
   Claude reads all of it the next time it teaches you or makes something with you.

Each turn of the loop makes the next one faster. That's the point: you don't make content *instead of* studying.
Making it is how you study, and what it leaves behind keeps teaching you.

```
  a concept you need (a lecture, a book, a question)
        │
        ▼
  1 MEET      cards come in from the lecture; Claude knows what you hold
  2 SEE       a great explanation, if one exists (watch it with Claude)
  3 PREDICT   say how you'd show it, and what should happen
  4 BUILD     compute the picture: a clip in your style (Claude drafts, you steer)
  5 KEEP      cards from what surprised you, the clip linked in them, the reusable piece harvested
        │
        └──▶ next concept starts with more: better amanim, more gallery, sharper taste, cards that hold
```

The same loop runs on **craft** (how to make things look good) through the dissection loop below.

## The spine: computational science, not just art

amanim's rule is *calculate what you teach* (metis D-014). Every clip, dissection, and amanim change names four
things:

| | Compton remake | Study mode |
|---|---|---|
| What's computed | λ′, recoil angle, recoil speed β | motion curve, holds, cuts, palettes |
| The method | momentum conservation | frame differencing, noise floor, median baseline |
| How it's verified | energy conservation, which the code never used | synthetic frames with known answers, real frames by eye |
| What it costs | about 3 min to render on the Obelisk | about 1 minute a study |

That's the part that transfers to your real work:
- **Study mode is a diagnostic pipeline:** sensor, features, events, an honest report.
- **amanim's physics** is the same numerics as P3: ODE integrators, conservation checks, convergence.

When Claude does something clever along the way, say "unpack that". It becomes a metis write-up, a drill you do
yourself, and cards. The first one is `metis/unpacked/2026-10-24-video-study-mode.md`.

## Loop A: learn a concept by building it

The worked example is what just happened with **Compton scattering** (NUEN 201, L09).

| Step | What you do | What Claude does | What's left behind |
|---|---|---|---|
| 1. Meet | File the lecture as usual | Cards it, checks the numbers | Cards in `Now`, your slide questions |
| 2. See | "Is there a good video on this?" | Finds one, watches it (`scripts/watch.py` or `mnemosyne_video_watch`), and points you to the moment | A watched video you can search later |
| 3. Predict | Say how you'd show it: "the electron should recoil slower than the photon, right?" | Asks before it tells (the tutor's rule) | Your prediction, to check against |
| 4. Build | "Make a clip of Compton scattering in my look" | Writes the scene on amanim, renders it, reads every still | `compton-light` on `/watch` |
| 5. Keep | Watch it once, answer the retrieval question | Proposes cards for what surprised you, links the clip in them, moves reusable code into amanim | Cards, `amanim.scattering`, a taste finding |

What made this one worth doing, concretely:
- **Computing the picture taught the physics.** Drawing the recoil at the right speed meant working out
  β = p_e c / E_e. At 60° it's 0.66: the electron is not slow. That's a card ("why doesn't the recoil electron
  crawl?"), and you'd never have asked it from the formula alone.
- **The code was harvested.** The Compton kinematics moved into `amanim.scattering`, tested against conservation of
  energy and momentum, so the next clip about scattering (pair production, Thomson, Klein–Nishina) starts with
  them.
- **The clip feeds the cards.** A card's `Elaboration` can link it:
  `<a href="http://<obelisk>:8110/watch?via=card#compton-light">▶ clip</a>`. The weekly audit counts which
  clips get opened from cards.

**When to build instead of just reading:** when the idea is motion or shape (a field, a collision, a transformation,
a 3D subspace), when a card keeps failing (a clip is another angle), or when you notice you can recite it but couldn't
draw it.

## Loop B: learn craft by taking apart what you love

This is the dissection loop (the `mnemosyne-clips` skill, "Dissecting a video"). It's how taste grows.

1. **Pick a span** (10–90 s) of something you love. [references.md](../.claude/skills/mnemosyne-clips/references.md)
   has the list so far: Arithmetes, Knowable, 3b1b on your subjects.
2. **Write what you think works first.** Predict, then measure.
3. **Study it:** `uv run scripts/watch.py <link> --study 1:20-1:50`. Reels, Vimeo, and saved files work too. Then go
   through it with Claude:
   - composition;
   - colour;
   - motion and easing;
   - pacing;
   - typography;
   - sync with the narration.
4. **The finding goes into the taste guide,** [amanim's `TASTE.md`](../../amanim/TASTE.md), credited and
   timestamped, with the amanim function that does it, or a new one.
5. **Recreate one shot** in your style, credited "after <creator>, <title>, <time>".

Every finding becomes something reusable: a rule in `TASTE.md` and `clips.md`, a function in amanim, a gallery
demo. The Laplace dissection, for example, gave "the camera move is the explanation" and "linked views";
Knowable gave "never fully still"; Arithmetes gave the bloom.

## Loop C: make good content quickly

Once a few turns of A and B have run, making a clip is mostly assembly:

1. **Say what the idea is and who it's for:** "a clip on the Debye length for PHYS 217, in my look".
2. **Claude starts from what exists:**
   - the gallery (`gallery-feynman`, `gallery-vector-field-3d`, ...: patterns to borrow);
   - amanim (`diagram`, `field_lines`, `lorentz_path`, `wave_packet`, `oblique_view`, ...: calculated, never typed
     in);
   - the taste guide (bloom, holds, `settle`, sparing backgrounds);
   - your cards on the topic (your notation).
3. **Draft locally in under a minute,** read the stills, fix, then publish to `/watch`, through the post pass with
   `"look": "ethereal"`. Compton went from a blank file to published in about an hour, including fixing the look;
   the next one should be faster.
4. **Anything reusable gets a `# harvest:` mark,** and moves into amanim with a test, so the following clip borrows it.

## Snippets for Claude to teach you with

Everything the loops leave behind is something Claude reads the next time:

| What accumulates | Where | How Claude uses it to teach you |
|---|---|---|
| Your cards (and how they're holding) | Anki, via the learner model | Checks what you own before explaining; quizzes what's weak |
| Clips | `/watch` (code kept even after the video is cleared) | Links a clip when a card fails; re-renders it on request |
| Watched videos and studies | `media/watch/`, `.watch/` | Quotes the moment ("3b1b, 7:05: watch the tilt") |
| Gallery demos | `scenes/gallery/` | Pattern-matches a new clip against them |
| amanim functions | `Projects/amanim` | Computes new pictures with your tested physics |
| Taste findings | `amanim/TASTE.md` | Makes new clips in the look you chose, and explains why |
| Your questions | Obsidian stubs, the Questions tab | Brings them up in sessions |
| Things you unpacked | metis (`mnemosyne-unpack`) | Drills the technique, not just the fact |

So the more you do this, the better Claude's explanations get, because they're built from things you've already
seen, in your notation and your style.

## What to say

| You want | Say |
|---|---|
| a clip of a concept | "make a clip of <idea> for <course>, in my look" |
| a better version of a clip | "spruce up <clip> with the glowing look" (`four-subspaces-3d-glow` is the before/after) |
| to understand a clip's code | "unpack how the Compton clip computes the recoil" |
| to take apart something you love | "let's dissect <link> from 1:20 to 1:50": write your prediction first |
| to grow the taste guide | "add that to the taste guide" |
| cards from a clip | "card what surprised me in that clip" |
| to find what exists | "what clips and gallery demos do I have on fields?" |

## Limits, honestly

- **Claude reads frames and transcripts, not video,** and hears no sound. Timing comes from frame differences.
- **The look is a post pass on ManimCE.** Real shader bloom, depth of field, and a GPU camera would mean ManimGL.
  Watch `compton-light` and `four-subspaces-3d-glow`, then decide whether a bake-off is worth it.
- **Making is slower than reading.** Build when the idea is motion or shape, or when a card keeps failing, not for
  every fact.
