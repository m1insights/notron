"""Copy/mark crash recovery uses actual filing + SQLite, with fake Apple storage."""
import pytest
from notron import filer, requests, operations, workspace, recovery, notes
from test_filer import store, FilerBrain
from test_action_recovery import Crash


@pytest.fixture
def filing_harness(store, monkeypatch):
    from types import SimpleNamespace
    dest = store.add('Supplements', '- original')
    source = store.add(workspace.DUMP, 'magnesium', folder=workspace.FOLDER)
    brain = FilerBrain({'magnesium': {'note': 'Supplements'}})
    h = SimpleNamespace(store=store, source=source, dest=dest, brain=brain, point=None, result=None)
    env = requests.create('file my brain dump', request_id='f1', source='cli', note_id=source,
                          source_revision=requests.revision(store.read_body(source)))
    def fail(point, oid):
        if point == h.point and (':copy:' in oid or ':source_receipt:' in oid):
            h.point = None
            raise Crash(point)
    monkeypatch.setattr(recovery, 'boundary', fail)
    def run():
        try:
            h.result = requests.run_job(env, lambda: filer.run(brain))
        except Crash:
            pass
    h.run = run
    return h


@pytest.mark.parametrize('point', ['before_applying', 'after_external_save', 'before_external_id', 'before_receipt', 'after_receipt'])
def test_filing_restart_repairs_only_missing_suboperation(filing_harness, point):
    h = filing_harness
    h.point = point
    h.run()
    operations.current()
    h.run()
    assert h.store.text('Supplements').count('magnesium') == 1
    source = h.store.text(workspace.DUMP)
    assert source.count('magnesium') == 1
    assert '✓ magnesium' in source
    assert h.brain.calls == 1
    assert h.result.status == 'completed'
    ids = [op.operation_id for op in operations.current().pending()]
    assert any(':copy:' in oid for oid in ids)
    assert any(':source_receipt:' in oid for oid in ids)


def test_changed_destination_requires_review_and_does_not_tick_source(filing_harness):
    h = filing_harness
    h.point = 'before_receipt'
    h.run()
    h.store.rows[h.dest]['body'] += '<div>user changed destination</div>'
    h.run()
    assert h.store.text('Supplements').count('magnesium') == 1
    assert '✓ magnesium' not in h.store.text(workspace.DUMP)
    assert h.result.status == 'needs_review'
    assert h.brain.calls == 1


@pytest.mark.parametrize('point', ['before_applying', 'after_external_save', 'before_external_id', 'before_receipt', 'after_receipt'])
def test_approved_creation_recovers_copy_and_both_marks(store, monkeypatch, point):
    source = store.add(workspace.DUMP, 'novel idea', folder=workspace.FOLDER)
    brain = FilerBrain({'novel idea': {'new': 'Novel'}})
    filer.run(brain)
    store.rows[source]['body'] += '<div>yes</div>'
    env = requests.create('file my brain dump', request_id='approval', source='cli', note_id=source,
                          source_revision=requests.revision(store.read_body(source)))
    hit = []
    def fail(name, oid):
        if name == point and not hit and (':create_copy:' in oid or ':source_receipt:' in oid):
            hit.append(oid)
            raise Crash(name)
    monkeypatch.setattr(recovery, 'boundary', fail)
    for _ in range(2):
        try:
            result = requests.run_job(env, lambda: filer.run(brain))
        except Crash:
            pass
    assert hit
    assert len([r for r in store.rows.values() if r['title'] == 'Novel']) == 1
    assert store.text('Novel').count('novel idea') == 1
    if point in {'after_external_save', 'before_external_id'}:
        # The returned ID was not durable. Creating again or inspecting denied
        # same-title candidates would be unsafe, so retain the source for review.
        assert 'novel idea' in store.text(workspace.DUMP)
        assert '✓ novel idea' not in store.text(workspace.DUMP)
        assert requests.current().get('approval').status == 'needs_review'
    else:
        assert '✓ novel idea' in store.text(workspace.DUMP)
        assert '✓ yes' in store.text(workspace.DUMP)
    assert brain.calls == 1


def test_inconclusive_copy_reconciliation_stays_paused_even_if_body_later_matches(filing_harness):
    h = filing_harness
    h.point = 'after_external_save'
    h.run()
    copied = h.store.rows[h.dest]['body']
    h.store.rows[h.dest]['body'] += '<div>remote change</div>'
    h.run()
    assert h.result.status == 'needs_review'
    h.store.rows[h.dest]['body'] = copied
    h.run()
    assert '✓ magnesium' not in h.store.text(workspace.DUMP)
    assert h.result.status == 'needs_review'


@pytest.mark.parametrize('point', ['after_external_save', 'before_receipt'])
def test_multiple_destinations_keep_exact_copy_ids_across_json_reopen(store, monkeypatch, point):
    store.add('Zebra', '- original')
    store.add('Alpha', '- original')
    source = store.add(workspace.DUMP, 'z thought\na thought', folder=workspace.FOLDER)
    brain = FilerBrain({'z thought': {'note': 'Zebra'}, 'a thought': {'note': 'Alpha'}},
                       shapes={'Zebra': 'list', 'Alpha': 'list'})
    env = requests.create('file my brain dump', request_id='multi', source='cli', note_id=source,
                          source_revision=requests.revision(store.read_body(source)))
    hit = []
    def fail(name, oid):
        if name == point and (':copy:' in oid or ':source_receipt:' in oid) and not hit:
            hit.append(oid)
            raise Crash(name)
    monkeypatch.setattr(recovery, 'boundary', fail)
    with pytest.raises(Crash):
        requests.run_job(env, lambda: filer.run(brain))
    result = None
    try:
        result = requests.run_job(env, lambda: filer.run(brain))
    except operations.OperationConflict:
        pass
    assert store.text('Zebra').count('z thought') == 1
    assert store.text('Alpha').count('a thought') == 1
    assert result is not None and result.status == 'completed'
    assert brain.calls == 1


@pytest.mark.parametrize('creation', [False, True])
@pytest.mark.parametrize('point', ['before_applying', 'before_receipt'])
def test_next_day_repair_reuses_exact_rendered_journal_write(store, monkeypatch, creation, point):
    from datetime import date
    if not creation:
        store.add('Supplements', '- original')
    source = store.add(workspace.DUMP, 'magnesium', folder=workspace.FOLDER)
    brain = FilerBrain({'magnesium': {'new' if creation else 'note': 'Supplements'}})
    if creation:
        filer.run(brain)
        store.rows[source]['body'] += '<div>yes</div>'
    env = requests.create('file my brain dump', request_id='midnight', source='cli', note_id=source,
                          source_revision=requests.revision(store.read_body(source)))
    monkeypatch.setattr(filer, '_today', lambda: date(2026, 9, 5))
    hit = []
    def fail(name, oid):
        if name == point and (':copy:' in oid or ':create_copy:' in oid or ':source_receipt:' in oid) and not hit:
            hit.append(oid)
            raise Crash(name)
    monkeypatch.setattr(recovery, 'boundary', fail)
    with pytest.raises(Crash):
        requests.run_job(env, lambda: filer.run(brain))
    copied = store.body('Supplements') if point == 'before_receipt' else None
    monkeypatch.setattr(filer, '_today', lambda: date(2026, 9, 6))
    requests.run_job(env, lambda: filer.run(brain))
    if copied is not None:
        assert store.body('Supplements') == copied
    assert 'Sat 5 Sep 2026' in store.text('Supplements')
    assert 'Sun 6 Sep 2026' not in store.text('Supplements')
    assert '✓ magnesium' in store.text(workspace.DUMP)
    if creation:
        assert '✓ yes' in store.text(workspace.DUMP)
    assert brain.calls == 1


@pytest.mark.parametrize('access', ['unselected', 'ignored'])
def test_lost_creation_id_never_reads_denied_same_title_notes(store, monkeypatch, access):
    from notron import policy, library
    source = store.add(workspace.DUMP, 'novel idea', folder=workspace.FOLDER)
    # Preconfigure denial of an ID that will be added remotely after the crash;
    # changing policy during recovery would instead purge all pending payloads.
    denied = 'denied-candidate'
    if access == 'ignored':
        lib = library.load()
        lib.ignore.add(denied)
        library.save(lib)
    brain = FilerBrain({'novel idea': {'new': 'Novel'}})
    filer.run(brain)
    store.rows[source]['body'] += '<div>yes</div>'
    env = requests.create('file my brain dump', request_id='approval', source='cli', note_id=source,
                          source_revision=requests.revision(store.read_body(source)))
    def fail(name, oid):
        if name == 'after_external_save' and ':create_copy:' in oid:
            raise Crash(name)
    monkeypatch.setattr(recovery, 'boundary', fail)
    with pytest.raises(Crash):
        requests.run_job(env, lambda: filer.run(brain))
    store.rows[denied] = {'title': 'Novel', 'folder': filer.FILING_FOLDER,
                         'body': '<div>Novel</div><div>private content</div>', 'modified': '1'}
    assert not policy.current().readable(notes.get_note(denied))
    reads = []
    original_read = notes.read_body
    def read(nid):
        reads.append(nid)
        return original_read(nid)
    monkeypatch.setattr(notes, 'read_body', read)
    result = requests.run_job(env, lambda: filer.run(brain))
    assert denied not in reads
    assert result.status == 'needs_review'
    assert len([r for r in store.rows.values() if r['title'] == 'Novel']) == 2
    assert 'novel idea' in store.text(workspace.DUMP)
    assert '✓ novel idea' not in store.text(workspace.DUMP)


def test_filing_tick_masks_failed_ask_receipt(store, monkeypatch):
    from notron import graph, conversation
    store.add('Supplements', '- original')
    store.add(workspace.DUMP, 'magnesium', folder=workspace.FOLDER)
    ask = store.add(workspace.ASK, 'file my brain dump', folder=workspace.FOLDER)
    class Brain(FilerBrain):
        def ask_json(self, **kw):
            if kw['purpose'] == 'route':
                return {'intent': 'file'}
            return super().ask_json(**kw)
    brain = Brain({'magnesium': {'note': 'Supplements'}})
    original_write = notes.write_body
    def fail_receipt(nid, body):
        if nid == ask:
            raise RuntimeError('simulated Notes receipt save failure')
        original_write(nid, body)
    monkeypatch.setattr(notes, 'write_body', fail_receipt)
    env = requests.create('file my brain dump', request_id='final-file', source='ask',
        note_id=ask, source_revision=requests.revision(store.read_body(ask)),
        source_text='file my brain dump', reply_to=(workspace.ASK, workspace.FOLDER, 1))
    state = graph.run_request(env, brain=brain)
    assert '✓ magnesium' in store.text(workspace.DUMP)
    assert any(r.startswith('✗') for r in state.results)
    assert requests.current().get('final-file').status == 'needs_review'
    assert conversation.unanswered(store.read_body(ask), ignore=(workspace.ASK,))
    assert not state.receipt_complete


@pytest.mark.parametrize('saved_before_error', [False, True])
@pytest.mark.parametrize('surface', ['ask', 'mention'])
def test_graph_watcher_restarts_failed_filing_reply_without_repeating_source_effects(store, monkeypatch, saved_before_error, surface):
    from notron import watch, conversation
    store.add('Supplements', '- original')
    store.add(workspace.DUMP, 'magnesium', folder=workspace.FOLDER)
    title, folder = (workspace.ASK, workspace.FOLDER) if surface == 'ask' else ('Scratch', 'Notes')
    raw = 'file my brain dump' if surface == 'ask' else '@notron file my brain dump'
    ask = store.add(title, raw, folder=folder)
    brain = FilerBrain({'magnesium': {'note': 'Supplements'}})
    env = requests.create('file my brain dump', request_id='filing-reply', source=surface,
        note_id=ask, source_revision=requests.revision(store.read_body(ask)),
        source_text=raw, reply_to=(title, folder, 1))
    original_write = notes.write_body
    failed = False
    def write(nid, body):
        nonlocal failed
        if nid == ask and not failed:
            failed = True
            if saved_before_error:
                original_write(nid, body)
            raise OSError('receipt save acknowledgement lost')
        original_write(nid, body)
    monkeypatch.setattr(notes, 'write_body', write)
    def answer(watcher):
        return watcher._answer('file my brain dump', title=title, folder=folder,
                               after=1, note_id=ask, envelope=env)
    first = watch.Watcher(brain, on_event=lambda message: None)
    assert not answer(first)
    assert requests.current().get(env.request_id).status == 'needs_review'
    operations.current()  # reopen SQLite and encrypted payloads
    restarted = watch.Watcher(brain, on_event=lambda message: None)
    assert answer(restarted) is saved_before_error
    assert requests.current().get(env.request_id).status == ('completed' if saved_before_error else 'needs_review')
    assert store.text('Supplements').count('magnesium') == 1
    assert store.text(workspace.DUMP).count('✓ magnesium') == 1
    assert sum(title == 'Supplements' for title, _ in store.writes) == 1
    assert sum(title == workspace.DUMP for title, _ in store.writes) == 1
    assert sum(written_title == title for written_title, _ in store.writes) == int(saved_before_error)
    assert bool(conversation.unanswered(store.read_body(ask), ignore=(title,))) is not saved_before_error
    if not saved_before_error:
        key = 'recover:' + env.request_id
        for _ in range(restarted.MAX_TRIES):
            assert restarted.recover_pending()
        assert restarted._failures[key][0] == restarted.MAX_TRIES
        assert not restarted._worth_trying(key)
        assert not restarted.recover_pending()
        assert sum(written_title in {'Supplements', workspace.DUMP} for written_title, _ in store.writes) == 2
    assert brain.calls == 1


def test_graph_watcher_accepts_source_only_filing_receipt_without_extra_reply(store):
    from notron import watch, conversation
    store.add('Supplements', '- original')
    raw = '@notron file this: magnesium'
    source = store.add('Scratch', raw)
    brain = FilerBrain({'magnesium': {'note': 'Supplements'}})
    env = requests.create('file this: magnesium', request_id='source-only', source='mention',
        note_id=source, source_revision=requests.revision(store.read_body(source)),
        source_text=raw, reply_to=('Scratch', 'Notes', 1))
    watcher = watch.Watcher(brain, on_event=lambda message: None)
    assert watcher._answer('file this: magnesium', title='Scratch', folder='Notes', after=1,
                           note_id=source, envelope=env)
    assert requests.current().get(env.request_id).status == 'completed'
    assert '✓ @notron file this: magnesium' in store.text('Scratch')
    assert 'Notron:' not in store.text('Scratch')
    assert sum(title == 'Scratch' for title, _ in store.writes) == 1
    assert brain.calls == 1


def test_partial_graph_filing_stays_incomplete_despite_successful_source_tick(store, monkeypatch):
    from notron import graph
    store.add('Supplements', '- original')
    other = store.add('Other', '- original')
    store.add(workspace.DUMP, 'magnesium\nzinc', folder=workspace.FOLDER)
    ask = store.add(workspace.ASK, 'file my brain dump', folder=workspace.FOLDER)
    brain = FilerBrain({'magnesium': {'note': 'Supplements'}, 'zinc': {'note': 'Other'}})
    original_write = notes.write_body
    def write(nid, body):
        if nid == other:
            raise OSError('one filing destination unavailable')
        original_write(nid, body)
    monkeypatch.setattr(notes, 'write_body', write)
    env = requests.create('file my brain dump', request_id='partial-file', source='ask',
        note_id=ask, source_revision=requests.revision(store.read_body(ask)),
        source_text='file my brain dump', reply_to=(workspace.ASK, workspace.FOLDER, 1))
    state = graph.run_request(env, brain=brain)
    assert '✓ magnesium' in store.text(workspace.DUMP)
    assert '✓ zinc' not in store.text(workspace.DUMP)
    assert any(result.startswith('✗') for result in state.results)
    assert not state.receipt_complete
    assert requests.current().get(env.request_id).status == 'needs_review'
