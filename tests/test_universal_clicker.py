import queue
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import universal_clicker as appmod


class FakeCamera:
    def __init__(self, bgra: np.ndarray, left: int = 0, top: int = 0):
        self._image = bgra
        self._left = left
        self._top = top
        self.monitors = [
            {
                "left": left,
                "top": top,
                "width": int(bgra.shape[1]),
                "height": int(bgra.shape[0]),
            }
        ]

    def grab(self, monitor: dict) -> np.ndarray:
        x1 = int(monitor["left"]) - self._left
        y1 = int(monitor["top"]) - self._top
        x2 = x1 + int(monitor["width"])
        y2 = y1 + int(monitor["height"])
        return self._image[y1:y2, x1:x2]


def core_app():
    app = appmod.UniversalClickerApp.__new__(appmod.UniversalClickerApp)
    app.stop_event = threading.Event()
    app.events = queue.Queue()
    return app


class UniversalClickerTests(unittest.TestCase):
    def test_new_profile_is_generic_and_safe(self):
        profile = appmod.new_profile("朋友的方案")
        self.assertEqual(profile["name"], "朋友的方案")
        self.assertFalse(profile["formal_mode"])
        self.assertEqual(len(profile["steps"]), 2)
        self.assertTrue(all(step["template"] is None for step in profile["steps"]))

    def test_share_export_strips_private_device_data(self):
        profile = appmod.new_profile("公开方案")
        profile["formal_mode"] = True
        profile["steps"][0]["template"] = {
            "path": r"C:\private\button.png",
            "x": 123,
            "y": 456,
            "width": 100,
            "height": 40,
        }
        payload = appmod.make_share_payload(profile)
        exported = payload["profile"]
        self.assertEqual(payload["format"], "通用定时图像点击器方案")
        self.assertFalse(exported["formal_mode"])
        self.assertTrue(all(step["template"] is None for step in exported["steps"]))
        self.assertNotEqual(exported["id"], profile["id"])
        self.assertNotEqual(exported["steps"][0]["id"], profile["steps"][0]["id"])

    def test_normalizer_caps_step_count_and_values(self):
        app = core_app()
        raw = {
            "name": "导入方案",
            "threshold": 10,
            "margin": -1,
            "steps": [
                {"name": f"步骤{i}", "timeout": 999, "delay_ms": -10}
                for i in range(10)
            ],
        }
        profile = app._normalize_profile(raw, "后备")
        self.assertEqual(len(profile["steps"]), appmod.MAX_STEPS)
        self.assertEqual(profile["threshold"], 0.999)
        self.assertEqual(profile["margin"], 20)
        self.assertTrue(all(step["timeout"] == 60 for step in profile["steps"]))
        self.assertTrue(all(step["delay_ms"] == 0 for step in profile["steps"]))

    def test_template_match_finds_exact_center_with_virtual_origin(self):
        rng = np.random.default_rng(20260917)
        screen = rng.integers(0, 256, size=(260, 360, 3), dtype=np.uint8)
        x, y, width, height = 135, 92, 64, 38
        needle_bgr = screen[y : y + height, x : x + width].copy()
        needle = cv2.cvtColor(needle_bgr, cv2.COLOR_BGR2GRAY)
        bgra = cv2.cvtColor(screen, cv2.COLOR_BGR2BGRA)
        origin_x, origin_y = -360, 45

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "button.png"
            self.assertTrue(cv2.imwrite(str(path), needle_bgr))
            template = appmod.TemplateInfo(
                path=path,
                x=origin_x + x,
                y=origin_y + y,
                width=width,
                height=height,
            )
            found = core_app()._find_once(
                FakeCamera(bgra, origin_x, origin_y), template, needle, 0.90, 100, True
            )

        self.assertIsNotNone(found)
        self.assertEqual(found[0:2], (origin_x + x + width // 2, origin_y + y + height // 2))
        self.assertGreater(found[2], 0.99)

    def test_system_clock_wait_crosses_target(self):
        app = core_app()
        target = datetime.now() + timedelta(milliseconds=60)
        app._wait_for_target(target)
        offset_ms = (datetime.now() - target).total_seconds() * 1000
        self.assertGreaterEqual(offset_ms, 0)
        self.assertLess(offset_ms, 40)

    def test_stop_interrupts_wait_and_delay(self):
        app = core_app()
        app.stop_event.set()
        with self.assertRaises(appmod.StopRequested):
            app._wait_for_target(datetime.now() + timedelta(seconds=1))
        with self.assertRaises(appmod.StopRequested):
            app._interruptible_delay(0.1)


if __name__ == "__main__":
    unittest.main()
