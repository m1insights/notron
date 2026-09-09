from types import SimpleNamespace
import pytest
from notron.outbound import Passage

class Session:
    def __init__(self): self.refresh=[]
    def access_token(self,force_refresh=False): self.refresh.append(force_refresh); return 'short-access'

@pytest.fixture
def managed(monkeypatch,tmp_path):
    from notron.transport import ManagedTransport
    calls=[]; session=Session()
    def http(operation,body,token,deadline):
        calls.append((operation,body,token))
        return 200,{'content':'answer','reasoning':False}
    t=ManagedTransport('https://service.example.test',session,lease_fence=lambda:1,http=http)
    return t,session,calls

def test_managed_brain_without_provider_key_preserves_preparation(managed,monkeypatch):
    from notron.brain import Brain
    from notron import credentials
    t,s,calls=managed
    original=credentials.require
    def get(name):
        assert name not in (credentials.NEBIUS_KEY,credentials.SEARCH_KEY)
        return original(name)
    monkeypatch.setattr(credentials,'require',get)
    b=Brain(api_key='',transport=t)
    assert b.ask(system='system',user=[Passage('password: hunter2','user_request')],purpose='write')=='answer'
    assert 'hunter2' not in str(calls)

def test_retry_refresh_once_keeps_id(managed):
    from notron.transport import ManagedError
    t,s,calls=managed
    def http(op,body,token,deadline):
        calls.append(body)
        return (401,{'code':'signin_required'}) if len(calls)==1 else (200,{'content':'answer','reasoning':False})
    t.http=http
    assert t.infer('fast','system',[Passage('hi','user_request')],4,False,.2,'write',9999999999).content=='answer'
    assert s.refresh==[False,True]
    assert calls[0]['request_id']==calls[1]['request_id']

def test_managed_errors_propagate_and_replay_stable_id(managed):
    from notron.transport import ManagedError
    t,s,calls=managed
    def http(op,body,token,deadline): calls.append(body); return 429,{'code':'allowance_exhausted'}
    t.http=http
    for _ in range(2):
        with pytest.raises(ManagedError,match='allowance_exhausted'): t.infer('fast','system',[Passage('hi','user_request')],4,False,.2,'write',9999999999)
    assert calls[0]['request_id']==calls[1]['request_id']

def test_managed_research_needs_no_search_key(managed,monkeypatch):
    from notron import research,transport
    t,s,calls=managed
    monkeypatch.setattr(transport,'_managed',t)
    t.http=lambda *args:(200,{'answer':'summary','results':[{'title':'title','url':'https://example.test','content':'text'}]})
    assert research.available()
    answer,findings=research.search([Passage('hi','user_request')])
    assert answer=='summary' and findings[0].snippet=='text'

def test_native_access_channel_roundtrip_and_rejects_refresh():
    from notron.transport import NativeTokenChannel,ManagedError
    import socket,json,threading
    a,b=socket.socketpair(); seen=[]
    def serve():
        with b:
            raw=bytearray()
            while not raw.endswith(b'\n'): raw.extend(b.recv(1))
            seen.append(json.loads(raw))
            b.sendall(b'{"access_token":"synthetic-access"}\n')
    worker=threading.Thread(target=serve);worker.start()
    channel=NativeTokenChannel(a)
    assert channel.access_token(True)=='synthetic-access'
    worker.join();channel.stop()
    assert seen==[{'operation':'access_token','force_refresh':True}]
    with pytest.raises(ManagedError):channel.access_token()

def test_managed_codes_preserved_by_worker():
    from notron.transport import ManagedError
    from notron.worker import failure
    from notron.health import HealthStore
    failure(ManagedError('allowance_exhausted'))
    assert HealthStore().row()['reason_code']=='allowance_exhausted'

def test_managed_vision_rechecks_live_source_without_provider_key(managed,monkeypatch):
    from notron.brain import Brain
    from notron import notes,policy
    t,s,calls=managed
    source=Passage('picture','note','n1','title','one')
    monkeypatch.setattr(policy,'require_ready',lambda:SimpleNamespace(readable=lambda n:True,system_role=lambda _:None))
    monkeypatch.setattr(notes,'get_note',lambda _:SimpleNamespace(id='n1',modified='one'))
    b=Brain(api_key='',transport=t)
    assert b.see(image=b'\x89PNG\r\n\x1a\nfixture',mime='image/png',question='describe',source=source)=='answer'
    assert calls[0][0]=='vision'
    monkeypatch.setattr(notes,'get_note',lambda _:SimpleNamespace(id='n1',modified='changed'))
    with pytest.raises(policy.PolicyError): b.see(image=b'fake',mime='image/png',question='describe',source=source)
    assert len(calls)==1

def test_parent_request_identity_survives_transport_reconstruction(managed):
    from notron.requests import create,execution
    from notron.transport import ManagedTransport
    t,s,calls=managed
    envelope=create('question')
    with execution(envelope):
        first=t._identity('infer',{'passages':['same']})
    other=ManagedTransport('https://service.example.test',s,lease_fence=lambda:1,http=t.http)
    with execution(envelope): assert other._identity('infer',{'passages':['same']})==first

def test_ambient_descriptor_does_not_bypass_protected_bootstrap(monkeypatch):
    from notron.transport import bootstrap_inherited,ManagedError
    monkeypatch.setenv('NOTRON_MANAGED_SESSION_FD','0')
    with pytest.raises(ManagedError,match='signin_required'):bootstrap_inherited()

def test_unscoped_request_identity_is_atomic_between_workers(managed,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from notron import securestore
    import time
    t,s,calls=managed;read=securestore.read_json
    def slow_read(path):
        value=read(path);time.sleep(.02);return value
    monkeypatch.setattr(securestore,'read_json',slow_read)
    with ThreadPoolExecutor(4) as pool: ids=list(pool.map(lambda _:t._identity('infer',{'same':'payload'}),range(4)))
    assert len(set(ids))==1

def test_direct_embedding_validation_matches_managed_boundary():
    from notron.transport import DirectTransport,ManagedError
    response=SimpleNamespace(data=[SimpleNamespace(index=0,embedding=[float('nan')])],usage=None)
    client=SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kw:response))
    direct=DirectTransport(SimpleNamespace(_client=client,_record=lambda *args:None))
    with pytest.raises(ManagedError):direct.embed([Passage('hi','user_request')],999999999)

def test_direct_search_rejects_malformed_answer(monkeypatch):
    from notron import research
    from notron.transport import ManagedError
    monkeypatch.setattr(research,'_search',lambda *args:{'answer':[],'results':[]})
    with pytest.raises(ManagedError):research.search([Passage('hi','user_request')])
