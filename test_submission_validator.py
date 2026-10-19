import struct
import tempfile
import unittest
from pathlib import Path

from validation_logic import (
    FileResult,
    _read_png_dimensions,
    _read_tga_dimensions,
    _unique_reasons,
    _validate_maya_name,
    _validate_texture,
    add_prefix_to_file,
    collect_errors_by_category,
    collect_error_names,
    fixed_filename,
    list_submission_files,
    validate_folder,
)
from validator_ui import SubmissionValidator


def _write_tga(path: Path, width: int, height: int) -> None:
    header = bytearray(18)
    struct.pack_into("<HH", header, 12, width, height)
    path.write_bytes(header)


def _write_png_header(path: Path, width: int, height: int) -> None:
    header = bytearray(b"\x89PNG\r\n\x1a\n")
    header.extend(struct.pack(">I", 13))
    header.extend(b"IHDR")
    header.extend(struct.pack(">II", width, height))
    path.write_bytes(header)


class SubmissionValidatorTests(unittest.TestCase):
    def test_add_prefix_preserves_existing_filename_and_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wood_albedo.png"
            source.write_bytes(b"png")

            target = add_prefix_to_file(root, Path("wood_albedo.png"), "T_")

            self.assertEqual(target.name, "T_wood_albedo.png")
            self.assertTrue(target.is_file())
            self.assertFalse(source.exists())

    def test_filename_fix_lowercases_and_removes_texture_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "t_Texture_Wood_Albedo.TGA"
            source.write_bytes(b"tga")

            target = add_prefix_to_file(root, Path(source.name), "T_")

            self.assertEqual(target.name, "T_wood_albedo.tga")
            self.assertTrue(target.is_file())
            self.assertFalse(source.exists())

    def test_filename_fix_replaces_special_characters_with_underscores(self) -> None:
        self.assertEqual(
            fixed_filename(Path("T_Wood-Color (Final)!_Albedo.TGA"), "T_"),
            "T_wood_color_final_albedo.tga",
        )
        self.assertEqual(
            fixed_filename(Path("MSH_Chair (High-Poly).MA"), "msh_"),
            "msh_chair_high_poly.ma",
        )

    def test_filename_rename_replaces_special_characters_with_underscores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MSH_Chair (High-Poly).MA"
            source.write_text("", encoding="utf-8")

            target = add_prefix_to_file(root, Path(source.name), "msh_")

            self.assertEqual(target.name, "msh_chair_high_poly.ma")
            self.assertTrue(target.is_file())
            self.assertFalse(source.exists())

    def test_maya_filename_fix_lowercases_and_removes_texture_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MSH_Chair_Texture.MA"
            source.write_text("", encoding="utf-8")

            target = add_prefix_to_file(root, Path(source.name), "msh_")

            self.assertEqual(target.name, "msh_chair.ma")
            self.assertTrue(target.is_file())

    def test_filename_fix_handles_case_only_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MSH_Chair.MA"
            source.write_text("", encoding="utf-8")

            target = add_prefix_to_file(root, Path(source.name), "msh_")

            self.assertEqual(target.name, "msh_chair.ma")
            self.assertTrue(target.is_file())
            self.assertEqual([path.name for path in root.iterdir()], [target.name])

    def test_fixed_filename_adds_missing_prefix_after_normalization(self) -> None:
        self.assertEqual(
            fixed_filename(Path("Wood_Textures_Albedo.PNG"), "T_"),
            "T_wood_albedo.png",
        )

    def test_fixed_filename_uses_selected_texture_suffix(self) -> None:
        self.assertEqual(
            fixed_filename(
                Path("T_Wood_Albedo.TGA"),
                "T_",
                "normal",
            ),
            "T_wood_normal.tga",
        )

    def test_fixed_filename_rejects_unsupported_texture_suffix(self) -> None:
        with self.assertRaises(ValueError):
            fixed_filename(Path("T_wood.tga"), "T_", "color")

    def test_texture_rename_applies_selected_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "T_wood.png"
            _write_png_header(source, 512, 512)

            target = add_prefix_to_file(
                root,
                Path(source.name),
                "T_",
                "roughness",
            )

            self.assertEqual(target.name, "T_wood_roughness.png")
            self.assertTrue(target.is_file())

    def test_add_prefix_rejects_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chair.ma").write_text("", encoding="utf-8")
            (root / "msh_chair.ma").write_text("", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                add_prefix_to_file(root, Path("chair.ma"), "msh_")

    def test_add_prefix_rejects_paths_outside_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "submission"
            root.mkdir()

            with self.assertRaises(ValueError):
                add_prefix_to_file(root, Path("..") / "outside.ma", "msh_")

    def test_error_summary_lists_unique_names_without_details(self) -> None:
        results = [
            FileResult(
                "T_bad_name.tga",
                "Texture",
                "FAIL",
                "Invalid name: expected T_ prefix; Invalid name: use underscores",
                ("Invalid name", "Invalid name"),
            ),
            FileResult(
                "notes.txt",
                "Other",
                "FAIL",
                "Unsupported file type; redundant file",
                ("Unsupported file type",),
            ),
        ]

        self.assertEqual(
            collect_error_names(results),
            ["Invalid name", "Unsupported file type"],
        )
        self.assertEqual(
            collect_errors_by_category(results),
            {
                "Maya": [],
                "Textures": ["Invalid name"],
                "Other errors": ["Unsupported file type"],
            },
        )

    def test_folder_summary_contains_only_error_categories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_tga(root / "wood.tga", 3000, 256)
            (root / "notes.txt").write_text("extra file", encoding="utf-8")

            results = validate_folder(root, "")

            self.assertEqual(
                collect_error_names(results),
                [
                    "Invalid name",
                    "Not Power of 2",
                    "Resolution too high",
                    "Unsupported file type",
                ],
            )

    def test_duplicate_validation_reasons_are_removed_in_original_order(self) -> None:
        self.assertEqual(
            _unique_reasons(["Invalid name", "Resolution too high", "Invalid name"]),
            ["Invalid name", "Resolution too high"],
        )

    def test_detail_log_formats_each_reason_on_a_separate_line(self) -> None:
        formatted = SubmissionValidator._format_detail_log(
            "Invalid name; Not Power of 2; Resolution too high"
        )

        self.assertEqual(
            formatted,
            "Invalid name\nNot Power of 2\nResolution too high",
        )

    def test_fixed_file_log_lists_old_and_new_filenames(self) -> None:
        fixed_files = {
            "wood_albedo.png": "T_wood_albedo.png",
            "models/chair.ma": "models/msh_chair.ma",
        }

        self.assertEqual(
            SubmissionValidator._format_fixed_file_log(fixed_files),
            "  wood_albedo.png -> T_wood_albedo.png\n"
            "  models/chair.ma -> models/msh_chair.ma\n",
        )

    def test_valid_texture_name_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "T_wood_albedo.tga"
            _write_tga(path, 1024, 512)
            self.assertEqual(_validate_texture(path), [])

    def test_valid_png_texture_name_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "T_wood_albedo.png"
            _write_png_header(path, 1024, 512)
            self.assertEqual(_validate_texture(path), [])

    def test_validation_suggests_only_missing_file_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_tga(root / "wood_albedo.tga", 512, 512)
            (root / "chair.ma").write_text("", encoding="utf-8")
            _write_tga(root / "T_table_albedo.tga", 512, 512)
            (root / "msh_stool.ma").write_text("", encoding="utf-8")

            results = validate_folder(root, "")
            suggestions = {
                result.relative_path: result.prefix_to_add for result in results
            }

            self.assertEqual(suggestions["wood_albedo.tga"], "T_")
            self.assertEqual(suggestions["chair.ma"], "msh_")
            self.assertIsNone(suggestions["T_table_albedo.tga"])
            self.assertIsNone(suggestions["msh_stool.ma"])

    def test_validation_suggests_fix_for_existing_prefix_with_texture_keyword(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_tga(root / "T_wood_texture_albedo.tga", 512, 512)

            result = validate_folder(root, "")[0]

            self.assertEqual(result.status, "FAIL")
            self.assertEqual(result.prefix_to_add, "T_")
            self.assertIn("remove the texture keyword", result.details)

    def test_png_resolution_constraints_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "T_wood_albedo.png"
            _write_png_header(path, 3000, 300)
            failures = _validate_texture(path)
            self.assertTrue(any("Resolution too high" in item for item in failures))
            self.assertTrue(any("Not Power of 2" in item for item in failures))

    def test_png_dimensions_are_read_from_ihdr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            _write_png_header(path, 2048, 1024)
            self.assertEqual(_read_png_dimensions(path), (2048, 1024))

    def test_texture_reports_naming_and_resolution_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t_Wood texture_color.TGA"
            _write_tga(path, 3000, 300)
            failures = _validate_texture(path)
            self.assertIn("Invalid format: expected .tga or .png", failures)
            self.assertTrue(any("Spaces in name" in item for item in failures))
            self.assertTrue(any("Invalid name" in item for item in failures))
            self.assertTrue(any("Resolution too high" in item for item in failures))
            self.assertTrue(any("Not Power of 2" in item for item in failures))

    def test_bad_tga_header_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "T_wood_albedo.tga"
            path.write_bytes(b"short")
            self.assertIn(
                "Cannot read texture resolution: TGA header is incomplete",
                _validate_texture(path),
            )

    def test_maya_name_validation(self) -> None:
        self.assertEqual(_validate_maya_name(Path("msh_chair_v01.ma")), [])
        failures = _validate_maya_name(Path("Msh_Chair File.MA"))
        self.assertIn("Invalid format: expected .ma", failures)
        self.assertTrue(any("Spaces in name" in item for item in failures))
        self.assertTrue(any("Invalid name" in item for item in failures))

    def test_read_tga_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.tga"
            _write_tga(path, 2048, 1024)
            self.assertEqual(_read_tga_dimensions(path), (2048, 1024))

    def test_folder_scan_keeps_other_files_and_fails_unchecked_maya(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_tga(root / "T_wood_albedo.tga", 512, 512)
            (root / "msh_chair.ma").write_text("", encoding="utf-8")
            (root / "notes.txt").write_text("submission notes", encoding="utf-8")

            results = validate_folder(root, "")

            self.assertEqual([result.relative_path for result in results], [
                "msh_chair.ma",
                "notes.txt",
                "T_wood_albedo.tga",
            ])
            self.assertEqual(results[0].status, "FAIL")
            self.assertIn("Maya QC was not run", results[0].details)
            self.assertEqual(results[1].status, "FAIL")
            self.assertIn("Unsupported file type", results[1].details)
            self.assertEqual(results[2].status, "PASS")

    def test_validation_reports_progress_for_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("submission notes", encoding="utf-8")
            progress: list[tuple[int, int, str]] = []

            validate_folder(root, "", lambda *event: progress.append(event))

            self.assertIn((0, 0, "Scanning files..."), progress)
            self.assertIn((0, 1, "Validating files..."), progress)
            self.assertIn((1, 1, "Validating files..."), progress)

    def test_file_list_is_sorted_and_includes_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "source"
            nested.mkdir()
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            (nested / "msh_chair.ma").write_text("", encoding="utf-8")

            files = list_submission_files(root)

            self.assertEqual(
                [path.relative_to(root) for path in files],
                [Path("notes.txt"), Path("source") / "msh_chair.ma"],
            )

if __name__ == "__main__":
    unittest.main()
