# Vue Dashboard Template

A Vue 3 admin dashboard template built with Vite, TypeScript, Tailwind CSS 4, and daisyUI 5. It includes 90 route views, reusable layout components, page-private components, modular Vue Router configuration, Pinia state management, local Heroicons, locally bundled chart elements, Oxfmt formatting, and Oxlint checks.

## Requirements

- Node.js 22 or newer
- pnpm 10 or newer

## Development

```bash
pnpm install
pnpm dev
```

The development server opens the dashboard automatically. The default URL is `http://localhost:5173/`.

## Quality and verification

```bash
pnpm format
pnpm check
pnpm build
pnpm preview
```

Production output is written to `dist/`.

## Source structure

```text
src/
├─ assets/styles/       Tailwind and daisyUI themes
├─ components/common/  Cross-page components and local icons
├─ config/              Sidebar navigation configuration
├─ layouts/             Dashboard shell, header, and sidebar
├─ router/routes/       Route records grouped by feature
├─ stores/              Pinia stores
└─ views/               Route views and page-private components
```

Complex route views use a page-local structure:

```text
views/index/
├─ Index.vue
└─ components/
   ├─ IndexStats.vue
   ├─ RevenueAndDemandCard.vue
   └─ OrderStatusCard.vue
```

Components used by only one route remain in that route's `components/` directory. Components shared by multiple routes belong in `src/components/`.
