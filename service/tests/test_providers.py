from uuid import uuid4

import pytest
from pydantic import ValidationError

from notron_service.principals import Principal
from notron_service.providers import PreparedPassage, provider_target


def test_client_cannot_choose_unknown_model_or_destination():
    with pytest.raises(ValueError):
        provider_target('infer', 'https://evil.test')
    with pytest.raises(ValueError):
        provider_target('shell', 'fast')
    with pytest.raises(TypeError):
        provider_target('infer', 'fast', url='https://evil.test')


def test_server_selects_existing_nebius_models_including_vision():
    assert provider_target('infer', 'fast').model == 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'
    assert provider_target('vision').model == 'openbmb/MiniCPM-V-4_5'
    assert provider_target('embed').model == 'Qwen/Qwen3-Embedding-8B'
    assert provider_target('search').base_url == 'https://api.tavily.com'


def test_prepared_note_inputs_require_source_and_reject_extra_transport_fields():
    with pytest.raises(ValidationError):
        PreparedPassage(text='note', origin='history')
    with pytest.raises(ValidationError):
        PreparedPassage(text='note', origin='user_request', provider_url='https://evil.test')
    assert PreparedPassage(text='note', origin='history', note_id='opaque-note').note_id == 'opaque-note'


def test_principal_validates_ids_and_never_promotes_prototype():
    p = Principal(account_id=uuid4(), device_id=uuid4(), scopes=frozenset({'account:read'}), kind='prototype')
    assert p.kind == 'prototype'
    with pytest.raises(ValueError):
        Principal(account_id='arbitrary-account', device_id=uuid4(), scopes=frozenset(), kind='managed')
    with pytest.raises(ValueError):
        Principal(account_id=uuid4(), device_id=uuid4(), scopes=frozenset(), kind='admin')
