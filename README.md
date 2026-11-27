# 高分子模型评测看板

内网入口为 <http://10.13.13.2:18175/>。应用部署在 cpu75 的内网地址上。浏览器通过成绩总表进入模型题目列表和单题详情，可以按题号、题面、类别和判定筛选，也可以并排核对同题答卷。

开发和维护前，请先阅读[智能体维护指南](AGENTS.md)，了解五套题库的评分口径、原生字段以及题目页的交互约定。

## 数据与更新

原始材料位于 `hpc:/polymer-multi-modal/Benchmark/`。每项成绩保留来源批次，空字段保留为空。当前导入器读取四个冻结评测集的 20261120 归档及相应的 20261126 Gemini 续测，以及 PolyReal 汇总表和逐题判定。新增批次文件名或格式变更时，先更新对应导入器中的批次映射，再核对统计口径。已有来源追加字段时，也应确认到网页字段的映射。

以下命令从本目录执行。同步到新建目录可以防止旧版本文件混入新快照。

```bash
snapshot=$(mktemp -d "$PWD/source-refresh.XXXXXXXX")
rsync -a -e 'ssh -o BatchMode=yes -o UpdateHostKeys=no' hpc:/polymer-multi-modal/Benchmark/ "$snapshot/"
python3 build.py --source "$snapshot" --version '填写实际归档和续测日期'
```

构建会生成包含题面、参考答案、判分、答卷和已关联题图的单个 SQLite 文件。外键、唯一性与完整性检查通过后替换本机待发布快照。可以在本机启动预览，再核对首页和逐题页。

```bash
python3 server.py --port 18175
# 在另一个终端验证
python3 verify_release.py http://127.0.0.1:18175 --source "$snapshot"
```

## 发布

```bash
bash deploy/publish.sh
python3 verify_release.py http://10.13.13.2:18175 --source "$snapshot"
```

发布脚本通过已配置的 `CPU75` 登录别名连接，核对远端主机名和内网地址，再上传独立版本目录。服务只监听 `10.13.13.2:18175`。systemd 负责进程启动与重启，进程使用临时低权限账号。发布脚本校验上传文件的摘要，切换版本后检查线上总表接口。若启动检查失败且存在旧版，脚本切回旧版。

- 服务名为 `polymer-benchmark-board.service`。用 `systemctl status polymer-benchmark-board` 查看状态。
- 部署根目录为 `/opt/polymer-benchmark-board`，`current` 指向正在使用的版本。每次发布保留旧版，可将 `current` 指回已验证版本并重启该服务。
- 生产目录只包含网页、服务程序和发布快照。原始运行回执以及答题请求归档保存在数据源端。
