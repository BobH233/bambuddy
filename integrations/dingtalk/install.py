import asyncio
import json
import os
from pathlib import Path
import plistlib
import secrets
import sqlite3
import subprocess
import sys
import time

root=Path('/Users/helen/bambuddy')
bridge=root/'dingtalk-bridge'
bridge.mkdir(mode=0o700,exist_ok=True)
os.chmod(bridge,0o700)
os.umask(0o077)
config=bridge/'config.json'
if not config.exists():
 config.write_text(json.dumps({
  'mode':'webhook','adapter_token':secrets.token_urlsafe(40),
  'webhook_url':'','sign_secret':'','app_key':'','app_secret':'','robot_code':'','open_conversation_id':''
 },ensure_ascii=False,indent=2))
config.chmod(0o600)
c=json.loads(config.read_text())
backup=root/'backups'/time.strftime('before-dingtalk-%Y%m%d-%H%M%S.db')
with sqlite3.connect(root/'data/bambuddy.db') as db:
 with sqlite3.connect(backup) as target: db.backup(target)
backup.chmod(0o600)
sys.path.insert(0,str(root/'app'))
os.environ['DATA_DIR']=str(root/'data')
os.environ['LOG_DIR']=str(root/'logs')
from backend.app.core.database import async_session,engine
from backend.app.models.notification import NotificationProvider
from sqlalchemy import select

async def create_provider():
 async with async_session() as db:
  name='钉钉 · 实验室通知'
  existing=(await db.execute(select(NotificationProvider).where(NotificationProvider.name==name))).scalar_one_or_none()
  if existing:
   print('Existing provider preserved:',existing.id)
  else:
   events={column.name:True for column in NotificationProvider.__table__.columns if column.name.startswith('on_')}
   provider=NotificationProvider(name=name,provider_type='webhook',enabled=False,
    config=json.dumps({'webhook_url':'http://127.0.0.1:11112/notify','auth_header':'Bearer '+c['adapter_token'],'payload_format':'generic'}),
    attach_photo=False,printer_id=None,quiet_hours_enabled=False,daily_digest_enabled=False,**events)
   db.add(provider)
   await db.commit()
   print('Created disabled provider:',provider.id,'event switches:',len(events))
 await engine.dispose()
asyncio.run(create_provider())
plist=Path('/Users/helen/Library/LaunchAgents/com.bambuddy.dingtalk.plist')
if plist.exists():
 raise RuntimeError('Existing launch agent preserved; inspect it before changing')
plist.write_bytes(plistlib.dumps({
 'Label':'com.bambuddy.dingtalk','ProgramArguments':[str(root/'venv/bin/python'),str(bridge/'bridge.py')],
 'WorkingDirectory':str(bridge),'EnvironmentVariables':{'PYTHONUNBUFFERED':'1','TZ':'Asia/Shanghai','DINGTALK_BRIDGE_DIR':str(bridge)},
 'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
 'StandardOutPath':str(root/'logs/dingtalk-bridge.stdout.log'),
 'StandardErrorPath':str(root/'logs/dingtalk-bridge.stderr.log'),
}))
plist.chmod(0o600)
subprocess.run(['launchctl','bootstrap','gui/501',str(plist)],check=True)
print('Installed loopback adapter. DingTalk credentials are not configured. Bambuddy provider remains disabled.')
print('Database backup:',backup)
