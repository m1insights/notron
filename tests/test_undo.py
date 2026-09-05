"""Encrypted snapshot lifecycle, including legacy recovery-only bodies."""
from notron import undo
from notron.requests import revision


def test_peek_never_consumes_and_stale_consume_cannot_delete_new_snapshot():
    undo.save('n1', '<div>old</div>')
    first = undo.peek('n1')
    assert first.before_html == '<div>old</div>'
    assert undo.peek('n1') == first
    undo.save('n1', '<div>newer</div>')
    undo.consume('n1', first.snapshot_id)
    assert undo.peek('n1').before_html == '<div>newer</div>'
    undo.consume('n1', undo.peek('n1').snapshot_id)
    assert undo.peek('n1') is None


def test_legacy_body_has_no_authority_to_restore():
    undo._write({'n1': '<div>legacy</div>'})
    snapshot = undo.peek('n1')
    assert snapshot.before_html == '<div>legacy</div>'
    assert snapshot.after_revision is None
    assert undo.peek('n1').snapshot_id == snapshot.snapshot_id


def test_staged_backup_preserves_previous_slot_until_verified():
    undo.save('n1', 'earlier')
    previous = undo.peek('n1')
    undo.save('n1', 'before', revision('after'), 'op1')
    assert undo.peek('n1').before_html == 'before'
    assert undo.peek('n1').after_revision is None
    undo.discard('n1', 'op1')
    assert undo.peek('n1') == previous


def test_verified_backup_is_revision_bound_and_empty_preimage_is_saved():
    undo.save('n1', '', revision('after'), 'op1')
    undo.promote('n1', 'op1')
    snapshot = undo.peek('n1')
    assert snapshot.before_html == ''
    assert snapshot.after_revision == revision('after')
    assert snapshot.operation_id == 'op1'


def test_ignore_purges_both_current_and_staged_preimages():
    from notron import library
    undo.save('n1', 'older original')
    undo.save('n1', 'pending original', revision('after'), 'pending-op')
    lib = library.load()
    lib.ignore.add('n1')
    library.save(lib)
    assert undo.peek('n1') is None
    assert 'n1' not in undo._load()
