from __future__ import annotations

from app.schemas import MediaResult

NOT_CONFIGURED_NOTE = (
    "No audio, image or video manipulation detector is configured in this build. The media was not downloaded or "
    "analyzed, and no statement is made about whether it is authentic or manipulated."
)


def analyze_media(media_ref: str | None, requested: bool, sample_mode: bool) -> list[MediaResult]:
    if not media_ref and not requested:
        return []
    ref = media_ref or "(no media supplied)"
    status = "disabled_sample_mode" if sample_mode and media_ref else ("not_configured" if media_ref else "skipped")
    note = NOT_CONFIGURED_NOTE if media_ref else "Media analysis was requested but no media reference was supplied."
    return [MediaResult(media_ref=ref, status=status, detector=None, findings=[], note=note)]  # type: ignore[arg-type]
