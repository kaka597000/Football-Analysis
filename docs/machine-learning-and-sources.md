# 机器学习test模型与数据源

用户授权于2026-10-12。当前模型WCPP-MAIN-1.2保持原算法与活动文件；test模型的新版本WCPP-ML-0.1使用LightGBM Poisson残差模型修正DC比分矩阵。旧test版本WCPP-WEIGHTED-0.1及其预测记录保留，不追改或混算成绩。

## 模型与验证

特征来自严格早于目标比赛日期的历史：近期及90天进失球、积分表现、主客场差异、双方攻防强度、同联赛比赛间隔、历史射正/射偏/控球率。38个特征槽包含有来源的历史xG及缺失覆盖率，当前实际训练集没有合格xG记录，所以不能称为已训练xG影响。阵容、伤停、完整赛程/旅行、数学战意、诚信和赔率数值调整尚未训练，仍需独立证据审查。

按日期分割训练、选择及测试，按月扩展历史拟合DC强度；LightGBM基线offset和特征均排除目标日及未来比赛。只在选择段确定融合系数，测试段不参与调参。测试后用全部已审计历史重训部署模型；检验仍是已看过历史的开发诊断，非未来冻结验证。未通过门槛仍可在用户授权test轨运行，不得替代当前模型或宣布正式升级。

安装与验证：

```bash
python -m pip install -r requirements.txt -r runtime/v6/requirements.txt
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m unittest discover -s runtime/v6 -p 'test_*.py'
python -m unittest discover -s runtime/data_sources -p 'test_*.py'
```

未来预测仍使用`runtime/predict_active.py fixture.json --out new-pair.json`，当前模型/test模型独立；`runtime/review_active.py`分别复盘，必须按test版本拆分统计。未知赛事分组不能套用其他男女、年龄、级别或阶段模型。

批量重训使用`python runtime/v6/train_registered.py --out NEW_DIRECTORY`，生成新目录，不自动修改活动注册表；其中`NEW_DIRECTORY`为尚不存在的输出目录。读取当前注册模型已审计历史，再按provider ID、日期、比分核对现存5Dollar过程归档，不盲目追加其他来源。

## 新接口

客户端：`runtime/data_sources/clients.py`；登记：`runtime/data_sources/registry.json`。

| 来源 | 用途 | 凭证环境变量 |
|---|---|---|
| football-data.org | 赛程、赛果、积分榜、射手榜等交叉核验 | WCPP_FOOTBALL_DATA_API_KEY |
| API-Football（API-SPORTS直连） | 首发、伤停、过程统计、事件、盘口补充 | WCPP_API_FOOTBALL_KEY |

只读调用示例，实际请求依赖环境绑定，不含密钥参数：

```bash
python runtime/data_sources/cli.py --out new-auth-audit.json
python runtime/data_sources/cli.py --provider football-data --path /competitions/PL/matches --params '{"season":2026}' --out new-fixtures.json
python runtime/data_sources/cli.py --provider api-football --path /leagues --params '{"country":"Japan"}' --out new-leagues.json
python runtime/data_sources/cli.py --provider api-football --match-id PROVIDER_NUMERIC_ID --out new-context.json
```

`PROVIDER_NUMERIC_ID`仅为占位说明，必须先获取并核对真实ID。来源ID不能互换；返回资料先留raw staging，核实球队、比赛时间、性别年龄及阶段、90分钟比分、盘口机构和发布时间后，才能进入预测或Canonical数据库。HTTP 200内含errors也算失败。缺密钥不发请求、不声称权限已验证；拒绝凭证query参数、阻止跨域重定向、清理响应密钥。没有绑定的钥匙必须在环境设置安全填写，不能复制到脚本或文件。

官方覆盖表与价目表描述可购买能力，账号实际权限、历史赛季、xG字段和赔率保留期都需要实测。API-Football的免费额度登记为100次/天且历史赛季有限，不适合无计划地整库抓取。

## 参考文章与5Dollar

用户提供CSDN文章直接访问HTTP 521，公开阅读入口成功取得正文。借鉴其采集/存储/训练分层；示例的本场射门/传球未明确赛前可用时间，不能直接用作赛前特征，示例框架没有证明目标命中率。无需为了参考文章改成MySQL或另建用户登录系统。

用户截图登记5Dollar付费服务$5/月、14,400次/天及EU/亚洲/大小球赔率。2026-10-12实际/status返回community、300次/小时、每分钟突发20、无日限额字段值；不认定已经获得付费额度。截图不证明xG、首发或伤停覆盖。5Dollar的API-Football兼容入口不是API-SPORTS的完整服务；官方兼容文档明确缺少首发/阵型、伤停和球员统计等模块，不能把两个站点能力混用。

## W-5参考层

参考essowedeo/football-prediction的五角色分工，新增runtime/v6/five_role_review.py并附加到test输出。当前仅生成统计、战术、新闻战意负荷、市场、风险证据包，未调用五个外部LLM、不改变概率。真实智能体调用和LLM融合权重尚未实现。源项目含随机失败兜底、目标命中率倒推脚本及筛选/二分类指标，不用于本项目性能依据。
