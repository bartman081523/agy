"""Socratic Dialog Module: Rule Generator
Writes Socratic dialog moves to .agents/rules/socratic.md.
"""
import os
from .dialog_moves import SOCRATIC_DIALOG_PREAMBLE

def install_socratic_rule(target_dir: str = ".agents/rules") -> str:
    """Ensure .agents/rules/socratic.md exists."""
    os.makedirs(target_dir, exist_ok=True)
    rule_file = os.path.join(target_dir, "socratic.md")
    
    header = (
        "---\n"
        "name: sokratischer-dialog-moves\n"
        "description: Enforces Socratic Aporia, Platonic Ideal-Checks, and Aristotelian Self-Critique.\n"
        "trigger: always_on\n"
        "---\n\n"
    )
    
    content = header + SOCRATIC_DIALOG_PREAMBLE
    with open(rule_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    return rule_file

if __name__ == "__main__":
    path = install_socratic_rule()
    print(f"Installed socratic rule at {path}")
