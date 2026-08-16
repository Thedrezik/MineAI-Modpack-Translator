from __future__ import annotations

from mineai.formatkit_bridge import (
    FORMATKIT_SOURCE_SHA,
    build_locale_output,
    is_formatkit_locale_path,
    plan_locale_work,
    validate_locale_candidate,
)
from mineai.processors.estimator import StringEstimator as LegacyStringEstimator
from mineai.processors.jar import JarProcessor as LegacyJarProcessor
from mineai.processors.selection import skip_threshold_reached


class FormatKitJarProcessor(LegacyJarProcessor):
    """Pilot JAR processor: FormatKit owns recognized Minecraft locale structure."""

    def _process_lang_entry(
        self,
        zin,
        zout,
        item,
        locale_files,
        target_file,
        target_lang,
        mode,
        output_mode,
        pack_writer,
        mod_name,
        written_inplace,
    ) -> bool:
        if not is_formatkit_locale_path(item.filename):
            return super()._process_lang_entry(
                zin,
                zout,
                item,
                locale_files,
                target_file,
                target_lang,
                mode,
                output_mode,
                pack_writer,
                mod_name,
                written_inplace,
            )

        try:
            raw_source = zin.read(item)
            source_bom = raw_source.startswith(b"\xef\xbb\xbf")
            source_text = raw_source.decode("utf-8-sig")

            # Current FormatKit's public Minecraft locale profile is already
            # Modonomicon-aware. v3.4 therefore has one deterministic locale
            # path instead of sniffing the JAR and swapping parser subclasses.
            preliminary = plan_locale_work(
                item.filename,
                source_text,
                target_lang["file"],
                None,
                mode,
            )
            assert preliminary is not None
            tr_path = preliminary.target_path
            tr_key = tr_path.lower()

            target_text = None
            if mode != "force" and tr_key in locale_files:
                try:
                    target_text = zin.read(locale_files[tr_key]).decode("utf-8-sig")
                except (OSError, UnicodeError):
                    target_text = None

            work = plan_locale_work(
                item.filename,
                source_text,
                target_lang["file"],
                target_text,
                mode,
            )
            assert work is not None
        except (OSError, UnicodeError, ValueError) as exc:
            self.callbacks.on_log(
                f"⚠ FormatKit пропустил {item.filename}: {exc}",
                "yellow",
            )
            return False

        if work.target_parse_error:
            self.callbacks.on_log(
                "⚠ FormatKit отбросил повреждённую существующую локализацию "
                f"{work.target_path}: {work.target_parse_error}",
                "yellow",
            )

        if work.total_translatable == 0:
            return False

        if mode == "skip" and skip_threshold_reached(
            work.total_translatable,
            len(work.pending),
        ):
            return self._copy_existing(
                zin,
                locale_files,
                tr_key,
                tr_path,
                output_mode,
                pack_writer,
                {},
                {},
                mode,
            )

        translated: dict[str, str] = {}
        if work.pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [Интерфейс/FormatKit] — "
                f"{len(work.pending)} строк",
                "cyan",
            )
            translated = self.service.translate_dict(
                dict(work.pending),
                target_lang,
                self.callbacks,
                context=mod_name,
                candidate_validator=lambda unit_id, candidate: validate_locale_candidate(
                    work, unit_id, candidate
                ),
                preserve_source_structure=True,
            )

        if not self.state.should_run():
            return False

        try:
            output_text = build_locale_output(work, translated)
        except ValueError as exc:
            self.callbacks.on_log(
                f"❌ FormatKit отклонил реконструкцию {item.filename}: {exc}",
                "red",
            )
            return False

        payload = output_text.encode("utf-8")
        if source_bom:
            payload = b"\xef\xbb\xbf" + payload

        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_path, payload)
            return True
        if zout:
            zout.writestr(tr_path, payload)
            written_inplace.add(tr_path)
            return True
        return False


class FormatKitStringEstimator(LegacyStringEstimator):
    """Estimator counterpart for the exact JAR locale pilot above."""

    def _count_lang(
        self,
        archive,
        item,
        locale,
        target_file,
        mode,
        target_regex,
    ) -> int:
        if not is_formatkit_locale_path(item.filename):
            return super()._count_lang(
                archive,
                item,
                locale,
                target_file,
                mode,
                target_regex,
            )

        try:
            source_text = archive.read(item).decode("utf-8-sig")
            preliminary = plan_locale_work(
                item.filename,
                source_text,
                target_file[:-5],
                None,
                mode,
            )
            assert preliminary is not None
            target_key = preliminary.target_path.lower()

            target_text = None
            if mode != "force" and target_key in locale:
                try:
                    target_text = archive.read(locale[target_key]).decode("utf-8-sig")
                except (OSError, UnicodeError):
                    target_text = None

            work = plan_locale_work(
                item.filename,
                source_text,
                target_file[:-5],
                target_text,
                mode,
            )
            assert work is not None
        except (OSError, UnicodeError, ValueError):
            return 0

        if mode == "skip" and skip_threshold_reached(
            work.total_translatable,
            len(work.pending),
        ):
            return 0
        return len(work.pending)


__all__ = [
    "FORMATKIT_SOURCE_SHA",
    "FormatKitJarProcessor",
    "FormatKitStringEstimator",
]
