"""Builds Demos/index.html: one self-contained file with three.js inlined.

Edit src/index.src.html (it loads ../vendor/ through an import map and needs an
http server), then run:  python build.py
The output works offline and when double-clicked (file://).
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
read = lambda *p: open(os.path.join(HERE, *p), encoding="utf-8").read()

src = read("src", "index.src.html")
three = read("vendor", "three.module.js")
room = read("vendor", "addons", "environments", "RoomEnvironment.js")

# three.module.js -> body of a function returning the namespace
m = re.search(r"^export \{([^}]*)\};\s*$", three, re.M)
names = [n.strip() for n in m.group(1).split(",") if n.strip()]
assert all(" as " not in n for n in names)
three_body = three[: m.start()]

# RoomEnvironment.js -> drop its import/export (it uses three's names in the same scope)
room = re.sub(r"^import \{[^}]*\} from 'three';\s*$", "", room, flags=re.M)
room = re.sub(r"^export \{ RoomEnvironment \};\s*$", "", room, flags=re.M)

bundle = (
    "const THREE = (() => {\n" + three_body + "\n" + room +
    "\nreturn Object.freeze({ " + ", ".join(names) + ", RoomEnvironment });\n})();\n"
    "const { RoomEnvironment } = THREE;\n"
)

out = re.sub(r'<script type="importmap">.*?</script>\s*', "", src, count=1, flags=re.S)
for line in ("import * as THREE from 'three';\n",
             "import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';\n"):
    assert line in out, line
    out = out.replace(line, "", 1)
marker = '<script type="module">\n'
assert marker in out
out = out.replace(marker, marker + "/* three.js r160 (MIT) inlined by build.py */\n" + bundle, 1)

open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(out)
print("wrote index.html", len(out) // 1024, "KB")
