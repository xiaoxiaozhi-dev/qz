import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maintenance import online_update as update


class OnlineUpdateTests(unittest.TestCase):
    package = b'example ZIP payload'

    def release(self):
        filename = 'QzTools-0.1.10-windows-x64.zip'
        prefix = f'https://github.com/{update.REPOSITORY}/releases/download/v0.1.10/'
        return dict(tag_name='v0.1.10', body='Release notes', assets=[
            dict(name=name, browser_download_url=prefix + name, size=len(self.package))
            for name in (filename, filename + '.sha256')])

    def check(self, payload, current='0.1.9'):
        with patch.object(update, '_open', return_value=io.BytesIO(json.dumps(payload).encode())):
            return update.check_latest(current)

    def test_numeric_versions_and_required_paired_assets(self):
        payload = self.release()
        self.assertEqual(self.check(payload).version, '0.1.10')
        self.assertIsNone(self.check(payload, '0.1.10'))
        self.assertIsNone(self.check(payload, '0.2.0'))
        payload['assets'][0]['browser_download_url'] = 'https://example.com/fake.zip'
        with self.assertRaisesRegex(ValueError, '有效附件'):
            self.check(payload)
        payload['prerelease'] = True
        with self.assertRaisesRegex(ValueError, '正式版'):
            self.check(payload)

    def test_download_verifies_before_replacing_local_file(self):
        release = self.check(self.release())
        digest = hashlib.sha256(self.package).hexdigest()
        checksum = f'{digest}  {release.filename}\n'.encode()
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / release.filename
            destination.write_bytes(b'existing')
            for blob, package_version in ((b'corrupted payload!!', release.version),
                                          (self.package, '0.1.11')):
                with (patch.object(update, '_open', side_effect=[io.BytesIO(checksum), io.BytesIO(blob)]),
                      patch.object(update, 'inspect_package', return_value={'version': package_version}),
                      self.assertRaises(ValueError)):
                    update.download_update(release, folder, lambda percent: None)
                self.assertEqual(destination.read_bytes(), b'existing')
                self.assertEqual(list(Path(folder).iterdir()), [destination])
            progress = []
            with (patch.object(update, '_open', side_effect=[io.BytesIO(checksum), io.BytesIO(self.package)]),
                  patch.object(update, 'inspect_package', return_value={'version': release.version})):
                self.assertEqual(update.download_update(release, folder, progress.append), str(destination))
            self.assertEqual(destination.read_bytes(), self.package)
            self.assertEqual(progress[-1], 100)

    def test_api_rate_limit_uses_public_release_page(self):
        limited = HTTPError(update.RELEASE_URL, 403, 'rate limit', {'X-RateLimit-Remaining': '0'}, None)
        page, asset = MagicMock(), MagicMock()
        page.__enter__.return_value.geturl.return_value = update.RELEASE_PAGE + '/tag/v0.1.10'
        asset.__enter__.return_value = SimpleNamespace(headers={'Content-Length': '1234'})
        with patch.object(update, '_open', side_effect=[limited, page, asset]) as request:
            release = update.check_latest('0.1.9')
        self.assertEqual(release.version, '0.1.10')
        self.assertEqual(release.size, 1234)
        self.assertEqual(request.call_args.args[0], release.download_url)


if __name__ == '__main__':
    unittest.main()
