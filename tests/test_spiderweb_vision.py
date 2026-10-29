"""Optional visual regression: cropped game icons, no desktop/user data."""
import ast
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(cv2 is None, "Visual regression requires the normal OpenCV runtime dependencies")
class SpiderwebVisionTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / "source/utils/utils.py").read_text(encoding="utf-8"))
        nodes = [n for n in tree.body if (
            isinstance(n,ast.ClassDef) and n.name in ("Locate","LocateRGB")
        ) or (isinstance(n,ast.FunctionDef) and n.name == "amplify")]
        ns = {"cv2":cv2,"np":np,"p":SimpleNamespace(WINDOW=(0,0,1280,720))}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),"vision_regression","exec"),ns)
        self.locate = ns["LocateRGB"]
        self.amplify = ns["amplify"]
        self.template = self.amplify(cv2.imread(str(ROOT / "ImageAssets/UI/teams/Keywordless/spiderweb.png")))
        self.fixtures = json.loads((ROOT / "tests/fixtures/spiderweb_inventory.json").read_text())

    def icon(self, name):
        image = cv2.imdecode(np.frombuffer(base64.b64decode(self.fixtures[name]),dtype=np.uint8),cv2.IMREAD_COLOR)
        return self.amplify(image)

    def test_existing_template_recognizes_visible_web_without_lowering_threshold(self):
        image = self.icon("visible_spiderweb")
        box = self.locate.locate(self.template,image=image,region=(0,0,128,134),conf=0.86)
        self.assertIsNotNone(box)
        self.assertGreaterEqual(self.locate.get_conf(self.template,image=image,region=(0,0,128,134)),0.86)

    def test_neighboring_non_web_gift_is_not_accepted_as_spiderweb(self):
        image = self.icon("other_gift")
        self.assertIsNone(self.locate.locate(self.template,image=image,region=(0,0,135,134),conf=0.86))


if __name__ == "__main__":
    unittest.main()
