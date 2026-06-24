# Tasks

## Later: Analytical Views

- [x] Add `vw_player_summary` or `mv_player_summary`
  - Summarize player profile, World Cup years, goals, appearances, starts, cards, and awards.
  - Supports questions like "梅西世界杯表现", "某球员进了几个球", and "谁参加了几届世界杯".

- [x] Add `vw_match_summary` or `mv_match_summary`
  - Summarize match teams, score, stage, date, stadium, and goal list.
  - Supports questions like "某场比赛结果", "某年决赛", and "阿根廷 vs 法国".

- [x] Add `vw_team_tournament_summary` or `mv_team_tournament_summary`
  - Summarize each team's tournament performance: matches, wins/draws/losses, goals for/against, and goal difference.
  - Supports questions like "巴西 2002 表现", "德国历届成绩", and future team-form analysis.

- [x] Add `vw_player_match_participation` or `mv_player_match_participation`
  - One row per player per match, including team, opponent, tournament year, starter/substitute, position, goals, and cards.
  - Supports head-to-head questions like "梅西和 C 罗是否在世界杯交过手" and "某球员参加过哪些比赛".
  - Current view is structural and will populate when `wc_player_appearances` / player event tables are loaded.
