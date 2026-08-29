import { existsSync, lstatSync, readdirSync, rmdirSync, unlinkSync } from "node:fs";
import { join } from "node:path";

const target = "dist";

function removeTree(path) {
  if (!existsSync(path)) return;
  const stat = lstatSync(path);
  if (!stat.isDirectory()) {
    unlinkSync(path);
    return;
  }
  for (const entry of readdirSync(path)) {
    removeTree(join(path, entry));
  }
  rmdirSync(path);
}

removeTree(target);
