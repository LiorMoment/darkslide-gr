// The desktop app's configuration. The version is the program's own
// (darkslide/__init__.py VERSION), so a new release needs no edit here;
// the build number is $DARKSLIDE_BUILD (CI's run number) or else the commit count.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const init = readFileSync(new URL("../darkslide/__init__.py", import.meta.url), "utf8");
const match = /^VERSION\s*=\s*"([^"]+)"/m.exec(init);
if (!match) throw new Error("VERSION not found in darkslide/__init__.py");

function buildNumber() {
  if (process.env.DARKSLIDE_BUILD) return process.env.DARKSLIDE_BUILD;
  try {
    return execFileSync("git", ["rev-list", "--count", "HEAD"], { encoding: "utf8" }).trim();
  } catch {
    return "0";
  }
}

export default {
  name: "Darkslide GR",
  identifier: "io.github.liormoment.darkslide-gr",
  version: match[1],
  copyright: "Darkslide GR contributors",
  // Everything the server reads at run time: scripts/check_resources.py fails the build
  // when the program starts reading a folder of the repository not listed here.
  resources: [
    "../launcher.py",
    "../darkslide",
    "../web",
    "../templates",
    "../film_curves",
    "../native", // photo_recipe_native.py reads its C source

    "../LICENSE",
    "../THIRD_PARTY_NOTICES.md",
    "DESKTOP_LICENSES.md", // the shell's MIT licence and those of what is built into it
  ],
  macos: {
    infoPlist: { CFBundleVersion: buildNumber() },
  },
};
