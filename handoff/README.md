# WCPP 2026-10-21 源码检查点

这是独立副本中的源码检查点，不改变原工作目录或main。
checkpoint-source-manifest.json登记原目录193个源码/规范文件的原路径、字节数和SHA256，及原工作区状态。
活动注册表、模型工件、数据库、冻结输出不在本源码仓库；配套交接包单独提供。

## 离线历史复现

解压配套交接包，在Python 3.12环境安装requirements-replay.txt后执行：
`PYTHONDONTWRITEBYTECODE=1 python handoff/replay.py --bundle /path/to/unpacked-bundle --out /tmp/wcpp-replay-new`

脚本先核对包内清单，再调用原predict_active.run；仅在适配层映射路径和历史时钟，原工件/输入/矩阵不改。
网络、SQLite和子进程调用在重放进程中禁止；不会调用冻结CLI或写入赛前记录。
回放是历史执行一致性检查，不是新预测，不证明准确率提升。

本检查点保留已有当前/test研究链；不意味着所有候选已训练、接通或获准晋级。
源码存在硬编码默认工作区路径，跨环境运行必须使用配套适配层，不能直接照搬生产CLI。
