"""Console entry point; shares detection and fixed-length repair logic with the UI."""
import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

import hold_core as core
import motion_core as motion
from motion_settings import default_min_move
from report_io import validate_report_path, write_report
import sdk_bridge
import source_format


VERSION = '1.0'


class UsageError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def finite_number(minimum, maximum=None, inclusive=True):
    def parse(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError('expected a finite number') from None
        if not math.isfinite(number) or (number < minimum if inclusive else number <= minimum):
            raise argparse.ArgumentTypeError(f'expected a finite number {">=" if inclusive else ">"} {minimum:g}')
        if maximum is not None and number > maximum:
            raise argparse.ArgumentTypeError(f'expected a number <= {maximum:g}')
        return number
    return parse


def bounded_integer(minimum, maximum):
    def parse(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError('expected a whole number') from None
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f'expected a whole number from {minimum} to {maximum}')
        return number
    return parse


def manual_range(value):
    match = re.fullmatch(r'(\d+):(\d+)', value)
    if not match:
        raise argparse.ArgumentTypeError('use LEFT:RIGHT with 1-based original frame numbers, e.g. 3:6')
    left, right = map(int, match.groups())
    if left < 1 or right - left < 2:
        raise argparse.ArgumentTypeError('anchors must be positive and contain at least one frame between them')
    return dict(left=left, right=right)


def excluded_key(value):
    match = re.fullmatch(r'(hold|jump|replace):(\d+):(\d+)', value)
    if not match:
        raise argparse.ArgumentTypeError('use a candidate key from analyze, e.g. jump:3:4 or replace:3:6')
    kind, left, right = match.groups(); left, right = int(left), int(right)
    if left < 1 or right <= left or (kind == 'jump' and right != left + 1) or (kind == 'replace' and right - left < 2):
        raise argparse.ArgumentTypeError('invalid candidate frame range')
    return f'{kind}:{left}:{right}'


def parser():
    result = Parser(prog='FrameHoldFinder-CLI',
                    description='Detect motion holds and jumps, then replace selected frames without changing clip length.')
    result.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    commands = result.add_subparsers(dest='command', required=True, parser_class=Parser)
    for name, description in (
        ('analyze', 'Analyze a video and print candidate keys and a fixed-length repair plan as JSON.'),
        ('render', 'Analyze and render selected replacements using NVIDIA Video Effects SDK.')):
        sub = commands.add_parser(name, help=description, description=description)
        sub.add_argument('video', help='Source video path (never modified).')
        sub.add_argument('--width', type=bounded_integer(32, 16384), default=960, help='Analysis width in pixels; capped at source width (default: 960).')
        sub.add_argument('--threshold', '--jump-threshold', dest='threshold', type=finite_number(0), default=5., help='Speed increase above BOTH neighboring intervals, percent (default: 5).')
        sub.add_argument('--hold-quiet', type=finite_number(0), default=.5, help='Hold movement tolerance in source pixels (default: 0.5).')
        sub.add_argument('--min-move', type=finite_number(0, inclusive=False), help='Minimum reference movement in source pixels; default: actual analysis width x 0.005.')
        sub.add_argument('--support-percent', type=finite_number(1, 100), default=30., help='Required share of eligible tracked points (default: 30).')
        sub.add_argument('--min-points', type=bounded_integer(1, 10000), default=20, help='Required tracked point count (default: 20).')
        sub.add_argument('--no-holds', action='store_true', help='Disable automatic hold detection.')
        sub.add_argument('--no-jumps', action='store_true', help='Disable automatic jump detection.')
        sub.add_argument('--add', action='append', type=manual_range, default=[], metavar='LEFT:RIGHT', help='Replace the existing frames strictly between two original anchors. Repeatable; does not insert frames.')
        sub.add_argument('--exclude', action='append', type=excluded_key, default=[], metavar='KEY', help='Exclude a candidate key returned by analyze. Repeatable.')
        sub.add_argument('--manual-only', action='store_true', help='Skip motion tracking; count source frames and use only --add ranges.')
        sub.add_argument('--report', metavar='JSON', help='Also write the JSON result atomically to this file.')
        sub.add_argument('--quiet', action='store_true', help='Suppress progress on stderr.')
        if name == 'render':
            sub.add_argument('-o', '--output', required=True, help='Output video; existing output may be replaced after validation. Cannot be the source.')
            sub.add_argument('--format', choices=('source', 'prores'), default='source', help='Preserve supported source encoding format (default), or explicitly encode ProRes MOV.')
            sub.add_argument('--quality', choices=('LOW', 'MEDIUM', 'HIGH'), default='HIGH', help='NVIDIA interpolation quality (default: HIGH).')
            sub.add_argument('--dry-run', action='store_true', help='Return the repair plan without invoking the SDK or writing a video.')
    sdk = commands.add_parser('sdk-check', help='Check the configured NVIDIA SDK Python environment.')
    sdk.add_argument('--python', metavar='EXE', help='Check this SDK Python executable instead of the saved one.')
    sdk.add_argument('--save', action='store_true', help='Save --python for both desktop and CLI after a successful check.')
    return result


_ERRORS = {
    '현재 분석은 SDR 영상용입니다.': 'Analysis currently supports SDR video only.',
    '영상 스트림이 없습니다.': 'The source has no video stream.',
    '프레임 속도를 읽지 못했습니다.': 'The source frame rate could not be read.',
    '디코딩된 프레임이 없습니다.': 'No video frames were decoded.',
    '분석 중 원본이 변경됐습니다.': 'The source changed during analysis. Analyze it again.',
    '수동 추가 구간은 겹칠 수 없습니다. 양끝 프레임만 공유할 수 있습니다.': 'Manual ranges cannot overlap; only a shared endpoint is allowed.',
    '영상 범위 안에서 교체할 프레임이 포함되도록 양끝을 선택하세요.': 'Manual anchors must be within the source and contain at least one frame to replace.',
    '보고서 경로가 폴더입니다. JSON 파일 경로를 선택하세요.': 'The report path is a directory. Choose a JSON file.',
    '보고서를 원본 또는 출력 영상 경로에 저장할 수 없습니다.': 'The report cannot overwrite or alias the source or output video.',
    'NVIDIA SDK 환경을 선택해 주세요.': 'Configure an NVIDIA SDK Python environment using sdk-check --python PATH --save.',
    'SDK Python 실행 파일을 찾을 수 없습니다. 경로를 다시 선택하세요.': 'The SDK Python executable does not exist. Configure its path again.',
    'SDK Python 실행 파일이 없습니다.': 'The SDK Python executable does not exist.',
    'SDK 확인이 실패했습니다.': 'The SDK environment check failed.',
    '수동 추가 구간과 겹침': 'Overlaps a manual range or its protected anchors.',
    '사용자가 제외한 홀드 구간입니다.': 'This hold was excluded.',
    '앞뒤 기준 프레임이 없어 교체할 수 없습니다.': 'Replacement requires an original reference frame on both sides.',
    '제외한 프레임과 겹쳐 홀드 교체를 보류합니다.': 'Hold replacement is deferred because it overlaps an excluded frame.',
    '제외되거나 보류된 홀드가 기준 프레임에 있어 교체를 보류합니다.': 'Replacement is deferred because a reference belongs to an excluded or deferred hold.',
    '제외하거나 보류한 프레임과 겹쳐 교체를 보류합니다.': 'Replacement is deferred because it overlaps an excluded or deferred frame.',
}


def error_text(error):
    message = str(error)
    return _ERRORS.get(message, message)


def same_path(left, right):
    return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))


def preflight(args):
    source = Path(args.video).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f'Source video does not exist: {source}')
    output = None
    if args.command == 'render':
        output = Path(args.output).expanduser().resolve()
        if output.is_dir():
            raise ValueError('The output path is a directory.')
        if same_path(source, output):
            raise ValueError('Output cannot overwrite or alias the source video.')
        if not output.parent.is_dir():
            raise ValueError(f'Output directory does not exist: {output.parent}')
        expected = source.suffix.lower() if args.format == 'source' else '.mov'
        if not expected or output.suffix.lower() != expected:
            raise ValueError(f'Output extension must be {expected or "the source container extension"} for --format {args.format}.')
    report = Path(args.report).expanduser().resolve() if args.report else None
    if report is not None:
        validate_report_path(report, [source, output])
        if not report.parent.is_dir():
            raise ValueError(f'Report directory does not exist: {report.parent}')
    # Detect obvious overlap before decoding. Final bounds use actual frame count.
    if args.add:
        motion.validate_manual_ranges(args.add, max(r['right'] for r in args.add))
    if args.manual_only and not args.add:
        raise ValueError('--manual-only requires at least one --add LEFT:RIGHT range.')
    return source, output, report


def manual_analysis(source, width):
    initial = source.stat()
    metadata = core.probe(source)
    if metadata['transfer'] in ('smpte2084', 'arib-std-b67'):
        raise ValueError('Manual replacement currently supports SDR video only.')
    # nb_frames/format duration may be missing or include longer audio. Count the
    # actual video frames without tracking so manual bounds are never estimated.
    process = subprocess.run([core.binary('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
        '-count_frames', '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(source)],
        capture_output=True, creationflags=core.FLAGS)
    if process.returncode:
        raise RuntimeError(process.stderr.decode('utf-8', 'replace')[-1500:])
    streams = json.loads(process.stdout).get('streams', [])
    value = streams[0].get('nb_read_frames', '') if streams else ''
    if not str(value).isdigit() or int(value) < 1:
        raise ValueError('Could not determine the exact source video frame count.')
    current = source.stat()
    if (initial.st_size, initial.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
        raise RuntimeError('The source changed while its frames were counted.')
    actual_width = min(width, metadata['width'])
    return dict(path=str(source), metadata=metadata, frames=int(value), steps=[],
        analysis_width=actual_width, analysis_height=max(1, round(metadata['height'] * actual_width / metadata['width'])),
        manual_only=True, source_stat=dict(size=current.st_size, mtime_ns=current.st_mtime_ns))


class Progress:
    def __init__(self, phase, quiet=False):
        self.phase, self.quiet, self.last = phase, quiet, 0.

    def __call__(self, frame, total):
        now = time.monotonic()
        if not self.quiet and (now - self.last >= .5 or frame == total):
            print(json.dumps(dict(event='progress', phase=self.phase, frame=frame, total=total)), file=sys.stderr, flush=True)
            self.last = now


def plan(args, source, cancel):
    analysis = manual_analysis(source, args.width) if args.manual_only else motion.scan(
        source, width=args.width, progress=Progress('analyze', args.quiet), cancel=cancel)
    ranges = motion.validate_manual_ranges(args.add, analysis['frames'])
    for key in args.exclude:
        if int(key.rsplit(':', 1)[1]) > analysis['frames']:
            raise ValueError(f'Excluded key is outside the source frame range: {key}')
    automatic_minimum = default_min_move(analysis['analysis_width'])
    minimum = automatic_minimum if args.min_move is None else args.min_move
    holds = not args.no_holds and not args.manual_only
    jumps = not args.no_jumps and not args.manual_only
    model = motion.prepare(analysis, holds, args.hold_quiet, minimum,
        excluded=args.exclude, support_percent=args.support_percent, min_points=args.min_points, manual_ranges=ranges)
    rows = motion.candidates(model, args.threshold, jumps)
    runs = motion.render_runs(rows)
    clean_rows = [dict(row, reason=error_text(row.get('reason', ''))) for row in rows]
    result = dict(version=VERSION, command=args.command, status='analyzed',
        source=str(source), mode='fixed_frame_replacement', frames=analysis['frames'],
        fps=analysis['metadata']['fps'], fps_fraction=analysis['metadata']['fps_fraction'],
        width=analysis['metadata']['width'], height=analysis['metadata']['height'],
        analysis_options=dict(width=analysis['analysis_width'], threshold=args.threshold,
            hold_quiet=args.hold_quiet, min_move=minimum, min_move_auto=args.min_move is None,
            support_percent=args.support_percent, min_points=args.min_points,
            holds=holds, jumps=jumps, manual_only=args.manual_only),
        manual_ranges=ranges, excluded=sorted(set(args.exclude)),
        candidate_count=len(rows), replacement_frames=len({frame for run in runs for frame in run['targets']}),
        frame_count_change=0, candidates=clean_rows, repair_runs=runs,
        metrics=[dict(start=m['start'], end=m['end'], score=m['score'], raw_score=m['raw_score'],
            uses_prediction=m['uses_prediction'], quiet=m['quiet'], occupied=m['occupied'],
            eligible_points=m['stats']['points'], required_points=m['stats']['required_points']) for m in model['metrics']])
    unknown = sorted(set(args.exclude) - {row['key'] for row in rows})
    if unknown:
        result['warnings'] = ['Excluded keys not present in the current candidate list: ' + ', '.join(unknown)]
    return analysis, runs, result


def execute(args, cancel):
    if args.command == 'sdk-check':
        if args.save and not args.python:
            raise UsageError('--save requires --python EXE')
        executable = str(Path(args.python).expanduser().resolve()) if args.python else None
        checked = sdk_bridge.check(executable)
        if args.save:
            sdk_bridge.save_runtime(executable)
        return dict(version=VERSION, command='sdk-check', status='ready', saved=args.save,
                    python=executable or sdk_bridge.runtime(), sdk=checked)
    source, output, report = preflight(args)
    if args.command == 'render':
        source_format.plan_format(source_format.probe_format(core.binary('ffprobe'), source),
                                  source, output, mode=args.format)
    analysis, runs, result = plan(args, source, cancel)
    if args.command == 'render':
        result.update(output=str(output), format=args.format, quality=args.quality)
        if args.dry_run:
            result['status'] = 'dry_run'
        elif not runs:
            raise ValueError('No included, repairable frames. Adjust detection, add a manual range, or use --dry-run to inspect the plan.')
        else:
            result['render'] = sdk_bridge.repair(analysis, runs, output, args.quality,
                Progress('render', args.quiet), cancel, format_mode=args.format)
            result['status'] = 'rendered'
    if report is not None:
        write_report(report, result, protected_paths=[source, output])
    return result


def main(argv=None):
    # UTF-8 JSON works consistently in PowerShell redirection and on Korean paths.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    cancel = threading.Event()
    try:
        args = parser().parse_args(argv)
        result = execute(args, cancel)
        code = 0
    except (KeyboardInterrupt, core.Cancelled):
        cancel.set()
        result, code = dict(version=VERSION, status='cancelled', error='Cancelled.'), 130
    except Exception as error:
        result, code = dict(version=VERSION, status='error', error=error_text(error)), 2 if isinstance(error, UsageError) else 1
        print(f'Error: {result["error"]}', file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
