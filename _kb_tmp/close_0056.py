import sys
sys.path.insert(0, 'orchestra')
from cards import _find_card, _request

card, h = _find_card('TASK-0056')
lines = card['content'].split('\n')
lines[0] = f"TASK-0056 failed {h['assignee']} | {h['title']}"
new_content = '\n'.join(lines)
_request('PATCH', f"/memories/{card['id']}", {"content": new_content})
print("TASK-0056 -> failed")
