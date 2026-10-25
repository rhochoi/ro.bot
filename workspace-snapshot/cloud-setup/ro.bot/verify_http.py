"""Check a running ro pet backend without calling an external model."""
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = "http://127.0.0.1:18765"
with urlopen(base + "/", timeout=10) as response:
    assert response.status == 200
    html = response.read().decode()
assert 'id="stage"' in html
match = re.search(r'const TOKEN = "([a-f0-9]+)";', html)
assert match, "token initialization missing"
token = match.group(1)

for headers in ({}, {"Host": "invalid.example"}):
    try:
        urlopen(Request(base + "/api/state", headers=headers), timeout=10)
    except HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("unauthorized request accepted")

with urlopen(Request(base + "/api/state", headers={"X-Ro-Token": token}), timeout=10) as response:
    state = json.load(response)
assert state["mood"] in ("idle", "anxious", "discovery", "focus", "resolved", "recording")
assert "ro.bot" in state["project_names"]

request = Request(
    base + "/api/ask/preview",
    data=json.dumps({"question": "ro.bot 구조 설명", "engine": "codex"}).encode(),
    headers={"X-Ro-Token": token, "Content-Type": "application/json"},
)
with urlopen(request, timeout=10) as response:
    preview = json.load(response)
assert preview.get("ok") and preview["engine"] == "codex" and "ro.bot" in preview["projects"]
print("PASS: pet HTML, authenticated state, token/Host rejection, Codex project preview (no model call)")
