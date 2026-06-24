# World Cup Gold 层

Silver `wc_*` 表导出的 **事实卡**（JSONL），供 EP04 RAG document loader 消费。

## 生成

```bash
# 全量
bash scripts/api.sh exec python ../../scripts/etl/worldcup/fact_cards.py

# 单届（如 2022）
bash scripts/api.sh exec python ../../scripts/etl/worldcup/fact_cards.py --tournament WC-2022
```

前置：Silver ETL 已跑完且 `validate.py` 全绿。

## 输出

| 文件 | 说明 |
| :--- | :--- |
| `fact_cards/matches.jsonl` | 比赛摘要 |
| `fact_cards/players.jsonl` | 球员摘要（基础） |
| `fact_cards/player_careers.jsonl` | **球员职业生涯**（进球/出场/奖项聚合，RAG 推荐） |
| `fact_cards/tournaments.jsonl` | 赛会摘要 |
| `fact_cards/samples.jsonl` | 10 条 spot-check 样例 |

每条记录：`id`、`entity_type`、`source_ids`、`text`。

模型说明见 [`docs/tech/worldcup-data-model.md`](../../docs/tech/worldcup-data-model.md)。
