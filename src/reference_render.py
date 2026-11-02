"""Render Change imagery on the Source timeline from a FrameMatch timing result."""
import threading

from preview_file import RenderedPreview as ReferencePreview, render_preview_file
import sdk_bridge
from sdk_worker import validate_output_paths


class ManualRangeError(ValueError):
    """Validation reason shared by the range editor and the render planner."""
    def __init__(self, code):
        self.code = code
        super().__init__({
            'bounds': 'Choose two original frames in order with at least one frame between them.',
            'overlap': 'Added ranges may share an endpoint but cannot overlap.',
            'edit_order': 'The endpoints must map to two distinct edited frames in forward order.',
            'pinned': 'The range contains a manually pinned frame match.',
            'edit_bounds': 'Choose two edited frames in forward order within the edit.',
            'unmapped_edit': 'No source position is available for this edited frame.',
            'ambiguous': 'Choose a source position for each ambiguous edited frame.',
            'mapping': 'The selected source position no longer corresponds to this edit.',
            'source_span': 'There must be at least one output frame between the selected positions.',
            'shared_anchor': 'Ranges sharing an output endpoint must use the same edit image there.',
            'not_direct': 'Select an endpoint that uses a specific edit image.',
        }[code])


def _edit_source_index(analysis):
    # Use the forward path first: multiple edited frames can land on a single
    # source frame, and only one of them survives the inverse lookup.
    positions = {}
    if 'change_to_source' in analysis:
        forward = enumerate(analysis['change_to_source'], 1)
    else:
        forward = ((r['change_frame'], r['source_frame']) for r in analysis.get('change_rows', ()))
    for edit, source in forward:
        positions[edit] = {source: None}
    for row in analysis['rows']:
        positions.setdefault(row['change_frame'], {})[row['source_frame']] = None
    return positions


def edit_source_candidates(analysis, edit):
    """Source positions for this edit, including nearest-image placeholders after direct matches."""
    if type(edit) is not int or not 1 <= edit <= analysis['change_frames']:
        return ()
    return tuple(_edit_source_index(analysis).get(edit, ()))


def resolve_edit_range(analysis, left_change, right_change, *, left_source=None, right_source=None):
    """Resolve user-selected edited images into a fixed, source-timed export span."""
    if (type(left_change) is not int or type(right_change) is not int
            or not 1 <= left_change < right_change <= analysis['change_frames']):
        raise ManualRangeError('edit_bounds')
    positions = _edit_source_index(analysis)
    def resolve(edit, source):
        options = positions.get(edit)
        if not options:
            raise ManualRangeError('unmapped_edit')
        if source is None:
            if len(options) != 1:
                raise ManualRangeError('ambiguous')
            return next(iter(options))
        if type(source) is not int or source not in options:
            raise ManualRangeError('mapping')
        return source
    left, right = resolve(left_change, left_source), resolve(right_change, right_source)
    if right - left < 2:
        raise ManualRangeError('source_span')
    item = dict(left=left, right=right, left_change=left_change, right_change=right_change)
    _manual_ranges(analysis, item, positions)
    return item


def resolve_source_row_range(analysis, first_source, second_source):
    """Capture the actual two edit images on the rows the operator trusts."""
    if any(type(s) is not int or not 1 <= s <= analysis['source_frames']
           for s in (first_source, second_source)):
        raise ManualRangeError('bounds')
    left, right = sorted((first_source, second_source))
    if right - left < 2:
        raise ManualRangeError('source_span')
    actions = describe_reference_frames(analysis)
    item = dict(left=left, right=right)
    for side, source in (('left', left), ('right', right)):
        action = actions[source-1]
        if action['kind'] not in ('use', 'override'):
            raise ManualRangeError('not_direct')
        edit = action['change_frame']
        item[side + '_change'] = edit
        if 'override_base' in action:
            # Keep the confirmed image as a reference even if the separate
            # one-slot override is cleared. Validate its original match on rematch.
            item[side + '_override_base'] = action['override_base']
        elif action['kind'] == 'override':
            base = analysis['rows'][source-1]['change_frame']
            if edit != base:
                item[side + '_override_base'] = base
    _manual_ranges(analysis, item)
    return item


def _manual_ranges(analysis, proposed=None, positions=None):
    ranges = analysis.get('manual_ranges', ())
    if not isinstance(ranges, (list, tuple)):
        raise ManualRangeError('bounds')
    if proposed is not None:
        ranges = [*ranges, proposed]
    if not ranges:
        return []
    rows = analysis['rows']
    pinned = {row['source_frame'] for row in rows if row.get('manual')}
    pinned.update(pair['source_frame'] for pair in analysis.get('manual_anchors', ()))
    validated = []
    for item in ranges:
        if not isinstance(item, dict):
            raise ManualRangeError('bounds')
        left, right = item.get('left'), item.get('right')
        if type(left) is not int or type(right) is not int:
            raise ManualRangeError('bounds')
        if not (1 <= left < right <= len(rows)) or right - left < 2:
            raise ManualRangeError('bounds')
        a, b = rows[left - 1]['change_frame'], rows[right - 1]['change_frame']
        if 'left_change' in item or 'right_change' in item:
            a, b = item.get('left_change'), item.get('right_change')
            if type(a) is not int or type(b) is not int or not 1 <= a < b <= analysis['change_frames']:
                raise ManualRangeError('edit_bounds')
            if positions is None:
                positions = _edit_source_index(analysis)
            for side, source, edit in (('left', left, a), ('right', right, b)):
                base_key = side + '_override_base'
                if base_key in item:
                    if type(item[base_key]) is not int or item[base_key] != rows[source-1]['change_frame']:
                        raise ManualRangeError('mapping')
                    continue
                if source not in positions.get(edit, ()):
                    raise ManualRangeError('mapping')
                if source in pinned and rows[source-1]['change_frame'] != edit:
                    raise ManualRangeError('pinned')
        if a >= b:
            raise ManualRangeError('edit_order')
        if any(left < frame < right for frame in pinned):
            raise ManualRangeError('pinned')
        validated.append((left, right, a, b))
    validated.sort()
    for (_, prev_right, _, prev_b), (left, _, a, _) in zip(validated, validated[1:]):
        if left < prev_right:
            raise ManualRangeError('overlap')
        if left == prev_right and a != prev_b:
            raise ManualRangeError('shared_anchor')
    return validated


def validate_reference_range(analysis, left, right):
    """Check one proposed source span against the current analysis and added ranges."""
    _manual_ranges(analysis, {'left': left, 'right': right})


class FrameOverrideError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__({
            'bounds': 'Choose an existing source frame and an existing edited frame.',
            'duplicate': 'A source frame can have only one output override.',
        }[code])


def validate_frame_override(analysis, source_frame, change_frame):
    """An output override can use any edited frame, including one already used elsewhere."""
    if (type(source_frame) is not int or not 1 <= source_frame <= analysis['source_frames']
            or type(change_frame) is not int or not 1 <= change_frame <= analysis['change_frames']):
        raise FrameOverrideError('bounds')


def _frame_overrides(analysis):
    items = analysis.get('frame_overrides', ())
    if not isinstance(items, (list, tuple)):
        raise FrameOverrideError('bounds')
    overrides = {}
    for item in items:
        if not isinstance(item, dict):
            raise FrameOverrideError('bounds')
        source, change = item.get('source_frame'), item.get('change_frame')
        validate_frame_override(analysis, source, change)
        if source in overrides:
            raise FrameOverrideError('duplicate')
        overrides[source] = change
    return overrides


def plan_reference(analysis):
    """Turn one-based correspondences into the integer lookup and bounded fills.

    Gap endpoint rules follow FrameMatch-for-Nuke's frame_match_rife.gaps.
    Review-only rows and missing edge frames keep their mapped Change image.
    """
    rows = analysis['rows']
    source_frames = analysis['source_frames']
    change_frames = analysis['change_frames']
    if not rows or len(rows) != source_frames or change_frames < 1:
        raise ValueError('Frame-match rows must cover the entire original timeline.')
    lookup = []
    for index, row in enumerate(rows, 1):
        frame = row['change_frame']
        if type(row['source_frame']) is not int or row['source_frame'] != index:
            raise ValueError('Original frame numbers must be consecutive, starting at 1.')
        if type(frame) is not int or not 1 <= frame <= change_frames or (lookup and frame < lookup[-1]):
            raise ValueError('Edited frame mapping is out of range or runs backwards.')
        if row['status'] not in ('matched', 'review', 'missing', 'held'):
            raise ValueError('Unknown frame-match status.')
        lookup.append(frame)

    generate = [row['status'] in ('missing', 'held') for row in rows]
    manual_refs = {}
    endpoints = {}
    for left, right, a, b in _manual_ranges(analysis):
        # The endpoints stay mapped images, including boundaries shared with an automatic gap.
        generate[left - 1] = generate[right - 1] = False
        generate[left:right - 1] = [True] * (right - left - 1)
        if (a, b) != (lookup[left-1], lookup[right-1]):
            manual_refs[(left, right)] = (a, b)
            for source, edit in ((left, a), (right, b)):
                if lookup[source-1] != edit:
                    endpoints[source] = edit

    overrides = _frame_overrides(analysis)
    runs = []
    unresolved = []
    index = 0
    while index < source_frames:
        if not generate[index]:
            index += 1
            continue
        first = index
        while index < source_frames and generate[index]:
            index += 1
        # first and index are also the 1-based left anchor and final target.
        targets = [frame for frame in range(first + 1, index + 1) if frame not in overrides]
        if not targets:
            continue
        refs = manual_refs.get((first, index + 1))
        if first == 0 or index == source_frames or (refs is None and lookup[first - 1] >= lookup[index]):
            unresolved.extend(targets)
        else:
            # Keep the full span on partial overrides so all other targets keep their fractions.
            run = {'start': first, 'end': index, 'generated': index - first}
            if refs is not None:
                run.update(left_change=refs[0], right_change=refs[1])
            runs.append(run)
    plan = {'lookup': lookup, 'runs': runs, 'unresolved_frames': unresolved}
    if endpoints:
        plan['range_endpoints'] = [{'source_frame': s, 'change_frame': e} for s, e in sorted(endpoints.items())]
    if overrides:
        plan['frame_overrides'] = [{'source_frame': s, 'change_frame': c} for s, c in sorted(overrides.items())]
    return plan


def describe_reference_frames(analysis):
    """Describe the actual export action for each one-based Source frame, in order."""
    plan = plan_reference(analysis)
    lookup = plan['lookup']
    unresolved = set(plan['unresolved_frames'])
    actions = [
        {'kind': 'fallback' if source in unresolved else 'use',
         'source_frame': source, 'change_frame': change}
        for source, change in enumerate(lookup, 1)
    ]
    for run in plan['runs']:
        left = run['start']
        right = run['end'] + 1
        for source in range(left + 1, right):
            actions[source - 1] = {
                'kind': 'generate', 'source_frame': source,
                'left_source': left, 'right_source': right,
                'left_change': run.get('left_change', lookup[left - 1]),
                'right_change': run.get('right_change', lookup[right - 1]),
                'step': source - left, 'count': run['generated'],
            }
    for endpoint in plan.get('range_endpoints', ()):
        actions[endpoint['source_frame'] - 1]['change_frame'] = endpoint['change_frame']
    for item in analysis.get('manual_ranges', ()):
        left, right = item['left'], item['right']
        for side, source in (('left', left), ('right', right)):
            action = actions[source-1]
            action['manual_anchor'] = True
            if side + '_override_base' in item:
                action['override_base'] = item[side + '_override_base']
        for source in range(left + 1, right):
            actions[source - 1]['manual'] = True
    for item in plan.get('frame_overrides', ()):
        actions[item['source_frame'] - 1] = {'kind': 'override', **item}
    return actions


def render_reference(analysis, output, *, progress=None, cancel=None, quality='HIGH'):
    """Apply the timing lookup, synthesize bounded gaps and copy original audio."""
    original, output = validate_output_paths(analysis['source_path'], output)
    edited, _ = validate_output_paths(analysis['change_path'], output)
    plan = plan_reference(analysis)
    if quality not in ('LOW', 'MEDIUM', 'HIGH'):
        raise ValueError('Unknown interpolation quality.')
    timing = analysis['source_meta']
    image = analysis['change_meta']
    request = {
        'source': str(edited), 'source_stat': analysis.get('change_stat'),
        'reference_path': str(original), 'reference_stat': analysis.get('source_stat'),
        'output': str(output), 'width': image['width'], 'height': image['height'],
        'frames': analysis['source_frames'], 'fps': timing['fps'],
        'fps_fraction': timing.get('fps_fraction'), 'quality': quality,
        'lookup': plan['lookup'], 'runs': plan['runs'],
        'preserve_frame_count': True, 'format_mode': 'source',
    }
    for key in ('range_endpoints', 'frame_overrides'):
        if key in plan:
            request[key] = plan[key]
            request['change_frames'] = analysis['change_frames']
    def on_frame(frame, total):
        if progress is not None:
            progress(frame / total)

    result = sdk_bridge.run_render_job(request, on_frame, cancel or threading.Event(),
                                       needs_sdk=bool(plan['runs']))
    result['unresolved_frames'] = plan['unresolved_frames']
    result['skipped'] = len(plan['unresolved_frames'])
    return result


def render_reference_preview(analysis, *, progress=None, cancel=None, quality='HIGH'):
    """Render the current export plan into a temporary movie on the Source timeline."""
    def render_to(path):
        render_reference(analysis, path, progress=progress, cancel=cancel, quality=quality)

    return render_preview_file(analysis['change_path'], render_to, cancel)
