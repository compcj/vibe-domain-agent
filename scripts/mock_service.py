"""Deterministic model/business simulator for isolated container acceptance tests."""
import asyncio
import hashlib
import json
import os
import re

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
operations: dict[str, int] = {}
requests: dict[str, int] = {}
idempotent: dict[str, dict] = {}


@app.post('/v1/chat/completions')
async def completion(request: Request):
    body = await request.json()
    messages = body['messages']
    user = next(item['content'] for item in reversed(messages) if item['role'] == 'user')
    is_write = user.startswith('WRITE ')

    async def stream():
        await asyncio.sleep(0.05)
        if is_write and messages[-1]['role'] != 'tool':
            call = {'index': 0, 'id': 'call_' + hashlib.sha256(user.encode()).hexdigest()[:16],
                    'type': 'function', 'function': {'name': 'record_event', 'arguments': json.dumps({'object_id': user[6:]})}}
            yield 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'tool_calls': [call]}}]}) + '\n\n'
        else:
            content = user
            if user == 'CONTEXT_CHECK':
                markers = sorted(set(re.findall(r'(?:kb|attachment)-marker-[a-f0-9]{32}', json.dumps(messages))))
                content = ', '.join(markers) if markers else 'No reference context provided'
            for text in ['Verified response: ', content, '.']:
                yield 'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': text}}]}) + '\n\n'
                await asyncio.sleep(0.03)
        yield 'data: ' + json.dumps({'choices': [], 'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}) + '\n\n'
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream')


@app.post('/v1/embeddings')
async def embeddings(request: Request):
    body = await request.json()
    texts = body['input'] if isinstance(body['input'], list) else [body['input']]
    return {'data': [{'index': index, 'embedding': [1.0, 0.2, 0.3, 0.4]} for index, _ in enumerate(texts)]}


@app.post('/business/record')
async def record(request: Request):
    body = await request.json()
    identifier = body['object_id']
    requests[identifier] = requests.get(identifier, 0) + 1
    key = request.headers.get('Idempotency-Key', '')
    if key and key in idempotent:
        return idempotent[key]
    operations[identifier] = operations.get(identifier, 0) + 1
    result = {'ok': True, 'object_id': identifier}
    if key:
        idempotent[key] = result
    return result


@app.get('/business/counts')
def counts():
    return {'operations': operations, 'requests': requests}


if __name__ == '__main__':
    if os.environ.get('APP_ENV') != 'test':
        raise SystemExit('Simulator requires APP_ENV=test; never expose it in production.')
    uvicorn.run(app, host='0.0.0.0', port=9000, log_level='warning')
