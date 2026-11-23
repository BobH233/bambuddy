import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.models.user import User
from backend.app.models.web_push import WebPushSubscription
from backend.app.schemas.web_push import DEFAULT_CATEGORIES, PushEndpoint, PushSubscriptionInput
from backend.app.services import web_push
from backend.app.utils.local_time import utcnow_naive

router = APIRouter(prefix="/web-push", tags=["web-push"])


async def push_user(user: User = Depends(get_current_user)) -> User:
    if not user.has_permission("printers:read"):
        raise HTTPException(403, "当前账号没有查看打印机的权限")
    return user


@router.get("/config")
async def config(response: Response, user: User = Depends(push_user), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    try:
        key = await asyncio.to_thread(web_push.public_key)
    except (OSError, ValueError):
        raise HTTPException(503, "推送密钥初始化失败，请联系管理员检查服务器数据目录权限") from None
    rows = (await db.execute(select(WebPushSubscription).where(WebPushSubscription.user_id == user.id))).scalars().all()
    return {
        "public_key": key,
        "default_categories": DEFAULT_CATEGORIES,
        "subscriptions": [{"endpoint_hash": row.endpoint_hash, "categories": row.categories} for row in rows],
    }


@router.post("/subscribe")
async def subscribe(data: PushSubscriptionInput, user: User = Depends(push_user), db: AsyncSession = Depends(get_db)):
    fingerprint = web_push.endpoint_hash(data.endpoint)
    row = (
        await db.execute(select(WebPushSubscription).where(WebPushSubscription.endpoint_hash == fingerprint))
    ).scalar_one_or_none()
    if row is not None and row.user_id != user.id:
        raise HTTPException(409, "此设备已订阅其他账号，请先关闭旧订阅再重新开启")
    if row is None:
        count = await db.scalar(
            select(func.count()).select_from(WebPushSubscription).where(WebPushSubscription.user_id == user.id)
        )
        if count is not None and count >= 100:
            raise HTTPException(409, "此账号已达到 100 台通知设备的上限")
        row = WebPushSubscription(user_id=user.id, endpoint_hash=fingerprint)
        db.add(row)
    row.endpoint = data.endpoint
    row.p256dh = data.keys.p256dh
    row.auth = data.keys.auth
    row.categories = data.categories
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "订阅已变化，请刷新页面后重试") from None
    return {"success": True}


@router.post("/unsubscribe")
async def unsubscribe(data: PushEndpoint, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(
        delete(WebPushSubscription).where(
            WebPushSubscription.endpoint_hash == web_push.endpoint_hash(data.endpoint),
            WebPushSubscription.user_id == user.id,
        )
    )
    await db.commit()
    return {"success": True}


@router.post("/test")
async def test(data: PushEndpoint, user: User = Depends(push_user), db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(
            select(WebPushSubscription).where(
                WebPushSubscription.endpoint_hash == web_push.endpoint_hash(data.endpoint),
                WebPushSubscription.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "此设备尚未开启通知")
    now = utcnow_naive()
    if row.last_test_at and row.last_test_at > now - timedelta(seconds=60):
        raise HTTPException(429, "请等待一分钟后再发送测试通知")
    row.last_test_at = now
    info = {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}}
    row_id = row.id
    await db.commit()
    result = await asyncio.to_thread(
        web_push.send_push,
        info,
        {
            "title": "Bambuddy 通知已开启",
            "body": "此设备可以接收打印通知。",
            "url": "/settings?tab=general#card-web-push",
            "tag": "bambuddy:push-test",
        },
    )
    if result == "expired":
        await db.execute(delete(WebPushSubscription).where(WebPushSubscription.id == row_id))
        await db.commit()
        raise HTTPException(410, "此设备的订阅已失效，请重新开启通知")
    if result != "sent":
        raise HTTPException(502, "通知发送失败，请稍后重试或联系管理员检查推送服务连接")
    return {"success": True}
