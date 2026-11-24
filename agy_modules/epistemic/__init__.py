"""AGY Epistemic Module"""
from .scimind_mandates import SCIMIND_5_0_MANDATES, get_epistemic_mandate_text
from .task_mandates import TASK_AUTOMATION_MANDATES, get_task_automation_mandate_text
from .rule_generator import install_epistemic_rule, install_task_automation_rule

__all__ = [
    "SCIMIND_5_0_MANDATES",
    "get_epistemic_mandate_text",
    "TASK_AUTOMATION_MANDATES",
    "get_task_automation_mandate_text",
    "install_epistemic_rule",
    "install_task_automation_rule",
]
