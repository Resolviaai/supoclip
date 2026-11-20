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
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .common import TRANSCRIPT_CACHE_SCHEMA_VERSION, logger
from .ffmpeg import ffprobe_duration
from .transcription import _prepare_audio_for_transcription
from .transliteration import (
    has_devanagari,
    phonetic_devanagari_to_roman,
    clean_hinglish_spelling,
    normalize_tokens_with_llm,
    normalize_text_with_llm,
)

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
    file_uri: str,
    api_key: str,
    model_name: str = "gemini-3.1-flash-lite",
    language: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Fallback: Structured timestamped transcription using Gemini Flash."""
    endpoint = f"{GEMINI_API_BASE}/v1beta/models/{model_name}:generateContent?key={api_key}"

    lang_hint = f" The audio language is {language}." if language and language != "auto" else ""
    dur_hint = (
        f" The audio clip is exactly {duration_seconds:.1f} seconds long (from 0.0s to {duration_seconds:.1f}s). "
        f"All segment start and end timestamps MUST be precisely within 0.0 and {duration_seconds:.1f}."
        if duration_seconds and duration_seconds > 0
        else ""
    )
    prompt = (
        f"You are an expert audio transcription engine for social media video clipping.{lang_hint}{dur_hint} "
        "Transcribe the spoken audio in natural, readable conversational Roman Hinglish (using Latin / English script). "
        "Do NOT use Devanagari script. "
        "CRITICAL SPELLING RULES: "
        "1. All English words spoken (e.g., video, confession, student, consistency, challenge, break, college, drop out, AI, projects, rule, exam, etc.) "
        "MUST be spelled with standard, correct English spelling. Never spell 'video' as 'veediyo' or 'college' as 'kolej'. "
        "2. Hindi words must be written in standard conversational Roman script (e.g., 'main', 'nahi', 'karenge', 'socha', 'kya', 'hai', 'bana raha hoon'). "
        f"Provide exact timestamps in seconds{' strictly bounded between 0.0 and ' + f'{duration_seconds:.1f}' if duration_seconds and duration_seconds > 0 else ''} "
        "and speaker labels in JSON array format: "
        '[{"start": 0.0, "end": 2.5, "speaker": "Speaker 1", "text": "clean Roman Hinglish sentence here"}] '
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
            "maxOutputTokens": 65536,
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


def _chunk_words_into_utterances(
    words: List[Dict[str, Any]], target_chunk_size: int = 15
) -> List[Dict[str, Any]]:
    """Chunk flat list of timestamped words into discrete, natural utterances.

    Splits when:
    - Speaker changes.
    - Silence gap between words exceeds 1.0 second (1000 ms).
    - Punctuation indicates sentence end (. ? ! । ...) and chunk has >= 5 words.
    - Chunk reaches target_chunk_size words.
    """
    if not words:
        return []

    utterances: List[Dict[str, Any]] = []
    current_chunk: List[Dict[str, Any]] = []

    for w in words:
        if not current_chunk:
            current_chunk.append(w)
            continue

        prev_w = current_chunk[-1]
        pause_gap = w.get("start", 0) - prev_w.get("end", 0)
        speaker_changed = (w.get("speaker") != current_chunk[0].get("speaker"))
        prev_text = str(prev_w.get("text") or "").strip()
        ends_sentence = prev_text.endswith((".", "?", "!", "।", "...", "…"))

        should_split = (
            speaker_changed
            or pause_gap >= 1000
            or (ends_sentence and len(current_chunk) >= 5)
            or len(current_chunk) >= target_chunk_size
        )

        if should_split and current_chunk:
            u_start = current_chunk[0]["start"]
            u_end = current_chunk[-1]["end"]
            spk = current_chunk[0].get("speaker", "spk_1")
            text = " ".join(str(item.get("text") or "") for item in current_chunk).strip()
            utterances.append({
                "text": text,
                "start": u_start,
                "end": max(u_end, u_start + 100),
                "speaker": spk,
                "words": current_chunk,
            })
            current_chunk = [w]
        else:
            current_chunk.append(w)

    if current_chunk:
        u_start = current_chunk[0]["start"]
        u_end = current_chunk[-1]["end"]
        spk = current_chunk[0].get("speaker", "spk_1")
        text = " ".join(str(item.get("text") or "") for item in current_chunk).strip()
        utterances.append({
            "text": text,
            "start": u_start,
            "end": max(u_end, u_start + 100),
            "speaker": spk,
            "words": current_chunk,
        })

    return utterances


def _rechunk_long_utterances(
    utterances: List[Dict[str, Any]],
    words: List[Dict[str, Any]],
    max_words: int = 20,
    max_duration_ms: int = 15000,
) -> List[Dict[str, Any]]:
    """Split oversized speaker-turn utterances into natural clip-ready segments.

    ``gemini-3.5-transcribe`` often groups an entire multi-minute monologue by
    one speaker into a single utterance even when 2-3 speaker turns exist in the
    video. Re-chunk every oversized utterance individually so AI clip selection
    always receives granular timestamps.
    """
    if not utterances:
        return _chunk_words_into_utterances(words) if words else []

    rechunked: List[Dict[str, Any]] = []
    changed = False
    for u in utterances:
        u_words = u.get("words") or []
        duration_ms = int(u.get("end", 0)) - int(u.get("start", 0))
        if u_words and (len(u_words) > max_words or duration_ms > max_duration_ms):
            changed = True
            rechunked.extend(_chunk_words_into_utterances(u_words))
        else:
            rechunked.append(u)

    if changed:
        logger.info(
            "Re-chunked oversized Gemini utterances: %d -> %d granular utterances (%d words)",
            len(utterances),
            len(rechunked),
            len(words),
        )
    return rechunked


def _extract_finish_reason(data: Dict[str, Any]) -> str:
    """Return the first candidate's finishReason (upper-cased), if present."""
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return ""
    return str(candidates[0].get("finishReason") or "").upper()


def _transcript_coverage_seconds(transcript_data: Dict[str, Any]) -> float:
    """Return the last timestamp (in seconds) covered by a parsed transcript."""
    words = transcript_data.get("words") or []
    if words:
        return max((float(w.get("end", 0)) for w in words), default=0.0) / 1000.0
    utterances = transcript_data.get("utterances") or []
    if utterances:
        return max((float(u.get("end", 0)) for u in utterances), default=0.0) / 1000.0
    return 0.0


def _is_truncated_transcript(
    transcript_data: Dict[str, Any],
    video_duration_seconds: float,
    finish_reason: str = "",
) -> bool:
    """Detect transcripts truncated early, starved of words, or containing large internal speech dropouts."""
    if video_duration_seconds < 45.0:
        return False
    words = transcript_data.get("words") or []
    coverage = _transcript_coverage_seconds(transcript_data)

    # 1. Hit max tokens before 85% coverage
    if finish_reason == "MAX_TOKENS" and coverage < video_duration_seconds * 0.85:
        return True

    # 2. Coverage stopped well short of video duration (less than 70%)
    if coverage < video_duration_seconds * 0.70:
        return True

    # 3. Word starvation: conversational speech is typically 120-160 WPM. Anything < 30 WPM on a video >= 60s
    # indicates massive dropped speech (e.g. 195 words on a 603s video = 19.4 WPM).
    if video_duration_seconds >= 60.0:
        wpm = len(words) / (video_duration_seconds / 60.0)
        if wpm < 30.0:
            logger.warning(
                "Transcript is word-starved: %d words over %.1fs (%.1f WPM < 30.0 min threshold)",
                len(words),
                video_duration_seconds,
                wpm,
            )
            return True

    # 4. Large internal speech gap: conversational videos do not have 35+ seconds of complete silence.
    # A gap > 35s between consecutive words indicates speech dropout.
    if words and len(words) >= 2 and video_duration_seconds >= 60.0:
        for i in range(len(words) - 1):
            gap_ms = words[i + 1].get("start", 0) - words[i].get("end", 0)
            if gap_ms > 35_000:
                logger.warning(
                    "Transcript contains a large internal speech gap of %.1fs between word '%s' (%.1fs) and '%s' (%.1fs)",
                    gap_ms / 1000.0,
                    words[i].get("text", ""),
                    words[i].get("end", 0) / 1000.0,
                    words[i + 1].get("text", ""),
                    words[i + 1].get("start", 0) / 1000.0,
                )
                return True

    return False


def _parse_gemini_transcribe_response(data: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
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

    # Re-chunk any oversized speaker-turn utterances (single-speaker or multi-speaker) into natural segments
    utterances = _rechunk_long_utterances(utterances, words)

    full_text = " ".join(text_pieces).strip() or " ".join(w["text"] for w in words).strip()

    # Clean and normalize all transcript text to readable Roman Hinglish (zero Devanagari, standard English spelling)
    if words:
        raw_tokens = [str(w.get("text", "") or w.get("word", "")) for w in words]
        normalized_tokens = normalize_tokens_with_llm(raw_tokens, api_key=api_key, force_normalize=True)
        for w, norm in zip(words, normalized_tokens):
            w["text"] = norm

    for u in utterances:
        if u.get("words"):
            u["text"] = " ".join(w["text"] for w in u["words"])
        else:
            raw_u = str(u.get("text", ""))
            u["text"] = phonetic_devanagari_to_roman(raw_u) if has_devanagari(raw_u) else clean_hinglish_spelling(raw_u)

    full_text = " ".join(w["text"] for w in words).strip() if words else (phonetic_devanagari_to_roman(full_text) if has_devanagari(full_text) else clean_hinglish_spelling(full_text))

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words,
        "utterances": utterances,
        "text": full_text,
        "language_code": None,
    }


def _parse_gemini_fallback_response(data: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
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

    segments = None
    try:
        segments = json.loads(raw_text)
    except Exception as e:
        # Fallback 1: Extract slice between outer brackets
        start_bracket = raw_text.find("[")
        end_bracket = raw_text.rfind("]")
        if start_bracket != -1 and end_bracket > start_bracket:
            try:
                segments = json.loads(raw_text[start_bracket:end_bracket + 1])
            except Exception:
                pass

        # Fallback 2: Truncated JSON array repair (find last complete '}' and close array)
        if segments is None and start_bracket != -1:
            last_brace = raw_text.rfind("}")
            if last_brace > start_bracket:
                try:
                    repaired = raw_text[start_bracket:last_brace + 1] + "\n]"
                    segments = json.loads(repaired)
                    logger.info("Successfully recovered %d segments from truncated Gemini JSON response", len(segments))
                except Exception:
                    pass

    if not isinstance(segments, list):
        raise GeminiTranscriptionError(f"Failed to parse Gemini Flash JSON output: {raw_text[:200]}")

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

    fallback_full_text = " ".join(full_text_pieces)
    # Clean and normalize all fallback transcript text to readable Roman Hinglish (zero Devanagari, standard English spelling)
    if words:
        raw_tokens = [str(w.get("text", "") or w.get("word", "")) for w in words]
        normalized_tokens = normalize_tokens_with_llm(raw_tokens, api_key=api_key, force_normalize=True)
        for w, norm in zip(words, normalized_tokens):
            w["text"] = norm

    for u in utterances:
        if u.get("words"):
            u["text"] = " ".join(w["text"] for w in u["words"])
        else:
            raw_u = str(u.get("text", ""))
            u["text"] = phonetic_devanagari_to_roman(raw_u) if has_devanagari(raw_u) else clean_hinglish_spelling(raw_u)

    fallback_full_text = " ".join(w["text"] for w in words).strip() if words else (phonetic_devanagari_to_roman(fallback_full_text) if has_devanagari(fallback_full_text) else clean_hinglish_spelling(fallback_full_text))

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words,
        "utterances": utterances,
        "text": fallback_full_text,
        "language_code": None,
    }


def _detect_silence_split_points(
    audio_path: Path,
    duration: float,
    target_chunk_sec: float = 80.0,
    min_chunk_sec: float = 60.0,
) -> List[float]:
    """Detect natural speech pauses using ffmpeg silencedetect to split long audio safely."""
    cmd = [
        "ffmpeg",
        "-i",
        str(audio_path),
        "-af",
        "silencedetect=noise=-28dB:d=0.25",
        "-f",
        "null",
        "-",
    ]
    silence_midpoints = []
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        current_start = None
        for line in res.stderr.splitlines():
            if "silence_start:" in line:
                m = re.search(r"silence_start:\s*([0-9.]+)", line)
                if m:
                    current_start = float(m.group(1))
            elif "silence_end:" in line and current_start is not None:
                m = re.search(r"silence_end:\s*([0-9.]+)", line)
                if m:
                    end = float(m.group(1))
                    silence_midpoints.append((current_start + end) / 2.0)
                    current_start = None
    except Exception as e:
        logger.warning("silencedetect failed or timed out: %s", e)

    splits: List[float] = []
    last_split = 0.0
    while last_split + target_chunk_sec < duration - (min_chunk_sec / 2):
        target = last_split + target_chunk_sec
        candidates = [s for s in silence_midpoints if (last_split + min_chunk_sec) <= s <= (target + 25.0)]
        best = min(candidates, key=lambda s: abs(s - target)) if candidates else target
        splits.append(round(best, 2))
        last_split = best

    return splits


def _transcribe_audio_in_chunks(
    audio_path: Path,
    video_duration: float,
    api_key: str,
    language: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Transcribe long audio (>90s) in silence-split chunks to ensure 100% speech coverage."""
    splits = _detect_silence_split_points(audio_path, video_duration)
    intervals: List[Tuple[float, float]] = []
    prev = 0.0
    for sp in splits:
        intervals.append((prev, sp))
        prev = sp
    intervals.append((prev, video_duration))

    logger.info("Transcribing audio in %d silence-split chunks across %.1fs", len(intervals), video_duration)
    all_words: List[Dict[str, Any]] = []
    all_utterances: List[Dict[str, Any]] = []

    for idx, (start_sec, end_sec) in enumerate(intervals):
        chunk_len = end_sec - start_sec
        chunk_file = audio_path.parent / f"{audio_path.stem}_chunk_{idx}.mp3"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-t", str(chunk_len),
            "-i", str(audio_path),
            "-ac", "1", "-ar", "16000", "-b:a", "64k",
            str(chunk_file),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=60)
        except Exception as e:
            logger.warning("Failed to extract audio chunk %d: %s", idx, e)
            return None

        chunk_fname, chunk_furi = None, None
        try:
            chunk_fname, chunk_furi = _upload_audio_file(chunk_file, api_key)
            chunk_parsed: Optional[Dict[str, Any]] = None

            # Step A: Try gemini-3.5-transcribe with 1 retry on 429/503
            for attempt in range(2):
                try:
                    raw = _generate_with_gemini_transcribe(chunk_furi, api_key, language)
                    parsed = _parse_gemini_transcribe_response(raw, api_key=api_key)
                    c_words = parsed.get("words") or []
                    c_wpm = len(c_words) / (chunk_len / 60.0) if chunk_len > 0 else 0
                    has_gap = any(
                        (c_words[i + 1]["start"] - c_words[i]["end"]) > 25000
                        for i in range(len(c_words) - 1)
                    ) if len(c_words) >= 2 else False

                    # If chunk is starved or dropped speech, reject gemini-3.5-transcribe and fall back to flash-lite
                    if c_wpm < 40.0 or has_gap or (chunk_len >= 45.0 and len(c_words) < 20):
                        logger.warning(
                            "Chunk %d (%.1fs - %.1fs) had low transcribe coverage (%d words, %.1f WPM, gap=%s); using flash-lite fallback",
                            idx, start_sec, end_sec, len(c_words), c_wpm, has_gap
                        )
                        break
                    chunk_parsed = parsed
                    break
                except Exception as err:
                    if attempt == 0 and any(code in str(err) for code in ("503", "429")):
                        time.sleep(3.0)
                        continue
                    logger.warning("Chunk %d gemini-3.5-transcribe failed: %s; falling back to flash-lite", idx, err)
                    break

            # Step B: If gemini-3.5-transcribe was starved or failed, use gemini-3.5-flash-lite on this chunk
            if not chunk_parsed:
                for fb_model in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"):
                    try:
                        raw_fb = _generate_with_gemini_flash_fallback(
                            chunk_furi, api_key, model_name=fb_model, language=language, duration_seconds=chunk_len
                        )
                        parsed_fb = _parse_gemini_fallback_response(raw_fb, api_key=api_key)
                        if parsed_fb.get("words") or parsed_fb.get("utterances"):
                            chunk_parsed = parsed_fb
                            logger.info("Chunk %d transcribed by %s fallback: %d words", idx, fb_model, len(chunk_parsed.get("words", [])))
                            break
                    except Exception as fb_err:
                        logger.warning("Chunk %d fallback %s failed: %s", idx, fb_model, fb_err)

            if not chunk_parsed:
                logger.error("Failed to transcribe chunk %d with all models", idx)
                return None

            # Offset timestamps to global video timeline
            offset_ms = int(start_sec * 1000)
            for w in chunk_parsed.get("words") or []:
                w_copy = dict(w)
                w_copy["start"] += offset_ms
                w_copy["end"] += offset_ms
                all_words.append(w_copy)

            for u in chunk_parsed.get("utterances") or []:
                u_copy = dict(u)
                u_copy["start"] += offset_ms
                u_copy["end"] += offset_ms
                if u_copy.get("words"):
                    shifted_words = []
                    for sw in u_copy["words"]:
                        sw_copy = dict(sw)
                        sw_copy["start"] += offset_ms
                        sw_copy["end"] += offset_ms
                        shifted_words.append(sw_copy)
                    u_copy["words"] = shifted_words
                all_utterances.append(u_copy)

        finally:
            if chunk_fname:
                _delete_audio_file(chunk_fname, api_key)
            if chunk_file.exists():
                try:
                    chunk_file.unlink()
                except OSError:
                    pass
            time.sleep(0.5)

    if not all_words:
        return None

    all_utterances = _rechunk_long_utterances(all_utterances, all_words)
    full_text = " ".join(w["text"] for w in all_words).strip()

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": all_words,
        "utterances": all_utterances,
        "text": full_text,
        "language_code": None,
    }


def get_transcript_with_gemini(video_path: Path, runtime_config: Any) -> str:
    """Entry point for Google Gemini transcription in SupoClip pipeline.
    
    1. Extracts 16kHz mono audio from video_path.
    2. Uploads to Gemini Files API.
    3. Calls gemini-3.5-transcribe with verbatim word timestamps and diarization.
    4. Falls back to gemini-3.1-flash-lite or gemini-flash-lite-latest if transcribe hits rate limits or errors.
    5. Saves {video_path}.transcript_cache.json in exact SupoClip cache schema.
    6. Returns formatted text lines for AI analysis.
    """
    api_key = _resolve_gemini_api_key(runtime_config)
    audio_path = _prepare_audio_for_transcription(video_path)
    language = getattr(runtime_config, "transcription_language", None)
    try:
        video_duration = float(ffprobe_duration(video_path) or 0.0)
    except Exception:
        video_duration = 0.0

    logger.info("Uploading audio to Gemini for transcription: %s (duration=%.1fs)", audio_path, video_duration)
    file_name, file_uri = _upload_audio_file(audio_path, api_key)

    transcript_data: Optional[Dict[str, Any]] = None
    best_partial: Optional[Dict[str, Any]] = None
    best_partial_coverage = -1.0

    def _record_partial(candidate: Dict[str, Any]) -> None:
        nonlocal best_partial, best_partial_coverage
        cov = _transcript_coverage_seconds(candidate)
        if cov > best_partial_coverage or (
            cov == best_partial_coverage
            and len(candidate.get("words") or []) > len((best_partial or {}).get("words") or [])
        ):
            best_partial = candidate
            best_partial_coverage = cov

    try:
        # 1. Primary: gemini-3.5-transcribe (retry once on transient 503/429 before falling back)
        for attempt in range(2):
            try:
                logger.info("Starting primary transcription with model: gemini-3.5-transcribe (attempt %d/2)", attempt + 1)
                raw_response = _generate_with_gemini_transcribe(file_uri, api_key, language)
                finish_reason = _extract_finish_reason(raw_response)
                parsed = _parse_gemini_transcribe_response(raw_response, api_key=api_key)
                if parsed.get("words") or parsed.get("text"):
                    if _is_truncated_transcript(parsed, video_duration, finish_reason):
                        cov = _transcript_coverage_seconds(parsed)
                        logger.warning(
                            "Primary gemini-3.5-transcribe returned low-coverage transcript "
                            "(%.1fs/%.1fs, finish=%s, words=%d); trying fallback models for fuller coverage",
                            cov,
                            video_duration,
                            finish_reason or "unknown",
                            len(parsed.get("words") or []),
                        )
                        _record_partial(parsed)
                        break
                    transcript_data = parsed
                    logger.info(
                        "gemini-3.5-transcribe succeeded: %d words, %d utterances",
                        len(parsed["words"]),
                        len(parsed["utterances"]),
                    )
                    break
                logger.warning("gemini-3.5-transcribe returned empty output; attempting fallback")
                break
            except Exception as primary_err:
                if attempt == 0 and any(code in str(primary_err) for code in ("503", "429", "500", "502", "504")):
                    logger.warning("Primary gemini-3.5-transcribe transient error (%s); retrying in 4s...", primary_err)
                    time.sleep(4)
                    continue
                logger.warning("Primary gemini-3.5-transcribe failed: %s. Initiating fallback...", primary_err)
                break

        # 2. For long audio (>90s), try silence-bounded chunking before monolithic fallbacks
        if not transcript_data and video_duration > 90.0:
            try:
                chunked_result = _transcribe_audio_in_chunks(audio_path, video_duration, api_key, language)
                if chunked_result and not _is_truncated_transcript(chunked_result, video_duration):
                    transcript_data = chunked_result
                    logger.info(
                        "Chunked transcription succeeded: %d words, %d utterances",
                        len(chunked_result["words"]),
                        len(chunked_result["utterances"]),
                    )
            except Exception as chunk_err:
                logger.warning("Chunked transcription failed (%s); trying monolithic fallback models", chunk_err)

        # 3. Fallback: prefer non-thinking flash-lite models first so full-length JSON transcripts never truncate
        if not transcript_data:
            fallback_models = [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-flash-lite-latest",
                "gemini-3-flash-preview",
                "gemini-3.8-flash",
            ]
            last_fallback_err = None
            for f_model in fallback_models:
                try:
                    logger.info("Attempting fallback transcription with model: %s", f_model)
                    try:
                        raw_fallback = _generate_with_gemini_flash_fallback(
                            file_uri, api_key, model_name=f_model, language=language, duration_seconds=video_duration
                        )
                    except TypeError:
                        raw_fallback = _generate_with_gemini_flash_fallback(
                            file_uri, api_key, model_name=f_model, language=language
                        )
                    finish_reason = _extract_finish_reason(raw_fallback)
                    parsed_fallback = _parse_gemini_fallback_response(raw_fallback, api_key=api_key)
                    if parsed_fallback.get("words") or parsed_fallback.get("text"):
                        if _is_truncated_transcript(parsed_fallback, video_duration, finish_reason):
                            cov = _transcript_coverage_seconds(parsed_fallback)
                            logger.warning(
                                "Fallback %s returned truncated/low-coverage transcript "
                                "(%.1fs/%.1fs, finish=%s, words=%d); trying next fallback",
                                f_model,
                                cov,
                                video_duration,
                                finish_reason or "unknown",
                                len(parsed_fallback.get("words") or []),
                            )
                            _record_partial(parsed_fallback)
                            continue
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

            if not transcript_data and best_partial is not None:
                logger.warning(
                    "Using best available partial Gemini transcript (%.1fs/%.1fs, %d words)",
                    best_partial_coverage,
                    video_duration,
                    len(best_partial.get("words") or []),
                )
                transcript_data = best_partial

            if not transcript_data:
                raise GeminiTranscriptionError(
                    f"All Gemini transcription attempts failed. Last error: {last_fallback_err}"
                )

    finally:
        # Cleanup uploaded file from Gemini storage
        _delete_audio_file(file_name, api_key)

    # Ensure utterances are granular before saving cache and formatting for AI analysis
    utterances = _rechunk_long_utterances(
        transcript_data.get("utterances") or [],
        transcript_data.get("words") or [],
    )
    transcript_data["utterances"] = utterances

    # Save to transcript cache file for subtitles and timeline rendering
    cache_path = video_path.with_suffix(".transcript_cache.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(transcript_data, f, ensure_ascii=False)
    logger.info("Saved Gemini transcript cache to: %s (%d words, %d utterances)", cache_path, len(transcript_data["words"]), len(utterances))

    # Format transcript lines for AI clip analysis
    formatted_lines: List[str] = []

    def _speaker_prefix(spk: Any) -> str:
        if not spk:
            return ""
        text_spk = str(spk).strip()
        if not text_spk:
            return ""
        return f"{text_spk}: " if text_spk.lower().startswith("speaker") else f"Speaker {text_spk}: "

    if utterances:
        for u in utterances:
            start_str = format_ms_to_timestamp(u.get("start", 0))
            end_str = format_ms_to_timestamp(u.get("end", 0))
            prefix = _speaker_prefix(u.get("speaker"))
            formatted_lines.append(f"[{start_str} - {end_str}] {prefix}{u.get('text', '')}")
    elif transcript_data.get("words"):
        # Group into ~15 word chunks
        words = transcript_data["words"]
        for i in range(0, len(words), 15):
            chunk = words[i:i + 15]
            start_str = format_ms_to_timestamp(chunk[0]["start"])
            end_str = format_ms_to_timestamp(chunk[-1]["end"])
            chunk_text = " ".join(w["text"] for w in chunk)
            prefix = _speaker_prefix(chunk[0].get("speaker"))
            formatted_lines.append(f"[{start_str} - {end_str}] {prefix}{chunk_text}")
    else:
        formatted_lines.append(f"[00:00 - 00:00] {transcript_data.get('text', '')}")

    result = "\n".join(formatted_lines)
    logger.info("Gemini transcript formatted: %d segments, %d chars", len(formatted_lines), len(result))
    return result
