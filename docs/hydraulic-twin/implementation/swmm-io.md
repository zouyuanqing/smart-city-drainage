# SWMM 导入导出实施记录

## 范围与契约依据

- 源需求要求导入 SWMM `inp`、导出可回跑 `inp`，并对缺失坐标、悬空节点、逆坡、重复管段、环路等做校验：`docs/hydraulic-twin/SOURCE_PRD.md:16`；AC-1/AC-2 要求合法计数和恶意输入拒绝：`docs/hydraulic-twin/SOURCE_PRD.md:32`。
- 冻结补充定义了 INP 语法错误/重复 section/缺坐标为 `INVALID_INP`，路径穿越/盘符/UNC/ADS 为 `UNSAFE_PATH`，以及 INP round-trip 必须重新解析并比较计数、稳定键、拓扑和 hash：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:301-340`。
- 实施契约要求 worker 解析不执行 INP include/脚本、不访问 GeoJSON URL，导出后重新 parse，失败不得返回伪可运行文件：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1279-1297`；EPA SWMM 外部执行和输入/输出沙箱边界见 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1352-1359`。
- 路径所有权由契约固定为 `swmm-io`：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2255-2273`。

## 实际改动

### 导入器

- `backend/app/services/hydraulic/importers/swmm.py:188` 起提供 `SwmmParseError`、结构化 `SwmmIssue` 和不可变记录模型；核心 `SwmmNetwork` 位于 `:685`。
- 解析入口和兼容 facade 位于 `:1839`、`:1866`、`:1884`，支持 `parse_swmm`、`parse_swmm_inp`、`parse_inp`、`SwmmParser`/`SwmmImporter`。
- 解析 `TITLE`、`OPTIONS`、`JUNCTIONS`、`OUTFALLS`、`CONDUITS`、`SUBCATCHMENTS`、`COORDINATES`、`SLOPES`，并保留常见声明性段（如 `XSECTIONS`、`VERTICES`、`POLYGONS`、`RAINGAGES`、`INFLOWS`、`RDII`、`REPORT`、`MAP`）的原始行用于回导。
- 段名大小写不敏感，支持 `;`/`//`/`#` 注释；重复 section、重复/跨 section ID、NaN/Inf、非法坐标、缺坐标、悬空引用、self-loop、slope 端点不匹配等均 fail closed。缺坐标和引用问题可用 `strict=False` 收集到 `network.issues`，默认 strict 直接抛出。
- `_reject_path_like`（`:245`）拒绝 `FILE/IMPORT/EXEC/RUN/SCRIPT/URL/USE/LOAD` 等指令、盘符/UNC/URL/`..`/数据文件路径和 shell 元字符；解析器不做文件打开、include、脚本或 subprocess 操作。
- 输入有 20 MiB、行数、行长度、token 数和 UTF-8/控制字符边界；`source_sha256` 与语义 `content_sha256` 分离，canonical hash 排除上传行号和源文件 hash。

### 导出器

- `backend/app/services/hydraulic/exporters/swmm.py:39` 提供 `SwmmExportError`；模型验证位于 `:549`，语义比较位于 `:726-733`，主入口 `export_swmm` 位于 `:829`，兼容 facade `SwmmExporter` 位于 `:1001`。
- 支持从 `SwmmNetwork`、普通 mapping 和字段别名构造 canonical 输出；缺少坐标、重复 ID、悬空端点、self-loop、非法 slope、非有限数和不安全 raw token 都会拒绝，不写部分结果。
- 输出固定、可读的 section 顺序，补齐默认 `FLOW_UNITS CFS`（`:726`），数字采用可逆 `repr`；默认开启重新 parse 和语义签名比较，失败抛出 `ROUNDTRIP_VALIDATION_FAILED`。
- 导出器不访问用户路径、不启动 EPA SWMM；EPA engine 可运行性仍由 solver-contract 负责，当前实现保证的是白名单段的自包含、语法可回跑和语义往返一致。

### 测试与 fixture

- 完整合法 fixture 位于 `backend/tests/hydraulic/test_swmm_io.py:24`，包含 PRD 指定段、混合大小写、注释和 raw 段。
- 路径/脚本恶意 fixture 位于 `:56`；畸形、重复 section/ID、缺坐标、NaN、坏行、重复 slope、悬空引用和跨 section ID fixture 位于 `:65`。
- 行为测试从 `:79` 起，覆盖解析、API facade、恶意/边界输入、非 strict 报告、大小/行数/编码/控制字符、导出拒绝、字段别名和往返一致性。

## 已执行检查

### 通过

```text
$ cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && pytest -q tests/hydraulic/test_swmm_io.py
22 passed, 366 warnings in 0.17s
```

```text
$ cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && pytest -q tests/hydraulic
68 passed, 1453 warnings in 0.17s
```

```text
$ cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && .venv/Scripts/black.exe --check app/services/hydraulic/importers/swmm.py app/services/hydraulic/exporters/swmm.py tests/hydraulic/test_swmm_io.py && .venv/Scripts/isort.exe --check-only app/services/hydraulic/importers/swmm.py app/services/hydraulic/exporters/swmm.py tests/hydraulic/test_swmm_io.py && .venv/Scripts/flake8.exe app/services/hydraulic/importers/swmm.py app/services/hydraulic/exporters/swmm.py tests/hydraulic/test_swmm_io.py && .venv/Scripts/mypy.exe app/services/hydraulic/importers/swmm.py app/services/hydraulic/exporters/swmm.py tests/hydraulic/test_swmm_io.py
All done! ✨ 🍰 ✨
3 files would be left unchanged.
Success: no issues found in 3 source files
```

```text
$ cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && python -m py_compile app/services/hydraulic/importers/swmm.py app/services/hydraulic/exporters/swmm.py tests/hydraulic/test_swmm_io.py
(exit 0, no output)
```

### 全量后端回归（已执行但未全绿）

```text
$ cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && pytest -q
5 failed, 212 passed, 4 skipped, 6216 warnings in 24.79s
```

失败均落在并行工作流已有的 contract-gate/依赖工件状态，不在 SWMM owned paths：`tests/test_contract_gate_scripts.py` 的 migration gate 看到并行新增 `backend/alembic/versions/0002_hydraulic_schema.py`，dependency handoff 的 `.env.example` hash 不匹配，以及 frontend performance 文件与 deferred 断言冲突。未修改这些越界文件，也未伪造全量通过。

## 风险与后续

- 当前主机只有 Python 3.14.4；`py -3.11 --version` 输出 `No suitable Python runtime found`，因此本轮未能在契约指定的 Python 3.11 环境运行。代码通过目标环境可用的 3.14 pytest/Black/isort/Flake8/mypy 检查。
- 未知但安全的 SWMM 扩展 section 当前以 `UNSUPPORTED_SECTION` fail closed；接入真实 EPA SWMM 文件前应按冻结 profile 扩充 allowlist，并补对应 fixture。
- round-trip 校验比较 canonical 支持段和 raw token，不等价于启动 EPA engine 的动态仿真成功；engine 版本/哈希和求解由 solver-contract 另行门禁。
- 输入上限、行数、行长度和 token 上限是可配置的安全预算；生产 worker 仍需在外层 quarantine 流式执行 20 MiB 截断和 finally 清理。
- 未提交、未推送；工作树中其他 agent 的改动保持原样。
