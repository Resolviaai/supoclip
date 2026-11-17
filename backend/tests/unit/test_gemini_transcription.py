import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.media.gemini_transcription import (
    _parse_offset_seconds,
    _parse_gemini_transcribe_response,
    _parse_gemini_fallback_response,
    _chunk_words_into_utterances,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
)
from src.ai import _get_gemini_fallback_model


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


def test_parse_gemini_fallback_response():
    sample_json = [
        {
            "start": 0.0,
            "end": 2.5,
            "speaker": "Speaker 1",
            "text": "Welcome to SupoClip AI video clipping.",
            "words": [
                {"word": "Welcome", "start": 0.0, "end": 0.4},
                {"word": "to", "start": 0.4, "end": 0.6},
                {"word": "SupoClip", "start": 0.6, "end": 1.2},
            ]
        }
    ]
    raw_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "```json\n" + json.dumps(sample_json) + "\n```"}
                    ]
                }
            }
        ]
    }
    result = _parse_gemini_fallback_response(raw_response)
    assert result["version"] == TRANSCRIPT_CACHE_SCHEMA_VERSION
    assert len(result["utterances"]) == 1
    assert len(result["words"]) == 3
    assert result["words"][0]["text"] == "Welcome"
    assert result["words"][0]["start"] == 0
    assert result["words"][0]["end"] == 400
    assert "Welcome to SupoClip" in result["text"]


def test_gemini_llm_fallback_hierarchy():
    assert _get_gemini_fallback_model("google-gla:gemini-3.5-flash-lite") == "google-gla:gemini-3.1-flash-lite"
    assert _get_gemini_fallback_model("google:gemini-3.5-flash-lite") == "google:gemini-3.1-flash-lite"
    assert _get_gemini_fallback_model("google-gla:gemini-3.1-flash-lite") == "google-gla:gemini-2.5-flash-lite"
    assert _get_gemini_fallback_model("openai:gpt-5.2") is None


def test_chunk_words_into_utterances():
    # 25 words with punctuation and pause
    words = []
    for i in range(25):
        punctuation = "." if i in (9, 19) else ""
        gap = 1500 if i == 15 else 100
        start = i * 500 + gap
        words.append({
            "text": f"word{i}{punctuation}",
            "start": start,
            "end": start + 300,
            "speaker": "speaker_0",
        })
    chunks = _chunk_words_into_utterances(words, target_chunk_size=15)
    assert len(chunks) >= 2
    for c in chunks:
        assert "text" in c
        assert "start" in c
        assert "end" in c
        assert c["end"] > c["start"]
        assert len(c["words"]) > 0


if __name__ == "__main__":
    test_parse_offset_seconds()
    test_parse_gemini_transcribe_response_valid()
    test_parse_gemini_transcribe_response_empty()
    test_parse_gemini_fallback_response()
    test_gemini_llm_fallback_hierarchy()
    test_chunk_words_into_utterances()
    print("ALL GEMINI TRANSCRIPTION & LLM FALLBACK TESTS PASSED!")
