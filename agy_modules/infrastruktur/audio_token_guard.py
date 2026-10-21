"""Infrastructure Module: Audio & Token Stability Guard
Ported from gemini-cli fix/audio-nesting-and-token-count (commit 587e3b9a5).
"""
from typing import Dict, Any, List

class AudioTokenGuard:
    """Protects tool payloads and multi-modal contexts against memory bloat and API overruns."""

    DEFAULT_MAX_TOKEN_BUDGET = 128_000
    AUDIO_WAV_MAX_BYTES = 10 * 1024 * 1024  # 10 MB bound

    @staticmethod
    def estimate_text_tokens(text: str) -> int:
        """Heuristic token estimation: ~4 chars per token for English/Code."""
        return max(1, len(text) // 4)

    @classmethod
    def sanitize_audio_payload(cls, data: bytes, mime_type: str = "audio/wav") -> Dict[str, Any]:
        """Validate audio payload size to avoid 400 Bad Request API errors."""
        byte_len = len(data)
        if byte_len > cls.AUDIO_WAV_MAX_BYTES:
            return {
                "valid": False,
                "error": f"Payload size {byte_len} exceeds maximum allowed {cls.AUDIO_WAV_MAX_BYTES} bytes",
                "bytes": byte_len
            }
        return {
            "valid": True,
            "bytes": byte_len,
            "mime_type": mime_type
        }

    @classmethod
    def check_context_window(cls, turns: List[Dict[str, str]], budget: int = DEFAULT_MAX_TOKEN_BUDGET) -> Dict[str, Any]:
        """Check accumulated context turns against the target token budget."""
        total_chars = sum(len(turn.get("content", "")) for turn in turns)
        estimated_tokens = total_chars // 4
        return {
            "estimated_tokens": estimated_tokens,
            "budget": budget,
            "within_budget": estimated_tokens <= budget,
            "utilization_pct": round((estimated_tokens / budget) * 100, 2)
        }
