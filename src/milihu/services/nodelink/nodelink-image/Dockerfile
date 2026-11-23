ARG NODELINK_IMAGE
FROM ${NODELINK_IMAGE}

# Seed config.ts and required directories so NodeLink runs cleanly with --read-only.
RUN cp /app/config.default.ts /app/config.ts \
    && mkdir -p /app/plugins /app/.nodelink /app/.cache /app/logs

# Force /v4/loadstream to always use sources.getTrackStream() + createAudioResource()
# (matching player.ts: YouTube chunked buffering & Deezer Blowfish decryption) and
# stream open-ended WAV without metadata-estimated Content-Length truncation.
RUN <<'JS' node --input-type=module
import { readFileSync, writeFileSync } from 'node:fs';

const path = '/app/dist/src/api/loadStream.js';
let source = readFileSync(path, 'utf8');

const edits = [
    [
        "        if (urlResult.url && !isHls && !isLocal && !isSabr) {",
        "        if (false) {",
    ],
    [
        "            'Accept-Ranges': isLiveStream ? 'none' : 'bytes',",
        "            'Accept-Ranges': 'none',",
    ],
    [
        "                    ? { 'Content-Length': String(totalFileSize) }",
        "                    ? {}",
    ],
    [
        "yield createWavHeader(isLiveStream ? null : totalPcmBytes);",
        "yield createWavHeader(null);",
    ],
];

for (const [before, after] of edits) {
    if (source.split(before).length !== 2) {
        throw new Error(`The NodeLink patch does not match ${path}: ${before}`);
    }
    source = source.replace(before, () => after);
}

writeFileSync(path, source);
JS
