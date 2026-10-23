# -*- coding: utf-8 -*-
"""Windows 打包配置健康检查（新 UI 栈）。

防止回归到旧状态：
- datas 引用不存在的文件/目录，或漏掉规则、v3 队徽、奖杯、氛围图；
- hiddenimports 仍是旧 ``ui_v2.pages.*`` 页面而不是 ``ui.pages.*``；
- spec 用 ``Path.cwd()`` 定位资源，换工作目录就打不出包；
- EXE 版本资源手工维护、落后于 ``football_simulator/version.py``；
- 构建脚本又去创建全局 conda 环境或把缓存写到工作区外。
"""

import ast
import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SPEC = ROOT / "Football Simulator UI v2 Windows.spec"
MAC_SPEC = ROOT / "Football Simulator UI v2.spec"
BUILD_SCRIPT = ROOT / "scripts" / "build_windows.ps1"
VERSION_TOOL = ROOT / "scripts" / "write_windows_version_info.py"
ASSEMBLER = ROOT / "scripts" / "assemble_windows_package.py"

#: 当前版本必须随包发布的资源（相对项目根）。缺任何一项都要让健康检查失败。
REQUIRED_RESOURCES = (
    "足球模拟器总配置.json",
    "football_simulator/rules/competitions.json",
    "football_simulator/rules/stadiums.json",
    "team_badges_40_v3/PNG",
    "team_badges_40_v3/manifest.json",
    "team_badges_40_v2/PNG",
    "assets/atmosphere/stadium_night.png",
    "assets/trophies/v1/manifest.json",
    "assets/app_icon_256.png",
)

#: 构建期图标（EXE 图标，不是 datas 条目）。
REQUIRED_BUILD_FILES = ("assets/app.ico",)

#: 新栈必须显式声明的模块（静态导入之外，仍需保证打包工具能分析到）。
REQUIRED_HIDDEN_IMPORTS = (
    "football_simulator.ui.main_window",
    "football_simulator.ui.pages.core",
    "football_simulator.ui.pages.competitions",
    "football_simulator.ui.pages.entities",
    "football_simulator.ui.pages.tools",
    "football_simulator.ui.pages.honors",
    "football_simulator.ui.trophies",
    "football_simulator.app.service",
    "football_simulator.app.queries",
    "football_simulator.persistence_v2.store",
    "football_simulator.ui.team_crest",
)

TROPHY_IDS = ("premier", "second", "aureole", "confluence", "starpath", "ember", "ascendant", "zenith")


def _module_constants(text: str, name: str) -> list:
    """读取 spec 里模块级 ``NAME = [...]`` 的字面量字符串元组列表。"""
    tree = ast.parse(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name not in targets:
            continue
        if not isinstance(node.value, ast.List):
            raise AssertionError(f"{name} 必须是字面量列表，便于健康检查")
        entries = []
        for element in node.value.elts:
            if isinstance(element, ast.Tuple):
                entries.append(tuple(
                    item.value for item in element.elts if isinstance(item, ast.Constant)
                ))
            elif isinstance(element, ast.Constant):
                entries.append(element.value)
        return entries
    raise AssertionError(f"未找到模块级 {name}")


class WindowsSpecTest(unittest.TestCase):
    def test_both_specs_exist(self) -> None:
        self.assertTrue(WINDOWS_SPEC.is_file(), WINDOWS_SPEC.name)
        self.assertTrue(MAC_SPEC.is_file(), MAC_SPEC.name)

    def test_required_runtime_resources_exist(self) -> None:
        missing = [name for name in REQUIRED_RESOURCES + REQUIRED_BUILD_FILES if not (ROOT / name).exists()]
        self.assertEqual(missing, [], f"缺少运行时资源：{missing}")

    @staticmethod
    def _covered_by_sources(resource: str, sources) -> bool:
        """目录条目算覆盖其下所有文件（spec 用目录打包整个资源树）。"""
        for source in sources:
            if resource == source:
                return True
            if resource.startswith(source.rstrip("/") + "/"):
                return True
        return False

    def test_windows_spec_bundles_every_required_resource(self) -> None:
        sources = [entry[0] for entry in _module_constants(WINDOWS_SPEC.read_text(encoding="utf-8"), "DATA_FILES")]
        missing = [name for name in REQUIRED_RESOURCES if not self._covered_by_sources(name, sources)]
        self.assertEqual(missing, [], f"Windows spec 未打包：{missing}")

    def test_windows_spec_entry_is_current_ui(self) -> None:
        text = WINDOWS_SPEC.read_text(encoding="utf-8")
        self.assertIn('ENTRY_POINT = "ui_v2_main.py"', text)
        self.assertIn("ui_v2_main.py", text)
        hidden = _module_constants(text, "HIDDEN_IMPORTS")
        stale = [name for name in hidden if ".ui_v2.pages." in name]
        self.assertEqual(stale, [], f"Windows spec 仍在收集旧页面：{stale}")

    def test_windows_spec_declares_new_stack_modules(self) -> None:
        hidden = set(_module_constants(WINDOWS_SPEC.read_text(encoding="utf-8"), "HIDDEN_IMPORTS"))
        missing = [name for name in REQUIRED_HIDDEN_IMPORTS if name not in hidden]
        self.assertEqual(missing, [], f"Windows spec 缺少新栈模块：{missing}")
        for page in ("core", "competitions", "entities", "tools", "honors"):
            self.assertTrue((ROOT / "football_simulator" / "ui" / "pages" / f"{page}.py").is_file(), page)

    def test_windows_spec_is_cwd_independent(self) -> None:
        text = WINDOWS_SPEC.read_text(encoding="utf-8")
        self.assertIn("ROOT = Path(SPECPATH)", text)
        self.assertNotIn("Path.cwd()", text)
        self.assertNotIn("os.getcwd()", text)

    def test_windows_spec_is_onedir_windowed_with_icon_and_version(self) -> None:
        text = WINDOWS_SPEC.read_text(encoding="utf-8")
        self.assertIn("exclude_binaries=True", text)
        self.assertIn("COLLECT(", text)
        self.assertIn("console=False", text)
        self.assertIn('icon=[str(ROOT / "assets" / "app.ico")]', text)
        self.assertIn("FS_WINDOWS_VERSION_FILE", text)
        self.assertIn("version=VERSION_FILE", text)

    def test_windows_spec_fails_when_resource_missing(self) -> None:
        text = WINDOWS_SPEC.read_text(encoding="utf-8")
        self.assertIn("缺少打包资源", text)
        self.assertIn("raise SystemExit", text)

    def test_mac_spec_still_targets_new_ui(self) -> None:
        text = MAC_SPEC.read_text(encoding="utf-8")
        hidden = set(_module_constants(text, "HIDDEN_IMPORTS")) if "HIDDEN_IMPORTS" in text else set()
        if not hidden:
            # macOS spec 保持原内联列表写法，直接检查文本包含关系。
            for module in REQUIRED_HIDDEN_IMPORTS:
                self.assertIn(module, text, f"macOS spec 缺少 {module}")
        else:
            for module in REQUIRED_HIDDEN_IMPORTS:
                self.assertIn(module, hidden)
        self.assertIn("app.icns", text)

    def test_trophy_manifest_matches_eight_competitions(self) -> None:
        import json

        manifest = json.loads((ROOT / "assets" / "trophies" / "v1" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("version"), 1)
        assets = manifest.get("assets") or {}
        for trophy in TROPHY_IDS:
            self.assertIn(trophy, assets, f"奖杯 manifest 缺少 {trophy}")
            self.assertEqual(assets[trophy].get("status"), "ready", trophy)
            image = ROOT / "assets" / "trophies" / "v1" / str(assets[trophy]["path"])
            self.assertTrue(image.is_file(), image)

    def test_v3_badge_manifest_covers_forty_clubs(self) -> None:
        import json

        manifest = json.loads((ROOT / "team_badges_40_v3" / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest.get("assets")
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 40)
        for entry in entries:
            asset = ROOT / str(entry.get("asset_path", ""))
            self.assertTrue(asset.is_file(), f"队徽缺失：{entry.get('asset_path')}")
        pngs = sorted((ROOT / "team_badges_40_v3" / "PNG").glob("*.png"))
        self.assertEqual(len(pngs), 40)


class VersionResourceTest(unittest.TestCase):
    def test_generated_version_matches_product_version(self) -> None:
        from football_simulator.version import APP_VERSION

        spec = importlib.util.spec_from_file_location("write_windows_version_info", VERSION_TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.read_app_version(ROOT), APP_VERSION)
        text = module.render_version_info(APP_VERSION)
        self.assertIn(f"StringStruct(u'FileVersion', u'{APP_VERSION}')", text)
        self.assertIn(f"StringStruct(u'ProductVersion', u'{APP_VERSION}')", text)
        self.assertNotIn("1.0.1", text)
        numbers = ", ".join(str(part) for part in module.version_tuple(APP_VERSION))
        self.assertIn(f"filevers=({numbers})", text)

    def test_writer_emits_up_to_date_file(self) -> None:
        tmp = tempfile.mkdtemp(prefix="fs_version_")
        target = Path(tmp) / "windows_version_info.txt"
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            try:
                import write_windows_version_info as writer
            finally:
                sys.path.pop(0)
            version, output = writer.write_version_info(target, ROOT)
            self.assertTrue(output.is_file())
            content = output.read_text(encoding="utf-8")
            self.assertIn(version, content)
            self.assertIn("# UTF-8", content)
        finally:
            support.assert_cleanup_path_is_temporary(Path(tmp))
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_committed_version_file_is_not_stale(self) -> None:
        """仓库根的文件可保留，但必须与产品版本同步（或被构建脚本覆盖）。"""
        from football_simulator.version import APP_VERSION

        legacy = ROOT / "windows_version_info.txt"
        if not legacy.exists():
            return
        text = legacy.read_text(encoding="utf-8")
        self.assertIn(APP_VERSION, text, "windows_version_info.txt 版本落后于 version.py")


class BuildScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_declares_required_parameters(self) -> None:
        for name in ("WorkspaceRoot", "PythonExe", "OutputDir"):
            self.assertIn(f"${name}", self.text, f"构建脚本缺少参数 {name}")

    def test_never_creates_global_conda_env(self) -> None:
        lowered = self.text.lower()
        self.assertNotIn("conda create", lowered)
        self.assertNotIn("conda run", lowered)
        self.assertNotIn("conda.exe", lowered)

    def test_redirects_caches_inside_workspace(self) -> None:
        for variable in ("PYINSTALLER_CONFIG_DIR", "PIP_CACHE_DIR", "TEMP", "TMP", "PYTHONUTF8"):
            self.assertIn(variable, self.text, f"构建脚本未重定向 {variable}")
        self.assertIn("Workspace", self.text)

    def test_checks_exit_code_of_every_external_command(self) -> None:
        self.assertGreaterEqual(self.text.count("$LASTEXITCODE"), 4)

    def test_forbids_reparse_point_escape(self) -> None:
        self.assertIn("ReparsePoint", self.text)

    def test_bat_wrapper_forwards_to_script(self) -> None:
        bat = (ROOT / "build_windows_ui_v2.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn(r"scripts\build_windows.ps1", bat)
        self.assertIn("ERRORLEVEL", bat)

    def test_assembler_builds_user_facing_readme_and_zip(self) -> None:
        text = ASSEMBLER.read_text(encoding="utf-8")
        self.assertIn("README.txt", text)
        self.assertIn("build-info.txt", text)
        self.assertIn("ZIP_DEFLATED", text)
        self.assertIn("candidate", text)
        readme_template = ROOT / "packaging" / "windows" / "README.txt"
        self.assertTrue(readme_template.is_file())
        body = readme_template.read_text(encoding="utf-8")
        self.assertIn("%APPDATA%", body)
        self.assertIn(".fsarchive", body)
        self.assertIn("_internal", body)

    def test_no_hardcoded_developer_absolute_path(self) -> None:
        for path in (BUILD_SCRIPT, ASSEMBLER):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text)
            self.assertIsNone(re.search(r"[A-Za-z]:\\Users", text), path.name)


if __name__ == "__main__":
    unittest.main()
