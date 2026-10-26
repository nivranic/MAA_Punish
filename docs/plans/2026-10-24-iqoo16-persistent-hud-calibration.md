# 逆冕持久HUD校准 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让既有布局缓存保存并加载逐节点顶部HUD校准，校准正式专项的技能中心落点，为真实HP和厄债识别提供当前Context中的有效矩形。

**Architecture:** 复用八区offset文件、`LoadROIZone`自定义动作和`Context.override_pipeline`。普通加载继续只应用八区；显式逆冕入口要求同布局、同画面尺寸的HUD块，随后读回验证。两处`strategy_not_ready`、已有真实失败和训练707源保持，不部署或操作设备。

**Tech Stack:** Python 3.12、现有MaaFramework 5.14.2接口、pytest、UTF-8 JSON。

---

## 已确认事实与范围

- 顶部`检查血量`与逆冕两能量节点不在八区map；不能以底部corepass_zone表达顶部平移。现有writer整文件覆盖，直接向缓存加未消费字段不能完成持久校准。
- 新鲜实际HP矩形为`[70,15,140,29]`；红条完整矩形仍由实际样本确定，不把此例坐标写死到角色代码。
- ADB技能默认框`[853,571,124,126]`，旧缓存recognized`[942,594,124,126]`、offset`[89,23,0,0]`均有效。旧技能target`[915,690]`平移后为`[1004,713]`，只在框内并不证明命中。专项应在当前Context把技能基础目标改为该默认框中心，沿既有平移得到当前已识别框中心；不改共享资源默认target。
- 当前手机在手动路线自然战后结算，未点退出。本计划仅离线实现；正式Maa/C、全部任务、最终APK与还原仍待完成。分支mine、不commit/push，保留其他dirty。

## Task 1: 白名单HUD块与布局绑定

**Files:** Create `agent/action/basics/InterfaceZone/hud_roi_calibration.py`；Create `agent/tests/test_hud_roi_calibration.py`。

调用方在`LoadROIZone.custom_action_param`中显式提供：

```json
{
    "controller": "adb",
    "from_screen": true,
    "hud_roi_calibration": {
        "schema": 1,
        "screen_size": [1280, 720],
        "source_frame_sha256": "64位实际来源图像hash",
        "nodes": {
            "检查血量": [70, 15, 140, 29],
            "检查逆冕能量空": [75, 62, 215, 3]
        }
    }
}
```

这里的矩形只示意接口，能量范围需实际校准后才提供。只允许这两个节点且必须齐全。`schema`严格整数1，尺寸与矩形为非bool整数、宽高正且完全位于当前画面；hash为64位小写十六进制。hash仅供追溯，不证明语义或设备验收。不读取调用方任意文件路径。

保存块仍名`hud_roi_calibration`，增加受支持controller和`layout_fingerprint`。fingerprint由八区的default/recognized/offset/hit规范化计算，且区域必须与该controller默认一致、几何等式成立。提供`hud_overrides_from_cache(cache_data, controller, screen_size)`用于验证与生成：

```python
{
    name: {"recognition": {"param": {"roi": rect, "roi_offset": [0, 0, 0, 0]}}}
    for name, rect in calibrated_nodes.items()
}
```

缺失、坏型、未知键、尺寸/controller不符或fingerprint漂移均明确拒绝，不能补默认值取得专项许可。

## Task 2: 接通现有保存者和加载者

**Files:** Modify `ReadRoiZone.py`、`LoadRoiZone.py`；Test `test_roi_zone_cache.py`及新增HUD测试。

1. `capture_roi_zones(context, controller, *, hud_calibration=None)`复用共享`capture_frame(context)`，先确认当前截图Job成功和有效图像，再执行已有八区识别。所有区域失败／停止／坏校准均保留原缓存。
2. 有显式合法HUD参数时，连同新识别的八区与绑定fingerprint一次保存；未提供时仅在原块有效、controller/画面尺寸与八区fingerprint都不变时保留该块。布局改变则丢弃旧HUD块，后续专项明确要求重校准。普通八区仍可成功更新。
3. 同目录临时文件加原子替换，避免部分写入。临时文件只能在该缓存父目录创建及清理；不删除目录或其它数据。
4. `LoadRoiZone.run`只接受`from_screen is True`时的显式`hud_roi_calibration`，拒绝坏类型／只加载时夹带保存参数；正常调用仍沿原入口。
5. `apply_offsets(context, controller, *, hud_screen_size=None)`：None保留普通八区行为；显式尺寸则先验证HUD块，再将八区与HUD overrides一次应用。缺失／坏校准不能静默跳过。不要额外读取Controller getter确定尺寸，尺寸由当前专项入口已有几何读取提供。

## Task 3: 专项入口读回与技能中心

**Files:** Modify `agent/action/combat/entry/inverse_crown_layout.py`；Test `agent/tests/test_inverse_crown_layout.py`。

入口保留现有八区严格校验和尺寸变化拒绝，向loader传当前已读屏幕尺寸，并验证缓存字节未变。读回两个顶部节点，必须保留OCR／ColorMatch类型、精确ROI与零roi_offset；不修改阈值、HP解析或角色检测器。

仅专项当前Context覆盖技能target为ADB默认skill_zone中心，保留loader的实际target_offset；核最终`primary_input_position("技能")`精确等于recognized框中心。其它动作、共享默认target、缓存坐标及普通模式不改。不用框内判定代替中心等式。

## Task 4: 定向验证与独审

- 先补失败回归：旧校准被重采丢失、布局变动误复用、坏HUD块、截图失败缓存污染、loader跳过或只应用部分节点、技能仍落旧边缘。
- 测试白名单／严格数值／越界／controller或尺寸漂移、原缓存不变、重复加载不叠加、普通No和无HUD旧缓存兼容、两正式门仍关闭。
- 在真实Maa Context上离线加载并读回overlay，仅是SDK组合证据，不创建ADB控制器，不操作设备。
- 使用`build-debug/venv/Scripts/python.exe -B -X utf8 -m pytest -c agent/pytest.ini ... -q -p no:cacheprovider`运行相关cache/layout/profile集合，必要时扩展直接消费者，不机械全仓。
- 冻结本批dirty基准、增量patch与实际结果，再独审。原有v11d707训练Stage和所有已执行收据核同。

## Task 5: 后续真实校准与信号

离线独审通过后另安排实际缓存保存／重启加载与专项Context读回。只有以真实HUD确认后提供的矩形才可计入设备校准。红条／技能ready producer、相／激活／满蓄／末段／终结信号及正式消费者继续逐项实现，不以新的存储接口或原手动路线代替。
