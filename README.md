# Football-Analysis · WCPP

足球赛事研究与赛前预测项目：输出唯一精确比分参考、胜平负概率及指定大小球盘口的结算概率，并保存证据、完整比分矩阵与研究记录。

目前运行的是独立云端候选0.3，包含战意、实力、全赛事赛程、阵容、战术与场地条件审计。原Champion V21.5完整数值执行链尚未恢复，保持原身份；没有证明精确比分70%或胜平负/大小球80%的目标已实现。

## 安装与测试

使用Python 3.12。在已有checkout内运行，不需要新建Git worktree。

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/run_tests.py
```

四组测试分别运行：原基线、候选0.2、上下文候选0.3和天皇杯数据库解析器。测试可离线运行；合成测试资料不用于训练或准确率证明。GitHub Actions会在push和pull request时运行同一命令。

## 当前研究入口

先准备真实模型产物和带来源、查询时间的赛前输入；这些文件在本地保存，仓库没有捏造的生产参数。

```bash
python runtime/v3/cli.py audit fixture.json --out outputs/audit.json
python runtime/v3/cli.py predict model.json fixture.json --out outputs/draft.json
```

输出文件拒绝覆盖。缺少官方阵容、完整全赛事赛程或其他关键证据时，结果标为OBSERVE，阻止研究冻结。只有兼容且独立验证的上下文系数才修正数值；未知数据不填零，不任意乘上“战意/疲劳系数”。

详细字段与训练、冻结、验收命令见[runtime/v3/README.md](runtime/v3/README.md)。

## 项目结构

| 目录 | 内容 |
|---|---|
| runtime/ | 原云端基线与Shadow研究实现 |
| runtime/v2/ | 对手强度校正、比分分布、校准与时间留出验证 |
| runtime/v3/ | 有来源的上下文特征、推荐门控及研究验收 |
| runtime/emperor_cup/ | JFA官方赛果解析与独立SQLite扩展库 |
| tests/fixtures/ | 人工构造的离线解析测试资料 |
| docs/ | 当前状态、历史规范与验证结论 |

## 数据与密钥

源码、规范和必要审计说明纳入Git。数据库、用户原始附件、网页/API快照、模型数据集、冻结输出及API密钥不上传；data/、reports/、packages/、recovered/与outputs/均忽略。

Canonical数据库仍在原云端WCPP目录保存，不通过Git分发。它的登记及实测SHA256是`8b83d0adb71cf2f4b26eab0acec29a235b84b4b24a5eed4c4475293bb736bcc0`。运行依赖该数据库的历史重放时，需自行提供本地文件路径。

可选5DollarFootballAPI凭据仅从环境变量`WCPP_5DOLLAR_API_KEY`读取。`.env.example`只有变量名，不会自动加载；通过终端、IDE或秘密管理器注入。密钥永不写入文件或日志。竞彩权限需独立验证；API密钥有效不代表已具备竞彩权限。

用户允许澳客竞彩足球页面；中国体彩官方与镜像保持禁止访问。市场用于最后校验与经验证的有界重排，不直接替代比分选择。Betfair连接尚未验证。

## 验证边界与版本管理

唯一正式KPI保持未来冻结Primary精确比分Top1；胜平负和大小球为辅助诊断。90分钟、加时、点球分列，不追认历史冻结。Champion、Selector4.1-RP1、Regime-MT-KL原定位和永久λ不变，500+200未重新预注册。

当前真实上下文训练样本不足，数值修正未启用。历史测试与最新交付说明见[候选0.2验证](docs/candidate-v2-validation.md)、[候选0.3交付](docs/candidate-v3-delivery.md)和[项目状态](docs/project-state.md)。docs/archive保留旧资料；其历史状态不能冒充最新能力。

```bash
git pull --ff-only
# 修改源码并运行测试
git add README.md runtime scripts tests docs requirements.txt .github .gitignore .python-version .env.example
git commit -m "Describe the change"
git push origin main
```

## 当前新主模型

当前研究执行入口为 [WCPP-MAIN-1.0](runtime/v4/README.md)，独立于原 Champion 参数。候选未通过验证时执行基线，完整结果见 [验证说明](docs/main-v1-validation.md)。

## 统一赛事库与赛果更新

数据消费入口已新增[统一赛事运行层](runtime/database/README.md)。旧主库只读保留，分散来源统一到`/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite`；赛事、赛季、球队身份、来源观察、修订与冲突分开登记。

```bash
python runtime/database/cli.py refresh --root /workspace/WCPP
python runtime/database/cli.py audit --root /workspace/WCPP
python runtime/database/cli.py export --root /workspace/WCPP
```

云端本机更新器按北京时间04:00、08:00、18:00运行；环境停止后需按startup instructions重启。比赛数量检查、来源清单导入与训练验证是不同状态。日本2026百年构想、2026/27常规赛及韩国附加赛分组，不能混合训练。数据库及原始快照仍不上传Git。
