"""Server-owned provider registry; no paid transport exists until P05 Task 4."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import NEBIUS_URL, TAVILY_URL


@dataclass(frozen=True)
class ProviderTarget:
    base_url: str
    model: str | None


def provider_target(purpose: str, tier: str = 'fast') -> ProviderTarget:
    if purpose == 'infer':
        models = {
            'fast': 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B',
            'smart': 'nvidia/nemotron-3-super-120b-a12b',
            'deep': 'nvidia/Nemotron-3-Ultra-550b-a55b',
        }
        if tier in models:
            return ProviderTarget(NEBIUS_URL, models[tier])
    elif purpose == 'vision' and tier == 'fast':
        return ProviderTarget(NEBIUS_URL, 'openbmb/MiniCPM-V-4_5')
    elif purpose == 'embed' and tier == 'fast':
        return ProviderTarget(NEBIUS_URL, 'Qwen/Qwen3-Embedding-8B')
    elif purpose == 'search' and tier == 'fast':
        return ProviderTarget(TAVILY_URL, None)
    raise ValueError('Unsupported provider operation or tier.')


class PreparedPassage(BaseModel):
    """P01 provenance is data. It does not authenticate or grant paid access."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    text: str = Field(max_length=32768)
    origin: Literal['user_request','note','standing','memory','lesson','web','history','agenda','model','diagnostic']
    note_id: str | None = Field(default=None, min_length=1, max_length=512)
    title: str = Field(default='', max_length=1024)
    modified: str = Field(default='', max_length=256)

    @model_validator(mode='after')
    def require_source(self):
        if self.origin in {'note','standing','memory','lesson','history'} and not self.note_id:
            raise ValueError('Note-derived passage requires a source identity.')
        return self
