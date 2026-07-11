#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""企业微信通知。

支持：
1. 企业微信群机器人：QYWX_KEY
2. 企业微信自建应用：QYWX_AM=corpid,corpsecret,touser,agentid
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _webhook_url(key_or_url: str) -> str:
    value = key_or_url.strip()
    if value.startswith("https://") or value.startswith("http://"):
        return value
    return f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={value}"


def send_wecom_bot(title: str, content: str, key_or_url: str) -> bool:
    """通过企业微信群机器人发送文本消息。"""
    url = _webhook_url(key_or_url)
    payload = {
        "msgtype": "text",
        "text": {"content": f"{title}\n\n{content}"},
    }
    try:
        response = requests.post(url, json=payload, timeout=(15, 30))
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") == 0:
            logger.info("企业微信群机器人推送成功。")
            return True
        logger.error("企业微信群机器人推送失败：%s", data)
    except Exception as exc:
        logger.error("企业微信群机器人推送异常：%s", exc)
    return False


def _get_wecom_access_token(corpid: str, corpsecret: str) -> Optional[str]:
    try:
        response = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": corpid, "corpsecret": corpsecret},
            timeout=(15, 30),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") == 0 and data.get("access_token"):
            return str(data["access_token"])
        logger.error("获取企业微信 access_token 失败：%s", data)
    except Exception as exc:
        logger.error("获取企业微信 access_token 异常：%s", exc)
    return None


def send_wecom_app(title: str, content: str, config: str) -> bool:
    """通过企业微信自建应用发送文本消息。

    QYWX_AM 格式：corpid,corpsecret,touser,agentid
    """
    parts = [part.strip() for part in config.split(",")]
    if len(parts) != 4 or not all(parts):
        logger.error("QYWX_AM 格式错误，应为 corpid,corpsecret,touser,agentid。")
        return False

    corpid, corpsecret, touser, agentid = parts
    try:
        agentid_value = int(agentid)
    except ValueError:
        logger.error("QYWX_AM 中的 agentid 必须是整数。")
        return False

    token = _get_wecom_access_token(corpid, corpsecret)
    if not token:
        return False

    payload = {
        "touser": touser,
        "msgtype": "text",
        "agentid": agentid_value,
        "text": {"content": f"{title}\n\n{content}"},
        "safe": 0,
    }
    try:
        response = requests.post(
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            params={"access_token": token},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=(15, 30),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errcode") == 0:
            logger.info("企业微信自建应用推送成功。")
            return True
        logger.error("企业微信自建应用推送失败：%s", data)
    except Exception as exc:
        logger.error("企业微信自建应用推送异常：%s", exc)
    return False


def send(title: str, content: str) -> bool:
    """发送所有已配置的企业微信通知。"""
    if not content:
        logger.warning("推送内容为空，跳过推送。")
        return False

    qywx_key = os.getenv("QYWX_KEY", "").strip()
    qywx_am = os.getenv("QYWX_AM", "").strip()

    if not qywx_key and not qywx_am:
        logger.warning("未配置 QYWX_KEY 或 QYWX_AM，跳过企业微信推送。")
        return False

    results = []
    if qywx_key:
        results.append(send_wecom_bot(title, content, qywx_key))
    if qywx_am:
        results.append(send_wecom_app(title, content, qywx_am))
    return any(results)
