"""Stable/Beta updater and installer release gates.

CI tests exact-match published hashes, version precedence and channel fencing.
"""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import app_updater as u


STABLE='a'*40
BETA='b'*40
DIGEST='0'*64


def mock_release(channel, commit, version, *, complete=True, prerelease=False,
                 manifest_channel=None):
    tag='Main' if channel=='main' else 'Beta'
    url=f'https://github.com/{u.REPO}/releases/download/{tag}/'
    assets=[
        {'name':u.RELEASE_ASSET,'browser_download_url':url+u.RELEASE_ASSET,
         'size':2048,'digest':'sha256:'+DIGEST},
        {'name':u.MANIFEST_ASSET,'browser_download_url':url+u.MANIFEST_ASSET},
    ] if complete else []
    release={'prerelease':prerelease,'assets':assets}
    manifest={'schema':1,'commit':commit,'channel':manifest_channel or channel,
              'version':version,'asset':u.RELEASE_ASSET,'sha256':DIGEST,'bytes':2048}
    return release,manifest


class ChannelUpdates(unittest.TestCase):
    def test_versions(self):
        self.assertLess(u.version_key('1.1.17'),u.version_key('1.1.17.1'))
        self.assertLess(u.version_key('1.1.17.1'),u.version_key('1.1.18-beta.1'))
        self.assertLess(u.version_key('1.1.18-alpha.9'),u.version_key('1.1.18-beta.1'))
        self.assertLess(u.version_key('1.1.18-beta.2'),u.version_key('1.1.18'))
        self.assertEqual(u.version_key('v1.1.17 - Performance'),u.version_key('1.1.17'))
        with self.assertRaises(ValueError):
            u.version_key('tomorrow')

    def test_only_highest_and_no_downgrades(self):
        stable=u.Update(STABLE,'1.1.17.1 - Stable',DIGEST,'stable',2048,'main')
        beta=u.Update(BETA,'1.1.18-beta.1 - Beta',DIGEST,'beta',2048,'beta')
        self.assertEqual(u.choose_latest([stable,beta],'1.1.17'),beta)
        self.assertIsNone(u.choose_latest([stable,beta],'1.1.18'))
        self.assertIsNone(u.choose_latest([stable,beta],'1.1.18-beta.1'))
        newer_stable=u.Update(STABLE,'1.1.18',DIGEST,'stable',2048,'main')
        self.assertEqual(u.choose_latest([beta,newer_stable],'1.1.18-beta.1'),newer_stable)
        same_version_beta=u.Update(BETA,'1.1.18',DIGEST,'beta',2048,'beta')
        self.assertEqual(u.choose_latest([newer_stable,same_version_beta],'1.1.17'),newer_stable)

    def test_channels_validated_against_exact_heads_and_prerelease(self):
        stable,stable_manifest=mock_release('main',STABLE,'1.1.17.1')
        beta,beta_manifest=mock_release('beta',BETA,'1.1.18-beta.1',prerelease=True)
        def get(url):
            if url.endswith('/branches/main'):
                return json.dumps({'commit':{'sha':STABLE}}).encode()
            if url.endswith('/branches/beta'):
                return json.dumps({'commit':{'sha':BETA}}).encode()
            if url.endswith('/releases/tags/Main'):
                return json.dumps(stable).encode()
            if url.endswith('/releases/tags/Beta'):
                return json.dumps(beta).encode()
            if url.endswith('/download/Main/'+u.MANIFEST_ASSET):
                return json.dumps(stable_manifest).encode()
            if url.endswith('/download/Beta/'+u.MANIFEST_ASSET):
                return json.dumps(beta_manifest).encode()
            raise AssertionError('Unexpected URL: '+url)
        with patch.object(u,'_get',side_effect=get):
            newest=u.discover_latest(STABLE,'1.1.17')
            self.assertEqual((newest.channel,newest.commit),('beta',BETA))
            beta_manifest['commit']=STABLE
            self.assertEqual(u.discover_latest(STABLE,'1.1.17').channel,'main')
            beta_manifest['commit']=BETA
            beta['prerelease']=False
            self.assertEqual(u.discover_latest(STABLE,'1.1.17').channel,'main')
            beta['prerelease']=True
            beta_manifest['channel']='main'
            self.assertEqual(u.discover_latest(STABLE,'1.1.17').channel,'main')

    def test_release_installer_and_updater_files_are_declared(self):
        root=Path(__file__).resolve().parent.parent
        script=(root/'installer/EvolveModManager.iss').read_text('utf-8')
        self.assertIn(r'DefaultDirName={localappdata}\EvolveModManager',script)
        self.assertIn('PrivilegesRequired=lowest',script)
        self.assertIn('Name: "desktopicon"',script)
        self.assertIn('Name: "startmenuicon"',script)
        self.assertNotIn('Source: "Data',script)
        updater=Path(u.__file__).read_text('utf-8')
        self.assertIn("'-ExpectedChannel', update.channel",updater)
        self.assertIn("'BUILD_CHANNEL.txt').read_text",updater)
        for workflow in ('windows-release.yml','windows-beta-release.yml'):
            w=(root/'.github/workflows'/workflow).read_text('utf-8')
            self.assertIn('EvolveModManager-Setup.exe',w)
            self.assertIn('dist/update-manifest.json',w)


if __name__=='__main__':
    unittest.main()
