"""Entry point for the Milihu Discord bot."""

import os

from dotenv import load_dotenv

from milihu.app.app import MilihuApp
from milihu.services.cobalt.client import Cobalt
from milihu.services.nodelink.client import NodeLinkClient
from milihu.types.default.env import DEFAULT_ENV_PATH
from milihu.types.default.prefix import DEFAULT_PREFIX


def main() -> None:
    """Load configuration and connect to the configured backend services."""
    load_dotenv(DEFAULT_ENV_PATH)

    token = os.environ.get("TOKEN") or os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing required environment variable: TOKEN")

    cobalt_url = os.environ.get("COBALT_URL")
    if not cobalt_url:
        raise RuntimeError("Missing required environment variable: COBALT_URL")
    nodelink_url = os.environ.get("NODELINK_URL")
    if not nodelink_url:
        raise RuntimeError("Missing required environment variable: NODELINK_URL")
    nodelink_password = os.environ.get("NODELINK_PASSWORD")
    if not nodelink_password:
        raise RuntimeError("Missing required environment variable: NODELINK_PASSWORD")

    MilihuApp(
        token=token,
        prefix=os.environ.get("PREFIX", DEFAULT_PREFIX),
        media_backend=Cobalt(cobalt_url, api_key=os.environ.get("COBALT_API_KEY")),
        audio_backend=NodeLinkClient(nodelink_url, nodelink_password),
    ).run()


if __name__ == "__main__":
    main()
