"""Pydantic models for the feur feature."""

from pydantic import BaseModel


class FeurStats(BaseModel):
    total: int = 0
    feur: int = 0
    pour_feur: int = 0
