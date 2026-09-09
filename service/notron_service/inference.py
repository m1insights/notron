"""Fixed metered operations. Rates are operator-supplied integer micro-USD."""
import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .providers import PreparedPassage, provider_target
from .usage import UsageError
try:
    from .privacy import redact  # wheel contains the exact desktop policy source
except ImportError:
    # Editable development loads the same source independent of working directory.
    # The built wheel contains this exact file as .privacy.
    from importlib.util import spec_from_file_location,module_from_spec
    from pathlib import Path
    _spec=spec_from_file_location('notron_shared_privacy',Path(__file__).resolve().parents[2]/'notron'/'privacy.py')
    _privacy=module_from_spec(_spec);_spec.loader.exec_module(_privacy)
    redact=_privacy.redact

class PaidRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: UUID
    lease_fence: int=Field(gt=0,strict=True)
    tier: Literal['fast','smart','deep']='fast'
    passages: list[PreparedPassage]=Field(min_length=1,max_length=64)
    system: str=Field(default='',max_length=16384)
    max_tokens: int=Field(default=3200,ge=1,le=16384,strict=True)
    json_mode: bool=False
    temperature: float=Field(default=0.3,ge=0,le=1)
    image: str | None=Field(default=None,max_length=700000,repr=False)
    mime: Literal['image/png','image/jpeg','image/webp','image/gif'] | None=None
    limit: int=Field(default=5,ge=1,le=5,strict=True)
    depth: Literal['basic','advanced']='basic'

    @model_validator(mode='after')
    def bounded(self):
        if sum(len(p.text.encode()) for p in self.passages)+len(self.system.encode())>131072:
            raise ValueError('input_too_large')
        return self

@dataclass(frozen=True)
class RateTable:
    version: str
    rates: dict
    vision_input_tokens: int | None = None
    def __post_init__(self):
        if type(self.vision_input_tokens) is not int or not 1<=self.vision_input_tokens<=2000000:
            raise ValueError('invalid_vision_budget')
        expected={'infer:fast','infer:smart','infer:deep','vision:fast','embed:fast','search:fast'}
        if not isinstance(self.version,str) or not 1<=len(self.version)<=128 or set(self.rates)!=expected:
            raise ValueError('invalid_rate_table')
        for rate in self.rates.values():
            if set(rate)!={'input','output','fixed'} or any(type(v) is not int or not 1<=v<=10**9 for v in rate.values()):
                raise ValueError('invalid_rate_table')
    def cost(self,op,tier,input_tokens,output_tokens):
        r=self.rates[f'{op}:{tier}']
        return r['fixed']+r['input']*input_tokens+r['output']*output_tokens

class Inference:
    def __init__(self,usage,rates,provider):
        self.usage,self.rates,self.provider=usage,rates,provider
    def execute(self,p,op,body):
        try: provider_target(op,body.tier)
        except ValueError: raise UsageError('invalid_request') from None
        if op=='vision':
            if not body.image or not body.mime or not any(x.origin=='note' and x.note_id for x in body.passages):
                raise UsageError('permission_required')
            try:
                image=base64.b64decode(body.image,validate=True)
                if not 1<=len(image)<=512000: raise ValueError()
                signatures={'image/png':b'\x89PNG\r\n\x1a\n','image/jpeg':b'\xff\xd8\xff','image/gif':b'GIF','image/webp':b'RIFF'}
                if not image.startswith(signatures[body.mime]): raise ValueError()
            except ValueError: raise UsageError('invalid_request') from None
        elif body.image is not None or body.mime is not None:
            raise UsageError('invalid_request')
        canonical=body.model_dump(mode='json',exclude={'lease_fence','request_id'})
        digest=hashlib.sha256(json.dumps({'operation':op,'body':canonical},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        texts=[redact(x.text) for x in body.passages]; system=redact(body.system)
        # UTF-8 bytes upper-bound text tokens; framing reserve is deliberately
        # conservative. The operator must supply the verified full vision context
        # bound; compressed bytes cannot establish an image token upper bound.
        inputs=sum(len(x.encode()) for x in texts)+len(system.encode())+1024
        if op=='vision': inputs+=self.rates.vision_input_tokens
        outputs=body.max_tokens if op in {'infer','vision'} else 0
        ceiling=self.rates.cost(op,body.tier,inputs,outputs)
        if op=='search': ceiling=self.rates.cost(op,body.tier,0,0)*(2 if body.depth=='advanced' else 1)
        reservation=self.usage.reserve(p,str(body.request_id),digest,ceiling,lease_fence=body.lease_fence,rate_version=self.rates.version)
        if reservation.response is not None: return reservation.response
        try:
            self.usage.check_admission(p,body.lease_fence)
            result,used_in,used_out=self.provider.execute(op,body,texts,system)
            if used_in>inputs or used_out>outputs: raise UsageError('outcome_uncertain')
            actual=ceiling if op=='search' else self.rates.cost(op,body.tier,used_in,used_out)
            self.usage.settle(p,reservation.id,actual,result)
            return result
        except Exception:
            self.usage.uncertain(p,reservation.id)
            raise UsageError('outcome_uncertain') from None
