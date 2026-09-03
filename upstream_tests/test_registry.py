import unittest

from mineai_formatkit.guideme import GuideMeMarkdownAdapter
from mineai_formatkit.ftb_quests import FtbQuestsChapterAdapter, FtbQuestsLangAdapter
from mineai_formatkit.minecraft_lang import MinecraftLangJsonAdapter
from mineai_formatkit.minecraft_text import MinecraftTextComponentAdapter
from mineai_formatkit.oracle_index import OracleIndexMdxAdapter, OracleIndexMetaJsonAdapter
from mineai_formatkit.patchouli import PatchouliBookJsonAdapter
from mineai_formatkit.registry import FormatRegistry


class RegistryTests(unittest.TestCase):
    def test_registry_detects_guideme_adapter(self) -> None:
        registry = FormatRegistry()
        adapter = GuideMeMarkdownAdapter()
        registry.register(adapter)
        self.assertIs(registry.detect("assets/ae2/ae2guide/index.md"), adapter)
        self.assertIsNone(registry.detect("assets/ae2/ae2guide/assets/logo.png"))

    def test_registry_detects_minecraft_lang_adapter(self) -> None:
        registry = FormatRegistry()
        adapter = MinecraftLangJsonAdapter()
        registry.register(adapter)
        self.assertIs(registry.detect("assets/rechiseled/lang/en_us.json"), adapter)
        self.assertIsNone(registry.detect("assets/rechiseled/models/block/foo.json"))

    def test_registry_detects_minecraft_text_component_adapter(self) -> None:
        registry = FormatRegistry()
        adapter = MinecraftTextComponentAdapter()
        registry.register(adapter)
        self.assertIs(registry.detect("data/demo/advancement/root.json"), adapter)
        self.assertIsNone(registry.detect("data/demo/worldgen/template_pool/root.json"))

    def test_registry_detects_ftb_quests_adapters(self) -> None:
        registry = FormatRegistry()
        lang = FtbQuestsLangAdapter()
        chapter = FtbQuestsChapterAdapter()
        registry.register(lang)
        registry.register(chapter)
        self.assertIs(
            registry.detect("config/ftbquests/quests/lang/en_us.snbt"), lang
        )
        self.assertIs(
            registry.detect("config/ftbquests/quests/chapters/welcome.snbt"), chapter
        )
        self.assertIsNone(registry.detect("config/ftbquests/quests/data.snbt"))

    def test_registry_detects_patchouli_adapter(self) -> None:
        registry = FormatRegistry()
        adapter = PatchouliBookJsonAdapter()
        registry.register(adapter)
        self.assertIs(registry.detect("assets/demo/patchouli_books/guide/en_us/entries/start.json"), adapter)
        self.assertIsNone(registry.detect("assets/demo/recipes/start.json"))

    def test_registry_detects_oracle_index_adapters(self) -> None:
        registry = FormatRegistry()
        mdx = OracleIndexMdxAdapter()
        meta = OracleIndexMetaJsonAdapter()
        registry.register(mdx)
        registry.register(meta)
        self.assertIs(registry.detect("oracle_index/books/demo/.content/start.mdx"), mdx)
        self.assertIs(registry.detect("oracle_index/books/demo/.content/_meta.json"), meta)
        self.assertIsNone(registry.detect("oracle_index/books/demo/sinytra-wiki.json"))


if __name__ == "__main__":
    unittest.main()
