import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from milihu.milihu import main


class EntrypointTests(unittest.TestCase):
    def test_connects_to_backends_from_environment(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "TOKEN=test-token\nCOBALT_URL=http://media:9000/\n"
                "NODELINK_URL=http://audio:2333/\nNODELINK_PASSWORD=audio-secret\n"
            )
            with (
                patch.dict("os.environ", {}, clear=True),
                patch("milihu.milihu.DEFAULT_ENV_PATH", env_path),
                patch("milihu.milihu.MilihuApp") as app_class,
            ):
                main()
        kwargs = app_class.call_args.kwargs
        self.assertEqual(kwargs["token"], "test-token")
        self.assertEqual(kwargs["media_backend"].base_url, "http://media:9000/")
        self.assertEqual(kwargs["audio_backend"].base_url, "http://audio:2333/")
        self.assertEqual(kwargs["audio_backend"]._password, "audio-secret")

    def test_missing_backend_settings_fail_before_starting_bot(self) -> None:
        for missing in ("COBALT_URL", "NODELINK_URL", "NODELINK_PASSWORD"):
            with self.subTest(missing=missing):
                environment = {
                    "TOKEN": "test-token",
                    "COBALT_URL": "http://media:9000/",
                    "NODELINK_URL": "http://audio:2333/",
                    "NODELINK_PASSWORD": "secret",
                }
                del environment[missing]
                with (
                    patch.dict("os.environ", environment, clear=True),
                    patch("milihu.milihu.load_dotenv"),
                    patch("milihu.milihu.MilihuApp") as app_class,
                ):
                    with self.assertRaisesRegex(RuntimeError, missing):
                        main()
                    app_class.assert_not_called()
