# 分赛事未来验证与晋级审查

research_program/promotion_review.py 只建立审查前门槛，从不写活动注册表。

需要真实提前冻结的research-preregistration记录：固定cohort/phase、Current与候选版本及工件SHA、完整比赛ID集合、最小样本、覆盖、质量容忍及显著性门槛。原500+200协议保持原样，本模块不追认或重新注册它。

只接收Current与候选的同输入、同截止、首次赛前冻结；核验模型身份、矩阵、时间、90分钟赛果。全量ID不得缺失或重复。比较唯一Primary同场净增及配对二项检验、WDL、LogLoss/Brier/RPS、置信校准及尾部校准。实际大小球五类结算和来源as-of审计须提供独立审查回执，不能以固定2.5或点比分替代。

即使所有软件检查满足，也只返回READY_FOR_INDEPENDENT_REVIEW。哈希回执及来源真实性仍须审查，不能由调用方自称“已验证”就自动部署。当前没有这样的未来独立验证包，实际状态BLOCKED；没有晋级。

版本和回滚沿用 docs/version-management.md：保留模型与源代码身份、独立候选及旧冻结；晋级另开PR、逐赛事检查并人工审查。每联赛监控Top1、WDL、真实线结算、覆盖和校准；旧版本不删。不能通过此函数修改Current/Test、永久lambda或冠军。
