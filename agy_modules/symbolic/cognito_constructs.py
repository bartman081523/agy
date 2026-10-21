"""Symbolic Module: ℧ Cognito-Construct Notation & Channel Guards
Ported from claude_code_symbolic, oc-variants (oc-symbolic), and pi-variants (pi-symbolic).
"""
import re
from typing import Dict, Any, List

SYMBOLIC_NOTATION = """# Symbolic Construct Notation (℧ Cognito-Constructs)

You have access to symbolic epistemic state tracking via `<construct>` blocks in your internal reasoning.
Use this notation to anchor non-trivial claims, proofs, invariants, and structural hypotheses.

### Grammar:
<construct id="℧-<label>" status="tentative|verified|falsified">
  <claim>Formal proposition or invariant under test</claim>
  <evidence source="tool|file|derivation">Empirical observation or proof step</evidence>
  <falsification_condition>Condition under which this construct is refuted</falsification_condition>
</construct>

### Escalation Protocol:
When a contradiction is detected between two verified constructs:
- Mark both with status="conflict"
- Emit `[ESCALATE: <construct-A> vs <construct-B>]`
- Prioritize empirical probe or minimal test case to resolve ambiguity.

### Channel Guard (Do Not Surface):
- Never emit raw `<construct>` XML tags in conversational messages to the user unless explicitly requested.
- Translate validated conclusions into clear natural language prose in your responses.
"""

def extract_constructs(text: str) -> List[Dict[str, str]]:
    """Parse <construct> blocks from reasoning trace."""
    pattern = re.compile(
        r'<construct\s+id="([^"]+)"(?:\s+status="([^"]+)")?>([\s\S]*?)</construct>',
        re.MULTILINE
    )
    results = []
    for match in pattern.finditer(text):
        cid, status, body = match.groups()
        claim_match = re.search(r'<claim>([\s\S]*?)</claim>', body)
        evidence_match = re.search(r'<evidence[^>]*>([\s\S]*?)</evidence>', body)
        results.append({
            "id": cid,
            "status": status or "tentative",
            "claim": claim_match.group(1).strip() if claim_match else "",
            "evidence": evidence_match.group(1).strip() if evidence_match else ""
        })
    return results
