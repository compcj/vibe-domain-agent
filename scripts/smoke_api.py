"""Unauthenticated deployment boundary check, executed inside the built image."""
import urllib.error
import urllib.request


def main():
    base = 'http://127.0.0.1:8000'
    for path in ('/health/live', '/health/ready'):
        with urllib.request.urlopen(base + path, timeout=5) as response:
            assert response.status == 200
    with urllib.request.urlopen(base + '/', timeout=5) as response:
        html = response.read().decode()
        assert '<div id="root">' in html and '/assets/' in html
    try:
        urllib.request.urlopen(base + '/api/v1/agents', timeout=5)
    except urllib.error.HTTPError as failure:
        assert failure.code == 401
    else:
        raise AssertionError('Member API allowed unauthenticated access')
    print('API/static/authorization smoke passed')


if __name__ == '__main__':
    main()
