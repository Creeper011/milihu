"""Default HTTP media fetching and audio probing constants."""

REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 512 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; MilihuBot/1.0)"
MIME_TO_EXTENSION: dict[str, str] = {
    "image/gif": ".gif",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}
VIDEO_META_KEYS: tuple[str, ...] = (
    "og:video:secure_url",
    "og:video:url",
    "og:video",
    "twitter:player:stream",
)
IMAGE_META_KEYS: tuple[str, ...] = (
    "og:image:secure_url",
    "og:image:url",
    "og:image",
    "twitter:image:src",
    "twitter:image",
)
HTML_EXTENSIONS: frozenset[str] = frozenset({".html", ".htm", ".php", ".aspx"})
GENERIC_STEMS: frozenset[str] = frozenset({"media", "video", "image", "gif", "file"})

MAX_ICY_METAINT_BYTES = 256 * 1024
HLS_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    }
)
DIRECT_AUDIO_CONTENT_TYPES: frozenset[str] = HLS_CONTENT_TYPES | {"application/ogg"}
EXCLUDED_AUDIO_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "audio/x-scpls",
        "audio/x-mpegurl",
    }
)
GENERIC_STREAM_PATH_NAMES: frozenset[str] = frozenset(
    {"stream", "live", "radio", "listen"}
)
MAX_STREAM_REDIRECTS = 5
