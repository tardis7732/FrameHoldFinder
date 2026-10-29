"""Publish JSON reports without modifying a protected video or partial report."""
import json
import os
from pathlib import Path
import tempfile


def validate_report_path(path, protected_paths=()):
    target = Path(path)
    if target.is_dir():
        raise ValueError('보고서 경로가 폴더입니다. JSON 파일 경로를 선택하세요.')
    for protected in protected_paths or ():
        if protected is None:
            continue
        protected = Path(protected)
        if target.resolve() == protected.resolve() or (
            target.exists() and protected.exists() and target.samefile(protected)
        ):
            raise ValueError('보고서를 원본 또는 출력 영상 경로에 저장할 수 없습니다.')
    return target


def write_report(path, data, protected_paths=()):
    protected_paths = tuple(protected_paths or ())
    target = validate_report_path(path, protected_paths)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    fd, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        validate_report_path(target, protected_paths)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
