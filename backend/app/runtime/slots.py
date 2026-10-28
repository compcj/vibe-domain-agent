"""Redis atomic expiring slot leases, shared by all run workers."""

import time

from redis import Redis

ACQUIRE = """
local now = tonumber(ARGV[1])
for i=1,2 do redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now) end
if redis.call('ZCARD', KEYS[1]) >= 10 or redis.call('ZCARD', KEYS[2]) >= 2 then return 0 end
for i=1,2 do
  redis.call('ZADD', KEYS[i], now + 420, ARGV[2])
  redis.call('EXPIRE', KEYS[i], 421)
end
return 1
"""
RELEASE = """
for i=1,2 do redis.call('ZREM', KEYS[i], ARGV[1]) end
return 1
"""


class RedisSlots:
    def __init__(self, url):
        self.redis = Redis.from_url(url, socket_timeout=5, socket_connect_timeout=5)

    def keys(self, owner):
        return ["domain-agent:{runs}:global", "domain-agent:{runs}:user:" + owner]

    def acquire(self, owner, token):
        return bool(self.redis.eval(ACQUIRE, 2, *self.keys(owner), time.time(), token))

    def release(self, owner, token):
        self.redis.eval(RELEASE, 2, *self.keys(owner), token)


class DevelopmentSlots:
    """Explicit single-process dev/test fallback when Redis is absent."""

    def __init__(self):
        from threading import Lock

        self.lock, self.leases = Lock(), {}

    def acquire(self, owner, token):
        with self.lock:
            now = time.monotonic()
            self.leases = {k: v for k, v in self.leases.items() if v[1] > now}
            if len(self.leases) >= 10 or sum(v[0] == owner for v in self.leases.values()) >= 2:
                return False
            self.leases[token] = (owner, now + 420)
            return True

    def release(self, owner, token):
        with self.lock:
            if self.leases.get(token, (None,))[0] == owner:
                self.leases.pop(token, None)
