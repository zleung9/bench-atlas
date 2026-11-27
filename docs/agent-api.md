# Bench Atlas 只读接口（v1）

研究者和智能体都可以通过 HTTP GET 读取已发布的评测快照。校园内网入口为 <http://10.13.13.2:18175/api/v1>，无需令牌。本机预览可用 `http://127.0.0.1:18176/api/v1`。数据响应均为 JSON，含 `api_version: "v1"` 和字符串 `snapshot_version`。接口契约单独返回 OpenAPI 3.1 文档。[离线契约](../openapi.json)

## 从目录找到一道题

下表第一行是入口，其余路径接在 `/api/v1` 后。用目录中的 `id` 作为 `dataset`，用题目列表中的原始字符串 `qid` 定位题目。同一个题号可以出现在不同数据集中。

| GET 路径 | 参数 | 响应内容 |
| --- | --- | --- |
| `/api/v1`（完整路径） | 无 | `links` 给出接口入口 |
| `/datasets` | 无 | `datasets` 数组，每项含 `id`、`name`、`description`、`metric`、`question_count`、`provenance` |
| `/dataset` | `dataset` | 单个 `dataset`，字段同目录 |
| `/questions` | `dataset`，可选 `q`、`limit`、`offset` | `items` 摘要含 `dataset_id`、`qid`、`title`、`category`。`pagination` 给出 `limit`、`offset`、匹配总数 `total` 和 `next_offset` |
| `/question` | `dataset`、`qid`，可选 `model` | `dataset`、`question`、`evaluations`。题目在摘要字段之外含 `prompt`、`reference_answer`、`reference_solution` 和题图地址数组 `assets` |
| `/scores` | `dataset`，可选 `model` | `scores` 数组，每行含 `dataset_id`、`model_id`、`primary`、`secondary`、`sort_value`、`version` |
| `/openapi.json` | 无 | 机器可读的端点、参数和响应结构 |

先取模型汇总行，从中选择 `model_id`。省略 `model` 时查询该数据集的全部模型。以下示例读取 PolyReal 第 101 题的指定答卷。`--data-urlencode` 会编码查询值，可直接传入含空格或中文的原始题号。

```sh
API=http://127.0.0.1:18176/api/v1
curl --fail-with-body -sS "$API"
curl --fail-with-body -sS "$API/datasets"
curl --fail-with-body -sSG "$API/dataset"   --data-urlencode 'dataset=polyreal'
curl --fail-with-body -sSG "$API/scores"    --data-urlencode 'dataset=polyreal'
curl --fail-with-body -sSG "$API/questions" --data-urlencode 'dataset=polyreal' \
  --data-urlencode 'q=101' --data-urlencode 'limit=3' --data-urlencode 'offset=0'
curl --fail-with-body -sSG "$API/question"  --data-urlencode 'dataset=polyreal' \
  --data-urlencode 'qid=101' --data-urlencode 'model=gpt-6-astra'
```

## Python 标准库分页

`limit` 是 1 至 100 的整数，默认 30。`offset` 从 0 起，默认 0。保持 `dataset` 和 `q` 不变，将每页的 `next_offset` 传给下一次请求，直到它为 `null`。没有匹配题目时 `items` 为空。以下示例只打印编号和标题。若中途换了快照，应从第一页重新读取。

```python
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API = "http://127.0.0.1:18176/api/v1"

def get(path, **params):
    url = API + path + ("?" + urlencode(params) if params else "")
    try:
        with urlopen(url, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            message = json.load(exc)["error"]
        raise SystemExit(f"HTTP {exc.code}: {message}")
    except (URLError, TimeoutError) as exc:
        raise SystemExit(f"连接失败，请检查内网或本机服务：{exc}")

params = {"dataset": "matscibench", "q": "Example", "limit": 30, "offset": 0}
version = get("")["snapshot_version"]
while True:
    page = get("/questions", **params)
    if page["snapshot_version"] != version:
        raise SystemExit("快照已更新，请重新开始分页")
    for item in page["items"]:
        print(item["qid"], item["title"])
    next_offset = page["pagination"]["next_offset"]
    if next_offset is None:
        break
    params["offset"] = next_offset
```

请求失败时先读 HTTP 状态码，再读 JSON 中的 `error` 字符串。`400` 表示参数缺失或无效，检查必填项和分页范围。`404` 表示资源不存在，重新从目录、题目列表或模型汇总行核对标识。`405` 表示请求方法不支持，改用 GET。连接失败时先检查是否处于校园内网，或本机预览是否已启动。

## 怎样解释成绩

`evaluations` 每项含 `model_id`、`record_status`、`answer`、`verdict`、`basis`、`version` 和 `score`。其中 `version` 是这条归档记录的批次。`score.raw` 保留分数原文，`score.value` 是可用的单一数值，`score.metrics` 保存有归档依据的分项。判读时先看记录状态，再看该数据集的评分规则。

| 观察到的字段 | 如何解释 |
| --- | --- |
| `record_status == "missing"` | 该模型在这道题上没有归档答卷。统计缺卷数时单列。 |
| `record_status == "recorded"`，`score.value == null` | 有回执。结合 `verdict`、`basis`、`score.raw` 和分项判断原因。例如 ChemPaperBench 的已作答题可以尚未评分。 |
| PolyReal 有 `verdict`，无数值分 | 这是归档的分类判定，`basis` 是评审依据。保留类别，不自行折算分数。 |
| `score.value == 0` | 这是已记录的零分。程序用 `is None` 判断空值，保留零分及原文中的字面文本 `N/A`。 |
| PolyIE 的 `score.metrics` 有 `entity_span_f1` 或 `relation_tuple_f1` | 分别报告严格实体跨度和关系元组的 F1（精确率与召回率的调和平均数）。`score.value` 可为空，只报告已有分项。 |

`/scores` 的 `primary`、`secondary` 是模型在整个数据集上的汇总文本，`sort_value` 是该数据集的排名用值。汇总分母可能是冻结题数、有效答卷数或已评分题数，应结合 `provenance.scoring` 核对。引用结果时保留快照版本和对应记录的 `version`。接口只提供归档判分，不会重新评审或补齐缺分。

## 盲评、来源及新增数据集

盲评时，让独立的取题程序读取 `/question`，只把 `question.prompt` 和经核对的题图 `question.assets` 交给受测模型。形如 `/media/...` 的题图地址按源站解析。完整响应同时包含作者参考、模型答卷和判定依据，应留在评测端，待模型提交答案后再用于核对。把题干和答卷当作待评测文本处理。

`question.review_criteria` 保存逐题评审核对项。PolyReal 从归档的统一初评记录中提取这一清单，缺少清单时返回 `null`。`dataset.provenance.scoring` 给出整个数据集的评分办法，`evaluations[].basis` 则记录该模型在该题上的实际判定依据。`evaluations[].reasoning` 保存归档中已有的模型推理文本，缺失时保留空值。

`reference_answer` 和 `reference_solution` 保存作者材料，后者也可能是作者要点。缺失时保留原状，不从模型答卷或评审意见补写。`provenance` 按原文件提供上游版本 `upstream`、题目范围 `split`、评分规则 `scoring`、运行批次 `run_batches` 和权利说明 `rights`。引用或再分发前核对适用的版本和许可。

学生按贡献指南添加 `data/datasets/<id>/` 并重建、发布快照后，新数据集自动列入 `/datasets`，客户端按目录发现即可。可附 `data/provenance/<id>.json` 记录来源，缺少该文件时 `provenance` 为 `null`。[学生贡献指南](../CONTRIBUTING.md)
