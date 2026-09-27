# SWMM 导入导出独立审查

- **模块**：`swmm-io`
- **审查对象**：
  - `backend/app/services/hydraulic/importers/swmm.py`
  - `backend/app/services/hydraulic/exporters/swmm.py`
  - `backend/tests/hydraulic/test_swmm_io.py`
- **冻结依据**：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:301-340`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1279-1297`、`:544-545`。
- **结论**：**NOT_APPROVED / BLOCKED**。存在 5 个高风险问题和 4 个中风险问题；不能把当前输出声明为满足“可回跑 inp”的发布条件。
- **审查方式**：只读；本文件是唯一写入产物，未修改实现或测试。

## 实际读取与检查

1. 冻结契约、补充 PRD、源实现、测试和实施摘要均已从上述路径读取。`contracts/*.md` 仅作为辅助背景，未用其旧状态机覆盖冻结文档。
2. 按 ask 指定的测试文件执行：

   ```text
   cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && .venv/Scripts/pytest.exe -q tests/hydraulic/test_swmm_io.py
   ```

   实际输出：`22 passed, 440 warnings in 0.07s`（exit 0）。这只证明当前测试文件通过，不证明 EPA SWMM engine 可运行或全量门禁通过；本轮没有重复全量门禁。
3. 执行了只读定向 Python 探针（导入解析器/导出器，覆盖路径形式、拓扑、raw section、异常类型、round-trip）。关键输出如下：

   ```text
   reverse_slope OK valid= True issues= []
   duplicate_segment OK valid= True issues= []
   malformed_raw_xsection OK valid= True issues= []
   invalid_flow_units OK valid= True issues= []
   timeseries_outfall ERR SwmmParseError INVALID_INP ... stage must be a finite number, got 'TS1'
   path_code ERR SwmmParseError UNSAFE_INP ... path-like directive is not allowed in an untrusted INP
   leading_double_slash OK valid= True issues= []
   export_nan_coordinate ERR AssertionError None coordinate J x must be finite
   export_unterminated_raw ERR ValueError None No closing quotation
   export_without_xsections OK output_has_xsections= False len= 145
   drive_relative ACCEPTED valid= True raw= [('TS1', 'C:secret')]
   ads ACCEPTED valid= True raw= [('TS1', 'rain.txt:stream')]
   duplicate_xsection_valid= True
   duplicate_xsection_issues= []
   duplicate_xsection_export_roundtrip=OK True
   model_valid= True issues= []
   export_valid_topology=OK duplicate_rows= 2 has_slopes= True
   model_options= (('flow_units', 'CFS'),) effective_keys= {'flow_units': 'CFS', 'FLOW_UNITS': 'CFS'}
   export=ERR SwmmExportError ROUNDTRIP_VALIDATION_FAILED ... duplicate option FLOW_UNITS
   ```

4. 检查固定 fixture 注册表：

   ```text
   cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage && printf 'fixture_count=' && find backend/tests/fixtures/hydraulic -type f | wc -l && find backend/tests/fixtures/hydraulic -maxdepth 3 -type d -print | sort | grep -E 'network_jobs|swmm' || true && find backend/tests/fixtures/hydraulic/network_jobs/v1 -maxdepth 1 -type f -print 2>/dev/null || true
   ```

   实际输出：`fixture_count=1`。目录中唯一文件是 `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json`；没有 `network_jobs/v1` 固定目录或 SWMM fixture。

## Findings

### F-01 — High — 路径安全分类不完整，且返回 code 与冻结契约不一致

**证据**

- `backend/app/services/hydraulic/importers/swmm.py:148-156` 的 `_PATH_RE` 只把带 `/` 或 `\\` 的盘符路径、绝对路径、URL、扩展名结尾和 `..` 识别为路径；`C:secret`（Windows drive-relative）和 `rain.txt:stream`（ADS）不匹配。
- `backend/app/services/hydraulic/importers/swmm.py:244-254` 将所有此类结果标成 `UNSAFE_INP`；`backend/tests/hydraulic/test_swmm_io.py:110-116` 也把 `UNSAFE_INP` 固化成了测试期望。
- 冻结契约要求路径穿越、盘符、UNC、ADS 统一为 `UNSAFE_PATH`：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1289`、`docs/hydraulic-twin/PRD_SUPPLEMENT.md:306-312`。
- 定向探针实际输出：`drive_relative ACCEPTED ... ('TS1', 'C:secret')`、`ads ACCEPTED ... ('TS1', 'rain.txt:stream')`；同一探针对 `FILE "C:\\Users\\outside.dat"` 返回 `UNSAFE_INP`。

**影响**

不可信 INP 的 ADS/盘符相对路径可以进入 raw section，而不是按 fail-closed 契约拒绝；即使当前解析器不执行这些 token，错误 code 也不能被外层稳定映射为规定的 `422 UNSAFE_PATH`。这会使恶意 fixture/gate 产生错误分类。

**建议**

在单一规范化函数中拒绝所有盘符（包括无分隔符形式）、任意 `name.ext:stream` ADS、UNC/URI/data/file 变体和路径分隔符组合；在 worker/API 边界只暴露 `UNSAFE_PATH`。增加 drive-relative、ADS、混合大小写和引号变体测试，并让测试断言冻结 code。

### F-02 — High — 物理拓扑错误没有进入 `SwmmNetwork` 的有效性/导出门禁

**证据**

- `backend/app/services/hydraulic/importers/swmm.py:1676-1837` 的 `_validate_references` 只检查 ID 引用、坐标、self-loop 和 slope 端点；没有 `DUPLICATE_SEGMENT`、`REVERSE_SLOPE`、连通分量或环路检查。
- `backend/app/services/hydraulic/importers/swmm.py:748-753` 将 `valid`/`exportable` 简化为“没有 error issue”；`backend/app/services/hydraulic/exporters/swmm.py:548-656` 的导出前校验也没有调用 topology validator。
- `backend/app/services/hydraulic/exporters/swmm.py:733-793` 的 round-trip signature 只比较行字段，未比较拓扑摘要或 `DUPLICATE_SEGMENT`/`REVERSE_SLOPE` 状态。
- 冻结契约要求这些拓扑问题分级报告，`DUPLICATE_SEGMENT` 为阻断错误，`REVERSE_SLOPE` 为 simulation-ready 阻断：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:319-334`；导出还必须比较拓扑摘要：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1293-1296`。
- 定向探针实际输出：`reverse_slope OK valid= True issues= []`、`duplicate_segment OK valid= True issues= []`，并且 `export_valid_topology=OK duplicate_rows= 2`。

**影响**

解析结果可以标记为 `exportable=true`，导出器也能完成 round-trip，导致重复物理管段或逆坡网络被当作可导出的 canonical revision。若外层 topology worker 没有额外强制门禁，这会绕过冻结的阻断语义。

**建议**

将 topology validator 的结果绑定到 `SwmmNetwork.issues`/`exportable`（至少阻断 `DUPLICATE_SEGMENT`、`REVERSE_SLOPE`、`SELF_LOOP`、悬空引用），导出前重新验证 topology summary；对环和断连分量按契约生成 warning/error，而不是静默标记 valid。增加 parser→topology→export 的集成 fixture。

### F-03 — High — raw section 是不透明 token 容器，畸形行和跨 section 重复 ID 会被当作合法

**证据**

- `backend/app/services/hydraulic/importers/swmm.py:65-144` 将许多 section 放入 `RAW_SECTIONS`；`backend/app/services/hydraulic/importers/swmm.py:1241-1245` 只把 raw 行复制进模型，没有 section-specific token 数、类型或引用校验。
- `backend/app/services/hydraulic/importers/swmm.py:1704-1738` 的全局 `entity_ids` 只注册 node、conduit、subcatchment，不注册 `XSECTIONS`、`VERTICES`、`POLYGONS` 等 raw section 的 ID。
- `backend/tests/hydraulic/test_swmm_io.py:64-75` 名为 `cross-section-duplicate-id` 的 fixture 实际把 `C-1 J-1 OUT` 替成 `J-1 J-1 OUT`，测试的是 node/conduit 冲突，不是 XSECTIONS 重复 ID。
- 定向探针实际输出：`malformed_raw_xsection OK valid= True issues= []`；包含两行相同 `C` 的 XSECTIONS 得到 `duplicate_xsection_valid= True`、`duplicate_xsection_issues= []`，并成功 `duplicate_xsection_export_roundtrip=OK True`。同一探针还显示 `FLOW_UNITS NOT_A_UNIT` 被接受。
- 冻结契约要求重复/畸形输入拒绝：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:306-312`；不可映射内容必须拒绝或显式 `lossy=true`，不能声称可运行：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:336-340`。

**影响**

一个只含单 token 的 XSECTIONS 行、重复 XSECTIONS ID 或未知 option 都能通过 parser 和默认 exporter round-trip；输出看起来“解析一致”，但 EPA SWMM 可能无法解释。这正是“畸形输入拒绝”和“重复 ID”测试目前没有覆盖的路径。

**建议**

为每个 profile section 建立严格 schema（列数、数值、枚举、引用和 ID 唯一性），至少实现 XSECTIONS/VERTICES/POLYGONS 的重复与悬空检查；未建模 section 必须拒绝导出或生成显式 `lossy` manifest。把测试 fixture 改成真正重复的 XSECTIONS 行，并加入 malformed raw rows、未知 option 和 raw-reference 缺失用例。

### F-04 — High — exporter 没有保证 conduit 所需的 XSECTIONS/profile，映射输入可生成不可运行的 INP

**证据**

- `backend/app/services/hydraulic/exporters/swmm.py:893-912` 只输出 `[CONDUITS]` 的字段；没有从 conduit 构造或强制要求 `[XSECTIONS]`。XSECTIONS 只能依赖调用者额外塞入 `raw_sections`（`:964-969`）。
- `backend/tests/hydraulic/test_swmm_io.py:208-233` 的 mapping exporter 测试包含 `C1` conduit，但没有 `raw_sections`/XSECTIONS；测试只重新 parse，没有运行 EPA SWMM。
- 定向探针实际输出：`export_without_xsections OK output_has_xsections= False len= 145`。
- 冻结导出契约要求只对可导出、可回跑 profile 生成文件：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1293-1297`；不可映射内容必须拒绝或标记 lossy：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:336-340`。

**影响**

`export_swmm()` 的公共 mapping API 接受一个 conduit-only 模型并返回字符串，但该字符串不包含 EPA SWMM 通常所需的 conduit cross-section。它不满足用户规格中的“可回跑 inp”，而当前测试的 parser-round-trip 不能证明 engine 可运行。

**建议**

把 profile、XSECTIONS/vertices/引用完整性作为强类型导出前置条件；没有完整 cross-section profile 时返回 `EXPORT_NOT_REPRESENTABLE`，而不是返回文本。为最小可运行 profile 增加固定 golden fixture，并在可用的锁定 EPA SWMM engine 上执行一次真实 round-trip；本轮环境未提供该 engine，未声称该动态检查已运行。

### F-05 — Medium — exporter 对非有限数和畸形 raw token 泄漏 AssertionError/ValueError

**证据**

- `backend/app/services/hydraulic/exporters/swmm.py:58-64` 的 `_finite` 对 NaN/Inf 使用 `assert math.isfinite(result)`，不会转换为 `SwmmExportError`。
- `backend/app/services/hydraulic/exporters/swmm.py:425-443` 对 raw token 字符串直接调用 `shlex.split`；未闭合引号等 `ValueError` 未被捕获。
- 定向探针实际输出：`export_nan_coordinate ERR AssertionError None ...`、`export_unterminated_raw ERR ValueError None No closing quotation`。

**影响**

mapping/部分 DTO 输入可能把已知输入错误变成未处理的 500，违反导出器“拒绝不可表示模型”的稳定错误边界；调用方也无法可靠映射为 `422 EXPORT_NOT_REPRESENTABLE` 或 `ROUNDTRIP_VALIDATION_FAILED`。

**建议**

用显式 `if not math.isfinite(...): _error(...)` 替换 assert；对 token 类型、引号解析和所有 coercion 边界捕获 `TypeError/ValueError/OverflowError` 并转换为 `SwmmExportError`，保留稳定 code 和输入字段上下文。补充 NaN、±Inf、未闭合引号、超长 token、错误 mapping 类型的测试。

### F-06 — Medium — 声明支持 `TIMESERIES` outfall，却把时间序列标识强制当数值 stage

**证据**

- `backend/app/services/hydraulic/importers/swmm.py:182` 将 `TIMESERIES` 列入 `_VALID_OUTFALL_TYPES`。
- `backend/app/services/hydraulic/importers/swmm.py:1372-1403` 对 outfall 的 stage 列无条件调用 `_number`；标准时间序列边界的 stage data 是时间序列名，不是有限浮点数。
- 定向探针实际输出：`timeseries_outfall ERR SwmmParseError INVALID_INP ... stage must be a finite number, got 'TS1'`。

**影响**

合法的时间序列 outfall 被错误标为 `INVALID_INP`，与“健壮解析 OUTFALLS”及边界条件白名单不一致；当前测试只覆盖 FREE outfall。

**建议**

将 outfall stage 建模为 `stage_value | timeseries_id` 的判别联合，并校验 TIMESERIES 引用；若本版本不支持，应从允许类型中移除并返回明确的 `UNSUPPORTED_FEATURE`，而不是半支持后报语法错误。增加 FIXED/TIDAL/TIMESERIES 矩阵测试。

### F-07 — High — 必需的固定 SWMM/恶意/边界 fixture registry 缺失

**证据**

- `backend/tests/hydraulic/test_swmm_io.py:23-75` 将合法和恶意输入写成测试文件内的字符串/字典，不是固定、带 hash/manifest 的 fixture。
- 实际执行的 fixture 检查命令输出：`fixture_count=1`；没有 `backend/tests/fixtures/hydraulic/network_jobs/v1/`，也没有 `valid-minimal.inp`、`local-transform.json`、`tree-loop.inp` 或 expected golden JSON。
- 冻结契约明确要求该固定目录、manifest、恶意矩阵、逆坡/悬空/环路等 fixture：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:544-545`。

**影响**

测试结果无法绑定 parser/profile/seed 版本和输入 hash，不能作为 AC-1/AC-2 的机器证据；tree-loop、逆坡、局部坐标和 expected validation/import golden 也无法复现。`22 passed` 只代表内联样例覆盖，不代表冻结验收交付物完成。

**建议**

建立固定 registry/manifest，加入合法最小、GeoJSON/local transform、tree+loop、重复/悬空/逆坡/错误坐标/恶意路径矩阵及 golden JSON；测试只读取 manifest 指向的不可变 fixture，不在运行时生成期望值。

### F-08 — Medium — parser 没有冻结的 coordinate mode/transform 输入，硬编码 EPSG:4326

**证据**

- `backend/app/services/hydraulic/importers/swmm.py:1609-1622` 对坐标直接要求 x∈[-180,180]、y∈[-90,90]，没有 local/wgs84 模式或 transform 参数。
- `backend/app/services/hydraulic/importers/swmm.py:1839-1846` 的公共 `parse_swmm` 签名也没有 `coordinate_mode`/transform。
- 冻结 fixture 清单仍要求 `local-transform.json`：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:544-545`；实现契约要求 import 构造 canonical 几何/bbox，而不是静默拒绝可转换坐标：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1281-1285`。

**影响**

本模块自身无法消费局部工程坐标的 INP；若外层没有先做可验证转换，`local` profile 永远无法通过，且没有 CRS_UNKNOWN/转换证据链。当前固定 fixture 缺失使该缺口未被测试发现。

**建议**

在导入 profile 中显式携带并版本化 coordinate mode/transform；对 local 坐标先验证控制点和有限值，再转换到 canonical EPSG:4326，失败返回 `CRS_UNKNOWN`。补 local-transform golden fixture 和 round-trip 断言。

### F-09 — Medium — 直接构造 `SwmmNetwork` 时 sequence options 不做大小写归一化，导出可自相矛盾

**证据**

- `backend/app/services/hydraulic/importers/swmm.py:720-729` 只在 `options` 是 Mapping 时把 key 转大写；sequence 分支保留原始 key。
- `backend/app/services/hydraulic/exporters/swmm.py:726-730` 的 `_effective_options` 只识别大写 `FLOW_UNITS`，因此会为小写 key 再补一个默认项。
- 定向探针实际输出：`model_options= (('flow_units', 'CFS'),) ...` 后 `export=ERR SwmmExportError ROUNDTRIP_VALIDATION_FAILED ... duplicate option FLOW_UNITS`。
- 现有 `backend/tests/hydraulic/test_swmm_io.py:236-242` 只覆盖 Mapping 形式的 `SwmmNetwork`，没有覆盖 tuple/list 形式。

**影响**

同一合法 option 的两种构造方式行为不同；直接 DTO 构造路径会产生伪重复 option，并把本应是确定性的导出变成 round-trip failure。

**建议**

在 `SwmmNetwork.__post_init__` 对 Mapping 和 sequence 统一执行 key/value canonicalization，并对重复 key fail closed；补 tuple/list、小写 key、重复 key 的导出测试。

## 通过项与边界

- `test_swmm_io.py` 的 22 个测试在本轮确实通过；核心行的有限数/范围、大小/行数/控制字符、missing coordinate、self-loop、dangling reference、case-insensitive section 和基础 round-trip 覆盖是有效起点。
- 解析器没有导入 `subprocess`/文件 I/O，当前 owned path 中未发现执行 INP include 或打开用户路径的实现。
- 未在本轮运行：全量 pytest、Black/isort/Flake8/mypy、EPA SWMM engine、Docker、网络 job worker、Compose 或最终 G0/G2/AC gate；不能将实施摘要中的历史结果当作本轮通过证据。

## 最终判定

**不批准。** 至少先修复 F-01～F-04 的安全/拓扑/runnable 阻断项，并补齐 F-07 的固定 fixture 证据；随后重新运行本审查列出的定向测试和真实 profile round-trip，再进行独立复审。
