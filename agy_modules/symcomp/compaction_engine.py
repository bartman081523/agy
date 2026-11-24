"""SymComp Module: Converged Construct Compaction Engine & Token Saver
Ported from pi-variants (extensions/pi-symcomp) and claude-code/rebuild (gen_patch_symcomp.py).
"""
import re
from typing import List, Dict, Any, Optional

SYMCOMP_MARKER = "<!-- symcomp-converged -->"
SYMCOMP_RULE_TEXT = """# SymComp Construct Compaction & Token Preservation

When context compaction or summarization occurs:
- Consolidate disparate `<construct>` nodes into a single unified `<construct id="℧-converged">` block.
- Retain only verified invariants and active hypotheses; prune historical intermediates.
- Keep the summary byte-bounded.
- Prime the immediate next step with a explicit `Next: <action>` trailer.
"""

class SymCompEngine:
    """Performs semantic reduction on reasoning history and construct chains."""

    def __init__(self, max_bytes: int = 2048):
        self.max_bytes = max_bytes

    def compact_constructs(self, text_history: str, next_action: Optional[str] = None) -> str:
        """Merge all <construct> occurrences into one consolidated construct."""
        # Find all construct matches
        pattern = re.compile(r'<construct\s+id="([^"]+)"[^>]*>([\s\S]*?)</construct>')
        matches = list(pattern.finditer(text_history))

        if not matches:
            summary = text_history[:self.max_bytes]
            if next_action:
                summary += f"\n\nNext: {next_action}"
            return summary

        claims = []
        for m in matches:
            body = m.group(2)
            c_match = re.search(r'<claim>([\s\S]*?)</claim>', body)
            if c_match:
                claim_text = c_match.group(1).strip()
                if claim_text not in claims:
                    claims.append(claim_text)

        consolidated = [
            SYMCOMP_MARKER,
            '<construct id="℧-converged" status="verified">',
            '  <claim>',
            *[f"    - {c}" for c in claims],
            '  </claim>',
            '</construct>'
        ]

        if next_action:
            consolidated.append(f"\nNext: {next_action}")

        result = "\n".join(consolidated)
        if len(result) > self.max_bytes:
            result = result[:self.max_bytes] + "\n<!-- truncated -->"

        return result
