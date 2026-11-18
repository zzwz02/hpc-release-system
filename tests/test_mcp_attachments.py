"""Attachment image responses and explicit handling of unread binary files."""
import base64
import hashlib
import io
import json
import random

import pytest
from PIL import Image

from app.integrations import jira
from tests.test_mcp import ISSUE, call, connect_client, logs, site  # noqa: F401


def image_bytes(size=(64, 32), format='PNG', noise=False):
    image = (Image.frombytes('RGB', size, random.Random(7).randbytes(size[0] * size[1] * 3))
             if noise else Image.new('RGB', size, 'blue'))
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def attachment_source(monkeypatch, data, filename='screenshot.png', mime='image/png'):
    attachment = {**ISSUE['attachments'][0], 'filename': filename, 'mime_type': mime, 'size': len(data)}
    monkeypatch.setattr(jira, 'get_issue', lambda key: {**ISSUE, 'key': key, 'attachments': [attachment]})
    monkeypatch.setattr(jira, 'download_attachment', lambda *args, **kwargs: data)


def read_image(site, **query):
    client, _, token_for, _ = site
    token = token_for('alice', 'Owner')
    session, _ = connect_client(client, token)
    return call(client, token, session, 'jira_read_attachment', {
        'issue_key': 'MC3-1', 'attachment_id': '77', **query,
    })


@pytest.mark.parametrize('format', ['PNG', 'JPEG', 'WEBP', 'GIF', 'BMP'])
def test_native_image_content_is_readable_without_local_attachment_files(site, monkeypatch, tmp_path, format):
    data = image_bytes(format=format)
    # Detect actual image bytes even if JIRA mislabels the file as a log.
    attachment_source(monkeypatch, data, filename='screenshot.log', mime='application/octet-stream')
    before = set(tmp_path.rglob('*'))
    result = read_image(site, max_lines=30)
    assert not result['isError']
    meta, picture = result['content']
    metadata = json.loads(meta['text'])
    assert metadata['kind'] == 'image' and metadata['sha256'] == hashlib.sha256(data).hexdigest()
    assert metadata['image']['returned_dimensions'] == [64, 32]
    assert not metadata['image']['lossy'] and not metadata['has_more']
    expected_mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp',
                     'GIF': 'image/gif', 'BMP': 'image/bmp'}[format]
    assert picture['type'] == 'image' and picture['mimeType'] == expected_mime
    decoded = base64.b64decode(picture['data'], validate=True)
    assert decoded == data
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.size == (64, 32)
        assert image.convert('RGB').getpixel((0, 0))[2] > 200
    assert 'data:image' not in meta['text'] and picture['data'] not in meta['text']
    # The website records only metadata; it does not create image/attachment files.
    assert not [p for p in set(tmp_path.rglob('*')) - before if p.suffix.lower() in {'.png', '.jpg', '.webp', '.gif', '.bmp'}]
    entry = logs(site[0], tool='jira_read_attachment')[0]
    assert '原图' in entry['result_summary']
    assert picture['data'] not in json.dumps(entry)
    assert json.loads(entry['extra_json'])['returned_dimensions'] == [64, 32]


@pytest.mark.parametrize('size,noise', [((3200, 1000), False), ((800, 800), True)])
def test_large_image_response_preserves_original_dimensions_and_bytes(site, monkeypatch, size, noise):
    original = image_bytes(size=size, noise=noise)
    attachment_source(monkeypatch, original)
    result = read_image(site)
    assert not result['isError']
    metadata = json.loads(result['content'][0]['text'])
    image = metadata['image']
    payload = base64.b64decode(result['content'][1]['data'])
    assert image['returned_dimensions'] == list(size)
    assert payload == original and len(payload) == image['returned_bytes']
    assert not image['lossy'] and not image['resized']
    assert image['original_dimensions'] == list(size)
    with Image.open(io.BytesIO(payload)) as delivered:
        assert list(delivered.size) == image['returned_dimensions']


def test_image_source_hash_still_validates(site, monkeypatch):
    data = image_bytes(size=(2400, 800))
    attachment_source(monkeypatch, data)
    result = read_image(site, expected_sha256=hashlib.sha256(data).hexdigest())
    assert json.loads(result['content'][0]['text'])['image']['returned_dimensions'] == [2400, 800]
    assert read_image(site, expected_sha256='0' * 64)['isError']
    assert read_image(site, start_line=2)['isError']


def test_transparent_image_preserves_alpha_and_original_bytes(site, monkeypatch):
    output = io.BytesIO()
    Image.new('RGBA', (40, 20), (10, 20, 30, 64)).save(output, format='PNG')
    original = output.getvalue()
    attachment_source(monkeypatch, original)
    result = read_image(site)
    returned = base64.b64decode(result['content'][1]['data'])
    assert returned == original
    with Image.open(io.BytesIO(returned)) as image:
        assert image.mode == 'RGBA' and image.getpixel((0, 0)) == (10, 20, 30, 64)


def test_animation_preserves_all_original_frames(site, monkeypatch):
    output = io.BytesIO()
    first, second = Image.new('RGB', (40, 20), 'red'), Image.new('RGB', (40, 20), 'blue')
    first.save(output, format='GIF', save_all=True, append_images=[second], duration=100, loop=0)
    attachment_source(monkeypatch, output.getvalue(), 'animation.gif', 'image/gif')
    result = read_image(site)
    metadata = json.loads(result['content'][0]['text'])
    assert metadata['image']['frame_count'] == 2
    assert base64.b64decode(result['content'][1]['data']) == output.getvalue()
    with Image.open(io.BytesIO(base64.b64decode(result['content'][1]['data']))) as image:
        assert image.convert('RGB').getpixel((0, 0)) == (255, 0, 0)
        image.seek(1)
        assert image.convert('RGB').getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize('filename,data', [('bundle.zip', b'PK archive'), ('report.pdf', b'%PDF-1.4'),
                                         ('dump.bin', b'\x00\x01\xff'), ('unknown.log', b'\x00\x01\xff')])
def test_unread_binary_returns_question_without_payload_or_download_command(site, monkeypatch, filename, data):
    attachment_source(monkeypatch, data, filename, 'application/octet-stream')
    result = read_image(site)
    assert not result['isError'] and len(result['content']) == 1
    metadata = json.loads(result['content'][0]['text'])
    assert metadata['status'] == 'needs_user_action' and metadata['content_returned'] is False
    assert metadata['filename'] == filename and metadata['size_bytes'] == len(data)
    assert '是否允许' in metadata['suggested_question'] and '保存本地文件' in metadata['suggested_question']
    assert '不要自动下载' in metadata['agent_instructions']
    assert 'content' not in metadata and 'data' not in metadata
    assert base64.b64encode(data).decode() not in result['content'][0]['text']
    assert 'curl ' not in result['content'][0]['text'] and '/api/mcp/downloads/' not in result['content'][0]['text']


def test_encoded_text_still_supports_retry_without_downloading_to_client(site, monkeypatch):
    original = '测试结果：全部通过\n'
    attachment_source(monkeypatch, original.encode('gb18030'), 'results.txt', 'text/plain')
    first = json.loads(read_image(site)['content'][0]['text'])
    assert first['status'] == 'needs_user_action' and first['retry_encoding'] == 'gb18030'
    retried = json.loads(read_image(site, encoding='gb18030')['content'][0]['text'])
    assert retried['content'] == original


@pytest.mark.parametrize('data,mime,reason', [(b'%PDF-1.4 ascii only', 'text/plain', 'unsupported_binary_format'),
                                           (b'ascii binary file', 'application/pdf', 'unsupported_binary_format'),
                                           (b'corrupt image', 'image/png', 'image_decoding_failed')])
def test_nontext_format_is_not_misreported_as_readable_text(site, monkeypatch, data, mime, reason):
    attachment_source(monkeypatch, data, 'unknown.log', mime)
    result = read_image(site)
    metadata = json.loads(result['content'][0]['text'])
    assert metadata['status'] == 'needs_user_action' and metadata['reason'] == reason
    assert metadata['content_returned'] is False and metadata['retry_encoding'] is None


def test_guest_image_read_preserves_jira_project_scope(site, monkeypatch):
    attachment_source(monkeypatch, image_bytes())
    client, _, token_for, _ = site
    token = token_for('guest', 'Guest')
    session, _ = connect_client(client, token)
    allowed = call(client, token, session, 'jira_read_attachment', {'issue_key': 'MC3-1', 'attachment_id': '77'})
    assert not allowed['isError'] and allowed['content'][1]['type'] == 'image'
    monkeypatch.setattr(jira, 'get_issue', lambda *a: pytest.fail('must check scope before Jira access'))
    denied = call(client, token, session, 'jira_read_attachment', {'issue_key': 'PHS-1', 'attachment_id': '77'})
    assert denied['isError'] and 'MC3' in denied['content'][0]['text']
