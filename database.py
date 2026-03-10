import json
import sqlite3
from datetime import datetime

DB_PATH = "dnd_game.db"

CAMPAIGN_DEFAULTS = {
    "ruleset": "dnd_2024_phb",
    "leveling_mode": "xp_auto_ai",
    "hp_gain_mode": None,
    "scene_round_timeout_minutes": 5,
    "main_quest_pressure": "soft",
    "campaign_mode": "generated",
}

CHARACTER_JSON_FIELDS = {
    "origin_asi": "origin_asi_json",
    "class_levels": "class_levels_json",
    "build_choices": "build_choices_json",
    "feats": "feats_json",
    "resources": "resources_json",
    "spellcasting": "spellcasting_json",
    "hit_dice": "hit_dice_json",
    "levelup_state": "levelup_state_json",
}


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _loads_json(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _character_from_row(row):
    if not row:
        return None
    char = dict(row)
    char["inventory"] = _loads_json(char.get("inventory"), [])
    for logical_field, db_field in CHARACTER_JSON_FIELDS.items():
        default = {} if logical_field in {"origin_asi", "class_levels", "build_choices", "resources", "spellcasting", "hit_dice", "levelup_state"} else []
        char[logical_field] = _loads_json(char.get(db_field), default)
    return char


def _campaign_from_row(row):
    if not row:
        return None
    campaign = dict(row)
    campaign["roll_mode"] = campaign.get("roll_mode") or None
    campaign["setup_status"] = campaign.get("setup_status") or "ready"
    campaign["intro_status"] = campaign.get("intro_status") or "not_started"
    campaign["intro_answers"] = _loads_json(campaign.get("intro_answers_json"), {})
    campaign["ruleset"] = campaign.get("ruleset") or CAMPAIGN_DEFAULTS["ruleset"]
    campaign["leveling_mode"] = campaign.get("leveling_mode") or CAMPAIGN_DEFAULTS["leveling_mode"]
    campaign["hp_gain_mode"] = campaign.get("hp_gain_mode") or CAMPAIGN_DEFAULTS["hp_gain_mode"]
    campaign["scene_round_timeout_minutes"] = (
        campaign.get("scene_round_timeout_minutes")
        or CAMPAIGN_DEFAULTS["scene_round_timeout_minutes"]
    )
    campaign["main_quest_pressure"] = (
        campaign.get("main_quest_pressure")
        or CAMPAIGN_DEFAULTS["main_quest_pressure"]
    )
    campaign["campaign_mode"] = campaign.get("campaign_mode") or CAMPAIGN_DEFAULTS["campaign_mode"]
    campaign["adventure_slug"] = campaign.get("adventure_slug") or None
    campaign["adventure_title"] = campaign.get("adventure_title") or None
    campaign["adventure_gm_brief"] = campaign.get("adventure_gm_brief") or None
    return campaign


def _round_from_row(row):
    if not row:
        return None
    round_data = dict(row)
    round_data["options"] = _loads_json(round_data.pop("options_json", "[]"), [])
    round_data["silent_round"] = bool(round_data.get("silent_round", 0))
    return round_data


def _runtime_from_row(row):
    if not row:
        return None
    runtime = dict(row)
    runtime["auto_wait_enabled"] = bool(runtime.get("auto_wait_enabled", 1))
    return runtime


def _pending_roll_from_row(row):
    if not row:
        return None
    return dict(row)


def _ensure_campaign_columns(conn):
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(campaigns)").fetchall()
    }
    if "roll_mode" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN roll_mode TEXT DEFAULT 'bot_auto'")
    if "setup_status" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN setup_status TEXT DEFAULT 'ready'")
    if "setup_owner_user_id" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN setup_owner_user_id TEXT")
    if "ruleset" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN ruleset TEXT DEFAULT 'dnd_2024_phb'")
    if "leveling_mode" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN leveling_mode TEXT DEFAULT 'xp_auto_ai'")
    if "hp_gain_mode" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN hp_gain_mode TEXT")
    if "intro_status" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN intro_status TEXT DEFAULT 'not_started'")
    if "intro_answers_json" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN intro_answers_json TEXT DEFAULT '{}'")
    if "scene_round_timeout_minutes" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN scene_round_timeout_minutes INTEGER DEFAULT 5")
    if "main_quest_pressure" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN main_quest_pressure TEXT DEFAULT 'soft'")
    if "campaign_mode" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN campaign_mode TEXT DEFAULT 'generated'")
    if "adventure_slug" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN adventure_slug TEXT")
    if "adventure_title" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN adventure_title TEXT")
    if "adventure_gm_brief" not in existing_columns:
        conn.execute("ALTER TABLE campaigns ADD COLUMN adventure_gm_brief TEXT")


def _ensure_character_columns(conn):
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(characters)").fetchall()
    }
    if "subrace" not in existing_columns:
        conn.execute("ALTER TABLE characters ADD COLUMN subrace TEXT")
    if "subclass" not in existing_columns:
        conn.execute("ALTER TABLE characters ADD COLUMN subclass TEXT")
    for db_field in CHARACTER_JSON_FIELDS.values():
        if db_field not in existing_columns:
            conn.execute(f"ALTER TABLE characters ADD COLUMN {db_field} TEXT")


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            title TEXT DEFAULT 'Новая кампания',
            is_active INTEGER DEFAULT 1,
            roll_mode TEXT DEFAULT 'bot_auto',
            setup_status TEXT DEFAULT 'ready',
            setup_owner_user_id TEXT,
            ruleset TEXT DEFAULT 'dnd_2024_phb',
            leveling_mode TEXT DEFAULT 'xp_auto_ai',
            hp_gain_mode TEXT,
            intro_status TEXT DEFAULT 'not_started',
            intro_answers_json TEXT DEFAULT '{}',
            scene_round_timeout_minutes INTEGER DEFAULT 5,
            main_quest_pressure TEXT DEFAULT 'soft',
            campaign_mode TEXT DEFAULT 'generated',
            adventure_slug TEXT,
            adventure_title TEXT,
            adventure_gm_brief TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            campaign_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            race TEXT NOT NULL,
            subrace TEXT,
            class TEXT NOT NULL,
            subclass TEXT,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            hp INTEGER NOT NULL,
            max_hp INTEGER NOT NULL,
            strength INTEGER DEFAULT 10,
            dexterity INTEGER DEFAULT 10,
            constitution INTEGER DEFAULT 10,
            intelligence INTEGER DEFAULT 10,
            wisdom INTEGER DEFAULT 10,
            charisma INTEGER DEFAULT 10,
            gold INTEGER DEFAULT 10,
            inventory TEXT DEFAULT '[]',
            origin_asi_json TEXT,
            class_levels_json TEXT,
            build_choices_json TEXT,
            feats_json TEXT,
            resources_json TEXT,
            spellcasting_json TEXT,
            hit_dice_json TEXT,
            levelup_state_json TEXT,
            is_alive INTEGER DEFAULT 1,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            UNIQUE(user_id, campaign_id)
        );

        CREATE TABLE IF NOT EXISTS game_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            user_id TEXT,
            username TEXT,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS scene_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            bot_message_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            deadline_at TEXT NOT NULL,
            options_json TEXT DEFAULT '[]',
            expected_count INTEGER DEFAULT 0,
            answered_count INTEGER DEFAULT 0,
            silent_round INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS scene_round_targets (
            round_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            character_name_snapshot TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'expected',
            reason TEXT,
            PRIMARY KEY (round_id, user_id),
            FOREIGN KEY (round_id) REFERENCES scene_rounds(id)
        );

        CREATE TABLE IF NOT EXISTS scene_round_responses (
            round_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            response_kind TEXT NOT NULL,
            selected_option INTEGER,
            content TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            PRIMARY KEY (round_id, user_id),
            FOREIGN KEY (round_id) REFERENCES scene_rounds(id)
        );

        CREATE TABLE IF NOT EXISTS campaign_runtime_state (
            campaign_id INTEGER PRIMARY KEY,
            consecutive_fully_silent_rounds INTEGER DEFAULT 0,
            auto_wait_enabled INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS pending_roll_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            round_id INTEGER,
            user_id TEXT NOT NULL,
            character_name_snapshot TEXT NOT NULL,
            action_text TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            dice_count INTEGER NOT NULL,
            dice_sides INTEGER NOT NULL,
            modifier_stat TEXT,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
            FOREIGN KEY (round_id) REFERENCES scene_rounds(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_roll_requests_open
            ON pending_roll_requests(campaign_id, user_id)
            WHERE status = 'open';

        CREATE TABLE IF NOT EXISTS xp_awards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            source_message_id TEXT NOT NULL,
            assistant_message_id TEXT,
            reason TEXT,
            amount INTEGER NOT NULL DEFAULT 0,
            awarded_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE(campaign_id, source_message_id),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS levelup_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            character_name_snapshot TEXT NOT NULL,
            target_level INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            queued_at TEXT NOT NULL,
            activated_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS levelup_sessions (
            campaign_id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL,
            character_name_snapshot TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            last_activity_at TEXT NOT NULL,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );
        """
    )
    _ensure_campaign_columns(conn)
    _ensure_character_columns(conn)

    conn.commit()
    conn.close()


# Campaigns


def create_campaign(
    guild_id: str,
    channel_id: str,
    title: str = "Новая кампания",
    setup_owner_user_id: str | None = None,
) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO campaigns (
            guild_id,
            channel_id,
            title,
            roll_mode,
            setup_status,
            setup_owner_user_id,
            intro_status,
            intro_answers_json,
            campaign_mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            channel_id,
            title,
            None,
            "awaiting_campaign_mode",
            setup_owner_user_id,
            "not_started",
            "{}",
            CAMPAIGN_DEFAULTS["campaign_mode"],
        ),
    )
    campaign_id = c.lastrowid
    conn.commit()
    conn.close()
    return campaign_id


def get_campaign(campaign_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM campaigns WHERE id = ?",
        (campaign_id,),
    ).fetchone()
    conn.close()
    return _campaign_from_row(row)


def get_active_campaign(channel_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM campaigns WHERE channel_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (channel_id,),
    ).fetchone()
    conn.close()
    return _campaign_from_row(row)


def set_campaign_roll_mode(campaign_id: int, roll_mode: str):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET roll_mode = ?, setup_status = ? WHERE id = ?",
        (roll_mode, "awaiting_hp_gain_mode", campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_hp_gain_mode(campaign_id: int, hp_gain_mode: str):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET hp_gain_mode = ?, setup_status = ? WHERE id = ?",
        (hp_gain_mode, "awaiting_campaign_title", campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_mode(campaign_id: int, campaign_mode: str):
    next_status = "awaiting_adventure_choice" if campaign_mode in {"preset", "preset_based"} else "awaiting_roll_mode"
    conn = get_connection()
    conn.execute(
        """
        UPDATE campaigns
        SET campaign_mode = ?, setup_status = ?, adventure_slug = NULL, adventure_title = NULL, adventure_gm_brief = NULL
        WHERE id = ?
        """,
        (campaign_mode, next_status, campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_adventure(campaign_id: int, adventure_slug: str, adventure_title: str):
    conn = get_connection()
    conn.execute(
        """
        UPDATE campaigns
        SET adventure_slug = ?, adventure_title = ?, adventure_gm_brief = NULL, setup_status = ?
        WHERE id = ?
        """,
        (adventure_slug, adventure_title, "awaiting_roll_mode", campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_title(campaign_id: int, title: str):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET title = ?, setup_status = ? WHERE id = ?",
        (title, "ready", campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_adventure_gm_brief(campaign_id: int, adventure_gm_brief: str | None):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET adventure_gm_brief = ? WHERE id = ?",
        (adventure_gm_brief, campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_intro_state(campaign_id: int, intro_status: str, intro_answers: dict | None = None):
    conn = get_connection()
    if intro_answers is None:
        conn.execute(
            "UPDATE campaigns SET intro_status = ? WHERE id = ?",
            (intro_status, campaign_id),
        )
    else:
        conn.execute(
            "UPDATE campaigns SET intro_status = ?, intro_answers_json = ? WHERE id = ?",
            (intro_status, json.dumps(intro_answers, ensure_ascii=False), campaign_id),
        )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_scene_round_timeout(campaign_id: int, timeout_minutes: int):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET scene_round_timeout_minutes = ? WHERE id = ?",
        (timeout_minutes, campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def set_campaign_main_quest_pressure(campaign_id: int, pressure_mode: str):
    conn = get_connection()
    conn.execute(
        "UPDATE campaigns SET main_quest_pressure = ? WHERE id = ?",
        (pressure_mode, campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


def end_campaign(campaign_id: int):
    conn = get_connection()
    conn.execute("UPDATE campaigns SET is_active = 0 WHERE id = ?", (campaign_id,))
    conn.execute(
        "UPDATE pending_roll_requests SET status = 'cancelled', resolved_at = ? WHERE campaign_id = ? AND status = 'open'",
        (utcnow_iso(), campaign_id),
    )
    conn.commit()
    conn.close()


# Characters


def create_character(user_id: str, campaign_id: int, data: dict) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO characters
            (user_id, campaign_id, name, race, subrace, class, subclass, hp, max_hp,
             strength, dexterity, constitution, intelligence, wisdom, charisma, origin_asi_json,
             class_levels_json, build_choices_json, feats_json, resources_json, spellcasting_json, hit_dice_json, levelup_state_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                campaign_id,
                data["name"],
                data["race"],
                data.get("subrace"),
                data["class"],
                data.get("subclass"),
                data["hp"],
                data.get("max_hp", data["hp"]),
                data["strength"],
                data["dexterity"],
                data["constitution"],
                data["intelligence"],
                data["wisdom"],
                data["charisma"],
                json.dumps(data.get("origin_asi", {}), ensure_ascii=False),
                json.dumps(data.get("class_levels", {}), ensure_ascii=False),
                json.dumps(data.get("build_choices", {}), ensure_ascii=False),
                json.dumps(data.get("feats", []), ensure_ascii=False),
                json.dumps(data.get("resources", {}), ensure_ascii=False),
                json.dumps(data.get("spellcasting", {}), ensure_ascii=False),
                json.dumps(data.get("hit_dice", {}), ensure_ascii=False),
                json.dumps(data.get("levelup_state", {}), ensure_ascii=False),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_character(user_id: str, campaign_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM characters WHERE user_id = ? AND campaign_id = ?",
        (user_id, campaign_id),
    ).fetchone()
    conn.close()
    return _character_from_row(row)


def get_all_characters(campaign_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM characters WHERE campaign_id = ? AND is_alive = 1",
        (campaign_id,),
    ).fetchall()
    conn.close()
    return [_character_from_row(row) for row in rows]


def update_character(user_id: str, campaign_id: int, updates: dict):
    if "inventory" in updates:
        updates["inventory"] = json.dumps(updates["inventory"], ensure_ascii=False)
    for logical_field, db_field in CHARACTER_JSON_FIELDS.items():
        if logical_field in updates:
            updates[db_field] = json.dumps(updates.pop(logical_field), ensure_ascii=False)
        if db_field in updates and not isinstance(updates[db_field], str):
            updates[db_field] = json.dumps(updates[db_field], ensure_ascii=False)
    fields = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [user_id, campaign_id]
    conn = get_connection()
    conn.execute(
        f"UPDATE characters SET {fields} WHERE user_id = ? AND campaign_id = ?",
        values,
    )
    conn.commit()
    conn.close()


# Leveling and XP


def get_xp_award(campaign_id: int, source_message_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM xp_awards WHERE campaign_id = ? AND source_message_id = ?",
        (campaign_id, source_message_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["awarded"] = _loads_json(item.get("awarded_json"), [])
    return item


def get_latest_xp_award(campaign_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM xp_awards WHERE campaign_id = ? ORDER BY datetime(created_at) DESC, id DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["awarded"] = _loads_json(item.get("awarded_json"), [])
    return item


def record_xp_award(
    campaign_id: int,
    source_message_id: str,
    assistant_message_id: str | None,
    amount: int,
    reason: str,
    awarded: list[dict],
):
    if get_xp_award(campaign_id, source_message_id):
        return None
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO xp_awards (campaign_id, source_message_id, assistant_message_id, reason, amount, awarded_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            campaign_id,
            source_message_id,
            assistant_message_id,
            reason,
            amount,
            json.dumps(awarded, ensure_ascii=False),
            utcnow_iso(),
        ),
    )
    award_id = cur.lastrowid
    conn.commit()
    conn.close()
    return award_id


def adjust_character_xp(user_id: str, campaign_id: int, delta: int):
    char = get_character(user_id, campaign_id)
    if not char:
        return None
    new_exp = max(0, int(char.get("exp", 0)) + int(delta))
    update_character(user_id, campaign_id, {"exp": new_exp})
    return get_character(user_id, campaign_id)


def get_levelup_queue(campaign_id: int, statuses: tuple[str, ...] | None = None):
    conn = get_connection()
    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        rows = conn.execute(
            f"SELECT * FROM levelup_queue WHERE campaign_id = ? AND status IN ({placeholders}) ORDER BY id ASC",
            (campaign_id, *statuses),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM levelup_queue WHERE campaign_id = ? ORDER BY id ASC",
            (campaign_id,),
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_levelup_queue_entry(campaign_id: int, user_id: str, statuses: tuple[str, ...] = ("pending", "active")):
    conn = get_connection()
    placeholders = ", ".join("?" for _ in statuses)
    row = conn.execute(
        f"SELECT * FROM levelup_queue WHERE campaign_id = ? AND user_id = ? AND status IN ({placeholders}) ORDER BY id ASC LIMIT 1",
        (campaign_id, user_id, *statuses),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def enqueue_levelup(campaign_id: int, user_id: str, character_name: str, target_level: int):
    existing = get_user_levelup_queue_entry(campaign_id, user_id)
    if existing:
        if int(existing.get("target_level", 1)) >= int(target_level):
            return existing
        conn = get_connection()
        conn.execute(
            "UPDATE levelup_queue SET target_level = ? WHERE id = ?",
            (target_level, existing["id"]),
        )
        conn.commit()
        conn.close()
        return get_user_levelup_queue_entry(campaign_id, user_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO levelup_queue (campaign_id, user_id, character_name_snapshot, target_level, status, queued_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (campaign_id, user_id, character_name, target_level, utcnow_iso()),
    )
    queue_id = cur.lastrowid
    conn.commit()
    conn.close()
    return next((item for item in get_levelup_queue(campaign_id, ("pending", "active")) if item["id"] == queue_id), None)


def activate_levelup_queue_entry(queue_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE levelup_queue SET status = 'active', activated_at = ? WHERE id = ?",
        (utcnow_iso(), queue_id),
    )
    conn.commit()
    conn.close()


def complete_levelup_queue_entry(queue_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE levelup_queue SET status = 'completed', completed_at = ? WHERE id = ?",
        (utcnow_iso(), queue_id),
    )
    conn.commit()
    conn.close()


def release_levelup_queue_entry(queue_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE levelup_queue SET status = 'pending', activated_at = NULL WHERE id = ? AND status = 'active'",
        (queue_id,),
    )
    conn.commit()
    conn.close()


def clear_levelup_queue(campaign_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE levelup_queue SET status = 'cancelled', completed_at = ? WHERE campaign_id = ? AND status IN ('pending', 'active')",
        (utcnow_iso(), campaign_id),
    )
    conn.commit()
    conn.close()


def get_active_levelup_session(campaign_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM levelup_sessions WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    session = dict(row)
    session["state"] = _loads_json(session.get("state_json"), {})
    return session


def upsert_levelup_session(campaign_id: int, user_id: str, character_name: str, state: dict):
    now = utcnow_iso()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO levelup_sessions (campaign_id, user_id, character_name_snapshot, state_json, started_at, last_activity_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(campaign_id) DO UPDATE SET
            user_id = excluded.user_id,
            character_name_snapshot = excluded.character_name_snapshot,
            state_json = excluded.state_json,
            last_activity_at = excluded.last_activity_at
        """,
        (campaign_id, user_id, character_name, json.dumps(state, ensure_ascii=False), now, now),
    )
    conn.commit()
    conn.close()
    return get_active_levelup_session(campaign_id)


def delete_levelup_session(campaign_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM levelup_sessions WHERE campaign_id = ?", (campaign_id,))
    conn.commit()
    conn.close()


def touch_levelup_session(campaign_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE levelup_sessions SET last_activity_at = ? WHERE campaign_id = ?",
        (utcnow_iso(), campaign_id),
    )
    conn.commit()
    conn.close()


# History


def add_message(
    campaign_id: int,
    role: str,
    content: str,
    user_id: str = None,
    username: str = None,
):
    conn = get_connection()
    conn.execute(
        "INSERT INTO game_history (campaign_id, role, user_id, username, content) VALUES (?, ?, ?, ?, ?)",
        (campaign_id, role, user_id, username, content),
    )
    conn.commit()
    conn.close()


def get_history(campaign_id: int, limit: int = 30) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM game_history WHERE campaign_id = ? ORDER BY id DESC LIMIT ?",
        (campaign_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


def get_history_summary(campaign_id: int) -> str:
    history = get_history(campaign_id, limit=50)
    lines = []
    for msg in history:
        if msg["role"] == "user":
            lines.append(f"[{msg['username']}]: {msg['content']}")
        else:
            lines.append(f"[Мастер]: {msg['content']}")
    return "\n".join(lines)


# Runtime State


def get_campaign_runtime_state(campaign_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM campaign_runtime_state WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO campaign_runtime_state (campaign_id, consecutive_fully_silent_rounds, auto_wait_enabled, updated_at) VALUES (?, 0, 1, ?)",
            (campaign_id, utcnow_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM campaign_runtime_state WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
    conn.close()
    return _runtime_from_row(row)


def _update_campaign_runtime_state(campaign_id: int, silent_rounds: int, auto_wait_enabled: bool):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO campaign_runtime_state (campaign_id, consecutive_fully_silent_rounds, auto_wait_enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(campaign_id) DO UPDATE SET
            consecutive_fully_silent_rounds = excluded.consecutive_fully_silent_rounds,
            auto_wait_enabled = excluded.auto_wait_enabled,
            updated_at = excluded.updated_at
        """,
        (campaign_id, silent_rounds, 1 if auto_wait_enabled else 0, utcnow_iso()),
    )
    conn.commit()
    conn.close()


def record_scene_round_activity(campaign_id: int, had_expected_reply: bool):
    runtime = get_campaign_runtime_state(campaign_id)
    if had_expected_reply:
        _update_campaign_runtime_state(campaign_id, 0, True)
    else:
        new_silent_rounds = runtime["consecutive_fully_silent_rounds"] + 1
        _update_campaign_runtime_state(campaign_id, new_silent_rounds, new_silent_rounds < 2)
    return get_campaign_runtime_state(campaign_id)


def resume_campaign_auto_wait(campaign_id: int):
    _update_campaign_runtime_state(campaign_id, 0, True)
    return get_campaign_runtime_state(campaign_id)


# Pending Roll Requests


def cancel_open_pending_roll_requests(campaign_id: int, user_id: str):
    conn = get_connection()
    conn.execute(
        "UPDATE pending_roll_requests SET status = 'cancelled', resolved_at = ? WHERE campaign_id = ? AND user_id = ? AND status = 'open'",
        (utcnow_iso(), campaign_id, user_id),
    )
    conn.commit()
    conn.close()


def create_pending_roll_request(
    campaign_id: int,
    user_id: str,
    character_name_snapshot: str,
    action_text: str,
    source_message_id: str,
    dice_count: int,
    dice_sides: int,
    modifier_stat: str | None,
    reason: str | None,
    round_id: int | None = None,
):
    cancel_open_pending_roll_requests(campaign_id, user_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pending_roll_requests
        (campaign_id, round_id, user_id, character_name_snapshot, action_text, source_message_id,
         dice_count, dice_sides, modifier_stat, reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            campaign_id,
            round_id,
            user_id,
            character_name_snapshot,
            action_text,
            source_message_id,
            dice_count,
            dice_sides,
            modifier_stat,
            reason,
            utcnow_iso(),
        ),
    )
    request_id = cur.lastrowid
    conn.commit()
    conn.close()
    return get_pending_roll_request(request_id)


def get_pending_roll_request(request_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM pending_roll_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    conn.close()
    return _pending_roll_from_row(row)


def get_open_pending_roll_request(campaign_id: int, user_id: str):
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM pending_roll_requests
        WHERE campaign_id = ? AND user_id = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (campaign_id, user_id),
    ).fetchone()
    conn.close()
    return _pending_roll_from_row(row)


def get_open_pending_roll_requests(campaign_id: int):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM pending_roll_requests
        WHERE campaign_id = ? AND status = 'open'
        ORDER BY id DESC
        """,
        (campaign_id,),
    ).fetchall()
    conn.close()
    return [_pending_roll_from_row(row) for row in rows]


def resolve_pending_roll_request(request_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE pending_roll_requests SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (utcnow_iso(), request_id),
    )
    conn.commit()
    conn.close()


def expire_pending_roll_requests_for_round(round_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE pending_roll_requests SET status = 'expired', resolved_at = ? WHERE round_id = ? AND status = 'open'",
        (utcnow_iso(), round_id),
    )
    conn.commit()
    conn.close()


# Scene Rounds


def create_scene_round(campaign_id: int, options: list[str], deadline_at: str, targets: list[dict]):
    expected_count = sum(1 for target in targets if target.get("status") == "expected")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO scene_rounds
        (campaign_id, deadline_at, options_json, expected_count, answered_count, silent_round, status, created_at)
        VALUES (?, ?, ?, ?, 0, 0, 'open', ?)
        """,
        (
            campaign_id,
            deadline_at,
            json.dumps(options, ensure_ascii=False),
            expected_count,
            utcnow_iso(),
        ),
    )
    round_id = cur.lastrowid
    cur.executemany(
        """
        INSERT INTO scene_round_targets (round_id, user_id, character_name_snapshot, status, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                round_id,
                target["user_id"],
                target["character_name_snapshot"],
                target.get("status", "expected"),
                target.get("reason"),
            )
            for target in targets
        ],
    )
    conn.commit()
    conn.close()
    return round_id


def set_scene_round_message_id(round_id: int, bot_message_id: str):
    conn = get_connection()
    conn.execute(
        "UPDATE scene_rounds SET bot_message_id = ? WHERE id = ?",
        (bot_message_id, round_id),
    )
    conn.commit()
    conn.close()


def _load_round_targets(conn, round_id: int):
    rows = conn.execute(
        "SELECT * FROM scene_round_targets WHERE round_id = ? ORDER BY character_name_snapshot COLLATE NOCASE",
        (round_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_round_responses(conn, round_id: int):
    rows = conn.execute(
        "SELECT * FROM scene_round_responses WHERE round_id = ? ORDER BY submitted_at ASC",
        (round_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_scene_round(round_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM scene_rounds WHERE id = ?",
        (round_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    round_data = _round_from_row(row)
    round_data["targets"] = _load_round_targets(conn, round_id)
    round_data["responses"] = _load_round_responses(conn, round_id)
    conn.close()
    return round_data


def get_active_scene_round(campaign_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM scene_rounds WHERE campaign_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (campaign_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    round_data = _round_from_row(row)
    round_data["targets"] = _load_round_targets(conn, round_data["id"])
    round_data["responses"] = _load_round_responses(conn, round_data["id"])
    conn.close()
    return round_data


def get_scene_round_target(round_id: int, user_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM scene_round_targets WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_scene_round_response(round_id: int, user_id: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM scene_round_responses WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_scene_round_response(
    round_id: int,
    user_id: str,
    message_id: str,
    response_kind: str,
    content: str,
    selected_option: int | None = None,
):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO scene_round_responses
        (round_id, user_id, message_id, response_kind, selected_option, content, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(round_id, user_id) DO UPDATE SET
            message_id = excluded.message_id,
            response_kind = excluded.response_kind,
            selected_option = excluded.selected_option,
            content = excluded.content,
            submitted_at = excluded.submitted_at
        """,
        (
            round_id,
            user_id,
            message_id,
            response_kind,
            selected_option,
            content,
            utcnow_iso(),
        ),
    )
    conn.execute(
        "UPDATE scene_round_targets SET status = 'answered' WHERE round_id = ? AND user_id = ? AND status IN ('expected', 'answered')",
        (round_id, user_id),
    )
    conn.commit()
    conn.close()


def mark_scene_round_target_status(round_id: int, user_id: str, status: str, reason: str | None = None):
    conn = get_connection()
    conn.execute(
        "UPDATE scene_round_targets SET status = ?, reason = ? WHERE round_id = ? AND user_id = ?",
        (status, reason, round_id, user_id),
    )
    conn.commit()
    conn.close()


def mark_scene_round_timeouts(round_id: int):
    conn = get_connection()
    conn.execute(
        """
        UPDATE scene_round_targets
        SET status = 'timed_out'
        WHERE round_id = ?
          AND status = 'expected'
          AND user_id NOT IN (
              SELECT user_id FROM scene_round_responses WHERE round_id = ?
          )
        """,
        (round_id, round_id),
    )
    conn.commit()
    conn.close()


def get_scene_round_pending_targets(round_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM scene_round_targets WHERE round_id = ? AND status = 'expected' ORDER BY character_name_snapshot COLLATE NOCASE",
        (round_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_due_scene_rounds(now_iso: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM scene_rounds WHERE status = 'open' AND deadline_at <= ? ORDER BY id ASC",
        (now_iso,),
    ).fetchall()
    conn.close()
    return [_round_from_row(row) for row in rows]


def finalize_scene_round(round_id: int, status: str):
    conn = get_connection()
    answered_count = conn.execute(
        "SELECT COUNT(*) FROM scene_round_targets WHERE round_id = ? AND status = 'answered'",
        (round_id,),
    ).fetchone()[0]
    silent_round = 1 if answered_count == 0 else 0
    conn.execute(
        """
        UPDATE scene_rounds
        SET status = ?, answered_count = ?, silent_round = ?, resolved_at = ?
        WHERE id = ?
        """,
        (status, answered_count, silent_round, utcnow_iso(), round_id),
    )
    conn.commit()
    conn.close()


def mark_scene_round_idle_stopped(round_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE scene_rounds SET status = 'idle_stopped', resolved_at = ? WHERE id = ?",
        (utcnow_iso(), round_id),
    )
    conn.commit()
    conn.close()



