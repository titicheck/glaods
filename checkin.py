#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GLaDOS / Railgun 多域名、多账号签到与积分兑换。

cron: 17 8 * * *
new Env('GLaDOS-Railgun签到');
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

import requests

from logging_config import init_logger
from notify import send as send_notify

logger = init_logger()


class CheckinStatus(Enum):
    SUCCESS = 0
    REPEAT = 1
    FAILURE = -2


class ExchangePlan(Enum):
    PLAN0 = "plan0"
    PLAN100 = "plan100"
    PLAN200 = "plan200"
    PLAN500 = "plan500"


EXCHANGE_POINTS: Dict[str, int] = {
    ExchangePlan.PLAN0.value: 0,
    ExchangePlan.PLAN100.value: 100,
    ExchangePlan.PLAN200.value: 200,
    ExchangePlan.PLAN500.value: 500,
}


@dataclass(frozen=True)
class DomainConfig:
    domain: str
    checkin_token: str
    cookies_env: str
    plans_env: str


@dataclass(frozen=True)
class AccountTask:
    account_name: str
    domain: str
    checkin_token: str
    cookie: str
    exchange_plan: str


@dataclass
class CheckinResult:
    account_name: str
    domain: str
    email: str = "未知"
    status: str = "签到失败"
    earned_points: str = "0"
    days: str = "未知"
    points_total: str = "未知"
    exchange: str = "未兑换"
    code: CheckinStatus = CheckinStatus.FAILURE

    def to_dict(self) -> Dict[str, Union[str, CheckinStatus]]:
        return asdict(self)


class Config:
    """从环境变量加载域名、账号、Cookie 与兑换计划。"""

    DOMAIN_CONFIGS = (
        DomainConfig(
            domain="glados.rocks",
            checkin_token="glados.one",
            cookies_env="GLADOS_ROCKS_COOKIES",
            plans_env="GLADOS_ROCKS_PLANS",
        ),
        DomainConfig(
            domain="railgun.info",
            checkin_token="railgun.info",
            cookies_env="RAILGUN_INFO_COOKIES",
            plans_env="RAILGUN_INFO_PLANS",
        ),
    )

    def __init__(self) -> None:
        self.verbose = self._parse_bool(os.getenv("GLADOS_VERBOSE", "false"))
        self.tasks: List[AccountTask] = []
        self._load_tasks()

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _split_records(raw: str) -> List[str]:
        """支持换行或 & 分隔多条记录。"""
        return [item.strip() for item in re.split(r"[\r\n]+|&", raw) if item.strip()]

    @classmethod
    def _parse_named_values(cls, raw: str, env_name: str) -> Dict[str, str]:
        """解析 `账号名|值` 记录，并拒绝重名。"""
        values: Dict[str, str] = {}
        for line_no, record in enumerate(cls._split_records(raw), 1):
            if "|" not in record:
                raise ValueError(
                    f"环境变量 {env_name} 第 {line_no} 条格式错误，应为：账号名|值"
                )
            account_name, value = record.split("|", 1)
            account_name = account_name.strip()
            value = value.strip()
            if not account_name or not value:
                raise ValueError(
                    f"环境变量 {env_name} 第 {line_no} 条账号名或值为空。"
                )
            if account_name in values:
                raise ValueError(
                    f"环境变量 {env_name} 存在重复账号名：{account_name}"
                )
            values[account_name] = value
        return values

    def _load_domain_tasks(self, spec: DomainConfig) -> None:
        raw_cookies = os.getenv(spec.cookies_env, "").strip()
        raw_plans = os.getenv(spec.plans_env, "").strip()

        if not raw_cookies:
            logger.info("域名 %s 未配置 %s，跳过。", spec.domain, spec.cookies_env)
            if raw_plans:
                logger.warning(
                    "%s 已配置但 %s 为空，兑换计划不会执行。",
                    spec.plans_env,
                    spec.cookies_env,
                )
            return

        cookies = self._parse_named_values(raw_cookies, spec.cookies_env)
        plans = self._parse_named_values(raw_plans, spec.plans_env) if raw_plans else {}

        unknown_plan_accounts = sorted(set(plans) - set(cookies))
        if unknown_plan_accounts:
            logger.warning(
                "%s 中以下账号没有对应 Cookie，已忽略：%s",
                spec.plans_env,
                ", ".join(unknown_plan_accounts),
            )

        for account_name, cookie in cookies.items():
            plan = plans.get(account_name, ExchangePlan.PLAN0.value).lower()
            if account_name not in plans:
                logger.warning(
                    "[%s][%s] 未配置兑换计划，按 plan0 处理，不兑换。",
                    spec.domain,
                    account_name,
                )
            if plan not in EXCHANGE_POINTS:
                raise ValueError(
                    f"[{spec.domain}][{account_name}] 兑换计划 {plan!r} 无效；"
                    "仅支持 plan0/plan100/plan200/plan500。"
                )
            self.tasks.append(
                AccountTask(
                    account_name=account_name,
                    domain=spec.domain,
                    checkin_token=spec.checkin_token,
                    cookie=cookie,
                    exchange_plan=plan,
                )
            )

    def _load_tasks(self) -> None:
        if os.getenv("GLADOS_COOKIES") or os.getenv("GLADOS_EXCHANGE_PLAN"):
            logger.warning(
                "检测到旧变量 GLADOS_COOKIES/GLADOS_EXCHANGE_PLAN；"
                "本版本不再使用它们，请迁移到按域名拆分的新变量。"
            )

        for spec in self.DOMAIN_CONFIGS:
            self._load_domain_tasks(spec)

        logger.info("共加载 %d 个独立账号任务。", len(self.tasks))
        for task in self.tasks:
            logger.info(
                "任务：账号=%s，域名=%s，兑换计划=%s",
                task.account_name,
                task.domain,
                task.exchange_plan,
            )


class API:
    CHECKIN_PATH = "/api/user/checkin"
    STATUS_PATH = "/api/user/status"
    POINTS_PATH = "/api/user/points"
    EXCHANGE_PATH = "/api/user/exchange"

    def __init__(self, task: AccountTask, verbose: bool = False) -> None:
        self.task = task
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(
            {
                "origin": f"https://{task.domain}",
                "referer": f"https://{task.domain}/console/checkin",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "content-type": "application/json;charset=UTF-8",
                "accept": "application/json, text/plain, */*",
            }
        )

    def __enter__(self) -> "API":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.session.close()
        return False

    def _prefix(self) -> str:
        return f"[{self.task.account_name}][{self.task.domain}]"

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, str]] = None,
    ) -> Optional[requests.Response]:
        url = f"https://{self.task.domain}{path}"
        headers = {"cookie": self.task.cookie}
        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=payload if method.upper() == "POST" else None,
                timeout=(30, 90),
            )
            if not response.ok:
                logger.error(
                    "%s 请求失败：%s %s，HTTP %s，响应：%s",
                    self._prefix(),
                    method,
                    path,
                    response.status_code,
                    response.text[:500],
                )
                return None
            if self.verbose:
                logger.info(
                    "%s %s %s 响应：%s",
                    self._prefix(),
                    method,
                    path,
                    response.text[:1000],
                )
            return response
        except requests.RequestException as exc:
            logger.error("%s 网络请求异常：%s", self._prefix(), exc)
            return None

    @staticmethod
    def _json(response: Optional[requests.Response]) -> Optional[Dict]:
        if response is None:
            return None
        try:
            data = response.json()
            return data if isinstance(data, dict) else None
        except (ValueError, json.JSONDecodeError):
            return None

    def get_status(self) -> Tuple[str, str, int]:
        data = self._json(self._request("GET", self.STATUS_PATH))
        if not data:
            return "未知", "未知", -2
        code = int(data.get("code", -2))
        user_data = data.get("data") or {}
        left_days = user_data.get("leftDays")
        email = str(user_data.get("email") or "未知")
        if left_days is None:
            return "未知", email, code
        try:
            days = str(int(float(left_days)))
        except (TypeError, ValueError):
            days = str(left_days)
        return f"{days} 天", email, code

    def checkin(self) -> Dict[str, Union[str, CheckinStatus]]:
        data = self._json(
            self._request(
                "POST",
                self.CHECKIN_PATH,
                {"token": self.task.checkin_token},
            )
        )
        if not data:
            return {
                "status": "签到失败",
                "points": "0",
                "message": "网络请求或响应解析失败",
                "code": CheckinStatus.FAILURE,
            }

        code = int(data.get("code", -2))
        message = str(data.get("message", ""))
        points = str(data.get("points", 0))
        if code == CheckinStatus.SUCCESS.value:
            return {
                "status": "签到成功",
                "points": points,
                "message": message,
                "code": CheckinStatus.SUCCESS,
            }
        if code == CheckinStatus.REPEAT.value:
            return {
                "status": "重复签到",
                "points": "0",
                "message": message,
                "code": CheckinStatus.REPEAT,
            }
        return {
            "status": "签到失败",
            "points": "0",
            "message": message or "未知错误",
            "code": CheckinStatus.FAILURE,
        }

    def get_points(self) -> Tuple[str, Optional[int]]:
        data = self._json(self._request("GET", self.POINTS_PATH))
        if not data or data.get("points") is None:
            return "未知", None
        try:
            points = int(float(data["points"]))
        except (TypeError, ValueError):
            return str(data["points"]), None
        return f"{points} 积分", points

    def exchange(self, plan: str) -> str:
        data = self._json(
            self._request(
                "POST",
                self.EXCHANGE_PATH,
                {"planType": plan},
            )
        )
        if not data:
            return "兑换失败（网络请求或响应解析失败）"
        code = int(data.get("code", -2))
        message = str(data.get("message", "未知错误"))
        if code == 0:
            return f"兑换成功（{plan}）"
        return f"兑换失败（{message}）"


class Checker:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.results: List[CheckinResult] = []

    def _run_one(self, task: AccountTask) -> CheckinResult:
        result = CheckinResult(account_name=task.account_name, domain=task.domain)
        prefix = f"[{task.account_name}][{task.domain}]"
        logger.info("%s 开始执行。", prefix)

        with API(task, verbose=self.config.verbose) as api:
            result.days, result.email, _ = api.get_status()

            checkin_result = api.checkin()
            result.status = str(checkin_result["status"])
            result.earned_points = str(checkin_result["points"])
            result.code = checkin_result["code"]  # type: ignore[assignment]

            result.points_total, points_num = api.get_points()

            required_points = EXCHANGE_POINTS[task.exchange_plan]
            if task.exchange_plan == ExchangePlan.PLAN0.value:
                result.exchange = "未兑换（plan0）"
            elif points_num is None:
                result.exchange = "未兑换（积分查询失败）"
            elif points_num < required_points:
                result.exchange = (
                    f"未兑换（积分不足：{points_num}/{required_points}）"
                )
            else:
                logger.info(
                    "%s 积分满足条件，执行 %s 兑换。",
                    prefix,
                    task.exchange_plan,
                )
                result.exchange = api.exchange(task.exchange_plan)

        logger.info(
            "%s 完成：%s，剩余=%s，总积分=%s，%s。",
            prefix,
            result.status,
            result.days,
            result.points_total,
            result.exchange,
        )
        return result

    def run_all(self) -> None:
        total = len(self.config.tasks)
        for index, task in enumerate(self.config.tasks, 1):
            logger.info("========== 任务 %d/%d ==========", index, total)
            try:
                self.results.append(self._run_one(task))
            except Exception as exc:
                logger.exception(
                    "[%s][%s] 未预期异常：%s",
                    task.account_name,
                    task.domain,
                    exc,
                )
                self.results.append(
                    CheckinResult(
                        account_name=task.account_name,
                        domain=task.domain,
                        status="执行异常",
                        exchange="未兑换（任务异常）",
                        code=CheckinStatus.FAILURE,
                    )
                )

    def format_results(self) -> Tuple[str, str]:
        success = sum(r.code == CheckinStatus.SUCCESS for r in self.results)
        repeat = sum(r.code == CheckinStatus.REPEAT for r in self.results)
        failure = len(self.results) - success - repeat
        title = (
            f"GLaDOS/Railgun 签到：成功{success}，"
            f"重复{repeat}，失败{failure}"
        )

        lines: List[str] = []
        for index, result in enumerate(self.results, 1):
            lines.extend(
                [
                    f"#{index} 【{result.account_name}】",
                    f"域名：{result.domain}",
                    f"账号：{result.email}",
                    f"签到：{result.status}（+{result.earned_points} 积分）",
                    f"剩余：{result.days}",
                    f"积分：{result.points_total}",
                    f"兑换：{result.exchange}",
                    "",
                ]
            )
        return title, "\n".join(lines).rstrip()


def main() -> int:
    try:
        config = Config()
        if not config.tasks:
            title = "GLaDOS/Railgun 签到配置错误"
            content = "未加载到任何账号任务，请检查四个按域名拆分的环境变量。"
            logger.error(content)
            send_notify(title, content)
            return 2

        checker = Checker(config)
        checker.run_all()
        title, content = checker.format_results()
        logger.info("\n%s\n%s", title, content)
        send_notify(title, content)
        return 0
    except Exception as exc:
        logger.exception("脚本执行失败：%s", exc)
        send_notify("GLaDOS/Railgun 脚本执行失败", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
