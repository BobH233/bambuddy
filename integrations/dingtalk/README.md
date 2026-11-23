# 钉钉通知适配器

从已部署的实验室适配器导入。使用 Python 标准库运行，接收 Bambuddy Webhook，将通知写入 SQLite 队列后投递到钉钉。默认监听 `127.0.0.1:11112`。

## 文件

- `bridge.py`：本地 HTTP 接口、消息队列、Webhook 加签及企业应用发送。
- `notifications_zh.py`、`lab_zh_CN.json`：中文通知和系统文字映射。
- `install.py`、`configure.py`：原部署的 macOS 安装与配置脚本。脚本中保留原服务器目录与 LaunchAgent 用户编号，迁移环境时应先按目标目录调整。
- `config.example.json`：无凭据的配置示例。
- `test_*.py`：模拟测试，不发送真实通知。

## 配置与运行

在受保护的运行目录创建 `config.json`，权限设为 `0600`。真实配置文件已被 Git 忽略。`adapter_token` 应为随机长字符串，与 Bambuddy Webhook 的 Bearer token 相同。

Webhook 模式填写 `webhook_url` 与 `sign_secret`；企业应用模式填写 `app_key`、`app_secret`、`robot_code` 和 `open_conversation_id`。

`external_url` 填写 Bambuddy 的外部 HTTPS 地址，与系统外部地址设置一致。图片使用现有的、带随机标识且会过期的单张通知照片地址，适配器不会转发实时摄像头令牌。

在 Bambuddy 中配置 Webhook 通道：

```json
{
  "webhook_url": "http://127.0.0.1:11112/notify",
  "auth_header": "Bearer <与适配器配置相同的随机 token>",
  "payload_format": "generic",
  "photo_as_url": true
}
```

通道还需开启 `attach_photo`。选择要转发的事件，并按需要限制打印机范围。

运行适配器：

```sh
DINGTALK_BRIDGE_DIR=/path/to/protected/runtime \
python /path/to/source/integrations/dingtalk/bridge.py
```

`GET /health` 返回健康状态和队列数量，不返回配置密钥。已排队消息在重启后继续投递；不确定的网络超时边界仍可能重复。`configure.py` 的配置流程会向配置中的机器人发送一条真实连接测试，普通启动和本目录的模拟测试不会。

## 测试

使用安装了 Bambuddy 后端依赖的虚拟环境，在本目录运行：

```sh
APP_ROOT=/absolute/path/to/bambuddy \
DATABASE_URL=sqlite+aiosqlite:////tmp/bambuddy-dingtalk-mock-tests.db \
python -m unittest test_bridge.py test_localization.py test_webhook_photos.py
```

`lab_zh_CN.json` 与前端 `frontend/src/i18n/lab-zh-CN.json`、后端 `backend/app/data/lab_zh_CN.json` 同步维护。
