"""Output contracts for the generic demonstration project."""

from pydantic import BaseModel, Field


class AnalysisResult(BaseModel):
    summary: str
    findings: list[str]


class ReviewResult(BaseModel):
    approved: bool
    concerns: list[str]


class FinalResult(BaseModel):
    answer: str
    confidence: float = Field(ge=0, le=1)
