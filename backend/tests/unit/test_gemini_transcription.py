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


from src.media.transliteration import (
    has_devanagari,
    phonetic_devanagari_to_roman,
    normalize_tokens_with_llm,
    normalize_text_with_llm,
)


def test_devanagari_transliteration():
    sample_hindi = "तो या तो कैसे शुरू किया जाए? इस वीडियो का नाम है द कन्फेशन।"
    assert has_devanagari(sample_hindi)
    roman = phonetic_devanagari_to_roman(sample_hindi)
    assert not has_devanagari(roman)
    assert "kaise" in roman and "shuru" in roman and "video" in roman


def test_normalize_tokens_with_llm_preserves_length(monkeypatch):
    tokens = ["प्रीमियर", "प्रो", "में", "कट", "लगाना", "सीखो"]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(["Premiere", "Pro", "mein", "cut", "lagana", "seekho"])}
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr("requests.post", lambda *a, **kw: mock_resp)

    normalized = normalize_tokens_with_llm(tokens, api_key="test-key")
    assert len(normalized) == len(tokens)
    assert not any(has_devanagari(t) for t in normalized)
    # Check that standard English spelling is produced
    assert "Premiere" in normalized or "premiere" in [t.lower() for t in normalized]
    assert "Pro" in normalized or "pro" in [t.lower() for t in normalized]


def test_normalize_tokens_fallback_without_api_key(monkeypatch):
    tokens = ["प्रीमियर", "प्रो", "में", "कट", "लगाना", "सीखो"]
    monkeypatch.setenv("GEMINI_API_KEY", "")
    normalized = normalize_tokens_with_llm(tokens, api_key="")
    assert len(normalized) == len(tokens)
    assert not any(has_devanagari(t) for t in normalized)


def test_parse_gemini_transcribe_rechunks_multi_speaker_giant_utterance():
    """Multi-speaker diarization with 1 long monologue utterance must still be split into granular utterances."""
    long_words = [
        {"word": f"word{i}" + ("." if i % 10 == 9 else ""), "startOffset": f"{10 + i * 0.5:.2f}s", "endOffset": f"{10 + i * 0.5 + 0.3:.2f}s"}
        for i in range(45)
    ]
    sample_response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "audioTranscription": {
                                "speakerLabel": "spk:0",
                                "words": [
                                    {"word": "Intro", "startOffset": "1.0s", "endOffset": "1.5s"},
                                    {"word": "hook.", "startOffset": "1.6s", "endOffset": "2.0s"},
                                ],
                            }
                        },
                        {
                            "audioTranscription": {
                                "speakerLabel": "spk:1",
                                "words": [
                                    {"word": "Stick", "startOffset": "3.0s", "endOffset": "3.4s"},
                                    {"word": "to", "startOffset": "3.5s", "endOffset": "3.7s"},
                                    {"word": "script.", "startOffset": "3.8s", "endOffset": "4.2s"},
                                ],
                            }
                        },
                        {
                            "audioTranscription": {
                                "speakerLabel": "spk:0",
                                "words": long_words,
                            }
                        },
                    ]
                },
            }
        ]
    }

    parsed = _parse_gemini_transcribe_response(sample_response)
    assert len(parsed["words"]) == 50
    # 2 short intro utterances + at least 4 chunked utterances from the 45-word monologue
    assert len(parsed["utterances"]) >= 6
    assert all(len(u["words"]) <= 15 for u in parsed["utterances"])


def test_get_transcript_with_gemini_skips_truncated_fallback_for_full_transcript(tmp_path, monkeypatch):
    from src.media import gemini_transcription as gt

    video_path = tmp_path / "sample.mp4"
    audio_path = tmp_path / "sample.transcription.mp3"
    video_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")

    monkeypatch.setattr(gt, "_prepare_audio_for_transcription", lambda p: audio_path)
    monkeypatch.setattr(gt, "ffprobe_duration", lambda p: 410.0)
    monkeypatch.setattr(gt, "_upload_audio_file", lambda p, k: ("files/test", "https://example/files/test"))
    monkeypatch.setattr(gt, "_delete_audio_file", lambda n, k: None)
    monkeypatch.setattr(gt, "_generate_with_gemini_transcribe", MagicMock(side_effect=RuntimeError("503")))
    monkeypatch.setattr(gt.time, "sleep", lambda _: None)

    calls = []

    def fake_fallback(file_uri, api_key, model_name, language=None, *args, **kwargs):
        calls.append(model_name)
        if len(calls) == 1:
            # Truncated at 26s of 410s video with MAX_TOKENS
            return {
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {
                            "parts": [
                                {"text": json.dumps([{"start": 2.0, "end": 26.0, "speaker": "Speaker 1", "text": "Only opening lines"}])}
                            ]
                        },
                    }
                ]
            }
        # Full transcript up to 405s with realistic word count
        first_half = "First half of the video covering everything in detail. " * 25
        second_half = "Second half of the video finishing all thoughts and closing out. " * 25
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps([
                                    {"start": 2.0, "end": 180.0, "speaker": "Speaker 1", "text": first_half.strip()},
                                    {"start": 185.0, "end": 405.0, "speaker": "Speaker 1", "text": second_half.strip()},
                                ])
                            }
                        ]
                    },
                }
            ]
        }

    monkeypatch.setattr(gt, "_generate_with_gemini_flash_fallback", fake_fallback)
    cfg = MagicMock(gemini_api_key="test-key", google_api_key=None, transcription_language=None)

    formatted = gt.get_transcript_with_gemini(video_path, cfg)

    assert len(calls) == 2
    assert "Second half of the video" in formatted
    assert "Speaker Speaker 1:" not in formatted
    assert "Speaker 1:" in formatted
    cached = json.loads(video_path.with_suffix(".transcript_cache.json").read_text(encoding="utf-8"))
    assert cached["words"][-1]["end"] >= 400_000


def test_is_truncated_transcript_detects_gap_and_low_wpm():
    from src.media.gemini_transcription import _is_truncated_transcript

    # Case 1: 380s gap in speech (like the previous zYR04cv5LGQ bug)
    gapped_transcript = {
        "words": [
            {"text": "Hello", "start": 1000, "end": 2000},
            {"text": "World", "start": 2500, "end": 5000},
            {"text": "Bye", "start": 386000, "end": 387000},  # 381s gap!
        ]
    }
    assert _is_truncated_transcript(gapped_transcript, video_duration_seconds=400.0) is True

    # Case 2: Low word density (starved words: 20 words for 300s = 4 WPM)
    starved_words = [
        {"text": f"word{i}", "start": i * 10000, "end": i * 10000 + 500}
        for i in range(20)
    ]
    starved_transcript = {"words": starved_words}
    assert _is_truncated_transcript(starved_transcript, video_duration_seconds=300.0) is True

    # Case 3: Normal conversational transcript (120 WPM, no gaps > 35s, covers video)
    normal_words = [
        {"text": f"word{i}", "start": i * 500, "end": i * 500 + 400}
        for i in range(200)
    ]
    normal_transcript = {"words": normal_words}
    assert _is_truncated_transcript(normal_transcript, video_duration_seconds=100.0) is False

    # Case 4: Trailing gap (last word ends at 200s for a 300s video -> 100s missing at the end)
    trailing_words = [
        {"text": f"word{i}", "start": i * 500, "end": i * 500 + 400}
        for i in range(400)  # ends at 200s
    ]
    trailing_transcript = {"words": trailing_words}
    assert _is_truncated_transcript(trailing_transcript, video_duration_seconds=300.0) is True


def test_normalize_tokens_fast_path_for_english(monkeypatch):
    tokens = ["Welcome", "to", "the", "future", "of", "video", "creation", "in", "2027"]
    # Ensure requests.post is NEVER called on non-Devanagari tokens
    post_called = False

    def fake_post(*a, **kw):
        nonlocal post_called
        post_called = True
        raise RuntimeError("Should not be called for English tokens")

    monkeypatch.setattr("requests.post", fake_post)
    normalized = normalize_tokens_with_llm(tokens, api_key="some-key")
    assert not post_called
    assert len(normalized) == len(tokens)
    assert normalized == tokens


if __name__ == "__main__":
    test_parse_offset_seconds()
    test_parse_gemini_transcribe_response_valid()
    test_parse_gemini_transcribe_response_empty()
    test_parse_gemini_fallback_response()
    test_gemini_llm_fallback_hierarchy()
    test_chunk_words_into_utterances()
    test_devanagari_transliteration()
    print("ALL GEMINI TRANSCRIPTION & LLM FALLBACK TESTS PASSED!")
