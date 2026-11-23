"""Cog dependency registry."""

from types import MappingProxyType

from milihu.discord.cogs.about import AboutCog
from milihu.discord.cogs.caption import CaptionCog
from milihu.discord.cogs.download import DownloadCog
from milihu.discord.cogs.gif import GifCog
from milihu.discord.cogs.help import HelpCog
from milihu.discord.cogs.music import MusicCog
from milihu.discord.cogs.openpoll import OpenpollCog
from milihu.discord.types.help import CogHelpCategory, HelpCategoryRegistry
from milihu.discord.types.registry import CogRegistry
from milihu.services.discord_voice import DiscordVoiceManager
from milihu.services.ffmpeg.caption_renderer import FfmpegCaptionRenderer
from milihu.services.ffmpeg.gif_renderer import FfmpegGifRenderer
from milihu.services.ffmpeg.runner import FfmpegRunner
from milihu.services.github_project import GitHubProjectFetcher
from milihu.services.http.http_fetcher import AiohttpFetcher
from milihu.services.http.remote_media import HttpRemoteMediaFetcher
from milihu.services.http.stream_probe import StreamProbe
from milihu.services.litterbox import Litterbox
from milihu.types.help import HelpCategoryKey
from milihu.usecases.caption_service import CaptionService
from milihu.usecases.downloader_service import DownloaderService
from milihu.usecases.faces.audio import AudioBackend
from milihu.usecases.faces.media import MediaBackend
from milihu.usecases.music_service import MusicPlayerService
from milihu.usecases.project_service import ProjectService
from milihu.usecases.togif_service import GifConverterService

HELP_CATEGORIES: HelpCategoryRegistry = MappingProxyType(
    {
        HelpCategoryKey.MEDIA: CogHelpCategory(
            label="Media",
            emoji="🖼️",
            items=(CaptionCog, DownloadCog, GifCog),
        ),
        HelpCategoryKey.MUSIC: CogHelpCategory(
            label="Music",
            emoji="🎵",
            items=(MusicCog,),
        ),
        HelpCategoryKey.UTILITY: CogHelpCategory(
            label="Utility",
            emoji="🧩",
            items=(AboutCog, OpenpollCog, HelpCog),
        ),
    }
)


def build_cog_registry(
    media_backend: MediaBackend | None,
    audio_backend: AudioBackend | None = None,
) -> CogRegistry:
    ffmpeg = FfmpegRunner()
    remote_media = HttpRemoteMediaFetcher()
    return {
        AboutCog: lambda bot: AboutCog(
            bot,
            project=ProjectService(GitHubProjectFetcher()),
        ),
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
        HelpCog: lambda bot: HelpCog(bot, categories=HELP_CATEGORIES),
        MusicCog: lambda bot: MusicCog(
            bot,
            player=MusicPlayerService(
                DiscordVoiceManager(bot),
                audio_backend,
                stream_probe=StreamProbe(),
            ),
        ),
    }
