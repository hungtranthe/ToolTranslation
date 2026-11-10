---
name: Fluent Monolith Translation Studio
colors:
  surface: '#131316'
  surface-dim: '#131316'
  surface-bright: '#39393c'
  surface-container-lowest: '#0e0e11'
  surface-container-low: '#1b1b1e'
  surface-container: '#1f1f22'
  surface-container-high: '#2a2a2d'
  surface-container-highest: '#353438'
  on-surface: '#e4e1e6'
  on-surface-variant: '#c2c6d8'
  inverse-surface: '#e4e1e6'
  inverse-on-surface: '#303033'
  outline: '#8c90a1'
  outline-variant: '#424656'
  surface-tint: '#b3c5ff'
  primary: '#b3c5ff'
  on-primary: '#002b75'
  primary-container: '#0066ff'
  on-primary-container: '#f8f7ff'
  inverse-primary: '#0054d6'
  secondary: '#4edea3'
  on-secondary: '#003824'
  secondary-container: '#00a572'
  on-secondary-container: '#00311f'
  tertiary: '#ffb59d'
  on-tertiary: '#5d1900'
  tertiary-container: '#cc4204'
  on-tertiary-container: '#fff6f4'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#dae1ff'
  primary-fixed-dim: '#b3c5ff'
  on-primary-fixed: '#001849'
  on-primary-fixed-variant: '#003fa4'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffdbd0'
  tertiary-fixed-dim: '#ffb59d'
  on-tertiary-fixed: '#390c00'
  on-tertiary-fixed-variant: '#832600'
  background: '#131316'
  on-background: '#e4e1e6'
  surface-variant: '#353438'
typography:
  headline-lg:
    fontFamily: Geist
    fontSize: 1.5rem
    fontWeight: '600'
    lineHeight: 2rem
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Geist
    fontSize: 1.25rem
    fontWeight: '600'
    lineHeight: 1.75rem
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Geist
    fontSize: 1rem
    fontWeight: '600'
    lineHeight: 1.5rem
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 0.9375rem
    fontWeight: '400'
    lineHeight: 1.6rem
  body-md:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.375rem
  body-sm:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1.125rem
  label-md:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: '500'
    lineHeight: 1.25rem
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Inter
    fontSize: 0.6875rem
    fontWeight: '500'
    lineHeight: 1rem
    letterSpacing: 0.02em
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1.125rem
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.5rem
  gutter-desktop: 0.75rem
  margin: 0.5rem
  margin-desktop: 0.75rem
  space-xs: 0.125rem
  space-sm: 0.25rem
  space-md: 0.5rem
  space-lg: 0.75rem
  space-xl: 1rem
  space-2xl: 1.5rem
---

## Brand & Style

The design system is engineered specifically for high-efficiency, power-user desktop productivity environments within PySide6 and native Windows 11 shell environments. The aesthetic sits at the intersection of Windows Fluent Design System and professional developer tooling (Visual Studio Code, JetBrains IDEs).

### Brand Personality & Core Attributes
- **Systematic & Predictable:** Every element adheres to strict desktop conventions (docking panels, status bars, structured splitters). Zero unexpected shifts or whimsical decorations.
- **Information-Dense:** Prioritizes vertical and horizontal space economy. Interfaces display large corpuses of text, parallel bilingual views, glossary term tables, and translation memory streams without unnecessary padding.
- **Focused & Low-Fatigue:** Deep neutral grays reduce eye strain during multi-hour translation and proofreading sprints. High-frequency accent cues prevent cognitive overload.
- **Precise & Crisp:** Exact 1px micro-borders, minimal corner radii, and high-legibility geometric sans-serif type ensure dense data remains scannable.

### Target Audience
Professional translators, localization engineers, novel web-scrapers, and digital editors translating large volumes of Chinese (Simplified and Traditional) text into Vietnamese. These users rely heavily on rapid keyboard navigation, structured terminology replacement databases, regex batch tools, and real-time inference logging.

## Colors

The color palette centers around a focused, dark desktop paradigm inspired by Visual Studio Code and Windows 11 Fluent dark surfaces. It combines deep slate-charcoal neutrals with an electric cobalt primary accent and utility indicators tailored for translation tasks.

### Color Tokens & Semantic Roles
- **Primary (`#0066FF`):** Reliable cobalt blue. Used for active navigation rail items, focused input outlines, primary action buttons, selected table rows, active tabs, and critical CTA highlights.
- **Secondary (`#10B981`):** Precision emerald green. Denotes verified dictionary matches, 100% translation memory parity, successful export states, and active worker thread progress bars.
- **Tertiary / Warning (`#F59E0B`):** Warm amber. Flags fuzzy glossary matches, uncommitted machine translation buffers, and pending review lines.
- **Critical / Error (`#EF4444`):** Pure crimson. Applied to parsing syntax errors, regex mismatches, dropped network connections, and missing terminology keys.
- **Surface Hierarchy:**
  - `surface-canvas` (`#0D0E11`): Base window framing, title bar, and background shell.
  - `surface-panel` (`#14161B`): Secondary activity sidebar, explorer panes, and docked terminal trays.
  - `surface-editor` (`#1A1D24`): Main bilingual workspace, translation grids, and document body.
  - `surface-overlay` (`#222630`): Modal dialogs, context menus, command palettes, and floating tooltips.
  - `border-subtle` (`#2B303C`): 1px structural dividing lines between resizable docked panels and table cells.
  - `border-muted` (`#3B4252`): Interactive element boundaries and unselected field borders.
- **Text & Foreground Hierarchy:**
  - `text-primary` (`#F3F4F6`): Source Chinese text, translated Vietnamese copy, and core labels.
  - `text-secondary` (`#9CA3AF`): Metadata, line numbers, word counts, and hotkey indicators.
  - `text-disabled` (`#4B5563`): Inactive features, disabled commands, and structural icons.

## Typography

The type system prioritizes high-density vertical rhythm and cross-lingual legibility across three language scripts: Latin-based Vietnamese (with complex stacked diacritics), Chinese ideograms (CJK Unified Ideographs), and English developer/UI terminology.

### Type Roles & Pairing Strategy
- **Headlines (`Geist`):** Delivers clean, geometric, high-precision titles for dock titles, project headers, and modal bars. Its tight metrics align well with technical desktop shells.
- **Body & Labels (`Inter`):** Handles all Vietnamese prose, English UI controls, and terminology pairs. Inter's tall x-height and clear distinction between glyphs prevent visual crowding when displaying multi-tone Vietnamese diacritics (e.g., `ệ`, `ở`, `ữ`).
- **Data, Logs & Terminology Keys (`JetBrains Mono`):** Dedicated to regex patterns, raw JSON configurations, pipeline terminal output, Chinese Hanzi pinyin transliterations, and segment index IDs.

### Vietnamese & Chinese Considerations
- Font sizes below `0.75rem` (`12px`) are avoided for Vietnamese prose to prevent diacritical mark clipping.
- Default body line height is set to `1.6` for Vietnamese reading viewports to allow comfortable breathing room between vertical tone markers and ascenders/descenders.

## Layout & Spacing

This design system uses a desktop-first panel docking model rather than a standard web columnar grid. The workspace is defined by multi-pane layouts: collapsible activity bars, tree navigation sidebars, dual-pane vertical splitters for source/target text, and a bottom drawer for log consoles and terminology lookups.

### Layout Model
- **Master Dock Window:** Outer margins are fixed to `0.5rem` (`8px`) or `0.75rem` (`12px`) maximizing usable desktop canvas on 1080p and 4K displays.
- **Splitters & Panels:** Panels are separated by 1px visible hairline gutters (`border-subtle`). When hovering over gutters, an invisible `4px` grab zone expands to allow fluid panel resizing.
- **Data Density Rhythm:**
  - `space-xs` (2px): Micro-spacing between icon badges and nested tree leaf indentations.
  - `space-sm` (4px): Gap between toolbar tool buttons, chip tags, and compact table cell vertical padding.
  - `space-md` (8px): Standard padding within inputs, control group gutters, and segment divider rows.
  - `space-lg` (12px): Modal interior padding and panel container header gaps.
  - `space-xl` (16px): Document margin separators and detached settings view sections.

## Elevation & Depth

Visual hierarchy is maintained primarily through structured surface luminance and low-contrast hairline borders, adhering to modern desktop UI patterns rather than deep web drop-shadows.

### Structural Tiers
1. **Tier 0 (Deepest Background - `surface-canvas` #0D0E11):** Application frame, status bar footer, and title bar drag areas.
2. **Tier 1 (Middle Ground - `surface-panel` #14161B):** File tree, term-base index list, and terminal logs. Separated from Tier 0 by `1px solid #2B303C`.
3. **Tier 2 (Primary Focus - `surface-editor` #1A1D24):** Translation text editor cards, data table rows, and active translation segments.
4. **Tier 3 (Elevated Overlays - `surface-overlay` #222630):** Dropdown select menus, auto-complete glossaries, and context menus. Uses a subtle ambient shadow: `0 4px 16px rgba(0, 0, 0, 0.45)` paired with a `1px solid #3B4252` border to clearly separate floating layers from active background text.

## Shapes

The design system implements a refined, low-curvature geometric profile (`roundedness: 1`). This provides the crisp, enterprise appearance expected of a fast developer tool, avoiding bubbly or consumer-style rounded corners that waste screen real estate in dense data tables.

### Corner Radius Implementation
- **Base Components (`rounded-sm` - 0.25rem / 4px):** Standard buttons, text inputs, search boxes, tab strips, badges, table selection highlights, and context menus.
- **Secondary Shells (`rounded-md` - 0.375rem / 6px):** Dialog windows, translation memory match cards, and bottom sheet flyouts.
- **Zero Radius (`0px`):** Resizable panels, splitter containers, document viewport containers, and window chrome boundaries to preserve clean snapping edges.

## Components

### Buttons & Action Controls
- **Primary Button:** Background `#0066FF`, text `#FFFFFF`, border `none`, corner radius `4px`, height `28px` (compact) or `32px` (standard). Hover: `#1A75FF`. Pressed: `#0052CC`.
- **Secondary / Ghost Button:** Background transparent, text `#D1D5DB`, border `1px solid #3B4252`. Hover: background `#222630`, text `#FFFFFF`.
- **Icon Tool Button:** 24x24px or 28x28px borderless square icon button. Hover: background `#2B303C`. Active/Pressed: background `#0066FF` with white icon.

### Form Inputs & Search Fields
- **Compact Text Input:** Height `28px`, background `#14161B`, border `1px solid #2B303C`, text `#F3F4F6`, radius `4px`, padding `0 8px`. Focus: border `1px solid #0066FF`, box-shadow `0 0 0 1px #0066FF`.
- **Regex Filter Bar:** Integrated inline action buttons (case-sensitive `Aa`, whole word `\b`, regex `.*`) embedded directly within the right trailing edge of the input.

### Dual-Column Translation Editor
- **Segment Grid:** Composed of alternating or hairline-bordered bilingual cells. Source Chinese segment on the left, editable Vietnamese target on the right.
- **Active Segment State:** Bordered with a left accent indicator (`3px solid #0066FF`), background `#202530`.
- **Match Indicator Chip:** Inline badge showing translation source (e.g., `TM 98%` in `#10B981`, `AI MT` in `#0066FF`, `Glossary` in `#F59E0B`). Height `18px`, text size `11px`, padding `0 6px`, radius `3px`.

### Terminology & Data Tables
- **Table Header:** Height `26px`, background `#14161B`, text `#9CA3AF`, uppercase `11px`, border bottom `1px solid #2B303C`.
- **Row Styling:** Height `28px`, vertical-align middle, font-size `12px`. Hover: background `#1E222B`. Selected: background `#0066FF` with text `#FFFFFF`. Alternating row zebra banding is disabled in favor of subtle border dividers.

### Terminal & Log Stream Views
- **Container:** Background `#0D0E11`, border top `1px solid #2B303C`, font family `JetBrains Mono`.
- **Message Types:**
  - Info: `#9CA3AF` prefix with timestamp `[HH:mm:ss]`.
  - Translation Success: `#10B981` segment counter (`✓ Segment 142 translated`).
  - Warning / Fuzzy Term: `#F59E0B`.
  - Batch Error: `#EF4444` with hyperlinked line jump.

### Status Bar & Progress Indicators
- **Window Footer Status Bar:** Height `22px`, background `#0A0B0D`, text `#9CA3AF`, font size `11px`, items separated by `1px solid #2B303C` dividers.
- **Linear Progress Bar:** Thin `3px` track spanning the top of the active view or inside the status bar. Track: `#1E222B`, Fill: `#10B981` (animated indeterminate gradient when actively querying APIs).