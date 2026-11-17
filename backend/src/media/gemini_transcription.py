"""Google Gemini transcription engine for SupoClip video pipeline.

Provides high-accuracy transcription with speaker diarization and word timestamps using:
1. Primary: gemini-3.5-transcribe (official audioTranscriptionConfig with word timestamps & diarization)
2. First Fallback: gemini-3.5-flash-lite with structured JSON timestamp extraction
3. Second Fallback: gemini-3.1-flash-lite with structured JSON timestamp extraction
Zero conflict design: completely self-contained module.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .common import TRANSCRIPT_CACHE_SCHEMA_VERSION, logger
from .transcription import _prepare_audio_for_transcription

GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
GEMINI_UPLOAD_BASE = "https://generativelanguage.googleapis.com/upload/v1beta/files"


def format_ms_to_timestamp(ms: int) -> str:
    """Format milliseconds to MM:SS format."""
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def _parse_offset_seconds(val: Any) -> float:
    """Parse Gemini duration string like '1.250s' or numeric seconds into float seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    if not val:
        return 0.0
    text = str(val).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except (ValueError, TypeError):
        return 0.0


def _parse_timestamp_to_seconds(val: Any) -> float:
    """Parse seconds, MM:SS or HH:MM:SS string into float seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val or "").strip()
    if text.endswith("s"):
        text = text[:-1]
    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (ValueError, TypeError):
            return 0.0
    return _parse_offset_seconds(text)


class GeminiUploadError(Exception):
    pass


class GeminiTranscriptionError(Exception):
    pass


def _resolve_gemini_api_key(runtime_config: Any) -> str:
    key = (
        getattr(runtime_config, "gemini_api_key", None)
        or getattr(runtime_config, "google_api_key", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    if not key or not str(key).strip():
        raise GeminiTranscriptionError(
            "GEMINI_API_KEY or GOOGLE_API_KEY is not set. Please configure it in your .env file."
        )
    return str(key).strip()


def _upload_audio_file(audio_path: Path, api_key: str, timeout: int = 180) -> Tuple[str, str]:
    """Upload audio file using Google Gemini Resumable Files API.
    
    Returns:
        Tuple of (file_name, file_uri), e.g. ("files/12345", "https://generativelanguage.googleapis.com/v1beta/files/12345")
    """
    file_size = audio_path.stat().st_size
    if file_size <= 0:
        raise GeminiUploadError(f"Audio file is empty: {audio_path}")

    # Step 1: Start resumable upload
    start_url = f"{GEMINI_UPLOAD_BASE}?key={api_key}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": "audio/mp3",
        "Content-Type": "application/json",
    }
    metadata = {
        "file": {
            "display_name": audio_path.name
        }
    }

    try:
        start_res = requests.post(start_url, headers=headers, json=metadata, timeout=30)
        start_res.raise_for_status()
    except Exception as e:
        raise GeminiUploadError(f"Failed to initiate Gemini file upload: {e}") from e

    upload_url = start_res.headers.get("X-Goog-Upload-URL") or start_res.headers.get("upload_url")
    if not upload_url:
        raise GeminiUploadError("Gemini upload initiation did not return upload URL")

    # Step 2: Upload bytes
    upload_headers = {
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
        "Content-Length": str(file_size),
    }

    try:
        with open(audio_path, "rb") as f:
            upload_res = requests.post(upload_url, headers=upload_headers, data=f, timeout=timeout)
            upload_res.raise_for_status()
            data = upload_res.json()
    except Exception as e:
        raise GeminiUploadError(f"Failed to upload audio bytes to Gemini: {e}") from e

    file_info = data.get("file") or {}
    file_name = file_info.get("name")
    file_uri = file_info.get("uri")

    if not file_uri and file_name:
        file_uri = f"{GEMINI_API_BASE}/v1beta/{file_name.lstrip('/')}"

    if not file_name or not file_uri:
        raise GeminiUploadError(f"Gemini file upload response missing name/uri: {data}")

    # Step 3: Wait for state to become ACTIVE if PROCESSING
    state = (file_info.get("state") or "ACTIVE").upper()
    if state == "PROCESSING":
        poll_url = f"{GEMINI_API_BASE}/v1beta/{file_name}?key={api_key}"
        for _ in range(30):
            time.sleep(1.5)
            try:
                poll_res = requests.get(poll_url, timeout=15)
                poll_res.raise_for_status()
                poll_info = poll_res.json()
                state = (poll_info.get("state") or "").upper()
                if state == "ACTIVE":
                    file_uri = poll_info.get("uri") or file_uri
                    break
                if state == "FAILED":
                    raise GeminiUploadError(f"Gemini audio processing failed: {poll_info}")
            except Exception as e:
                logger.warning("Error polling Gemini file status: %s", e)

    return file_name, file_uri


def _delete_audio_file(file_name: str, api_key: str) -> None:
    """Best-effort cleanup of uploaded file in Gemini Files API."""
    if not file_name:
        return
    try:
        delete_url = f"{GEMINI_API_BASE}/v1beta/{file_name.lstrip('/')}?key={api_key}"
        requests.delete(delete_url, timeout=10)
    except Exception as e:
        logger.debug("Failed to delete Gemini temporary file %s: %s", file_name, e)


def _generate_with_gemini_transcribe(
    file_uri: str, api_key: str, language: Optional[str] = None
) -> Dict[str, Any]:
    """Call primary gemini-3.5-transcribe model with audioTranscriptionConfig."""
    endpoint = f"{GEMINI_API_BASE}/v1beta/models/gemini-3.5-transcribe:generateContent?key={api_key}"

    audio_cfg: Dict[str, Any] = {
        "wordTimestamp": True,
        "diarization": True,
        "mode": "VERBATIM",
    }
    if language and language != "auto":
        audio_cfg["languageCodes"] = [language]

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "fileData": {
                            "fileUri": file_uri,
                            "mimeType": "audio/mp3",
                        }
                    }
                ],
            }
        ],
        "generationConfig": {
            "audioTranscriptionConfig": audio_cfg
        },
    }

    t0 = time.time()
    response = requests.post(endpoint, json=payload, timeout=600)
    elapsed = time.time() - t0
    logger.info("gemini-3.5-transcribe completed HTTP call in %.2fs (status: %d)", elapsed, response.status_code)

    if response.status_code >= 400:
        raise GeminiTranscriptionError(
            f"gemini-3.5-transcribe HTTP error {response.status_code}: {response.text[:300]}"
        )

    return response.json()


def _generate_with_gemini_flash_fallback(
    file_uri: str, api_key: str, model_name: str = "gemini-3.5-flash-lite", language: Optional[str] = None
) -> Dict[str, Any]:
    """Fallback: Structured timestamped transcription using Gemini Flash."""
    endpoint = f"{GEMINI_API_BASE}/v1beta/models/{model_name}:generateContent?key={api_key}"

    lang_hint = f" The audio language is {language}." if language and language != "auto" else ""
    prompt = (
        f"You are an expert audio transcription engine.{lang_hint} "
        "Transcribe the spoken audio verbatim in the original spoken language. "
        "If speakers switch between English and Hindi (Hinglish), transcribe naturally using Latin / English script. "
        "Provide exact timestamps and speaker labels in JSON array format: "
        '[{"start": 0.0, "end": 2.5, "speaker": "Speaker 1", "text": "...", "words": [{"word": "...", "start": 0.0, "end": 0.5}]}] '
        "Return ONLY the valid JSON array."
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "fileData": {
                            "fileUri": file_uri,
                            "mimeType": "audio/mp3",
                        }
                    },
                    {
                        "text": prompt
                    }
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0,
        },
    }

    t0 = time.time()
    response = requests.post(endpoint, json=payload, timeout=600)
    elapsed = time.time() - t0
    logger.info("%s completed HTTP call in %.2fs (status: %d)", model_name, elapsed, response.status_code)

    if response.status_code >= 400:
        raise GeminiTranscriptionError(
            f"{model_name} HTTP error {response.status_code}: {response.text[:300]}"
        )

    return response.json()


def _parse_gemini_transcribe_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse gemini-3.5-transcribe response with word timestamps and speaker diarization."""
    words: List[Dict[str, Any]] = []
    utterances: List[Dict[str, Any]] = []
    text_pieces: List[str] = []

    candidates = data.get("candidates") or []
    for cand in candidates:
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            trans = part.get("audioTranscription") or part.get("audio_transcription")
            if not trans:
                part_text = part.get("text")
                if part_text:
                    text_pieces.append(part_text)
                continue

            spk = trans.get("speakerLabel") or trans.get("speaker_label") or "spk_1"
            segment_text = trans.get("text") or ""
            if segment_text:
                text_pieces.append(segment_text)

            raw_words = trans.get("words") or []
            seg_words: List[Dict[str, Any]] = []

            for w in raw_words:
                w_text = w.get("word") or ""
                w_start = int(_parse_offset_seconds(w.get("startOffset") or w.get("start_offset")) * 1000)
                w_end = int(_parse_offset_seconds(w.get("endOffset") or w.get("end_offset")) * 1000)
                word_obj = {
                    "text": w_text,
                    "start": w_start,
                    "end": max(w_end, w_start + 50),
                    "confidence": 1.0,
                    "speaker": spk,
                }
                words.append(word_obj)
                seg_words.append(word_obj)

            if seg_words:
                u_start = seg_words[0]["start"]
                u_end = seg_words[-1]["end"]
                utterances.append({
                    "text": segment_text or " ".join(w["text"] for w in seg_words),
                    "start": u_start,
                    "end": u_end,
                    "speaker": spk,
                    "words": seg_words,
                })

    full_text = " ".join(text_pieces).strip() or " ".join(w["text"] for w in words).strip()

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words,
        "utterances": utterances,
        "text": full_text,
        "language_code": None,
    }


def _parse_gemini_fallback_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON output from Gemini Flash fallback model into transcript cache format."""
    candidates = data.get("candidates") or []
    raw_text = ""
    for cand in candidates:
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            if part.get("text"):
                raw_text += part.get("text")

    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            raw_text = "\n".join(lines[1:-1 if lines[-1].startswith("```") else len(lines)]).strip()

    try:
        segments = json.loads(raw_text)
    except Exception as e:
        # Fallback extract JSON array
        start_bracket = raw_text.find("[")
        end_bracket = raw_text.rfind("]")
        if start_bracket != -1 and end_bracket > start_bracket:
            segments = json.loads(raw_text[start_bracket:end_bracket + 1])
        else:
            raise GeminiTranscriptionError(f"Failed to parse Gemini Flash JSON output: {raw_text[:200]}") from e

    words: List[Dict[str, Any]] = []
    utterances: List[Dict[str, Any]] = []
    full_text_pieces: List[str] = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        st_sec = _parse_timestamp_to_seconds(seg.get("start", 0))
        en_sec = _parse_timestamp_to_seconds(seg.get("end", st_sec + 2.0))
        st_ms = int(st_sec * 1000)
        en_ms = int(en_sec * 1000)
        spk = seg.get("speaker") or "Speaker 1"
        seg_text = str(seg.get("text") or "").strip()
        if seg_text:
            full_text_pieces.append(seg_text)

        seg_words: List[Dict[str, Any]] = []
        raw_words = seg.get("words")
        if isinstance(raw_words, list) and raw_words:
            for w in raw_words:
                if isinstance(w, dict):
                    w_st = int(_parse_timestamp_to_seconds(w.get("start", st_sec)) * 1000)
                    w_en = int(_parse_timestamp_to_seconds(w.get("end", en_sec)) * 1000)
                    w_obj = {
                        "text": str(w.get("word") or w.get("text") or ""),
                        "start": w_st,
                        "end": max(w_en, w_st + 50),
                        "confidence": 0.95,
                        "speaker": spk,
                    }
                    words.append(w_obj)
                    seg_words.append(w_obj)
        else:
            # Interpolate word timings across segment duration
            tokens = seg_text.split()
            if tokens:
                duration_ms = max(100, en_ms - st_ms)
                step_ms = duration_ms / len(tokens)
                for idx, tok in enumerate(tokens):
                    tok_start = int(st_ms + idx * step_ms)
                    tok_end = int(st_ms + (idx + 1) * step_ms)
                    w_obj = {
                        "text": tok,
                        "start": tok_start,
                        "end": tok_end,
                        "confidence": 0.9,
                        "speaker": spk,
                    }
                    words.append(w_obj)
                    seg_words.append(w_obj)

        utterances.append({
            "text": seg_text,
            "start": st_ms,
            "end": en_ms,
            "speaker": spk,
            "words": seg_words,
        })

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words,
        "utterances": utterances,
        "text": " ".join(full_text_pieces),
        "language_code": None,
    }


def get_transcript_with_gemini(video_path: Path, runtime_config: Any) -> str:
    """Entry point for Google Gemini transcription in SupoClip pipeline.
    
    1. Extracts 16kHz mono audio from video_path.
    2. Uploads to Gemini Files API.
    3. Calls gemini-3.5-transcribe with verbatim word timestamps and diarization.
    4. Falls back to gemini-3.5-flash or gemini-3.1-flash if transcribe hits rate limits or errors.
    5. Saves {video_path}.transcript_cache.json in exact SupoClip cache schema.
    6. Returns formatted text lines for AI analysis.
    """
    api_key = _resolve_gemini_api_key(runtime_config)
    audio_path = _prepare_audio_for_transcription(video_path)
    language = getattr(runtime_config, "transcription_language", None)

    logger.info("Uploading audio to Gemini for transcription: %s", audio_path)
    file_name, file_uri = _upload_audio_file(audio_path, api_key)

    transcript_data: Optional[Dict[str, Any]] = None

    try:
        # 1. Primary: gemini-3.5-transcribe
        try:
            logger.info("Starting primary transcription with model: gemini-3.5-transcribe")
            raw_response = _generate_with_gemini_transcribe(file_uri, api_key, language)
            parsed = _parse_gemini_transcribe_response(raw_response)
            if parsed.get("words") or parsed.get("text"):
                transcript_data = parsed
                logger.info(
                    "gemini-3.5-transcribe succeeded: %d words, %d utterances",
                    len(parsed["words"]),
                    len(parsed["utterances"]),
                )
            else:
                logger.warning("gemini-3.5-transcribe returned empty output; attempting fallback")
        except Exception as primary_err:
            logger.warning("Primary gemini-3.5-transcribe failed: %s. Initiating fallback...", primary_err)

        # 2. Fallback: gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemini-3.5-flash
        if not transcript_data:
            fallback_models = [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.1-flash",
            ]
            last_fallback_err = None
            for f_model in fallback_models:
                try:
                    logger.info("Attempting fallback transcription with model: %s", f_model)
                    raw_fallback = _generate_with_gemini_flash_fallback(file_uri, api_key, model_name=f_model, language=language)
                    parsed_fallback = _parse_gemini_fallback_response(raw_fallback)
                    if parsed_fallback.get("words") or parsed_fallback.get("text"):
                        transcript_data = parsed_fallback
                        logger.info(
                            "%s fallback succeeded: %d words, %d utterances",
                            f_model,
                            len(parsed_fallback["words"]),
                            len(parsed_fallback["utterances"]),
                        )
                        break
                except Exception as fb_err:
                    last_fallback_err = fb_err
                    logger.warning("Fallback model %s failed: %s", f_model, fb_err)

            if not transcript_data:
                raise GeminiTranscriptionError(
                    f"All Gemini transcription attempts failed. Last error: {last_fallback_err}"
                )

    finally:
        # Cleanup uploaded file from Gemini storage
        _delete_audio_file(file_name, api_key)

    # Save to transcript cache file for subtitles and timeline rendering
    cache_path = video_path.with_suffix(".transcript_cache.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(transcript_data, f, ensure_ascii=False)
    logger.info("Saved Gemini transcript cache to: %s (%d words)", cache_path, len(transcript_data["words"]))

    # Format transcript lines for AI clip analysis
    formatted_lines: List[str] = []
    utterances = transcript_data.get("utterances") or []
    if utterances:
        for u in utterances:
            start_str = format_ms_to_timestamp(u.get("start", 0))
            end_str = format_ms_to_timestamp(u.get("end", 0))
            spk = u.get("speaker")
            prefix = f"Speaker {spk}: " if spk else ""
            formatted_lines.append(f"[{start_str} - {end_str}] {prefix}{u.get('text', '')}")
    elif transcript_data.get("words"):
        # Group into ~15 word chunks
        words = transcript_data["words"]
        for i in range(0, len(words), 15):
            chunk = words[i:i + 15]
            start_str = format_ms_to_timestamp(chunk[0]["start"])
            end_str = format_ms_to_timestamp(chunk[-1]["end"])
            chunk_text = " ".join(w["text"] for w in chunk)
            spk = chunk[0].get("speaker")
            prefix = f"Speaker {spk}: " if spk else ""
            formatted_lines.append(f"[{start_str} - {end_str}] {prefix}{chunk_text}")
    else:
        formatted_lines.append(f"[00:00 - 00:00] {transcript_data.get('text', '')}")

    result = "\n".join(formatted_lines)
    logger.info("Gemini transcript formatted: %d segments, %d chars", len(formatted_lines), len(result))
    return result
