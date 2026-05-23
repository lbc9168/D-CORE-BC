// 构建前把根 enums_mapping.json 拷进 src/generated/enums.json，保证前端单一可信源。
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../enums_mapping.json");
const destDir = resolve(here, "../src/generated");
mkdirSync(destDir, { recursive: true });
copyFileSync(src, resolve(destDir, "enums.json"));
console.log("[copy-enums] enums.json 已同步");
