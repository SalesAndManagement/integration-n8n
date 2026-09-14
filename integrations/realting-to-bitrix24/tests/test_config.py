import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from realting_sync.config import Config, ConfigError, load_env_file

BASE_ENV = {
    "REALTING_EXPORT_URL": "https://realting.com/api/orders",
    "REALTING_API_TOKEN": "secret",
    "BITRIX_WEBHOOK_URL": "https://portal.bitrix24.ua/rest/1/hooktoken/",
}


class ConfigTest(unittest.TestCase):
    def test_minimal_env_is_enough(self):
        config = Config.from_env(env=BASE_ENV, env_file="/nonexistent")
        self.assertEqual(config.realting.auth_mode, "bearer")
        self.assertEqual(config.bitrix.webhook_url, "https://portal.bitrix24.ua/rest/1/hooktoken")
        self.assertEqual(config.bitrix.external_id_field, "UF_CRM_REALTING_ID")

    def test_missing_url_is_allowed_for_webhook_mode(self):
        env = {k: v for k, v in BASE_ENV.items() if k != "REALTING_EXPORT_URL"}
        config = Config.from_env(env={**env, "WEBHOOK_TOKEN": "s3cret"}, env_file="/nonexistent")
        self.assertEqual(config.realting.url, "")
        self.assertEqual(config.webhook.token, "s3cret")

    def test_webhook_defaults(self):
        config = Config.from_env(env=BASE_ENV, env_file="/nonexistent")
        self.assertEqual(config.webhook.path, "/realting/webhook")
        self.assertEqual(config.webhook.host, "127.0.0.1")
        self.assertEqual(config.webhook.auth_mode, "query")

    def test_webhook_path_must_be_absolute(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={**BASE_ENV, "WEBHOOK_PATH": "realting"}, env_file="/nonexistent")

    def test_unknown_webhook_auth_mode_is_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={**BASE_ENV, "WEBHOOK_AUTH_MODE": "magic"}, env_file="/nonexistent")

    def test_missing_token_is_rejected_unless_auth_none(self):
        env = {k: v for k, v in BASE_ENV.items() if k != "REALTING_API_TOKEN"}
        with self.assertRaises(ConfigError):
            Config.from_env(env=env, env_file="/nonexistent")
        config = Config.from_env(env={**env, "REALTING_AUTH_MODE": "none"}, env_file="/nonexistent")
        self.assertEqual(config.realting.auth_mode, "none")

    def test_bad_webhook_shape_is_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={**BASE_ENV, "BITRIX_WEBHOOK_URL": "https://portal.bitrix24.ua"}, env_file="/nonexistent")

    def test_unknown_auth_mode_is_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={**BASE_ENV, "REALTING_AUTH_MODE": "magic"}, env_file="/nonexistent")

    def test_non_numeric_int_is_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_env(env={**BASE_ENV, "SYNC_OVERLAP_MINUTES": "п'ять"}, env_file="/nonexistent")

    def test_extra_params_parsing(self):
        config = Config.from_env(env={**BASE_ENV, "REALTING_EXTRA_PARAMS": "format=json, status=new"}, env_file="/nonexistent")
        self.assertEqual(config.realting.extra_params, {"format": "json", "status": "new"})

    def test_env_file_is_read_and_overridden_by_process_env(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sync.env"
            path.write_text(
                "\n".join([
                    "# коментар",
                    "REALTING_EXPORT_URL=https://from-file.example/api",
                    'REALTING_API_TOKEN="quoted-secret"',
                    "BITRIX_WEBHOOK_URL=https://portal.bitrix24.ua/rest/1/hook/",
                    "BITRIX_ASSIGNED_BY_ID=9",
                ]),
                encoding="utf-8",
            )
            config = Config.from_env(env={"BITRIX_ASSIGNED_BY_ID": "12"}, env_file=str(path))
            self.assertEqual(config.realting.url, "https://from-file.example/api")
            self.assertEqual(config.realting.token, "quoted-secret")
            self.assertEqual(config.bitrix.assigned_by_id, 12)   # оточення має пріоритет

    def test_load_env_file_missing_returns_empty(self):
        self.assertEqual(load_env_file("/nonexistent/file.env"), {})


if __name__ == "__main__":
    unittest.main()
