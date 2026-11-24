"""Epistemic Module: Rule Generator
Writes epistemic rules to the Antigravity discovery location (.agents/rules/epistemic.md).
"""
import os
from .scimind_mandates import SCIMIND_5_0_MANDATES

def install_epistemic_rule(target_dir: str = ".agents/rules") -> str:
    """Ensure .agents/rules/epistemic.md exists with current mandates."""
    os.makedirs(target_dir, exist_ok=True)
    rule_file = os.path.join(target_dir, "epistemic.md")
    
    header = (
        "---\n"
        "name: epistemic-humility-mandates\n"
        "description: Enforces SciMind 5.0 Epistemic Humility, Empirical Verification, and Operational Robustness.\n"
        "trigger: always_on\n"
        "---\n\n"
    )
    
    content = header + SCIMIND_5_0_MANDATES
    with open(rule_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    return rule_file

if __name__ == "__main__":
    path = install_epistemic_rule()
    print(f"Installed epistemic rule at {path}")
