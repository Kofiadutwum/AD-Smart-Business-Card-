"""The site survives a broken image library, and the repair script finds missing library files."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
from importlib.metadata import PathDistribution
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)


def load_repair_script():
    spec = importlib.util.spec_from_file_location("repair_libraries", ROOT / "scripts" / "repair_libraries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SiteStartsWithoutPillowTests(SimpleTestCase):
    def test_every_page_module_loads_when_pillow_is_missing(self):
        code = textwrap.dedent(
            """
            import sys

            class Block:
                def find_spec(self, name, path=None, target=None):
                    if name.split(".")[0] in ("PIL", "pillow_heif"):
                        raise ImportError("blocked: " + name)

            sys.meta_path.insert(0, Block())
            import django
            django.setup()
            from django.urls import get_resolver
            get_resolver().url_patterns  # imports every view module, as the first request does
            from apps.core import utils
            from apps.marketing.demo import _logo_photo_line
            assert utils.Image is None
            assert _logo_photo_line() is None
            print("site loads")
            """
        )
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings")}
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180
        )
        self.assertIn("site loads", result.stdout, result.stderr[-2000:])


class RepairScriptTests(SimpleTestCase):
    def make_package(self, folder):
        """A tiny installed package 'fake 1.0' with two files and a cached .pyc."""
        (folder / "fake" / "__pycache__").mkdir(parents=True)
        (folder / "fake" / "__init__.py").write_bytes(b"hello")
        (folder / "fake" / "_core.so").write_bytes(b"abc")
        info = folder / "fake-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fake\nVersion: 1.0\n")
        (info / "RECORD").write_text(
            "fake/__init__.py,sha256=x,5\n"
            "fake/_core.so,sha256=y,3\n"
            "fake/__pycache__/__init__.cpython-312.pyc,sha256=z,99\n"
            "fake-1.0.dist-info/RECORD,,\n"
        )
        return PathDistribution(info)

    def test_nothing_reported_when_every_file_is_in_place(self):
        repair = load_repair_script()
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.make_package(Path(tmp))
            self.assertEqual(repair.damaged([dist]), {})

    def test_missing_and_changed_files_are_reported_per_package(self):
        repair = load_repair_script()
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.make_package(Path(tmp))
            (Path(tmp) / "fake" / "_core.so").unlink()
            self.assertEqual(repair.damaged([dist]), {("fake", "1.0"): ["fake/_core.so"]})
            (Path(tmp) / "fake" / "__init__.py").write_bytes(b"changed!")
            self.assertEqual(
                repair.damaged([dist]), {("fake", "1.0"): ["fake/__init__.py", "fake/_core.so"]}
            )

    def test_installed_packages_here_are_complete(self):
        """The real check against this computer's packages finds nothing to repair."""
        from importlib.metadata import distributions

        self.assertEqual(load_repair_script().damaged(distributions()), {})
