# 学生如何提交一份评测

## 1. 下载并本地预览

在 GitHub 上 Fork 本仓库，将自己的副本克隆到本机。需要 Python 3.9 或更高版本。以下命令都从仓库根目录运行。

```bash
git clone https://github.com/YOUR-ACCOUNT/bench-atlas.git
cd bench-atlas
git switch -c add-my-benchmark
python3 server.py --port 18176
```

打开 <http://127.0.0.1:18176/>。服务器直接读取仓库内的 SQLite 快照。用 Ctrl+C 停止预览。

## 2. 准备题库或一次新的模型评测

复制[单题示例](examples/student-demo/)，即可用示例数据试走整个流程。

```bash
cp -R examples/student-demo data/datasets/student-demo
```

正式提交时，将示例目录改为自己的数据集编号，并使 `metadata.json` 的 `id` 与目录名一致。新增目录会自动进入看板。若只是给已有题库添加一个模型，进入它已有的目录，在 `scores.jsonl` 追加汇总行，在 `answers/` 增加以模型命名的文件即可。给同一模型更新结果时，替换对应的汇总与逐题记录，写清新版本。Git 提交历史保存各次修订。

| 文件 | 填写内容 |
| --- | --- |
| `metadata.json` | 数据集编号 `id`、展示名 `name`、主指标 `metric`、说明 `description`。说明中写明来源、所选范围和任务。 |
| `questions.jsonl` | 每题一行。`qid` 是数据集内唯一的字符串编号。`title`、`prompt`、`reference`、`solution`、`category` 分别保存标题、题干、作者答案、作者解法或要点、类别。`assets` 依次列出题图。行顺序即网页题序。 |
| `scores.jsonl` | 每模型一行。`model`、`primary`、`secondary`、`sort_value`、`version` 分别保存模型名、主指标显示文本、补充指标、排序用数值、批次。写明分子、分母和未评分数量。 |
| `answers/<model>.jsonl` | 已有答卷每题一行。保存 `qid`、`model`、`answer`、`reasoning`、`verdict`、`score`、`basis`、`version`。`basis` 存评审依据。`score` 是显示文本，例如 `"1"`。 |
| `data/provenance/` | 参照现有题库的记录补充数据来源、原始版本、取样范围、评测与评分规则、运行批次和许可信息。 |
| `data/manifest.json` | 全库快照版本 `version` 和目录展示顺序 `datasets`。新批次时更新版本。未列出的新目录追加在末尾。 |

所有文本用 UTF-8 编码。JSONL 文件每行是一个完整 JSON 对象，字段参照示例保留，未知的可选值写 `null`。缺少题图用 `[]`。没有收到答卷的题目仍可列在题库内。数据库记录归档分数，构建时校验引用和数据类型，评测者负责计算每项成绩。

需要题图时，把 PNG、JPEG 或 WebP 图片存为 `data/assets/<SHA-256摘要>.<png|jpg|webp>`，在 `assets` 中填写 `/media/<摘要>.<扩展名>`。可用以下命令计算摘要。

```bash
python3 -c "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())" your-image.png
```

## 3. 重建并验收

```bash
python3 data_bundle.py build
python3 data_bundle.py check
python3 server.py --port 18176
```

在另一个终端运行接口验收。打开本地看板，进入你的数据集，核对总分、几条答卷、题图和判定依据。示例可从[本地单题链接](http://127.0.0.1:18176/#view=detail&dataset=student-demo&qid=q1&model=example-model)打开。

```bash
python3 verify_release.py http://127.0.0.1:18176/
```

`build` 只在构建成功后替换 `release/board.sqlite`。`check` 会从仓库文本和图片另外构建一份快照，逐表比较全部记录与题序。做界面改动时，也可用 Node.js 22+ 和 Chrome 运行 `node check_browser.mjs http://127.0.0.1:18176/`。

## 4. 提交给维护者合并

```bash
git add data release/board.sqlite
git diff --cached --stat
git commit -m "Add results for my benchmark"
git push -u origin HEAD
```

在 GitHub 为该分支创建拉取请求（Pull Request，简称 PR），目标选 `zleung9/bench-atlas` 的 `main`。说明题库来源、模型与运行时间、题数和分母、评分规则、待定数量，以及验收结果。维护者可以查看逐行差异并在本地复查快照，合并后按发布流程更新正式看板。

如果两份 PR 同时修改快照，先合并各自的数据文本和题图，再运行 `python3 data_bundle.py build` 和 `check`，将重新生成的数据库加入合并提交。
