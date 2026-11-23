"""Check the local services without connecting a bot to Discord."""

import json
import os
import socket
import urllib.error
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:9000/", timeout=5) as response:
    assert "cobalt" in json.load(response)

request = urllib.request.Request(
    "http://127.0.0.1:2333/version",
    headers={"Authorization": os.environ["NODELINK_PASSWORD"]},
)
with urllib.request.urlopen(request, timeout=5) as response:
    assert response.read().strip() not in (b"", b"unknown")

try:
    urllib.request.urlopen("http://127.0.0.1:2333/version", timeout=5)
except urllib.error.HTTPError as exc:
    assert exc.code == 401
else:
    raise AssertionError("NodeLink accepted an unauthenticated request")

for port in (9000, 2333):
    with socket.socket() as sock:
        sock.bind(("127.0.0.2", port))

print("Cobalt and NodeLink are healthy, local, and NodeLink requires authentication.")
