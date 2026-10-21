"""AGY Socratic Dialog Module"""
from .dialog_moves import SOCRATIC_DIALOG_PREAMBLE, get_socratic_dialog_text
from .rule_generator import install_socratic_rule

__all__ = ["SOCRATIC_DIALOG_PREAMBLE", "get_socratic_dialog_text", "install_socratic_rule"]
