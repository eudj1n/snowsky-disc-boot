"""Resolve host stack pins or the disposable container's explicit profile."""
import json
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Do not prepend scripts/: emulator.py would shadow the reference's emulator
# package before its runtime helpers are imported by guest checks.
spec = importlib.util.spec_from_file_location('disc_firmware_profile', ROOT/'scripts/firmware_profile.py')
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def selected():
    if os.environ.get('CI_DISPOSABLE') == '1':
        if not os.environ.get('FW_VERSION'):
            raise ValueError('Disposable integration needs explicit FW_VERSION')
        return profiles.load_profile(os.environ['FW_VERSION'])
    return profiles.state_profile(json.loads((ROOT/'work/emulator.json').read_text()))


PROFILE = selected()
VERSION = PROFILE['version']
MAIN_OS = PROFILE['main_os_version']
IDENTITY = PROFILE['protocol_identity']
