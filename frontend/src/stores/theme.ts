import { computed, ref } from "vue";
import { defineStore } from "pinia";

export const themeNames = [
  "light",
  "dark",
  "cupcake",
  "bumblebee",
  "emerald",
  "corporate",
  "synthwave",
  "retro",
  "cyberpunk",
  "valentine",
  "halloween",
  "garden",
  "forest",
  "aqua",
  "lofi",
  "pastel",
  "fantasy",
  "wireframe",
  "black",
  "luxury",
  "dracula",
  "cmyk",
  "autumn",
  "business",
  "acid",
  "lemonade",
  "night",
  "coffee",
  "winter",
  "dim",
  "nord",
  "sunset",
  "caramellatte",
  "abyss",
  "silk",
] as const;

export type ThemeName = (typeof themeNames)[number];

export const themeOptions = themeNames.map((name) => ({
  name,
  label:
    name === "cmyk"
      ? "CMYK"
      : `${name.charAt(0).toUpperCase()}${name.slice(1)}`,
}));

const storageKey = "dashboard:theme";

function isThemeName(value: string | null): value is ThemeName {
  return value !== null && themeNames.includes(value as ThemeName);
}

export const useThemeStore = defineStore("theme", () => {
  const current = ref<ThemeName>("dark");
  const isLight = computed(() => current.value === "light");

  function setTheme(theme: ThemeName) {
    current.value = theme;
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(storageKey, theme);
  }

  function previewTheme(theme: ThemeName) {
    document.documentElement.dataset.theme = theme;
  }

  function restoreTheme() {
    document.documentElement.dataset.theme = current.value;
  }

  function toggle() {
    setTheme(isLight.value ? "dark" : "light");
  }

  function initialize() {
    const storedTheme = localStorage.getItem(storageKey);
    setTheme(isThemeName(storedTheme) ? storedTheme : "dark");
  }

  return {
    current,
    initialize,
    isLight,
    previewTheme,
    restoreTheme,
    setTheme,
    toggle,
  };
});
