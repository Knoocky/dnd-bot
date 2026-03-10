import asyncio
import sqlite3
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import adventure_library
import ai_provider_runtime as ai_provider
import database
import dungeon_master
from cogs.game import GameCog

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class AdventureLibraryTests(unittest.TestCase):
    def setUp(self):
        self.original_dir = adventure_library.ADVENTURES_DIR

    def tearDown(self):
        adventure_library.ADVENTURES_DIR = self.original_dir

    def test_list_adventures_extracts_title_and_description(self):
        adventure_library.ADVENTURES_DIR = FIXTURES_DIR / "adventures"

        adventures = adventure_library.list_adventures()

        self.assertEqual(len(adventures), 1)
        self.assertEqual(adventures[0]["slug"], "frozen-vault")
        self.assertEqual(adventures[0]["title"], "Frozen Vault")
        self.assertEqual(adventures[0]["description"], "Древнее хранилище спит подо льдом.")

    def test_list_adventures_parses_realistic_titles_and_safe_descriptions(self):
        adventure_library.ADVENTURES_DIR = FIXTURES_DIR / "adventure_samples"

        adventures = {item["slug"]: item for item in adventure_library.list_adventures()}

        self.assertEqual(adventures["Candlekeep Mysteries"]["title"], "Candlekeep Mysteries")
        self.assertEqual(
            adventures["Candlekeep Mysteries"]["description"],
            "Candlekeep Mysteries is an anthology of adventures written by members of the Dungeons & Dragons community.",
        )
        self.assertNotIn("missing sage", adventures["Candlekeep Mysteries"]["description"].lower())

        self.assertEqual(
            adventures["Borderlands Quest_ Goblin Trouble"]["title"],
            "Borderlands Quest: Goblin Trouble",
        )
        self.assertTrue(
            adventures["Borderlands Quest_ Goblin Trouble"]["description"].startswith(
                "Borderlands Quest: Goblin Trouble is a Dungeons & Dragons adventure"
            )
        )

        self.assertEqual(
            adventures["Baldur's Gate_ Descent Into Avernus"]["title"],
            "Baldur's Gate: Descent into Avernus",
        )
        self.assertTrue(
            adventures["Baldur's Gate_ Descent Into Avernus"]["description"].startswith(
                "Designed for an adventuring party of four to six 1st-level characters"
            )
        )
        self.assertNotIn("elturel", adventures["Baldur's Gate_ Descent Into Avernus"]["description"].lower())

    def test_empty_adventure_catalog_returns_empty_list(self):
        adventure_library.ADVENTURES_DIR = FIXTURES_DIR / "empty_adventures"
        self.assertEqual(adventure_library.list_adventures(), [])


class AdventurePromptTests(unittest.TestCase):
    def setUp(self):
        self.original_provider = ai_provider._provider
        self.original_get_campaign = dungeon_master.db.get_campaign
        self.original_get_all_characters = dungeon_master.db.get_all_characters
        self.original_add_message = dungeon_master.db.add_message
        self.original_set_campaign_adventure_gm_brief = dungeon_master.db.set_campaign_adventure_gm_brief
        self.original_get_adventure = adventure_library.get_adventure
        self.original_call_bootstrap_api = ai_provider.call_bootstrap_api
        self.original_stream_api = ai_provider.stream_api
        self.original_compact_context = dungeon_master.compact_characters_context
        self.original_characters_context = dungeon_master.characters_context
        ai_provider._provider = "local"

        self.campaign = {
            "id": 101,
            "title": "Adventure Test",
            "campaign_mode": "preset",
            "roll_mode": "bot_auto",
            "main_quest_pressure": "soft",
            "adventure_slug": "frozen-vault",
            "adventure_title": "Frozen Vault",
            "adventure_gm_brief": None,
        }
        self.history = []
        self.saved_brief = None

        dungeon_master.db.get_campaign = lambda campaign_id: dict(self.campaign) if campaign_id == 101 else None
        dungeon_master.db.get_all_characters = lambda campaign_id: []
        dungeon_master.db.add_message = (
            lambda campaign_id, role, content, user_id=None, username=None: self.history.append(
                {"campaign_id": campaign_id, "role": role, "content": content}
            )
        )

        def fake_set_campaign_adventure_gm_brief(campaign_id, brief):
            self.saved_brief = brief
            self.campaign["adventure_gm_brief"] = brief
            return dict(self.campaign)

        dungeon_master.db.set_campaign_adventure_gm_brief = fake_set_campaign_adventure_gm_brief
        dungeon_master.compact_characters_context = lambda campaign_id: "ПАРТИЯ:\n- Гаррик, воин"
        dungeon_master.characters_context = lambda campaign_id: "АКТИВНЫЕ ПЕРСОНАЖИ:\n- Гаррик"

    def tearDown(self):
        ai_provider._provider = self.original_provider
        dungeon_master.db.get_campaign = self.original_get_campaign
        dungeon_master.db.get_all_characters = self.original_get_all_characters
        dungeon_master.db.add_message = self.original_add_message
        dungeon_master.db.set_campaign_adventure_gm_brief = self.original_set_campaign_adventure_gm_brief
        adventure_library.get_adventure = self.original_get_adventure
        ai_provider.call_bootstrap_api = self.original_call_bootstrap_api
        ai_provider.stream_api = self.original_stream_api
        dungeon_master.compact_characters_context = self.original_compact_context
        dungeon_master.characters_context = self.original_characters_context

    def test_bootstrap_system_blocks_keep_adventure_markdown_in_system_messages(self):
        adventure = {
            "slug": "frozen-vault",
            "title": "Frozen Vault",
            "content": "# Frozen Vault\n\nСекретный ледяной зал.",
        }
        system_blocks = dungeon_master.build_adventure_bootstrap_system_blocks(101, adventure, "preset")
        messages = ai_provider._build_chat_completion_messages(
            system_blocks,
            [{"role": "user", "content": "Открой первую сцену."}],
        )

        self.assertEqual(messages[-1]["role"], "user")
        self.assertTrue(all(message["role"] == "system" for message in messages[:-1]))
        self.assertIn(dungeon_master.READY_ADVENTURE_SYSTEM_PROMPT, system_blocks)
        self.assertTrue(any("Секретный ледяной зал." in block for block in system_blocks))
        self.assertNotIn("Секретный ледяной зал.", messages[-1]["content"])

    def test_local_bootstrap_compacts_large_adventure_source(self):
        huge_section = "Очень длинное описание сцены. " * 5000
        adventure = {
            "slug": "huge-book",
            "title": "Huge Book",
            "description": "Крупный источник для локального запуска.",
            "content": (
                "# About the Adventure\n\n"
                "Designed for an adventuring party, *Huge Book* is a sprawling campaign.\n\n"
                "## Adventure Overview\n\n"
                f"{huge_section}\n\n"
                "## Chapter 1\n\n"
                f"{huge_section}"
            ),
        }

        system_blocks = dungeon_master.build_adventure_bootstrap_system_blocks(101, adventure, "preset")
        source_block = system_blocks[-1]

        self.assertIn("КОМПАКТНАЯ СВОДКА ИСТОЧНИКА ДЛЯ ЛОКАЛЬНОЙ МОДЕЛИ", source_block)
        self.assertLess(len(source_block), 9500)
        self.assertIn("Huge Book", source_block)

    def test_start_campaign_preset_saves_gm_brief_and_uses_system_blocks(self):
        captured_bootstrap_calls = []
        captured_api_calls = []

        adventure_library.get_adventure = lambda slug: {
            "slug": "frozen-vault",
            "title": "Frozen Vault",
            "content": "# Frozen Vault\n\nСекретный ледяной зал.",
        }

        def fake_call_bootstrap_api(system_blocks, messages, max_tokens=1000, openai_options=None):
            captured_bootstrap_calls.append(
                {
                    "system_blocks": list(system_blocks),
                    "messages": [dict(message) for message in messages],
                    "max_tokens": max_tokens,
                }
            )
            return "GM brief"

        def fake_call_api(system_prompt, messages, max_tokens=1000, openai_options=None):
            captured_api_calls.append(
                {
                    "system_prompt": system_prompt,
                    "messages": [dict(message) for message in messages],
                    "max_tokens": max_tokens,
                }
            )
            return "Стартовая сцена."

        ai_provider.call_bootstrap_api = fake_call_bootstrap_api
        original_call_api = ai_provider.call_api
        ai_provider.call_api = fake_call_api

        try:
            result = asyncio.run(dungeon_master.start_campaign(101, "Adventure Test"))
        finally:
            ai_provider.call_api = original_call_api

        self.assertEqual(result, "Стартовая сцена.")
        self.assertEqual(len(captured_bootstrap_calls), 1)
        self.assertEqual(len(captured_api_calls), 1)
        self.assertIn(dungeon_master.READY_ADVENTURE_SYSTEM_PROMPT, captured_bootstrap_calls[0]["system_blocks"])
        self.assertTrue(
            any("Секретный ледяной зал." in block for block in captured_bootstrap_calls[0]["system_blocks"])
        )
        self.assertIn(dungeon_master.ADVENTURE_GM_BRIEF_PROMPT, captured_bootstrap_calls[0]["messages"][0]["content"])
        self.assertEqual(captured_api_calls[0]["messages"][0]["role"], "user")
        self.assertNotIn("Секретный ледяной зал.", captured_api_calls[0]["messages"][0]["content"])
        self.assertIn("GM brief", captured_api_calls[0]["system_prompt"])
        self.assertEqual(self.saved_brief, "GM brief")
        self.assertEqual([item["role"] for item in self.history], ["user", "assistant"])
        self.assertEqual(self.history[-1]["content"], "Стартовая сцена.")

    def test_stream_campaign_start_generated_persists_opening_scene(self):
        self.campaign["campaign_mode"] = "generated"

        def fake_stream_api(system_prompt, messages, max_tokens=1000):
            self.assertIn("Adventure Test", messages[0]["content"])
            yield "Стартовая "
            yield "сцена."

        ai_provider.stream_api = fake_stream_api

        async def collect():
            events = []
            async for event in dungeon_master.stream_campaign_start(101, "Adventure Test"):
                events.append(event)
            return events

        events = asyncio.run(collect())

        self.assertEqual(events[-1]["text"], "Стартовая сцена.")
        self.assertTrue(events[-1]["done"])
        self.assertEqual([item["role"] for item in self.history], ["user", "assistant"])
        self.assertEqual(self.history[-1]["content"], "Стартовая сцена.")

    def test_stream_campaign_start_preset_saves_brief_before_streaming_scene(self):
        captured_bootstrap_calls = []
        adventure_library.get_adventure = lambda slug: {
            "slug": "frozen-vault",
            "title": "Frozen Vault",
            "content": "# Frozen Vault\n\nСекретный ледяной зал.",
        }

        def fake_call_bootstrap_api(system_blocks, messages, max_tokens=1000, openai_options=None):
            captured_bootstrap_calls.append((list(system_blocks), [dict(message) for message in messages]))
            return "GM brief"

        def fake_stream_api(system_prompt, messages, max_tokens=1000):
            self.assertIn("GM brief", system_prompt)
            self.assertNotIn("Секретный ледяной зал.", messages[0]["content"])
            yield "Первая "
            yield "сцена."

        ai_provider.call_bootstrap_api = fake_call_bootstrap_api
        ai_provider.stream_api = fake_stream_api

        async def collect():
            events = []
            async for event in dungeon_master.stream_campaign_start(101, "Adventure Test"):
                events.append(event)
            return events

        events = asyncio.run(collect())

        self.assertEqual(len(captured_bootstrap_calls), 1)
        self.assertEqual(self.saved_brief, "GM brief")
        self.assertEqual(events[-1]["text"], "Первая сцена.")
        self.assertTrue(events[-1]["done"])
        self.assertEqual([item["role"] for item in self.history], ["user", "assistant"])
        self.assertEqual(self.history[-1]["content"], "Первая сцена.")

    def test_preset_based_bootstrap_uses_adaptation_prompt(self):
        self.campaign["campaign_mode"] = "preset_based"
        adventure = {
            "slug": "ember-road",
            "title": "Ember Road",
            "content": "# Ember Road\n\nДорога в пепле.",
        }

        system_blocks = dungeon_master.build_adventure_bootstrap_system_blocks(101, adventure, "preset_based")

        self.assertIn(dungeon_master.READY_ADVENTURE_ADAPTATION_SYSTEM_PROMPT, system_blocks)

    def test_runtime_prompt_can_add_relevant_adventure_excerpt_for_local_mode(self):
        self.campaign["adventure_gm_brief"] = "Краткая служебная сводка."
        adventure_library.get_adventure = lambda slug: {
            "slug": "frozen-vault",
            "title": "Frozen Vault",
            "description": "Ледяное хранилище с тайнами.",
            "content": (
                "# Frozen Vault\n\n"
                "Ледяное хранилище с тайнами.\n\n"
                "## Тайный зал\n\n"
                "В тайном зале под печатью хранится крошечный ключ и древний алтарь.\n\n"
                "## Стражи\n\n"
                "Ледяные стражи пробуждаются, если кто-то ломает печать."
            ),
        }

        system_prompt = dungeon_master.get_system_prompt(101, retrieval_query="осматриваю тайный зал и ищу ключ")

        self.assertIn("Краткая служебная сводка.", system_prompt)
        self.assertIn("РЕЛЕВАНТНЫЕ ФРАГМЕНТЫ ИСТОЧНИКА", system_prompt)
        self.assertIn("Тайный зал", system_prompt)


class CampaignSetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.original_get_connection = database.get_connection
        self.anchor_connection = sqlite3.connect("file:campaign_setup_tests?mode=memory&cache=shared", uri=True)
        self.anchor_connection.row_factory = sqlite3.Row

        def get_test_connection():
            conn = sqlite3.connect("file:campaign_setup_tests?mode=memory&cache=shared", uri=True)
            conn.row_factory = sqlite3.Row
            return conn

        database.get_connection = get_test_connection
        database.init_db()

    def tearDown(self):
        database.get_connection = self.original_get_connection
        self.anchor_connection.close()

    def test_preset_campaign_title_is_saved_only_at_final_step(self):
        campaign_id = database.create_campaign("guild", "channel", "Черновик")

        campaign = database.set_campaign_mode(campaign_id, "preset")
        self.assertEqual(campaign["setup_status"], "awaiting_adventure_choice")

        campaign = database.set_campaign_adventure(
            campaign_id,
            "Baldur's Gate_ Descent Into Avernus",
            "Baldur's Gate: Descent into Avernus",
        )
        self.assertEqual(campaign["title"], "Черновик")
        self.assertEqual(campaign["setup_status"], "awaiting_roll_mode")

        campaign = database.set_campaign_roll_mode(campaign_id, "bot_auto")
        self.assertEqual(campaign["setup_status"], "awaiting_hp_gain_mode")

        campaign = database.set_campaign_hp_gain_mode(campaign_id, "fixed")
        self.assertEqual(campaign["setup_status"], "awaiting_campaign_title")

        campaign = database.set_campaign_title(campaign_id, "Падение в Авернус")
        self.assertEqual(campaign["title"], "Падение в Авернус")
        self.assertEqual(campaign["setup_status"], "ready")

    def test_generated_campaign_also_waits_for_final_title(self):
        campaign_id = database.create_campaign("guild", "channel", "Черновик")

        campaign = database.set_campaign_mode(campaign_id, "generated")
        campaign = database.set_campaign_roll_mode(campaign_id, "bot_auto")
        campaign = database.set_campaign_hp_gain_mode(campaign_id, "fixed")

        self.assertEqual(campaign["setup_status"], "awaiting_campaign_title")


class AdventureChoiceTests(unittest.TestCase):
    def setUp(self):
        self.cog = GameCog.__new__(GameCog)

    def test_resolve_adventure_choice_accepts_number_and_slug(self):
        adventures = [
            {"slug": "frozen-vault", "title": "Frozen Vault"},
            {"slug": "ember-road", "title": "Ember Road"},
        ]

        by_number = self.cog._resolve_adventure_choice("2", adventures)
        by_slug = self.cog._resolve_adventure_choice("frozen-vault", adventures)

        self.assertEqual(by_number["slug"], "ember-road")
        self.assertEqual(by_slug["slug"], "frozen-vault")

    def test_format_adventure_choice_embed_shows_safe_description_separately(self):
        embed = self.cog._format_adventure_choice_embed(
            "preset",
            [
                {
                    "slug": "candlekeep",
                    "title": "Candlekeep Mysteries",
                    "description": "Антология приключений, связанных с библиотекой Candlekeep.",
                }
            ],
        )

        self.assertIn("**1. Candlekeep Mysteries** (`candlekeep`)", embed.description)
        self.assertIn("Кратко: Антология приключений, связанных с библиотекой Candlekeep.", embed.description)

    def test_campaign_title_prompt_uses_adventure_title_as_default(self):
        prompt = self.cog._campaign_title_prompt(
            {
                "campaign_mode": "preset",
                "adventure_title": "Baldur's Gate: Descent into Avernus",
                "title": "Новая кампания",
            }
        )

        self.assertIn("Название по умолчанию", prompt)
        self.assertIn("Baldur's Gate: Descent into Avernus", prompt)
        self.assertIn("`1`", prompt)


if __name__ == "__main__":
    unittest.main()
