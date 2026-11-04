import pathlib
src=pathlib.Path("shots.py").read_text(encoding="utf-8")
src=src.replace("import sys\nimport time", "import os\nimport sys\nimport time")
src=src.replace("browser = p.chromium.launch()", "browser = p.chromium.launch(executable_path=os.path.expandvars(r\"%LOCALAPPDATA%\\ms-playwright\\chromium-1243\\chrome-win64\\chrome.exe\"))")
pathlib.Path("_shots_run.py").write_text(src,encoding="utf-8")
print("patched")
