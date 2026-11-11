# 模型与代码用途

## 从哪里调用

|类别|入口或位置|用途与限制|
|---|---|---|
|活动双模型|`runtime/predict_active.py`；外部 `WCPP/runtime/active-model.json`|默认分析入口；逐项验证工件、依赖、队列和加载状态|
|活动推理实现|`runtime/prediction_engine.py`、`active_route_engines.py`、`v4`、`v5`、`v6`、`continental`、`aus_expert`|按路由使用，不能单凭目录版本号删除|
|欧国联研究|`runtime/nations_league`|按等级/阶段隔离，未晋级；见[说明](../docs/nations-league-model.md)|
|日联杯研究|`runtime/jleague_cup`|旧edition与八强迁移候选独立；见[八强研究](../docs/jleague-cup-quarterfinal-research.md)|
|独立实验与诊断|`runtime/research`、`research_program`、`v2_test`、`optimization*`、`v7`、`upset*`、`score_adjustment`|候选和实验不等于活动模型；保留时间隔离评价与冻结证据|
|历史实现与共享依赖|`runtime/wcpp.py`、`shadow.py`、`v2`、`v3`|兼容、测试和复现仍使用；其中v2数学函数被日联杯和欧国联直接导入|
|仓库内历史参数|`models/main-v1`、`models/main-v1.1`|旧参数/验证快照，不是当前活动注册表，不含完整可运行历史；保留核对与复现用途|

## 清理边界

先查Python导入、CLI、测试、文档和外部工件依赖，再判定文件是否可删。模型源码可能参与工件哈希，移动或改写也可能使已冻结候选无法重载。旧参数快照、失败实验、未通过验收候选并非无用文件：仍用于对照、复盘或回滚。

基础安装与测试统一使用根目录 `requirements.txt` 和 `requirements-test.txt`；可选研究依赖仍在 `runtime/research/requirements.txt`。历史工具自身的限制不覆盖项目当前冻结规范。

2026-11-11整理删除了重复依赖清单及过时交接页，保留所有模型参数和Python实现。历史原始交接材料继续位于 `docs/archive/`，当前状态只在 [CURRENT_STATE.md](../CURRENT_STATE.md) 维护。
