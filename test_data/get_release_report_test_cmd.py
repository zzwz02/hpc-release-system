"""
HPC 发布信息汇总脚本

功能：
    从 hpc_app.csv 读取 app 清单（CSV 文件名由命令行参数传入，列名称保持不变），
    再以每个 app 仓库的 app_info.json 为准，生成用于发版/测试的 CSV 报告，
    并把下载到的 app_info.json 打包归档。

工作流程：
    1. 从 CSV 读取清单；对字符串列做 strip 清洗。
    2. 将 CSV 列 `名称`, `git_url`, `git_branch` 转成内部使用的
       (app_name, git_url, git_branch) 清单；app_version 仅从 app_info.json
       顶层 `app_version` 字段读取，缺失时输出为空。
    3. 对每个 app：
         - 若 git_url 是短仓库名（如 hpc_hpl），先补全为 Gerrit SSH URL。
         - 若 git_url 以 .xml 结尾，先从 MANIFEST_REPO_URL@master 拉取该 XML，
           解析含 <linkfile src="app_info.json"/> 的 <project>，得到真实
           (repo_url, branch)；否则直接使用原值。
         - 通过 `git archive --remote=<url> <branch> app_info.json` 拉取并解析
           app_info.json；随后用 filter_app_info 预处理：
             * app_build：删除 enabled != true 的条目；
             * app_test ：删除 enabled != true 或 test_period == 'weekly' 的条目。
    4. release_df 行：
         - arch 来源于 app_info.app_build.*.arch（归一化到 arm64/amd64，
           再 denormalize 成 arm/x86 输出）。
         - 对每个 arch，chips = 该 arch 下所有 build entry 的 supported_chip
           并集，切成 maca_chip / hpcc_chip（x201 → hpcc）。
         - app_info 拉不到时，如果输入数据没有 arch/app_chip 兜底信息，则不生成
           release 行。
    5. test_df 行：仅当 app_info 拉取成功时生成；遍历过滤后的 app_test，
       按测试自身 supported_chip (`{chip: [build_key, ...]}`) 拆解出支持的
       arch 列表，每 (test, arch) 一行；拼接 `docker run --pull always --rm
       <container_args> [docker_image_<img_target>] sh -c '<test_cmd>'`。

输出：
    - hpc_release_report_{TARGET_VERSION}_{MMDD}.csv
        列：app_name, app_version, maca_chip, hpcc_chip, arch, maca_version,
            git_url, git_branch
    - hpc_test_cmd_{TARGET_VERSION}_{MMDD}.csv
        列：app_name, git_branch, app_version, arch, maca_version, test_name, docker_cmd
"""

import io
import os
import json
import argparse
import shlex
import subprocess
import tarfile
import tempfile
import datetime
import xml.etree.ElementTree as ET
import pandas as pd

# Global Configuration
today_date = datetime.date.today().strftime("%m%d")
TARGET_VERSION = "3.8.0"
MANIFEST_REPO_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/manifest"
MANIFEST_BRANCH = "master"
RESOLVED_REPO_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC"


def fetch_test_data(localhost, username, password, database):
    from sqlalchemy import create_engine

    db_uri = f"mysql+pymysql://{username}:{password}@{localhost}/{database}?charset=utf8"
    engine = create_engine(db_uri)
    try:
        # Modified SQL to filter maca_version starting with '3.'
        sql = f"""
            SELECT * FROM hpc_autobuild
            WHERE maca_version LIKE '{TARGET_VERSION}%%'
            ORDER BY app_name ASC, app_version DESC;
        """
        print(f"Executing SQL: {sql}")
        df_raw = pd.read_sql(sql, con=engine)

        if not df_raw.empty:
            # 解除 Pandas 打印时的最大列数限制
            pd.set_option('display.max_columns', None)
            # 可选: 解除最大宽度的限制，防止换行太乱
            pd.set_option('display.width', 1000)

            # 打印前 3 行数据看看
            # print(df_raw.head(3))

            # 打印完可以重置回去（避免影响后续其他地方的打印）
            pd.reset_option('display.max_columns')
            pd.reset_option('display.width')

        # Filter DataFrame to keep only specific columns
        target_columns = [
            'app_name', 'app_version', 'app_chip',
            'arch', 'maca_version', 'git_url', 'git_branch'
        ]

        # Check if columns exist before filtering to avoid errors
        available_cols = [c for c in target_columns if c in df_raw.columns]
        df_out = df_raw[available_cols].copy()
        # Strip whitespace from all string columns — some DB rows have trailing
        # spaces in git_url/git_branch which break downstream `git archive`.
        for col in df_out.select_dtypes(include=['object']).columns:
            df_out[col] = df_out[col].apply(
                lambda v: v.strip() if isinstance(v, str) else v
            )
        return df_out

    finally:
        engine.dispose()


def _strip_string_columns(df):
    """Strip whitespace from all string columns."""
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
    return df


def _is_absolute_git_url(git_url):
    value = str(git_url or '').strip()
    return (
        '://' in value
        or value.startswith('git@')
        or value.endswith('.xml')
    )


def normalize_git_url(git_url):
    """Convert CSV short repo names into full Gerrit SSH URLs."""
    value = str(git_url or '').strip()
    if not value:
        return value
    if _is_absolute_git_url(value):
        return value
    return f"{RESOLVED_REPO_BASE}/{value.lstrip('/')}"


def read_app_csv(csv_file):
    """Read hpc_app.csv without changing its column names on disk.

    Required input columns:
      - 名称
      - git_url
      - git_branch

    The returned DataFrame uses the internal schema consumed by the existing
    report builder.
    """
    df_raw = pd.read_csv(csv_file, encoding='utf-8-sig')
    df_raw = _strip_string_columns(df_raw)

    required_cols = ['名称', 'git_url', 'git_branch']
    missing = [c for c in required_cols if c not in df_raw.columns]
    if missing:
        raise ValueError(
            f"{csv_file} 缺少必要列: {', '.join(missing)}; "
            f"当前列: {', '.join(df_raw.columns)}"
        )

    df_out = pd.DataFrame({
        'app_name': df_raw['名称'],
        'app_version': '',
        'app_chip': df_raw.get('app_chip', ''),
        'arch': df_raw.get('arch', ''),
        'maca_version': TARGET_VERSION,
        'git_url': df_raw['git_url'].apply(normalize_git_url),
        'git_branch': df_raw['git_branch'],
    })
    df_out = _strip_string_columns(df_out)
    df_out = df_out[
        df_out['app_name'].astype(str).str.len().gt(0)
        & df_out['git_url'].astype(str).str.len().gt(0)
        & df_out['git_branch'].astype(str).str.len().gt(0)
    ].copy()
    return df_out.drop_duplicates(
        subset=['app_name', 'app_version', 'git_url', 'git_branch']
    ).reset_index(drop=True)


def process_and_merge(df):
    if df.empty:
        return df

    # 1. Standardize maca_version using the global variable
    df['maca_version'] = TARGET_VERSION

    # 2. Group and aggregate chips first to ensure we have the full list per app/arch
    df_merged = df.groupby(['app_name', 'app_version', 'arch', 'maca_version', 'git_url', 'git_branch'], as_index=False).agg({
        'app_chip': lambda x: ','.join(sorted(set(','.join(x).split(','))))
    })

    # 3. Define the split logic
    def split_chips(chip_str):
        chips = [c.strip() for c in chip_str.split(',')]
        maca_chips = [c for c in chips if c != 'x201']
        hpcc_chips = [c for c in chips if c == 'x201']
        return ','.join(maca_chips), ','.join(hpcc_chips)

    # 4. Apply the split to create two new columns
    df_merged[['maca_chip', 'hpcc_chip']] = df_merged['app_chip'].apply(
        lambda x: pd.Series(split_chips(x))
    )

    # 5. Reorder columns to your new requested format
    # Note: we drop 'app_chip' since it's now split into two
    column_order = [
        'app_name', 'app_version', 'maca_chip',
        'hpcc_chip', 'arch', 'maca_version', 'git_url', 'git_branch'
    ]

    return df_merged[column_order]


def _git_archive_extract(remote, branch, path, dest_dir):
    """Run `git archive --remote=<remote> <branch> <path> | tar -x -C <dest_dir>`.
    Returns True on success."""
    cmd = (
        f"git archive --remote={shlex.quote(remote)} "
        f"{shlex.quote(branch)} {shlex.quote(path)} | "
        f"tar -x -C {shlex.quote(dest_dir)}"
    )
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"  [warn] git archive failed:")
        print(f"    cmd   : {cmd}")
        print(f"    remote={remote!r}")
        print(f"    branch={branch!r}")
        print(f"    path  ={path!r}")
        print(f"    stderr: {result.stderr.strip()}")
        return False
    return True


def resolve_manifest_url(git_url, git_branch):
    """If git_url ends with .xml, fetch the manifest from MANIFEST_REPO_URL@master
    and resolve to the actual (repo_url, branch) of the project carrying
    <linkfile src="app_info.json"/>.

    Returns:
      (resolved_url, resolved_branch) — on success, or when input was not an .xml.
      (None, None) — when input was .xml but resolution failed; callers must skip
                     fetching app_info (otherwise the .xml path would be passed
                     to `git archive` as a repo URL, which always fails).
    """
    if not git_url or not git_url.endswith('.xml'):
        return git_url, git_branch

    xml_path = git_url.lstrip('/')
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            if not _git_archive_extract(MANIFEST_REPO_URL, MANIFEST_BRANCH, xml_path, tmpdir):
                return None, None
            xml_full = os.path.join(tmpdir, xml_path)
            if not os.path.exists(xml_full):
                print(f"  [warn] manifest xml missing after extract: {xml_path} "
                      f"(file does not exist in {MANIFEST_REPO_URL}@{MANIFEST_BRANCH})")
                return None, None

            root = ET.parse(xml_full).getroot()
            default_rev = ''
            default_elem = root.find('default')
            if default_elem is not None:
                default_rev = default_elem.get('revision', '')

            target_project = None
            for proj in root.findall('project'):
                for link in proj.findall('linkfile'):
                    if link.get('src') == 'app_info.json':
                        target_project = proj
                        break
                if target_project is not None:
                    break

            if target_project is None:
                print(f"  [warn] no project with app_info.json linkfile in {xml_path}")
                print(f"    available projects: {[p.get('name') for p in root.findall('project')]}")
                return None, None

            raw_name = target_project.get('name')
            raw_rev = target_project.get('revision')
            name = (raw_name or '').strip()
            revision = (raw_rev or default_rev or '').strip()
            if raw_name != name or (raw_rev or '') != revision:
                print(f"  [debug] manifest {xml_path}: stripped whitespace "
                      f"name {raw_name!r}->{name!r}, revision {raw_rev!r}->{revision!r}")
            resolved_url = f"{RESOLVED_REPO_BASE}/{name}"
            print(f"  [debug] resolved {xml_path} -> url={resolved_url!r} branch={revision!r}")
            return resolved_url, revision
    except Exception as e:
        print(f"  [warn] error resolving manifest {git_url}: {e}")
        return None, None


def fetch_app_info(git_url, git_branch):
    """Fetch and parse app_info.json from a remote git repo via `git archive`.

    Returns (parsed_dict, raw_bytes) on success, (None, None) on failure.
    raw_bytes is the exact file content from the remote, so callers can
    archive it verbatim alongside the parsed view.
    """
    if not git_url or not git_branch:
        return None, None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            if not _git_archive_extract(git_url, git_branch, 'app_info.json', tmpdir):
                return None, None
            app_info_path = os.path.join(tmpdir, 'app_info.json')
            if not os.path.exists(app_info_path):
                print(f"  [warn] app_info.json missing in {git_url}@{git_branch}")
                return None, None
            with open(app_info_path, 'rb') as f:
                raw = f.read()
            return json.loads(raw.decode('utf-8')), raw
    except Exception as e:
        print(f"  [warn] Error fetching app_info from {git_url}@{git_branch}: {e}")
        return None, None


def filter_app_info(app_info):
    """Pre-filter app_info before downstream use:
    - app_build: drop entries with enabled == false (keep enabled=true / missing).
    - app_test:  drop entries with enabled != true OR test_period == 'weekly'.

    Returns a new dict; original is not mutated.
    """
    if not isinstance(app_info, dict):
        return app_info
    out = dict(app_info)

    raw_build = app_info.get('app_build') or {}
    out['app_build'] = {
        k: v for k, v in raw_build.items()
        if isinstance(v, dict)
        and str(v.get('enabled', '')).strip().lower() != 'false'
    }

    raw_test = app_info.get('app_test') or {}
    out['app_test'] = {
        k: v for k, v in raw_test.items()
        if isinstance(v, dict) and v.get('enabled')
        and str(v.get('test_period', '')).strip().lower() != 'weekly'
    }

    return out


def _normalize_arch(a):
    """Normalize arch aliases so DB values (arm, x86) match JSON values
    (arm64, amd64). Returns lowercased string."""
    a = str(a or '').strip().lower()
    if a in ('arm', 'arm64', 'aarch64'):
        return 'arm64'
    if a in ('x86', 'x86_64', 'amd64'):
        return 'amd64'
    return a


def _denormalize_arch(a):
    """Map normalized arch (arm64/amd64) back to DB-style short names
    (arm/x86) for CSV output."""
    n = _normalize_arch(a)
    if n == 'arm64':
        return 'arm'
    if n == 'amd64':
        return 'x86'
    return n


def get_supported_chip(app_info, arch):
    """Return supported_chip set from app_build entries matching `arch`
    (union across all matching OS/build entries for that arch)."""
    chips = set()
    if not app_info:
        return chips
    target_arch = _normalize_arch(arch)
    for _, build_cfg in (app_info.get('app_build') or {}).items():
        if target_arch and _normalize_arch(build_cfg.get('arch')) != target_arch:
            continue
        for chip in build_cfg.get('supported_chip') or []:
            chips.add(chip.strip().lower())
    return chips


def get_arches_from_app_info(app_info):
    """Set of normalized arches declared in app_info.app_build.*.arch."""
    arches = set()
    if not app_info:
        return arches
    for _, build_cfg in (app_info.get('app_build') or {}).items():
        a = _normalize_arch(build_cfg.get('arch'))
        if a:
            arches.add(a)
    return arches


def get_app_version_from_app_info(app_info):
    """Return app_info.json top-level app_version, or empty if missing."""
    if isinstance(app_info, dict):
        value = app_info.get('app_version')
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''


def get_test_arches(test_cfg):
    """Set of normalized arches a single test supports — derived from the
    build_key suffix in `supported_chip: {chip: [build_key, ...]}`."""
    arches = set()
    sc = test_cfg.get('supported_chip')
    if isinstance(sc, dict):
        for _chip, build_keys in sc.items():
            if not isinstance(build_keys, list):
                continue
            for k in build_keys:
                a = _normalize_arch(str(k).rsplit('_', 1)[-1])
                if a:
                    arches.add(a)
    return arches


def split_maca_hpcc(chips):
    """Split chip set into (maca_chip_str, hpcc_chip_str). x201 -> hpcc."""
    maca_chips = sorted(c for c in chips if c != 'x201')
    hpcc_chips = sorted(c for c in chips if c == 'x201')
    return ','.join(maca_chips), ','.join(hpcc_chips)


def build_docker_cmd(test_cfg):
    """Assemble `docker run ...` command from a test config dict.

    If test_cfg.mount_dataset is truthy, also bind-mount the dataset directory
    read-only into the container.
    """
    container_args = test_cfg.get('container_args', '').strip()
    test_cmd = test_cfg.get('test_cmd', '').strip()
    img_target = test_cfg.get('img_target', '').strip().lower()
    image_placeholder = f"[docker_image_{img_target}]" if img_target else "[docker_image]"

    parts = ['docker run --pull always --rm -e MACA_PERF_DIR=/tmp']
    if test_cfg.get('mount_dataset'):
        parts.append('-v /pde_hpc/dataset:/hpc_dataset:ro')
    if container_args:
        parts.append(container_args)
    parts.append(image_placeholder)
    parts.append(f"sh -c '{test_cmd}'")
    return ' '.join(parts)


def get_test_supported_chip(test_cfg, arch):
    """Return (chip_str, skip_for_arch) for a single app_test entry.

    Schema: `supported_chip: { <chip>: [<build_key>...] }` where build_key
    looks like "ubuntu20.04_amd64" or "kylin2309a_arm64". A chip is kept only
    if any of its build_keys' arch suffix matches the row's arch.

    skip_for_arch is True when supported_chip is a non-empty dict but every
    chip got filtered out — i.e., the test explicitly does not support arch
    on any chip, so the caller should drop the row entirely. Otherwise False
    (missing/list-form supported_chip is treated as "no info" → keep all).
    """
    sc = test_cfg.get('supported_chip')
    target_arch = _normalize_arch(arch)

    if isinstance(sc, dict) and sc:
        chips = []
        for chip, build_keys in sc.items():
            keys = build_keys if isinstance(build_keys, list) else []
            if not target_arch:
                chips.append(chip)
                continue
            if any(_normalize_arch(str(k).rsplit('_', 1)[-1]) == target_arch
                   for k in keys):
                chips.append(chip)
        chip_str = ','.join(sorted(
            {str(c).strip().lower() for c in chips if str(c).strip()}
        ))
        return chip_str, (bool(target_arch) and not chips)

    if isinstance(sc, list):
        return ','.join(sorted(
            {str(c).strip().lower() for c in sc if str(c).strip()}
        )), False

    return '', False


def build_release_and_test_reports(df_raw):
    """Build release_df and test_df from app_info.json as the source of truth
    for arch / supported_chip.

    df_raw is the cleaned DB result (columns: app_name, app_version, app_chip,
    arch, maca_version, git_url, git_branch). DB rows are only used to:
      - enumerate distinct apps via (app_name, app_version, git_url, git_branch);
      - provide chip fallback when app_info.json can't be fetched.

    Once app_info.json is in hand, arches come from `app_build.*.arch` (so
    apps supporting arches the DB hasn't recorded yet — e.g., lammps arm —
    still appear in the release report) and chips come from each build's
    `supported_chip`. Tests come from app_info.app_test (enabled + daily),
    with one row per arch supported by that test.
    """
    if df_raw.empty:
        return pd.DataFrame(), pd.DataFrame(), {}

    # DB chip fallback: {(app, version, url, branch): {normalized_arch: chip_set}}
    db_fallback = {}
    for _, r in df_raw.iterrows():
        key = (r['app_name'], r['app_version'], r['git_url'], r['git_branch'])
        n_arch = _normalize_arch(r.get('arch'))
        chips = {c.strip().lower() for c in str(r.get('app_chip') or '').split(',')
                 if c.strip()}
        db_fallback.setdefault(key, {}).setdefault(n_arch, set()).update(chips)

    apps_df = df_raw[['app_name', 'app_version', 'git_url', 'git_branch']].drop_duplicates()

    release_rows = []
    test_rows = []
    resolve_cache = {}
    info_cache = {}
    # {(app_name, app_version): raw_bytes} — raw app_info.json blobs for tar.
    app_info_blobs = {}

    for _, row in apps_df.iterrows():
        app_name = row['app_name']
        input_app_version = row['app_version']
        raw_bytes = None
        original_key = (row['git_url'], row['git_branch'])

        if original_key not in resolve_cache:
            resolve_cache[original_key] = resolve_manifest_url(*original_key)
        resolved_url, resolved_branch = resolve_cache[original_key]

        if resolved_url is None:
            print(f"  [warn] skip app_info fetch for {original_key[0]}@{original_key[1]} "
                  f"(manifest resolution failed)")
            app_info = None
            out_url, out_branch = original_key
        else:
            info_key = (resolved_url, resolved_branch)
            if info_key not in info_cache:
                print(f"Fetching app_info.json from {resolved_url}@{resolved_branch}")
                raw_info, raw_bytes = fetch_app_info(resolved_url, resolved_branch)
                info_cache[info_key] = (
                    filter_app_info(raw_info) if raw_info else None,
                    raw_bytes,
                )
            app_info, raw_bytes = info_cache[info_key]
            out_url, out_branch = resolved_url, resolved_branch

        app_version = get_app_version_from_app_info(app_info)
        if raw_bytes is not None:
            app_info_blobs[(app_name, app_version)] = raw_bytes

        fallback_key = (app_name, input_app_version, row['git_url'], row['git_branch'])

        # Decide arches for the release rows: prefer app_info, else fall back to DB.
        if app_info:
            arches = get_arches_from_app_info(app_info)
        else:
            arches = set(db_fallback.get(fallback_key, {}).keys())

        for n_arch in sorted(a for a in arches if a):
            if app_info:
                chips = get_supported_chip(app_info, n_arch)
            else:
                chips = db_fallback.get(fallback_key, {}).get(n_arch, set())
            maca_str, hpcc_str = split_maca_hpcc(chips)
            release_rows.append({
                'app_name': app_name,
                'app_version': app_version,
                'maca_chip': maca_str,
                'hpcc_chip': hpcc_str,
                'arch': _denormalize_arch(n_arch),
                'maca_version': TARGET_VERSION,
                'git_url': out_url,
                'git_branch': out_branch,
            })

        # Test rows: only emitted when app_info exists.
        if not app_info:
            continue
        app_arches = get_arches_from_app_info(app_info)
        for test_name, test_cfg in (app_info.get('app_test') or {}).items():
            test_arches = get_test_arches(test_cfg) or app_arches
            for n_arch in sorted(a for a in test_arches if a):
                _, skip = get_test_supported_chip(test_cfg, n_arch)
                if skip:
                    continue
                test_rows.append({
                    'app_name': app_name,
                    'git_branch': out_branch,
                    'app_version': app_version,
                    'arch': _denormalize_arch(n_arch),
                    'maca_version': TARGET_VERSION,
                    'test_name': test_name,
                    'docker_cmd': build_docker_cmd(test_cfg),
                })

    release_cols = [
        'app_name', 'app_version', 'maca_chip', 'hpcc_chip',
        'arch', 'maca_version', 'git_url', 'git_branch',
    ]
    test_cols = [
        'app_name', 'git_branch', 'app_version', 'arch', 'maca_version',
        'test_name', 'docker_cmd',
    ]
    release_df = pd.DataFrame(release_rows, columns=release_cols)
    test_df = pd.DataFrame(test_rows, columns=test_cols)
    if not test_df.empty:
        test_df = test_df.sort_values(
            by=['app_name', 'git_branch', 'app_version', 'arch'], kind='stable'
        ).reset_index(drop=True)
    return release_df, test_df, app_info_blobs


def save_app_info_tar(blobs, tar_path):
    """Write collected raw app_info.json blobs into a gzipped tar archive.

    Entries are named `<app_name>_<app_version>.json` inside the tar so each
    app/version pair gets its own file even when versions diverge.
    """
    if not blobs:
        print(f"  [warn] no app_info.json blobs to archive; skipping {tar_path}")
        return
    with tarfile.open(tar_path, 'w:gz') as tar:
        for (app_name, app_version), raw in sorted(blobs.items()):
            member_name = f"{app_name}_{app_version}.json"
            info = tarfile.TarInfo(name=member_name)
            info.size = len(raw)
            info.mtime = int(datetime.datetime.now().timestamp())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(raw))


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read hpc_app.csv, fetch app_info.json, archive app_info, "
            "and generate release/test command reports."
        )
    )
    parser.add_argument(
        'csv_file', nargs='?', default='hpc_app.csv',
        help='App list CSV file. Default: hpc_app.csv'
    )
    parser.add_argument(
        '--target-version', default=TARGET_VERSION,
        help=f'MACA version written to reports. Default: {TARGET_VERSION}'
    )
    return parser.parse_args()


def main():
    global TARGET_VERSION

    args = parse_args()
    TARGET_VERSION = args.target_version

    df = read_app_csv(args.csv_file)
    print(f"Loaded {len(df)} apps from {args.csv_file}")

    if df.empty:
        print("No app data found to save.")
        return

    release_df, test_df, app_info_blobs = build_release_and_test_reports(df)

    release_csv = f"hpc_release_report_{TARGET_VERSION}_{today_date}.csv"
    test_csv = f"hpc_test_cmd_{TARGET_VERSION}_{today_date}.csv"
    app_info_tar = f"app_info_{TARGET_VERSION}_{today_date}.tar.gz"

    release_df.to_csv(release_csv, index=False, encoding='utf-8-sig')
    test_df.to_csv(test_csv, index=False, encoding='utf-8-sig')
    save_app_info_tar(app_info_blobs, app_info_tar)

    print(f"\n--- Release report saved to {release_csv} ({len(release_df)} rows) ---")
    print(release_df.head())
    print(f"\n--- Test command report saved to {test_csv} ({len(test_df)} rows) ---")
    print(test_df.head())
    print(f"\n--- app_info.json archive saved to {app_info_tar} "
          f"({len(app_info_blobs)} files) ---")


if __name__ == "__main__":
    main()
