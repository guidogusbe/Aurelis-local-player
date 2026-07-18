"""
metadata.py

Extracts audio metadata (title, artist, album, genre, year, duration,
format) from music files using the `mutagen` library.

Design notes:
    - Uses `mutagen.File(path, easy=True)` where possible, which exposes a
      normalized, format-agnostic tag dictionary (works across MP3, FLAC,
      OGG, M4A, etc. without needing per-format parsing code).
    - Wraps every extraction step in defensive try/except blocks: real-world
      music libraries always contain a few files with missing, malformed,
      or corrupted tags. When a tag is missing or unreadable, we fall back
      to a sensible default (e.g. the filename for the title) rather than
      raising, so a single bad file never aborts a whole library scan.
    - Returns a `SongData` instance (defined in database_manager/songs.py)
      so the scanner can pass the result straight to `insert_song()`
      without any extra mapping step.
"""

import re
from pathlib import Path

import mutagen

from database_manager.songs import SongData

# Audio file extensions supported by Aurelis (per project specifications).
SUPPORTED_FORMATS: set[str] = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"}

# Matches a 4-digit year anywhere in a string (e.g. "2021-05-06" -> "2021").
_YEAR_PATTERN = re.compile(r"(\d{4})")


def extract_metadata(file_path: Path) -> SongData:
    """
    Extract metadata from an audio file.

    Args:
        file_path: Absolute path to the audio file.

    Returns:
        A SongData instance populated with whatever metadata could be
        read. Fields that are missing or unreadable are set to None,
        except `title` (falls back to the filename) and `format` (always
        derived from the file extension, never from tags).

    Note:
        This function never raises on a malformed/corrupted file: any
        error while reading tags is caught internally and results in a
        SongData with fallback values, so the caller (the scanner) can
        keep processing the rest of the library uninterrupted.
    """
    file_format = file_path.suffix.lower().lstrip(".")
    fallback_title = _filename_to_title(file_path)

    audio = _safe_load_audio(file_path)

    if audio is None:
        # File could not be opened/parsed at all (corrupted or unsupported
        # internal structure) -> return filename-based fallback data.
        return SongData(
            path=file_path,
            title=fallback_title,
            artist=None,
            album=None,
            genre=None,
            track_number=None,
            year=None,
            duration=None,
            format=file_format,
            cover_path=None,
        )

    tags = _safe_get_easy_tags(file_path)

    return SongData(
        path=file_path,
        title=_first_tag_value(tags, "title") or fallback_title,
        artist=_first_tag_value(tags, "artist"),
        album=_first_tag_value(tags, "album"),
        genre=_first_tag_value(tags, "genre"),
        track_number=_extract_track_number(tags),
        year=_extract_year(tags),
        duration=_extract_duration(audio),
        format=file_format,
        cover_path=None,  # Cover extraction is handled in a later phase.
    )


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _safe_load_audio(file_path: Path) -> mutagen.FileType | None:
    """
    Attempt to open the file with mutagen (without the "easy" tag
    interface) in order to access technical info such as duration.

    Returns None if the file cannot be parsed (corrupted, unsupported,
    or not actually an audio file despite its extension).
    """
    try:
        return mutagen.File(file_path)
    except Exception:
        return None


def _safe_get_easy_tags(file_path: Path) -> dict:
    """
    Attempt to open the file with mutagen's "easy" tag interface, which
    provides normalized, human-readable keys (title, artist, album, ...)
    across different audio formats.

    Returns an empty dict if the easy interface is unavailable for this
    format or the tags cannot be read, so downstream lookups degrade
    gracefully to None instead of raising.
    """
    try:
        audio = mutagen.File(file_path, easy=True)
        if audio is None or audio.tags is None:
            return {}
        return dict(audio.tags)
    except Exception:
        return {}


def _first_tag_value(tags: dict, key: str) -> str | None:
    """
    Return the first value for `key` in an easy-tags dict.

    Mutagen's easy tags store values as lists of strings (a tag can
    technically be repeated), so we take the first non-empty entry.
    """
    values = tags.get(key)
    if not values:
        return None
    value = str(values[0]).strip()
    return value if value else None


def _extract_year(tags: dict) -> int | None:
    """
    Extract a 4-digit year from the 'date' tag (EasyID3/most formats use
    'date', which can be a full date like "2021-05-06" or just "2021").
    """
    raw_date = _first_tag_value(tags, "date")
    if raw_date is None:
        return None
    match = _YEAR_PATTERN.search(raw_date)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_track_number(tags: dict) -> int | None:
    """
    Extract the track number from the 'tracknumber' tag.

    The tag value is often formatted as "N/Total" (e.g. "3/12"), so we
    only parse the part before the slash.
    """
    raw_track = _first_tag_value(tags, "tracknumber")
    if raw_track is None:
        return None
    track_part = raw_track.split("/")[0].strip()
    try:
        return int(track_part)
    except ValueError:
        return None


def _extract_duration(audio: mutagen.FileType) -> int | None:
    """
    Extract the track duration in whole seconds from mutagen's technical
    audio info. Returns None if unavailable.
    """
    try:
        length = audio.info.length
        return int(round(length)) if length is not None else None
    except AttributeError:
        return None


def _filename_to_title(file_path: Path) -> str:
    """
    Build a human-readable fallback title from the filename when no
    'title' tag is present, e.g. "01 - my_song_name" -> "My Song Name".
    """
    stem = file_path.stem
    # Replace common separators with spaces and collapse extra whitespace.
    cleaned = re.sub(r"[_\-]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else stem
