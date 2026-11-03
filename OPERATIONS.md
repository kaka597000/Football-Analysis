# 运行与验证

需要 Python 3.12、requirements.txt、requirements-test.txt 和外部运行资产。单独克隆源码不能恢复完整预测环境。

```bash
python runtime/predict_active.py fixture.json --registry /workspace/WCPP/runtime/active-model.json --out NEW.json
python scripts/check_repository.py
python scripts/run_tests.py
```

输出必须使用新路径。CLI追加双模型冻结，run仅返回对象，不发布。恢复入口及资产身份应与当前环境清单核验。

尚待处理：预测引擎仍以赛前为范围，CLI旧live选项并不证明支持滚球；fcntl锁尚未跨平台抽象；冻结后交付文件失败没有事务恢复；first_known_at缺失不能认证历史as-of回测；公开CI缺资产的跳过不等于真实集成通过。

本轮优先实施PR-01至PR-03；ML共享核心、统一路径契约及跨平台测试属于后续PR-04至PR-06。未启用分支保护、未改定时任务或模型参数。
