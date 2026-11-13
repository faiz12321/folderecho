# FolderEcho

Compare two folders, or find files that are exactly the same. Nothing is ever deleted: extra copies move to a holding folder, and Undo puts them back.

Free and open source (MIT). Windows first, Linux works too. Early prototype: read "Limits" before using it on files you care about, and keep your own backups.

## What you can do

- **Compare two folders**: what is only in A, only in B, same in both, same name but different content, and same content in a different place. This only reads files.
- **Find duplicates**: files with identical content, even when the names differ. You see a preview, tick what to move, and confirm.
- **Master folder** (optional): pick one folder that is never touched. Only copies outside it are moved.
- **Undo**: puts moved files back, without overwriting anything.

## Download and run (Windows)

1. Download `FolderEcho-windows.zip` and unzip it into an ordinary folder (not OneDrive).
2. Double-click `FolderEcho.exe`. A black window opens and your browser shows the app. Keep the black window open while you use it. Close it to stop.
3. Windows may warn "Windows protected your PC" because the program is not code-signed (signing costs money). Choose "More info", then "Run anyway", only if you downloaded it from the place you trust. Compare the SHA-256 in `FolderEcho.exe.sha256.txt` with the file if you want to check it.

The page is served only on your own computer (127.0.0.1) and protected by a random key in the link. Nothing is uploaded and there is no network access to the internet.

## Run from source (Windows or Linux)

You need Python 3.10 or newer (tested on 3.10 and 3.12). There is nothing to install.

```
python run.py            # opens the window in your browser
python run.py compare "C:\data\A" "C:\data\B"
python run.py dupes "C:\data\A" "C:\data\B" --master "C:\data\B"
python run.py move "C:\data\A" "C:\data\B" --master "C:\data\B"        # preview only
python run.py move "C:\data\A" "C:\data\B" --master "C:\data\B" --yes  # really move
python run.py undo "C:\data\FolderEcho holding\run-20261016-221500"
```

## How it stays safe

- Files match only if their size is equal and the full SHA-256 of their content is equal. Names and dates are never used to decide. Both files are read again right before a move.
- Nothing is deleted. Duplicates are renamed into `FolderEcho holding\run-<time>` on the same drive. Free disk space only returns when you delete that folder yourself.
- A journal is saved before the first move. Undo restores from it and refuses to overwrite anything, to restore a file that was edited while it was held, or to follow a link.
- Refused: whole drives, system and program folders, your user folder itself, AppData, links and junctions (as the chosen folder or any parent), nested chosen folders, and folders that look cloud-synced. The holding folder follows the same rules: it cannot be inside a folder being cleaned, go through a link, or sit inside a cloud-synced folder (files moved there would be uploaded).
- Skipped and listed: shortcuts and links, hidden and system files, files with several names (hardlinks), pipes and devices, unreadable files, and empty files. If two files in one folder have names that look the same to the drive (letter case or Unicode form), both are left out and the clash is reported, never silently treated as one file.
- The master folder is never a source of moves.

## External drives and other file systems

What the program does by design:

- Compare works across drives and file systems (for example an internal disk against a USB drive).
- Moving duplicates only ever uses a rename on the same drive. It never copies a file to another drive and never copies-then-deletes. With "Automatic" holding folders, each chosen folder gets its own `FolderEcho holding` folder next to it, so files on a USB drive stay on that drive. If you pick one holding folder yourself and some files are on a different drive, the move is refused.
- A whole drive (for example `E:\`) cannot be chosen. Choose a folder on it.
- If a drive is write-protected or read-only, or a folder cannot be written to, the files are not moved and the reason is shown.
- If a drive disappears while moving, each file that did not move is reported and left where it was. Files that already moved are in the holding folder, and Undo can put them back.
- File names are compared ignoring letter case on Windows and on file systems that report themselves as case-insensitive (checked with a read-only test). Matching of content never depends on dates, so the coarse 2-second times of FAT and rounded times from copy tools do not cause wrong matches.

What was tested: simulations only. In automated tests: two real different file systems on Linux (disk and RAM disk), forced "cross-device" errors, a drive that fails halfway, a file that cannot be read, a vanished folder, read-only folders, identical rounded times and a case-insensitive file system. Windows tests cover long paths and junctions.

What was NOT tested: real USB drives, SD cards, exFAT, FAT32 or NTFS external disks, network shares or NAS (including `\\server\share` paths), phones connected as cameras (these are not normal folders and will not work), BitLocker or encrypted drives, drives that go to sleep, or very slow drives. Treat external drives as experimental and try a small test folder first.

Also know:

- Undo needs the drive to appear with the same letter or mount path as when you moved the files. If the drive letter changes, nothing is lost: the files are in the `FolderEcho holding` folder on the drive and you can copy them back by hand. Undo will refuse rather than guess.
- Reading every candidate file in full over USB 2 or a network is slow.
- Unplug a drive only after Windows says it is safe, as usual. Do not unplug while a move is running.

## Limits (please read)

- This is a prototype. It was tested on Windows Server and Ubuntu with generated files in automated tests. It has not been tested on real photo libraries, very large drives (millions of files), network shares, or real OneDrive/Dropbox accounts.
- The cloud-sync check looks at folder names and the OneDrive setting. It can miss unusual setups. Use ordinary local folders.
- Do not edit, sync or back up the folders while it runs. A file that changes between preview and move is left alone, but there is no protection against someone racing the program on purpose.
- Moving many files is not one all-or-nothing step. If it is interrupted, Undo still works from the journal.
- Reading every candidate file in full is slow on big folders.
- Preview, error text and the journal contain your file paths. They stay on your computer, but do not share them if the names are private.
- Not a backup tool. Keep real backups.

## Tests

```
python -m pip install websocket-client   # only needed for the browser test
python -m unittest discover -s tests -v
```

Tests run on Windows and Ubuntu through GitHub Actions: engine, safety refusals, undo, local server and a real browser run of the window.

## License

MIT, see `LICENSE`. The program itself uses only the Python standard library. The Windows `.exe` is built with PyInstaller and bundles the Python runtime, see `THIRD-PARTY.md`.

