# 现代欧冠采集阶段契约兼容

现有数据配置的 cohort 为 `uefa.champions:NEW_FORMAT:league_phase`，stages 为 `["NEW_FORMAT:league_phase"]`。旧 collector 只识别 `league_phase`，故合法现代联赛阶段被隔离。修复只识别这一明确时代/阶段组合，不截断任意前缀；配置使用短阶段时保留短值，配置使用时代限定值时保留限定值。cohort、路由 key、旧记录与生产配置不变。

要求赛季年份为整数且不早于2024，原生 slug 若存在必须是 league-phase，name 若存在其末段必须明确为 League Phase。scoreboard/summary 两侧均须满足阶段，且提供的 year/type 不得冲突。旧制分组、淘汰赛、未知前缀及冲突均拒绝。两个新增合成回归测试覆盖这些反例及两种配置表示；真实反例本轮未单独归档重放。

## 真实同场配对

使用数据仓留存 requests.jsonl/raw 中 espn:401915311；不请求ESPN新数据。两份响应解压后SHA均匹配原采集回执：

- scoreboard：17d2c4592254be4cc3eb3f87489880532c0f042884585dc99ba9f27837d8cf67；原捕获北京时间2026-11-05 03:44:50；URL https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard?dates=20261103&limit=1000
- summary：be7c1f4428fad6ee2b6577cd5669e92660a434049163d98be4dc47e288a85952；原捕获北京时间2026-11-05 13:50:09；URL https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/summary?event=401915311

scoreboard原生year=2026/type=14534/slug=league-phase；summary为同year/type，name末段League Phase。真实数据scope取自数据提交a88c1d5ec67ded62a6cf8e0043a52117e549f22f。

修复前 `regulation_row` 拒绝：phase or era not compatible。修复后返回原cohort、NEW_FORMAT:league_phase与常规时间2–0；共享 `certify_retained` 同样通过。临时目录仅放配置、这一条规范化记录和原始配对字节，`trainer._load` 接受1条、隔离0、排除0。没有调用train/fit，不把1条可读入当作达到120条训练门槛或未来效果证据。

## 复现和边界

针对测试：`python -m unittest discover -s runtime/automation -p test_collector.py -v`。
完整资产集成：`python scripts/run_tests.py --layer integration`。
本地真实重放脚本 `/tmp/ucl-preview.py`，输出 `/tmp/ucl-phase-validation/replay.log`；读取两个SHA对应gzip文件及requests原引用，调用regulation_row → certify_retained → 临时目录trainer._load。

精确提交的完整测试和远端CI在原草稿PR #51追加验收。此前根路径安全修复与ESPN阶段配对实现保留。没有新采集、真实训练、历史修改、工作流pin改动、模型权重改动或部署。未证明所有欧冠阶段/来源均已生产验收，未扩大允许阶段。
