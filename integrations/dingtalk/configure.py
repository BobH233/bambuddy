"""Read robot configuration JSON from stdin, test delivery, then enable Bambuddy."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import bridge

root=Path('/Users/helen/bambuddy')
c=bridge.config()
received=json.load(sys.stdin)
allowed={'mode','webhook_url','sign_secret','app_key','app_secret','robot_code','open_conversation_id'}
if not isinstance(received,dict) or set(received)-allowed:
 raise SystemExit('Only robot configuration fields are accepted.')
c.update(received)
if c.get('mode') not in ('webhook','app') or not bridge.ready(c):
 raise SystemExit('Robot configuration is incomplete.')
try:
 bridge.Sender().send({'title':'通知连接测试','message':'实验室 Bambuddy 钉钉通知已连接。这是一条配置测试消息。'},c)
except Exception as exc:
 print('Test failed:',str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__)
 raise SystemExit(1)
config=bridge.ROOT/'config.json'
staging=config.with_suffix('.json.new')
staging.write_text(json.dumps(c,ensure_ascii=False,indent=2))
staging.chmod(0o600)
staging.replace(config)
with sqlite3.connect(root/'data/bambuddy.db',timeout=15) as db:
 cursor=db.execute('UPDATE notification_providers SET enabled=1 WHERE name=? AND provider_type=?',('钉钉 · 实验室通知','webhook'))
 if cursor.rowcount != 1:raise RuntimeError('Expected exactly one Bambuddy DingTalk provider')
print('DingTalk test accepted. Bambuddy notification channel enabled.')
