# Vue Dashboard Template

## Project Structure & Module Organization

This repository is a Vue 3 dashboard template built with Vite, TypeScript, Tailwind CSS, and daisyUI. Route views live in `src/views/`, page-private components live beside their view in a `components/` directory, shared components live in `src/components/`, layouts live in `src/layouts/`, and feature-grouped route records live in `src/router/routes/`. Production output is written to `dist/`.

## Build, Test, and Development Commands

- `pnpm dev`: starts the Vite development server.
- `pnpm format`: formats supported project files with Oxfmt, including Tailwind class sorting.
- `pnpm check`: checks formatting, runs Oxlint, and type-checks Vue SFCs with `vue-tsc`.
- `pnpm build`: type-checks and produces the production build in `dist/`.

Run `pnpm install` after changing dependencies. There is no dedicated test command yet; use `pnpm check` and `pnpm build` as the primary automated verification.

## Coding Style & Naming Conventions

Use Vue Single-File Components with TypeScript. Name reusable components in PascalCase and keep route-specific components in the owning view's `components/` directory. Oxfmt is the source of truth for formatting and Tailwind class ordering; run `pnpm format` after editing supported files. Keep `vue-tsc` in the validation path because Oxlint does not fully lint Vue templates.

## HTML & CSS

HTML must be based on daisyUI syntax and conventions. Use daisyUI Blueprint MCP server (if available) or daisyUI Skill to look up component syntax and conventions every time you're writing HTML.

Do not use Tailwind CSS utility classes if a daisyUI component already provides the desired styling. If a daisyUI does not have a specific component, you can use Tailwind CSS utility classes. For customizing daisyUI components, if customization is required, use the daisyUI modifier class names (get from Blueprint MCP or daisyUI Skill). If the customization is not available as a modifier class, use Tailwind CSS utility classes.

You are allowed to use Tailwind CSS grid, flex, and spacing utility classes to position and align elements and blocks.

Do not write any custom CSS unless it's absolutely necessary and cannot be achieved with daisyUI or Tailwind CSS utility classes.

## Testing Guidelines

No automated test framework or coverage threshold is currently configured. For changes, run `pnpm check` and `pnpm build`, then exercise the affected route through the development server. When editing shared layouts, check representative routes from the dashboard, products, users, and settings areas at desktop and narrow viewport widths.

## Security & Configuration Tips

Do not commit secrets or environment-specific values. Keep dependency changes minimal and update both `package.json` and `pnpm-lock.yaml` together.
