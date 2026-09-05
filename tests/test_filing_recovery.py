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
