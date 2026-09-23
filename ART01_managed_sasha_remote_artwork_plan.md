# Managed Sasha: remote client artwork plan

## Decisions

- The caller supplies client artwork URLs with the message, as in the Claude CLI.
- This POC accepts direct artwork URLs from any HTTP or HTTPS host. AWS or CloudFront host restrictions are deferred until those hosts are known.
- Accept the same file extensions as the Claude POC: PNG, JPG, JPEG, GIF, WEBP, SVG, PDF, AI, and EPS.
- The original remote URL remains the source. Downloaded files are available for this turn only and are removed after the run, including failed runs. The caller supplies a URL again if a later turn needs the artwork.
- Sasha receives local file handles and paths, not the remote URLs or signed URL query strings.
- The managed runner continues to return a draft message; it does not send it.

## Starting point

- `run_cli.py` accepts repeatable `--file URL`; `attachments.py` downloads files and assigns `file_1`, `file_2`, and so on.
- Claude's `attach_file` tool maps a handle to a local file and sets the page's file input.
- The managed CLI and `SashaTask` currently carry no files. The managed runner sends only text to Astra.
- The managed Design Tool guidance already names the artwork input (`upload-file-input`) and requires visual inspection after upload.

## Intended flow

```text
Deal ID + client message + direct artwork URL(s)
    -> runner downloads allowed files before opening an OpenAI session
    -> files appear inside the disposable container as /workspace/client-files/file_N.ext
    -> Astra receives only file handles, names, sizes, and local paths
    -> Astra uses Playwright to upload to the Design Tool
    -> Astra inspects the artwork, saves the proof or revision, and reopens it
    -> runner returns a draft and removes the downloaded files
```

## Implementation tasks

- [x] **1. Add the input contract.** Add repeatable `--file URL` to `scripts/run_openai_managed_sasha.py` and carry those URLs in the managed task. Keep a run with no files unchanged. Test CLI parsing and task construction.
- [x] **2. Reuse the downloader.** Give `attachments.py` a destination-directory entry point while preserving the Claude CLI's current per-deal destination. Retain its extension, nonempty-file, 20 MB, and timeout checks. Accept HTTP and HTTPS direct file URLs and reject HTML pages. Do not log full URLs or signed query strings. Test valid files and download failures without network calls.
- [x] **3. Stage files before the model session.** Download into the managed run workspace under `client-files/`, which the existing Docker mount exposes at `/workspace/client-files/`. Build the task message after downloads succeed and list `file_1`, etc., with their local paths. Keep remote URLs out of `TASK.md`, `task.json`, model input, and run logs. A failed download returns a clear failed result without creating an OpenAI session.
- [x] **4. Make the upload route explicit.** In the Sasha skill or task guidance, tell Astra to use the supplied local file with Playwright's file-input method on `upload-file-input`. Keep product, placement, save, and proof verification decisions with Astra. Do not add a custom `attach_file` browser tool.
- [x] **5. Remove temporary artwork.** Delete `client-files/` in the runner's cleanup path after success, failure, or timeout. Retain existing screenshots and run evidence. Do not put the downloaded bytes or expiring URL into the per-deal conversation JSON.
- [x] **6. Verify locally and on QA.** Test no-file runs, multiple files, rejected URL/type/size, no OpenAI session on download failure, no URL leakage, and cleanup. Then use an authorized QA deal and direct artwork URL to verify upload, canvas appearance, saved proof or revision, and the reopened proof. Confirm no client message is sent.

All 101 local tests passed. The first QA run on deal 303906 received the SVG, but Sasha could not find the selected AS Colour Relax Hood 5161. A second run selected the Hanes EcoSmart Hoodie in Yellow, but the Design Tool rejected the SVG gradient and the art-team form rejected the SVG format. A third run used Wikimedia's direct PNG rendering of the same full logo. Sasha uploaded it, placed it on the back, saved proof 576524 with "Send a Copy to Client" set to No, and reopened the proof. The saved back-view screenshot shows the complete logo on the Yellow Hanes hoodie. The runner removed its temporary `client-files/` directory. The SVG limitation remains a separate product-format issue; end-to-end proof creation is verified for the PNG.

## Inputs needed before the QA verification

- QA deal `303906`; client instruction: use the selected garment and put the full Kotlin logo on the back.
- The original SVG file linked from the supplied Wikimedia Commons page, used as the direct file URL for the QA run.
