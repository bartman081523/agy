"""SymComp Module: Rule Generator
Writes SymComp rules to .agents/rules/symcomp.md.
"""
import os
from .compaction_engine import SYMCOMP_RULE_TEXT

def install_symcomp_rule(target_dir: str = ".agents/rules") -> str:
    """Ensure .agents/rules/symcomp.md exists."""
    os.makedirs(target_dir, exist_ok=True)
    rule_file = os.path.join(target_dir, "symcomp.md")
    
    header = (
        "---\n"
        "name: symcomp-construct-compaction\n"
        "description: Governs token-efficient converged construct compaction and next-step priming.\n"
        "trigger: always_on\n"
        "---\n\n"
    )
    
    content = header + SYMCOMP_RULE_TEXT
    with open(rule_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    return rule_file

if __name__ == "__main__":
    path = install_symcomp_rule()
    print(f"Installed symcomp rule at {path}")
