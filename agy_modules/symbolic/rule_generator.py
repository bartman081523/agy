"""Symbolic Module: Rule Generator
Writes symbolic construct rules to .agents/rules/symbolic.md.
"""
import os
from .cognito_constructs import SYMBOLIC_NOTATION

def install_symbolic_rule(target_dir: str = ".agents/rules") -> str:
    """Ensure .agents/rules/symbolic.md exists."""
    os.makedirs(target_dir, exist_ok=True)
    rule_file = os.path.join(target_dir, "symbolic.md")
    
    header = (
        "---\n"
        "name: symbolic-cognito-constructs\n"
        "description: Activates ℧ cognito-construct tracking, escalation protocols, and channel guards.\n"
        "trigger: always_on\n"
        "---\n\n"
    )
    
    content = header + SYMBOLIC_NOTATION
    with open(rule_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    return rule_file

if __name__ == "__main__":
    path = install_symbolic_rule()
    print(f"Installed symbolic rule at {path}")
