"""Entry point for the Milihu Discord bot."""

import os

from dotenv import load_dotenv

from milihu.app.app import MilihuApp
from milihu.services.cobalt.runtime import CobaltRuntime
from milihu.types.default.env import DEFAULT_ENV_PATH
from milihu.types.default.prefix import DEFAULT_PREFIX


def main() -> None:
    """Load the Discord token and start the Nix-defined Cobalt runtime."""
    load_dotenv(DEFAULT_ENV_PATH)

    token = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing required environment variable: TOKEN")

    prefix = os.environ.get("PREFIX", DEFAULT_PREFIX)
    image = os.environ.get("COBALT_IMAGE")
    if not image:
        raise RuntimeError("COBALT_IMAGE is missing; start the bot inside nix-shell")
    media_backend = CobaltRuntime(image)
    MilihuApp(token=token, prefix=prefix, media_backend=media_backend).run()


if __name__ == "__main__":
    main()
