"""领域层共享纯函数（阶段 2 抽取）。

从 state.py 抽出的冻结纯函数：评分/身价公式与排名链。行为与抽取前
完全一致（阶段 0 冻结测试锁定）；state.py 通过 re-import shim 继续使用。

查询层（football_simulator/queries/）与未来的写命令层都从这里引用，
避免反向依赖 state.py 私有实现。
"""
