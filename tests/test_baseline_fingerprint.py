"""三赛季种子基线指纹对照测试（阶段 0）。

基线由 ``python3 -m tests.generate_baseline`` 生成，记录固定随机源下三个
完整赛季的逐周比分、积分榜、杯赛冠军、升降级过渡与奖项结构。

这是后续所有重构（尤其阶段 1 持久化替换与阶段 2 查询拆分）的“同随机源
对照”验收工具：只要随机调用顺序与公式不变，重跑结果必须与基线完全一致。
若确有意的玩法变化，必须先通过提案流程，再重新生成基线并在评审记录中说明。
"""

from __future__ import annotations

import unittest

from tests.support import (
    FreezeTestCase,
    baseline_path,
    load_baseline,
    master_config_sha256,
    run_three_season_fingerprint,
)


class BaselineFingerprintTests(FreezeTestCase):
    def test_three_season_fingerprint_matches_baseline(self) -> None:
        baseline = load_baseline()
        if baseline is None:
            self.skipTest("缺少基线文件，请先运行：python3 -m tests.generate_baseline")

        self.assertEqual(
            baseline["meta"]["master_config_sha256"],
            master_config_sha256(),
            "足球模拟器总配置.json 已变化：指纹必须基于同一配置重生成，"
            "请在用户批准后运行 python3 -m tests.generate_baseline 并记录原因。",
        )

        actual = run_three_season_fingerprint()
        self.assertEqual(
            actual,
            baseline["seasons"],
            "三赛季指纹与基线不一致：如无对应提案，说明玩法行为被意外改变。",
        )

    def test_baseline_file_exists_and_wellformed(self) -> None:
        path = baseline_path()
        self.assertTrue(path.exists(), "缺少基线文件，请先运行：python3 -m tests.generate_baseline")
        baseline = load_baseline()
        self.assertEqual(baseline["meta"]["format_version"], 1)
        self.assertEqual(baseline["meta"]["seed"], 20260828)
        self.assertEqual(len(baseline["seasons"]), 3)
        for season in baseline["seasons"]:
            self.assertEqual(len(season["weeks"]), 52)
            self.assertEqual(len(season["final"]["premier_table"]), 20)
            self.assertEqual(len(season["final"]["second_table"]), 20)
            self.assertEqual(len(season["final"]["top20"]), 20)


if __name__ == "__main__":
    unittest.main()
