import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai_formatkit.jar_container import JarContainer, SignedJarError
from mineai_formatkit.minecraft_lang import MinecraftLangJsonAdapter
from mineai_formatkit.registry import FormatRegistry


class JarContainerTests(unittest.TestCase):
    def _jar(self, root: Path, signed: bool = False) -> Path:
        path = root / "mod.jar"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("assets/demo/lang/en_us.json", '{"x":"Hello"}')
            z.writestr("data/demo/recipe/a.json", '{"type":"minecraft:crafting_shaped"}')
            if signed:
                z.writestr("META-INF/DEMO.SF", b"sig")
                z.writestr("META-INF/DEMO.RSA", b"rsa")
        return path

    def test_discovers_registered_translatable_entries(self):
        with tempfile.TemporaryDirectory() as td:
            jar = self._jar(Path(td))
            reg = FormatRegistry(); reg.register(MinecraftLangJsonAdapter())
            inspection = JarContainer().inspect(jar, reg)
            self.assertEqual(inspection.candidate_entries, ("assets/demo/lang/en_us.json",))

    def test_signed_jar_cannot_be_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); jar = self._jar(root, signed=True)
            with self.assertRaises(SignedJarError):
                JarContainer().rebuild(jar, root / "out.jar", {"assets/demo/lang/ru_ru.json": b'{}'})

    def test_unsigned_rebuild_preserves_unmodified_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); jar = self._jar(root)
            out = JarContainer().rebuild(jar, root / "out.jar", {"assets/demo/lang/ru_ru.json": '{"x":"Привет"}'.encode('utf-8')})
            with zipfile.ZipFile(jar) as a, zipfile.ZipFile(out) as b:
                for name in a.namelist():
                    self.assertEqual(a.read(name), b.read(name))
                self.assertEqual(b.read("assets/demo/lang/ru_ru.json").decode(), '{"x":"Привет"}')
