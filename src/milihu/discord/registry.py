"""Cog dependency registry."""

from milihu.discord.cogs.download import DownloadCog
from milihu.discord.cogs.gif import GifCog
from milihu.services.downloader_service import DownloaderService
from milihu.services.faces.media import MediaBackend
from milihu.services.togif_service import FfmpegGifConverter
from milihu.types.registry import CogRegistry


def build_cog_registry(media_backend: MediaBackend | None) -> CogRegistry:
    return {
        GifCog: lambda bot: GifCog(bot, converter=FfmpegGifConverter()),
        DownloadCog: lambda bot: DownloadCog(bot, DownloaderService(media_backend)),
    }
