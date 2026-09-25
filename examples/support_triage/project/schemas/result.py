"""Structured outputs keep workflow routing and final responses inspectable."""

from typing import Literal

from pydantic import BaseModel, Field


class TicketClassification(BaseModel):
    priority: Literal["P1", "P2", "P3"]
    category: Literal["access", "billing", "other"]
    route: Literal["escalate", "knowledge"]
    reason: str


class KnowledgeResult(BaseModel):
    article_ids: list[str]
    facts: list[str]
    no_match: bool


class ResponseDraft(BaseModel):
    message: str
    article_ids: list[str]
    needs_human: bool


class SupportResponse(BaseModel):
    priority: Literal["P1", "P2", "P3"]
    category: Literal["access", "billing", "other"]
    response: str = Field(min_length=1)
    article_ids: list[str]
    needs_human: bool
