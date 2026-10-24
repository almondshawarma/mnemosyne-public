# The render sandbox's image: ManimCE, pinned by digest, plus what amanim needs that ManimCE doesn't ship
# (matplotlib, for its equipotential contours). Built on the Obelisk as mnemosyne-manim:1; scripts/render.py and
# scripts/render_runner.py run every scene in it, with no network (docs/security/clip-submit.md).
#   docker build -t mnemosyne-manim:1 services/manim
# amanim itself isn't installed here: each render copies the Obelisk's ~/helios/amanim into the job, so a push to
# amanim reaches the next render without rebuilding this image.
FROM manimcommunity/manim:v0.21.0@sha256:89ab433ce59134a4dcf351deb2511e067ab354393c0bb7d1859f3e8f0b2406a3
USER root
RUN pip install --no-cache-dir matplotlib==3.11.2
USER 1000:1000
