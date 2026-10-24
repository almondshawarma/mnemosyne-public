# The watcher (watch_job.py): captions and the frames that matter from a lecture video, never the video itself; and
# study mode (study.py's measurements of one span).
# Built on the Obelisk: docker build -t mnemosyne-watch:1 services/watch   (scripts/watch.py runs it)
# yt-dlp is pinned; YouTube changes often, so when fetching breaks, bump it here and rebuild. scripts/watch.py reads
# the pip line to run studies locally with the same versions. Debian's ffmpeg lets yt-dlp fetch only a study's span
# (imageio-ffmpeg's static build segfaults on network reads in this image, 2026-10-24).
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp==2026.8.19 av==19.0.1 pillow==12.3.0 numpy==2.5.4
COPY watch_job.py study.py /app/
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python", "/app/watch_job.py"]
