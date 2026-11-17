import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.media.gemini_transcription import (
    _parse_offset_seconds,
    _parse_gemini_transcribe_response,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
)


def test_parse_offset_seconds():
    assert _parse_offset_seconds("0.500s") == 0.5
    assert _parse_offset_seconds("12.345s") == 12.345
    assert _parse_offset_seconds("0s") == 0.0
    assert _parse_offset_seconds(3.5) == 3.5
    assert _parse_offset_seconds("invalid") == 0.0


def test_parse_gemini_transcribe_response_valid():
    sample_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "audioTranscription": {
                                "speakerLabel": "speaker_0",
                                "words": [
                                    {"word": "Hello", "startOffset": "0.100s", "endOffset": "0.400s"},
                                    {"word": "world", "startOffset": "0.450s", "endOffset": "0.900s"},
                                ],
                            }
                        },
                        {
                            "audioTranscription": {
                                "speakerLabel": "speaker_1",
                                "words": [
                                    {"word": "SupoClip", "startOffset": "1.000s", "endOffset": "1.500s"},
                                    {"word": "transcription", "startOffset": "1.550s", "endOffset": "2.100s"},
                                ],
                            }
                        },
                    ]
                }
            }
        ]
    }

    result = _parse_gemini_transcribe_response(sample_response)
    assert result["version"] == TRANSCRIPT_CACHE_SCHEMA_VERSION
    words = result["words"]
    assert len(words) == 4
    assert words[0]["text"] == "Hello"
    assert words[0]["start"] == 100  # ms
    assert words[0]["end"] == 400
    assert words[0]["speaker"] == "speaker_0"

    assert words[2]["text"] == "SupoClip"
    assert words[2]["start"] == 1000
    assert words[2]["speaker"] == "speaker_1"

    assert len(result["utterances"]) == 2
    assert "Hello world" in result["text"]


def test_parse_gemini_transcribe_response_empty():
    res = _parse_gemini_transcribe_response({})
    assert res["version"] == TRANSCRIPT_CACHE_SCHEMA_VERSION
    assert res["words"] == []
    assert res["text"] == ""
