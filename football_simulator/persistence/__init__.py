"""SQLite 持久化层（阶段 1）。

公开入口：
- ``connection``：连接管理与事务原语；
- ``schema``：DDL 与 schema_version；
- ``save_repository.SaveRepository``：工作字典 ↔ 规范化表的双向转换。

UI 层与查询层不得绕过 repository 直接访问数据库文件之外的存档状态；
查询（阶段 2）通过只读连接与 DTO 提供。
"""
