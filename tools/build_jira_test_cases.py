"""Validate and package built-in inputs without reading databases or integrations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain import jira_test_catalog as catalog
from app.domain import jira_test_package as package
from app.domain import jira_testing as domain


def build(root, output):
    entries = catalog.sources(root)
    hashes = set()
    for key, raw in entries:
        body = package.decode(raw)
        digest = domain.digest(body['input'])
        if digest in hashes:
            raise ValueError('内置测试输入重复')
        hashes.add(digest)
    output.mkdir(parents=True, exist_ok=True)
    index = {'format': 'jira-test-catalog', 'version': 1, 'cases': []}
    # Publish the index last; all listed packages are already complete.
    with tempfile.TemporaryDirectory(dir=output, prefix='.build-') as temporary:
        stage = Path(temporary)
        for key, raw in entries:
            name = key + '.zip'
            (stage / name).write_bytes(raw)
            os.replace(stage / name, output / name)
            index['cases'].append({'key': key, 'sha256': hashlib.sha256(raw).hexdigest()})
        (stage / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
        os.replace(stage / 'index.json', output / 'index.json')
    return len(entries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'app/resources/jira_test_cases')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    count = build(args.source, args.output or args.source / '_packages')
    print(f'已校验并打包 {count} 个内置测试用例。')
