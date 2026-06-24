CREATE OR REPLACE VIEW vw_player_summary AS
WITH player_cards AS (
    SELECT
        replace(d.external_id, 'player_career:', '') AS player_id,
        dc.content
    FROM documents d
    JOIN document_chunks dc ON dc.document_id = d.id
    WHERE d.collection = 'worldcup-player_careers'
)
SELECT
    player_id,
    trim(substring(content FROM '\[Player Career\] ([^·]+) ·')) AS display_name,
    trim(substring(content FROM '· ([^\n]+)\nID:')) AS competition,
    string_to_array(substring(content FROM 'Teams: ([^.]+)\.'), ', ') AS team_codes,
    substring(content FROM 'Born: ([0-9]{4}-[0-9]{2}-[0-9]{2})\.')::date AS birth_date,
    substring(content FROM 'Position: ([A-Z]+)\.') AS position_code,
    substring(content FROM 'World Cups \(([0-9]+)\):')::integer AS world_cup_count,
    substring(content FROM 'World Cups \([0-9]+\): ([^.]+)\.') AS world_cup_years,
    substring(content FROM 'Squad listings: ([0-9]+)\.')::integer AS squad_listings,
    substring(content FROM 'Goals: ([0-9]+)')::integer AS goals,
    substring(content FROM 'Appearances: ([0-9]+)')::integer AS appearances,
    substring(content FROM '\(([0-9]+) starts')::integer AS starts,
    substring(content FROM ', ([0-9]+) substitute')::integer AS substitute_appearances,
    COALESCE(substring(content FROM 'Discipline: ([0-9]+) yellow')::integer, 0) AS yellow_cards,
    COALESCE(substring(content FROM 'yellow, ([0-9]+) red')::integer, 0) AS red_cards,
    substring(content FROM 'Awards: ([^\n]+)') AS awards,
    content AS source_text
FROM player_cards;

CREATE OR REPLACE VIEW vw_match_summary AS
WITH match_cards AS (
    SELECT
        replace(d.external_id, 'match:', '') AS match_id,
        dc.content
    FROM documents d
    JOIN document_chunks dc ON dc.document_id = d.id
    WHERE d.collection = 'worldcup-matches'
),
parsed AS (
    SELECT
        match_id,
        content,
        substring(content FROM '\[Match\] ([^·]+) ·') AS tournament_name,
        trim(substring(content FROM '· ([^·]+) vs [^·]+ ·')) AS home_team,
        trim(substring(content FROM '· [^·]+ vs ([^·]+) ·')) AS away_team,
        trim(substring(content FROM ' vs [^·]+ · ([^·]+) · [0-9]{4}-')) AS stage_name,
        substring(content FROM ' · ([0-9]{4}-[0-9]{2}-[0-9]{2})\nScore')::date AS match_date,
        substring(content FROM 'Score: ([^.]+)\.') AS score,
        substring(content FROM 'Stadium: ([^.]+)\.') AS stadium,
        substring(content FROM 'Goals: (.*)') AS goals
    FROM match_cards
)
SELECT
    p.match_id,
    COALESCE(m.tournament_id, substring(p.match_id FROM 'M-([0-9]+)-')) AS tournament_id,
    p.tournament_name,
    COALESCE(m.stage_name, p.stage_name) AS stage_name,
    m.group_name,
    COALESCE(m.match_date, p.match_date) AS match_date,
    m.home_team_id,
    p.home_team,
    m.away_team_id,
    p.away_team,
    COALESCE(m.home_score, split_part(p.score, '-', 1)::integer) AS home_score,
    COALESCE(m.away_score, split_part(p.score, '-', 2)::integer) AS away_score,
    COALESCE(
        p.score,
        COALESCE(m.home_score::text, '?') || '-' || COALESCE(m.away_score::text, '?')
    ) AS score,
    m.extra_time,
    m.penalty_shootout,
    m.home_penalty_score,
    m.away_penalty_score,
    p.stadium,
    p.goals,
    p.content AS source_text
FROM parsed p
LEFT JOIN wc_matches m ON m.id = p.match_id;

CREATE OR REPLACE VIEW vw_team_tournament_summary AS
SELECT
    vm.tournament_id,
    vm.tournament_name,
    EXTRACT(YEAR FROM MIN(vm.match_date))::integer AS year,
    s.team_id,
    MAX(
        CASE
            WHEN s.team_id = vm.home_team_id THEN vm.home_team
            WHEN s.team_id = vm.away_team_id THEN vm.away_team
            ELSE NULL
        END
    ) AS team_name,
    COUNT(*)::integer AS matches,
    SUM(CASE WHEN s.won THEN 1 ELSE 0 END)::integer AS wins,
    SUM(CASE WHEN s.drew THEN 1 ELSE 0 END)::integer AS draws,
    SUM(CASE WHEN s.lost THEN 1 ELSE 0 END)::integer AS losses,
    SUM(s.goals_for)::integer AS goals_for,
    SUM(s.goals_against)::integer AS goals_against,
    SUM(s.goal_differential)::integer AS goal_difference,
    SUM(s.penalties_for)::integer AS penalties_for,
    SUM(s.penalties_against)::integer AS penalties_against
FROM wc_team_match_stats s
JOIN vw_match_summary vm ON vm.match_id = s.match_id
GROUP BY vm.tournament_id, vm.tournament_name, s.team_id;

CREATE OR REPLACE VIEW vw_player_match_participation AS
WITH participants AS (
    SELECT
        match_id,
        team_id,
        player_id,
        tournament_id,
        shirt_number,
        position_name,
        position_code,
        starter,
        substitute
    FROM wc_player_appearances

    UNION

    SELECT
        match_id,
        team_id,
        player_id,
        tournament_id,
        shirt_number,
        NULL::varchar AS position_name,
        NULL::varchar AS position_code,
        NULL::boolean AS starter,
        NULL::boolean AS substitute
    FROM wc_goals

    UNION

    SELECT
        match_id,
        team_id,
        player_id,
        tournament_id,
        shirt_number,
        NULL::varchar AS position_name,
        NULL::varchar AS position_code,
        NULL::boolean AS starter,
        NULL::boolean AS substitute
    FROM wc_bookings

    UNION

    SELECT
        match_id,
        team_id,
        player_id,
        tournament_id,
        shirt_number,
        NULL::varchar AS position_name,
        NULL::varchar AS position_code,
        NULL::boolean AS starter,
        NULL::boolean AS substitute
    FROM wc_substitutions
),
goals AS (
    SELECT
        match_id,
        player_id,
        COUNT(*)::integer AS goals,
        SUM(CASE WHEN penalty THEN 1 ELSE 0 END)::integer AS penalty_goals,
        SUM(CASE WHEN own_goal THEN 1 ELSE 0 END)::integer AS own_goals
    FROM wc_goals
    GROUP BY match_id, player_id
),
cards AS (
    SELECT
        match_id,
        player_id,
        SUM(CASE WHEN yellow_card THEN 1 ELSE 0 END)::integer AS yellow_cards,
        SUM(CASE WHEN red_card OR second_yellow_card THEN 1 ELSE 0 END)::integer AS red_cards
    FROM wc_bookings
    GROUP BY match_id, player_id
)
SELECT
    p.player_id,
    wp.display_name,
    p.match_id,
    p.tournament_id,
    vm.tournament_name,
    EXTRACT(YEAR FROM vm.match_date)::integer AS year,
    p.team_id,
    CASE
        WHEN p.team_id = vm.home_team_id THEN vm.home_team
        WHEN p.team_id = vm.away_team_id THEN vm.away_team
        ELSE NULL
    END AS team_name,
    CASE
        WHEN p.team_id = vm.home_team_id THEN vm.away_team_id
        WHEN p.team_id = vm.away_team_id THEN vm.home_team_id
        ELSE NULL
    END AS opponent_id,
    CASE
        WHEN p.team_id = vm.home_team_id THEN vm.away_team
        WHEN p.team_id = vm.away_team_id THEN vm.home_team
        ELSE NULL
    END AS opponent_name,
    p.shirt_number,
    p.position_name,
    p.position_code,
    p.starter,
    p.substitute,
    COALESCE(g.goals, 0) AS goals,
    COALESCE(g.penalty_goals, 0) AS penalty_goals,
    COALESCE(g.own_goals, 0) AS own_goals,
    COALESCE(c.yellow_cards, 0) AS yellow_cards,
    COALESCE(c.red_cards, 0) AS red_cards
FROM participants p
LEFT JOIN wc_players wp ON wp.id = p.player_id
LEFT JOIN vw_match_summary vm ON vm.match_id = p.match_id
LEFT JOIN goals g ON g.match_id = p.match_id AND g.player_id = p.player_id
LEFT JOIN cards c ON c.match_id = p.match_id AND c.player_id = p.player_id;
