# 运行与验证

需要 Python 3.12、requirements.txt、requirements-test.txt 和外部运行资产。单独克隆源码不能恢复完整预测环境。

```bash
python runtime/predict_active.py fixture.json --registry /workspace/WCPP/runtime/active-model.json --out NEW.json
python scripts/check_repository.py
python scripts/run_tests.py
```

输出必须使用新路径。CLI追加双模型冻结，run仅返回对象，不发布。恢复入口及资产身份应与当前环境清单核验。

完整重构说明见 [六阶段交付](docs/refactor-completion-20261103.md)。

恢复失败交付：`python runtime/predict_active.py --out NEW.json --resume-publication`。

跨平台检查：`python scripts/run_tests.py --layer portable`；真实运行资产严格检查：`python scripts/run_tests.py --layer integration`（任何跳过均失败）。

历史first_known缺失仍未补齐，历史固定配方证据缺失必须恢复后才能通过严格集成。源码重构不代表模型训练完成或准确率提高。
