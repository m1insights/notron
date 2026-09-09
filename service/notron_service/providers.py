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


def _http(url,payload,headers,timeout):
    """No proxy, redirects, logging or automatic retry; bounded decoded response."""
    import json
    import time
    from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    deadline=time.monotonic()+timeout
    request=Request(url,json.dumps(payload,allow_nan=False).encode(),headers|{'Content-Type':'application/json'},method='POST')
    with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=timeout) as response:
        data=bytearray()
        while True:
            if time.monotonic()>=deadline: raise TimeoutError()
            chunk=response.read(65536)
            if not chunk: break
            data.extend(chunk)
            if len(data)>4*1024*1024: raise ValueError('response_too_large')
        return json.loads(data)


class ProviderAdapter:
    """Deployable fixed Nebius/Tavily adapters, injectable at the HTTP boundary."""
    def __init__(self,nebius_key,tavily_key,*,http=_http,timeout=25):
        from concurrent.futures import ThreadPoolExecutor
        from threading import BoundedSemaphore
        self.nebius_key,self.tavily_key=nebius_key,tavily_key
        self.http,self.timeout=http,timeout
        self.pool=ThreadPoolExecutor(max_workers=8,thread_name_prefix='provider')
        self.slots=BoundedSemaphore(8)

    def execute(self,op,body,texts,system):
        import math
        target=provider_target(op,body.tier)
        headers={'Authorization':'Bearer '+self.nebius_key}
        if op=='search':
            url=target.base_url+'/search'; headers={}
            payload={'api_key':self.tavily_key,'query':'\n'.join(texts),'max_results':body.limit,'search_depth':body.depth,'include_answer':True}
        elif op=='embed':
            url=target.base_url+'embeddings'; payload={'model':target.model,'input':texts}
        else:
            url=target.base_url+'chat/completions'
            content='\n'.join(texts)
            if op=='vision': content=[{'type':'text','text':content},{'type':'image_url','image_url':{'url':f'data:{body.mime};base64,{body.image}'}}]
            payload={'model':target.model,'messages':[{'role':'system','content':system},{'role':'user','content':content}],
                     'max_tokens':body.max_tokens,'temperature':body.temperature}
            if body.json_mode: payload['response_format']={'type':'json_object'}
        if not self.slots.acquire(blocking=False): raise RuntimeError('provider_unavailable')
        def perform():
            try: return self.http(url,payload,headers,self.timeout)
            finally: self.slots.release()
        future=self.pool.submit(perform)
        data=future.result(timeout=self.timeout)
        if op=='search':
            from .search import validate_search
            return validate_search(data,body.limit),0,0
        usage=data['usage']
        if not isinstance(usage,dict): raise ValueError('invalid_usage')
        used_in=usage.get('prompt_tokens',usage.get('total_tokens')) if op=='embed' else usage.get('prompt_tokens')
        used_out=usage.get('completion_tokens',0) if op=='embed' else usage['completion_tokens']
        if any(type(x) is not int or x<0 for x in (used_in,used_out)): raise ValueError('invalid_usage')
        total=usage.get('total_tokens')
        if 'total_tokens' in usage and (type(total) is not int or total!=used_in+used_out): raise ValueError('invalid_usage')
        if op=='embed' and used_out!=0: raise ValueError('invalid_usage')
        # completion_tokens includes reasoning; a separate count must not exceed it.
        details=usage.get('completion_tokens_details')
        if details is None: details={}
        if not isinstance(details,dict): raise ValueError('invalid_usage')
        reasoning=details.get('reasoning_tokens',0)
        if type(reasoning) is not int or not 0<=reasoning<=used_out: raise ValueError('invalid_usage')
        if op=='embed':
            rows=sorted(data['data'],key=lambda r:r['index'])
            if [r['index'] for r in rows]!=list(range(len(texts))): raise ValueError('invalid_embeddings')
            vectors=[r['embedding'] for r in rows]
            if any(not isinstance(v,list) or not 1<=len(v)<=8192 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in vectors): raise ValueError('invalid_embeddings')
            if len({len(v) for v in vectors})!=1: raise ValueError('invalid_embeddings')
            return {'embeddings':vectors},used_in,used_out
        msg=data['choices'][0]['message']
        if not isinstance(msg,dict): raise ValueError('invalid_response')
        from .search import optional_text
        content=optional_text(msg,'content',262144)
        reasoning=optional_text(msg,'reasoning',262144)
        alternate_reasoning=optional_text(msg,'reasoning_content',262144)
        reasoning=reasoning or alternate_reasoning
        return {'content':content,'reasoning':bool(reasoning)},used_in,used_out
