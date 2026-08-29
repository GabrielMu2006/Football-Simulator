"""重新生成三赛季种子基线指纹。

用法（在项目根目录）::

    python3 -m tests.generate_baseline

注意：基线代表“当前工作树在固定随机源下的玩法行为”。只有两种情况允许
重新生成：一是刚完成一次经过批准的玩法变化（需附提案），二是基线生成
逻辑本身升级（format_version 变更）。日常重构必须保持现有基线通过。
"""

from __future__ import annotations

import json

from tests.support import (
    TEST_SEED,
    baseline_path,
    isolate_save_root,
    master_config_sha256,
    run_three_season_fingerprint,
)


def main() -> None:
    with isolate_save_root(TEST_SEED):
        fingerprints = run_three_season_fingerprint()
    payload = {
        "meta": {
            "format_version": 1,
            "seed": TEST_SEED,
            "master_config_sha256": master_config_sha256(),
            "scope": [
                "三个完整赛季的逐周比分（premier/second/cup/playoff）",
                "每赛季最终积分榜、杯赛冠军、升降级过渡",
                "年度 Top20 与各赛事个人奖得主",
            ],
            "note": (
                "随机源为 state.set_rng_provider 注入的 random.Random(seed)，"
                "每次 _rng() 调用返回新实例；生产默认仍是 random.SystemRandom。"
            ),
        },
        "seasons": fingerprints,
    }
    target = baseline_path()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入基线：{target}")
    print(f"主配置 sha256：{payload['meta']['master_config_sha256']}")


if __name__ == "__main__":
    main()
