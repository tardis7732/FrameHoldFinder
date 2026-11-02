"""Source ↔ Change timing matching for a single forward-playing shot.

Port of FrameMatch-for-Nuke/src/matcher.cpp, b3fcfffb (MIT, copyright
2026 tardis7732, see LICENSE). The desktop decoder samples the two SDR clips
on the descriptor grid. No spatial transform is estimated or applied.
"""

from pathlib import Path
from operator import index
import subprocess
import tempfile
import threading

import cv2
import numpy as np

import hold_core as core
from reference_geometry import width_overlap_boxes


WIDTH, HEIGHT = 192, 108
TILE_W, TILE_H = 16, 12
GRID_W, GRID_H = WIDTH // TILE_W, HEIGHT // TILE_H
INNER = np.arange(GRID_H * GRID_W).reshape(GRID_H, GRID_W)[1:-1, 1:-1].ravel()
MAX_PAIRS = 1_000_000
MAX_TOTAL_FRAMES = 4096
NO_DETAIL_COST = 10.


class ReferenceReview(dict):
    """JSON-compatible report, with an in-memory score matrix for the GUI."""

    def __init__(self, report, costs, automatic_change):
        super().__init__(report)
        # Scoring is complete: reuse the matrix allocation. A sentinel from the
        # matcher means too few textured tiles for this pair, not a distance.
        costs[costs == NO_DETAIL_COST] = np.nan
        costs.setflags(write=False)
        self.candidate_costs = costs
        self.automatic_change = tuple(automatic_change)


def validate_anchors(anchors, source_frames=None, change_frames=None):
    """Return sorted, strictly forward, one-based (Source, Change) pairs."""
    pairs = []
    try:
        for source, change in anchors:
            if isinstance(source, (bool, np.bool_)) or isinstance(change, (bool, np.bool_)):
                raise TypeError
            pairs.append((index(source), index(change)))
    except (TypeError, ValueError) as error:
        raise ValueError('Each anchor must be a pair of integer frame numbers (Source, Change).') from error
    pairs.sort()
    previous_source = previous_change = 0
    for source, change in pairs:
        if source < 1 or change < 1:
            raise ValueError('Anchor frame numbers start at 1.')
        if ((source_frames is not None and source > source_frames) or
                (change_frames is not None and change > change_frames)):
            raise ValueError('Anchor frame number is outside the decoded clip.')
        if source <= previous_source or change <= previous_change:
            raise ValueError('Anchors must use unique Source and Change frames in forward order.')
        previous_source, previous_change = source, change
    return tuple(pairs)


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise core.Cancelled()


def _file_stat(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _decode(path, meta, box, limit, progress, cancel):
    # Decode frames in display order without manufacturing CFR duplicates.
    # RGB sampling makes the matching luma coefficients independent of the YUV matrix.
    _, top, _, bottom = box
    sampling = f'scale={WIDTH}:{HEIGHT}:flags=area'
    if top or bottom != meta['height']:
        # Keep odd rows and offsets on subsampled clips when cropping the overlap.
        sampling = f'crop=iw:{bottom-top}:0:{top}:exact=1,' + sampling
    args = [core.binary('ffmpeg'), '-v', 'error', '-nostdin', '-noautorotate', '-i', str(path),
            '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', sampling,
            '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb48le', 'pipe:1']
    frames = []
    frame_bytes = WIDTH * HEIGHT * 6
    done = threading.Event()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, creationflags=core.FLAGS)

        def interrupt_decoder():
            while not done.wait(.1):
                if cancel.is_set():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return

        watcher = threading.Thread(target=interrupt_decoder, daemon=True)
        watcher.start()
        try:
            while True:
                _check_cancel(cancel)
                data = process.stdout.read(frame_bytes)
                _check_cancel(cancel)
                if not data:
                    break
                if len(data) != frame_bytes:
                    raise RuntimeError('Incomplete decoded video frame.')
                if len(frames) >= limit:
                    raise ValueError('Shot too long: maximum 1,000,000 frame pairs and 4,096 combined frames.')
                rgb = np.frombuffer(data, dtype='<u2').reshape(HEIGHT, WIDTH, 3)
                gray = cv2.cvtColor(rgb.astype(np.float32) / 65535, cv2.COLOR_RGB2GRAY)
                frames.append(gray)
                progress(min(1., len(frames) / max(1, meta['estimated'])))
            code = process.wait()
            _check_cancel(cancel)
            if code:
                errors.seek(0)
                raise RuntimeError(errors.read().decode('utf-8', 'replace')[-1500:] or 'Video decoding failed.')
        finally:
            done.set()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            watcher.join()
    if not frames:
        raise ValueError('No video frames decoded.')
    return frames


def _tiles(image):
    channels = image.shape[2:]
    return (image.reshape(GRID_H, TILE_H, GRID_W, TILE_W, *channels)
            .swapaxes(1, 2).reshape(GRID_H * GRID_W, -1))


def _feature(gray):
    # Reflect-101 and radius ceil(4*sigma) match the upstream separable filters.
    g = cv2.GaussianBlur(gray, (7, 7), .7, borderType=cv2.BORDER_REFLECT_101)
    mean = cv2.GaussianBlur(g, (25, 25), 3., borderType=cv2.BORDER_REFLECT_101)
    mean_square = cv2.GaussianBlur(g * g, (25, 25), 3., borderType=cv2.BORDER_REFLECT_101)
    var = np.maximum(mean_square - mean * mean, 0)
    noise_floor = np.sqrt(var + .002)
    contrast = np.clip((g - mean) / np.sqrt(var + .0004), -3., 3.)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) / noise_floor
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) / noise_floor
    values = _tiles(np.stack((contrast, gx, gy), axis=-1))
    values -= values.mean(axis=1, keepdims=True)
    values /= np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-6)
    texture = np.minimum(1., _tiles(np.sqrt(var)).mean(axis=1) / .04)
    return values, texture, g


def _sequence(frames, progress, cancel):
    count = len(frames)
    # Tile-first layout lets OpenCV multiply whole frame blocks without creating
    # a temporary [Change, Source, tile, component] tensor. Border descriptors
    # only affect the motion-weight percentile and need not all stay in memory.
    descriptors = np.empty((len(INNER), count, TILE_W * TILE_H * 3), np.float32)
    weights = np.empty((count, len(INNER)), np.float32)
    lumas = np.empty((count, HEIGHT - 2*TILE_H, WIDTH - 2*TILE_W), np.float32)
    motion = np.zeros(GRID_W * GRID_H, np.float32)
    previous = None
    for i, gray in enumerate(frames):
        _check_cancel(cancel)
        values, texture, blurred = _feature(gray)
        descriptors[:, i] = values[INNER]
        weights[i] = texture[INNER]
        lumas[i] = blurred[TILE_H:-TILE_H, TILE_W:-TILE_W]
        if previous is not None:
            motion += np.linalg.norm(values - previous, axis=1) / (count - 1)
        previous = values
        frames[i] = None  # Release decoded sample storage as features are built.
        progress((i + 1) / count)
    if count == 1:
        motion.fill(1.)
    ref = max(float(np.sort(motion)[int(.75 * (len(motion)-1))]), 1e-5)
    return descriptors, weights, lumas, np.clip(motion[INNER] / ref, .15, 1.)


def _costs(source, change, progress, cancel, allow_untextured=False):
    a, aw, _, motion = source
    b, bw, _, _ = change
    m, n = len(bw), len(aw)
    costs = np.empty((m, n), np.float64)
    # Bound the sorting buffers on asymmetric and near-limit shots.
    batch = min(64, max(1, 16384 // n))
    for first in range(0, m, batch):
        _check_cancel(cancel)
        stop = min(m, first + batch)
        weight = np.minimum(bw[first:stop, None, :], aw[None, :, :]) * motion
        valid = weight > .02
        distance = np.empty(weight.shape, np.float32)
        for t in range(len(INNER)):
            dots = cv2.gemm(b[t, first:stop], a[t], 1., None, 0., flags=cv2.GEMM_2_T)
            distance[..., t] = np.clip(1. - dots, 0, 2)
        # The C++ matcher discards low-detail tiles, then averages the best 75%.
        distance[~valid] = 3.
        order = np.argsort(distance, axis=2, kind='stable')
        valid_count = valid.sum(axis=2)
        keep = np.maximum(8, valid_count * 3 // 4)
        weighted = np.take_along_axis(weight, order, axis=2)
        weighted *= np.arange(len(INNER)) < keep[:, :, None]
        summed = (np.take_along_axis(distance, order, axis=2) * weighted).sum(axis=2, dtype=np.float64)
        costs[first:stop] = np.where(valid_count >= 8, summed / np.maximum(weighted.sum(axis=2), 1e-12), NO_DETAIL_COST)
        # In a manually constrained shot, a textureless row keeps its high cost
        # and is flagged for review unless the user explicitly anchored it.
        if not allow_untextured and not np.all(np.any(valid_count >= 8, axis=1)):
            raise ValueError('Insufficient textured image detail for reliable frame matching.')
        progress(stop / m)
    return costs


def _forward(cost, penalty, with_back, cancel):
    m, n = cost.shape
    scores = np.empty_like(cost)
    scores[0] = cost[0]
    back = np.empty((m, n), np.int32) if with_back else None
    pos = np.arange(n, dtype=np.int32)
    for j in range(1, m):
        _check_cancel(cancel)
        previous = scores[j-1]
        best = previous + penalty  # Map consecutive Change frames to the same Source.
        pred = pos.copy()
        advance = previous[:-1] < best[1:]
        np.copyto(best[1:], previous[:-1], where=advance)
        np.copyto(pred[1:], pos[:-1], where=advance)
        if n > 2:
            offset = previous[:-2] - penalty * pos[:-2]
            prefix = np.minimum.accumulate(offset)
            jumps = prefix + penalty * (pos[2:] - 1)
            skip = jumps < best[2:]
            np.copyto(best[2:], jumps, where=skip)
            # Keep the first minimum on ties, as in fm::forward.
            minima = np.r_[True, prefix[1:] < prefix[:-1]]
            prefix_index = np.maximum.accumulate(np.where(minima, pos[:-2], 0))
            np.copyto(pred[2:], prefix_index, where=skip)
        scores[j] = best + cost[j]
        if with_back:
            back[j] = pred
    return scores, back


def _solve(cost, cancel=None, anchors=()):
    cost = np.asarray(cost, dtype=np.float64)
    m, n = cost.shape
    row = np.arange(m)
    mins = cost.argmin(axis=1)
    selected = cost[row, mins]
    neighbors = np.r_[np.abs(cost[row[mins > 0], mins[mins > 0]-1] - selected[mins > 0]),
                      np.abs(cost[row[mins < n-1], mins[mins < n-1]+1] - selected[mins < n-1])]
    unit = max(float(np.median(neighbors)) if neighbors.size else .01, .0001)
    penalty = .15 * unit
    # Constrain both dynamic-programming passes; other Change rows can still
    # map to the same Source when the edit contains duplicates. The original
    # finite costs remain available for the path margin and image review.
    constrained = cost.copy() if anchors else cost
    for source, change in anchors:
        constrained[change-1] = np.inf
        constrained[change-1, source-1] = cost[change-1, source-1]
    forward, back = _forward(constrained, penalty, True, cancel)
    path = np.empty(m, np.int32)
    path[-1] = forward[-1].argmin()
    for j in range(m-1, 0, -1):
        path[j-1] = back[j, path[j]]
    backward, _ = _forward(constrained[::-1, ::-1], penalty, False, cancel)
    combined = forward + backward[::-1, ::-1] - cost
    chosen = combined[row, path]
    combined[row, path] = np.inf
    margins = np.maximum(0, combined.min(axis=1) - chosen) if n > 1 else np.zeros(m)
    # A sole path allowed by manual constraints has no visual alternative to
    # score. Unpinned rows in that position should be reviewed, not rated 100%.
    confidence = np.divide(margins, margins + unit, out=np.zeros_like(margins), where=np.isfinite(margins))
    best = np.full(n, -1, np.int32)
    for j, i in enumerate(path):
        if best[i] < 0 or cost[j, i] < cost[best[i], i]:
            best[i] = j
    for source, change in anchors:
        best[source-1] = change-1
    matched = np.flatnonzero(best >= 0)
    positions = np.arange(n)
    insertion = np.searchsorted(matched, positions)
    right = matched[np.minimum(insertion, len(matched)-1)]
    left = matched[np.maximum(insertion-1, 0)]
    nearest = np.where(positions-left <= right-positions, left, right)
    return path, best[nearest], best < 0, confidence


def _image_holds(source_luma, change_luma, inverse, missing, cancel=None):
    n = len(inverse)
    candidate = np.zeros(n, bool)
    output_mean = np.zeros(n, np.float64)
    for i in range(1, n):
        _check_cancel(cancel)
        diff = np.abs(change_luma[inverse[i]] - change_luma[inverse[i-1]]).ravel()
        mean = output_mean[i] = diff.mean(dtype=np.float64)
        if missing[i-1] or missing[i] or inverse[i] <= inverse[i-1] or mean > .006:
            continue
        index = int(.95 * (len(diff)-1))
        if np.partition(diff, index)[index] > .025:
            continue
        reference_mean = np.abs(source_luma[i] - source_luma[i-1]).mean(dtype=np.float64)
        candidate[i] = reference_mean >= .008 and mean <= .2 * reference_mean

    held = np.zeros(n, bool)
    boundaries = np.flatnonzero(np.diff(np.r_[False, candidate, False]))
    for start, stop in boundaries.reshape(-1, 2):
        nearby = np.r_[output_mean[max(1, start-4):start], output_mean[stop:stop+4]]
        if nearby.size:
            motion = np.sort(nearby)[int(.75 * (len(nearby)-1))]
            if motion >= .008:
                held[start:stop] = output_mean[start:stop] <= .2 * motion
    return held


def _analyze_images(source_path, change_path, progress, cancel, anchors=(), allow_untextured=False):
    _check_cancel(cancel)
    paths = [Path(p).resolve(strict=True) for p in (source_path, change_path)]
    initial = [_file_stat(path) for path in paths]
    metas = [core.probe(path) for path in paths]
    if any(meta['transfer'] in ('smpte2084', 'arib-std-b67') for meta in metas):
        raise ValueError('Reference timing analysis requires SDR review clips.')
    source_meta, change_meta = metas
    source_box, change_box = width_overlap_boxes(
        (source_meta['width'], source_meta['height']), (change_meta['width'], change_meta['height']),
        source_meta.get('sample_aspect_ratio'), change_meta.get('sample_aspect_ratio'))

    def update(fraction):
        _check_cancel(cancel)
        if progress is not None:
            progress(fraction)

    # Container estimates are for progress only; count the decoded frames to
    # enforce limits even on inaccurate MOV headers or long audio tracks.
    update(0.)
    raw_src = _decode(paths[0], source_meta, source_box, MAX_TOTAL_FRAMES-1, lambda p: update(.15*p), cancel)
    n = len(raw_src)
    raw_chg = _decode(paths[1], change_meta, change_box, min(MAX_TOTAL_FRAMES-n, MAX_PAIRS//n),
                      lambda p: update(.15+.15*p), cancel)
    m = len(raw_chg)
    validate_anchors(anchors, n, m)
    source = _sequence(raw_src, lambda p: update(.3+.125*p), cancel)
    change = _sequence(raw_chg, lambda p: update(.425+.125*p), cancel)
    cost = _costs(source, change, lambda p: update(.55+.4*p), cancel, allow_untextured=allow_untextured)
    info = dict(source_path=str(paths[0]), change_path=str(paths[1]), source_stat=initial[0], change_stat=initial[1],
                source_meta=metas[0], change_meta=metas[1], source_frames=n, change_frames=m)
    return info, cost, source[2], change[2]


def _finish_analysis(info, progress, cancel):
    _check_cancel(cancel)
    for clip in ('source', 'change'):
        if _file_stat(Path(info[f'{clip}_path'])) != info[f'{clip}_stat']:
            raise RuntimeError('A clip changed during analysis. Analyze again.')
    if progress is not None:
        progress(1.)


def prepare_candidate_costs(source_path, change_path, *, progress=None, cancel=None):
    """Measure edit × source image costs without solving a timing path."""
    cancel = cancel or threading.Event()
    info, cost, _, _ = _analyze_images(source_path, change_path, progress, cancel, allow_untextured=True)
    review = ReferenceReview(info, cost, [None] * info['source_frames'])
    _finish_analysis(info, progress, cancel)
    return review


def analyze_reference(source_path, change_path, *, progress=None, cancel=None, anchors=(), review_cache=False):
    """Return both directions of the 1-based frame map; Source sets the timeline.

    The Change frame on a missing Source row is the same nearest-frame placeholder
    used by FrameMatch's TimeWarp, not a claimed correspondence. The review score
    is the upstream path margin, not a calibrated probability. Each optional
    (Source, Change) anchor uses 1-based frame numbers and pins both mappings.
    With review_cache, candidate_costs[change-1, source-1] is the actual image
    cost and automatic_change[source-1] is the original, unconstrained choice.
    """
    cancel = cancel or threading.Event()
    _check_cancel(cancel)
    anchors = validate_anchors(anchors)
    info, cost, source_luma, change_luma = _analyze_images(
        source_path, change_path, progress, cancel, anchors, allow_untextured=bool(anchors))
    m, n = cost.shape
    path, inverse, missing, confidence = _solve(cost, cancel, anchors)
    if progress is not None:
        progress(.98)
    image_held = _image_holds(source_luma, change_luma, inverse, missing, cancel)
    selected = cost[np.arange(m), path]
    reviews = (confidence < .25) | (selected > .45)
    pinned_source = {s-1 for s, _ in anchors}
    pinned_change = {c-1 for _, c in anchors}
    for s, c in anchors:
        image_held[s-1] = False
        reviews[c-1] = False
    rows = []
    for i, j in enumerate(inverse):
        status = 'missing' if missing[i] else 'held' if image_held[i] else 'review' if reviews[j] else 'matched'
        rows.append(dict(source_frame=i+1, change_frame=int(j)+1, matched_source_frame=int(path[j])+1,
                         status=status, review=bool(not missing[i] and reviews[j]),
                         confidence=None if missing[i] else float(confidence[j])))
        if i in pinned_source:
            rows[-1].update(manual=True, confidence=None)
    change_rows = []
    for j, i in enumerate(path):
        step = int(i - path[j-1]) if j else 1
        event = 'start' if j == 0 else 'hold_candidate' if step == 0 else 'drop_candidate' if step > 1 else 'normal'
        change_rows.append(dict(change_frame=j+1, source_frame=int(i)+1, source_step=step, event=event,
                                confidence=float(confidence[j]), cost=float(selected[j]), review=bool(reviews[j])))
        if j in pinned_change:
            change_rows[-1].update(manual=True, confidence=None)
    info.update(rows=rows, change_rows=change_rows, source_to_change=(inverse+1).tolist(),
                change_to_source=(path+1).tolist(),
                manual_anchors=[dict(source_frame=s, change_frame=c) for s, c in anchors])
    if review_cache:
        if anchors:
            # Only repeat the cheap timing solve. Both passes share the exact
            # same decoded pixels, descriptors and pairwise image costs.
            _, inverse, missing, _ = _solve(cost, cancel)
            image_held = _image_holds(source_luma, change_luma, inverse, missing, cancel)
        unavailable = missing | image_held | (cost[inverse, np.arange(n)] == NO_DETAIL_COST)
        markers = (None if unavailable[i] else int(j)+1 for i,j in enumerate(inverse))
        info = ReferenceReview(info, cost, markers)
    _finish_analysis(info, progress, cancel)
    return info
