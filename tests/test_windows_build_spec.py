# -*- coding: utf-8 -*-
"""Windows 打包配置健康检查。

防止再次出现：
- datas 引用仓库根目录不存在的文件（如 football_simulator_config.json）；
- hiddenimports 漏掉动态路由页面（PyInstaller 无法静态分析 importlib）；
- Windows exe 未配置图标。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "football_simulator" / "ui_v2" / "pages"

SPEC_NAMES = (
    "Football Simulator UI v2.spec",
    "Football Simulator UI v2 Windows.spec",
)


def _spec_text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _datas_files(text: str) -> list:
    """返回 spec 中 all Analysis datas 的源文件路径。"""
    files = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            for kw in node.keywords:
                if kw.arg == "datas" and isinstance(kw.value, ast.List):
                    for el in kw.value.elts:
                        if (
                            isinstance(el, ast.Tuple)
                            and el.elts
                            and isinstance(el.elts[0], ast.Constant)
                        ):
                            files.append(el.elts[0].value)
    return files


def _hiddenimports(text: str) -> list:
    """返回 spec 中 hiddenimports 的模块名列表。"""
    modules = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            for kw in node.keywords:
                if kw.arg == "hiddenimports" and isinstance(kw.value, ast.List):
                    for el in kw.value.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            modules.append(el.value)
    return modules


class WindowsSpecSanityTest(unittest.TestCase):
    def test_specs_exist(self) -> None:
        for name in SPEC_NAMES:
            self.assertTrue((ROOT / name).exists(), name)

    def test_datas_reference_existing_files(self) -> None:
        for name in SPEC_NAMES:
            text = _spec_text(name)
            for source in _datas_files(text):
                self.assertTrue(
                    (ROOT / source).exists(),
                    f"{name}: datas 引用了不存在的文件/目录: {source}",
                )

    def test_hiddenimports_cover_all_page_modules(self) -> None:
        for name in SPEC_NAMES:
            text = _spec_text(name)
            hidden = {m for m in _hiddenimports(text) if m.startswith("football_simulator.ui_v2.pages.")}
            expected = {f"football_simulator.ui_v2.pages.{p.stem}" for p in PAGES_DIR.glob("*_page.py")}
            missing = sorted(expected - hidden)
            self.assertFalse(missing, f"{name} 缺少 hiddenimports: {missing}")

    def test_windows_spec_uses_ico_and_component_imports(self) -> None:
        text = _spec_text("Football Simulator UI v2 Windows.spec")
        hidden = set(_hiddenimports(text))
        self.assertIn("football_simulator.ui_v2.components.crest_delegate", hidden)
        self.assertIn("football_simulator.ui_v2.components.global_search", hidden)
        self.assertIn("football_simulator.ui_v2.design_tokens", hidden)
        self.assertIn("app.ico", text)

    def test_windows_spec_does_not_reference_missing_root_config(self) -> None:
        # 英文配置副本由 scripts/build_windows.ps1 生成，spec 不应直接打包它。
        text = _spec_text("Football Simulator UI v2 Windows.spec")
        sources = _datas_files(text)
        self.assertNotIn("football_simulator_config.json", sources)


if __name__ == "__main__":
    unittest.main()
