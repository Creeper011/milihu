"""Entry point for the Milihu Discord bot."""

import os

from dotenv import load_dotenv

from milihu.app.app import MilihuApp
from milihu.services.cobalt.runtime import CobaltRuntime
from milihu.services.nodelink.runtime import NodeLinkRuntime
from milihu.types.default.env import DEFAULT_ENV_PATH
from milihu.types.default.prefix import DEFAULT_PREFIX


def main() -> None:
    """Load the Discord token and start the Nix-defined Cobalt and NodeLink runtimes."""
    load_dotenv(DEFAULT_ENV_PATH)

    token = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing required environment variable: TOKEN")

    prefix = os.environ.get("PREFIX", DEFAULT_PREFIX)
    cobalt_image = os.environ.get("COBALT_IMAGE")
    if not cobalt_image:
        raise RuntimeError("COBALT_IMAGE is missing; start the bot inside nix-shell")
    nodelink_image = os.environ.get("NODELINK_IMAGE")
    if not nodelink_image:
        raise RuntimeError("NODELINK_IMAGE is missing; start the bot inside nix-shell")

    media_backend = CobaltRuntime(cobalt_image)
    audio_backend = NodeLinkRuntime(
        nodelink_image,
        spotify_client_id=os.environ.get("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET"),
    )
    MilihuApp(
        token=token,
        prefix=prefix,
        media_backend=media_backend,
        audio_backend=audio_backend,
    ).run()


if __name__ == "__main__":
    main()
