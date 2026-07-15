"""Options research pipeline — chain, events, strategy ranking, OptionsTradePlan."""

from .models import OptionsResearchDoc

__all__ = ["OptionsResearchDoc", "format_options_report", "run_options_research"]


def __getattr__(name: str):
    if name == "run_options_research":
        from .aggregator import run_options_research

        return run_options_research
    if name == "format_options_report":
        from .format import format_options_report

        return format_options_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
