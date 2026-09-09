"""Managed and user-key transports. Managed credentials never come from env/files."""
from dataclasses import asdict
import hashlib
import json
import math
import socket
import ssl
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5
from urllib.parse import urlsplit
from .outbound import prepare_outbound
from .policy import PolicyError
from . import network

CODES={'signin_required','subscription_required','allowance_exhausted','provider_unavailable','permission_required','outcome_uncertain','request_conflict'}
_NAMESPACE=UUID('a1ec9d32-06c9-4ec0-a86e-8af5fecc43ee')
_managed=None

class ManagedError(PolicyError):
    def __init__(self,code):
        self.code=code if code in CODES else 'provider_unavailable'
        super().__init__(self.code)

def configured(): return _managed

def configure(transport):
    """Called by protected native startup; no environment-driven mode switch."""
    global _managed
    _managed=transport

class NativeTokenChannel:
    """Dedicated inherited socket; bounded access-only protocol, no refresh token."""
    def __init__(self,channel):
        if not isinstance(channel,socket.socket): raise ManagedError('signin_required')
        self.channel=channel; self.lock=threading.Lock(); self.stopped=False
    def stop(self):
        self.stopped=True
        try: self.channel.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.channel.close()
    def access_token(self,force_refresh=False):
        with self.lock:
            if self.stopped: raise ManagedError('signin_required')
            try:
                self.channel.settimeout(10)
                self.channel.sendall(json.dumps({'operation':'access_token','force_refresh':bool(force_refresh)}).encode()+b'\n')
                raw=bytearray()
                while not raw.endswith(b'\n'):
                    chunk=self.channel.recv(1)
                    if not chunk or len(raw)>=32768: raise ValueError()
                    raw.extend(chunk)
                response=json.loads(raw)
                token=response['access_token']
                if set(response)!={'access_token'} or not isinstance(token,str) or not 1<=len(token)<=16384 or any(ord(x)<33 for x in token): raise ValueError()
                return token
            except Exception:
                self.stop(); raise ManagedError('signin_required') from None

class ManagedTransport:
    def __init__(self,service_url,session,*,lease_fence,http=None):
        parts=network._https_parts(service_url)
        if parts is None or parts.path not in ('','/') or parts.query or parts.fragment or parts.port not in (None,443):
            raise ManagedError('permission_required')
        self.service_url=service_url.rstrip('/'); self.session=session; self.lease_fence=lease_fence
        self.http=http or self._http; self.stopped=False
    def stop(self):
        lease=getattr(self,'worker_lease',None)
        if lease:lease.stop()
        self.stopped=True
        if hasattr(self.session,'stop'): self.session.stop()
    def _http(self,operation,body,token,deadline):
        if operation not in {'infer','embed','vision','search',*(f'worker/lease/{x}' for x in ('acquire','renew','release','check'))}: raise ManagedError('permission_required')
        host=urlsplit(self.service_url).hostname
        connection=network._ProviderConnection(host,443,timeout=max(.001,deadline-time.monotonic()),context=ssl.create_default_context())
        connection.write_timeout=max(.001,deadline-time.monotonic())
        try:
            with network.deadline(deadline):
                connection.request('POST','/v1/'+operation,json.dumps(body,allow_nan=False).encode(),
                    {'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'})
                connection.sock.settimeout(network.remaining())
                response_socket=connection.sock
                response=connection.getresponse(); data=bytearray()
                if 300<=response.status<400: raise ManagedError('permission_required')
                while True:
                    response_socket.settimeout(network.remaining())
                    chunk=response.read(65536)
                    if not chunk: break
                    data.extend(chunk)
                    if len(data)>4*1024*1024: raise ManagedError('outcome_uncertain')
                return response.status,json.loads(data)
        finally: connection.close()
    def control(self,operation,body):
        if operation not in {'acquire','renew','release','check'}:raise ManagedError('permission_required')
        deadline=time.monotonic()+10
        for attempt in range(2):
            if self.stopped:raise ManagedError('signin_required')
            try:
                token=self.session.access_token(force_refresh=bool(attempt))
                if self.stopped:raise ManagedError('signin_required')
                status,data=self.http('worker/lease/'+operation,body,token,deadline)
            except ManagedError:raise
            except Exception:raise ManagedError('provider_unavailable') from None
            if self.stopped:raise ManagedError('signin_required')
            if status==401 and attempt==0:continue
            if status!=200 or not isinstance(data,dict):
                raise ManagedError('signin_required' if status==401 else 'permission_required')
            return data
        raise ManagedError('signin_required')

    def _identity(self,operation,payload):
        from .requests import active_request
        from . import securestore
        from .paths import DATA_DIR
        canonical=json.dumps({'op':operation,'body':payload},sort_keys=True,separators=(',',':'),allow_nan=False)
        digest=hashlib.sha256(canonical.encode()).hexdigest()
        parent=active_request()
        if parent is not None: return str(uuid5(_NAMESPACE,parent.request_id+':'+digest))
        # Outside P02, persist one opaque ID per payload. Never expire an uncertain
        # ID into a second purchase. A bounded full ledger pauses for local review.
        path=DATA_DIR/'managed-requests.json'
        import fcntl,os
        securestore.private_directory(path.parent)
        fd=os.open(path.with_suffix('.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            records=securestore.read_json(path)
            if digest not in records:
                if len(records)>=10000: raise ManagedError('permission_required')
                records[digest]=str(uuid4());securestore.write_json(path,records)
            value=records[digest]
            try: UUID(value)
            except (ValueError,TypeError,AttributeError): raise ManagedError('outcome_uncertain') from None
            return value
        finally:
            fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

    def request(self,operation,payload,deadline,*,before_send):
        if self.stopped: raise ManagedError('signin_required')
        body=payload|{'request_id':self._identity(operation,payload),'lease_fence':self.lease_fence()}
        if type(body['lease_fence']) is not int or body['lease_fence']<=0: raise ManagedError('permission_required')
        for attempt in range(2):
            if self.stopped: raise ManagedError('signin_required')
            try:
                token=self.session.access_token(force_refresh=bool(attempt))
            except ManagedError: raise
            except Exception: raise ManagedError('signin_required') from None
            if self.stopped: raise ManagedError('signin_required')
            # Native refresh can yield while Notes or policy changes. Validate
            # the original source immediately before every network transmission.
            before_send()
            try:
                status,data=self.http(operation,body,token,deadline)
            except ManagedError: raise
            except Exception: raise ManagedError('outcome_uncertain') from None
            if self.stopped: raise ManagedError('signin_required')
            if status==401 and attempt==0: continue
            if status!=200:
                code=data.get('code') if isinstance(data,dict) else None
                if status==401: code='signin_required'
                if status==403 and code not in CODES: code='permission_required'
                raise ManagedError(code)
            if not isinstance(data,dict): raise ManagedError('outcome_uncertain')
            return data
        raise ManagedError('signin_required')
    def _passages(self,purpose,passages):
        from .privacy import redact
        return [asdict(p)|{'text':text,'title':redact(p.title)} for p,text in zip(passages,prepare_outbound(purpose,passages))]
    def _prepared_boundary(self,purpose,passages):
        sources=tuple(passages)
        prepared=self._passages(purpose,sources)
        def validate():
            from . import notes,policy,retention
            retention.require_ready()
            for source in sources:
                if source.note_id:
                    live=notes.get_note(source.note_id)
                    if (live is None or live.id!=source.note_id or not policy.require_ready().readable(live)
                            or live.modified!=source.modified):
                        raise ManagedError('permission_required')
            if self._passages(purpose,sources)!=prepared:
                raise ManagedError('permission_required')
        return prepared,validate
    def infer(self,tier,system,passages,budget,json_mode,temperature,purpose,deadline):
        from .privacy import redact
        prepared,validate=self._prepared_boundary(purpose,passages)
        data=self.request('infer',{'tier':tier,'system':redact(system),'passages':prepared,
             'max_tokens':budget,'json_mode':json_mode,'temperature':temperature},deadline,before_send=validate)
        return self._message(data)
    @staticmethod
    def _message(data):
        if not isinstance(data.get('content'),str) or type(data.get('reasoning')) is not bool or len(data['content'])>262144:
            raise ManagedError('outcome_uncertain')
        return SimpleNamespace(content=data['content'],reasoning=data['reasoning'])
    def vision(self,question,data_uri,budget,temperature,source,deadline):
        from .outbound import Passage
        mime,encoded=data_uri.split(';base64,',1)
        if len(encoded)>682668: raise ManagedError('permission_required')
        prepared,validate=self._prepared_boundary('write',[source,Passage(question,'user_request')])
        return self._message(self.request('vision',{'passages':prepared,
            'image':encoded,'mime':mime.removeprefix('data:'),'max_tokens':budget,'temperature':temperature},deadline,before_send=validate))
    def embed(self,passages,deadline):
        prepared,validate=self._prepared_boundary('embed',passages)
        data=self.request('embed',{'passages':prepared},deadline,before_send=validate)
        return _validated_embeddings(data.get('embeddings'),len(passages))
    def search(self,passages,limit,depth,deadline):
        prepared,validate=self._prepared_boundary('search',passages)
        data=self.request('search',{'passages':prepared,'limit':limit,'depth':depth},deadline,before_send=validate)
        return _validated_search(data,limit)

def _validated_search(data,limit):
    if isinstance(data,dict) and data.get('answer') is None: data=data|{'answer':''}
    if not isinstance(data,dict) or not isinstance(data.get('answer'),str) or not isinstance(data.get('results'),list) or len(data['results'])>limit:
        raise ManagedError('outcome_uncertain')
    for r in data['results']:
        if not isinstance(r,dict) or any(not isinstance(r.get(k),str) for k in ('title','url','content')): raise ManagedError('outcome_uncertain')
    return data

def _validated_embeddings(vectors,count):
    if not isinstance(vectors,list) or len(vectors)!=count or any(not isinstance(v,list) or not 1<=len(v)<=8192 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in vectors):
        raise ManagedError('outcome_uncertain')
    if len({len(v) for v in vectors})>1: raise ManagedError('outcome_uncertain')
    return vectors

def _direct_message(message):
    content=getattr(message,'content',None)
    reasoning=getattr(message,'reasoning',None)
    if content is not None and not isinstance(content,str): raise ManagedError('outcome_uncertain')
    if reasoning is not None and not isinstance(reasoning,str): raise ManagedError('outcome_uncertain')
    return ManagedTransport._message({'content':content or '', 'reasoning':bool(reasoning)})

class DirectTransport:
    """Existing constrained user-Keychain adapter behind the same Brain methods."""
    def __init__(self,brain): self.brain=brain
    def search(self,passages,limit,depth,deadline):
        from .research import _search
        return _validated_search(_search('\n'.join(prepare_outbound('search',passages)),limit,depth,deadline),limit)
    def infer(self,tier,system,passages,budget,json_mode,temperature,purpose,deadline):
        b=self.brain
        kwargs={'response_format':{'type':'json_object'}} if json_mode else {}
        response=b._client.chat.completions.create(model=b.model_for(tier),messages=[{'role':'system','content':system},
            {'role':'user','content':'\n'.join(prepare_outbound(purpose,passages))}],max_tokens=budget,
            temperature=temperature,timeout=max(.001,deadline-time.monotonic()),**kwargs)
        b._record(tier,getattr(response,'usage',None));return _direct_message(response.choices[0].message)
    def vision(self,question,data_uri,budget,temperature,source,deadline,model=None):
        b=self.brain
        response=b._client.chat.completions.create(model=model,messages=[{'role':'user','content':[
            {'type':'text','text':question},{'type':'image_url','image_url':{'url':data_uri}}]}],max_tokens=budget,
            temperature=temperature,timeout=max(.001,deadline-time.monotonic()))
        b._record('vision',getattr(response,'usage',None));return _direct_message(response.choices[0].message)
    def embed(self,passages,deadline):
        from .brain import EMBED_MODEL
        import os
        b=self.brain
        response=b._client.embeddings.create(model=os.environ.get('NOTRON_MODEL_EMBED',EMBED_MODEL),
            input=prepare_outbound('embed',passages),timeout=max(.001,deadline-time.monotonic()))
        b._record('embed',getattr(response,'usage',None))
        rows=sorted(response.data,key=lambda d:d.index)
        if [d.index for d in rows]!=list(range(len(passages))): raise ManagedError('outcome_uncertain')
        return _validated_embeddings([d.embedding for d in rows],len(passages))

_lease_fence_supplier=lambda:0

def configure_lease_fence(supplier):
    """Task 5 supplies current server-issued fence; no lease means no admission."""
    global _lease_fence_supplier
    _lease_fence_supplier=supplier

def bootstrap_inherited():
    """Consume native channel only after the protected Keychain startup succeeds.

    FD metadata carries no token or URL and cannot make startup succeed. P06
    must replace credentials.startup with signed-bundle verification first.
    """
    import os
    from . import credentials
    marker=os.environ.get('NOTRON_MANAGED_SESSION_FD')
    if marker is None or configured() is not None: return
    if marker!='0' or not isinstance(credentials._provider,credentials.KeychainStore):
        raise ManagedError('signin_required')
    channel=socket.socket(fileno=os.dup(0))
    if channel.family!=socket.AF_UNIX or channel.type!=socket.SOCK_STREAM:
        channel.close();raise ManagedError('signin_required')
    channel.settimeout(10)
    try:
        channel.sendall(b'{"operation":"configuration"}\n')
        raw=bytearray()
        while not raw.endswith(b'\n'):
            chunk=channel.recv(1)
            if not chunk or len(raw)>=4096: raise ValueError()
            raw.extend(chunk)
        info=json.loads(raw)
        if set(info)!={'service_url'}: raise ValueError()
        transport=ManagedTransport(info['service_url'],NativeTokenChannel(channel),lease_fence=lambda:_lease_fence_supplier())
        from .managed_lease import ManagedLease
        transport.worker_lease=ManagedLease(transport)
        configure_lease_fence(transport.worker_lease.fence)
        configure(transport)
        transport.worker_lease.start()
    except ManagedError:
        channel.close();raise
    except Exception:
        channel.close();raise ManagedError('signin_required') from None
