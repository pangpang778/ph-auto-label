# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** PH Auto Label
**Generated:** 2026-07-17
**Category:** Developer Tool / Data Annotation
**Design Direction:** Apple Human Interface — clarity, deference, depth

---

## Global Rules

### Color Palette

| Role | Hex | CSS Variable |
|------|-----|--------------|
| Primary | `#007AFF` | `--color-primary` |
| On Primary | `#FFFFFF` | `--color-on-primary` |
| Secondary | `#5E5CE6` | `--color-secondary` |
| Accent/CTA | `#007AFF` | `--color-accent` |
| Background | `#F5F5F7` | `--color-background` |
| Foreground | `#1D1D1F` | `--color-foreground` |
| Muted | `#E8E8ED` | `--color-muted` |
| Border | `rgba(0,0,0,0.08)` | `--color-border` |
| Destructive | `#FF3B30` | `--color-destructive` |
| Ring | `#007AFF` | `--color-ring` |

**Color Notes:** Apple light mode — off-white background, vivid blue actions, dark grey text. Avoid saturated accents except system colors.

### Typography

- **Heading Font:** `-apple-system`, `BlinkMacSystemFont`, `SF Pro Display`, `Inter`, sans-serif
- **Body Font:** `-apple-system`, `BlinkMacSystemFont`, `SF Pro Text`, `Inter`, sans-serif
- **Monospace:** `SF Mono`, `SFMono-Regular`, `Menlo`, `Consolas`, monospace
- **Mood:** clean, precise, premium, accessible
- **Google Fonts fallback:** [Inter](https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap)

**CSS Import:**
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
```

### Spacing Variables

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `4px` / `0.25rem` | Tight gaps |
| `--space-sm` | `8px` / `0.5rem` | Inline spacing |
| `--space-md` | `12px` / `0.75rem` | Compact padding |
| `--space-lg` | `16px` / `1rem` | Standard padding |
| `--space-xl` | `24px` / `1.5rem` | Section padding |
| `--space-2xl` | `32px` / `2rem` | Large gaps |
| `--space-3xl` | `48px` / `3rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.04)` | Subtle lift |
| `--shadow-md` | `0 4px 16px rgba(0,0,0,0.06)` | Cards, panels |
| `--shadow-lg` | `0 12px 32px rgba(0,0,0,0.10)` | Modals, dropdowns |
| `--shadow-xl` | `0 24px 48px rgba(0,0,0,0.14)` | Overlays |

### Radius Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | `8px` | Small buttons, inputs |
| `--radius-md` | `12px` | Buttons, cards |
| `--radius-lg` | `18px` | Modals, large cards |
| `--radius-xl` | `24px` | Hero cards |
| `--radius-full` | `999px` | Pills, badges |

---

## Component Specs

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #007AFF;
  color: #FFFFFF;
  padding: 7px 14px;
  border-radius: 10px;
  font-weight: 500;
  font-size: 13px;
  transition: all 160ms cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  border: none;
}

.btn-primary:hover {
  background: #0051D5;
}

.btn-primary:active {
  transform: scale(0.98);
}

/* Secondary Button */
.btn-secondary {
  background: rgba(120,120,128,0.12);
  color: #1D1D1F;
  padding: 7px 14px;
  border-radius: 10px;
  font-weight: 500;
  font-size: 13px;
  transition: all 160ms cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  border: none;
}

.btn-secondary:hover {
  background: rgba(120,120,128,0.18);
}
```

### Cards / Panels

```css
.card {
  background: rgba(255,255,255,0.82);
  backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(0,0,0,0.06);
  border-radius: 18px;
  padding: 16px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.06);
}
```

### Inputs

```css
.input {
  padding: 6px 10px;
  border: 1px solid rgba(0,0,0,0.10);
  border-radius: 8px;
  font-size: 13px;
  background: rgba(120,120,128,0.10);
  color: #1D1D1F;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.input:focus {
  border-color: #007AFF;
  outline: none;
  box-shadow: 0 0 0 3px rgba(0,122,255,0.18);
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0,0,0,0.35);
  backdrop-filter: blur(4px);
}

.modal {
  background: rgba(255,255,255,0.92);
  backdrop-filter: blur(24px) saturate(200%);
  border-radius: 20px;
  padding: 24px;
  box-shadow: 0 24px 48px rgba(0,0,0,0.14);
  max-width: 520px;
  width: 90%;
  border: 1px solid rgba(0,0,0,0.06);
}
```

---

## Style Guidelines

**Style:** Apple Light / Vibrancy

**Keywords:** Light mode, frosted glass, large radii, soft shadows, system typography, clear hierarchy, generous whitespace, blue accent, minimal decoration

**Best For:** Productivity apps, creative tools, macOS-style web apps, premium SaaS

**Key Effects:**
- Backdrop blur (`backdrop-filter: blur(20px) saturate(180%)`) on floating surfaces
- Translucent white surfaces over soft grey background
- System font stack for native feel
- Smooth 150–200ms transitions with `cubic-bezier(0.4, 0, 0.2, 1)`
- No heavy gradients; rely on shadow and depth

### Page Pattern

**Pattern Name:** Productivity Workspace

- **Layout:** Fixed toolbar, collapsible side panels, large central canvas.
- **Visual Hierarchy:** Canvas first, controls defer to content.
- **CTA Placement:** Primary action in top toolbar; secondary actions in overflow menu.
- **Sections:** 1. Toolbar, 2. Context / workflow strip, 3. Sidebar (assets), 4. Canvas, 5. Inspector sidebar.

---

## Anti-Patterns (Do NOT Use)

- ❌ Dark mode by default
- ❌ Heavy gradients or neon glows
- ❌ Excessive decoration (borders, shadows on every element)
- ❌ Large drop shadows on small items
- ❌ Emoji as icons — use SVG / Font Awesome consistently
- ❌ Missing cursor:pointer on clickable elements
- ❌ Instant state changes — always transition 150–200ms
- ❌ Low contrast text — maintain 4.5:1 minimum
- ❌ Invisible focus states — visible keyboard focus ring required
- ❌ Layout-shifting hover effects

### Additional Forbidden Patterns

- ❌ **Generic template look** — avoid Tailwind/shadcn defaults passed off as finished
- ❌ **Uniform spacing everywhere** — use deliberate rhythm
- ❌ **Decorative animation** — motion must convey meaning
- ❌ **Animating width/height/top/left** — use transform/opacity only

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG/Font Awesome consistently)
- [ ] All icons from a single consistent icon set
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150–200ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile
- [ ] Backdrop blur has solid fallback for unsupported browsers
