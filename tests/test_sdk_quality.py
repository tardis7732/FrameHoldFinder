import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import sdk_bridge
from sdk_settings_dialog import SDKSettingsDialog


class SDKQualityTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root_dir = Path(temp)
        self.config = self.root_dir / 'user.json'
        self.enterContext(patch.object(sdk_bridge, 'settings_file', return_value=self.config))
        self.enterContext(patch.object(sdk_bridge, 'app_dir', return_value=self.root_dir))
        self.python = self.root_dir / 'python.exe'
        self.python.touch()

    def test_legacy_and_invalid_configs_use_high_and_path_updates_keep_quality(self):
        for old in ('{', '[]', '{"quality":null}', '{"quality":"BROKEN"}', '{"python":"legacy.exe"}'):
            self.config.write_text(old, encoding='utf-8')
            self.assertEqual(sdk_bridge.configured_quality(), 'HIGH')
        self.config.write_text(json.dumps({'python': str(self.python), 'other': True}), encoding='utf-8-sig')
        sdk_bridge.save_quality('LOW')
        self.assertEqual(sdk_bridge.runtime(), str(self.python))
        sdk_bridge.save_runtime(self.python)
        self.assertEqual(sdk_bridge.configured_quality(), 'LOW')
        self.assertTrue(json.loads(self.config.read_text(encoding='utf-8'))['other'])
        before = self.config.read_bytes()
        with self.assertRaises(ValueError):
            sdk_bridge.save_runtime(self.python, quality='BROKEN')
        self.assertEqual(self.config.read_bytes(), before)

    def test_applying_an_sdk_path_preserves_the_quality_chosen_for_rendering(self):
        sdk_bridge.save_quality('LOW')
        applied = Mock()
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        root.withdraw()
        dialog = SDKSettingsDialog(root, applied)
        self.addCleanup(lambda: dialog.destroy() if dialog.winfo_exists() else None)
        dialog.selected_path = str(self.python)
        dialog._update_buttons()
        before = self.config.read_bytes()

        def apply_and_wait():
            dialog.apply_button.invoke()
            deadline = time.monotonic() + 3
            while dialog.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            self.assertFalse(dialog.busy)
        with patch.object(sdk_bridge, 'check', side_effect=RuntimeError('unavailable')):
            apply_and_wait()
        self.assertEqual(self.config.read_bytes(), before)
        applied.assert_not_called()
        result = {'gpu': 'GPU'}
        with patch.object(sdk_bridge, 'check', return_value=result):
            apply_and_wait()
        self.assertEqual(sdk_bridge.runtime(), str(self.python))
        self.assertEqual(sdk_bridge.configured_quality(), 'LOW')
        applied.assert_called_once_with(str(self.python), result)
