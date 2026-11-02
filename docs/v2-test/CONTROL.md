# V2 Test 对照（Champion / 当前活动配方保持不动）

审计时间：2026-11-02 18:00（北京时间，UTC+8）。分支：`cursor/v2-test-phase1-architecture-audit-9c64`（V2 Test 审计轨；仓库代理分支名必须带 `cursor/` 前缀和 `-9c64` 后缀）。

当前活动研究入口 `runtime/predict_active.py` 与注册表 `WCPP-DUAL-5` 继续作为对照。本分支只增加审计文档和只读调用图检查，不改 MAIN-1.2、ML-0.1、Dixon–Coles ρ、LightGBM 残差或任何 `alpha`。

Champion V21.5 在本仓库里没有可执行实现，活动路径只把它写成未修改标记。在 V2 Test 用时间外样本证明净增命中之前，不得把研究融合、xG shadow、Egidi/Shin、分层选分或冷门分类器写成对照模型的替代。

活动工件仍在 `/workspace/WCPP`（本环境未挂载）。身份以 `manifests/runtime-assets-20261029-queue-supplement.json` 的哈希为准。缺文件时拒绝预测，不得静默换权重。
