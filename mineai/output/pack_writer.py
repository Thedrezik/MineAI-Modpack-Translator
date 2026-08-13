import json
import os
import re
import zipfile

from mineai.constants import PACK_FORMATS
from mineai.io_utils import atomic_write_bytes, atomic_write_text


class PackWriter:
    """Create translation packs and install data through an available loader."""

    _MOD_METADATA_PATHS = (
        "META-INF/neoforge.mods.toml",
        "META-INF/mods.toml",
        "fabric.mod.json",
        "quilt.mod.json",
    )

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
        self.rp_written = False
        self.dp_written = False
        self.resourcepack_enabled = False
        self.datapack_install_mode = self._detect_datapack_install_mode(mc_dir)
        self.datapack_installed_paths: list[str] = []
        self._kubejs_originals: list[tuple[str, bytes | None]] = []
        self._pending_files: dict[str, bytes] = {}
        fmt = PACK_FORMATS.get(mc_version, PACK_FORMATS["1.21.1"])

        rp_dir = os.path.join(mc_dir, "resourcepacks")
        if self.datapack_install_mode == "openloader":
            dp_dir: str | None = os.path.join(
                mc_dir, "config", "openloader", "data"
            )
        elif self.datapack_install_mode == "manual":
            dp_dir = os.path.join(mc_dir, "MineAI_Datapacks")
        else:
            dp_dir = None
        os.makedirs(rp_dir, exist_ok=True)
        if dp_dir:
            os.makedirs(dp_dir, exist_ok=True)

        safe_name = re.sub(r'[\\/*?:"<>|]', "", pack_base_name.strip() or "MineAI_Pack")
        if not safe_name.lower().endswith(".zip"):
            safe_name += ".zip"

        try:
            self.rp_zip_path = self._unique_path(rp_dir, safe_name)
            self._create_zip(
                self.rp_zip_path,
                fmt["rp"],
                f"{os.path.basename(self.rp_zip_path)} - MineAI",
            )
            self.rp_handle = zipfile.ZipFile(
                self.rp_zip_path,
                "a",
                compression=zipfile.ZIP_DEFLATED,
            )

            if dp_dir:
                dp_name = os.path.basename(self.rp_zip_path).replace(
                    ".zip",
                    "_Datapack.zip",
                )
                self.dp_zip_path = self._unique_path(dp_dir, dp_name)
                self._create_zip(
                    self.dp_zip_path,
                    fmt["dp"],
                    f"{dp_name} - MineAI",
                )
                self.dp_handle = zipfile.ZipFile(
                    self.dp_zip_path,
                    "a",
                    compression=zipfile.ZIP_DEFLATED,
                )
        except Exception:
            self._cleanup_partial_archives()
            raise

    @classmethod
    def _detect_datapack_install_mode(cls, mc_dir: str) -> str:
        mod_ids = cls._installed_mod_ids(os.path.join(mc_dir, "mods"))
        if "openloader" in mod_ids:
            return "openloader"
        if "kubejs" in mod_ids:
            return "kubejs"
        return "manual"

    @classmethod
    def _installed_mod_ids(cls, mods_dir: str) -> set[str]:
        """Read exact mod IDs so renamed JAR files are detected correctly."""
        mod_ids: set[str] = set()
        if not os.path.isdir(mods_dir):
            return mod_ids
        try:
            jar_names = os.listdir(mods_dir)
        except OSError:
            return mod_ids
        for jar_name in jar_names:
            if not jar_name.casefold().endswith(".jar"):
                continue
            jar_path = os.path.join(mods_dir, jar_name)
            try:
                with zipfile.ZipFile(jar_path) as archive:
                    names = {name.casefold(): name for name in archive.namelist()}
                    for metadata_path in cls._MOD_METADATA_PATHS:
                        actual_path = names.get(metadata_path.casefold())
                        if not actual_path:
                            continue
                        text = archive.read(actual_path).decode(
                            "utf-8-sig", errors="replace"
                        )
                        if metadata_path.endswith(".toml"):
                            mod_ids.update(
                                value.casefold()
                                for value in re.findall(
                                    r'(?mi)^\s*modId\s*=\s*["\']([^"\']+)["\']',
                                    text,
                                )
                            )
                        else:
                            try:
                                metadata = json.loads(text)
                            except json.JSONDecodeError:
                                metadata = None
                            if isinstance(metadata, dict):
                                mod_id = metadata.get("id")
                                if isinstance(mod_id, str):
                                    mod_ids.add(mod_id.casefold())
                                quilt_loader = metadata.get("quilt_loader")
                                if isinstance(quilt_loader, dict):
                                    quilt_id = quilt_loader.get("id")
                                    if isinstance(quilt_id, str):
                                        mod_ids.add(quilt_id.casefold())
            except (OSError, zipfile.BadZipFile, RuntimeError):
                continue
        return mod_ids

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

    @staticmethod
    def _normalize_output_path(internal_path: str) -> str:
        normalized = internal_path.replace("\\", "/").strip("/")
        lower_path = normalized.casefold()
        embedded_assets = lower_path.find("/assets/")
        if embedded_assets >= 0:
            return normalized[embedded_assets + 1 :]
        match = re.fullmatch(
            r"(?i)(?:data/)?(?P<namespace>[a-z0-9_.-]+)/lang/"
            r"(?P<locale>[a-z]{2}_[a-z]{2}\.json)",
            normalized,
        )
        if match:
            return (
                f"assets/{match.group('namespace')}/lang/"
                f"{match.group('locale')}"
            )
        return normalized

    def write(self, internal_path: str, data: bytes) -> None:
        internal_path = self._normalize_output_path(internal_path)
        handle = self.handle_for_path(internal_path)
        is_data = internal_path.lower().startswith("data/")
        if not handle and not (
            is_data and self.datapack_install_mode == "kubejs"
        ):
            return
        if internal_path in self._pending_files:
            self._pending_files[internal_path] = self._merge_locale_json(
                internal_path,
                self._pending_files[internal_path],
                data,
            )
            return
        self._pending_files[internal_path] = data
        self.written.add(internal_path)
        if is_data:
            self.dp_written = True
        else:
            self.rp_written = True

    @staticmethod
    def _merge_locale_json(path: str, first: bytes, second: bytes) -> bytes:
        if not re.search(
            r"(?i)(?:^|/)lang/[a-z]{2}_[a-z]{2}\.json$",
            path.replace("\\", "/"),
        ):
            return first
        try:
            merged = json.loads(first.decode("utf-8-sig"))
            incoming = json.loads(second.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return first
        if not isinstance(merged, dict) or not isinstance(incoming, dict):
            return first
        merged.update(incoming)
        return json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")

    def _flush_pending_files(self) -> None:
        kubejs_files: dict[str, bytes] = {}
        kubejs_data_root = os.path.realpath(
            os.path.join(self.mc_dir, "kubejs", "data")
        )
        for path, payload in self._pending_files.items():
            if (
                path.lower().startswith("data/")
                and self.datapack_install_mode == "kubejs"
            ):
                target = os.path.join(
                    self.mc_dir,
                    "kubejs",
                    *path.replace("\\", "/").split("/"),
                )
                target = os.path.realpath(target)
                try:
                    inside_data_root = (
                        os.path.commonpath((kubejs_data_root, target))
                        == kubejs_data_root
                    )
                except ValueError:
                    inside_data_root = False
                if not inside_data_root or target == kubejs_data_root:
                    raise ValueError(f"Unsafe KubeJS data path: {path}")
                kubejs_files[target] = payload
                continue
            handle = self.handle_for_path(path)
            if handle:
                handle.writestr(path, payload)
        if kubejs_files:
            self._install_kubejs_files(kubejs_files)
        self._pending_files.clear()

    def _install_kubejs_files(self, files: dict[str, bytes]) -> None:
        """Install loose data transactionally; restore originals on failure."""
        originals: list[tuple[str, bytes | None]] = []
        try:
            for target, payload in files.items():
                original = None
                if os.path.isfile(target):
                    with open(target, "rb") as handle:
                        original = handle.read()
                originals.append((target, original))
                atomic_write_bytes(target, payload)
        except BaseException:
            for target, original in reversed(originals):
                try:
                    if original is None:
                        if os.path.exists(target):
                            os.remove(target)
                    else:
                        atomic_write_bytes(target, original)
                except OSError:
                    pass
            raise
        self._kubejs_originals = originals
        self.datapack_installed_paths = list(files)

    def _rollback_kubejs_files(self) -> None:
        for target, original in reversed(self._kubejs_originals):
            try:
                if original is None:
                    if os.path.exists(target):
                        os.remove(target)
                else:
                    atomic_write_bytes(target, original)
            except OSError:
                pass
        self._kubejs_originals.clear()
        self.datapack_installed_paths.clear()

    @staticmethod
    def _validate_zip(path: str | None) -> None:
        if not path:
            return
        with zipfile.ZipFile(path, "r") as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise zipfile.BadZipFile(
                    f"CRC check failed for {bad_entry} in {path}"
                )

    def _close_handles(self) -> list[Exception]:
        errors: list[Exception] = []
        for attribute in ("rp_handle", "dp_handle"):
            handle = getattr(self, attribute)
            if handle is None:
                continue
            try:
                handle.close()
            except Exception as exc:
                errors.append(exc)
            finally:
                setattr(self, attribute, None)
        return errors

    def _remove_output_archives(self) -> None:
        for path in (self.rp_zip_path, self.dp_zip_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _cleanup_partial_archives(self) -> None:
        self._pending_files.clear()
        self._close_handles()
        self._remove_output_archives()
        self._rollback_kubejs_files()

    def abort(self) -> None:
        """Close and remove archives created by an incomplete translation job."""
        self._cleanup_partial_archives()

    def close(self) -> tuple[str | None, str | None]:
        errors: list[Exception] = []
        try:
            self._flush_pending_files()
        except Exception as exc:
            errors.append(exc)
        errors.extend(self._close_handles())
        for path_attribute, has_payload in (
            ("rp_zip_path", self.rp_written),
            ("dp_zip_path", self.dp_written),
        ):
            path = getattr(self, path_attribute)
            if not has_payload:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError as exc:
                        errors.append(exc)
                setattr(self, path_attribute, None)
        if not errors:
            for path in (self.rp_zip_path, self.dp_zip_path):
                try:
                    self._validate_zip(path)
                except Exception as exc:
                    errors.append(exc)
                    break
        if errors:
            self._remove_output_archives()
            self._rollback_kubejs_files()
            raise errors[0]
        if self.rp_zip_path:
            self.resourcepack_enabled = self._enable_resource_pack(
                self.rp_zip_path
            )
        self._kubejs_originals.clear()
        return self.rp_zip_path, self.dp_zip_path

    def _enable_resource_pack(self, resourcepack_path: str) -> bool:
        """Add the generated pack to options.txt after existing lower priorities."""
        options_path = os.path.join(self.mc_dir, "options.txt")
        if not os.path.isfile(options_path):
            return False
        try:
            with open(options_path, encoding="utf-8-sig", newline="") as handle:
                content = handle.read()
            match = re.search(
                r"(?m)^resourcePacks:(\[[^\r\n]*\])(?=\r?$)", content
            )
            if not match:
                return False
            selected = json.loads(match.group(1))
            if not isinstance(selected, list) or not all(
                isinstance(value, str) for value in selected
            ):
                return False
            pack_id = f"file/{os.path.basename(resourcepack_path)}"
            selected = [value for value in selected if value != pack_id]
            selected.append(pack_id)
            replacement = "resourcePacks:" + json.dumps(
                selected, ensure_ascii=False
            )
            updated = content[: match.start()] + replacement + content[match.end() :]
            if updated != content:
                atomic_write_text(options_path, updated)
            return True
        except (OSError, ValueError, TypeError):
            return False
