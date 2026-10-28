"""File/index/retrieval acceptance checks for disposable test deployments only."""
import time
from uuid import uuid4


def wait_document(actor, path, identifier):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        documents = actor.request('GET', path)
        document = next((item for item in documents if item['id'] == identifier), None)
        if document and document['status'] == 'ready':
            return document
        if document and document['status'] == 'failed':
            raise AssertionError('Document processing failed: ' + (document.get('error') or 'unknown'))
        time.sleep(0.3)
    raise AssertionError('Document did not become ready')


def answer(actor, agent, attachment=None, conversation=None):
    conversation = conversation or actor.request('POST', '/conversations', json={'agent_id': agent['id']})['id']
    run = actor.request('POST', '/conversations/' + conversation + '/runs', json={
        'message': 'CONTEXT_CHECK', 'attachment_ids': [attachment] if attachment else [],
    })
    actor.wait(run['id'])
    message = actor.request('GET', '/conversations/' + conversation + '/messages')[-1]
    return conversation, message


def run_knowledge_checks(admin, actors, agent, mock):
    owner, other = actors[:2]
    model = admin.request('POST', '/admin/models', json={
        'name': 'Embedding simulator', 'kind': 'embedding', 'model': 'test-embedding', 'base_url': mock + '/v1',
    })
    probe = admin.request('POST', '/admin/models/' + model['id'] + '/test')
    assert probe['ok'] and probe['capabilities']['embedding_dimensions'] == 4
    kb = admin.request('POST', '/admin/knowledge-bases', json={
        'name': 'Acceptance references', 'description': 'Synthetic fixtures only', 'embedding_model_id': model['id'],
    })
    docs_path = '/admin/knowledge-bases/' + kb['id'] + '/documents'
    kb_marker = 'kb-marker-' + uuid4().hex
    doc = admin.request('POST', docs_path, files={'file': ('reference.md', '# Reference\n' + kb_marker, 'text/markdown')})
    wait_document(admin, docs_path, doc['id'])
    results = admin.request('POST', '/admin/knowledge-bases/' + kb['id'] + '/search', json={'query': 'reference'})
    results = results if isinstance(results, list) else results['results']
    assert any(kb_marker in item['excerpt'] for item in results)
    admin.request('PATCH', '/admin/agents/' + agent['id'], json={
        'draft': {**agent['draft'], 'knowledge_base_ids': [kb['id']]},
    })
    admin.request('POST', '/admin/agents/' + agent['id'] + '/publish')

    conversation = owner.request('POST', '/conversations', json={'agent_id': agent['id']})['id']
    attachment_marker = 'attachment-marker-' + uuid4().hex
    attachment = owner.request('POST', '/files', data={'conversation_id': conversation}, files={
        'file': ('private.txt', attachment_marker, 'text/plain'),
    })
    wait_document(owner, '/files?conversation_id=' + conversation, attachment['id'])
    download = owner.client.get('/files/' + attachment['id'] + '/download')
    download.raise_for_status()
    assert download.text == attachment_marker
    assert other.client.get('/files/' + attachment['id']).status_code in (403, 404)
    assert other.client.get('/files/' + attachment['id'] + '/download').status_code in (403, 404)
    _conversation, message = answer(owner, agent, attachment['id'], conversation)
    assert kb_marker in message['content'] and attachment_marker in message['content']
    assert len(message['citations']) >= 2
    reference = next(item for item in message['citations'] if kb_marker in item['excerpt'])
    citation_path = '/citations/' + reference['id']
    assert kb_marker in owner.request('GET', citation_path)['excerpt']
    assert other.client.get(citation_path).status_code in (403, 404)

    # A different member gets the shared reference, never the owner's attachment.
    other_conversation, other_message = answer(other, agent)
    assert kb_marker in other_message['content'] and attachment_marker not in other_message['content']
    assert other.client.post('/conversations/' + other_conversation + '/runs', json={
        'message': 'CONTEXT_CHECK', 'attachment_ids': [attachment['id']],
    }).status_code in (400, 403, 404, 409, 422)

    # Replacement stops the old revision being retrieved and old citations being read.
    replacement_marker = 'kb-marker-' + uuid4().hex
    replacement = admin.request('POST', docs_path, files={
        'file': ('reference.md', '# Updated reference\n' + replacement_marker, 'text/markdown'),
    })
    wait_document(admin, docs_path, replacement['id'])
    restored_conversation, replaced_message = answer(owner, agent)
    assert replacement_marker in replaced_message['content'] and kb_marker not in replaced_message['content']
    assert owner.client.get(citation_path).status_code in (403, 404, 410)
    restored_citation = next(item['id'] for item in replaced_message['citations'] if replacement_marker in item['excerpt'])

    # Delete a separate reference and prove it is excluded from a fresh retrieval.
    deleted_marker = 'kb-marker-' + uuid4().hex
    removed = admin.request('POST', docs_path, files={'file': ('removed.txt', deleted_marker, 'text/plain')})
    wait_document(admin, docs_path, removed['id'])
    admin.request('DELETE', '/admin/documents/' + removed['id'])
    _conversation, after_delete = answer(owner, agent)
    assert deleted_marker not in after_delete['content']
    assert replacement_marker in after_delete['content']
    print('Knowledge acceptance passed: async parsing, embedding, pgvector retrieval, private attachments, citations, replacement and deletion.')
    return {'attachment': attachment['id'], 'attachment_marker': attachment_marker,
            'citation': restored_citation, 'citation_marker': replacement_marker,
            'conversation': restored_conversation}


def verify_knowledge_restore(actor, state):
    download = actor.client.get('/files/' + state['attachment'] + '/download')
    download.raise_for_status()
    assert download.text == state['attachment_marker']
    citation = actor.request('GET', '/citations/' + state['citation'])
    assert state['citation_marker'] in citation['excerpt']
    messages = actor.request('GET', '/conversations/' + state['conversation'] + '/messages')
    assert any(state['citation_marker'] in message['content'] for message in messages)
    print('Knowledge restore verified: authenticated attachment, citation and conversation.')
