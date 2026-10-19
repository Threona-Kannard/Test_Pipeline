# Submission Folder Checker

Use this tool to check a submission folder before handing it off. It checks
texture files and Maya scenes, then shows which files passed and what needs
attention.

## Before you start

The checker scans the folder you choose, including all of its subfolders.
**Fix Name**, **Fix All**, and deleting an unsupported file make changes to that
folder. If you need to keep an untouched copy, make a copy of the submission
folder first and check the copy.

## Check a submission

1. Start the checker. If your technical artist provided an app shortcut, use
   that. Otherwise, open a terminal in the tool folder and run:

   ```text
   python submission_validator.py
   ```

2. Click **Browse...** and select the submission folder you want to check.
   The file list shows files in that folder and its subfolders.
3. Click **Validate** and wait for the progress bar to finish. Maya scenes may
   take a few minutes to check.
4. Read the **Status** and **Details** columns:
   - **PASS** means the file passed its checks.
   - **FAIL** means there is an issue. Read **Details** for the reason.
   - Click **View** on a row to read its full validation log.
5. Fix any failed items in your art application, then click **Validate** again.
   A new validation run checks the current contents of the folder.
6. Click **Summary** for a grouped list of issue types and a record of any
   filenames changed by the checker.

## Fix filenames

When the checker can suggest a filename correction, that row has a **Fix Name**
action. Click it to preview or confirm the new name. For a texture, enter its
type: `albedo`, `normal`, or `roughness`.

**Fix All** applies the suggested naming cleanup to all eligible files. It asks
you to choose a type for each texture before it renames anything. The checker
reloads the folder and runs validation again after renaming.

Filename fixes lowercase the name, replace unsupported characters with
underscores, remove `texture` or `textures` from the filename, and apply the
required prefix. They do not fix image dimensions or problems inside a Maya
scene. Review the suggested filename and keep a backup if you are unsure.

Files with unsupported types are marked as failed. They are not removed by
**Fix All**. A delete action is available for an unsupported file; use it only
if you are sure the file is not needed. Deletion cannot be undone by the checker.

## What the checker expects

### Textures

- Use `.tga` or `.png` files.
- Name each texture `T_<asset>_<type>.tga` or `T_<asset>_<type>.png`.
- The prefix must be uppercase `T_`; use lowercase letters, numbers, and
  underscores for the rest of the name and the file extension.
- `<type>` must be `albedo`, `normal`, or `roughness`. For example:
  `T_robot_body_albedo.png`.
- Image width and height must each be a power of two and no larger than
  2048 pixels. For example, `1024 x 1024` is valid; `3000 x 1024` is not.
- Do not include spaces or the word `texture`/`textures` in the filename.

### Maya scenes

- Use Maya ASCII `.ma` files named `msh_<asset>.ma`, for example
  `msh_robot_body.ma`.
- Use lowercase letters, numbers, and underscores after `msh_`.
- The scene must pass the Maya checks: meshes must have no construction
  history, transforms must be frozen, faces must have fewer than five sides,
  and each mesh must have exactly one UV set.

Maya scenes are checked in a separate Maya process; Maya does not need to be
open. If a Maya file fails because Maya QC could not run, or the checker cannot
find Maya, contact your technical artist rather than treating that scene as
passed. If needed, they can set the Maya executable in **Settings**.
