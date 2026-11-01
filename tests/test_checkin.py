"""Offline regression tests: fictional credentials, no API or notification calls."""

import json
import os
import unittest
from unittest.mock import patch

import requests

import checkin


def response(data=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data or {}).encode("utf-8")
    return result


def task(domain="glados.rocks", name="test", plan="plan0"):
    return checkin.AccountTask(
        account_name=name,
        domain=domain,
        checkin_token=domain,
        cookie="gld:sess=fictional==; gld:sess.sig=fake-signature;",
        exchange_plan=plan,
        user_agent="TestBrowser/1.0",
    )


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        # Any forgotten mock must fail, rather than reaching an external service.
        self.network = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Unexpected network access in offline test"),
        )
        self.network.start()
        self.addCleanup(self.network.stop)
        self.notify = patch("checkin.send_notify")
        self.notify.start()
        self.addCleanup(self.notify.stop)

    def run_task(self, responses, account=None):
        config = checkin.Config()
        with patch("requests.sessions.Session.request", side_effect=responses) as request:
            result = checkin.Checker(config)._run_one(account or task())
        return result, request

    def test_full_new_and_legacy_cookie_preserved(self):
        cookie = "gld:sess=new==; gld:sess.sig=new-sign; koa:sess=old==; koa:sess.sig=old-sign; extra=a&b"
        os.environ["GLADOS_ROCKS_COOKIES"] = "test|Cookie: " + cookie
        config = checkin.Config()
        self.assertEqual(config.tasks[0].cookie, cookie)
        self.assertEqual(config.tasks[0].checkin_token, "glados.rocks")

    def test_legacy_railgun_without_ua_stays_supported(self):
        cookie = "koa:sess=legacy==; koa:sess.sig=legacy-sign;"
        os.environ["RAILGUN_INFO_COOKIES"] = "test|" + cookie
        config = checkin.Config()
        self.assertEqual(len(config.tasks), 1)
        self.assertEqual(config.tasks[0].cookie, cookie)
        self.assertEqual(config.tasks[0].domain, "railgun.info")
        self.assertEqual(config.tasks[0].user_agent, checkin.DEFAULT_USER_AGENT)

    def test_ua_follows_account_name_and_domain(self):
        os.environ.update({
            "GLADOS_ROCKS_COOKIES": "a|gld:sess=A; gld:sess.sig=AS\nb|gld:sess=B; gld:sess.sig=BS",
            "GLADOS_ROCKS_USER_AGENTS": "b|Browser B/2\na|Browser A/1",
            "RAILGUN_INFO_COOKIES": "a|koa:sess=R; koa:sess.sig=RS",
            "RAILGUN_INFO_USER_AGENTS": "a|Railgun Browser/3",
        })
        config = checkin.Config()
        self.assertEqual(
            [(x.domain, x.account_name, x.user_agent) for x in config.tasks],
            [("glados.rocks", "a", "Browser A/1"),
             ("glados.rocks", "b", "Browser B/2"),
             ("railgun.info", "a", "Railgun Browser/3")],
        )

    def test_ua_ampersand_is_not_an_account_separator(self):
        os.environ["GLADOS_ROCKS_COOKIES"] = "a|gld:sess=A; gld:sess.sig=AS"
        os.environ["GLADOS_ROCKS_USER_AGENTS"] = "a|Browser & Device/1"
        self.assertEqual(checkin.Config().tasks[0].user_agent, "Browser & Device/1")

    def test_old_ampersand_records_still_work(self):
        records = checkin.Config._parse_named_values("a|plan0&b|plan100", "TEST")
        self.assertEqual(records, {"a": "plan0", "b": "plan100"})

    def test_duplicate_ua_rejected_without_value_in_error(self):
        os.environ["GLADOS_ROCKS_COOKIES"] = "a|gld:sess=A; gld:sess.sig=AS"
        os.environ["GLADOS_ROCKS_USER_AGENTS"] = "a|private-value-1\na|private-value-2"
        with self.assertRaises(ValueError) as error:
            checkin.Config()
        self.assertNotIn("private-value", str(error.exception))

    def test_json_cookie_rejected_without_value_in_error(self):
        with self.assertRaises(ValueError) as error:
            checkin.Config._normalize_cookie('[{"name":"gld:sess","value":"private-cookie="}]')
        self.assertNotIn("private-cookie", str(error.exception))

    def test_missing_gld_warns_but_does_not_rewrite_cookie(self):
        cookie = "koa:sess=old; koa:sess.sig=sign;"
        os.environ["GLADOS_ROCKS_COOKIES"] = "test|" + cookie
        with self.assertLogs(level="WARNING") as logs:
            config = checkin.Config()
        self.assertEqual(config.tasks[0].cookie, cookie)
        self.assertTrue(any("gld:sess" in line for line in logs.output))
        self.assertNotIn("koa:sess=old", "\n".join(logs.output))

    def test_request_uses_exact_cookie_ua_and_current_domain_token(self):
        account = task()
        with checkin.API(account) as api:
            with patch.object(api.session, "request", return_value=response({"code": 0})) as request:
                result = api.checkin()
            self.assertEqual(api.session.headers["user-agent"], account.user_agent)
        self.assertEqual(result["code"], checkin.CheckinStatus.SUCCESS)
        self.assertEqual(request.call_args.kwargs["headers"]["cookie"], account.cookie)
        self.assertEqual(request.call_args.kwargs["url"], "https://glados.rocks/api/user/checkin")
        self.assertEqual(request.call_args.kwargs["json"], {"token": "glados.rocks"})

    def test_status_auth_failure_stops_before_checkin(self):
        result, request = self.run_task([response({"code": -2, "message": "没有权限"})])
        self.assertEqual(result.code, checkin.CheckinStatus.FAILURE)
        self.assertIn("认证失败", result.detail)
        self.assertEqual(request.call_count, 1)

    def test_http_auth_failure_stops_before_checkin(self):
        for status in (401, 403):
            with self.subTest(status=status):
                result, request = self.run_task([response(status=status)])
                self.assertEqual(result.code, checkin.CheckinStatus.FAILURE)
                self.assertIn(str(status), result.detail)
                self.assertEqual(request.call_count, 1)

    def test_device_mismatch_stops_points_and_exchange(self):
        result, request = self.run_task([
            response({"code": 0, "data": {"leftDays": 5}}),
            response({"code": 4, "reason": "device-mismatch", "loginDevice": "private-device"}),
        ], task(plan="plan500"))
        self.assertEqual(result.code, checkin.CheckinStatus.FAILURE)
        self.assertIn("device-mismatch", result.detail)
        self.assertNotIn("private-device", result.detail)
        self.assertEqual(request.call_count, 2)

    def test_automated_rejection_does_not_claim_device_mismatch(self):
        result, request = self.run_task([
            response({"code": 0}),
            response({"code": 4, "message": "Automated check-in detected. private-data"}),
        ])
        self.assertIn("向服务方确认原因", result.detail)
        self.assertNotIn("device-mismatch", result.detail)
        self.assertNotIn("private-data", result.detail)
        self.assertEqual(request.call_count, 2)

    def test_unknown_failure_is_sanitized_and_stops_followups(self):
        result, request = self.run_task([
            response({"code": 0}),
            response({"code": 7, "message": "private-response", "reason": "private-reason"}),
        ])
        self.assertIn("code=7", result.detail)
        self.assertNotIn("private", result.detail)
        self.assertEqual(request.call_count, 2)

    def test_success_and_plan0_never_exchange(self):
        result, request = self.run_task([
            response({"code": 0}), response({"code": 0, "points": 1}),
            response({"code": 0, "points": 600}),
        ])
        self.assertEqual(result.code, checkin.CheckinStatus.SUCCESS)
        self.assertEqual(result.exchange, "未兑换（plan0）")
        self.assertEqual(request.call_count, 3)

    def test_repeat_is_normal_and_keeps_account_plan(self):
        result, request = self.run_task([
            response({"code": 0}), response({"code": 1, "message": "Checkin Repeats! Please Try Tomorrow"}),
            response({"code": 0, "points": 99}),
        ], task(plan="plan100"))
        self.assertEqual(result.code, checkin.CheckinStatus.REPEAT)
        self.assertIn("积分不足", result.exchange)
        self.assertEqual(request.call_count, 3)

    def test_successful_exchange_still_uses_own_plan(self):
        result, request = self.run_task([
            response({"code": 0}), response({"code": 0}),
            response({"code": 0, "points": 500}), response({"code": 0}),
        ], task(plan="plan500"))
        self.assertEqual(result.exchange, "兑换成功（plan500）")
        self.assertEqual(request.call_args.kwargs["json"], {"planType": "plan500"})

    def test_failed_account_does_not_stop_next_domain(self):
        config = checkin.Config()
        config.tasks = [task(), task("railgun.info", name="second")]
        with patch("requests.sessions.Session.request", side_effect=[
            response({"code": -2}), response({"code": 0}),
            response({"code": 0}), response({"code": 0, "points": 2}),
        ]) as request:
            checker = checkin.Checker(config)
            checker.run_all()
        self.assertEqual([r.code for r in checker.results], [checkin.CheckinStatus.FAILURE, checkin.CheckinStatus.SUCCESS])
        self.assertEqual(request.call_count, 4)

    def test_verbose_and_http_error_logs_do_not_dump_body(self):
        with checkin.API(task(), verbose=True) as api:
            with patch.object(api.session, "request", side_effect=[
                response({"code": 0, "private": "private-response"}),
                response({"private": "private-error"}, status=403),
            ]), self.assertLogs(level="INFO") as logs:
                api.get_status()
                api.checkin()
        self.assertNotIn("private-response", "\n".join(logs.output))
        self.assertNotIn("private-error", "\n".join(logs.output))

    def test_invalid_header_exception_does_not_dump_cookie(self):
        with checkin.API(task()) as api:
            with patch.object(api.session, "request", side_effect=requests.exceptions.InvalidHeader("private-cookie")), self.assertLogs(level="ERROR") as logs:
                api.checkin()
        self.assertNotIn("private-cookie", "\n".join(logs.output))

    def test_main_returns_failure_for_mixed_results(self):
        config = checkin.Config()
        config.tasks = [task()]
        checker = checkin.Checker(config)
        checker.results = [checkin.CheckinResult("a", "glados.rocks"), checkin.CheckinResult("b", "railgun.info", code=checkin.CheckinStatus.SUCCESS)]
        with patch("checkin.Config", return_value=config), patch("checkin.Checker", return_value=checker), patch.object(checker, "run_all"):
            self.assertEqual(checkin.main(), 1)

    def test_main_returns_success_for_success_and_repeat(self):
        config = checkin.Config()
        config.tasks = [task()]
        checker = checkin.Checker(config)
        checker.results = [checkin.CheckinResult("a", "glados.rocks", code=checkin.CheckinStatus.SUCCESS), checkin.CheckinResult("b", "railgun.info", code=checkin.CheckinStatus.REPEAT)]
        with patch("checkin.Config", return_value=config), patch("checkin.Checker", return_value=checker), patch.object(checker, "run_all"):
            self.assertEqual(checkin.main(), 0)


if __name__ == "__main__":
    unittest.main()
