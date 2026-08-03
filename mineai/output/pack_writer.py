import json
import os
import re
import zipfile

from mineai.constants import PACK_FORMATS


class PackWriter:
    """Creates resource pack and datapack zip handles for translated assets."""

    def __init__(
        self,
        mc_dir: str,
        pack_base_name: str,
        mc_version: str,
        lang_name: str,
    ) -> None:
        self.mc_dir = mc_dir
        self.rp_zip_path: str | None = None
        self.dp_zip_path: str | None = None
        self.rp_handle: zipfile.ZipFile | None = None
        self.dp_handle: zipfile.ZipFile | None = None
        self.written: set[str] = set()
        fmt = PACK_FORMATS.get(mc_version, PACK_FORMATS["1.21.1"])

        rp_dir = os.path.join(mc_dir, "resourcepacks")
        dp_dir = os.path.join(mc_dir, "config", "openloader", "data")
        os.makedirs(rp_dir, exist_ok=True)
        os.makedirs(dp_dir, exist_ok=True)

        safe_name = re.sub(r'[\\/*?:"<>|]', "", pack_base_name.strip() or "MineAI_Pack")
        if not safe_name.lower().endswith(".zip"):
            safe_name += ".zip"

        self.rp_zip_path = self._unique_path(rp_dir, safe_name)
        self._create_zip(
            self.rp_zip_path,
            fmt["rp"],
            f"{os.path.basename(self.rp_zip_path)} - MineAI",
        )
        self.rp_handle = zipfile.ZipFile(self.rp_zip_path, "a", compression=zipfile.ZIP_DEFLATED)

        dp_name = os.path.basename(self.rp_zip_path).replace(".zip", "_Datapack.zip")
        self.dp_zip_path = os.path.join(dp_dir, dp_name)
        self.dp_zip_path = self._unique_path(dp_dir, dp_name)
        self._create_zip(self.dp_zip_path, fmt["dp"], f"{dp_name} - MineAI")
        self.dp_handle = zipfile.ZipFile(self.dp_zip_path, "a", compression=zipfile.ZIP_DEFLATED)

    @staticmethod
    def _unique_path(directory: str, filename: str) -> str:
        path = os.path.join(directory, filename)
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(filename)
        counter = 1
        while True:
            candidate = os.path.join(directory, f"{base}_{counter}{ext}")
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    @staticmethod
    def _create_zip(path: str, pack_format: int, description: str) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "pack.mcmeta",
                json.dumps({"pack": {"pack_format": pack_format, "description": description}}, indent=2),
            )

    def handle_for_path(self, internal_path: str) -> zipfile.ZipFile | None:
        if internal_path.lower().startswith("data/"):
            return self.dp_handle
        return self.rp_handle

    def write(self, internal_path: str, data: bytes) -> None:
        handle = self.handle_for_path(internal_path)
        if handle and internal_path not in self.written:
            handle.writestr(internal_path, data)
            self.written.add(internal_path)

    def close(self) -> tuple[str | None, str | None]:
        rp, dp = self.rp_zip_path, self.dp_zip_path
        if self.rp_handle:
            self.rp_handle.close()
            self.rp_handle = None
        if self.dp_handle:
            self.dp_handle.close()
            self.dp_handle = None
        return rp, dp
