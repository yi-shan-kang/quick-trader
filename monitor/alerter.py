# -*- coding: utf-8 -*-
"""告警推送模块 - 飞书 / 企业微信双通道

提供带去抖限频的推送能力：
- 支持飞书自定义机器人、企业微信机器人两种 webhook
- 按「类型+实例」维度做冷却去抖，避免重复事件刷屏
- 通过 config/alert_config.json 配置通道与阈值

用法（进程内单例）：
    from monitor.alerter import get_alerter
    get_alerter().send('对账偏差', '账户 xxx 发现持仓偏差 ...')

用法（独立看门狗）：
    from monitor.alerter import get_alerter
    get_alerter().send('策略停止', 'QixingGaozhao_SimTrading 心跳超时')
"""

import hashlib
import hmac
import base64
import json
import os
import time
import logging
from typing import Dict, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config', 'alert_config.template.json'
)


def _load_config(path: Optional[str] = None) -> dict:
    """加载告警配置

    优先级：
    1. 显式传入的 path（如独立看门狗启动时）
    2. config/alert_config.local.json（真实凭据，不入库，首选）
    3. config/alert_config.template.json（占位模板，入库）
    无任何文件时返回空配置（所有通道禁用）。
    """
    candidates = []
    if path:
        candidates.append(path)
    cfg_dir = os.path.dirname(_DEFAULT_CONFIG_PATH)
    candidates.append(os.path.join(cfg_dir, 'alert_config.local.json'))
    candidates.append(_DEFAULT_CONFIG_PATH)

    for cfg_path in candidates:
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f'读取告警配置失败: {cfg_path}, {e}')
    return {}


def _gen_feishu_sign(timestamp: str, secret: str) -> str:
    """飞书机器人加签：string_to_sign = timestamp + '\\n' + secret"""
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode('utf-8')


class Alerter:
    """告警推送器（含去抖限频）"""

    def __init__(self, config_path: Optional[str] = None):
        self._config = _load_config(config_path)
        self._cooldown_sec = float(self._config.get('cooldown_sec', 300))
        self._last_sent: Dict[str, float] = {}

    # ---- 通道状态 ----
    @property
    def feishu_cfg(self) -> dict:
        return self._config.get('channels', {}).get('feishu', {})

    @property
    def wecom_cfg(self) -> dict:
        return self._config.get('channels', {}).get('wecom', {})

    def has_channel(self) -> bool:
        """是否至少配置了一个可用通道

        设计：群里发消息模式。如需「只发给自己」，
        请建一个只包含本人的「私人群」（飞书/企微都支持）并把机器人拉进去，
        这种用法对个人和小群都足够友好，无需复杂凭据配置。
        """
        return bool(
            (self.feishu_cfg.get('enabled') and self.feishu_cfg.get('webhook_url'))
            or (self.wecom_cfg.get('enabled') and self.wecom_cfg.get('webhook_url'))
        )

    def is_enabled(self, monitor_key: str) -> bool:
        """某个监控项是否启用"""
        monitors = self._config.get('monitors', {})
        mon = monitors.get(monitor_key, {})
        if isinstance(mon, dict):
            return mon.get('enabled', True)
        return True

    def get_config(self, monitor_key: str) -> dict:
        """读取监控项的配置参数"""
        mon = self._config.get('monitors', {}).get(monitor_key, {})
        return mon if isinstance(mon, dict) else {}

    # ---- 去抖 ----
    def _allow(self, key: str) -> bool:
        now = time.time()
        if key in self._last_sent:
            if now - self._last_sent[key] < self._cooldown_sec:
                return False
        self._last_sent[key] = now
        return True

    # ---- 发送 ----
    def send(self, title: str, message: str, dedup_key: Optional[str] = None):
        """发送告警到所有已启用通道

        Args:
            title: 标题
            message: 正文
            dedup_key: 去抖键，同一键在冷却期内不重复推送
        """
        if not self.has_channel():
            logger.warning(f'[告警] 无可用推送通道，仅记日志: {title} | {message}')
            return

        dedup_key = dedup_key or title
        if self._cooldown_sec > 0 and not self._allow(dedup_key):
            logger.debug(f'[告警] 冷却期内跳过重复推送: {title}')
            return

        total = len(self.feishu_cfg.get('webhook_url') or '')
        if self.feishu_cfg.get('enabled') and self.feishu_cfg.get('webhook_url'):
            ok = self._send_feishu(title, message)
            if ok:
                logger.info(f'[告警] 飞书推送成功: {title}')
        if self.wecom_cfg.get('enabled') and self.wecom_cfg.get('webhook_url'):
            ok = self._send_wecom(title, message)
            if ok:
                logger.info(f'[告警] 企业微信推送成功: {title}')

    # ---- 飞书 ----
    def _send_feishu(self, title: str, message: str) -> bool:
        cfg = self.feishu_cfg
        url = cfg.get('webhook_url', '')
        secret = cfg.get('secret', '')
        payload = {
            'msg_type': 'interactive',
            'card': {
                'header': {
                    'template': 'red',
                    'title': {'tag': 'plain_text', 'content': title},
                },
                'elements': [{'tag': 'div', 'text': {'tag': 'lark_md', 'content': message}}],
            },
        }
        headers = {'Content-Type': 'application/json'}
        if secret:
            timestamp = str(int(time.time()))
            payload['timestamp'] = timestamp
            payload['sign'] = _gen_feishu_sign(timestamp, secret)
        return self._post(url, payload, headers)

    # ---- 企业微信 ----
    def _send_wecom(self, title: str, message: str) -> bool:
        cfg = self.wecom_cfg
        url = cfg.get('webhook_url', '')
        secret = cfg.get('secret', '')
        payload = {
            'msgtype': 'markdown',
            'markdown': {
                'content': f'<font color="warning">{title}</font>\n{message}',
            },
        }
        headers = {'Content-Type': 'application/json'}
        if secret:
            timestamp = str(int(time.time()))
            payload['timestamp'] = timestamp
            payload['sign'] = _gen_feishu_sign(timestamp, secret)
        return self._post(url, payload, headers)

    @staticmethod
    def _post(url: str, payload: dict, headers: dict) -> bool:
        if not HAS_REQUESTS:
            logger.error('requests 未安装，无法发送告警')
            return False
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            # 飞书: code==0 成功；企业微信: errcode==0 成功
            code = data.get('code', data.get('errcode', 0))
            if code != 0:
                logger.error(f'推送通道返回错误: code={code}, resp={data}')
                return False
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f'推送请求失败: {e}')
            return False


# 进程内单例（避免每个调用方重复加载配置）
_singleton: Optional[Alerter] = None


def get_alerter() -> Alerter:
    global _singleton
    if _singleton is None:
        _singleton = Alerter()
    return _singleton
