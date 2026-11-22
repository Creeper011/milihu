import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from milihu.milihu import main


class EntrypointTests(unittest.TestCase):
    def test_loads_discord_token_from_dotenv(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("TOKEN=test-token\n")
            with (
                patch.dict("os.environ", {"COBALT_IMAGE": "pinned-image"}, clear=True),
                patch("milihu.milihu.DEFAULT_ENV_PATH", env_path, create=True),
                patch("milihu.milihu.MilihuApp") as app_class,
            ):
                main()

        self.assertEqual(app_class.call_args.kwargs["token"], "test-token")
        self.assertEqual(
            app_class.call_args.kwargs["media_backend"].image, "pinned-image"
        )
