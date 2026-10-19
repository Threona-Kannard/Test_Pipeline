# Submission Folder Validator

A standalone Tkinter application for checking texture and Maya submission files.
It uses only the Python standard library for the UI and TGA checks. Maya scene
checks run in a separate `mayapy` process; Maya does not need to be open.

## Run

```text
python submission_validator.py
```

The application is split into focused modules: `validator_ui.py` contains the
Tkinter interface, `validation_logic.py` handles file scanning and validation,
and `maya_qc_worker.py` runs scene checks inside Maya standalone.

Select the submission folder and validate. Click a row's **View** button to
open its full validation log in a scrollable dialog. Set or browse to the `mayapy`
executable if it is not found automatically (the `MAYAPY` environment variable
and `PATH` are also checked). If Maya QC is unavailable, each Maya file is
marked as failed with an explanation; the app does not report unchecked scenes
as passed.

Validation and filename fixes run in background threads so the interface stays
responsive. The progress bar indicates scanning, Maya QC, per-file validation,
and rename progress.

After validation, use **Summary** in the footer to view the unique error names
found in the selected folder, along with a **Fixed files** section listing
renamed files. The footer shows the number of files fixed for the selected folder.
Files with a correctable naming issue offer a **Fix Name** action. It lowercases
the filename, replaces characters other than letters, numbers, and underscores
with underscores, removes `texture`/`textures` from the name, applies the required
prefix (preserving the uppercase `T_` texture prefix), reloads the file list, and
reruns validation.
For texture files, the rename dialog requires entering a texture type suffix:
`albedo`, `normal`, or `roughness`. **Fix All** asks for a suffix for each
texture before renaming any files, and does not delete unsupported files.

The scan includes files in subfolders. Unsupported file types fail validation
and can be deleted from their row after confirmation.

## Rules checked

- Textures must be named `T_<asset>_<suffix>.tga` or
  `T_<asset>_<suffix>.png`, with a lowercase asset name
  and one of `_albedo`, `_normal`, or `_roughness`. The required `T_` prefix is
  uppercase; the rest of the name and extension must be lowercase. Names may
  use lowercase letters, numbers, and underscores, and must not contain
  `texture` or `textures`.
- TGA and PNG dimensions must be powers of two and no larger than 2048x2048.
  Dimensions are read from the image headers; no image package is required.
- Maya files must be named `msh_<asset>.ma`, using lowercase letters, numbers,
  and underscores after the required prefix.
- Each Maya scene is opened in Maya standalone with script-node execution
  disabled. Meshes are checked for construction history, non-default
  translate/rotate/scale values, faces with five or more sides, and anything
  other than exactly one UV set.

Run the standard-library tests with:

```text
python -m unittest -v
```
