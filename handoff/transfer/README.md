# WCPP 私有交付附件

此分支专门提供单场离线复现包下载；源码检查点仍固定在checkpoint/wcpp-handoff-20261021-0956（d7c0d214369db04bf5c9deb2fa7b14d8df23c944），main未修改。

在GitHub打开本目录的ZIP，点击Download raw file下载，再按ZIP内README执行。
ZIP：WCPP_双模型交接包_20261021.zip，1,387,486字节。
SHA256：b26a56ca5c8c7cfb9c1adaddc41e4def2fd831be7262bae5bbaa6fa5f956f6e5。

仓库为私有，需登录有权限的GitHub账号。源码推送/API读取已验证；uploads.github.com附件上传返回401，未绕过鉴权或打印凭据，空草稿已清理，故改用独立交付分支保存这一小型ZIP。

包内有两份实际工件、原输入、完整矩阵、注册表、冻结索引、原记录和哈希清单；没有完整数据库或真实密钥。
已在本Cloud解压到新路径并禁止读取原工作区后通过离线重放，两份51×51矩阵最大差异0。
另一Cloud尚未实际重放；手工综合记录独立保存，不将历史复现当成新预测或准确率提升。
原单场stdout/stderr未找到，缺失项详见ZIP内missing-and-scope.json。
