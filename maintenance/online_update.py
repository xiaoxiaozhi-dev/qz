"""Read the public GitHub release and download a verified Qz update."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .release import inspect_package
from .version import version_tuple

REPOSITORY = 'xiaoxiaozhi-dev/qz'
RELEASE_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASE_PAGE = f'https://github.com/{REPOSITORY}/releases'
PACKAGE_LIMIT = 2 * 1024 ** 3


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    filename: str
    size: int
    download_url: str
    checksum_url: str
    github_digest: str = ''


def _open(url, accept='application/octet-stream', method='GET'):
    return urlopen(Request(url, headers={'User-Agent': 'QzTools-Updater', 'Accept': accept},
                           method=method), timeout=20)


def _check_public_page(current_version):
    """Anonymous API limits can be shared by users behind the same proxy."""
    with _open(RELEASE_PAGE + '/latest', 'text/html', 'HEAD') as response:
        match = re.fullmatch(re.escape(RELEASE_PAGE) + r'/tag/v([0-9]+\.[0-9]+\.[0-9]+)',
                             response.geturl())
    if not match:
        raise ValueError('未找到有效的正式版发行页')
    version = match.group(1)
    if version_tuple(version) <= version_tuple(current_version):
        return None
    filename = f'QzTools-{version}-windows-x64.zip'
    url = f'{RELEASE_PAGE}/download/v{version}/{filename}'
    with _open(url, method='HEAD') as response:
        size = int(response.headers.get('Content-Length', '0'))
    if not 0 < size <= PACKAGE_LIMIT:
        raise ValueError('更新包大小无效')
    return Release(version, '', filename, size, url, url + '.sha256')


def check_latest(current_version):
    try:
        with _open(RELEASE_URL, 'application/vnd.github+json') as response:
            raw = response.read(1024 * 1024 + 1)
    except HTTPError as error:
        if error.code == 429 or (error.code == 403 and error.headers.get('X-RateLimit-Remaining') == '0'):
            return _check_public_page(current_version)
        raise
    if len(raw) > 1024 * 1024:
        raise ValueError('GitHub 发行信息过大')
    data = json.loads(raw)
    tag = data.get('tag_name', '')
    match = re.fullmatch(r'v([0-9]+\.[0-9]+\.[0-9]+)', tag)
    if not match or data.get('draft') or data.get('prerelease'):
        raise ValueError('未找到有效的正式版发行信息')
    version = match.group(1)
    if version_tuple(version) <= version_tuple(current_version):
        return None
    filename = f'QzTools-{version}-windows-x64.zip'
    prefix = f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
    assets = data.get('assets', [])
    selected = []
    for name in (filename, filename + '.sha256'):
        matches = [asset for asset in assets if asset.get('name') == name]
        if len(matches) != 1 or matches[0].get('browser_download_url') != prefix + name:
            raise ValueError(f'发行版缺少有效附件：{name}')
        selected.append(matches[0])
    size = selected[0].get('size')
    if type(size) is not int or not 0 < size <= PACKAGE_LIMIT:
        raise ValueError('更新包大小无效')
    return Release(version, str(data.get('body') or '')[:6000], filename, size,
                   prefix + filename, prefix + filename + '.sha256',
                   selected[0].get('digest') or '')


def download_update(release, directory, progress):
    """Stream to staging, verify size/hash and package version, then publish locally."""
    with _open(release.checksum_url) as response:
        raw = response.read(1025)
    match = re.fullmatch(r'([a-fA-F0-9]{64})[ \t]+\*?' + re.escape(release.filename),
                         raw.decode('ascii').strip())
    if len(raw) > 1024 or not match:
        raise ValueError('SHA256 校验文件格式无效')
    expected = match.group(1).lower()
    if release.github_digest and release.github_digest != 'sha256:' + expected:
        raise ValueError('GitHub 附件与 SHA256 校验文件不一致')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / release.filename
    with tempfile.TemporaryDirectory(prefix='.download-', dir=directory) as staging:
        part = Path(staging) / release.filename
        size, digest, last_percent = 0, hashlib.sha256(), -1
        with _open(release.download_url) as response, part.open('wb') as stream:
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > release.size:
                    raise ValueError('更新包超过发行版标注的大小')
                stream.write(chunk)
                digest.update(chunk)
                percent = size * 100 // release.size
                if percent != last_percent:
                    progress(percent)
                    last_percent = percent
        if size != release.size or digest.hexdigest() != expected:
            raise ValueError('更新包大小或 SHA256 校验失败')
        if inspect_package(part)['version'] != release.version:
            raise ValueError('更新包内的版本与发行标签不一致')
        part.replace(destination)
    return str(destination)
