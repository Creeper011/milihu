ARG COBALT_IMAGE
FROM ${COBALT_IMAGE}

# The pinned Cobalt release ships an iOS client whose media URLs can reject
# requests mid-stream. Use its supported VISIONOS client with YouTube.js 18.1.
COPY --chown=node:node youtube-client/ /app/node_modules/cobalt-youtube-client/
RUN npm ci --prefix node_modules/cobalt-youtube-client --omit=dev --ignore-scripts --no-audit --no-fund \
    && ln -sfn cobalt-youtube-client/node_modules/youtubei.js node_modules/youtubei.js
ENV CUSTOM_INNERTUBE_CLIENT=VISIONOS

RUN <<'JS' node --input-type=module
import { readFileSync, writeFileSync } from 'node:fs';

function patch(path, edits) {
    let source = readFileSync(path, 'utf8');
    for (const [before, after] of edits) {
        if (source.split(before).length !== 2) {
            throw new Error(`The Cobalt patch does not match ${path}`);
        }
        source = source.replace(before, () => after);
    }
    writeFileSync(path, source);
}

// Match Cobalt's proposed VISIONOS support (imputnet/cobalt#1581).
patch('src/processing/services/youtube.js', [
    ["const clientsWithNoCipher = ['IOS',", "const clientsWithNoCipher = ['VISIONOS', 'IOS',"],
]);

// Node drains unconsumed child output on exit. Finish probing before spawning FFmpeg.
const spawnLine = '        process = spawn(...getCommand(args), {';
const probe = 'await estimateTunnelLength(streamInfo, estimateMultiplier)';
patch('src/stream/ffmpeg.js', [
    [probe, 'estimatedLength'],
    [spawnLine, '        const estimatedLength = ' + probe + ';\n' + spawnLine],
]);
JS
