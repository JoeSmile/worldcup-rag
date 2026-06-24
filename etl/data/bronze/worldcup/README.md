# World Cup — Bronze（原始 CSV）

把约 30 个世界杯 CSV **直接放进本目录**，保持原始文件名即可。

```text
memoryOS/data/bronze/worldcup/
├── README.md          ← 本说明（会进 Git）
├── teams.csv          ← 示例：你的文件放这里
├── players.csv
├── matches.csv
└── …                  ← 其余 CSV 同级放置
```

## 约定

| 项 | 说明 |
| :--- | :--- |
| 格式 | UTF-8 CSV，首行为表头 |
| 命名 | 沿用源文件名（如 `teams.csv`、`players.csv`）；后续 `profile.py` 会按文件名建清单 |
| Git | `*.csv` 已加入 `.gitignore`，**大文件不会进仓库**；仅本 README 与目录结构进 Git |
| 下游 | ETL 史诗见 [`docs/tasks/epics/EP04-01-worldcup-data-etl.md`](../../../docs/tasks/epics/EP04-01-worldcup-data-etl.md) |

放好文件后，在仓库根目录跑盘点：

```bash
# 安装 dev 依赖（含 pandas）后；默认路径已指向本目录
bash scripts/api.sh exec python ../../scripts/etl/worldcup/profile.py

# 或仓库根目录
python scripts/etl/worldcup/profile.py
```

输出写入 `_profile/manifest.json` 与 `_profile/report.md`（可进 Git）。

维度表入库（需 `pnpm db:migrate` 后）：

```bash
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py dimensions
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py players
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py matches
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py events
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py subpen
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py appearances
bash scripts/api.sh exec python ../../scripts/etl/worldcup/run.py standings
```

校验 Silver 数据：

```bash
bash scripts/api.sh exec python ../../scripts/etl/worldcup/validate.py
bash scripts/api.sh exec python ../../scripts/etl/worldcup/validate.py --tournament WC-2022
```

模型说明见 [`docs/tech/worldcup-data-model.md`](../../../docs/tech/worldcup-data-model.md)。

Gold 事实卡：

```bash
bash scripts/api.sh exec python ../../scripts/etl/worldcup/fact_cards.py
```
