import { defineConfig } from "astro/config";

const isPages = process.env.GITHUB_PAGES === "true";

export default defineConfig({
  output: "static",
  site: "https://d3banjan.github.io",
  base: isPages ? "/kobiyal/" : "/",
});
