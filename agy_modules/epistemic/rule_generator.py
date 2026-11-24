"""Epistemic Module: Rule Generator
Writes epistemic rules to the Antigravity discovery location (.agents/rules/epistemic.md and task_automation.md).
"""
import os
from .scimind_mandates import SCIMIND_5_0_MANDATES
from .task_mandates import TASK_AUTOMATION_MANDATES

def install_task_automation_rule(target_dir: str = ".agents/rules") -> str:
    """Ensure .agents/rules/task_automation.md exists with task automation mandates."""
    os.makedirs(target_dir, exist_ok=True)
    rule_file = os.path.join(target_dir, "task_automation.md")
    
    header = (
        "---\n"
        "name: task-automation-mandates\n"
        "description: Mandates proactive task decomposition, live UI step tracking via update_steps, task.md lifecycle, and asynchronous task management.\n"
        "trigger: always_on\n"
        "---\n\n"
    )
    
    content = header + TASK_AUTOMATION_MANDATES
    with open(rule_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    return rule_file

def install_epistemic_rule(target_dir: str = ".agents/rules") -> str:
    """Ensure .agents/rules/epistemic.md and task_automation.md exist with current mandates."""
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
        
    # Also ensure task automation rule is deployed alongside epistemic rules
    install_task_automation_rule(target_dir)
        
    return rule_file

if __name__ == "__main__":
    path = install_epistemic_rule()
    task_path = install_task_automation_rule()
    print(f"Installed epistemic rule at {path} and task automation at {task_path}")
