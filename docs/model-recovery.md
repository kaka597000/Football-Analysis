HELLO BOSS

# 模型恢复核查结论

## 可恢复内容

- 17个模型版本登记及其中保存的参数已提取到recovered/model-parameters-from-canonical.json，未修改原库。
- R1启动候选保存BR/ES/IT/PT四联赛球队攻击、防守、主场、衰减与滚动状态参数；状态为unvalidated_cross_league，不是Champion。
- R2研究/Shadow保存四联赛方向和比分权重；仍未晋级。
- Regime-MT-KL保存标准化均值、尺度、类别、逻辑回归系数和截距等开发参数；可作为影子实现恢复依据，但仍DO_NOT_PROMOTE，现有实现源码未取得。
- 1751条历史Replay的λ对均为有限正数，其中EPL为380条，可支持历史基线重放与开发对照，不能据此视作未来正式验证。
- 226条forecast Payload及55条近期Payload均可解析为JSON。字段覆盖见详细清单，不代表每条包含完整执行链。

## 实测矩阵检查

- 115条历史矩阵：113条20×20、2条40×40，形状、有限非负值、质量字段一致性通过，质量和为1（浮点容差内）。
- 55条v2矩阵：均8×8，形状、有限非负值、质量字段一致性通过；截断矩阵质量约0.981739至0.999999609，raw_mass与未分配质量之和均为1（1e-8容差）。未分配质量不能冒充已定义的尾部分布。
- 矩阵哈希序列化算法仍待对应源码确认，本轮没有试猜算法并宣称哈希通过。

## 核心阻塞

Champion登记明确为label_retained_numeric_artifact_unavailable，parameters_json为null；另一历史版本登记修复parameters_json为空对象，并标注numeric_parameters_recovered=false。因此V21.5完整数值Champion尚不能恢复。

仍需：Champion原始参数产物；对应源码、入口与依赖锁定；Selector4.1-RP1、TFA/MPF/URE/ISP和市场有界重排实现；特征处理与来源时间证据；尾部及矩阵哈希约定；10月6日12场原始增量。研究参数和旧Payload均不能替代这些材料。

15个赛事族profile中2个ACTIVE_SHADOW、1个DEVELOPMENT_ONLY、4个NEEDS_CALIBRATION、8个NEEDS_DATA，不能称已全部训练完成。

## 当前执行状态

原数据库审计前后哈希一致。未训练、未修改永久λ、未升级Champion、未补造概率、未追认冻结、未写回历史预测。

API最新验证为/status HTTP200、community套餐；竞彩接口固定types=jingcailottery返回403 insufficient_plan，需Ultra。旧报告中“密钥缺失”是历史状态。公开竞彩清单备选已发现，比赛身份与时间仍需交叉核验。
