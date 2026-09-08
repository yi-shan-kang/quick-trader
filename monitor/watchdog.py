# -*- coding: utf-8 -*-
"""独立进程看门狗 - 监测策略进程心跳，超时/进程消失时推送告警

独立于主交易进程运行，由计划任务周期调度（见 setup_scheduled_tasks.ps1）。
它读取 trading_records/../logs/instances/<id>/heartbeat.json（策略心跳文件），
当 last_heartbeat 距今超过 timeout_sec 或 pid 进程已不存在时，通过
monitor.alerter 推送飞书 / 企业微信告警。

用法（单次检查，适合计划任务）：
    python -m monitor.watchdog --once
用法（常驻模式，内部循环）：
    python -m monitor.watchdog
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

# 项目根目录（本文件在 <root>/monitor/watchdog.py）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HEARTBEAT_ROOT = os.path.join(_ROOT, 'logs', 'instances')

sys.path.insert(0, _ROOT)

try:
    from monitor.alerter import get_alerter
    HAS_ALERTER = True
except ImportError:
    HAS_ALERTER = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


def _parse_time(s) -> Optional[datetime]:
    """解析 heartbeat.json 中的时间字符串 -> datetime，解析失败返回 None"""
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _process_alive(pid):
    """判断 pid 对应进程是否存活（跨平台）

    - 优先使用 psutil.pid_exists，跨平台一致
    - psutil 不可用时退回 os.kill(pid, 0)
      （Windows 上对不在同会话的 PID 可能抛 AccessError，此时视为
      "无法判定"，保持 True 让心跳时间检测兜底，避免误告警）
    """
    if not pid:
        return True  # 无 pid 信息，跳过进程级判断，仅依赖心跳时间
    if HAS_PSUTIL:
        try:
            return bool(psutil.pid_exists(int(pid)))
        except (ValueError, TypeError):
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        # 权限不足/平台差异：宁可信任心跳时间，避免误告警
        return True


def check_instance_health(instance_id: str, timeout_sec: float) -> Optional[dict]:
    """检查单个实例心跳是否超时，返回告警信息字典或 None"""
    hb_file = os.path.join(_HEARTBEAT_ROOT, instance_id, 'heartbeat.json')
    if not os.path.isfile(hb_file):
        return {
            'instance_id': instance_id,
            'level': 'info',
            'message': '未找到心跳文件，可能是新实例或已清理。',
        }

    try:
        with open(hb_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        return {
            'instance_id': instance_id,
            'level': 'error',
            'message': f'心跳文件解析失败: {e}',
        }

    last_hb_str = data.get('last_heartbeat', '')
    last_dt = _parse_time(last_hb_str)
    status = data.get('status', 'unknown')

    issues = []
    if status != 'running':
        issues.append(f'状态异常 (status={status})')
    if last_dt is not None:
        elapsed = (datetime.now() - last_dt).total_seconds()
        if elapsed > timeout_sec:
            issues.append(f'心跳超时 {elapsed:.0f}s (阈值 {timeout_sec:.0f}s)')
    else:
        issues.append(f'心跳时间无法解析: {last_hb_str!r}')

    pid_alive = _process_alive(data.get('pid'))
    if not pid_alive:
        issues.append(f'进程已不存在 (pid={data.get("pid")})')

    if not issues:
        return None

    return {
        'instance_id': instance_id,
        'level': 'error',
        'message': '，'.join(issues),
    }


def run_once(timeout_sec: float):
    """执行一次全量心跳检查并推送告警"""
    if not os.path.isdir(_HEARTBEAT_ROOT):
        logger.info(f'无心跳目录: {_HEARTBEAT_ROOT}，跳过')
        return
    instance_ids = [
        name for name in os.listdir(_HEARTBEAT_ROOT)
        if os.path.isdir(os.path.join(_HEARTBEAT_ROOT, name))
    ]
    if not instance_ids:
        logger.info('未发现实例心跳，跳过')
        return

    alerts = []
    for instance_id in instance_ids:
        result = check_instance_health(instance_id, timeout_sec)
        if result and result.get('level') == 'error':
            alerts.append(result)
            logger.warning(f"[看门狗] {instance_id}: {result['message']}")

    if not alerts:
        logger.info(f'看门狗检查通过: {len(instance_ids)} 个实例全部正常 ✅')
        return

    if not HAS_ALERTER:
        logger.error('monitor.alerter 不可用，无法推送看门狗告警')
        return

    alerter = get_alerter()
    for a in alerts:
        alerter.send(
            f'策略进程异常 ({a["instance_id"]})',
            f'策略 **{a["instance_id"]}** 异常：\n{a["message"]}\n请检查进程或 QMT 客户端。',
            dedup_key=f'watchdog:{a["instance_id"]}',
        )


def main():
    parser = argparse.ArgumentParser(description='策略进程看门狗')
    parser.add_argument('--once', action='store_true', help='仅执行一次检查（适合计划任务）')
    parser.add_argument('--timeout', type=float, default=None, help='心跳超时阈值(秒)，默认读配置')
    parser.add_argument('--interval', type=float, default=60, help='常驻模式检查间隔(秒)')
    args = parser.parse_args()

    # 读取配置中的 heartbeat_watchdog 超时
    timeout_sec = args.timeout
    if timeout_sec is None and HAS_ALERTER:
        cfg = get_alerter().get_config('heartbeat_watchdog')
        timeout_sec = float(cfg.get('timeout_sec', 180))
    if not timeout_sec:
        timeout_sec = 180.0

    if args.once:
        run_once(timeout_sec)
        return

    logger.info(f'看门狗常驻模式启动 (超时 {timeout_sec:.0f}s, 间隔 {args.interval:.0f}s)')
    while True:
        run_once(timeout_sec)
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
