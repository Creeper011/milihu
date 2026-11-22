"""Cog dependency registry."""

from milihu.discord.cogs.caption import CaptionCog
from milihu.discord.cogs.download import DownloadCog
from milihu.discord.cogs.gif import GifCog
from milihu.discord.cogs.music import MusicCog
from milihu.services.discord_voice import DiscordVoiceManager
from milihu.services.ffmpeg.caption_renderer import FfmpegCaptionRenderer
from milihu.services.ffmpeg.gif_renderer import FfmpegGifRenderer
from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.services.http.http_fetcher import AiohttpFetcher
from milihu.services.http.remote_media import HttpRemoteMediaFetcher
from milihu.services.http.stream_probe import StreamProbe
from milihu.services.litterbox import Litterbox
from milihu.services.youtube_playlist import YouTubePlaylistResolver
from milihu.types.registry import CogRegistry
from milihu.usecases.caption_service import CaptionService
from milihu.usecases.downloader_service import DownloaderService
from milihu.usecases.faces.media import MediaBackend
from milihu.usecases.music_service import MusicPlayerService
from milihu.usecases.togif_service import GifConverterService


def build_cog_registry(media_backend: MediaBackend | None) -> CogRegistry:
    ffmpeg = FfmpegRunner()
    remote_media = HttpRemoteMediaFetcher()
    return {
        CaptionCog: lambda bot: CaptionCog(
            bot,
            captioner=CaptionService(
                FfmpegCaptionRenderer(ffmpeg),
                remote_fetcher=remote_media,
            ),
        ),
        GifCog: lambda bot: GifCog(
            bot,
            converter=GifConverterService(
                FfmpegGifRenderer(ffmpeg),
                remote_fetcher=remote_media,
            ),
        ),
        DownloadCog: lambda bot: DownloadCog(
            bot,
            DownloaderService(
                media_backend,
                uploader=Litterbox(),
                fetcher=AiohttpFetcher(),
            ),
        ),
        MusicCog: lambda bot: MusicCog(
            bot,
            player=MusicPlayerService(
                DiscordVoiceManager(bot),
                media_backend,
                stream_probe=StreamProbe(),
                playlist_resolver=YouTubePlaylistResolver(),
            ),
        ),
    }
