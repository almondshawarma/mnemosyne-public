"""Service settings from MNEMOSYNE_* environment variables. Secrets come from files, never the environment."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def read_secret(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MNEMOSYNE_", extra="ignore")

    ledger: Path = Path("/ledger/helios.db")
    repo: Path = Path("/app")                      # holds config/ and desktop/notetypes/
    anki_core_url: str = "http://anki-core:8000/mcp"
    anki_core_token_file: Path | None = None
    propose_token_file: Path | None = None         # may only propose (cloud-facing MCP)
    approve_token_file: Path | None = None         # may also approve (Telegram bot, local tools)
    paperless_url: str = "http://webserver:8000"
    paperless_public_url: str | None = None        # links in cards; defaults to paperless_url
    paperless_token_file: Path | None = None
    ollama_url: str = "http://ollama:11434"
    poll_minutes: int = 5                          # Paperless poller; 0 turns it off
    tz: str = "America/Chicago"
    daily_write_at: str = "04:15"                  # invariant 8: the only scheduled collection write
    influx_url: str = "http://influxdb:8086"       # Helios InfluxDB on helios_default (PLAN §9 metrics)
    influx_org: str = "helios"
    influx_bucket: str = "mnemosyne"
    influx_token_file: Path | None = None          # written by scripts/obelisk_secrets.sh; without it, Ledger only
    stubs_dir: Path | None = None                  # the vault's Sources/Paperless/ (lecture stubs hold questions)
    old_notes_dir: Path | None = None              # Sources/Lectures/: the retired '… · questions' notes
    helios_mcp_url: str | None = None              # Helios MCP, for Hyperion investigations; unset = none
    calibre_dir: Path | None = None                # the Calibre library, mounted read-only (the reading path)
    calibre_public_url: str | None = None          # Calibre-Web, for links from cards back to a book
    books_index: Path | None = None                # library.Index: full text of the books (a cache, not state)
    media_dir: Path | None = None                  # pipeline media too big for git (ASL videos); see audio.py
    watch_queue: Path | None = None                # requests for the watcher (videos.py); unset = no new videos
    render_queue: Path | None = None               # clips for the render runner (clips.py); unset = none
    public_url: str | None = None                  # this api on the tailnet, for /watch links in tool results
    scenes_dir: Path | None = None                 # the repo's scenes/ (mn_style, brand tokens), read-only
    amanim_dir: Path | None = None                 # Aman's amanim checkout, read-only: the clip toolkit

    @property
    def media_roots(self) -> list[Path]:
        """Where pipeline media is found: the Obelisk's media folder (videos), then the repo's data/ (IPA audio)."""
        return [p for p in (self.media_dir, self.repo / "data") if p is not None]

    @property
    def config_dir(self) -> Path:
        return self.repo / "config"

    @property
    def skill_dir(self) -> Path:
        return self.repo / ".claude" / "skills" / "mnemosyne-cards"

    @property
    def notetypes_dir(self) -> Path:
        return self.repo / "desktop" / "notetypes"
