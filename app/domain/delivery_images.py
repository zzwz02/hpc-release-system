"""Delivery images keyed by an enabled app_build variant, frozen on publication."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.domain.container_image import delivered_image_reference, parse_base_image_reference
from app.domain.test_commands import IMAGE_TARGET_SUFFIX, command_rows, docker_cmd, image_values_for_build
from app.domain.test_platforms import build_platforms, build_target_matches, public_platform, release_tests


def _build_key(platform: dict, target: str, image: str) -> str:
    identity = [platform[k] for k in ('build', 'os', 'arch', 'python_label', 'pytorch_label')]
    return json.dumps([*identity, target, image], ensure_ascii=False, separators=(',', ':'))


def builds(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for platform in build_platforms(raw):
        for target in IMAGE_TARGET_SUFFIX:
            if not build_target_matches(platform['config'], target):
                continue
            for _, image in image_values_for_build(raw, platform['config'], target):
                key = _build_key(platform, target, image)
                result.append({**public_platform(platform), 'build_key': key, 'image_target': target,
                               'image_pattern': image})
    return list({row['build_key']: row for row in result}.values())


def recorded(images: list[dict], image: str = '', digest: str = '', commit: str = '') -> list[dict]:
    if images:
        return images
    if not image and not digest:
        return []
    return [{'build_key': 'legacy', 'build': '', 'os': '', 'arch': '', 'python_label': '',
             'pytorch_label': '', 'image_target': '', 'image': image, 'digest': digest,
             'commit_id': commit, 'reference': delivered_image_reference(image, digest)}]


def published_commands(raw: dict, images: list[dict], excluded: set[str] | None = None) -> list[dict]:
    """Bind each frozen test/build to its delivered digest, preserving unfilled templates."""
    saved = {image['build_key']: image for image in images}
    configs = dict(release_tests(raw))
    result = []
    for row in command_rows(raw, excluded):
        image = saved.get(_build_key(row, row['image_target'], row['image']))
        reference = delivered_image_reference(image.get('image', ''), image.get('digest', '')) if image else ''
        result.append({
            **row, 'path': row['test'],
            'image': reference or row['image'],
            'command': docker_cmd(configs[row['test']], reference) if reference else row['command'],
            'delivery_reference': reference,
            'delivery_build_key': image['build_key'] if image else '',
            'digest': image.get('digest', '') if image else '',
        })
    return result


def revision(entry: dict) -> str:
    values = {key: entry.get(key) for key in ('locked_version_id', 'deliverable_images', 'deliverable_image',
              'deliverable_digest', 'deliverable_commit', 'qa_status', 'qa_note')}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize(values: Any, raw: dict, entry: dict) -> list[dict]:
    if not isinstance(values, list):
        raise ValueError('交付镜像必须是列表')
    available = {row['build_key']: row for row in builds(raw)}
    previous = recorded(entry.get('deliverable_images') or [], entry.get('deliverable_image', ''),
                        entry.get('deliverable_digest', ''), entry.get('deliverable_commit', ''))
    legacy = next((row for row in previous if row['build_key'] == 'legacy'), None)
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('交付镜像行格式错误')
        key = value.get('build_key', '')
        if not isinstance(key, str):
            raise ValueError('交付镜像的构建标识格式错误')
        if key in seen:
            raise ValueError('同一构建配置只能填写一条交付镜像')
        seen.add(key)
        if key == 'legacy':
            if not legacy or value != legacy:
                raise ValueError('旧交付镜像请改填到对应的构建行，不能伪造未指定构建的镜像')
            result.append(legacy)
            continue
        if key not in available:
            raise ValueError('交付镜像的构建配置不属于报名锁定的 app_info，请刷新后重试')
        reference = str(value.get('reference') or '').strip()
        if not reference:
            continue
        parsed = parse_base_image_reference(reference)
        if not parsed['digest']:
            raise ValueError('交付镜像必须填写包含 sha256 digest 的完整镜像名')
        spec = available[key]
        expected = spec['image_pattern'].partition(':')[0]
        address = parsed['image'].rsplit('/', 1)[-1].partition(':')[0]
        if address != expected:
            raise ValueError(f'构建 {spec["build"]} 的镜像名称应为 {expected}')
        result.append({**spec, 'image': parsed['image'], 'digest': parsed['digest'],
                       'reference': f'{parsed["image"]}@{parsed["digest"]}',
                       'commit_id': str(value.get('commit_id') or '').strip()})
    assigned = {row.get('reference') for row in result if row['build_key'] != 'legacy'}
    return [row for row in result if row['build_key'] != 'legacy' or row.get('reference') not in assigned]
