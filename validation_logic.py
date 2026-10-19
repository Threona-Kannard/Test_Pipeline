from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


TEXTURE_SUFFIXES = ("albedo", "normal", "roughness")
MAX_TEXTURE_SIZE = 2048
MAYA_QC_TIMEOUT_SECONDS = 300
RESULT_MARKER = "QC_RESULT_JSON:"


@dataclass
class FileResult:
    relative_path: str
    file_type: str
    status: str
    details: str
    error_names: tuple[str, ...] = ()
    prefix_to_add: str | None = None


def _unique_reasons(reasons: list[str]) -> list[str]:
    return list(dict.fromkeys(reason for reason in reasons if reason))


def _error_name(reason: str) -> str:
    name = reason.partition(":")[0].partition(";")[0].strip()
    return name or reason.strip()


def collect_errors_by_category(results: list[FileResult]) -> dict[str, list[str]]:
    categories = {
        "Maya": "Maya",
        "Texture": "Textures",
        "Other": "Other errors",
    }
    errors: dict[str, set[str]] = {name: set() for name in categories.values()}
    for result in results:
        category = categories.get(result.file_type)
        if result.status == "FAIL" and category:
            errors[category].update(result.error_names)
    return {
        category: sorted(names, key=str.casefold)
        for category, names in errors.items()
    }


def collect_error_names(results: list[FileResult]) -> list[str]:
    return sorted(
        {
            error_name
            for names in collect_errors_by_category(results).values()
            for error_name in names
        },
        key=str.casefold,
    )


def _has_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def _has_texture_keyword(value: str) -> bool:
    return re.search(r"textures?", value, re.IGNORECASE) is not None


def _is_texture_candidate(path: Path) -> bool:
    return path.suffix.lower() in (".tga", ".png") or path.name.lower().startswith("t_")


def _is_maya_candidate(path: Path) -> bool:
    return path.suffix.lower() == ".ma" or path.name.lower().startswith("msh_")


def fixed_filename(
    path: Path,
    prefix: str,
    texture_suffix: str | None = None,
) -> str:
    if prefix not in ("T_", "msh_"):
        raise ValueError(f"Unsupported filename prefix: {prefix}")
    if texture_suffix is not None and texture_suffix not in TEXTURE_SUFFIXES:
        raise ValueError(f"Unsupported texture suffix: {texture_suffix}")

    stem = re.sub(r"textures?", "", path.stem.lower(), flags=re.IGNORECASE)
    stem = re.sub(r"[^a-z0-9_]", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    if prefix == "T_":
        if stem.startswith("t_"):
            stem = f"T_{stem[2:]}"
        else:
            stem = f"T_{stem}"
        if texture_suffix is not None:
            for suffix in TEXTURE_SUFFIXES:
                ending = f"_{suffix}"
                if stem.endswith(ending):
                    stem = stem[: -len(ending)]
                    break
            stem = f"{stem}_{texture_suffix}"
    elif not stem.startswith(prefix):
        stem = f"{prefix}{stem}"

    return f"{stem}{path.suffix.lower()}"


def add_prefix_to_file(
    root: Path,
    relative_path: Path,
    prefix: str,
    texture_suffix: str | None = None,
) -> Path:
    if prefix not in ("T_", "msh_"):
        raise ValueError(f"Unsupported filename prefix: {prefix}")

    resolved_root = root.resolve()
    source = root / relative_path
    try:
        source.resolve(strict=False).relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("The source file is outside the selected folder.") from error

    resolved_source = source.resolve(strict=True)
    resolved_source.relative_to(resolved_root)
    if not source.is_file():
        raise OSError(f"The source path is not a file: {source}")

    target = source.with_name(fixed_filename(source, prefix, texture_suffix))
    try:
        target.resolve(strict=False).relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("The target file would be outside the selected folder.") from error

    if target.name == source.name:
        return source
    if target.exists() or target.is_symlink():
        try:
            same_file = target.samefile(source)
        except OSError:
            same_file = False
        if not same_file:
            raise FileExistsError(f"The target file already exists: {target.name}")

        temporary = source.with_name(f".{source.name}.rename-{os.getpid()}")
        if temporary.exists() or temporary.is_symlink():
            raise FileExistsError(f"The temporary rename path already exists: {temporary.name}")
        source.rename(temporary)
        try:
            temporary.rename(target)
        except OSError:
            temporary.rename(source)
            raise
        return target

    source.rename(target)
    return target


def _read_tga_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as image_file:
        header = image_file.read(18)
    if len(header) < 18:
        raise ValueError("TGA header is incomplete")
    width, height = struct.unpack_from("<HH", header, 12)
    if width < 1 or height < 1:
        raise ValueError("TGA dimensions are invalid")
    return width, height


def _read_png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as image_file:
        header = image_file.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG header is invalid or incomplete")
    if struct.unpack_from(">I", header, 8)[0] != 13 or header[12:16] != b"IHDR":
        raise ValueError("PNG IHDR chunk is invalid")
    width, height = struct.unpack_from(">II", header, 16)
    if width < 1 or height < 1:
        raise ValueError("PNG dimensions are invalid")
    return width, height


def _validate_texture(path: Path) -> list[str]:
    failures: list[str] = []
    name = path.name
    stem = path.stem

    if path.suffix not in (".tga", ".png"):
        failures.append("Invalid format: expected .tga or .png")
    if _has_whitespace(name):
        failures.append("Spaces in name; use underscores instead")
    if not stem.startswith("T_"):
        failures.append("Invalid name: expected T_ prefix")
    elif stem[2:] != stem[2:].lower():
        failures.append("Invalid name: use lowercase after the T_ prefix")
    if _has_texture_keyword(stem):
        failures.append("Invalid name: remove the texture keyword")

    suffix = next(
        (item for item in TEXTURE_SUFFIXES if stem.endswith(f"_{item}")),
        None,
    )
    if suffix is None:
        failures.append(
            "Invalid name: expected suffix _albedo, _normal, or _roughness"
        )
    elif not re.fullmatch(r"T_[a-z0-9]+(?:_[a-z0-9]+)*", stem[: -(len(suffix) + 1)]):
        failures.append("Invalid name: use lowercase letters, numbers, and underscores")

    if path.suffix.lower() in (".tga", ".png"):
        try:
            if path.suffix.lower() == ".tga":
                width, height = _read_tga_dimensions(path)
            else:
                width, height = _read_png_dimensions(path)
            if width > MAX_TEXTURE_SIZE or height > MAX_TEXTURE_SIZE:
                failures.append(
                    f"Resolution too high: {width}x{height}; maximum is "
                    f"{MAX_TEXTURE_SIZE}x{MAX_TEXTURE_SIZE}"
                )
            if width & (width - 1) or height & (height - 1):
                failures.append(f"Not Power of 2: {width}x{height}")
        except (OSError, ValueError) as error:
            failures.append(f"Cannot read texture resolution: {error}")

    return failures


def _validate_maya_name(path: Path) -> list[str]:
    failures: list[str] = []
    name = path.name
    stem = path.stem
    if path.suffix != ".ma":
        failures.append("Invalid format: expected .ma")
    if _has_whitespace(name):
        failures.append("Spaces in name; use underscores instead")
    if not stem.startswith("msh_"):
        failures.append("Invalid name: expected msh_ prefix")
    elif stem[4:] != stem[4:].lower():
        failures.append("Invalid name: use lowercase after the msh_ prefix")
    if not re.fullmatch(r"msh_[a-z0-9]+(?:_[a-z0-9]+)*", stem):
        failures.append("Invalid name: use lowercase letters, numbers, and underscores")
    if _has_texture_keyword(stem):
        failures.append("Invalid name: remove the texture keyword")
    return failures


def _raise_scan_error(error: OSError) -> None:
    raise error


def list_submission_files(root: Path) -> list[Path]:
    try:
        files: list[Path] = []
        for directory, _, filenames in os.walk(root, onerror=_raise_scan_error):
            files.extend(Path(directory) / filename for filename in filenames)
        files.sort(key=lambda path: str(path.relative_to(root)).lower())
        return files
    except OSError as error:
        raise OSError(f"Could not scan folder: {error}") from error


def file_type_for_path(path: Path) -> str:
    if _is_texture_candidate(path):
        return "Texture"
    if _is_maya_candidate(path):
        return "Maya"
    return "Other"


def _find_mayapy() -> str:
    configured = os.environ.get("MAYAPY")
    if configured and Path(configured).is_file():
        return configured

    on_path = shutil.which("mayapy")
    if on_path:
        return on_path

    if os.name == "nt":
        installations = sorted(
            Path(r"C:\Program Files\Autodesk").glob(r"Maya*\bin\mayapy.exe"),
            reverse=True,
        )
        if installations:
            return str(installations[0])
    return ""


def _run_maya_qc(mayapy: str, paths: list[Path]) -> dict[str, dict[str, object]]:
    worker = Path(__file__).with_name("maya_qc_worker.py")
    if not worker.is_file():
        reason = f"Maya QC worker not found: {worker}"
        return {os.path.normcase(str(path)): {"error": reason} for path in paths}

    manifest_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as manifest:
            json.dump([str(path) for path in paths], manifest)
            manifest_path = manifest.name

        completed = subprocess.run(
            [mayapy, str(worker), manifest_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=MAYA_QC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        reason = f"Maya QC timed out after {MAYA_QC_TIMEOUT_SECONDS} seconds"
        return {os.path.normcase(str(path)): {"error": reason} for path in paths}
    except OSError as error:
        reason = f"Could not run mayapy: {error}"
        return {os.path.normcase(str(path)): {"error": reason} for path in paths}
    finally:
        if manifest_path:
            try:
                os.unlink(manifest_path)
            except FileNotFoundError:
                pass

    result_line = next(
        (
            line[len(RESULT_MARKER) :]
            for line in reversed(completed.stdout.splitlines())
            if line.startswith(RESULT_MARKER)
        ),
        None,
    )
    if completed.returncode != 0 or result_line is None:
        diagnostic = (completed.stderr or completed.stdout).strip()
        if not diagnostic:
            diagnostic = f"mayapy exited with code {completed.returncode}"
        reason = f"Maya QC could not complete: {diagnostic[-1000:]}"
        return {os.path.normcase(str(path)): {"error": reason} for path in paths}

    try:
        worker_results = json.loads(result_line)
    except json.JSONDecodeError as error:
        reason = f"Maya QC returned invalid results: {error}"
        return {os.path.normcase(str(path)): {"error": reason} for path in paths}

    if not isinstance(worker_results, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or ("issues" in item and not isinstance(item["issues"], list))
        for item in worker_results
    ):
        reason = "Maya QC returned results in an unexpected format"
        return {os.path.normcase(str(path)): {"error": reason} for path in paths}

    return {
        os.path.normcase(str(item["path"])): item
        for item in worker_results
    }


def validate_folder(
    root: Path,
    mayapy: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> list[FileResult]:
    if progress_callback:
        progress_callback(0, 0, "Scanning files...")
    files = list_submission_files(root)
    if progress_callback:
        progress_callback(0, len(files), "Validating files...")

    maya_files = [path for path in files if _is_maya_candidate(path)]
    maya_results: dict[str, dict[str, object]] = {}
    if maya_files:
        if mayapy and Path(mayapy).is_file():
            if progress_callback:
                progress_callback(0, 0, "Running Maya QC...")
            maya_results = _run_maya_qc(mayapy, maya_files)
            if progress_callback:
                progress_callback(0, len(files), "Finishing validation...")
        else:
            reason = "mayapy not found or not configured, so Maya QC was not run"
            maya_results = {
                os.path.normcase(str(path)): {"error": reason} for path in maya_files
            }

    results: list[FileResult] = []
    for index, path in enumerate(files, start=1):
        relative_path = str(path.relative_to(root))
        if _is_texture_candidate(path):
            failures = _unique_reasons(_validate_texture(path))
            prefix_to_add = (
                "T_"
                if fixed_filename(path, "T_") != path.name
                or any(
                    reason.startswith(("Invalid name", "Spaces in name"))
                    for reason in failures
                )
                else None
            )
            results.append(
                FileResult(
                    relative_path,
                    "Texture",
                    "FAIL" if failures else "PASS",
                    "; ".join(failures) if failures else "Naming and resolution valid",
                    tuple(_error_name(reason) for reason in failures),
                    prefix_to_add,
                )
            )
        elif _is_maya_candidate(path):
            failures = _validate_maya_name(path)
            prefix_to_add = (
                "msh_" if fixed_filename(path, "msh_") != path.name else None
            )
            qc_result = maya_results.get(os.path.normcase(str(path)))
            if qc_result is None:
                failures.append("Maya QC returned no result for this file")
            elif qc_result.get("error"):
                failures.append(str(qc_result["error"]))
            else:
                failures.extend(str(issue) for issue in qc_result.get("issues", []))
            failures = _unique_reasons(failures)
            results.append(
                FileResult(
                    relative_path,
                    "Maya",
                    "FAIL" if failures else "PASS",
                    "; ".join(failures) if failures else "Naming and Maya QC passed",
                    tuple(_error_name(reason) for reason in failures),
                    prefix_to_add,
                )
            )
        else:
            results.append(
                FileResult(
                    relative_path,
                    "Other",
                    "FAIL",
                    "Unsupported file type; redundant file",
                    ("Unsupported file type",),
                )
            )
        if progress_callback:
            progress_callback(index, len(files), "Validating files...")
    return results
