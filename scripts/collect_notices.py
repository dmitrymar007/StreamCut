"""Retain installed runtime distribution licenses in the binary release."""
import importlib.metadata
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
destination = ROOT/"vendor"/"notices"
destination.mkdir(parents=True, exist_ok=True)
inventory = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name", "unknown")
    folder = destination/name
    notices = []
    for file in distribution.files or []:
        filename = Path(str(file)).name.lower()
        if ("license" in filename or filename.startswith("copying") or filename.startswith("notice")) and not filename.endswith((".py", ".pyc")):
            source = Path(distribution.locate_file(file))
            if source.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                output = folder/str(file).replace("/", "__").replace("\\", "__").replace(":", "_")
                shutil.copy2(source, output)
                notices.append(output.name)
    inventory.append({"name": name, "version": distribution.version, "license": distribution.metadata.get("License-Expression") or
                      distribution.metadata.get("License", "See upstream"), "urls": distribution.metadata.get_all("Project-URL", []),
                      "notice_files": notices})
(destination/"inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
for source in (ROOT/"vendor").glob("*LICENSE*"):
    shutil.copy2(source, destination/source.name)
print(f"Third-party inventory: {len(inventory)} distributions -> {destination}")
