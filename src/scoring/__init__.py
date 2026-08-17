"""Transparent rules-based prospect and data-quality scoring."""

from .model import DataQualityInputs, ProspectFeatures, ScoreResult, score_prospect

__all__ = ["DataQualityInputs", "ProspectFeatures", "ScoreResult", "score_prospect"]

