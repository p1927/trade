"""GitHub open datasets (bond yields, gold, FX) ingest into hub."""

from .ingest import ingest_github_macro_datasets, verify_github_macro_merge

__all__ = ["ingest_github_macro_datasets", "verify_github_macro_merge"]
